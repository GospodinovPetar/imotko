"""База данни (SQLite): история на цените, запазени търсения и кеш за местата."""

import functools
import json
import sqlite3
import time
from contextlib import closing
from itertools import batched

from imotko.config import DATA_DIR

DB_PATH = DATA_DIR / "imot_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT PRIMARY KEY, url TEXT, title TEXT, location TEXT, deal TEXT,
    first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS prices (
    id TEXT, ts REAL, price REAL
);
CREATE INDEX IF NOT EXISTS prices_id ON prices (id, ts);
CREATE TABLE IF NOT EXISTS saved_searches (
    name TEXT PRIMARY KEY, params TEXT, seen TEXT DEFAULT '[]', created REAL
);
CREATE TABLE IF NOT EXISTS seen (
    name TEXT, id TEXT, PRIMARY KEY (name, id)
);
CREATE TABLE IF NOT EXISTS places (
    key TEXT PRIMARY KEY, data TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
    question TEXT, answer TEXT, correction TEXT, data TEXT
);
"""


@functools.cache
def _create_schema() -> None:
    """Създава таблиците веднъж на процес.

    По-старите версии пазеха видените обяви като JSON в saved_searches.seen.
    Тук ги преместваме в таблицата seen.
    """
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
        connection.executescript(SCHEMA)

        old_rows = connection.execute("SELECT name, seen FROM saved_searches WHERE seen != '[]'").fetchall()
        for name, seen_json in old_rows:
            rows = [(name, listing_id) for listing_id in json.loads(seen_json)]
            connection.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", rows)
            connection.execute("UPDATE saved_searches SET seen = '[]' WHERE name = ?", (name,))


def _connect() -> sqlite3.Connection:
    _create_schema()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection

    
# История на цените 

def record_prices(listings: list[dict], deal: str) -> None:
    """Записва, че сме видели обявите, и цената им, когато е нова или различна от последната.

    На обявите, чиято цена се е променила, добавя first_price и price_change_pct.
    """
    if not listings:
        return

    now = time.time()
    listing_ids = [listing["id"] for listing in listings]

    with closing(_connect()) as connection, connection:
        price_history = _load_price_history(connection, listing_ids)
        _save_listings_seen(connection, listings, deal, now)
        _save_changed_prices(connection, listings, price_history, now)

    for listing in listings:
        previous_prices = price_history.get(listing["id"], [])
        if previous_prices:
            _add_price_change(listing, first_price=previous_prices[0])


def _save_listings_seen(connection: sqlite3.Connection, listings: list[dict], deal: str, now: float) -> None:
    seen_rows = []
    for listing in listings:
        seen_rows.append((listing["id"], listing["url"], listing["title"], listing["location"], deal, now, now))

    connection.executemany(
        "INSERT INTO listings (id, url, title, location, deal, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen",
        seen_rows,
    )


def _save_changed_prices(connection: sqlite3.Connection, listings: list[dict],
                         price_history: dict[str, list[float]], now: float) -> None:
    new_price_rows = []
    for listing in listings:
        previous_prices = price_history.get(listing["id"], [])
        last_price = previous_prices[-1] if previous_prices else None
        if listing["price_eur"] != last_price:
            new_price_rows.append((listing["id"], now, listing["price_eur"]))

    connection.executemany("INSERT INTO prices VALUES (?, ?, ?)", new_price_rows)


def _add_price_change(listing: dict, first_price: float) -> None:
    if not first_price or first_price == listing["price_eur"]:
        return

    change_percent = (listing["price_eur"] - first_price) / first_price * 100
    listing["first_price"] = first_price
    listing["price_change_pct"] = round(change_percent, 1)


def _load_price_history(connection: sqlite3.Connection, listing_ids: list[str]) -> dict[str, list[float]]:
    """id на обява -> всички записани цени, от най-старата към най-новата."""
    history: dict[str, list[float]] = {}

    # SQLite има лимит на параметрите в една заявка, затова питаме на части
    for ids in batched(listing_ids, 500):
        placeholders = ", ".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT id, price FROM prices WHERE id IN ({placeholders}) ORDER BY ts", ids
        )
        for row in rows:
            history.setdefault(row["id"], []).append(row["price"])

    return history


# Запазени търсения

def save_search(name: str, params: dict) -> None:
    """Запазва търсене (или заменя търсене със същото име). Видените му обяви се нулират."""
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO saved_searches (name, params, created) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET params = excluded.params",
            (name, json.dumps(params, ensure_ascii=False), time.time()),
        )
        connection.execute("DELETE FROM seen WHERE name = ?", (name,))


def list_searches() -> list[dict]:
    """[{name, params, seen}], където seen е множеството от вече показаните обяви."""
    with closing(_connect()) as connection:
        seen_by_search: dict[str, set[str]] = {}
        for row in connection.execute("SELECT name, id FROM seen"):
            seen_by_search.setdefault(row["name"], set()).add(row["id"])

        searches = []
        for row in connection.execute("SELECT name, params FROM saved_searches ORDER BY created"):
            searches.append({
                "name": row["name"],
                "params": json.loads(row["params"]),
                "seen": seen_by_search.get(row["name"], set()),
            })
        return searches


def delete_search(name: str) -> bool:
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM seen WHERE name = ?", (name,))
        deleted = connection.execute("DELETE FROM saved_searches WHERE name = ?", (name,))
        return deleted.rowcount > 0


def mark_seen(name: str, listing_ids: list[str]) -> None:
    with closing(_connect()) as connection, connection:
        rows = [(name, listing_id) for listing_id in listing_ids]
        connection.executemany("INSERT OR IGNORE INTO seen VALUES (?, ?)", rows)


# Кеш за местата (геокодиране, маршрути, магазини)

def get_place(key: str) -> tuple[object, float] | None:
    """(данните, преди колко секунди са записани) или None, ако ги няма."""
    with closing(_connect()) as connection:
        row = connection.execute("SELECT data, ts FROM places WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None

    data = json.loads(row["data"])
    age_seconds = time.time() - row["ts"]
    return data, age_seconds


def save_place(key: str, data) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO places (key, data, ts) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data = excluded.data, ts = excluded.ts",
            (key, json.dumps(data, ensure_ascii=False), time.time()),
        )


# Обратна връзка и следене на отговорите

def add_feedback(kind: str, question: str = "", answer: str = "", correction: str = "", data: dict | None = None) -> None:
    """kind: "user_report" (потребителят казва, че нещо е грешно)
    или "unverified_numbers" (в отговора има числа, които не идват от инструмент).
    """
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO feedback (ts, kind, question, answer, correction, data) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), kind, question, answer, correction, json.dumps(data or {}, ensure_ascii=False)),
        )


def list_feedback(kind: str | None = None, limit: int = 50) -> list[dict]:
    """Последните записи, най-новите първо."""
    query = "SELECT * FROM feedback"
    params: tuple = ()
    if kind:
        query += " WHERE kind = ?"
        params = (kind,)
    query += " ORDER BY ts DESC LIMIT ?"

    with closing(_connect()) as connection:
        rows = connection.execute(query, (*params, limit)).fetchall()

    records = []
    for row in rows:
        record = dict(row)
        record["data"] = json.loads(row["data"] or "{}")
        records.append(record)
    return records
