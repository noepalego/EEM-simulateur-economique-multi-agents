"""Lot B (novembre 2026) : réglage des taux d'intérêt.

Taux directeur forcé à la place de la règle de Taylor, marge supplémentaire des banques
commerciales (``bank_margin_offset``, réglage de base et forçage) et écart des dépôts forcé.
Comme ``test_interventions.py``, ce module désactive la période de chauffe.
"""

import dataclasses
import os
import re
import unittest

from economy.credit_risk import risk_loan_spread
from economy.finance import extend_credit, new_loan_rate
from economy.interventions import (
    interventions,
    release_bank_rate,
    release_policy_rate,
    set_bank_rate,
    set_policy_rate,
)
from economy.models import Company, SimulationConfig
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
    period = sim.config.election_period_months
    sim.step()
    while (sim.month - 1) % period != 0:
        sim.step()


def _firm(**fields) -> Company:
    return Company("f", "Entreprise", "food", 50_000.0, 10.0, **fields)


class PolicyRateInterventionTests(unittest.TestCase):

    def test_forced_rate_replaces_taylor_rule_until_released(self) -> None:
        sim = simulation()
        self.assertEqual(set_policy_rate(sim, 0.05), 0.05)
        self.assertEqual(sim.policy_rate, 0.05 / 12.0)
        for _ in range(3):
            sim.step()
            self.assertEqual(sim.policy_rate, 0.05 / 12.0)
            self.assertEqual(sim.history[-1].policy_rate, 0.05 / 12.0)
        release_policy_rate(sim)
        self.assertIsNone(interventions(sim).forced_policy_rate)
        sim.step()
        # la règle repart du taux forcé, avec son lissage
        self.assertNotEqual(sim.policy_rate, 0.05 / 12.0)
        self.assertLess(abs(sim.policy_rate - 0.05 / 12.0), 0.05 / 12.0)
        kinds = [row["Intervention"] for row in interventions(sim).journal]
        self.assertEqual(kinds, ["Taux directeur forcé", "Taux directeur rendu à la règle"])

    def test_forced_rate_is_bounded_and_survives_elections(self) -> None:
        sim = simulation(election_period_months=12)
        self.assertEqual(set_policy_rate(sim, 0.9), 0.20)
        step_to_election(sim)
        self.assertEqual(interventions(sim).forced_policy_rate, 0.20)
        self.assertEqual(sim.policy_rate, 0.20 / 12.0)

    def test_forced_rate_changes_the_economy(self) -> None:
        reference, sim = simulation(), simulation()
        set_policy_rate(sim, 0.15)
        for _ in range(4):
            reference.step(), sim.step()
        self.assertGreater(sim.deposit_rate, reference.deposit_rate)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)


class BankMarginTests(unittest.TestCase):

    def test_offset_is_disabled_by_default_and_neutral_for_identity(self) -> None:
        spec = {f.name: f for f in dataclasses.fields(SimulationConfig)}["bank_margin_offset"]
        self.assertEqual(spec.default, 0.0)
        self.assertEqual(spec.metadata.get("identity"), "neutre")
        reference, sim = simulation(), simulation(bank_margin_offset=0.0)
        for _ in range(3):
            self.assertEqual(reference.step(), sim.step())

    def test_offset_raises_every_lending_rate(self) -> None:
        base, raised = SimulationConfig(), SimulationConfig(bank_margin_offset=0.001)
        self.assertAlmostEqual(new_loan_rate(raised, 0.002) - new_loan_rate(base, 0.002), 0.001, places=12)
        firm = _firm(productive_capital_value=200_000.0)
        lent = {}
        for name, config in (("base", base), ("raised", raised)):
            company = _firm(productive_capital_value=200_000.0)
            extend_credit(company, 10_000.0, 1e9, config, policy_rate=0.002)
            lent[name] = company.debt_interest_rate
        spread = risk_loan_spread(firm, 10_000.0, 250_000.0, base, 0.002)
        self.assertGreaterEqual(spread, 0.0)
        self.assertAlmostEqual(lent["raised"] - lent["base"], 0.001, places=12)
        sim = simulation(months=1, bank_margin_offset=0.001)
        self.assertAlmostEqual(sim.live.mortgage_spread, sim.config.mortgage_spread + 0.001, places=15)
        self.assertAlmostEqual(sim.live.consumer_credit_spread,
                               sim.config.consumer_credit_spread + 0.001, places=15)
        self.assertEqual(sim.config.mortgage_spread, SimulationConfig().mortgage_spread)

    def test_negative_offset_never_makes_a_spread_negative(self) -> None:
        sim = simulation(months=1, bank_margin_offset=-0.01)
        self.assertEqual(sim.live.mortgage_spread, 0.0)
        self.assertEqual(sim.live.consumer_credit_spread, 0.0)
        self.assertGreaterEqual(new_loan_rate(sim.live, 0.002), 0.002)


class BankRateInterventionTests(unittest.TestCase):

    def test_forced_margin_and_deposit_spread_until_released(self) -> None:
        sim = simulation(election_period_months=12)
        self.assertEqual(set_bank_rate(sim, "bank_margin_offset", 0.024), 0.024)
        self.assertEqual(set_bank_rate(sim, "deposit_spread", 0.0), 0.0)
        sim.step()
        self.assertEqual(sim.live.bank_margin_offset, 0.024 / 12.0)
        self.assertAlmostEqual(sim.live.mortgage_spread, sim.config.mortgage_spread + 0.002, places=15)
        self.assertEqual(sim.live.deposit_spread, 0.0)
        # pas de fin à l'élection : ces taux ne relèvent pas du gouvernement
        step_to_election(sim)
        self.assertEqual(sim.live.bank_margin_offset, 0.024 / 12.0)
        release_bank_rate(sim, "bank_margin_offset")
        release_bank_rate(sim, "deposit_spread")
        sim.step()
        self.assertEqual(sim.live.bank_margin_offset, 0.0)
        self.assertEqual(sim.live.mortgage_spread, sim.config.mortgage_spread)
        self.assertEqual(sim.live.deposit_spread, sim.config.deposit_spread)
        self.assertEqual(len(interventions(sim).journal), 4)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_unknown_rate_and_bounds(self) -> None:
        sim = simulation(months=0)
        with self.assertRaises(KeyError):
            set_bank_rate(sim, "vat_rate", 0.1)
        self.assertEqual(set_bank_rate(sim, "deposit_spread", -1.0), -0.02)


class RateRegistryTests(unittest.TestCase):
    """Chaque réglage ajouté par le programme de modifications (lots B à F) est listé et justifié
    dans la section « Réglages du programme de modifications » d'AUTONOMIE.md, et existe dans
    ``SimulationConfig`` ; ils sont désactivés par défaut (neutres pour l'identité)."""

    ADDED = {"bank_margin_offset", "enriched_objectives", "objective_effort", "secondary_objective_weight",
             "volume_discount_max", "volume_discount_half_volume", "fixed_cost_share",
             "ownership_regime", "ownership_transition_months", "cooperative_share_max",
             "cooperative_purchase_rate", "countercyclical_capital", "countercyclical_floor",
             "countercyclical_step", "countercyclical_trigger", "bank_capital_backstop",
             "bank_backstop_months", "bank_backstop_capacity", "stock_earnings_smoothing_months"}

    def _section(self) -> dict[str, str]:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du programme de modifications")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        return {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}

    def test_added_settings_are_registered(self) -> None:
        registry = self._section()
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), self.ADDED)
        for name in self.ADDED:
            with self.subTest(parametre=name):
                self.assertIn(name, fields)
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)


if __name__ == "__main__":
    unittest.main()
