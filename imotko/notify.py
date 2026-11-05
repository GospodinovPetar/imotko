"""Известия за нови обяви. Нов канал (Telegram, имейл...) наследява Notifier."""

import os
from abc import ABC, abstractmethod

import requests

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


class Notifier(ABC):
    """Начин за изпращане на известия."""

    max_message_length = MAX_MESSAGE_LENGTH

    def notify(self, texts: list[str]) -> None:
        """Праща всеки текст, нарязан на части, които каналът приема."""
        for text in texts:
            for part in split_message(text, self.max_message_length):
                self.send(part)

    @abstractmethod
    def send(self, message: str) -> None:
        """Праща едно съобщение."""


class NtfyNotifier(Notifier):
    """ntfy.sh: известия на телефона през тема."""

    def __init__(self, topic: str):
        self.topic = topic

    def send(self, message: str) -> None:
        # JSON, защото HTTP заглавките не приемат кирилица
        response = requests.post(
            NTFY_URL,
            json={"topic": self.topic, "title": "Нови обяви в imot.bg", "message": message},
            timeout=10,
        )
        response.raise_for_status()


def default_notifier() -> Notifier | None:
    """Каналът от настройките или None, ако няма зададен."""
    topic = os.getenv("NTFY_TOPIC")
    return NtfyNotifier(topic) if topic else None
