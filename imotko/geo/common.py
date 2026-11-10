"""Общото за картата: заглавка към услугите, ограничение на скоростта, разстояния и кеш."""

import fcntl
import math
import time

from imotko import store
from imotko.config import DATA_DIR

# Nominatim иска име на приложението; личен имейл не пращаме
HEADERS = {"User-Agent": "imotko/0.1 (personal apartment search assistant)"}

DAY = 86400
FOUND_MAX_AGE = 180 * DAY
NOT_FOUND_MAX_AGE = 1 * DAY
EARTH_RADIUS_KM = 6371.0


class RateLimiter:
    """Не повече от една заявка за `interval` секунди, дори от няколко нишки и процеса (агентът и watch.py).

    Времето на последната заявка е във файл в DATA_DIR, заключен докато се чака.
    """

    def __init__(self, interval: float, name: str):
        self.interval = interval
        self.path = DATA_DIR / f".{name}.ratelimit"

    def wait(self) -> None:
        with open(self.path, "a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            last_request_time = float(file.read() or 0)
            seconds_to_wait = last_request_time + self.interval - time.time()
            if seconds_to_wait > 0:
                time.sleep(seconds_to_wait)
            file.seek(0)
            file.truncate()
            file.write(str(time.time()))


def distance_km(start: dict, end: dict) -> float:
    """Права линия между две точки {"lat", "lon"} (формулата на haversine)."""
    start_lat = math.radians(start["lat"])
    end_lat = math.radians(end["lat"])
    delta_lat = end_lat - start_lat
    delta_lon = math.radians(end["lon"] - start["lon"])

    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(start_lat) * math.cos(end_lat) * math.sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine))


def _outer_rings(geojson: dict) -> list[list[list[float]]]:
    """Външните контури на Polygon или MultiPolygon като списъци от [lon, lat]."""
    if geojson["type"] == "Polygon":
        return [geojson["coordinates"][0]]
    return [polygon[0] for polygon in geojson["coordinates"]]


def is_inside(point: dict, geojson: dict) -> bool:
    """Точката вътре в многоъгълника ли е (ray casting). Дупките в многоъгълника се пренебрегват."""
    for ring in _outer_rings(geojson):
        if _is_inside_ring(point["lon"], point["lat"], ring):
            return True
    return False


def _is_inside_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    previous_lon, previous_lat = ring[-1][0], ring[-1][1]
    for corner in ring:
        corner_lon, corner_lat = corner[0], corner[1]
        edge_crosses_latitude = (corner_lat > lat) != (previous_lat > lat)
        if edge_crosses_latitude:
            edge_lon_at_lat = previous_lon + (lat - previous_lat) * (corner_lon - previous_lon) / (corner_lat - previous_lat)
            if lon < edge_lon_at_lat:
                inside = not inside
        previous_lon, previous_lat = corner_lon, corner_lat
    return inside


def width_km(geojson: dict) -> float:
    """Диагоналът на правоъгълника около многоъгълника."""
    corners = [corner for ring in _outer_rings(geojson) for corner in ring]
    lons = [corner[0] for corner in corners]
    lats = [corner[1] for corner in corners]
    south_west = {"lat": min(lats), "lon": min(lons)}
    north_east = {"lat": max(lats), "lon": max(lons)}
    return distance_km(south_west, north_east)


def cached_place(key: str):
    """Данните от кеша или None, ако ги няма или са остарели."""
    cached = store.get_place(key)
    if cached is None:
        return None

    data, age_seconds = cached
    max_age = FOUND_MAX_AGE if data.get("found") else NOT_FOUND_MAX_AGE
    if age_seconds >= max_age:
        return None
    return data


def cache_key(*parts: str) -> str:
    return "|".join(part.strip().lower() for part in parts)


def point_key(point: dict) -> str:
    return f"{point['lat']:.4f},{point['lon']:.4f}"
