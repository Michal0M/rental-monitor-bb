"""
Kritériá vyhľadávania a nastavenia projektu (monitor prenájmov v Banskej Bystrici).
Uprav tento súbor, keď budeš chcieť zmeniť rozsah ceny, typ bytu alebo správanie scrapera.
"""

# --- Čo hľadáme ---
# Počet izieb. Garsónky ("garsonka") sa NEberú. 1-izbové sa pridajú, keď sem dopíšeš 1.
ROOMS = [2, 3]  # neskôr: [1, 2, 3]

# Cena v EUR za mesiac (tzv. "headline" cena z inzerátu, bez energií, ak sú extra).
# Tvrdý filter - inzerát mimo rozsahu sa z DB vymaže (nie je "stiahnutý", len nesedí).
# Inzeráty typu "750 € + 80 € energie" prejdú (filtruje sa na 750), skutočná suma
# s energiami sa zobrazí na karte a podľa nej sa dá aj radiť.
PRICE_MIN = 400
PRICE_MAX = 800

# Stav bytu: chceme novostavbu alebo rekonštrukciu, nie "pôvodný stav".
# MÄKKÝ filter (rovnaký princíp ako farby/rok pri arteon-monitor): nič sa nemaže,
# len sa inzerát zobrazí pod inou záložkou:
#   new, renovated  -> záložka "Novostavba / rekonštrukcia"
#   partial, unknown-> záložka "Neistý stav" (čiastočná rekonštrukcia alebo inzerát to nespomína)
#   old             -> záložka "Pôvodný stav"
# Parkovanie NIE je kritérium, len badge na karte (zahrnuté / za príplatok / spomenuté).

# --- Zdroje ---
# bazos.sk je zámerne vynechaný: filtrovanie podľa lokality (hlokalita=, humkreis)
# je v ich robots.txt zakázané pre všetkých robotov (overené 25.9.2026).
BASE_NEHNUTELNOSTI = "https://www.nehnutelnosti.sk"
BASE_REALITY = "https://www.reality.sk"

# Overené 25.9.2026 v prehliadači (1/2/3-izbové existujú na oboch portáloch):
NEHNUTELNOSTI_URL = BASE_NEHNUTELNOSTI + "/vysledky/{rooms}-izbove-byty/banska-bystrica/prenajom"
REALITY_URL = BASE_REALITY + "/byty/{rooms}-izbovy-byt/banska-bystrica/prenajom/"

# --- Technické nastavenia scrapera ---
REQUEST_DELAY_SECONDS = 4        # pauza medzi requestami (throttling)
REQUEST_TIMEOUT_SECONDS = 20
# Zámerne poctivý User-Agent (identifikuje osobný nástroj, nie predstiera prehliadač).
# Ak by ho portály blokovali, viď README "Čo robiť, ak to prestane fungovať".
USER_AGENT = "Mozilla/5.0 (compatible; rental-monitor-bb/1.0; osobne pouzitie, 1 beh denne)"
MAX_PAGES_PER_QUERY = 6          # strop stránok na jeden dopyt (nehnutelnosti: 30/strana, reality: 24/strana)
# Ak počet nájdených inzerátov je menší než tento podiel deklarovaného počtu
# (napr. "71 inzerátov" v titulku), beh sa považuje za NEÚPLNÝ a inzeráty sa
# kvôli tomu neoznačia ako stiahnuté.
COMPLETENESS_RATIO = 0.9

NEW_BADGE_DAYS = 3               # "NOVÉ" badge pre inzeráty videné prvýkrát pred <= N dňami

DB_PATH = "data/listings.db"
OUTPUT_HTML_PATH = "docs/index.html"
