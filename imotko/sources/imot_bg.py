"""Сваляне и разчитане на страниците на imot.bg.

Модулът знае всичко за сайта: адресите, HTML структурата и как се четат цени, етажи и белези.
Останалите модули работят само с готовите речници с данни за обявите.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from imotko.sources.base import ListingSource


BASE_URL = "https://www.imot.bg"
BGN_PER_EUR = 1.95583

CACHE_SECONDS = 600
CACHE_MAX_PAGES = 200
MAX_PARALLEL_REQUESTS = 4

# Дума от потребителя -> част от адреса в imot.bg
DEALS = {
    "продажба": "prodazhbi",
    "продажби": "prodazhbi",
    "наем": "naemi",
    "наеми": "naemi",
}

ROOMS = {
    "1": "ednostaen",
    "едностаен": "ednostaen",
    "2": "dvustaen",
    "двустаен": "dvustaen",
    "3": "tristaen",
    "тристаен": "tristaen",
    "4": "chetiristaen",
    "четиристаен": "chetiristaen",
    "многостаен": "mnogostaen",
    "мезонет": "mezonet",
    "студио": "atelie-tavan",
    "ателие": "atelie-tavan",
}

CONSTRUCTION_TYPES = ("тухла", "панел", "епк", "пк", "гредоред", "монолит")

# Етажите без номер: партерът е 0, сутеренът е -1
NAMED_FLOORS = {"партер": 0, "сутерен": -1}

# Кирилица -> латиница, както imot.bg пише градовете в адресите ("София" -> "sofiya")
CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
}


# Сваляне на страници (с кеш)

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0"

# адрес -> (кога е свалена, HTML)
_page_cache: dict[str, tuple[float, str]] = {}
_page_cache_lock = threading.Lock()

# Един общ пул за целия процес, за да важи лимитът и при няколко търсения едновременно
_download_pool = ThreadPoolExecutor(MAX_PARALLEL_REQUESTS, thread_name_prefix="imot")


def http_get(url: str) -> requests.Response:
    response = _session.get(url, timeout=15)
    response.raise_for_status()
    return response


def fetch_html(url: str) -> str:
    """HTML на страница. Повторно поискана страница се взима от кеша."""
    now = time.time()

    with _page_cache_lock:
        cached = _page_cache.get(url)

    if cached is not None:
        downloaded_at, html = cached
        if now - downloaded_at < CACHE_SECONDS:
            return html

    # imot.bg е в кодировка windows-1251
    html = http_get(url).content.decode("cp1251", errors="replace")

    with _page_cache_lock:
        if len(_page_cache) >= CACHE_MAX_PAGES:
            _remove_expired_pages(now)
        _page_cache[url] = (time.time(), html)

    return html


def forget_page(url: str) -> None:
    """Маха страницата от кеша, за да се свали наново следващия път."""
    with _page_cache_lock:
        _page_cache.pop(url, None)


def _remove_expired_pages(now: float) -> None:
    expired = []
    for url, (downloaded_at, _html) in _page_cache.items():
        if now - downloaded_at >= CACHE_SECONDS:
            expired.append(url)

    for url in expired:
        del _page_cache[url]


# Градове и адреси

def city_slug(city: str) -> str:
    """ "Стара Загора" -> "grad-stara-zagora". Приема и готов адрес на град."""
    city = city.strip().lower()
    if city.startswith("grad-"):
        return city

    latin = ""
    for letter in city:
        latin += CYRILLIC_TO_LATIN.get(letter, letter)

    # интервалите и другите знаци стават тирета
    latin = re.sub(r"[^a-z0-9]+", "-", latin).strip("-")
    return "grad-" + latin


_known_city_slugs: frozenset[str] = frozenset()


def known_city_slugs() -> frozenset[str]:
    """Градовете с обяви в imot.bg (областните центрове). Списъкът не се променя, затова се сваля веднъж.

    Ако сайтът върне страница без градове (временен проблем при тях), списъкът не се запомня
    и се хвърля requests.RequestException, за да се опита пак при следващото търсене.
    """
    global _known_city_slugs
    if _known_city_slugs:
        return _known_city_slugs

    homepage_url = BASE_URL + "/"
    city_slugs = frozenset(re.findall(r"obiavi/prodazhbi/(grad-[a-z-]+)\"", fetch_html(homepage_url)))
    if not city_slugs:
        forget_page(homepage_url)
        raise requests.RequestException("imot.bg не върна списъка с градове")

    _known_city_slugs = city_slugs
    return city_slugs


def listing_url(deal: str, city: str, rooms: str) -> str:
    """Адресът на списъка с обяви.

    Хвърля ValueError при непозната сделка, брой стаи или град
    и requests.RequestException, ако списъкът с градове не може да се свали.
    """
    deal_part = DEALS.get(deal.strip().lower())
    rooms_part = ROOMS.get(rooms.strip().lower())
    if deal_part is None or rooms_part is None:
        raise ValueError("Невалиден филтър. Сделка: продажба/наем. Стаи: " + ", ".join(sorted(ROOMS)))

    city_part = city_slug(city)
    known_cities = known_city_slugs()
    if city_part not in known_cities:
        city_names = sorted(slug.removeprefix("grad-") for slug in known_cities)
        raise ValueError(f"Не познавам град \"{city}\" в imot.bg. Налични: " + ", ".join(city_names))

    return f"{BASE_URL}/obiavi/{deal_part}/{city_part}/{rooms_part}"


def listing_id(url: str) -> str | None:
    """id на обявата от пълния ѝ линк, или None, ако линкът не е към обява в imot.bg."""
    match = re.match(r"https://(?:www\.)?imot\.bg/obiava-([0-9a-z]+)-", url)
    if match is None:
        return None
    return match.group(1)


def created_timestamp(ad_id: str) -> int | None:
    """Кога е създадена обявата.

    Датата е вградена в id-то: 2 знака + unix време (10 цифри) + още цифри.
    Тя не се променя, когато обявата се редактира (за разлика от "Коригирана" на страницата).
    """
    match = re.fullmatch(r"\w\w(\d{10})\d*", ad_id)
    if match is None:
        return None

    timestamp = int(match.group(1))
    year_2000 = 946684800
    tomorrow = time.time() + 86400
    if year_2000 <= timestamp <= tomorrow:
        return timestamp
    return None


# Цени и етажи

def parse_price(text: str) -> float | None:
    """Цена в евро. При цена в две валути ("125 000 € / 244 479 лв.") взима еврото.

    Връща None за "Цена по договаряне".
    """
    euro = None
    leva = None

    for number, currency in re.findall(r"(\d[\d\s.,]*)\s*(€|eur|лв)", text, re.IGNORECASE):
        amount = _parse_amount(number)
        if currency.lower() == "лв":
            if leva is None:
                leva = amount
        elif euro is None:
            euro = amount

    if euro:
        return euro
    if leva:
        return round(leva / BGN_PER_EUR)
    return None


def _parse_amount(text: str) -> float | None:
    """"125 000" -> 125000, "125.000" -> 125000, "1 250,50" -> 1250.5"""
    digits = re.sub(r"\s", "", text).rstrip(".,")

    # точки или запетаи през три цифри са разделители на хилядите
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits):
        return float(re.sub(r"[.,]", "", digits))

    if re.fullmatch(r"\d+(?:[.,]\d+)?", digits):
        return float(digits.replace(",", "."))

    return None


def _floor_number(text: str) -> int:
    """"3" -> 3, "Партер" -> 0, "Сутерен" -> -1"""
    if text.isdigit():
        return int(text)
    return NAMED_FLOORS.get(text.lower(), 0)


def parse_listing_floor(info: str) -> tuple[int | None, int | None]:
    """(етаж, общо етажи) от краткото описание в списъка: "3-ти ет. от 6" или "Партер от 9"."""
    numbered = re.search(r"(\d+)\D{0,3}\s*ет\.?\s*от\s*(\d+)", info)
    if numbered:
        return int(numbered.group(1)), int(numbered.group(2))

    named = re.search(r"(партер|сутерен)\s*от\s*(\d+)", info, re.IGNORECASE)
    if named:
        return _floor_number(named.group(1)), int(named.group(2))

    return None, None


# Списък с обяви

def _text_of(parent, selector: str, separator: str = " ") -> str:
    element = parent.select_one(selector)
    if element is None:
        return ""
    return element.get_text(separator, strip=True)


def _area_from_info(info: str) -> float | None:
    area_match = re.search(r"(\d+(?:[.,]\d+)?)\s*кв\.?\s*м", info)
    if area_match is None:
        return None
    return float(area_match.group(1).replace(",", "."))


def _year_from_info(info: str) -> int | None:
    year_match = re.search(r"(?:експлоатация|строителство)\s*(\d{4})", info)
    if year_match is None:
        return None
    return int(year_match.group(1))


def _street_from_info(info: str) -> str | None:
    # "64 кв.м, ул. Ген. Никола Жеков, 2-ри ет. от 4": улицата е веднага след квадратурата, ако я има
    street_match = re.match(r"[\d.,]+\s*кв\.?\s*м,\s*((?:ул|бул)\.\s*[^,]{2,40})", info)
    if street_match is None:
        return None
    return street_match.group(1).strip()


def _district_from_location(location: str) -> str:
    # "град София, Лозенец" -> квартал "Лозенец"
    if "," in location:
        return location.split(",", 1)[1].strip()
    return location


def parse_listing(item) -> dict | None:
    """Данните за една обява от списъка. None, ако обявата няма цена."""
    title_link = item.select_one("a.title")
    price_element = item.select_one("div.price")
    if title_link is None or price_element is None:
        return None

    price = parse_price(price_element.get_text(" ", strip=True))
    if price is None:
        return None

    info = _text_of(item, "div.info")
    area = _area_from_info(info)
    floor, floors_total = parse_listing_floor(info)

    location_element = title_link.find("location")
    location = location_element.get_text(strip=True) if location_element else ""

    url = urljoin(BASE_URL, title_link["href"])
    ad_id = listing_id(url)

    return {
        "id": ad_id or url,
        "created": created_timestamp(ad_id) if ad_id else None,
        "title": title_link.contents[0].strip() if title_link.contents else "",
        "location": location,
        "district": _district_from_location(location),
        "street": _street_from_info(info),
        "price_eur": float(price),
        "area_m2": area,
        "eur_per_m2": round(price / area, 1) if area else None,
        "floor": floor,
        "floors_total": floors_total,
        "year_built": _year_from_info(info),
        "info": info[:300],
        "url": url,
    }


def parse_listing_page(html: str) -> list[dict]:
    page = BeautifulSoup(html, "html.parser")

    listings = []
    for item in page.select("div.item"):
        listing = parse_listing(item)
        if listing is not None:
            listings.append(listing)
    return listings


def fetch_listings(deal: str, city: str, rooms: str, pages: int) -> tuple[list[dict], list[str]]:
    """Обявите от първите `pages` страници (без повторения) и списък с грешките при свалянето."""
    first_page = listing_url(deal, city, rooms)
    pages = max(1, min(pages, 8))

    urls = [first_page]
    for page_number in range(2, pages + 1):
        urls.append(f"{first_page}/p-{page_number}")

    errors: list[str] = []

    def fetch_one_page(url: str) -> list[dict]:
        try:
            page_listings = parse_listing_page(fetch_html(url))
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            errors.append(f"{url}: {status or error}")
            return []

        if not page_listings:
            # празна страница може да е временен проблем при тях: да не стои 10 минути в кеша
            forget_page(url)
        return page_listings

    listings_by_id: dict[str, dict] = {}
    for page_listings in _download_pool.map(fetch_one_page, urls):
        for listing in page_listings:
            # промотираните обяви се повтарят на няколко страници
            if listing["id"] not in listings_by_id:
                listings_by_id[listing["id"]] = listing

    return list(listings_by_id.values()), errors


# Страницата на една обява

def is_mentioned(text: str, pattern: str) -> bool:
    """Споменато ли е нещо без отрицание пред него.

    "има асансьор" -> да; "без асансьор", "няма гараж", "не е обзаведен" -> не.
    """
    for match in re.finditer(pattern, text):
        before_start = max(0, match.start() - 25)
        words_before = text[before_start:match.start()]
        is_negated = re.search(r"(?<!\w)(?:без|няма|не)\s+(?:\S+\s+)?$", words_before)
        if not is_negated:
            return True
    return False


def listing_photos(html: str) -> list[str]:
    """Снимките на обявата в размер /big/ (800 px).

    Страницата показва и снимки на други обяви, затова взимаме само тези с id-то на обявата.
    Размерът /big/ съществува за всяка снимка, за разлика от по-големия /big1/.
    """
    ad_id = re.search(r'"sku":\s*"([0-9a-z]+)"', html)
    if ad_id is None:
        return []

    pattern = (
        r"//(imotstatic\d*\.focus\.bg/imot/photosimotbg/\d/\d+)/+(?:big1?/)?("
        + ad_id.group(1)
        + r"_\w+\.jpg)"
    )

    photo_urls: dict[str, str] = {}  # име на файла -> адрес
    for match in re.finditer(pattern, html):
        folder, file_name = match.group(1), match.group(2)
        if file_name not in photo_urls:
            photo_urls[file_name] = f"https://{folder}/big/{file_name}"

    return list(photo_urls.values())


def parse_detail(html: str) -> dict:
    """Данните от страницата на обявата. Неразпознатите полета са None."""
    page = BeautifulSoup(html, "html.parser")

    params = _text_of(page, "div.adParams")
    # "Особености" са подредени белези; описанието е свободен текст
    feature_items = [item.get_text(strip=True) for item in page.select("div.carExtri div.items div")]
    features = ", ".join(feature_items)
    description = _text_of(page, "div.moreInfo div.text")

    all_text = f"{params} {features} {description}".lower()

    floor, floors_total = _detail_floor(params)
    construction_match = re.search(r"\b(" + "|".join(CONSTRUCTION_TYPES) + r")\b", all_text)
    year_match = re.search(r"експлоатация\s*(\d{4})", params)

    return {
        "floor": floor,
        "floors_total": floors_total,
        "construction": construction_match.group(1) if construction_match else None,
        "year_built": int(year_match.group(1)) if year_match else None,
        "in_construction": _is_in_construction(params, all_text),
        "has_act16": is_mentioned(all_text, r"акт 16"),
        "has_elevator": is_mentioned(all_text, r"асансьор"),
        "has_parking": is_mentioned(all_text, r"паркинг|гараж|паркомясто"),
        "furnished": is_mentioned(all_text, r"\bобзаведен|с обзавеждане"),
        "has_terrace": is_mentioned(all_text, r"тераса|(?<!френски )балкон"),
        "photos": listing_photos(html),
    }


def _detail_floor(params: str) -> tuple[int | None, int | None]:
    floor_match = re.search(r"етаж:\s*(\d+|партер|сутерен)\D*?от\s*(\d+)", params, re.IGNORECASE)
    if floor_match is None:
        return None, None
    return _floor_number(floor_match.group(1)), int(floor_match.group(2))


def _is_in_construction(params: str, all_text: str) -> bool:
    not_finished_yet = re.search(r"ще бъде въведен|не е въведен", params, re.IGNORECASE)
    construction_words = re.search(r"в строеж|новостро[ия]щ|акт 14|акт 15", all_text)
    return bool(not_finished_yet or construction_words)


def add_details(listings) -> None:
    """Сваля страницата на всяка обява и добавя данните от нея към речника на обявата.

    Ако страницата не се свали, обявата остава без тези полета (неизвестни).
    Поле, което страницата не съдържа (None), не изтрива стойността от списъка.
    """

    def add_details_to_one(listing: dict) -> None:
        try:
            details = parse_detail(fetch_html(listing["url"]))
        except requests.RequestException:
            return

        for key, value in details.items():
            if value is not None:
                listing[key] = value

    list(_download_pool.map(add_details_to_one, listings))


# Клас за останалия код

class ImotBg(ListingSource):
    """imot.bg. Само подава към функциите по-горе."""

    construction_types = CONSTRUCTION_TYPES
    named_floors = NAMED_FLOORS
    max_parallel_requests = MAX_PARALLEL_REQUESTS

    def is_rent(self, deal: str) -> bool:
        return DEALS.get(deal.strip().lower()) == "naemi"

    def fetch_listings(self, deal, city, rooms, pages):
        return fetch_listings(deal, city, rooms, pages)

    def add_details(self, listings) -> None:
        add_details(listings)

    def listing_id(self, url):
        return listing_id(url)

    def fetch_html(self, url):
        return fetch_html(url)

    def listing_photos(self, html):
        return listing_photos(html)

    def download(self, url):
        return http_get(url).content
