"""Общият вид на сайт с обяви. Всеки нов сайт (homes.bg, alo.bg...) наследява ListingSource."""

from abc import ABC, abstractmethod


class ListingSource(ABC):
    """Сайт, от който се четат обяви. Останалите модули знаят само за този клас.

    Обявите са речници със стандартни полета (id, url, price_eur, area_m2, floor...).
    """

    @property
    @abstractmethod
    def construction_types(self) -> tuple[str, ...]:
        """Допустимите видове строителство във филтъра."""

    @property
    @abstractmethod
    def named_floors(self) -> dict[str, int]:
        """Етажи с име, например {"партер": 0}."""

    @property
    @abstractmethod
    def max_parallel_requests(self) -> int:
        """Колко заявки към сайта може да вървят едновременно."""

    @abstractmethod
    def is_rent(self, deal: str) -> bool:
        """Наем ли е сделката."""

    @abstractmethod
    def fetch_listings(self, deal: str, city: str, rooms: str, pages: int) -> tuple[list[dict], list[str]]:
        """Обявите от първите `pages` страници и списък с грешките при свалянето.

        Хвърля ValueError при непозната сделка, брой стаи или град.
        """

    @abstractmethod
    def add_details(self, listings) -> None:
        """Допълва обявите с данните от страницата на всяка."""

    @abstractmethod
    def listing_id(self, url: str) -> str | None:
        """Номерът на обявата или None, ако адресът не е на този сайт."""

    @abstractmethod
    def fetch_html(self, url: str) -> str:
        """HTML на страница."""

    @abstractmethod
    def listing_photos(self, html: str) -> list[str]:
        """Адресите на снимките от страницата на обява."""

    @abstractmethod
    def download(self, url: str) -> bytes:
        """Сваля файл (снимка)."""
