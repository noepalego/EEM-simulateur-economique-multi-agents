"""Lot A (novembre 2026) : lectures pour l'interface, sans effet sur la simulation.

A2 : classes adaptatives de la répartition des fortunes (``economy.wealth.wealth_distribution``).
"""

import random
import unittest

from economy.wealth import money_label, wealth_distribution


def _counts(rows):
    return [row["Nombre de ménages"] for row in rows]


class WealthDistributionTests(unittest.TestCase):
    def test_every_household_is_counted_once(self):
        generator = random.Random(7)
        samples = [
            [generator.lognormvariate(10.0, 1.5) for _ in range(500)],
            [-3000.0, -10.0, 0.0, 0.0, 12.0, 4.0e5, 7.0e8],
            [5000.0 + i for i in range(100)],
            [250.0] * 40,
            [0.0, 3.0, 1.0e12],
        ]
        for fortunes in samples:
            rows = wealth_distribution(fortunes)
            self.assertEqual(sum(_counts(rows)), len(fortunes))
            self.assertLessEqual(len(rows), 10 + 2)  # classes positives + négative + nulle

    def test_empty_population(self):
        self.assertEqual(wealth_distribution([]), [])

    def test_negative_and_null_classes_come_first(self):
        rows = wealth_distribution([-500.0, 0.0, 0.0, 120.0, 9000.0])
        self.assertTrue(rows[0]["Tranche de fortune"].startswith("Négative"))
        self.assertEqual(rows[0]["Nombre de ménages"], 1)
        self.assertEqual(rows[1]["Tranche de fortune"], "Nulle (0 €)")
        self.assertEqual(rows[1]["Nombre de ménages"], 2)

    def test_bounds_follow_the_scale_of_the_economy(self):
        """Économie pauvre ou riche : les ménages se répartissent sur plusieurs classes au
        lieu de tomber dans une ou deux (ancien défaut des tranches fixes 0–250 k€)."""
        generator = random.Random(3)
        for scale in (50.0, 2.0e4, 5.0e7):
            fortunes = [scale * generator.lognormvariate(0.0, 1.2) for _ in range(400)]
            rows = wealth_distribution(fortunes)
            largest = max(_counts(rows))
            self.assertLess(largest / len(fortunes), 0.5)
            self.assertGreaterEqual(sum(count > 0 for count in _counts(rows)), 4)
            self.assertLessEqual(rows[0]["Borne basse"], min(fortunes))
            self.assertGreater(rows[-1]["Borne haute"], max(fortunes))

    def test_narrow_range_uses_distinct_regular_bounds(self):
        rows = wealth_distribution([5000.0 + i for i in range(100)])
        labels = [row["Tranche de fortune"] for row in rows]
        self.assertEqual(len(labels), len(set(labels)))
        steps = {round(row["Borne haute"] - row["Borne basse"], 6) for row in rows}
        self.assertEqual(len(steps), 1)

    def test_money_labels(self):
        self.assertEqual(money_label(950.0), "950")
        self.assertEqual(money_label(1500.0), "1,5 k")
        self.assertEqual(money_label(20000.0), "20 k")
        self.assertEqual(money_label(1.2e6), "1,2 M")
        self.assertEqual(money_label(3.0e9), "3 Md")
        self.assertEqual(money_label(-2500.0), "-2,5 k")
        self.assertEqual(money_label(5020.0, resolution=20.0), "5,02 k")


class ResearchEfficiencySliderTests(unittest.TestCase):
    """Curseur « Efficacité de la recherche » (12 novembre 2026) : sa valeur de départ est celle de
    ``SimulationConfig``, et elle est transmise à la configuration (trajectoire de base inchangée)."""

    def test_default_matches_config_and_is_passed(self):
        import re
        from pathlib import Path

        from economy.models import SimulationConfig

        source = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
        slider = re.search(
            r'"Efficacité de la recherche",\s*min_value=[^,]+, max_value=[^,]+, value=([0-9.]+),'
            r'[^)]*key="knowledge_growth_per_intensity"',
            source,
        )
        self.assertIsNotNone(slider)
        self.assertEqual(float(slider.group(1)), SimulationConfig().knowledge_growth_per_intensity)
        self.assertIn(
            "knowledge_growth_per_intensity=st.session_state.knowledge_growth_per_intensity", source
        )

    def test_all_on_preset_covers_research_and_scarcity(self):
        """« Tout activer » coche la rareté des matières et porte la recherche à 0,5 ;
        « Tout désactiver » rétablit la valeur de ``SimulationConfig``."""
        import ast
        from pathlib import Path

        from economy.models import SimulationConfig

        tree = ast.parse((Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8"))
        literals = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("OPTIONAL_MECHANISMS", "OPTIONAL_SETTINGS")
        }
        self.assertIs(literals["OPTIONAL_MECHANISMS"]["raw_material_scarcity"], True)
        on_value, base_value = literals["OPTIONAL_SETTINGS"]["knowledge_growth_per_intensity"]
        self.assertEqual(on_value, 0.5)
        self.assertEqual(base_value, SimulationConfig().knowledge_growth_per_intensity)
        for key in literals["OPTIONAL_MECHANISMS"]:
            self.assertTrue(hasattr(SimulationConfig(), key), key)


if __name__ == "__main__":
    unittest.main()
