"""Тестове за фактите от описанието и за дубликатите по адрес. Без заявки към мрежата."""

import unittest

from imotko.search import facts, formatting, scan


class Facts(unittest.TestCase):
    def test_facts_from_the_real_listing(self):
        text = (
            "Без комисионна! Отдава се двустаен апартамент. В цената е включен и ГАРАЖ !!! "
            "Отопление: ГАЗ Намира се на комуникативно и тихо място."
        )
        self.assertEqual(facts.find_facts(text), ["отопление: газ", "в цената: гараж", "без комисионна"])

    def test_tenant(self):
        self.assertEqual(facts.find_facts("ИНВЕСТИЦИОНЕН ИМОТ с наемател до 2027"), ["с наемател"])
        self.assertEqual(facts.find_facts("Апартаментът е без наемател."), [])

    def test_what_is_not_included_is_not_a_fact(self):
        self.assertEqual(facts.find_facts("В цената не е включен паркинг. Паркомясто по договаряне."), [])
        self.assertEqual(facts.find_facts("Светъл апартамент до парка."), [])
        self.assertEqual(facts.find_facts(None), [])

    def test_included_is_cut_to_a_few_words(self):
        text = "В цената са включени гараж и мазе и всички такси и данъци за годината"
        self.assertEqual(facts.find_facts(text), ["в цената: гараж и мазе и"])

    def test_card_shows_facts_only_when_there_are_some(self):
        listing = {"description_facts": ["отопление: газ", "в цената: гараж"]}
        self.assertEqual(formatting._facts_lines(listing), ["   от описанието: отопление: газ; в цената: гараж"])
        self.assertEqual(formatting._facts_lines({"description_facts": []}), [])
        self.assertEqual(formatting._facts_lines({}), [])


def listing(**changes):
    return {"area_m2": 70.4, "floor": 3, "price_eur": 650, "address_from_description": "ул. Българска легия 28", **changes}


class DuplicatesByAddress(unittest.TestCase):
    def test_same_address_area_and_floor_is_one_flat(self):
        cheap = listing(id="a", price_eur=620)
        dear = listing(id="b", price_eur=700, duplicates=1, duplicates_max_price=720)
        result = scan.remove_duplicates_by_address([dear, cheap])

        self.assertEqual(result, [cheap])
        self.assertEqual(cheap["duplicates"], 2)
        self.assertEqual(cheap["duplicates_max_price"], 720)

    def test_other_floor_area_or_address_are_other_flats(self):
        flats = [listing(), listing(floor=4), listing(area_m2=90), listing(address_from_description="ул. Враня 101")]
        self.assertEqual(len(scan.remove_duplicates_by_address(flats)), 4)

    def test_listings_without_address_floor_or_area_are_kept(self):
        flats = [listing(address_from_description=None), listing(address_from_description=None),
                 listing(floor=None), listing(floor=None), listing(area_m2=None), listing(area_m2=None)]
        self.assertEqual(len(scan.remove_duplicates_by_address(flats)), 6)

    def test_address_is_compared_without_case(self):
        flats = [listing(address_from_description="УЛ. ВРАНЯ 101"), listing(address_from_description="ул. Враня 101")]
        self.assertEqual(len(scan.remove_duplicates_by_address(flats)), 1)


if __name__ == "__main__":
    unittest.main()
