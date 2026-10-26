from agno.agent.agent import Agent
from agno.models.ollama import OllamaResponses
from agno.os import AgentOS
from agno.os.interfaces.agui import AGUI

chat_agent = Agent(model=OllamaResponses(id="qwen3.5-4b-16k:latest"))

agent_os = AgentOS(agents=[chat_agent], interfaces=[AGUI(agent=chat_agent)])
app = agent_os.get_app()

if __name__ == "__main__":
    agent_os.serve(app="basic:app", reload=True)