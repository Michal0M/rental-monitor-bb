"""
Vygeneruje statickú HTML stránku (docs/index.html) z dát v SQLite databáze.
Publikuje sa cez GitHub Pages - žiadny server, všetky dáta sú "zapečené" do HTML.

Ten istý byt inzerovaný na oboch portáloch (rovnaké portal_id) sa zobrazí ako JEDNA karta
s odkazmi na oba portály. Všetky texty z inzerátov sa escapujú (html.escape).
"""

import html
from datetime import datetime, timezone

import config
import db
import textutils

# Zdroje, ktorých zlyhanie nie je poplach (viď REQUIRED v scraperoch) - v HTML len nenápadná poznámka.
OPTIONAL_SOURCES = {"reality_sk"}

CONDITION_LABELS = {
    "new": "Novostavba",
    "renovated": "Rekonštrukcia",
    "partial": "Čiastočná rekonštrukcia",
    "unknown": "Stav neuvedený",
    "old": "Pôvodný stav",
}
SOURCE_LABELS = {"nehnutelnosti_sk": "nehnutelnosti.sk", "reality_sk": "reality.sk"}
PARKING_LABELS = {"included": "Parkovanie v cene", "optional": "Parkovanie za príplatok", "mentioned": "Parkovanie (zmienka)"}
FILL_FIELDS = ["area_m2", "street", "floor", "main_photo_url", "energy_included", "energy_extra",
               "parking", "furnished", "rooms"]


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_eur(value) -> str:
    return f"{value:,.0f} €".replace(",", " ")


def merge_listings(listings: list[dict]) -> list[dict]:
    """Zlúči riadky s rovnakým portal_id (ten istý byt na viacerých portáloch) do jednej karty."""
    groups: dict[str, list[dict]] = {}
    for item in listings:
        groups.setdefault(item["portal_id"], []).append(item)

    merged = []
    for items in groups.values():
        active = [i for i in items if i["status"] == "active"]
        pool = active or items
        # Primárny záznam: ten so štruktúrovaným stavom (reality.sk), inak naposledy videný.
        pool_sorted = sorted(pool, key=lambda i: (i["condition_source"] == "structured", i["last_seen_at"]), reverse=True)
        card = dict(pool_sorted[0])
        for key in FILL_FIELDS:
            if card.get(key) in (None, ""):
                card[key] = next((o[key] for o in pool_sorted[1:] if o.get(key) not in (None, "")), card.get(key))
        card["status"] = "active" if active else "removed"
        card["first_seen_at"] = min(i["first_seen_at"] for i in items)
        card["last_seen_at"] = max(i["last_seen_at"] for i in items)
        card["sources"] = [{"label": SOURCE_LABELS.get(i["source"], i["source"]), "url": i["url"], "status": i["status"],
                            "price": i["price"]} for i in sorted(items, key=lambda i: i["source"])]
        card["price_differs"] = len({i["price"] for i in pool if i["price"] is not None}) > 1
        card["histories_from"] = [i["id"] for i in pool_sorted]
        merged.append(card)
    return merged


def dedupe_similar(cards: list[dict]) -> list[dict]:
    """
    Zlúči karty, ktoré majú RÔZNE ID, ale zjavne ide o ten istý byt (agent dal inzerát dvakrát):
    rovnaký počet izieb, plocha, cena a titulok (bez diakritiky/veľkosti písmen). Prísny kľúč zámerne -
    radšej zostane duplicita než zlúčenie dvoch rôznych bytov. Druhý odkaz sa označí "(#2)".
    """
    groups: dict[tuple, list[dict]] = {}
    for c in cards:
        if c["area_m2"] and c["price"] and c["rooms"]:
            key = (c["rooms"], round(c["area_m2"], 1), c["price"], textutils.fold(c["title"]))
        else:
            key = ("unique", c["portal_id"])
        groups.setdefault(key, []).append(c)

    result = []
    for items in groups.values():
        items = sorted(items, key=lambda c: (c["status"] == "active", c["first_seen_at"]), reverse=True)
        main = dict(items[0])
        main["dup_count"] = len(items)
        if len(items) > 1:
            extra_sources = []
            for n, other in enumerate(items[1:], start=2):
                for src in other["sources"]:
                    extra_sources.append({**src, "label": f'{src["label"]} (#{n})'})
            main["sources"] = main["sources"] + extra_sources
            if any(i["status"] == "active" for i in items):
                main["status"] = "active"
            main["first_seen_at"] = min(i["first_seen_at"] for i in items)
        result.append(main)
    return result


def category(card: dict) -> str:
    """Záložka: removed / good / unsure / old."""
    if card["status"] != "active":
        return "removed"
    return {"new": "good", "renovated": "good", "partial": "unsure", "unknown": "unsure", "old": "old"}.get(
        card["condition"], "unsure")


def total_cost(card: dict) -> float | None:
    if card["price"] is None:
        return None
    return card["price"] + (card["energy_extra"] or 0)


def price_html(card: dict) -> str:
    price = fmt_eur(card["price"])
    extra = card["energy_extra"]
    if extra:
        return (f'<div class="card-price">{price} <span class="plus">+ {fmt_eur(extra)} energie</span></div>'
                f'<div class="total-note">celkom ≈ {fmt_eur(card["price"] + extra)} / mes.</div>')
    if card["energy_included"] == 1:
        return f'<div class="card-price">{price} <span class="plus ok">energie v cene</span></div>'
    if card["energy_included"] == 0:
        return f'<div class="card-price">{price} <span class="plus">+ energie</span></div>'
    return f'<div class="card-price">{price} <span class="plus dim">energie neuvedené</span></div>'


def price_trend_html(history: list[dict]) -> str:
    prices = [h["price"] for h in history if h["price"] is not None]
    if len(prices) <= 1:
        return ""
    css = "price-down" if prices[-1] < prices[0] else ("price-up" if prices[-1] > prices[0] else "")
    arrow = "↓" if css == "price-down" else ("↑" if css == "price-up" else "→")
    chain = " → ".join(f"{p:,.0f}".replace(",", " ") for p in prices)
    return f'<div class="price-history {css}">{arrow} história: {chain} €</div>'


def card_html(card: dict, history: list[dict], seed_day: str | None = None) -> str:
    cat = category(card)
    total = total_cost(card)
    per_m2 = (card["price"] / card["area_m2"]) if card["price"] and card["area_m2"] else None
    first = datetime.fromisoformat(card["first_seen_at"])
    age_days = (datetime.now(timezone.utc) - first).days

    badges = []
    if card["status"] != "active":
        badges.append('<span class="badge badge-sold">Stiahnuté z ponuky</span>')
    elif age_days <= config.NEW_BADGE_DAYS and card["first_seen_at"][:10] != seed_day:
        badges.append('<span class="badge badge-fresh">NOVÉ</span>')
    if card.get("availability") == "reserved" and card["status"] == "active":
        badges.append('<span class="badge badge-warn" title="V titulku inzerátu je REZERVOVANÉ">Rezervované</span>')
    if card.get("dup_count", 1) > 1:
        badges.append('<span class="badge badge-info" title="Rovnaký byt (izby, plocha, cena, titulok) inzerovaný '
                      'pod viacerými ID - zlúčené">možný duplikát</span>')
    hint = "z popisu inzerátu" if card["condition_source"] == "text" else (
        "štruktúrované pole portálu" if card["condition_source"] == "structured" else "inzerát stav nespomína")
    badges.append(f'<span class="badge cond-{esc(card["condition"])}" title="{esc(hint)}">'
                  f'{CONDITION_LABELS.get(card["condition"], "?")}</span>')
    if card["parking"]:
        badges.append(f'<span class="badge badge-parking">{PARKING_LABELS[card["parking"]]}</span>')
    if card["furnished"] == 1:
        badges.append('<span class="badge badge-info">Zariadený</span>')
    elif card["furnished"] == 0:
        badges.append('<span class="badge badge-info">Nezariadený</span>')
    if card["is_panel"]:
        badges.append('<span class="badge badge-info">Panelák</span>')
    if card["price_differs"]:
        badges.append('<span class="badge badge-warn" title="Cena sa na portáloch líši">Cena sa líši</span>')

    photo = card["main_photo_url"]
    if photo:
        photo_html = (f'<img class="card-photo" src="{esc(photo)}" alt="" loading="lazy" referrerpolicy="no-referrer" '
                      f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
                      f'<div class="card-photo card-photo-placeholder" style="display:none;">Bez fotky</div>')
    else:
        photo_html = '<div class="card-photo card-photo-placeholder">Bez fotky</div>'

    links = " · ".join(
        f'<a href="{esc(s["url"])}" target="_blank" rel="noopener">{esc(s["label"])}'
        f'{" (stiahnuté)" if s["status"] != "active" else ""}</a>' for s in card["sources"])
    primary_url = card["sources"][0]["url"]

    meta_parts = [f'{card["rooms"]}-izbový' if card["rooms"] else "?"]
    if card["area_m2"]:
        meta_parts.append(f'{card["area_m2"]:g} m²')
    if per_m2:
        meta_parts.append(f'{per_m2:.1f} €/m²')
    if card["street"]:
        meta_parts.append(esc(card["street"]))
    if card["floor"]:
        meta_parts.append(f'{esc(card["floor"])}. poschodie')

    return f"""
    <div class="card" data-cat="{cat}" data-total="{total or 0:.0f}" data-ppm2="{per_m2 or 0:.2f}"
         data-area="{card['area_m2'] or 0}" data-first="{int(first.timestamp())}">
      <a href="{esc(primary_url)}" target="_blank" rel="noopener" class="card-photo-link">{photo_html}</a>
      <div class="card-body">
        <div class="badges">{''.join(badges)}</div>
        <a href="{esc(primary_url)}" target="_blank" rel="noopener" class="card-title">{esc(card['title'])}</a>
        {price_html(card)}
        {price_trend_html(history)}
        <div class="card-meta">{' · '.join(meta_parts)}</div>
        <div class="card-footer">
          <span class="source-tag">{links}</span>
          <span class="dates">od {card['first_seen_at'][:10]} · naposledy {card['last_seen_at'][:10]}</span>
        </div>
      </div>
    </div>"""


def run_status_html(runs: dict[str, dict]) -> str:
    if not runs:
        return ""
    parts, problem = [], False
    for source, label in SOURCE_LABELS.items():
        run = runs.get(source)
        if not run:
            parts.append(f"{label}: zatiaľ neprebehol")
            continue
        if run["status"] == "ok":
            parts.append(f'{label}: OK ({run["found"]} inzerátov, {run["run_at"][:16].replace("T", " ")} UTC)')
        elif source in OPTIONAL_SOURCES:
            parts.append(f'{label}: nedostupný ({esc(run["status"])}, {run["run_at"][:16].replace("T", " ")} UTC) '
                         f'- nepovinný zdroj, dáta z nehnutelnosti.sk sú úplné')
        else:
            problem = True
            parts.append(f'<b>{label}: ZLYHALO ({esc(run["status"])})</b> - {esc((run["message"] or "")[:200])}'
                         f' ({run["run_at"][:16].replace("T", " ")} UTC)')
    css = "run-status run-problem" if problem else "run-status"
    prefix = "⚠️ Posledný beh zlyhal aspoň pre jeden zdroj - dáta u neho môžu byť neaktuálne. " if problem else ""
    return f'<div class="{css}">{prefix}{" · ".join(parts)}</div>'


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Prenájmy BB - monitor</title>
<style>
  :root { --bg:#0f1115; --card-bg:#1a1d24; --border:#2a2e38; --text:#e8eaed; --text-dim:#9aa0aa;
          --accent:#4f9dff; --green:#3ecf8e; --red:#ff5c5c; --yellow:#f5c518; }
  * { box-sizing: border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,"Segoe UI",Roboto,sans-serif; margin:0; padding:16px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .subtitle { color:var(--text-dim); font-size:13px; margin-bottom:12px; }
  .run-status { font-size:12px; color:var(--text-dim); margin-bottom:12px; }
  .run-problem { color:#fff; background:rgba(255,92,92,.18); border:1px solid var(--red); border-radius:6px; padding:8px 10px; }
  .controls { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; align-items:center; }
  .controls button { background:var(--card-bg); border:1px solid var(--border); color:var(--text); padding:6px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  .controls button.active, .controls button.sort-btn-active { background:var(--accent); border-color:var(--accent); color:#fff; }
  .sort-label { color:var(--text-dim); font-size:13px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:14px; }
  .card { background:var(--card-bg); border:1px solid var(--border); border-radius:10px; overflow:hidden; display:flex; flex-direction:column; }
  .card[data-cat="removed"] { opacity:.5; }
  .card-photo-link { display:block; }
  .card-photo { width:100%; height:170px; object-fit:cover; background:#000; display:block; }
  .card-photo-placeholder { align-items:center; justify-content:center; color:var(--text-dim); font-size:12px; background:#15171c; display:flex; }
  .card-body { padding:12px; display:flex; flex-direction:column; gap:6px; }
  .badges { display:flex; gap:4px; flex-wrap:wrap; }
  .badge { font-size:10px; padding:2px 6px; border-radius:4px; font-weight:600; }
  .badge-fresh { background:var(--green); color:#000; }
  .badge-sold { background:var(--red); color:#fff; }
  .cond-new, .cond-renovated { background:#1f4d3a; color:#8ff0c0; }
  .cond-partial, .cond-unknown { background:#4a3a1a; color:#f0c070; }
  .cond-old { background:#4a1a2a; color:#f08fb0; }
  .badge-parking { background:#2a3f5f; color:#9dc4ff; }
  .badge-info { background:#2a2e38; color:var(--text-dim); }
  .badge-warn { background:var(--yellow); color:#000; }
  .card-title { color:var(--text); font-weight:600; font-size:14px; text-decoration:none; line-height:1.3; }
  .card-title:hover { color:var(--accent); }
  .card-price { font-size:20px; font-weight:700; }
  .plus { font-size:12px; font-weight:500; color:var(--yellow); }
  .plus.ok { color:var(--green); } .plus.dim { color:var(--text-dim); }
  .total-note { font-size:12px; color:var(--yellow); margin-top:-4px; }
  .price-history { font-size:11px; color:var(--text-dim); }
  .price-history.price-down { color:var(--green); } .price-history.price-up { color:var(--red); }
  .card-meta { font-size:12px; color:var(--text-dim); }
  .card-footer { display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; font-size:10px; color:var(--text-dim); margin-top:4px; border-top:1px solid var(--border); padding-top:6px; }
  .card-footer a { color:var(--accent); }
  .empty-state { color:var(--text-dim); padding:40px; text-align:center; }
</style>
</head>
<body>
  <h1>🏠 Prenájmy 2- a 3-izbových bytov · Banská Bystrica</h1>
  <div class="subtitle">Aktualizované: %%UPDATED%% · rozsah %%PRICE_MIN%%–%%PRICE_MAX%% € · %%ROOMS%%-izbové</div>
  %%RUN_STATUS%%
  <div class="controls">
    <button class="filter-btn active" data-filter="good">Novostavba / rekonštrukcia (%%N_GOOD%%)</button>
    <button class="filter-btn" data-filter="unsure">Neistý stav (%%N_UNSURE%%)</button>
    <button class="filter-btn" data-filter="old">Pôvodný stav (%%N_OLD%%)</button>
    <button class="filter-btn" data-filter="removed">Stiahnuté (%%N_REMOVED%%)</button>
    <button class="filter-btn" data-filter="all">Všetky (%%N_ALL%%)</button>
  </div>
  <div class="controls">
    <span class="sort-label">Zoradiť:</span>
    <button class="sort-btn sort-btn-active" data-field="total">Cena celkom</button>
    <button class="sort-btn" data-field="ppm2">€/m²</button>
    <button class="sort-btn" data-field="area">Plocha</button>
    <button class="sort-btn" data-field="first">Najnovšie</button>
  </div>
  <div id="grid" class="grid">
    %%CARDS%%
  </div>
  <div id="empty" class="empty-state" style="display:none;">Žiadne inzeráty v tomto filtri.</div>

<script>
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  const cards = Array.from(grid.children);
  let currentFilter = 'good';
  // Predvolený smer: cena/€/m² vzostupne, plocha a "najnovšie" zostupne.
  const sortState = { field: 'total', asc: true };
  const DEFAULT_ASC = { total: true, ppm2: true, area: false, first: false };

  function applyFilter() {
    let visible = 0;
    cards.forEach(c => {
      const show = currentFilter === 'all' || c.dataset.cat === currentFilter;
      c.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    empty.style.display = visible === 0 ? 'block' : 'none';
  }

  function applySort() {
    const value = c => parseFloat(c.dataset[sortState.field]) || 0;
    // Karty bez údaja (0) idú na koniec pri vzostupnom radení.
    cards.slice().sort((a, b) => {
      const va = value(a), vb = value(b);
      if (sortState.asc && (va === 0 || vb === 0) && va !== vb) return va === 0 ? 1 : -1;
      return sortState.asc ? va - vb : vb - va;
    }).forEach(c => grid.appendChild(c));
  }

  function updateSortLabels() {
    document.querySelectorAll('.sort-btn').forEach(btn => {
      const active = btn.dataset.field === sortState.field;
      btn.classList.toggle('sort-btn-active', active);
      btn.textContent = btn.textContent.replace(/ [↑↓]$/, '') + (active ? (sortState.asc ? ' ↑' : ' ↓') : '');
    });
  }

  document.querySelectorAll('.filter-btn').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    applyFilter();
  }));

  document.querySelectorAll('.sort-btn').forEach(btn => btn.addEventListener('click', () => {
    if (sortState.field === btn.dataset.field) sortState.asc = !sortState.asc;
    else { sortState.field = btn.dataset.field; sortState.asc = DEFAULT_ASC[btn.dataset.field]; }
    updateSortLabels(); applySort();
  }));

  updateSortLabels(); applySort(); applyFilter();
</script>
</body>
</html>
"""


def render(db_path: str | None = None, output_path: str | None = None) -> str:
    db_path = db_path or config.DB_PATH
    output_path = output_path or config.OUTPUT_HTML_PATH
    with db.connect(db_path) as conn:
        all_rows = db.get_all_listings(conn)
        seed_day = min((r["first_seen_at"][:10] for r in all_rows), default=None)
        cards = dedupe_similar(merge_listings(all_rows))
        histories = {c["histories_from"][0]: db.get_price_history(conn, c["histories_from"][0]) for c in cards}
        runs = db.last_source_runs(conn)

    cards.sort(key=lambda c: (total_cost(c) is None, total_cost(c) or 0))
    counts = {"good": 0, "unsure": 0, "old": 0, "removed": 0}
    for c in cards:
        counts[category(c)] += 1

    page = (PAGE_TEMPLATE
            .replace("%%UPDATED%%", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
            .replace("%%PRICE_MIN%%", str(config.PRICE_MIN)).replace("%%PRICE_MAX%%", str(config.PRICE_MAX))
            .replace("%%ROOMS%%", "/".join(str(r) for r in config.ROOMS))
            .replace("%%RUN_STATUS%%", run_status_html(runs))
            .replace("%%N_GOOD%%", str(counts["good"])).replace("%%N_UNSURE%%", str(counts["unsure"]))
            .replace("%%N_OLD%%", str(counts["old"])).replace("%%N_REMOVED%%", str(counts["removed"]))
            .replace("%%N_ALL%%", str(len(cards)))
            .replace("%%CARDS%%", "\n".join(card_html(c, histories[c["histories_from"][0]], seed_day) for c in cards)))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Vygenerované: {output_path} ({len(cards)} kariet: {counts})")
    return page


if __name__ == "__main__":
    render()
