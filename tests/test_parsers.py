import pathlib
import unittest

import nehnutelnosti_scraper as nehn
import reality_scraper as rea
import scraper

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


class NehnutelnostiParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards, cls.declared = nehn.parse_page(load("nehnutelnosti_page.html"))
        cls.by_id = {c["portal_id"]: c for c in cls.cards}

    def test_counts(self):
        self.assertEqual(self.declared, 71)
        self.assertEqual([c["portal_id"] for c in self.cards], ["JuvathsD90K", "Ju1d52jDfHs", "JufVX5CX1Zz"])

    def test_lazovna(self):
        c = self.by_id["JuvathsD90K"]
        self.assertEqual(c["title"], "PRENÁJOM MODERNÉHO 2-IZBOVÉHO BYTU V NOVOSTAVBE – LAZOVNÁ ULICA")
        self.assertEqual((c["rooms"], c["area_m2"], c["price"]), (2, 58.0, 800.0))
        self.assertEqual(c["street"], "Lazovná")
        self.assertIn("180 € /mesiac energie", c["description_raw"])
        self.assertTrue(c["main_photo_url"].startswith("https://img.unitedclassifieds.sk/"))
        self.assertTrue(c["url"].startswith("https://www.nehnutelnosti.sk/detail/JuvathsD90K/"))

    def test_house_no_street(self):
        c = self.by_id["Ju1d52jDfHs"]
        self.assertEqual((c["rooms"], c["area_m2"], c["price"]), (2, 87.0, 720.0))
        self.assertIsNone(c["street"])   # lokalita je len "Banská Bystrica, okres ..."
        self.assertIn("vrátane energii", c["description_raw"])

    def test_price_without_mes(self):
        c = self.by_id["JufVX5CX1Zz"]
        self.assertEqual(c["price"], 999.0)      # "999 €" bez "/mes.", nie cena/m² 12,87
        self.assertEqual(c["area_m2"], 77.63)


class RealityParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards, cls.declared = rea.parse_page(load("reality_page.html"))
        cls.by_id = {c["portal_id"]: c for c in cls.cards}

    def test_counts(self):
        self.assertEqual(self.declared, 58)
        self.assertEqual(len(self.cards), 4)

    def test_jsonld_with_raw_newline_is_parsed(self):
        # JSON-LD v fixture obsahuje surový \n v reťazci -> so strict=True by sa to rozpadlo
        c = self.by_id["JuvathsD90K"]
        self.assertEqual(c["structured_condition"], "Novostavba")
        self.assertIn("180 € /mesiac energie", c["description_raw"])   # plný popis z JSON-LD, nie orezaný z karty

    def test_lazovna_fields(self):
        c = self.by_id["JuvathsD90K"]
        self.assertEqual((c["rooms"], c["area_m2"], c["price"]), (2, 58.0, 800.0))
        self.assertEqual(c["street"], "Lazovná")
        self.assertIs(c["structured_energy_included"], False)
        self.assertEqual(c["floor"], "4")
        self.assertIn("Vyhradené parkovanie", c["structured_parking"])
        self.assertEqual(c["url"], "https://www.reality.sk/byty/prenajom-moderneho-2-izboveho-bytu-v-novostavbe-lazovna-ulica/JuvathsD90K/")
        self.assertTrue(c["main_photo_url"].startswith("https://img.unitedclassifieds.sk/foto/MzY4"))  # fotka bytu, nie logo agentúry

    def test_empty_street(self):
        c = self.by_id["JuJL6GXwLgA"]
        self.assertIsNone(c["street"])           # prázdny <span> medzi typom a plochou
        self.assertEqual((c["area_m2"], c["price"]), (89.0, 600.0))
        self.assertEqual(c["structured_condition"], "Pôvodný stav")

    def test_demand_card_has_no_price(self):
        c = self.by_id["JuDEMAND01"]
        self.assertIsNone(c["price"])


class Pipeline(unittest.TestCase):
    """enrich() + rejection_reason() na reálnych kartách z oboch portálov."""

    @classmethod
    def setUpClass(cls):
        cls.n = {c["portal_id"]: c for c in nehn.parse_page(load("nehnutelnosti_page.html"))[0]}
        cls.r = {c["portal_id"]: c for c in rea.parse_page(load("reality_page.html"))[0]}

    def test_lazovna_from_both_portals_is_same_id_and_classified_new(self):
        a = scraper.enrich(self.n["JuvathsD90K"])
        b = scraper.enrich(self.r["JuvathsD90K"])
        self.assertEqual(a["portal_id"], b["portal_id"])
        for x in (a, b):
            self.assertEqual(x["condition"], "new")
            self.assertEqual((x["energy_included"], x["energy_extra"]), (0, 180.0))
            self.assertEqual(x["parking"], "included")
            self.assertIsNone(scraper.rejection_reason(x))
        self.assertEqual(b["condition_source"], "structured")
        self.assertEqual(a["condition_source"], "text")

    def test_price_above_range_rejected(self):
        c = scraper.enrich(self.n["JufVX5CX1Zz"])          # 999 €
        self.assertIn("mimo", scraper.rejection_reason(c))
        c = scraper.enrich(self.r["JuPn7FPBzKk"])          # 850 €
        self.assertIn("mimo", scraper.rejection_reason(c))

    def test_old_condition_kept_not_rejected(self):
        c = scraper.enrich(self.r["JuJL6GXwLgA"])          # 600 €, Pôvodný stav
        self.assertEqual(c["condition"], "old")
        self.assertIsNone(scraper.rejection_reason(c))     # mäkký filter: ide na záložku "Pôvodný stav"

    def test_house_unknown_condition_energy_included(self):
        c = scraper.enrich(self.n["Ju1d52jDfHs"])
        self.assertEqual(c["condition"], "unknown")
        self.assertEqual(c["energy_included"], 1)
        self.assertEqual(c["parking"], "included")

    def test_demand_rejected(self):
        c = scraper.enrich(self.r["JuDEMAND01"])
        self.assertIn("dopyt", scraper.rejection_reason(c))


if __name__ == "__main__":
    unittest.main()
