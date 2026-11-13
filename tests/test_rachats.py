"""Lot T (novembre 2026) : offres publiques réalistes et croissance proportionnelle des effectifs
(``economy/consolidation.py``, ``economy/labor.py``). Désactivés par défaut ; activés, la monnaie et
les titres sont conservés."""

import dataclasses
import math
import os
import re
import unittest

from economy.consolidation import _merge_into, takeover_by_value
from economy.labor import monthly_staff_bounds
from economy.models import Company, Person, SimulationConfig
from economy.simulation import EconomySimulation
from economy.safeguard import SAFEGUARD_BRIDGE_MONTHS, safeguard_plans, serviceable_debt
from economy.stock_market import _fundamental_price

_ORIGINAL_WARM_UP = EconomySimulation._warm_up
SWITCHES = ("synergy_takeovers", "proportional_hiring", "safeguard_procedure")


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 1, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def market(productivity_gap: float = 0.3, stock_price: float = 10.0):
    """Un acquéreur riche et productif, une cible cotée sous sa valeur fondamentale, détenue par
    deux ménages prudents ; un troisième concurrent garde le secteur ouvert."""
    buyer = Company("a", "Acquéreur", "food", 5_000_000.0, 10.0, shares_outstanding=1000.0,
                    treasury_shares=0.0, stock_price=200.0, previous_stock_price=200.0,
                    productivity_trait=1.0 + productivity_gap, employees=["e1", "e2", "e3"])
    target = Company("b", "Cible", "food", 100_000.0, 10.0, shares_outstanding=1000.0,
                     treasury_shares=0.0, stock_price=stock_price, previous_stock_price=stock_price,
                     productivity_trait=1.0, employees=["e4"])
    other = Company("c", "Concurrent", "food", 1_000.0, 10.0, shares_outstanding=1000.0,
                    treasury_shares=0.0, stock_price=1.0, previous_stock_price=1.0)
    holders = [Person("h1", "Porteur 1", 0.0, shares={"b": 600.0}, risk_tolerance=0.3),
               Person("h2", "Porteur 2", 0.0, shares={"b": 400.0}, risk_tolerance=0.3),
               Person("h3", "Porteur 3", 0.0, shares={"a": 1000.0}, risk_tolerance=0.5)]
    return [buyer, target, other], holders


def holdings(companies, people, company_id):
    return sum(h.shares.get(company_id, 0.0) for h in [*companies, *people])


class TakeoverRegistryTests(unittest.TestCase):
    """Chaque réglage du lot T est listé et justifié dans la section « Réglages du lot T »
    d'AUTONOMIE.md, existe dans ``SimulationConfig`` et y est neutre pour l'identité."""

    ADDED = {*SWITCHES, "takeover_share_payment", "staff_change_share", "safeguard_debt_share"}

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot T")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        registry = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), self.ADDED)
        for name in self.ADDED:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)
        for name in SWITCHES:
            self.assertIs(fields[name].default, False, name)

    def test_disabled_is_identical(self) -> None:
        reference = simulation()
        explicit = simulation(**{name: False for name in SWITCHES}, takeover_share_payment=0.9,
                              staff_change_share=0.5, safeguard_debt_share=0.2)
        for _ in range(6):
            self.assertEqual(reference.step(), explicit.step())


class SynergyTakeoverTests(unittest.TestCase):

    def test_old_rule_fails_when_the_target_is_cheap(self) -> None:
        """Sans le réglage, l'offre (cours + 30 %) reste sous la valeur que les porteurs connaissent :
        personne n'apporte ses titres, l'offre échoue sans échange."""
        companies, people = market()
        config = SimulationConfig()
        fundamental = _fundamental_price(companies[1], companies, config, 12.0 * config.loan_interest_rate)
        self.assertGreater(fundamental, 13.0)
        completed, bids = takeover_by_value(companies, people, config, 12)
        self.assertEqual((completed, bids), (0, 1))
        self.assertTrue(companies[1].active)
        self.assertEqual(people[0].shares, {"b": 600.0})

    def test_offer_at_value_succeeds_and_pays_part_in_shares(self) -> None:
        companies, people = market()
        buyer, target, _ = companies
        config = SimulationConfig(synergy_takeovers=True, takeover_share_payment=0.5)
        fundamental = _fundamental_price(target, companies, config, 12.0 * config.loan_interest_rate)
        cash_before = sum(c.cash for c in companies) + sum(p.cash for p in people)
        buyer_shares = buyer.shares_outstanding
        completed, bids = takeover_by_value(companies, people, config, 12,
                                            synergy=lambda b, t, v: 0.1, hurdle=lambda b: 1.0)
        self.assertEqual((completed, bids), (1, 1))
        self.assertFalse(target.active)
        self.assertEqual(target.acquired_by, buyer.id)
        # monnaie conservée : un paiement entre agents, aucune création
        cash_after = sum(c.cash for c in companies) + sum(p.cash for p in people)
        self.assertAlmostEqual(cash_before, cash_after, delta=1e-6)
        # les porteurs reçoivent la moitié du prix (valeur fondamentale) en monnaie
        paid = people[0].cash + people[1].cash
        self.assertAlmostEqual(paid, 0.5 * 0.5 * 1000.0 * fundamental, delta=1e-6)
        # et le reste en actions de l'acquéreur : chaque titre émis a un porteur
        self.assertGreater(buyer.shares_outstanding, buyer_shares)
        self.assertAlmostEqual(holdings(companies, people, buyer.id) + buyer.treasury_shares,
                               buyer.shares_outstanding, delta=1e-6)
        self.assertEqual(holdings(companies, people, target.id), 0.0)

    def test_no_offer_without_anything_to_bring(self) -> None:
        companies, people = market()
        config = SimulationConfig(synergy_takeovers=True)
        completed, bids = takeover_by_value(companies, people, config, 12,
                                            synergy=lambda b, t, v: 0.0, hurdle=lambda b: 1.0)
        self.assertEqual((completed, bids), (0, 0))
        self.assertTrue(companies[1].active)

    def test_cash_limit_counts_only_the_cash_part(self) -> None:
        companies, people = market()
        buyer, target, _ = companies
        config = SimulationConfig(synergy_takeovers=True, takeover_share_payment=0.5)
        fundamental = _fundamental_price(target, companies, config, 12.0 * config.loan_interest_rate)
        price = 0.5 * 1000.0 * fundamental
        # trésorerie libre entre la moitié et la totalité du prix : seule l'offre mixte passe
        buyer.cash = 0.75 * price
        completed, _ = takeover_by_value(companies, people, config, 12,
                                         synergy=lambda b, t, v: 0.1, hurdle=lambda b: 1.0)
        self.assertEqual(completed, 1)
        companies, people = market()
        companies[0].cash = 0.75 * price
        completed, bids = takeover_by_value(companies, people, SimulationConfig(synergy_takeovers=True,
                                                                                takeover_share_payment=0.0),
                                            12, synergy=lambda b, t, v: 0.1, hurdle=lambda b: 1.0)
        self.assertEqual((completed, bids), (0, 0))

    def test_merged_productivity_is_weighted_by_staff(self) -> None:
        companies, people = market(productivity_gap=0.4)
        buyer, target, _ = companies
        config = SimulationConfig(synergy_takeovers=True)
        _merge_into(buyer, target, people, companies, config=config)
        # 4 et 2 postes pondérés (salariés + 1) ; la cible rattrape un quart de l'écart
        expected = (4 * 1.4 + 2 * (1.0 + 0.25 * 0.4)) / 6
        self.assertAlmostEqual(buyer.productivity_trait, expected)
        companies, people = market(productivity_gap=0.4)
        _merge_into(companies[0], companies[1], people, companies)
        self.assertAlmostEqual(companies[0].productivity_trait, 1.2)  # ancienne moyenne simple


class ProportionalHiringTests(unittest.TestCase):

    def test_bounds_scale_with_staff_and_keep_the_fixed_floor(self) -> None:
        base = SimulationConfig()
        scaled = SimulationConfig(proportional_hiring=True, staff_change_share=0.10)
        self.assertEqual(monthly_staff_bounds(200, base), (base.max_monthly_hiring, base.max_monthly_layoffs))
        self.assertEqual(monthly_staff_bounds(200, scaled), (20, 20))
        self.assertEqual(monthly_staff_bounds(5, scaled), (base.max_monthly_hiring, base.max_monthly_layoffs))


def distressed(**changes) -> Company:
    """Une entreprise de 40 salariés en pertes depuis trois mois, presque sans trésorerie, très
    endettée : ses recettes paient la moitié de sa masse salariale après les autres charges."""
    company = Company("f", "En difficulté", "food", 50_000.0, 10.0, employees=[f"e{i}" for i in range(40)],
                      debt=10_000_000.0, debt_interest_rate=0.01, wage_bill=100_000.0,
                      revenue_ema=180_000.0, previous_revenue=180_000.0, expenses=330_000.0,
                      profit=-150_000.0, loss_streak=2)
    for key, value in changes.items():
        setattr(company, key, value)
    return company


class SafeguardTests(unittest.TestCase):

    def test_plan_downsizes_writes_off_and_asks_for_a_bridge(self) -> None:
        company = distressed()
        config = SimulationConfig(safeguard_procedure=True, safeguard_debt_share=0.5)
        kept = 0.5 * serviceable_debt(company, config, 0.002)
        plans = safeguard_plans([company], config, 0.002, 500)
        self.assertEqual(len(plans), 1)
        _, written, bridge = plans[0]
        self.assertAlmostEqual(company.debt, kept)
        self.assertAlmostEqual(written, 10_000_000.0 - kept)
        self.assertAlmostEqual(company.default_loss, written)  # perte des banques, pas de monnaie
        self.assertTrue(10 <= company.safeguard_staff < 40)
        payroll = 100_000.0 * company.safeguard_staff / 40
        self.assertAlmostEqual(bridge, SAFEGUARD_BRIDGE_MONTHS * payroll - 50_000.0)
        self.assertEqual((company.loss_streak, company.safeguard_month), (0, 500))
        # un seul plan par période de 24 mois ; l'effectif du plan ne vaut qu'un mois
        company.loss_streak, company.profit = 2, -1.0
        self.assertEqual(safeguard_plans([company], config, 0.002, 510), [])
        self.assertEqual(company.safeguard_staff, 0)

    def test_no_plan_without_every_condition(self) -> None:
        config = SimulationConfig(safeguard_procedure=True)
        cases = {
            "petite": {"employees": [f"e{i}" for i in range(5)]},
            "rentable": {"profit": 1.0},
            "pertes recentes": {"loss_streak": 0},
            "tresorerie": {"cash": 10_000_000.0},
            "sans noyau viable": {"revenue_ema": 60_000.0},
            "banque": {"sector": "bank"},
        }
        for name, change in cases.items():
            with self.subTest(cas=name):
                company = distressed(**change)
                self.assertEqual(safeguard_plans([company], config, 0.002, 500), [])
                self.assertEqual(company.debt, 10_000_000.0)
        public = distressed()
        self.assertEqual(safeguard_plans([public], config, 0.002, 500, excluded=frozenset({"f"})), [])

    def test_plan_lifts_the_layoff_bound_for_one_month(self) -> None:
        from economy.labor import update_workforce_targets
        config = SimulationConfig(safeguard_procedure=True)
        people = [Person(f"e{i}", f"S {i}", 0.0, employer_id="f") for i in range(40)]
        company = distressed(age_months=60, safeguard_staff=15)
        update_workforce_targets([company], people, config)
        self.assertLessEqual(company.target_employees, 15)
        company.safeguard_staff = 0
        update_workforce_targets([company], people, config)
        self.assertGreaterEqual(company.target_employees, 40 - config.max_monthly_layoffs)


class TakeoverTogetherTests(unittest.TestCase):

    def test_enabled_runs_with_exact_accounts(self) -> None:
        sim = simulation(months=1, **{name: True for name in SWITCHES})
        for _ in range(60):
            record = sim.step()
            self.assertTrue(math.isfinite(record.nominal_gdp))
            self.assertLess(abs(record.loan_reconciliation_error), 1e-3)
            self.assertLess(abs(record.public_debt_reconciliation_error), 1e-3)
            self.assertLess(abs(record.ledger_residual), 1e-3)
        for company in sim.companies:
            issued = sum(h.shares.get(company.id, 0.0) for h in [*sim.people, *sim.companies,
                                                                   *sim._outside_shareholders()])
            if company.active:
                self.assertAlmostEqual(issued + company.treasury_shares, company.shares_outstanding,
                                       delta=1e-6 * max(1.0, company.shares_outstanding))

    def test_same_seed_gives_the_same_story(self) -> None:
        first = simulation(months=30, **{name: True for name in SWITCHES})
        second = simulation(months=30, **{name: True for name in SWITCHES})
        self.assertEqual(first.history[-1], second.history[-1])


if __name__ == "__main__":
    unittest.main()
