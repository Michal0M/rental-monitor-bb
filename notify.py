"""
Oznámenia na Discord cez webhook.

Webhook URL je TAJOMSTVO: berie sa iba z premennej prostredia (GitHub Secret), nikdy nie z kódu.
Chyba pri odosielaní NIKDY neukončí scraper - len sa vypíše do logu.
"""

import os
import time

import requests

import config

SECRET_ENV = "DISCORD_WEBHOOK_BYTY"
COLORS = {"new": 0x2ECC71, "price_drop": 0xF1C40F, "price_up": 0xE67E22, "reappeared": 0x3498DB}
HEADINGS = {"new": "🆕 Nový inzerát", "price_drop": "📉 Zľava", "price_up": "📈 Zvýšená cena",
            "reappeared": "🔁 Opäť v ponuke"}
CONDITION_TEXT = {"new": "novostavba", "renovated": "rekonštrukcia", "partial": "čiastočná rekonštrukcia",
                  "old": "pôvodný stav", "unknown": "stav neznámy"}
PARKING_TEXT = {"included": "parkovanie v cene", "optional": "parkovanie za príplatok", "mentioned": "parkovanie spomenuté"}


def kind_for(result: str, old_price, new_price) -> str | None:
    """Zmapuje výsledok upsertu na druh oznámenia (None = neoznamovať)."""
    if result == "new":
        return "new"
    if result == "reappeared":
        return "reappeared"
    if result == "price_changed" and old_price is not None and new_price is not None:
        return "price_drop" if new_price < old_price else "price_up"
    return None


def should_notify(listing: dict) -> bool:
    if listing.get("availability") == "reserved":
        return False
    return listing.get("condition") in config.NOTIFY_CONDITIONS


def fmt_eur(v) -> str:
    return f"{v:,.0f} €".replace(",", " ")


def build_embed(item: dict) -> dict:
    l, kind = item["listing"], item["kind"]
    price = fmt_eur(l["price"])
    if kind in ("price_drop", "price_up") and item.get("old_price"):
        price = f"~~{fmt_eur(item['old_price'])}~~ → **{fmt_eur(l['price'])}**"
    if l.get("energy_extra"):
        price += f" + {fmt_eur(l['energy_extra'])} energie (celkom ≈ {fmt_eur(l['price'] + l['energy_extra'])})"
    elif l.get("energy_included") == 1:
        price += " (s energiami)"
    facts = [f"{l['rooms']}-izbový"]
    if l.get("area_m2"):
        facts.append(f"{l['area_m2']:.0f} m²")
    facts.append(CONDITION_TEXT.get(l.get("condition"), "stav neznámy"))
    if PARKING_TEXT.get(l.get("parking")):
        park = PARKING_TEXT[l["parking"]]
        if l.get("parking_extra"):
            park += f" ({l['parking_extra']:.0f} €)"
        facts.append(park)
    embed = {
        "title": (l.get("title") or "Inzerát")[:250],
        "url": l["url"],
        "description": f"{HEADINGS[kind]}\n**{price}**\n" + " · ".join(facts),
        "color": COLORS[kind],
    }
    if l.get("main_photo_url"):
        embed["thumbnail"] = {"url": l["main_photo_url"]}
    return embed


def _post(url: str, payload: dict) -> bool:
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as e:
            print(f"[notify] chyba siete: {type(e).__name__}")  # bez URL, aby sa tajomstvo nedostalo do logu
            return False
        if r.status_code == 429:
            try:
                wait = float(r.json().get("retry_after", 2))
            except Exception:
                wait = 2
            time.sleep(min(wait, 30) + 0.5)
            continue
        if r.status_code >= 300:
            print(f"[notify] Discord vrátil HTTP {r.status_code}")
            return False
        return True
    return False


def send_notifications(items: list[dict], webhook: str | None = None) -> int:
    """Pošle oznámenia (po 5 embedov v správe, max NOTIFY_MAX_PER_RUN). Vracia počet odoslaných."""
    webhook = webhook or os.environ.get(SECRET_ENV)
    if not items:
        return 0
    if not webhook:
        print(f"[notify] {len(items)} oznámení preskočených - chýba premenná {SECRET_ENV}")
        return 0
    shown = items[:config.NOTIFY_MAX_PER_RUN]
    sent = 0
    for i in range(0, len(shown), 5):
        chunk = shown[i:i + 5]
        payload = {"username": "Byty BB", "embeds": [build_embed(x) for x in chunk]}
        if i == 0 and len(items) > len(shown):
            payload["content"] = f"Dnes {len(items)} zmien, zobrazených prvých {len(shown)} - zvyšok na stránke."
        if _post(webhook, payload):
            sent += len(chunk)
        time.sleep(1)
    print(f"[notify] odoslaných {sent}/{len(items)} oznámení")
    return sent


def send_test(webhook: str | None = None) -> bool:
    webhook = webhook or os.environ.get(SECRET_ENV)
    if not webhook:
        print(f"Chýba premenná {SECRET_ENV}")
        return False
    return _post(webhook, {"username": "Byty BB", "content": "✅ Testovacia správa: Discord notifikácie fungujú."})


if __name__ == "__main__":
    import sys
    sys.exit(0 if send_test() else 1)
