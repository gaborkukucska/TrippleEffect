import asyncio
import websockets
import json
import uuid

async def test_chat():
    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri) as ws:
        msg = {
            "type": "chat",
            "content": "Hi! Can you create a team with 3 coder roles to make a Snake game in Python?",
            "client_id": "testclient"
        }
        await ws.send(json.dumps(msg))
        print("Sent chat message. Waiting for responses...")
        
        while True:
            try:
                recv = await asyncio.wait_for(ws.recv(), timeout=60.0)
                data = json.loads(recv)
                t = data.get("type")
                if t in ["agent_thought", "agent_raw_response"]:
                    print(f"[{data.get('agent_id')}] {t}: {data.get('content')[:100]}...")
                elif t == "agent_status_update":
                    print(f"[{data.get('agent_id')}] Status: {data.get('status', {}).get('status')}")
                elif t == "error":
                    print(f"ERROR from {data.get('agent_id')}: {data.get('content')}")
                elif t == "tool_result":
                    print(f"[{data.get('agent_id')}] Tool Result: {data.get('content')[:100]}...")
            except asyncio.TimeoutError:
                print("Timeout waiting for more messages. Ending test.")
                break

if __name__ == "__main__":
    asyncio.run(test_chat())
