"""Проверява запазените търсения и показва само новите изгодни обяви.

Пускане по график (cron, всеки час):
    0 * * * * cd /Users/leshokefa/Developer/ai-agent && uv run watch.py

Ако е зададена NTFY_TOPIC в .env, новите обяви се пращат и като известие през ntfy.sh.
Прагът за "изгодна" е WATCH_MIN_SCORE (по подразбиране 20).
Обявите се отбелязват като видени само след успешна доставка.
"""

from imotko import search
from imotko.notify import default_notifier


def main() -> None:
    reports = search.check_saved_searches()
    texts = [report["text"] for report in reports]

    print("\n\n".join(texts) or "Няма нови обяви.")
    notifier = default_notifier()
    if notifier:
        notifier.notify(texts)

    # чак след успешна доставка, иначе при грешка обявите биха се изгубили
    search.mark_as_delivered(reports)


if __name__ == "__main__":
    main()
