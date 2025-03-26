class Agent:
    def __init__(self, agent_id):
        self.id = agent_id
        self.config = {}
        self.sandbox_path = f"../sandboxes/agent_{agent_id}"
    
    async def send_message(self, content):
        return f"Agent {self.id} received: {content}"
