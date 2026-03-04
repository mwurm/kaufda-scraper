import unittest
from kaufda import extract_base_unit, extract_price_of_base_unit

class TestExtractBaseUnit(unittest.TestCase):

    def test_3er_match_multiplier_grams(self):
        # "4 x 125g" -> 4 * 125g = 500g = 0.5kg
        result = extract_base_unit("4 x 125g")
        self.assertEqual(result, (0.5, "kg"))

    def test_3er_match_multiplier_milliliters(self):
        # "2 x 500ml" -> 2 * 500ml = 1000ml = 1.0l
        result = extract_base_unit("2 x 500ml")
        self.assertEqual(result, (1.0, "l"))

    def test_2er_match_simple_grams(self):
        # "100g" -> 100g = 0.1kg
        result = extract_base_unit("100g")
        self.assertEqual(result, (0.1, "kg"))

    def test_2er_match_simple_kilograms(self):
        # "2kg" -> 2.0kg
        result = extract_base_unit("2kg")
        self.assertEqual(result, (2.0, "kg"))

    def test_2er_match_simple_liters(self):
        # "1l" -> 1.0l
        result = extract_base_unit("1l")
        self.assertEqual(result, (1.0, "l"))

    def test_2er_match_dash_grams(self):
        # "100-g-Pckg" -> 100g = 0.1kg
        result = extract_base_unit("100-g-Pckg")
        self.assertEqual(result, (0.1, "kg"))

    def test_1er_match_per_kg(self):
        # "15.23 / kg" -> 1.0kg
        result = extract_base_unit("15.23 / kg")
        self.assertEqual(result, (1.0, "kg"))

    def test_1er_match_kg_equals(self):
        # "1 kg = ..." -> 1.0kg
        result = extract_base_unit("1 kg = 46.13")
        self.assertEqual(result, (1.0, "kg"))

    def test_1er_match_pro_kg(self):
        # "pro kg" -> 1.0kg
        result = extract_base_unit("pro kg")
        self.assertEqual(result, (1.0, "kg"))

    def test_1er_match_je_liter(self):
        # "je liter" -> 1.0l
        result = extract_base_unit("je liter")
        self.assertEqual(result, (1.0, "liter"))

    def test_no_match(self):
        # Kein Match -> (None, None)
        result = extract_base_unit("keine Einheit hier")
        self.assertEqual(result, (None, None))

    def test_case_insensitive(self):
        # Groß-/Kleinschreibung ignorieren
        result = extract_base_unit("4 X 125g")
        self.assertEqual(result, (0.5, "kg"))

    def test_mixed_units(self):
        # Mehrere Einheiten, aber erster Match zählt
        result = extract_base_unit("100g und 1kg")
        self.assertEqual(result, (0.1, "kg"))

    def test_range(self):
        # Mehrere Einheiten, aber erster Match zählt
        result = extract_base_unit("1kg = 11,63–6,20")
        self.assertEqual(result, (1.0, "kg"))


class TestExtractPriceOfBaseUnit(unittest.TestCase):

    def test_per_kg(self):
        # "15.23 / kg" -> 15.23
        result = extract_price_of_base_unit("15.23 / kg")
        self.assertEqual(result, 15.23)

    def test_kg_equals(self):
        # "1 kg = 15.23" -> 15.23
        result = extract_price_of_base_unit("1 kg = 15.23")
        self.assertEqual(result, 15.23)

    def test_comma_separator(self):
        # "15,23 / kg" -> 15.23
        result = extract_price_of_base_unit("15,23 / kg")
        self.assertEqual(result, 15.23)

    def test_with_eur(self):
        # "15.23 EUR / kg" -> 15.23
        result = extract_price_of_base_unit("15.23 EUR / kg")
        self.assertEqual(result, 15.23)

    def test_with_euro_symbol(self):
        # "15.23 € / kg" -> 15.23
        result = extract_price_of_base_unit("15.23 € / kg")
        self.assertEqual(result, 15.23)

    def test_dash_price(self):
        # "10,- EUR / kg" -> 10.00
        result = extract_price_of_base_unit("10,- EUR / kg")
        self.assertEqual(result, 10.00)

    def test_dot_dash_price(self):
        # "10.- EUR / kg" -> 10.00
        result = extract_price_of_base_unit("10.- EUR / kg")
        self.assertEqual(result, 10.00)

    def test_ml_unit(self):
        # "5.50 / ml" -> 5.50
        result = extract_price_of_base_unit("5.50 / ml")
        self.assertEqual(result, 5.50)

    def test_l_equals(self):
        # "1 l = 2.99" -> 2.99
        result = extract_price_of_base_unit("1 l = 2.99")
        self.assertEqual(result, 2.99)

    def test_no_match(self):
        # Kein Preis gefunden -> None
        result = extract_price_of_base_unit("kein Preis hier")
        self.assertIsNone(result)

    def test_invalid_number(self):
        # Ungültige Zahl -> None
        result = extract_price_of_base_unit("abc / kg")
        self.assertIsNone(result)

    def test_case_insensitive(self):
        # Groß-/Kleinschreibung ignorieren
        result = extract_price_of_base_unit("15.23 / KG")
        self.assertEqual(result, 15.23)

    def test_multiple_matches(self):
        # Mehrere Matches, aber erster zählt
        result = extract_price_of_base_unit("15.23 / kg und 20.00 / l")
        self.assertEqual(result, 15.23)

    def test_range(self):
        # Mehrere Einheiten, aber erster Match zählt
        result = extract_price_of_base_unit("1kg = 11,63–6,20")
        self.assertEqual(result, 6.2)


if __name__ == '__main__':
    unittest.main()
