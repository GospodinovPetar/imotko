"""Запазените търсения: проверка за нови обяви и отбелязване на показаните."""

import os

from imotko import store
from imotko.search import formatting, scan


def check_saved_searches() -> list[dict]:
    """Пуска всяко запазено търсене и връща [{name, text, new_ids}] за тези с нови обяви или с грешка.

    Нова е обява, която не е показвана досега и има оценка поне WATCH_MIN_SCORE.
    Тук нищо не се отбелязва като видяно: извикващият вика mark_as_delivered,
    след като е показал или изпратил текста.
    """
    min_score = float(os.getenv("WATCH_MIN_SCORE", "20"))
    reports = []

    for saved_search in store.list_searches():
        name = saved_search["name"]
        query = {"pages": 3, "top": 10, **saved_search["params"]}  # по-старите търсения нямат pages и top

        try:
            result = scan.run_search(**query)
        except Exception as error:
            # една счупена заявка или мрежова грешка не бива да спира останалите търсения
            reports.append({"name": name, "text": f"[{name}] грешка: {error}", "new_ids": []})
            continue

        new_listings = _new_listings(result["results"], saved_search["seen"], min_score)
        errors = result["meta"]["errors"]

        if new_listings:
            text = f"== {name} ==\n" + formatting.format_results({**result, "results": new_listings})
        elif errors and not result["results"]:
            text = f"[{name}] imot.bg не отговори: " + "; ".join(errors[:3])
        else:
            continue

        new_ids = [listing["id"] for listing in new_listings]
        reports.append({"name": name, "text": text, "new_ids": new_ids})

    return reports


def _new_listings(listings: list[dict], seen_ids, min_score: float) -> list[dict]:
    new_listings = []
    for listing in listings:
        is_unseen = listing["id"] not in seen_ids
        if is_unseen and listing["score"] >= min_score:
            new_listings.append(listing)
    return new_listings


def mark_as_delivered(reports: list[dict]) -> None:
    for report in reports:
        store.mark_seen(report["name"], report["new_ids"])
