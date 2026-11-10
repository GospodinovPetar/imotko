"""Проверява запазените търсения и показва само новите изгодни обяви.

Пускане по график (cron, всеки час):
    0 * * * * cd /Users/leshokefa/Developer/ai-agent && uv run watch.py

Ако е зададена NTFY_TOPIC в .env, новите обяви се пращат и като известие през ntfy.sh.
Прагът за "изгодна" е WATCH_MIN_SCORE (по подразбиране 20).
Обявите се отбелязват като видени само след успешна доставка.
"""

from imotko import store
from imotko.geo import shops, transit
from imotko.notify import default_notifier
from imotko.quality import checks
from imotko.search import saved

CHECKS_EVERY_SECONDS = 7 * 86400
LAST_CHECK_KEY = "checks|last_run"


def refresh_chain_stores() -> None:
    """Магазините в градовете на запазените търсения, веднъж в месеца. Търсенето само чете от кеша."""
    cities = set()
    for saved_search in store.list_searches():
        city = saved_search["params"].get("city")
        if city:
            cities.add(city)

    for city in sorted(cities):
        if not shops.chains_need_refresh(city):
            continue
        if shops.refresh_chains(city):
            print(f"Магазини в {city}: обновени.")
        else:
            print(f"Магазини в {city}: Overpass не отговори, ще опитам пак.")


def weekly_tool_checks() -> list[str] | None:
    """Проверка на инструментите срещу проверените факти, веднъж седмично. Връща текст само при грешка,
    None, ако още не е време. main я отбелязва като пусната чак след доставката.
    """
    last_run = store.get_place(LAST_CHECK_KEY)
    if last_run is not None:
        _, seconds_since_last_run = last_run
        if seconds_since_last_run < CHECKS_EVERY_SECONDS:
            return None

    results = checks.run_checks()
    report = checks.format_report(results)
    print(report)

    if all(result["ok"] for result in results):
        return []
    return ["Имотко: някои инструменти връщат неверни данни.\n" + report]


def run_safely(task, fallback):
    try:
        return task()
    except Exception as error:
        print(f"{task.__name__}: {type(error).__name__}: {error}")
        return fallback


def main() -> None:
    run_safely(refresh_chain_stores, None)
    run_safely(transit.refresh_feeds, [])
    check_texts = run_safely(weekly_tool_checks, None)
    reports = saved.check_saved_searches()
    report_texts = [report["text"] for report in reports]
    texts = (check_texts or []) + report_texts

    print("\n\n".join(texts) or "Няма нови обяви.")
    notifier = default_notifier()
    if notifier:
        notifier.notify(texts)

    # чак след успешна доставка, иначе при грешка обявите и сигналът от проверката биха се изгубили
    saved.mark_as_delivered(reports)
    if check_texts is not None:
        store.save_place(LAST_CHECK_KEY, {"found": True})


if __name__ == "__main__":
    main()
