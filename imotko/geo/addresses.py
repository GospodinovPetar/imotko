"""Адресът на жилището, написан в описанието на обявата: "на ул. Българска легия 28".

Продавачите често пишат адреса само в свободния текст, а не в полето за улица. Взимаме само адрес
с номер: "близо до бул. България" е ориентир, не адрес, а грешна улица мести жилището на километри.
"""

import re

from imotko.geo.names import ADDRESS_WORDS, QUOTES, name_words

MAX_ADDRESSES = 3

ADDRESS = re.compile(
    r"(?<!\w)(?i:(?P<prefix>булевард|бул|улица|ул))\.?\s*"
    rf"[{re.escape(QUOTES)}]?\s*"
    r"(?P<name>[A-ZА-ЯЁ0-9][\w.-]*(?: [\w.-]+){0,3}?)"
    rf"\s*[{re.escape(QUOTES)}]?\s*,?\s*(?:№\s*|(?i:No|номер|н-р)\.?\s*)?"
    r"(?P<number>\d{1,3}(?:\s?[А-ЯA-Z])?)(?![\w])"
    r"(?!\s*(?:-?\s*стаен|стаи|кв\.?\s*м|м2|м²|етаж|ет\.|мин|метра|евро|€|лв|%))"
)

# Думи преди улицата, които значат "ориентир", а не адрес на жилището
LANDMARK_WORDS = {
    "до", "близо", "близост", "от", "към", "между", "по", "покрай", "край", "пред", "срещу", "отсреща",
    "ъгъла", "ъгъл", "излаз", "достъп", "подход", "метро", "метростанция", "спирка",
}
# "ж.к. Банишора, ул. Зайчар." - улица без номер, но веднага след квартала: това е адресът, не ориентир
AFTER_DISTRICT = re.compile(
    r"(?:ж\.\s?к\.|кв\.|квартал)\s+[A-ZА-ЯЁ][\w .-]{1,30}?,\s*"
    r"(?i:(?P<prefix>булевард|бул|улица|ул))\.?\s*"
    rf"[{re.escape(QUOTES)}]?\s*(?P<name>[A-ZА-ЯЁ][\w-]*(?: [^\W\d_][\w-]*){{0,2}}?)\s*[{re.escape(QUOTES)}]?\s*(?=[.,;]|$)"
)

# Думи преди адреса, които значат адрес на агенцията, не на жилището ("Офис: бул. Витоша 12")
OFFICE_WORDS = {"офис", "офиса", "агенция", "агенцията", "фирмата", "контакти", "контакт", "седалище", "централа"}
# Думи, които не са част от име на улица: значи регулярният израз е хванал изречение
SENTENCE_WORDS = {"е", "са", "се", "на", "до", "от", "във", "в", "със", "с", "за", "при", "има", "няма", "ще", "като"}


def find_addresses(description: str | None) -> list[dict]:
    """Адресите в описанието: първо тези с номер, по реда в текста, после улица без номер веднага след квартала.
    Всеки е {"text", "street"}; при адрес без номер двете са еднакви.

    "text" е "ул. Българска легия 28", "street" е "ул. Българска легия".
    """
    if not description:
        return []

    text = description
    addresses = []
    for match in ADDRESS.finditer(text):
        if _is_landmark(text, match) or _is_sentence(match.group("name")):
            continue

        street = f"{ADDRESS_WORDS[match.group('prefix').lower()]} {match.group('name').strip(' .')}"
        number = re.sub(r"\s", "", match.group("number")).upper()
        address = {"text": f"{street} {number}", "street": street}
        if address not in addresses:
            addresses.append(address)

    for match in AFTER_DISTRICT.finditer(text):
        if _is_sentence(match.group("name")):
            continue
        street = f"{ADDRESS_WORDS[match.group('prefix').lower()]} {match.group('name').strip(' .')}"
        if all(address["street"] != street for address in addresses):
            addresses.append({"text": street, "street": street})
    return addresses[:MAX_ADDRESSES]


def street_words(street: str) -> frozenset[str]:
    """Различаващите думи от улицата без номера ("ул. Българска легия 28" -> {БЪЛГАРСКА, ЛЕГИЯ}), за сравнение."""
    return name_words(re.sub(r"\s+\d{1,3}\s?[А-ЯA-Z]?$", "", street))


def _is_landmark(text: str, match: re.Match) -> bool:
    words_before = re.findall(r"\w+", text[:match.start()].lower())[-4:]
    is_landmark = any(word in LANDMARK_WORDS for word in words_before[-2:])
    return is_landmark or any(word in OFFICE_WORDS for word in words_before)


def _is_sentence(name: str) -> bool:
    return any(word.lower() in SENTENCE_WORDS for word in name.split())
