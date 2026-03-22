import requests
import time

def trigger_test():
    base_url = "http://localhost:8000"
    
    print("Creating chat message to kick off agent...")
    res = requests.post(f"{base_url}/api/chat", json={
        "message": "hi, create a team of 3 coders to build a snake game in python, make them all coders",
        "client_id": "test_client_123"
    })
    
    print(res.status_code, res.text)
    print("Waiting for agents to be created...")

if __name__ == "__main__":
    trigger_test()
