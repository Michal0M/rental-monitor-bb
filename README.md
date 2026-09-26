# Monitor prenájmov BB

Denne prechádza **nehnutelnosti.sk** a **reality.sk**, hľadá 1-, 2- a 3-izbové byty na prenájom
v Banskej Bystrici podľa `config.py`, zapisuje ich do SQLite databázy s históriou cien a generuje
statickú HTML stránku (`docs/index.html`) publikovanú cez GitHub Pages. Rovnaká architektúra ako
`arteon-monitor`.

## Ako to funguje

1. **`scraper.py`** - pre každý zdroj stiahne výpis (1/2/3-izbové × všetky stránky), vyfiltruje podľa
   `config.py`, doplní odvodené polia (stav bytu, energie, parkovanie) a zapíše do `data/listings.db`.
2. **`render.py`** - z databázy vygeneruje `docs/index.html`. Ten istý byt na oboch portáloch je
   **jedna karta** s odkazmi na oba (zlúčenie podľa spoločného ID inzerátu).
3. **GitHub Actions** (`.github/workflows/daily-scrape.yml`) - raz denne (5:30 UTC) spustí testy, scraper
   a render a výsledok commitne späť do repozitára.

Súbory: `config.py` (kritériá) · `nehnutelnosti_scraper.py`, `reality_scraper.py` (parsery) ·
`textutils.py` (extrakcia z textu) · `http_util.py` (sťahovanie, detekcia blokovania) · `db.py` · `render.py` ·
`tests/` (52 testov, časť na reálnych kartách a reálnych meta popisoch detailov).

## Prvotné nastavenie

1. Vytvor nový **prázdny** repozitár `rental-monitor-bb` na GitHube (bez README).
2. Nahraj kód:
   ```bash
   cd rental-monitor-bb
   git init && git add . && git commit -m "Prvotný commit: monitor prenájmov BB"
   git branch -M main
   git remote add origin https://github.com/<username>/rental-monitor-bb.git
   git push -u origin main
   ```
3. Settings → Pages: Source **Deploy from a branch**, branch **main**, priečinok **/docs**.
4. Settings → Actions → General → Workflow permissions: **Read and write permissions**.
5. Actions → *Denný scrape prenájmov BB* → **Run workflow** (prvý beh ručne, potom sleduj log).

**Prvý beh je test, či portály pustia GitHub runnery** (viď nižšie). Pozri v logu riadky
`GET ... -> 200` a `deklarovaných N inzerátov, na 1. strane M kariet`.

## Kritériá (`config.py`)

| Nastavenie | Predvolené | Poznámka |
|---|---|---|
| `ROOMS` | `[1, 2, 3]` | na stránke sa dá filtrovať podľa počtu izieb (tlačidlá *Izby*) v kombinácii so záložkou stavu a radením |
| `PRICE_MIN` / `PRICE_MAX` | 400 / 800 € | tvrdý filter na **inzerovanú** cenu, nie na cenu s energiami |
| Stav bytu | mäkký filter | záložky, nič sa nemaže |

**Stav bytu** (novostavba/rekonštrukcia, nie "pôvodný"): nehnutelnosti.sk ho vo výpise nemá, ale **detail
inzerátu** ho má v `<meta name="description">` (Novostavba / Kompletná rekonštrukcia / Čiastočná rekonštrukcia /
Pôvodný stav; rovnaké hodnoty ako pole *Stav nehnuteľnosti* na reality.sk) a cenový riadok tam obsahuje
"s energiami", ak sú energie v cene. Scraper stiahne detail **len pre nové inzeráty** a potom raz za
`DETAIL_REFRESH_DAYS` (14) dní, max `DETAIL_MAX_PER_RUN` (120) za beh; údaje sa cacheujú v DB. Prvý beh po
tejto zmene stiahne ~90 detailov (4 s pauza = ~6-10 min), ďalšie behy len pár. Ak inzerát štítok stavu
nemá (inzerent ho nevyplnil), stav sa hádže z textu. Neznámy štítok sa zaloguje ("neznámy štítok stavu").
Ak by detail vrátil 403, ďalšie detaily sa v behu nesťahujú a výpis funguje ďalej s textovým odhadom.
Ak je byt aj na reality.sk, vyhráva štruktúrované pole. Záložky: *Novostavba / rekonštrukcia*,
*Neistý stav* (čiastočná rekonštrukcia alebo inzerát stav nespomína), *Pôvodný stav*.

**Energie:** inzeráty typu "750 € + 80 € energie" prejdú filtrom (filtruje sa na 750). Ak sa suma energií
dá vyčítať z textu, karta ukáže `celkom ≈ 830 €` a dá sa podľa nej radiť. Reálny príklad z 25.9.2026:
"800 € nájom a 180 € energie" = 980 €.

**Celý popis z detailu:** výpis skracuje popis, preto sa energie a parkovanie často nedali vyčítať. Detail má
celý popis v `<p id="detail-description">`; ukladá sa do DB (`description_raw`) a texty ako "Energie a správa 100€"
alebo "Garážové parkovacie miesto 50 €" sa vyhodnocujú z neho. Karta ukáže `+ 100 € energie`, `celkom ≈ 850 €`
a odznak `Parkovanie +50 €/mes.` (cena parkovania sa do "celkom" nepočíta). Zmena parsera detailu = zvýš
`DETAIL_VERSION` v `config.py`, všetky detaily sa jednorazovo stiahnu znova.

**Obľúbené:** hviezdička ☆/★ na karte. Obľúbené karty sú vždy na začiatku (v rámci zvolenej záložky, izieb aj radenia)
a záložka *★ Obľúbené* ukáže len ich. Ukladajú sa **iba v prehliadači** (localStorage, kľúč = ID inzerátu), takže sú
zvlášť na PC a v mobile a po vymazaní údajov prehliadača zmiznú. Stránka je statická, žiadny server nemá.

**Ďalšie filtre:** `PRENAJATÉ` v titulku = vyradené; `REZERVOVANÉ` = zostane so žltým odznakom; "1,5i byt"
sa nepočíta za 2-izbový; minimálna plocha `MIN_AREA_M2` (2-izb. 35 m², 3-izb. 50 m²; inzerát bez plochy sa nevyradí).

**Parkovanie** nie je kritérium, len badge: *v cene* / *za príplatok* / *zmienka*.

## Čo je overené a čo nie (25.9.2026)

Overené priamo v prehliadači (nie hádané):
- robots.txt oboch portálov povoľuje naše URL (`?page=N`). Bazoš je vynechaný, lebo jeho robots.txt
  zakazuje filtrovanie podľa lokality (`hlokalita=`, `humkreis`).
- Oba portály vracajú inzeráty v surovom HTML (bez JavaScriptu).
- Stránkovanie je úplné: nehnutelnosti.sk 71/71 (2-izb.) a 67/67 (3-izb.), reality.sk 63/63 a 58/58.
- Ten istý byt má **rovnaké ID** na oboch portáloch: 60 z 63 (2-izb.) a 58 z 58 (3-izb.) ponúk z reality.sk
  je aj na nehnutelnosti.sk. Nehnutelnosti.sk má navyše ~20 ponúk, ktoré reality.sk nemá.

Zistené prvým behom na GitHub Actions (25.9.2026): **nehnutelnosti.sk funguje**, **reality.sk dáva z runnera
`ConnectTimeout`** (sieťová úroveň, nie parser). Preto je reality.sk **nepovinný zdroj** (`REQUIRED = False`):
jeho zlyhanie neoznačí workflow červeno, v HTML je len šedá poznámka a údaje o stave bytu prináša detail
z nehnutelnosti.sk. Ak by si reality.sk chcel, spúšťaj scraper lokálne (domáca IP).

**Neoverené:**
- Formát meta popisu detailu je overený na ~6 inzerátoch; inzerát s nezvyčajnou štruktúrou môže dať "neznámy
  štítok" (zaloguje sa, nie je to chyba).
- Ako dlho vydržia podpísané URL fotiek (`?st=...`); pri chybe sa zobrazí "Bez fotky" a URL sa obnoví
  pri ďalšom behu.

## Čo robiť, ak to prestane fungovať

- **Workflow červený, log ukazuje `ZLYHALO (blocked)`** - portál blokuje runner. Skús neskôr ručne;
  ak je to trvalé, spúšťaj scraper lokálne cez plánovač úloh na PC (domáca IP). User-Agent v `config.py` je
  zámerne poctivý (identifikuje nástroj); nemeň ho na predstieranie prehliadača bez toho, aby si zvážil dôsledky.
- **`ZLYHALO (structure)` alebo 0 kariet** - zmenila sa štruktúra stránky. Over ju znova v prehliadači a uprav
  parser (`parse_page` v príslušnom `*_scraper.py`). Na nehnutelnosti.sk sa nespoliehame na hashované CSS
  triedy (`mui-...`), len na odkazy `/detail/<id>/`, `<h2>` a `<p data-test-id="text">`.
- **Zlé stavy/energie/parkovanie** - texty inzerátov sú voľné; doplň vzor do `textutils.py` a test do
  `tests/test_textutils.py`.

Poistky: inzeráty sa označia ako *stiahnuté* **len po úplnom úspešnom behu zdroja** (počet nájdených
musí zodpovedať deklarovanému počtu z portálu). Pri chybe sa nič neoznačí.

## Známe obmedzenia

- Pokrýva len mesto Banská Bystrica (výpisy portálov "Banská Bystrica"), nie okolité obce.
- Filtrujeme na inzerovanú cenu; "850 € vrátane energií" sa vyradí, hoci je reálne lacnejšie než "800 € + 80 €".
  Dá sa doladiť (napr. filtrovať na `price + energy_extra`).
- Údaje v inzerátoch si niekedy odporujú (reálne: v texte 48 m², v poli 58 m²). Zobrazuje sa údaj z poľa portálu.
- Duplicity sa zlučujú pri zhodnom ID (obe portály) a **prísne** aj pri rôznych ID, ak sedí počet izieb,
  plocha, cena a titulok (odznak "možný duplikát", odkazy "(#2)"). Rovnaký byt s mierne inak napísaným
  titulkom sa nezlúči - radšej duplicita než zlúčenie dvoch rôznych bytov.
- Odznak NOVÉ sa v prvý (seed) deň nezobrazuje - vtedy je "nové" všetko.
- Testovacie fixtures sú skrátené karty z reálnych výpisov, nie celé stránky.

## Discord oznámenia

- Webhook kanála sa berie **len** z GitHub Secretu `DISCORD_WEBHOOK_BYTY` (nikdy nie z kódu ani z chatu).
- Oznamuje sa: nový inzerát, zmena ceny (zľava/zvýšenie), opätovne objavený inzerát. Iba pre stavy z `config.NOTIFY_CONDITIONS` (pôvodný stav a rezervované sa neoznamujú).
- Prvý beh pre daný zdroj (prázdna DB) je tichý, aby neprišla stena správ. Max `NOTIFY_MAX_PER_RUN` oznámení za beh.
- Chyba Discordu scraper nezhodí (len riadok `[notify]` v logu).
- Test: Actions → Run workflow → zaškrtnúť „Poslať testovaciu správu na Discord“.
