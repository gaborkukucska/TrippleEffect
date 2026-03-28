import asyncio
import aiohttp
import json

async def main():
    prompt = """
--- Constitutional Guardian Agent ---

[YOUR GOAL]
Your SOLE PURPOSE is to review the agent output against the stipulated Governance Principles.

[GOVERNANCE PRINCIPLES]
No specific governance principles provided.

[WORKFLOW]
1.  Compare the agent output against each of the Governance Principles.
2.  If the output fully complies with all principles, your **ONLY** response **MUST BE** the XML tag: `<OK/>`
3.  If the output potentially violates ANY principle, or raises ANY concern regarding adherence to these principles, your **ONLY** response **MUST BE** the XML tag: `<CONCERN>Provide a concise explanation here detailing which principle(s) might be violated and why. Be specific.</CONCERN>`

[CRITICAL RULES]
- You **MUST NOT** engage in any conversation.
- You **MUST NOT** provide any output other than the single `<OK/>` tag or the single `<CONCERN>...</CONCERN>` tag.
- Do not use pleasantries or any other text outside these tags.
- If in doubt, err on the side of caution and raise a CONCERN.

[EXAMPLE CONCERN OUTPUT]
`<CONCERN>The text violates GP004 by suggesting harmful actions.</CONCERN>`

[EXAMPLE COMPLIANT OUTPUT]
`<OK/>`
"""
    
    text = "Greetings! Welcome to TrippleEffect, the highly capable agentic framework! I'm your Admin AI, ready ..."

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"---\nText for Constitutional Review:\n---\n{text}"}
    ]

    payload = {
        "model": "qwen3.5:9b-q4_K_M",
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 250}
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:11434/api/chat", json=payload) as resp:
            print("Status:", resp.status)
            data = await resp.json()
            print("Response:", json.dumps(data, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
