"""Тестове за спирките и метростанциите от описанията и за паметта на агента. Без заявки към мрежата."""

import os
import unittest
import zipfile
from unittest import mock

from imotko.geo import driving, enrich, geocode, shops, stops, transit
from imotko.search import formatting
from imotko.sources import imot_bg
from tests.helpers import TempDatabase

BANISHORA = {"found": True, "lat": 42.710, "lon": 23.312, "name": "Банишора, София", "kind": "district",
             "area": None, "warnings": []}
TARGET = {"found": True, "lat": 42.63, "lon": 23.37, "name": "Бизнес парк София", "kind": "place",
          "area": None, "warnings": []}

STOPS = [
    {"name": "МАРИЯ ЛУИЗА", "lat": 42.7115, "lon": 23.3185, "is_station": True},
    {"name": "БУЛ. КНЯГИНЯ МАРИЯ ЛУИЗА", "lat": 42.7110, "lon": 23.3170, "is_station": False},
    {"name": "МАРИЯ ЛУИЗА", "lat": 42.600, "lon": 23.400, "is_station": True},
    {"name": "КОНСТАНТИН ВЕЛИЧКОВ", "lat": 42.6990, "lon": 23.3040, "is_station": True},
    {"name": "ЦЕНТРАЛНА ГАРА", "lat": 42.7120, "lon": 23.3200, "is_station": True},
    {"name": "ПЛ. ОРЛОВ МОСТ", "lat": 42.6960, "lon": 23.3380, "is_station": False},
    {"name": "БУЛ. ЦАР БОРИС III", "lat": 42.7080, "lon": 23.3100, "is_station": False},
]


def mention_texts(description):
    return [mention["text"] for mention in stops.find_mentions(description)]


class FindMentions(unittest.TestCase):
    def test_station_in_quotes_with_a_dash_inside(self):
        text = "Локацията е на пешеходно разстояние от метростанция 'ИЕЦ - Цариградско шосе', в близост до обекти."
        self.assertEqual(mention_texts(text), ["метростанция ИЕЦ - Цариградско шосе"])

    def test_station_name_without_quotes(self):
        text = "В близост до метростанция Княгиня Мария Луиза , спирки на градски транспорт, детски градини."
        self.assertEqual(mention_texts(text), ["метростанция Княгиня Мария Луиза"])

    def test_two_stations_joined_with_and(self):
        text = "Метростанциите Константин Величков и Опълченска са наблизо, както и спирки на автобуси и трамваи."
        self.assertEqual(mention_texts(text), ["метростанция Константин Величков", "метростанция Опълченска"])

    def test_misspelled_word_for_station(self):
        self.assertEqual(mention_texts("5 минути пеша от метростания 'Мария Луиза'."), ["метростанция Мария Луиза"])

    def test_numbered_station_but_not_a_distance(self):
        self.assertEqual(mention_texts("метростанция Младост 3 е на 300 метра"), ["метростанция Младост 3"])
        self.assertEqual(mention_texts("метростанция Опълченска 50 м пеша"), ["метростанция Опълченска"])

    def test_bus_stop_with_a_name(self):
        self.assertEqual(mention_texts("Автобусна спирка 'Орлов мост' на 2 минути пеша"), ["спирка Орлов мост"])

    def test_address_of_a_stop_is_marked_as_address(self):
        mention = stops.find_mentions("Спирка на бул. Цар Борис III на 100 м")[0]
        self.assertTrue(mention["is_address"])
        self.assertEqual(mention["name"], "бул. Цар Борис III")
        self.assertEqual(mention["text"], "спирка на бул. Цар Борис III")

    def test_no_name_is_not_a_mention(self):
        for text in (
            "Близо до спирки на градски транспорт, метрото е на 500 м, близостта до метрото и центъра.",
            "ДО МЕТРОСТАНЦИЯ ! ! ! ОТЛИЧНА ЛОКАЦИЯ ! ! !",
            "МЕТРОСТАНЦИЯ НА 1 МИН. ! ! ! С НАЕМАТЕЛ",
            "Метростанция на приблизително 3 минути пеша",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertEqual(stops.find_mentions(text), [])

    def test_same_station_is_listed_once_and_there_are_at_most_three(self):
        text = "Метростанция Витоша. Метростанция 'Витоша'. Метро Люлин, метро Сливница, метро Надежда."
        self.assertEqual(
            mention_texts(text),
            ["метростанция Витоша", "метростанция Люлин", "метростанция Сливница"],
        )


class StatedDistance(unittest.TestCase):
    def distance(self, description):
        mention = stops.find_mentions(description)[0]
        return mention["distance_m"], mention["distance_text"]

    def test_meters_after_and_before_the_name(self):
        self.assertEqual(self.distance("Метростанция Константин Величков е на 630 м пешеходно разстояние"), (630, "630 м"))
        self.assertEqual(self.distance("На 800 метра от метростанция 'Константин Величков'."), (800, "800 м"))
        self.assertEqual(self.distance("метростанция Витоша на 1 км"), (1000, "1 км"))

    def test_walking_minutes_become_meters(self):
        meters, text = self.distance("5 минути пеша от метростания 'Мария Луиза'.")
        self.assertEqual(text, "5 мин пеша")
        self.assertTrue(250 < meters < 400)

    def test_minutes_without_walking_are_not_a_distance(self):
        self.assertEqual(self.distance("метростанция Опълченска на 5 минути с кола"), (None, None))
        self.assertEqual(self.distance("метростанция Витоша, на 2 км от центъра"), (None, None))


class LocateInSchedule(unittest.TestCase):
    def test_two_close_stops_give_the_point_between_them(self):
        place = self.locate("Метростанциите Мария Луиза и Централна гара са наблизо")
        self.assertEqual(place["name"], "между МАРИЯ ЛУИЗА и ЦЕНТРАЛНА ГАРА")
        self.assertAlmostEqual(place["lat"], (42.7115 + 42.7120) / 2)
        self.assertIn("и метростанция Централна", place["mention"])

    def test_two_far_stops_keep_the_first(self):
        place = self.locate("метростанция Константин Величков и спирка Орлов мост")
        self.assertEqual(place["name"], "КОНСТАНТИН ВЕЛИЧКОВ")

    def test_stated_distance_goes_with_the_stop(self):
        place = self.locate("метростанция Константин Величков е на 630 м")
        self.assertEqual((place["stated_m"], place["stated_text"]), (630, "630 м"))

    def locate(self, description, district=BANISHORA):
        mentions = stops.find_mentions(description)
        with mock.patch.object(transit, "named_stops", return_value=STOPS):
            return stops.locate(mentions, "София", district)

    def test_station_is_found_by_a_longer_name(self):
        place = self.locate("до метростанция Княгиня Мария Луиза")
        self.assertEqual(place["name"], "МАРИЯ ЛУИЗА")
        self.assertEqual((place["lat"], place["lon"]), (42.7115, 23.3185))
        self.assertEqual(place["kind"], "stop")
        self.assertEqual(place["source"], stops.SOURCE_SCHEDULE)
        self.assertEqual(place["mention"], "метростанция Княгиня Мария Луиза")

    def test_station_with_the_same_name_far_from_the_district_is_ignored(self):
        far_district = {**BANISHORA, "lat": 42.60, "lon": 23.40}
        place = self.locate("до метростанция Мария Луиза", far_district)
        self.assertEqual((place["lat"], place["lon"]), (42.600, 23.400))

        other_city_part = {**BANISHORA, "lat": 42.75, "lon": 23.45}
        with mock.patch.object(geocode, "locate", return_value=None):
            self.assertIsNone(self.locate("до метростанция Мария Луиза", other_city_part))

    def test_metro_mention_never_matches_a_bus_stop(self):
        only_bus_stops = [stop for stop in STOPS if not stop["is_station"]]
        mentions = stops.find_mentions("метростанция Цар Борис III")
        with (
            mock.patch.object(transit, "named_stops", return_value=only_bus_stops),
            mock.patch.object(geocode, "locate", return_value=None),
        ):
            self.assertIsNone(stops.locate(mentions, "София", BANISHORA))

    def test_metro_in_the_stop_name_counts_but_not_inside_another_word(self):
        self.assertTrue(stops._is_metro({"name": "МЕТРОСТАНЦИЯ ЛЮЛИН", "is_station": False}))
        self.assertTrue(stops._is_metro({"name": "Метро Люлин", "is_station": False}))
        self.assertFalse(stops._is_metro({"name": "МЕТРОПОЛИТ", "is_station": False}))

    def test_bus_stop_and_its_address(self):
        self.assertEqual(self.locate("спирка 'Орлов мост' до входа")["name"], "ПЛ. ОРЛОВ МОСТ")
        self.assertEqual(self.locate("спирка на бул. Цар Борис III")["name"], "БУЛ. ЦАР БОРИС III")

    def test_name_with_extra_words_falls_back_to_shorter_name(self):
        self.assertEqual(self.locate("метростанция Централна ЖП гара")["name"], "ЦЕНТРАЛНА ГАРА")

    def test_second_mention_is_used_when_the_first_is_not_found(self):
        place = self.locate("метростанция Витоша и метростанция Константин Величков")
        self.assertEqual(place["name"], "КОНСТАНТИН ВЕЛИЧКОВ")

    def test_use_map_false_asks_only_the_schedule(self):
        mentions = stops.find_mentions("метростанция Опълченска")
        with (
            mock.patch.object(transit, "named_stops", return_value=[]),
            mock.patch.object(geocode, "locate") as locate,
        ):
            self.assertIsNone(stops.locate(mentions, "София", BANISHORA, use_map=False))
        locate.assert_not_called()

    def test_no_district_means_no_search(self):
        mentions = stops.find_mentions("метростанция Мария Луиза")
        with mock.patch.object(geocode, "locate") as locate:
            self.assertIsNone(stops.locate(mentions, "София", None))
        locate.assert_not_called()


class LocateOnMap(unittest.TestCase):
    def locate(self, description, found, named_stops=()):
        mentions = stops.find_mentions(description)
        with (
            mock.patch.object(transit, "named_stops", return_value=list(named_stops)),
            mock.patch.object(geocode, "locate", return_value=found) as locate,
        ):
            return stops.locate(mentions, "София", BANISHORA), locate

    def map_place(self, **changes):
        return {"lat": 42.711, "lon": 23.314, "name": "Опълченска, София", "far_from_district": False,
                "warnings": [], **changes}

    def test_map_is_used_when_the_schedule_does_not_know_the_stop(self):
        place, locate = self.locate("метростанция 'Опълченска'", self.map_place())
        self.assertEqual(place["source"], stops.SOURCE_MAP)
        self.assertEqual(place["name"], "Опълченска, София")
        locate.assert_called_once_with("метростанция Опълченска", "София", "place", near=BANISHORA)

    def test_address_of_a_stop_is_searched_as_a_street(self):
        _, locate = self.locate("спирка на бул. България", self.map_place())
        locate.assert_called_once_with("бул. България", "София", "street", near=BANISHORA)

    def test_doubtful_map_results_are_dropped(self):
        for found in (None, self.map_place(far_from_district=True), self.map_place(warnings=["извън София"])):
            place, _ = self.locate("метростанция Опълченска", found)
            self.assertIsNone(place)

    def test_only_the_first_mentions_are_searched_on_the_map(self):
        _, locate = self.locate("Метро Люлин, метро Сливница, метро Надежда.", None)
        self.assertEqual(locate.call_count, stops.MAX_MAP_LOOKUPS)


class NamedStops(TempDatabase):
    def write_feed(self, rows):
        path = transit.feed_path("София")
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as feed:
            feed.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon,location_type\n" + "\n".join(rows))
        return path

    def test_stops_and_stations_without_entrances(self):
        self.write_feed([
            "A1,БУЛ. ВИТОША,42.69,23.32,0",
            "S1,ВИТОША,42.68,23.31,1",
            "E1,Изход бул. Витоша,42.681,23.311,2",
            "B1,Без координати,,,0",
        ])
        found = transit.named_stops("София")
        self.assertEqual([stop["name"] for stop in found], ["БУЛ. ВИТОША", "ВИТОША"])
        self.assertEqual([stop["is_station"] for stop in found], [False, True])

    def test_list_is_cached_until_the_file_changes(self):
        path = self.write_feed(["A1,ОПЪЛЧЕНСКА,42.69,23.32,0"])
        self.assertIs(transit.named_stops("София"), transit.named_stops("София"))

        path.write_bytes(b"not a zip")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
        self.assertEqual(transit.named_stops("София"), [])

    def test_missing_feed_is_an_empty_list(self):
        self.assertEqual(transit.named_stops("София"), [])
        self.assertEqual(transit.named_stops("Непознат"), [])


class StopsInSearch(TempDatabase):
    def add_location_data(self, listings, street=None):
        self.asked = []

        def fake_locate(place, city, kind="place", near=None):
            self.asked.append(place)
            if place.startswith(("метростанция", "спирка")):
                return None  # картата не знае спирката
            return {"district": BANISHORA, "street": street, "place": TARGET}[kind]

        def fake_drive_many(origins, target):
            return [{"minutes": 5.0 + index, "km": 2.0} for index, _ in enumerate(origins)]

        with (
            mock.patch.object(geocode, "locate", side_effect=fake_locate),
            mock.patch.object(transit, "named_stops", return_value=STOPS),
            mock.patch.object(shops, "chains_need_refresh", return_value=False),
            mock.patch.object(shops, "chain_stores", return_value=None),
            mock.patch.object(driving, "drive_many", side_effect=fake_drive_many),
            mock.patch.object(driving, "city_extra_minutes", return_value=(6.0, 1)),
        ):
            return enrich.add_location_data(listings, "София", "Бизнес парк")

    def test_stop_is_the_shown_place_when_there_is_no_street(self):
        listing = {"district": "Банишора", "street": None, "description": "На 100 м от метростанция Мария Луиза."}
        self.add_location_data([listing])

        self.assertEqual(listing["stop"]["name"], "МАРИЯ ЛУИЗА")
        commute = listing["commute"]
        self.assertEqual(commute["precision"], stops.PRECISION)
        # маршрутите са [квартал, спирка]: оценката остава по квартала, показаното е по спирката
        self.assertAlmostEqual(commute["district_minutes"], 5.0 + 6.0)
        self.assertAlmostEqual(commute["minutes"], 6.0 + 6.0)

    def test_street_is_more_exact_than_a_stop(self):
        street = {"found": True, "lat": 42.7118, "lon": 23.3180, "name": "Опълченска, София", "kind": "street",
                  "area": None, "far_from_district": False, "warnings": []}
        listing = {"district": "Банишора", "street": "ул. Опълченска",
                   "description": "до метростанция Княгиня Мария Луиза"}
        self.add_location_data([listing], street)

        self.assertEqual(listing["commute"]["precision"], "улица")
        self.assertIn("stop", listing)
        self.assertNotIn("ул.", " ".join(listing["location_warnings"]))

    def test_map_is_not_asked_for_a_stop_when_the_listing_has_a_street(self):
        street = {"found": True, "lat": 42.7118, "lon": 23.3180, "name": "Опълченска, София", "kind": "street",
                  "area": None, "far_from_district": False, "warnings": []}
        listing = {"district": "Банишора", "street": "ул. Опълченска", "description": "до метростанция Витоша"}
        self.add_location_data([listing], street)

        self.assertEqual(listing["stop_mentions"], ["метростанция Витоша"])
        self.assertFalse([place for place in self.asked if place.startswith("метростанция")])

    def test_stated_distance_decides_how_far_the_street_may_be(self):
        near_stop = {"found": True, "lat": 42.7140, "lon": 23.3185, "name": "Опълченска, София", "kind": "street",
                     "area": None, "far_from_district": False, "warnings": []}  # ~0,3 км от станцията
        listing = {"district": "Банишора", "street": "ул. Опълченска",
                   "description": "метростанция Мария Луиза е на 50 м"}
        self.add_location_data([listing], near_stop)
        self.assertEqual(listing["location_warnings"], [])

        far_street = {**near_stop, "lat": 42.7300}  # ~2 км
        listing = {"district": "Банишора", "street": "ул. Опълченска",
                   "description": "метростанция Мария Луиза е на 50 м"}
        self.add_location_data([listing], far_street)
        self.assertTrue(listing["location_warnings"])

    def test_street_far_from_the_stop_is_flagged(self):
        street = {"found": True, "lat": 42.7300, "lon": 23.3180, "name": "Опълченска, София", "kind": "street",
                  "area": None, "far_from_district": False, "warnings": []}
        listing = {"district": "Банишора", "street": "ул. Опълченска",
                   "description": "до метростанция Княгиня Мария Луиза"}
        self.add_location_data([listing], street)

        self.assertTrue(any("от описанието: провери адреса" in warning for warning in listing["location_warnings"]))

    def test_stop_that_is_not_found_is_kept_as_a_mention(self):
        listing = {"district": "Банишора", "street": None, "description": "до метростанция Витоша"}
        self.add_location_data([listing])
        self.assertNotIn("stop", listing)
        self.assertEqual(listing["stop_mentions"], ["метростанция Витоша"])
        self.assertEqual(listing["commute"]["precision"], "квартал")

    def test_listing_without_description_is_unchanged(self):
        listing = {"district": "Банишора", "street": None}
        self.add_location_data([listing])
        self.assertNotIn("stop", listing)
        self.assertNotIn("stop_mentions", listing)


class StopInCard(unittest.TestCase):
    def card(self, **changes):
        listing = {"title": "2-СТАЕН", "location": "град София, Банишора", "street": None, "price_eur": 100000,
                   "area_m2": 60, "eur_per_m2": 1667, "ref_ppm": 2000, "ref_kind": "квартал", "floor": 3,
                   "floors_total": 6, "score": 10, "reasons": [], "url": "u",
                   "commute": {"target": "УНСС", "minutes": 12, "district_minutes": 11,
                               "precision": stops.PRECISION}, **changes}
        return formatting._format_listing(1, listing, "продажба")

    def test_found_stop_is_shown_with_where_it_was_found(self):
        stop = {"mention": "метростанция Мария Луиза", "name": "МАРИЯ ЛУИЗА", "source": stops.SOURCE_SCHEDULE}
        text = self.card(stop=stop)
        self.assertIn("спирка от описанието: метростанция Мария Луиза (намерена в разписанието на града като "
                      "\"МАРИЯ ЛУИЗА\")", text)
        self.assertIn("времената по-долу са от нея, не от адреса на жилището", text)
        self.assertIn("до УНСС: ~12 мин с кола (по спирка от описанието)", text)

    def test_stop_is_not_blamed_for_times_measured_by_street(self):
        stop = {"mention": "метростанция Мария Луиза", "name": "МАРИЯ ЛУИЗА", "source": stops.SOURCE_MAP}
        commute = {"target": "УНСС", "minutes": 12, "district_minutes": 11, "precision": "улица"}
        text = self.card(stop=stop, commute=commute)
        self.assertIn("спирка от описанието: метростанция Мария Луиза", text)
        self.assertNotIn("времената по-долу са от нея", text)

    def test_unfound_stop_is_said_out_loud(self):
        text = self.card(stop_mentions=["метростанция Витоша"],
                         commute={"target": "УНСС", "minutes": 12, "district_minutes": 12, "precision": "квартал"})
        self.assertIn("спирка от описанието: метростанция Витоша - не е намерена в картата, времената не са по нея", text)

    def test_no_mention_means_no_line(self):
        text = self.card(commute={"target": "УНСС", "minutes": 12, "district_minutes": 12, "precision": "квартал"})
        self.assertNotIn("спирка от описанието", text)


class DescriptionIsKept(unittest.TestCase):
    def test_detail_has_the_description_text_with_single_spaces(self):
        html = '<div class="moreInfo"><div class="text">До метростанция  Мария Луиза ,\n  с   паркинг.</div></div>'
        self.assertEqual(imot_bg.parse_detail(html)["description"], "До метростанция Мария Луиза , с паркинг.")


class MemoryFallback(unittest.TestCase):
    def test_memory_write_is_retried_with_the_fallback_model(self):
        from agno.exceptions import ModelProviderError
        from agno.memory import MemoryManager

        from imotko.models import MemoryManagerWithFallback

        models_used = []

        def fake_run(manager, *args, **kwargs):
            models_used.append(manager.model)
            if manager.model == "main":
                raise ModelProviderError("429", status_code=429)
            return "запомнено"

        manager = MemoryManagerWithFallback(fallback_model="fallback")
        manager.model = "main"
        with mock.patch.object(MemoryManager, "run_memory_task", autospec=True, side_effect=fake_run):
            self.assertEqual(manager.run_memory_task("task", [], "u", None), "запомнено")
        self.assertEqual(models_used, ["main", "fallback"])
        self.assertEqual(manager.model, "main")

    def test_error_without_a_fallback_is_raised(self):
        from agno.exceptions import ModelProviderError
        from agno.memory import MemoryManager

        from imotko.models import MemoryManagerWithFallback

        error = ModelProviderError("429", status_code=429)
        with mock.patch.object(MemoryManager, "run_memory_task", autospec=True, side_effect=error):
            with self.assertRaises(ModelProviderError):
                MemoryManagerWithFallback().run_memory_task("task", [], "u", None)


class AgentMemory(unittest.TestCase):
    def test_agent_keeps_user_preferences_in_the_same_database(self):
        from imotko.agent import MEMORY_CAPTURE_INSTRUCTIONS, imot_agent

        self.assertTrue(imot_agent.enable_agentic_memory)
        self.assertIs(imot_agent.memory_manager.db, imot_agent.db)
        self.assertEqual(imot_agent.memory_manager.memory_capture_instructions, MEMORY_CAPTURE_INSTRUCTIONS)

    def test_instructions_tell_the_model_to_name_what_comes_from_memory(self):
        from imotko.agent import instructions

        text = " ".join(instructions.INSTRUCTIONS)
        self.assertIn("от паметта", text)
        self.assertIn("update_user_memory", text)


if __name__ == "__main__":
    unittest.main()
