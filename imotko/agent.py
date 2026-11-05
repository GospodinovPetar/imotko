"""Агентът "Имотко": инструментите, които моделът може да вика, и самият агент."""

import requests
from agno.agent import Agent
from agno.db.sqlite import SqliteDb

from imotko import search, store, vision
from imotko.config import DATA_DIR, FALLBACK_MODEL, MAIN_MODEL
from imotko.models import main_and_fallback_models

CHAT_HISTORY_DB = DATA_DIR / "imot.db"


# Инструменти. Описанието (docstring) и Args се показват на модела.


def search_apartments(
    city: str,
    rooms: str,
    deal: str = "продажба",
    max_price_eur: int | None = None,
    min_price_eur: int | None = None,
    min_area_m2: int | None = None,
    max_area_m2: int | None = None,
    district: str | None = None,
    construction: str | None = None,
    exclude_first_floor: bool = False,
    exclude_last_floor: bool = False,
    exclude_in_construction: bool = False,
    require_elevator: bool = False,
    require_parking: bool = False,
    require_furnished: bool = False,
    priorities: list[str] | None = None,
    order_by: str = "date_created",
    pages: int = 3,
    top: int = 6,
    save_as: str | None = None,
) -> str:
    """Търси апартаменти в imot.bg, анализира ги и връща най-изгодните.
    Със save_as търсенето се запазва и после се следи за нови обяви.

    Args:
        city: Град на български, например "София", "Пловдив", "Стара Загора".
        rooms: "1", "2", "3", "4", "многостаен", "мезонет" или "студио".
        deal: "продажба" или "наем".
        max_price_eur: Максимална цена в евро.
        min_price_eur: Минимална цена в евро.
        min_area_m2: Минимална квадратура.
        max_area_m2: Максимална квадратура.
        district: Част от името на квартал, например "Лозенец".
        construction: "тухла", "панел", "епк", "пк", "гредоред" или "монолит".
        exclude_first_floor: Без първи етаж.
        exclude_last_floor: Без последен етаж.
        exclude_in_construction: Без сгради в строеж.
        require_elevator: Само с асансьор.
        require_parking: Само с паркинг или гараж.
        require_furnished: Само обзаведени (отпадат необзаведените и повечето в строеж).
        priorities: Какво е важно за потребителя, от: асансьор, паркинг, тухла, ново, акт16, обзаведен, намаление.
        order_by: "date_created" (подразбиране) - най-новите обяви първо; "score" - най-изгодните първо.
        pages: Колко страници да се обходят (1-8, по ~40 обяви).
        top: Колко обяви да се върнат.
        save_as: Кратко име, под което търсенето да се запази и следи ("следи ми", "уведоми ме").
    """
    query = {
        "city": city,
        "rooms": rooms,
        "deal": deal,
        "max_price_eur": max_price_eur,
        "min_price_eur": min_price_eur,
        "min_area_m2": min_area_m2,
        "max_area_m2": max_area_m2,
        "district": district,
        "construction": construction,
        "exclude_first_floor": exclude_first_floor,
        "exclude_last_floor": exclude_last_floor,
        "exclude_in_construction": exclude_in_construction,
        "require_elevator": require_elevator,
        "require_parking": require_parking,
        "require_furnished": require_furnished,
        "priorities": priorities,
        "order_by": order_by,
        "pages": pages,
        "top": top,
    }

    result = search.run_search(**query)
    found = result["results"]

    text = search.format_results(result)

    if save_as:
        # запазва се цялата заявка, за да пуска проверката същото търсене (страници и брой)
        store.save_search(save_as, query)
        # показаните сега обяви не са "нови" при следващата проверка
        store.mark_seen(save_as, [listing["id"] for listing in found])
        text += f"\n\nТърсенето \"{save_as}\" е запазено. Проверява се с watch.py или с check_saved_searches."

    return text


def compare_districts(city: str, rooms: str, deal: str = "продажба", pages: int = 3) -> str:
    """Сравнява медианната цена на кв.м по квартали в даден град.

    Args:
        city: Град на български.
        rooms: "1", "2", "3", "4", "многостаен", "мезонет" или "студио".
        deal: "продажба" или "наем".
        pages: Колко страници да се обходят (1-8).
    """
    return search.district_prices_table(city, rooms, deal, pages)


def list_saved_searches() -> str:
    """Показва запазените търсения."""
    searches = store.list_searches()
    if not searches:
        return "Няма запазени търсения."

    lines = []
    for saved in searches:
        lines.append(f"- {saved['name']}: {saved['params']} (видяни: {len(saved['seen'])})")
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
    reports = search.check_saved_searches()
    # отговорът на инструмента е самата доставка, затова отбелязваме веднага
    search.mark_as_delivered(reports)

    texts = [report["text"] for report in reports]
    return "\n\n".join(texts) or "Няма нови обяви."


def _return_errors_as_text(function_call, arguments):
    """Невалиден филтър или недостъпен imot.bg се връщат на модела като текст, а не като срив.

    agno подава аргументите по име, затова имената function_call и arguments са задължителни.
    """
    try:
        return function_call(**arguments)
    except ValueError as error:
        return str(error)
    except requests.RequestException as error:
        return f"imot.bg не отговаря в момента ({error}). Опитай пак след малко."
    
    

INSTRUCTIONS = [
    "Ти си брокер с 10 години опит в търсене на апартаменти в imot.bg. Отговаряш само на български.",
    "Когато потребителят зададе филтър, извикай search_apartments. Ако липсва град или брой стаи, питай.",
    "Преведи изискванията в параметри: \"не искам последен етаж\"-> exclude_last_floor, \"задължително асансьор\"-> require_elevator, "
    "\"важно ми е паркинг\" -> priorities, \"обзаведен\"-> require_furnished. По подразбиране най-новите обяви са първи (order_by=\"date_created\"); ако потребителят иска най-изгодните първо-> order_by=\"score\". Не питай за неща, които не са казани.",
    "Въпрос за видими неща в обява (хладилник, мебели, ремонт, състояние)-> analyze_photos с линка на обявата. Не отговаряй по памет.",
    "Търсене за \"кой квартал е евтин\"-> compare_districts. \"Следи ми\" или \"уведоми ме\"-> search_apartments със save_as (кратко име) и всички казани филтри.",
    "След резултата направи кратък анализ: кои 2-3 обяви са най-добри и защо, на какво да се внимава (етаж, строеж, цена над квартала).",
    "Не измисляй обяви и числа. Показвай само данните от инструментите, с линковете им. Не противоречи на тях (напр. \"в строеж\" не е \"готово за заселване\").",
    "Пиши внимателно, на чист български, без чужди думи и без смесване на езици.",
]

TOOLS = [
    search_apartments,
    compare_districts,
    vision.analyze_photos,
    list_saved_searches,
    delete_saved_search,
    check_saved_searches,
]

main_model, fallback_models = main_and_fallback_models(MAIN_MODEL, FALLBACK_MODEL, max_tokens=2048, temperature=0.3)

imot_agent = Agent(
    id="imot",
    name="Имотко",
    model=main_model,
    fallback_models=fallback_models,
    instructions=INSTRUCTIONS,
    db=SqliteDb(db_file=str(CHAT_HISTORY_DB)),
    add_history_to_context=True,
    num_history_runs=3,
    tools=TOOLS,
    tool_hooks=[_return_errors_as_text],
)
