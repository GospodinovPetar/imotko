"""Градски транспорт по официалните разписания (GTFS) на общините.

Няма външна услуга: маршрутът се смята тук с алгоритъма Connection Scan. Връзка е едно
пътуване на превозно средство от една спирка до следващата. Връзките са подредени по час на
тръгване и се обхождат веднъж, като се пази най-ранното пристигане на всяка спирка.

Времето е за делничен ден сутрин: медианата от няколко тръгвания между 7:40 и 8:40,
за да не зависи от това дали автобусът току-що е минал.

Разписанията се свалят в DATA_DIR/gtfs/ при първа нужда и се обновяват веднъж седмично (watch.py).
"""

import csv
import io
import math
import statistics
import threading
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import requests

from imotko.config import DATA_DIR
from imotko.geo import common

# Проверени източници (Mobility Database и Националната точка за достъп sipbg.gov.bg).
# Ключът е градът с малки букви, както го пише потребителят.
FEEDS = {
    "софия": "https://gtfs.sofiatraffic.bg/api/v1/static",
    "пловдив": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/2527423b-e006-438d-8743-19d987b609fd/download",
    "варна": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/79642de1-da00-47b3-9417-994ccf53ed4e/download",
    "бургас": "https://external.gtfs.ro/cities/bg-burgas.zip",
    "стара загора": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/21fa52d7-7813-490d-9069-cf719de0534f/download",
    "плевен": "https://external.gtfs.ro/pleven/PLEVEN.zip",
    "велико търново": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/34d5d718-dcf4-467d-b984-223c5ea88cca/download",
    "добрич": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/8b343269-c867-4963-a644-0e8f3a03561c/download",
    "сливен": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/6bf05b1e-26c8-41fa-b25f-9f757c94e3e5/download",
    "ямбол": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/d1f0dd69-6973-45d3-a67a-fe815b2bf888/download",
    "казанлък": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/a1a711a6-73c9-409a-b24a-211f3f08550b/download",
    "самоков": "https://sipbg.gov.bg/bgnap/portal/api/catalog/files/f56d65db-c1f2-4dd9-b3d7-e8983eae7a69/download",
    "айтос": "https://transport.aytos.bg/gtfs/AytosGTFS.zip",
}

FEED_DIR = DATA_DIR / "gtfs"
REFRESH_SECONDS = 7 * 24 * 3600
DOWNLOAD_TIMEOUT = 120

TIMEZONE = ZoneInfo("Europe/Sofia")
DEPARTURES = [7 * 3600 + 40 * 60 + step * 15 * 60 for step in range(5)]  # 7:40, 7:55 ... 8:40
WINDOW_START = 7 * 3600 + 30 * 60
WINDOW_END = 10 * 3600 + 30 * 60

# пеша: 5 км/ч, а пътят е ~30% по-дълъг от правата линия
WALK_METERS_PER_SECOND = 5 / 3.6 / 1.3
MAX_WALK_TO_STOP_M = 1000
TRANSFER_WALK_M = 400
TRANSFER_WALK_EXTRA_S = 60
MIN_TRANSFER_S = 90
MAX_DAYS_AHEAD = 7

ROUTE_TYPES = {"0": "трамвай", "1": "метро", "2": "влак", "3": "автобус", "11": "тролей", "800": "тролей"}
WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

GRID_LAT = 0.0045  # ~500 м
GRID_LON = 0.006
GRID_CELL_M = 500


# Сваляне и обновяване

def city_key(city: str) -> str:
    return city.strip().lower()


def has_feed(city: str) -> bool:
    return city_key(city) in FEEDS


def feed_path(city: str) -> Path:
    return FEED_DIR / f"{city_key(city)}.zip"


def download_feed(city: str) -> bool:
    """Сваля разписанието на града. False при грешка (старият файл остава)."""
    url = FEEDS.get(city_key(city))
    if not url:
        return False

    try:
        response = requests.get(url, headers={"User-Agent": "imotko"}, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        first_broken_file = zipfile.ZipFile(io.BytesIO(response.content)).testzip()
    except (requests.RequestException, zipfile.BadZipFile):
        return False
    if first_broken_file is not None:
        return False

    FEED_DIR.mkdir(parents=True, exist_ok=True)
    partial_file = feed_path(city).with_suffix(".part")
    partial_file.write_bytes(response.content)
    partial_file.replace(feed_path(city))
    forget(city)
    return True


def refresh_feeds() -> list[str]:
    """Обновява разписанията, по-стари от седмица (и на София винаги). Връща обновените градове."""
    refreshed_cities = []
    for city in FEEDS:
        path = feed_path(city)
        is_downloaded = path.exists()

        if city != "софия" and not is_downloaded:
            continue  # другите градове се свалят при първа нужда
        if is_downloaded and time.time() - path.stat().st_mtime < REFRESH_SECONDS:
            continue

        if download_feed(city):
            refreshed_cities.append(city)
    return refreshed_cities


# Зареждане на разписанието

class Connection(NamedTuple):
    departure: int
    arrival: int
    from_stop: int
    to_stop: int
    trip: int


@dataclass
class Timetable:
    """Връзките за един ден между WINDOW_START и WINDOW_END и спирките."""

    stops: list[dict]
    connections: list[Connection]
    trip_labels: list[str]
    transfers: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    grid: dict[tuple[int, int], list[int]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self):
        for stop_index, stop in enumerate(self.stops):
            self.grid[grid_cell(stop)].append(stop_index)

    def stops_near(self, point: dict, meters: float) -> list[tuple[int, int]]:
        """[(спирка, секунди пеша)] до `meters` от точката."""
        cells_around = math.ceil(meters / GRID_CELL_M)
        center_row, center_column = grid_cell(point)

        nearby_stops = []
        for row in range(center_row - cells_around, center_row + cells_around + 1):
            for column in range(center_column - cells_around, center_column + cells_around + 1):
                for stop_index in self.grid.get((row, column), []):
                    distance_m = common.distance_km(point, self.stops[stop_index]) * 1000
                    if distance_m <= meters:
                        nearby_stops.append((stop_index, walking_seconds(distance_m)))
        return nearby_stops


_timetables_by_city: dict[str, tuple[date, Timetable]] = {}
# по едно заключване на град: докато се сваля или чете разписанието на един град, другите не чакат
_city_locks: dict[str, threading.Lock] = {}
_city_locks_guard = threading.Lock()


def _city_lock(key: str) -> threading.Lock:
    with _city_locks_guard:
        return _city_locks.setdefault(key, threading.Lock())


def forget(city: str) -> None:
    _timetables_by_city.pop(city_key(city), None)


def grid_cell(point: dict) -> tuple[int, int]:
    return int(point["lat"] // GRID_LAT), int(point["lon"] // GRID_LON)


def walking_seconds(distance_m: float) -> int:
    return int(distance_m / WALK_METERS_PER_SECOND)


def read_csv(feed: zipfile.ZipFile, file_name: str):
    if file_name not in feed.namelist():
        return
    with feed.open(file_name) as file:
        yield from csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig"))


def seconds_after_midnight(clock_time: str) -> int | None:
    """"25:10:00" -> 90600. Часът може да е след 24:00 за курсове след полунощ."""
    try:
        hours, minutes, seconds = clock_time.strip().split(":")
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    except ValueError:
        return None


def services_on(feed: zipfile.ZipFile, day: date) -> set[str]:
    """Кои service_id се движат на датата: calendar.txt (дни от седмицата) плюс/минус calendar_dates.txt."""
    day_stamp = day.strftime("%Y%m%d")
    weekday_column = WEEKDAY_COLUMNS[day.weekday()]

    services = set()
    for row in read_csv(feed, "calendar.txt"):
        runs_on_this_weekday = row.get(weekday_column) == "1"
        is_in_date_range = row["start_date"] <= day_stamp <= row["end_date"]
        if runs_on_this_weekday and is_in_date_range:
            services.add(row["service_id"])

    for row in read_csv(feed, "calendar_dates.txt"):
        if row["date"] != day_stamp:
            continue
        if row["exception_type"] == "1":
            services.add(row["service_id"])
        elif row["exception_type"] == "2":
            services.discard(row["service_id"])

    return services


def route_label(route_row: dict) -> str:
    line_name = route_row.get("route_short_name") or route_row.get("route_long_name") or "?"
    vehicle = ROUTE_TYPES.get(route_row.get("route_type", ""), "линия")
    if vehicle == "метро" and line_name.upper().startswith("M"):
        return f"метро {line_name.upper()}"
    return f"{vehicle} {line_name}"


def parse_feed(path: Path, day: date) -> Timetable | None:
    """Разписанието за деня. None, ако в този ден не се движи нищо (изтекли данни или празник)."""
    with zipfile.ZipFile(path) as feed:
        services = services_on(feed, day)
        if not services:
            return None

        trip_index_by_id, trip_labels = read_trips(feed, services)
        stop_index_by_id, stops = read_stops(feed)
        stop_visits_by_trip = read_stop_visits(feed, trip_index_by_id, stop_index_by_id)

    connections = build_connections(stop_visits_by_trip)
    timetable = Timetable(stops=stops, connections=connections, trip_labels=trip_labels)
    timetable.transfers = build_transfers(timetable)
    return timetable


def read_trips(feed: zipfile.ZipFile, services: set[str]) -> tuple[dict[str, int], list[str]]:
    label_by_route_id = {row["route_id"]: route_label(row) for row in read_csv(feed, "routes.txt")}

    trip_index_by_id = {}
    trip_labels = []
    for row in read_csv(feed, "trips.txt"):
        if row["service_id"] not in services:
            continue
        trip_index_by_id[row["trip_id"]] = len(trip_labels)
        trip_labels.append(label_by_route_id.get(row["route_id"], "линия ?"))
    return trip_index_by_id, trip_labels


def read_stops(feed: zipfile.ZipFile) -> tuple[dict[str, int], list[dict]]:
    stop_index_by_id = {}
    stops = []
    for row in read_csv(feed, "stops.txt"):
        is_station_or_entrance = row.get("location_type", "0") not in ("", "0")
        if is_station_or_entrance or not row.get("stop_lat"):
            continue  # гари и входове, не спирки
        stop_index_by_id[row["stop_id"]] = len(stops)
        stops.append({"lat": float(row["stop_lat"]), "lon": float(row["stop_lon"]), "name": row.get("stop_name", "")})
    return stop_index_by_id, stops


def read_stop_visits(feed: zipfile.ZipFile, trip_index_by_id: dict[str, int],
                     stop_index_by_id: dict[str, int]) -> dict[int, list[tuple[int, int, int, int]]]:
    # редовете на курса може да не са подредени: събираме ги и ги подреждаме по stop_sequence
    stop_visits_by_trip = defaultdict(list)
    for row in read_csv(feed, "stop_times.txt"):
        trip_index = trip_index_by_id.get(row["trip_id"])
        stop_index = stop_index_by_id.get(row["stop_id"])
        if trip_index is None or stop_index is None:
            continue

        arrival = seconds_after_midnight(row["arrival_time"]) if row["arrival_time"] else None
        departure = seconds_after_midnight(row["departure_time"]) if row["departure_time"] else arrival
        if arrival is None:
            arrival = departure
        if departure is None:
            continue  # спирка без час (между две с час): прескачаме я

        stop_sequence = int(row["stop_sequence"])
        stop_visits_by_trip[trip_index].append((stop_sequence, stop_index, arrival, departure))

    for visits in stop_visits_by_trip.values():
        visits.sort()
    return stop_visits_by_trip


def build_connections(stop_visits_by_trip: dict[int, list[tuple[int, int, int, int]]]) -> list[Connection]:
    connections = []
    for trip_index, visits in stop_visits_by_trip.items():
        for current_visit, next_visit in zip(visits, visits[1:]):
            _, from_stop, _, departure = current_visit
            _, to_stop, arrival, _ = next_visit

            is_in_morning_window = WINDOW_START <= departure <= WINDOW_END
            if is_in_morning_window and arrival >= departure:
                connections.append(Connection(departure, arrival, from_stop, to_stop, trip_index))

    connections.sort()
    return connections


def build_transfers(timetable: Timetable) -> dict[int, list[tuple[int, int]]]:
    transfers = {}
    for stop_index, stop in enumerate(timetable.stops):
        nearby_stops = timetable.stops_near(stop, TRANSFER_WALK_M)
        transfers[stop_index] = [
            (other_stop, seconds + TRANSFER_WALK_EXTRA_S)
            for other_stop, seconds in nearby_stops
            if other_stop != stop_index
        ]
    return transfers


def next_weekday(now: datetime | None = None) -> date:
    """Днес, ако е делник, иначе следващият понеделник (по българско време)."""
    today = (now or datetime.now(TIMEZONE)).date()
    while today.weekday() >= 5:
        today += timedelta(days=1)
    return today


def load_timetable(city: str) -> tuple[Timetable | None, str]:
    """(разписание, "") или (None, причина). Сваля данните при първа нужда."""
    key = city_key(city)
    if key not in FEEDS:
        return None, f"няма публично разписание за {city}"

    with _city_lock(key):
        day = next_weekday()
        cached = _timetables_by_city.get(key)
        if cached and cached[0] == day:
            return cached[1], ""

        if not feed_path(city).exists() and not download_feed(city):
            return None, "разписанието не може да се свали в момента"

        for days_ahead in range(MAX_DAYS_AHEAD):
            candidate_day = day + timedelta(days=days_ahead)
            if candidate_day.weekday() >= 5:
                continue
            timetable = parse_feed(feed_path(city), candidate_day)
            if timetable is not None:
                _timetables_by_city[key] = (day, timetable)
                return timetable, ""

        return None, f"разписанието на {city} е изтекло (няма курсове в следващите дни)"


# Маршрут

@dataclass
class WalkFromStart:
    seconds: int


@dataclass
class WalkBetweenStops:
    from_stop: int
    seconds: int


@dataclass
class Ride:
    trip: int
    boarded_at: int


ON_FOOT = -1


def fastest_journey(timetable: Timetable, origin: dict, destination: dict, departure: int) -> dict:
    """Най-ранното пристигане при тръгване в `departure`. Пеша, ако е по-бързо."""
    earliest_arrival = defaultdict(lambda: math.inf)
    arrived_by_trip = {}
    how_we_got_there = {}

    for stop_index, seconds in timetable.stops_near(origin, MAX_WALK_TO_STOP_M):
        earliest_arrival[stop_index] = departure + seconds
        arrived_by_trip[stop_index] = ON_FOOT
        how_we_got_there[stop_index] = WalkFromStart(seconds)

    walk_to_destination = dict(timetable.stops_near(destination, MAX_WALK_TO_STOP_M))
    walk_only_seconds = walking_seconds(common.distance_km(origin, destination) * 1000)
    best_arrival = departure + walk_only_seconds
    last_stop = None
    boarded_at_by_trip = {}

    for connection in timetable.connections:
        if connection.departure < departure:
            continue
        if connection.departure >= best_arrival:
            break

        if connection.trip not in boarded_at_by_trip:
            ready_at = earliest_arrival[connection.from_stop]
            arrived_on_another_trip = arrived_by_trip.get(connection.from_stop, ON_FOOT) not in (ON_FOOT, connection.trip)
            if arrived_on_another_trip:
                ready_at += MIN_TRANSFER_S
            if ready_at > connection.departure:
                continue
            boarded_at_by_trip[connection.trip] = connection.from_stop

        if connection.arrival >= earliest_arrival[connection.to_stop]:
            continue

        earliest_arrival[connection.to_stop] = connection.arrival
        arrived_by_trip[connection.to_stop] = connection.trip
        how_we_got_there[connection.to_stop] = Ride(connection.trip, boarded_at_by_trip[connection.trip])

        for other_stop, seconds in timetable.transfers[connection.to_stop]:
            if connection.arrival + seconds < earliest_arrival[other_stop]:
                earliest_arrival[other_stop] = connection.arrival + seconds
                arrived_by_trip[other_stop] = ON_FOOT
                how_we_got_there[other_stop] = WalkBetweenStops(connection.to_stop, seconds)

        if connection.to_stop in walk_to_destination:
            arrival_at_destination = connection.arrival + walk_to_destination[connection.to_stop]
            if arrival_at_destination < best_arrival:
                best_arrival = arrival_at_destination
                last_stop = connection.to_stop

    if last_stop is None:
        walk_only_minutes = walk_only_seconds / 60
        return {"minutes": walk_only_minutes, "lines": [], "walk_minutes": walk_only_minutes}

    lines, walking_seconds_total = trace_back(timetable, how_we_got_there, last_stop)
    walking_seconds_total += walk_to_destination[last_stop]
    return {"minutes": (best_arrival - departure) / 60, "lines": lines, "walk_minutes": walking_seconds_total / 60}


def trace_back(timetable: Timetable, how_we_got_there: dict, last_stop: int) -> tuple[list[str], int]:
    lines_backwards = []
    previous_trip = None
    walking_seconds_total = 0
    stop = last_stop

    while True:
        step = how_we_got_there[stop]
        if isinstance(step, WalkFromStart):
            walking_seconds_total += step.seconds
            break
        if isinstance(step, WalkBetweenStops):
            walking_seconds_total += step.seconds
            stop = step.from_stop
            continue

        # всеки друг курс е прекачване, дори да е на същата линия
        if step.trip != previous_trip:
            lines_backwards.append(timetable.trip_labels[step.trip])
            previous_trip = step.trip
        stop = step.boarded_at

    return list(reversed(lines_backwards)), walking_seconds_total


def route(city: str, origin: dict, destination: dict) -> dict:
    """{"ok": True, "minutes", "lines", "transfers", "walk_minutes", "walk_only"} или {"ok": False, "reason"}.

    minutes е медианата от няколко тръгвания в делничен ден сутрин, с чакането и ходенето пеша.
    """
    try:
        timetable, reason = load_timetable(city)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as error:
        return {"ok": False, "reason": f"разписанието не може да се прочете ({type(error).__name__})"}
    if timetable is None:
        return {"ok": False, "reason": reason}

    journeys = [fastest_journey(timetable, origin, destination, departure) for departure in DEPARTURES]
    journeys.sort(key=lambda journey: journey["minutes"])
    typical_journey = journeys[len(journeys) // 2]

    return {
        "ok": True,
        "minutes": statistics.median(journey["minutes"] for journey in journeys),
        "lines": typical_journey["lines"],
        "transfers": max(0, len(typical_journey["lines"]) - 1),
        "walk_minutes": typical_journey["walk_minutes"],
        "walk_only": not typical_journey["lines"],
    }


def short_text(result: dict) -> str:
    """"~22 мин (автобус 94 → автобус 413)" за списъка с обяви."""
    if not result.get("ok"):
        return "няма данни"
    if result["walk_only"]:
        return f"~{result['minutes']:.0f} мин пеша (по-бързо от транспорта)"
    return f"~{result['minutes']:.0f} мин ({' → '.join(result['lines'])})"


def describe(result: dict) -> str:
    """Ред за distance_between."""
    if not result.get("ok"):
        return f"Градски транспорт: {result['reason']}."

    minutes = result["minutes"]
    if result["walk_only"]:
        return f"Градски транспорт: пеша е по-бързо (~{minutes:.0f} мин). По разписанието, делничен ден сутрин."

    lines = " → ".join(result["lines"])
    transfers = transfers_text(result["transfers"])
    return (
        f"Градски транспорт: ~{minutes:.0f} мин в делничен ден сутрин "
        f"({lines}, {transfers}, пеша общо ~{result['walk_minutes']:.0f} мин). "
        "По разписанието, с чакането; приблизително."
    )


def transfers_text(transfers: int) -> str:
    if transfers == 0:
        return "без прекачване"
    if transfers == 1:
        return "1 прекачване"
    return f"{transfers} прекачвания"
