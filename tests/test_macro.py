"""Lot J (novembre 2026) : obligations d'entreprise, commerce extérieur, négociation collective
(``economy/macro.py``). Désactivés par défaut ; activés, la monnaie est conservée."""

import dataclasses
import os
import re
import unittest

from economy.macro import (
    AGREEMENT_MONTHS,
    FOREIGN_HOLDING_SHARE,
    STRIKE_LOSS,
    UNION_SECTORS,
    settle_bondholders,
)
from economy.models import Company, Person, SimulationConfig
from economy.records import MonthFlows
from economy.simulation import EconomySimulation
from economy.wealth import company_balance_sheet, household_net_worth

_ORIGINAL_WARM_UP = EconomySimulation._warm_up
SWITCHES = ("corporate_bonds", "foreign_trade", "collective_bargaining")


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 1, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def money(sim: EconomySimulation) -> float:
    return sim.ledger.nonbank_cash(sim)


class MacroRegistryTests(unittest.TestCase):
    """Chaque réglage du lot J est listé et justifié dans la section « Réglages du lot J »
    d'AUTONOMIE.md, existe dans ``SimulationConfig`` et y est neutre pour l'identité."""

    ADDED = {*SWITCHES, "corporate_bond_share", "corporate_bond_term_months", "corporate_bond_leverage",
             "trade_openness", "trade_price_elasticity", "exchange_rate_speed", "union_power",
             "union_max_raise"}

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot J")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        registry = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), self.ADDED)
        for name in self.ADDED:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)
        for name in SWITCHES:
            self.assertIs(fields[name].default, False, name)

    def test_disabled_is_identical_and_creates_no_state(self) -> None:
        reference = simulation()
        explicit = simulation(**{name: False for name in SWITCHES})
        for _ in range(5):
            self.assertEqual(reference.step(), explicit.step())
        self.assertIsNone(getattr(explicit, "enterprise", None))
        self.assertTrue(all(c.bond_debt == 0.0 and not c.corporate_bonds for c in explicit.companies))
        self.assertTrue(all(not p.corporate_bonds for p in explicit.people))


class CorporateBondTests(unittest.TestCase):

    def _issuer(self, sim, **fields):
        company = next(c for c in sim.companies if c.active and c.sector == "goods" and c.employees)
        company.age_months, company.previous_profit, company.cash = 30, 50_000.0, 0.0
        for name, value in fields.items():
            setattr(company, name, value)
        for person in sim.people:
            person.cash += 50_000.0
        sim.deposit_rate = 0.0
        return company

    def test_issue_moves_savings_to_the_company_without_creating_money(self) -> None:
        sim = simulation(months=3, corporate_bonds=True)
        company = self._issuer(sim)
        need, before = sim._financing_need(company), money(sim)
        self.assertGreater(need, 0.0)
        for other in sim.companies:
            if other is not company:
                other.previous_profit = -1.0   # un seul émetteur
        sim._issue_corporate_bonds()
        self.assertGreater(company.bond_debt, 0.0)
        self.assertAlmostEqual(company.cash, company.bond_debt)
        self.assertAlmostEqual(sum(p.corporate_bonds.get(company.id, 0.0) for p in sim.people), company.bond_debt)
        self.assertGreater(company.bond_rate, max(0.0, sim.policy_rate))
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        equity = company_balance_sheet(company, sim.companies, sim.live)["Capitaux propres"]
        self.assertLessEqual(company.bond_debt, sim.config.corporate_bond_leverage * (equity + company.bond_debt) + 1e-6)

    def test_no_issue_when_unprofitable_young_or_cheaper_at_the_bank_of_savers(self) -> None:
        for fields in ({"previous_profit": -1.0}, {"age_months": 3}):
            sim = simulation(months=3, corporate_bonds=True)
            company = self._issuer(sim, **fields)
            for other in sim.companies:
                if other is not company:
                    other.previous_profit = -1.0
            sim._issue_corporate_bonds()
            self.assertEqual(company.bond_debt, 0.0)
        sim = simulation(months=3, corporate_bonds=True)
        company = self._issuer(sim)
        sim.deposit_rate = 1.0   # les dépôts rapportent plus : aucun acheteur
        sim._issue_corporate_bonds()
        self.assertEqual(company.bond_debt, 0.0)

    def test_coupon_and_amortisation_are_paid_to_the_holders(self) -> None:
        sim = simulation(months=2, corporate_bonds=True)
        company = next(c for c in sim.companies if c.active and c.sector == "goods")
        holder = sim.people[0]
        company.bond_debt, company.bond_rate, company.cash = 6000.0, 0.01, 1e6
        holder.corporate_bonds[company.id] = 6000.0
        cash, expenses, before = holder.cash, company.expenses, money(sim)
        sim._service_corporate_bonds()
        term = sim.config.corporate_bond_term_months
        self.assertAlmostEqual(holder.cash - cash, 60.0 + 6000.0 / term)
        self.assertAlmostEqual(company.expenses - expenses, 60.0)          # seul le coupon est une charge
        self.assertAlmostEqual(company.bond_debt, 6000.0 * (1.0 - 1.0 / term))
        self.assertAlmostEqual(holder.corporate_bonds[company.id], company.bond_debt)
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        company.cash = 0.0   # sans trésorerie : le coupon impayé s'ajoute à l'encours
        debt = company.bond_debt
        sim._service_corporate_bonds()
        self.assertAlmostEqual(company.bond_debt, debt * 1.01)

    def test_bonds_are_a_debt_and_a_household_asset(self) -> None:
        sim = simulation(corporate_bonds=True)
        company, person = sim.companies[0], sim.people[0]
        equity = company_balance_sheet(company, sim.companies, sim.live)["Capitaux propres"]
        worth = household_net_worth(person, sim.companies, sim.live)
        company.bond_debt = 1000.0
        person.corporate_bonds[company.id] = 1000.0
        self.assertAlmostEqual(company_balance_sheet(company, sim.companies, sim.live)["Capitaux propres"], equity - 1000.0)
        self.assertAlmostEqual(household_net_worth(person, sim.companies, sim.live), worth + 1000.0)

    def test_bondholders_rank_before_shareholders_in_a_liquidation(self) -> None:
        company = Company("x", "X", "goods", 0.0, 1.0, bond_debt=1000.0)
        first, second = Person("a", "A", 0.0), Person("b", "B", 0.0)
        first.corporate_bonds["x"], second.corporate_bonds["x"] = 750.0, 250.0
        self.assertAlmostEqual(settle_bondholders(company, [first, second], 400.0), 400.0)
        self.assertAlmostEqual(first.cash, 300.0)
        self.assertAlmostEqual(second.cash, 100.0)
        self.assertEqual((company.bond_debt, first.corporate_bonds, second.corporate_bonds), (0.0, {}, {}))
        company.bond_debt = 100.0
        first.corporate_bonds["x"] = 100.0
        self.assertAlmostEqual(settle_bondholders(company, [first], 5000.0), 100.0)   # jamais plus que la créance

    def test_absorbed_issuer_passes_its_bonds_to_the_acquirer(self) -> None:
        sim = simulation(corporate_bonds=True)
        buyer, target = [c for c in sim.companies if c.sector == "goods"][:2]
        holder = sim.people[0]
        target.bond_debt, target.bond_rate = 500.0, 0.02
        holder.corporate_bonds[target.id] = 500.0
        target.active, target.acquired_by = False, buyer.id
        sim._transfer_absorbed_bonds()
        self.assertEqual((target.bond_debt, buyer.bond_debt, buyer.bond_rate), (0.0, 500.0, 0.02))
        self.assertEqual(holder.corporate_bonds, {buyer.id: 500.0})

    def test_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=2, corporate_bonds=True)
        for person in sim.people:
            person.cash += 30_000.0
        sim.ledger.calibrate(sim)
        for company in sim.companies:
            company.age_months = 30
        for _ in range(48):
            record = sim.step()
            self.assertLess(abs(record.ledger_residual), 1e-3)
        for company in sim.companies:
            held = sum(p.corporate_bonds.get(company.id, 0.0) for p in sim.people)
            self.assertAlmostEqual(held, company.bond_debt if company.active else 0.0, places=4)
        self.assertGreater(sum(row.get("obligations_emissions", 0.0) for row in sim.enterprise.history), 0.0)


class ForeignTradeTests(unittest.TestCase):

    def test_imports_serve_unmet_demand_and_pay_the_world(self) -> None:
        sim = simulation(months=3, foreign_trade=True)
        foreign = sim._foreign_account()
        sim.enterprise.world_prices.update({"food": sim.food_price, "goods": sim.goods_price})
        foreign.cash = 0.0
        sim.enterprise.exchange_rate = 1.0
        flows = MonthFlows(unmet_food_demand=1.0, unmet_goods_demand=0.0)
        bought = sum(p.last_food_bought for p in sim.people)
        state, before = sim.public.cash, money(sim)
        sim._foreign_trade(flows)
        imported = sum(p.last_food_bought for p in sim.people) - bought
        dearer = (1.0 + sim.live.inflation_target) ** sim.config.trade_price_elasticity
        self.assertAlmostEqual(imported, sim.config.trade_openness * 1.0 / dearer, places=6)
        self.assertGreater(foreign.cash, 0.0)
        self.assertAlmostEqual(sim.public.cash - state, foreign.cash * sim.live.vat_rate)   # TVA à l'État
        self.assertAlmostEqual(flows.vat_revenue, foreign.cash * sim.live.vat_rate)
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        self.assertGreater(sim.enterprise.exchange_rate, 1.0)   # déficit : la monnaie se déprécie
        self.assertEqual(flows.exports, 0.0)

    def test_expensive_imports_are_bought_less_and_holdings_are_bounded(self) -> None:
        quantities = {}
        for rate in (1.0, 2.0):
            sim = simulation(months=3, foreign_trade=True)
            foreign = sim._foreign_account()
            sim.enterprise.world_prices.update({"food": sim.food_price, "goods": sim.goods_price})
            sim.enterprise.exchange_rate = rate
            bought = sum(p.last_food_bought for p in sim.people)
            sim._foreign_trade(MonthFlows(unmet_food_demand=100.0))
            quantities[rate] = sum(p.last_food_bought for p in sim.people) - bought
        self.assertLess(quantities[2.0], quantities[1.0])
        sim = simulation(months=3, foreign_trade=True)
        foreign = sim._foreign_account()
        foreign.cash = 10.0 * FOREIGN_HOLDING_SHARE * max(sim._annual_gdp(), 1.0) + 1e9
        sim.ledger.calibrate(sim)
        bought = sum(p.last_food_bought for p in sim.people)
        sim._foreign_trade(MonthFlows(unmet_food_demand=100.0))
        self.assertEqual(sum(p.last_food_bought for p in sim.people), bought)   # le monde ne vend plus

    def test_the_world_spends_what_it_earned_on_domestic_products(self) -> None:
        sim = simulation(months=3, foreign_trade=True)
        foreign = sim._foreign_account()
        foreign.cash = 100_000.0
        sim.ledger.calibrate(sim)
        firm = next(c for c in sim.companies if c.active and c.sector == "goods")
        firm.goods_inventory = 1e6
        revenue, before = sum(c.revenue for c in sim.companies), money(sim)
        rate = sim.enterprise.exchange_rate
        flows = MonthFlows()
        sim._foreign_trade(flows)
        self.assertAlmostEqual(flows.exports, 100_000.0 - foreign.cash)
        self.assertGreater(flows.exports, 0.0)
        self.assertAlmostEqual(sum(c.revenue for c in sim.companies) - revenue, flows.exports)
        self.assertLess(firm.goods_inventory, 1e6)
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        self.assertLess(sim.enterprise.exchange_rate, rate)   # excédent : la monnaie s'apprécie

    def test_exports_enter_gdp_and_foreign_cash_is_money(self) -> None:
        sim = simulation(months=3, foreign_trade=True)
        foreign = sim._foreign_account()
        before = money(sim)
        foreign.cash += 500.0
        self.assertAlmostEqual(money(sim), before + 500.0)
        foreign.cash -= 500.0
        from economy.records import build_record
        plain = build_record(sim, MonthFlows(), 0.0).nominal_gdp
        self.assertAlmostEqual(build_record(sim, MonthFlows(exports=1000.0), 0.0).nominal_gdp, plain + 1000.0)

    def test_run_keeps_accounts_exact_and_trade_roughly_balanced(self) -> None:
        sim = simulation(months=60, foreign_trade=True)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        history = sim.enterprise.history
        imports = sum(row.get("importations", 0.0) for row in history)
        exports = sum(row.get("exportations", 0.0) for row in history)
        self.assertGreater(imports, 0.0)
        self.assertAlmostEqual(imports - exports, sim.enterprise.foreign.cash, delta=1e-3)   # il détient l'écart
        self.assertTrue(0.2 <= sim.enterprise.exchange_rate <= 5.0)


class CollectiveBargainingTests(unittest.TestCase):

    def _with_history(self, sim, sector: str, inflation: float, wage_growth: float, margin: float = 0.0):
        """Douze mois d'historique fabriqué : inflation, hausse des salaires et marge du secteur."""
        base = sim.history[-1]
        sim.history = [
            dataclasses.replace(
                base,
                consumer_price_index=100.0 * (1.0 + inflation * index / 12.0),
                sector_average_wage={**base.sector_average_wage, sector: 1000.0 * (1.0 + wage_growth * index / 12.0)},
                sector_revenue={**base.sector_revenue, sector: 1000.0},
                sector_profit={**base.sector_profit, sector: 1000.0 * margin},
            )
            for index in range(13)
        ]

    def test_power_follows_the_government_and_unemployment(self) -> None:
        sim = simulation(collective_bargaining=True)
        powers = {}
        for orientation in (0.0, 100.0):
            sim.government.orientation = orientation
            powers[orientation] = sim._union_power(0.0)
        self.assertAlmostEqual(powers[0.0], 0.25)
        self.assertAlmostEqual(powers[100.0], 0.75)
        self.assertLess(sim._union_power(0.10), powers[100.0])
        self.assertEqual(sim._union_power(0.25), 0.0)

    def test_demand_is_the_real_wage_loss_plus_a_share_of_excess_margins(self) -> None:
        sim = simulation(months=2, collective_bargaining=True)
        self._with_history(sim, "goods", inflation=0.04, wage_growth=0.01)
        self.assertAlmostEqual(sim._union_demand("goods"), 0.03, places=4)
        self._with_history(sim, "goods", inflation=0.01, wage_growth=0.04)
        self.assertEqual(sim._union_demand("goods"), 0.0)
        target = sim.config.sector_margin_target["goods"]
        self._with_history(sim, "goods", inflation=0.0, wage_growth=0.0, margin=target + 0.04)
        self.assertAlmostEqual(sim._union_demand("goods"), 0.02, places=4)
        self._with_history(sim, "goods", inflation=0.50, wage_growth=0.0)
        self.assertAlmostEqual(sim._union_demand("goods"), sim.config.union_max_raise)

    def _negotiate(self, orientation: float, inflation: float):
        sim = simulation(months=2, collective_bargaining=True)
        sector = "goods"
        self._with_history(sim, sector, inflation=inflation, wage_growth=0.0)
        sim.government.orientation = orientation
        sim.month = UNION_SECTORS.index(sector)   # mois de négociation du secteur
        flows = MonthFlows(unemployment=0.0)
        flows.worker_skills = {p.id: (p.skill, p.technology_level) for p in sim.people}
        employers = [c for c in sim.companies if c.active and c.sector == sector and c.employees]
        offers = {c.id: c.wage_offer for c in employers}
        sim._collective_bargaining(flows)
        return sim, flows, employers, offers

    def test_agreement_raises_offers_and_holds_them_for_a_year(self) -> None:
        sim, flows, employers, offers = self._negotiate(orientation=100.0, inflation=0.02)
        agreement = sim.enterprise.agreements[-1]
        self.assertFalse(agreement["greve"])
        self.assertAlmostEqual(agreement["accord"], 0.02 * 0.75, places=4)
        for company in employers:
            self.assertAlmostEqual(company.wage_offer, offers[company.id] * (1.0 + agreement["accord"]))
            company.wage_offer *= 0.5   # l'employeur voudrait baisser
        sim._apply_wage_agreements()
        for company in employers:
            self.assertAlmostEqual(company.wage_offer, offers[company.id] * (1.0 + agreement["accord"]))
        sim.month += AGREEMENT_MONTHS   # accord échu : plus de plancher
        for company in employers:
            company.wage_offer = 1.0
        sim._apply_wage_agreements()
        self.assertTrue(all(c.wage_offer == 1.0 for c in employers))
        self.assertEqual(sim.enterprise.wage_floors, {})

    def test_wide_gap_triggers_a_strike_that_costs_work_and_wins_more(self) -> None:
        sim, flows, employers, _ = self._negotiate(orientation=30.0, inflation=0.05)
        agreement = sim.enterprise.agreements[-1]
        self.assertTrue(agreement["greve"])
        self.assertAlmostEqual(agreement["pouvoir"], 0.4)
        self.assertAlmostEqual(agreement["accord"], 0.05 * 0.4 + 0.5 * 0.05 * 0.6, places=4)
        people = {p.id: p for p in sim.people}
        striker = employers[0].employees[0]
        self.assertAlmostEqual(flows.worker_skills[striker][0], people[striker].skill * (1.0 - STRIKE_LOSS))
        outsider = next(p for p in sim.people if p.employer_id is None or p.employer_id == "state")
        self.assertEqual(flows.worker_skills[outsider.id][0], outsider.skill)
        self.assertEqual(sim.enterprise.month_stats["greves"], 1.0)
        # l'année suivante, même écart : pas de seconde grève avant trois ans
        sim.month += 12
        sim._collective_bargaining(flows)
        self.assertFalse(sim.enterprise.agreements[-1]["greve"])
        self.assertAlmostEqual(sim.enterprise.agreements[-1]["accord"], 0.05 * 0.4, places=4)

    def test_no_negotiation_without_history_or_claim(self) -> None:
        sim = simulation(months=2, collective_bargaining=True)
        sim.month = 0
        flows = MonthFlows()
        sim._collective_bargaining(flows)
        self.assertEqual(sim.enterprise.agreements, [])

    def test_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=72, collective_bargaining=True)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        for row in sim.enterprise.agreements:
            self.assertTrue(0.0 <= row["accord"] <= row["demande"] <= sim.config.union_max_raise + 1e-12)


class MacroTogetherTests(unittest.TestCase):

    def test_three_mechanisms_run_together_with_the_others(self) -> None:
        everything = {name: True for name in (
            *SWITCHES, "experience_learning", "property_upkeep", "consumer_preferences", "diversification",
            "conglomerate_takeovers", "retail_stores", "group_distribution", "private_startups",
            "shareholder_governance")}
        sim = simulation(months=0, staffless_closure_months=6, **everything)
        for _ in range(72):
            record = sim.step()
            self.assertLess(abs(record.ledger_residual), 1e-3)
        self.assertEqual(len(sim.enterprise_history()), 72)


if __name__ == "__main__":
    unittest.main()
