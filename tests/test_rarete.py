"""Rareté des matières premières (option ``raw_material_scarcity``, 12 novembre 2026).

Sans l'option, l'exploration et le prix plancher des matières sont ceux d'avant (identité
contrôlée par ``identity_check.py``). Avec, le prix plancher reçoit une rente de rareté et
l'exploration croît avec la part du stock de départ consommée.
"""

import unittest

from economy.goods import exploration_rate, raw_material_scarcity, scarcity_rent_factor
from economy.models import Resources, SimulationConfig
from economy.simulation import EconomySimulation


def _resources(stock: float) -> Resources:
    return Resources(raw_materials=stock, land_units=0, knowledge=100.0)


class ScarcityFunctionsTests(unittest.TestCase):
    def test_scarcity_is_the_consumed_share_of_the_starting_stock(self):
        config = SimulationConfig(raw_materials=1000.0)
        self.assertEqual(raw_material_scarcity(_resources(1000.0), config), 0.0)
        self.assertEqual(raw_material_scarcity(_resources(1500.0), config), 0.0)
        self.assertAlmostEqual(raw_material_scarcity(_resources(250.0), config), 0.75)
        self.assertEqual(raw_material_scarcity(_resources(0.0), config), 1.0)

    def test_without_the_option_exploration_is_unchanged(self):
        config = SimulationConfig(raw_materials=1000.0, reserve_replacement_rate=0.95)
        self.assertEqual(exploration_rate(_resources(100.0), config), 0.95)

    def test_exploration_grows_with_scarcity(self):
        config = SimulationConfig(raw_materials=1000.0, reserve_replacement_rate=0.95,
                                  raw_material_scarcity=True, scarcity_exploration_response=0.5)
        self.assertEqual(exploration_rate(_resources(1000.0), config), 0.95)
        self.assertAlmostEqual(exploration_rate(_resources(500.0), config), 1.2)

    def test_rent_grows_with_scarcity_and_stays_finite(self):
        config = SimulationConfig(raw_materials=1000.0, raw_material_scarcity=True, scarcity_rent=0.5)
        self.assertEqual(scarcity_rent_factor(_resources(1000.0), config), 1.0)
        self.assertAlmostEqual(scarcity_rent_factor(_resources(500.0), config), 1.5)
        self.assertAlmostEqual(scarcity_rent_factor(_resources(0.0), config), 11.0)


class ScarcitySimulationTests(unittest.TestCase):
    """Même graine, stock vierge ramené à 30 % du départ : l'option relève le prix des matières
    et reconstitue le stock plus vite."""

    def _run(self, scarcity: bool):
        sim = EconomySimulation(SimulationConfig(seed=5, warmup_months=0, raw_material_scarcity=scarcity))
        sim.resources.raw_materials = 0.3 * sim.config.raw_materials
        prices = []
        for _ in range(24):
            sim.step()
            prices.append(sim.resources.raw_material_price)
        return sim, max(prices)

    def test_option_raises_price_and_rebuilds_stock(self):
        without, price_without = self._run(False)
        with_option, price_with = self._run(True)
        self.assertGreater(price_with, price_without)
        self.assertGreater(with_option.resources.raw_materials, without.resources.raw_materials)


class ScarcityConstructionTests(unittest.TestCase):
    """Les matériaux des chantiers (``deliveries.deliver_capital_work``) sont remplacés par
    l'exploration avec l'option, sans création ni perte de monnaie entre entreprises."""

    def _deliver(self, scarcity: bool):
        from economy.deliveries import deliver_capital_work

        sim = EconomySimulation(SimulationConfig(seed=5, warmup_months=0, raw_material_scarcity=scarcity))
        for _ in range(6):  # les extracteurs embauchent (aucun écart de stock à 100 % du départ)
            sim.step()
        for company in sim.companies:
            company.materials_supplied_this_turn = 0.0
        res = sim.resources
        res.raw_materials = 0.5 * sim.config.raw_materials
        res.recycled_materials = 0.0
        for company in sim.companies:
            if company.sector == "services":
                company.construction_work_available = 1e6
        buyer = next(c for c in sim.companies if c.active and c.sector == "food")
        buyer.cash = 1e7
        money_before = sum(c.cash for c in sim.companies) + res.market_cash
        stock_before = res.raw_materials
        delivered, _ = deliver_capital_work(buyer, 20000.0, 0.5, sim.companies, res, sim.live)
        money_after = sum(c.cash for c in sim.companies) + res.market_cash
        return delivered, res.raw_materials - stock_before, money_after - money_before

    def test_construction_materials_are_explored_with_the_option(self):
        delivered_off, change_off, money_off = self._deliver(False)
        delivered_on, change_on, money_on = self._deliver(True)
        self.assertGreater(delivered_off, 0.0)
        self.assertEqual(delivered_on, delivered_off)
        self.assertLess(change_off, 0.0)
        # rareté ≈ 0,5 (mesurée après le prélèvement) : l'exploration remplace ≈ 0,95 + 0,5 × 0,5
        # = 1,2 fois les volumes prélevés, le stock remonte d'environ 20 % du prélèvement
        self.assertAlmostEqual(change_on / -change_off, 0.2, delta=0.01)
        self.assertAlmostEqual(money_off, 0.0, places=4)
        self.assertAlmostEqual(money_on, 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
