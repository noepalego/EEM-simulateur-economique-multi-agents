"""Les diagnostics datent leurs mesures sur la période réellement observée."""

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import evaluate
import realism_probe
from economy.models import Company, SimulationConfig
from economy.simulation import EconomySimulation


class EvaluationDiagnosticsTests(unittest.TestCase):
    def test_no_sales_do_not_make_three_firms_a_monopoly(self):
        companies = [Company(str(i), str(i), "food", 0.0, 0.0) for i in range(3)]
        sim = SimpleNamespace(companies=companies)
        self.assertEqual(evaluate.top_share(sim, "food"), (0.0, 3))
        companies[0].previous_revenue = 60.0
        companies[1].previous_revenue = 40.0
        self.assertEqual(evaluate.top_share(sim, "food"), (0.6, 3))

    def test_window_growth_uses_distance_between_window_centres(self):
        monthly_growth = 1.01
        values = [100 * monthly_growth ** i for i in range(300)]
        expected = 100 * (monthly_growth ** 12 - 1)
        self.assertAlmostEqual(evaluate.annualized_window_growth(values), expected)
        self.assertAlmostEqual(evaluate.annualized_window_growth(values, start=240), expected)

    def test_growth_is_unavailable_without_two_dated_windows(self):
        for months in (1, 12, 24, 240, 252, 264):
            with self.subTest(months=months):
                self.assertTrue(math.isnan(evaluate.annualized_window_growth([100] * months, start=240)))
        self.assertTrue(math.isnan(evaluate.annualized_change(0, 100, 12)))
        self.assertTrue(math.isnan(evaluate.annualized_change(100, 100, 0)))

    def test_evaluation_excludes_warmup_prices_and_preexisting_startups(self):
        config = SimulationConfig(population_size=20, warmup_months=3, seed=11)
        sim = EconomySimulation(config)
        initial_cpi = sim.price_index
        # Une ancienne société fermée reste dans le registre après la chauffe.
        # Elle ne doit pas être présentée comme une entrée du premier mois.
        old = Company("startup_before_observation", "Ancienne", "food", 0.0, 0.0)
        old.active = False
        sim.companies.append(old)
        with patch.object(evaluate, "EconomySimulation", return_value=sim):
            result = evaluate.run((11, 1, {}))
        self.assertAlmostEqual(result["infl_avg"],
                               evaluate.annualized_change(initial_cpi, sim.price_index, 1))
        self.assertNotIn("food", result["entry_by_sector"])
        self.assertTrue(math.isnan(result["growth"][1]))
        self.assertEqual(len(result["real_wage"]), 3)

    def test_probe_accepts_one_month_without_warmup(self):
        result = realism_probe.run((11, 1, {"population_size": 20, "warmup_months": 0}, "test", None))
        self.assertEqual(result["months"], 1)
        self.assertTrue(math.isfinite(result["infl"]))
        self.assertTrue(math.isnan(result["growth"]))
        self.assertTrue(math.isnan(result["infl_last10y"]))
        self.assertTrue(math.isnan(result["saving_rate_W"]))
        self.assertTrue(math.isnan(result["sector"]["food"]["hhi"]))

    def test_probe_roe_uses_actual_number_of_observed_months(self):
        sim = realism_probe.ProbeSimulation(SimulationConfig(population_size=20, warmup_months=0, seed=11))
        initial_cpi = sim.price_index
        sim.step()
        row = sim._probe_rows[0]
        row["sec"]["food"]["rev"] = 100.0
        row["sec"]["food"]["prof"] = 10.0
        row["sec"]["food"]["eq"] = 100.0
        row["b_loss_cum"] = 5.0
        result = realism_probe.summarize({"seed": 11, "warmup": 0, "rows": sim._probe_rows,
                                         "initial_cpi": initial_cpi, "initial_bank_losses": 0.0,
                                         "exits": [], "erp": sim.config.equity_risk_premium})
        self.assertEqual(result["sector"]["food"]["roe"], 120.0)
        self.assertGreater(result["bank_loss_gdp"], 0.0)

    def test_empty_observation_is_rejected_explicitly(self):
        with self.assertRaisesRegex(ValueError, "au moins un mois"):
            evaluate.run((11, 0, {}))
        with self.assertRaisesRegex(ValueError, "au moins un mois"):
            realism_probe.run((11, 0, {}, "test", None))


if __name__ == "__main__":
    unittest.main()
