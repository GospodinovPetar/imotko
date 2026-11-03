"""Проверява запазените търсения и показва само новите изгодни обяви.

Пускане по график (cron, всеки час):
    0 * * * * cd /Users/leshokefa/Developer/ai-agent && uv run watch.py

Ако е зададена NTFY_TOPIC в .env, новите обяви се пращат и като известие през ntfy.sh.
Прагът за "изгодна" е WATCH_MIN_SCORE (по подразбиране 20).
Обявите се отбелязват като видени само след успешна доставка.
"""

import os

import requests
from dotenv import load_dotenv

import imot_search as search

load_dotenv()

NTFY_URL = "https://ntfy.sh/"
MAX_MESSAGE_LENGTH = 3500  # ntfy приема до 4096 байта на съобщение


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Дели текста на части по редове, за да не се реже обява по средата."""
    parts = []
    current = ""

    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > max_length:
            parts.append(current)
            current = ""
        current += line[:max_length]  # ред, по-дълъг от лимита, се отрязва

    if current:
        parts.append(current)
    return parts


def notify(texts: list[str]) -> None:
    """Праща текстовете през ntfy.sh. Без NTFY_TOPIC не прави нищо."""
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return

    for text in texts:
        for part in split_message(text):
            # JSON, защото HTTP заглавките не приемат кирилица
            response = requests.post(
                NTFY_URL,
                json={"topic": topic, "title": "Нови обяви в imot.bg", "message": part},
                timeout=10,
            )
            response.raise_for_status()


def main() -> None:
    reports = search.check_saved_searches()
    texts = [report["text"] for report in reports]

    print("\n\n".join(texts) or "Няма нови обяви.")
    notify(texts)

    # чак след успешна доставка, иначе при грешка обявите биха се изгубили
    search.mark_as_delivered(reports)


if __name__ == "__main__":
    main()
