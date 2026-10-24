# START OF FILE src/agents/cg_escalation.py
"""
Constitutional Guardian escalation ladder (Round 2o).

2026-10-21 15:43-16:06 (WiFi_Snake_Game, qwen3.5:9b): the Guardian reviewed EVERY plain-text reply of every agent (50 reviews,
about 4.5 minutes of the single Ollama server) and raised 14 concerns, all false alarms ("writing models/score_manager.py
violates GP008", "'let me list all tasks' may expose personal data"). Each concern went straight to the user as a card with
Acknowledge / Revise Plan / Provide Feedback, but the agent was never paused: the NextStepScheduler set it back to 'idle' and
started its next cycle in the same millisecond, so by the time the user answered the agent was doing something else.

Now the Guardian reviews only text that LEAVES an agent: send_message content (to another agent) and Admin AI's replies to
the user. A concern holds that text and only its author waits; the concern climbs a ladder of deciders:

    worker -> its Project Manager -> Admin AI -> the user        PM -> Admin AI -> the user        Admin AI -> the user

* An agent decider answers with the 'resolve_concern' tool: 'approve' (deliver it as it is), 'revise' (the author rewrites it
  with the decider's feedback; the new version is reviewed again) or 'escalate' (pass it up).
* A concern about a CRITICAL principle (governance.yaml 'critical: true') is not settled by an agent's approval: the Guardian
  passes it up, and only a revised version the Guardian accepts (or the user) settles it.
* No decision within CG_DECISION_TIMEOUT_SECONDS: the Guardian passes it up. A version revised more than
  CG_MAX_REVISIONS_PER_LEVEL times that still raises a concern goes up too.
* At the user: the whole system pauses (no agent cycle starts) until the user approves, asks for a revision or stops the agent.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.agents.constants import (
    AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG, AGENT_STATUS_ERROR, AGENT_STATUS_IDLE,
    AGENT_TYPE_ADMIN, AGENT_TYPE_PM, AGENT_TYPE_WORKER, BOOTSTRAP_AGENT_ID, CONSTITUTIONAL_GUARDIAN_AGENT_ID,
)

if TYPE_CHECKING:
    from src.agents.manager import AgentManager

logger = logging.getLogger(__name__)

USER = "user"
KIND_MESSAGE = "message"   # send_message content to another agent
KIND_REPLY = "reply"       # Admin AI's reply to the user
DECISIONS = ("approve", "revise", "escalate")
DEFAULT_DECISION_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_REVISIONS_PER_LEVEL = 2
DEFAULT_REVISION_TIMEOUT_SECONDS = 900.0
HELD_TEXT_SHOWN_CHARS = 1500
_PRINCIPLE_ID_RE = re.compile(r"\bGP\d{3}\b")
_CONCERN_RE = re.compile(r"<CONCERN>(.*?)</CONCERN>", re.DOTALL | re.IGNORECASE)


def _setting(name: str, default: float) -> float:
    from src.config.settings import settings
    try:
        value = float(getattr(settings, name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def concern_text(verdict: str) -> str:
    """The explanation inside <CONCERN>...</CONCERN> (or the whole verdict if it is malformed)."""
    match = _CONCERN_RE.search(verdict or "")
    return (match.group(1) if match else (verdict or "")).strip() or "The Guardian raised a concern without details."


def cited_principles(verdict: str) -> List[str]:
    """Principle ids (GP001, ...) the Guardian named, in order, without repeats."""
    found: List[str] = []
    for principle_id in _PRINCIPLE_ID_RE.findall(verdict or ""):
        if principle_id not in found:
            found.append(principle_id)
    return found


def critical_principle_ids() -> List[str]:
    """Ids of the enabled governance principles marked 'critical: true'."""
    from src.config.settings import settings
    ids = []
    for principle in getattr(settings, "GOVERNANCE_PRINCIPLES", None) or []:
        if isinstance(principle, dict) and principle.get("enabled", False) and principle.get("critical", False):
            ids.append(str(principle.get("id")))
    return ids


def is_reviewed_reply(agent: Any) -> bool:
    """Only Admin AI's plain-text replies reach the user, so only those are reviewed (Round 2o)."""
    return getattr(agent, "agent_id", None) == BOOTSTRAP_AGENT_ID or getattr(agent, "agent_type", None) == AGENT_TYPE_ADMIN


def review_context(agent: Any, kind: str, target_id: Optional[str] = None) -> str:
    """Who wrote the reviewed text and where it goes (given to the Guardian with the text)."""
    who = (f"Author: agent '{getattr(agent, 'agent_id', '?')}' (type '{getattr(agent, 'agent_type', '?')}', "
           f"state '{getattr(agent, 'state', '?')}').")
    if kind == KIND_MESSAGE:
        return f"{who} This is a message the author is sending to agent '{target_id}'."
    return f"{who} This is the author's reply to the human user."


@dataclass
class CGConcern:
    concern_id: str
    author_id: str
    author_type: str
    kind: str
    text: str
    concern: str
    principles: List[str]
    critical: bool
    chain: List[str]
    target_id: Optional[str] = None
    event_data: Optional[Dict[str, Any]] = None
    level_index: int = 0
    status: str = "pending"          # pending -> revising -> resolved
    revisions: int = 0
    created: float = field(default_factory=time.time)
    level_since: float = field(default_factory=time.time)
    trail: List[str] = field(default_factory=list)

    @property
    def decider(self) -> str:
        return self.chain[min(self.level_index, len(self.chain) - 1)]

    @property
    def next_decider(self) -> Optional[str]:
        return self.chain[self.level_index + 1] if self.level_index + 1 < len(self.chain) else None

    def summary(self) -> Dict[str, Any]:
        return {
            "concern_id": self.concern_id, "author_id": self.author_id, "kind": self.kind, "target_id": self.target_id,
            "concern": self.concern, "principles": self.principles, "critical": self.critical, "decider": self.decider,
            "status": self.status, "revisions": self.revisions, "trail": list(self.trail),
        }


class CGEscalation:
    """Holds the concerns, the authors waiting for a decision, and the system pause while the user decides."""

    def __init__(self, manager: "AgentManager"):
        self._manager = manager
        self._concerns: Dict[str, CGConcern] = {}
        self._counter = 0
        self._paused_for: Optional[str] = None          # concern id the user is deciding (whole system paused)
        self._deferred: Dict[str, int] = {}              # agent_id -> retry_count of cycles not started while blocked

    # ------------------------------------------------------------------------------------------------ queries
    def get(self, concern_id: Any) -> Optional[CGConcern]:
        key = str(concern_id or "").strip().strip("'\"").lower()
        if key and not key.startswith("cg_") and key.isdigit():
            key = f"cg_{key}"
        return self._concerns.get(key)

    def open_concern_of(self, author_id: str) -> Optional[CGConcern]:
        """The author's concern that is still pending or being revised (newest first)."""
        for concern in reversed(list(self._concerns.values())):
            if concern.author_id == author_id and concern.status in ("pending", "revising"):
                return concern
        return None

    def held_concern(self, agent_id: str) -> Optional[CGConcern]:
        """The pending concern that keeps this agent waiting (its held text has not been decided yet)."""
        concern = self.open_concern_of(agent_id)
        return concern if concern and concern.status == "pending" else None

    def pending_for_decider(self, decider_id: str) -> List[CGConcern]:
        return [c for c in self._concerns.values() if c.status == "pending" and c.decider == decider_id]

    def user_concern_for(self, author_id: str) -> Optional[CGConcern]:
        concern = self.held_concern(author_id)
        return concern if concern and concern.decider == USER else None

    @property
    def paused_for(self) -> Optional[str]:
        return self._paused_for

    def is_system_paused(self) -> bool:
        return self._paused_for is not None

    def cycle_block_reason(self, agent: Any) -> Optional[str]:
        """Why this agent may not start a cycle now (None if it may)."""
        agent_id = getattr(agent, "agent_id", None)
        if not agent_id or agent_id == CONSTITUTIONAL_GUARDIAN_AGENT_ID:
            return None
        if self._paused_for:
            return f"the system is paused while the user decides Constitutional Guardian concern '{self._paused_for}'"
        held = self.held_concern(agent_id)
        if held:
            decider = "the user" if held.decider == USER else f"'{held.decider}'"
            return f"its {held.kind} is held by Constitutional Guardian concern '{held.concern_id}' until {decider} decides"
        return None

    def defer(self, agent: Any, retry_count: int = 0) -> None:
        """Remember a cycle that was not started, to start it when the agent may run again."""
        agent_id = getattr(agent, "agent_id", None)
        if not agent_id:
            return
        self._deferred[agent_id] = min(self._deferred.get(agent_id, retry_count), retry_count)
        held = self.held_concern(agent_id)
        if held and hasattr(agent, "set_status"):
            wanted = AGENT_STATUS_AWAITING_USER_REVIEW_CG if held.decider == USER else AGENT_STATUS_AWAITING_CG_REVIEW
            if getattr(agent, "status", None) != wanted:
                agent.set_status(wanted)

    # ------------------------------------------------------------------------------------------------ ladder
    def decider_chain(self, author: Any) -> List[str]:
        author_type = getattr(author, "agent_type", None)
        author_id = getattr(author, "agent_id", None)
        chain: List[str] = []
        if author_type == AGENT_TYPE_WORKER:
            pm_id = self._team_pm(author_id)
            if pm_id:
                chain.append(pm_id)
        if author_type in (AGENT_TYPE_WORKER, AGENT_TYPE_PM) and author_id != BOOTSTRAP_AGENT_ID:
            if BOOTSTRAP_AGENT_ID in getattr(self._manager, "agents", {}):
                chain.append(BOOTSTRAP_AGENT_ID)
        chain.append(USER)
        return chain

    def _team_pm(self, worker_id: Optional[str]) -> Optional[str]:
        state_manager = getattr(self._manager, "state_manager", None)
        try:
            team_id = state_manager.get_agent_team(worker_id) if state_manager else None
            if team_id:
                for member in state_manager.get_agents_in_team(team_id):
                    if getattr(member, "agent_type", None) == AGENT_TYPE_PM:
                        return member.agent_id
        except Exception as err:  # the ladder must never break a cycle
            logger.debug(f"CGEscalation: team lookup for '{worker_id}' failed: {err}")
        pms = [a.agent_id for a in getattr(self._manager, "agents", {}).values() if getattr(a, "agent_type", None) == AGENT_TYPE_PM]
        return pms[0] if len(pms) == 1 else None

    def _usable_decider(self, decider_id: str) -> bool:
        if decider_id == USER:
            return True
        agent = getattr(self._manager, "agents", {}).get(decider_id)
        return bool(agent) and getattr(agent, "status", None) != AGENT_STATUS_ERROR and not self.held_concern(decider_id)

    # ------------------------------------------------------------------------------------------------ raising
    async def raise_concern(self, author: Any, kind: str, text: str, verdict: str, target_id: Optional[str] = None,
                            event_data: Optional[Dict[str, Any]] = None) -> CGConcern:
        """Hold the author's text and give the concern to the first usable decider. A revision that is flagged again
        stays with its concern (and climbs after CG_MAX_REVISIONS_PER_LEVEL tries)."""
        principles = cited_principles(verdict)
        critical_ids = critical_principle_ids()
        critical = any(p in critical_ids for p in principles)
        explanation = concern_text(verdict)
        concern = self.open_concern_of(author.agent_id)
        if concern and concern.status == "revising":
            concern.revisions += 1
            concern.text, concern.concern, concern.kind = text, explanation, kind
            concern.target_id, concern.event_data = target_id, event_data
            concern.principles = sorted(set(concern.principles) | set(principles))
            concern.critical = concern.critical or critical
            concern.status = "pending"
            concern.trail.append(f"revision {concern.revisions} still raised a concern: {explanation[:200]}")
            max_revisions = int(_setting("CG_MAX_REVISIONS_PER_LEVEL", DEFAULT_MAX_REVISIONS_PER_LEVEL))
            if concern.revisions > max_revisions and concern.next_decider:
                await self._climb(concern, f"{concern.revisions} revisions still raised a concern")
                return concern
        else:
            self._counter += 1
            concern = CGConcern(
                concern_id=f"cg_{self._counter}", author_id=author.agent_id,
                author_type=str(getattr(author, "agent_type", "")), kind=kind, text=text, concern=explanation,
                principles=principles, critical=critical, chain=self.decider_chain(author), target_id=target_id,
                event_data=event_data)
            concern.trail.append(f"raised on {author.agent_id}'s {kind}" + (f" to {target_id}" if target_id else "") +
                                 f": {explanation[:200]}")
            self._concerns[concern.concern_id] = concern
        concern.level_since = time.time()
        while not self._usable_decider(concern.decider) and concern.next_decider:
            concern.trail.append(f"'{concern.decider}' cannot decide now, passed to '{concern.next_decider}'")
            concern.level_index += 1
        logger.warning(f"CGEscalation: concern '{concern.concern_id}' on {author.agent_id}'s {kind} "
                       f"(critical={concern.critical}, principles={concern.principles}) -> decider '{concern.decider}'.")
        if hasattr(author, "set_status"):
            author.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG if concern.decider == USER else AGENT_STATUS_AWAITING_CG_REVIEW)
        await self._notify_decider(concern)
        return concern

    async def _notify_decider(self, concern: CGConcern) -> None:
        await self._ui({"type": "cg_escalation", "agent_id": CONSTITUTIONAL_GUARDIAN_AGENT_ID, **concern.summary(),
                        "content": f"Concern {concern.concern_id} on {concern.author_id}'s {concern.kind} -> {concern.decider}"})
        if concern.decider == USER:
            await self._start_user_review(concern)
            return
        await self._message_agent(concern.decider, self.decider_message(concern))

    def decider_message(self, concern: CGConcern) -> str:
        author = concern.author_id
        held = concern.text if len(concern.text) <= HELD_TEXT_SHOWN_CHARS else concern.text[:HELD_TEXT_SHOWN_CHARS] + "\n...(shortened)"
        what = f"{author}'s message to '{concern.target_id}'" if concern.kind == KIND_MESSAGE else f"{author}'s reply to the user"
        next_up = concern.next_decider
        next_name = "the user" if next_up == USER else f"'{next_up}'"
        lines = [
            f"[Constitutional Guardian - DECISION NEEDED] concern_id='{concern.concern_id}'",
            f"{what} was HELD (not delivered). The Guardian's concern: {concern.concern}",
        ]
        if concern.revisions:
            lines.append(f"This is revision {concern.revisions} of it.")
        lines += [
            'Held text:\n"""\n' + held + '\n"""',
            f"Decide with the 'resolve_concern' tool (concern_id='{concern.concern_id}'):",
            "- decision='approve': it is delivered as it is.",
            f"- decision='revise' with 'feedback': {author} is told what to change and sends a new version, which the Guardian checks again.",
        ]
        if next_up:
            lines.append(f"- decision='escalate': {next_name} decides instead.")
        if concern.critical:
            lines.append(f"This concern is about a CRITICAL principle ({', '.join(concern.principles)}): approving does NOT settle it "
                         f"- it then goes to {next_name}. Only a revised version that the Guardian accepts settles it here.")
        if next_up:
            minutes = int(round(_setting("CG_DECISION_TIMEOUT_SECONDS", DEFAULT_DECISION_TIMEOUT_SECONDS) / 60)) or 1
            lines.append(f"Without a decision within about {minutes} minute(s) it goes to {next_name}.")
        return "\n".join(lines)

    async def _climb(self, concern: CGConcern, reason: str) -> bool:
        """Pass the concern to the next decider. False if it is already with the user."""
        previous = concern.decider
        if not concern.next_decider:
            return False
        concern.level_index += 1
        while not self._usable_decider(concern.decider) and concern.next_decider:
            concern.level_index += 1
        concern.level_since = time.time()
        concern.status = "pending"
        concern.trail.append(f"passed from '{previous}' to '{concern.decider}': {reason}")
        logger.warning(f"CGEscalation: concern '{concern.concern_id}' passed from '{previous}' to '{concern.decider}' ({reason}).")
        if previous != USER and previous in getattr(self._manager, "agents", {}):
            await self._message_agent(previous, f"[Constitutional Guardian]: concern '{concern.concern_id}' about "
                                                f"{concern.author_id}'s {concern.kind} was passed to "
                                                f"{'the user' if concern.decider == USER else repr(concern.decider)} ({reason}). "
                                                "You do not need to decide it any more.", wake=False)
        author = getattr(self._manager, "agents", {}).get(concern.author_id)
        if author is not None and hasattr(author, "set_status"):
            author.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG if concern.decider == USER else AGENT_STATUS_AWAITING_CG_REVIEW)
        await self._notify_decider(concern)
        return True

    # ------------------------------------------------------------------------------------------------ deciding
    async def decide(self, decider_id: str, concern_id: Any, decision: Any, feedback: Any = None) -> Tuple[bool, str]:
        """An agent's 'resolve_concern' call. Returns (ok, text for the decider)."""
        concern = self.get(concern_id)
        pending_ids = [c.concern_id for c in self.pending_for_decider(decider_id)]
        if concern is None:
            return False, (f"There is no Constitutional Guardian concern '{concern_id}'. "
                           + (f"Concerns waiting for your decision: {', '.join(pending_ids)}." if pending_ids else "None is waiting for your decision."))
        if concern.status != "pending":
            return False, f"Concern '{concern.concern_id}' is already {concern.status}; nothing to decide."
        if concern.decider != decider_id and decider_id != BOOTSTRAP_AGENT_ID:
            return False, f"Concern '{concern.concern_id}' is for '{concern.decider}' to decide, not for you."
        choice = str(decision or "").strip().strip("'\"").lower()
        if choice not in DECISIONS:
            return False, f"'decision' must be one of: {', '.join(DECISIONS)}."
        note = str(feedback or "").strip()
        concern.trail.append(f"'{decider_id}' decided '{choice}'" + (f": {note[:200]}" if note else ""))
        if choice == "escalate":
            if not await self._climb(concern, f"'{decider_id}' escalated it" + (f": {note}" if note else "")):
                return False, "This concern is already with the user."
            return True, f"Concern '{concern.concern_id}' was passed to {'the user' if concern.decider == USER else repr(concern.decider)}."
        if choice == "approve":
            if concern.critical:
                await self._climb(concern, f"'{decider_id}' approved it, but it concerns critical principle(s) "
                                           f"{', '.join(concern.principles)} and was not revised")
                return True, (f"Concern '{concern.concern_id}' is about a CRITICAL principle, so your approval does not settle it: "
                              f"it was passed to {'the user' if concern.decider == USER else repr(concern.decider)}.")
            await self.release(concern, decided_by=decider_id)
            return True, f"Approved: {concern.author_id}'s {concern.kind} was delivered."
        await self.request_revision(concern, decided_by=decider_id, feedback=note)
        return True, f"{concern.author_id} was asked to revise it and was given your feedback. Its new version will be checked again."

    async def release(self, concern: CGConcern, decided_by: str) -> None:
        """Deliver the held message (or, for a reply, leave delivery to the caller) and let the author run again."""
        concern.status = "resolved"
        concern.trail.append(f"released by '{decided_by}'")
        author = getattr(self._manager, "agents", {}).get(concern.author_id)
        if concern.kind == KIND_MESSAGE and concern.target_id:
            handler = getattr(self._manager, "interaction_handler", None)
            delivered = "could not be delivered"
            if handler is not None:
                status, _task = await handler.route_and_activate_agent_message(
                    sender_id=concern.author_id, target_identifier=concern.target_id, message_content=concern.text)
                delivered = "has now been delivered" if status.get("status") in ("delivered", "queued") else f"could not be delivered: {status.get('message')}"
            if author is not None:
                who = "the user" if decided_by == USER else f"'{decided_by}'"
                author.message_history.append({"role": "system", "content": (
                    f"[Constitutional Guardian]: {who} approved your held message to '{concern.target_id}' "
                    f"(concern '{concern.concern_id}'); it {delivered}. Do not send it again - continue with your task.")})
        await self._author_may_run(author)

    async def request_revision(self, concern: CGConcern, decided_by: str, feedback: str) -> None:
        concern.status = "revising"
        concern.level_since = time.time()
        author = getattr(self._manager, "agents", {}).get(concern.author_id)
        if author is not None:
            who = "the user" if decided_by == USER else f"'{decided_by}'"
            what = f"message to '{concern.target_id}'" if concern.kind == KIND_MESSAGE else "reply to the user"
            author.message_history.append({"role": "system", "content": (
                f"[Constitutional Guardian]: your {what} was NOT delivered (concern '{concern.concern_id}': {concern.concern}). "
                f"{who} asks you to REVISE it. Feedback: {feedback or 'Rewrite it so that it no longer raises this concern.'} "
                + ("Send the corrected message with send_message." if concern.kind == KIND_MESSAGE else "Write a corrected reply."))})
        await self._author_may_run(author)

    async def note_outgoing_ok(self, author: Any) -> None:
        """The author's next outgoing text passed the review: a concern it was revising is settled."""
        concern = self.open_concern_of(getattr(author, "agent_id", ""))
        if concern and concern.status == "revising":
            concern.status = "resolved"
            concern.trail.append("the revised version passed the Guardian's review")
            logger.info(f"CGEscalation: concern '{concern.concern_id}' settled - {concern.author_id}'s revised version passed.")
            await self._ui({"type": "cg_escalation", "agent_id": CONSTITUTIONAL_GUARDIAN_AGENT_ID, **concern.summary(),
                            "content": f"Concern {concern.concern_id} settled: the revised version passed."})

    async def _author_may_run(self, author: Any) -> None:
        if author is None:
            return
        if getattr(author, "status", None) in (AGENT_STATUS_AWAITING_CG_REVIEW, AGENT_STATUS_AWAITING_USER_REVIEW_CG):
            author.set_status(AGENT_STATUS_IDLE)
        self._deferred.pop(author.agent_id, None)
        if self.cycle_block_reason(author) is None:
            await self._manager.schedule_cycle(author, 0)
        else:
            self._deferred[author.agent_id] = 0

    # ------------------------------------------------------------------------------------------------ the user
    async def _start_user_review(self, concern: CGConcern) -> None:
        """Pause the whole system and show the concern to the user (the legacy fields drive the card and its answers)."""
        self._paused_for = concern.concern_id
        author = getattr(self._manager, "agents", {}).get(concern.author_id)
        event = concern.event_data or {"type": "final_response", "agent_id": concern.author_id, "content": concern.text}
        if author is not None:
            author.cg_original_text = concern.text
            author.cg_concern_details = f"<CONCERN>{concern.concern}</CONCERN>"
            author.cg_original_event_data = event
            author.cg_awaiting_user_decision = True
            author.cg_review_start_time = time.time()
            if hasattr(author, "set_status"):
                author.set_status(AGENT_STATUS_AWAITING_USER_REVIEW_CG)
        what = f"{concern.author_id}'s message to {concern.target_id}" if concern.kind == KIND_MESSAGE else f"{concern.author_id}'s reply"
        logger.warning(f"CGEscalation: SYSTEM PAUSED - the user decides concern '{concern.concern_id}' on {what}.")
        await self._ui({
            "type": "cg_concern", "agent_id": concern.author_id, "concern_id": concern.concern_id,
            "original_text": concern.text, "concern_details": f"<CONCERN>{concern.concern}</CONCERN>",
            "kind": concern.kind, "target_id": concern.target_id, "critical": concern.critical,
            "principles": concern.principles, "trail": list(concern.trail), "system_paused": True,
        })
        paused_text = f"All agents are PAUSED until you decide on the Constitutional Guardian concern about {what}."
        await self._ui({"type": "system_event", "agent_id": "system", "message": paused_text, "content": paused_text})

    async def finish_user_review(self, author_id: str, outcome: str, feedback: Optional[str] = None) -> Optional[CGConcern]:
        """Called after the user decided (approve / revise / stop): settle the concern and resume the system."""
        concern = self.user_concern_for(author_id) or self.get(self._paused_for)
        if concern is not None and concern.author_id == author_id:
            concern.status = "revising" if outcome == "revise" else "resolved"
            concern.level_since = time.time()
            concern.trail.append(f"the user decided '{outcome}'")
            self._tell_agent_deciders(concern, outcome, feedback)  # Round 3l
        await self.resume()
        return concern

    def _tell_agent_deciders(self, concern: CGConcern, outcome: str, feedback: Optional[str]) -> None:
        """
        Round 3l: the AGENTS of the concern's chain (e.g. the Admin AI that passed it up) learn what the user decided.
        2026-10-24 20:31:47 Admin AI approved concern 'cg_1' (PM1's message to W4 about sudo/apt-get) and it went to the user;
        Admin AI marked the Guardian's message read, so when the system resumed at 21:25:35 after the user's decision, the
        newest user message in its history was the ORIGINAL project request - it took it for a new one and planned the
        project again (a project update) while PM1 and the workers went on. A note, not a message: nobody is woken up.
        """
        what = {"revise": "asked the author to REVISE it", "approve": "APPROVED it", "stop": "STOPPED it"}.get(
            outcome, f"decided '{outcome}'")
        detail = f" The user's feedback: {feedback}" if feedback else ""
        agents = getattr(self._manager, "agents", {})
        for agent_id in dict.fromkeys(concern.chain):
            if agent_id in (USER, concern.author_id) or agent_id not in agents:
                continue
            agents[agent_id].message_history.append({"role": "system", "content": (
                f"[Constitutional Guardian]: the USER decided concern '{concern.concern_id}' on {concern.author_id}'s "
                f"{concern.kind}: the user {what}.{detail} {concern.author_id} handles it - there is nothing for you to do. "
                "This is NOT a new request from the user: do not plan or start anything because of it.")})
            logger.info(f"CGEscalation: told '{agent_id}' that the user decided '{outcome}' on concern '{concern.concern_id}'.")

    async def resume(self) -> None:
        """End the system pause (if no other concern waits for the user) and start the cycles that were not started."""
        waiting = [c for c in self._concerns.values() if c.status == "pending" and c.decider == USER]
        if waiting:
            self._paused_for = waiting[0].concern_id
            await self._start_user_review(waiting[0])
            return
        was_paused = self._paused_for is not None
        self._paused_for = None
        if was_paused:
            await self._ui({"type": "system_event", "agent_id": "system", "message": "Agents RESUMED.", "content": "Agents RESUMED."})
        agents = getattr(self._manager, "agents", {})
        for agent_id, retry_count in list(self._deferred.items()):
            agent = agents.get(agent_id)
            if agent is None or self.cycle_block_reason(agent) is not None:
                continue
            self._deferred.pop(agent_id, None)
            if getattr(agent, "status", None) in (AGENT_STATUS_IDLE, AGENT_STATUS_AWAITING_CG_REVIEW):
                if getattr(agent, "status", None) != AGENT_STATUS_IDLE:
                    agent.set_status(AGENT_STATUS_IDLE)
                await self._manager.schedule_cycle(agent, retry_count)

    # ------------------------------------------------------------------------------------------------ oversight
    async def check_timeouts(self, now: Optional[float] = None) -> List[str]:
        """The Guardian's oversight (periodic): undecided concerns climb, abandoned revisions are closed."""
        if self._paused_for:
            return []
        now = now or time.time()
        decision_timeout = _setting("CG_DECISION_TIMEOUT_SECONDS", DEFAULT_DECISION_TIMEOUT_SECONDS)
        revision_timeout = _setting("CG_REVISION_TIMEOUT_SECONDS", DEFAULT_REVISION_TIMEOUT_SECONDS)
        moved: List[str] = []
        for concern in list(self._concerns.values()):
            waited = now - concern.level_since
            if concern.status == "pending" and concern.decider != USER and waited > decision_timeout:
                if await self._climb(concern, f"no decision from '{concern.decider}' within {int(decision_timeout)} s"):
                    moved.append(concern.concern_id)
            elif concern.status == "revising" and waited > revision_timeout:
                concern.status = "resolved"
                concern.trail.append(f"closed: no revised version within {int(revision_timeout)} s (the held text was never delivered)")
                logger.info(f"CGEscalation: concern '{concern.concern_id}' closed - {concern.author_id} sent no revised version.")
                moved.append(concern.concern_id)
        return moved

    # ------------------------------------------------------------------------------------------------ helpers
    async def _message_agent(self, agent_id: str, text: str, wake: bool = True) -> None:
        agent = getattr(self._manager, "agents", {}).get(agent_id)
        if agent is None:
            return
        handler = getattr(self._manager, "interaction_handler", None)
        if wake and handler is not None and CONSTITUTIONAL_GUARDIAN_AGENT_ID in getattr(self._manager, "agents", {}):
            try:
                await handler.route_and_activate_agent_message(
                    sender_id=CONSTITUTIONAL_GUARDIAN_AGENT_ID, target_identifier=agent_id, message_content=text)
                return
            except Exception as err:
                logger.error(f"CGEscalation: could not route the concern to '{agent_id}': {err}", exc_info=True)
        agent.message_history.append({"role": "system", "content": text})
        if wake and getattr(agent, "status", None) == AGENT_STATUS_IDLE:
            await self._manager.schedule_cycle(agent, 0)

    async def _ui(self, payload: Dict[str, Any]) -> None:
        send = getattr(self._manager, "send_to_ui", None)
        if send is None:
            return
        try:
            await send(payload)
        except Exception as err:
            logger.debug(f"CGEscalation: UI update failed: {err}")

# END OF FILE src/agents/cg_escalation.py
