"""Уеб сървър (AgentOS) за агента. Пускане: uv run app.py"""

from agno.os import AgentOS

from imotko import tracing
from imotko.agent import imot_agent

tracing.setup("production")

agent_os = AgentOS(agents=[imot_agent])
app = agent_os.get_app()


if __name__ == "__main__":
    agent_os.serve(app="app:app", reload=True)
