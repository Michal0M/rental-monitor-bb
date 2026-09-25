"""
Extrakcia údajov z voľného textu inzerátov (cena, plocha, stav bytu, energie, parkovanie...).

Všetko sa porovnáva na textoch bez diakritiky a malými písmenami (fold()), takže
vzory nižšie sú bez diakritiky ("rekonstrukc", nie "rekonštrukc"). Euro znak sa pred
tým nahradí slovom " eur ", aby prežil odstránenie diakritiky.

Vzory sú odladené na REÁLNYCH inzerátoch z nehnutelnosti.sk / reality.sk (25.9.2026),
napr. "Cena 800 € /mesačne najom a 180 € /mesiac energie" alebo
"Cena je kompletná vrátane energii, internetu, odpadu, jedného parkovacieho miesta".
Nie sú neomylné - texty inzerátov sú voľné. Pri novom formáte pridaj test do
tests/test_textutils.py a doplň vzor.
"""

import re
import unicodedata


def fold(text: str | None) -> str:
    """Malé písmená, bez diakritiky, € -> ' eur ', zjednotené medzery."""
    if not text:
        return ""
    s = text.replace("€", " eur ").replace(" ", " ")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", s)


def fold_lines(text: str | None) -> str:
    """Ako fold(), ale každý koniec riadku sa zmení na ". " - popisy z detailu majú položky po riadkoch
    ("Byt 750€⏎Energie 100€") a bez oddelenia by sa čísla a slová z rôznych riadkov zlepili."""
    return fold(re.sub(r"\s*[\r\n]+\s*", ". ", text or ""))


# ------------------------------------------------------------------ čísla

def parse_price(text: str | None) -> float | None:
    """'800 €/mes.' -> 800.0, '999 €' -> 999.0, '1 200 €/mesiac' -> 1200.0. Cena za m² sa NEberie."""
    if not text:
        return None
    t = text.replace(" ", " ").strip()
    if re.search(r"€\s*/\s*m\s*[²2]", t):   # '13,79 €/m²/mes.' = cena za m², nie nájom
        return None
    m = re.search(r"(\d[\d ]*(?:[.,]\d{1,2})?)\s*€", t)
    if not m:
        return None
    num = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_area(text: str | None) -> float | None:
    """'58 m²' -> 58.0, '60.42 m²' -> 60.42, '77,63 m2' -> 77.63."""
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m\s*[²2]", text.replace(" ", " "))
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def parse_rooms(text: str | None) -> int | None:
    """'2 izbový byt' -> 2. Garsónka/apartmán bez čísla -> None (nepatrí medzi 1/2/3-izbové)."""
    if not text:
        return None
    m = re.search(r"(\d)\s*[- ]?\s*izb", fold(text))
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ stav bytu

_STRUCTURED_CONDITION = {
    "novostavba": "new",
    "kompletna rekonstrukcia": "renovated",
    "ciastocna rekonstrukcia": "partial",
    "povodny stav": "old",
}


def detect_condition(text: str | None, structured: str | None = None) -> tuple[str, str | None]:
    """
    Vráti (kategória, zdroj) kde kategória je:
        'new' | 'renovated' | 'partial' | 'unknown' | 'old'
    a zdroj 'structured' (reality.sk pole "Stav nehnuteľnosti") | 'text' | None.

    Štruktúrované pole má prednosť pred voľným textom. Hodnoty reality.sk overené
    25.9.2026 na 72 inzerátoch: Novostavba, Kompletná rekonštrukcia,
    Čiastočná rekonštrukcia, Pôvodný stav.
    """
    if structured:
        key = fold(structured).strip()
        for pattern, category in _STRUCTURED_CONDITION.items():
            if pattern in key:
                return category, "structured"

    t = fold(text)
    if re.search(r"\b(bez|pred)\s+rekonstrukc|nerekonstruovan|vyzaduj\w*\s+rekonstrukc"
                 r"|povodn\w+\s+(stav|jadro|bytove jadro)|povodnom stave", t):
        return "old", "text"
    if "novostavb" in t:
        return "new", "text"
    if re.search(r"ciastocn\w+\s+rekonstrukc", t):
        return "partial", "text"
    if re.search(r"rekonstrukc|rekonstruovan|renovovan|renovaci", t):
        return "renovated", "text"
    return "unknown", None


# ------------------------------------------------------------------ energie

_ENERGY_EXTRA_PATTERNS = [
    # "180 € /mesiac energie", "80 eur energie", "80 € záloha na energie"
    r"(\d{2,3})(?:,-)?\s*eur\w*\s*/?\s*(?:mesiac|mesacne|mes)?\s*(?:za\s+|na\s+)?(?:zalohy?\s+(?:na\s+)?)?energi",
    # "energie 80 €", "energie: cca 80 €", "energie vo výške 80 €",
    # "Energie a správa 100€", "energie + poplatky 90 €", "energie, internet 120 €"
    r"energi\w*(?:\s*(?:a|\+|,|/)\s*(?:sprav\w*|poplat\w*|internet\w*|odpad\w*))*"
    r"\s*(?:cca|asi|ca|priblizne|okolo|vo vyske|:|-)?\s*(?:cca\s*)?(\d{2,3})(?:,-)?\s*eur",
    # "zálohy na energie 80"
    r"zalohy?\s+(?:na\s+)?energi\w*\s*(?:cca|vo vyske|:)?\s*(\d{2,3})\b",
]

_ENERGY_INCLUDED = (
    r"(?:vratane|s|v cene)\s+(?:vsetk\w+\s+)?(?:energi|inkasa)|energi\w*\s+(?:su\s+|sú\s+)?(?:v\s+cene|zahrnut)"
    r"|cena\s+(?:je\s+)?(?:kompletna|vratane)|zahrna\w*\s+(?:zalohov\w+\s+platby\s+za\s+)?energi"
)
_ENERGY_EXCLUDED = r"bez\s+energi|energi\w*\s+(?:navyse|zvlast|hradi|sa\s+plat)"


def detect_energy(text: str | None, structured_included: bool | None = None) -> tuple[bool | None, float | None]:
    """
    Vráti (energie_v_cene, extra_energie_eur).
        energie_v_cene: True / False / None (neznáme)
        extra: mesačná suma energií navyše, ak sa dala vyčítať z textu (20-400 €), inak None.
    """
    t = fold_lines(text)
    extra = None
    for pattern in _ENERGY_EXTRA_PATTERNS:
        m = re.search(pattern, t)
        if m:
            value = float(m.group(1))
            if 20 <= value <= 400:
                extra = value
                break

    if structured_included is True:
        return True, None
    included: bool | None = None
    if extra is not None or re.search(_ENERGY_EXCLUDED, t):
        included = False
    elif structured_included is False:
        included = False
    elif re.search(_ENERGY_INCLUDED, t):
        included = True
    return included, extra


# ------------------------------------------------------------------ parkovanie

_PARK_WORD = r"parkovac|parkovan|parking|garaz|statie|carport"
_PARK_NEG = r"bez\s+(?:parkovan|garaz)|nema\s+(?:parkovan|garaz)|parkovan\w*\s+(?:nie je|nemozne)"
_PARK_OPTIONAL = (r"prikupit|dokupit|priplatok|za\s+poplatok|prenajat\s+si\s+(?:zvlast|samostatne)"
                  r"|moznost\w*\s+(?:prenaj\w+|prikup\w+|dokup\w+)|volitelne|zvlast")
_PARK_INCLUDED = (r"k\s+bytu\s+(?:patri|prislucha|nalezi)|sucastou\s+(?:bytu|ceny)|v\s+cene|vratane"
                  r"|vlastn\w+|vyhraden\w+|garazove\s+statie|pridelen\w+")


def _parking_scan(text: str | None, structured: str | None = None) -> tuple[str | None, float | None]:
    """(druh, cena parkovania navyše za mesiac | None). Cena sa berie len z vety o parkovaní, ktorá NIE JE
    "v cene/vrátane" (inak by to bola cena nájmu). Rozsah 10-300 €."""
    if structured and re.search(_PARK_WORD, fold(structured)):
        return "included", None
    found, found_price = None, None
    rank = {"mentioned": 1, "optional": 2, "included": 3}
    for sentence in re.split(r"[.!?;]", fold_lines(text)):
        if not re.search(_PARK_WORD, sentence) or re.search(_PARK_NEG, sentence):
            continue
        price = None
        m = re.search(r"(\d{2,3})(?:,-)?\s*eur", sentence)
        if m and 10 <= float(m.group(1)) <= 300 and not re.search(r"v\s+cene|vratane|zahrnut", sentence):
            price = float(m.group(1))
        if re.search(_PARK_OPTIONAL, sentence) or price is not None:
            kind = "optional"
        elif re.search(_PARK_INCLUDED, sentence):
            kind = "included"
        else:
            kind = "mentioned"
        if found is None or rank[kind] > rank[found]:
            found = kind
        if kind == "optional" and price is not None and found_price is None:
            found_price = price
    return found, found_price


def detect_parking(text: str | None, structured: str | None = None) -> str | None:
    """
    'included' (patrí k bytu / vyhradené) | 'optional' (za príplatok / dá sa prikúpiť)
    | 'mentioned' (spomenuté bez detailu) | None.
    Parkovanie NIE je kritérium filtra, len informácia (badge) na karte.
    """
    return _parking_scan(text, structured)[0]


def detect_parking_extra(text: str | None) -> float | None:
    """Mesačná cena parkovania navyše ("Garážové parkovacie miesto 50 €"), inak None."""
    return _parking_scan(text)[1]


# ------------------------------------------------------------------ ostatné

def is_rented(title: str | None) -> bool:
    """'PRENAJATÉ - ...' v titulku = byt je už prenajatý. ('Prenajmeme...' je bežný inzerát - "prenajat" tam nie je.)"""
    return bool(re.search(r"\bprenajat", fold(title)))


def is_reserved(title: str | None) -> bool:
    """'REZERVOVANÉ' / 'Rezervované!!!' v titulku."""
    return bool(re.search(r"\brezervovan", fold(title)))


def is_half_room(title: str | None) -> bool:
    """'1,5i byt', '1,5-izbový', '1.5 izb.' - poloviční izba, nepatrí medzi 2-izbové."""
    return bool(re.search(r"\b1[.,]5\s*[- ]?\s*i(?:zb|\b)", fold(title)))


def detect_furnished(text: str | None) -> bool | None:
    t = fold(text)
    if re.search(r"nezariaden|bez\s+zariaden|bez\s+nabytku|prazdny", t):
        return False
    if re.search(r"zariaden|vybaven\w+\s+nabytkom|s\s+nabytkom", t):
        return True
    return None


def detect_panel(text: str | None) -> bool:
    return bool(re.search(r"panelov|panelak|panelovom", fold(text)))


def is_demand_ad(title: str | None) -> bool:
    """Dopyt ('Dopyt na prenájom...', 'Hľadám byt...') - niekto byt hľadá, neponúka. 'Hľadáte...' je normálny inzerát."""
    t = fold(title).strip()
    return bool(re.match(r"(dopyt|hladam\b|hladame\b|kupim\b|prenajmem si\b)", t))
