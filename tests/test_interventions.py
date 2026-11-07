"""Mode intervention (``economy/interventions.py``) : actions de l'opérateur en cours de simulation.

Comme ``test_simulation.py``, ce module désactive la période de chauffe.
"""

import unittest

from economy.interventions import (
    adjust_company_cash,
    adjust_household_cash,
    clear_lever,
    interventions,
    request_orientation,
    set_household_skill,
    set_lever,
)
from economy.models import SimulationConfig
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 3, seed: int = 11, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=seed, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def step_to_election(sim: EconomySimulation) -> None:
    """Avance jusqu'au mois d'élection suivant (inclus)."""
    period = sim.config.election_period_months
    sim.step()
    while (sim.month - 1) % period != 0:
        sim.step()


class InterventionTests(unittest.TestCase):

    def test_empty_interventions_are_neutral(self) -> None:
        reference, other = simulation(), simulation()
        interventions(other)  # objet créé, aucune intervention
        for _ in range(4):
            self.assertEqual(reference.step(), other.step())
        self.assertEqual(reference.live.vat_rate, other.live.vat_rate)

    def test_household_cash_is_kept_and_money_identity_holds(self) -> None:
        sim = simulation()
        person = sim.people[0]
        residual = sim.ledger.residual(sim)
        before, wealth = person.cash, sim.household_wealth(person)
        self.assertEqual(adjust_household_cash(sim, person.id, 100000.0), 100000.0)
        self.assertEqual(person.cash, before + 100000.0)
        self.assertAlmostEqual(sim.ledger.residual(sim), residual, delta=1e-6)
        sim.step()
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        # la somme reste au ménage (trésorerie, ou ce qu'il en achète : logement, titres)
        self.assertGreater(sim.household_wealth(person), wealth + 80000.0)

    def test_withdrawal_is_capped_at_available_cash(self) -> None:
        sim = simulation()
        person = sim.people[1]
        available = max(0.0, person.cash)
        self.assertEqual(adjust_household_cash(sim, person.id, -(available + 1e9)), -available)
        self.assertEqual(person.cash, min(0.0, person.cash))

    def test_company_cash_and_banks_excluded(self) -> None:
        sim = simulation()
        firm = next(c for c in sim.companies if c.active and c.sector != "bank")
        bank = next(c for c in sim.companies if c.sector == "bank")
        before = firm.cash
        self.assertEqual(adjust_company_cash(sim, firm.id, 25000.0), 25000.0)
        self.assertEqual(firm.cash, before + 25000.0)
        self.assertEqual(adjust_company_cash(sim, bank.id, 25000.0), 0.0)
        sim.step()
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_skill_is_bounded(self) -> None:
        sim = simulation()
        person = sim.people[2]
        self.assertEqual(set_household_skill(sim, person.id, 5.0), 2.0)
        self.assertEqual(person.skill, 2.0)
        self.assertEqual(set_household_skill(sim, person.id, 0.0), 0.5)

    def test_forced_lever_lasts_until_election(self) -> None:
        sim = simulation(election_period_months=12)
        set_lever(sim, "vat_rate", 0.30)
        sim.step()
        self.assertEqual(sim.live.vat_rate, 0.30)
        step_to_election(sim)
        self.assertEqual(interventions(sim).levers, {})
        self.assertNotEqual(sim.live.vat_rate, 0.30)

    def test_released_lever_returns_to_voted_value(self) -> None:
        reference, sim = simulation(), simulation()
        set_lever(sim, "minimum_wage", 5000.0)
        reference.step(), sim.step()
        self.assertEqual(sim.live.minimum_wage, 5000.0)
        clear_lever(sim, "minimum_wage")
        sim.step()
        self.assertEqual(sim.live.minimum_wage, sim.institutions.minimum_wage)

    def test_imposed_government_until_next_election(self) -> None:
        reference, sim = simulation(election_period_months=12), simulation(election_period_months=12)
        set_lever(sim, "vat_rate", 0.30)
        set_lever(sim, "corporate_tax_rate", 0.40)
        request_orientation(sim, 0.0)
        # leviers forcés avant la demande : rendus ; forcés après : valent pour le mandat imposé
        self.assertEqual(interventions(sim).levers, {})
        set_lever(sim, "corporate_tax_rate", 0.05)
        reference.step(), sim.step()
        self.assertEqual(sim.government.orientation, 0.0)
        self.assertEqual(sim.government.since_month, sim.month)
        self.assertEqual(sim.institutions.terms[-1].since, sim.month)
        self.assertIsNone(interventions(sim).pending_orientation)
        self.assertEqual(interventions(sim).levers, {"corporate_tax_rate": 0.05})
        self.assertEqual(sim.live.corporate_tax_rate, 0.05)
        self.assertNotEqual(sim.live.vat_rate, 0.30)
        # le générateur des institutions n'a pas avancé à cause de l'intervention
        self.assertEqual(sim.institutions.rng.getstate(), reference.institutions.rng.getstate())
        self.assertEqual(len(sim.institutions.offices), len(reference.institutions.offices))
        step_to_election(sim)
        self.assertEqual(sim.government.since_month, sim.month)  # élection normale
        self.assertEqual(interventions(sim).levers, {})
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)


if __name__ == "__main__":
    unittest.main()
