"""Спирките и метростанциите, които продавачът е написал в описанието на обявата.

Това често е най-точното местоположение в обявата ("на 200 м от метростанция Мария Луиза"):
по-точно от квартала, който е голям няколко километра. Спирката се търси първо в разписанието
на града (точно е и не иска мрежа), после в картата.
"""

import re

from imotko.geo import common, geocode, transit
from imotko.geo.names import ADDRESS_WORDS, QUOTES, name_words

METRO = "метро"
STOP = "спирка"

KIND = "stop"
PRECISION = "спирка от описанието"

MAX_MENTIONS = 3
MAX_MAP_LOOKUPS = 2  # всяка заявка към картата струва поне секунда
MAX_NAME_WORDS = 4
MAX_PAIR_KM = 1.5  # две спирки в описанието: жилището е между тях, ако са толкова близо
MIN_PAIR_KM = 0.05

UNITS = r"(?P<unit>км|м|метра|метър|метри|мин\.?|минути)"
DISTANCE_AFTER = re.compile(rf"[^.;!,]{{0,25}}?(?P<number>\d{{1,4}})\s*{UNITS}\b")
DISTANCE_BEFORE = re.compile(
    rf"(?P<number>\d{{1,4}})\s*{UNITS}(?P<walk>\s+(?:пеша|пешеходно))?(?:\s+разстояние)?\s+от\s+(?:\w+\s+)?$"
)

SOURCE_SCHEDULE = "разписанието на града"
SOURCE_MAP = "картата"

KEYWORD = re.compile(
    r"\b(?P<metro>метростан\w*|метро(?:то)?)\b"
    r"|\b(?P<stop>(?:(?:автобусна|трамвайна|тролейбусна)\s+)?спирка(?:та)?)\b",
    re.IGNORECASE,
)

QUOTED_NAME = re.compile(f"[{re.escape(QUOTES)}]\\s*([^{re.escape(QUOTES)}]{{2,60}}?)\\s*[{re.escape(QUOTES)}]")
SEPARATOR = re.compile(r"\s*[-–:]?\s*")
ADDRESS_PREFIX = re.compile(
    r"(?:на\s+|до\s+|при\s+)?(булевард|бул|улица|ул|площад|пл)(?:\.\s*|\s+|(?=[" + re.escape(QUOTES) + "]))",
    re.IGNORECASE,
)
METRO_NAME = re.compile(r"\bМЕТРО(?:СТАНЦИЯ)?\b", re.IGNORECASE)
CAPITAL_WORD = re.compile(r"[A-ZА-ЯЁ][\w-]*")
NUMBER = re.compile(r"\d{1,2}[а-я]?\b(?!\s*(?:м|км|мин|метра|метър|метри|минута|минути)\b)", re.IGNORECASE)
AND = re.compile(r"\s+и\s+", re.IGNORECASE)

# Думи, които не са част от име, дори да започват с главна буква (текстове с големи букви: "НА 1 МИН")
STOP_WORDS = {
    "на", "до", "от", "в", "във", "е", "са", "се", "и", "или", "с", "със", "за", "към", "при", "по", "има", "няма",
    "наблизо", "близо", "пеша", "пешеходно", "точно", "директно", "само", "около", "приблизително", "разстояние",
    "минути", "мин", "метра", "м", "км", "метро", "метростанция", "спирка", "спирки", "транспорт", "линия", "линии",
    "автобус", "автобуси", "трамвай", "трамваи", "тролей", "тролеи", "градски", "градския",
}


# Намиране в текста

def find_mentions(description: str | None) -> list[dict]:
    """Спирките и метростанциите с име в описанието, по реда в текста.

    Всяка е {"kind": "метро" или "спирка", "name", "is_address", "text", "distance_m", "distance_text"}.
    is_address е за "спирка на бул. България": тогава името е на улицата, на която е спирката.
    distance_m е разстоянието до спирката, ако продавачът го е написал ("на 630 м", "5 минути пеша"), иначе None.
    "Спирки на градски транспорт" без име не е споменаване.
    """
    if not description:
        return []

    text = description
    mentions = []
    seen = set()
    for keyword in KEYWORD.finditer(text):
        kind = METRO if keyword.group("metro") else STOP
        for name, is_address, end in _read_names(text, keyword.end()):
            words = name_words(name)
            if words in seen:
                continue
            seen.add(words)
            word = "метростанция" if kind == METRO else "спирка"
            text_after_word = f"на {name}" if is_address else name
            distance_m, distance_text = _stated_distance(text, keyword.start(), end)
            mentions.append({
                "kind": kind, "name": name, "is_address": is_address, "text": f"{word} {text_after_word}",
                "distance_m": distance_m, "distance_text": distance_text,
            })
    return mentions[:MAX_MENTIONS]


def _stated_distance(text: str, keyword_start: int, name_end: int) -> tuple[int | None, str | None]:
    """Разстоянието, написано до спирката: "на 630 м", "500 метра от ...", "5 минути пеша от ..."."""
    before = DISTANCE_BEFORE.search(text[max(0, keyword_start - 60):keyword_start])
    if before:
        return _distance(before, is_walk=bool(before.group("walk")))

    window = text[name_end:name_end + 45]
    after = DISTANCE_AFTER.match(window)
    if after:
        return _distance(after, is_walk="пеша" in window or "пешеходно" in window)
    return None, None


def _distance(match: re.Match, is_walk: bool) -> tuple[int | None, str | None]:
    number = int(match.group("number"))
    unit = match.group("unit").lower()
    if unit.startswith("мин"):
        if not is_walk:
            return None, None  # минути с кола не са разстояние до спирката
        return round(number * 60 * transit.WALK_METERS_PER_SECOND), f"{number} мин пеша"
    meters = number * 1000 if unit == "км" else number
    return meters, f"{number} {'км' if unit == 'км' else 'м'}"


def _read_names(text: str, position: int) -> list[tuple[str, bool, int]]:
    """Името след ключовата дума и имената след "и": "Константин Величков и Опълченска"."""
    names = []
    while True:
        read = _read_name(text, position)
        if read is None:
            return names
        name, is_address, position = read
        names.append((name, is_address, position))

        and_match = AND.match(text, position)
        if and_match is None:
            return names
        position = and_match.end()


def _read_name(text: str, position: int) -> tuple[str, bool, int] | None:
    position = SEPARATOR.match(text, position).end()

    prefix = ADDRESS_PREFIX.match(text, position)
    if prefix:
        position = prefix.end()

    quoted = QUOTED_NAME.match(text, position)
    if quoted:
        name, end = quoted.group(1), quoted.end()
    else:
        name, end = _read_capitalized_words(text, position)

    if not name or not re.search(r"[A-Za-zА-Яа-я]", name):
        return None
    if name.lower() in STOP_WORDS:
        return None
    if prefix:
        name = f"{ADDRESS_WORDS[prefix.group(1).lower()]} {name}"
    return name, bool(prefix), end


def _read_capitalized_words(text: str, position: int) -> tuple[str, int]:
    words = []
    end = position
    while len(words) < MAX_NAME_WORDS:
        match = CAPITAL_WORD.match(text, position)
        if match is None and words:
            match = NUMBER.match(text, position)  # "Младост 3"
        if match is None or match.group().lower() in STOP_WORDS:
            break
        words.append(match.group())
        end = match.end()
        position = end + len(text[end:]) - len(text[end:].lstrip(" "))
    return " ".join(words), end


# Намиране на място

def locate(mentions: list[dict], city: str, district: dict | None, use_map: bool = True) -> dict | None:
    """Първата спирка от описанието, която се намира в квартала.

    Връща {"lat", "lon", "name", "kind": "stop", "source", "mention"} или None. Без точка на квартала
    не търсим: в града има много спирки с едно и също име и нямаме как да изберем правилната.
    use_map=False търси само в разписанието: картата е бавна и е нужна само когато спирката е единственото място.
    """
    if not mentions or not district:
        return None

    found = []
    for mention in mentions:
        place = _find_in_schedule(mention, city, district)
        if place and all(common.distance_km(place, other) > MIN_PAIR_KM for other in found):
            found.append(place)

    if not found and use_map:
        for mention in mentions[:MAX_MAP_LOOKUPS]:
            place = _find_on_map(mention, city, district)
            if place:
                return place
    if not found:
        return None
    if len(found) >= 2 and common.distance_km(found[0], found[1]) <= MAX_PAIR_KM:
        return _between(found[0], found[1])
    return found[0]


def _between(first: dict, second: dict) -> dict:
    """Две спирки в описанието: жилището е някъде между тях, затова точката е по средата."""
    return {
        "lat": (first["lat"] + second["lat"]) / 2,
        "lon": (first["lon"] + second["lon"]) / 2,
        "name": f"между {first['name']} и {second['name']}",
        "kind": KIND,
        "source": first["source"],
        "mention": f"{first['mention']} и {second['mention']}",
        "stated_m": None,
        "stated_text": None,
    }


def _match_rank(wanted: frozenset[str], candidate: frozenset[str]) -> int | None:
    """0 е същото име, 1 е по-пълно име на спирката, 2 е спирка с по-кратко име от написаното."""
    if not wanted or not candidate:
        return None
    if wanted == candidate:
        return 0
    if wanted < candidate:
        return 1
    if candidate < wanted and len(candidate) >= 2:
        return 2
    return None


def _name_variants(name: str) -> list[str]:
    """Името, а при неуспех и без последните думи: "Централна ЖП гара още" -> "Централна ЖП"."""
    words = name.split()
    return [" ".join(words[:count]) for count in range(len(words), 0, -1)]


def _is_metro(stop: dict) -> bool:
    return stop["is_station"] or bool(METRO_NAME.search(stop["name"]))


# град -> (списъкът със спирките, спирките по всяка дума от името им)
_stops_by_word: dict[str, tuple[list[dict], dict[str, list[dict]]]] = {}


def _index_of(city: str) -> dict[str, list[dict]]:
    """Спирките на града по думите от името им: така не обхождаме всичките ~5000 за всяко споменаване."""
    stops = transit.named_stops(city)
    cached = _stops_by_word.get(transit.city_key(city))
    if cached and cached[0] is stops:
        return cached[1]

    index = {}
    for stop in stops:
        for word in name_words(stop["name"]):
            index.setdefault(word, []).append(stop)
    _stops_by_word[transit.city_key(city)] = (stops, index)
    return index


def _find_in_schedule(mention: dict, city: str, district: dict) -> dict | None:
    index = _index_of(city)
    # всяко съвпадение има поне една обща дума с написаното име
    with_common_word = {id(stop): stop for word in name_words(mention["name"]) for stop in index.get(word, [])}
    candidates = [stop for stop in with_common_word.values() if mention["kind"] != METRO or _is_metro(stop)]
    nearby = [(stop, common.distance_km(district, stop)) for stop in candidates]
    nearby = [(stop, km) for stop, km in nearby if km <= geocode.STREET_MAX_KM_FROM_DISTRICT]

    for variant in _name_variants(mention["name"]):
        wanted = name_words(variant)
        matches = [
            (rank, km, stop) for stop, km in nearby
            if (rank := _match_rank(wanted, name_words(stop["name"]))) is not None
        ]
        if matches:
            _, _, best = min(matches, key=lambda match: match[:2])
            return _stop_place(mention, best, SOURCE_SCHEDULE)
    return None


def _find_on_map(mention: dict, city: str, district: dict) -> dict | None:
    if mention["is_address"]:
        found = geocode.locate(mention["name"], city, "street", near=district)
    else:
        found = geocode.locate(mention["text"], city, "place", near=district)

    if found is None or found["far_from_district"] or found["warnings"]:
        return None
    return _stop_place(mention, found, SOURCE_MAP)


def _stop_place(mention: dict, point: dict, source: str) -> dict:
    return {
        "lat": point["lat"],
        "lon": point["lon"],
        "name": point["name"],
        "kind": KIND,
        "source": source,
        "mention": mention["text"],
        "stated_m": mention["distance_m"],
        "stated_text": mention["distance_text"],
    }
