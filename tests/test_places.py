"""Тестове за улиците, терасата, картата и предупрежденията. Без истински заявки към мрежата."""

import unittest
from pathlib import Path
from unittest import mock

import requests

from imotko.geo import common, distance, driving, enrich, geocode, shops
from imotko.search import formatting, scan, scoring
from imotko.sources import imot_bg
from tests.helpers import TempDatabase

FIXTURES = Path(__file__).parent / "fixtures"

SOFIA = {"found": True, "lat": 42.6977, "lon": 23.3219, "box": "23.19,42.79,23.47,42.60"}

# квадрат около Младост 1 (lon, lat)
MLADOST_AREA = {
    "type": "Polygon",
    "coordinates": [[[23.36, 42.64], [23.39, 42.64], [23.39, 42.66], [23.36, 42.66], [23.36, 42.64]]],
}


class Street(unittest.TestCase):
    def test_street_from_listing_info(self):
        items = imot_bg.parse_listing_page((FIXTURES / "listing_sofia_2.html").read_text(encoding="utf-8"))
        listing = next(item for item in items if item["id"] == "1b178854547278550")
        self.assertEqual(listing["street"], "ул. Ген. Никола Жеков")

    def test_no_street_is_none(self):
        items = imot_bg.parse_listing_page((FIXTURES / "listing_sofia_2.html").read_text(encoding="utf-8"))
        self.assertTrue(any(item["street"] is None for item in items))

    def test_map_link_uses_street(self):
        listing = {"location": "град София, Надежда 4", "street": "ул. Ген. Никола Жеков"}
        link = formatting._map_link(listing)
        self.assertIn("query=ул.+Ген.+Никола+Жеков%2C+град+София%2C+Надежда+4", link)  # кирилицата не е кодирана


class Terrace(unittest.TestCase):
    def detail(self, text):
        return imot_bg.parse_detail(f'<div class="moreInfo"><div class="text">{text}</div></div>')

    def test_terrace(self):
        self.assertTrue(self.detail("Голяма тераса с гледка.")["has_terrace"])
        self.assertTrue(self.detail("Два балкона.")["has_terrace"])
        self.assertFalse(self.detail("Френски балкон в спалнята.")["has_terrace"])
        self.assertFalse(self.detail("Без тераса.")["has_terrace"])

    def test_terrace_scores_only_as_priority(self):
        listing = {"eur_per_m2": 1500, "price_eur": 100000, "area_m2": 66, "floor": 3, "floors_total": 6,
                   "has_terrace": True}
        plain = scoring.score_listing(dict(listing), 2000, {}, set())[0]
        without = scoring.score_listing({**listing, "has_terrace": None}, 2000, {}, set())[0]
        wanted = scoring.score_listing(dict(listing), 2000, {}, {"тераса"})[0]
        self.assertEqual(plain, without)
        self.assertGreater(wanted, plain)


class Geometry(unittest.TestCase):
    def test_distance(self):
        sofia_center = {"lat": 42.6977, "lon": 23.3219}
        plovdiv = {"lat": 42.1354, "lon": 24.7453}
        self.assertAlmostEqual(common.distance_km(sofia_center, plovdiv), 132, delta=3)

    def test_inside_polygon(self):
        self.assertTrue(common.is_inside({"lat": 42.65, "lon": 23.37}, MLADOST_AREA))
        self.assertFalse(common.is_inside({"lat": 42.70, "lon": 23.32}, MLADOST_AREA))

    def test_chain_names(self):
        self.assertEqual(shops._chain_name({"brand": "Lidl"}), "Lidl")
        self.assertEqual(shops._chain_name({"name": "Кауфланд Младост"}), "Kaufland")
        self.assertEqual(shops._chain_name({"name": "T MARKET"}), "T Market")
        self.assertIsNone(shops._chain_name({"name": "Автомобила"}))
        self.assertIsNone(shops._chain_name({"name": "Квартален магазин"}))

    def test_chains_in_district_by_border_and_by_circle(self):
        stores = [
            {"chain": "Lidl", "lat": 42.65, "lon": 23.37},
            {"chain": "Billa", "lat": 42.655, "lon": 23.38},
            {"chain": "Billa", "lat": 42.645, "lon": 23.385},
            {"chain": "Kaufland", "lat": 42.70, "lon": 23.32},  # в центъра, извън квартала
        ]
        with_border = {"lat": 42.65, "lon": 23.375, "area": MLADOST_AREA}
        self.assertEqual(shops.chains_in_district(with_border, stores),
                         {"counts": {"Lidl": 1, "Billa": 2}, "area": "граница"})

        no_border = {"lat": 42.70, "lon": 23.32, "area": None}
        self.assertEqual(shops.chains_in_district(no_border, stores)["counts"], {"Kaufland": 1})


class Locate(TempDatabase):
    def test_wrong_city_is_flagged(self):
        village = [{"lat": "42.9789", "lon": "27.3211", "class": "place", "type": "village",
                    "display_name": "Аспарухово, Дългопол, Варна, България"}]
        with (
            mock.patch.object(geocode, "city_area", return_value={"found": True, "lat": 43.2141, "lon": 27.9147,
                                                                 "box": "27.80,43.30,27.99,43.15"}),
            mock.patch.object(geocode, "_nominatim", return_value=village),
        ):
            found = geocode.locate("Аспарухово", "Варна", "district")
        self.assertTrue(any("над 25 км" in warning for warning in found["warnings"]))

    def test_building_instead_of_district_is_flagged(self):
        church = [{"lat": "42.71", "lon": "23.25", "class": "amenity", "type": "place_of_worship",
                   "display_name": "Храм, Люлин 5, София"}]
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=church),
        ):
            found = geocode.locate("Люлин 5", "София", "district")
        self.assertIsNone(found["area"])
        self.assertTrue(any("не самият квартал" in warning for warning in found["warnings"]))

    def test_results_are_cached_and_misses_too(self):
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=[]) as nominatim,
        ):
            self.assertIsNone(geocode.locate("Несъществуващо", "София", "district"))
            calls_first_time = nominatim.call_count
            self.assertIsNone(geocode.locate("Несъществуващо", "София", "district"))
        self.assertEqual(nominatim.call_count, calls_first_time)  # второто идва от кеша

    def test_district_tries_osm_names(self):
        station = [{"lat": "42.65", "lon": "23.37", "class": "railway", "display_name": "Младост 1, София"}]
        district = [{"lat": "42.65", "lon": "23.375", "class": "boundary", "display_name": "ж.к. Младост 1, София",
                     "geojson": MLADOST_AREA}]

        def fake_nominatim(params):
            return district if params["q"].startswith("ж.к.") else station

        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", side_effect=fake_nominatim),
        ):
            found = geocode.locate("Младост 1", "София", "district")
        self.assertTrue(found["is_area"])
        self.assertEqual(found["area"], MLADOST_AREA)
        self.assertFalse(any("не самият квартал" in warning for warning in found["warnings"]))

    def test_place_outside_city_is_tried_without_city_box(self):
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=[]) as nominatim,
        ):
            geocode.locate("Банско", "София")
        first, second = (call.args[0] for call in nominatim.call_args_list)
        self.assertEqual(first.get("bounded"), 1)
        self.assertNotIn("bounded", second)

    def test_network_error_is_not_cached(self):
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", side_effect=requests.ConnectionError("down")) as nominatim,
        ):
            self.assertIsNone(geocode.locate("Младост 1", "София", "district"))
            self.assertIsNone(geocode.locate("Младост 1", "София", "district"))
        self.assertEqual(nominatim.call_count, 2)

    def test_alias_and_street_prefix(self):
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=[]) as nominatim,
        ):
            geocode.locate("УНСС", "София")
            geocode.locate("ул. Враня", "София", "street")
        queries = [call.args[0]["q"] for call in nominatim.call_args_list]
        self.assertEqual(queries[0], "Университет за национално и световно стопанство, София")
        self.assertEqual(queries[-1], "Враня, София")

    def test_address_with_number_is_not_another_street(self):
        # без номера "Цар Борис III" намираше друга улица в Казичене
        kazichene = {"lat": "42.6663", "lon": "23.4624", "class": "highway", "type": "residential",
                     "display_name": "Цар Борис III, Казичене, Район Панчарево, София"}
        address = {"lat": "42.6802", "lon": "23.2883", "class": "leisure", "type": "amusement_arcade",
                   "display_name": "DRS Sim Racing Sofia, 81-83, бул. Цар Борис III, ж.к. Хиподрума, София"}

        def fake_nominatim(params):
            return [address, kazichene] if params["q"].startswith("бул. ") else [kazichene]

        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", side_effect=fake_nominatim),
        ):
            found = geocode.locate_any("бул. Цар Борис III 81", "София")
        self.assertIn("Хиподрума", found["name"])
        self.assertEqual(geocode.precision_text(found), "по адрес")
        self.assertEqual(found["warnings"], [])

    def test_unknown_house_number_falls_back_to_street(self):
        street = {"lat": "42.68", "lon": "23.29", "class": "highway", "type": "primary",
                  "display_name": "бул. Цар Борис III, ж.к. Славия, София"}
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=[street]),
        ):
            found = geocode.locate("бул. Цар Борис III 999", "София", "street")
        self.assertEqual(found["kind"], "street")
        self.assertIn("Славия", found["name"])

    def test_house_number_match(self):
        self.assertTrue(geocode._has_house_number("16, Сребърна, Хладилника, София", "16"))
        self.assertTrue(geocode._has_house_number("DRS, 81-83, бул. Цар Борис III, София", "81"))
        self.assertFalse(geocode._has_house_number("Цар Борис III, Казичене, София", "81"))
        self.assertFalse(geocode._has_house_number("Блок 816, Люлин, София", "81"))

    def test_name_with_made_up_address_is_found_by_name(self):
        sap = {"lat": "42.6597", "lon": "23.3209", "class": "building", "type": "office",
               "display_name": "SAP Center Sofia, 16, Сребърна, Промишлена зона Хладилника, София"}

        def fake_nominatim(params):
            return [sap] if params["q"] == "SAP Center Sofia, София" else []

        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", side_effect=fake_nominatim),
        ):
            found = geocode.locate_any("SAP Labs Bulgaria, бул. Цариградско шосе 136А", "София")
        self.assertIn("Сребърна", found["name"])
        self.assertIn("намерено е само по \"SAP Labs Bulgaria\"", found["warnings"])


class PlaceScoring(unittest.TestCase):
    base = {"eur_per_m2": 2000, "price_eur": 120000, "area_m2": 60, "floor": 3, "floors_total": 6}

    def score(self, **extra):
        return scoring.score_listing({**self.base, **extra}, 2000, {}, set())[0]

    def test_no_map_data_changes_nothing(self):
        self.assertEqual(self.score(), self.score(chains=None))

    def test_chain_variety_counts_not_number(self):
        one_chain_many = self.score(chains={"counts": {"Billa": 5}, "area": "граница"})
        three_chains = self.score(chains={"counts": {"Billa": 1, "Lidl": 1, "Kaufland": 1}, "area": "граница"})
        self.assertGreater(three_chains, one_chain_many)
        self.assertLessEqual(self.score(chains={"counts": dict.fromkeys(shops.CHAINS, 1), "area": "граница"})
                             - self.score(), scoring.MAX_CHAIN_POINTS)

    def test_street_never_lowers_score(self):
        district_only = self.score(chains={"counts": {"Lidl": 1}, "area": "граница"})
        with_street = self.score(chains={"counts": {"Lidl": 1}, "area": "граница"}, chain_near_street=("Lidl", 0.3))
        self.assertGreaterEqual(with_street, district_only)

    def test_commute_scored_by_district_only(self):
        # улицата е далеч, кварталът е близо: оценката е като на съседите без улица
        by_street = {"target": "Бизнес парк", "district_minutes": 8, "minutes": 25, "precision": "улица"}
        by_district = {"target": "Бизнес парк", "district_minutes": 8, "minutes": 8, "precision": "квартал"}
        self.assertEqual(self.score(commute=by_street), self.score(commute=by_district))
        self.assertGreater(self.score(commute=by_district), self.score())

    def test_far_commute_is_not_penalised(self):
        far = {"target": "Летище", "district_minutes": 50, "minutes": 50, "precision": "квартал"}
        self.assertEqual(self.score(commute=far), self.score())


class Warnings(unittest.TestCase):
    def test_suspicious_price(self):
        cheap = {"eur_per_m2": 300, "ref_ppm": 2000, "ref_kind": "квартал"}
        normal = {"eur_per_m2": 1800, "ref_ppm": 2000, "ref_kind": "квартал"}
        self.assertTrue(any("съмнителна цена" in warning for warning in scoring.listing_warnings(cheap)))
        self.assertEqual(scoring.listing_warnings(normal), [])

    def test_contradiction(self):
        listing = {"in_construction": True, "has_act16": True}
        self.assertTrue(any("противоречи" in warning for warning in scoring.listing_warnings(listing)))

    def test_location_warnings_are_included(self):
        listing = {"location_warnings": ["голям квартал"]}
        self.assertEqual(scoring.listing_warnings(listing), ["голям квартал"])

    def test_warnings_are_shown(self):
        listing = {"title": "2-СТАЕН", "location": "град София, Младост 1", "street": None, "price_eur": 100000,
                   "area_m2": 60, "eur_per_m2": 1667, "ref_ppm": 2000, "ref_kind": "квартал", "floor": 3,
                   "floors_total": 6, "score": 10, "reasons": [], "url": "u",
                   "warnings": ["обявата си противоречи"],
                   "chains": {"counts": {"Billa": 2, "Lidl": 1}, "area": "кръг 1 км"},
                   "commute": {"target": "УНСС", "minutes": 12, "district_minutes": 12, "precision": "квартал"}}
        text = formatting._format_listing(1, listing, "продажба")
        self.assertIn("⚠ обявата си противоречи", text)
        self.assertIn("Billa ×2, Lidl", text)
        self.assertIn("кръг 1 км", text)
        self.assertIn("до УНСС: ~12 мин с кола (по квартал)", text)

    def test_several_commute_targets(self):
        self.assertEqual(enrich.commute_targets("УНСС"), ["УНСС"])
        self.assertEqual(enrich.commute_targets(["УНСС", " УНСС", "", "ТУ", "SAP", "НДК", "летище"]),
                         ["УНСС", "ТУ", "SAP", "НДК"])
        self.assertEqual(enrich.commute_targets(None), [])

    def test_map_failure_does_not_stop_search(self):
        with mock.patch.object(enrich, "add_location_data", side_effect=RuntimeError("boom")):
            notes = scan._add_location_data([{"district": "Младост 1"}], "София", None)
        self.assertTrue(notes and "не са достъпни" in notes[0])


class AddLocationData(TempDatabase):
    def test_commute_and_chains(self):
        mladost = {"found": True, "lat": 42.65, "lon": 23.375, "name": "Младост 1, София", "kind": "district",
                   "area": MLADOST_AREA, "warnings": []}
        street = {"found": True, "lat": 42.651, "lon": 23.376, "name": "Враня, София", "kind": "street",
                  "area": None, "far_from_district": False, "warnings": []}
        target = {"found": True, "lat": 42.63, "lon": 23.37, "name": "Бизнес парк София", "kind": "place",
                  "area": None, "warnings": []}

        def fake_locate(place, city, kind="place", near=None):
            return {"district": mladost, "street": street, "place": target}[kind]

        stores = [{"chain": "Lidl", "lat": 42.6512, "lon": 23.3762}, {"chain": "Billa", "lat": 42.655, "lon": 23.38}]
        listings = [{"district": "Младост 1", "street": "ул. Враня"}, {"district": "Младост 1", "street": None}]
        with (
            mock.patch.object(geocode, "locate", side_effect=fake_locate),
            mock.patch.object(shops, "chains_need_refresh", return_value=False),
            mock.patch.object(shops, "chain_stores", return_value=(stores, 3)),
            mock.patch.object(driving, "drive_many", return_value=[{"minutes": 9.0, "km": 3.0},
                                                                  {"minutes": 14.0, "km": 5.0}]),
            mock.patch.object(driving, "city_extra_minutes", return_value=(6.0, 1)),
        ):
            notes = enrich.add_location_data(listings, "София", "Бизнес парк")

        with_street, without_street = listings
        self.assertEqual(with_street["chains"], without_street["chains"])
        self.assertEqual(with_street["chain_near_street"][0], "Lidl")
        self.assertNotIn("chain_near_street", without_street)
        self.assertEqual(with_street["commute"]["district_minutes"], without_street["commute"]["district_minutes"])
        # реалистично време в града: OSRM + 6 мин за светофари и движение
        self.assertAlmostEqual(with_street["commute"]["minutes"], 14.0 + 6.0)
        self.assertEqual(with_street["commute"]["precision"], "улица")
        self.assertAlmostEqual(without_street["commute"]["district_minutes"], 9.0 + 6.0)
        self.assertIn("Бизнес парк София", notes[0])

    def test_two_targets_are_named_and_first_is_scored(self):
        mladost = {"found": True, "lat": 42.65, "lon": 23.375, "name": "Младост 1, София", "kind": "district",
                   "area": MLADOST_AREA, "warnings": []}
        unss = {"found": True, "lat": 42.6505, "lon": 23.3486, "name": "УНСС", "kind": "place", "area": None, "warnings": []}
        tu = {"found": True, "lat": 42.6571, "lon": 23.3553, "name": "ТУ", "kind": "place", "area": None, "warnings": []}

        def fake_locate(place, city, kind="place", near=None):
            return {"district": mladost}.get(kind) or {"УНСС": unss, "ТУ": tu}[place]

        listings = [{"district": "Младост 1", "street": None}]
        with (
            mock.patch.object(geocode, "locate", side_effect=fake_locate),
            mock.patch.object(shops, "chains_need_refresh", return_value=False),
            mock.patch.object(shops, "chain_stores", return_value=None),
            mock.patch.object(driving, "drive_many", side_effect=[[{"minutes": 5.0, "km": 3.0}], [{"minutes": 9.0, "km": 5.0}]]),
            mock.patch.object(driving, "city_extra_minutes", return_value=(6.0, 1)),
        ):
            notes = enrich.add_location_data(listings, "София", ["УНСС", "ТУ"])

        commutes = listings[0]["commutes"]
        self.assertEqual([commute["label"] for commute in commutes], ["УНСС", "ТУ"])
        self.assertIs(listings[0]["commute"], commutes[0])
        self.assertAlmostEqual(commutes[1]["minutes"], 15.0)
        self.assertTrue(any(note.startswith("ТУ: целта \"ТУ\"") for note in notes))
        text = formatting._format_listing(1, {**listings[0], "title": "2-СТАЕН", "location": "Младост 1", "price_eur": 500,
                                          "area_m2": 60, "eur_per_m2": 8.3, "ref_ppm": 8.0, "ref_kind": "квартал",
                                          "score": 10, "reasons": [], "url": "u", "floor": None}, "наем")
        self.assertIn("до УНСС: ~11 мин с кола", text)
        self.assertIn("до ТУ: ~15 мин с кола", text)

    def test_no_chain_data_starts_refresh_and_says_so(self):
        with (
            mock.patch.object(geocode, "locate", return_value=None),
            mock.patch.object(shops, "chains_need_refresh", return_value=True),
            mock.patch.object(shops, "refresh_chains_in_background") as refresh,
            mock.patch.object(shops, "chain_stores", return_value=None),
        ):
            notes = enrich.add_location_data([{"district": "Лозенец"}], "София")
        refresh.assert_called_once_with("София")
        self.assertIn("още се свалят", notes[0])


class ChainsRefresh(TempDatabase):
    def test_overpass_timeout_remark_is_not_saved(self):
        timeout = mock.Mock(ok=True, text="{}")
        timeout.json.return_value = {"elements": [], "remark": "runtime error: Query timed out"}
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(shops.requests, "post", return_value=timeout),
        ):
            self.assertFalse(shops.refresh_chains("София"))
        self.assertIsNone(shops.chain_stores("София"))

    def test_nominatim_error_returns_false(self):
        with mock.patch.object(geocode, "city_area", side_effect=requests.ConnectionError):
            self.assertFalse(shops.refresh_chains("София"))


class DriveManyBatches(TempDatabase):
    def test_large_requests_are_split(self):
        origins = [{"lat": 42.0 + index / 1000, "lon": 23.0} for index in range(120)]
        destination = {"lat": 42.5, "lon": 23.5}

        def fake_osrm(path, params):
            count = int(params["destinations"])
            return {"durations": [[60.0]] * count, "distances": [[1000.0]] * count}

        with mock.patch.object(driving, "_osrm", side_effect=fake_osrm) as osrm:
            routes = driving.drive_many(origins, destination)
        self.assertEqual(osrm.call_count, 3)
        self.assertTrue(all(route == {"minutes": 1.0, "km": 1.0} for route in routes))


class SameStreetName(TempDatabase):
    def test_street_closest_to_district_is_chosen(self):
        banishora = {"lat": 42.7085, "lon": 23.3050}
        two_streets = [
            {"lat": "42.6900", "lon": "23.5500", "class": "highway", "display_name": "Враня, Гниляне, София"},
            {"lat": "42.7090", "lon": "23.3060", "class": "highway", "display_name": "Враня, Банишора, София"},
        ]
        with (
            mock.patch.object(geocode, "city_area", return_value=SOFIA),
            mock.patch.object(geocode, "_nominatim", return_value=two_streets),
        ):
            found = geocode.locate("ул. Враня", "София", "street", near=banishora)
        self.assertIn("Банишора", found["name"])
        self.assertFalse(found["far_from_district"])

    def test_street_far_from_district_is_dropped(self):
        banishora = {"found": True, "lat": 42.7085, "lon": 23.3050, "name": "Банишора, София", "kind": "district",
                     "area": None, "warnings": []}
        far_street = {"found": True, "lat": 42.69, "lon": 23.55, "name": "Враня, Гниляне, София", "kind": "street",
                      "area": None, "far_from_district": True, "warnings": []}

        def fake_locate(place, city, kind="place", near=None):
            return banishora if kind == "district" else far_street

        listings = [{"district": "Банишора", "street": "ул. Враня"}]
        with (
            mock.patch.object(geocode, "locate", side_effect=fake_locate),
            mock.patch.object(shops, "chains_need_refresh", return_value=False),
            mock.patch.object(shops, "chain_stores", return_value=([{"chain": "Lidl", "lat": 42.69, "lon": 23.5501}], 1)),
        ):
            enrich.add_location_data(listings, "София")
        self.assertNotIn("chain_near_street", listings[0])
        self.assertTrue(any("не е намерена в квартала" in warning for warning in listings[0]["location_warnings"]))


class DistanceTool(TempDatabase):
    def test_describes_precision_and_warnings(self):
        start = {"found": True, "lat": 42.65, "lon": 23.375, "name": "Младост 1, София", "kind": "district",
                 "area": None, "warnings": ["голям квартал"]}
        end = {"found": True, "lat": 42.6505, "lon": 23.3486, "name": "УНСС, София", "kind": "place",
               "area": None, "warnings": []}
        with (
            mock.patch.object(geocode, "locate_any", side_effect=[start, end]),
            mock.patch.object(driving, "drive", return_value={"minutes": 6.1, "km": 3.3}),
            mock.patch.object(driving, "city_extra_minutes", return_value=(6.0, 1)),
        ):
            text = distance.describe_distance("Младост 1", "УНСС", "София")
        self.assertIn("по точка на квартала", text)
        self.assertIn("С кола: ~12 мин, приблизително (9-15 мин)", text)
        self.assertNotIn("празни", text)
        self.assertIn("⚠ голям квартал", text)

    def test_many_places_in_one_call(self):
        points = {
            "Лозенец": {"lat": 42.676, "lon": 23.322, "name": "ж.к. Лозенец", "kind": "district", "area": {}, "warnings": []},
            "Младост 1": {"lat": 42.65, "lon": 23.375, "name": "ж.к. Младост 1", "kind": "district", "area": {},
                          "warnings": ["голям квартал"]},
            "УНСС": {"lat": 42.6505, "lon": 23.3486, "name": "УНСС", "kind": "place", "area": None, "warnings": []},
        }
        bus = {"ok": True, "minutes": 28.0, "lines": ["автобус 94"], "transfers": 0, "walk_minutes": 9, "walk_only": False}
        with (
            mock.patch.object(geocode, "locate_any", side_effect=lambda name, city: points.get(name)),
            mock.patch.object(driving, "drive_many", return_value=[{"minutes": 9.0, "km": 4.0}, {"minutes": 7.0, "km": 3.0}]) as drive_many,
            mock.patch.object(driving, "city_extra_minutes", return_value=(6.0, 1)),
            mock.patch.object(distance.transit, "route", return_value=bus),
        ):
            text = distance.describe_distances(["Лозенец", "Младост 1", "Несъществуващо"], ["УНСС"], "София")
        drive_many.assert_called_once()  # една заявка за всички начала
        self.assertIn("Лозенец → УНСС: права линия", text)
        self.assertIn("кола ~15 мин", text)
        self.assertIn("транспорт ~28 мин (автобус 94)", text)
        self.assertIn("Младост 1 → УНСС", text)
        self.assertIn("⚠ голям квартал", text)
        self.assertIn("Несъществуващо: не е намерено", text)

    def test_one_pair_keeps_full_text(self):
        with mock.patch.object(distance, "describe_distance", return_value="пълен текст") as single:
            self.assertEqual(distance.describe_distances(["А"], ["Б"], "София"), "пълен текст")
        single.assert_called_once_with("А", "Б", "София")

    def test_not_found_says_not_to_guess(self):
        with mock.patch.object(geocode, "locate_any", return_value=None):
            text = distance.describe_distance("Нещо", "Друго", "София")
        self.assertIn("Не давай разстояние по памет", text)

    def test_unusual_drive(self):
        self.assertTrue(driving.unusual_drive({"minutes": 1, "km": 10}))
        self.assertFalse(driving.unusual_drive({"minutes": 12, "km": 6}))


if __name__ == "__main__":
    unittest.main()
