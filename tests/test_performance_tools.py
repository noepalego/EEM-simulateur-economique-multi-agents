"""Identité des empreintes en flux et des calculs réutilisés pendant un marché."""
import dataclasses
import hashlib
import random
import unittest

from benchmark_simulation import summary
from economy.goods import _SalesWeights, _allocate_sales
from economy.models import Company, LoanAccounts, SimulationConfig
from economy.simulation import EconomySimulation
from performance_probe import digest, exact_state


class PerformanceToolsTests(unittest.TestCase):
    def assert_digest(self, value):
        self.assertEqual(digest(value), hashlib.sha256(repr(exact_state(value)).encode()).hexdigest())

    def test_streaming_digest_preserves_format_and_chunk_boundaries(self):
        generator = random.Random(3)
        generator.gauss(0, 1)
        loans = LoanAccounts({"échéance": 0.1, "autre": -0.0})
        loans.positive_book()
        self.assert_digest([generator, loans, (True, None, "\"é\\\n"), {1, "a", 0.1},
                            frozenset(), float("inf"), float("nan"),
                            [{str(i): [i / 3, -0.0, i]} for i in range(10000)]])

    def test_neutral_dataclass_fields(self):
        @dataclasses.dataclass
        class Example:
            ordinary: int = 0
            neutral: list = dataclasses.field(default_factory=list, metadata={"identity": "neutre"})
        self.assert_digest(Example())
        self.assert_digest(Example(neutral=[1]))

    def test_complete_simulation(self):
        sim = EconomySimulation(SimulationConfig(population_size=20, warmup_months=0, seed=31))
        for _ in range(3):
            sim.step()
        self.assert_digest(sim)

    def test_statistics_expose_spread_not_only_the_fastest_run(self):
        self.assertEqual(summary([1.0, 2.0, 8.0]),
                         {"median": 2.0, "min": 1.0, "max": 8.0, "mad": 1.0})


class MarketWeightsReuseTests(unittest.TestCase):
    def test_submarkets_keep_original_household_arithmetic_and_order(self):
        firms = [Company(str(i), str(i), "food", 100.0, 10.0,
                         price=2.0 + i, quality_trait=0.7 + i * 0.3) for i in range(4)]
        demands = {"p2": 0.01, "p0": 123456.78, "p1": 42.0}
        preferences = {"p2": 1.7, "p0": 0.9, "p1": 0.1}
        affinity = {"p0": {"0": 0.3, "2": 1.5}}
        prepared = _SalesWeights(firms, 1.2, 3.5, demands, preferences, 0.8, affinity)
        # Oracle : formule historique, recalculée indépendamment pour chaque sous-marché.
        for subset in (firms, firms[1:], firms[::2], firms[::-1], []):
            expected = {c.id: 0.0 for c in subset}
            for pid, demand in demands.items():
                values = [(3.5 * max(0.5, c.quality_trait) ** (preferences[pid] * 0.8)
                           / max(c.price, 0.01)) ** 1.2 for c in subset]
                if affinity.get(pid):
                    values = [v * affinity[pid].get(c.id, 1.0) for c, v in zip(subset, values)]
                total = sum(values)
                if total > 0.0:
                    for c, v in zip(subset, values):
                        expected[c.id] += demand * v / total
            self.assertEqual(prepared(subset), expected)
            self.assertEqual(list(prepared(subset)), list(expected))

    def test_rationing_still_exhausts_small_stocks_before_other_firms(self):
        firms = [Company(str(i), str(i), "food", 100.0, 10.0,
                         price=1.0, inventory=inventory) for i, inventory in enumerate((1.0, 2.0, 100.0))]
        self.assertEqual(_allocate_sales(firms, 30.0, "inventory"),
                         {"0": 1.0, "1": 2.0, "2": 27.0})
