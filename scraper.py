"""
Hlavný skript: stiahne inzeráty zo zdrojov, vyfiltruje podľa config.py, zapíše do SQLite.

Rozlíšenie (rovnaká filozofia ako arteon-monitor):
  - inzerát, ktorý NESEDÍ na kritériá (cena mimo rozsahu, iný počet izieb, dopyt) -> vymaže sa z DB úplne;
  - inzerát, ktorý sedí, ale zmizol z portálu -> 'removed' (zostane viditeľný s poslednou cenou);
  - zdroj, ktorý zlyhal alebo dal neúplné dáta -> NIČ sa neoznačí za stiahnuté, chyba sa zapíše
    do source_runs a zobrazí v HTML.

Exit kód: 0 ak aspoň jeden zdroj prebehol OK, 1 ak zlyhali VŠETKY (workflow sa označí červeno).
"""

import sys

import config
import db
import nehnutelnosti_scraper
import reality_scraper
import textutils
from http_util import SourceError

SOURCES = [nehnutelnosti_scraper, reality_scraper]


def enrich(c: dict) -> dict:
    """Doplní odvodené polia (stav, energie, parkovanie...) z textu a štruktúrovaných údajov."""
    text = f"{c['title']}. {c['description_raw'] or ''}"
    condition, cond_source = textutils.detect_condition(text, c.get("structured_condition"))
    included, extra = textutils.detect_energy(text, c.get("structured_energy_included"))
    c = dict(c)
    c.update({
        "condition": condition,
        "condition_source": cond_source,
        "energy_included": None if included is None else int(included),
        "energy_extra": extra,
        "parking": textutils.detect_parking(text, c.get("structured_parking")),
        "furnished": (lambda v: None if v is None else int(v))(textutils.detect_furnished(text)),
        "is_panel": int(textutils.detect_panel(text)),
    })
    return c


def rejection_reason(c: dict) -> str | None:
    """Prečo inzerát nesedí na tvrdé kritériá (None = sedí)."""
    if textutils.is_demand_ad(c["title"]):
        return "dopyt (niekto byt hľadá)"
    if c["price"] is None:
        return "bez ceny"
    if c["rooms"] not in config.ROOMS:
        return f"počet izieb {c['rooms']}"
    if not (config.PRICE_MIN <= c["price"] <= config.PRICE_MAX):
        return f"cena {c['price']:.0f} € mimo {config.PRICE_MIN}-{config.PRICE_MAX}"
    return None


def process_source(module, conn) -> tuple[str, dict]:
    """Spracuje jeden zdroj. Vracia (status, štatistiky)."""
    name = module.SOURCE_NAME
    print(f"\n=== Zdroj: {module.LABEL} ===")
    try:
        candidates = module.fetch_all(config.ROOMS)
    except SourceError as e:
        print(f"[{name}] ZLYHALO ({e.kind}): {e.message}")
        db.record_source_run(conn, name, e.kind, None, e.message[:500])
        return e.kind, {}

    stats = {"new": 0, "price_changed": 0, "unchanged": 0, "reappeared": 0, "rejected": 0, "removed": 0}
    seen = set()
    for raw in candidates:
        seen.add(raw["portal_id"])
        reason = rejection_reason(raw)
        if reason:
            db.delete_listing(conn, name, raw["portal_id"])
            stats["rejected"] += 1
            print(f"[{name}] VYRADENÉ ({reason}): {raw['title'][:70]}")
            continue
        listing = enrich(raw)
        result = db.upsert_listing(conn, listing)
        stats[result] += 1
        if result != "unchanged":
            print(f"[{name}] {result.upper()}: {listing['title'][:60]} | {listing['rooms']}i {listing['area_m2']} m² "
                  f"| {listing['price']:.0f} € | stav={listing['condition']} energie={listing['energy_included']}/"
                  f"{listing['energy_extra']} parking={listing['parking']}")

    removed = db.mark_missing_as_removed(conn, name, seen)
    stats["removed"] = len(removed)
    db.record_source_run(conn, name, "ok", len(candidates), None)
    print(f"[{name}] Súhrn: {stats}")
    return "ok", stats


def main() -> int:
    db.init_db(config.DB_PATH)
    statuses = []
    with db.connect(config.DB_PATH) as conn:
        for module in SOURCES:
            status, _ = process_source(module, conn)
            statuses.append(status)
            conn.commit()  # výsledok zdroja sa uloží hneď, aj keď ďalší zdroj spadne
    failed = [s for s in statuses if s != "ok"]
    if failed and len(failed) == len(statuses):
        print("\nVŠETKY zdroje zlyhali - ukončujem s chybou (viď logy vyššie).")
        return 1
    if failed:
        print(f"\nPozor: {len(failed)} z {len(statuses)} zdrojov zlyhalo, ostatné prebehli OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
