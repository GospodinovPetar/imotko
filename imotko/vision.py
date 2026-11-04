"""Анализ на снимките на обява с мултимодален модел (хладилник, обзавеждане, състояние)."""

import io
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image as PillowImage
from agno.agent import Agent
from agno.media import Image
from agno.run import RunStatus

from imotko.config import VISION_FALLBACK_MODEL, VISION_MODEL
from imotko.models import main_and_fallback_models
from imotko.sources import source

MAX_PHOTOS = 6
MAX_PHOTO_BYTES = 1_500_000
# моделът брои снимката по размера ѝ: по-малката струва няколко пъти по-малко токени,
# а хладилникът и мебелите пак се виждат
MAX_PHOTO_SIDE = 1024
PHOTO_QUALITY = 80

INSTRUCTIONS = [
    "Анализираш снимки от обява за апартамент. Отговаряш само на български, на чист език, без чужди думи.",
    "Описвай само това, което се вижда. Ако нещо не се вижда на нито една снимка, пиши \"не се вижда\". Не гадай.",
    "Формат: 1) Обзавеждане и уреди (хладилник - малък/стандартен/голям или двукрилен, готварска печка, "
    "климатик, пералня, телевизор, мебели), 2) Състояние (ремонт, влага, износване), "
    "3) Светлина и гледка, 4) Отговор на въпроса на потребителя, 5) Какво да се провери на огледа.",
    "Размерът на хладилник преценявай спрямо шкафовете и вратите около него и го отбелязвай като приблизителна оценка.",
]

# Моделите с мислене харчат част от лимита за мислене. При 1500 отговорът оставаше празен.
MAX_ANSWER_TOKENS = 8192

main_model, fallback_models = main_and_fallback_models(
    VISION_MODEL, VISION_FALLBACK_MODEL, max_tokens=MAX_ANSWER_TOKENS, temperature=0.2
)
vision_agent = Agent(model=main_model, fallback_models=fallback_models, instructions=INSTRUCTIONS)


def _download_photo(url: str) -> bytes | None:
    """Снимката или None, ако не се изтегли или е прекалено голяма."""
    try:
        photo = source.download(url)
    except requests.RequestException:
        return None

    if len(photo) > MAX_PHOTO_BYTES:
        return None
    return shrink_photo(photo)


def shrink_photo(photo: bytes) -> bytes:
    """Снимката, смалена до MAX_PHOTO_SIDE пиксела по дългата страна, като JPEG.
    При грешка (непознат формат) връща оригинала."""
    try:
        with PillowImage.open(io.BytesIO(photo)) as image:
            is_small_jpeg = max(image.size) <= MAX_PHOTO_SIDE and image.format == "JPEG"
            if is_small_jpeg:
                return photo

            image = image.convert("RGB")
            image.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
            smaller = io.BytesIO()
            image.save(smaller, "JPEG", quality=PHOTO_QUALITY)
            return smaller.getvalue()
    except (OSError, ValueError):
        return photo


def analyze_photos(listing_url: str, question: str = "") -> str:
    """Анализира снимките на конкретна обява: уреди (напр. размер на хладилника), обзавеждане, състояние.

    Args:
        listing_url: Пълният линк към обявата от резултатите на search_apartments.
        question: Какво точно да се провери, например "голям ли е хладилникът?".
    """
    if not source.listing_id(listing_url):
        return "Нужен е пълен линк към обява от imot.bg (https://www.imot.bg/obiava-...)."

    try:
        listing_html = source.fetch_html(listing_url)
    except requests.RequestException as error:
        return f"Обявата не може да се зареди: {error}"

    photo_urls = source.listing_photos(listing_html)[:MAX_PHOTOS]
    if not photo_urls:
        return "Обявата няма снимки."

    # снимките са в CDN-а (imotstatic), не в imot.bg, затова се теглят всички наведнъж
    with ThreadPoolExecutor(len(photo_urls)) as pool:
        photos = list(pool.map(_download_photo, photo_urls))

    downloaded_photos = [photo for photo in photos if photo]
    images = [Image(content=photo, mime_type="image/jpeg") for photo in downloaded_photos]
    if not images:
        return "Снимките не можаха да се изтеглят."

    user_question = question or "Опиши обзавеждането и състоянието."
    run = vision_agent.run(f"Въпрос на потребителя: {user_question}", images=images)
    answer = (run.content or "").strip()

    if run.status == RunStatus.error or not answer:
        error_text = answer[:120] or "без отговор"
        return (
            f"Моделът за снимки е недостъпен ({error_text}). "
            "Не опитвай пак в този отговор: кажи на потребителя, че снимките не са проверени."
        )
    return f"Анализ по {len(images)} снимки на {listing_url}\n\n{answer}"
