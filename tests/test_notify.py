import os
import tempfile
import unittest
from unittest import mock

import config
import db
import notify
import scraper


def listing(**kw):
    base = {"title": "Pekný byt", "url": "https://x/1", "price": 700.0, "rooms": 2, "area_m2": 50.0,
            "condition": "new", "energy_included": 1, "energy_extra": None, "parking": "included",
            "parking_extra": None, "main_photo_url": "https://x/p.jpg", "availability": None}
    base.update(kw)
    return base


class KindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(notify.kind_for("new", None, 700), "new")
        self.assertEqual(notify.kind_for("price_changed", 750, 700), "price_drop")
        self.assertEqual(notify.kind_for("price_changed", 700, 750), "price_up")
        self.assertEqual(notify.kind_for("reappeared", None, 700), "reappeared")
        self.assertIsNone(notify.kind_for("unchanged", 700, 700))

    def test_should_notify(self):
        self.assertTrue(notify.should_notify(listing()))
        self.assertFalse(notify.should_notify(listing(condition="old")))
        self.assertFalse(notify.should_notify(listing(availability="reserved")))


class EmbedTests(unittest.TestCase):
    def test_embed_content(self):
        e = notify.build_embed({"kind": "price_drop", "listing": listing(), "old_price": 750.0})
        self.assertIn("750", e["description"])
        self.assertIn("700", e["description"])
        self.assertIn("s energiami", e["description"])
        self.assertIn("novostavba", e["description"])
        self.assertEqual(e["thumbnail"]["url"], "https://x/p.jpg")

    def test_extra_energy(self):
        e = notify.build_embed({"kind": "new", "listing": listing(energy_included=0, energy_extra=100.0)})
        self.assertIn("celkom ≈ 800", e["description"])


class SendTests(unittest.TestCase):
    def item(self):
        return {"kind": "new", "listing": listing(), "old_price": None}

    def test_no_webhook_skips(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("notify.requests.post") as p:
            self.assertEqual(notify.send_notifications([self.item()]), 0)
            p.assert_not_called()

    def test_batches_and_cap(self):
        items = [self.item() for _ in range(20)]
        ok = mock.Mock(status_code=204)
        with mock.patch("notify.requests.post", return_value=ok) as p, mock.patch("notify.time.sleep"):
            sent = notify.send_notifications(items, webhook="https://h/w")
        self.assertEqual(sent, config.NOTIFY_MAX_PER_RUN)
        self.assertEqual(p.call_count, 3)
        self.assertIn("content", p.call_args_list[0].kwargs["json"])

    def test_rate_limit_retry(self):
        limited = mock.Mock(status_code=429)
        limited.json.return_value = {"retry_after": 0.1}
        ok = mock.Mock(status_code=204)
        with mock.patch("notify.requests.post", side_effect=[limited, ok]), mock.patch("notify.time.sleep"):
            self.assertEqual(notify.send_notifications([self.item()], webhook="https://h/w"), 1)

    def test_error_does_not_raise(self):
        bad = mock.Mock(status_code=500)
        with mock.patch("notify.requests.post", return_value=bad), mock.patch("notify.time.sleep"):
            self.assertEqual(notify.send_notifications([self.item()], webhook="https://h/w"), 0)


class PipelineTests(unittest.TestCase):
    def raw(self, pid, price):
        return {"source": "fake", "portal_id": pid, "title": "Byt 2 izbový", "url": f"https://x/{pid}",
                "price": price, "rooms": 2, "area_m2": 50.0, "description_raw": "novostavba",
                "main_photo_url": None}

    def run_once(self, conn, raws):
        mod = mock.Mock(SOURCE_NAME="fake", LABEL="Fake", spec=["SOURCE_NAME", "LABEL", "fetch_all"])
        mod.fetch_all = lambda rooms: raws
        pending = []
        scraper.process_source(mod, conn, pending)
        return pending

    def test_seed_silent_then_new_and_drop(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            db.init_db(path)
            with db.connect(path) as conn:
                self.assertEqual(self.run_once(conn, [self.raw("a", 700)]), [])  # seed
                p = self.run_once(conn, [self.raw("a", 650), self.raw("b", 600)])
                self.assertEqual(sorted(x["kind"] for x in p), ["new", "price_drop"])


if __name__ == "__main__":
    unittest.main()
