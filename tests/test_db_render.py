import os
import tempfile
import unittest

import config
import db
import render
import scraper
from http_util import SourceError


def sample(source="nehnutelnosti_sk", portal_id="JuAAA", price=700.0, **kw):
    base = {"source": source, "portal_id": portal_id, "url": f"https://x/{source}/{portal_id}", "title": "Byt",
            "description_raw": "", "rooms": 2, "area_m2": 55.0, "price": price, "energy_included": None,
            "energy_extra": None, "street": "Ulica", "location": "Ulica", "condition": "new",
            "condition_source": "text", "parking": None, "furnished": None, "is_panel": 0, "floor": None,
            "main_photo_url": None}
    base.update(kw)
    return base


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.db")
        db.init_db(self.path)

    def test_price_history_and_reappear(self):
        with db.connect(self.path) as conn:
            self.assertEqual(db.upsert_listing(conn, sample()), "new")
            self.assertEqual(db.upsert_listing(conn, sample()), "unchanged")
            self.assertEqual(db.upsert_listing(conn, sample(price=650.0)), "price_changed")
            lid = db.make_id("nehnutelnosti_sk", "JuAAA")
            self.assertEqual([h["price"] for h in db.get_price_history(conn, lid)], [700.0, 650.0])
            self.assertEqual(db.mark_missing_as_removed(conn, "nehnutelnosti_sk", set()), [lid])
            self.assertEqual(db.get_listing(conn, lid)["status"], "removed")
            self.assertEqual(db.upsert_listing(conn, sample(price=650.0)), "reappeared")
            self.assertEqual(db.get_listing(conn, lid)["status"], "active")

    def test_delete_removes_history_too(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample())
            self.assertTrue(db.delete_listing(conn, "nehnutelnosti_sk", "JuAAA"))
            self.assertIsNone(db.get_listing(conn, db.make_id("nehnutelnosti_sk", "JuAAA")))

    def test_failed_source_does_not_mark_anything_removed(self):
        """Kľúčová poistka: 403/timeout nesmie označiť všetky inzeráty za stiahnuté."""
        class Boom:
            SOURCE_NAME, LABEL = "nehnutelnosti_sk", "nehnutelnosti.sk"

            @staticmethod
            def fetch_all(rooms):
                raise SourceError("blocked", "HTTP 403")

        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample())
            status, _ = scraper.process_source(Boom, conn)
            self.assertEqual(status, "blocked")
            self.assertEqual(db.get_listing(conn, db.make_id("nehnutelnosti_sk", "JuAAA"))["status"], "active")
            run = db.last_source_runs(conn)["nehnutelnosti_sk"]
            self.assertEqual((run["status"], run["message"]), ("blocked", "HTTP 403"))

    def test_successful_source_marks_missing(self):
        class Fine:
            SOURCE_NAME, LABEL = "nehnutelnosti_sk", "nehnutelnosti.sk"

            @staticmethod
            def fetch_all(rooms):
                return [sample(portal_id="JuBBB")]

        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="JuAAA"))
            status, stats = scraper.process_source(Fine, conn)
            self.assertEqual((status, stats["new"], stats["removed"]), ("ok", 1, 1))
            self.assertEqual(db.get_listing(conn, db.make_id("nehnutelnosti_sk", "JuAAA"))["status"], "removed")


class DetailAndFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.db")
        db.init_db(self.path)

    def _module(self, listings, detail_fn, required=True):
        class Mod:
            SOURCE_NAME, LABEL, REQUIRED = "nehnutelnosti_sk", "nehnutelnosti.sk", required
            calls = []

            @staticmethod
            def fetch_all(rooms):
                return [dict(l) for l in listings]

            @staticmethod
            def fetch_detail(url):
                Mod.calls.append(url)
                return detail_fn(url)
        return Mod

    def test_rejections(self):
        r = scraper.rejection_reason
        self.assertIn("prenajat", (r(sample(title="PRENAJATÉ - Na prenájom veľký 2-izbový byt")) or "").lower() + "prenajat")
        self.assertEqual(r(sample(title="PRENAJATÉ - Na prenájom veľký 2-izbový byt")), "už prenajatý")
        self.assertIsNone(r(sample(title="Prenajmeme veľký 3-izb byt", rooms=3, area_m2=80.0)))
        self.assertIsNone(r(sample(title="REZERVOVANÉ - byt")))
        self.assertEqual(r(sample(title="Zrekonštruovaný 1,5i byt v Radvani")), "1,5-izbový byt")
        self.assertIn("plocha", r(sample(title="Byt", area_m2=25.0)))
        self.assertIsNone(r(sample(title="Byt", area_m2=None)))     # bez plochy sa neodmieta

    def test_detail_fetched_once_then_cached(self):
        detail = lambda url: {"condition_label": "Novostavba", "condition_candidates": ["Novostavba"], "energy_included": True}
        mod = self._module([sample(portal_id="JuA", condition_source=None)], detail)
        with db.connect(self.path) as conn:
            scraper.process_source(mod, conn)
            row = db.get_listing(conn, "nehnutelnosti_sk:JuA")
            self.assertEqual((row["condition"], row["condition_source"], row["energy_included"]), ("new", "structured", 1))
            self.assertEqual(row["structured_condition"], "Novostavba")
            self.assertTrue(row["detail_checked_at"])
            scraper.process_source(mod, conn)          # druhý beh - detail sa nesťahuje znova
            self.assertEqual(len(mod.calls), 1)
            row = db.get_listing(conn, "nehnutelnosti_sk:JuA")
            self.assertEqual((row["condition"], row["condition_source"]), ("new", "structured"))   # cache prežila výpis

    def test_full_description_used_and_survives_next_run(self):
        full = "Byt 750€\nEnergie a správa 100€\nGarážové parkovacie miesto  50 €\n(v podzemnej garáži)"
        detail = lambda url: {"condition_label": "Novostavba", "condition_candidates": ["Novostavba"],
                              "energy_included": None, "description": full}
        mod = self._module([sample(portal_id="JuA", description_raw="Skrátený popis...")], detail)
        with db.connect(self.path) as conn:
            scraper.process_source(mod, conn)
            row = db.get_listing(conn, "nehnutelnosti_sk:JuA")
            self.assertEqual((row["energy_included"], row["energy_extra"], row["parking"], row["parking_extra"]),
                             (0, 100.0, "optional", 50.0))
            scraper.process_source(mod, conn)     # druhý beh: výpis má znova len skrátený popis, detail sa nesťahuje
            self.assertEqual(len(mod.calls), 1)
            row = db.get_listing(conn, "nehnutelnosti_sk:JuA")
            self.assertEqual((row["energy_extra"], row["parking_extra"]), (100.0, 50.0))

    def test_old_detail_version_is_refetched_once(self):
        detail = lambda url: {"condition_label": None, "condition_candidates": [], "energy_included": None, "description": ""}
        mod = self._module([sample(portal_id="JuA")], detail)
        with db.connect(self.path) as conn:
            scraper.process_source(mod, conn)
            conn.execute("UPDATE listings SET detail_version = 1")     # cache z predošlej verzie parsera
            scraper.process_source(mod, conn)
            self.assertEqual(len(mod.calls), 2)
            scraper.process_source(mod, conn)
            self.assertEqual(len(mod.calls), 2)

    def test_blocked_detail_stops_fetching_but_keeps_listings(self):
        def boom(url):
            raise SourceError("blocked", "HTTP 403")
        mod = self._module([sample(portal_id="JuA"), sample(portal_id="JuB", title="Iný")], boom)
        with db.connect(self.path) as conn:
            status, stats = scraper.process_source(mod, conn)
            self.assertEqual(status, "ok")
            self.assertEqual(stats["new"], 2)
            self.assertEqual(len(mod.calls), 1)        # po prvom blokovaní sa už nepokračuje
            self.assertIsNone(db.get_listing(conn, "nehnutelnosti_sk:JuA")["detail_checked_at"])   # skúsi sa znova

    def test_migration_adds_columns_to_old_db(self):
        import sqlite3
        old = os.path.join(self.tmp, "old.db")
        c = sqlite3.connect(old)
        # Stará schéma = dnešná bez 4 nových stĺpcov (tak vyzerá DB, ktorú už vytvoril prvý beh na GitHube).
        new_cols = ("availability", "structured_condition", "structured_energy", "detail_checked_at")
        old_schema = "\n".join(l for l in db.SCHEMA.splitlines() if not l.strip().startswith(new_cols))
        c.executescript(old_schema)
        c.execute("INSERT INTO listings (id, source, portal_id, url, title, first_seen_at, last_seen_at) "
                  "VALUES ('x:1', 'x', '1', 'u', 't', 'a', 'b')")
        c.commit(); c.close()
        db.init_db(old); db.init_db(old)               # opakovane bezpečné
        with db.connect(old) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
            n = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        self.assertTrue({"availability", "structured_condition", "structured_energy", "detail_checked_at"} <= cols)
        self.assertEqual(n, 1)                         # existujúce dáta ostali

    def test_exit_code_optional_vs_required(self):
        import unittest.mock as mock

        def mod(name, required, ok):
            class M:
                SOURCE_NAME, LABEL, REQUIRED = name, name, required

                @staticmethod
                def fetch_all(rooms):
                    if not ok:
                        raise SourceError("network", "timeout")
                    return []
            return M

        def run(*mods):
            with mock.patch.object(scraper, "SOURCES", list(mods)), mock.patch.object(config, "DB_PATH", self.path):
                return scraper.main()
        self.assertEqual(run(mod("a", True, True), mod("b", False, False)), 0)   # nepovinný spadol -> 0
        self.assertEqual(run(mod("a", True, False), mod("b", False, True)), 1)   # povinný spadol -> 1
        self.assertEqual(run(mod("a", True, False), mod("b", False, False)), 1)  # všetko spadlo -> 1


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.db")
        self.out = os.path.join(self.tmp, "index.html")
        db.init_db(self.path)

    def test_same_flat_on_two_portals_is_one_card_with_two_links(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample("nehnutelnosti_sk", "JuvathsD90K", 800.0, energy_included=0, energy_extra=180.0))
            db.upsert_listing(conn, sample("reality_sk", "JuvathsD90K", 800.0, energy_included=0, energy_extra=180.0,
                                           condition_source="structured"))
        page = render.render(self.path, self.out)
        self.assertEqual(page.count('class="card"'), 1)
        self.assertIn("nehnutelnosti.sk", page)
        self.assertIn("reality.sk", page)
        self.assertIn("980 €", page)             # 800 + 180 energie
        self.assertIn("+ 180 € energie", page)

    def test_parking_price_badge(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(parking="optional", parking_extra=50.0, energy_included=0, energy_extra=100.0))
        page = render.render(self.path, self.out)
        self.assertIn("Parkovanie +50 €/mes.", page)
        self.assertIn("+ 100 € energie", page)
        self.assertIn("celkom ≈ 800 €", page)

    def test_room_filter_buttons_and_card_attribute(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1", rooms=1, title="A", area_m2=30.0))
            db.upsert_listing(conn, sample(portal_id="J2", rooms=2, title="B"))
            db.upsert_listing(conn, sample(portal_id="J3", rooms=3, title="C", area_m2=70.0))
        page = render.render(self.path, self.out)
        for r in (1, 2, 3):
            self.assertIn(f'data-rooms="{r}" data-label="{r}-izbové">{r}-izbové (1)', page)
            self.assertIn(f'class="card" data-cat="good" data-rooms="{r}"', page)
        self.assertIn("Všetky izby (3)", page)

    def test_html_is_escaped(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(title='<script>alert(1)</script> "byt"', street="<b>x</b>"))
        page = render.render(self.path, self.out)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_categories_and_failed_run_banner(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1", condition="new", title="Byt A"))
            db.upsert_listing(conn, sample(portal_id="J2", condition="old", title="Byt B"))
            db.upsert_listing(conn, sample(portal_id="J3", condition="unknown", title="Byt C"))
            db.record_source_run(conn, "nehnutelnosti_sk", "blocked", None, "HTTP 403 pri https://x")
            db.record_source_run(conn, "reality_sk", "network", None, "ConnectTimeout")
        page = render.render(self.path, self.out)
        self.assertIn("Novostavba / rekonštrukcia (1)", page)
        self.assertIn("Neistý stav (1)", page)
        self.assertIn("Pôvodný stav (1)", page)
        self.assertIn("ZLYHALO (blocked)", page)          # povinný zdroj = červený pruh
        self.assertIn("reality.sk: nedostupný (network", page)   # nepovinný = len poznámka
        self.assertEqual(page.count("run-problem\">"), 1)

    def test_optional_source_failure_is_not_a_red_banner(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1"))
            db.record_source_run(conn, "nehnutelnosti_sk", "ok", 1, None)
            db.record_source_run(conn, "reality_sk", "network", None, "ConnectTimeout")
        page = render.render(self.path, self.out)
        self.assertNotIn('class="run-status run-problem"', page)
        self.assertIn("nepovinný zdroj", page)

    def test_fuzzy_duplicate_merged_but_different_flats_not(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="JdupA", title="Pekný byt", price=700.0))
            db.upsert_listing(conn, sample(portal_id="JdupB", title="Pekný byt", price=700.0))
            db.upsert_listing(conn, sample(portal_id="Jother", title="Pekný byt", price=650.0))   # iná cena
            db.upsert_listing(conn, sample(portal_id="JbigA", title="Pekný byt", price=700.0, area_m2=70.0))  # iná plocha
        page = render.render(self.path, self.out)
        self.assertEqual(page.count('class="card"'), 3)
        self.assertIn("možný duplikát", page)
        self.assertIn("(#2)", page)

    def test_reserved_badge_and_no_fresh_badge_on_seed_day(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1", title="REZERVOVANÉ - byt", availability="reserved"))
        page = render.render(self.path, self.out)
        self.assertIn("Rezervované", page)
        self.assertNotIn(">NOVÉ<", page)     # prvý (seed) deň sa NOVÉ nezobrazuje

    def test_fresh_badge_after_seed_day(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="Jold", title="Starý"))
            db.upsert_listing(conn, sample(portal_id="Jnew", title="Nový"))
            conn.execute("UPDATE listings SET first_seen_at = '2020-01-01T00:00:00+00:00' WHERE portal_id = 'Jold'")
        page = render.render(self.path, self.out)
        self.assertEqual(page.count(">NOVÉ<"), 1)

    def test_removed_goes_to_removed_tab_with_last_price(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1", price=700.0))
            db.mark_missing_as_removed(conn, "nehnutelnosti_sk", set())
        page = render.render(self.path, self.out)
        self.assertIn("Stiahnuté (1)", page)
        self.assertIn("Novostavba / rekonštrukcia (0)", page)
        self.assertIn("700 €", page)


if __name__ == "__main__":
    unittest.main()
