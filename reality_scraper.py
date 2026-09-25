"""
Zdroj: reality.sk (výpis prenájmov 1/2/3-izbových bytov v Banskej Bystrici).

Overené 25.9.2026 v prehliadači:
  - robots.txt zakazuje len /hladania/, /app-api/, /modal/, /moj-ucet/ a pod. Naše URL
    (/byty/N-izbovy-byt/banska-bystrica/prenajom/ a ?page=N) sú povolené.
  - Server-side renderované, 24 kariet na stranu, karta je `div.offer[data-offer-id]`
    (stabilné, nehashované triedy): `h2.offer-title`, `p.offer-params` so 3 <span>
    ("2 izbový byt", "| ulica", "| 58 m²" - ulica môže byť prázdna), `p.offer-price`
    ("800 €/mesiac"), `.offer-img-wrapper img[data-lazy-src]` (fotka).
  - V stránke je JSON-LD (ItemList) so ŠTRUKTÚROVANÝMI dátami ku všetkým kartám na strane:
    celý popis, cena, plocha a `amenityFeature` (Stav nehnuteľnosti = Novostavba /
    Kompletná rekonštrukcia / Čiastočná rekonštrukcia / Pôvodný stav; Cena vrátane
    energií = Áno/Nie; Podlažie; Vybavenie ...). Preto z neho berieme stav bytu, energie
    a plný text popisu.
  - POZOR: JSON-LD obsahuje SUROVÉ riadkové znaky v reťazcoch -> `json.loads` s predvoleným
    strict=True padá; treba `strict=False`.
  - Občas sa v zozname objaví "Dopyt na prenájom ..." (niekto byt HĽADÁ, bez ceny) -
    tieto sa preskakujú (textutils.is_demand_ad + chýbajúca cena).
  - ID inzerátu (data-offer-id, napr. JuvathsD90K) je zhodné s ID na nehnutelnosti.sk.
"""

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import config
import textutils
from http_util import SourceError, diagnose_empty, fetch

SOURCE_NAME = "reality_sk"
LABEL = "reality.sk"
# Nepovinný zdroj: z GitHub runnerov dáva reality.sk ConnectTimeout (overené 1. behom, 25.9.2026).
# Jeho zlyhanie neoznačí workflow červeno a v HTML sa zobrazí len nenápadná poznámka.
REQUIRED = False


def _amenities(entity: dict) -> dict[str, str]:
    return {a.get("name", ""): str(a.get("value", "")) for a in entity.get("amenityFeature", []) or []}


def parse_jsonld(soup: BeautifulSoup) -> dict[str, dict]:
    """{portal_id: {description, price, area, amenities}} z JSON-LD ItemList (ak sa nedá spracovať, {})."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if "ItemList" not in raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except json.JSONDecodeError as e:
            print(f"[{SOURCE_NAME}] JSON-LD sa nepodarilo spracovať: {e}")
            return {}
        result = {}
        for element in data.get("itemListElement", []):
            entity = element.get("mainEntity", {})
            url = entity.get("url", "")
            m = re.search(r"/([^/]+)/?$", url)
            if not m:
                continue
            offers = entity.get("offers") or [{}]
            result[m.group(1)] = {
                "description": entity.get("description", "") or "",
                "price": offers[0].get("price"),
                "area": (entity.get("floorSize") or {}).get("value"),
                "amenities": _amenities(entity),
            }
        return result
    return {}


def parse_page(html: str) -> tuple[list[dict], int | None]:
    """Vráti (kandidáti, deklarovaný počet ponúk z hlavičky stránky alebo None)."""
    soup = BeautifulSoup(html, "html.parser")
    ld = parse_jsonld(soup)

    declared = None
    m = re.search(r"(\d+)\s+ponúk", soup.get_text(" ", strip=True))
    if m:
        declared = int(m.group(1))

    candidates = []
    for card in soup.select(".offer[data-offer-id]"):
        pid = card["data-offer-id"]
        link = card.select_one("a[href]")
        h2 = card.select_one("h2.offer-title") or card.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""

        params = [s.get_text(" ", strip=True).lstrip("| ").strip()
                  for s in card.select("p.offer-params span")]
        rooms_text = params[0] if params else None
        area_text = next((p for p in params if re.search(r"m\s*[²2]", p)), None)
        street = next((p for p in params[1:] if p and p != area_text), None)

        price_el = card.select_one("p.offer-price")
        price_text = None
        if price_el:
            first_text = price_el.find(string=True, recursive=False)
            price_text = first_text.strip() if first_text else None
        card_price = textutils.parse_price(price_text)

        extra = ld.get(pid, {})
        amenities = extra.get("amenities", {})
        energy_flag = amenities.get("Cena vrátane energií")
        parking_parts = [amenities.get("Vybavenie", "")]
        if amenities.get("Počet vonkajších parkovacích miest") or amenities.get("Počet parkovacích miest v garáži"):
            parking_parts.append("parkovacie miesto")

        desc_el = card.select_one("p.offer-desc")
        description = extra.get("description") or (desc_el.get_text(" ", strip=True) if desc_el else "")

        img = card.select_one(".offer-img-wrapper img[data-lazy-src]")
        href = link["href"] if link else ""
        candidates.append({
            "portal_id": pid,
            "source": SOURCE_NAME,
            "url": urljoin(config.BASE_REALITY, href),
            "title": title,
            "description_raw": description,
            "rooms": textutils.parse_rooms(rooms_text),
            "area_m2": textutils.parse_area(area_text) or extra.get("area"),
            "price": extra.get("price") if extra.get("price") else card_price,
            "street": street,
            "location": street,
            "structured_condition": amenities.get("Stav nehnuteľnosti"),
            "structured_energy_included": {"Áno": True, "Nie": False}.get(energy_flag),
            "structured_parking": " ".join(p for p in parking_parts if p) or None,
            "floor": amenities.get("Podlažie"),
            "main_photo_url": img["data-lazy-src"] if img else None,
        })
    return candidates, declared


def fetch_all(rooms_list: list[int]) -> list[dict]:
    prefix = f"[{SOURCE_NAME}]"
    results: dict[str, dict] = {}
    for rooms in rooms_list:
        base = config.REALITY_URL.format(rooms=rooms)
        seen_for_query: set[str] = set()
        declared_total = None
        for page_no in range(1, config.MAX_PAGES_PER_QUERY + 1):
            url = base if page_no == 1 else f"{base}?page={page_no}"
            page = fetch(url, prefix, attempts=1)
            candidates, declared = parse_page(page.text)
            if page_no == 1:
                declared_total = declared
                print(f"{prefix} {rooms}-izbové: deklarovaných {declared} ponúk, "
                      f"na 1. strane {len(candidates)} kariet"
                      + (f", prvá: {candidates[0]['title']!r}" if candidates else ""))
                if not candidates:
                    raise diagnose_empty(page, prefix)
            new = [c for c in candidates if c["portal_id"] not in seen_for_query]
            if not new:
                break
            for c in new:
                seen_for_query.add(c["portal_id"])
                results[c["portal_id"]] = c
            if declared_total and len(seen_for_query) >= declared_total:
                break
        if declared_total and len(seen_for_query) < declared_total * config.COMPLETENESS_RATIO:
            raise SourceError("structure", f"{rooms}-izbové: nájdených {len(seen_for_query)} z deklarovaných "
                                           f"{declared_total} - stránkovanie asi nefunguje")
        print(f"{prefix} {rooms}-izbové: spolu {len(seen_for_query)} ponúk")
    return list(results.values())
