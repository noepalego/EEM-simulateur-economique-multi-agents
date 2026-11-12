"""Équivalence exacte des valorisations réutilisées dans une même phase."""

import copy
import unittest
from unittest.mock import patch

from economy import macro
from economy.models import SimulationConfig
from economy.simulation import EconomySimulation
from economy.wealth import company_balance_sheet, household_net_worth, share_prices
from performance_probe import digest


def simulation(**settings):
    config = dict(seed=11, population_size=80, warmup_months=0,
                  influence_ranking=True, personal_popularity=True,
                  shareholder_governance=True, corporate_bonds=True)
    config.update(settings)
    sim = EconomySimulation(SimulationConfig(**config))
    for _ in range(4):
        sim.step()
    return sim


def current_wealth(sim):
    prices = share_prices(sim.companies)
    return {p.id: household_net_worth(p, sim.companies, sim.live, prices) for p in sim.people}


def original_issue(sim):
    """Oracle gelé avant la réutilisation des bilans (deux lectures par candidat).

    Le calcul financier est gardé pour comparer les encours, paiements, caches
    et dictionnaires exactement, y compris un placement partiel et un refinancement.
    """
    live = sim.live
    excluded = sim._public_controlled_ids()
    minimum = macro.BOND_MINIMUM_ISSUE * sim._wage_scale()
    issues = []
    for company in sim.companies:
        if (
            not company.active or company.sector == "bank" or company.id in excluded
            or company.age_months < macro.BOND_MIN_AGE_MONTHS or company.previous_profit <= 0.0
            or not company.employees
        ):
            continue
        rate = sim._corporate_bond_rate(company)
        if rate <= sim.deposit_rate:
            continue
        equity = company_balance_sheet(company, sim.companies, live)["Capitaux propres"]
        room = max(0.0, live.corporate_bond_leverage) * max(0.0, equity) - company.bond_debt
        need = sim._financing_need(company)
        bank_rate = company.debt_interest_rate if company.debt_interest_rate is not None else 0.0
        refinancing = company.debt if company.debt > 0.0 and rate < bank_rate else 0.0
        wanted = min(need + refinancing, room)
        if wanted >= minimum:
            issues.append((company, wanted, rate, min(need, wanted)))
    if not issues:
        return
    orders = sim._bond_orders()
    demand = sum(amount for _, amount in orders)
    supply = sum(wanted for _, wanted, _, _ in issues)
    if demand <= 1.0:
        return
    placed = min(demand, supply)
    buyer_fill, issue_fill = placed / demand, placed / supply
    for company, wanted, rate, need in issues:
        amount = wanted * issue_fill
        for holder, order in orders:
            part = order * buyer_fill * wanted / supply
            macro.transfer_cash(holder, company, part)
            holder.corporate_bonds[company.id] = holder.corporate_bonds.get(company.id, 0.0) + part
        total = company.bond_debt + amount
        company.bond_rate = (company.bond_debt * company.bond_rate + amount * rate) / total
        company.bond_debt = total
        repaid = sim._prepay_company_loan(company, max(0.0, amount - need * issue_fill))
        sim._enterprise_count("obligations_emissions", amount)
        sim._enterprise_count("obligations_refinancement", repaid)


class ValuationReuseTests(unittest.TestCase):
    def test_explicit_wealth_keeps_scores_state_and_population_order(self):
        actual = simulation()
        actual.people[0].cash = -1.0e9
        actual.people[1].fund_value = 345.678
        actual.people[2].corporate_bonds = {"premier": 1.0e16, "second": 1.1, "tiers": 1.0e-8}
        reference = copy.deepcopy(actual)
        expected = reference.influence_scores(refresh=True)
        # L'ordre fourni ne doit pas remplacer celui des ménages dans les sommes.
        values = dict(reversed(list(current_wealth(actual).items())))
        with patch("economy.power.household_net_worth", side_effect=AssertionError("recalcul redondant")):
            result = actual.influence_scores(refresh=True, household_wealth=values)
        self.assertEqual(digest(result), digest(expected))
        self.assertEqual(digest(actual), digest(reference))

    def test_external_refresh_reads_cash_prices_and_holdings_again(self):
        sim = simulation()
        before = sim.influence_scores(refresh=True)
        person = sim.people[0]
        person.cash += 1_234_567.89
        sim.companies[0].stock_price *= 1.75
        person.shares[sim.companies[1].id] = 123.4
        expected = max(0.0, current_wealth(sim)[person.id])
        # L'historique ancien est volontairement conservé après l'intervention.
        self.assertNotEqual(person.wealth_history[-1], expected)
        current = sim.influence_scores(refresh=True)
        self.assertEqual(current[person.id]["patrimoine"].hex(), expected.hex())
        self.assertNotEqual(current[person.id]["patrimoine"], before[person.id]["patrimoine"])

    def test_power_record_reuses_the_same_wealth_without_changing_any_state(self):
        actual = simulation()
        reference = copy.deepcopy(actual)
        values = current_wealth(actual)
        reference._power_record()
        actual._power_record(household_wealth=values)
        self.assertEqual(digest(actual), digest(reference))

    def test_institutional_recording_does_not_change_wealth_determinants(self):
        sim = simulation()
        before = current_wealth(sim)
        # Ces lectures sont celles qui séparent l'enregistrement des fortunes du pouvoir.
        from economy.records import MonthFlows
        sim._adjust_policy_rate(0.01, 0.05)
        sim._learn_credibility()
        sim._record_institutions(MonthFlows())
        self.assertEqual(digest(current_wealth(sim)), digest(before))


class CorporateBalanceReuseTests(unittest.TestCase):
    def issuer_case(self, *, refinancing=False, thin_demand=False, no_rate=False):
        sim = simulation()
        eligible = [c for c in sim.companies if c.active and c.sector != "bank" and c.employees][:2]
        self.assertEqual(len(eligible), 2)
        for company in sim.companies:
            company.previous_profit = -1.0
        for index, company in enumerate(eligible):
            company.age_months = 30
            company.previous_profit = 50_000.0
            company.cash = 0.0
            company.productive_capital_value += 200_000.0
            company.target_employees = 20 + index
            company.wage_offer = 1000.0
            company.debt_interest_rate = 0.5 if refinancing else 0.0
        for index, person in enumerate(sim.people):
            person.cash = 100.0 if thin_demand else 50_000.0 + index * 0.1
            person.corporate_bonds = {}
        sim.deposit_rate = 1.0 if no_rate else 0.0
        return sim, eligible

    def test_issues_match_the_frozen_two_balance_oracle_exactly(self):
        for options in ({}, {"refinancing": True}, {"thin_demand": True}, {"no_rate": True}):
            with self.subTest(options=options):
                actual, _ = self.issuer_case(**options)
                expected = copy.deepcopy(actual)
                original_issue(expected)
                actual._issue_corporate_bonds()
                self.assertEqual(digest(actual), digest(expected))

    def test_each_eligible_issuer_has_one_balance_and_the_price_table_is_local(self):
        sim, eligible = self.issuer_case()
        with patch("economy.macro.company_balance_sheet", wraps=company_balance_sheet) as balances:
            with patch("economy.macro.share_prices", wraps=share_prices) as prices:
                sim._issue_corporate_bonds()
        self.assertEqual(balances.call_count, len(eligible))
        self.assertEqual(prices.call_count, 1)
        self.assertFalse(any(name.startswith("_bond_prices") for name in vars(sim)))

    def test_standalone_bank_rate_still_reads_its_loan_book(self):
        sim = simulation()
        bank = next(c for c in sim.companies if c.sector == "bank")
        bank.bank_loan_accounts["nouveau"] = 12345.67
        reference = copy.deepcopy(sim)
        expected_bank = next(c for c in reference.companies if c.id == bank.id)
        balance = company_balance_sheet(expected_bank, reference.companies, reference.live)
        assets = max(balance["Total des actifs"], 1.0)
        leverage = min(2.0, (expected_bank.debt + expected_bank.bond_debt) / assets)
        expected = max(0.0, reference.policy_rate) + reference.live.credit_risk_spread * (0.5 + leverage)
        self.assertEqual(sim._corporate_bond_rate(bank).hex(), expected.hex())
        self.assertEqual(digest(sim), digest(reference))


if __name__ == "__main__":
    unittest.main()
