"""Инструментите, които моделът може да вика. Описанието (docstring) и Args се показват на модела."""

import json

from agno.run import RunContext

from imotko import store
from imotko.geo import distance, driving, geocode
from imotko.search import formatting, saved, scan

# Повече обяви правят отговора на инструмента огромен и моделът не успява да отговори.
MAX_TOP = 15


# Търсенията, вече направени в даден отговор (run_id -> заявка -> id-тата на показаните обяви).
# Моделът понякога пуска същото търсене втори път, а всеки резултат е ~15 хил. знака,
# които после се пращат наново. Пази се само успешно търсене: след грешка повторът е позволен.
_searches_by_run: dict[str, dict[str, list[str]]] = {}

MAX_REMEMBERED_RUNS = 50


def _search_key(query: dict) -> str:
    return json.dumps(query, sort_keys=True, ensure_ascii=False)


def _previous_search(run_context: RunContext | None, query: dict) -> list[str] | None:
    """id-тата на обявите от същото търсене по-рано в този отговор или None, ако го няма."""
    if run_context is None:
        return None
    searches_this_run = _searches_by_run.get(run_context.run_id, {})
    return searches_this_run.get(_search_key(query))


def _remember_search(run_context: RunContext | None, query: dict, shown_ids: list[str]) -> None:
    if run_context is None:
        return
    searches_this_run = _searches_by_run.setdefault(run_context.run_id, {})
    searches_this_run[_search_key(query)] = shown_ids
    _forget_old_runs()


def _forget_old_runs() -> None:
    while len(_searches_by_run) > MAX_REMEMBERED_RUNS:
        oldest_run_id = next(iter(_searches_by_run))
        del _searches_by_run[oldest_run_id]


def _save_search(save_as: str, query: dict, shown_ids: list[str]) -> str:
    # запазва се цялата заявка, за да пуска проверката същото търсене (страници и брой)
    store.save_search(save_as, query)
    # показаните сега обяви не са "нови" при следващата проверка
    store.mark_seen(save_as, shown_ids)
    return f"Търсенето \"{save_as}\" е запазено. Проверява се с watch.py или с check_saved_searches."


def search_apartments(
    city: str,
    rooms: str,
    deal: str = "продажба",
    max_price_eur: int | None = None,
    max_price_with_parking_eur: int | None = None,
    min_price_eur: int | None = None,
    min_area_m2: int | None = None,
    max_area_m2: int | None = None,
    districts: list[str] | None = None,
    exclude_districts: list[str] | None = None,
    construction: str | None = None,
    exclude_first_floor: bool = False,
    exclude_last_floor: bool = False,
    exclude_in_construction: bool = False,
    require_elevator: bool = False,
    require_parking: bool = False,
    require_furnished: bool = False,
    require_terrace: bool = False,
    priorities: list[str] | None = None,
    order_by: str = "date_created",
    commute_to: list[str] | None = None,
    pages: int = 3,
    top: int = 6,
    save_as: str | None = None,
    run_context: RunContext | None = None,
) -> str:
    """Търси апартаменти в imot.bg, анализира ги и връща най-изгодните.
    Със save_as търсенето се запазва и после се следи за нови обяви.

    Args:
        city: Град на български, например "София", "Пловдив", "Стара Загора".
        rooms: "1", "2", "3", "4", "многостаен", "мезонет" или "студио".
        deal: "продажба" или "наем".
        max_price_eur: Максимална цена в евро.
        max_price_with_parking_eur: По-висок таван само за обяви с паркинг, например наем до 650 €,
            а с паркинг до 750 € -> max_price_eur=650, max_price_with_parking_eur=750.
        min_price_eur: Минимална цена в евро.
        min_area_m2: Минимална квадратура.
        max_area_m2: Максимална квадратура.
        districts: Само тези квартали, например ["Лозенец", "Иван Вазов"]. Без него се търси в целия град.
        exclude_districts: Квартали, които потребителят не иска, например ["Студентски град"].
        construction: "тухла", "панел", "епк", "пк", "гредоред" или "монолит".
        exclude_first_floor: Без първи етаж.
        exclude_last_floor: Без последен етаж.
        exclude_in_construction: Без сгради в строеж.
        require_elevator: Само с асансьор.
        require_parking: Само с паркинг или гараж.
        require_furnished: Само обзаведени (отпадат необзаведените и повечето в строеж).
        require_terrace: Само с тераса или балкон.
        priorities: Какво е важно за потребителя (дава точки, не филтрира), от: асансьор, паркинг, тухла, ново,
            акт16, обзаведен, намаление, тераса, висок етаж (от 4-ти нагоре).
        order_by: "date_created" (подразбиране) - най-новите обяви първо; "score" - най-изгодните първо.
        commute_to: Местата, до които потребителят пътува често (най-много 4), с пълните имена,
            например ["Бизнес парк София"]. Първото е най-важното: обявите по-близо до него получават повече точки.
            За всяко се показва време с кола и с градски транспорт.
        pages: Колко страници да се обходят (1-8, по ~40 обяви).
        top: Колко обяви да се върнат (най-много 15).
        save_as: Кратко име, под което търсенето да се запази и следи ("следи ми", "уведоми ме").
    """
    query = {
        "city": city,
        "rooms": rooms,
        "deal": deal,
        "max_price_eur": max_price_eur,
        "max_price_with_parking_eur": max_price_with_parking_eur,
        "min_price_eur": min_price_eur,
        "min_area_m2": min_area_m2,
        "max_area_m2": max_area_m2,
        "district": districts,
        "exclude_districts": exclude_districts,
        "construction": construction,
        "exclude_first_floor": exclude_first_floor,
        "exclude_last_floor": exclude_last_floor,
        "exclude_in_construction": exclude_in_construction,
        "require_elevator": require_elevator,
        "require_parking": require_parking,
        "require_furnished": require_furnished,
        "require_terrace": require_terrace,
        "priorities": priorities,
        "order_by": order_by,
        "commute_to": commute_to,
        "pages": pages,
        "top": min(top, MAX_TOP),
    }

    previous_ids = _previous_search(run_context, query)
    if previous_ids is not None:
        text = ("Това търсене със същите филтри вече е направено в този отговор и резултатът е по-горе. "
                "Не го пускай отново: ползвай онзи резултат или промени филтрите.")
        if save_as:
            text += "\n\n" + _save_search(save_as, query, previous_ids)
        return text

    result = scan.run_search(**query)
    shown_ids = [listing["id"] for listing in result["results"]]
    _remember_search(run_context, query, shown_ids)

    text = formatting.format_results(result)
    if save_as:
        text += "\n\n" + _save_search(save_as, query, shown_ids)
    return text


def compare_districts(city: str, rooms: str, deal: str = "продажба", pages: int = 3) -> str:
    """Сравнява медианната цена на кв.м по квартали в даден град.

    Args:
        city: Град на български.
        rooms: "1", "2", "3", "4", "многостаен", "мезонет" или "студио".
        deal: "продажба" или "наем".
        pages: Колко страници да се обходят (1-8).
    """
    return formatting.district_prices_table(city, rooms, deal, pages)


def distance_between(places_from: list[str], places_to: list[str], city: str = "София") -> str:
    """Разстояние между места в града по картата: права линия, с кола и с градски транспорт.
    Смята всяко място откъде до всяко място докъде (най-много 4 и 4) с едно извикване.
    Казва и колко точна е мярката (по адрес, по улица, по квартал или по обект).

    Args:
        places_from: Откъде: улици ("ул. Враня"), квартали ("Младост 1") или обекти.
        places_to: Докъде, с пълните имена, например ["Университет за национално и световно стопанство"].
        city: Градът, на български.
    """
    if isinstance(places_from, str):
        places_from = [places_from]
    if isinstance(places_to, str):
        places_to = [places_to]
    return distance.describe_distances(places_from, places_to, city)


MIN_REAL_TO_OSRM = 0.8

MAX_REAL_TO_OSRM = 5


def report_error(
    what_was_wrong: str,
    correct_value: str,
    place_from: str | None = None,
    place_to: str | None = None,
    real_minutes: float | None = None,
    city: str = "София",
) -> str:
    """Записва грешка, която потребителят е забелязал, заедно с вярната стойност.
    Поправките се ползват за проверка на инструментите. Поправка за време с кола
    (place_from, place_to, real_minutes) веднага уточнява времето с кола в града.

    Args:
        what_was_wrong: Какво беше грешно, например "УНСС на 9 мин с кола от ул. Елин Връх".
        correct_value: Вярното според потребителя, например "20 мин с кола вечер".
        place_from: За време с кола: откъде.
        place_to: За време с кола: докъде, с пълното име.
        real_minutes: За време с кола: колко минути е реално.
        city: Градът.
    """
    is_drive_time_report = place_from and place_to and real_minutes
    if not is_drive_time_report:
        store.add_feedback("user_report", what_was_wrong, "", correct_value, {})
        return "Записах поправката. Ще се ползва при проверката на инструментите."

    start = geocode.locate_any(place_from, city)
    end = geocode.locate_any(place_to, city)
    route = driving.drive(start, end) if start and end else None

    if route is None:
        store.add_feedback("user_report", what_was_wrong, "", correct_value)
        return "Записах поправката, но не намерих маршрута в картата, затова скоростта в града не е уточнена."

    if not _plausible_drive_minutes(real_minutes, route["minutes"]):
        store.add_feedback("user_report", what_was_wrong, "", correct_value)
        return (
            f"Записах поправката, но {real_minutes:g} мин не приличат на маршрута "
            f"(по картата без трафик е ~{route['minutes']:.0f} мин), затова не променям скоростта в града."
        )

    data = {
        "from": place_from,
        "to": place_to,
        "city": city,
        "from_point": {"lat": start["lat"], "lon": start["lon"]},
        "to_point": {"lat": end["lat"], "lon": end["lon"]},
        "real_minutes": real_minutes,
        "osrm_minutes": round(route["minutes"], 1),
        "osrm_km": round(route["km"], 1),
    }
    store.add_feedback("user_report", what_was_wrong, "", correct_value, data)

    driving.forget_calibration()
    _, measured_route_count = driving.city_extra_minutes()
    return (
        f"Записах: {place_from} -> {place_to}, {real_minutes:g} мин с кола. "
        f"Времето с кола вече е уточнено по {measured_route_count} проверени пътувания."
    )


def _plausible_drive_minutes(real_minutes: float, osrm_minutes: float) -> bool:
    lowest = MIN_REAL_TO_OSRM * osrm_minutes
    highest = MAX_REAL_TO_OSRM * osrm_minutes
    return lowest <= real_minutes <= highest


def list_saved_searches() -> str:
    """Показва запазените търсения."""
    searches = store.list_searches()
    if not searches:
        return "Няма запазени търсения."

    lines = []
    for search in searches:
        lines.append(f"- {search['name']}: {search['params']} (видяни: {len(search['seen'])})")
    return "\n".join(lines)


def delete_saved_search(name: str) -> str:
    """Изтрива запазено търсене.

    Args:
        name: Името на търсенето.
    """
    if store.delete_search(name):
        return "Изтрито."
    return "Няма такова търсене."


def check_saved_searches() -> str:
    """Проверява всички запазени търсения и връща само новите обяви от последната проверка."""
    reports = saved.check_saved_searches()
    # отговорът на инструмента е самата доставка, затова отбелязваме веднага
    saved.mark_as_delivered(reports)

    texts = [report["text"] for report in reports]
    return "\n\n".join(texts) or "Няма нови обяви."
