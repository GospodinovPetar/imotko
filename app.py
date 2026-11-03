"""Уеб сървър (AgentOS) за агента. Пускане: uv run app.py"""

from agno.os import AgentOS

from imot_agent import imot_agent

agent_os = AgentOS(agents=[imot_agent])
app = agent_os.get_app()


if __name__ == "__main__":
    agent_os.serve(app="app:app", reload=True)
