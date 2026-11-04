"""Текстът за модела: резултатите от търсенето и таблицата с цените по квартали."""

from urllib.parse import quote_plus

from imotko.geo import transit
from imotko.search import scan, scoring
from imotko.sources import source


def format_price_per_m2(value: float, deal: str) -> str:
    """При наем цената на кв.м е няколко евро и има нужда от знак след запетаята."""
    is_rent = source.is_rent(deal)
    return f"{value:.1f}" if is_rent else f"{value:.0f}"


def _floor_text(listing: dict) -> str:
    floor = listing.get("floor")
    floors_total = listing.get("floors_total")
    if floor is None or not floors_total:
        return "?"

    floor_names = {number: name for name, number in source.named_floors.items()}
    floor_name = floor_names.get(floor, floor)
    return f"{floor_name}/{floors_total}"


def _map_link(listing: dict) -> str:
    query = listing["location"]
    if listing.get("street"):
        query = f"{listing['street']}, {query}"
    # кирилицата остава некодирана: линкът работи и е няколко пъти по-кратък за модела
    encoded = "".join(char if ord(char) > 127 else quote_plus(char) for char in query)
    return "https://www.google.com/maps/search/?api=1&query=" + encoded


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
    for note in meta.get("notes", []):
        header += f"\nБележка: {note}."
    if not listings:
        return header + "\nНито една не отговаря на всички условия."

    lines = [header, ""]
    for number, listing in enumerate(listings, 1):
        lines.append(_format_listing(number, listing, deal))
    return "\n".join(lines)


def _format_listing(number: int, listing: dict, deal: str) -> str:
    price_per_m2 = format_price_per_m2(listing["eur_per_m2"], deal) if listing["eur_per_m2"] else "?"
    reference = format_price_per_m2(listing["ref_ppm"], deal)

    first_line = (
        f"{number}. {listing['title']} - {_address_text(listing)} | {listing['price_eur']:,.0f} € | "
        f"{listing['area_m2'] or '?'} кв.м | {price_per_m2} €/кв.м "
        f"(реф. {listing['ref_kind']}: {reference}) | етаж {_floor_text(listing)} | "
        f"оценка {listing['score']}{_extra_text(listing)}"
    )
    features_text = ", ".join(_feature_names(listing)) or "-"
    reasons_text = "; ".join(listing["reasons"]) or "-"
    second_line = f"   {features_text}; защо: {reasons_text}"

    lines = [first_line, second_line]
    lines += _place_lines(listing)
    for warning in listing.get("warnings", []):
        lines.append(f"   ⚠ {warning}")
    lines.append(f"   {listing['url']}")
    lines.append(f"   карта: {_map_link(listing)}")
    return "\n".join(lines)


def _address_text(listing: dict) -> str:
    address = listing["location"]
    if listing.get("street"):
        address += f", {listing['street']}"
    return address


def _feature_names(listing: dict) -> list[str]:
    names = []
    if listing.get("construction"):
        names.append(listing["construction"])
    for feature in scoring.FEATURES.values():
        if listing.get(feature.field):
            names.append(feature.label)
    return names


def _extra_text(listing: dict) -> str:
    extra = ""

    age = scoring.days_since_created(listing)
    if age == 0:
        extra += " | днес"
    elif age is not None:
        extra += f" | преди {scoring.days_text(age)}"

    if listing.get("price_change_pct"):
        extra += f" | от {listing['first_price']:,.0f} € ({listing['price_change_pct']:+.1f}%)"

    if listing.get("duplicates"):
        extra += f" (+{listing['duplicates']} дубликата"
        if listing.get("duplicates_max_price", 0) > listing["price_eur"] * 1.02:
            extra += f", другаде до {listing['duplicates_max_price']:,.0f} €"
        extra += ")"

    return extra


def _place_lines(listing: dict) -> list[str]:
    """Магазини и време до целта, винаги с точността на мярката."""
    lines = []

    chains = listing.get("chains")
    if chains:
        lines.append(_chains_line(chains))

    chain_near_street = listing.get("chain_near_street")
    if chain_near_street:
        chain, distance_km = chain_near_street
        lines.append(f"   най-близо до улицата: {chain} ~{distance_km * 1000:.0f} м (по улица)")

    for commute in _commutes_of(listing):
        lines.append(_commute_line(commute))

    return lines


def _chains_line(chains: dict) -> str:
    if chains["area"] == "граница":
        where = "в квартала"
    else:
        where = f"в {chains['area']} от точката на квартала"

    if chains["counts"]:
        most_first = sorted(chains["counts"].items(), key=lambda item: -item[1])
        names = [f"{chain} ×{count}" if count > 1 else chain for chain, count in most_first]
        shops_text = ", ".join(names)
    else:
        shops_text = "няма големи вериги"

    return f"   магазини {where}: {shops_text}"


def _commutes_of(listing: dict) -> list[dict]:
    if listing.get("commutes"):
        return listing["commutes"]
    if listing.get("commute"):
        return [listing["commute"]]
    return []


def _commute_line(commute: dict) -> str:
    time_range = ""
    if "low" in commute:
        time_range = f"{commute['low']:.0f}-{commute['high']:.0f} мин, "

    line = (
        f"   до {commute.get('label', commute['target'])}: ~{commute['minutes']:.0f} мин с кола "
        f"({time_range}по {commute['precision']})"
    )

    transit_route = commute.get("transit")
    if transit_route and transit_route.get("ok"):
        line += f"; транспорт {transit.short_text(transit_route)}"
    return line


def district_prices_table(city: str, rooms: str, deal: str, pages: int) -> str:
    """Медианната цена на кв.м по квартали, от най-евтиния към най-скъпия."""
    listings, errors, city_median, district_medians = scan.scan_market(city, rooms, deal, pages)
    city_text = format_price_per_m2(city_median, deal)

    if not listings:
        return _nothing_found_text(errors)
    if not district_medians:
        return f"Малко обяви за сравнение по квартали. Медиана за града: {city_text} €/кв.м."

    lines = [f"Медиана за града: {city_text} €/кв.м ({len(listings)} обяви). По квартали (от най-евтин):"]
    cheapest_first = sorted(district_medians.items(), key=lambda item: item[1])
    for district, median in cheapest_first:
        lines.append(f"- {district}: {format_price_per_m2(median, deal)} €/кв.м")
    return "\n".join(lines)
