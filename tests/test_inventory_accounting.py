"""Le PIB mesure les variations physiques des stocks, sans gains de détention."""

import unittest
from unittest.mock import patch

from economy.models import SimulationConfig
from economy.records import MonthFlows, build_record
from economy.simulation import EconomySimulation


class InventoryAccountingTests(unittest.TestCase):
    def setUp(self):
        self.sim = EconomySimulation(SimulationConfig(
            seed=73, population_size=40, warmup_months=0))
        self.food = next(c for c in self.sim.companies if c.sector == "food")
        self.goods = next(c for c in self.sim.companies if c.sector == "goods")
        self.food.inventory = 100.0
        self.goods.goods_inventory = 50.0
        self.sim._previous_stocks = ((100.0, 50.0), 8600.0)

    def test_repricing_existing_inventory_is_not_production(self):
        before = build_record(self.sim, MonthFlows(), 0.0)
        self.sim.resources.input_cost_index = 3.0
        after = build_record(self.sim, MonthFlows(), 0.0)
        self.assertEqual(after.inventory_change, 0.0)
        self.assertEqual(after.nominal_gdp, before.nominal_gdp)
        self.assertEqual(after.real_gdp, before.real_gdp)

    def test_inventory_flows_use_current_costs_and_base_costs(self):
        self.sim.resources.input_cost_index = 3.0
        self.food.inventory += 10.0
        self.goods.goods_inventory -= 5.0
        result = build_record(self.sim, MonthFlows(), 0.0)
        expected_real = 10.0 * 65.0 - 5.0 * 42.0
        self.assertEqual(result.inventory_change, expected_real * 3.0)
        self.food.inventory = 100.0
        self.goods.goods_inventory = 50.0
        unchanged = build_record(self.sim, MonthFlows(), 0.0)
        self.assertEqual(result.nominal_gdp - unchanged.nominal_gdp, expected_real * 3.0)
        self.assertEqual(result.real_gdp - unchanged.real_gdp, expected_real)

    def test_building_a_record_does_not_consume_inventory_flows(self):
        self.food.inventory += 10.0
        previous = self.sim._previous_stocks
        first = build_record(self.sim, MonthFlows(), 0.0)
        second = build_record(self.sim, MonthFlows(), 0.0)
        self.assertEqual(first, second)
        self.assertEqual(self.sim._previous_stocks, previous)

    def test_first_month_counts_new_inventory(self):
        sim = EconomySimulation(SimulationConfig(
            seed=73, population_size=40, warmup_months=0))
        food = next(c for c in sim.companies if c.sector == "food")
        consume = sim._consumer_markets

        def unsold_output(flows):
            consume(flows)
            food.inventory += 10.0

        with patch.object(sim, "_consumer_markets", side_effect=unsold_output):
            result = sim.step()
        expected = (sum(c.inventory for c in sim.companies if c.active) * sim.live.food_input_cost
                    + sum(c.goods_inventory for c in sim.companies if c.active) * sim.live.goods_input_cost)
        self.assertGreater(expected, 0.0)
        self.assertEqual(result.inventory_change, expected * sim.resources.input_cost_index)
        # Une consultation après la clôture n'enregistre pas une seconde entrée en stock.
        self.assertEqual(build_record(sim, MonthFlows(), 0.0).inventory_change, 0.0)


if __name__ == "__main__":
    unittest.main()
