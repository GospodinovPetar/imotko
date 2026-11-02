"""Търсене: филтриране, премахване на повторения, оценка и текст за отговора.

Модулът не знае нищо за агента. Обявите са речници, които връща imot_scraper.
"""

import os
import statistics
import time
from dataclasses import dataclass
from itertools import batched
from urllib.parse import quote_plus

import imot_scraper as scraper
import imot_store as store

MIN_LISTINGS_FOR_DISTRICT_MEDIAN = 5
MAX_DETAIL_CHECKS = 25  # толкова обяви най-много проверяваме в детайли (по една заявка на обява)


# Белези, приоритети и филтри

@dataclass(frozen=True)
class Feature:
    """Белег от страницата на обявата (асансьор, паркинг...)."""
    field: str
    label: str
    required_filter: str | None
    points: int
    priority_points: int


# Ключът е думата, с която потребителят го посочва като приоритет
FEATURES = {
    "асансьор": Feature("has_elevator", "асансьор", "require_elevator", points=4, priority_points=6),
    "паркинг": Feature("has_parking", "паркинг", "require_parking", points=3, priority_points=7),
    "акт16": Feature("has_act16", "акт 16", None, points=2, priority_points=5),
    "обзаведен": Feature("furnished", "обзаведен", "require_furnished", points=0, priority_points=6),
}

PRIORITIES = set(FEATURES) | {"тухла", "ново", "намаление"}
PRIORITY_SYNONYMS = {"новострой": "ново", "акт": "акт16", "спаднацена": "намаление"}

FILTERS = {
    "max_price_eur", "min_price_eur", "min_area_m2", "max_area_m2", "district", "construction",
    "exclude_first_floor", "exclude_last_floor", "exclude_in_construction",
    "require_elevator", "require_parking", "require_furnished",
}


def _normalize_priorities(priorities: list[str] | None) -> set[str]:
    """["Паркинг", "ново-строй"] -> {"паркинг", "ново"}. Непознат приоритет е грешка."""
    normalized = set()
    for priority in priorities or []:
        word = priority.strip().lower().replace(" ", "").replace("-", "")
        normalized.add(PRIORITY_SYNONYMS.get(word, word))

    unknown = normalized - PRIORITIES
    if unknown:
        raise ValueError(
            f"Непознат приоритет: {', '.join(sorted(unknown))}. Възможни: {', '.join(sorted(PRIORITIES))}."
        )
    return normalized


def _validate_filters(filters: dict) -> None:
    unknown = set(filters) - FILTERS
    if unknown:
        raise ValueError(f"Непознат филтър: {', '.join(sorted(unknown))}.")

    construction = filters.get("construction")
    if construction and construction.lower() not in scraper.CONSTRUCTION_TYPES:
        raise ValueError(f"Строителство трябва да е едно от: {', '.join(scraper.CONSTRUCTION_TYPES)}.")


# Подредба

def _newest_first(listing: dict):
    # при еднаква дата по-високата оценка е първа; обявите без дата са накрая
    return (listing.get("created") or 0, listing["score"])


def _best_score_first(listing: dict):
    return listing["score"]


SORT_ORDERS = {
    "date_created": _newest_first,
    "score": _best_score_first,
}


# Пазар, повторения, етажи

def median_prices(listings: list[dict]) -> tuple[float, dict[str, float]]:
    """Медианна цена на кв.м за града и за всеки квартал с достатъчно обяви."""
    city_prices = []
    prices_by_district: dict[str, list[float]] = {}

    for listing in listings:
        price = listing["eur_per_m2"]
        if not price:
            continue
        city_prices.append(price)
        prices_by_district.setdefault(listing["district"], []).append(price)

    city_median = statistics.median(city_prices) if city_prices else 0.0

    district_medians = {}
    for district, prices in prices_by_district.items():
        if len(prices) >= MIN_LISTINGS_FOR_DISTRICT_MEDIAN:
            district_medians[district] = statistics.median(prices)

    return city_median, district_medians


def remove_duplicates(listings: list[dict]) -> list[dict]:
    """Един имот често е качен от няколко агенции. Оставяме обявата с най-ниската цена.

    Еднакви са обявите в същия квартал, със същата квадратура и на същия етаж.
    """
    same_property: dict[tuple, list[dict]] = {}
    cannot_compare = []

    for listing in listings:
        if listing["area_m2"] is None or listing["floor"] is None:
            cannot_compare.append(listing)
            continue
        key = (listing["district"], round(listing["area_m2"]), listing["floor"], listing["floors_total"])
        same_property.setdefault(key, []).append(listing)

    unique = []
    for copies in same_property.values():
        cheapest = min(copies, key=lambda listing: listing["price_eur"])
        cheapest["duplicates"] = len(copies) - 1
        unique.append(cheapest)

    return unique + cannot_compare


def _is_low_floor(listing: dict) -> bool:
    """Първи етаж, партер или сутерен."""
    floor = listing.get("floor")
    return floor is not None and floor <= 1


def _is_top_floor(listing: dict) -> bool:
    floors_total = listing.get("floors_total")
    return bool(floors_total) and listing.get("floor") == floors_total


def days_since_created(listing: dict) -> int | None:
    created = listing.get("created")
    if not created:
        return None
    days = int((time.time() - created) // 86400)
    # часовникът на сайта може да е малко напред спрямо нашия
    return max(0, days)


# Оценка и филтри

def score_listing(listing: dict, reference_price: float, filters: dict, priorities: set[str]) -> tuple[float, list[str]]:
    """Колко изгодна е обявата: по-високо е по-добре. Връща (оценка, причини).

    Полетата от страницата на обявата липсват, докато тя не е свалена, и тогава не дават точки.
    """
    points = 0.0
    reasons = []

    # Цена спрямо пазара (квартала или града): до +/- 50 точки
    price = listing["eur_per_m2"]
    if price and reference_price:
        percent_below_market = (reference_price - price) / reference_price * 100
        percent_below_market = max(-50.0, min(50.0, percent_below_market))
        points += percent_below_market
        if percent_below_market >= 0:
            reasons.append(f"под пазара с {percent_below_market:.0f}%")
        else:
            reasons.append(f"над пазара с {-percent_below_market:.0f}%")

    # Етаж
    if _is_low_floor(listing) or _is_top_floor(listing):
        points -= 5
        reasons.append("първи/последен етаж")

    # Сграда: в строеж или нова
    year_built = listing.get("year_built")
    current_year = time.localtime().tm_year
    if listing.get("in_construction") or (year_built and year_built > current_year):
        points += 5 if "ново" in priorities else -10
        reasons.append("в строеж")
    elif year_built and year_built >= 2000:
        points += 10 if "ново" in priorities else 5
        reasons.append(f"сграда от {year_built}")

    # Белези от страницата на обявата
    for priority_name, feature in FEATURES.items():
        feature_points = feature.points
        if priority_name in priorities:
            feature_points += feature.priority_points
        if listing.get(feature.field) and feature_points:
            points += feature_points
            reasons.append(feature.label)

    if listing.get("has_elevator") is False and (listing.get("floors_total") or 0) >= 6:
        points -= 6
        reasons.append("без асансьор на висока сграда")

    if listing.get("construction") == "тухла":
        points += 8 if "тухла" in priorities else 2

    # Намалена цена (от историята)
    price_change = listing.get("price_change_pct")
    if price_change is not None and price_change < 0:
        drop_points = min(10, -price_change)
        if "намаление" in priorities:
            drop_points *= 2
        points += drop_points
        reasons.append(f"цената е паднала с {-price_change:.1f}%")

    # Добре под бюджета или над желаната квадратура
    max_price = filters.get("max_price_eur")
    if max_price and listing["price_eur"] <= max_price * 0.9:
        points += 3

    min_area = filters.get("min_area_m2")
    if min_area and listing["area_m2"] and listing["area_m2"] >= min_area * 1.15:
        points += 3

    # Нова обява
    age = days_since_created(listing)
    if age is not None:
        if age <= 3:
            freshness_points = 8
        elif age <= 7:
            freshness_points = 5
        elif age <= 14:
            freshness_points = 2
        else:
            freshness_points = 0

        if freshness_points:
            points += freshness_points
            reasons.append(f"нова обява ({_days_text(age)})")

    return round(points, 1), reasons


def _passes_basic_filters(listing: dict, filters: dict) -> bool:
    """Филтрите, които се проверяват още от списъка с обяви."""
    price = listing["price_eur"]
    area = listing["area_m2"]

    if filters.get("max_price_eur") and price > filters["max_price_eur"]:
        return False
    if filters.get("min_price_eur") and price < filters["min_price_eur"]:
        return False
    if filters.get("min_area_m2") and (area or 0) < filters["min_area_m2"]:
        return False
    if filters.get("max_area_m2") and area and area > filters["max_area_m2"]:
        return False

    district = filters.get("district")
    if district:
        searchable_text = f"{listing['location']} {listing['info']}".lower()
        if district.lower() not in searchable_text:
            return False

    return True


def _passes_detail_filters(listing: dict, filters: dict) -> bool:
    """Филтрите, за които трябва страницата на обявата. Неизвестно поле не минава изискване."""
    if filters.get("exclude_first_floor") and _is_low_floor(listing):
        return False
    if filters.get("exclude_last_floor") and _is_top_floor(listing):
        return False
    if filters.get("exclude_in_construction") and listing.get("in_construction"):
        return False

    construction = filters.get("construction")
    if construction and listing.get("construction") != construction.lower():
        return False

    for feature in FEATURES.values():
        is_required = feature.required_filter and filters.get(feature.required_filter)
        if is_required and not listing.get(feature.field):
            return False

    return True

# Търсене


def _scan_market(city: str, rooms: str, deal: str, pages: int):
    """Сваля обявите, записва цените им в историята и смята медианите.

    Връща (обяви, грешки при свалянето, медиана за града, медиани по квартали).
    """
    listings, errors = scraper.fetch_listings(deal, city, rooms, pages)
    store.record_prices(listings, deal)
    city_median, district_medians = median_prices(listings)
    return listings, errors, city_median, district_medians


def run_search(
    city: str,
    rooms: str,
    deal: str,
    pages: int,
    top: int,
    priorities: list[str] | None = None,
    order_by: str = "date_created",
    **filters,
) -> dict:
    """Търси, оценява и връща {'results': [най-добрите обяви], 'meta': {броячи и грешки}}.

    Хвърля ValueError при невалиден филтър, преди да е свалена каквато и да е страница.
    """
    sort_key = SORT_ORDERS.get(order_by)
    if sort_key is None:
        raise ValueError(f"order_by трябва да е едно от: {', '.join(SORT_ORDERS)}.")
    _validate_filters(filters)
    priorities = _normalize_priorities(priorities)

    listings, errors, city_median, district_medians = _scan_market(city, rooms, deal, pages)

    # 1. Филтри от списъка, премахване на повторения, първа оценка
    candidates = remove_duplicates([listing for listing in listings if _passes_basic_filters(listing, filters)])

    for listing in candidates:
        if listing["district"] in district_medians:
            listing["ref_ppm"] = district_medians[listing["district"]]
            listing["ref_kind"] = "квартал"
        else:
            listing["ref_ppm"] = city_median
            listing["ref_kind"] = "град"
        listing["score"], listing["reasons"] = score_listing(listing, listing["ref_ppm"], filters, priorities)

    candidates.sort(key=sort_key, reverse=True)

    # 2. Детайлна проверка на най-обещаващите
    matching, detail_checked = _check_details(candidates, filters, top, order_by)

    # 3. Окончателна оценка с данните от страниците
    for listing in matching:
        listing["score"], listing["reasons"] = score_listing(listing, listing["ref_ppm"], filters, priorities)
    matching.sort(key=sort_key, reverse=True)

    return {
        "results": matching[:top],
        "meta": {
            "deal": deal,
            "scanned": len(listings),
            "after_filter": len(candidates),
            "detail_checked": detail_checked,
            "after_detail": len(matching),
            "city_median_ppm": city_median,
            "errors": errors,
        },
    }


def _check_details(candidates: list[dict], filters: dict, top: int, order_by: str) -> tuple[list[dict], int]:
    """Сваля страниците на кандидатите и връща (минаващите детайлните филтри, колко са проверени).

    Всяка обява струва една заявка, затова проверяваме най-много MAX_DETAIL_CHECKS.
    По дата редът е известен предварително: проверяваме на групи и спираме, щом има достатъчно.
    По оценка детайлите променят реда, затова проверяваме всички кандидати наведнъж.
    """
    to_check = candidates[:max(MAX_DETAIL_CHECKS, top * 3)]

    if order_by == "score":
        group_size = max(len(to_check), 1)
    else:
        group_size = scraper.MAX_PARALLEL_REQUESTS

    matching = []
    checked = 0
    for group in batched(to_check, group_size):
        scraper.add_details(group)
        checked += len(group)

        for listing in group:
            if _passes_detail_filters(listing, filters):
                matching.append(listing)

        if len(matching) >= top:
            break

    return matching, checked


# Запазени търсения

def check_saved_searches() -> list[dict]:
    """Пуска всяко запазено търсене и връща [{name, text, new_ids}] за тези с нови обяви или с грешка.

    Нова е обява, която не е показвана досега и има оценка поне WATCH_MIN_SCORE.
    Тук нищо не се отбелязва като видяно: извикващият вика mark_as_delivered,
    след като е показал или изпратил текста.
    """
    min_score = float(os.getenv("WATCH_MIN_SCORE", "20"))
    reports = []

    for saved in store.list_searches():
        name = saved["name"]
        query = {"pages": 3, "top": 10, **saved["params"]}  # по-старите търсения нямат pages и top

        try:
            result = run_search(**query)
        except Exception as error:
            # една счупена заявка или мрежова грешка не бива да спира останалите търсения
            reports.append({"name": name, "text": f"[{name}] грешка: {error}", "new_ids": []})
            continue

        new_listings = []
        for listing in result["results"]:
            if listing["id"] not in saved["seen"] and listing["score"] >= min_score:
                new_listings.append(listing)

        if new_listings:
            text = f"== {name} ==\n" + format_results({**result, "results": new_listings})
        elif result["meta"]["errors"] and not result["results"]:
            text = f"[{name}] imot.bg не отговори: " + "; ".join(result["meta"]["errors"][:3])
        else:
            continue

        reports.append({"name": name, "text": text, "new_ids": [listing["id"] for listing in new_listings]})

    return reports


def mark_as_delivered(reports: list[dict]) -> None:
    for report in reports:
        store.mark_seen(report["name"], report["new_ids"])


# Текст

def _days_text(days: int) -> str:
    return f"{days} ден" if days == 1 else f"{days} дни"


def format_price_per_m2(value: float, deal: str) -> str:
    """При наем цената на кв.м е няколко евро и има нужда от знак след запетаята."""
    is_rent = scraper.DEALS.get(deal.strip().lower()) == "naemi"
    return f"{value:.1f}" if is_rent else f"{value:.0f}"


def _floor_text(listing: dict) -> str:
    floor = listing.get("floor")
    floors_total = listing.get("floors_total")
    if floor is None or not floors_total:
        return "?"

    floor_names = {number: name for name, number in scraper.NAMED_FLOORS.items()}
    return f"{floor_names.get(floor, floor)}/{floors_total}"


def _map_link(listing: dict) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(listing["location"])


def _nothing_found_text(errors: list[str]) -> str:
    return "Не са намерени обяви. " + "; ".join(errors)


def format_results(result: dict) -> str:
    meta = result["meta"]
    listings = result["results"]
    deal = meta["deal"]

    if not meta["scanned"]:
        return _nothing_found_text(meta["errors"])

    header = (
        f"Прегледани {meta['scanned']} обяви → {meta['after_filter']} след филтър → "
        f"от {meta['detail_checked']} проверени в детайли {meta['after_detail']} отговарят на всички условия. "
        f"Медиана за града: {format_price_per_m2(meta['city_median_ppm'], deal)} €/кв.м."
    )
    if not listings:
        return header + " Нито една не отговаря на всички условия."

    lines = [header, ""]
    for number, listing in enumerate(listings, 1):
        lines.append(_format_listing(number, listing, deal))
    return "\n".join(lines)


def _format_listing(number: int, listing: dict, deal: str) -> str:
    features = []
    if listing.get("construction"):
        features.append(listing["construction"])
    for feature in FEATURES.values():
        if listing.get(feature.field):
            features.append(feature.label)

    price_per_m2 = format_price_per_m2(listing["eur_per_m2"], deal) if listing["eur_per_m2"] else "?"
    reference = format_price_per_m2(listing["ref_ppm"], deal)

    extra = ""
    age = days_since_created(listing)
    if age == 0:
        extra += " | днес"
    elif age is not None:
        extra += f" | преди {_days_text(age)}"
    if listing.get("price_change_pct"):
        extra += f" | от {listing['first_price']:,.0f} € ({listing['price_change_pct']:+.1f}%)"
    if listing.get("duplicates"):
        extra += f" (+{listing['duplicates']} дубликата)"

    first_line = (
        f"{number}. {listing['title']} - {listing['location']} | {listing['price_eur']:,.0f} € | "
        f"{listing['area_m2'] or '?'} кв.м | {price_per_m2} €/кв.м "
        f"(реф. {listing['ref_kind']}: {reference}) | етаж {_floor_text(listing)} | "
        f"оценка {listing['score']}{extra}"
    )
    second_line = f"   {', '.join(features) or '-'}; защо: {'; '.join(listing['reasons']) or '-'}"

    return "\n".join([
        first_line,
        second_line,
        f"   {listing['url']}",
        f"   карта: {_map_link(listing)}",
    ])


def district_prices_table(city: str, rooms: str, deal: str, pages: int) -> str:
    """Медианната цена на кв.м по квартали, от най-евтиния към най-скъпия."""
    listings, errors, city_median, district_medians = _scan_market(city, rooms, deal, pages)
    city_text = format_price_per_m2(city_median, deal)

    if not listings:
        return _nothing_found_text(errors)
    if not district_medians:
        return f"Малко обяви за сравнение по квартали. Медиана за града: {city_text} €/кв.м."

    lines = [f"Медиана за града: {city_text} €/кв.м ({len(listings)} обяви). По квартали (от най-евтин):"]
    for district, median in sorted(district_medians.items(), key=lambda item: item[1]):
        lines.append(f"- {district}: {format_price_per_m2(median, deal)} €/кв.м")
    return "\n".join(lines)
