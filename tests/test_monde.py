"""Lots L à S (novembre 2026) : médias (``economy/media.py``), relations bancaires
(``economy/bank_relations.py``), agents apprenants (``economy/learning.py``) et monde à plusieurs
régions (``economy/world.py``). Désactivés par défaut ; activés, la monnaie de chaque région est
conservée."""

import dataclasses
import os
import re
import unittest

from economy.bank_relations import account_owner
from economy.learning import PRECAUTION, TRAITS
from economy.media import ADVERTISER_CONFLICT, MEDIA_SECTOR, Outlet, ad_factor
from economy.models import SimulationConfig
from economy.records import MonthFlows
from economy import world_views
from economy.institutions import display_name
from economy.simulation import EconomySimulation
from economy.world import (
    CONTROL,
    PRODUCTIVITY_TRANSFER,
    REGION_PROFILES,
    Intent,
    World,
    WorldConfig,
    region_config,
)

SWITCHES = ("media_sector", "bank_relationships", "adaptive_agents")
PARAMETERS = (
    "media_outlets", "media_subscription_share", "media_ad_share", "media_ad_reach", "media_vote_weight",
    "bank_review_share", "bank_run_threshold", "bank_specialization",
    "learning_period_months", "learning_step", "imitation_rate",
)
WORLD_SETTINGS = tuple(
    f.name for f in dataclasses.fields(WorldConfig) if f.name not in ("base", "profiles")
)


def simulation(months: int = 1, population: int = 80, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=population, warmup_months=0, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def money(sim: EconomySimulation) -> float:
    return sim.ledger.nonbank_cash(sim) + sum(c.cash for c in sim.companies if c.sector == "bank")


def media_simulation(months: int = 3, **overrides) -> EconomySimulation:
    """Simulation où trois ménages ont de quoi fonder un média dès le premier mois."""
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, warmup_months=0,
                                             media_sector=True, **overrides))
    for person in sim.people[:9]:
        person.cash += 200_000.0
    for _ in range(months):
        sim.step()
    return sim


def publish(sim: EconomySimulation) -> list:
    """Donne un journaliste à chaque média qui n'en a pas (une économie naissante de quatre-vingts
    ménages ne fait pas vivre trois rédactions) et rend les médias en activité."""
    outlets = sim._active_outlets()
    for company in outlets:
        if not company.employees:
            company.employees.append("journaliste")
    sim._media_readers()
    return outlets


def world(regions: int = 2, months: int = 0, population: int = 80, profiles=(), **overrides) -> World:
    result = World(WorldConfig(
        base=SimulationConfig(seed=11, population_size=population, warmup_months=0),
        regions=regions, profiles=profiles, **overrides,
    ))
    for _ in range(months):
        result.step()
    return result


def registry(title: str) -> dict[str, str]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start = text.index(title)
    end = text.find("\n## ", start + 1)
    section = text[start:] if end < 0 else text[start:end]
    return {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}


class NewLotsRegistryTests(unittest.TestCase):
    """Chaque réglage des lots M, R et S est listé et justifié dans AUTONOMIE.md, existe dans
    ``SimulationConfig`` et y est neutre pour l'identité ; de même chaque réglage du monde."""

    def test_settings_are_registered_and_neutral(self) -> None:
        listed = registry("## Réglages des lots M, R et S")
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(listed), {*SWITCHES, *PARAMETERS})
        for name in listed:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(listed[name]), 60)
        for name in SWITCHES:
            self.assertIs(fields[name].default, False, name)

    def test_world_settings_are_registered(self) -> None:
        listed = registry("## Réglages du monde")
        self.assertEqual(set(listed), set(WORLD_SETTINGS))
        for name, text in listed.items():
            self.assertGreaterEqual(len(text), 60, name)

    def test_disabled_is_identical_and_creates_no_state(self) -> None:
        reference = simulation(months=0)
        explicit = simulation(months=0, **{name: False for name in SWITCHES})
        for _ in range(5):
            self.assertEqual(reference.step(), explicit.step())
        for attribute in ("media", "bankrel", "learning", "world_accounts", "nonresident"):
            self.assertIsNone(getattr(explicit, attribute, None), attribute)


class MediaTests(unittest.TestCase):

    def test_households_found_media_with_their_own_money(self) -> None:
        sim = simulation(months=1, media_sector=True)
        for person in sim.people[:9]:
            person.cash += 200_000.0
        before = money(sim)
        sim._found_media(3)
        outlets = [c for c in sim.companies if c.sector == MEDIA_SECTOR]
        self.assertEqual(len(outlets), 3)
        self.assertAlmostEqual(money(sim), before, places=6)
        for company in outlets:
            founder = next(p for p in sim.people if p.id == company.founder_id)
            self.assertGreater(founder.shares[company.id], 0.0)
            self.assertEqual(sim.media.outlets[company.id].line, sim.institutions.lean[founder.id])

    def test_founders_come_from_the_three_political_camps(self) -> None:
        sim = media_simulation(months=1)
        lines = sorted(outlet.line for outlet in sim.media.outlets.values())
        self.assertEqual(len(lines), 3)
        self.assertLess(lines[0], 40.0)
        self.assertGreater(lines[-1], 60.0)

    def test_subscriptions_conserve_money_and_pay_vat(self) -> None:
        sim = media_simulation(months=3)
        self.assertTrue(publish(sim))
        before, treasury = money(sim), sim.public.cash
        flows = MonthFlows()
        sim._media_subscriptions(flows)
        self.assertAlmostEqual(money(sim), before, places=6)
        self.assertGreater(flows.household_services, 0.0)
        self.assertAlmostEqual(sim.public.cash - treasury, flows.vat_revenue, places=6)
        self.assertAlmostEqual(flows.vat_revenue, flows.household_services * sim.live.vat_rate, places=6)

    def test_a_medium_without_journalists_collects_nothing(self) -> None:
        sim = media_simulation(months=3)
        for company in sim._active_outlets():
            company.employees.clear()
        flows = MonthFlows()
        sim._media_subscriptions(flows)
        self.assertEqual(flows.household_services, 0.0)

    def test_advertising_is_paid_to_media_and_shifts_purchase_weights(self) -> None:
        sim = media_simulation(months=3)
        publish(sim)
        advertiser = next(c for c in sim.companies if c.active and c.sector == "food" and c.employees)
        advertiser.previous_profit = 1_000.0
        advertiser.previous_revenue = 100_000.0
        advertiser.cash += 100_000.0
        before = money(sim)
        sim._media_operations()
        self.assertAlmostEqual(money(sim), before, places=6)
        self.assertGreater(sim.media.ad_stock[advertiser.id], 0.0)
        weights = sim._media_affinity("food", None)
        factor = weights[sim.people[0].id][advertiser.id]
        self.assertGreater(factor, 1.0)
        self.assertLessEqual(factor, 1.0 + sim.live.media_ad_reach)
        self.assertIsNone(sim._media_affinity("technology", None))

    def test_advertising_has_decreasing_returns(self) -> None:
        self.assertEqual(ad_factor(0.0, 1000.0, 0.3), 1.0)
        small, large, huge = (ad_factor(s, 1000.0, 0.3) for s in (1_000.0, 10_000.0, 10_000_000.0))
        self.assertLess(small, large)
        self.assertLess(large - small, 9 * (small - 1.0))
        self.assertLess(huge, 1.3)

    def test_the_controlling_owner_pulls_the_editorial_line(self) -> None:
        sim = media_simulation(months=2)
        company = sim._active_outlets()[0]
        outlet = sim.media.outlets[company.id]
        buyer = next(p for p in sim.people if abs(sim.institutions.lean[p.id] - outlet.line) > 30.0)
        for holder in sim.people:
            holder.shares.pop(company.id, None)
        buyer.shares[company.id] = company.shares_outstanding - company.treasury_shares
        gap = abs(sim.institutions.lean[buyer.id] - outlet.line)
        sim._media_lines()
        self.assertEqual(outlet.owner, buyer.id)
        self.assertLess(abs(sim.institutions.lean[buyer.id] - outlet.line), gap)

    def test_tone_follows_the_distance_to_the_government(self) -> None:
        sim = media_simulation(months=2)
        orientation = sim.government.orientation
        self.assertEqual(sim._media_tone(Outlet(line=orientation, founder="x")), 1.0)
        self.assertEqual(sim._media_tone(Outlet(line=(orientation + 50.0) % 100.0, founder="x")), -1.0)
        reader = next(pid for pid, cid in sim.media.readers.items())
        outlet = sim.media.outlets[sim.media.readers[reader]]
        outlet.line = orientation
        self.assertGreater(sim._media_vote_bias(reader), 0.0)
        outlet.line = (orientation + 50.0) % 100.0
        self.assertLess(sim._media_vote_bias(reader), 0.0)

    def test_a_medium_does_not_investigate_its_owner_or_a_big_advertiser(self) -> None:
        sim = media_simulation(months=3, political_favoritism=True, business_subsidies=True)
        company = publish(sim)[0]
        del company.employees[1:]
        outlet = sim.media.outlets[company.id]
        affair = {"presidence": "inconnu", "patron": "p0070", "entreprise": "goods_1", "nom": "X"}
        outlet.owner = "p0001"
        outlet.advertisers.clear()
        free = sim._media_effort(company, affair)
        self.assertGreater(free, 0.0)
        company.employees.extend(["a", "b", "c", "d"])
        self.assertGreater(sim._media_effort(company, affair), free)
        outlet.owner = "p0070"
        self.assertEqual(sim._media_effort(company, affair), 0.0)
        outlet.owner = "p0001"
        outlet.advertisers = {"goods_1": ADVERTISER_CONFLICT, "food_1": 1.0 - ADVERTISER_CONFLICT}
        self.assertEqual(sim._media_effort(company, affair), 0.0)

    def test_accounts_stay_exact_with_media(self) -> None:
        sim = media_simulation(months=0)
        start = sim.ledger.residual(sim)  # la trésorerie donnée aux futurs fondateurs
        for _ in range(40):
            sim.step()
        self.assertLess(abs(sim.ledger.residual(sim) - start), 1e-4)
        self.assertEqual(len(sim.media.history), 40)
        self.assertTrue(sim.media.outlets)


class BankRelationsTests(unittest.TestCase):

    def test_every_client_has_one_active_bank(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        banks = {b.id for b in sim._active_banks()}
        for agent_id, _ in sim._bank_clients():
            self.assertIn(sim.bankrel.home[agent_id], banks)
        deposits = sim.bank_deposits()
        expected = sum(max(0.0, holder.cash) for _, holder in sim._bank_clients()) * sim._deposit_ratio()
        self.assertAlmostEqual(sum(deposits.values()), expected, places=4)

    def test_a_loan_goes_to_the_borrower_s_bank_first(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        borrower = next(c for c in sim.companies if c.active and c.sector == "goods")
        home = next(b for b in sim._active_banks() if b.id == sim._home_bank(borrower.id))
        amount = 0.25 * sim._bank_capacity(home)
        self.assertGreater(amount, 0.0)
        before = {b.id: b.bank_loan_accounts.get(borrower.id, 0.0) for b in sim._active_banks()}
        sim._book_bank_loan(borrower.id, amount)
        for bank in sim._active_banks():
            change = bank.bank_loan_accounts.get(borrower.id, 0.0) - before[bank.id]
            self.assertAlmostEqual(change, amount if bank is home else 0.0, places=6)

    def test_a_large_loan_spills_over_to_the_other_banks(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        borrower = next(c for c in sim.companies if c.active and c.sector == "goods")
        home = next(b for b in sim._active_banks() if b.id == sim._home_bank(borrower.id))
        capacity = sim._bank_capacity(home)
        amount = capacity + 0.5 * (sim.credit_capacity() - capacity)
        parts = dict((bank.id, part) for bank, part in sim._allocate_loan(borrower.id, amount))
        self.assertAlmostEqual(sum(parts.values()), amount, places=4)
        self.assertAlmostEqual(parts[home.id], capacity, places=4)
        for bank in sim._active_banks():
            self.assertLessEqual(parts.get(bank.id, 0.0), sim._bank_capacity(bank) + 1e-4)

    def test_household_loan_accounts_name_their_owner(self) -> None:
        self.assertEqual(account_owner("mortgage:p0003"), ("p0003", "mortgage"))
        self.assertEqual(account_owner("consumer:p0003"), ("p0003", "consumer"))
        self.assertEqual(account_owner("food_1"), ("food_1", None))

    def test_clients_flee_a_fragile_bank(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        fragile = sim._active_banks()[0]
        fragile.bank_loan_accounts["goods_1"] = fragile.bank_loan_accounts.get("goods_1", 0.0) + 5_000_000.0
        fragile.bank_equity = 1_000.0
        self.assertLess(sim._bank_solidity(fragile), sim.live.bank_run_threshold)
        clients = sum(1 for home in sim.bankrel.home.values() if home == fragile.id)
        for _ in range(3):
            sim._review_banks(sim._active_banks(), sim._bank_clients())
        left = sum(1 for home in sim.bankrel.home.values() if home == fragile.id)
        self.assertLess(left, 0.5 * clients)
        self.assertGreater(sim.bankrel.month_stats.get("fuites", 0), 0)

    def test_funding_settlement_moves_cash_between_banks_only(self) -> None:
        sim = simulation(months=6, bank_relationships=True)
        banks = sim._active_banks()
        for bank in banks:
            bank.cash += 10_000.0
        before_banks = sum(b.cash for b in banks)
        before_others = sim.ledger.nonbank_cash(sim)
        sim._settle_funding(banks)
        self.assertAlmostEqual(sum(b.cash for b in banks), before_banks, places=6)
        self.assertEqual(sim.ledger.nonbank_cash(sim), before_others)
        self.assertAlmostEqual(
            sum(sim.bankrel.positions.values()),
            sum(sum(v for v in b.bank_loan_accounts.values() if v > 0.0) + b.bond_holdings
                - max(0.0, b.bank_equity) for b in banks) - sum(sim.bankrel.deposits.values()),
            places=3,
        )

    def test_a_failed_bank_s_clients_follow_their_accounts(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        banks = sim._active_banks()
        failed, receiver = banks[0], banks[1]
        clients = [agent for agent, home in sim.bankrel.home.items() if home == failed.id]
        self.assertTrue(clients)
        failed.active = False
        sim._transfer_clients(failed, receiver)
        for agent in clients:
            self.assertEqual(sim._home_bank(agent), receiver.id)

    def test_a_haircut_hits_the_bank_s_own_depositors_first(self) -> None:
        sim = simulation(months=3, bank_relationships=True)
        bank = sim._active_banks()[0]
        own = [p for p in sim.people if sim.bankrel.home[p.id] == bank.id and p.cash > 0.0]
        others = [p for p in sim.people if sim.bankrel.home[p.id] != bank.id and p.cash > 0.0]
        self.assertTrue(own and others)
        before_others = sum(p.cash for p in others)
        loss = 0.5 * sum(p.cash for p in own)
        destroyed = sim._haircut_deposits(loss, bank)
        self.assertAlmostEqual(destroyed, loss, places=6)
        self.assertEqual(sum(p.cash for p in others), before_others)

    def test_accounts_stay_exact_with_bank_relationships(self) -> None:
        sim = simulation(months=60, bank_relationships=True)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-4)
        stats = sim.bankrel.history[-1]
        self.assertTrue(0.0 < stats["concentration_depots"] <= 1.0)
        booked = sum(row.get("prets_inscrits", 0.0) for row in sim.bankrel.history)
        main = sum(row.get("prets_banque_principale", 0.0) for row in sim.bankrel.history)
        self.assertGreater(main, 0.5 * booked)


class LearningTests(unittest.TestCase):

    def firm(self, sim):
        company = next(c for c in sim._learning_firms() if c.employees)
        company.age_months = sim.live.learning_period_months
        return company

    def test_a_trial_that_lowers_the_margin_is_undone(self) -> None:
        sim = simulation(months=2, adaptive_agents=True)
        company = self.firm(sim)
        sim.learning.firms.clear()
        company.revenue, company.profit = 1_000.0, 100.0
        sim._learn_firms([company], sim.live.learning_period_months)
        memory = sim.learning.firms[company.id]
        trait = TRAITS[memory["trait"]]
        step, tried = memory["pas"], getattr(company, trait)
        self.assertNotEqual(step, 0.0)
        company.age_months += sim.live.learning_period_months
        company.profit = -100.0
        sim.live.imitation_rate = 0.0
        sim._learn_firms([company], sim.live.learning_period_months)
        self.assertAlmostEqual(getattr(company, trait), tried - step)
        self.assertEqual(sim.learning.month_stats["essais_annules"], 1.0)

    def test_a_trial_that_raises_the_margin_is_kept(self) -> None:
        sim = simulation(months=2, adaptive_agents=True)
        company = self.firm(sim)
        sim.learning.firms.clear()
        company.revenue, company.profit = 1_000.0, 100.0
        sim._learn_firms([company], sim.live.learning_period_months)
        memory = sim.learning.firms[company.id]
        trait, tried = TRAITS[memory["trait"]], None
        tried = getattr(company, trait)
        company.age_months += sim.live.learning_period_months
        company.profit = 300.0
        sim.live.imitation_rate = 0.0
        sim._learn_firms([company], sim.live.learning_period_months)
        self.assertEqual(getattr(company, trait), tried)
        self.assertEqual(sim.learning.month_stats["essais_gardes"], 1.0)

    def test_traits_stay_within_their_bounds(self) -> None:
        sim = simulation(months=60, adaptive_agents=True, learning_period_months=3, learning_step=0.4)
        for company in sim.companies:
            self.assertTrue(0.0 <= company.pricing_aggression <= 1.0)
            self.assertTrue(0.05 <= company.reinvestment_preference <= 1.0 or company.sector == "bank"
                            or company.reinvestment_preference >= 0.0)
        for person in sim.people:
            self.assertTrue(0.02 <= person.risk_tolerance <= 0.98 or person.id not in sim.learning.base_propensity)

    def test_unemployment_experience_raises_precaution(self) -> None:
        sim = simulation(months=2, adaptive_agents=True)
        period = sim.live.learning_period_months
        index = sim.month % period
        person = sim.people[index]
        person.unemployment_risk = 1.0
        base = person.consumption_propensity
        sim.learning.base_propensity[person.id] = (person.generation, base)
        sim._learn_households(period, {})
        self.assertLess(person.consumption_propensity, base)
        self.assertGreaterEqual(person.consumption_propensity, base * (1.0 - PRECAUTION) - 1e-9)

    def test_a_new_executive_brings_half_of_his_style(self) -> None:
        sim = simulation(months=2, adaptive_agents=True)
        company = next(c for c in sim._learning_firms() if c.executive_id)
        sim._transfer_styles([company])
        newcomer = "p0079"
        sim.learning.styles[newcomer] = (1.0, 1.0)
        before = company.pricing_aggression
        company.executive_id = newcomer
        sim._transfer_styles([company])
        self.assertAlmostEqual(company.pricing_aggression, before + 0.5 * (1.0 - before))

    def test_learning_is_reproducible(self) -> None:
        first = simulation(months=0, adaptive_agents=True)
        second = simulation(months=0, adaptive_agents=True)
        for _ in range(30):
            self.assertEqual(first.step(), second.step())
        self.assertLess(abs(first.ledger.residual(first)), 1e-4)


class PolicyLearningTests(unittest.TestCase):

    def test_learned_bank_buffer_reduces_capacity_without_relaxing_regulation(self):
        sim = simulation(months=1, adaptive_agents=True)
        bank = sim._active_banks()[0]
        bank.bank_equity = 1_000_000.0
        before = sim._bank_capacity(bank)
        sim.learning.banks[bank.id]["level"] = 0.5
        self.assertLess(sim._bank_capacity(bank), before)
        self.assertGreaterEqual(sim._bank_capacity(bank), 0.0)

    def test_a_failed_policy_trial_is_undone_and_its_direction_reversed(self):
        sim = simulation(months=1, adaptive_agents=True, learning_step=0.05)
        memory = dict(level=0.3, before=1.0, step=0.1, direction=1.0)
        self.assertFalse(sim._policy_trial(memory, 0.0, (0.0, 1.0), 2))
        self.assertTrue(sim._policy_trial(memory, 0.0, (0.0, 1.0), 2))
        self.assertLess(memory["level"], 0.2)
        self.assertEqual(memory["direction"], -1.0)

    def test_minister_learns_effort_on_his_own_objective(self):
        sim = simulation(months=13, adaptive_agents=True, enriched_objectives=True)
        self.assertGreater(sum(h.get("essais_ministres", 0) for h in sim.learning.history), 0)
        role, office = next((r, o) for r, o in sim.institutions.offices.items() if o.objective is not None)
        person = next(p for p in sim.people if p.id == office.person_id)
        office.objective.comparison, office.objective.target, office.objective.value = ">=", 100.0, 0.0
        key = (person.id, person.generation, role)
        sim.learning.ministers[key]["level"] = 1.0
        before = sim._objective_pressure(role, office.objective.lever)
        sim.learning.ministers[key]["level"] = 1.5
        self.assertGreater(before, 0.0)
        self.assertAlmostEqual(sim._objective_pressure(role, office.objective.lever), before * 1.5)


class CommutingTests(unittest.TestCase):

    def contract(self):
        planet = world(regions=2, months=1, migration=False, interregional_trade=False,
                       cross_border_takeovers=False, commuting_monthly_share=0.0,
                       clearing_holding_share=100.0)
        home, host = planet.regions
        protected = planet._protected(home)
        person = next(p for p in home.people if p.id not in protected)
        person.skill = 10.0
        person.current_wage = person.expected_income = 0.0
        person.employer_id = None
        company = next(c for c in host.companies if c.active and c.sector == "services")
        for sim in planet.regions:
            for c in sim.companies:
                c.target_employees = len(c.employees)
        company.target_employees += 1
        company.cash, company.wage_offer = 1_000_000.0, 100_000.0
        planet.commuting_progress[0] = 1.0
        planet._recruit_commuters()
        return planet, person, company, planet.commuters[(0, person.id)]

    def test_commuter_keeps_residence_home_debts_and_vote(self):
        planet, person, company, contract = self.contract()
        home, host = planet.regions
        self.assertIn(person, home.people)
        self.assertNotIn(person, host.people)
        self.assertIn(contract["proxy"], host.world_accounts)
        self.assertEqual(len(home.people), 80)
        self.assertEqual(len(host.people), 80)
        self.assertEqual(person.employment_role, "Frontalier")
        self.assertIn(person.id, home.institutions.lean)
        self.assertEqual(contract["proxy"].cash, 0.0)

    def test_wage_repatriation_is_a_transfer_and_is_declared_once(self):
        planet, person, company, contract = self.contract()
        home, host = planet.regions
        planet._prepare_commuters()
        totals = [money(sim) for sim in planet.regions]
        host._world_pay_workers()
        proxy = contract["proxy"]
        self.assertIn(proxy.id, company.employees)
        self.assertGreater(proxy.cash, 0.0)
        self.assertAlmostEqual(money(host), totals[1])
        wage = proxy.cash
        planet.pools[0].cash += wage
        received_before = person.cash
        planet._settle(planet._wage_intents())
        self.assertAlmostEqual(person.cash - received_before, wage)
        self.assertAlmostEqual(proxy.cash, 0.0)
        person.income_this_turn = 0.0
        home._world_resident_labor()
        self.assertAlmostEqual(person.income_this_turn, wage)
        home._world_resident_labor()
        self.assertAlmostEqual(person.income_this_turn, wage)

    def test_frontier_work_stops_with_the_employer(self):
        planet, person, company, contract = self.contract()
        company.active = False
        planet._prepare_commuters()
        self.assertNotIn((0, person.id), planet.commuters)
        self.assertIsNone(person.employer_id)

    def test_cross_border_payroll_keeps_every_regions_accounts_exact(self):
        planet = World(WorldConfig(base=SimulationConfig(seed=1001, population_size=120,
            warmup_months=0, bank_relationships=True, adaptive_agents=True, enriched_objectives=True),
            regions=3, commuting_monthly_share=0.02))
        for _ in range(60):
            planet.step()
            for residual in planet.money_residuals():
                self.assertLess(abs(residual), 1e-3)
        self.assertTrue(any(e["type"] == "emploi_frontalier" for e in planet.log))


class WorldTests(unittest.TestCase):

    def test_mixed_prepaid_and_new_payments_remain_funded_across_currencies(self):
        import random
        rng = random.Random(429)
        planet = world(regions=4, months=1, migration=False, commuting=False,
                       cross_border_takeovers=False, interregional_trade=False,
                       clearing_holding_share=0.001)
        payers = [sim.people[0] for sim in planet.regions]
        recipients = [sim.people[1] for sim in planet.regions]
        for trial in range(60):
            planet.rates = [0.5, 1.0, 1.0, 2.0]
            planet.claims = []
            planet._stats = [{"dividendes_recus": 0.0} for _ in planet.regions]
            for r, (payer, recipient, pool) in enumerate(zip(payers, recipients, planet.pools)):
                payer.cash = rng.uniform(0.0, 100_000.0)
                recipient.cash = 0.0
                pool.cash = rng.uniform(0.0, 50_000.0)
                dst = rng.choice([d for d in range(4) if d != r])
                prepaid = rng.uniform(0.0, 100_000.0)
                pool.cash += prepaid
                planet.claims.append({"de": r, "vers": dst, "menage": recipients[dst].id, "montant": prepaid})
            before = [p.cash + r.cash + pool.cash for p, r, pool in zip(payers, recipients, planet.pools)]
            intents = planet._claim_intents()
            for _ in range(16):
                src = rng.randrange(4)
                dst = rng.choice([d for d in range(4) if d != src])
                intents.append(Intent(src, dst, rng.uniform(0.0, 200_000.0),
                    planet._pay_into_pool(src, payers[src]),
                    planet._pay_owners(dst, {recipients[dst].id: 1.0}, 1.0)))
            with self.subTest(trial=trial):
                planet._settle(intents)
                for r, (payer, recipient, pool) in enumerate(zip(payers, recipients, planet.pools)):
                    self.assertGreaterEqual(min(payer.cash, recipient.cash, pool.cash), -1e-6)
                    self.assertAlmostEqual(payer.cash + recipient.cash + pool.cash, before[r], places=6)
                self.assertTrue(all(c["montant"] >= -1e-6 for c in planet.claims))

    def test_import_vat_is_in_the_same_months_public_book_and_gdp(self):
        planet = world(regions=2, months=1, migration=False, commuting=False,
                       cross_border_takeovers=False, interregional_trade=False)
        sim = planet.regions[0]
        record, state = sim.history[-1], sim.institutions.history[-1]
        before = (sim.public.cash, record.vat_revenue, record.tax_revenue,
                  record.nominal_gdp, state.cash_residual)
        paid = planet._buy_imports(0, "food", 1.0, {})(100.0)
        vat = paid * sim.live.vat_rate
        self.assertGreater(paid, 0.0)
        self.assertAlmostEqual(sim.public.cash, before[0] + vat)
        self.assertAlmostEqual(record.vat_revenue, before[1] + vat)
        self.assertAlmostEqual(record.tax_revenue, before[2] + vat)
        self.assertAlmostEqual(record.nominal_gdp, before[3] + vat)
        self.assertAlmostEqual(state.cash_residual, before[4], places=6)
        self.assertAlmostEqual(state.cash, sim.public.cash)

    def test_orphan_foreign_dividends_are_recorded_as_public_receipts(self):
        planet = world(regions=2, months=1, migration=False, commuting=False,
                       cross_border_takeovers=False, interregional_trade=False)
        sim = planet.regions[0]
        state = sim.institutions.history[-1]
        before = sim.public.cash, state.cash_residual, state.flows.get("dividendes", 0.0)
        planet.pools[0].cash = 100.0
        planet._pay_owners(0, {"porteur_disparu": 1.0}, 1.0)(100.0)
        self.assertAlmostEqual(sim.public.cash, before[0] + 100.0)
        self.assertAlmostEqual(state.cash_residual, before[1], places=6)
        self.assertAlmostEqual(state.flows["dividendes"], before[2] + 100.0)

    def test_tax_competition_respects_the_governments_doctrine(self):
        planet = world(regions=3, months=1, migration=False, commuting=False,
                       cross_border_takeovers=False, interregional_trade=False)
        a, b, c = planet.regions
        a.government.orientation, b.government.orientation = 20.0, 80.0
        a.live.corporate_tax_rate = b.live.corporate_tax_rate = 0.4
        c.live.corporate_tax_rate = 0.1
        planet._fiscal_competition()
        self.assertLess(a.world_corporate_tax_factor, 1.0)
        self.assertEqual(b.world_corporate_tax_factor, 1.0)

    def test_unfunded_promises_cannot_finance_a_chain_of_payments(self) -> None:
        planet = world(regions=3, months=1, migration=False, interregional_trade=False,
                       cross_border_takeovers=False)
        payers = [s.people[0] for s in planet.regions]
        payers[0].cash, payers[1].cash, payers[2].cash = 100.0, 100.0, 0.0
        before = [p.cash for p in payers]
        intents = [Intent(i, (i + 1) % 3, 100.0,
                          planet._pay_into_pool(i, payers[i]),
                          planet._pay_owners((i + 1) % 3, {payers[(i + 1) % 3].id: 1.0}, 1.0))
                   for i in range(3)]
        planet._settle(intents)
        self.assertEqual([p.cash for p in payers], before)
        self.assertEqual([i.collected for i in intents], [0.0, 0.0, 0.0])

    def test_two_invoices_share_the_same_payers_available_cash(self) -> None:
        planet = world(regions=3, months=1, migration=False, interregional_trade=False,
                       cross_border_takeovers=False)
        payers = [s.people[0] for s in planet.regions]
        for p in payers:
            p.cash = 100.0
        transfers = [(0, 1), (0, 2), (1, 0), (2, 0)]
        intents = [Intent(s, d, 100.0, planet._pay_into_pool(s, payers[s]),
                          planet._pay_owners(d, {payers[d].id: 1.0}, 1.0)) for s, d in transfers]
        planet._settle(intents)
        self.assertTrue(all(p.cash >= -1e-9 for p in payers))
        self.assertLessEqual(sum(i.collected for i in intents if i.src == 0), 100.0)
        self.assertAlmostEqual(sum(p.cash for p in payers) + sum(p.cash for p in planet.pools), 300.0)

    def test_prepaid_migration_claim_is_not_counted_as_new_pool_funding(self) -> None:
        planet = world(regions=2, months=1, migration=False, interregional_trade=False,
                       cross_border_takeovers=False)
        person = planet.regions[1].people[0]
        planet.pools[0].cash = 100.0
        planet.claims = [{"de": 0, "vers": 1, "menage": person.id, "montant": 100.0}]
        cash = person.cash
        planet._settle(planet._claim_intents())
        self.assertEqual(person.cash, cash)
        self.assertEqual(planet.claims[0]["montant"], 100.0)

    def test_normalizing_exchange_rates_respects_both_bounds(self) -> None:
        planet = world(regions=4, months=1, migration=False, interregional_trade=False,
                       cross_border_takeovers=False)
        planet.rates = [0.2, 5.0, 5.0, 5.0]
        payer, receiver = planet.regions[0].people[0], planet.regions[1].people[0]
        planet._settle([Intent(0, 1, 1.0, planet._pay_into_pool(0, payer),
                              planet._pay_owners(1, {receiver.id: 1.0}, 1.0))])
        product = 1.0
        for rate in planet.rates:
            self.assertGreaterEqual(rate, 0.2 - 1e-12)
            self.assertLessEqual(rate, 5.0 + 1e-12)
            product *= rate
        self.assertAlmostEqual(product, 1.0, places=9)

    def test_one_region_without_profile_is_the_engine(self) -> None:
        single = world(regions=1)
        reference = simulation(months=0)
        for _ in range(6):
            self.assertEqual(single.step()[0], reference.step())
        self.assertIsNone(getattr(single.regions[0], "world_accounts", None))

    def test_profiles_give_each_region_its_own_settings(self) -> None:
        base = SimulationConfig(seed=5, population_size=200)
        south = region_config(base, REGION_PROFILES[1], 1)
        self.assertEqual(south.population_size, 160)
        self.assertAlmostEqual(south.food_productivity, base.food_productivity * 1.20)
        self.assertEqual(south.initial_political_orientation, 60.0)
        self.assertNotEqual(south.seed, base.seed)
        self.assertEqual(region_config(base, None, 0), base)
        self.assertEqual(WorldConfig(regions=9).regions, 4)

    def test_balanced_payments_clear_and_conserve_each_currency(self) -> None:
        planet = world(regions=2, months=2)
        payer_a, payer_b = planet.regions[0].people[0], planet.regions[1].people[0]
        payer_a.cash += 500.0
        payer_b.cash += 500.0
        totals = [money(sim) for sim in planet.regions]
        cash = (payer_a.cash, payer_b.cash)
        planet._stats = [{"dividendes_verses": 0.0, "dividendes_recus": 0.0} for _ in planet.regions]
        planet._settle([
            Intent(0, 1, 100.0, planet._pay_into_pool(0, payer_a), planet._pay_owners(1, {payer_b.id: 1.0}, 1.0)),
            Intent(1, 0, 100.0, planet._pay_into_pool(1, payer_b), planet._pay_owners(0, {payer_a.id: 1.0}, 1.0)),
        ])
        for sim, total in zip(planet.regions, totals):
            self.assertAlmostEqual(money(sim), total, places=6)
        self.assertAlmostEqual(payer_a.cash, cash[0], places=6)
        self.assertAlmostEqual(payer_b.cash, cash[1], places=6)
        self.assertEqual(planet.rates, [1.0, 1.0])

    def test_a_region_cannot_pay_abroad_what_nobody_pays_it(self) -> None:
        planet = world(regions=2, months=2)
        payer, receiver = planet.regions[0].people[0], planet.regions[1].people[0]
        payer.cash += 500.0
        before = (payer.cash, receiver.cash)
        planet._stats = [{"dividendes_verses": 0.0, "dividendes_recus": 0.0} for _ in planet.regions]
        planet._settle([
            Intent(0, 1, 100.0, planet._pay_into_pool(0, payer), planet._pay_owners(1, {receiver.id: 1.0}, 1.0)),
        ])
        self.assertEqual((payer.cash, receiver.cash), before)

    def test_exports_follow_the_price_advantage(self) -> None:
        planet = world(regions=2, months=3, migration=False, cross_border_takeovers=False)
        cheap, dear = planet.regions
        cheap.food_price, dear.food_price = 1.0, 3.0
        cheap.goods_price = dear.goods_price = 2.0
        dear.history[-1].unmet_food_demand = 500.0
        cheap.history[-1].unmet_food_demand = 500.0
        for company in cheap.companies:
            if company.sector == "food":
                company.units_sold = max(company.units_sold, 100.0)
        planet._plan_exports()
        self.assertIn((0, "inventory"), planet.shipments)
        self.assertNotIn((1, "inventory"), planet.shipments)
        self.assertNotIn((0, "goods_inventory"), planet.shipments)
        ordered = sum(cheap.world_orders["inventory"].values())
        self.assertGreater(ordered, 0.0)
        self.assertLessEqual(ordered, planet.config.base.trade_openness * 500.0 + 1e-9)

    def test_docked_units_come_back_when_they_are_not_paid(self) -> None:
        planet = world(regions=2, months=2)
        sim = planet.regions[0]
        producer = next(c for c in sim.companies if c.active and c.sector == "food")
        producer.inventory = 50.0
        sim.world_orders = {"inventory": {producer.id: 80.0}}
        sim._ship_world_orders("inventory")
        self.assertEqual(producer.inventory, 0.0)
        self.assertEqual(sim.world_dock["inventory"][producer.id], 50.0)
        planet._return_docks()
        self.assertEqual(producer.inventory, 50.0)
        self.assertEqual(sim.world_dock, {})

    def test_a_household_settles_everything_before_leaving(self) -> None:
        planet = world(regions=2, months=3)
        sim = planet.regions[0]
        protected = planet._protected(sim)
        person = next(p for p in sim.people if planet._mobile(p, protected) and not p.owns_home
                      and p.mortgage_debt == 0.0)
        buyer = planet._home_buyer(sim)
        buyer.cash += 10 * sim.live.housing_price
        person.owns_home = True
        person.mortgage_debt = 1_000.0
        person.cash += 1_000.0
        sim._book_bank_loan(f"mortgage:{person.id}", 1_000.0)
        homes = buyer.property_inventory
        residual = sim.ledger.residual(sim)
        expected = planet._departure_proceeds(sim, person)
        self.assertIsNotNone(expected)
        planet._settle_departure(sim, person)
        self.assertAlmostEqual(person.cash, expected, places=6)
        self.assertFalse(person.owns_home)
        self.assertEqual(person.mortgage_debt, 0.0)
        self.assertEqual(buyer.property_inventory, homes + 1)
        self.assertFalse(any(f"mortgage:{person.id}" in b.bank_loan_accounts for b in sim.companies))
        self.assertAlmostEqual(sim.ledger.residual(sim), residual, places=5)

    def test_a_household_that_cannot_repay_stays(self) -> None:
        planet = world(regions=2, months=3)
        sim = planet.regions[0]
        person = sim.people[0]
        person.owns_home = False
        person.bond_holdings = 0.0
        person.mortgage_debt = 0.0
        person.consumer_debt = person.cash + 1_000.0
        self.assertIsNone(planet._departure_proceeds(sim, person))

    def test_moving_keeps_shares_and_identifiers_unique(self) -> None:
        planet = world(regions=2, months=3)
        origin, destination = planet.regions
        protected = planet._protected(origin)
        person = next(p for p in origin.people if planet._mobile(p, protected)
                      and planet._departure_proceeds(origin, p) is not None and p.shares)
        held = dict(person.shares)
        counts = (len(origin.people), len(destination.people))
        residuals = planet.money_residuals()
        planet._stats = [{"departs": 0, "arrivees": 0} for _ in planet.regions]
        planet._settle_departure(origin, person)
        planet._move(person, 0, 1)
        self.assertEqual((len(origin.people), len(destination.people)), (counts[0] - 1, counts[1] + 1))
        newcomer = destination.people[-1]
        self.assertEqual(newcomer.cash, 0.0)
        self.assertEqual(len({p.id for p in destination.people}), len(destination.people))
        for company_id, quantity in held.items():
            self.assertAlmostEqual(planet.holdings[0][company_id][(1, newcomer.id)], quantity)
            self.assertGreaterEqual(planet.nonresidents[0].shares[company_id], quantity - 1e-9)
        for after, before in zip(planet.money_residuals(), residuals):
            self.assertAlmostEqual(after, before, places=5)
        for _ in range(12):
            planet.step()
        for sim in planet.regions:
            self.assertEqual(len({p.id for p in sim.people}), len(sim.people))

    def test_control_transfers_part_of_the_productivity_lead(self) -> None:
        planet = world(regions=2, months=3)
        acquirer = next(c for c in planet.regions[0].companies if c.active and c.sector == "goods")
        target = next(c for c in planet.regions[1].companies if c.active and c.sector == "goods")
        acquirer.productivity_trait, target.productivity_trait = 1.4, 1.0
        floated = target.shares_outstanding - target.treasury_shares
        planet.bids[(0, acquirer.id)] = {"region": 1, "cible": target.id, "depuis": planet.month, "verse": 0.0}
        planet._add_holding(1, target.id, (0, acquirer.id), 0.4 * floated, target.stock_price)
        planet._close_bids()
        self.assertNotIn((1, target.id), planet.controlled)
        planet._add_holding(1, target.id, (0, acquirer.id), (CONTROL - 0.4) * floated, target.stock_price)
        planet._close_bids()
        self.assertEqual(planet.controlled[(1, target.id)][:2], (0, acquirer.id))
        self.assertAlmostEqual(target.productivity_trait, 1.0 + PRODUCTIVITY_TRANSFER * 0.4)
        self.assertEqual(planet.bids, {})

    def test_a_social_government_screens_critical_sectors(self) -> None:
        def offers(orientation: float) -> list:
            planet = world(regions=2, months=3, migration=False, interregional_trade=False)
            home, host = planet.regions
            host.government.orientation = orientation
            planet._stats = [{} for _ in planet.regions]
            for company in home.companies:
                if company.active and company.sector == "energy":
                    company.age_months, company.previous_profit = 60, 50_000.0
                    company.cash += 5_000_000.0
                    company.productivity_trait = 3.0
                    company.stock_price = 500.0
            for company in host.companies:
                if company.active and company.sector == "energy":
                    company.previous_profit = 500_000.0
                    company.productivity_trait = 1.0
                    company.stock_price = 1.0
                    if not company.employees:
                        company.employees.append(host.people[0].id)
            planet._open_bid()
            return [bid for (region, _), bid in planet.bids.items() if region == 0], planet._stats[1]

        open_market, _ = offers(30.0)
        screened, stats = offers(90.0)
        self.assertEqual(len(open_market), 1)
        self.assertEqual(screened, [])
        self.assertGreater(stats.get("rachats_refuses", 0), 0)

    def test_voters_compare_their_region_with_the_others(self) -> None:
        planet = world(regions=2, months=25, migration=False, cross_border_takeovers=False,
                       interregional_trade=False)
        for record in planet.regions[0].history[-12:]:
            record.real_gdp *= 1.5
        planet._yardstick()
        self.assertGreater(planet.regions[0].yardstick_bonus, 0.0)
        self.assertLess(planet.regions[1].yardstick_bonus, 0.0)
        self.assertLessEqual(planet.regions[0].yardstick_bonus, 0.2 * planet.config.yardstick_weight + 1e-12)

    def test_isolated_regions_do_not_exchange(self) -> None:
        planet = world(regions=2, months=6, integration_month=100)
        self.assertEqual(planet.log, [])
        for row in planet.region_table():
            self.assertEqual(row["importations"] + row["exportations"] + row["arrivees"] + row["rachats"], 0.0)
        self.assertEqual([pool.cash for pool in planet.pools], [0.0, 0.0])

    def test_each_region_keeps_exact_accounts(self) -> None:
        planet = world(regions=3, months=48, profiles=None, migration_monthly_share=0.02)
        for residual in planet.money_residuals():
            self.assertLess(abs(residual), 1e-3)
        self.assertEqual(len(planet.history), 48)
        self.assertEqual(len(planet.region_table()), 3)
        product = 1.0
        for rate in planet.rates:
            product *= rate
        self.assertAlmostEqual(product, 1.0, places=9)


class WorldViewsTests(unittest.TestCase):
    """Statistiques communes aux régions (``economy/world_views.py``) : lectures seules."""

    def _controlled_world(self):
        """Monde de trois régions où une entreprise de biens de la première contrôle celle de la
        deuxième, et a une filiale dans sa propre région."""
        planet = world(regions=3, months=3)
        goods = [next(c for c in sim.companies if c.active and c.sector == "goods") for sim in planet.regions]
        acquirer, target = goods[0], goods[1]
        local = next(c for c in planet.regions[0].companies
                     if c.active and c.sector == "food" and c.parent_id is None)
        local.parent_id = acquirer.id
        floated = target.shares_outstanding - target.treasury_shares
        planet._add_holding(1, target.id, (0, acquirer.id), 0.6 * floated, target.stock_price)
        planet.controlled[(1, target.id)] = (0, acquirer.id, planet.month)
        return planet, acquirer, target, local

    def test_views_do_not_change_the_world(self) -> None:
        from performance_probe import variant_digest

        planet, *_ = self._controlled_world()
        before = variant_digest(planet, keep_config=True)
        state = planet.random.getstate()
        world_views.world_totals(planet)
        world_views.company_ranking(planet)
        world_views.group_rows(planet)
        world_views.ownership_matrix(planet)
        world_views.sector_leaders(planet)
        world_views.household_ranking(planet)
        self.assertEqual(variant_digest(planet, keep_config=True), before)
        self.assertEqual(planet.random.getstate(), state)

    def test_totals_add_up_the_regions(self) -> None:
        planet = world(regions=3, months=3)
        totals = world_views.world_totals(planet)
        self.assertEqual(totals["menages"], sum(len(sim.people) for sim in planet.regions))
        self.assertEqual(totals["entreprises"], len(world_views.company_ranking(planet, limit=None)))
        for key in ("Part des ménages", "Part du PIB", "Part de la capitalisation", "Part des entreprises"):
            self.assertAlmostEqual(sum(row[key] for row in totals["regions"]), 1.0)

    def test_ranking_orders_every_region_in_common_units(self) -> None:
        planet, acquirer, target, _ = self._controlled_world()
        planet.rates = [1.0, 2.0, 0.5]
        acquirer.stock_price = 10_000.0
        ranking = world_views.company_ranking(planet, limit=None)
        values = [row["Capitalisation"] for row in ranking]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual([row["Rang"] for row in ranking], list(range(1, len(ranking) + 1)))
        self.assertEqual(ranking[0]["Région"], planet.names[0])
        self.assertEqual(ranking[0]["Groupe"], "tête de groupe")
        self.assertEqual(ranking[0]["Filiales"], 2)
        self.assertEqual(ranking[0]["Régions des filiales"], f"{planet.names[0]} 1 · {planet.names[1]} 1")
        label = planet.regions[1].company_labels()[target.id]
        row = next(r for r in ranking if r["Entreprise"] == label and r["Région"] == planet.names[1])
        floated = target.shares_outstanding - target.treasury_shares
        self.assertAlmostEqual(row["Capitalisation"], 2.0 * target.stock_price * floated)
        self.assertTrue(row["Groupe"].endswith(f"({planet.names[0]})"))
        by_staff = world_views.company_ranking(planet, limit=5, sort="Salariés")
        self.assertEqual(len(by_staff), 5)
        self.assertEqual([r["Salariés"] for r in by_staff], sorted((r["Salariés"] for r in by_staff), reverse=True))

    def test_groups_show_subsidiaries_by_region(self) -> None:
        planet, acquirer, target, local = self._controlled_world()
        groups, subsidiaries = world_views.group_rows(planet)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["Siège"], planet.names[0])
        self.assertEqual([group[f"Filiales · {name}"] for name in planet.names], [1, 1, 0])
        self.assertEqual(group["Régions d'implantation"], 2)
        self.assertEqual(group["Salariés du groupe"],
                         len(acquirer.employees) + len(target.employees) + len(local.employees))
        links = {row["Région"]: row for row in subsidiaries}
        self.assertEqual(links[planet.names[0]]["Lien"], world_views.LOCAL_LINK)
        self.assertEqual(links[planet.names[1]]["Lien"], world_views.FOREIGN_LINK)
        self.assertAlmostEqual(links[planet.names[1]]["Part détenue"], 0.6)

    def test_a_group_follows_the_company_that_absorbed_its_head(self) -> None:
        planet, acquirer, target, _ = self._controlled_world()
        heir = next(c for c in planet.regions[0].companies
                    if c.active and c.sector == "goods" and c is not acquirer)
        acquirer.active, acquirer.acquired_by = False, heir.id
        links = world_views.group_links(planet)
        self.assertEqual(links[(1, target.id)]["mere"], (0, heir.id))
        self.assertEqual(len(links), 1)   # la filiale locale d'une société fermée n'a plus de mère

    def test_ownership_matrix_values_cross_region_shares(self) -> None:
        planet, _, target, _ = self._controlled_world()
        planet.rates = [1.0, 2.0, 0.5]
        column = f"Porteurs · {planet.names[2]}"
        before = world_views.ownership_matrix(planet)
        # des ménages partis vivre ailleurs peuvent déjà porter des titres : mesurer l'ajout
        planet._add_holding(1, target.id, (2, "porteur"), 40.0, target.stock_price)
        after = world_views.ownership_matrix(planet)
        self.assertAlmostEqual(after[1][column] - before[1][column], 40.0 * target.stock_price * 2.0)
        self.assertEqual(after[0], before[0])
        self.assertGreater(after[1]["Part de la capitalisation"], before[1]["Part de la capitalisation"])

    def test_sector_leaders_share_out_world_revenue(self) -> None:
        planet = world(regions=3, months=3)
        for row in world_views.sector_leaders(planet):
            shares = [row[f"Part · {name}"] for name in planet.names]
            if sum(shares) > 0.0:
                self.assertAlmostEqual(sum(shares), 1.0)
                self.assertLessEqual(row["Part du leader"], 1.0 + 1e-12)
            self.assertIn(row["Région du leader"], planet.names)

    def test_fortunes_count_shares_kept_in_another_region(self) -> None:
        planet = world(regions=2, months=3)
        person = planet.regions[1].people[0]
        company = next(c for c in planet.regions[0].companies if c.active)
        before = {(r["Région"], r["Ménage"]): r for r in world_views.household_ranking(planet, limit=10_000)}
        planet._add_holding(0, company.id, (1, person.id), 50.0, company.stock_price)
        after = {(r["Région"], r["Ménage"]): r for r in world_views.household_ranking(planet, limit=10_000)}
        key = (planet.names[1], f"{display_name(person.id)} ({person.name})")
        gain = 50.0 * company.stock_price * planet.rates[0]
        self.assertAlmostEqual(after[key]["Fortune nette"] - before[key]["Fortune nette"], gain)
        self.assertAlmostEqual(after[key]["dont titres d'une autre région"], gain)
        ranking = world_views.household_ranking(planet, limit=5)
        self.assertEqual([r["Rang"] for r in ranking], [1, 2, 3, 4, 5])


class ParallelRegionsTests(unittest.TestCase):
    """Régions calculées chacune dans son fil (Python sans verrou global, ou forcé) : mêmes
    résultats, bit pour bit, que l'une après l'autre."""

    def _run(self, forced: str, months: int, **world) -> tuple:
        import os
        import sys
        from unittest.mock import patch

        from performance_probe import digest

        base = SimulationConfig(seed=77, population_size=60, warmup_months=6)
        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-5)  # entrelacer les fils au plus fin
        try:
            with patch.dict(os.environ, {"ECONOMY_PARALLEL_REGIONS": forced}):
                engine = World(WorldConfig(base=base, regions=3, **world))
                records = [repr(engine.step()) for _ in range(months)]
        finally:
            sys.setswitchinterval(interval)
        return records, digest(engine)

    def test_parallel_regions_match_sequential_with_commuting(self):
        self.assertEqual(self._run("1", 18), self._run("0", 18))

    def test_parallel_regions_match_sequential_without_commuting(self):
        self.assertEqual(self._run("1", 12, commuting=False), self._run("0", 12, commuting=False))

    def test_diagnostics_and_default_interpreter_keep_regions_sequential(self):
        import os
        from unittest.mock import patch

        from economy import diagnostics, world

        with patch.dict(os.environ, {"ECONOMY_PARALLEL_REGIONS": "1"}):
            self.assertTrue(world.parallel_regions())
            diagnostics.start()
            try:
                self.assertFalse(world.parallel_regions())
            finally:
                diagnostics.stop()
        with patch.dict(os.environ, {"ECONOMY_PARALLEL_REGIONS": "0"}):
            self.assertFalse(world.parallel_regions())

    def test_a_world_stays_serializable_after_a_parallel_month(self):
        import os
        import pickle
        from unittest.mock import patch

        with patch.dict(os.environ, {"ECONOMY_PARALLEL_REGIONS": "1"}):
            engine = World(WorldConfig(base=SimulationConfig(seed=5, population_size=40, warmup_months=2),
                                       regions=2, profiles=()))
            engine.step()
            self.assertEqual(pickle.loads(pickle.dumps(engine)).month, 1)


if __name__ == "__main__":
    unittest.main()
