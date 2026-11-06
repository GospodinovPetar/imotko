"""Текстът за модела: резултатите от търсенето и таблицата с цените по квартали."""

from urllib.parse import quote_plus

from imotko.geo import stops, transit
from imotko.search import requirements, scan, scoring
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
    if meta.get("applied"):
        header += "\n" + meta["applied"]
    for note in meta.get("notes", []):
        header += f"\nБележка: {note}."
    if not listings:
        return header + "\nНито една не отговаря на всички условия."

    filters = meta.get("filters") or {}
    lines = [header, ""]
    for number, listing in enumerate(listings, 1):
        lines.append(_format_listing(number, listing, deal, filters))
    return "\n".join(lines)


def _format_listing(number: int, listing: dict, deal: str, filters: dict | None = None) -> str:
    price_per_m2 = format_price_per_m2(listing["eur_per_m2"], deal) if listing["eur_per_m2"] else "?"
    reference = format_price_per_m2(listing["ref_ppm"], deal)

    first_line = (
        f"{number}. {listing['title']} - {_address_text(listing)} | {listing['price_eur']:,.0f} € | "
        f"{listing['area_m2'] or '?'} кв.м | {price_per_m2} €/кв.м "
        f"(реф. {listing['ref_kind']}: {reference}) | етаж {_floor_text(listing)} | "
        f"оценка {listing['score']}{_extra_text(listing)}"
    )
    reasons_text = "; ".join(listing["reasons"]) or "-"

    lines = [first_line, *_address_lines(listing), _details_line(listing), *_facts_lines(listing)]
    conditions_line = _conditions_line(listing, filters or {})
    if conditions_line:
        lines.append(conditions_line)
    lines.append(f"   защо тази оценка: {reasons_text}")
    lines += _place_lines(listing)
    for warning in listing.get("warnings", []):
        lines.append(f"   ⚠ {warning}")
    lines.append("   снимки: не са проверени (analyze_photos с линка на обявата)")
    lines.append(f"   {listing['url']}")
    lines.append(f"   карта: {_map_link(listing)}")
    return "\n".join(lines)


def _address_lines(listing: dict) -> list[str]:
    """Адресът, който търсенето е намерило в описанието, и по кой адрес са мерките."""
    address = listing.get("address_from_description")
    if not address:
        return []

    if listing.get("street") != address:
        return [f"   адрес от описанието: {address} - различава се от улицата от обявата, мерките са по нея"]
    return [f"   адрес от описанието: {address} (написан в текста на обявата; мерките до местата са по него)"]


def _facts_lines(listing: dict) -> list[str]:
    """Отоплението, комисионната и другото, което изрично пише в описанието (само ако е намерено)."""
    facts = listing.get("description_facts")
    if not facts:
        return []
    return [f"   от описанието: {'; '.join(facts)}"]


def _address_text(listing: dict) -> str:
    address = listing["location"]
    if listing.get("street"):
        address += f", {listing['street']}"
    return address


def _details_line(listing: dict) -> str:
    """Всеки белег изрично: "да" или "не пише в обявата", никога пропуснат."""
    parts = [f"етаж: {_floor_details(listing)}"]
    for feature in scoring.FEATURES.values():
        value = "да" if listing.get(feature.field) else "не пише в обявата"
        parts.append(f"{feature.label}: {value}")
    parts.append(f"строителство: {listing.get('construction') or 'не е посочено'}")
    if listing.get("year_built"):
        parts.append(f"година: {listing['year_built']}")
    if listing.get("in_construction"):
        parts.append("в строеж")
    return "   " + "; ".join(parts)


def _floor_details(listing: dict) -> str:
    floor_text = _floor_text(listing)
    if floor_text == "?":
        return "не е посочен в обявата"
    return floor_text


def _conditions_line(listing: dict, filters: dict) -> str:
    """Всяко условие на потребителя с ✓ (изпълнено), ✗ (не е) или ? (не е посочено в обявата)."""
    checks = requirements.all_checks(listing, filters)
    if not checks:
        return ""
    return "   условия: " + "; ".join(f"{check.status} {check.text}" for check in checks)


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
    else:
        lines.append("   магазини: няма данни")

    chain_near_street = listing.get("chain_near_street")
    if chain_near_street:
        chain, distance_km = chain_near_street
        lines.append(f"   най-близо до улицата: {chain} ~{distance_km * 1000:.0f} м (по улица)")

    commutes = _commutes_of(listing)
    lines += _stop_lines(listing, commutes)
    for commute in commutes:
        lines.append(_commute_line(commute))

    return lines


def _stop_lines(listing: dict, commutes: list[dict]) -> list[str]:
    """Спирката или метростанцията, написана в описанието, и дали времената са по нея."""
    stop = listing.get("stop")
    if stop:
        stated = f", по описанието на {stop['stated_text']}" if stop.get("stated_text") else ""
        line = f"   спирка от описанието: {stop['mention']}{stated} (намерена в {stop['source']} като \"{stop['name']}\")"
        if any(commute["precision"] == stops.PRECISION for commute in commutes):
            line += "; времената по-долу са от нея, не от адреса на жилището"
        return [line]

    mentions = listing.get("stop_mentions")
    if mentions:
        return [f"   спирка от описанието: {', '.join(mentions)} - не е намерена в картата, времената не са по нея"]
    return []


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
    elif transit_route:
        line += f"; транспорт: няма данни ({transit_route.get('reason', 'грешка')})"
    else:
        line += "; транспорт: няма разписание за града"
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
