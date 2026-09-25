import unittest

import textutils

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

class RealDetailDescription(unittest.TestCase):
    """Reálny popis z detailu inzerátu JuOsmCsCuql (25.9.2026): výpis ho skracuje, detail ho má celý."""
    TEXT = ("Byt sa prenajíma minimálne na 1 rok.\nByt 750€\nEnergie a správa 100€\nV cene TV a internet (optika).\n"
            "Garážové parkovacie miesto  50 €\n(v podzemnej garáži bytového domu)\nSPOLU 900€\n\nVratná kaucia 900 €")

    def test_energy_with_management_fee(self):
        self.assertEqual(textutils.detect_energy(self.TEXT), (False, 100.0))

    def test_parking_with_price_is_optional(self):
        self.assertEqual(textutils.detect_parking(self.TEXT), "optional")
        self.assertEqual(textutils.detect_parking_extra(self.TEXT), 50.0)

    def test_more_real_descriptions(self):
        cases = [  # (text, energie, parkovanie, cena parkovania) - úryvky reálnych inzerátov z 25.9.2026
            ("Parkovacie miesto na uzavretom parkovisku za mesačný poplatok 50 eur.", (None, None), "optional", 50.0),
            ("Cena je KONEČNÁ – už zahŕňa zálohové platby za energie, ako aj internet a TV!", (True, None), None, None),
            ("720 eur mesačne vrátane energií, internetu a parkovacieho miesta. Depozit vo výške 600 eur", (True, None), "included", None),
            ("Cena na mesiac 380,- eur + 220,- eur energie .", (False, 220.0), None, None),
            ("Byt 350 €\nEnergie 80 €", (False, 80.0), None, None),     # riadky sa nesmú zlepiť (350 nie sú energie)
            ("verejné parkovanie pri dome\nCena: 600 € mesačne vrátane energií.", (True, None), "mentioned", None),
        ]
        for text, energy, parking, extra in cases:
            self.assertEqual(textutils.detect_energy(text), energy, text)
            self.assertEqual(textutils.detect_parking(text), parking, text)
            self.assertEqual(textutils.detect_parking_extra(text), extra, text)

    def test_parking_included_price_is_not_parking_price(self):
        t = "Cena je vrátane parkovacieho miesta, nájom 750 €"
        self.assertEqual(textutils.detect_parking(t), "included")
        self.assertIsNone(textutils.detect_parking_extra(t))



if __name__ == "__main__":
    unittest.main()
