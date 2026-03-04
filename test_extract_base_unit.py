import unittest
from kaufda import extract_base_unit

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


if __name__ == '__main__':
    unittest.main()
