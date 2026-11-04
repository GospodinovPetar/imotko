"""Намиране на места в града (Nominatim): квартали, улици, адреси с номер и обекти.

Nominatim позволява най-много 1 заявка в секунда. Всичко се кешира в базата.
Когато резултатът е съмнителен, към него се добавя предупреждение ("warnings"), което стига до модела.
"""

import re

import requests

from imotko import store
from imotko.geo import common

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

MAX_KM_FROM_CITY = 25
BIG_DISTRICT_KM = 3.0
STREET_MAX_KM_FROM_DISTRICT = 3.0

AREA_CLASSES = ("boundary", "place")
STREET_CLASS = "highway"

# Честите съкращения -> пълното име, което OSM познава
ALIASES = {
    "унсс": "Университет за национално и световно стопанство",
    "ту": "Технически университет",
    "су": "Софийски университет",
    "му": "Медицински университет",
    "ндк": "Национален дворец на културата",
    "летище": "Летище София",
    "летището": "Летище София",
    "бизнес парк": "Бизнес парк София",
    "бизнеспарк": "Бизнес парк София",
    "централна гара": "Централна гара София",
    # в OpenStreetMap сградата е "SAP Center Sofia" (ул. Сребърна 16)
    "sap": "SAP Center Sofia",
    "sap labs": "SAP Center Sofia",
    "sap labs bulgaria": "SAP Center Sofia",
    "sap labs българия": "SAP Center Sofia",
    "sap българия": "SAP Center Sofia",
}

STREET_PREFIX = re.compile(r"^\s*(?:ул\.?|улица|бул\.?|булевард)\s*", re.IGNORECASE)
BOULEVARD_PREFIX = re.compile(r"^\s*(?:бул\.?|булевард)\s*", re.IGNORECASE)
HOUSE_NUMBER = re.compile(r"\s+(\d+)\s*[а-яa-z]?\s*$", re.IGNORECASE)
HOUSE_NUMBER_PART = re.compile(r"\d+[а-яa-z]?(?:\s*[-–]\s*\d+[а-яa-z]?)?", re.IGNORECASE)
DISTRICT_PREFIX = re.compile(r"^\s*(?:с\.|село|гр\.|ж\.?\s*к\.?|кв\.)\s*", re.IGNORECASE)

_nominatim_limit = common.RateLimiter(1.1)


def _nominatim(params: dict) -> list[dict]:
    _nominatim_limit.wait()
    response = requests.get(
        NOMINATIM_URL,
        params={"format": "json", "countrycodes": "bg", **params},
        headers=common.HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def city_area(city: str) -> dict | None:
    """{"lat", "lon", "box"} за града. box е рамката, в която търсим кварталите и улиците."""
    key = common.cache_key("city", city)
    cached = common.cached_place(key)
    if cached is not None:
        return cached if cached["found"] else None

    results = _nominatim({"q": city, "limit": 1, "featuretype": "settlement"})
    if not results:
        store.save_place(key, {"found": False})
        return None

    best = results[0]
    south, north, west, east = (float(value) for value in best["boundingbox"])
    city_data = {
        "found": True,
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "box": f"{west},{north},{east},{south}",
    }
    store.save_place(key, city_data)
    return city_data


def locate(place: str, city: str, kind: str = "place", near: dict | None = None, use_cache: bool = True) -> dict | None:
    """Намира място в града. kind е "district" (квартал), "street" (улица) или "place" (обект).

    near е точката на квартала: в един град има по няколко улици с едно и също име,
    затова от тях се избира най-близката до квартала.

    Връща {"lat", "lon", "name", "kind", "is_area", "area", "far_from_district", "warnings"}
    или None, ако не е намерено или услугата не отговаря. area е границата на квартала (GeoJSON) или None.
    """
    original = place.strip()
    query = _clean_query(original, kind)
    key = _cache_key_for(original, query, city, kind, near)

    if use_cache:
        cached = common.cached_place(key)
        if cached is not None:
            return cached if cached["found"] else None

    try:
        if kind == "street":
            found = _search_street(original, query, city, near)
        else:
            found = _search_in_city(query, city, kind, near)
    except (requests.RequestException, ValueError):
        return None  # не кешираме: може да е временен проблем

    store.save_place(key, found or {"found": False})
    return found


def _clean_query(place: str, kind: str) -> str:
    if kind == "street":
        return STREET_PREFIX.sub("", place)
    if kind == "district":
        return DISTRICT_PREFIX.sub("", place)
    return ALIASES.get(place.lower(), place)


def _cache_key_for(original: str, query: str, city: str, kind: str, near: dict | None) -> str:
    near_key = common.point_key(near) if near else ""
    if kind == "street":
        # за улицата ключът е с "бул."/"ул." и номера, защото търсенето ги ползва
        return common.cache_key("geo2", kind, city, original, near_key)
    return common.cache_key("geo", kind, city, query, near_key)


def _search_params(query: str, city: str, limit: int, city_info: dict | None) -> dict:
    params = {"q": f"{query}, {city}", "limit": limit}
    if city_info:
        # само в рамката на града: иначе "Аспарухово, Варна" намира село на 60 км
        params["viewbox"] = city_info["box"]
        params["bounded"] = 1
    return params


def _search_in_city(query: str, city: str, kind: str, near: dict | None = None) -> dict | None:
    city_info = city_area(city)
    params = _search_params(query, city, 10 if near else 5, city_info)

    if kind == "district":
        params["polygon_geojson"] = 1
        params["polygon_threshold"] = 0.0003  # опростена граница, за да е малък отговорът
        results = _search_district(query, city, params)
    else:
        results = _nominatim(params)

    if not results and kind == "place" and city_info:
        # обектът може да е извън града (летище, курорт)
        params_without_city_box = {key: value for key, value in params.items() if key not in ("viewbox", "bounded")}
        results = _nominatim(params_without_city_box)

    if not results:
        return None

    best = _best_result(results, kind, near)
    return _place_from_result(best, query, kind, city, city_info, near)


def _search_district(query: str, city: str, params: dict) -> list[dict]:
    """В OSM кварталите често са "ж.к. Младост 1" или "Район Лозенец". Без това Nominatim
    намира метростанция или улица със същото име. Пробваме по ред, докато намерим истински квартал.
    """
    first_results = []
    for district_name in (f"ж.к. {query}", query, f"Район {query}"):
        results = _nominatim({**params, "q": f"{district_name}, {city}"})
        if any(_is_area(result) for result in results):
            return results
        if not first_results:
            first_results = results
    return first_results


def _search_street(original: str, name: str, city: str, near: dict | None) -> dict | None:
    """Улица, а ако има номер - точният адрес.

    Без номера "Цар Борис III 81" намира друга улица "Цар Борис III" в Казичене.
    Затова първо търсим адреса с номера, после булеварда с "бул." (така се казва в картата),
    и чак накрая само името на улицата.
    """
    house_number_match = HOUSE_NUMBER.search(name)
    is_boulevard = bool(BOULEVARD_PREFIX.match(original))

    if house_number_match:
        house_number = house_number_match.group(1)
        prefix = "бул. " if is_boulevard else "ул. "
        for address_query in (f"{prefix}{name}", name):
            found = _search_address(address_query, house_number, city, near)
            if found:
                return found

    street_name = HOUSE_NUMBER.sub("", name).strip() if house_number_match else name

    if is_boulevard:
        found = _search_in_city(f"бул. {street_name}", city, "street", near)
        if found:
            return found

    return _search_in_city(street_name, city, "street", near)


def _search_address(query: str, house_number: str, city: str, near: dict | None) -> dict | None:
    """Точният адрес (улица и номер). None, ако картата не знае този номер."""
    city_info = city_area(city)
    params = _search_params(query, city, 5, city_info)

    for result in _nominatim(params):
        if _has_house_number(result.get("display_name", ""), house_number):
            return _place_from_result(result, query, "address", city, city_info, near)
    return None


def _has_house_number(display_name: str, number: str) -> bool:
    """Номерът е в началото на адреса: "81, бул. ..." или "DRS Sim Racing, 81-83, бул. ..."."""
    first_two_parts = display_name.split(", ")[:2]
    for part in first_two_parts:
        looks_like_house_number = HOUSE_NUMBER_PART.fullmatch(part)
        if looks_like_house_number and number in re.findall(r"\d+", part):
            return True
    return False


def _is_area(result: dict) -> bool:
    return result.get("class") in AREA_CLASSES


def _point_of(result: dict) -> dict:
    return {"lat": float(result["lat"]), "lon": float(result["lon"])}


def _best_result(results: list[dict], kind: str, near: dict | None = None) -> dict:
    """За квартал - истинска граница пред сграда или спирка със същото име.
    За улица - самата улица, и от няколко еднакви улици най-близката до квартала.
    """
    if kind == "district":
        return _best_district(results)
    if kind == "street":
        return _best_street(results, near)
    return results[0]


def _best_district(results: list[dict]) -> dict:
    for result in results:
        if _is_area(result):
            return result
    return results[0]


def _best_street(results: list[dict], near: dict | None) -> dict:
    streets = [result for result in results if result.get("class") == STREET_CLASS]
    if not streets:
        streets = results
    if not near:
        return streets[0]
    return min(streets, key=lambda street: common.distance_km(_point_of(street), near))


def _place_from_result(result: dict, query: str, kind: str, city: str, city_info: dict | None,
                       near: dict | None) -> dict:
    place = {
        "found": True,
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "name": result.get("display_name", query),
        "kind": kind,
        "is_area": _is_area(result),
        "area": None,
        "far_from_district": False,
        "warnings": [],
    }

    geometry = result.get("geojson") or {}
    has_border = geometry.get("type") in ("Polygon", "MultiPolygon")
    if kind == "district" and place["is_area"] and has_border:
        place["area"] = geometry

    if near and common.distance_km(place, near) > STREET_MAX_KM_FROM_DISTRICT:
        place["far_from_district"] = True

    place["warnings"] = _location_warnings(place, result, city, city_info)
    return place


def _location_warnings(place: dict, result: dict, city: str, city_info: dict | None) -> list[str]:
    """Проверки дали е намерено правилното място."""
    warnings = []

    if city.lower() not in place["name"].lower():
        warnings.append(f"намерено е място извън {city}, вероятно грешно: {place['name']}")

    if place["kind"] == "district" and not _is_area(result):
        warnings.append(f"намерен е обект, не самият квартал: {place['name']}")

    if place["kind"] == "street" and result.get("class") != STREET_CLASS:
        warnings.append(f"намерено е \"{place['name']}\", не самата улица")

    if city_info and common.distance_km(place, city_info) > MAX_KM_FROM_CITY:
        warnings.append(f"мястото е на над {MAX_KM_FROM_CITY} км от центъра на {city}")

    if place["area"] and common.width_km(place["area"]) > BIG_DISTRICT_KM:
        warnings.append("голям квартал, разстоянията по квартала може да грешат с 1-2 км")

    return warnings


def locate_any(place: str, city: str) -> dict | None:
    """Улица, квартал или обект. Ако цялото име не се намери, пробва частите между запетаите:
    "SAP Labs Bulgaria, бул. Цариградско шосе 136А" - моделът често добавя адрес по памет.
    """
    found = _locate_one(place, city)
    if found or "," not in place:
        return found

    for part in place.split(","):
        part = part.strip()
        if not part or part.lower() == city.lower():
            continue
        found = _locate_one(part, city)
        if found:
            return {**found, "warnings": found["warnings"] + [f"намерено е само по \"{part}\""]}
    return None


def _locate_one(place: str, city: str) -> dict | None:
    """Улица, квартал или обект: опитва по реда, в който е най-вероятно.

    Квартал се приема само ако е намерен истински квартал, а не спирка или сграда със същото име.
    """
    if STREET_PREFIX.match(place):
        return locate(place, city, "street")

    is_known_alias = place.strip().lower() in ALIASES
    if not is_known_alias:
        district = locate(place, city, "district")
        if district and district.get("is_area"):
            return district

    return locate(place, city, "place") or locate(place, city, "district")


def precision_text(place: dict) -> str:
    if place["kind"] == "address":
        return "по адрес"
    if place["kind"] == "street":
        return "по улица"
    if place["kind"] == "district":
        return "по квартал" if place["area"] else "по точка на квартала"
    return "по обект"
