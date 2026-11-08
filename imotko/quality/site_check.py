"""Проверка, че imot.bg още изглежда така, както го чете imotko/sources/imot_bg.py.

Пускане: uv run python -m imotko.quality.site_check
Сваля една страница с обяви и страниците на първите няколко обяви. Връща код 1, ако нещо не се чете
(сменен HTML) или сайтът не отговаря. GitHub Actions я пуска всеки ден (.github/workflows/site-check.yml).
"""

import sys

import requests

from imotko.sources import imot_bg

MIN_LISTINGS = 5  # на страница има ~40; под 5 значи, че обявите вече не се намират в HTML-а
DETAILS_TO_CHECK = 3
LIST_FIELDS = ("price_eur", "area_m2", "district", "floor", "created")
DETAIL_FIELDS = ("floors_total", "construction", "description", "photos")


def problems(listings: list[dict], errors: list[str], details: list[dict]) -> list[str]:
    """Какво не се чете. listings са от страницата със списъка, details са parse_detail на първите обяви."""
    if errors:
        return [f"imot.bg не отговаря: {'; '.join(errors)}"]
    if len(listings) < MIN_LISTINGS:
        return [f"от страницата с обяви се четат само {len(listings)} обяви: HTML-ът на списъка е сменен"]

    found = []
    for field in LIST_FIELDS:
        with_field = sum(1 for listing in listings if listing.get(field) not in (None, ""))
        if with_field < len(listings) / 2:
            found.append(f"списък: полето {field} се чете само в {with_field} от {len(listings)} обяви")
    for field in DETAIL_FIELDS:
        if not any(detail.get(field) for detail in details):
            found.append(f"страница на обява: полето {field} не се чете в нито една от {len(details)} обяви")
    return found


def main() -> int:
    try:
        listings, errors = imot_bg.fetch_listings("продажба", "София", "2", 1)
        details = [imot_bg.parse_detail(imot_bg.fetch_html(listing["url"])) for listing in listings[:DETAILS_TO_CHECK]]
    except (requests.RequestException, ValueError) as error:
        listings, errors, details = [], [str(error)], []

    found = problems(listings, errors, details)
    for problem in found:
        print(f"✗ {problem}")
    if not found:
        print(f"✓ imot.bg: {len(listings)} обяви и {len(details)} страници на обяви се четат както трябва")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
