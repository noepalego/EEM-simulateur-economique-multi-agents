"""Mécanismes ajoutés par la mission « améliorations » (novembre 2026).

Logement (demandes de location, foncier de la ville, louer ou vendre, parc locatif complet),
logements-patrimoine des ménages (achat selon le rendement, revente), fondateurs (vente de leurs
titres, capital émis), introduction en Bourse, indicateurs d'inégalités et leviers de l'État.
"""
import random
import unittest

from economy.expectations import update_housing_gaps
from economy.models import Company, Person, SimulationConfig
from economy.real_estate import allocate_housing_and_collect_rent, buy_homes
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def promoter(cid: str, homes: int, cash: float = 1_000_000.0) -> Company:
    company = Company(cid, cid, "real_estate", cash, 1500.0)
    company.property_inventory = homes
    company.active = True
    return company


def small(seed: int = 5, months: int = 0, **overrides) -> EconomySimulation:
    simulation = EconomySimulation(SimulationConfig(seed=seed, population_size=60, **overrides))
    for _ in range(months):
        simulation.step()
    return simulation


class HousingMarketTests(unittest.TestCase):
    """Lot 1 : la demande des ménages sans logement est vue, le foncier suit la ville."""

    def test_unhoused_households_file_applications_split_between_promoters(self) -> None:
        firms = [promoter("a", 1), promoter("b", 1)]
        people = [Person(f"p{i}", f"P{i}", 5000.0) for i in range(5)]
        for person in people:
            person.income_this_turn = 2000.0
        allocate_housing_and_collect_rent(firms, people, 600.0)
        self.assertEqual([f.occupied_units for f in firms], [1, 1])
        # trois ménages restent sans logement : 2 demandes au premier promoteur, 1 au second
        self.assertEqual([f.unserved_rentals for f in firms], [2, 1])

    def test_promoter_forecast_counts_unserved_applications(self) -> None:
        config = SimulationConfig(demand_forecast_error=0.0, housing_vacancy_norm=0.0)
        firm = promoter("a", 2)
        firm.occupied_units, firm.unserved_rentals = 2, 3
        update_housing_gaps([firm], config, random.Random(1))
        self.assertEqual(firm.housing_gap, 3)
        firm.unserved_rentals = 0
        update_housing_gaps([firm], config, random.Random(1))
        self.assertEqual(firm.housing_gap, 0)

    def test_land_expands_with_the_whole_city_not_the_promoters_balance_sheet(self) -> None:
        simulation = small()
        for company in simulation.companies:
            if company.sector == "real_estate":
                company.property_inventory = 0
                company.construction_pipeline = []
        for person in simulation.people:
            person.owns_home = True
        simulation.resources.land_units = 0
        simulation._land_progress = 0.0
        for _ in range(12):
            simulation._update_housing_costs()
        expected = int(12 * len(simulation.people) * simulation.config.land_expansion_rate)
        self.assertGreaterEqual(simulation.resources.land_units, expected)
        self.assertGreater(simulation.resources.land_units, 0)

    def test_rental_stock_includes_household_landlords(self) -> None:
        simulation = small()
        promoters = sum(c.property_inventory for c in simulation.companies
                        if c.active and c.sector == "real_estate")
        simulation.people[0].investment_homes = 3
        self.assertEqual(simulation._rental_stock(), promoters + 3)

    def test_a_promoter_without_tenants_sells_to_households(self) -> None:
        # le prix de réserve « louer ou vendre » a été retiré (mesure sur 800 mois) : un promoteur
        # vend à tout ménage solvable, comme avant la mission
        config = SimulationConfig()
        buyer = Person("p", "Acheteur", 10 * config.housing_price)
        buyer.income_this_turn = 5000.0
        firm = promoter("a", 1)
        buy_homes([firm], [buyer], config.housing_price, config.housing_build_cost, config)
        self.assertTrue(buyer.owns_home)
        self.assertEqual(firm.property_inventory, 0)
        self.assertFalse(hasattr(EconomySimulation, "_promoter_reserve_prices"))


class HouseholdLandlordTests(unittest.TestCase):
    """A1 : un ménage n'achète un logement-patrimoine que s'il rapporte, et peut le revendre."""

    def test_investment_purchase_requires_a_rental_yield_above_savings(self) -> None:
        config = SimulationConfig(home_investment_probability=1.0)
        investor = Person("p", "Investisseur", 10 * config.housing_price, owns_home=True)
        firm = promoter("a", 3)
        args = ([firm], [investor], config.housing_price, config.housing_build_cost, config)
        buy_homes(*args, rng=random.Random(1), investment_yield=0.01, required_return=0.04)
        self.assertEqual(investor.investment_homes, 0)
        buy_homes(*args, rng=random.Random(1), investment_yield=0.12, required_return=0.04)
        self.assertEqual(investor.investment_homes, 1)

    def test_household_landlord_sells_to_a_buyer_without_creating_money(self) -> None:
        config = SimulationConfig()
        seller = Person("s", "Bailleur", 0.0, owns_home=True, investment_homes=2)
        buyer = Person("b", "Acheteur", 2 * config.housing_price)
        buyer.income_this_turn = 4000.0
        before = seller.cash + buyer.cash
        buy_homes([], [buyer, seller], config.housing_price, config.housing_build_cost, config,
                  household_offers={"s": 1})
        self.assertTrue(buyer.owns_home)
        self.assertEqual(seller.investment_homes, 1)
        self.assertAlmostEqual(seller.cash, config.housing_price)
        self.assertAlmostEqual(seller.cash + buyer.cash, before)

    def test_vacant_home_with_low_yield_is_offered_for_sale(self) -> None:
        simulation = small(months=2)
        landlord = simulation.people[0]
        landlord.investment_homes, landlord.rented_homes = 2, 0
        landlord.risk_tolerance = 0.5
        simulation.rent = 1.0  # loyer dérisoire : rendement attendu inférieur à l'épargne
        _, _, offers = simulation._household_housing_market()
        self.assertEqual(offers.get(landlord.id), 2)
        landlord.rented_homes = 2  # loués : rien à vendre
        _, _, offers = simulation._household_housing_market()
        self.assertNotIn(landlord.id, offers)


class FounderTests(unittest.TestCase):
    """Lot 3 : vente des titres du fondateur, capital émis, introduction en Bourse (A2)."""

    def test_concentrated_founder_diversifies_at_the_rebalancing_pace(self) -> None:
        from economy.stock_market import _founder_sale

        config = SimulationConfig()
        founder = Person("f", "Fondatrice", 10_000.0, risk_tolerance=0.5)
        # ligne de 990 000 € pour 1 000 000 € de fortune : limite 50 % (0,25 + 0,5 × 0,5)
        sold = _founder_sale(founder, 990.0, 0.0, 1000.0, 990_000.0, 0.0, config, 0.15)
        self.assertAlmostEqual(sold, 0.15 * (990_000.0 - 500_000.0) / 1000.0)
        # ce que le rééquilibrage vend déjà compte dans sa décision
        self.assertAlmostEqual(
            _founder_sale(founder, 990.0, 50.0, 1000.0, 990_000.0, 0.0, config, 0.15), sold - 50.0)

    def test_diversified_founder_sells_only_to_rebuild_its_cash_reserve(self) -> None:
        from economy.stock_market import _founder_sale

        config = SimulationConfig()
        founder = Person("f", "Fondateur", 900_000.0, risk_tolerance=0.5)
        self.assertEqual(_founder_sale(founder, 100.0, 0.0, 1000.0, 100_000.0, 0.0, config, 0.15), 0.0)
        founder.cash = 1_000.0
        founder.bond_holdings = 900_000.0
        sold = _founder_sale(founder, 100.0, 0.0, 1000.0, 100_000.0, 6_000.0, config, 0.15)
        self.assertAlmostEqual(sold, 5.0)  # 5 000 € manquants au cours de 1 000 €

    def test_initial_public_offering_serves_the_company_before_sellers(self) -> None:
        from economy.stock_market import _clear_stock_market

        config = SimulationConfig(stock_flow_impact=0.0, stock_adjustment_speed=0.0)
        firm = Company("startup_1", "Jeune", "goods", 0.0, 1500.0, stock_price=10.0,
                       shares_outstanding=1000.0, treasury_shares=250.0)
        founder = Person("f", "Fondateur", 0.0, shares={"startup_1": 750.0})
        buyer = Person("b", "Acheteur", 3000.0)
        money = founder.cash + buyer.cash + firm.cash
        _clear_stock_market(firm, 10.0, 10.0, [(buyer, 3000.0)], [(founder, 100.0)], config,
                            primary_first=True)
        self.assertAlmostEqual(firm.cash, 2500.0)       # 250 titres offerts émis d'abord
        self.assertAlmostEqual(founder.cash, 500.0)     # puis 50 titres du fondateur
        self.assertAlmostEqual(buyer.shares["startup_1"], 300.0)
        self.assertAlmostEqual(founder.cash + buyer.cash + firm.cash, money)
        # sans introduction : les ventes du fondateur passent avant la levée
        firm2 = Company("startup_2", "Autre", "goods", 0.0, 1500.0, stock_price=10.0,
                        shares_outstanding=1000.0, treasury_shares=250.0)
        founder2 = Person("g", "Fondatrice", 0.0, shares={"startup_2": 750.0})
        buyer2 = Person("c", "Acheteuse", 3000.0)
        _clear_stock_market(firm2, 10.0, 10.0, [(buyer2, 3000.0)], [(founder2, 100.0)], config)
        self.assertAlmostEqual(founder2.cash, 1000.0)
        self.assertAlmostEqual(firm2.cash, 2000.0)

    def test_founder_stake_is_measured_on_issued_capital(self) -> None:
        simulation = small()
        person = simulation.people[0]
        firm = Company("startup_9_1_x", "Jeune", "goods", 0.0, 1500.0, shares_outstanding=1000.0,
                       treasury_shares=250.0, founder_id=person.id)
        person.shares[firm.id] = 750.0
        simulation.companies.append(firm)
        name, stake = simulation.founder_stake(firm)
        self.assertIn(person.name, name)
        self.assertAlmostEqual(stake, 1.0)
        self.assertAlmostEqual(simulation._company_shares(firm.id), 750.0)


class DisplayAndIndicatorTests(unittest.TestCase):
    """Lot 4 : fondateur visible, indicateurs d'inégalités et de logement (A3)."""

    def test_household_founded_company_is_named_after_its_founder(self) -> None:
        from economy.institutions import display_name

        simulation = small(seed=8, company_founder_probability=1.0, entrepreneurial_trait_threshold=0.0,
                           startup_threshold=1000.0)
        created = []
        for _ in range(120):
            simulation.step()
            created = [c for c in simulation.companies if c.founder_id is not None]
            if created:
                break
        self.assertTrue(created, "aucune entreprise fondée en 120 mois")
        firm = created[0]
        surname = display_name(firm.founder_id).split(" ", 1)[-1]
        self.assertIn(surname, firm.name)
        self.assertTrue(firm.name.endswith(")"))
        name, _ = simulation.founder_stake(firm)
        self.assertIn(display_name(firm.founder_id), name)

    def test_monthly_record_carries_inequality_and_housing_indicators(self) -> None:
        simulation = small(months=3)
        record = simulation.history[-1]
        self.assertGreaterEqual(record.income_gini, 0.0)
        self.assertLessEqual(record.disposable_income_gini, 1.0)
        self.assertGreater(record.household_top1_share, 0.0)
        self.assertLessEqual(record.household_top1_share, record.household_top10_share + 1e-12)
        self.assertGreaterEqual(record.poverty_rate, 0.0)
        unhoused = sum(1 for p in simulation.people if not p.owns_home and p.rental_owner_id is None)
        self.assertEqual(record.unhoused_households, unhoused)
        self.assertGreaterEqual(record.housing_wealth_share, 0.0)
        self.assertLessEqual(record.housing_wealth_share, 1.0)


class StateLeverTests(unittest.TestCase):
    """Lot 5 : barème progressif réel, revenus du capital, transferts ciblés, salaire minimum."""

    def _taxed(self, orientation: float):
        simulation = small(months=1, political_cycle_enabled=False,
                           initial_political_orientation=orientation)
        live = simulation.live
        live.income_tax_rate, live.top_marginal_income_tax_rate, live.income_tax_threshold = 0.1, 0.2, 3000.0
        simulation.institutions.tax_backlog = {}
        worker = Person("w", "Salarié", 10_000.0, income_this_turn=4000.0)
        rentier = Person("r", "Rentier", 10_000.0, income_this_turn=0.0)
        rentier.capital_income_last_month = 4000.0
        simulation.people = [worker, rentier]
        simulation._collect_assessed_taxes(1.0)
        return simulation, worker, rentier

    def test_surtax_applies_above_the_threshold_and_capital_joins_the_scale(self) -> None:
        simulation, worker, rentier = self._taxed(60.0)
        self.assertIsNone(simulation.institutions.doctrine.capital_flat_factor)
        expected = 3000.0 * 0.1 + 1000.0 * (0.1 + 0.2)
        self.assertAlmostEqual(worker.taxes_paid, expected)
        self.assertAlmostEqual(rentier.taxes_paid, expected)  # même barème pour le capital

    def test_right_wing_government_taxes_capital_income_at_a_flat_reduced_rate(self) -> None:
        simulation, worker, rentier = self._taxed(10.0)
        flat = simulation.institutions.doctrine.capital_flat_factor
        self.assertIsNotNone(flat)
        self.assertLess(flat, 1.0)
        self.assertAlmostEqual(rentier.taxes_paid, 4000.0 * 0.1 * flat)
        self.assertLess(rentier.taxes_paid, worker.taxes_paid)

    def test_threshold_follows_the_nominal_wage_level(self) -> None:
        simulation = small(months=1)
        simulation.wage_reference = 2.0 * simulation.config.starting_wage
        simulation._apply_budget_decisions()
        self.assertAlmostEqual(simulation.live.income_tax_threshold,
                               2.0 * simulation.config.income_tax_threshold)

    def test_capital_income_of_the_month_enters_next_month_base(self) -> None:
        simulation = small(months=1)
        person = simulation.people[0]
        person.dividend_income_this_turn, person.rent_income_this_turn = 120.0, 80.0
        simulation.step()
        self.assertAlmostEqual(person.capital_income_last_month, 200.0)

    def test_targeted_transfers_go_to_households_below_the_median(self) -> None:
        from economy.government import pay_public_transfers

        people = [Person(f"p{i}", f"P{i}", 0.0, income_this_turn=income)
                  for i, income in enumerate((0.0, 1000.0, 2000.0, 3000.0, 4000.0))]
        paid = pay_public_transfers(people, 1000.0, targeting=1.0)
        self.assertAlmostEqual(paid, 1000.0)
        self.assertAlmostEqual(sum(p.cash for p in people), 1000.0)  # rien de créé
        self.assertGreater(people[0].cash, people[1].cash)
        self.assertEqual(people[3].cash, 0.0)
        universal = [Person(f"u{i}", f"U{i}", 0.0, income_this_turn=float(i)) for i in range(4)]
        pay_public_transfers(universal, 400.0)
        self.assertTrue(all(abs(p.cash - 100.0) < 1e-9 for p in universal))

    def test_doctrines_derive_targeting_and_capital_regime_without_new_draws(self) -> None:
        from economy.institutions import make_doctrine

        config = SimulationConfig()
        left, right = make_doctrine(90.0, random.Random(4), config), make_doctrine(10.0, random.Random(4), config)
        self.assertGreater(left.transfer_targeting, 0.5)
        self.assertEqual(right.transfer_targeting, 0.0)
        self.assertIsNone(left.capital_flat_factor)
        self.assertLess(right.capital_flat_factor, 0.5)
        # dérivés des positions tirées, sans tirage supplémentaire
        self.assertAlmostEqual(left.transfer_targeting,
                               max(0.0, min(1.0, left.positions["social_transfers"])))
        self.assertAlmostEqual(right.capital_flat_factor, max(0.0, 1.0 + right.positions["top_tax"]))

    def test_social_government_freezes_a_minimum_above_its_ambition(self) -> None:
        simulation = small(months=14, political_cycle_enabled=False, initial_political_orientation=90.0)
        inst = simulation.institutions
        inst.minimum_wage = level = 1.2 * simulation._lagged_median_wage()
        for _ in range(13):
            simulation.step()
        if any(r.get("inflation", 0.0) > 0.0 for r in inst.minimum_wage_revisions[-1:]):
            self.assertTrue(any("gel décidé" in d.decision for d in inst.journal))
        self.assertGreaterEqual(inst.minimum_wage, level)  # jamais de baisse
        self.assertAlmostEqual(inst.minimum_wage_revisions[-1]["indexation"], 0.0)

    def test_minimum_wage_boost_is_not_diluted_over_the_term(self) -> None:
        simulation = small(months=14, political_cycle_enabled=False, initial_political_orientation=95.0)
        inst = simulation.institutions
        inst.minimum_wage = 0.3 * simulation._lagged_median_wage()
        for _ in range(13):
            simulation.step()
        boosts = [r["boost"] for r in inst.minimum_wage_revisions if r["kind"] == "revalorisation"]
        self.assertAlmostEqual(boosts[-1], simulation.live.minimum_wage_boost_cap)


class ImprovementRegistryTests(unittest.TestCase):
    """Les bornes ajoutées en novembre 2026 sont listées et justifiées dans AUTONOMIE.md."""

    NEW_FIELDS = ("founder_max_stake_share", "founder_stake_risk_weight", "ipo_window_months")

    def _section(self) -> dict[str, str]:
        import os
        import re

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Bornes et hypothèses ajoutées en novembre 2026")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        return {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}

    def test_new_bounds_are_listed_with_a_justification(self) -> None:
        import dataclasses

        from economy import institutions

        rows = self._section()
        fields = {f.name for f in dataclasses.fields(SimulationConfig)}
        for name in self.NEW_FIELDS:
            self.assertIn(name, rows)
        for name, text in rows.items():
            with self.subTest(borne=name):
                self.assertTrue(name in fields or hasattr(institutions, name)
                                or name in institutions.Doctrine.__dataclass_fields__)
                self.assertGreaterEqual(len(text), 60)


if __name__ == "__main__":
    unittest.main()
