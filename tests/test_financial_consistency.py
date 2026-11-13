"""Régressions économiques : capital bancaire pondéré et ventes sur saisie."""

import unittest
from unittest.mock import patch

from economy.household_credit import foreclose_homes
from economy.models import Company, Person, SimulationConfig
from economy.simulation import EconomySimulation


class SovereignCapitalConsistencyTests(unittest.TestCase):
    def simulation(self, weight):
        with patch.object(EconomySimulation, "_warm_up", lambda self: None):
            sim = EconomySimulation(SimulationConfig(
                seed=11, population_size=40, sovereign_risk_weight=weight,
                bank_bailout_cap_gdp=1.0,
            ))
        bank = next(c for c in sim.companies if c.sector == "bank")
        bank.bank_loan_accounts.clear()
        bank.bank_loan_accounts["emprunteur"] = 100_000.0
        bank.bond_holdings = 1_000_000.0
        bank.bank_equity = 20_000.0
        bank.treasury_shares = 0.0
        bank.stock_price = 10.0
        return sim, bank

    def test_risk_free_bonds_do_not_block_dividends_or_request_capital(self):
        sim, bank = self.simulation(0.0)
        outstanding = bank.shares_outstanding
        self.assertNotIn(bank.id, sim._undercapitalized_banks())
        sim._authorize_bank_rights_issues()
        self.assertEqual(bank.treasury_shares, 0.0)
        self.assertEqual(bank.shares_outstanding, outstanding)

    def test_rights_issue_uses_weighted_exposure(self):
        for weight in (0.0, 0.5, 1.0):
            with self.subTest(weight=weight):
                sim, bank = self.simulation(weight)
                ratio = sim.live.capital_requirement + sim.live.bank_recap_buffer
                required = ratio * (100_000.0 + weight * 1_000_000.0)
                sim._authorize_bank_rights_issues()
                self.assertAlmostEqual(bank.treasury_shares,
                                       max(0.0, required - 20_000.0) / 10.0)

    def test_public_recapitalisation_accounts_for_weight_of_new_bonds(self):
        for weight in (0.0, 0.5, 1.0):
            with self.subTest(weight=weight):
                sim, bank = self.simulation(weight)
                bank.bank_equity = 1_000.0
                ratio = sim.live.capital_requirement + sim.live.bank_recap_buffer
                exposure = 100_000.0 + weight * 1_000_000.0
                injection = (ratio * exposure - 1_000.0) / (1.0 - ratio * weight)
                residual = sim.ledger.residual(sim)
                sim._recapitalize_banks()
                self.assertAlmostEqual(bank.bank_equity, 1_000.0 + injection)
                self.assertAlmostEqual(bank.bond_holdings, 1_000_000.0 + injection)
                self.assertAlmostEqual(bank.bank_equity, ratio * (
                    100_000.0 + weight * bank.bond_holdings))
                self.assertAlmostEqual(sim.ledger.residual(sim), residual, delta=1e-6)

    def test_capital_cost_uses_the_same_weight_as_credit_capacity(self):
        for weight in (0.0, 0.5, 1.0):
            with self.subTest(weight=weight):
                sim, bank = self.simulation(weight)
                bank.bank_equity = 1_000_000.0
                monthly = sim._cost_of_equity() / 12.0
                ratio = sim.live.capital_requirement + sim.live.bank_recap_buffer
                expected = monthly * ratio * (100_000.0 + weight * 1_000_000.0)
                self.assertAlmostEqual(sim._monthly_capital_costs()[bank.id], expected)

    def test_cash_backstop_uses_weighted_exposure(self):
        sim, bank = self.simulation(0.0)
        sim._institutions()
        sim._open_state_book()
        bank.bank_equity = 0.0
        sim.public.cash = 1_000_000_000.0
        sim.live.bank_backstop_months = 1
        expected = (sim.live.capital_requirement
                    * (1.0 + sim.live.bank_backstop_capacity) * 100_000.0)
        with patch.object(sim, "credit_capacity", return_value=0.0):
            sim._bank_capital_backstop()
        self.assertAlmostEqual(bank.bank_equity, expected)


class ForeclosureSolvencyTests(unittest.TestCase):
    def owner(self, config, identifier="proprietaire"):
        return Person(identifier, "Propriétaire", 0.0, owns_home=True,
                      mortgage_debt=50_000.0,
                      mortgage_arrears=config.mortgage_default_months)

    def test_no_sale_when_no_buyer_can_pay_the_forced_sale_price(self):
        config = SimulationConfig()
        price = config.housing_price * (1.0 - config.foreclosure_discount)
        for cash in (0.0, price - 1.0):
            with self.subTest(cash=cash):
                owner = self.owner(config)
                buyer = Company("immo", "Immobilier", "real_estate", cash, 1000.0)
                before = owner.__dict__.copy(), buyer.__dict__.copy()
                self.assertEqual(foreclose_homes([owner], [buyer], config), {})
                self.assertEqual((owner.__dict__, buyer.__dict__), before)

    def test_buyer_cannot_receive_a_second_home_without_paying_for_it(self):
        config = SimulationConfig()
        price = config.housing_price * (1.0 - config.foreclosure_discount)
        owners = [self.owner(config, str(i)) for i in range(2)]
        buyer = Company("immo", "Immobilier", "real_estate", price, 1000.0)
        sold = foreclose_homes(owners, [buyer], config)
        self.assertEqual(set(sold), {"0"})
        self.assertEqual(buyer.property_inventory, 1)
        self.assertEqual(buyer.cash, 0.0)
        self.assertTrue(owners[1].owns_home)
        self.assertEqual(owners[1].mortgage_debt, 50_000.0)

    def test_sold_home_and_repaid_debt_have_the_agreed_price(self):
        config = SimulationConfig()
        price = config.housing_price * (1.0 - config.foreclosure_discount)
        owner = self.owner(config)
        buyer = Company("immo", "Immobilier", "real_estate", 2.0 * price, 1000.0)
        before = buyer.cash + owner.cash
        sold = foreclose_homes([owner], [buyer], config)
        self.assertEqual(sold, {owner.id: (50_000.0, 50_000.0)})
        self.assertAlmostEqual(before - buyer.cash - owner.cash, 50_000.0)
        self.assertEqual(buyer.property_inventory, 1)


if __name__ == "__main__":
    unittest.main()
