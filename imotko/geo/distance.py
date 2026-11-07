"""Разстояние между места (инструментът distance_between): права линия, кола и градски транспорт."""

from imotko.geo import common, driving, enrich, geocode, transit


WALK_DETOUR = 1.3
WALK_SPEED_KMH = 5


def describe_distance(place_from: str, place_to: str, city: str) -> str:
    """Текст за модела: права линия, пеша, с кола и колко точно е всичко."""
    start = geocode.locate_any(place_from, city)
    end = geocode.locate_any(place_to, city)

    missing_names = []
    if start is None:
        missing_names.append(place_from)
    if end is None:
        missing_names.append(place_to)
    if missing_names:
        return (
            f"Не успях да намеря в картата: {', '.join(missing_names)}. "
            "Опитай с пълното име или с квартала. Не давай разстояние по памет."
        )

    straight_km = common.distance_km(start, end)
    walk_minutes = straight_km * WALK_DETOUR / WALK_SPEED_KMH * 60  # пътят пеша е ~30% по-дълъг от правата линия
    lines = [
        f"От: {start['name']} (точност: {geocode.precision_text(start)})",
        f"До: {end['name']} (точност: {geocode.precision_text(end)})",
        f"Права линия: {straight_km:.1f} км. Пеша: ~{walk_minutes:.0f} мин (оценка).",
    ]

    route = driving.drive(start, end)
    if route:
        lines.append(f"С кола: {driving.drive_text(route)}")
        if driving.unusual_drive(route):
            lines.append("⚠ необичайно време с кола за това разстояние, провери дали местата са намерени правилно")
    else:
        lines.append("С кола: услугата за маршрути не отговаря в момента.")

    lines.append(transit.describe(transit.route(city, start, end)))

    for warning in start["warnings"] + end["warnings"]:
        lines.append(f"⚠ {warning}")
    return "\n".join(lines)


def describe_distances(places_from: list[str], places_to: list[str], city: str) -> str:
    """Няколко места наведнъж (всяко откъде до всяко докъде), с по един кратък ред за двойка.

    Едно извикване вместо много: всяко извикване на инструмент праща целия разговор към модела наново.
    """
    places_from = enrich.commute_targets(places_from)
    places_to = enrich.commute_targets(places_to)
    if len(places_from) == 1 and len(places_to) == 1:
        return describe_distance(places_from[0], places_to[0], city)
    if not places_from or not places_to:
        return "Подай поне едно място откъде и едно докъде."

    found = {}
    for name in places_from + places_to:
        if name not in found:
            found[name] = geocode.locate_any(name, city)

    lines = _found_places_lines(found)
    lines.append("")

    for destination_name in places_to:
        destination = found[destination_name]
        origin_names = [name for name in places_from if found[name] and name != destination_name]
        if destination is None or not origin_names:
            continue

        origins = [found[name] for name in origin_names]
        routes = driving.drive_many(origins, destination)
        for origin_name, origin, route in zip(origin_names, origins, routes):
            pair_text = _pair_text(origin, destination, route, city)
            lines.append(f"{origin_name} → {destination_name}: {pair_text}")

    lines.append("Колата е с обичаен трафик, транспортът - по разписанието в делничен ден сутрин. И двете са приблизителни.")
    return "\n".join(lines)


def _found_places_lines(found: dict) -> list[str]:
    lines = []
    for name, place in found.items():
        if place is None:
            lines.append(f"{name}: не е намерено в картата. Не давай разстояние по памет.")
            continue
        lines.append(f"{name}: {place['name'][:90]} (точност: {geocode.precision_text(place)})")
        for warning in place["warnings"]:
            lines.append(f" ⚠ {warning}")
    return lines


def _pair_text(origin: dict, destination: dict, route: dict | None, city: str) -> str:
    parts = [f"права линия {common.distance_km(origin, destination):.1f} км"]

    if route:
        city_time = driving.city_drive_time(route)
        parts.append(f"кола ~{city_time['typical']:.0f} мин ({city_time['low']:.0f}-{city_time['high']:.0f})")
    else:
        parts.append("кола: няма данни")

    if transit.has_feed(city):
        transit_route = transit.route(city, origin, destination)
        parts.append(f"транспорт {transit.short_text(transit_route)}")

    return "; ".join(parts)
