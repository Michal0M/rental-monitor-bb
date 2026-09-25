"""
Hlavný skript: stiahne inzeráty zo zdrojov, vyfiltruje podľa config.py, zapíše do SQLite.

Rozlíšenie (rovnaká filozofia ako arteon-monitor):
  - inzerát, ktorý NESEDÍ na kritériá (cena mimo rozsahu, iný počet izieb, dopyt) -> vymaže sa z DB úplne;
  - inzerát, ktorý sedí, ale zmizol z portálu -> 'removed' (zostane viditeľný s poslednou cenou);
  - zdroj, ktorý zlyhal alebo dal neúplné dáta -> NIČ sa neoznačí za stiahnuté, chyba sa zapíše
    do source_runs a zobrazí v HTML.

Exit kód: 1 ak zlyhal niektorý POVINNÝ zdroj (REQUIRED = True) alebo zlyhali všetky; nepovinný zdroj
(reality.sk) môže zlyhať bez červeného workflowu.
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
    structured_energy = c.get("structured_energy_included")
    if structured_energy is None and c.get("structured_energy"):
        structured_energy = True
    included, extra = textutils.detect_energy(text, structured_energy)
    c = dict(c)
    c.update({
        "availability": "reserved" if textutils.is_reserved(c["title"]) else None,
        "condition": condition,
        "condition_source": cond_source,
        "energy_included": None if included is None else int(included),
        "energy_extra": extra,
        "parking": textutils.detect_parking(text, c.get("structured_parking")),
        "parking_extra": textutils.detect_parking_extra(text),
        "furnished": (lambda v: None if v is None else int(v))(textutils.detect_furnished(text)),
        "is_panel": int(textutils.detect_panel(text)),
    })
    return c


def rejection_reason(c: dict) -> str | None:
    """Prečo inzerát nesedí na tvrdé kritériá (None = sedí)."""
    if textutils.is_demand_ad(c["title"]):
        return "dopyt (niekto byt hľadá)"
    if textutils.is_rented(c["title"]):
        return "už prenajatý"
    if textutils.is_half_room(c["title"]):
        return "1,5-izbový byt"
    if c["price"] is None:
        return "bez ceny"
    if c["rooms"] not in config.ROOMS:
        return f"počet izieb {c['rooms']}"
    if not (config.PRICE_MIN <= c["price"] <= config.PRICE_MAX):
        return f"cena {c['price']:.0f} € mimo {config.PRICE_MIN}-{config.PRICE_MAX}"
    min_area = config.MIN_AREA_M2.get(c["rooms"], 0)
    if c.get("area_m2") and c["area_m2"] < min_area:
        return f"plocha {c['area_m2']:.0f} m² < {min_area} m² pre {c['rooms']}-izbový"
    return None


def apply_detail(conn, module, raw: dict, budget: dict) -> None:
    """
    Doplní `raw` o údaje z detailu (štruktúrovaný stav, "s energiami"), ak to zdroj podporuje.
    Detail sa sťahuje len pre nové/zastarané inzeráty (cache v DB), max DETAIL_MAX_PER_RUN za beh.
    Pri blokovaní/chybe sa ďalšie detaily v tomto behu nesťahujú a inzerát ostáva s textovým odhadom.
    """
    if not hasattr(module, "fetch_detail"):
        return
    existing = db.get_listing(conn, db.make_id(module.SOURCE_NAME, raw["portal_id"]))
    if existing:  # prenes uloženú cache, aby ju výpis (ktorý stav nepozná) neprepísal
        raw.setdefault("structured_condition", None)
        raw["structured_condition"] = raw.get("structured_condition") or existing.get("structured_condition")
        raw["structured_energy"] = existing.get("structured_energy")
        raw["detail_checked_at"] = existing.get("detail_checked_at")
        raw["detail_version"] = existing.get("detail_version")
        # výpis dáva len skrátený popis - uložený celý popis z detailu nesmie byť prepísaný kratším
        if len(existing.get("description_raw") or "") > len(raw.get("description_raw") or ""):
            raw["description_raw"] = existing["description_raw"]
    if not db.detail_is_stale(existing, config.DETAIL_REFRESH_DAYS, config.DETAIL_VERSION):
        return
    if budget["stopped"] or budget["used"] >= config.DETAIL_MAX_PER_RUN:
        budget["skipped"] += 1
        return
    budget["used"] += 1
    try:
        detail = module.fetch_detail(raw["url"])
    except SourceError as e:
        print(f"[{module.SOURCE_NAME}] detail {raw['portal_id']} ZLYHAL ({e.kind}): {e.message}")
        if e.kind in ("blocked", "network"):
            budget["stopped"] = True
            print(f"[{module.SOURCE_NAME}] ďalšie detaily sa v tomto behu nesťahujú")
        budget["failed"] += 1
        return
    label = detail.get("condition_label")
    if label:
        raw["structured_condition"] = label
    elif detail.get("condition_candidates"):
        print(f"[{module.SOURCE_NAME}] neznámy štítok stavu {detail['condition_candidates']} "
              f"({raw['portal_id']}) - doplň do KNOWN_CONDITION_LABELS / textutils._STRUCTURED_CONDITION")
    if detail.get("energy_included"):
        raw["structured_energy"] = 1
    if len(detail.get("description") or "") > len(raw.get("description_raw") or ""):
        raw["description_raw"] = detail["description"]
    raw["detail_checked_at"] = db.now_iso()
    raw["detail_version"] = config.DETAIL_VERSION


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
    budget = {"used": 0, "skipped": 0, "failed": 0, "stopped": False}
    seen = set()
    for raw in candidates:
        seen.add(raw["portal_id"])
        reason = rejection_reason(raw)
        if reason:
            db.delete_listing(conn, name, raw["portal_id"])
            stats["rejected"] += 1
            print(f"[{name}] VYRADENÉ ({reason}): {raw['title'][:70]}")
            continue
        apply_detail(conn, module, raw, budget)
        listing = enrich(raw)
        result = db.upsert_listing(conn, listing)
        stats[result] += 1
        if result != "unchanged":
            print(f"[{name}] {result.upper()}: {listing['title'][:60]} | {listing['rooms']}i {listing['area_m2']} m² "
                  f"| {listing['price']:.0f} € | stav={listing['condition']} energie={listing['energy_included']}/"
                  f"{listing['energy_extra']} parking={listing['parking']}")

    if budget["used"] or budget["skipped"]:
        print(f"[{name}] Detaily: stiahnutých {budget['used'] - budget['failed']}, zlyhalo {budget['failed']}, "
              f"odložených na ďalší beh {budget['skipped']}")
    removed = db.mark_missing_as_removed(conn, name, seen)
    stats["removed"] = len(removed)
    db.record_source_run(conn, name, "ok", len(candidates), None)
    print(f"[{name}] Súhrn: {stats}")
    return "ok", stats


def main() -> int:
    db.init_db(config.DB_PATH)
    results = []
    with db.connect(config.DB_PATH) as conn:
        for module in SOURCES:
            status, _ = process_source(module, conn)
            results.append((module, status))
            conn.commit()  # výsledok zdroja sa uloží hneď, aj keď ďalší zdroj spadne
    failed = [(m, s) for m, s in results if s != "ok"]
    if failed and len(failed) == len(results):
        print("\nVŠETKY zdroje zlyhali - ukončujem s chybou (viď logy vyššie).")
        return 1
    required_failed = [m.LABEL for m, _ in failed if getattr(m, "REQUIRED", True)]
    if required_failed:
        print(f"\nPOVINNÝ zdroj zlyhal: {', '.join(required_failed)} - ukončujem s chybou.")
        return 1
    if failed:
        print(f"\nPozor: nepovinný zdroj zlyhal ({', '.join(m.LABEL for m, _ in failed)}), ostatné prebehli OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
