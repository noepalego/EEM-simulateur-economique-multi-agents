"""Lot E (novembre 2026) : régime de propriété selon l'orientation (``economy/ownership.py``).

Désactivé par défaut. Activé : règles interpolées entre ultra-capitalisme (0) et coopératives
(100), application progressive, parts salariées acquises sans expropriation et bloquées.
"""

import copy
import dataclasses
import unittest

from economy.consolidation import _employee_stake
from economy.interventions import request_orientation
from economy.models import SimulationConfig
from economy.ownership import apply_ownership_rules, regime_intensities
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(orientation: float | None = None, months: int = 0, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    sim.step()
    for _ in range(months):
        if orientation is not None and (sim.month - 1) % sim.config.election_period_months in (0, 1):
            request_orientation(sim, orientation)
        sim.step()
    return sim


class OwnershipRuleTests(unittest.TestCase):

    def test_intensities_are_continuous(self) -> None:
        self.assertEqual(regime_intensities(0.0), (1.0, 0.0))
        self.assertEqual(regime_intensities(0.5), (0.0, 0.0))
        self.assertEqual(regime_intensities(1.0), (0.0, 1.0))
        self.assertEqual(regime_intensities(0.25), (0.5, 0.0))
        self.assertEqual(regime_intensities(0.75), (0.0, 0.5))

    def test_centre_changes_nothing(self) -> None:
        base = SimulationConfig()
        live = copy.copy(base)
        apply_ownership_rules(live, 0.5)
        for name in ("monopoly_duration_months", "control_threshold", "min_firms_per_sector",
                     "dividend_payout_rate", "executive_wage_multiplier"):
            self.assertEqual(getattr(live, name), getattr(base, name), name)

    def test_extremes(self) -> None:
        base = SimulationConfig()
        liberal, social = copy.copy(base), copy.copy(base)
        apply_ownership_rules(liberal, 0.0)
        apply_ownership_rules(social, 1.0)
        self.assertGreater(liberal.monopoly_duration_months, 10 ** 6)       # aucune régulation
        self.assertLess(liberal.control_threshold, base.control_threshold)  # OPA facilitées
        self.assertGreater(liberal.dividend_payout_rate, base.dividend_payout_rate)
        self.assertEqual(liberal.executive_wage_multiplier, base.executive_wage_multiplier)
        self.assertGreater(social.control_threshold, base.control_threshold)
        self.assertEqual(social.min_firms_per_sector, 3)
        self.assertLess(social.dividend_payout_rate, base.dividend_payout_rate)
        self.assertLess(social.executive_wage_multiplier, base.executive_wage_multiplier)
        self.assertEqual(social.monopoly_duration_months, base.monopoly_duration_months)

    def test_disabled_by_default_and_neutral(self) -> None:
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertFalse(fields["ownership_regime"].default)
        for name in ("ownership_regime", "ownership_transition_months", "cooperative_share_max",
                     "cooperative_purchase_rate"):
            self.assertEqual(fields[name].metadata.get("identity"), "neutre", name)
        reference, sim = simulation(), simulation(ownership_regime=False)
        for _ in range(4):
            self.assertEqual(reference.step(), sim.step())
        self.assertIsNone(sim.institutions.ownership_regime)
        self.assertTrue(all(not p.coop_shares for p in sim.people))

    def test_employee_stake_is_never_tendered(self) -> None:
        sim = simulation()
        person = sim.people[0]
        self.assertEqual(_employee_stake(person, "x"), 0.0)
        person.coop_shares["x"] = 3.0
        self.assertEqual(_employee_stake(person, "x"), 3.0)
        self.assertEqual(_employee_stake(sim.companies[0], "x"), 0.0)


class OwnershipDynamicsTests(unittest.TestCase):

    def test_regime_moves_progressively(self) -> None:
        sim = simulation(orientation=100.0, months=1, ownership_regime=True, ownership_transition_months=20.0)
        start = sim.institutions.ownership_regime
        previous = start
        for _ in range(6):
            sim.step()
            regime = sim.institutions.ownership_regime
            self.assertLessEqual(regime - previous, 1.0 / 20.0 + 1e-12)
            previous = regime
        self.assertGreater(previous, start)

    def test_cooperative_shares_are_acquired_and_kept_consistent(self) -> None:
        sim = simulation(orientation=100.0, months=40, ownership_regime=True, ownership_transition_months=6.0)
        self.assertGreater(sim.institutions.ownership_regime, 0.99)
        stakes = sum(sum(p.coop_shares.values()) for p in sim.people)
        self.assertGreater(stakes, 0.0)
        companies = {c.id: c for c in sim.companies}
        for person in sim.people:
            for company_id, quantity in person.coop_shares.items():
                self.assertLessEqual(quantity, person.shares.get(company_id, 0.0) + 1e-9)
                self.assertTrue(companies[company_id].active)
                self.assertNotEqual(companies[company_id].sector, "bank")
        for company in sim.companies:
            if company.active and company.shares_outstanding > 0.0:
                held = sum(p.coop_shares.get(company.id, 0.0) for p in sim.people)
                self.assertLessEqual(held, sim.config.cooperative_share_max * company.shares_outstanding + 1e-6)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_liberal_regime_has_no_cooperative_shares(self) -> None:
        sim = simulation(orientation=0.0, months=24, ownership_regime=True, ownership_transition_months=6.0)
        self.assertLess(sim.institutions.ownership_regime, 0.01)
        self.assertTrue(all(not p.coop_shares for p in sim.people))
        self.assertGreater(sim.live.monopoly_duration_months, 10 ** 6)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)


if __name__ == "__main__":
    unittest.main()
