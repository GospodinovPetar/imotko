"""Време с кола (OSRM) и калибрирането му по реални пътувания.

Времето с кола в града е времето на OSRM плюс минутите, изгубени на светофари и в движение,
изчислени от проверените пътувания в quality/ground_truth.json и от поправките на потребителя.
"""

import json
import statistics
import time
from itertools import batched
from pathlib import Path

import requests

from imotko import store
from imotko.geo import common

OSRM_URL = "https://router.project-osrm.org"

ROUTE_MAX_AGE = 90 * common.DAY
# „няма път“ се помни за ден: точката може да е намерена грешно и после поправена
NO_ROUTE_MAX_AGE = common.DAY
NO_ROUTE_CODES = ("NoRoute", "NoSegment")
NO_ROUTE = {"no_route": True}

GROUND_TRUTH_FILE = Path(__file__).resolve().parent.parent / "quality" / "ground_truth.json"

DEFAULT_CITY_EXTRA_MINUTES = 6.0

CITY_TIME_SPREAD = 0.25

CALIBRATION_CACHE_SECONDS = 300

OSRM_TABLE_BATCH = 50

_osrm_limit = common.RateLimiter(1.0)


class NoRoute(requests.RequestException):
    """OSRM отговори, но между точките няма път с кола."""


def _osrm(path: str, params: dict) -> dict:
    _osrm_limit.wait()
    response = requests.get(f"{OSRM_URL}{path}", params=params, headers=common.HEADERS, timeout=20)
    # OSRM връща „няма път“ с код 400, затова кодът се гледа преди raise_for_status
    code = _osrm_code(response)
    if code in NO_ROUTE_CODES:
        raise NoRoute(f"OSRM: {code}")

    response.raise_for_status()
    data = response.json()
    if data.get("code") != "Ok":
        raise requests.RequestException(f"OSRM: {data.get('code')}")
    return data


def _osrm_code(response: requests.Response) -> str | None:
    try:
        return response.json().get("code")
    except (ValueError, AttributeError):
        return None


def _osrm_point(point: dict) -> str:
    # OSRM иска дължина,ширина - обратно на Nominatim
    return f"{point['lon']:.6f},{point['lat']:.6f}"


def _route_key(origin: dict, destination: dict) -> str:
    return common.cache_key("route", common.point_key(origin), common.point_key(destination))


def _cached_route(origin: dict, destination: dict) -> tuple[bool, dict | None]:
    """(има ли пресен запис, маршрутът). Маршрутът е None, ако е записано, че няма път."""
    cached = store.get_place(_route_key(origin, destination))
    if cached is None:
        return False, None

    route, age_seconds = cached
    if route.get("no_route"):
        return age_seconds < NO_ROUTE_MAX_AGE, None
    if age_seconds >= ROUTE_MAX_AGE:
        return False, None
    return True, route


def drive(origin: dict, destination: dict) -> dict | None:
    """{"minutes", "km"} с кола без трафик или None."""
    is_cached, cached_route = _cached_route(origin, destination)
    if is_cached:
        return cached_route

    path = f"/route/v1/driving/{_osrm_point(origin)};{_osrm_point(destination)}"
    try:
        data = _osrm(path, {"overview": "false"})
    except NoRoute:
        store.save_place(_route_key(origin, destination), NO_ROUTE)
        return None
    except requests.RequestException:
        return None

    first_route = data["routes"][0]
    route = {"minutes": first_route["duration"] / 60, "km": first_route["distance"] / 1000}
    store.save_place(_route_key(origin, destination), route)
    return route


def drive_many(origins: list[dict], destination: dict) -> list[dict | None]:
    """{"minutes", "km"} с кола от всяка точка до една цел. Една заявка за всички; None, където няма път."""
    routes: list[dict | None] = []
    missing_indexes = []

    for index, origin in enumerate(origins):
        is_cached, cached_route = _cached_route(origin, destination)
        routes.append(cached_route)
        if not is_cached:
            missing_indexes.append(index)

    for batch_indexes in batched(missing_indexes, OSRM_TABLE_BATCH):
        batch_origins = [origins[index] for index in batch_indexes]
        batch_routes = _drive_table(batch_origins, destination)
        if batch_routes is None:
            continue

        for index, origin, route in zip(batch_indexes, batch_origins, batch_routes):
            routes[index] = route
            store.save_place(_route_key(origin, destination), route or NO_ROUTE)

    return routes


def _drive_table(origins: list[dict], destination: dict) -> list[dict | None] | None:
    all_points = [*origins, destination]
    points_text = ";".join(_osrm_point(point) for point in all_points)
    source_indexes = ";".join(str(number) for number in range(len(origins)))
    destination_index = str(len(origins))

    try:
        data = _osrm(
            f"/table/v1/driving/{points_text}",
            {"sources": source_indexes, "destinations": destination_index, "annotations": "duration,distance"},
        )
    except requests.RequestException:
        return None

    routes = []
    for row in range(len(origins)):
        seconds = data["durations"][row][0]
        meters = data["distances"][row][0]
        if seconds is None:
            routes.append(None)
        else:
            routes.append({"minutes": seconds / 60, "km": meters / 1000})
    return routes


def load_ground_truth() -> dict:
    try:
        return json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def measured_routes() -> list[dict]:
    """Пътуванията с реално измерено време: от quality/ground_truth.json и от поправките на потребителя.

    Всяко има real_minutes (реалното време) и osrm_minutes (времето на OSRM за същите точки).
    """
    routes = list(load_ground_truth().get("routes", []))
    for feedback in store.list_feedback("user_report", limit=500):
        data = feedback["data"]
        if data.get("real_minutes") and data.get("osrm_minutes"):
            routes.append(data)
    return routes


_calibration_cache: dict = {}


def city_extra_minutes() -> tuple[float, int]:
    """(колко минути добавяме към OSRM, по колко проверени пътувания).

    OSRM смята като по празни улици. Реално всяко пътуване в града губи няколко минути на светофари,
    тръгване и паркиране, почти независимо от дължината. Проверено с Google Maps на 8 маршрута в София:
    "OSRM + минути" греши средно с 13%, а "средна скорост" - с 43% (по Околовръстното пътят е бърз
    и средната скорост дава двойно време). Медиана, за да не я развали едно странно пътуване.
    """
    cached_value = _calibration_cache.get("value")
    if cached_value:
        cache_age_seconds = time.time() - _calibration_cache["at"]
        if cache_age_seconds < CALIBRATION_CACHE_SECONDS:
            return cached_value

    extra_minutes_per_route = [route["real_minutes"] - route["osrm_minutes"] for route in measured_routes()]
    if extra_minutes_per_route:
        median_extra = max(0.0, statistics.median(extra_minutes_per_route))
        value = (median_extra, len(extra_minutes_per_route))
    else:
        value = (DEFAULT_CITY_EXTRA_MINUTES, 0)

    _calibration_cache["value"] = value
    _calibration_cache["at"] = time.time()
    return value


def forget_calibration() -> None:
    """След нова поправка от потребителя калибрирането се смята наново."""
    _calibration_cache.clear()


def city_drive_time(route: dict) -> dict:
    """Реалистично време с кола в града за маршрут от OSRM.

    {"typical", "low", "high"} в минути.
    """
    extra_minutes, _ = city_extra_minutes()
    typical = route["minutes"] + extra_minutes
    return {
        "typical": typical,
        "low": typical * (1 - CITY_TIME_SPREAD),
        "high": typical * (1 + CITY_TIME_SPREAD),
    }


def drive_text(route: dict) -> str:
    """ "~16 мин, приблизително (12-20 мин), 6.2 км." Само времето с движение: по празни улици не е полезно."""
    city_time = city_drive_time(route)
    return (
        f"~{city_time['typical']:.0f} мин, приблизително ({city_time['low']:.0f}-{city_time['high']:.0f} мин), "
        f"{route['km']:.1f} км. В пиковия час може да е повече."
    )


def unusual_drive(route: dict) -> bool:
    """Време с кола, което не изглежда реално (под 1 или над 6 минути на км)."""
    if route["km"] < 0.5:
        return False
    minutes_per_km = route["minutes"] / route["km"]
    return minutes_per_km < 1 or minutes_per_km > 6
