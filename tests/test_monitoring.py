"""Тестове за следенето на отговорите, калибрирането на времето и проверките. Без мрежа."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import watch
from imotko import store
from imotko.agent import hooks, tools
from imotko.geo import common, driving, geocode
from imotko.quality import checks, monitoring
from tests.helpers import TempDatabase

ELIN_VRAH_TRIP = {"from": "ул. Елин Връх", "to": "УНСС", "city": "София", "real_minutes": 20, "osrm_minutes": 9.0,
                  "from_point": {"lat": 42.668, "lon": 23.315}, "to_point": {"lat": 42.650, "lon": 23.348}}


def fake_run(content, tool_results=(), question="въпрос"):
    tools = [SimpleNamespace(tool_name="distance_between", result=result) for result in tool_results]
    return SimpleNamespace(content=content, tools=tools, input=SimpleNamespace(input_content=question))


class Numbers(unittest.TestCase):
    def test_parsing(self):
        text = "117 000 €, 117,000 €, 5.7 км, 5,7 км, 1 561 €/кв.м, https://www.imot.bg/obiava-123456"
        self.assertEqual(monitoring.numbers_in(text), [117000, 117000, 5.7, 5.7, 1561])

    def test_rounded_numbers_are_verified(self):
        tool = "~19 мин (14-24 мин), 5.7 км, 117,000 €, 1561 €/кв.м"
        answer = "Около 19 мин и 6 км. Цената е 117 000 €, 1 560 €/кв.м."
        self.assertEqual(monitoring.unverified_numbers(answer, [tool]), [])

    def test_made_up_numbers_are_caught(self):
        self.assertEqual(monitoring.unverified_numbers("До УНСС е 35 мин.", ["~19 мин, 5.7 км"]), ["35"])

    def test_calculated_difference_is_allowed(self):
        tools = ["1. 550 € | 60 кв.м", "2. 630 € | 73 кв.м"]
        self.assertEqual(monitoring.unverified_numbers("вторият дава 13 кв.м повече за 80 € разлика", tools), [])
        self.assertEqual(monitoring.unverified_numbers("общо 1180 € за двата", tools), [])

    def test_wrong_calculation_is_caught(self):
        tools = ["1. 550 € | 60 кв.м", "2. 630 € | 73 кв.м"]
        self.assertEqual(monitoring.unverified_numbers("за 95 € разлика", tools), ["95"])

    def test_small_numbers_are_ignored(self):
        self.assertEqual(monitoring.unverified_numbers("1. първа, 2-3 обяви, 9 мин", []), [])


class CheckAnswer(TempDatabase):
    def test_unverified_answer_is_flagged_and_saved(self):
        run = fake_run("До УНСС е 35 мин.", ["С кола: ~19 мин, 5.7 км"], "Колко е до УНСС?")
        monitoring.check_answer(run)
        self.assertIn("⚠ Проверка: числата 35", run.content)
        saved = store.list_feedback("unverified_numbers")
        self.assertEqual(saved[0]["data"]["numbers"], ["35"])
        self.assertEqual(saved[0]["question"], "Колко е до УНСС?")

    def test_verified_answer_is_untouched(self):
        run = fake_run("До УНСС е ~19 мин.", ["С кола: ~19 мин, 5.7 км"])
        monitoring.check_answer(run)
        self.assertEqual(run.content, "До УНСС е ~19 мин.")
        self.assertEqual(store.list_feedback(), [])

    def test_numbers_from_question_and_history_count(self):
        earlier = fake_run("", ["Billa ×8, 2291 €/кв.м"])
        session = SimpleNamespace(runs=[earlier])
        run = fake_run("Бюджетът ти е 600 €, а там е 2291 €/кв.м.", [], "Имам 600 евро")
        monitoring.check_answer(run, session)
        self.assertNotIn("⚠", run.content)

    def test_broken_input_never_raises(self):
        monitoring.check_answer(SimpleNamespace(content=None))
        monitoring.check_answer(object())


class CityDriveTime(TempDatabase):
    def test_extra_minutes_from_measured_trips(self):
        trips = [ELIN_VRAH_TRIP, {**ELIN_VRAH_TRIP, "real_minutes": 16, "osrm_minutes": 9.7},
                 {**ELIN_VRAH_TRIP, "real_minutes": 18, "osrm_minutes": 16.1}]
        with mock.patch.object(driving, "load_ground_truth", return_value={"routes": trips}):
            extra, measured = driving.city_extra_minutes()
        self.assertAlmostEqual(extra, 6.3)  # медиана на 11, 6.3 и 1.9
        self.assertEqual(measured, 3)

    def test_default_without_trips(self):
        with mock.patch.object(driving, "load_ground_truth", return_value={}):
            self.assertEqual(driving.city_extra_minutes(), (driving.DEFAULT_CITY_EXTRA_MINUTES, 0))

    def test_short_and_ring_road_routes(self):
        # кратък път из града и дълъг по Околовръстното: и двата са близо до Google
        with mock.patch.object(driving, "city_extra_minutes", return_value=(6.3, 9)):
            short = driving.city_drive_time({"minutes": 6.7, "km": 4.2})["typical"]
            ring_road = driving.city_drive_time({"minutes": 16.1, "km": 14.1})["typical"]
        self.assertAlmostEqual(short, 13.0)
        self.assertAlmostEqual(ring_road, 22.4)

    def test_text_shows_both_times(self):
        with mock.patch.object(driving, "city_extra_minutes", return_value=(6.3, 9)):
            text = driving.drive_text({"minutes": 9.7, "km": 5.9})
        self.assertIn("~16 мин, приблизително (12-20 мин), 5.9 км", text)
        self.assertNotIn("празни", text)

    def test_user_correction_changes_calibration(self):
        start = {"lat": 42.668, "lon": 23.315}
        end = {"lat": 42.650, "lon": 23.348}
        with (
            mock.patch.object(driving, "load_ground_truth", return_value={"routes": [ELIN_VRAH_TRIP]}),
            mock.patch.object(geocode, "locate_any", side_effect=[start, end]),
            mock.patch.object(driving, "drive", return_value={"minutes": 10.0, "km": 6.0}),
        ):
            self.assertEqual(driving.city_extra_minutes(), (11.0, 1))  # кеширано
            text = tools.report_error("10 мин", "30 мин", "Лозенец", "Бизнес парк София", 30)
            extra, measured = driving.city_extra_minutes()
        self.assertEqual(measured, 2)
        self.assertAlmostEqual(extra, (11 + 20) / 2)
        self.assertIn("по 2 проверени пътувания", text)
        saved = store.list_feedback("user_report")[0]["data"]
        self.assertEqual((saved["real_minutes"], saved["osrm_minutes"]), (30, 10.0))

    def test_implausible_minutes_do_not_change_calibration(self):
        with (
            mock.patch.object(geocode, "locate_any", side_effect=[{"lat": 1.0, "lon": 1.0}, {"lat": 2.0, "lon": 2.0}]),
            mock.patch.object(driving, "drive", return_value={"minutes": 10.0, "km": 6.0}),
        ):
            text = tools.report_error("10 мин", "2 мин", "Лозенец", "Бизнес парк София", 2)
        self.assertIn("не приличат", text)
        self.assertEqual(store.list_feedback("user_report")[0]["data"], {})

    def test_correction_without_route_is_still_saved(self):
        text = tools.report_error("грешен квартал", "Лозенец")
        self.assertIn("Записах", text)
        self.assertEqual(store.list_feedback("user_report")[0]["correction"], "Лозенец")


class SumOrDifference(unittest.TestCase):
    def test_sum_and_difference_of_known_numbers(self):
        known = {100.0, 250.0, 40.0}
        self.assertTrue(monitoring._is_sum_or_difference(350, known))
        self.assertTrue(monitoring._is_sum_or_difference(150, known))
        self.assertFalse(monitoring._is_sum_or_difference(500, known))

    def test_number_is_not_summed_with_itself(self):
        self.assertFalse(monitoring._is_sum_or_difference(200, {100.0}))


class ToolChecks(TempDatabase):
    def test_route_within_tolerance(self):
        with (
            mock.patch.object(driving, "drive", return_value={"minutes": 9, "km": 5.7}) as drive,
            mock.patch.object(driving, "city_extra_minutes", return_value=(8.0, 9)),
        ):
            self.assertTrue(checks._check_route(ELIN_VRAH_TRIP)["ok"])
        # точните точки на измерването, без търсене в картата
        drive.assert_called_once_with(ELIN_VRAH_TRIP["from_point"], ELIN_VRAH_TRIP["to_point"])

    def test_route_with_wrong_time_fails(self):
        with (
            mock.patch.object(driving, "drive", return_value={"minutes": 9, "km": 5.7}),
            mock.patch.object(driving, "city_extra_minutes", return_value=(0.0, 0)),  # като по празни улици
        ):
            result = checks._check_route(ELIN_VRAH_TRIP)
        self.assertFalse(result["ok"])
        self.assertIn("реално 20", result["details"])

    def test_missing_place_fails(self):
        trip = {key: value for key, value in ELIN_VRAH_TRIP.items() if not key.endswith("_point")}
        with mock.patch.object(geocode, "locate_any", return_value=None):
            self.assertFalse(checks._check_route(trip)["ok"])


class WeeklyChecks(TempDatabase):
    FAILING = [{"check": "път", "ok": False, "details": "~9 мин (реално 20)"}]

    def run_watch(self, notifier):
        with (
            mock.patch.object(checks, "run_checks", return_value=self.FAILING) as run_checks,
            mock.patch.object(watch, "refresh_chain_stores"),
            mock.patch.object(watch.transit, "refresh_feeds", return_value=[]),
            mock.patch.object(watch.saved, "check_saved_searches", return_value=[]),
            mock.patch.object(watch, "default_notifier", return_value=notifier),
        ):
            try:
                watch.main()
            except RuntimeError:
                pass
        return run_checks.call_count

    def test_runs_once_a_week_and_reports_failures(self):
        notifier = mock.Mock()
        self.assertEqual(self.run_watch(notifier) + self.run_watch(notifier), 1)
        self.assertIn("неверни данни", notifier.notify.call_args_list[0].args[0][0])

    def test_check_runs_again_when_alert_was_not_delivered(self):
        broken = mock.Mock()
        broken.notify.side_effect = RuntimeError("ntfy не отговаря")
        self.assertEqual(self.run_watch(broken), 1)
        self.assertIsNone(store.get_place(watch.LAST_CHECK_KEY))
        self.assertEqual(self.run_watch(mock.Mock()), 1)

    def test_nothing_to_send_when_all_ok(self):
        with mock.patch.object(checks, "run_checks", return_value=[{"check": "x", "ok": True, "details": ""}]):
            self.assertEqual(watch.weekly_tool_checks(), [])



class SharedRateLimit(unittest.TestCase):
    def test_second_process_waits_for_the_first(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(common, "DATA_DIR", Path(folder)),
            mock.patch.object(common.time, "sleep") as sleep,
        ):
            common.RateLimiter(1.1, "test").wait()
            common.RateLimiter(1.1, "test").wait()  # друг процес: свой обект, същият файл
        self.assertEqual(sleep.call_count, 1)


class RepeatedSearch(unittest.TestCase):
    def test_same_search_in_one_answer_is_not_repeated(self):
        found = {"results": [], "meta": {"deal": "наем", "scanned": 1, "after_filter": 0, "detail_checked": 0,
                                         "after_detail": 0, "city_median_ppm": 9.0, "errors": [], "notes": []}}
        first_run = SimpleNamespace(run_id="run-1")
        with mock.patch.object(tools.scan, "run_search", return_value=found) as run_search:
            tools.search_apartments("София", "2", "наем", run_context=first_run)
            repeated = tools.search_apartments("София", "2", "наем", run_context=first_run)
            tools.search_apartments("София", "3", "наем", run_context=first_run)  # други филтри
            tools.search_apartments("София", "2", "наем", run_context=SimpleNamespace(run_id="run-2"))  # нов въпрос
            tools.search_apartments("София", "2", "наем")  # без контекст (тестове, watch)
        self.assertIn("вече е направено", repeated)
        self.assertEqual(run_search.call_count, 4)


class PhotoSize(unittest.TestCase):
    def test_big_photo_is_shrunk_and_small_is_kept(self):
        import io

        from PIL import Image

        from imotko import vision

        def jpeg(size):
            buffer = io.BytesIO()
            Image.new("RGB", size, "white").save(buffer, "JPEG")
            return buffer.getvalue()

        big = vision.shrink_photo(jpeg((3000, 2000)))
        self.assertEqual(Image.open(io.BytesIO(big)).size, (1024, 683))
        small = jpeg((800, 600))
        self.assertIs(vision.shrink_photo(small), small)
        self.assertEqual(vision.shrink_photo(b"not an image"), b"not an image")

    def test_uploaded_images_are_shrunk(self):
        from agno.media import Image as AgnoImage

        image = AgnoImage(content=b"x" * 10, mime_type="image/png")
        with mock.patch.object(hooks.vision, "shrink_photo", return_value=b"small"):
            hooks.shrink_uploaded_images(SimpleNamespace(images=[image]))
        self.assertEqual((image.content, image.mime_type), (b"small", "image/jpeg"))
        hooks.shrink_uploaded_images(SimpleNamespace(images=None))  # без снимки - нищо


if __name__ == "__main__":
    unittest.main()


class RepeatedSearchAfterProblems(TempDatabase):
    found = {"results": [{"id": "a1"}], "meta": {"deal": "наем", "scanned": 1, "after_filter": 1, "detail_checked": 1,
                                                 "after_detail": 1, "city_median_ppm": 9.0, "errors": [], "notes": []}}

    def test_failed_search_can_be_retried(self):
        run = SimpleNamespace(run_id="run-fail")
        with mock.patch.object(tools.scan, "run_search", side_effect=[ValueError("лош филтър"), self.found]) as run_search, \
                mock.patch.object(tools.formatting, "format_results", return_value="резултат"):
            with self.assertRaises(ValueError):
                tools.search_apartments("София", "2", "наем", run_context=run)
            retried = tools.search_apartments("София", "2", "наем", run_context=run)
        self.assertEqual(retried, "резултат")
        self.assertEqual(run_search.call_count, 2)

    def test_repeated_search_with_save_as_is_saved(self):
        run = SimpleNamespace(run_id="run-save")
        with mock.patch.object(tools.scan, "run_search", return_value=self.found) as run_search, \
                mock.patch.object(tools.formatting, "format_results", return_value="резултат"):
            tools.search_apartments("София", "2", "наем", run_context=run)
            repeated = tools.search_apartments("София", "2", "наем", save_as="двустайни", run_context=run)
        self.assertEqual(run_search.call_count, 1)
        self.assertIn("вече е направено", repeated)
        self.assertIn("е запазено", repeated)
        [saved_search] = store.list_searches()
        self.assertEqual(saved_search["name"], "двустайни")
        self.assertEqual(saved_search["seen"], {"a1"})


class NoRouteCache(TempDatabase):
    def test_no_route_is_remembered_for_a_day(self):
        start, end = {"lat": 42.7, "lon": 23.3}, {"lat": 42.8, "lon": 23.4}
        with mock.patch.object(driving, "_osrm", side_effect=driving.NoRoute("OSRM: NoRoute")) as osrm:
            self.assertIsNone(driving.drive(start, end))
            self.assertIsNone(driving.drive(start, end))
            self.assertEqual(driving.drive_many([start], end), [None])
        self.assertEqual(osrm.call_count, 1)

    def test_network_error_is_not_remembered(self):
        start, end = {"lat": 42.7, "lon": 23.3}, {"lat": 42.8, "lon": 23.4}
        with mock.patch.object(driving, "_osrm", side_effect=driving.requests.ConnectionError()) as osrm:
            driving.drive(start, end)
            driving.drive(start, end)
        self.assertEqual(osrm.call_count, 2)

    def test_no_route_code_is_read_before_http_status(self):
        response = mock.Mock(status_code=400)
        response.json.return_value = {"code": "NoRoute"}
        response.raise_for_status.side_effect = driving.requests.HTTPError("400")
        with mock.patch.object(driving.requests, "get", return_value=response), \
                mock.patch.object(driving._osrm_limit, "wait"):
            with self.assertRaises(driving.NoRoute):
                driving._osrm("/route/v1/driving/x", {})
