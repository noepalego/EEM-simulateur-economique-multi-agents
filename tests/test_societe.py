"""Lot V (12 novembre 2026) : surendettement, philanthropie, loterie, addictions, franchises, marques,
coopératives, vente à découvert, titrisation, financement participatif, manifestations, grands
travaux (``economy/society.py``, ``economy/ventures.py``). Désactivés par défaut ; activés, la
monnaie est conservée."""

import dataclasses
import os
import re
import unittest

from economy.household_credit import extend_consumer_credit
from economy.goods import trade_technology
from economy.models import Company, SimulationConfig
from economy.records import MonthFlows
from economy.simulation import EconomySimulation
from economy.society import (
    ALL_SWITCHES,
    RIOT_DAMAGE,
    UNREST_MAX_CONCESSIONS,
    UNREST_RELIEF,
    VICE_SECTOR,
    WORKS,
)
from economy.ventures import BRAND_HALF, FRANCHISE_METHODS, HedgeFund

_ORIGINAL_WARM_UP = EconomySimulation._warm_up
PARAMETERS = (
    "debt_relief_service_share", "credit_ban_months",
    "philanthropy_wealth_multiple", "philanthropy_giving_rate",
    "lottery_stake_share", "lottery_payout_share", "lottery_draw_chance",
    "addiction_firms", "addiction_budget_share", "addiction_excise_rate", "addiction_care_cost",
    "addiction_mortality_gain", "addiction_technology_gain", "addiction_technology_value_share",
    "addiction_margin_target",
    "franchise_entry_fee", "franchise_royalty_rate",
    "marketing_agencies", "marketing_budget_share", "brand_decay_rate", "brand_reach",
    "buyout_min_workers", "buyout_benefit_months",
    "hedge_funds", "short_float_share", "short_fee_rate",
    "securitization_share", "securitization_savings_share", "securitization_fee_rate",
    "crowdfunding_savings_share", "crowdfunding_max_age_months", "crowdfunding_premium",
    "unrest_unemployment", "unrest_inequality", "unrest_production_loss", "unrest_concession",
    "public_works_gdp_share", "public_works_gain", "public_works_wear_rate", "public_works_debt_limit",
)


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 1, population: int = 80, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=population, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def flows(sim: EconomySimulation, unemployment: float = 0.1) -> MonthFlows:
    f = MonthFlows()
    f.unemployment = unemployment
    f.worker_skills = {p.id: (1.0, 0.0) for p in sim.people}
    return f


def founded(sector: str, **overrides) -> EconomySimulation:
    """Simulation où des ménages ont de quoi fonder les entreprises d'un secteur propre au lot."""
    sim = simulation(months=1, population=200, **overrides)
    for person in sim.people[:10]:
        person.cash += 1e6
    sim.society.foundings.pop(sector, None)
    sim.step()
    return sim


def firm(sim: EconomySimulation, sector: str):
    return next(c for c in sim.companies if c.active and c.sector == sector and c.employees)


class SocietyRegistryTests(unittest.TestCase):
    """Chaque réglage du lot V est listé et justifié dans la section « Réglages du lot V »
    d'AUTONOMIE.md, existe dans ``SimulationConfig`` et y est neutre pour l'identité."""

    ADDED = {*ALL_SWITCHES, *PARAMETERS}

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot V")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        registry = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), self.ADDED)
        for name in self.ADDED:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)
        for name in ALL_SWITCHES:
            self.assertIs(fields[name].default, False, name)

    def test_disabled_is_identical_and_creates_no_state(self) -> None:
        reference = simulation()
        explicit = simulation(**{name: False for name in ALL_SWITCHES})
        for _ in range(5):
            self.assertEqual(reference.step(), explicit.step())
        self.assertIsNone(getattr(explicit, "society", None))
        self.assertFalse(any(p.credit_banned for p in explicit.people))

    def test_reading_tables_creates_no_state(self) -> None:
        sim = simulation(months=2)
        for reader in (sim.society_history, sim.society_events, sim.works_table, sim.foundations_table,
                       sim.donors_table, sim.brands_table, sim.franchises_table, sim.cooperatives_table,
                       sim.hedge_table, sim.shorts_table, sim.pools_table, sim.crowd_table):
            self.assertEqual(reader(), [])
        self.assertEqual(sim.society_profile(sim.people[0].id), {})
        self.assertIsNone(getattr(sim, "society", None))


class AllMechanismsTests(unittest.TestCase):
    """Les douze mécanismes ensemble : aucune monnaie n'apparaît ni ne disparaît."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sim = EconomySimulation(SimulationConfig(
            seed=11, population_size=200, **{name: True for name in ALL_SWITCHES}
        ))
        cls.residual = 0.0
        for _ in range(120):
            cls.sim.step()
            cls.residual = max(cls.residual, abs(cls.sim.ledger.residual(cls.sim)))
        cls.totals: dict = {}
        for row in cls.sim.society.history:
            for key, value in row.items():
                cls.totals[key] = cls.totals.get(key, 0.0) + value

    def test_money_is_conserved(self) -> None:
        self.assertLess(self.residual, 1e-3)

    def test_state_book_stays_reconciled(self) -> None:
        for record in self.sim.institutions.history:
            self.assertLess(abs(record.cash_residual), 1e-3)
            self.assertLess(abs(record.debt_residual), 1e-3)

    def test_every_month_is_recorded(self) -> None:
        self.assertEqual(len(self.sim.society.history), 120)
        self.assertEqual(len(self.sim.society_history()), 120)

    def test_lottery_splits_stakes_between_state_and_pot(self) -> None:
        stakes, levy, prizes = (self.totals[k] for k in ("loterie_mises", "loterie_etat", "loterie_gains"))
        self.assertGreater(stakes, 0.0)
        self.assertAlmostEqual(levy, 0.5 * stakes, delta=1e-6 * stakes)
        self.assertAlmostEqual(self.sim.society.lottery.cash, stakes - levy - prizes, delta=1e-6 * stakes)
        self.assertEqual(len(self.sim.society.winners), sum(1 for r in self.sim.society.history if r.get("loterie_gains")))

    def test_excise_follows_sales(self) -> None:
        self.assertGreater(self.totals["vice_ventes"], 0.0)
        self.assertAlmostEqual(self.totals["vice_accises"], 0.5 * self.totals["vice_ventes"],
                               delta=1e-6 * self.totals["vice_ventes"])
        self.assertTrue(any(c.sector == VICE_SECTOR for c in self.sim.companies))

    def test_activity_of_the_other_mechanisms(self) -> None:
        for key in ("marques_depenses", "titrisation_emissions", "chantiers_lances", "participatif_prets",
                    "franchises_ouvertes", "fonds_speculatifs_crees"):
            with self.subTest(mesure=key):
                self.assertGreater(self.totals.get(key, 0.0), 0.0)

    def test_securitized_claims_match_their_holders(self) -> None:
        for pool in self.sim.society.pools.values():
            self.assertAlmostEqual(pool["encours"], sum(pool["porteurs"].values()), places=6)
            self.assertTrue(all(amount > 0.0 for amount in pool["porteurs"].values()))

    def test_lent_shares_are_owed_by_a_position(self) -> None:
        for fund in self.sim.society.hedge_funds:
            self.assertEqual(fund.shares, {})
            for position in fund.positions.values():
                self.assertAlmostEqual(position["titres"], sum(position["preteurs"].values()), places=6)


class PersonalBankruptcyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = simulation(months=3, personal_bankruptcy=True)
        sim = self.sim
        self.person, other = sim.people[5], sim.people[6]
        debt = min(40.0 * sim.wage_reference, 0.5 * sim.credit_capacity())
        with sim._consumer_credit_transaction(self.person, debt):
            self.person.consumer_rate = 0.01
            self.person.consumer_debt += debt
            self.person.cash += debt
        # il a tout dépensé : sa trésorerie passe à un autre ménage (monnaie conservée)
        other.cash += self.person.cash
        self.person.cash = 0.0
        self.debt = debt

    def test_commission_writes_off_the_debt_and_bans_the_household(self) -> None:
        sim, person = self.sim, self.person
        losses = sum(b.default_loss for b in sim.companies if b.sector == "bank")
        sim.step()
        self.assertEqual(person.consumer_debt, 0.0)
        self.assertTrue(person.credit_banned)
        self.assertEqual(sim.society.bans[person.id], sim.month + sim.live.credit_ban_months)
        entry = sim.society.relief[-1]
        self.assertEqual((entry["menage"], entry["motif"]), (person.id, "dossier"))
        self.assertGreater(entry["efface"], 0.5 * self.debt)
        self.assertFalse(any(f"consumer:{person.id}" in b.bank_loan_accounts
                             for b in sim.companies if b.sector == "bank"))
        self.assertGreater(sum(b.default_loss for b in sim.companies if b.sector == "bank"), losses)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_banned_household_gets_no_credit_until_the_ban_ends(self) -> None:
        sim, person = self.sim, self.person
        sim.step()
        person.current_wage = 2000.0
        self.assertEqual(extend_consumer_credit([person], 1e9, sim.live, 0.01), {})
        sim.society.bans[person.id] = sim.month + 1
        sim.step()
        self.assertFalse(person.credit_banned)
        self.assertNotIn(person.id, sim.society.bans)

    def test_succession_lifts_the_ban(self) -> None:
        sim, person = self.sim, self.person
        sim.step()
        sim._succeed(person, 0.0)
        self.assertFalse(person.credit_banned)


class PhilanthropyTests(unittest.TestCase):
    def test_a_very_rich_household_gives_and_becomes_known(self) -> None:
        sim = simulation(months=2, philanthropy=True, philanthropy_wealth_multiple=2.0)
        rich = sim.people[0]
        for person in sim.people[1:]:
            rich.cash += 0.5 * person.cash
            person.cash *= 0.5
        sim._society_state().traits[rich.id] = (0.5, 1.0, 0.5)
        sim.step()
        state = sim.society
        self.assertGreater(state.gifts[rich.id], 0.0)
        self.assertGreater(state.fame[rich.id], 0.0)
        cause = state.causes[rich.id]
        self.assertIn(cause, state.foundations)
        spent = sum(row.get(f"depenses_{cause}", 0.0) for row in state.history)
        self.assertAlmostEqual(state.foundations[cause].cash + spent, state.gifts[rich.id],
                               delta=1e-6 * state.gifts[rich.id])
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        self.assertEqual(sim.donors_table()[0]["menage"], rich.id)

    def test_foundations_raise_schooling_and_lower_mortality(self) -> None:
        sim = simulation(months=2, philanthropy=True)
        state = sim._society_state()
        self.assertEqual(sim._society_school_quality(), 0.0)
        self.assertEqual(sim._society_mortality(sim.people[0], 0.01), 0.01)
        state.foundation_effort.update(ecole=0.01, hopital=0.01)
        self.assertAlmostEqual(sim._society_school_quality(), 0.15)
        self.assertAlmostEqual(sim._society_mortality(sim.people[0], 0.01), 0.01 * (1.0 - 0.075))


class LotteryTests(unittest.TestCase):
    def test_a_certain_draw_pays_the_whole_pot_to_one_player(self) -> None:
        sim = simulation(months=4, state_lottery=True, lottery_draw_chance=1.0)
        winners = sim.society.winners
        self.assertEqual(len(winners), 4)
        self.assertTrue(all(w["gain"] > 0.0 for w in winners))
        self.assertLess(sim.society.lottery.cash, 1.0)
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)

    def test_stake_does_not_depend_on_income(self) -> None:
        sim = simulation(months=2, state_lottery=True, lottery_draw_chance=0.0)
        state = sim.society
        poor, rich = sorted(sim.people, key=lambda p: (p.cash, p.id))[0::len(sim.people) - 1]
        for person in sim.people:
            state.traits[person.id] = (1.0, 0.5, 0.5)
            person.cash = max(person.cash, 100.0 * sim.wage_reference)
        poor.cash, rich.cash = 50.0 * sim.wage_reference, 1000.0 * sim.wage_reference
        before = {p.id: p.cash for p in (poor, rich)}
        sim._lottery(flows(sim))
        base = sim.live.lottery_stake_share * sim.wage_reference
        self.assertAlmostEqual(before[poor.id] - poor.cash, 1.5 * base)
        self.assertAlmostEqual(before[rich.id] - rich.cash, 0.5 * base)


class AddictionTests(unittest.TestCase):
    @staticmethod
    def controlled_market(population: int = 1, makers: int = 1) -> EconomySimulation:
        """Marché isolé : goûts, liquidités et produits identiques avant une expérience causale."""
        sim = simulation(months=0, population=80, addictive_goods=True)
        sim._institutions()
        sim.people[:] = sim.people[:population]
        state = sim._society_state()
        reference = sim._vice_reference_price()
        for person in sim.people:
            person.cash = 1e6
            person.unemployment_months = 0
            state.traits[person.id] = (0.5, 0.5, 0.65)
            state.dependence[person.id] = 0.25
        sim.companies[:] = [
            Company(
                id=f"vice_test_{index}", name=f"Fabricant {index}", sector=VICE_SECTOR,
                cash=1e6, wage_offer=1000.0, price=reference, target_employees=1,
                employees=[sim.people[0].id], productivity_trait=1.0,
                previous_units_sold=1.0,
            )
            for index in range(makers)
        ]
        state.vice_units = {c.id: 1.0 for c in sim.companies}
        return sim

    def test_damage_raises_mortality(self) -> None:
        sim = simulation(months=2, addictive_goods=True)
        person = sim.people[0]
        sim.society.damage[person.id] = 1.0
        self.assertAlmostEqual(sim._society_mortality(person, 0.01), 0.02)

    def test_goal_follows_the_trait_and_long_unemployment(self) -> None:
        goal = EconomySimulation._vice_goal
        self.assertEqual(goal(0.2, False), 0.0)
        self.assertGreater(goal(0.2, True), 0.0)
        self.assertAlmostEqual(goal(1.0, False), 1.0)
        self.assertGreater(goal(0.6, True), goal(0.6, False))

    def test_technology_strength_is_increasing_and_bounded(self) -> None:
        sim = self.controlled_market()
        maker = sim.companies[0]
        strengths = []
        for level in (0.0, 0.1, 1.0, 10.0, 1e9):
            maker.tech_level = level
            strengths.append(sim._vice_addictiveness(maker))
        self.assertEqual(strengths[0], 1.0)
        self.assertTrue(all(a < b for a, b in zip(strengths, strengths[1:])))
        self.assertLessEqual(strengths[-1], 1.0 + sim.live.addiction_technology_gain)
        sim.live.addiction_technology_gain = 0.0
        self.assertEqual(sim._vice_addictiveness(maker), 1.0)

    def test_real_technology_purchase_pays_supplier_and_strengthens_product(self) -> None:
        sim = self.controlled_market()
        maker = sim.companies[0]
        maker.previous_revenue = maker.revenue_ema = 10000.0
        maker.profit = 1000.0
        provider = Company(
            id="tech_test", name="Équipements", sector="technology", cash=1000.0,
            wage_offer=1000.0, employees=[sim.people[0].id],
            productivity_trait=1.0, innovation=1.0,
        )
        before = maker.cash, provider.cash, sim._vice_addictiveness(maker)
        sold = trade_technology([provider, maker], sim.resources, sim.live)
        self.assertGreater(sold, 0.0)
        paid = before[0] - maker.cash
        self.assertGreater(paid, 0.0)
        self.assertAlmostEqual(provider.cash - before[1], paid)
        self.assertAlmostEqual(maker.technology_asset_value, paid)
        self.assertAlmostEqual(maker.capital_expenditure, paid)
        self.assertAlmostEqual(maker.supplier_spending[provider.id], paid)
        self.assertGreater(maker.tech_level, 0.0)
        self.assertGreater(sim._vice_addictiveness(maker), before[2])

    def test_technology_without_available_supply_has_no_effect(self) -> None:
        sim = self.controlled_market()
        maker = sim.companies[0]
        maker.previous_revenue = maker.revenue_ema = 10000.0
        maker.profit = 1000.0
        provider = Company(
            id="tech_test", name="Équipements", sector="technology", cash=1000.0,
            wage_offer=1000.0, employees=[], tech_inventory=0.0,
        )
        cash = maker.cash, provider.cash
        self.assertEqual(trade_technology([provider, maker], sim.resources, sim.live), 0.0)
        self.assertEqual((maker.cash, provider.cash), cash)
        self.assertEqual(maker.tech_level, 0.0)
        self.assertEqual(sim._vice_addictiveness(maker), 1.0)

    def test_dependence_follows_strength_of_products_actually_consumed(self) -> None:
        dependencies = []
        for technology in ((0.0,), (10.0, 0.0), (10.0,)):
            sim = self.controlled_market(makers=len(technology))
            for maker, level in zip(sim.companies, technology):
                maker.tech_level = level
            person = sim.people[0]
            money = person.cash + sim.public.cash + sum(c.cash for c in sim.companies)
            sim._vice_sales(flows(sim))
            self.assertGreater(sim.society.month_stats["vice_unites"], 0.0)
            self.assertAlmostEqual(
                person.cash + sim.public.cash + sum(c.cash for c in sim.companies), money,
            )
            dependencies.append(sim.society.dependence[person.id])
        self.assertGreater(dependencies[1], dependencies[0])
        self.assertGreater(dependencies[2], dependencies[1])

    def test_undelivered_product_does_not_strengthen_dependence(self) -> None:
        dependencies = []
        for level in (0.0, 10.0):
            sim = self.controlled_market()
            maker, person = sim.companies[0], sim.people[0]
            maker.tech_level = level
            maker.employees.clear()
            cash = person.cash, maker.cash
            sim._vice_sales(flows(sim))
            self.assertEqual(sim.society.month_stats["vice_unites"], 0.0)
            self.assertEqual((person.cash, maker.cash), cash)
            self.assertEqual(maker.revenue, 0.0)
            dependencies.append(sim.society.dependence[person.id])
        self.assertEqual(dependencies[0], dependencies[1])

    def test_losses_do_not_trigger_a_price_cut_when_volumes_decline(self) -> None:
        sim = self.controlled_market()
        maker, person = sim.companies[0], sim.people[0]
        person_id = person.id
        sim.society.dependence[person_id] = 0.5
        reference = sim._vice_reference_price()
        maker.previous_units_sold = 0.5
        maker.previous_profit = -reference
        maker.expenses = maker.wage_bill = 0.65 * reference
        previous_price = maker.price
        sim._vice_sales(flows(sim))
        self.assertGreater(maker.price, previous_price)
        self.assertLessEqual(maker.price, 2.5 * reference)

    def test_saturated_maker_passes_orders_to_available_competitor(self) -> None:
        sim = self.controlled_market(population=40, makers=2)
        for person in sim.people:
            sim.society.dependence[person.id] = 1.0
        sim.companies[0].productivity_trait = 0.1  # capacité de quinze unités
        sim._vice_sales(flows(sim))
        self.assertAlmostEqual(sum(c.units_sold for c in sim.companies), 40.0)
        self.assertAlmostEqual(sum(c.units_produced for c in sim.companies), 40.0)
        self.assertLessEqual(sim.companies[0].units_sold, 15.0 + 1e-9)
        self.assertAlmostEqual(sum(c.unmet_units for c in sim.companies), 0.0)

    def test_capacity_shortage_is_recorded_as_unserved_demand(self) -> None:
        sim = self.controlled_market(population=40, makers=2)
        for person in sim.people:
            sim.society.dependence[person.id] = 1.0
        for maker in sim.companies:
            maker.productivity_trait = 0.1
        sim._vice_sales(flows(sim))
        self.assertAlmostEqual(sum(c.units_sold for c in sim.companies), 30.0)
        self.assertAlmostEqual(sum(c.unmet_units for c in sim.companies), 10.0)

    def test_demand_ignores_the_price_and_the_state_collects_the_excise(self) -> None:
        sim = founded(VICE_SECTOR, addictive_goods=True)
        makers = [c for c in sim.companies if c.active and c.sector == VICE_SECTOR]
        self.assertTrue(makers)
        for doubling in (1.0, 2.0):
            person = sim.people[10]
            sim.society.dependence[person.id] = 0.5
            sim.society.vice_units = {c.id: 0.0 for c in makers}
            for company in makers:
                company.previous_units_sold = 1.0  # volumes tenus : le prix ne baisse pas
                company.price = sim._vice_reference_price() * doubling / 1.01
                if not company.employees:
                    company.employees.append(sim.people[0].id)
            person.cash = 1000.0 * sim.wage_reference
            sold = sum(c.units_sold for c in makers)
            cash, public = person.cash, sim.public.cash
            f = flows(sim)
            sim._vice_sales(f)
            stats = sim.society.month_stats
            self.assertAlmostEqual(stats["vice_accises"], 0.5 * stats["vice_ventes"])
            self.assertAlmostEqual(sim.public.cash - public, stats["vice_accises"] + f.vat_revenue)
            bought = sum(c.units_sold for c in makers) - sold
            self.assertGreater(cash - person.cash, 0.0)
            with self.subTest(prix=doubling):
                # la quantité voulue par ce ménage ne dépend pas du prix
                self.assertAlmostEqual(stats["vice_unites"], stats["vice_demande"], delta=1e-9 + 1e-6 * bought)


class BrandTests(unittest.TestCase):
    def test_brand_raises_the_weight_in_purchases_and_fades(self) -> None:
        sim = simulation(months=2, brand_marketing=True)
        state = sim._society_state()
        company = firm(sim, "food")
        wage = sim.wage_reference
        state.brands[company.id] = BRAND_HALF * wage
        self.assertAlmostEqual(sim._brand_factor(company.id), 1.25)
        affinity = sim._brand_affinity("food", None)
        self.assertEqual(affinity[sim.people[0].id], {company.id: 1.25})
        self.assertIsNone(sim._brand_affinity("goods", None))
        merged = sim._brand_affinity("food", {sim.people[0].id: {company.id: 2.0}})
        self.assertAlmostEqual(merged[sim.people[0].id][company.id], 2.5)
        company.previous_profit = -1.0  # pas de campagne ce mois : la notoriété s'efface seulement
        sim._brand_campaigns()
        self.assertAlmostEqual(state.brands[company.id], 0.97 * BRAND_HALF * wage)

    def test_campaigns_are_paid_to_agencies_within_their_capacity(self) -> None:
        sim = founded("marketing", brand_marketing=True)
        agencies = [c for c in sim.companies if c.active and c.sector == "marketing"]
        self.assertTrue(agencies)
        advertiser = firm(sim, "goods")
        advertiser.previous_profit, advertiser.previous_revenue = 1.0, 1e9
        advertiser.cash += 1e9
        for agency in agencies:
            if not agency.employees:
                agency.employees.append(sim.people[0].id)
        capacity = sum(len(a.employees) * 4.0 * sim.wage_reference * a.productivity_trait for a in agencies)
        before = sum(a.cash for a in agencies)
        sim._brand_campaigns()
        self.assertAlmostEqual(sum(a.cash for a in agencies) - before, capacity, delta=1e-6 * capacity)
        self.assertGreater(sim.society.brands[advertiser.id], 0.0)
        self.assertGreater(sim.society.month_stats["marques_non_servies"], 0.0)


class FranchiseTests(unittest.TestCase):
    def test_opening_then_royalties(self) -> None:
        sim = simulation(months=3, franchising=True)
        franchisor = firm(sim, "food")
        franchisor.age_months, franchisor.previous_profit, franchisor.previous_unmet_units = 30, 1000.0, 5.0
        for other in sim.companies:
            if other is not franchisor and other.sector in ("food", "goods"):
                other.previous_unmet_units = 0.0
        fee = sim.live.franchise_entry_fee * sim._wage_scale()
        cabinet = {office.person_id for office in sim.institutions.offices.values()}
        founder = next(p for p in sim.people if p.id not in cabinet and p.employer_id != "state")
        for person in sim.people:
            person.entrepreneurial_spirit = min(person.entrepreneurial_spirit, 0.9)
        founder.entrepreneurial_spirit, founder.founded_company = 1.0, False
        founder.cash = 10.0 * fee / sim.live.startup_investment_fraction
        f = flows(sim)
        f.unmet_food_demand = 10.0
        revenue = franchisor.revenue
        sim._open_franchises(f)
        link_id = next(iter(sim.society.franchises))
        franchisee = next(c for c in sim.companies if c.id == link_id)
        self.assertTrue(franchisee.id.startswith("franchise_"))
        self.assertEqual(franchisee.founder_id, founder.id)
        self.assertEqual((franchisee.sector, franchisee.price), (franchisor.sector, franchisor.price))
        self.assertAlmostEqual(franchisee.productivity_trait, FRANCHISE_METHODS * franchisor.productivity_trait)
        self.assertAlmostEqual(franchisor.revenue - revenue, fee)
        self.assertTrue(founder.founded_company)
        # une seule ouverture par semestre
        sim._open_franchises(f)
        self.assertEqual(len(sim.society.franchises), 1)
        franchisee.revenue = 1000.0
        cash = franchisor.cash
        sim._franchise_royalties()
        self.assertAlmostEqual(franchisor.cash - cash, 50.0)
        self.assertAlmostEqual(sim.society.franchises[link_id]["redevances"], 50.0)
        # le contrat s'éteint avec le franchiseur
        franchisor.active = False
        sim._franchise_royalties()
        self.assertEqual(sim.society.franchises, {})


class WorkerBuyoutTests(unittest.TestCase):
    def test_laid_off_staff_restart_a_liquidated_firm(self) -> None:
        sim = simulation(months=3, worker_buyouts=True)
        company = max((c for c in sim.companies if c.active and c.sector == "goods"),
                      key=lambda c: len(c.employees))
        staff = [p for p in sim.people if p.id in set(company.employees)]
        self.assertGreaterEqual(len(staff), 3)
        f = flows(sim)
        sim._worker_buyouts(f)  # mémoire des salariés du mois
        for person in staff:
            person.employer_id = None
        company.active, company.exit_reason, company.exit_month = False, "bankrupt", sim.month
        money = sum(p.cash for p in sim.people) + sim.public.cash
        sim._worker_buyouts(f)
        coop_id, entry = next(iter(sim.society.cooperatives.items()))
        coop = next(c for c in sim.companies if c.id == coop_id)
        self.assertEqual((coop.sector, coop.price), (company.sector, company.price))
        self.assertEqual(sorted(coop.employees), sorted(p.id for p in staff))
        self.assertGreater(entry["avance"], 0.0)
        self.assertAlmostEqual(coop.cash, money - sum(p.cash for p in sim.people) - sim.public.cash)
        for person in staff:
            self.assertEqual(person.employer_id, coop.id)
            self.assertAlmostEqual(person.shares[coop.id], 1000.0 / len(staff))
            self.assertEqual(person.coop_shares[coop.id], person.shares[coop.id])
        self.assertIn("Reprise en coopérative", sim.institutions.journal[-1].decision)

    def test_state_advance_keeps_the_state_book_reconciled(self) -> None:
        sim = EconomySimulation(SimulationConfig(seed=1001, population_size=300, worker_buyouts=True))
        for _ in range(200):
            sim.step()
            if sim.society.cooperatives:
                break
        self.assertTrue(sim.society.cooperatives)
        self.assertLess(abs(sim.institutions.history[-1].cash_residual), 1e-3)

    def test_no_buyout_below_the_minimum_staff(self) -> None:
        sim = simulation(months=3, worker_buyouts=True, buyout_min_workers=10 ** 6)
        company = firm(sim, "goods")
        sim._worker_buyouts(flows(sim))
        company.active, company.exit_reason, company.exit_month = False, "bankrupt", sim.month
        sim._worker_buyouts(flows(sim))
        self.assertEqual(sim.society.cooperatives, {})


class ShortSellingTests(unittest.TestCase):
    def test_borrow_sell_then_profit_from_a_bankruptcy(self) -> None:
        sim = simulation(months=3, short_selling=True, hedge_funds=0)
        state = sim._society_state()
        fund = HedgeFund("hedge_test", "Fonds test", "hedge", 100000.0, 0.0)
        fund.capital = 100000.0
        state.hedge_funds.append(fund)
        target = max((c for c in sim.companies if c.active and c.sector == "goods"),
                     key=lambda c: sum(p.shares.get(c.id, 0.0) for p in sim.people))
        target.loss_streak, target.debt, target.stock_price = 5, max(target.debt, 1e9), 10.0
        held = sum(p.shares.get(target.id, 0.0) for p in sim.people)
        self.assertGreater(held, 0.0)
        orders = sim._short_orders()
        self.assertEqual([(o[0], o[1]) for o in orders], [(fund, target.id)])
        quantity = orders[0][2]
        self.assertGreater(quantity, 0.0)
        self.assertAlmostEqual(sum(p.shares.get(target.id, 0.0) for p in sim.people), held - quantity)
        # la séance n'écoule que la moitié des titres
        fund.shares[target.id] = quantity / 2.0
        fund.cash += quantity / 2.0 * 10.0
        sim._settle_short_sales()
        self.assertEqual(fund.shares, {})
        self.assertAlmostEqual(sum(p.shares.get(target.id, 0.0) for p in sim.people), held - quantity / 2.0)
        position = fund.positions[target.id]
        self.assertAlmostEqual(position["titres"], quantity / 2.0)
        self.assertAlmostEqual(position["entree"], 10.0)
        self.assertAlmostEqual(sum(position["preteurs"].values()), quantity / 2.0)
        # faillite : le fonds ne doit plus rien et garde le produit de la vente
        target.active, target.exit_reason = False, "bankrupt"
        cash = fund.cash
        sim._short_orders()
        self.assertEqual(fund.positions.get(target.id), None)
        self.assertEqual(fund.cash, cash)
        closed = state.shorts[-1]
        self.assertEqual(closed["motif"], "faillite")
        self.assertAlmostEqual(closed["gain"], quantity / 2.0 * 10.0)

    def test_stop_loss_pays_the_lenders_at_the_market_price(self) -> None:
        sim = simulation(months=3, short_selling=True, hedge_funds=0)
        state = sim._society_state()
        fund = HedgeFund("hedge_test", "Fonds test", "hedge", 1000.0, 0.0)
        state.hedge_funds.append(fund)
        target = firm(sim, "goods")
        target.stock_price = 20.0
        lender = sim.people[3]
        fund.positions[target.id] = {"titres": 10.0, "preteurs": {lender.id: 10.0}, "entree": 10.0,
                                     "cours": 10.0, "mois": sim.month, "produit": 100.0, "frais": 0.0}
        cash = lender.cash
        sim._short_orders()
        fee = sim.live.short_fee_rate * 10.0 * 20.0
        self.assertAlmostEqual(lender.cash - cash, 200.0 + fee)
        self.assertAlmostEqual(state.shorts[-1]["gain"], 100.0 - 200.0 - fee)
        self.assertEqual(state.shorts[-1]["motif"], "rachat à perte")


class SecuritizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = simulation(months=3, mortgage_securitization=True)
        sim = self.sim
        self.borrower = sim.people[7]
        self.debt = min(50.0 * sim.wage_reference, 0.25 * sim.credit_capacity())
        sim._book_bank_loan(f"mortgage:{self.borrower.id}", self.debt)
        person = self.borrower
        person.mortgage_debt, person.mortgage_rate = self.debt, 0.005
        person.mortgage_months_remaining, person.owns_home = 240, True
        person.mortgage_payment = self.debt * 0.008
        person.cash += self.debt  # le prêt crée le dépôt
        self.banks = [b for b in sim.companies if b.sector == "bank" and b.active
                      and b.bank_loan_accounts.get(f"mortgage:{person.id}", 0.0) > 0.0]

    def test_issue_frees_capital_and_keeps_the_ledger_balanced(self) -> None:
        sim = self.sim
        capacity = sim.credit_capacity()
        for _ in range(3):
            sim.step()
            self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        state = sim.society
        self.assertGreater(state.securitized(), 0.0)
        for bank in self.banks:
            pool = state.pools.get(bank.id)
            if pool is None:
                continue
            book = sim._mortgage_book(bank)
            self.assertLessEqual(pool["encours"], 0.5 * book * 1.01 + 1e-6)
            sim.live.mortgage_securitization = False
            full = sim._bank_risk_exposure(bank)
            sim.live.mortgage_securitization = True
            self.assertAlmostEqual(full - sim._bank_risk_exposure(bank), min(pool["encours"], full), places=4)
        self.assertGreater(sum(row.get("titrisation_coupons", 0.0) for row in state.history), 0.0)
        self.assertGreater(capacity, 0.0)

    def test_foreclosure_loss_falls_on_the_holders(self) -> None:
        sim, person = self.sim, self.borrower
        state = sim._society_state()
        holder = sim.people[20]
        for bank in self.banks:
            half = 0.5 * bank.bank_loan_accounts[f"mortgage:{person.id}"]
            state.pools[bank.id] = {"encours": half, "porteurs": {holder.id: half}}
        snapshot = sim._abs_snapshot()
        # saisie : 40 % de la dette recouvrés, le reste passé en perte par les banques
        foreclosed = {person.id: (self.debt, 0.4 * self.debt)}
        sim._write_off_household_loan(f"mortgage:{person.id}", 0.6 * self.debt)
        person.mortgage_debt = 0.0
        losses = sum(b.default_loss for b in self.banks)
        cash = holder.cash
        sim._abs_pass_through(snapshot, {person.id: self.debt}, foreclosed, 0.0)
        self.assertAlmostEqual(losses - sum(b.default_loss for b in self.banks), 0.5 * 0.6 * self.debt)
        self.assertAlmostEqual(holder.cash - cash, 0.5 * 0.4 * self.debt)
        self.assertAlmostEqual(state.securitized(), 0.0, places=6)


class CrowdfundingTests(unittest.TestCase):
    def test_households_lend_directly_to_a_young_firm(self) -> None:
        sim = simulation(months=3, crowdfunding=True)
        company = firm(sim, "services")
        company.founder_id, company.age_months = sim.people[0].id, 5
        company.productive_capital_value += 1e6  # fonds propres positifs
        surplus = company.cash
        sim.people[1].cash += surplus  # la trésorerie manque (monnaie conservée)
        company.cash = 0.0
        for person in sim.people:
            person.risk_tolerance = 1.0
        money = sum(p.cash for p in sim.people) + company.cash
        sim._crowdfunding()
        self.assertGreater(company.bond_debt, 0.0)
        self.assertAlmostEqual(company.bond_debt, sum(p.corporate_bonds.get(company.id, 0.0) for p in sim.people))
        self.assertAlmostEqual(sum(p.cash for p in sim.people) + company.cash, money)
        self.assertAlmostEqual(company.cash, company.bond_debt)
        self.assertAlmostEqual(company.bond_rate, sim.policy_rate + sim.live.crowdfunding_premium)
        self.assertEqual(sim.crowd_table()[0]["id"], company.id)

    def test_old_firms_are_not_eligible(self) -> None:
        sim = simulation(months=3, crowdfunding=True)
        for company in sim.companies:
            company.age_months = 100
            company.cash = 0.0
        sim._crowdfunding()
        self.assertEqual(sim.society.crowd, {})


class UnrestTests(unittest.TestCase):
    def test_riot_costs_work_and_stocks_and_wins_a_concession(self) -> None:
        sim = simulation(months=3, social_unrest=True)
        state = sim._society_state()
        state.tension = 3.0
        maker = firm(sim, "goods")
        maker.goods_inventory = 100.0
        doctrine = sim.institutions.doctrine
        transfers, benefit = doctrine.transfer_share, doctrine.benefit_factor
        f = flows(sim, unemployment=0.5)
        sim._social_unrest(f)
        loss = sim.live.unrest_production_loss
        self.assertTrue(all(abs(skill - (1.0 - loss)) < 1e-12 for skill, _ in f.worker_skills.values()))
        self.assertAlmostEqual(maker.goods_inventory, 100.0 * (1.0 - RIOT_DAMAGE))
        conceded = sim.live.unrest_concession * (0.5 if sim.government.orientation < 35.0 else 1.0)
        self.assertAlmostEqual(doctrine.transfer_share, transfers * (1.0 + conceded))
        self.assertAlmostEqual(doctrine.benefit_factor, benefit * (1.0 + conceded))
        protest = state.protests[-1]
        self.assertTrue(protest["emeute"])
        self.assertAlmostEqual(state.tension, protest["tension"] * (1.0 - UNREST_RELIEF))
        # pas deux manifestations de suite
        state.tension = 3.0
        sim._social_unrest(flows(sim, unemployment=0.5))
        self.assertEqual(len(state.protests), 1)

    def test_a_government_stops_conceding(self) -> None:
        sim = simulation(months=3, social_unrest=True)
        state = sim._society_state()
        state.tension, state.concessions = 1.5, UNREST_MAX_CONCESSIONS
        state.concession_term = sim.government.since_month
        transfers = sim.institutions.doctrine.transfer_share
        sim._social_unrest(flows(sim, unemployment=0.3))
        self.assertEqual(state.protests[-1]["concession"], 0.0)
        self.assertEqual(sim.institutions.doctrine.transfer_share, transfers)
        self.assertGreater(state.tension, 1.0)

    def test_calm_economy_has_no_pressure(self) -> None:
        sim = simulation(months=3, social_unrest=True)
        self.assertEqual(sim._unrest_pressure(0.05) if not sim.history or
                         sim.history[-1].household_top10_share <= sim.live.unrest_inequality else 0.0, 0.0)


class PublicWorksTests(unittest.TestCase):
    def test_site_completion_gain_and_decay(self) -> None:
        sim = simulation(months=3, public_works=True, public_works_debt_limit=1000.0)
        state = sim.society
        self.assertEqual(len(state.works), 1)
        work = state.works[0]
        self.assertEqual((work["type"], work["fin"]), (WORKS[0][0], None))
        self.assertIn("Grands travaux", " ".join(d.decision for d in sim.institutions.journal))
        work["paye"] = work["cout"]
        sim.step()
        self.assertEqual((work["fin"], work["etat"] > 0.99), (sim.month, True))
        self.assertLess(abs(sim.ledger.residual(sim)), 1e-3)
        bonus = sim.live.public_capital_bonus
        sim._society_live_effects()
        self.assertAlmostEqual(sim.live.public_capital_bonus - bonus, sim.live.public_works_gain * work["etat"])
        # sans fonds, pas d'entretien : l'ouvrage se dégrade
        condition = work["etat"]
        sim._state_funds = lambda amount: 0.0
        sim._public_works_step(flows(sim))
        self.assertAlmostEqual(work["etat"], condition - sim.live.public_works_wear_rate)
        self.assertEqual(work["entretien"], 0.0)

    def test_no_site_above_the_debt_limit(self) -> None:
        sim = simulation(months=1, public_works=True, public_works_debt_limit=0.0)
        sim.public.debt = max(sim.public.debt, 1.0)
        sim.society.works.clear()
        sim.society.last_launch = -10 ** 6
        sim._public_works_step(flows(sim))
        self.assertEqual(sim.society.works, [])


if __name__ == "__main__":
    unittest.main()
