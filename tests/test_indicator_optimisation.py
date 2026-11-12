"""Valeurs et ordres des agrégats réutilisés pendant l'enregistrement."""

import math
import unittest

from economy.models import Company, MarketRecord, Person, SimulationConfig
from economy.records import MonthFlows, build_record
from economy.sectors import FIRM_SECTORS
from economy.simulation import EconomySimulation
from economy.wealth import capture_wealth_record, enrich_wealth_record, gini


def record():
    return MarketRecord(0, 120.0, 1400.0, 0.0, 0.0, 700.0, 0.0, 0.0, "aucun")


class WealthRecordReuseTests(unittest.TestCase):
    def test_ranked_ratios_preserve_their_original_addition_orders(self):
        values = [1e16, 1.0, -1e16, math.nextafter(1.0, 2.0), 0.0, -0.0,
                  1e-100, 1234.567, -1234.567, 1e15] * 13
        people = [Person(str(i), str(i), value, income_this_turn=i * 0.1)
                  for i, value in enumerate(values)]
        result = record()
        enrich_wealth_record(result, people, [], SimulationConfig(), price_index=123.4)
        ranked = sorted(values, reverse=True)
        total = sum(values)
        self.assertEqual(result.household_wealth_total.hex(), total.hex())
        self.assertEqual(result.household_wealth_median, sorted(values)[len(values) // 2])
        self.assertEqual(result.household_gini, gini(values))
        self.assertEqual(result.household_top10_share,
                         sum(ranked[:max(1, (len(values) + 9) // 10)]) / total)
        self.assertEqual(result.household_top1_share,
                         sum(ranked[:max(1, len(values) // 100)]) / sum(ranked))
        self.assertEqual([p.wealth_history[-1] for p in people], values)
        for person in people:
            row = person.financial_history[-1]
            self.assertEqual(row["Fortune réelle"], row["Fortune"] * 100.0 / 123.4)

    def test_empty_and_negative_populations_keep_zero_ratios(self):
        for values in ([], [-5.0, -1000.0, 0.0], [0.0, -0.0]):
            people = [Person(str(i), str(i), value) for i, value in enumerate(values)]
            result = record()
            enrich_wealth_record(result, people, [], SimulationConfig())
            self.assertEqual(result.household_top10_share, 0.0)
            self.assertEqual(result.household_top1_share, 0.0)
            self.assertEqual(result.household_gini, 0.0)
            self.assertEqual(len(people), len([p.financial_history[-1] for p in people]))

    def test_public_capture_contract_and_history_columns_are_preserved(self):
        person = Person("p", "Ménage", 250.25, shares={"firm": 2.0},
                        mortgage_debt=40.0, bond_holdings=3.5)
        firm = Company("firm", "Société", "food", 1000.0, 10.0, stock_price=12.5)
        values = capture_wealth_record([person], [firm], SimulationConfig(), 5)
        self.assertIsInstance(values, tuple)
        self.assertEqual(len(values), 6)
        self.assertEqual(values[2], 250.25 + 25.0 + 3.5 - 40.0)
        self.assertEqual(list(person.financial_history[-1]),
                         ["Période", "Trésorerie nette", "Revenus", "Dividendes", "Dépenses",
                          "Fortune", "Actions", "Valeur immobilière"])
        self.assertEqual(person.financial_history[-1]["Période"], 5.0)

    def test_manually_shared_history_keeps_the_historical_top_one_definition(self):
        history = []
        people = [Person("a", "Premier", 100.0, wealth_history=history),
                  Person("b", "Second", 200.0, wealth_history=history)]
        result = record()
        values = {}
        enrich_wealth_record(result, people, [], SimulationConfig(), household_wealth=values)
        self.assertEqual(result.household_wealth_total, 300.0)
        self.assertEqual(result.household_top10_share, 200.0 / 300.0)
        self.assertEqual(result.household_top1_share, 0.5)
        self.assertEqual(values, {"a": 100.0, "b": 200.0})
        self.assertIs(people[0].wealth_history, people[1].wealth_history)


class SectorRecordReuseTests(unittest.TestCase):
    def test_closed_and_empty_sectors_keep_stock_flow_and_money_definitions(self):
        sim = EconomySimulation(SimulationConfig(seed=31, population_size=40, warmup_months=0))
        for i, company in enumerate(sim.companies):
            company.revenue = (1e16, -1e16, 0.125)[i % 3]
            company.profit = company.revenue * 0.5
            company.wage_bill = i + 0.5
            if company.sector == "food":
                company.active, company.exit_month = False, -1
        closing = next(c for c in sim.companies if c.sector == "goods")
        closing.active, closing.exit_month = False, sim.month
        operated = [c for c in sim.companies if c.active or c.exit_month == sim.month]
        money = sim.ledger.nonbank_cash(sim)
        deposits = sim.ledger.deposits_from_cash(sim)
        residual = sim.ledger.residual(sim)
        result = build_record(sim, MonthFlows(), 0.0, operated=operated)
        self.assertEqual(result.money_supply, money)
        self.assertEqual(result.deposits, max(0.0, deposits))
        self.assertEqual(result.ledger_residual, residual)
        for sector, _ in FIRM_SECTORS:
            staff = sum(len(c.employees) for c in sim.companies if c.active and c.sector == sector)
            payroll = sum(c.wage_bill for c in sim.companies if c.active and c.sector == sector)
            self.assertEqual(result.sector_employment[sector], staff)
            self.assertEqual(result.sector_average_wage[sector], payroll / max(1, staff))
            self.assertEqual(result.sector_revenue[sector], sum(c.revenue for c in operated if c.sector == sector))
            self.assertEqual(result.sector_profit[sector], sum(c.profit for c in operated if c.sector == sector))
        self.assertIs(type(result.sector_revenue["food"]), int)
        self.assertEqual(result.company_prices[closing.id], 0.0)


if __name__ == "__main__":
    unittest.main()
