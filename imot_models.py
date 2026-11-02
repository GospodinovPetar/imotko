"""Безплатните модели през OpenRouter, всеки с резервен модел."""

from agno.exceptions import ModelProviderError
from agno.models.openrouter import OpenRouter

# agno минава на резервния модел само при 429, 5xx и мрежови грешки.
# OpenRouter обаче връща 400, 402 или 404, когато безплатен модел е спрян
# или не приема снимки. За нас и това означава "моделът е недостъпен".
UNAVAILABLE_STATUS_CODES = {400, 402, 404}
SERVICE_UNAVAILABLE = 503


def _mark_as_unavailable(error: ModelProviderError) -> ModelProviderError:
    """Представя грешката като 503, за да опита agno резервния модел."""
    if error.status_code in UNAVAILABLE_STATUS_CODES:
        error.status_code = SERVICE_UNAVAILABLE
    return error


class FreeOpenRouter(OpenRouter):
    """OpenRouter, при който спрян безплатен модел води до резервния, а не до грешка."""

    def invoke(self, *args, **kwargs):
        try:
            return super().invoke(*args, **kwargs)
        except ModelProviderError as error:
            raise _mark_as_unavailable(error)

    async def ainvoke(self, *args, **kwargs):
        try:
            return await super().ainvoke(*args, **kwargs)
        except ModelProviderError as error:
            raise _mark_as_unavailable(error)

    def invoke_stream(self, *args, **kwargs):
        try:
            yield from super().invoke_stream(*args, **kwargs)
        except ModelProviderError as error:
            raise _mark_as_unavailable(error)

    async def ainvoke_stream(self, *args, **kwargs):
        try:
            async for chunk in super().ainvoke_stream(*args, **kwargs):
                yield chunk
        except ModelProviderError as error:
            raise _mark_as_unavailable(error)


def main_and_fallback_models(main_model: str, fallback_model: str, **settings):
    """(основен модел, [резервен модел]) с еднакви настройки.

    Употреба: Agent(model=main, fallback_models=fallbacks, ...)
    """
    main = FreeOpenRouter(main_model, **settings)
    fallbacks = [FreeOpenRouter(fallback_model, **settings)]
    return main, fallbacks
