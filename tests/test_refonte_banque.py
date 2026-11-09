"""Refonte de la comptabilité bancaire (novembre 2026) : trésorerie propre des banques.

``bank_own_funds="equity"`` : la trésorerie propre d'une banque (ce qu'elle peut payer) est, en
début de mois, la part liquide de ses fonds propres, et l'excédent qu'elle distribue est son
capital au-delà de l'exigence et de son coussin. Désactivé par défaut (« cash ») : neutre pour
l'empreinte d'identité. Comme ``test_taux.py``, ce module désactive la période de chauffe.
"""

import dataclasses
import os
import re
import unittest

from economy.models import Company, Person, SimulationConfig
from economy.simulation import EconomySimulation
from economy.stock_market import distribute_dividends

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


def banks(sim: EconomySimulation) -> list[Company]:
    return [c for c in sim.companies if c.sector == "bank" and c.active]


class BankOwnFundsTests(unittest.TestCase):
    def test_setting_is_neutral_and_registered(self) -> None:
        spec = {f.name: f for f in dataclasses.fields(SimulationConfig)}["bank_own_funds"]
        self.assertEqual(spec.default, "cash")
        self.assertEqual(spec.metadata.get("identity"), "neutre")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Refonte de la comptabilité bancaire")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        rows = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        self.assertEqual(set(rows), {"bank_own_funds"})
        self.assertGreaterEqual(len(rows["bank_own_funds"]), 60)

    def test_own_funds_are_the_liquid_part_of_equity(self) -> None:
        sim = simulation(bank_own_funds="equity")
        first, second = banks(sim)[:2]
        first.bank_equity, first.technology_asset_value, first.productive_capital_value = 1000.0, 120.0, 30.0
        second.bank_equity, second.technology_asset_value, second.productive_capital_value = 500.0, 0.0, 0.0
        first.interbank_claims = {second.id: 200.0}  # la première a prêté 200 à la seconde
        second.interbank_claims = {}
        for other in banks(sim)[2:]:
            other.interbank_claims = {}
        first.cash = second.cash = 1e9  # compteur sans contrepartie
        sim._reconcile_bank_own_funds()
        self.assertAlmostEqual(first.cash, 1000.0 - 150.0 - 200.0)
        self.assertAlmostEqual(second.cash, 500.0 + 200.0)
        first.interbank_claims = {}
        second.bank_equity = -50.0  # insolvable, sans emprunt : rien à payer
        sim._reconcile_bank_own_funds()
        self.assertEqual(second.cash, 0.0)

    def test_a_loan_loss_reduces_what_the_bank_can_pay(self) -> None:
        paid = {}
        for mode in ("cash", "equity"):
            sim = simulation(bank_own_funds=mode)
            borrower = next(c for c in sim.companies if c.sector != "bank" and c.active
                            and any(b.bank_loan_accounts.get(c.id, 0.0) > 0.0 for b in banks(sim)))
            lenders = [b for b in banks(sim) if b.bank_loan_accounts.get(borrower.id, 0.0) > 0.0]
            cash_before = sum(b.cash for b in lenders)
            equity_before = sum(b.bank_equity for b in lenders)
            loss = 0.5 * min(borrower.debt, equity_before)
            borrower.default_loss = loss  # perte constatée (restructuration), répartie entre prêteurs
            sim._write_off_defaulted_bank_loans()
            if mode == "equity":
                sim._reconcile_bank_own_funds()  # début du mois suivant
            self.assertAlmostEqual(sum(b.bank_equity for b in lenders), equity_before - loss, delta=1e-6 * loss)
            paid[mode] = cash_before - sum(b.cash for b in lenders)
        self.assertEqual(paid["cash"], 0.0)  # l'ancien compteur ignore la perte
        self.assertGreater(paid["equity"], 0.0)

    def test_bank_excess_payout_comes_from_capital_above_the_buffer(self) -> None:
        def payout(ratio: float | None) -> float:
            bank = Company("bank_1", "Banque", "bank", 10_000.0, 1000.0, bank_equity=200.0,
                           bank_loan_accounts={"food_1": 1000.0})
            owner = Person("p", "Actionnaire", 0.0, shares={"bank_1": 10.0})
            distribute_dividends([bank], [owner], 0.12, 0.0, 0.01, 0.25, bank_capital_ratio=ratio)
            return owner.cash

        # trésorerie de 10 000 pour 200 de fonds propres : l'ancien calcul distribue la trésorerie
        self.assertAlmostEqual(payout(None), (10_000.0 - 0.25 * 1000.0) * 0.01)
        # réconcilié : seul le capital au-delà de 10,5 % des prêts est restitué
        self.assertAlmostEqual(payout(0.105), (200.0 - 0.105 * 1000.0) * 0.01)

    def test_reconciled_banks_keep_money_and_accounts(self) -> None:
        sim = EconomySimulation(SimulationConfig(seed=7, population_size=120, bank_own_funds="equity"))
        for _ in range(36):
            record = sim.step()
            self.assertAlmostEqual(sim.ledger.residual(sim), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            for bank in banks(sim):
                self.assertGreaterEqual(bank.cash, 0.0)


if __name__ == "__main__":
    unittest.main()
