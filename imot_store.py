"""База данни (SQLite): история на цените и запазени търсения."""

import functools
import json
import os
import sqlite3
import time
from contextlib import closing
from itertools import batched
from pathlib import Path

# IMOT_DATA_DIR позволява базите да са извън кода (напр. volume в Docker)
DATA_DIR = Path(os.getenv("IMOT_DATA_DIR", Path(__file__).parent))
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


# 
# История на цените
# 

def record_prices(listings: list[dict], deal: str) -> None:
    """Записва, че сме видели обявите, и цената им, когато е нова или различна от последната.

    На обявите, чиято цена се е променила, добавя first_price и price_change_pct.
    """
    if not listings:
        return

    now = time.time()

    with closing(_connect()) as connection, connection:
        price_history = _load_price_history(connection, [listing["id"] for listing in listings])

        seen_rows = []
        for listing in listings:
            seen_rows.append((listing["id"], listing["url"], listing["title"], listing["location"], deal, now, now))
        connection.executemany(
            "INSERT INTO listings (id, url, title, location, deal, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen",
            seen_rows,
        )

        new_price_rows = []
        for listing in listings:
            previous_prices = price_history.get(listing["id"], [])
            last_price = previous_prices[-1] if previous_prices else None
            if listing["price_eur"] != last_price:
                new_price_rows.append((listing["id"], now, listing["price_eur"]))
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?)", new_price_rows)

    for listing in listings:
        previous_prices = price_history.get(listing["id"], [])
        if not previous_prices:
            continue

        first_price = previous_prices[0]
        if first_price and first_price != listing["price_eur"]:
            listing["first_price"] = first_price
            listing["price_change_pct"] = round((listing["price_eur"] - first_price) / first_price * 100, 1)


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


# 
# Запазени търсения
# 

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
