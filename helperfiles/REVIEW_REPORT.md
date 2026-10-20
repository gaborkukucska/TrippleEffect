<!-- # START OF FILE helperfiles/REVIEW_REPORT.md -->
# TrippleEffect — Code & Documentation Review

**Reviewed version:** README 2.49/2.50, PROJECT_PLAN 2.50
**Review date:** 2026-10-14
**Scope:** `src/` (~28.9k LOC Python), `static/`, `prompts.yaml`, `config.yaml`, `governance.yaml`, `docs/`, `helperfiles/`, and the supplied `tests/` archive.

---

## 1. Executive summary

TrippleEffect's central idea is sound and worth continuing: **let the framework own orchestration (state machine, per-state prompts, bounded context, loop-breaking, error recovery) and let a small model do only the thinking.** Many of the individual mechanisms are genuinely clever for small models: contextual error recovery (directory listing appended to file-not-found), alias auto-correction for hallucinated actions, duplicate-call caching with truncated results, forced state transitions that skip the LLM entirely, and inbox-based message delivery.

The project's problem is not the idea, it is that **nothing proves which parts of it work**, and the code has grown by reactive patching of a single test scenario:

| Area | State |
|---|---|
| Builds / imports | Compiles cleanly; imports on current dependency versions; no undefined names (pyflakes) |
| Tests | 63 test functions exist (not 76). 25 pass, 32 fail, 1 module cannot import. None cover the scheduler, health monitor, project_management, file_system, code_editor or executor |
| Security | Three P0 issues (secret leakage, un-gated command execution, Guardian never sees tool calls) |
| Docs vs code | ~15 verified discrepancies (section 5) |
| Maintainability | `cycle_handler.py` 2,978 lines, `file_system.py` 1,859, `agent_health_monitor.py` 1,633; about five overlapping loop-detection systems |

**Top recommendation:** build a small evaluation harness *before* adding Phase 28 features, then use it to decide which of the existing mechanisms earn their complexity.

---

## 2. Method and confidence

Findings are marked:

* **[RUN]** reproduced by executing code in a sandbox
* **[READ]** established by reading the code path (not executed)
* **[DOC]** a documentation inconsistency

I did not run the full system against a live Ollama/vLLM backend, so behavioural claims about agents (loops, quality of output) rely on your logs summarised in `CURRENT_ISSUES.md`, not on my own runs. Token figures use the framework's own ~4 chars/token heuristic and are estimates.

---

## 3. What is good (keep and build on it)

1. **Per-state prompts are compact**: 100–1,900 tokens each (largest: `pm_manage_prompt` ~1.9k, `worker_work_prompt` ~1.2k). This is right for small models.
2. **Hard framework interventions that bypass the LLM** (forced `worker_work → worker_report → worker_wait`) are the correct answer to autoregressive loops in 4–9B models.
3. **Error messages that teach**: action-specific help injected on tool failure; fuzzy filename suggestions; directory listing on missing files.
4. **Message inbox / acknowledgement** keeps stale messages out of context.
5. **Filtered, bounded views** (workspace tree depth 4 / 200 files; `list_tasks` defaults to unassigned/pending).
6. **Provider layer** handles Ollama RAW templates, per-model `num_ctx`/stop tokens, and a tool-support blacklist for failover.
7. **File path sandboxing** in `file_system` is sound: rejects `..`, resolves symlinks, enforces containment.
8. **UI escaping** of agent output is mostly correct: `agent_response`, tool results and thoughts are passed through `escapeHTML` before reaching `innerHTML`.

---

## 4. Findings

Severity: **P0** fix before any further use on a machine with real data · **P1** fix soon · **P2** cleanup.

### 4.1 Security

| ID | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| S1 | P0 | `command_executor` passes the **entire process environment** to agent shells (`env=os.environ.copy()`). `.env` is loaded into the environment, so API keys, GitHub PAT and `SECRET_KEY` are readable with `printenv`. **[RUN]** a planted fake key was printed by an agent command. | `command_executor.py` ~L190 | Build an explicit minimal env (`PATH`, `HOME` → sandbox, `LANG`); never inherit. Strip provider keys. |
| S2 | P0 | The Constitutional Guardian is **only invoked on final text responses**. Native tool calls (the default mode) never pass through it. The GP008 "command check" tests for the literal string `<tool_call>` in text, which does not occur in native JSON mode, so Phase VI's claim is not true for the default configuration. **[READ]** (`_get_cg_verdict` has two call sites, both on final text). | `cycle_handler.py` L2506–2520, L2778 | Replace with a **deterministic pre-execution policy gate** in `ToolExecutor` (allow/deny rules, path checks, network rules). Keep the LLM Guardian only for ambiguous cases. |
| S3 | P0 | `test_runner` is a second **unrestricted shell** (`create_subprocess_shell`), ignores `DISABLE_COMMAND_EXECUTION`, and `scope="projects"` sets cwd to the parent of all projects. **[READ]** + grep: the kill-switch is checked only in `command_executor.py`. | `test_runner.py` L60 | Route through one shared `run_sandboxed()` helper that enforces the kill-switch, env scrubbing, process-group kill and output limits. |
| S4 | P1 | `command_executor` is described as a "restricted sandbox" but only sets `cwd`. A command can read/write anywhere the server user can, and make network calls. | docstring vs implementation | Run in a container/bubblewrap/`unshare` where available; otherwise document honestly as *unsandboxed* and rely on S2's policy gate. |
| S5 | P1 | **First-user takeover:** whoever reaches the port first registers as main/admin user (`count_users` then `create_user` is also a race). No login rate limiting. Cookie lacks `Secure` option. No WebSocket `Origin` check (Lax cookies still go to same-site other ports on localhost). **[READ]** | `auth_routes.py`, `websocket_manager.py` | One-time setup token printed in the console (or CLI `create-admin`); atomic first-user insert; simple per-IP backoff; `Secure` when behind TLS; verify `Origin` on WS handshake. |
| S6 | P1 | **bcrypt latent crash:** `hash_password` raises `ValueError` for passwords over 72 bytes on bcrypt ≥ 5, and `max_length=128` allows them. The `bcrypt<4.1.0` pin is stale (passlib is gone) and blocks security updates. **[RUN]** bcrypt 5.0.0 confirmed. | `auth.py`, `requirements.txt` | Pre-hash with SHA-256 (base64) before bcrypt, or cap at 72 bytes; remove the pin. |
| S7 | P2 | `codebase_search` runs `grep -rnIE <query>` without `--`; a query starting with `-` is parsed as an option. | `codebase_search.py` | Use `grep -e <query> --`. |
| S8 | P2 | Web pages fetched by `web_search get_page` flow to agents that hold shell tools: classic prompt-injection path. No trust boundary between fetched content and instructions. | design | Mark fetched content as untrusted data; deny `command_executor` in the same turn as untrusted content unless policy gate approves. |

### 4.2 Correctness and reliability

| ID | Sev | Finding | Fix |
|---|---|---|---|
| R1 | P1 | **Tool filtering is inconsistent.** Phase T says all state-based blocking was removed. `executor.py` is clean, but `core.py` L269–278 still filters native schemas per state (e.g. workers in `worker_report`/`worker_wait` cannot see `code_editor` or `command_executor`). **[READ]** | Decide deliberately: make per-state tool subsets a *feature* (see 6.3) driven by one table, used by both prompt and schema paths. |
| R2 | P1 | **Timeout mismatch.** `CURRENT_ISSUES #27` says read timeout was cut to 120s / total 600s. Code has `DEFAULT_READ_TIMEOUT = 1800`, `DEFAULT_TOTAL_TIMEOUT = 3600`; a comment says "240s per chunk". Also, with tools attached the provider forces `stream=False`, so the server is silent until generation ends; a short read timeout would kill slow hardware, a long one re-opens the original deadlock. **[READ]** | Keep streaming on with tools (Ollama supports it), apply a *first-token* timeout plus an *inter-chunk* timeout, and make both configurable. |
| R3 | P1 | **npm install skip** returns fabricated stdout ("up to date, audited…") whenever `node_modules` exists, even if the agent just added dependencies to `package.json`. **[READ]** | Compare `package.json`/lockfile mtime or hash against a stored marker; return a truthful "skipped" message. |
| R4 | P1 | **Timeout hint causes the hang it warns about.** A bare `cmd &` keeps the stdout pipe open, so `communicate()` blocks until timeout; the timeout message then tells the agent to use `&`. **[RUN]** 30s measured. | Always redirect and detach (`nohup … >log 2>&1 &`, `start_new_session=True`), and fix the message. |
| R5 | P1 | **Unpinned dependencies.** `requirements.txt` has no version bounds except two. `httpx` is imported directly (`openai_provider.py`) but not declared, and newer `openai` releases no longer pull it in. **[RUN]** | Add `httpx`; generate a lock file; pin major versions. |
| R6 | P1 | `httpx`/`openai_provider` import fails when the module is imported first (circular import through the package). Harmless in the app, breaks isolated tests. **[RUN]** | Lazy import or restructure `llm_providers/__init__`. |
| R7 | P2 | `ContextSummarizer.estimate_token_count` ignores tool schemas (~5k tokens/call for workers) and uses 4 chars/token, which is optimistic for code/JSON. Ollama returns real `prompt_eval_count`/`eval_count` but they are never captured. **[READ]** | Use real counts from the provider; fall back to heuristic. |
| R8 | P2 | **WIP "heartbeat" is a snapshot.** The system prompt (containing `{team_wip_updates}`, address book, time) is generated on state change and reused until the next state change. So the "real-time" team status in docs is stale during long states. **[READ]** | Move volatile data into a single trailing block rebuilt each cycle (also keeps the prompt prefix stable for KV-cache reuse). |
| R9 | P2 | Check-then-insert race in registration; first-run `SECRET_KEY` is random per process (sessions die on restart, documented only as a log warning). | See S5; write a generated key to `.env` on first run. |
| R10 | P2 | `preexec_fn=os.setsid` is unsafe with threads; use `start_new_session=True`. | |
| R11 | P2 | `command_executor` description says scope defaults to `private`; code defaults to `shared`. | |
| R12 | P2 | Provider does `json.dumps(payload, indent=2)` and a deep copy on every call regardless of log level. | Guard with `logger.isEnabledFor(DEBUG)`. |

### 4.3 Code hygiene

* 175 unused imports, ~25 unused locals, 8 redefinitions of imports (pyflakes).
* Dead code: `PM_STATE_WORK` / `pm_work_prompt` (mapped but never entered), `PM_STATE_PLAN_DECOMPOSITION`.
* A stray non-ASCII junk entry in `OPENAI_CLIENT_VALID_INIT_KWARGS` (`" 스트리밍_usage"`, in both OpenAI and OpenRouter providers).
* `handlers.js` (cg_concern template) contains a JSX-style comment `{/* … */}` inside a template literal, which renders as visible text.
* 45 `innerHTML` assignments in the frontend: currently safe on the paths I traced, but worth a one-time audit plus a CSP header.
* 297 `except Exception` blocks; several fail open silently (the Guardian by design).

### 4.4 Overlapping control systems

Loop/stall handling is spread across: `AgentCycleHandler` (duplicate cache + AUTO-ADVANCE), `NextStepScheduler` (loop detection + forced transitions), `AgentHealthMonitor` (stuck-state, empty responses), `AgentManager` watchdog (PM check, standby wake, worker auto-recovery), the Guardian heartbeat, and XML cleanup. `LoopCoordinator` was introduced to arbitrate but is only consulted from `manager.py` and `next_step_scheduler.py`, not from `cycle_handler.py` or `agent_health_monitor.py`. Each patch was reasonable; collectively the behaviour is hard to predict or test.

---

## 5. Documentation vs code discrepancies

| # | Document says | Code / reality |
|---|---|---|
| D1 | Phase T: state-based tool blocking "completely removed" | Still present in `core.py` (R1) |
| D2 | Phase VI: Guardian flags suspicious shell commands (GP008) | Not enforced in native mode (S2) |
| D3 | Issue #27: read timeout 120s / total 600s | 1800s / 3600s in code (R2) |
| D4 | `DISABLE_COMMAND_EXECUTION` global kill-switch | Covers `command_executor` only; `test_runner` bypasses (S3) |
| D5 | LoopCoordinator "prevents thrashing across the various watchdogs" | Partially adopted (4.4) |
| D6 | Team WIP "real-time heartbeat" | Snapshot at state entry (R8) |
| D7 | Issue #16: knowledge "vectorized" into SQLite | Keyword substring match on a comma-separated column |
| D8 | Plan: "76/76 tests passing across 11 modules" | 63 test functions in 10 files; 25 pass / 32 fail / 1 import error. `tests/` is in `.gitignore` |
| D9 | Issue #18 (`command_executor` omits stderr) listed Active | Already fixed: last 500 chars of stderr are returned |
| D10 | TECH_STACK / Plan: Python 3.9+ | Code requires 3.10+ (`X \| None` annotations, `contextlib.aclosing`); README says 3.10+ |
| D11 | `prompts.json` referenced in Plan, TECH_STACK, config.yaml, DEVELOPMENT_RULES | File is `prompts.yaml` |
| D12 | CORE_CONCEPTS/FRAMEWORK admin states `conversation`, `delegated` | Constants: `admin_conversation`, `work_delegated`, `admin_standby`, `startup`, `planning`, `work` |
| D13 | FRAMEWORK §5.1 worker states omit `worker_test`, `worker_startup`; PM `pm_work` listed in constants but unused | |
| D14 | `FEATURES.md` model tier `FREE` or `ALL` | `LOCAL` also exists (and is the default in `.env.example`) |
| D15 | Undocumented tools: `zim_search`, `github_tool`, `mark_message_read`; `test_runner`/`codebase_search` only in README | |
| D16 | README header "Version 2.49", status "2.50"; Plan "2.50, 2026-04-30" but contains May 2026 phases; phases are numbered 1–27, lettered A–Y, then Roman "VI" | Version/phase naming is inconsistent |
| D17 | CURRENT_ISSUES numbering | Duplicate and signed numbers (`+4` appears twice, `-16`…), no active-vs-resolved index |
| D18 | `TASKWARRIOR.md`: `add_task` "includes both tag and UDA attempt" as a test | Long-lived workaround; see section 7 |

---

## 6. Small-model analysis

### 6.1 Token budget (measured from the real tool classes)

Native tool-schema size (JSON chars; ≈ chars/4 tokens):

| Tool | chars | | Tool | chars |
|---|---|---|---|---|
| file_system | 4,914 | | command_executor | 1,133 |
| manage_team (pm) | 2,186 | | code_editor | 1,083 |
| knowledge_base | 1,988 | | tool_information | 1,017 |
| asset_search | 1,831 | | web_search | 987 |
| github_tool | 1,566 | | codebase_search | 838 |
| project_management | 1,517 | | send_message | 785 |
| zim_search | 1,280 | | test_runner / mark_message_read | 696 / 660 |

**Total ≈ 23.1k chars ≈ 5.8k tokens; a worker sees ≈ 20.3k chars ≈ 5.1k tokens on every call**, on top of a ~1.2k-token state prompt and history. On an 8k context that is most of the window before any work happens; the summarizer threshold (7,000) does not even count it. A worker in `worker_work` realistically needs about eight tools, and `worker_decompose`/`report`/`wait` need three to five, so per-state subsets could plausibly remove **40–60 %** of fixed overhead. This is the cheapest high-impact change available.

### 6.2 Where the framework compensates for model weakness that tooling could remove

| Current patch | Root cause | Better fix |
|---|---|---|
| `action_suggestions` alias maps; "Invalid action" retries | model invents action names | JSON-schema `enum` + constrained decoding (Ollama `format`, vLLM guided decoding, llama.cpp grammar) |
| Issue #19: workers omit `task_id` | free-form arguments | schema `required` enforced by grammar; auto-infer as fallback |
| XML recovery, `<tool_call>` text parsing, `<think>` prefix allowances | thinking tokens mixed into content | Ollama `think` parameter (separates reasoning) or `think:false` for deterministic steps |
| Guardian `CG_MAX_TOKENS=4000` for empty verdicts | thinking model starves a one-word verdict | non-thinking tiny model or constrained `<OK/>`/`<CONCERN>` output |
| Truncating last 4 messages to break loops | autoregressive repetition | repetition penalty / `min_p` / temperature bump on retry, then state hop |

### 6.3 Prompt-prefix stability

Local runtimes reuse the KV cache for an identical prompt prefix. The framework rewrites history in several places (summaries, "remove last 4 messages", de-duplicating framework messages, inserting the workspace tree at the bottom). Keeping the prefix append-only (static system + tools → history → one volatile trailing block) would cut prompt-evaluation time substantially on CPU/phone-class hardware, where prompt processing dominates.

---

## 7. Replacing Taskwarrior (you approved)

**Why:** `tasklib` is a wrapper around the `task` CLI. The code spends most of its effort working around it (assignee stored as a *tag* because the UDA was unreliable; `task_progress` UDA mapped onto three native statuses; ID parsing from CLI output; PID/UUID fuzzy lookups). It adds a native binary dependency on Termux/Android, and Taskwarrior 3.x changed its storage format, a compatibility risk. SQLite is already in the stack.

**Footprint** (reference counts): `project_management.py` 33, `cycle_handler.py` 7, `workflow_manager.py` 3, `prompt_assembler.py` 3, `pm_kickoff_workflow.py` 3, `interaction_handler.py` 1. Small enough for a clean swap behind one interface.

**Design:**

```text
TaskStore (interface)  ←  SqliteTaskStore (new)   |  (optional) TaskwarriorExport
  add(title, description, project, assignee, priority, depends[], tags[], parent)
  get(id|uuid|alias)      list(filters: progress, assignee, project, unblocked_only)
  update(id, progress|assignee|priority|notes)    complete(id)
  deps(id) / ready()      graph() -> mermaid
```

* One SQLite file per session at `projects/{Project}/{Session}/task_data/tasks.db` (keeps session isolation and save/load/zip semantics).
* Tables: `tasks(uuid, alias, project, title, description, progress, assignee, priority, parent_uuid, created, updated, finished)`, `task_deps(task_uuid, depends_on_uuid)`, `task_tags(task_uuid, tag)`.
* `progress` is the existing vocabulary (`todo`, `in_progress`, `waiting`, `stuck`, `failed`, `finished`), now a first-class column instead of a UDA.
* `ready()` returns tasks whose dependencies are all finished, so the PM prompt can be given *only* actionable work (replaces the dependency-checking logic in `cycle_handler.py`).
* Keep every action name and alias in `ProjectManagementTool` so prompts are unchanged; swap only the backend.
* Migration helper: import an existing `task_data` (JSON export) into the new store; `get_dependency_graph` stays.
* Delete after migration: Taskwarrior install steps in `setup.sh`, `TASKWARRIOR.md`, `tasklib`/`taskw` requirements.

---

## 8. Test suite status

Archive supplied: 10 files, 63 test functions. Run against the current code:

| Result | Count | Cause |
|---|---|---|
| Passed | 25 | |
| Failed | 32 | stale tests: missing imports inside the test (`AGENT_TYPE_ADMIN`, `AgentManager`), changed signatures (`_select_alternate_models(..., agent)`), removed behaviour (global governance prompt injection was deliberately removed), changed `extract_keywords_from_text` output |
| Collection error | 6 tests | `test_agent_lifecycle.py` imports `PerformanceTracker`; the class is now `ModelPerformanceTracker` |

The tests are an **older snapshot**: either the "76/76" run used a newer set, or the number refers to a different count. Either way, the suite does not reflect the current code and `tests/` is gitignored, so it can never protect it. There is **no** coverage of the scheduler, health monitor, `project_management`, `file_system`, `code_editor`, executor, auth, or the PM kickoff workflow.

**Status after Round 2m (2026-10-20):** all stale tests repaired against the current code (none skipped or deleted), `test_agent_lifecycle.py` imports again, and seven `async def` tests that had never run (plain `unittest.TestCase`) now run. `python -m pytest -q -W error tests`: 1057 passed, 0 failed, 0 warnings on Python 3.13 and 3.14. The repaired tests found real defects, fixed in the same round: the localhost Ollama (`ollama-local`) counted as a remote provider in failover and model selection; one malformed governance principle broke every system prompt; agents' code with an invalid escape printed SyntaxWarnings to the server's stderr.

**Plan:** un-ignore `tests/`; repair/retire stale tests; add characterization tests for the highest-risk modules first (policy gate, command env scrubbing, task store, state-transition table, loop-breaker decisions).

---

## 9. Opinion and feature ideas

1. **Evaluation harness (highest value).** 5–10 fixed scenarios (e.g. "build a CLI todo app with tests", "research and write a one-page brief", "fix a seeded bug"). Record: success (tests pass / rubric), cycles, wall-clock, prompt/completion tokens, framework interventions, failovers. Run each with every mechanism switchable (duplicate cache, forced transitions, summarizer, state prompts, alias maps, Guardian). This turns "months of ideas" into a defensible result: *which harness features lift a 4–9B model, and by how much, versus a plain ReAct loop.* It is also the thing most worth publishing.
2. **Declarative state machine.** Move states, transitions, allowed tools, max tokens, output schema, and exit predicates into one YAML/Python table that the prompt builder, schema builder, scheduler and docs are all generated from. This removes D1/D12/D13-style drift and shrinks `cycle_handler.py`.
3. **Constrained decoding + `think` control** (6.2). Likely removes entire categories of retry cycles.
4. **Capability-based model routing.** Use the performance tracker to learn per-model reliability (tool-call validity, context, thinking), route cheap steps (summaries, Guardian, classification) to a 1–3B model and coding to the 9B, escalate on validator failure. Your failover code already does half of this, driven by errors instead of capability.
5. **Transactional workspace.** `git` snapshot before each worker cycle; roll back and re-plan when the loop-breaker fires. Eliminates the whole "overwrite protection" family of workarounds.
6. **Framework-run verification.** Let the framework (not the agent) run the project's tests after each task and feed back a 10-line summary; the model decides what to fix.
7. **Real memory (Phase 28):** SQLite FTS5/BM25 first (zero dependencies, runs on a phone), optional local embeddings later; a "lessons learned" store populated from stall/loop events, so the same failure is not repeated in the next project.
8. **Device profiles** (`phone`, `laptop`, `workstation`) that set context, concurrency, tool subsets and Guardian mode in one switch, matching the "old Android phone" goal.
9. **Offline knowledge:** `zim_search` (offline Wikipedia/docs) is a standout feature for the stated goal; document it and promote it.
10. **Observability:** a per-run report (tokens per state, interventions per agent, time-to-first-token) in the UI. It doubles as the harness's data source.

---

## 10. Assumptions and open points

* I assumed the supplied `tests.zip` is the only test archive; whether it is the latest could not be determined from the content (it is older than the code).
* Python 3.10+ is treated as the minimum.
* Phase numbering in the updated plan is a **proposal**: measurement is inserted before memory/learning. Re-order freely.
* Not verified: behaviour under a real Ollama/vLLM backend, the Termux install path, GitHub/asset/ZIM tools with live credentials, and Taskwarrior 3.x compatibility.

---

## Appendix A — Reproduction notes

* **S1/R4:** instantiate `CommandExecutionTool`, call `execute(agent_id=…, agent_sandbox_path=…, action="run_command", scope="private", command="printenv FAKE_KEY")` with the variable set in the parent environment; and `command="sleep 600 &"` with `timeout=30`.
* **S6:** `bcrypt.hashpw(b"a"*100, bcrypt.gensalt())` with bcrypt ≥ 5.
* **Schema sizes:** instantiate each `BaseTool` subclass and `len(json.dumps(tool.get_json_schema()))`.
* **Static checks:** `python -m pyflakes src`; `python -m compileall src`.
<!-- # END OF FILE helperfiles/REVIEW_REPORT.md -->
