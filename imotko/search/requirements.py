"""Условията на потребителя: кои обяви минават и какво точно е проверено за всяка.

Филтрите в търсенето и редът "условия" в отговора ползват едни и същи проверки,
за да не може моделът да каже "отговаря", когато филтърът е пуснал обявата по друга причина.
"""

from dataclasses import dataclass

from imotko.search import scoring

OK = "✓"
FAILED = "✗"
UNKNOWN = "?"


@dataclass(frozen=True)
class Check:
    """Едно условие за една обява. UNKNOWN минава филтъра, но се казва изрично."""

    status: str
    text: str


def district_names(filters: dict, key: str) -> list[str]:
    """Кварталите като списък. По-старите запазени търсения пазят един квартал като текст."""
    value = filters.get(key)
    if not value:
        return []
    if isinstance(value, str):
        value = [value]
    return [name.strip() for name in value if name and name.strip()]


def _in_district(listing: dict, name: str) -> bool:
    # само по местоположението: в описанието е и улицата ("бул. Витоша" не е квартал Витоша)
    return name.lower() in listing["location"].lower()


def _price_check(listing: dict, filters: dict, details_known: bool) -> Check | None:
    price = listing["price_eur"]
    max_price = filters.get("max_price_eur")
    max_with_parking = filters.get("max_price_with_parking_eur")
    min_price = filters.get("min_price_eur")

    if min_price and price < min_price:
        return Check(FAILED, f"цена {price:,.0f} € (под {min_price:,.0f} €)")
    if not max_price and not max_with_parking:
        return None

    if max_price and price <= max_price:
        return Check(OK, f"цена {price:,.0f} € (до {max_price:,.0f} €)")

    if max_with_parking and price <= max_with_parking:
        if not details_known:
            return Check(UNKNOWN, f"цена {price:,.0f} € (минава само ако има паркинг)")
        if listing.get("has_parking"):
            return Check(OK, f"цена {price:,.0f} € (до {max_with_parking:,.0f} €, защото има паркинг)")
        return Check(FAILED, f"цена {price:,.0f} € (над {max_price or 0:,.0f} € и не пише паркинг)")

    limit = max_with_parking or max_price
    return Check(FAILED, f"цена {price:,.0f} € (над {limit:,.0f} €)")


def _area_check(listing: dict, filters: dict) -> Check | None:
    area = listing["area_m2"]
    min_area = filters.get("min_area_m2")
    max_area = filters.get("max_area_m2")
    if not min_area and not max_area:
        return None

    if area is None:
        if min_area:
            return Check(FAILED, "квадратурата не е посочена")
        return Check(UNKNOWN, "квадратурата не е посочена")
    if min_area and area < min_area:
        return Check(FAILED, f"{area:g} кв.м (под {min_area:g})")
    if max_area and area > max_area:
        return Check(FAILED, f"{area:g} кв.м (над {max_area:g})")
    return Check(OK, f"{area:g} кв.м")


def _district_checks(listing: dict, filters: dict) -> list[Check]:
    checks = []

    wanted = district_names(filters, "district")
    if wanted:
        matching = [name for name in wanted if _in_district(listing, name)]
        if matching:
            checks.append(Check(OK, f"квартал {matching[0]}"))
        else:
            checks.append(Check(FAILED, f"квартал извън избраните ({listing['location']})"))

    for name in district_names(filters, "exclude_districts"):
        if _in_district(listing, name):
            checks.append(Check(FAILED, f"квартал {name} е изключен"))

    return checks


def _floor_checks(listing: dict, filters: dict) -> list[Check]:
    wants_not_first = filters.get("exclude_first_floor")
    wants_not_last = filters.get("exclude_last_floor")
    if not wants_not_first and not wants_not_last:
        return []

    floor = listing.get("floor")
    floors_total = listing.get("floors_total")
    if floor is None:
        return [Check(UNKNOWN, "етажът не е посочен в обявата")]

    checks = []
    if wants_not_first:
        if scoring.is_low_floor(listing):
            checks.append(Check(FAILED, f"етаж {floor} (първи или по-нисък)"))
        else:
            checks.append(Check(OK, f"етаж {floor} (не е първи)"))

    if wants_not_last:
        if not floors_total:
            checks.append(Check(UNKNOWN, "не е ясно дали е последен етаж"))
        elif scoring.is_top_floor(listing):
            checks.append(Check(FAILED, f"етаж {floor}/{floors_total} (последен)"))
        else:
            checks.append(Check(OK, f"етаж {floor}/{floors_total} (не е последен)"))

    return checks


def _building_checks(listing: dict, filters: dict) -> list[Check]:
    checks = []

    if filters.get("exclude_in_construction"):
        if listing.get("in_construction"):
            checks.append(Check(FAILED, "в строеж"))
        else:
            checks.append(Check(OK, "не е в строеж"))

    construction = filters.get("construction")
    if construction:
        actual = listing.get("construction")
        if actual == construction.lower():
            checks.append(Check(OK, actual))
        elif actual:
            checks.append(Check(FAILED, f"{actual}, не {construction.lower()}"))
        else:
            checks.append(Check(FAILED, "строителството не е посочено"))

    return checks


def _feature_checks(listing: dict, filters: dict) -> list[Check]:
    checks = []
    for feature in scoring.FEATURES.values():
        is_required = feature.required_filter and filters.get(feature.required_filter)
        if not is_required:
            continue
        if listing.get(feature.field):
            checks.append(Check(OK, feature.label))
        else:
            checks.append(Check(FAILED, f"{feature.label}: не пише в обявата"))
    return checks


def list_page_checks(listing: dict, filters: dict) -> list[Check]:
    """Условията, които се виждат още в списъка с обяви (цена, квадратура, квартал)."""
    checks = [
        _price_check(listing, filters, details_known=False),
        _area_check(listing, filters),
        *_district_checks(listing, filters),
    ]
    return [check for check in checks if check is not None]


def all_checks(listing: dict, filters: dict) -> list[Check]:
    """Всички условия, след като е свалена страницата на обявата."""
    checks = [
        _price_check(listing, filters, details_known=True),
        _area_check(listing, filters),
        *_district_checks(listing, filters),
        *_floor_checks(listing, filters),
        *_building_checks(listing, filters),
        *_feature_checks(listing, filters),
    ]
    return [check for check in checks if check is not None]


def passes(checks: list[Check]) -> bool:
    return all(check.status != FAILED for check in checks)


def filters_text(filters: dict, priorities: set[str] | list[str] | None, commute_to: list[str]) -> str:
    """Кои условия е приложило търсенето, за да се види, ако моделът е изпуснал някое."""
    parts = []

    max_price = filters.get("max_price_eur")
    max_with_parking = filters.get("max_price_with_parking_eur")
    if max_price and max_with_parking:
        parts.append(f"цена до {max_price:,.0f} € (с паркинг до {max_with_parking:,.0f} €)")
    elif max_price:
        parts.append(f"цена до {max_price:,.0f} €")
    elif max_with_parking:
        parts.append(f"цена до {max_with_parking:,.0f} € само с паркинг")
    if filters.get("min_price_eur"):
        parts.append(f"цена от {filters['min_price_eur']:,.0f} €")

    if filters.get("min_area_m2"):
        parts.append(f"над {filters['min_area_m2']:g} кв.м")
    if filters.get("max_area_m2"):
        parts.append(f"под {filters['max_area_m2']:g} кв.м")

    wanted = district_names(filters, "district")
    if wanted:
        parts.append("квартали: " + ", ".join(wanted))
    excluded = district_names(filters, "exclude_districts")
    if excluded:
        parts.append("без: " + ", ".join(excluded))

    if filters.get("exclude_first_floor"):
        parts.append("не първи етаж")
    if filters.get("exclude_last_floor"):
        parts.append("не последен етаж")
    if filters.get("exclude_in_construction"):
        parts.append("не в строеж")
    if filters.get("construction"):
        parts.append(filters["construction"])
    for feature in scoring.FEATURES.values():
        if feature.required_filter and filters.get(feature.required_filter):
            parts.append(f"задължително {feature.label}")

    lines = ["Приложени филтри: " + ("; ".join(parts) if parts else "няма")]
    if priorities:
        names = [scoring.PRIORITY_NAMES.get(priority, priority) for priority in sorted(priorities)]
        lines.append("Приоритети (дават точки): " + ", ".join(names))
    if commute_to:
        lines.append("Места за път (по важност): " + "; ".join(commute_to))
    return "\n".join(lines)
