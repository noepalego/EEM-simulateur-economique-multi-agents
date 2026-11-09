"""Lot C (novembre 2026) : objectifs ministériels enrichis (``enriched_objectives``).

Deux objectifs par ministre (principal et secondaire) choisis selon la doctrine, cibles qui
dépendent de la situation de départ, levier réel actionné par un objectif en retard, bilan pondéré
dans le vote. Désactivé par défaut : un seul objectif, sans levier, comme avant.
"""

import unittest

from economy.institutions import (
    LEVER_TEXT,
    OBJECTIVE_LEVERS,
    PORTFOLIOS,
    Objective,
    demanding_target,
)
from economy.models import SimulationConfig
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 14, seed: int = 11, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=seed, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


class DemandingTargetTests(unittest.TestCase):

    def test_target_kept_when_not_yet_reached(self) -> None:
        self.assertEqual(demanding_target("chomage", "<=", 0.06, 0.08), 0.06)
        self.assertEqual(demanding_target("smic_median", ">=", 0.6, 0.5), 0.6)

    def test_target_raised_when_already_met(self) -> None:
        # chômage déjà sous la cible : un point de mieux, jamais sous 2 %
        self.assertAlmostEqual(demanding_target("chomage", "<=", 0.06, 0.04), 0.03)
        self.assertAlmostEqual(demanding_target("chomage", "<=", 0.06, 0.025), 0.02)
        self.assertAlmostEqual(demanding_target("couverture_services", ">=", 0.9, 0.97), 1.0)
        self.assertAlmostEqual(demanding_target("salaire_reel_median", ">=", 900.0, 1000.0), 1020.0)

    def test_maintenance_objective_at_its_bound_is_kept(self) -> None:
        self.assertEqual(demanding_target("chomage_longue_duree", "<=", 0.0, 0.0), 0.0)
        self.assertEqual(demanding_target("monopoles", "<=", 0.0, 0.0), 0.0)
        self.assertEqual(demanding_target("dette_pib", "~", 0.2, 0.2), 0.2)

    def test_every_lever_is_described(self) -> None:
        for indicator, lever in OBJECTIVE_LEVERS.items():
            with self.subTest(indicateur=indicator):
                self.assertIn(lever, LEVER_TEXT)


class EnrichedObjectivesTests(unittest.TestCase):

    def test_disabled_by_default_one_objective_without_lever(self) -> None:
        sim = simulation(months=2)
        for role in PORTFOLIOS:
            office = sim.institutions.offices[role]
            self.assertIsNone(office.secondary)
            self.assertEqual(office.objective.lever, "")
            self.assertEqual(sim._objective_pressure(role, office.objective.lever), 0.0)

    def test_two_objectives_with_levers_and_demanding_targets(self) -> None:
        sim = simulation(months=2, enriched_objectives=True)
        for role in PORTFOLIOS:
            office = sim.institutions.offices[role]
            with self.subTest(ministere=role):
                self.assertEqual([o.rank for o in office.objectives()], ["principal", "secondaire"])
                self.assertNotEqual(office.objective.indicator, office.secondary.indicator)
                for objective in office.objectives():
                    self.assertIn(objective.lever, LEVER_TEXT)
                    self.assertEqual(objective.deadline, office.objective.deadline)
                    # cible déjà exigeante : la règle ne la relève plus (situation de départ prise en compte)
                    self.assertEqual(demanding_target(objective.indicator, objective.comparison,
                                                      objective.target, objective.baseline), objective.target)
                # enveloppe partagée : principal + secondaire = enveloppe du ministère
                self.assertGreaterEqual(office.objective.envelope, office.secondary.envelope)

    def test_objectives_are_tracked_and_closed_with_a_weighted_record(self) -> None:
        sim = simulation(months=1, enriched_objectives=True, election_period_months=12)
        while sim.month <= 13:
            sim.step()
        term = sim.institutions.terms[-2]
        self.assertEqual(set(term.record), set(PORTFOLIOS))
        self.assertEqual(set(term.secondary_record), set(PORTFOLIOS))
        weight = sim.config.secondary_objective_weight
        met = sum(s == "atteint" for s in term.record.values()) + weight * sum(
            s == "atteint" for s in term.secondary_record.values())
        self.assertAlmostEqual(term.objectives_met, met / (len(PORTFOLIOS) * (1.0 + weight)))
        self.assertTrue(any("secondary" in member for member in term.members))

    def test_vote_record_weights_secondary_objectives(self) -> None:
        sim = simulation(months=2, enriched_objectives=True)
        offices = [sim.institutions.offices[role] for role in PORTFOLIOS]
        for office in offices:
            office.objective.value = office.objective.target if office.objective.comparison != "~" else office.objective.baseline
            office.secondary.value = (office.secondary.target + (1.0 if office.secondary.comparison == "<=" else -1.0)
                                      * (abs(office.secondary.target) + 1.0))
        self.assertTrue(all(o.objective.met(o.objective.value) for o in offices))
        self.assertTrue(not any(o.secondary.met(o.secondary.value) for o in offices))
        weight = sim.live.secondary_objective_weight
        self.assertAlmostEqual(sim._objectives_met_share(), 1.0 / (1.0 + weight))

    def test_lagging_objective_pulls_its_lever(self) -> None:
        sim = simulation(months=2, enriched_objectives=True)
        office = sim.institutions.offices["finances"]
        office.objective = Objective("solde_pib", "test", ">=", 1.0, 0.0, sim.month + 12,
                                     value=0.0, lever="loi_de_finances", rank="principal")
        office.secondary = None
        effort = sim.live.objective_effort
        self.assertAlmostEqual(sim._objective_pressure("finances", "loi_de_finances"), effort)
        gdp, revenue = 1_000_000.0, 300_000.0
        pressed = sim._balance_goal(gdp, revenue, 0.0)
        office.objective.value = 2.0  # atteint : plus de pression
        self.assertEqual(sim._objective_pressure("finances", "loi_de_finances"), 0.0)
        relaxed = sim._balance_goal(gdp, revenue, 0.0)
        self.assertAlmostEqual(pressed - relaxed, 0.01 * effort * gdp)

    def test_enriched_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=30, enriched_objectives=True, election_period_months=12)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        self.assertGreaterEqual(len(sim.institutions.terms), 3)


if __name__ == "__main__":
    unittest.main()
