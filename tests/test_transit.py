"""Тестове за градския транспорт с малко измислено разписание. Без мрежа."""

import io
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from imotko.geo import distance, driving, geocode, transit
from imotko.quality import checks
from tests.helpers import TempDatabase

WEDNESDAY = date(2026, 11, 11)
HOLIDAY = date(2026, 11, 12)  # махнат с calendar_dates.txt

# четири спирки в права линия, на ~1.6 км една от друга
STOPS = {"S1": (42.70, 23.30), "S2": (42.70, 23.32), "S3": (42.70, 23.34), "S4": (42.70, 23.36)}

FILES = {
    "routes.txt": "route_id,route_short_name,route_type\nB1,1,3\nT5,5,0\nM1,M1,1\n",
    "trips.txt": "route_id,service_id,trip_id\nB1,WD,bus\nT5,WD,tram_early\nT5,WD,tram\nM1,DATED,metro\nB1,WD,night\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
    + "".join(f"{stop},Спирка {stop},{lat},{lon}\n" for stop, (lat, lon) in STOPS.items()),
    # редовете на автобуса са разбъркани нарочно
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "bus,08:20:00,08:20:00,S3,3\n"
        "bus,08:00:00,08:00:00,S1,1\n"
        "bus,08:10:00,08:10:00,S2,2\n"
        # тръгва минута след пристигането на автобуса: няма време за прекачване
        "tram_early,08:21:00,08:21:00,S3,1\n"
        "tram_early,08:30:00,08:30:00,S4,2\n"
        "tram,08:25:00,08:25:00,S3,1\n"
        "tram,08:34:00,08:34:00,S4,2\n"
        "metro,08:00:00,08:00:00,S1,1\n"
        "metro,08:05:00,08:05:00,S4,2\n"
        # курс след полунощ: извън сутрешния прозорец, не трябва да чупи нищо
        "night,25:10:00,25:10:00,S1,1\n"
        "night,25:20:00,25:20:00,S2,2\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WD,1,1,1,1,1,0,0,20261101,20261130\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\nWD,20261112,2\nDATED,20261113,1\n",
}


def write_feed(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as feed:
        for name, text in FILES.items():
            feed.writestr(name, text)
    return path


def point(stop: str) -> dict:
    lat, lon = STOPS[stop]
    return {"lat": lat, "lon": lon}


class Feed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_feed(Path(self.tmp.name) / "feed.zip")

    def test_services_from_calendar_and_dates(self):
        with zipfile.ZipFile(self.path) as feed:
            self.assertEqual(transit.services_on(feed, WEDNESDAY), {"WD"})
            self.assertEqual(transit.services_on(feed, HOLIDAY), set())  # махнат празник
            self.assertEqual(transit.services_on(feed, date(2026, 11, 13)), {"WD", "DATED"})  # добавен ден
            self.assertEqual(transit.services_on(feed, date(2027, 1, 6)), set())  # след края

    def test_no_service_means_no_timetable(self):
        self.assertIsNone(transit.parse_feed(self.path, HOLIDAY))

    def test_unsorted_stop_times_make_ordered_connections(self):
        table = transit.parse_feed(self.path, WEDNESDAY)
        bus = [connection for connection in table.connections if table.trip_labels[connection[4]] == "автобус 1"]
        self.assertEqual([(connection[0], connection[1]) for connection in bus],
                         [(8 * 3600, 8 * 3600 + 600), (8 * 3600 + 600, 8 * 3600 + 1200)])
        self.assertEqual(len(table.connections), 4)  # нощният курс и метрото (друг ден) ги няма

    def test_transfer_needs_time(self):
        table = transit.parse_feed(self.path, WEDNESDAY)
        journey = transit.fastest_journey(table, point("S1"), point("S4"), 7 * 3600 + 55 * 60)
        # трамваят в 8:21 не се хваща (минута след автобуса), а този в 8:25 - да
        self.assertEqual(journey["lines"], ["автобус 1", "трамвай 5"])
        self.assertAlmostEqual(journey["minutes"], 39)

    def test_walking_when_faster(self):
        table = transit.parse_feed(self.path, WEDNESDAY)
        near = {"lat": 42.7005, "lon": 23.3005}
        journey = transit.fastest_journey(table, point("S1"), near, 7 * 3600 + 55 * 60)
        self.assertEqual(journey["lines"], [])
        self.assertLess(journey["minutes"], 2)

    def test_next_weekday_in_sofia_time(self):
        saturday = datetime(2026, 11, 7, 12, tzinfo=transit.TIMEZONE)
        self.assertEqual(transit.next_weekday(saturday), date(2026, 11, 9))
        self.assertEqual(transit.next_weekday(datetime(2026, 11, 11, 23, tzinfo=transit.TIMEZONE)), WEDNESDAY)


class Route(TempDatabase):
    def setUp(self):
        super().setUp()
        transit.FEED_DIR.mkdir(parents=True)
        write_feed(transit.feed_path("София"))

    def test_route_and_texts(self):
        with mock.patch.object(transit, "next_weekday", return_value=WEDNESDAY):
            result = transit.route("София", point("S1"), point("S4"))
        self.assertTrue(result["ok"])
        self.assertIn("мин", transit.describe(result))
        self.assertIn("делничен ден сутрин", transit.describe(result))

    def test_expired_timetable(self):
        with mock.patch.object(transit, "next_weekday", return_value=date(2027, 3, 3)):
            result = transit.route("София", point("S1"), point("S4"))
        self.assertFalse(result["ok"])
        self.assertIn("изтекло", transit.describe(result))

    def test_city_without_timetable(self):
        result = transit.route("Русе", point("S1"), point("S4"))
        self.assertEqual(transit.describe(result), "Градски транспорт: няма публично разписание за Русе.")

    def test_missing_feed_is_not_downloaded_in_tests(self):
        result = transit.route("Пловдив", point("S1"), point("S4"))
        self.assertFalse(result["ok"])
        self.assertIn("не може да се свали", result["reason"])

    def test_distance_text_has_transit(self):
        with (
            mock.patch.object(transit, "next_weekday", return_value=WEDNESDAY),
            mock.patch.object(geocode, "locate_any", side_effect=lambda name, city: {
                **point(name), "name": name, "kind": "place", "area": None, "warnings": []}),
            mock.patch.object(driving, "drive", return_value={"minutes": 10.0, "km": 5.0}),
        ):
            text = distance.describe_distance("S1", "S4", "София")
        # в измисленото разписание след 8:00 няма курсове, затова медианата е пеша
        self.assertIn("Градски транспорт: пеша е по-бързо", text)
        self.assertNotIn("няма безплатни данни", text)


class TransitCheck(unittest.TestCase):
    ROUTE = {"from": "Борово", "to": "SAP", "city": "София", "from_point": point("S1"), "to_point": point("S4"),
             "real_transit_minutes": 27}

    def test_within_tolerance(self):
        found = {"ok": True, "minutes": 32.0, "lines": ["автобус 83"], "transfers": 0, "walk_minutes": 11, "walk_only": False}
        with mock.patch.object(transit, "route", return_value=found):
            self.assertTrue(checks._check_transit(self.ROUTE)["ok"])

    def test_too_far_or_no_data_fails(self):
        slow = {"ok": True, "minutes": 60.0, "lines": ["автобус 83"], "transfers": 0, "walk_minutes": 11, "walk_only": False}
        with mock.patch.object(transit, "route", return_value=slow):
            self.assertFalse(checks._check_transit(self.ROUTE)["ok"])
        with mock.patch.object(transit, "route", return_value={"ok": False, "reason": "изтекло"}):
            self.assertFalse(checks._check_transit(self.ROUTE)["ok"])


if __name__ == "__main__":
    unittest.main()


class ReviewFixes(unittest.TestCase):
    def test_broken_download_keeps_old_feed(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as feed:
            feed.writestr("stops.txt", "stop_id,stop_name\nS1,Спирка\n")
        broken = buffer.getvalue().replace(b"S1,", b"X1,", 1)  # грешна контролна сума

        response = mock.Mock(content=broken, raise_for_status=mock.Mock())
        with tempfile.TemporaryDirectory() as folder:
            old_feed = Path(folder) / "софия.zip"
            old_feed.write_bytes(b"old")
            with mock.patch.object(transit, "FEED_DIR", Path(folder)), \
                    mock.patch.object(transit.requests, "get", return_value=response):
                self.assertFalse(transit.download_feed("София"))
            self.assertEqual(old_feed.read_bytes(), b"old")

    def test_same_line_change_counts_as_transfer(self):
        table = transit.Timetable(stops=[], connections=[], trip_labels=["автобус 94", "автобус 94"])
        steps = {
            0: transit.WalkFromStart(60),
            1: transit.Ride(trip=0, boarded_at=0),
            2: transit.Ride(trip=1, boarded_at=1),
        }
        lines, walking_seconds = transit.trace_back(table, steps, 2)
        self.assertEqual(lines, ["автобус 94", "автобус 94"])
        self.assertEqual(walking_seconds, 60)
