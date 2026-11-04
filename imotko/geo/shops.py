"""Големите вериги магазини в града (Overpass): кои има в квартала и коя е най-близо до улицата.

Overpass е бавен и често претоварен, затова данните се свалят веднъж в месеца, във фонов режим.
"""

import re
import threading

import requests

from imotko import store
from imotko.geo import common, geocode

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

CHAINS_MAX_AGE = 30 * common.DAY

CHAINS_STALE_AGE = 45 * common.DAY

DISTRICT_RADIUS_KM = 1.0

CHAIN_NEAR_STREET_KM = 0.5


# Големите вериги: име -> как се пишат в OSM (brand или name), без значение на малки и главни букви
CHAINS = {
    "Lidl": r"lidl|лидл",
    "Kaufland": r"kaufland|кауфланд",
    "Billa": r"billa|била",
    "Fantastico": r"fantastico|фантастико",
    "T Market": r"t ?market|т ?маркет",
}

_refreshing: set[str] = set()

_refreshing_lock = threading.Lock()


def _chains_key(city: str) -> str:
    return common.cache_key("chains", city)


def chain_stores(city: str) -> tuple[list[dict], float] | None:
    """([{"chain", "lat", "lon"}], преди колко дни са свалени) от кеша или None, ако няма данни."""
    cached = store.get_place(_chains_key(city))
    if cached is None:
        return None

    data, age_seconds = cached
    age_days = age_seconds / common.DAY
    return data["stores"], age_days


def chains_need_refresh(city: str) -> bool:
    cached = store.get_place(_chains_key(city))
    if cached is None:
        return True

    _, age_seconds = cached
    return age_seconds > CHAINS_MAX_AGE


def refresh_chains(city: str) -> bool:
    """Сваля всички големи вериги в града от Overpass. Отнема до минута. True при успех."""
    try:
        city_info = geocode.city_area(city)
    except (requests.RequestException, ValueError):
        return False
    if city_info is None:
        return False

    west, north, east, south = city_info["box"].split(",")
    query = f'[out:json][timeout:90];nwr["shop"="supermarket"]({south},{west},{north},{east});out tags center;'

    elements = _download_supermarkets(query)
    if elements is None:
        return False

    stores = _chain_stores_from(elements)
    store.save_place(_chains_key(city), {"found": True, "stores": stores})
    return True


def _download_supermarkets(query: str) -> list[dict] | None:
    for url in OVERPASS_URLS:
        try:
            response = requests.post(url, data={"data": query}, headers=common.HEADERS, timeout=120)
            is_json = response.ok and response.text.lstrip().startswith("{")
            if not is_json:
                continue

            data = response.json()
            is_complete = "elements" in data and "remark" not in data
            if is_complete:
                return data["elements"]
        except (requests.RequestException, ValueError):
            continue
    return None


def _chain_stores_from(elements: list[dict]) -> list[dict]:
    stores = []
    for element in elements:
        chain = _chain_name(element.get("tags", {}))
        point = element.get("center") or element
        if chain and "lat" in point:
            stores.append({"chain": chain, "lat": point["lat"], "lon": point["lon"]})
    return stores


def refresh_chains_in_background(city: str) -> None:
    """Пуска refresh_chains в отделна нишка, без да чака. Търсенето не бива да чака Overpass."""
    city_name = city.lower()
    with _refreshing_lock:
        if city_name in _refreshing:
            return
        _refreshing.add(city_name)

    def refresh_and_release() -> None:
        try:
            refresh_chains(city)
        finally:
            with _refreshing_lock:
                _refreshing.discard(city_name)

    threading.Thread(target=refresh_and_release, name=f"chains-{city}", daemon=True).start()


def _chain_name(tags: dict) -> str | None:
    for text in (tags.get("brand", ""), tags.get("name", "")):
        for chain, pattern in CHAINS.items():
            if re.match(rf"\s*(?:{pattern})\b", text, re.IGNORECASE):
                return chain
    return None


def chains_in_district(district: dict, stores: list[dict]) -> dict:
    """{"counts": {верига: брой}, "area": "граница" | "кръг 1 км"} за квартала."""
    counts: dict[str, int] = {}

    for chain_store in stores:
        if _is_in_district(chain_store, district):
            chain = chain_store["chain"]
            counts[chain] = counts.get(chain, 0) + 1

    if district["area"]:
        area_text = "граница"
    else:
        area_text = f"кръг {DISTRICT_RADIUS_KM:g} км"
    return {"counts": counts, "area": area_text}


def _is_in_district(point: dict, district: dict) -> bool:
    if district["area"]:
        return common.is_inside(point, district["area"])
    return common.distance_km(point, district) <= DISTRICT_RADIUS_KM


def nearest_chain(point: dict, stores: list[dict]) -> tuple[str, float] | None:
    """(верига, км) на най-близкия голям магазин или None."""
    if not stores:
        return None
    closest = min(stores, key=lambda chain_store: common.distance_km(point, chain_store))
    closest_km = common.distance_km(point, closest)
    return closest["chain"], closest_km
