import unittest

import textutils as tu


class ParseNumbers(unittest.TestCase):
    def test_price(self):
        self.assertEqual(tu.parse_price("800 €/mes."), 800.0)
        self.assertEqual(tu.parse_price("800 €/mesiac"), 800.0)     # reality.sk formát
        self.assertEqual(tu.parse_price("999 €"), 999.0)            # bez "/mes." (reálny inzerát)
        self.assertEqual(tu.parse_price("1 200 €/mes."), 1200.0)
        self.assertEqual(tu.parse_price("1 200 €"), 1200.0)
        self.assertIsNone(tu.parse_price("13,79 €/m²/mes."))        # cena za m² nie je nájom
        self.assertIsNone(tu.parse_price("Cena dohodou"))
        self.assertIsNone(tu.parse_price(None))

    def test_area(self):
        self.assertEqual(tu.parse_area("58 m²"), 58.0)
        self.assertEqual(tu.parse_area("60.42 m²"), 60.42)
        self.assertEqual(tu.parse_area("77,63 m2"), 77.63)
        self.assertIsNone(tu.parse_area("bez plochy"))

    def test_rooms(self):
        self.assertEqual(tu.parse_rooms("2 izbový byt"), 2)
        self.assertEqual(tu.parse_rooms("3-izbový byt"), 3)
        self.assertEqual(tu.parse_rooms("3 izbové byty"), 3)
        self.assertIsNone(tu.parse_rooms("Garsónka"))
        self.assertIsNone(tu.parse_rooms(None))


class Condition(unittest.TestCase):
    def test_structured_wins_over_text(self):
        self.assertEqual(tu.detect_condition("nič", "Novostavba"), ("new", "structured"))
        self.assertEqual(tu.detect_condition("novostavba", "Pôvodný stav"), ("old", "structured"))
        self.assertEqual(tu.detect_condition("", "Kompletná rekonštrukcia"), ("renovated", "structured"))
        self.assertEqual(tu.detect_condition("", "Čiastočná rekonštrukcia"), ("partial", "structured"))

    def test_text(self):
        self.assertEqual(tu.detect_condition("Byt v novostavbe na Lazovnej")[0], "new")
        self.assertEqual(tu.detect_condition("2 izb. bytu po komplet. rekonštrukcii od bytového architekta")[0], "renovated")
        self.assertEqual(tu.detect_condition("Zrekonštruovaný byt")[0], "renovated")
        self.assertEqual(tu.detect_condition("byt v pôvodnom stave")[0], "old")
        self.assertEqual(tu.detect_condition("byt bez rekonštrukcie")[0], "old")
        self.assertEqual(tu.detect_condition("čiastočná rekonštrukcia kúpeľne")[0], "partial")

    def test_unknown_when_nothing_said(self):
        # reálny popis: "novou kuchyňou, čerstvo vymaľovaný" nie je dôkaz o rekonštrukcii
        self.assertEqual(tu.detect_condition("Byt je k dispozícii IHNEĎ s novou kuchyňou, čerstvo vymaľovaný"),
                         ("unknown", None))


class Energy(unittest.TestCase):
    def test_extra_real_ad(self):
        # reálny text: Lazovná
        text = "Cena 800 € /mesačne najom a 180 € /mesiac energie. Byt je ideálny"
        self.assertEqual(tu.detect_energy(text), (False, 180.0))

    def test_extra_variants(self):
        self.assertEqual(tu.detect_energy("nájom 750 € + energie 80 €"), (False, 80.0))
        self.assertEqual(tu.detect_energy("750 € + 80 € energie")[1], 80.0)
        self.assertEqual(tu.detect_energy("zálohy na energie 90")[1], 90.0)

    def test_included_real_ad(self):
        text = "Cena je kompletná vrátane energii, internetu, odpadu, jedného parkovacieho miesta."
        self.assertEqual(tu.detect_energy(text), (True, None))
        self.assertEqual(tu.detect_energy("2-IZB. APARTMÁN, 574,-Eur s energiami")[0], True)

    def test_structured(self):
        self.assertEqual(tu.detect_energy("nič", True), (True, None))
        self.assertEqual(tu.detect_energy("nič", False), (False, None))
        self.assertEqual(tu.detect_energy("nič"), (None, None))

    def test_no_false_positive_from_area(self):
        self.assertEqual(tu.detect_energy("pivnica s výmerou 5,5 m², energie podľa spotreby")[1], None)


class Parking(unittest.TestCase):
    def test_included_real_ads(self):
        self.assertEqual(tu.detect_parking("K bytu prislúcha vyhradené vonkajšie parkovacie miesto. "
                                           "Zároveň je možnosť prenajať si zvlášť aj vnútorné kryté parkovacie miesto."),
                         "included")
        self.assertEqual(tu.detect_parking("Cena je kompletná vrátane energii, jedného parkovacieho miesta."), "included")
        self.assertEqual(tu.detect_parking("pivnica, garážové státie pre maximálne pohodlie, okamžitá pripravenosť"),
                         "included")

    def test_optional(self):
        self.assertEqual(tu.detect_parking("Parkovacie miesto je možné prikúpiť za 30 € mesačne."), "optional")
        self.assertEqual(tu.detect_parking("Parkovanie za príplatok."), "optional")

    def test_mentioned_and_none(self):
        self.assertEqual(tu.detect_parking("v okolí je parkovanie pred domom"), "mentioned")
        self.assertIsNone(tu.detect_parking("byt bez parkovania"))
        self.assertIsNone(tu.detect_parking("pekný byt"))

    def test_structured(self):
        self.assertEqual(tu.detect_parking("", "Výťah, Garážové státie, Pivnica"), "included")
        self.assertIsNone(tu.detect_parking("", "Výťah, Pivnica"))


class Misc(unittest.TestCase):
    def test_demand(self):
        self.assertTrue(tu.is_demand_ad("Dopyt na prenájom 2- 3 izbový byt Banská Bystrica."))
        self.assertTrue(tu.is_demand_ad("Hľadám 2-izbový byt"))
        self.assertFalse(tu.is_demand_ad("Hľadáte priestranné bývanie s parkovaním v Banskej Bystrici?"))  # reálny inzerát!

    def test_furnished_panel(self):
        self.assertTrue(tu.detect_furnished("kompletne zariadený byt"))
        self.assertFalse(tu.detect_furnished("nezariadený byt"))
        self.assertIsNone(tu.detect_furnished("pekný byt"))
        self.assertTrue(tu.detect_panel("v panelovom dome"))
        self.assertFalse(tu.detect_panel("v tehlovom dome"))


if __name__ == "__main__":
    unittest.main()
