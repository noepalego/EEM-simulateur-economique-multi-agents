"""Lot F (novembre 2026) : stabilité.

F1 : exigence de fonds propres contracyclique, recapitalisation de dernier recours, compteur des
entreprises non examinées faute de capacité bancaire. F2 : valeur fondamentale fondée sur un
bénéfice lissé. Réglages désactivés par défaut.
"""

import copy
import dataclasses
import unittest

from economy import diagnostics
from economy.finance import request_operating_credit
from economy.models import Company, SimulationConfig
from economy.simulation import EconomySimulation
from economy.stock_market import _fundamental_price

_ORIGINAL_WARM_UP = EconomySimulation._warm_up

ADDED = ("countercyclical_capital", "countercyclical_floor", "countercyclical_step", "countercyclical_trigger",
         "bank_capital_backstop", "bank_backstop_months", "bank_backstop_capacity",
         "stock_earnings_smoothing_months")


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 14, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def _falling(sim: EconomySimulation) -> None:
    """Activité en baisse : PIB réel décroissant sur les douze derniers relevés (test seulement)."""
    for i, record in enumerate(sim.history[-12:]):
        record.real_gdp = 1000.0 - 10.0 * i


class DefaultsTests(unittest.TestCase):

    def test_settings_are_disabled_and_neutral(self) -> None:
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        for name in ADDED:
            self.assertEqual(fields[name].metadata.get("identity"), "neutre", name)
        self.assertFalse(fields["countercyclical_capital"].default)
        self.assertFalse(fields["bank_capital_backstop"].default)
        self.assertEqual(fields["stock_earnings_smoothing_months"].default, 0)
        reference = simulation(months=0)
        explicit = simulation(months=0, countercyclical_capital=False, bank_capital_backstop=False,
                              stock_earnings_smoothing_months=0)
        for _ in range(4):
            self.assertEqual(reference.step(), explicit.step())


class CountercyclicalCapitalTests(unittest.TestCase):

    def test_relief_grows_in_a_credit_freeze_and_recedes_after(self) -> None:
        sim = simulation(countercyclical_capital=True)
        _falling(sim)
        sim.credit_capacity = lambda: 0.0  # capacité épuisée
        base = sim.config.capital_requirement
        for month in range(1, 12):
            live = copy.copy(sim.config)
            sim._countercyclical_capital(live)
            expected = min(month * sim.config.countercyclical_step, base - sim.config.countercyclical_floor)
            self.assertAlmostEqual(sim.central_bank.capital_relief, expected)
            self.assertAlmostEqual(live.capital_requirement, base - expected)
        self.assertAlmostEqual(live.capital_requirement, sim.config.countercyclical_floor)
        sim.credit_capacity = lambda: 1e12  # capacité reconstituée
        live = copy.copy(sim.config)
        sim._countercyclical_capital(live)
        self.assertAlmostEqual(sim.central_bank.capital_relief,
                               base - sim.config.countercyclical_floor - sim.config.countercyclical_step)

    def test_no_relief_without_a_downturn(self) -> None:
        sim = simulation(countercyclical_capital=True)
        for i, record in enumerate(sim.history[-12:]):
            record.real_gdp = 1000.0 + 10.0 * i  # activité en hausse
        sim.credit_capacity = lambda: 0.0
        live = copy.copy(sim.config)
        sim._countercyclical_capital(live)
        self.assertEqual(sim.central_bank.capital_relief, 0.0)
        self.assertEqual(live.capital_requirement, sim.config.capital_requirement)


class BackstopTests(unittest.TestCase):

    def test_state_recapitalizes_banks_after_a_durable_freeze(self) -> None:
        sim = simulation(bank_capital_backstop=True)
        banks = [b for b in sim.companies if b.sector == "bank" and b.active]
        for bank in banks:
            bank.bank_equity = 0.0  # fonds propres épuisés : capacité nulle
        self.assertLess(sim.credit_capacity(), 1.0)
        residual = sim.ledger.residual(sim)
        months = sim.config.bank_backstop_months
        for _ in range(months - 1):
            sim._bank_capital_backstop()
        self.assertEqual(sim.central_bank.frozen_months, months - 1)
        self.assertTrue(all(b.bank_equity == 0.0 for b in banks))
        sim._bank_capital_backstop()
        self.assertEqual(sim.central_bank.frozen_months, 0)
        recapitalized = [b for b in banks if b.bank_equity > 0.0]
        self.assertTrue(recapitalized)
        for bank in recapitalized:
            self.assertGreater(sim.public.account.shares.get(bank.id, 0.0), 0.0)
        self.assertGreater(sim.credit_capacity(), 0.0)
        self.assertAlmostEqual(sim.ledger.residual(sim), residual, delta=1e-3)
        self.assertTrue(any("dernier recours" in d.decision for d in sim.institutions.journal))


class RefusalCounterTests(unittest.TestCase):

    def test_firms_skipped_for_lack_of_capacity_are_counted(self) -> None:
        firm = Company("f", "F", "food", 10.0, 10.0)
        bank = Company("b", "B", "bank", 10.0, 10.0)
        diagnostics.start()
        try:
            request_operating_credit([firm, bank], [], 0.0, SimulationConfig())
            self.assertEqual(diagnostics._refusals["credit_exploitation"]["capacite_nulle"], 1)
        finally:
            diagnostics.stop()


class SmoothedEarningsTests(unittest.TestCase):

    def test_fundamental_uses_the_average_profit(self) -> None:
        firm = Company("f", "F", "food", 0.0, 10.0, shares_outstanding=1000.0, profit=1200.0,
                       previous_profit=0.0)
        firm.financial_history = [{"Bénéfice": 0.0}, {"Bénéfice": 0.0}, {"Bénéfice": 0.0}]
        base = SimulationConfig(stock_book_value_weight=0.0)
        smooth = SimulationConfig(stock_book_value_weight=0.0, stock_earnings_smoothing_months=4)
        flat = dataclasses.replace(firm, profit=300.0)
        flat.financial_history = [{"Bénéfice": 300.0}] * 3
        # même bénéfice moyen (300) : même valeur, que le bénéfice soit concentré sur un mois ou non
        self.assertAlmostEqual(_fundamental_price(firm, [firm], smooth, 0.05),
                               _fundamental_price(flat, [flat], smooth, 0.05))
        # ancienne formule : le mois exceptionnel est capitalisé et extrapolé
        self.assertGreater(_fundamental_price(firm, [firm], base, 0.05),
                           _fundamental_price(firm, [firm], smooth, 0.05))

    def test_enabled_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=24, stock_earnings_smoothing_months=12)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)


if __name__ == "__main__":
    unittest.main()
