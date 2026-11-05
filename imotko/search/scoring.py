"""Оценка на обява: цена спрямо квартала, белези, приоритети на потребителя, място и предупреждения."""

import time
from dataclasses import dataclass

# Цена на кв.м извън тези граници спрямо квартала е съмнителна (грешка, гараж, само дял)
SUSPICIOUS_LOW_PRICE = 0.4

SUSPICIOUS_HIGH_PRICE = 2.5

POINTS_PER_CHAIN = 2

MAX_CHAIN_POINTS = 8

CHAIN_NEAR_STREET_POINTS = 2


@dataclass(frozen=True)
class Feature:
    """Белег от страницата на обявата (асансьор, паркинг...)."""
    field: str
    label: str
    required_filter: str | None
    points: int
    priority_points: int


# Ключът е думата, с която потребителят го посочва като приоритет
FEATURES = {
    "асансьор": Feature("has_elevator", "асансьор", "require_elevator", points=4, priority_points=6),
    "паркинг": Feature("has_parking", "паркинг", "require_parking", points=3, priority_points=7),
    "акт16": Feature("has_act16", "акт 16", None, points=2, priority_points=5),
    "обзаведен": Feature("furnished", "обзаведен", "require_furnished", points=0, priority_points=6),
    # почти всички апартаменти имат тераса, затова точки само когато е важна за потребителя
    "тераса": Feature("has_terrace", "тераса", "require_terrace", points=0, priority_points=5),
}

PRIORITIES = set(FEATURES) | {"тухла", "ново", "намаление"}

PRIORITY_SYNONYMS = {"новострой": "ново", "акт": "акт16", "спаднацена": "намаление", "балкон": "тераса"}


def normalize_priorities(priorities: list[str] | None) -> set[str]:
    """["Паркинг", "ново-строй"] -> {"паркинг", "ново"}. Непознат приоритет е грешка."""
    normalized = set()
    for priority in priorities or []:
        word = priority.strip().lower().replace(" ", "").replace("-", "")
        normalized.add(PRIORITY_SYNONYMS.get(word, word))

    unknown = normalized - PRIORITIES
    if unknown:
        raise ValueError(
            f"Непознат приоритет: {', '.join(sorted(unknown))}. Възможни: {', '.join(sorted(PRIORITIES))}."
        )
    return normalized


def is_low_floor(listing: dict) -> bool:
    """Първи етаж, партер или сутерен."""
    floor = listing.get("floor")
    return floor is not None and floor <= 1


def is_top_floor(listing: dict) -> bool:
    floors_total = listing.get("floors_total")
    return bool(floors_total) and listing.get("floor") == floors_total


def days_since_created(listing: dict) -> int | None:
    created = listing.get("created")
    if not created:
        return None
    days = int((time.time() - created) // 86400)
    # часовникът на сайта може да е малко напред спрямо нашия
    return max(0, days)


class _ScoreSheet:
    def __init__(self):
        self.points = 0.0
        self.reasons: list[str] = []

    def add(self, points: float, reason: str | None = None) -> None:
        self.points += points
        if reason:
            self.reasons.append(reason)


def score_listing(listing: dict, reference_price: float, filters: dict, priorities: set[str]) -> tuple[float, list[str]]:
    """Колко изгодна е обявата: по-високо е по-добре. Връща (оценка, причини).

    Полетата от страницата на обявата липсват, докато тя не е свалена, и тогава не дават точки.
    """
    sheet = _ScoreSheet()

    _score_price(sheet, listing, reference_price)
    _score_floor(sheet, listing)
    _score_building(sheet, listing, priorities)
    _score_features(sheet, listing, priorities)
    _score_price_drop(sheet, listing, priorities)
    _score_budget(sheet, listing, filters)

    # Място: големи вериги в квартала и време до целта на потребителя. Само бонуси:
    # мярката е по квартала, затова неточността ѝ не бива да сваля оценката.
    place_points, place_reasons = _place_points(listing)
    sheet.points += place_points
    sheet.reasons += place_reasons

    _score_freshness(sheet, listing)

    return round(sheet.points, 1), sheet.reasons


def _score_price(sheet: _ScoreSheet, listing: dict, reference_price: float) -> None:
    # Цена спрямо пазара (квартала или града): до +/- 50 точки
    price = listing["eur_per_m2"]
    if not price or not reference_price:
        return

    percent_below_market = (reference_price - price) / reference_price * 100
    percent_below_market = max(-50.0, min(50.0, percent_below_market))
    if percent_below_market >= 0:
        sheet.add(percent_below_market, f"под пазара с {percent_below_market:.0f}%")
    else:
        sheet.add(percent_below_market, f"над пазара с {-percent_below_market:.0f}%")


def _score_floor(sheet: _ScoreSheet, listing: dict) -> None:
    if is_low_floor(listing) or is_top_floor(listing):
        sheet.add(-5, "първи/последен етаж")


def _score_building(sheet: _ScoreSheet, listing: dict, priorities: set[str]) -> None:
    wants_new = "ново" in priorities
    year_built = listing.get("year_built")
    current_year = time.localtime().tm_year
    is_in_construction = listing.get("in_construction") or (year_built and year_built > current_year)

    if is_in_construction:
        sheet.add(5 if wants_new else -10, "в строеж")
    elif year_built and year_built >= 2000:
        sheet.add(10 if wants_new else 5, f"сграда от {year_built}")


def _score_features(sheet: _ScoreSheet, listing: dict, priorities: set[str]) -> None:
    # Белези от страницата на обявата
    for priority_name, feature in FEATURES.items():
        feature_points = feature.points
        if priority_name in priorities:
            feature_points += feature.priority_points
        if listing.get(feature.field) and feature_points:
            sheet.add(feature_points, feature.label)

    is_high_building = (listing.get("floors_total") or 0) >= 6
    if listing.get("has_elevator") is False and is_high_building:
        sheet.add(-6, "без асансьор на висока сграда")

    if listing.get("construction") == "тухла":
        sheet.add(8 if "тухла" in priorities else 2)


def _score_price_drop(sheet: _ScoreSheet, listing: dict, priorities: set[str]) -> None:
    # Намалена цена (от историята)
    price_change = listing.get("price_change_pct")
    if price_change is None or price_change >= 0:
        return

    drop_points = min(10, -price_change)
    if "намаление" in priorities:
        drop_points *= 2
    sheet.add(drop_points, f"цената е паднала с {-price_change:.1f}%")


def _score_budget(sheet: _ScoreSheet, listing: dict, filters: dict) -> None:
    # Добре под бюджета или над желаната квадратура
    max_price = filters.get("max_price_eur")
    if max_price and listing["price_eur"] <= max_price * 0.9:
        sheet.add(3)

    min_area = filters.get("min_area_m2")
    if min_area and listing["area_m2"] and listing["area_m2"] >= min_area * 1.15:
        sheet.add(3)


def _score_freshness(sheet: _ScoreSheet, listing: dict) -> None:
    # Нова обява
    age = days_since_created(listing)
    if age is None:
        return

    if age <= 3:
        freshness_points = 8
    elif age <= 7:
        freshness_points = 5
    elif age <= 14:
        freshness_points = 2
    else:
        freshness_points = 0

    if freshness_points:
        sheet.add(freshness_points, f"нова обява ({days_text(age)})")


def _place_points(listing: dict) -> tuple[int, list[str]]:
    """Точки от картата. Всички обяви в един квартал получават едно и също,
    а улицата може само да добави (магазин до 500 м), никога да отнеме.
    """
    points = 0
    reasons = []

    chains = listing.get("chains")
    if chains and chains["counts"]:
        different_chains = len(chains["counts"])
        points += min(MAX_CHAIN_POINTS, different_chains * POINTS_PER_CHAIN)
        if different_chains == 1:
            reasons.append("1 голяма верига в квартала")
        else:
            reasons.append(f"{different_chains} големи вериги в квартала")

    chain_near_street = listing.get("chain_near_street")
    if chain_near_street:
        chain, distance_km = chain_near_street
        points += CHAIN_NEAR_STREET_POINTS
        reasons.append(f"{chain} на ~{distance_km * 1000:.0f} м")

    commute = listing.get("commute")
    if commute:
        # оценката е по квартала за всички; времето е реалистично за града, не по празни улици
        minutes = commute["district_minutes"]
        if minutes <= 40:
            points += 8 if minutes <= 20 else 4
            reasons.append(f"близо до {_commute_place_name(commute)} (~{minutes:.0f} мин с кола)")

    return points, reasons


def _commute_place_name(commute: dict) -> str:
    return commute.get("label", commute["target"])


def listing_warnings(listing: dict) -> list[str]:
    """Съмнения в данните на обявата, които моделът трябва да каже на потребителя."""
    warnings = []

    price = listing.get("eur_per_m2")
    reference = listing.get("ref_ppm")
    if price and reference:
        ratio = price / reference
        if ratio < SUSPICIOUS_LOW_PRICE or ratio > SUSPICIOUS_HIGH_PRICE:
            warnings.append(
                f"съмнителна цена: {ratio:.0%} от средната за {listing.get('ref_kind', 'района')}; "
                "може да е грешка в обявата или друг вид имот (гараж, ателие, само дял)"
            )

    if listing.get("in_construction") and listing.get("has_act16"):
        warnings.append("обявата си противоречи: пише и \"в строеж\", и \"акт 16\"")

    year_built = listing.get("year_built")
    is_future_year = year_built and year_built > time.localtime().tm_year
    if is_future_year and listing.get("in_construction") is False:
        warnings.append(f"обявата си противоречи: година {year_built}, но не пише, че е в строеж")

    return warnings + listing.get("location_warnings", [])


def days_text(days: int) -> str:
    return f"{days} ден" if days == 1 else f"{days} дни"
