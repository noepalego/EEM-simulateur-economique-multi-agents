"""Contraintes physiques et financement des livraisons de matières premières."""

import unittest

from economy.deliveries import deliver_capital_work
from economy.goods import produce_consumer_goods, produce_food
from economy.intersector import household_suppliers, pay_suppliers_from_household
from economy.models import Company, Person, Resources, SimulationConfig


class MaterialDeliveryConstraintsTests(unittest.TestCase):
    def _production(self, sector, recycled=0.0, extractor=False, services=False,
                    exploration_cost=2.0, supplier_cash=1000.0):
        config = SimulationConfig(
            food_productivity=10.0, goods_productivity=10.0,
            raw_material_extraction_productivity=10.0,
            raw_material_unit_price=8.0, raw_material_extraction_cost=0.0,
            reserve_replacement_rate=1.0, exploration_cost_per_unit=exploration_cost,
            target_inventory_months=1.0, goods_material_per_unit=1.0,
            input_cost_split={"services": 1.0},
        )
        buyer = Company("buyer", "Producteur", sector, 10000.0, 10.0,
                        employees=["w"], demand_forecast=10.0)
        companies = [buyer]
        if extractor:
            companies.append(Company("miner", "Extracteur", "materials", supplier_cash,
                                     10.0, employees=["m"]))
        if services:
            companies.append(Company("crew", "Prestataire", "services", 0.0,
                                     10.0, employees=["s"]))
        resources = Resources(raw_materials=100.0, land_units=0, knowledge=0.0,
                              raw_material_price=8.0, recycled_materials=recycled)
        operation = produce_food if sector == "food" else produce_consumer_goods
        before = sum(c.cash for c in companies)
        produced = operation(companies, resources, config)
        self.assertAlmostEqual(sum(c.cash for c in companies), before)
        return produced, resources, companies

    def test_virgin_materials_need_an_active_extractor(self):
        for sector in ("food", "goods"):
            with self.subTest(sector=sector):
                produced, resources, _ = self._production(sector)
                self.assertEqual(produced, 0.0)
                self.assertEqual(resources.raw_materials, 100.0)

    def test_recycled_materials_remain_available_without_an_extractor(self):
        produced, resources, _ = self._production("food", recycled=4.0)
        self.assertEqual(produced, 4.0)
        self.assertEqual(resources.raw_materials, 100.0)
        self.assertEqual(resources.recycled_materials, 0.0)

    def test_exploration_requires_a_service_provider(self):
        produced, resources, _ = self._production("food", extractor=True)
        self.assertEqual(produced, 10.0)
        self.assertEqual(resources.raw_materials, 90.0)

    def test_exploration_is_limited_to_the_amount_paid(self):
        # L'extracteur reçoit 80 pour ses matières : il ne peut financer que
        # 0,8 unité d'exploration à 100 par unité.
        produced, resources, companies = self._production(
            "food", extractor=True, services=True, exploration_cost=100.0,
            supplier_cash=0.0,
        )
        self.assertEqual(produced, 10.0)
        self.assertAlmostEqual(resources.raw_materials, 90.8)
        self.assertAlmostEqual(companies[1].cash, 0.0)

    def test_fully_paid_exploration_replenishes_extraction(self):
        produced, resources, _ = self._production("food", extractor=True, services=True)
        self.assertEqual(produced, 10.0)
        self.assertEqual(resources.raw_materials, 100.0)

    def test_construction_exploration_requires_service_delivery(self):
        config = SimulationConfig(raw_material_scarcity=True,
                                  reserve_replacement_rate=1.0,
                                  scarcity_exploration_response=0.0)
        buyer = Company("buyer", "Acheteur", "food", 10000.0, 10.0)
        miner = Company("miner", "Extracteur", "materials", 1000.0, 10.0,
                        employees=["m"])
        resources = Resources(raw_materials=100.0, land_units=0, knowledge=0.0,
                              raw_material_price=config.raw_material_unit_price)
        delivered, paid = deliver_capital_work(
            buyer, 120.0, 1.0, [buyer, miner], resources, config,
        )
        self.assertEqual(delivered, 120.0)
        self.assertEqual(paid, 120.0)
        self.assertEqual(resources.raw_materials, 90.0)
        self.assertEqual(buyer.cash + miner.cash, 11000.0)

    def test_construction_exploration_is_limited_to_the_amount_paid(self):
        config = SimulationConfig(raw_material_scarcity=True,
                                  reserve_replacement_rate=1.0,
                                  scarcity_exploration_response=0.0,
                                  exploration_cost_per_unit=100.0)
        buyer = Company("buyer", "Acheteur", "food", 10000.0, 10.0)
        miner = Company("miner", "Extracteur", "materials", 0.0, 10.0,
                        employees=["m"])
        crew = Company("crew", "Prestataire", "services", 0.0, 10.0,
                       employees=["s"])
        companies = [buyer, miner, crew]
        resources = Resources(raw_materials=100.0, land_units=0, knowledge=0.0,
                              raw_material_price=config.raw_material_unit_price)
        deliver_capital_work(buyer, 120.0, 1.0, companies, resources, config)
        # 120 de recettes permettent de découvrir 1,2 unité, pas les 10 voulues.
        self.assertAlmostEqual(resources.raw_materials, 91.2)
        self.assertAlmostEqual(sum(c.cash for c in companies), 10000.0)
        self.assertEqual(miner.cash, 0.0)


class EmptyHouseholdOrdersTests(unittest.TestCase):
    def test_empty_or_zero_weight_orders_leave_household_cash_unchanged(self):
        for split in ({}, {"services": 0.0}, {"services": -1.0}):
            for batched in (False, True):
                with self.subTest(split=split, batched=batched):
                    person = Person("person", "Ménage", 100.0)
                    resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
                    batch = household_suppliers([], split, split=split) if batched else None
                    paid = (batch.pay(person, 40.0, resources) if batched else
                            pay_suppliers_from_household(person, 40.0, split, [], resources))
                    self.assertEqual(paid, 0.0)
                    self.assertEqual(person.cash, 100.0)
                    self.assertEqual(person.expenses_this_turn, 0.0)
                    self.assertEqual(person.cumulative_expenses, 0.0)


if __name__ == "__main__":
    unittest.main()
