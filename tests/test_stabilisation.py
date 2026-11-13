"""Lot U (12 novembre 2026) : impôt borné par la trésorerie, titres publics pondérés dans les
fonds propres exigés, embauche tirée par la demande non servie, encadrement des prix levé en cas
de pénurie. Neutres à leur valeur par défaut ; mesures dans ``results/chantiers_20261112``."""

import dataclasses
import os
import re
import unittest

from economy.labor import demand_led_hires, monthly_staff_bounds, unserved_with_margin
from economy.models import Company, MarketRecord, SimulationConfig
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up
SWITCHES = ("tax_capped_by_cash", "demand_led_hiring", "shortage_price_release")
ADDED = {*SWITCHES, "sovereign_risk_weight", "demand_hiring_share", "price_control_shortage_limit",
         "tax_backlog_months"}
ENABLED = {"tax_capped_by_cash": True, "demand_led_hiring": True, "shortage_price_release": True,
           "sovereign_risk_weight": 0.0}


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 1, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def producer(**overrides) -> Company:
    values = dict(price=100.0, unit_input_cost=40.0, staffing_need=50, employees=["e1", "e2"])
    values.update(overrides)
    return Company("g", "Producteur", "goods", 10_000.0, 10.0, **values)


class StabilisationRegistryTests(unittest.TestCase):
    """Chaque réglage du lot U est listé et justifié dans AUTONOMIE.md, existe dans
    ``SimulationConfig`` et y est neutre pour l'identité."""

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot U")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        registry = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), ADDED)
        for name in ADDED:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)
        for name in SWITCHES:
            self.assertIs(fields[name].default, False, name)
        self.assertEqual(fields["sovereign_risk_weight"].default, 1.0)

    def test_disabled_is_identical(self) -> None:
        """Les curseurs seuls ne changent rien tant que leur case est décochée."""
        reference = simulation()
        explicit = simulation(**{name: False for name in SWITCHES}, sovereign_risk_weight=1.0,
                              demand_hiring_share=0.9, price_control_shortage_limit=0.01)
        for _ in range(6):
            self.assertEqual(reference.step(), explicit.step())

    def test_enabled_keeps_the_accounts(self) -> None:
        sim = simulation(0, **ENABLED)
        for _ in range(24):
            record = sim.step()
            self.assertLess(abs(record.ledger_residual), 1e-3)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertLess(record.public_debt_reconciliation_error, 1e-3)
        self.assertTrue(all(person.cash >= -1e-6 for person in sim.people))


class TaxCapTests(unittest.TestCase):

    def collect(self, capped: bool) -> tuple[EconomySimulation, float]:
        sim = simulation(3, tax_capped_by_cash=capped)
        person = sim.people[0]
        person.cash, person.income_this_turn, person.capital_income_last_month = 100.0, 0.0, 0.0
        sim.institutions.tax_backlog[person.id] = 5_000.0
        sim.live = sim.config
        sim._collect_assessed_taxes(1.0)
        return sim, person.cash

    def test_backlog_is_called_in_instalments(self) -> None:
        """Avec l'étalement, un mois n'appelle qu'un N-ième de l'arriéré ; rien n'est effacé."""
        sim = simulation(3, tax_backlog_months=24)
        person = sim.people[0]
        person.cash, person.income_this_turn, person.capital_income_last_month = 10_000.0, 0.0, 0.0
        sim.institutions.tax_backlog[person.id] = 4_800.0
        sim.live = sim.config
        sim._collect_assessed_taxes(1.0)
        self.assertAlmostEqual(person.taxes_paid, 200.0)
        self.assertAlmostEqual(person.cash, 9_800.0)
        self.assertAlmostEqual(sim.institutions.tax_backlog[person.id], 4_600.0)

    def test_historic_rule_can_overdraw_a_household(self) -> None:
        _, cash = self.collect(False)
        self.assertAlmostEqual(cash, -4_900.0)

    def test_capped_tax_leaves_the_balance_due(self) -> None:
        sim, cash = self.collect(True)
        person = sim.people[0]
        self.assertEqual(cash, 0.0)
        self.assertEqual(person.taxes_paid, 100.0)
        self.assertAlmostEqual(sim.institutions.tax_backlog[person.id], 4_900.0)


class SovereignWeightTests(unittest.TestCase):

    def test_bonds_stop_consuming_bank_capital(self) -> None:
        sim = simulation(1)
        bank = next(c for c in sim.companies if c.active and c.sector == "bank")
        bank.bond_holdings += 1_000_000.0
        sim.live = dataclasses.replace(sim.config)
        with_bonds = sim._bank_capacity(bank)
        sim.live.sovereign_risk_weight = 0.0
        without = sim._bank_capacity(bank)
        sim.live.sovereign_risk_weight = 0.5
        half = sim._bank_capacity(bank)
        self.assertGreaterEqual(without, half)
        self.assertGreaterEqual(half, with_bonds)
        if without > 0.0:
            self.assertGreater(without, with_bonds)


class DemandLedHiringTests(unittest.TestCase):

    def test_cap_follows_the_staffing_gap(self) -> None:
        off, on = SimulationConfig(), SimulationConfig(demand_led_hiring=True, demand_hiring_share=0.25)
        hires, _ = monthly_staff_bounds(2, off)
        company = producer()
        self.assertEqual(demand_led_hires(company, 50, 2, hires, off), hires)
        self.assertEqual(demand_led_hires(company, 50, 2, hires, on), 12)
        # jamais sous la borne fixe, et rien pour un autre secteur
        self.assertEqual(demand_led_hires(company, 6, 2, hires, on), hires)
        services = Company("s", "Services", "services", 0.0, 10.0)
        self.assertEqual(demand_led_hires(services, 50, 2, hires, on), hires)

    def test_loss_only_stops_blocking_with_a_unit_margin(self) -> None:
        on = SimulationConfig(demand_led_hiring=True)
        self.assertTrue(unserved_with_margin(producer(), 2, on))
        self.assertFalse(unserved_with_margin(producer(), 2, SimulationConfig()))
        self.assertFalse(unserved_with_margin(producer(price=30.0), 2, on))       # vend sous ses intrants
        self.assertFalse(unserved_with_margin(producer(staffing_need=2), 2, on))  # aucun poste manquant
        self.assertFalse(unserved_with_margin(producer(unit_input_cost=0.0), 2, on))


class ShortagePriceReleaseTests(unittest.TestCase):

    def record(self, unmet: float, sold: float) -> MarketRecord:
        return MarketRecord(month=1, food_price=1.0, average_wage=1.0, food_sold=sold, unemployment=0.0,
                            housing_rent=1.0, total_revenue=0.0, total_profit=0.0, event="",
                            unmet_food_demand=unmet, goods_sold=100.0, unmet_goods_demand=0.0)

    def test_cap_is_lifted_only_for_the_product_in_shortage(self) -> None:
        sim = simulation(1, shortage_price_release=True, price_control_shortage_limit=0.20)
        sim.live = sim.config
        sim.history.append(self.record(unmet=50.0, sold=50.0))
        self.assertIsNone(sim._sector_price_cap("food", 0.01))
        self.assertEqual(sim._sector_price_cap("goods", 0.01), 0.01)
        sim.history.append(self.record(unmet=10.0, sold=90.0))
        self.assertEqual(sim._sector_price_cap("food", 0.01), 0.01)
        self.assertIsNone(sim._sector_price_cap("food", None))

    def test_without_the_setting_the_cap_is_kept(self) -> None:
        sim = simulation(1)
        sim.live = sim.config
        sim.history.append(self.record(unmet=50.0, sold=50.0))
        self.assertEqual(sim._sector_price_cap("food", 0.01), 0.01)


class DepositHaircutTests(unittest.TestCase):

    def test_hole_larger_than_deposits_stays_a_hole(self) -> None:
        """Dernière banque : quand les déposants n'ont pas de quoi combler le trou, le reste demeure
        un trou de fonds propres au lieu d'être effacé (ce qui créait de la monnaie)."""
        sim = simulation(2)
        sim.live = sim.config
        banks = [c for c in sim.companies if c.active and c.sector == "bank"]
        bank = banks[0]
        holders = [p for p in sim.people if p.cash > 0.0] + [
            c for c in sim.companies if c.sector != "bank" and c.cash > 0.0]
        deposits = sum(h.cash for h in holders)
        taken = sim._haircut_deposits(deposits * 3.0, None)
        self.assertAlmostEqual(taken, deposits, delta=1e-6 * deposits)
        self.assertLess(taken, deposits * 3.0)
        self.assertTrue(all(h.cash <= 1e-6 for h in holders))
        self.assertIsNotNone(bank)


if __name__ == "__main__":
    unittest.main()
