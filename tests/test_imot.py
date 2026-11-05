"""Тестове върху записани HTML файлове: ще гърмят, ако imot.bg смени вида си."""

import functools
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
from agno.exceptions import ModelProviderError

from imotko import models, notify, search as se, store as st
from imotko.sources import imot_bg as sc

FIXTURES = Path(__file__).parent / "fixtures"


@functools.cache
def _listing_items():
    return sc.parse_listing_page((FIXTURES / "listing_sofia_2.html").read_text(encoding="utf-8"))


def listing_items():
    return [dict(i) for i in _listing_items()]


@functools.cache
def detail():
    return sc.parse_detail((FIXTURES / "detail_nadezhda.html").read_text(encoding="utf-8"))


class ListingParsing(unittest.TestCase):
    def test_parses_most_items(self):
        items = listing_items()
        self.assertGreaterEqual(len(items), 5)
        for i in items:
            self.assertGreater(i["price_eur"], 1000)
            self.assertTrue(i["url"].startswith("https://www.imot.bg/obiava-"))
            self.assertTrue(i["district"])

    def test_known_listing(self):
        i = next(i for i in listing_items() if i["id"] == "1b178854547278550")
        self.assertEqual(i["price_eur"], 112000)
        self.assertEqual(i["area_m2"], 64)
        self.assertEqual((i["floor"], i["floors_total"]), (2, 4))
        self.assertEqual(i["eur_per_m2"], 1750)

    def test_ground_floor_in_listing(self):
        # в листинга партерът се пише "Партер от N", без "ет."
        self.assertEqual(sc.parse_listing_floor("72 кв.м, Партер от 9, Тухла"), (0, 9))
        self.assertEqual(sc.parse_listing_floor("40 кв.м, Сутерен от 5"), (-1, 5))
        self.assertEqual(sc.parse_listing_floor("64 кв.м, 2-ри ет. от 4"), (2, 4))

    def test_price(self):
        self.assertEqual(sc.parse_price("112 000 €"), 112000)
        self.assertEqual(sc.parse_price("125 000 € / 244 479 лв."), 125000)  # двете валути
        self.assertEqual(sc.parse_price("1 250,50 €"), 1250.5)
        self.assertEqual(sc.parse_price("195 583 лв."), 100000)
        self.assertIsNone(sc.parse_price("Цена по договаряне"))


class DetailParsing(unittest.TestCase):
    def test_detail(self):
        d = detail()
        self.assertEqual((d["floor"], d["floors_total"]), (2, 4))
        self.assertEqual(d["construction"], "панел")
        self.assertTrue(d["has_parking"])
        self.assertFalse(d["has_elevator"])
        self.assertFalse(d["in_construction"])
        self.assertEqual(d["year_built"], 1960)

    def test_photos_belong_to_listing(self):
        photos = detail()["photos"]
        self.assertGreaterEqual(len(photos), 1)
        self.assertTrue(all("1b178854547278550_" in p and "/big/" in p for p in photos))

    def test_negations(self):
        d = sc.parse_detail('<div class="moreInfo"><div class="text">Без паркинг, няма асансьор, не е обзаведен.</div></div>')
        self.assertFalse(d["has_parking"])
        self.assertFalse(d["has_elevator"])
        self.assertFalse(d["furnished"])
        d = sc.parse_detail('<div class="moreInfo"><div class="text">Има асансьор и паркомясто, напълно обзаведен.</div></div>')
        self.assertTrue(d["has_parking"] and d["has_elevator"] and d["furnished"])
        self.assertFalse(sc.parse_detail('<div class="moreInfo"><div class="text">Необзаведен.</div></div>')["furnished"])

    def test_not_yet_in_use_is_in_construction(self):
        d = sc.parse_detail('<div class="adParams">Етаж: 3-ти от 6 Строителство: Тухла, Не е въведен в експлоатация</div>')
        self.assertTrue(d["in_construction"])

    def test_basement(self):
        self.assertEqual(sc.parse_detail('<div class="adParams">Етаж: Сутерен от 5</div>')["floor"], -1)


class Created(unittest.TestCase):
    def test_created_from_id(self):
        ts = sc.created_timestamp("2c179319958045934")  # Публикувана 28 окт 2026
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(ts)), "2026-10-28")
        self.assertIsNone(sc.created_timestamp("garbage"))
        self.assertIsNone(sc.created_timestamp("1b999999999999999"))  # в бъдещето

    def test_all_fixture_items_have_date(self):
        self.assertTrue(all(i["created"] for i in listing_items()))

    def test_slightly_future_created_is_today(self):
        self.assertEqual(se.days_since_created({"created": time.time() + 600}), 0)


class Slugs(unittest.TestCase):
    def test_city_slug(self):
        self.assertEqual(sc.city_slug("София"), "grad-sofiya")
        self.assertEqual(sc.city_slug("Стара Загора"), "grad-stara-zagora")
        self.assertEqual(sc.city_slug("Пловдив"), "grad-plovdiv")
        self.assertEqual(sc.city_slug("Бургас"), "grad-burgas")
        self.assertEqual(sc.city_slug("Шумен"), "grad-shumen")
        self.assertEqual(sc.city_slug("grad-ruse"), "grad-ruse")

    def test_bad_filter(self):
        with self.assertRaises(ValueError):
            sc.listing_url("продажба", "София", "пет")

    def test_listing_id(self):
        self.assertEqual(sc.listing_id("https://www.imot.bg/obiava-2c179319958045934-dava-pod-naem"), "2c179319958045934")
        self.assertIsNone(sc.listing_id("https://example.com/?u=https://www.imot.bg/obiava-1-x"))


class Scoring(unittest.TestCase):
    base = {"eur_per_m2": 1500, "price_eur": 100000, "area_m2": 66, "floor": 3, "floors_total": 6,
            "year_built": None}

    def score(self, listing, priorities=frozenset()):
        return se.score_listing(listing, 2000, {}, set(priorities))[0]

    def test_cheaper_scores_higher(self):
        self.assertGreater(self.score(dict(self.base)), self.score(dict(self.base, eur_per_m2=2000)))

    def test_last_floor_penalised(self):
        self.assertGreater(self.score(dict(self.base)), self.score(dict(self.base, floor=6)))

    def test_basement_penalised(self):
        self.assertGreater(self.score(dict(self.base)), self.score(dict(self.base, floor=-1)))

    def test_priority_boosts(self):
        a = dict(self.base, has_parking=True)
        self.assertGreater(self.score(a, {"паркинг"}), self.score(a))

    def test_unknown_elevator_not_penalised(self):
        # преди страницата на обявата да е заредена, липсата на асансьор не е известна
        self.assertGreater(self.score(dict(self.base)), self.score(dict(self.base, has_elevator=False)))

    def test_furnished_filter(self):
        f = {"require_furnished": True}
        self.assertFalse(se._passes_detail_filters(dict(self.base, furnished=False), f))
        self.assertTrue(se._passes_detail_filters(dict(self.base, furnished=True), f))
        self.assertFalse(se._passes_detail_filters(dict(self.base), f))  # страницата не се е заредила

    def test_order_by_date(self):
        old = dict(self.base, score=90, created=time.time() - 30 * 86400)
        new = dict(self.base, score=10, created=time.time() - 86400)
        by_date = sorted([old, new], key=se.SORT_ORDERS["date_created"], reverse=True)
        by_score = sorted([old, new], key=se.SORT_ORDERS["score"], reverse=True)
        self.assertIs(by_date[0], new)
        self.assertIs(by_score[0], old)

    def test_bad_input_fails_before_scraping(self):
        for kwargs in ({"order_by": "price"}, {"priorities": ["басейн"]}, {"construction": "дърво"},
                       {"requre_parking": True}):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                se.run_search("София", "2", "продажба", 1, 1, **kwargs)

    def test_recent_listing_gets_bonus(self):
        fresh = dict(self.base, created=time.time() - 86400)
        stale = dict(self.base, created=time.time() - 60 * 86400)
        self.assertGreater(self.score(fresh), self.score(stale))

    def test_dedupe_keeps_cheapest(self):
        a = dict(self.base, district="Център", id="a")
        b = dict(self.base, district="Център", id="b", price_eur=90000)
        out = se.remove_duplicates([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "b")
        self.assertEqual(out[0]["duplicates"], 1)

    def test_format_price_per_m2(self):
        self.assertEqual(se.format_price_per_m2(1461.04, "продажба"), "1461")
        self.assertEqual(se.format_price_per_m2(7.06, "наем"), "7.1")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(st, "DB_PATH", Path(self.tmp.name) / "t.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        st._create_schema.cache_clear()
        self.addCleanup(st._create_schema.cache_clear)

    def test_price_change_recorded(self):
        listing = {"id": "x", "url": "u", "title": "t", "location": "l", "price_eur": 100000.0}
        st.record_prices([dict(listing)], "продажба")
        changed = dict(listing, price_eur=90000.0)
        st.record_prices([changed], "продажба")
        self.assertEqual((changed["first_price"], changed["price_change_pct"]), (100000.0, -10.0))

    def test_seen_roundtrip_and_reset(self):
        st.save_search("s", {"city": "София"})
        st.mark_seen("s", ["a", "b"])
        st.mark_seen("s", ["b", "c"])
        self.assertEqual(st.list_searches()[0]["seen"], {"a", "b", "c"})
        st.save_search("s", {"city": "Варна"})
        self.assertEqual(st.list_searches()[0]["seen"], set())


class SavedSearch(unittest.TestCase):
    def test_save_as_stores_full_query(self):
        from imotko import agent as ag

        res = {"results": [{"id": "a"}], "meta": {}}
        with (
            mock.patch.object(ag.search, "run_search", return_value=res) as run,
            mock.patch.object(ag.search, "format_results", return_value="ok"),
            mock.patch.object(ag.store, "save_search") as save,
            mock.patch.object(ag.store, "mark_seen") as seen,
        ):
            ag.search_apartments("София", "2", construction="тухла", require_parking=True,
                                 pages=2, top=3, save_as="t")
        name, query = save.call_args.args
        self.assertEqual(name, "t")
        self.assertEqual((query["construction"], query["require_parking"]), ("тухла", True))
        # проверката пуска същата заявка, затова страниците и броят се пазят
        self.assertEqual((query["pages"], query["top"]), (2, 3))
        self.assertNotIn("save_as", query)
        self.assertEqual(run.call_args.kwargs, query)
        seen.assert_called_once_with("t", ["a"])

    def test_check_saved_survives_errors_and_does_not_mark(self):
        saved = [{"name": "bad", "params": {"city": "София", "rooms": "2", "deal": "продажба"}, "seen": set()},
                 {"name": "ok", "params": {"city": "Варна", "rooms": "2", "deal": "продажба"}, "seen": {"old"}}]
        good = {"results": [{"id": "old", "score": 50}, {"id": "new", "score": 50}, {"id": "weak", "score": 1}],
                "meta": {"errors": []}}

        def fake_run(**params):
            if params["city"] == "София":
                raise requests.ConnectionError("timeout")
            return good

        with (
            mock.patch.object(se.store, "list_searches", return_value=saved),
            mock.patch.object(se, "run_search", side_effect=fake_run),
            mock.patch.object(se, "format_results", return_value="txt"),
            mock.patch.object(se.store, "mark_seen") as mark,
        ):
            reports = se.check_saved_searches()
            mark.assert_not_called()  # нищо не се отбелязва преди доставката
            se.mark_as_delivered(reports)
        self.assertIn("грешка", reports[0]["text"])
        self.assertEqual(reports[1]["new_ids"], ["new"])  # слабата обява не се отбелязва и може да стане изгодна
        mark.assert_any_call("ok", ["new"])

    def test_check_saved_reports_fetch_errors(self):
        saved = [{"name": "s", "params": {"city": "София", "rooms": "2", "deal": "продажба"}, "seen": set()}]
        blind = {"results": [], "meta": {"errors": ["https://www.imot.bg/...: 429"]}}
        with (
            mock.patch.object(se.store, "list_searches", return_value=saved),
            mock.patch.object(se, "run_search", return_value=blind),
        ):
            self.assertIn("не отговори", se.check_saved_searches()[0]["text"])


class Watch(unittest.TestCase):
    def test_split_message_on_lines(self):
        text = "\n".join("ред %d " % i + "x" * 100 for i in range(100))
        parts = notify.split_message(text, 1000)
        self.assertTrue(all(len(part) <= 1000 for part in parts))
        self.assertEqual("".join(parts), text)

    def test_notify_sends_cyrillic_title_as_json(self):
        with mock.patch.object(notify.requests, "post") as post:
            notify.NtfyNotifier("t").notify(["Нови"])
        self.assertEqual(post.call_args.kwargs["json"]["title"], "Нови обяви в imot.bg")
        self.assertNotIn("headers", post.call_args.kwargs)


class Notifiers(unittest.TestCase):
    def test_no_topic_means_no_notifier(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(notify.default_notifier())
        with mock.patch.dict("os.environ", {"NTFY_TOPIC": "t"}):
            self.assertIsInstance(notify.default_notifier(), notify.NtfyNotifier)

    def test_custom_channel_only_implements_send(self):
        class Memory(notify.Notifier):
            def __init__(self):
                self.sent = []

            def send(self, message):
                self.sent.append(message)

        channel = Memory()
        channel.notify(["a", "b"])
        self.assertEqual(channel.sent, ["a", "b"])


class Models(unittest.TestCase):
    def test_retired_free_model_counts_as_unavailable(self):
        for status, expected in ((404, 503), (400, 503), (402, 503), (401, 401), (429, 429)):
            error = ModelProviderError("x", status_code=status, model_name="m", model_id="m")
            self.assertEqual(models._mark_as_unavailable(error).status_code, expected)


if __name__ == "__main__":
    unittest.main()
