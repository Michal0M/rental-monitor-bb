"""
Zdroj: nehnutelnosti.sk (výpis prenájmov 1/2/3-izbových bytov v Banskej Bystrici).

Overené 25.9.2026 v prehliadači:
  - robots.txt: zakázané `?order=NEWEST|PRICE_ASC|PRICE_DESC`, `/api/`, `/profil/*` a pod.
    Používame len `?page=N` (nie je zakázané) a žiadne order= parametre.
  - Stránka je server-side renderovaná (Next.js/MUI) - inzeráty sú v surovom HTML,
    JavaScript netreba (fetch() surového HTML obsahoval 30 unikátnych `/detail/<id>/<slug>`).
  - Stránkovanie: `?page=2`, `?page=3`. 30 inzerátov na stranu.
  - MUI CSS triedy sú hashované (`mui-1it92ak`) a menia sa - NEPOUŽÍVAME ich.
    Namiesto toho: každý inzerát má v HTML 5 odkazov `<a href=".../detail/<ID>/...">`,
    "karta" je najmenší spoločný predok týchto odkazov (overené: pre všetkých 30 kariet
    obsahuje práve jedno ID). Vnútri sú `<h2>` (titulok) a `<p data-test-id="text">`
    v poradí: lokalita, "N izbový byt", "X m²", cena, cena/m², a popis (najdlhší <p>).
  - Cena býva "800 €/mes." ALE aj "999 €" (bez /mes.) - regex to musí zvládnuť.
  - ID inzerátu (napr. JuvathsD90K) je zhodné s ID na reality.sk (rovnaký prevádzkovateľ),
    čo sa využíva na zlúčenie duplicít v render.py.
"""

import re

from bs4 import BeautifulSoup

import config
import textutils
from http_util import SourceError, diagnose_empty, fetch

SOURCE_NAME = "nehnutelnosti_sk"
LABEL = "nehnutelnosti.sk"
_ID_RE = re.compile(r"/detail/([^/]+)/")


def _card_for(anchors):
    """Najmenší spoločný predok všetkých odkazov jedného inzerátu."""
    first = anchors[0]
    ancestor_ids = [{id(p) for p in a.parents} for a in anchors[1:]]
    for parent in first.parents:
        if all(id(parent) in s for s in ancestor_ids):
            return parent
    return first.parent


def parse_page(html: str) -> tuple[list[dict], int | None]:
    """
    Vráti (zoznam kandidátov, deklarovaný počet inzerátov z titulku alebo None).
    Kandidát je slovník so všetkými poľami potrebnými pre DB (ešte bez filtrovania kritérií).
    """
    soup = BeautifulSoup(html, "html.parser")

    declared = None
    if soup.title and soup.title.string:
        m = re.search(r"\((\d+)\s+inzer", soup.title.string)
        if m:
            declared = int(m.group(1))

    by_id: dict[str, list] = {}
    order: list[str] = []
    for a in soup.select('a[href*="/detail/"]'):
        m = _ID_RE.search(a["href"])
        if not m:
            continue
        pid = m.group(1)
        if pid not in by_id:
            by_id[pid] = []
            order.append(pid)
        by_id[pid].append(a)

    candidates = []
    for pid in order:
        anchors = by_id[pid]
        card = _card_for(anchors)
        h2 = card.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        paragraphs = []
        for p in card.find_all("p"):
            text = p.get_text(" ", strip=True)
            if text and text not in paragraphs:
                paragraphs.append(text)

        rooms_text = next((t for t in paragraphs if re.fullmatch(r"\d\s*izbov\w*\s+byt", t, re.I)), None)
        area_text = next((t for t in paragraphs if re.fullmatch(r"[\d.,]+\s*m²", t)), None)
        price_text = next((t for t in paragraphs
                           if re.fullmatch(r"\d[\d\s.,]*\s*€(?:\s*/\s*mes\.?)?", t)), None)
        location_text = next((t for t in paragraphs if "okres" in t), None)
        street = None
        if location_text:
            first = location_text.split(",")[0].strip()
            if first and not first.lower().startswith("banská bystrica"):
                street = first
        used = {title, rooms_text, area_text, price_text, location_text}
        description = max((t for t in paragraphs if t not in used and len(t) >= 40 and not re.search(r"€/m²", t)),
                          key=len, default="")

        img = card.find("img", src=re.compile(r"^https?://"))
        href = anchors[0]["href"]
        candidates.append({
            "portal_id": pid,
            "source": SOURCE_NAME,
            "url": href if href.startswith("http") else config.BASE_NEHNUTELNOSTI + href,
            "title": title,
            "description_raw": description,
            "rooms": textutils.parse_rooms(rooms_text),
            "area_m2": textutils.parse_area(area_text),
            "price": textutils.parse_price(price_text),
            "street": street,
            "location": location_text,
            "structured_condition": None,       # tento portál v zozname stav nezobrazuje
            "structured_energy_included": None,
            "structured_parking": None,
            "floor": None,
            "main_photo_url": img["src"] if img else None,
        })
    return candidates, declared


def fetch_all(rooms_list: list[int]) -> list[dict]:
    """Stiahne všetky stránky pre každý počet izieb. Vyhodí SourceError pri blokovaní/zmene štruktúry/neúplnosti."""
    prefix = f"[{SOURCE_NAME}]"
    results: dict[str, dict] = {}
    for rooms in rooms_list:
        base = config.NEHNUTELNOSTI_URL.format(rooms=rooms)
        seen_for_query: set[str] = set()
        declared_total = None
        for page_no in range(1, config.MAX_PAGES_PER_QUERY + 1):
            url = base if page_no == 1 else f"{base}?page={page_no}"
            page = fetch(url, prefix)
            candidates, declared = parse_page(page.text)
            if page_no == 1:
                declared_total = declared
                print(f"{prefix} {rooms}-izbové: deklarovaných {declared} inzerátov, "
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
        print(f"{prefix} {rooms}-izbové: spolu {len(seen_for_query)} inzerátov")
    return list(results.values())
