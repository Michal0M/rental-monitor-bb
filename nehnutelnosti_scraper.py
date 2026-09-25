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
REQUIRED = True   # zlyhanie tohto zdroja = chyba celého behu (workflow sa označí červeno)

# Štítky stavu, ktoré poznáme (bez diakritiky). Iný štítok sa nezahodí - ide do textovej detekcie
# a zaloguje sa ako "neznámy štítok stavu", aby sa dal doplniť do textutils._STRUCTURED_CONDITION.
KNOWN_CONDITION_LABELS = {"novostavba", "kompletna rekonstrukcia", "ciastocna rekonstrukcia", "povodny stav"}
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


def parse_detail(html: str) -> dict:
    """
    Údaje z DETAILU inzerátu (overené 25.9.2026 na 5 inzerátoch, zhodné s reality.sk):
      - `<meta name="description" content="2 izbový byt, Prenájom, Banská Bystrica, Novostavba, 58 m², 800 €/mes., ...">`
        -> stav bytu je časť medzi mestom a plochou (chýba, ak ho inzerent nevyplnil);
      - hlavný cenový riadok `<p data-test-id="text">720 €/mes. s energiami</p>` -> "s energiami" = energie v cene.
    Vracia {"condition_label": str|None, "condition_candidates": [str], "energy_included": True|None, "description": str}.
    energy_included je True alebo None (absencia "s energiami" ešte nedokazuje, že energie NIE sú v cene).
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    content = (meta.get("content") or "") if meta else ""
    parts = [p.strip() for p in content.split(",")]

    candidates: list[str] = []
    for part in parts[3:8]:                       # 0: typ, 1: Prenájom, 2: mesto, potom stav / ulica, potom plocha
        if re.search(r"\d\s*m\s*[²2]", part):
            break
        if part:
            candidates.append(part)
    label = next((c for c in candidates if textutils.fold(c) in KNOWN_CONDITION_LABELS), None)

    energy = None
    for p in soup.find_all("p", attrs={"data-test-id": "text"}):
        text = p.get_text(" ", strip=True)
        if re.match(r"^\d[\d\s.,]*\s*€\s*/\s*mes", text):
            energy = True if "s energiami" in textutils.fold(text) else None
            break
    # Celý popis (výpis ho skracuje): <p id="detail-description">, riadkovanie je v texte ako \n.
    # Overené 25.9.2026 - sú tam napr. "Energie a správa 100€", "Garážové parkovacie miesto 50 €", "SPOLU 900€".
    desc_el = soup.find(id="detail-description")
    description = desc_el.get_text("\n", strip=True) if desc_el else ""
    return {"condition_label": label, "condition_candidates": candidates, "energy_included": energy,
            "description": description}


def fetch_detail(url: str) -> dict:
    """Stiahne a spracuje detail jedného inzerátu (throttled). Vyhodí SourceError pri chybe/blokovaní."""
    page = fetch(url, f"[{SOURCE_NAME}] detail")
    return parse_detail(page.text)


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
