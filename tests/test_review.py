"""Régressions de la revue des phases 4 et 5, sur le moteur et ses contrats."""
import copy
import math
import random
import unittest
from unittest.mock import patch

from economy.models import Company, Person, SimulationConfig
from economy.simulation import EconomySimulation
from economy.finance import extend_credit
from economy.household_credit import extend_consumer_credit, service_consumer_credit
from economy.real_estate import service_mortgages
from economy.expectations import update_housing_gaps
from economy.records import validate_financial_accounts


class CreditIntegrationReviewTests(unittest.TestCase):
    def setUp(self):
        self.sim = EconomySimulation(SimulationConfig(seed=1001, population_size=60, warmup_months=0))
        self.banks = [c for c in self.sim.companies if c.sector == 'bank']

    def test_no_new_claim_when_all_banks_have_no_capital(self):
        for bank in self.banks:
            bank.bank_equity = 0
        before = [b.bank_loan_accounts.copy() for b in self.banks]
        with self.assertRaises(ValueError):
            self.sim._book_bank_loan('unknown', 100)
        self.assertEqual(before, [b.bank_loan_accounts for b in self.banks])

    def test_excess_and_nonfinite_credit_are_rejected_without_mutation(self):
        before = [b.bank_loan_accounts.copy() for b in self.banks]
        for amount in (self.sim.credit_capacity() + 1, float('nan'), float('inf'), -1):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.sim._book_bank_loan('unknown', amount)
        self.assertEqual(before, [b.bank_loan_accounts for b in self.banks])

    def test_company_credit_is_booked_before_money_and_contract_change(self):
        firm = Company('test', 'Test', 'food', 10000, 1400)
        original = self.sim._book_bank_loan
        def observe(account, amount):
            self.assertEqual((firm.cash, firm.debt), (10000, 0))
            original(account, amount)
        with patch.object(self.sim, '_book_bank_loan', side_effect=observe):
            loan = extend_credit(firm, 500, self.sim.credit_capacity(), self.sim.live,
                                 credit_transaction=self.sim._credit_transaction)
        self.assertEqual((loan, firm.cash, firm.debt), (500, 10500, 500))
        self.assertAlmostEqual(sum(b.bank_loan_accounts.get(firm.id, 0) for b in self.banks), loan)

    def test_partial_booking_failure_restores_company_and_banks(self):
        firm = Company('test', 'Test', 'food', 10000, 1400)
        before = copy.deepcopy(firm.__dict__)
        accounts = [b.bank_loan_accounts.copy() for b in self.banks]
        original = self.sim._book_bank_loan
        def fail(account, amount):
            original(account, amount)
            raise RuntimeError('panne injectée')
        with patch.object(self.sim, '_book_bank_loan', side_effect=fail):
            with self.assertRaises(RuntimeError):
                extend_credit(firm, 500, self.sim.credit_capacity(), self.sim.live,
                              credit_transaction=self.sim._credit_transaction)
        self.assertEqual(firm.__dict__, before)
        self.assertEqual(accounts, [b.bank_loan_accounts for b in self.banks])

    def test_failure_after_disbursement_restores_contract_and_cash(self):
        firm = Company('test', 'Test', 'food', 10000, 1400)
        before = copy.deepcopy(firm.__dict__)
        accounts = [b.bank_loan_accounts.copy() for b in self.banks]
        with self.assertRaises(RuntimeError):
            with self.sim._credit_transaction(firm, 500):
                firm.cash += 500
                firm.debt += 500
                firm.loan_spread = .5
                raise RuntimeError('panne après décaissement')
        self.assertEqual(firm.__dict__, before)
        self.assertEqual(accounts, [b.bank_loan_accounts for b in self.banks])

    def test_consumer_credit_is_booked_before_disbursement(self):
        person = Person('client', 'Client', 0, current_wage=1400)
        original = self.sim._book_bank_loan
        def observe(account, amount):
            self.assertEqual(account, 'consumer:client')
            self.assertEqual((person.cash, person.consumer_debt), (0, 0))
            original(account, amount)
        with patch.object(self.sim, '_book_bank_loan', side_effect=observe):
            loans = extend_consumer_credit([person], 1000, self.sim.live, .01,
                                           credit_transaction=self.sim._consumer_credit_transaction)
        self.assertGreater(loans['client'], 0)
        self.assertEqual(person.cash, person.consumer_debt)

    def test_month_end_check_detects_misassigned_claims(self):
        validate_financial_accounts(self.sim)
        bank = self.banks[0]
        account = next(iter(bank.bank_loan_accounts))
        bank.bank_loan_accounts[account] -= .01
        bank.bank_loan_accounts['unknown'] = .01
        with self.assertRaises(ArithmeticError):
            validate_financial_accounts(self.sim)

    def test_month_end_check_detects_nonfinite_claim(self):
        self.banks[0].bank_loan_accounts['unknown'] = math.nan
        with self.assertRaises(ArithmeticError):
            validate_financial_accounts(self.sim)

    def test_month_end_check_runs_in_engine(self):
        with patch('economy.records.validate_financial_accounts', side_effect=ArithmeticError('injected')):
            with self.assertRaises(ArithmeticError):
                self.sim.step()

    def test_operating_credit_books_before_disbursement(self):
        from economy.finance import request_operating_credit
        firm = Company('test', 'Test', 'food', 0, 1400, target_employees=2,
                       productive_capital_value=100000)
        original = self.sim._book_bank_loan
        def observe(account, amount):
            self.assertEqual((firm.cash, firm.debt), (0, 0))
            original(account, amount)
        with patch.object(self.sim, '_book_bank_loan', side_effect=observe):
            loan = request_operating_credit([firm], [], self.sim.credit_capacity(), self.sim.live,
                                            credit_transaction=self.sim._credit_transaction)
        self.assertGreater(loan, 0)
        self.assertEqual(firm.debt, loan)

    def test_construction_booking_failure_does_not_start_or_pay_work(self):
        from economy.real_estate import build_housing
        firm = Company('test', 'Test', 'real_estate', 1e6, 1400, housing_gap=1)
        before = copy.deepcopy(firm.__dict__)
        land = self.sim.resources.land_units
        with patch.object(self.sim, '_book_bank_loan', side_effect=RuntimeError('panne')):
            with self.assertRaises(RuntimeError):
                build_housing([firm], self.sim.resources, config=self.sim.live,
                              bank_reserves=self.sim.credit_capacity(),
                              credit_transaction=self.sim._credit_transaction)
        self.assertEqual(firm.__dict__, before)
        self.assertEqual(self.sim.resources.land_units, land)

    def test_failed_startup_loan_does_not_take_founder_capital_or_issue_shares(self):
        from economy.finance import create_household_startups
        person = Person('founder', 'F', 1e7, entrepreneurial_spirit=1)
        config = SimulationConfig(company_founder_probability=1, entry_signal_scale=.001)
        before = copy.deepcopy(person.__dict__)
        with patch('economy.finance.sector_entry_signals', return_value={'food': 1}), \
                patch.object(self.sim, '_book_bank_loan', side_effect=RuntimeError('panne')):
            with self.assertRaises(RuntimeError):
                create_household_startups(self.sim.companies, [person], 1,
                                          self.sim.credit_capacity(), config, random.Random(1),
                                          credit_transaction=self.sim._credit_transaction)
        self.assertEqual(person.__dict__, before)


class ForecastAndPaymentReviewTests(unittest.TestCase):
    def test_occupied_stock_is_not_multiplied_by_forecast_noise(self):
        firm = Company('r', 'R', 'real_estate', 100000, 1400,
                       occupied_units=100, property_inventory=105)
        config = SimulationConfig(demand_forecast_error=.3)
        for seed in range(100):
            update_housing_gaps([firm], config, random.Random(seed))
            self.assertEqual(firm.housing_gap, 0)

    def test_sales_forecast_decays_when_no_more_homes_are_sold(self):
        firm = Company('r', 'R', 'real_estate', 100000, 1400,
                       occupied_units=100, property_inventory=110, previous_homes_sold=10)
        config = SimulationConfig(demand_forecast_error=0)
        update_housing_gaps([firm], config, random.Random(1))
        self.assertGreater(firm.housing_gap, 0)
        firm.previous_homes_sold = 0
        for _ in range(60):
            update_housing_gaps([firm], config, random.Random(1))
        self.assertEqual(firm.housing_gap, 0)

    def test_subcent_mortgage_is_not_forgiven_without_payment(self):
        person = Person('p', 'P', 0, mortgage_debt=.005, mortgage_rate=0,
                        mortgage_payment=.005, mortgage_months_remaining=1)
        service_mortgages([person])
        self.assertEqual(person.mortgage_debt, .005)
        person.cash = .005
        service_mortgages([person])
        self.assertEqual((person.cash, person.mortgage_debt), (0, 0))

    def test_subcent_consumer_credit_is_not_forgiven_without_payment(self):
        person = Person('p', 'P', 0, consumer_debt=.005, consumer_rate=0)
        config = SimulationConfig(consumer_credit_term_months=1)
        service_consumer_credit([person], config)
        self.assertEqual(person.consumer_debt, .005)
        person.cash = .005
        service_consumer_credit([person], config)
        self.assertEqual((person.cash, person.consumer_debt), (0, 0))

    def test_invalid_transfers_do_not_mutate_balances(self):
        from economy.payments import transfer_cash
        sender, receiver = Person('p', 'P', 100), Person('q', 'Q', 200)
        for amount in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                transfer_cash(sender, receiver, amount)
            self.assertEqual((sender.cash, receiver.cash), (100, 200))

    def test_negative_cash_never_reverses_rent_payment(self):
        from economy.real_estate import allocate_housing_and_collect_rent
        person = Person('tenant', 'T', -1e-12, income_this_turn=1000)
        firm = Company('r', 'R', 'real_estate', 10000, 1400, property_inventory=1)
        self.assertEqual(allocate_housing_and_collect_rent([firm], [person], 500), 0)
        self.assertEqual((person.cash, firm.cash), (-1e-12, 10000))

    def test_bond_orders_cannot_sell_unowned_bonds_or_buy_without_cash(self):
        from economy.stock_market import trade_bonds_with_banks
        bank = Company('b', 'B', 'bank', 0, 1400, bond_holdings=100)
        person = Person('p', 'P', 20, bond_holdings=10)
        traded = trade_bonds_with_banks([(person, -100)], [bank])
        self.assertEqual((traded, person.cash, person.bond_holdings, bank.bond_holdings), (-10, 30, 0, 110))
        traded = trade_bonds_with_banks([(person, 1000)], [bank])
        self.assertEqual((traded, person.cash, person.bond_holdings, bank.bond_holdings), (30, 0, 30, 80))


if __name__ == '__main__':
    unittest.main()
