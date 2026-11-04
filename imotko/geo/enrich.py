"""Данните от картата за обявите: квартал, улица, магазини и време до местата, до които се пътува.

Мрежова грешка никога не спира търсенето: обявата просто остава без тези данни.
"""

from imotko.geo import common, driving, geocode, shops, transit

MAX_COMMUTE_TARGETS = 4


def commute_targets(commute_to: str | list[str] | None) -> list[str]:
    """Целите като списък без повторения, най-много MAX_COMMUTE_TARGETS. Приема и един низ
    (така са записани по-старите запазени търсения)."""
    if not commute_to:
        return []
    if isinstance(commute_to, str):
        commute_to = [commute_to]
    targets = []
    for target in commute_to:
        target = str(target).strip()
        if target and target not in targets:
            targets.append(target)
    return targets[:MAX_COMMUTE_TARGETS]


def add_location_data(listings: list[dict], city: str, commute_to: str | list[str] | None = None) -> list[str]:
    """Добавя към всяка обява:

    - "chains": веригите в квартала (липсва, ако няма данни);
    - "chain_near_street": (верига, км) до 500 м от улицата, ако обявата има улица;
    - "commutes": време с кола и с градски транспорт до всяка цел, по квартала (за оценката)
      и по улицата (за показване); "commute" е първата цел, по нея е оценката;
    - "location_warnings": съмнения от картата за тази обява.

    Връща общите бележки за търсенето (напр. "целта не е намерена"), за да се кажат веднъж.
    Кварталите и улиците се търсят по веднъж, с кеш. При грешка в мрежата полетата липсват.
    """
    notes = []

    stores_and_age = _load_chain_stores(city, notes)
    districts = _locate_districts(listings, city)
    streets = _locate_streets(listings, city, districts)

    chains_by_district = {}
    if stores_and_age:
        stores, _ = stores_and_age
        chains_by_district = _chains_by_district(districts, stores)

    listing_streets = []
    for listing in listings:
        district = districts.get(listing.get("district"))
        street = streets.get((listing.get("street"), listing.get("district")))
        street = _add_place_warnings(listing, district, street)
        listing_streets.append(street)

        if stores_and_age and district:
            stores, _ = stores_and_age
            _add_chains(listing, street, stores, chains_by_district)

    targets = commute_targets(commute_to)
    for number, target in enumerate(targets, 1):
        # при няколко цели в списъка пише "цел 1", за да не се повтаря дългото име при всяка обява
        label = target if len(targets) == 1 else f"цел {number}"
        notes += _add_commute(listings, listing_streets, city, target, label, districts)

    return notes


def _load_chain_stores(city: str, notes: list[str]) -> tuple | None:
    if shops.chains_need_refresh(city):
        shops.refresh_chains_in_background(city)

    stores_and_age = shops.chain_stores(city)
    if stores_and_age is None:
        notes.append("данните за големите магазини в града още се свалят, затова няма точки за магазини")
        return None

    _, age_days = stores_and_age
    if age_days > shops.CHAINS_STALE_AGE / common.DAY:
        notes.append(f"данните за магазините са отпреди {age_days:.0f} дни")
    return stores_and_age


def _locate_districts(listings: list[dict], city: str) -> dict:
    districts = {}
    for listing in listings:
        name = listing.get("district")
        if name and name not in districts:
            districts[name] = geocode.locate(name, city, "district")
    return districts


def _locate_streets(listings: list[dict], city: str, districts: dict) -> dict:
    # улицата се търси близо до квартала си; ключът е (улица, квартал)
    streets = {}
    for listing in listings:
        street_name = listing.get("street")
        district_name = listing.get("district")
        street_key = (street_name, district_name)
        if street_name and street_key not in streets:
            district = districts.get(district_name)
            streets[street_key] = geocode.locate(street_name, city, "street", near=district)
    return streets


def _chains_by_district(districts: dict, stores: list) -> dict:
    chains_by_district = {}
    for name, district in districts.items():
        if district:
            chains_by_district[name] = shops.chains_in_district(district, stores)
    return chains_by_district


def _add_place_warnings(listing: dict, district: dict | None, street: dict | None) -> dict | None:
    listing["location_warnings"] = list(district["warnings"]) if district else []

    if street and street.get("far_from_district"):
        listing["location_warnings"].append(
            f"улица \"{listing['street']}\" не е намерена в квартала, мярката е по квартала"
        )
        return None

    if street:
        listing["location_warnings"] += street["warnings"]
    return street


def _add_chains(listing: dict, street: dict | None, stores: list, chains_by_district: dict) -> None:
    listing["chains"] = chains_by_district[listing["district"]]
    if not street:
        return

    nearest = shops.nearest_chain(street, stores)
    if nearest:
        _, distance_km = nearest
        if distance_km <= shops.CHAIN_NEAR_STREET_KM:
            listing["chain_near_street"] = nearest


def _add_commute(listings, listing_streets, city, commute_to, label, districts) -> list[str]:
    """Време с кола и с градски транспорт до целта. Връща бележките за самата цел."""
    prefix = "" if label == commute_to else f"{label}: "
    target = geocode.locate_any(commute_to, city)
    if target is None:
        return [f"{prefix}не успях да намеря \"{commute_to}\" в картата, затова няма време до него"]

    # оценката е винаги по квартала, за да се мерят всички обяви еднакво; улицата е само за показване
    origins = _unique_points([*districts.values(), *listing_streets])
    drive_routes = driving.drive_many(origins, target)
    routes_by_point = {common.point_key(origin): route for origin, route in zip(origins, drive_routes)}
    transit_by_point = _transit_routes(origins, target, city)

    for listing, street in zip(listings, listing_streets):
        district = districts.get(listing.get("district"))
        district_route = routes_by_point.get(common.point_key(district)) if district else None
        if district_route is None:
            continue

        if street:
            shown_place, precision = street, "улица"
        else:
            shown_place, precision = district, "квартал"
        shown_point = common.point_key(shown_place)
        shown_route = routes_by_point.get(shown_point) or district_route
        shown_time = driving.city_drive_time(shown_route)

        commute = {
            # само за показване: оценката е по колата
            "transit": transit_by_point.get(shown_point),
            "target": commute_to,
            "label": label,
            # реалистично време в града, не по празни улици
            "district_minutes": driving.city_drive_time(district_route)["typical"],
            "minutes": shown_time["typical"],
            "low": shown_time["low"],
            "high": shown_time["high"],
            "precision": precision,
        }
        listing.setdefault("commutes", []).append(commute)
        listing.setdefault("commute", commute)

    notes = [f"{prefix}целта \"{commute_to}\" е намерена като: {target['name']}"]
    for warning in target["warnings"]:
        notes.append(prefix + warning)

    if not _any_commute_to(listings, commute_to):
        notes.append(f"{prefix}услугата за маршрути не отговори, затова няма време до целта")
    return notes


def _unique_points(places: list[dict | None]) -> list[dict]:
    unique = {}
    for place in places:
        if place:
            unique[common.point_key(place)] = place
    return list(unique.values())


def _any_commute_to(listings: list[dict], target: str) -> bool:
    for listing in listings:
        for commute in listing.get("commutes", []):
            if commute["target"] == target:
                return True
    return False


def _transit_routes(origins: list[dict], target: dict, city: str) -> dict:
    """Градски транспорт от всяка точка до целта. Празно, ако няма разписание за града."""
    if not transit.has_feed(city):
        return {}
    try:
        return {common.point_key(origin): transit.route(city, origin, target) for origin in origins}
    except Exception:
        return {}  # транспортът е допълнение: грешка в него не спира търсенето
