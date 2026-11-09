"""Lot D (novembre 2026) : économies d'échelle.

Remise sur le volume des achats entre entreprises (``volume_discount_max``) et frais de structure
des producteurs (``fixed_cost_share``), désactivés par défaut.
"""

import dataclasses
import unittest

from economy.deliveries import deliver_energy
from economy.firm_pricing import variable_unit_cost
from economy.goods import _pay_fixed_cost
from economy.intersector import pay_business_suppliers, volume_discount
from economy.models import Company, Resources, SimulationConfig
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def _supplier(company_id: str, sector: str = "services", margin: float = 0.2) -> Company:
    firm = Company(company_id, company_id, sector, 1000.0, 10.0, employees=["p1", "p2"])
    firm.previous_revenue = 10_000.0
    firm.previous_profit = margin * 10_000.0
    return firm


def _base(**fields) -> Resources:
    return Resources(raw_materials=1000.0, land_units=0, knowledge=0.0, **fields)


def _resources(maximum: float = 0.15, half: float = 5000.0) -> Resources:
    resources = _base()
    resources.volume_discount_max = maximum
    resources.volume_discount_half_volume = half
    return resources


class VolumeDiscountTests(unittest.TestCase):

    def test_disabled_by_default(self) -> None:
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        for name, default in (("volume_discount_max", 0.0), ("fixed_cost_share", 0.0)):
            self.assertEqual(fields[name].default, default)
            self.assertEqual(fields[name].metadata.get("identity"), "neutre")
        self.assertEqual(volume_discount(1e6, [_supplier("s")], _base()), 0.0)

    def test_discount_grows_with_volume_and_is_bounded(self) -> None:
        resources, suppliers = _resources(), [_supplier("s", margin=0.5)]
        discounts = [volume_discount(v, suppliers, resources) for v in (0.0, 100.0, 5000.0, 1e5, 1e9)]
        self.assertEqual(discounts[0], 0.0)
        self.assertEqual(discounts, sorted(discounts))
        self.assertAlmostEqual(discounts[2], 0.075)  # moitié du maximum au volume de référence
        self.assertLessEqual(discounts[-1], 0.15)

    def test_discount_never_below_supplier_cost(self) -> None:
        resources = _resources(maximum=0.5)
        self.assertAlmostEqual(volume_discount(1e9, [_supplier("s", margin=0.04)], resources), 0.04)
        self.assertEqual(volume_discount(1e9, [_supplier("s", margin=-0.1)], resources), 0.0)

    def test_payment_is_conserved_and_cheaper_for_big_orders(self) -> None:
        paid = {}
        for label, resources in (("plein", _base()), ("remise", _resources())):
            buyer = Company("b", "Acheteur", "food", 1e6, 10.0)
            supplier = _supplier("s")
            cash_before = buyer.cash + supplier.cash
            paid[label] = pay_business_suppliers(buyer, 50_000.0, {"services": 1.0}, [buyer, supplier], resources)
            self.assertAlmostEqual(buyer.cash + supplier.cash, cash_before, places=6)
            self.assertAlmostEqual(supplier.revenue, paid[label], places=6)
        self.assertLess(paid["remise"], paid["plein"])
        self.assertAlmostEqual(paid["remise"], paid["plein"] * (1.0 - volume_discount(50_000.0, [_supplier("s")], _resources())))

    def test_energy_discount_credits_what_is_returned(self) -> None:
        resources = _base(energy_unit_price=2.0)
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0)
        supplier = Company("e", "Énergie", "energy", 0.0, 10.0, energy_available=100.0)
        paid = deliver_energy(buyer, 10.0, 1000.0, [supplier, buyer], resources, discount=0.1)
        self.assertAlmostEqual(paid, 10.0 * 2.0 * 0.9)
        self.assertAlmostEqual(supplier.revenue, paid)


class FixedCostTests(unittest.TestCase):

    def test_bigger_firm_has_lower_unit_cost(self) -> None:
        small = Company("a", "Petite", "food", 0.0, 10.0, wage_bill=1000.0, potential_output=100.0,
                        unit_input_cost=5.0, fixed_cost=2000.0)
        big = Company("b", "Grande", "food", 0.0, 10.0, wage_bill=10_000.0, potential_output=1000.0,
                      unit_input_cost=5.0, fixed_cost=2000.0)
        self.assertLess(variable_unit_cost(big), variable_unit_cost(small))
        self.assertAlmostEqual(variable_unit_cost(big), 10.0 + 5.0 + 2.0)
        plain = dataclasses.replace(big, fixed_cost=0.0)
        self.assertAlmostEqual(variable_unit_cost(plain), 15.0)

    def test_fixed_cost_is_sector_average_and_idle_firms_pay_nothing(self) -> None:
        config = SimulationConfig(fixed_cost_share=0.3)
        resources = _base(input_cost_index=2.0)
        firm = Company("a", "A", "food", 1e6, 10.0, employees=["p"], previous_units_sold=100.0)
        other = Company("b", "B", "food", 1e6, 10.0, employees=["q"], previous_units_sold=300.0)
        idle = Company("c", "C", "food", 1e6, 10.0, previous_units_sold=0.0)
        supplier = _supplier("s")
        everyone = [firm, other, idle, supplier]
        cash = sum(c.cash for c in everyone)
        paid = _pay_fixed_cost(firm, [firm, other, idle], 65.0, everyone, resources, config)
        # forfait = part × coût de base × indice × ventes moyennes du secteur, payé selon
        # ``input_cost_split`` ; seule la part des services a un fournisseur ici
        split = config.input_cost_split
        services = split.get("services", 0.0) / sum(max(0.0, v) for v in split.values())
        self.assertAlmostEqual(paid, 0.3 * 65.0 * 2.0 * (400.0 / 3.0) * services)
        self.assertAlmostEqual(sum(c.cash for c in everyone), cash, places=6)
        self.assertEqual(_pay_fixed_cost(idle, [firm, other, idle], 65.0, everyone, resources, config), 0.0)

    def test_enabled_runs_keep_accounts_exact(self) -> None:
        for overrides in ({"volume_discount_max": 0.15}, {"fixed_cost_share": 0.3}):
            with self.subTest(**overrides):
                sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
                for _ in range(24):
                    sim.step()
                self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_zero_settings_are_identical(self) -> None:
        reference = EconomySimulation(SimulationConfig(seed=11, population_size=80))
        explicit = EconomySimulation(SimulationConfig(seed=11, population_size=80, volume_discount_max=0.0,
                                                      fixed_cost_share=0.0))
        for _ in range(6):
            self.assertEqual(reference.step(), explicit.step())


if __name__ == "__main__":
    unittest.main()
