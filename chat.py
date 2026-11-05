"""Чат с Имотко в терминала. Пускане: uv run chat.py"""

from imotko.agent import imot_agent

if __name__ == "__main__":
    imot_agent.cli_app(user="Какво търсиш", emoji="", user_id="default", exit_on=["", "изход"])
