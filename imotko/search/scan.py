"""Търсенето: сваляне на обявите, филтри, премахване на повторения, проверка в детайли и данни от картата.

Модулът не знае нищо за агента. Обявите са речници, които връща imotko.sources.
"""

import statistics
from itertools import batched

from imotko import store
from imotko.geo import enrich
from imotko.search import facts, requirements, scoring
from imotko.sources import source

MIN_LISTINGS_FOR_DISTRICT_MEDIAN = 5

MAX_DETAIL_CHECKS = 25  # толкова обяви най-много проверяваме в детайли (по една заявка на обява)

FILTERS = {
    "max_price_eur", "max_price_with_parking_eur", "min_price_eur", "min_area_m2", "max_area_m2",
    "district", "exclude_districts", "construction",
    "exclude_first_floor", "exclude_last_floor", "exclude_in_construction",
    "require_elevator", "require_parking", "require_furnished", "require_terrace",
}


def _validate_filters(filters: dict) -> None:
    unknown = set(filters) - FILTERS
    if unknown:
        raise ValueError(f"Непознат филтър: {', '.join(sorted(unknown))}.")

    construction = filters.get("construction")
    if construction and construction.lower() not in source.construction_types:
        raise ValueError(f"Строителство трябва да е едно от: {', '.join(source.construction_types)}.")


def _newest_first(listing: dict):
    # при еднаква дата по-високата оценка е първа; обявите без дата са накрая
    return (listing.get("created") or 0, listing["score"])


def _best_score_first(listing: dict):
    return listing["score"]


SORT_ORDERS = {
    "date_created": _newest_first,
    "score": _best_score_first,
}


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
        property_key = (listing["district"], round(listing["area_m2"]), listing["floor"], listing["floors_total"])
        same_property.setdefault(property_key, []).append(listing)

    unique = []
    for copies in same_property.values():
        prices = [copy["price_eur"] for copy in copies]
        cheapest = min(copies, key=lambda copy: copy["price_eur"])
        cheapest["duplicates"] = len(copies) - 1
        cheapest["duplicates_max_price"] = max(prices)
        unique.append(cheapest)

    return unique + cannot_compare


def remove_duplicates_by_address(listings: list[dict]) -> list[dict]:
    """Същият адрес, същата квадратура и етаж са един имот, дори да е качен от няколко агенции.

    След търсенето в описанията адресът е по-силен ключ от квартала. Остава най-евтината обява.
    Броячите на дубликатите се събират с тези от първото премахване (remove_duplicates).
    """
    same_property: dict[tuple, list[dict]] = {}
    rest = []
    for listing in listings:
        address = listing.get("address_from_description")
        if not address or listing["area_m2"] is None or listing["floor"] is None:
            rest.append(listing)
            continue
        key = (address.lower(), round(listing["area_m2"]), listing["floor"])
        same_property.setdefault(key, []).append(listing)

    unique = []
    for copies in same_property.values():
        cheapest = min(copies, key=lambda copy: copy["price_eur"])
        others = [copy for copy in copies if copy is not cheapest]
        if others:
            cheapest["duplicates"] = cheapest.get("duplicates", 0) + sum(1 + copy.get("duplicates", 0) for copy in others)
            prices = [copy.get("duplicates_max_price", copy["price_eur"]) for copy in copies]
            cheapest["duplicates_max_price"] = max(prices)
        unique.append(cheapest)
    return unique + rest


def _passes_basic_filters(listing: dict, filters: dict) -> bool:
    """Филтрите, които се проверяват още от списъка с обяви."""
    return requirements.passes(requirements.list_page_checks(listing, filters))


def _passes_detail_filters(listing: dict, filters: dict) -> bool:
    """Филтрите, за които трябва страницата на обявата. Неизвестно поле не минава изискване."""
    return requirements.passes(requirements.all_checks(listing, filters))


def _unmatched_district_notes(listings: list[dict], filters: dict) -> list[str]:
    """Квартал, който не съвпада с нито една обява, обикновено е написан различно от imot.bg."""
    notes = []
    for name in requirements.district_names(filters, "district"):
        is_found = any(name.lower() in listing["location"].lower() for listing in listings)
        if not is_found:
            notes.append(f"кварталът \"{name}\" не съвпада с нито една обява (може да е написан различно в imot.bg)")
    return notes


def scan_market(city: str, rooms: str, deal: str, pages: int):
    """Сваля обявите, записва цените им в историята и смята медианите.

    Връща (обяви, грешки при свалянето, медиана за града, медиани по квартали).
    """
    listings, errors = source.fetch_listings(deal, city, rooms, pages)
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
    commute_to: str | list[str] | None = None,
    **filters,
) -> dict:
    """Търси, оценява и връща {'results': [най-добрите обяви], 'meta': {броячи, грешки, бележки}}.

    commute_to са местата, до които потребителят пътува често ("Бизнес парк София"), едно или списък.
    Оценката е по първото.
    Хвърля ValueError при невалиден филтър, преди да е свалена каквато и да е страница.
    """
    sort_key = SORT_ORDERS.get(order_by)
    if sort_key is None:
        raise ValueError(f"order_by трябва да е едно от: {', '.join(SORT_ORDERS)}.")
    _validate_filters(filters)
    priorities = scoring.normalize_priorities(priorities)

    listings, errors, city_median, district_medians = scan_market(city, rooms, deal, pages)

    # Филтри от списъка, премахване на повторения, първа оценка
    passing_listings = [listing for listing in listings if _passes_basic_filters(listing, filters)]
    candidates = remove_duplicates(passing_listings)

    for listing in candidates:
        _set_reference_price(listing, city_median, district_medians)
        _score(listing, filters, priorities)

    candidates.sort(key=sort_key, reverse=True)

    # Детайлна проверка на най-обещаващите
    matching, detail_checked = _check_details(candidates, filters, top, order_by)

    for listing in matching:
        listing["description_facts"] = facts.find_facts(listing.get("description"))

    #  Място: квартал, улица, магазини, време до целта (с кеш; при грешка обявите остават без тях)
    notes = _unmatched_district_notes(listings, filters)
    notes += _add_location_data(matching, city, commute_to)
    matching = remove_duplicates_by_address(matching)

    # Окончателна оценка с всички данни
    for listing in matching:
        _score(listing, filters, priorities)
        listing["warnings"] = scoring.listing_warnings(listing)
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
            "notes": notes,
            "filters": filters,
            "applied": requirements.filters_text(filters, priorities, enrich.commute_targets(commute_to)),
        },
    }


def _set_reference_price(listing: dict, city_median: float, district_medians: dict[str, float]) -> None:
    district = listing["district"]
    if district in district_medians:
        listing["ref_ppm"] = district_medians[district]
        listing["ref_kind"] = "квартал"
    else:
        listing["ref_ppm"] = city_median
        listing["ref_kind"] = "град"


def _score(listing: dict, filters: dict, priorities: list[str]) -> None:
    score, reasons = scoring.score_listing(listing, listing["ref_ppm"], filters, priorities)
    listing["score"] = score
    listing["reasons"] = reasons


def _add_location_data(listings: list[dict], city: str, commute_to: str | list[str] | None) -> list[str]:
    """enrich.add_location_data, което никога не спира търсенето."""
    if not listings:
        return []
    try:
        return enrich.add_location_data(listings, city, commute_to)
    except Exception as error:
        # картата е допълнение: обявите се показват и без нея
        return [f"данните от картата не са достъпни в момента ({type(error).__name__})"]


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
        group_size = source.max_parallel_requests

    matching = []
    checked = 0
    for group in batched(to_check, group_size):
        source.add_details(group)
        checked += len(group)

        for listing in group:
            if _passes_detail_filters(listing, filters):
                matching.append(listing)

        if len(matching) >= top:
            break

    return matching, checked
