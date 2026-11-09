"""Lot G (novembre 2026) : huit mécanismes d'entreprise (``economy/enterprise.py``).

Expérience (G1), gros entretien (G2), préférences et données (G3), filiales (G4), rachats entre
secteurs (G5), magasins (G6), distribution de groupe (G7), non cotées et fonds des banques (G8).
Tous désactivés par défaut ; activés, la monnaie est conservée et les comptes restent rapprochés.
"""

import copy
import dataclasses
import math
import os
import re
import unittest

from economy.consolidation import _merge_into
from economy.enterprise import (
    CONSUMER_SECTORS,
    IPO_DILUTION_LIMIT,
    MINIMUM_RENT_CONDITION,
    InvestmentFund,
    fund_assets,
    store_factor,
    taste_fit,
)
from economy.goods import _sales_weights
from economy.labor import _pay, experience_skill, negotiate_hire
from economy.models import Company, Person, SimulationConfig
from economy.real_estate import allocate_housing_and_collect_rent
from economy.simulation import EconomySimulation
from economy.stock_market import _fundamental_price

_ORIGINAL_WARM_UP = EconomySimulation._warm_up

SWITCHES = ("experience_learning", "property_upkeep", "consumer_preferences", "diversification",
            "conglomerate_takeovers", "retail_stores", "group_distribution", "private_startups")
EVERYTHING = {name: True for name in SWITCHES}


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
    """Monnaie détenue par tous les agents non bancaires (fonds compris)."""
    return sim.ledger.nonbank_cash(sim)


class EnterpriseRegistryTests(unittest.TestCase):
    """Chaque réglage du lot G est listé et justifié dans la section « Réglages du lot G »
    d'AUTONOMIE.md, existe dans ``SimulationConfig`` et y est neutre pour l'identité ; les huit
    mécanismes sont désactivés par défaut."""

    ADDED = {*SWITCHES, "experience_gain", "experience_half_count", "property_wear_rate",
             "property_repair_cost_share", "preference_strength", "data_budget_share", "data_decay_rate",
             "store_opening_cost", "store_running_cost", "store_reach", "store_half_count",
             "fund_savings_share", "fund_fee_rate", "fund_ticket_share", "fund_support_gain",
             "fund_exit_months", "ipo_need_share"}

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot G")
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

    def test_new_attributes_are_neutral(self) -> None:
        for cls, names in ((Person, ("ventures", "negotiations", "negotiated_factor", "home_condition",
                                     "taste", "fund_units", "fund_value")),
                           (Company, ("recruitments", "property_condition", "product_style", "market_insight",
                                      "parent_id", "stores", "listed", "listed_month", "fund_backed_month"))):
            fields = {f.name: f for f in dataclasses.fields(cls)}
            for name in names:
                self.assertEqual(fields[name].metadata.get("identity"), "neutre", name)

    def test_disabled_is_identical_and_creates_no_state(self) -> None:
        reference = simulation()
        explicit = simulation(**{name: False for name in SWITCHES})
        for _ in range(5):
            self.assertEqual(reference.step(), explicit.step())
        for sim in (reference, explicit):
            self.assertIsNone(getattr(sim, "enterprise", None))
            self.assertEqual(sim.enterprise_history(), [])
            self.assertTrue(all(c.listed and c.parent_id is None and c.stores == 0.0 for c in sim.companies))
            self.assertTrue(all(p.taste < 0.0 and not p.fund_units and p.negotiated_factor == 1.0
                                for p in sim.people))
            self.assertEqual(sim._outside_shareholders(), (sim.public.account,))
            self.assertIsNone(sim._sales_affinity("food"))
            self.assertIsNone(sim._rent_conditions())
            self.assertIsNone(sim._strategic_holdings())
            self.assertEqual(sim._unlisted_ids(), frozenset())


class ExperienceTests(unittest.TestCase):
    """G1 : compétence acquise par la pratique."""

    def test_skill_has_decreasing_returns(self) -> None:
        config = SimulationConfig(experience_half_count=3.0)
        values = [experience_skill(n, config) for n in (0, 1, 3, 10, 1000)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[2], 0.5)
        self.assertLess(values[-1], 1.0)
        self.assertEqual(experience_skill(-5, config), 0.0)

    def test_negotiation_favours_the_more_experienced_side(self) -> None:
        config = SimulationConfig(experience_learning=True, experience_gain=0.10)
        veteran = Person("p1", "Vétéran", 0.0, negotiations=12)
        novice = Person("p2", "Débutant", 0.0)
        young_firm = Company("a", "Jeune", "food", 0.0, 1000.0)
        old_firm = Company("b", "Rodée", "food", 0.0, 1000.0, recruitments=400)
        negotiate_hire(young_firm, veteran, config)
        negotiate_hire(old_firm, novice, config)
        self.assertGreater(veteran.negotiated_factor, 1.0)
        self.assertLess(novice.negotiated_factor, 1.0)
        for person in (veteran, novice):
            self.assertLessEqual(abs(person.negotiated_factor - 1.0), 0.05 + 1e-12)
        self.assertEqual((veteran.negotiations, novice.negotiations), (13, 1))
        self.assertEqual((young_firm.recruitments, old_firm.recruitments), (1, 401))

    def test_negotiated_wage_is_paid_only_with_the_setting_and_above_the_minimum(self) -> None:
        person = Person("p", "P", 0.0, negotiated_factor=0.95)
        firm = Company("a", "A", "food", 0.0, 1000.0)
        plain = _pay(firm, person, "Production", SimulationConfig())
        negotiated = _pay(firm, person, "Production", SimulationConfig(experience_learning=True))
        self.assertAlmostEqual(negotiated, plain * 0.95)
        floor = SimulationConfig(experience_learning=True)
        floor.minimum_wage = plain
        self.assertEqual(_pay(firm, person, "Production", floor), plain)

    def test_serial_founder_starts_a_better_company(self) -> None:
        sim = simulation(experience_learning=True)
        founder, beginner = sim.people[0], sim.people[1]
        founder.ventures = 3
        created = []
        for index, person in enumerate((founder, beginner)):
            created.append(Company(f"s{index}", "S", "goods", 1000.0, 1000.0, productivity_trait=1.0,
                                   innovation=0.5, founder_id=person.id, ceo_experience=1))
        sim._after_startups(created)
        self.assertAlmostEqual(created[0].productivity_trait, 1.05)   # 10 % × 3 / (3 + 3)
        self.assertAlmostEqual(created[0].innovation, 0.525)
        self.assertEqual(created[0].ceo_experience, 6)
        self.assertEqual((created[1].productivity_trait, created[1].innovation, created[1].ceo_experience),
                         (1.0, 0.5, 1))
        self.assertEqual((founder.ventures, beginner.ventures), (4, 1))
        self.assertTrue(all(c.listed for c in created))  # G8 désactivé : cotées comme avant

    def test_experienced_acquirer_keeps_best_practices(self) -> None:
        results = {}
        for experience in (0.0, 1.0):
            buyer = Company("b", "B", "food", 100.0, 10.0, productivity_trait=1.0)
            target = Company("t", "T", "food", 100.0, 10.0, productivity_trait=1.4, stores=2.0)
            _merge_into(buyer, target, [], [buyer, target], (), experience)
            results[experience] = buyer.productivity_trait
            self.assertEqual(buyer.stores, 2.0)   # les magasins de la cible rejoignent le réseau
        self.assertAlmostEqual(results[0.0], 1.2)
        self.assertAlmostEqual(results[1.0], 1.4)

    def test_heir_starts_without_the_experience(self) -> None:
        sim = simulation(experience_learning=True)
        person = sim.people[0]
        person.ventures, person.negotiations = 2, 5
        sim._succeed(person, 0.0)
        self.assertEqual((person.ventures, person.negotiations), (0, 0))


class PropertyUpkeepTests(unittest.TestCase):
    """G2 : usure du parc, gros travaux, loyer selon l'état."""

    def test_new_homes_enter_in_new_condition(self) -> None:
        sim = simulation(property_upkeep=True)
        sim._enterprise_state().homes["x"] = 2
        self.assertAlmostEqual(sim._worn_condition("x", 4, 0.5, 0.0), 0.75)
        self.assertAlmostEqual(sim._worn_condition("x", 4, 0.75, 0.1), 0.675)
        self.assertEqual(sim._worn_condition("x", 0, 0.2, 0.1), 1.0)

    def test_degraded_home_rents_for_less(self) -> None:
        landlord = Person("l", "Bailleur", 0.0, owns_home=True, investment_homes=1)
        tenant = Person("t", "Locataire", 5000.0, rental_owner_id="l", income_this_turn=5000.0)
        paid = allocate_housing_and_collect_rent([], [landlord, tenant], 1000.0, 1.0, conditions={"l": 0.6})
        self.assertAlmostEqual(paid, 600.0)
        self.assertAlmostEqual(landlord.cash, 600.0)
        other = Person("t2", "Locataire", 5000.0, rental_owner_id="l", income_this_turn=5000.0)
        self.assertAlmostEqual(
            allocate_housing_and_collect_rent([], [landlord, other], 1000.0, 1.0), 1000.0)

    def test_owner_without_cash_lets_the_home_decay_and_money_is_conserved(self) -> None:
        sim = simulation(months=3, property_upkeep=True)
        poor, rich = sim.people[0], sim.people[1]
        poor.owns_home = rich.owns_home = True
        poor.cash, rich.cash = 0.0, 1e6
        poor.home_condition = rich.home_condition = 0.8
        sim.enterprise.homes.update({poor.id: 1, rich.id: 1})   # logements déjà détenus le mois dernier
        before = money(sim)
        sim._property_upkeep()
        self.assertLess(poor.home_condition, 0.8)             # usure, aucun moyen de réparer
        self.assertAlmostEqual(rich.home_condition, 1.0)      # travaux payés en entier
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        conditions = sim._rent_conditions()
        self.assertTrue(all(MINIMUM_RENT_CONDITION <= v <= 1.0 for v in conditions.values()))

    def test_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=30, property_upkeep=True)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        self.assertTrue(all(0.0 <= p.home_condition <= 1.0 for p in sim.people))
        self.assertTrue(all(0.0 <= c.property_condition <= 1.0 for c in sim.companies))
        self.assertGreater(sum(row.get("travaux", 0.0) for row in sim.enterprise.history), 0.0)


class PreferenceTests(unittest.TestCase):
    """G3 : goûts des ménages, styles des producteurs, données vendues par la technologie."""

    def test_fit_is_circular(self) -> None:
        self.assertEqual(taste_fit(0.3, 0.3), 1.0)
        self.assertAlmostEqual(taste_fit(0.1, 0.6), 0.0)
        self.assertAlmostEqual(taste_fit(0.05, 0.95), 0.8)   # 0,05 et 0,95 sont voisins sur le cercle
        self.assertAlmostEqual(taste_fit(0.2, 0.45), taste_fit(0.45, 0.2))

    def test_affinity_moves_market_shares_and_none_changes_nothing(self) -> None:
        firms = [Company("a", "A", "food", 0.0, 10.0, price=100.0), Company("b", "B", "food", 0.0, 10.0, price=100.0)]
        demands, preferences = {"p": 10.0}, {"p": 1.0}
        plain = _sales_weights(firms, 1.5, 100.0, demands, preferences, 1.0)
        self.assertEqual(plain, _sales_weights(firms, 1.5, 100.0, demands, preferences, 1.0, None))
        self.assertAlmostEqual(plain["a"], plain["b"])
        tilted = _sales_weights(firms, 1.5, 100.0, demands, preferences, 1.0, {"p": {"a": 1.5, "b": 1.0}})
        self.assertAlmostEqual(tilted["a"] / tilted["b"], 1.5)
        self.assertAlmostEqual(sum(tilted.values()), sum(plain.values()))   # la demande ne change pas

    def test_tastes_use_their_own_generator(self) -> None:
        sim = simulation(consumer_preferences=True)
        states = (sim.random.getstate(), sim.labor_random.getstate(), sim.forecast_random.getstate())
        affinity = sim._sales_affinity("food")
        self.assertEqual(states, (sim.random.getstate(), sim.labor_random.getstate(), sim.forecast_random.getstate()))
        self.assertTrue(all(0.0 <= p.taste < 1.0 for p in sim.people))
        firms = [c for c in sim.companies if c.active and c.sector == "food"]
        self.assertTrue(all(0.0 <= c.product_style < 1.0 for c in firms))
        strength = sim.config.preference_strength
        for row in affinity.values():
            self.assertEqual(set(row), {c.id for c in firms})
            self.assertTrue(all(1.0 <= factor <= 1.0 + strength + 1e-12 for factor in row.values()))

    def test_customer_data_brings_the_offer_closer_to_equipped_households(self) -> None:
        sim = simulation(consumer_preferences=True)
        firm = next(c for c in sim.companies if c.active and c.sector == "food")
        sim._sales_affinity("food")
        person = max(sim.people, key=lambda p: -taste_fit(p.taste, firm.product_style))  # le moins proche
        person.technology_level = sim.config.technology_household_max_level
        before = sim._sales_affinity("food")[person.id][firm.id]
        firm.market_insight = 1.0
        after = sim._sales_affinity("food")[person.id][firm.id]
        self.assertGreater(after, before)
        self.assertAlmostEqual(after, 1.0 + sim.config.preference_strength)
        person.technology_level = 0.0   # un ménage sans équipement ne laisse aucune donnée
        self.assertAlmostEqual(sim._sales_affinity("food")[person.id][firm.id], before)

    def test_data_is_bought_from_technology_firms(self) -> None:
        sim = simulation(months=3, consumer_preferences=True)
        buyer = next(c for c in sim.companies if c.active and c.sector == "goods" and c.employees)
        sellers = [c for c in sim.companies if c.active and c.sector == "technology" and c.employees]
        self.assertTrue(sellers)
        sim.people[0].technology_level = 1.0
        buyer.previous_profit, buyer.previous_revenue, buyer.cash = 1000.0, 200_000.0, 5e6
        buyer.market_insight = 0.0
        revenue = sum(c.revenue for c in sellers)
        before = money(sim)
        sim._trade_customer_data()
        budget = sim.config.data_budget_share * 200_000.0
        self.assertAlmostEqual(sum(c.revenue for c in sellers) - revenue, budget, delta=budget * 0.5 + 1e-6)
        self.assertGreater(buyer.market_insight, 0.0)
        self.assertLessEqual(buyer.market_insight, 1.0)
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        insight = buyer.market_insight
        buyer.previous_profit = -1.0   # en pertes : aucun achat, la connaissance s'oublie
        sim._trade_customer_data()
        self.assertAlmostEqual(buyer.market_insight, insight * (1.0 - sim.config.data_decay_rate))


class StoreTests(unittest.TestCase):
    """G6 et G7 : magasins et réseau de groupe."""

    def test_reach_has_decreasing_returns(self) -> None:
        config = SimulationConfig(store_reach=0.5, store_half_count=3.0)
        factors = [store_factor(n, config) for n in (0.0, 1.0, 3.0, 30.0, 1e9)]
        self.assertEqual(factors[0], 1.0)
        self.assertEqual(factors, sorted(factors))
        self.assertAlmostEqual(factors[2], 1.25)
        self.assertLessEqual(factors[-1], 1.5)

    def _producer(self, sim):
        firm = next(c for c in sim.companies if c.active and c.sector == "food" and c.employees)
        firm.cash, firm.previous_profit = 5e6, 50_000.0
        firm.price, firm.unit_input_cost = 300.0, 100.0
        firm.units_sold = firm.previous_units_sold = 5000.0
        firm.inventory = 5000.0
        return firm

    def test_profitable_producer_opens_a_store(self) -> None:
        sim = simulation(months=3, retail_stores=True)
        firm = self._producer(sim)
        capital, expenses, before = firm.productive_capital_value, firm.expenses, money(sim)
        invested = firm.capital_expenditure
        sim._run_stores()
        self.assertGreater(firm.stores, 0.0)
        self.assertLessEqual(firm.stores, 1.0 + 1e-9)
        paid = firm.productive_capital_value - capital
        self.assertGreater(paid, 0.0)
        self.assertAlmostEqual(firm.capital_expenditure - invested, paid)   # investissement capitalisé
        self.assertAlmostEqual(firm.expenses, expenses)          # pas une charge du mois
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        affinity = sim._sales_affinity("food")
        row = next(iter(affinity.values()))
        self.assertGreater(row[firm.id], 1.0)
        self.assertTrue(all(row[c.id] == 1.0 for c in sim.companies
                            if c.active and c.sector == "food" and c is not firm))

    def test_no_store_without_margin_and_stores_close_without_cash(self) -> None:
        sim = simulation(months=3, retail_stores=True)
        firm = self._producer(sim)
        firm.unit_input_cost = firm.price   # aucune marge : un magasin ne rapporte rien
        sim._run_stores()
        self.assertEqual(firm.stores, 0.0)
        firm.stores, firm.cash = 4.0, 0.0   # charges impayées : le réseau ferme
        sim._run_stores()
        self.assertEqual(firm.stores, 0.0)

    def test_group_network_serves_every_company_of_the_group(self) -> None:
        sim = simulation(retail_stores=True, group_distribution=True)
        food = [c for c in sim.companies if c.active and c.sector == "food"]
        goods = [c for c in sim.companies if c.active and c.sector == "goods"]
        parent, subsidiary, outsider = food[0], goods[0], goods[1]
        parent.stores = 3.0
        subsidiary.parent_id = parent.id
        network = sim._network_stores()
        self.assertEqual((network[parent.id], network[subsidiary.id], network[outsider.id]), (3.0, 3.0, 0.0))
        row = next(iter(sim._sales_affinity("goods").values()))
        self.assertAlmostEqual(row[subsidiary.id], 1.25)
        self.assertEqual(row[outsider.id], 1.0)
        self.assertAlmostEqual(sim._distribution_synergy(parent, outsider, network), 0.25)
        alone = simulation(retail_stores=True)
        alone.companies[0].stores = 3.0
        self.assertEqual(alone._network_stores()[alone.companies[1].id], alone.companies[1].stores)


class GroupTests(unittest.TestCase):
    """G4 et G5 : filiales créées, rachats entre secteurs."""

    ENTRY = {"unserved": {"food": (0.0, 100.0), "goods": (0.0, 100.0)}, "input_cost_index": 1.0,
             "market_prices": {"food": 120.0, "goods": 100.0}, "refused_orders": {}}

    def test_profitable_company_founds_a_subsidiary_in_another_sector(self) -> None:
        sim = simulation(months=2, diversification=True, entry_signal_scale=0.0)
        parent = next(c for c in sim.companies if c.active and c.sector == "food" and c.employees)
        parent.cash, parent.previous_profit, parent.age_months = 1e7, 50_000.0, 30
        parent.last_acquisition_month = -1000
        sim.ledger.calibrate(sim)   # trésorerie ajoutée pour le test
        count, before = len(sim.companies), money(sim)
        self.assertEqual(sim._create_subsidiaries(self.ENTRY), 1)
        subsidiary = sim.companies[-1]
        self.assertEqual(len(sim.companies), count + 1)
        self.assertEqual(subsidiary.parent_id, parent.id)
        self.assertNotEqual(subsidiary.sector, parent.sector)
        self.assertNotIn(subsidiary.sector, ("bank", "real_estate"))
        self.assertFalse(subsidiary.listed)
        self.assertGreater(subsidiary.cash, 0.0)
        self.assertEqual(parent.shares[subsidiary.id], subsidiary.shares_outstanding)
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        self.assertEqual(sim._strategic_holdings(), {parent.id: frozenset({subsidiary.id})})
        self.assertIn(subsidiary.id, sim._undiluted_ids())
        self.assertIn(subsidiary.id, sim._unlisted_ids())
        self.assertEqual(sim._group_roots()[subsidiary.id], parent.id)
        # une seule filiale par mois, et la société mère attend avant une nouvelle opération
        self.assertEqual(sim._create_subsidiaries(self.ENTRY), 0)
        for _ in range(12):
            sim.step()
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_subsidiary_is_not_created_without_signal_or_cash(self) -> None:
        sim = simulation(months=2, diversification=True, entry_signal_scale=1e9)
        parent = next(c for c in sim.companies if c.active and c.sector == "food" and c.employees)
        parent.cash, parent.previous_profit, parent.age_months = 1e7, 50_000.0, 30
        parent.last_acquisition_month = -1000
        self.assertEqual(sim._create_subsidiaries(self.ENTRY), 0)
        sim = simulation(months=2, diversification=True, entry_signal_scale=0.0)
        for company in sim.companies:
            company.age_months, company.previous_profit, company.cash = 30, 1000.0, 1000.0
            company.last_acquisition_month = -1000
        self.assertEqual(sim._create_subsidiaries(self.ENTRY), 0)

    def _takeover_setup(self, **overrides):
        sim = simulation(months=2, conglomerate_takeovers=True, **overrides)
        buyer = next(c for c in sim.companies if c.active and c.sector == "food")
        target = next(c for c in sim.companies if c.active and c.sector == "services")
        for company in sim.companies:   # un seul acquéreur possible
            company.previous_profit = -1.0
        buyer.cash, buyer.previous_profit, buyer.age_months = 5e7, 50_000.0, 30
        buyer.stock_price, buyer.last_acquisition_month = 1e5, -1000
        for person in sim.people:
            person.risk_tolerance = 0.0   # actionnaires prudents : estimation à 80 % de la valeur
        for company in sim.companies:
            if company is not buyer and company is not target:
                company.parent_id = "autre_groupe"   # déjà dans un groupe : une seule cible possible
        value = _fundamental_price(target, sim.companies, sim.live, 12.0 * max(0.0, sim.policy_rate))
        target.stock_price = value / 1.2   # l'offre (cours + 30 %) dépasse la valeur du titre
        buyer.productivity_trait, target.productivity_trait = 1.6, 1.0   # acquéreur mieux géré
        sim.ledger.calibrate(sim)          # trésorerie ajoutée pour le test
        return sim, buyer, target

    def test_cross_sector_takeover_makes_a_subsidiary_without_merging(self) -> None:
        sim, buyer, target = self._takeover_setup()
        before, staff = money(sim), list(target.employees)
        self.assertEqual(sim._conglomerate_takeovers(), 1)
        self.assertTrue(target.active)
        self.assertEqual(target.parent_id, buyer.id)
        self.assertEqual(target.employees, staff)
        circulating = target.shares_outstanding - target.treasury_shares
        self.assertGreaterEqual(buyer.shares[target.id], sim.live.control_threshold * circulating - 1e-6)
        self.assertGreater(buyer.secondary_asset_purchases, 0.0)
        self.assertEqual(buyer.acquisition_count, 1)
        self.assertAlmostEqual(target.productivity_trait, 1.15)   # un quart de l'écart transmis
        self.assertAlmostEqual(money(sim), before, delta=1e-6)
        sim._enterprise_housekeeping()
        self.assertEqual(target.parent_id, buyer.id)
        self.assertEqual(sim._conglomerate_takeovers(), 0)   # délai d'intégration
        for _ in range(12):
            sim.step()
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_no_bid_without_synergy_or_on_a_cheap_bigger_or_public_target(self) -> None:
        sim, buyer, target = self._takeover_setup()
        buyer.productivity_trait = target.productivity_trait   # rien à apporter : aucune offre
        self.assertEqual(sim._conglomerate_takeovers(), 0)
        self.assertEqual(sim._management_synergy(buyer, target), 0.0)
        sim, buyer, target = self._takeover_setup()
        target.stock_price /= 2.0   # offre sous la valeur du titre : un actionnaire informé refuserait
        self.assertEqual(sim._conglomerate_takeovers(), 0)
        sim, buyer, target = self._takeover_setup()
        target.stock_price *= 3.0   # trop chère même avec ce que l'acquéreur apporte
        self.assertEqual(sim._conglomerate_takeovers(), 0)
        sim, buyer, target = self._takeover_setup()
        buyer.stock_price = 1.0     # cible plus grosse que l'acquéreur
        self.assertEqual(sim._conglomerate_takeovers(), 0)
        sim, buyer, target = self._takeover_setup()
        sim._public_controlled_ids = lambda: frozenset({target.id})
        self.assertEqual(sim._conglomerate_takeovers(), 0)

    def test_subsidiary_leaves_the_group_when_control_is_lost(self) -> None:
        sim, buyer, target = self._takeover_setup()
        sim._conglomerate_takeovers()
        buyer.shares[target.id] *= 0.5   # sous les deux tiers du seuil de contrôle
        sim._enterprise_housekeeping()
        self.assertIsNone(target.parent_id)
        self.assertTrue(target.listed)


class PrivateCompanyTests(unittest.TestCase):
    """G8 : entreprises non cotées, fonds des banques, introduction en Bourse."""

    def _private_company(self, sim, cash: float = 0.0):
        company = next(c for c in sim.companies if c.active and c.sector == "services" and c.employees)
        company.listed, company.cash = False, cash
        company.founder_id = sim.people[0].id   # fondée par un ménage, comme toute non cotée indépendante
        return company

    def test_household_startups_are_private_except_banks(self) -> None:
        sim = simulation(private_startups=True)
        firm = Company("s1", "S", "goods", 1000.0, 1000.0, founder_id=sim.people[0].id)
        bank = Company("s2", "B", "bank", 1000.0, 1000.0, founder_id=sim.people[1].id)
        sim._after_startups([firm, bank])
        self.assertFalse(firm.listed)
        self.assertTrue(bank.listed)

    def test_funds_collect_savings_and_finance_private_companies(self) -> None:
        sim = simulation(months=3, private_startups=True)
        company = self._private_company(sim)
        for person in sim.people:
            person.cash += 50_000.0
        before, shares = money(sim), company.shares_outstanding - company.treasury_shares
        trait = company.productivity_trait
        need = sim._financing_need(company)
        self.assertGreater(need, 0.0)
        sim._fund_operations()
        sim._fund_operations()
        funds = sim.enterprise.funds
        banks = [b for b in sim.companies if b.active and b.sector == "bank"]
        self.assertEqual({f.bank_id for f in funds}, {b.id for b in banks})
        self.assertGreater(sum(f.units for f in funds), 0.0)
        fund = next(f for f in funds if f.shares.get(company.id, 0.0) > 0.0)
        self.assertGreater(company.cash, 0.0)
        self.assertGreater(company.shares_outstanding - company.treasury_shares, shares)
        self.assertEqual(company.fund_backed_month, sim.month)
        self.assertAlmostEqual(company.productivity_trait, min(1.8, trait * (1.0 + sim.config.fund_support_gain)))
        # parts : celles des ménages font le total du fonds, leur valeur est l'actif du fonds
        for each in funds:
            self.assertAlmostEqual(sum(p.fund_units.get(each.id, 0.0) for p in sim.people), each.units, places=6)
        prices = {c.id: c.stock_price for c in sim.companies if c.active}
        self.assertAlmostEqual(sum(p.fund_value for p in sim.people),
                               sum(fund_assets(f, prices) for f in funds if f.units > 1e-9), places=4)
        # les frais vont à la banque gérante : hors d'eux, la monnaie est conservée
        fees = sim.enterprise.month_stats.get("fonds_frais", 0.0)
        self.assertGreater(fees, 0.0)
        self.assertAlmostEqual(money(sim), before - fees, delta=1e-6)
        self.assertIn(fund, sim._outside_shareholders())
        self.assertIsInstance(fund, InvestmentFund)

    def test_fund_value_counts_in_household_wealth(self) -> None:
        from economy.wealth import household_net_worth
        sim = simulation(private_startups=True)
        person = sim.people[0]
        person.fund_value = 0.0
        worth = household_net_worth(person, sim.companies, sim.live)
        person.fund_value = 1234.5
        self.assertAlmostEqual(household_net_worth(person, sim.companies, sim.live), worth + 1234.5)

    def test_private_company_is_outside_the_stock_market(self) -> None:
        sim = simulation(months=3, private_startups=True)
        company = self._private_company(sim, cash=1e6)
        holdings = {p.id: p.shares.get(company.id, 0.0) for p in sim.people}
        treasury = company.treasury_shares
        for _ in range(3):
            sim.step()
            self.assertEqual(company.stock_trade_volume, 0.0)
            self.assertGreaterEqual(company.stock_price, 1.0)
        if not company.listed:
            self.assertEqual(company.treasury_shares, treasury)
            for person in sim.people:
                if person.id != company.executive_id:   # le dirigeant reçoit des titres en rémunération
                    self.assertLessEqual(person.shares.get(company.id, 0.0), holdings[person.id] + 1e-9)

    def test_listing_offers_new_shares_within_the_dilution_limit(self) -> None:
        sim = simulation(private_startups=True)
        company = self._private_company(sim)
        company.treasury_shares, company.shares_outstanding, company.stock_price = 0.0, 1000.0, 10.0
        sim._list_company(company, need=1e9)
        self.assertTrue(company.listed)
        self.assertEqual(company.listed_month, sim.month)
        self.assertAlmostEqual(company.treasury_shares, IPO_DILUTION_LIMIT * 1000.0)
        self.assertAlmostEqual(company.shares_outstanding, 1000.0 * (1.0 + IPO_DILUTION_LIMIT))
        self.assertIn(company.id, sim._recent_listings())
        self.assertNotIn(company.id, sim._unlisted_ids())

    def test_big_need_or_fund_exit_triggers_the_listing(self) -> None:
        sim = simulation(months=3, private_startups=True, ipo_need_share=0.0)
        company = self._private_company(sim)   # sans trésorerie, aucun fonds doté : besoin entier
        sim._fund_operations()
        self.assertTrue(company.listed)
        sim = simulation(months=3, private_startups=True, fund_exit_months=1)
        company = self._private_company(sim, cash=1e7)   # aucun besoin : seule la sortie du fonds joue
        company.fund_backed_month = sim.month - 1
        sim._fund_operations()
        self.assertTrue(company.listed)

    def test_fund_sells_listed_shares_progressively(self) -> None:
        sim = simulation(months=2, private_startups=True)
        sim._fund_operations()
        fund = sim.enterprise.funds[0]
        listed = next(c for c in sim.companies if c.active and c.sector == "food")
        private = self._private_company(sim)
        fund.shares[listed.id], fund.shares[private.id] = 100.0, 50.0
        orders = sim._fund_sell_orders()
        self.assertEqual([(holder.id, cid) for holder, cid, _ in orders], [(fund.id, listed.id)])
        self.assertAlmostEqual(orders[0][2], 10.0)

    def test_run_keeps_accounts_exact(self) -> None:
        sim = simulation(months=2, private_startups=True)
        self._private_company(sim)
        for person in sim.people:
            person.cash += 20_000.0
        sim.ledger.calibrate(sim)   # monnaie fiduciaire ajoutée pour le test
        for _ in range(36):
            record = sim.step()
        self.assertLess(abs(record.ledger_residual), 1e-3)
        self.assertGreater(sum(f.units for f in sim.enterprise.funds), 0.0)
        self.assertGreater(sum(row.get("fonds_frais", 0.0) for row in sim.enterprise.history), 0.0)


class EverythingTogetherTests(unittest.TestCase):
    """Les huit mécanismes ensemble : comptes rapprochés, grandeurs finies, relevés tenus."""

    def test_all_mechanisms_run_together(self) -> None:
        sim = simulation(months=0, **EVERYTHING)
        for _ in range(72):
            record = sim.step()   # ``validate_financial_accounts`` lève à la moindre incohérence
            self.assertLess(abs(record.ledger_residual), 1e-3)
        for value in (record.nominal_gdp, record.real_gdp, record.average_wage, record.consumer_price_index):
            self.assertTrue(math.isfinite(value))
        history = sim.enterprise_history()
        self.assertEqual(len(history), 72)
        self.assertEqual(history[-1]["periode"], sim.period)
        for company in sim.companies:
            self.assertGreaterEqual(company.stores, 0.0)
            self.assertTrue(0.0 <= company.market_insight <= 1.0)
            if company.active and company.parent_id is not None:
                parent = next(c for c in sim.companies if c.id == company.parent_id)
                self.assertTrue(parent.active)
                self.assertGreater(parent.shares.get(company.id, 0.0), 0.0)
            if company.active and company.sector not in CONSUMER_SECTORS:
                self.assertEqual(company.market_insight, 0.0)
        for person in sim.people:
            self.assertGreaterEqual(person.fund_value, 0.0)
            self.assertTrue(0.95 - 1e-9 <= person.negotiated_factor <= 1.05 + 1e-9)

    def test_state_survives_a_copy(self) -> None:
        sim = simulation(months=6, **EVERYTHING)
        clone = copy.deepcopy(sim)
        for _ in range(6):
            self.assertEqual(sim.step(), clone.step())


if __name__ == "__main__":
    unittest.main()
