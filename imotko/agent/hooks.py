"""Куките на агента: преди отговора (снимки) и около всеки инструмент (грешки като текст)."""

from pathlib import Path

import requests

from imotko import vision


def return_errors_as_text(function_call, arguments):
    """Невалиден филтър или недостъпен imot.bg се връщат на модела като текст, а не като срив.

    agno подава аргументите по име, затова имената function_call и arguments са задължителни.
    """
    try:
        return function_call(**arguments)
    except ValueError as error:
        return str(error)
    except requests.RequestException as error:
        return f"imot.bg не отговаря в момента ({error}). Опитай пак след малко."


def shrink_uploaded_images(run_input) -> None:
    """pre_hook: снимките, качени в чата, се смаляват до 1024 px, преди да стигнат до модела.
    Така и базата с разговорите не пази снимки от телефон в пълен размер.
    """
    try:
        images = getattr(run_input, "images", None) or []
        for image in images:
            _shrink_image(image)
    except Exception:
        pass  # по-добре скъпа снимка, отколкото спрян отговор


def _shrink_image(image) -> None:
    content = image.content
    if content is None and image.filepath:
        content = Path(image.filepath).read_bytes()
    if not content:
        return  # снимка по линк: моделът я сваля сам

    smaller = vision.shrink_photo(content)
    if smaller is content:
        return

    image.content = smaller
    image.filepath = None
    image.format = "jpeg"
    image.mime_type = "image/jpeg"
