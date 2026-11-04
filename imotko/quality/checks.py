"""Проверка на инструментите срещу проверени факти (quality/ground_truth.json и поправките на потребителя).

Пускане: uv run python -m imotko.quality.checks
Връща код 1, ако нещо се разминава. watch.py го пуска веднъж седмично и праща известие при грешка.
"""

import sys

from imotko import store
from imotko.geo import driving, geocode, transit

ROUTE_TOLERANCE = 0.25
# нашето време включва чакането на спирката, а Google показва най-добрия курс
TRANSIT_TOLERANCE = 0.30


def run_checks() -> list[dict]:
    """[{"check": какво, "ok": bool, "details": подробности}] за всеки проверен факт."""
    truth = driving.load_ground_truth()
    results = []

    for route in driving.measured_routes():
        results.append(_check_route(route))
    for route in truth.get("routes", []):
        if route.get("real_transit_minutes"):
            results.append(_check_transit(route))
    for district in truth.get("districts", []):
        results.append(_check_district(district))
    for street in truth.get("streets", []):
        results.append(_check_street(street))
    for place in truth.get("places", []):
        results.append(_check_place(place))
    for address in truth.get("addresses", []):
        results.append(_check_address(address))

    return results


def _result(check: str, ok: bool, details: str) -> dict:
    return {"check": check, "ok": ok, "details": details}


def _within(value: float, expected: float) -> bool:
    return abs(value - expected) <= expected * ROUTE_TOLERANCE


def _name_contains(found: dict, expected_text: str) -> bool:
    return expected_text.lower() in found["name"].lower()


def _check_route(route: dict) -> dict:
    """Времето с кола в града срещу реалното. Ползва точните точки на измерването, ако ги има,
    за да не се смесва грешка в маршрута с грешка в намирането на мястото.
    """
    name = f"път {route['from']} -> {route['to']}"
    start = route.get("from_point") or geocode.locate_any(route["from"], route["city"])
    end = route.get("to_point") or geocode.locate_any(route["to"], route["city"])
    if start is None or end is None:
        return _result(name, False, "мястото не е намерено в картата")

    drive = driving.drive(start, end)
    if drive is None:
        return _result(name, False, "услугата за маршрути не отговаря")

    city_time = driving.city_drive_time(drive)
    details = f"~{city_time['typical']:.0f} мин (реално {route['real_minutes']:g}), {drive['km']:.1f} км"
    city_time_ok = _within(city_time["typical"], route["real_minutes"])
    osrm_time_ok = _within(drive["minutes"], route["osrm_minutes"])
    return _result(name, city_time_ok and osrm_time_ok, details)


def _check_transit(route: dict) -> dict:
    name = f"транспорт {route['from']} -> {route['to']}"
    result = transit.route(route["city"], route["from_point"], route["to_point"])
    if not result["ok"]:
        return _result(name, False, result["reason"])

    real_minutes = route["real_transit_minutes"]
    lines_text = " → ".join(result["lines"]) or "пеша"
    details = f"~{result['minutes']:.0f} мин (реално {real_minutes:g}), {lines_text}"
    ok = abs(result["minutes"] - real_minutes) <= real_minutes * TRANSIT_TOLERANCE
    return _result(name, ok, details)


def _check_district(district: dict) -> dict:
    name = f"квартал {district['name']}, {district['city']}"
    found = geocode.locate(district["name"], district["city"], "district", use_cache=False)
    if found is None:
        return _result(name, False, "не е намерен")

    problems = []
    if district.get("expect_area") and not found["area"]:
        problems.append("без граница")
    if district.get("name_contains") and not _name_contains(found, district["name_contains"]):
        problems.append("друго място")

    details = f"{found['name'][:80]} {'; '.join(problems)}".strip()
    return _result(name, not problems, details)


def _check_street(street: dict) -> dict:
    name = f"улица {street['street']} в {street['district']}"
    district = geocode.locate(street["district"], street["city"], "district", use_cache=False)
    found = geocode.locate(street["street"], street["city"], "street", near=district, use_cache=False)
    if found is None:
        return _result(name, False, "не е намерена")

    ok = _name_contains(found, street["name_contains"]) and not found.get("far_from_district")
    return _result(name, ok, found["name"][:80])


def _check_place(place: dict) -> dict:
    name = f"място {place['name']}"
    found = geocode.locate(place["name"], place["city"], "place", use_cache=False)
    if found is None:
        return _result(name, False, "не е намерено")

    ok = _name_contains(found, place["name_contains"])
    return _result(name, ok, found["name"][:80])


def _check_address(address: dict) -> dict:
    """Свободен текст, както го подава моделът (адрес с номер, име и адрес през запетая)."""
    name = f"адрес {address['address']}"
    found = geocode.locate_any(address["address"], address["city"])
    if found is None:
        return _result(name, False, "не е намерен")

    ok = _name_contains(found, address["name_contains"])
    return _result(name, ok, found["name"][:80])


def format_report(results: list[dict]) -> str:
    passed_count = sum(1 for result in results if result["ok"])
    extra_minutes, measured_count = driving.city_extra_minutes()
    lines = [
        f"Проверка на инструментите: {passed_count} от {len(results)} са верни.",
        f"Време с кола в града: OSRM + {extra_minutes:.1f} мин (по {measured_count} проверени пътувания).",
    ]
    for result in results:
        mark = "✓" if result["ok"] else "✗"
        lines.append(f"{mark} {result['check']}: {result['details']}")
    return "\n".join(lines)


def format_feedback(limit: int = 20) -> str:
    """Последните поправки на потребителя и отговорите с непроверени числа."""
    records = store.list_feedback(limit=limit)
    if not records:
        return "Няма записани поправки или съмнителни отговори."

    lines = ["Последни записи:"]
    for record in records:
        if record["kind"] == "user_report":
            lines.append(f"- поправка: {record['question'][:80]} -> {record['correction'][:80]}")
        else:
            numbers = ", ".join(record["data"].get("numbers", []))
            lines.append(f"- непроверени числа ({numbers}) във въпрос: {record['question'][:80]}")
    return "\n".join(lines)


def main() -> int:
    results = run_checks()
    print(format_report(results))
    print()
    print(format_feedback())
    all_ok = all(result["ok"] for result in results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
