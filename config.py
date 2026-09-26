"""
Kritériá vyhľadávania a nastavenia projektu (monitor prenájmov v Banskej Bystrici).
Uprav tento súbor, keď budeš chcieť zmeniť rozsah ceny, typ bytu alebo správanie scrapera.
"""

# --- Čo hľadáme ---
# Počet izieb. Garsónky ("garsonka") sa NEberú. Na stránke sa dá filtrovať podľa počtu izieb.
ROOMS = [1, 2, 3]

# Cena v EUR za mesiac (tzv. "headline" cena z inzerátu, bez energií, ak sú extra).
# Tvrdý filter - inzerát mimo rozsahu sa z DB vymaže (nie je "stiahnutý", len nesedí).
# Inzeráty typu "750 € + 80 € energie" prejdú (filtruje sa na 750), skutočná suma
# s energiami sa zobrazí na karte a podľa nej sa dá aj radiť.
PRICE_MIN = 400
PRICE_MAX = 800

# Minimálna plocha (m²) podľa počtu izieb - odfiltruje veci ako "1,5i byt 32 m²" alebo
# "2-izbový" s 25 m². Zámerne voľné hranice; inzerát bez uvedenej plochy sa neodmieta.
MIN_AREA_M2 = {1: 0, 2: 35, 3: 50}

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

# Detail inzerátu (nehnutelnosti.sk) obsahuje štruktúrovaný stav bytu ("Novostavba", "Kompletná
# rekonštrukcia"...) a "s energiami", ktoré vo výpise nie sú. Detail sa sťahuje len pre NOVÉ
# inzeráty a potom raz za DETAIL_REFRESH_DAYS dní (údaje sa ukladajú do DB).
DETAIL_REFRESH_DAYS = 14
DETAIL_MAX_PER_RUN = 120
DETAIL_VERSION = 2               # zvýš, ak detail začne poskytovať nové údaje - cache sa jednorazovo obnoví         # strop detailov na jeden beh (zvyšok sa dotiahne nasledujúci deň)

NEW_BADGE_DAYS = 3               # "NOVÉ" badge pre inzeráty videné prvýkrát pred <= N dňami
                                 # (pri úplne prvom behu sa "NOVÉ" nezobrazuje - to je len počiatočný stav)

DB_PATH = "data/listings.db"
OUTPUT_HTML_PATH = "docs/index.html"

# --- Discord oznámenia (webhook v GitHub Secret DISCORD_WEBHOOK_BYTY) ---
# Pre aké stavy sa posiela oznámenie ("old" = pôvodný stav sa neoznamuje).
NOTIFY_CONDITIONS = ("new", "renovated", "partial", "unknown")
NOTIFY_MAX_PER_RUN = 15  # ochrana pred zaplavením kanála
