"""Тестове върху записани HTML файлове: ще гърмят, ако imot.bg смени вида си."""

import functools
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
from agno.exceptions import ModelProviderError

from imotko import models, notify
from imotko import store as st
from imotko.search import formatting, requirements, saved, scan, scoring
from imotko.sources import imot_bg as sc
from tests.helpers import TempDatabase

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
        self.assertEqual(scoring.days_since_created({"created": time.time() + 600}), 0)


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
        return scoring.score_listing(listing, 2000, {}, set(priorities))[0]

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
        self.assertFalse(scan._passes_detail_filters(dict(self.base, furnished=False), f))
        self.assertTrue(scan._passes_detail_filters(dict(self.base, furnished=True), f))
        self.assertFalse(scan._passes_detail_filters(dict(self.base), f))  # страницата не се е заредила

    def test_order_by_date(self):
        old = dict(self.base, score=90, created=time.time() - 30 * 86400)
        new = dict(self.base, score=10, created=time.time() - 86400)
        by_date = sorted([old, new], key=scan.SORT_ORDERS["date_created"], reverse=True)
        by_score = sorted([old, new], key=scan.SORT_ORDERS["score"], reverse=True)
        self.assertIs(by_date[0], new)
        self.assertIs(by_score[0], old)

    def test_bad_input_fails_before_scraping(self):
        for kwargs in ({"order_by": "price"}, {"priorities": ["басейн"]}, {"construction": "дърво"},
                       {"requre_parking": True}):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                scan.run_search("София", "2", "продажба", 1, 1, **kwargs)

    def test_recent_listing_gets_bonus(self):
        fresh = dict(self.base, created=time.time() - 86400)
        stale = dict(self.base, created=time.time() - 60 * 86400)
        self.assertGreater(self.score(fresh), self.score(stale))

    def test_dedupe_keeps_cheapest(self):
        a = dict(self.base, district="Център", id="a")
        b = dict(self.base, district="Център", id="b", price_eur=90000)
        out = scan.remove_duplicates([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "b")
        self.assertEqual(out[0]["duplicates"], 1)

    def test_dedupe_keeps_far_prices_apart(self):
        # еднакви панелни блокове: същите квартал, квадратура и етаж, но различни жилища
        a = dict(self.base, district="Дружба 2", id="a", price_eur=171900)
        b = dict(self.base, district="Дружба 2", id="b", price_eur=219800)
        self.assertEqual({listing["id"] for listing in scan.remove_duplicates([a, b])}, {"a", "b"})

    def test_district_matches_whole_name_only(self):
        lyulin_10 = {"location": "град София, Люлин 10"}
        self.assertFalse(requirements.in_district(lyulin_10, "Люлин 1"))
        self.assertTrue(requirements.in_district(lyulin_10, "люлин 10"))

    def test_format_price_per_m2(self):
        self.assertEqual(formatting.format_price_per_m2(1461.04, "продажба"), "1461")
        self.assertEqual(formatting.format_price_per_m2(7.06, "наем"), "7.1")


class Store(TempDatabase):
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
        from imotko.agent import tools as ag

        res = {"results": [{"id": "a"}], "meta": {}}
        with (
            mock.patch.object(ag.scan, "run_search", return_value=res) as run,
            mock.patch.object(ag.formatting, "format_results", return_value="ok"),
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
        searches = [{"name": "bad", "params": {"city": "София", "rooms": "2", "deal": "продажба"}, "seen": set()},
                 {"name": "ok", "params": {"city": "Варна", "rooms": "2", "deal": "продажба"}, "seen": {"old"}}]
        good = {"results": [{"id": "old", "score": 50}, {"id": "new", "score": 50}, {"id": "weak", "score": 1}],
                "meta": {"errors": []}}

        def fake_run(**params):
            if params["city"] == "София":
                raise requests.ConnectionError("timeout")
            return good

        with (
            mock.patch.object(st, "list_searches", return_value=searches),
            mock.patch.object(scan, "run_search", side_effect=fake_run),
            mock.patch.object(formatting, "format_results", return_value="txt"),
            mock.patch.object(st, "mark_seen") as mark,
        ):
            reports = saved.check_saved_searches()
            mark.assert_not_called()  # нищо не се отбелязва преди доставката
            saved.mark_as_delivered(reports)
        self.assertIn("грешка", reports[0]["text"])
        self.assertEqual(reports[1]["new_ids"], ["new"])  # слабата обява не се отбелязва и може да стане изгодна
        mark.assert_any_call("ok", ["new"])

    def test_check_saved_reports_fetch_errors(self):
        searches = [{"name": "s", "params": {"city": "София", "rooms": "2", "deal": "продажба"}, "seen": set()}]
        blind = {"results": [], "meta": {"errors": ["https://www.imot.bg/...: 429"]}}
        with (
            mock.patch.object(st, "list_searches", return_value=searches),
            mock.patch.object(scan, "run_search", return_value=blind),
        ):
            self.assertIn("не отговори", saved.check_saved_searches()[0]["text"])


class Watch(unittest.TestCase):
    def test_split_message_on_lines(self):
        text = "\n".join("ред %d " % i + "x" * 100 for i in range(100))
        parts = notify.split_message(text, 1000)
        self.assertTrue(all(len(part) <= 1000 for part in parts))
        self.assertEqual("".join(parts), text)

    def test_parts_fit_ntfy_byte_limit(self):
        # ntfy праща по-голямо от 4096 байта като файл, а кирилицата е 2 байта на буква
        text = "\n".join("   до Бизнес парк София: ~24 мин с кола (по адрес)" for _ in range(300))
        self.assertTrue(all(len(part.encode()) <= 4096 for part in notify.split_message(text)))

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


class EmptyPagesAreNotCached(unittest.TestCase):
    """При временен проблем imot.bg връща празни страници. Те не бива да остават в кеша."""

    def setUp(self):
        for patcher in (
            mock.patch.dict(sc._page_cache, clear=True),
            mock.patch.object(sc, "_known_city_slugs", frozenset()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def page(html: str):
        return mock.Mock(content=html.encode("cp1251", errors="replace"))

    def test_empty_listing_page_is_downloaded_again(self):
        empty = self.page("<html><div>Няма намерени обяви!</div></html>")
        full = self.page((FIXTURES / "listing_sofia_2.html").read_text(encoding="utf-8"))
        sc._known_city_slugs = frozenset({"grad-sofiya"})

        with mock.patch.object(sc, "http_get", side_effect=[empty, full]) as http_get:
            during_problem, _ = sc.fetch_listings("продажба", "София", "2", 1)
            after_problem, _ = sc.fetch_listings("продажба", "София", "2", 1)
            from_cache, _ = sc.fetch_listings("продажба", "София", "2", 1)

        self.assertEqual(during_problem, [])
        self.assertGreater(len(after_problem), 0)
        self.assertEqual(len(from_cache), len(after_problem))
        self.assertEqual(http_get.call_count, 2)

    def test_city_list_is_not_remembered_when_empty(self):
        broken = self.page("<html>грешка</html>")
        working = self.page('<a href="/obiavi/prodazhbi/grad-sofiya">София</a>')

        with mock.patch.object(sc, "http_get", side_effect=[broken, working]):
            with self.assertRaises(requests.RequestException):
                sc.known_city_slugs()
            self.assertEqual(sc.known_city_slugs(), frozenset({"grad-sofiya"}))


class Requirements(unittest.TestCase):
    """Филтрите и редът "условия" ползват едни и същи проверки."""

    @staticmethod
    def listing(**fields):
        base = {"price_eur": 600.0, "area_m2": 70.0, "location": "град София, Лозенец", "info": "",
                "floor": 5, "floors_total": 8, "has_parking": False, "has_elevator": True, "has_terrace": True}
        return {**base, **fields}

    def statuses(self, listing, filters):
        from imotko.search import requirements
        return [(check.status, check.text) for check in requirements.all_checks(listing, filters)]

    def test_parking_allows_higher_rent(self):
        from imotko.search import requirements
        filters = {"max_price_eur": 650, "max_price_with_parking_eur": 750}
        expensive = self.listing(price_eur=700.0)
        expensive_with_parking = self.listing(price_eur=700.0, has_parking=True)

        # от списъка още не се знае дали има паркинг: обявата се проверява в детайли
        self.assertTrue(requirements.passes(requirements.list_page_checks(expensive, filters)))
        self.assertFalse(requirements.passes(requirements.all_checks(expensive, filters)))
        self.assertTrue(requirements.passes(requirements.all_checks(expensive_with_parking, filters)))
        self.assertFalse(requirements.passes(requirements.list_page_checks(self.listing(price_eur=800.0), filters)))
        self.assertIn("защото има паркинг", self.statuses(expensive_with_parking, filters)[0][1])

    def test_districts_match_location_not_street(self):
        from imotko.search import requirements
        on_vitosha_boulevard = self.listing(location="град София, Център", info="бул. Витоша 20")
        filters = {"district": ["Лозенец", "Иван Вазов"], "exclude_districts": ["Витоша"]}

        self.assertFalse(requirements.passes(requirements.all_checks(on_vitosha_boulevard, filters)))
        self.assertTrue(requirements.passes(requirements.all_checks(self.listing(), filters)))
        self.assertTrue(requirements.passes(requirements.all_checks(on_vitosha_boulevard, {"exclude_districts": ["Витоша"]})))
        self.assertFalse(requirements.passes(requirements.all_checks(self.listing(location="град София, Витоша"),
                                                                     {"exclude_districts": ["Витоша"]})))
        # по-старите запазени търсения пазят един квартал като текст
        self.assertTrue(requirements.passes(requirements.all_checks(self.listing(), {"district": "Лозенец"})))

    def test_unknown_floor_passes_but_is_said(self):
        statuses = self.statuses(self.listing(floor=None, floors_total=None), {"exclude_first_floor": True})
        self.assertEqual(statuses, [("?", "етажът не е посочен в обявата")])

    def test_missing_required_feature_fails(self):
        from imotko.search import requirements
        checks = requirements.all_checks(self.listing(has_parking=False), {"require_parking": True})
        self.assertFalse(requirements.passes(checks))
        self.assertEqual(checks[0].text, "паркинг: не пише в обявата")

    def test_applied_filters_text(self):
        from imotko.search import requirements
        text = requirements.filters_text(
            {"max_price_eur": 650, "max_price_with_parking_eur": 750, "min_area_m2": 60, "district": ["Лозенец"],
             "exclude_districts": ["Витоша"], "exclude_first_floor": True, "require_terrace": True},
            {"високетаж", "паркинг"}, ["SAP Labs Bulgaria, бул. Цариградско шосе 136А"])
        self.assertIn("цена до 650 € (с паркинг до 750 €)", text)
        self.assertIn("над 60 кв.м", text)
        self.assertIn("квартали: Лозенец", text)
        self.assertIn("без: Витоша", text)
        self.assertIn("задължително тераса", text)
        self.assertIn("висок етаж", text)
        self.assertIn("SAP Labs Bulgaria", text)

    def test_high_floor_priority_gives_points(self):
        listing = {**self.listing(), "eur_per_m2": None, "price_change_pct": None}
        without, _ = scoring.score_listing(listing, 0, {}, set())
        with_priority, reasons = scoring.score_listing(listing, 0, {}, {"високетаж"})
        self.assertEqual(with_priority - without, scoring.HIGH_FLOOR_POINTS)
        self.assertIn("висок етаж (5)", reasons)
        self.assertEqual(scoring.normalize_priorities(["висок етаж"]), {"високетаж"})

    def test_short_commute_labels(self):
        from imotko.geo import enrich
        labels = enrich.commute_labels(["SAP Labs Bulgaria, бул. Цариградско шосе 136А", "бул. Цар Борис III 81"])
        self.assertEqual(list(labels.values()), ["SAP Labs Bulgaria", "бул. Цар Борис III 81"])
        same = enrich.commute_labels(["Бизнес парк, сграда 1", "Бизнес парк, сграда 7"])
        self.assertEqual(list(same.values()), ["Бизнес парк, сграда 1", "Бизнес парк, сграда 7"])
