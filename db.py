"""
SQLite vrstva: inzeráty, história cien a záznamy o behoch zdrojov.

Tabuľky:
    listings      - jeden riadok = jeden inzerát NA JEDNOM PORTÁLI (aktuálny stav).
                    Ten istý byt na oboch portáloch = 2 riadky s rovnakým `portal_id`;
                    zlúčia sa až pri vykresľovaní (render.py), aby logika "zmizol z portálu
                    X" ostala per-zdroj.
    price_history - append-only log cien (nikdy sa nemaže okrem vymazania celého inzerátu).
    source_runs   - výsledok posledných behov každého zdroja (ok / partial / blocked / ...),
                    aby bolo vidno, že zdroj zlyhal, a nie že "nič nové".
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                TEXT PRIMARY KEY,      -- '<source>:<portal_id>'
    source            TEXT NOT NULL,
    portal_id         TEXT NOT NULL,         -- rovnaké ID na nehnutelnosti.sk aj reality.sk
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    description_raw   TEXT,
    rooms             INTEGER,
    area_m2           REAL,
    price             REAL,                  -- mesačný nájom (headline cena z inzerátu)
    energy_included   INTEGER,               -- 1 = energie v cene, 0 = nie, NULL = neznáme
    energy_extra      REAL,                  -- mesačná suma energií navyše, ak sa dala vyčítať
    street            TEXT,
    location          TEXT,
    condition         TEXT,                  -- new / renovated / partial / unknown / old
    condition_source  TEXT,                  -- structured / text / NULL
    parking           TEXT,                  -- included / optional / mentioned / NULL
    furnished         INTEGER,
    is_panel          INTEGER DEFAULT 0,
    floor             TEXT,
    main_photo_url    TEXT,
    availability      TEXT,                  -- 'reserved' (v titulku "REZERVOVANÉ") alebo NULL
    structured_condition TEXT,               -- surový štítok stavu z portálu (z detailu / JSON-LD), pre cache
    structured_energy INTEGER,               -- 1 = portál uvádza "s energiami", NULL = neznáme
    detail_checked_at TEXT,                  -- kedy sa naposledy stiahol detail inzerátu (nehnutelnosti.sk)
    status            TEXT DEFAULT 'active', -- active / removed
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    removed_at        TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    price       REAL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES listings(id)
);

CREATE TABLE IF NOT EXISTS source_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL,
    run_at    TEXT NOT NULL,
    status    TEXT NOT NULL,                 -- ok / blocked / network / structure
    found     INTEGER,
    message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_portal ON listings(portal_id);
"""

_COLUMNS = ["source", "portal_id", "url", "title", "description_raw", "rooms", "area_m2", "price",
            "energy_included", "energy_extra", "street", "location", "condition", "condition_source",
            "parking", "furnished", "is_panel", "floor", "main_photo_url", "availability",
            "structured_condition", "structured_energy", "detail_checked_at"]

# Stĺpce pridané po prvom nasadení - ALTER TABLE pre už existujúce DB súbory (CREATE TABLE IF NOT EXISTS
# na existujúcu tabuľku nové stĺpce nedopíše). Bezpečné spúšťať opakovane.
_MIGRATIONS = [
    ("availability", "TEXT"),
    ("structured_condition", "TEXT"),
    ("structured_energy", "INTEGER"),
    ("detail_checked_at", "TEXT"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(listings)")}
        for column, ctype in _MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {ctype}")


def make_id(source: str, portal_id: str) -> str:
    return f"{source}:{portal_id}"


def get_listing(conn, listing_id: str):
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return dict(row) if row else None


def upsert_listing(conn, listing: dict) -> str:
    """
    Vloží alebo aktualizuje inzerát. Vracia 'new' | 'price_changed' | 'unchanged' | 'reappeared'.
    Zmena ceny pridá NOVÝ riadok do price_history (staré zostávajú). Ak inzerát predtým
    bol 'removed' a znova sa objavil, vráti 'reappeared' a vráti sa medzi aktívne.
    """
    listing_id = make_id(listing["source"], listing["portal_id"])
    existing = get_listing(conn, listing_id)
    ts = now_iso()
    values = [listing.get(c) for c in _COLUMNS]

    if existing is None:
        conn.execute(
            f"INSERT INTO listings (id, {', '.join(_COLUMNS)}, status, first_seen_at, last_seen_at) "
            f"VALUES (?, {', '.join('?' for _ in _COLUMNS)}, 'active', ?, ?)",
            [listing_id, *values, ts, ts],
        )
        conn.execute("INSERT INTO price_history (listing_id, price, recorded_at) VALUES (?, ?, ?)",
                     (listing_id, listing.get("price"), ts))
        return "new"

    was_removed = existing["status"] != "active"
    new_price = listing.get("price")
    price_changed = new_price is not None and existing["price"] != new_price

    conn.execute(
        f"UPDATE listings SET {', '.join(c + ' = ?' for c in _COLUMNS)}, "
        "status = 'active', last_seen_at = ?, removed_at = NULL WHERE id = ?",
        [*values, ts, listing_id],
    )
    if price_changed:
        conn.execute("INSERT INTO price_history (listing_id, price, recorded_at) VALUES (?, ?, ?)",
                     (listing_id, new_price, ts))
        return "price_changed"
    return "reappeared" if was_removed else "unchanged"


def detail_is_stale(existing: dict | None, max_age_days: int) -> bool:
    """True, ak sa detail inzerátu ešte nikdy nesťahoval alebo je starší než max_age_days."""
    if existing is None or not existing.get("detail_checked_at"):
        return True
    checked = datetime.fromisoformat(existing["detail_checked_at"])
    return (datetime.now(timezone.utc) - checked).days >= max_age_days


def delete_listing(conn, source: str, portal_id: str) -> bool:
    """Natvrdo zmaže inzerát + históriu (keď prestal sedieť na kritériá - NIE keď zmizol z portálu)."""
    listing_id = make_id(source, portal_id)
    # Najprv "dieťa" (price_history), potom "rodič" (listings) - foreign_keys je zapnuté.
    conn.execute("DELETE FROM price_history WHERE listing_id = ?", (listing_id,))
    return conn.execute("DELETE FROM listings WHERE id = ?", (listing_id,)).rowcount > 0


def mark_missing_as_removed(conn, source: str, seen_portal_ids: set[str]) -> list[str]:
    """
    Aktívne inzeráty zdroja, ktoré v tomto (ÚPLNOM a úspešnom) behu neboli vo výpise,
    sa označia 'removed'. Volá sa LEN po úspešnom kompletnom behu zdroja - pri chybe
    (403, timeout, neúplné stránkovanie) sa NEvolá, aby výpadok siete neoznačil
    všetky inzeráty za stiahnuté.
    """
    rows = conn.execute("SELECT id, portal_id FROM listings WHERE source = ? AND status = 'active'",
                        (source,)).fetchall()
    removed = []
    ts = now_iso()
    for row in rows:
        if row["portal_id"] not in seen_portal_ids:
            conn.execute("UPDATE listings SET status = 'removed', removed_at = ? WHERE id = ?", (ts, row["id"]))
            removed.append(row["id"])
    return removed


def get_price_history(conn, listing_id: str) -> list[dict]:
    rows = conn.execute("SELECT price, recorded_at FROM price_history WHERE listing_id = ? "
                        "ORDER BY recorded_at, id", (listing_id,)).fetchall()
    return [dict(r) for r in rows]


def get_all_listings(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM listings ORDER BY price ASC").fetchall()]


def record_source_run(conn, source: str, status: str, found: int | None, message: str | None) -> None:
    conn.execute("INSERT INTO source_runs (source, run_at, status, found, message) VALUES (?, ?, ?, ?, ?)",
                 (source, now_iso(), status, found, message))


def last_source_runs(conn) -> dict[str, dict]:
    """Posledný záznam behu pre každý zdroj."""
    rows = conn.execute(
        "SELECT r.* FROM source_runs r JOIN (SELECT source, MAX(id) AS mid FROM source_runs GROUP BY source) m "
        "ON r.id = m.mid").fetchall()
    return {r["source"]: dict(r) for r in rows}
