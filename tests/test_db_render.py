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

    def test_html_is_escaped(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(title='<script>alert(1)</script> "byt"', street="<b>x</b>"))
        page = render.render(self.path, self.out)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_categories_and_failed_run_banner(self):
        with db.connect(self.path) as conn:
            db.upsert_listing(conn, sample(portal_id="J1", condition="new"))
            db.upsert_listing(conn, sample(portal_id="J2", condition="old"))
            db.upsert_listing(conn, sample(portal_id="J3", condition="unknown"))
            db.record_source_run(conn, "nehnutelnosti_sk", "ok", 3, None)
            db.record_source_run(conn, "reality_sk", "blocked", None, "HTTP 403 pri https://x")
        page = render.render(self.path, self.out)
        self.assertIn("Novostavba / rekonštrukcia (1)", page)
        self.assertIn("Neistý stav (1)", page)
        self.assertIn("Pôvodný stav (1)", page)
        self.assertIn("ZLYHALO (blocked)", page)

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
