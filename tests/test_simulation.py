import math
import unittest
import random
from unittest.mock import patch

from economy.business import (
    adjust_profit_sharing_wages,
    invest_in_production_capacity,
)
from economy.finance import (
    create_household_startups,
    extend_credit,
    request_operating_credit,
    resolve_distressed_companies,
    service_company_debt,
)
from economy.goods import (
    charge_inventory_holding_cost,
    collect_recyclable_materials,
    produce_food,
    sell_consumer_goods,
    sell_food,
    trade_technology,
    update_food_price,
)
from economy.government import (
    buy_public_goods,
    pay_unemployment_benefits,
    unemployment_benefit_entitlement,
    pay_public_transfers,
)
from economy.labor import allocate_and_pay_labor, workforce_targets
from economy.labor import (
    effective_worker_count,
    redistribute_orphan_fund,
    update_workforce_targets,
)
from economy.models import Company, Person, Resources, SimulationConfig
from economy.real_estate import (
    allocate_housing_and_collect_rent,
    build_housing,
    buy_homes,
    complete_construction,
    maintain_housing,
    pay_construction_progress,
    service_mortgages,
)
from economy.energy import (
    infrastructure_is_worthwhile,
    invest_in_energy_plants,
    pay_infrastructure,
    trade_infrastructure,
)
from economy.innovation import research_effort, update_knowledge
from economy.intersector import pay_business_suppliers, settle_operating_inputs
from economy.records import MonthFlows
from economy.simulation import EconomySimulation
from economy.trades import TradeMarket, pick_hire
from economy.stock_market import distribute_dividends, update_prices_and_trade
from economy.wealth import (
    company_balance_sheet,
    company_net_worth,
    company_valuation_metrics,
    gini,
    household_net_worth,
)


_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    # Les tests vérifient les mécanismes depuis l'état initial des paramètres :
    # pas de période de chauffe, sauf dans ``WarmupTests``.
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


class EconomySimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulation = EconomySimulation(
            SimulationConfig(
                population_size=30,
                companies_per_sector=2,
                initial_company_cash=3000.0,
                initial_person_cash=200.0,
                raw_materials=500.0,
                land_units=35,
                seed=11,
            )
        )

    def test_month_advances_and_updates_all_markets(self) -> None:
        record = self.simulation.step()
        self.assertEqual(record.month, 1)
        self.assertGreaterEqual(record.food_price, 2.0)
        self.assertGreaterEqual(record.average_wage, 0.0)
        self.assertEqual(len(self.simulation.history), 1)
        self.assertEqual(len(self.simulation.company_rows()), 16)  # 8 secteurs x 2
        self.assertGreaterEqual(record.goods_price, 2.0)
        self.assertGreaterEqual(record.tax_revenue, 0.0)
        self.assertGreaterEqual(record.benefits_paid, 0.0)
        self.assertGreaterEqual(record.real_gdp, 0.0)
        self.assertGreaterEqual(record.nominal_gdp, 0.0)
        self.assertTrue(0.0 <= record.policy_rate <= 0.04)
        company_rows = self.simulation.company_rows()
        self.assertIn("Biens courants", [row["Secteur"] for row in company_rows])
        self.assertTrue(all("Unités vendues" in row for row in company_rows))

    def test_default_simulation_generates_positive_profit_over_first_year(self) -> None:
        simulation = EconomySimulation(SimulationConfig())
        records = [simulation.step() for _ in range(12)]
        first_year_profit = sum(record.total_profit for record in records)

        self.assertGreater(first_year_profit, 0.0)
        shareholders = [person for person in simulation.people if person.shares]
        self.assertGreaterEqual(len(shareholders), 40)
        self.assertTrue(
            any(person.dividend_income_this_turn > 0.0 for person in shareholders)
        )
        for person in simulation.people:
            history = person.financial_history[-1]
            self.assertAlmostEqual(
                history["Fortune réelle"],
                history["Fortune"] * 100.0 / records[-1].consumer_price_index,
            )

    def test_default_workforce_target_is_configurable_and_records_stock_prices(self) -> None:
        config = SimulationConfig(population_size=80, companies_per_sector=2)
        targets = workforce_targets(config)
        total_jobs = sum(targets[sector] * config.companies_per_sector for sector in targets)
        # 60 % de 80 ménages, à l'arrondi près (l'extraction impose un plancher
        # d'effectif, un peu plus haut depuis la TVA à 10 % au lieu de l'ancien 1,16)
        self.assertLessEqual(abs(total_jobs - 48), 4)
        simulation = EconomySimulation(config)
        record = simulation.step()
        private_jobs = record.employed_count - simulation.public.employees
        self.assertLessEqual(abs(private_jobs - total_jobs), 3)
        self.assertEqual(len(record.company_prices), len(simulation.companies))
        self.assertTrue(all(price > 0.0 for price in record.company_prices.values()))

    def test_market_price_adjustment_is_stable_at_supply_demand_balance(self) -> None:
        self.assertAlmostEqual(update_food_price(100.0, 100.0, 100.0), 100.0)
        self.assertGreater(update_food_price(100.0, 120.0, 100.0), 100.0)
        self.assertLess(update_food_price(100.0, 80.0, 100.0), 100.0)
        self.assertLess(update_food_price(100.0, 0.0, 0.0), 100.0)
        self.assertEqual(update_food_price(1.0, 0.0, 0.0), 1.0)

    def test_market_prices_follow_part_of_inflation_and_retain_temporary_shocks(self) -> None:
        baseline_price = update_food_price(
            100.0,
            100.0,
            100.0,
            baseline_growth_rate=0.001,
        )
        shocked_price = update_food_price(
            100.0,
            100.0,
            100.0,
            baseline_growth_rate=0.001,
            demand_multiplier=1.25,
        )
        price_after_shock = update_food_price(
            shocked_price,
            100.0,
            110.0,
            baseline_growth_rate=0.001,
        )

        self.assertAlmostEqual(baseline_price, 100.1)
        self.assertGreater(shocked_price, baseline_price)
        self.assertGreater(price_after_shock, baseline_price)
        self.assertLess(price_after_shock, shocked_price)

    def test_household_demand_is_limited_by_budget_not_a_fixed_quantity(self) -> None:
        simulation = EconomySimulation(SimulationConfig(population_size=1))
        household = simulation.people[0]
        household.cash = 5000.0
        household.income_this_turn = 1400.0
        household.expected_income = 1400.0  # budget sur le revenu attendu (R10)

        food_budget = min(
            household.cash,
            household.income_this_turn
            * simulation.config.food_income_budget_share
            + household.cash * simulation.config.food_cash_draw_rate,
        )
        goods_budget = min(
            household.cash,
            household.income_this_turn
            * simulation.config.goods_income_budget_share
            + household.cash * simulation.config.goods_cash_draw_rate,
        )
        tax_factor = 1.0 + simulation.config.vat_rate  # prix toutes taxes comprises
        expected_food_demand = (
            food_budget
            * household.consumption_propensity
            / (simulation.food_price * tax_factor)
        )
        expected_goods_demand = (
            goods_budget
            * household.consumption_propensity
            / (simulation.goods_price * tax_factor)
        )

        self.assertGreater(expected_food_demand, 2.0)
        self.assertGreater(expected_goods_demand, 1.5)
        self.assertAlmostEqual(simulation._food_demand(), expected_food_demand)
        self.assertAlmostEqual(simulation._goods_demand(), expected_goods_demand)

        goods_firm = Company(
            "goods", "Producteur de biens", "goods", 1000.0, 100.0,
            price=100.0, goods_inventory=100.0,
        )
        household = Person(
            "goods_buyer", "Acheteur", 5000.0, income_this_turn=1400.0, expected_income=1400.0
        )
        goods_sold, _, _ = sell_consumer_goods(
            [goods_firm],
            [household],
            market_price=100.0,
            config=SimulationConfig(
                goods_income_budget_share=1.0, goods_cash_draw_rate=0.0
            ),
        )
        self.assertAlmostEqual(goods_sold, 1400.0 / (100.0 * (1.0 + SimulationConfig().vat_rate)))

    def test_worker_skill_increases_effective_labor_and_food_capacity(self) -> None:
        company = Company(
            "food",
            "Producteur",
            "food",
            1000.0,
            100.0,
            employees=["worker"],
        )
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            food_productivity=10.0,
            food_input_cost=0.0,
            raw_material_unit_price=0.0,
        )

        low_skill = effective_worker_count(company, {"worker": 0.5})
        company.demand_forecast = 100.0  # prévision large : la capacité borne la production
        produce_food(
            [company], resources, config, worker_skills={"worker": 0.5}
        )
        low_output = company.inventory
        company.inventory = 0.0
        produce_food(
            [company], resources, config, worker_skills={"worker": 2.0}
        )

        self.assertEqual(low_skill, 0.5)
        self.assertEqual(low_output, 5.0)
        self.assertEqual(company.inventory, 20.0)

    def test_household_technology_increases_effective_labor(self) -> None:
        company = Company(
            "food", "Producteur", "food", 0.0, 100.0, employees=["worker"]
        )

        base = effective_worker_count(company, {"worker": (1.0, 0.0)})
        upgraded = effective_worker_count(company, {"worker": (1.0, 3.0)})

        self.assertAlmostEqual(base, 1.0)
        self.assertAlmostEqual(upgraded, 1.45)

    def test_competing_food_firms_share_sales_weighted_by_price(self) -> None:
        cheaper = Company(
            "cheap", "Producteur à bas prix", "food", 0.0, 100.0,
            price=90.0, inventory=10.0,
        )
        dearer = Company(
            "dear", "Producteur plus cher", "food", 0.0, 100.0,
            price=110.0, inventory=10.0,
        )
        households = [
            Person("p1", "Ménage 1", 1000.0),
            Person("p2", "Ménage 2", 1000.0),
        ]

        sold, revenue, _ = sell_food(
            [cheaper, dearer], households, market_price=100.0
        )

        # budget de 12 % de la trésorerie, au prix TTC (TVA de la configuration)
        self.assertAlmostEqual(sold, 2.0 * 120.0 / (100.0 * (1.0 + SimulationConfig().vat_rate)))
        self.assertGreater(cheaper.units_sold, dearer.units_sold)
        self.assertGreater(dearer.units_sold, 0.0)
        self.assertAlmostEqual(
            revenue,
            cheaper.units_sold * cheaper.price + dearer.units_sold * dearer.price,
        )

    def test_consumer_sales_never_exceed_household_cash(self) -> None:
        for sector, sell, inventory_field in (
            ("food", sell_food, "inventory"),
            ("goods", sell_consumer_goods, "goods_inventory"),
        ):
            with self.subTest(sector=sector):
                company = Company(
                    sector,
                    "Vendeur",
                    sector,
                    0.0,
                    100.0,
                    price=200.0,
                    **{inventory_field: 10.0},
                )
                household = Person("household", "Ménage", 100.0)

                sold, revenue, _ = sell(
                    [company], [household], market_price=1.0
                )

                # le ménage dépense toute sa trésorerie TTC ; l'entreprise reçoit le
                # hors taxe, la différence est la TVA due à l'État
                tax_factor = 1.0 + SimulationConfig().vat_rate
                self.assertAlmostEqual(revenue, 100.0 / tax_factor)
                self.assertAlmostEqual(company.cash, 100.0 / tax_factor)
                self.assertAlmostEqual(household.cash, 0.0)
                self.assertAlmostEqual(household.expenses_this_turn, revenue * tax_factor)
                self.assertAlmostEqual(sold, 0.5 / tax_factor)

    def test_households_favor_higher_quality_at_the_same_price(self) -> None:
        higher_quality = Company(
            "premium",
            "Qualité supérieure",
            "food",
            0.0,
            100.0,
            price=100.0,
            inventory=10.0,
            quality_trait=1.4,
        )
        basic_quality = Company(
            "basic",
            "Qualité standard",
            "food",
            0.0,
            100.0,
            price=100.0,
            inventory=10.0,
            quality_trait=0.8,
        )
        households = [
            Person(
                f"p{index}",
                f"Ménage {index}",
                1000.0,
                quality_preference=1.2,
            )
            for index in range(5)
        ]

        sell_food(
            [higher_quality, basic_quality],
            households,
            market_price=100.0,
            config=SimulationConfig(consumer_price_elasticity=1.5),
        )

        self.assertGreater(higher_quality.units_sold, basic_quality.units_sold)

    def test_unsold_goods_remain_in_inventory(self) -> None:
        firm = Company(
            "food",
            "Producteur",
            "food",
            10000.0,
            100.0,
            employees=["worker"],
        )
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        household = Person("p1", "Ménage", 1000.0)
        config = SimulationConfig(
            food_productivity=10.0,
            food_input_cost=0.0,
            raw_material_unit_price=0.0,
        )

        firm.demand_forecast = 20.0  # sa prévision dépasse sa capacité
        produced = produce_food([firm], resources, config)
        sold, _, _ = sell_food([firm], [household], 100.0, config)

        self.assertEqual(produced, 10.0)
        self.assertGreater(sold, 0.0)
        self.assertGreater(firm.inventory, 0.0)
        self.assertAlmostEqual(firm.units_produced, 10.0)

    def test_population_grows_gradually_and_adds_some_job_capacity(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                population_size=10,
                companies_per_sector=1,
                monthly_population_growth_rate=0.10,
                maximum_population_growth=0.20,
            )
        )
        original_job_targets = sum(
            company.target_employees for company in simulation.companies
        )

        simulation._grow_population()
        self.assertEqual(len(simulation.people), 11)
        self.assertEqual(simulation.new_households_this_turn, 1)

        simulation._grow_population()
        self.assertEqual(len(simulation.people), 12)
        self.assertGreater(
            sum(company.target_employees for company in simulation.companies),
            original_job_targets,
        )

    def test_technology_serves_firms_and_households_with_tiered_prices(self) -> None:
        buyer = Company(
            "buyer", "Acheteur", "food", 100000.0, 1000.0, profit=1000.0,
            previous_revenue=200000.0, revenue_ema=200000.0,
        )
        provider = Company(
            "provider",
            "Technologie",
            "technology",
            1000.0,
            1000.0,
            employees=["worker"],
        )
        household = Person(
            "p1",
            "Ménage",
            1000.0,
            income_this_turn=1000.0,
        )
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=100.0)
        config = SimulationConfig(
            technology_productivity=3.0,
            technology_knowledge_use_rate=0.01,
            technology_household_budget_share=0.05,
            technology_household_price_fraction=0.05,
            technology_household_supply_share=0.30,
        )

        units_sold = trade_technology(
            [buyer, provider], resources, config, [household]
        )

        self.assertGreater(units_sold, 0.0)
        self.assertGreater(buyer.tech_level, 0.0)
        self.assertGreater(household.technology_level, 0.0)
        self.assertGreater(resources.household_technology_revenue, 0.0)
        self.assertGreater(provider.revenue, resources.household_technology_revenue)
        self.assertLess(resources.knowledge, 100.0)

    def test_household_food_budget_is_configurable(self) -> None:
        firm = Company(
            "food", "Producteur", "food", 1000.0, 100.0,
            price=100.0, inventory=10.0,
        )
        household = Person(
            "p1", "Ménage 1", 500.0, income_this_turn=1000.0, expected_income=1000.0
        )

        sold_without_budget, _, _ = sell_food(
            [firm],
            [household],
            market_price=100.0,
            config=SimulationConfig(
                food_income_budget_share=0.0, food_cash_draw_rate=0.0
            ),
        )
        sold_with_budget, _, _ = sell_food(
            [firm],
            [household],
            market_price=100.0,
            config=SimulationConfig(
                food_income_budget_share=0.5, food_cash_draw_rate=0.0
            ),
        )

        self.assertEqual(sold_without_budget, 0.0)
        self.assertAlmostEqual(sold_with_budget, 500.0 / (100.0 * (1.0 + SimulationConfig().vat_rate)))

    def test_price_elasticity_shifts_sales_toward_cheaper_firm(self) -> None:
        def cheap_firm_sales(elasticity: float) -> float:
            firms = [
                Company("cheap", "Moins chère", "food", 0.0, 100.0, price=80.0, inventory=100.0),
                Company("dear", "Plus chère", "food", 0.0, 100.0, price=120.0, inventory=100.0),
            ]
            households = [
                Person(
                    f"p{index}",
                    f"Ménage {index}",
                    1000.0,
                    income_this_turn=1000.0,
                    expected_income=1000.0,
                )
                for index in range(5)
            ]
            sell_food(
                firms,
                households,
                market_price=100.0,
                config=SimulationConfig(
                    food_income_budget_share=1.0,
                    food_cash_draw_rate=0.0,
                    consumer_price_elasticity=elasticity,
                ),
            )
            return firms[0].units_sold

        self.assertAlmostEqual(
            cheap_firm_sales(0.0),
            5.0 * 1000.0 / (100.0 * (1.0 + SimulationConfig().vat_rate)) / 2.0,
        )
        self.assertGreater(
            cheap_firm_sales(3.0), cheap_firm_sales(0.0)
        )

    def test_consumer_price_index_uses_configured_market_weights(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                cpi_food_weight=0.5,
                cpi_goods_weight=0.3,
                cpi_rent_weight=0.2,
                cpi_services_weight=0.0,
            )
        )
        simulation.food_price *= 2.0
        simulation.goods_price *= 1.5
        simulation.rent *= 0.5

        self.assertAlmostEqual(simulation._consumer_price_index(), 155.0)
        # les services (prix indexé sur le salaire de référence) entrent avec leur poids
        simulation.live.cpi_services_weight = 0.25
        simulation.wage_reference = 2.0 * simulation.config.starting_wage
        self.assertAlmostEqual(
            simulation._consumer_price_index(), 100.0 * (1.55 + 0.25 * 2.0) / 1.25
        )

    def test_policy_rate_follows_a_taylor_rule(self) -> None:
        def rate_after(inflation_per_month: float, unemployment: float) -> float:
            simulation = EconomySimulation(
                SimulationConfig(seed=2, policy_rate_smoothing=1.0)
            )
            for _ in range(14):
                simulation.step()
            level = 100.0
            for record in simulation.history[-14:]:
                level *= 1.0 + inflation_per_month
                record.consumer_price_index = level
            simulation._adjust_policy_rate(inflation_per_month, unemployment)
            return simulation.policy_rate

        overheating = rate_after(0.01, 0.05)
        calm = rate_after(0.0, 0.05)
        slump = rate_after(0.0, 0.30)
        self.assertGreater(overheating, calm)  # inflation : le taux monte plus que 1 pour 1
        self.assertGreater(calm, slump)  # chômage élevé : le taux baisse
        self.assertGreaterEqual(slump, 0.0)
        cap = SimulationConfig().policy_rate_cap / 12.0
        self.assertLessEqual(rate_after(0.05, 0.0), cap + 1e-12)
    def test_wage_reference_does_not_follow_the_price_index(self) -> None:
        # Même économie, mais les prix sont gonflés de 50 % à la main : la
        # référence salariale ne doit pas réagir à l'IPC (pas d'indexation).
        def run(price_shock: float) -> float:
            simulation = EconomySimulation(SimulationConfig(seed=5))
            for _ in range(6):
                simulation.step()
            simulation.food_price *= price_shock
            simulation.goods_price *= price_shock
            simulation.price_index *= price_shock
            simulation.history[-1].consumer_price_index *= price_shock
            simulation._update_wage_reference()
            return simulation.wage_reference

        self.assertAlmostEqual(run(1.0), run(1.5), places=9)

    def test_average_wage_grows_in_nominal_terms_over_the_long_run(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7))
        records = [simulation.step() for _ in range(120)]
        year2 = sum(r.average_wage for r in records[12:24]) / 12
        last_year = sum(r.average_wage for r in records[-12:]) / 12
        # croissance nominale : les salaires progressent sur la décennie
        # (l'année 1 est un régime transitoire de mise en place des postes)
        self.assertGreater(last_year, year2 * 1.05)

    def test_unemployment_benefits_are_shared_within_public_budget(self) -> None:
        people = [
            Person("p1", "Ménage 1", 0.0),
            Person("p2", "Ménage 2", 0.0),
        ]
        per_person = unemployment_benefit_entitlement(100.0, 100.0, 0.5, 1.0)
        paid = pay_unemployment_benefits(people, per_person, funds=150.0)

        self.assertAlmostEqual(paid, 150.0)
        self.assertAlmostEqual(people[0].income_this_turn, 75.0)
        self.assertAlmostEqual(people[1].income_this_turn, 75.0)

    def test_progressive_income_tax_only_applies_higher_rate_above_threshold(self) -> None:
        people = [
            Person("p1", "Sous le seuil", 5000.0, income_this_turn=2000.0),
            Person("p2", "Au-dessus du seuil", 5000.0, income_this_turn=4000.0),
        ]

        # barème du service des Finances (État institutionnel, créé au premier mois)
        simulation = EconomySimulation(SimulationConfig(population_size=5))
        simulation.step()
        live = simulation.live
        live.income_tax_rate, live.income_tax_threshold, live.top_marginal_income_tax_rate = 0.10, 3000.0, 0.30
        simulation.people = people
        taxes = simulation._collect_assessed_taxes(1.0)  # recouvrement complet

        # barème de novembre 2026 : au-delà du seuil, taux de base + surtaxe (10 % + 30 %) ;
        # auparavant max(10 %, 30 %) = 30 %, soit 600 et 800
        self.assertAlmostEqual(taxes, 900.0)
        self.assertAlmostEqual(people[0].taxes_paid, 200.0)
        self.assertAlmostEqual(people[1].taxes_paid, 700.0)

    def test_orphan_fund_is_redistributed_to_households(self) -> None:
        # Une dépense restée sans fournisseur actif ne dort pas : elle est reversée
        people = [Person(f"p{index}", f"Ménage {index}", 0.0) for index in range(4)]
        resources = Resources(
            raw_materials=0.0, land_units=0, knowledge=0.0, market_cash=8000.0
        )
        paid = redistribute_orphan_fund(people, resources)
        self.assertAlmostEqual(paid, 8000.0)
        self.assertAlmostEqual(resources.market_cash, 0.0)
        self.assertAlmostEqual(sum(p.cash for p in people), 8000.0)  # rien de créé

    def test_services_are_real_firms_that_earn_business_spending(self) -> None:
        buyer = Company("b", "Acheteur", "food", 10000.0, 10.0)
        services = Company("s", "Services", "services", 0.0, 10.0, employees=["w"])
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        pay_business_suppliers(buyer, 1000.0, {"services": 1.0}, [buyer, services], resources)
        self.assertAlmostEqual(services.revenue, 1000.0)
        self.assertAlmostEqual(resources.market_cash, 0.0)  # plus de fonds amont agrégé

    def test_retraining_fees_are_paid_to_training_services(self) -> None:
        config = SimulationConfig(
            retraining_start_month=0, retraining_probability=1.0,
            retraining_min_unemployment=0.0,
        )
        market = TradeMarket(config, random.Random(1))
        tech = Company("t", "Tech", "technology", 1e5, 100.0, target_employees=40)
        school = Company("s", "Formation", "services", 0.0, 10.0, employees=["w"])
        worker = Person("w", "Chômeur", 5000.0, trade="commercial")
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        market.refresh([worker], [tech])
        market.retrain([worker], resources, 1000.0, [tech, school])
        self.assertAlmostEqual(school.revenue, 1000.0)
        self.assertAlmostEqual(worker.cash, 4000.0)
    def test_rent_is_capped_by_tenant_income(self) -> None:
        landlord = Company(
            "landlord", "Bailleur", "real_estate", 1000.0, 100.0,
            property_inventory=1,
        )
        tenant = Person(
            "tenant", "Locataire", 1000.0, income_this_turn=800.0
        )

        revenue = allocate_housing_and_collect_rent(
            [landlord], [tenant], monthly_rent=700.0, rent_income_share=0.35
        )

        self.assertAlmostEqual(revenue, 280.0)
        self.assertAlmostEqual(tenant.cash, 720.0)
        self.assertAlmostEqual(landlord.cash, 1280.0)

    def test_housing_maintenance_counts_owned_units_once(self) -> None:
        landlord = Company(
            "landlord", "Bailleur", "real_estate", 10000.0, 100.0,
            property_inventory=2,
        )
        tenant = Person(
            "tenant", "Locataire", 1000.0, rental_owner_id=landlord.id
        )
        # l'entretien est servi par des artisans et une carrière qui emploient
        crew = Company("services_1", "Artisans", "services", 0.0, 10.0, employees=["w"])
        quarry = Company("materials_1", "Carrière", "materials", 0.0, 10.0, employees=["m"])
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            housing_price=120000.0, housing_maintenance_rate=0.001
        )

        paid = maintain_housing([landlord, crew, quarry], [tenant], resources, config)

        self.assertAlmostEqual(paid, 240.0)
        self.assertAlmostEqual(landlord.cash, 9760.0)

    def test_mortgage_purchase_respects_bank_reserves_and_is_serviced(self) -> None:
        seller = Company(
            "builder",
            "Constructeur",
            "real_estate",
            10000.0,
            1000.0,
            property_inventory=1,
        )
        household = Person(
            "buyer",
            "Acheteur",
            75000.0,
            income_this_turn=4000.0,
        )
        config = SimulationConfig(
            housing_price=120000.0,
            mortgage_down_payment_rate=0.20,
            mortgage_payment_income_share=0.40,
            mortgage_interest_rate=0.004,
            mortgage_term_months=360,
        )

        purchases, loans = buy_homes(
            [seller],
            [household],
            config.housing_price,
            config.housing_build_cost,
            config,
            bank_reserves=50000.0,
        )

        self.assertEqual(purchases, 1)
        self.assertAlmostEqual(loans, 50000.0)
        self.assertTrue(household.owns_home)
        self.assertAlmostEqual(household.cash, 5000.0)
        self.assertAlmostEqual(seller.cash, 130000.0)
        previous_debt = household.mortgage_debt
        self.assertGreater(service_mortgages([household]), 0.0)
        self.assertLess(household.mortgage_debt, previous_debt)
        self.assertAlmostEqual(
            household_net_worth(household, [], config),
            household.cash + config.housing_price - household.mortgage_debt,
        )

        simulation = EconomySimulation(config)
        simulation.people[0].mortgage_debt = household.mortgage_debt
        summary = simulation.resource_summary()
        self.assertAlmostEqual(summary["Prêts immobiliers"], household.mortgage_debt)
        self.assertAlmostEqual(
            summary["Encours des prêts bancaires"],
            summary["Prêts immobiliers"] + summary["Dettes des entreprises"],
        )

    def test_inventory_holding_cost_is_charged_on_input_value(self) -> None:
        company = Company(
            "stocked", "Entreprise avec stocks", "food", 10000.0, 100.0,
            inventory=10.0,
        )
        # entrepôts loués à une entreprise immobilière qui emploie
        warehouse = Company("real_estate_1", "Entrepôts", "real_estate", 0.0, 10.0, employees=["w"])
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            food_input_cost=20.0, inventory_holding_cost_rate=0.01
        )

        paid = charge_inventory_holding_cost([company, warehouse], resources, config)

        self.assertAlmostEqual(paid, 2.0)
        self.assertAlmostEqual(company.cash, 9998.0)
        self.assertAlmostEqual(warehouse.cash, 2.0)

    def test_dormant_firm_with_cash_can_restart_with_one_employee(self) -> None:
        company = Company(
            "dormant", "Entreprise dormante", "food", 10000.0, 1000.0,
            target_employees=3, age_months=5, previous_profit=-100.0,
        )
        person = Person("p1", "Ménage 1", 1000.0)

        allocate_and_pay_labor(
            [company], [person], starting_wage=1000.0,
            config=SimulationConfig(),
        )

        self.assertEqual(company.employees, ["p1"])
        self.assertEqual(person.employer_id, company.id)

    def test_worker_stays_when_competing_offer_misses_mobility_premium(self) -> None:
        incumbent_firm = Company(
            "incumbent", "Employeur", "food", 10000.0, 1000.0,
            employees=["p1"], target_employees=1, wage_bill=1000.0,
            previous_revenue=2000.0, previous_profit=500.0,
        )
        competing_firm = Company(
            "competitor", "Concurrent", "goods", 10000.0, 1050.0,
            target_employees=1,
        )
        person = Person(
            "p1", "Salarié", 1000.0, employer_id=incumbent_firm.id,
            current_wage=1000.0,
        )

        allocate_and_pay_labor(
            [incumbent_firm, competing_firm], [person], starting_wage=1000.0,
            config=SimulationConfig(labor_mobility_premium=0.1),
        )

        self.assertEqual(person.employer_id, incumbent_firm.id)

    def test_loss_making_company_reduces_payroll_to_realized_revenue(self) -> None:
        company = Company(
            "idle",
            "Entreprise sans ventes",
            "food",
            100000.0,
            1000.0,
            employees=["p1", "p2", "p3", "p4"],
            target_employees=4,
            previous_revenue=0.0,
            wage_bill=4000.0,
            previous_profit=-4000.0,
        )
        people = [Person(f"p{i}", f"Ménage {i}", 1000.0) for i in range(1, 5)]
        allocate_and_pay_labor(
            [company], people, starting_wage=1000.0, config=SimulationConfig()
        )
        self.assertEqual(company.employees, [])
        self.assertEqual(company.wage_bill, 0.0)

    def test_profitable_company_can_rebuild_its_workforce_gradually(self) -> None:
        company = Company(
            "growing",
            "Entreprise rentable",
            "food",
            100000.0,
            1000.0,
            employees=["p1"],
            target_employees=3,
            previous_revenue=2000.0,
            wage_bill=1000.0,
            previous_profit=500.0,
        )
        people = [
            Person(
                f"p{i}",
                f"Ménage {i}",
                1000.0,
                employer_id=company.id if i == 1 else None,
                current_wage=1000.0 if i == 1 else 0.0,
            )
            for i in range(1, 4)
        ]
        # recrutement borné par ``max_monthly_hiring`` (1 poste/mois : 2 salariés)
        allocate_and_pay_labor(
            [company], people, starting_wage=1000.0,
            config=SimulationConfig(max_monthly_hiring=1),
        )
        self.assertEqual(len(company.employees), 2)

    def test_hiring_limit_is_configurable_and_no_longer_one_post_per_month(self) -> None:
        def employees_after(limit: int) -> int:
            company = Company(
                "growing", "Entreprise rentable", "food", 100000.0, 1000.0,
                employees=["p1"], target_employees=6,
                previous_revenue=2000.0, wage_bill=1000.0, previous_profit=500.0,
            )
            people = [
                Person(
                    f"p{i}", f"Ménage {i}", 1000.0,
                    employer_id=company.id if i == 1 else None,
                    current_wage=1000.0 if i == 1 else 0.0,
                )
                for i in range(1, 8)
            ]
            allocate_and_pay_labor(
                [company], people, starting_wage=1000.0,
                config=SimulationConfig(max_monthly_hiring=limit),
            )
            return len(company.employees)

        self.assertEqual(employees_after(1), 2)
        self.assertEqual(employees_after(3), 4)  # +3 par mois, plus +1 seulement

    def test_company_balance_sheet_reconciles_assets_liabilities_and_equity(self) -> None:
        config = SimulationConfig()
        company = Company(
            "firm",
            "Entreprise",
            "food",
            100.0,
            10.0,
            inventory=2.0,
            goods_inventory=3.0,
            property_inventory=1,
            construction_in_progress=20.0,
            tech_inventory=2.0,
            tech_level=0.03,
            debt=50.0,
        )
        balance_sheet = company_balance_sheet(company, [company], config)
        expected_assets = (
            100.0
            + 2.0 * config.food_input_cost
            + 3.0 * config.goods_input_cost
            + config.housing_price
            + 20.0
            + 2.0 * config.tech_unit_price
            + config.tech_unit_price
        )
        self.assertAlmostEqual(balance_sheet["Total des actifs"], expected_assets)
        self.assertAlmostEqual(balance_sheet["Total des passifs"], 50.0)
        self.assertAlmostEqual(
            balance_sheet["Capitaux propres"],
            balance_sheet["Total des actifs"] - balance_sheet["Total des passifs"],
        )
        self.assertAlmostEqual(company_net_worth(company, [company], config), expected_assets - 50.0)

    def test_stock_price_converges_to_fundamentals_without_automatic_growth(self) -> None:
        config = SimulationConfig(stock_book_value_weight=1.0, stock_adjustment_speed=0.2)
        company = Company(
            "firm",
            "Entreprise",
            "food",
            100000.0,
            10.0,
            stock_price=100.0,
            previous_stock_price=100.0,
            treasury_shares=0.0,
        )
        update_prices_and_trade([company], [], config=config)
        self.assertAlmostEqual(company.stock_price, 100.0)

        company.stock_price = 150.0
        update_prices_and_trade([company], [], config=config)
        self.assertLess(company.stock_price, 150.0)

    def test_growth_and_recession_scenarios_move_prices_in_explainable_directions(self) -> None:
        config = SimulationConfig(stock_book_value_weight=0.2)
        growing = Company(
            "growing",
            "En croissance",
            "goods",
            100000.0,
            10.0,
            stock_price=50.0,
            profit=1000.0,
            previous_profit=500.0,
            revenue=6000.0,
            previous_revenue=5000.0,
            treasury_shares=0.0,
        )
        declining = Company(
            "declining",
            "En récession",
            "goods",
            100000.0,
            10.0,
            stock_price=120.0,
            profit=-500.0,
            previous_profit=1000.0,
            revenue=5000.0,
            previous_revenue=10000.0,
            treasury_shares=0.0,
        )
        update_prices_and_trade([growing, declining], [], config=config)
        self.assertGreater(growing.stock_price, 50.0)
        self.assertLess(declining.stock_price, 120.0)

    def test_valuation_ratios_reflect_company_finances(self) -> None:
        config = SimulationConfig()
        company = Company(
            "firm",
            "Entreprise",
            "food",
            100000.0,
            10.0,
            stock_price=100.0,
            profit=1000.0,
            revenue=5000.0,
            debt=20000.0,
            dividend_paid=100.0,
            shares_outstanding=1000.0,
        )
        metrics = company_valuation_metrics(company, [company], config)
        self.assertAlmostEqual(metrics["PER"], 100000.0 / 12000.0)
        self.assertAlmostEqual(metrics["Rendement du dividende"], 0.012)
        self.assertAlmostEqual(metrics["Marge nette"], 0.2)
        self.assertAlmostEqual(metrics["Dette / actifs"], 0.2)

    def test_materials_company_supplies_food_producer_and_receives_payment(self) -> None:
        config = SimulationConfig(
            food_productivity=20.0,
            food_input_cost=20.0,
            raw_material_unit_price=8.0,
            raw_material_extraction_cost=2.0,
            raw_material_extraction_productivity=10.0,
            reserve_replacement_rate=0.0,  # l'exploration est testée séparément
            input_cost_split={"services": 1.0},  # isole le paiement des matières premières
            target_inventory_months=1.0,
        )
        producer = Company("food", "Producteur alimentaire", "food", 1000.0, 10.0, employees=["worker"])
        supplier = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["miner"])
        crew = Company("services", "Prestataire", "services", 0.0, 10.0, employees=["s"])
        resources = Resources(
            raw_materials=20.0,
            land_units=0,
            knowledge=0.0,
            raw_material_price=8.0,
        )
        producer.demand_forecast = 10.0  # production voulue : sa prévision (stock visé d'un mois)
        produced = produce_food([producer, supplier], resources, config)
        self.assertAlmostEqual(produced, 10.0)
        self.assertAlmostEqual(producer.supplier_spending[supplier.id], 80.0)
        self.assertAlmostEqual(supplier.customer_revenue[producer.id], 80.0)
        self.assertAlmostEqual(supplier.expenses, 0.0)  # aucun fournisseur de services ne livre
        self.assertAlmostEqual(producer.cash + supplier.cash + resources.market_cash, 2000.0)

    def test_recycled_materials_can_supply_production_without_an_extractor(self) -> None:
        config = SimulationConfig(
            food_productivity=10.0,
            food_input_cost=20.0,
            raw_material_unit_price=8.0,
            raw_material_extraction_productivity=10.0,
            input_cost_split={"services": 1.0},  # isole les matières recyclées
            target_inventory_months=1.0,
        )
        producer = Company(
            "food", "Producteur alimentaire", "food", 1000.0, 10.0,
            employees=["worker"],
        )
        idle_supplier = Company(
            "materials", "Extracteur inactif", "materials", 1000.0, 10.0
        )
        crew = Company("services", "Prestataire", "services", 0.0, 10.0, employees=["s"])
        resources = Resources(
            raw_materials=0.0,
            land_units=0,
            knowledge=0.0,
            raw_material_price=8.0,
            recycled_materials=10.0,
        )

        producer.demand_forecast = 10.0  # production voulue : sa prévision (stock visé d'un mois)
        produced = produce_food([producer, idle_supplier], resources, config)

        self.assertAlmostEqual(produced, 10.0)
        self.assertAlmostEqual(resources.raw_materials, 0.0)
        self.assertAlmostEqual(resources.recycled_materials, 0.0)
        self.assertAlmostEqual(producer.expenses, 0.0)  # recyclage disponible, aucun fournisseur employeur
        self.assertAlmostEqual(idle_supplier.revenue, 0.0)
        self.assertAlmostEqual(resources.market_cash, 0.0)
        self.assertAlmostEqual(
            producer.cash + idle_supplier.cash + resources.market_cash, 2000.0
        )

    def test_recycling_is_delayed_and_limited_to_sold_materials(self) -> None:
        config = SimulationConfig(material_recovery_rate=0.5)
        food_firm = Company(
            "food", "Producteur", "food", 1000.0, 10.0, units_sold=4.0
        )
        goods_firm = Company(
            "goods", "Fabricant", "goods", 1000.0, 10.0, units_sold=2.0
        )
        materials_firm = Company(
            "materials", "Extracteur", "materials", 1000.0, 10.0, units_sold=100.0
        )
        resources = Resources(
            raw_materials=10.0,
            land_units=0,
            knowledge=0.0,
            recycled_materials=3.0,
        )

        recovered = collect_recyclable_materials(
            [food_firm, goods_firm, materials_firm], resources, config
        )

        self.assertAlmostEqual(recovered, 3.5)
        self.assertAlmostEqual(resources.recycling_pending, 3.5)
        self.assertAlmostEqual(resources.recycled_materials, 3.0)

    def test_pending_recycled_materials_become_available_next_month(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                population_size=5,  # l'État institutionnel nomme cinq membres du gouvernement
                companies_per_sector=1,
                initial_company_cash=0.0,
                initial_person_cash=0.0,
                raw_materials=0.0,
                bank_initial_reserves=0.0,
                material_recovery_rate=0.0,
            )
        )
        simulation.resources.recycling_pending = 5.0

        simulation.step()

        self.assertAlmostEqual(simulation.resources.recycled_materials, 5.0)
        self.assertAlmostEqual(simulation.resources.recycling_pending, 0.0)

    def test_recycling_rate_is_bounded(self) -> None:
        self.assertEqual(SimulationConfig(material_recovery_rate=-0.2).material_recovery_rate, 0.0)
        self.assertEqual(SimulationConfig(material_recovery_rate=1.2).material_recovery_rate, 1.0)

    def test_consumer_spending_matches_company_revenue(self) -> None:
        company = Company(
            "food",
            "Producteur",
            "food",
            1000.0,
            10.0,
            price=12.0,
            inventory=10.0,
        )
        households = [
            Person("p1", "Ménage 1", 100.0),
            Person("p2", "Ménage 2", 100.0),
        ]
        _, revenue, _ = sell_food([company], households, market_price=10.0)
        # dépense des ménages = chiffre d'affaires hors taxe + TVA
        self.assertAlmostEqual(revenue, company.revenue)
        self.assertAlmostEqual(
            sum(person.expenses_this_turn for person in households),
            company.revenue * (1.0 + SimulationConfig().vat_rate),
        )

    def test_inactive_company_does_not_regain_a_stock_price_or_trade(self) -> None:
        inactive = Company(
            "closed", "Fermée", "food", 0.0, 0.0, profit=1000.0,
            stock_price=0.0, treasury_shares=0.0, active=False,
        )
        self.assertEqual(update_prices_and_trade([inactive], []), 0.0)
        self.assertEqual(inactive.stock_price, 0.0)

    def test_technology_purchase_is_capitalized_and_amortized(self) -> None:
        config = SimulationConfig()
        buyer = Company(
            "buyer", "Acheteur", "food", 100000.0, 1000.0, profit=1000.0,
            previous_revenue=200000.0, revenue_ema=200000.0,
        )
        provider = Company(
            "provider", "Technologie", "technology", 1000.0, 1000.0,
            employees=["worker"],
        )
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=10.0)
        initial_wealth = company_net_worth(buyer, [buyer, provider], config)
        trade_technology([buyer, provider], resources, config)
        self.assertEqual(buyer.expenses, 0.0)
        self.assertAlmostEqual(
            company_net_worth(buyer, [buyer, provider], config), initial_wealth
        )
        self.assertEqual(provider.customer_revenue[buyer.id], buyer.capital_expenditure)
        self.assertEqual(buyer.supplier_spending[provider.id], buyer.capital_expenditure)

    def test_operating_credit_only_funds_housing_when_a_shortage_exists(self) -> None:
        property_firm = Company(
            "builder", "Bâtisseur", "real_estate", 0.0, 1000.0,
            property_inventory=1,
        )
        config = SimulationConfig()
        loan_without_shortage = request_operating_credit(
            [property_firm], [], 1000000.0, config
        )
        self.assertEqual(loan_without_shortage, 0.0)
        loan_for_shortage = request_operating_credit(
            [property_firm], [], 1000000.0, config, housing_shortage=1
        )
        self.assertGreaterEqual(loan_for_shortage, config.housing_build_cost)

    def test_resources_are_finite_and_non_negative(self) -> None:
        for _ in range(20):
            self.simulation.step()
        self.assertGreaterEqual(self.simulation.resources.raw_materials, 0.0)
        self.assertGreaterEqual(self.simulation.resources.land_units, 0)
        self.assertGreaterEqual(self.simulation.resources.knowledge, 0.0)
        self.assertTrue(all(person.cash >= -0.000001 for person in self.simulation.people))

    def test_raw_material_shortage_raises_its_market_price(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(raw_materials=0.0, seed=23)
        )
        initial_price = simulation.resources.raw_material_price
        producer = next(c for c in simulation.companies if c.sector == "food")
        producer.production_capacity = 1000.0
        simulation._update_raw_material_price(1000.0, 1000.0)
        self.assertEqual(simulation.resources.raw_materials, 0.0)
        self.assertGreater(simulation.resources.raw_material_price, initial_price)
        self.assertGreater(simulation._raw_shortfall, 0.0)

    def test_stock_holdings_match_issued_shares(self) -> None:
        for _ in range(5):
            self.simulation.step()
        for company in self.simulation.companies:
            held = sum(person.shares.get(company.id, 0.0) for person in self.simulation.people)
            held += sum(other.shares.get(company.id, 0.0) for other in self.simulation.companies)
            # l'État est actionnaire (participations, ou reprise d'un dernier acteur restructuré)
            held += self.simulation.public.account.shares.get(company.id, 0.0)
            self.assertAlmostEqual(held + company.treasury_shares, company.shares_outstanding, places=5)

    def test_shareholders_receive_dividends(self) -> None:
        company = Company("c1", "Entreprise", "food", 1000.0, 10.0, profit=100.0)
        shareholder = Person("p1", "Investisseur", 50.0, shares={"c1": 100.0})
        paid = distribute_dividends([company], [shareholder])
        self.assertAlmostEqual(paid, 7.5)
        self.assertAlmostEqual(shareholder.cash, 57.5)
        self.assertAlmostEqual(shareholder.income_this_turn, 7.5)
        self.assertAlmostEqual(shareholder.dividend_income_this_turn, 7.5)
        self.assertAlmostEqual(shareholder.lifetime_earnings, 7.5)
        self.assertAlmostEqual(company.cash, 992.5)

    def test_secondary_market_transfers_shares_between_households(self) -> None:
        company = Company(
            "c1", "Entreprise", "food", 1000.0, 10.0,
            profit=100.0, stock_price=10.0, treasury_shares=0.0,
        )
        seller = Person("p1", "Vendeur", 0.0, shares={"c1": 100.0})
        buyer = Person("p2", "Acheteur", 2000.0)
        volume = update_prices_and_trade([company], [seller, buyer])
        self.assertGreater(volume, 0.0)
        self.assertGreater(buyer.shares.get("c1", 0.0), 0.0)
        self.assertLess(seller.shares["c1"], 100.0)

    def test_household_wealth_and_gini_track_assets_and_concentration(self) -> None:
        company = self.simulation.companies[0]
        wealthy = Person("rich", "Riche", 1000.0, shares={company.id: 20.0}, owns_home=True)
        poorer = Person("poor", "Modeste", 1000.0)
        self.assertGreater(household_net_worth(wealthy, [company], self.simulation.config), 1000.0)
        self.assertEqual(gini([100.0, 100.0]), 0.0)
        self.assertGreater(gini([0.0, 100.0]), 0.0)
        self.assertLessEqual(gini([wealthy.cash, poorer.cash]), 1.0)

    def test_ceo_risk_changes_access_to_operating_credit(self) -> None:
        config = SimulationConfig(starting_wage=1000.0, maximum_loan_leverage=3.0)
        low_risk = Person("low", "Prudent", 0.0, risk_tolerance=0.1)
        high_risk = Person("high", "Audacieux", 0.0, risk_tolerance=0.9)
        low_firm = Company(
            "low-firm", "Prudente", "food", 0.0, 1000.0,
            target_employees=5, executive_id="low", property_inventory=1,
        )
        high_firm = Company(
            "high-firm", "Audacieuse", "food", 0.0, 1000.0,
            target_employees=5, executive_id="high", property_inventory=1,
        )
        low_loan = request_operating_credit([low_firm], [low_risk], 100000.0, config)
        high_loan = request_operating_credit([high_firm], [high_risk], 100000.0, config)
        self.assertGreater(high_loan, low_loan)
        self.assertGreater(low_firm.debt, 0.0)
        self.assertGreater(high_firm.debt, 0.0)

    def test_housing_construction_takes_configured_six_months(self) -> None:
        from economy.deliveries import prepare_delivery_month

        config = SimulationConfig(
            build_time_months=6, housing_build_cost=100.0, starting_wage=10.0
        )
        firm = Company("builder", "Bâtisseur", "real_estate", 500.0, 10.0)
        crew = Company("services_1", "Artisans", "services", 0.0, 10.0, employees=["w"])
        quarry = Company("materials_1", "Carrière", "materials", 0.0, 10.0, employees=["m"])
        land = Resources(raw_materials=1000.0, land_units=1, knowledge=0.0)
        companies = [firm, crew, quarry]
        firm.housing_gap = 1  # sa prévision demande un logement
        self.assertEqual(build_housing([firm], land, month=3, config=config), 1)
        self.assertEqual(firm.property_inventory, 0)
        for month in range(4, 10):  # six mois de travaux livrés et payés
            prepare_delivery_month(companies, land, config)
            pay_construction_progress(companies, land, config)
            delivered = complete_construction(companies, month=month, config=config)
            self.assertEqual(delivered, 1 if month == 9 else 0)
        self.assertEqual(firm.property_inventory, 1)

    def test_unpaid_construction_is_never_delivered(self) -> None:
        # régression (audit P1) : la date d'échéance ne suffit pas, il faut le travail
        config = SimulationConfig(build_time_months=6, housing_build_cost=100.0, starting_wage=10.0)
        firm = Company("builder", "Bâtisseur", "real_estate", 500.0, 10.0)
        land = Resources(raw_materials=0.0, land_units=1, knowledge=0.0)
        build_housing([firm], land, month=0, config=config)
        firm.cash = 0.0  # plus de quoi payer les travaux
        for month in range(1, 13):
            pay_construction_progress([firm], land, config)
            self.assertEqual(complete_construction([firm], month=month, config=config), 0)
        self.assertEqual(firm.property_inventory, 0)

    def test_household_startup_reserves_founder_equity(self) -> None:
        config = SimulationConfig(
            startup_threshold=10000.0,
            company_founder_probability=1.0,
            startup_investment_fraction=0.5,
        )
        founder = Person(
            "founder", "Entrepreneur", 20000.0, skill=1.4, entrepreneurial_spirit=0.9
        )
        startups, _, count = create_household_startups(
            [], [founder], 1, 100000.0, config, random.Random(1)
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(startups), 1)
        self.assertEqual(founder.cash, 10000.0)
        self.assertAlmostEqual(founder.shares[startups[0].id], 750.0)
        self.assertAlmostEqual(startups[0].treasury_shares, 250.0)
        self.assertAlmostEqual(
            founder.shares[startups[0].id] + startups[0].treasury_shares,
            startups[0].shares_outstanding,
        )

    def test_company_wages_reflect_employee_roles(self) -> None:
        company = Company(
            "firm",
            "Entreprise",
            "goods",
            10000.0,
            100.0,
            target_employees=2,
            executive_id="ceo",
        )
        ceo = Person("ceo", "Dirigeant", 0.0, skill=1.5)
        manager = Person("manager", "Manager", 0.0, skill=1.4)
        config = SimulationConfig(
            executive_wage_multiplier=1.35,
            manager_wage_multiplier=1.15,
            skill_wage_slope=0.0,  # isole les multiplicateurs de poste
            executive_full_pay_headcount=1.0,  # pas de décote de taille ici
            trade_base_wage={trade: 1.0 for trade in SimulationConfig().trades},
        )

        allocate_and_pay_labor([company], [ceo, manager], 100.0, config)

        self.assertEqual(ceo.employment_role, "Direction")
        self.assertEqual(manager.employment_role, "Management")
        self.assertAlmostEqual(ceo.current_wage, 135.0)
        self.assertAlmostEqual(manager.current_wage, 115.0)

    def test_bank_companies_track_loans_and_can_fail(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                population_size=30,
                companies_per_sector=1,
                initial_company_cash=0.0,
                bank_initial_reserves=500000.0,
                seed=2,
            )
        )
        simulation.step()
        banks = [
            company
            for company in simulation.companies
            if company.sector == "bank" and company.active
        ]
        borrower_debt = sum(
            company.debt
            for company in simulation.companies
            if company.active and company.sector != "bank"
        ) + sum(person.mortgage_debt for person in simulation.people)
        loan_book = sum(
            sum(bank.bank_loan_accounts.values()) for bank in banks
        )
        self.assertGreater(borrower_debt, 0.0)
        self.assertAlmostEqual(loan_book, borrower_debt)
        self.assertGreater(
            company_balance_sheet(banks[0], simulation.companies, simulation.config)[
                "Prêts aux ménages et entreprises"
            ],
            0.0,
        )

        failed_bank, surviving_bank = banks[0], banks[0]
        if len(banks) == 1:
            simulation.companies.append(
                Company("bank_2", "Banque de secours", "bank", 0.0, 100.0)
            )
            surviving_bank = simulation.companies[-1]
        else:
            surviving_bank = banks[1]
        defaulted_borrower = Company(
            "defaulted_borrower", "Défaillante", "goods", 0.0, 100.0, active=False,
            default_loss=100.0,
        )
        simulation.companies.append(defaulted_borrower)
        failed_bank.bank_equity = 50.0
        failed_bank.bank_loan_accounts[defaulted_borrower.id] = 100.0

        simulation._write_off_defaulted_bank_loans()

        self.assertFalse(failed_bank.active)
        self.assertTrue(surviving_bank.active)
        self.assertNotIn(defaulted_borrower.id, failed_bank.bank_loan_accounts)

    def test_company_portfolio_counts_for_solvency_and_creditors_come_first(self) -> None:
        company = Company(
            "distressed",
            "En difficulté",
            "goods",
            0.0,
            10.0,
            debt=100.0,
            shares_outstanding=1000.0,
            treasury_shares=0.0,
            shares={"target": 20.0},
        )
        target = Company(
            "target",
            "Cible",
            "technology",
            1000.0,
            10.0,
            shares_outstanding=1000.0,
            treasury_shares=980.0,
        )
        owner = Person(
            "owner",
            "Actionnaire",
            0.0,
            shares={"distressed": 1000.0},
        )
        rival = Company("rival", "Concurrent sain", "goods", 1000.0, 10.0)
        # Modifié en phase 2 de l'autonomie : le portefeuille compte dans la solvabilité,
        # et les créanciers passent avant les actionnaires. 20 titres à 20 € couvrent la
        # dette de 100 € : plus de faillite.
        _, defaults = resolve_distressed_companies(
            [company, target, rival], [owner], 500.0, SimulationConfig(), random.Random(1)
        )
        self.assertEqual(defaults, 0)
        self.assertTrue(company.active)
        # à 1 € le titre, le portefeuille (20 €) ne couvre plus la dette : faillite, et
        # les créanciers non remboursés font revenir les titres chez leur émetteur
        target.stock_price = 1.0
        reserves, defaults = resolve_distressed_companies(
            [company, target, rival], [owner], 500.0, SimulationConfig(), random.Random(1)
        )
        self.assertEqual(defaults, 1)
        self.assertEqual(reserves, 500.0)
        self.assertNotIn("target", owner.shares)
        self.assertEqual(target.treasury_shares, 1000.0)
        self.assertFalse(company.active)

    # OPT-BANK-001 : titres publics d'une banque fermée après résolution, défaut conservé (aucune
    # annulation ajoutée). Sa date dépend de la trajectoire : absent après le lot 3 de novembre
    # 2026, il réapparaît avec les leviers de l'État (docs/AMELIORATIONS_2026-11.md)
    @unittest.expectedFailure
    def test_long_run_wealth_debt_and_market_values_remain_bounded(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=1))
        for _ in range(240):
            simulation.step()
            # comptabilité en partie double : aucune monnaie n'apparaît hors banques
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            for company in simulation.companies:
                shares_held = sum(
                    person.shares.get(company.id, 0.0)
                    for person in simulation.people
                ) + sum(
                    owner.shares.get(company.id, 0.0)
                    for owner in simulation.companies
                ) + simulation.public.account.shares.get(company.id, 0.0)  # participations de l'État
                self.assertAlmostEqual(
                    shares_held + company.treasury_shares,
                    company.shares_outstanding,
                    places=5,
                )
        self.assertEqual(simulation.month, 240)
        self.assertTrue(all(person.cash >= 0.0 for person in simulation.people))
        self.assertTrue(all(company.cash >= 0.0 and company.debt >= 0.0 for company in simulation.companies))
        # plus de plafond de 5000 € (autonomie, phase 1) : cours finis, au moins au plancher
        self.assertTrue(all(
            math.isfinite(company.stock_price) and company.stock_price >= 1.0
            for company in simulation.companies if company.active
        ))
    def test_cash_transfers_are_conserved_across_economic_accounts(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                population_size=20,
                companies_per_sector=1,
                initial_company_cash=100000.0,
                initial_person_cash=5000.0,
                raw_materials=500.0,
                land_units=20,
                seed=19,
            )
        )
        for _ in range(24):
            simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)

    def test_unpaid_interest_is_expensed_and_capitalized_into_debt(self) -> None:
        company = Company("borrower", "Emprunteur", "food", 0.0, 10.0, debt=1000.0)
        received = service_company_debt([company], SimulationConfig(loan_interest_rate=0.01))
        self.assertEqual(received, 0.0)
        self.assertAlmostEqual(company.expenses, 10.0)
        self.assertAlmostEqual(company.debt, 1010.0)


class EconomicMechanismTests(unittest.TestCase):
    """Mécanismes introduits par la refonte (emploi, État, monnaie, salaires)."""

    @staticmethod
    def money(simulation: EconomySimulation) -> float:
        return (
            sum(person.cash for person in simulation.people)
            + sum(company.cash for company in simulation.companies)
            + simulation.government_balance
            + simulation.resources.market_cash
            - simulation.base_money_issued  # monnaie émise par la banque centrale
        )

    # --- emploi -----------------------------------------------------------
    def test_employment_target_follows_demand_and_changes_gradually(self) -> None:
        config = SimulationConfig()
        people = [Person(f"p{i}", f"Ménage {i}", 0.0) for i in range(80)]
        firm = Company(
            "food_x", "Producteur", "food", 100000.0, 1400.0,
            employees=[f"p{i}" for i in range(10)], target_employees=10,
            age_months=10, demand_ema=240.0, inventory=360.0, price=200.0,
        )
        update_workforce_targets([firm], people, config)
        stable = firm.target_employees  # 240 u. / 24 par travailleur = 10
        firm.demand_ema = 1200.0  # la demande quintuple
        update_workforce_targets([firm], people, config)
        self.assertEqual(stable, 10)
        # la cible monte avec la demande, mais d'au plus max_monthly_hiring
        self.assertEqual(firm.target_employees, 10 + config.max_monthly_hiring)
        self.assertGreater(firm.staffing_need, 40)
        firm.demand_ema = 24.0  # la demande s'effondre
        update_workforce_targets([firm], people, config)
        self.assertEqual(firm.target_employees, 10 - config.max_monthly_layoffs)

    def test_layoffs_are_limited_and_not_brutal(self) -> None:
        company = Company(
            "idle", "Sans ventes", "food", 100000.0, 1000.0,
            employees=["p1", "p2", "p3", "p4", "p5", "p6"], target_employees=6,
            previous_revenue=0.0, revenue_ema=0.0, wage_bill=6000.0,
            previous_profit=-6000.0,
        )
        people = [
            Person(f"p{i}", f"Ménage {i}", 1000.0, employer_id="idle", current_wage=1000.0)
            for i in range(1, 7)
        ]
        allocate_and_pay_labor([company], people, 1000.0, SimulationConfig())
        self.assertEqual(len(company.employees), 4)  # 6 - max_monthly_layoffs

    def test_frictional_unemployment_comes_from_job_separations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3))
        rates = [simulation.step().unemployment for _ in range(30)]
        steady = rates[12:]
        average = sum(steady) / len(steady)
        self.assertGreater(average, 0.02)
        self.assertLess(average, 0.09)
        no_friction = EconomySimulation(
            SimulationConfig(seed=3, job_separation_rate=0.0)
        )
        frictionless = [no_friction.step().unemployment for _ in range(30)][12:]
        self.assertGreater(average, sum(frictionless) / len(frictionless))

    def test_job_search_excludes_separated_workers_for_one_month(self) -> None:
        company = Company(
            "firm", "Employeur", "food", 100000.0, 1000.0,
            employees=["p1"], target_employees=1, wage_bill=1000.0,
            previous_revenue=5000.0, previous_profit=100.0,
        )
        person = Person("p1", "Salarié", 0.0, employer_id="firm", current_wage=1000.0)
        rng = random.Random(1)
        config = SimulationConfig(job_separation_rate=1.0)
        allocate_and_pay_labor([company], [person], 1000.0, config, rng)
        self.assertIsNone(person.employer_id)
        self.assertEqual(person.job_search_months, 1)
        allocate_and_pay_labor([company], [person], 1000.0, config, rng)
        self.assertEqual(person.employer_id, "firm")  # réembauché le mois suivant

    # --- demande, argent, État -------------------------------------------
    def test_no_consumption_ratchet(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=2))
        initial = {p.id: p.consumption_propensity for p in simulation.people}
        for _ in range(36):
            simulation.step()
        # changement de modèle documenté (novembre 2026, démographie) : un ménage dont le chef est
        # décédé a un héritier qui tire sa propre propension ; aucune propension ne change sinon
        same_head = {p.id for p in simulation.people if p.id in initial and p.generation == 1}
        self.assertGreater(len(same_head), 0.8 * len(initial))
        for person in simulation.people:
            if person.id in same_head:
                self.assertEqual(person.consumption_propensity, initial[person.id])
        mean_before = sum(initial[pid] for pid in same_head) / len(same_head)
        mean_after = sum(
            p.consumption_propensity for p in simulation.people if p.id in same_head
        ) / len(same_head)
        self.assertAlmostEqual(mean_before, mean_after)

    def test_public_purchases_and_transfers_conserve_money(self) -> None:
        firm = Company(
            "food_1", "Producteur", "food", 1000.0, 100.0, inventory=100.0, price=10.0
        )
        goods = Company(
            "goods_1", "Fabricant", "goods", 500.0, 100.0, goods_inventory=40.0, price=20.0
        )
        spent, food_units, goods_units = buy_public_goods([firm, goods], 400.0)
        self.assertAlmostEqual(spent, 400.0)
        self.assertAlmostEqual(firm.cash + goods.cash, 1500.0 + 400.0)
        self.assertAlmostEqual(food_units * 10.0 + goods_units * 20.0, 400.0)
        self.assertAlmostEqual(firm.revenue + goods.revenue, 400.0)
        people = [Person(f"p{i}", f"Ménage {i}", 0.0) for i in range(4)]
        self.assertAlmostEqual(pay_public_transfers(people, 100.0), 100.0)
        self.assertAlmostEqual(sum(p.cash for p in people), 100.0)
        self.assertEqual(pay_public_transfers(people, -5.0), 0.0)

    def test_upstream_fund_does_not_accumulate_over_time(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=6))
        funds = []
        gdp = []
        for _ in range(60):
            record = simulation.step()
            funds.append(simulation.resources.market_cash)
            gdp.append(record.recurrent_gdp)
        # le fonds ne contient que le flux d'un mois, pas un stock cumulé : il
        # reste une fraction stable du PIB (il croît avec l'activité nominale)
        ratios = [fund / g for fund, g in zip(funds[12:], gdp[12:])]
        self.assertLess(max(ratios), 0.5)
        self.assertLess(sum(ratios[-24:]) / 24, 1.5 * sum(ratios[:24]) / 24 + 0.01)

    def test_money_is_conserved_with_credit_construction_and_transfers(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=8))
        for _ in range(60):
            simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)

    def test_construction_is_paid_progressively_and_conserves_cash(self) -> None:
        from economy.deliveries import prepare_delivery_month

        config = SimulationConfig(housing_build_cost=60000.0, build_time_months=6)
        builder = Company("builder", "Promoteur", "real_estate", 200000.0, 100.0)
        # les travaux sont livrés par des artisans (services) et une carrière (matériaux)
        crew = Company("services_1", "Artisans", "services", 0.0, 10.0,
                       employees=[f"w{i}" for i in range(4)])
        quarry = Company("materials_1", "Carrière", "materials", 0.0, 10.0,
                         employees=[f"m{i}" for i in range(4)])
        companies = [builder, crew, quarry]
        resources = Resources(raw_materials=100000.0, land_units=3, knowledge=0.0)
        builder.housing_gap = 2  # sa prévision demande deux logements
        build_housing([builder], resources, month=1, config=config)
        self.assertEqual(len(builder.construction_pipeline), 2)
        self.assertEqual(builder.cash, 200000.0)  # rien n'est versé au lancement
        prepare_delivery_month(companies, resources, config)
        paid = pay_construction_progress(companies, resources, config)
        self.assertAlmostEqual(paid, 2 * 60000.0 / 6)
        self.assertAlmostEqual(builder.cash + crew.cash + quarry.cash, 200000.0)
        self.assertAlmostEqual(builder.construction_in_progress, paid)
        self.assertAlmostEqual(builder.construction_pipeline[0].work_done, 1.0 / 6)

    # --- dividendes, crédit, banques --------------------------------------
    def test_banks_do_not_buy_shares_of_industrial_companies(self) -> None:
        bank = Company("bank_1", "Banque", "bank", 2_000_000.0, 1000.0, profit=5000.0)
        firm = Company(
            "food_1", "Producteur", "food", 100000.0, 1000.0, profit=9000.0,
            stock_price=50.0, previous_stock_price=50.0, treasury_shares=500.0,
            shares_outstanding=1000.0,
        )
        update_prices_and_trade([bank, firm], [], None, SimulationConfig())
        self.assertEqual(bank.shares.get("food_1", 0.0), 0.0)

    def test_extend_credit_is_a_transfer_limited_by_reserves(self) -> None:
        company = Company(
            "firm", "Emprunteur", "food", 10000.0, 1000.0,
            productive_capital_value=100000.0,
        )
        config = SimulationConfig()
        lent = extend_credit(company, 5000.0, 2000.0, config)
        self.assertEqual(lent, 2000.0)  # plafonné par les réserves de la banque
        self.assertEqual(company.debt, 2000.0)
        self.assertEqual(company.cash, 12000.0)
        self.assertEqual(extend_credit(company, 100.0, 0.0, config), 0.0)

    def test_banks_earn_a_positive_profit_from_real_lending(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=9))
        # la première décennie est un transitoire dont le signe change au moindre réglage
        # (frais de mise en place, intérêts sur dépôts) : on juge sur 20 ans
        records = [simulation.step() for _ in range(240)]
        banks_profit = [r.sector_profit["bank"] for r in records]
        self.assertGreater(sum(banks_profit) / len(banks_profit), 0.0)
        self.assertGreater(records[-1].bank_loan_portfolio, 0.0)
        self.assertGreater(records[-1].bank_interest_income, 0.0)

    # --- PIB et salaires ---------------------------------------------------
    def test_recurrent_gdp_excludes_construction_and_has_no_spikes(self) -> None:
        # comptabilité de la construction : ancien mode de prix. Le mode « firm_signals »
        # est plus volatil au démarrage brut (écart max/min 1,88 sur cette graine),
        # résultat consigné dans AUTONOMIE.md (phase 3), pas une erreur de comptabilité
        simulation = EconomySimulation(SimulationConfig(seed=7))
        records = [simulation.step() for _ in range(36)]
        for record in records:
            self.assertAlmostEqual(
                record.total_gdp, record.recurrent_gdp + record.construction_value_added
            )
            self.assertAlmostEqual(record.nominal_gdp, record.total_gdp)
            self.assertGreaterEqual(record.construction_value_added, 0.0)
        # pas de pic de construction : la valeur ajoutée suit les chantiers en
        # cours (coût/durée par logement), jamais le coût complet à la mise en chantier
        monthly_work = simulation.live.housing_build_cost / simulation.config.build_time_months
        for record in records:
            started = record.housing_construction / simulation.live.housing_build_cost
            self.assertLessEqual(
                record.construction_value_added,
                (started + 40) * monthly_work,
            )
        # La volatilité du PIB dépend des prix locaux ; ce test porte sur la comptabilisation.

    def test_productivity_index_starts_at_one_and_drives_a_wage_term(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=1))
        simulation.step()
        self.assertAlmostEqual(simulation.productivity_index, 1.0, delta=0.15)
        for _ in range(30):
            simulation.step()
        self.assertGreater(simulation.productivity_index, 1.0)
        self.assertEqual(
            set(simulation.wage_terms),
            {"marché"},
        )

    def test_wages_rise_over_several_seeds_without_cpi_indexation(self) -> None:
        for seed in (3, 5, 12):
            simulation = EconomySimulation(SimulationConfig(seed=seed))
            records = [simulation.step() for _ in range(96)]
            # l'année 1 est un régime transitoire (mise en place des postes et
            # des prix) : on compare l'année 2 à l'année 8
            year2 = sum(r.average_wage for r in records[12:24]) / 12
            year8 = sum(r.average_wage for r in records[84:]) / 12
            self.assertGreater(year8, year2 * 1.10, f"seed {seed}")


class LongRunStabilityTests(unittest.TestCase):
    """Phases 1 à 3 : épuisement, banque centrale, hiérarchie, marges."""

    def test_virgin_reserves_are_replenished_by_exploration(self) -> None:
        config = SimulationConfig(
            food_productivity=20.0, raw_material_unit_price=8.0,
            raw_material_extraction_productivity=10.0, reserve_replacement_rate=1.0,
            exploration_cost_per_unit=1.0, material_recovery_rate=0.0, target_inventory_months=1.0,
        )
        producer = Company("food", "Producteur", "food", 1000.0, 10.0, employees=["w"])
        supplier = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["m"])
        resources = Resources(raw_materials=20.0, land_units=0, knowledge=0.0, raw_material_price=8.0)
        producer.demand_forecast = 10.0  # production voulue : sa prévision (stock visé d'un mois)
        produce_food([producer, supplier], resources, config)
        self.assertAlmostEqual(resources.raw_materials, 20.0)  # reconstituées

        depleting = SimulationConfig(reserve_replacement_rate=0.0, food_productivity=20.0,
                                     raw_material_extraction_productivity=10.0, raw_material_unit_price=8.0,
                                     target_inventory_months=1.0)
        resources2 = Resources(raw_materials=20.0, land_units=0, knowledge=0.0, raw_material_price=8.0)
        producer2 = Company("food", "Producteur", "food", 1000.0, 10.0, employees=["w"])
        supplier2 = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["m"])
        producer2.demand_forecast = 10.0
        produce_food([producer2, supplier2], resources2, depleting)
        self.assertLess(resources2.raw_materials, 20.0)

    def test_central_bank_supplies_required_reserves_as_advances(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=2, reserve_requirement=0.10))
        for _ in range(8):
            simulation.step()
        bank = simulation.central_bank
        self.assertEqual(bank.advances, 0.0)  # les réserves initiales suffisent
        # une masse de dépôts dix fois plus grande exige des réserves supplémentaires
        for person in simulation.people:
            person.cash += 10.0 * bank.initial_reserves / len(simulation.people)
        issued = simulation._central_bank_operations()
        self.assertGreater(issued, 0.0)
        self.assertAlmostEqual(simulation.base_money_issued, bank.advances)
        self.assertAlmostEqual(bank.reserves, bank.initial_reserves + bank.advances)
        # sans exigence, la banque centrale ne fournit aucune avance
        free = EconomySimulation(SimulationConfig(seed=2, reserve_requirement=0.0))
        for _ in range(8):
            free.step()
        self.assertEqual(free.base_money_issued, 0.0)

    def test_policy_rate_remunerates_household_deposits_but_not_currency(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3))
        for _ in range(12):
            simulation.step()
        deposits = simulation.ledger.deposits_from_cash(simulation)
        self.assertGreater(deposits, 0.0)
        self.assertLess(deposits, simulation.ledger.nonbank_cash(simulation))

    def test_money_supply_is_created_by_bank_credit_and_matches_the_ledger(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3))
        initial = simulation.ledger.nonbank_cash(simulation)
        for _ in range(30):
            record = simulation.step()
        self.assertAlmostEqual(record.money_supply, simulation.ledger.nonbank_cash(simulation))
        # la monnaie ne dépasse la monnaie fiduciaire initiale que par les dépôts créés
        self.assertAlmostEqual(
            record.money_supply - simulation.ledger.currency, record.deposits, delta=1.0
        )
        self.assertGreater(record.money_supply, initial)
        self.assertAlmostEqual(record.ledger_residual, 0.0, delta=1.0)

    def test_ranks_form_a_pyramid_with_one_leader_and_rising_pay(self) -> None:
        from economy.labor import assign_ranks, skill_level

        config = SimulationConfig(skill_wage_slope=0.0)
        firm = Company("g", "Firme", "goods", 1e6, 1000.0, executive_id="p0")
        people = []
        for index in range(20):
            person = Person(f"p{index}", f"P{index}", 0.0, employer_id="g", skill=1.0 + index * 0.04,
                            career_months=60, skills={"goods": 1.0 + index * 0.2})
            people.append(person)
            firm.employees.append(person.id)
        assign_ranks([firm], people, config)
        ranks = [p.rank for p in people]
        self.assertEqual(ranks.count(4), 1)
        self.assertLessEqual(ranks.count(3), int(20 * config.manager_share))
        self.assertGreater(ranks.count(0), 5)
        self.assertGreater(skill_level(people[-1], "goods"), skill_level(people[1], "goods"))
        top = max((p for p in people if p.rank == 3), key=lambda p: p.skills["goods"], default=None)
        if top is not None:
            self.assertGreater(people[-1].rank + 1, 1)

    def test_entry_level_pay_is_lowest_in_food_and_highest_in_technology(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=4))
        offers = {}
        for company in simulation.companies:
            offers.setdefault(company.sector, []).append(company.wage_offer)
        average = {sector: sum(v) / len(v) for sector, v in offers.items()}
        self.assertEqual(min(average, key=average.get), "food")
        self.assertEqual(max(average, key=average.get), "technology")
        ratio = average["technology"] / average["food"]
        self.assertTrue(1.8 <= ratio <= 2.3)  # fourchette attendue : ×1,8 à ×2,2
        self.assertTrue(1.2 <= average["materials"] / average["food"] <= 1.45)
        self.assertTrue(1.4 <= average["real_estate"] / average["food"] <= 1.65)

    def test_executive_pay_is_a_multiple_of_entry_pay(self) -> None:
        config = SimulationConfig()
        self.assertTrue(3.0 <= config.executive_wage_multiplier <= 10.0)

    def test_failed_real_estate_firm_hands_its_housing_to_a_surviving_firm(self) -> None:
        failing = Company("re1", "Promoteur A", "real_estate", 10.0, 100.0,
                          debt=10_000_000.0, property_inventory=4)
        heir = Company("re2", "Promoteur B", "real_estate", 500000.0, 100.0)
        resolve_distressed_companies([failing, heir], [], 0.0, SimulationConfig(), random.Random(1))
        self.assertFalse(failing.active)
        self.assertEqual(heir.property_inventory, 4)

    def test_no_nan_or_infinite_values_over_a_long_run(self) -> None:
        import math

        simulation = EconomySimulation(SimulationConfig(seed=5))
        for _ in range(150):
            record = simulation.step()
            for value in (record.nominal_gdp, record.average_wage, record.unemployment,
                          record.consumer_price_index, record.total_profit, record.money_supply):
                self.assertTrue(math.isfinite(value))
        # moyennes sur 12 mois (novembre 2026) : les ventes groupées de centrales (secteur énergie)
        # font des pics d'un mois (graine 5, mois 140 : 13,8 M€ d'énergie, PIB × 3,4 ce mois-là) ;
        # comparer un mois à un pic d'un mois ne mesure pas un effondrement de l'activité
        gdp = [r.recurrent_gdp for r in simulation.history]
        annual = [sum(gdp[i - 12:i]) / 12.0 for i in range(12, len(gdp) + 1)]
        self.assertGreater(annual[-1], 0.3 * max(annual))


class InterSectorMarketTests(unittest.TestCase):
    """Étapes 1 et 2 : demande inter-entreprises, boosts, énergie et infrastructures."""

    @staticmethod
    def _resources() -> Resources:
        return Resources(raw_materials=0.0, land_units=0, knowledge=100.0)

    @staticmethod
    def _energy_producer(**fields) -> Company:
        """Producteur d'énergie avec une centrale et un salarié, prêt à livrer."""
        from economy.deliveries import open_energy_supply

        energy = Company("e", "Énergie", "energy", 0.0, 10.0, employees=["w"],
                         energy_capacity=1000.0, **fields)
        open_energy_supply([energy], Resources(raw_materials=0.0, land_units=0, knowledge=0.0),
                           SimulationConfig())
        return energy

    def test_business_inputs_are_real_payments_between_agents(self) -> None:
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0)
        energy = self._energy_producer()
        services = Company("s", "Prestataire", "services", 0.0, 10.0, employees=["w"])
        resources = self._resources()
        paid = pay_business_suppliers(
            buyer, 100.0, {"energy": 0.5, "services": 0.5}, [buyer, energy, services], resources
        )
        self.assertAlmostEqual(paid, 100.0)
        self.assertAlmostEqual(energy.revenue, 50.0)
        self.assertAlmostEqual(energy.customer_revenue["b"], 50.0)
        self.assertAlmostEqual(buyer.supplier_spending["e"], 50.0)
        self.assertAlmostEqual(services.revenue, 50.0)  # prestataire employeur, rien au fonds amont
        self.assertAlmostEqual(resources.market_cash, 0.0)
        self.assertAlmostEqual(buyer.cash + energy.cash + services.cash, 1000.0)

    def test_energy_without_producers_is_a_shortage_not_a_payment(self) -> None:
        # l'énergie est une livraison physique : sans centrale, rien n'est livré ni
        # payé, et la demande reste insatisfaite (elle ne part plus au fonds amont)
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0)
        resources = self._resources()
        pay_business_suppliers(buyer, 100.0, {"energy": 1.0}, [buyer], resources)
        self.assertAlmostEqual(resources.market_cash, 0.0)
        self.assertAlmostEqual(buyer.cash, 1000.0)
        self.assertAlmostEqual(resources.energy_unmet, 100.0)
        self.assertAlmostEqual(buyer.energy_received, 0.0)

    def test_grid_saving_lowers_the_energy_bill_without_creating_money(self) -> None:
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0, energy_saving=0.2)
        energy = self._energy_producer()
        resources = self._resources()
        pay_business_suppliers(buyer, 100.0, {"energy": 1.0}, [buyer, energy], resources)
        self.assertAlmostEqual(energy.revenue, 80.0)
        self.assertAlmostEqual(buyer.cash, 920.0)

    def test_operating_inputs_flow_from_banks_to_technology_providers(self) -> None:
        config = SimulationConfig()
        bank = Company("bank", "Banque", "bank", 100000.0, 10.0, previous_revenue=10000.0)
        tech = Company("t", "Tech", "technology", 0.0, 10.0, employees=["w"])
        resources = self._resources()
        settle_operating_inputs([bank, tech], resources, config)
        self.assertGreater(tech.revenue, 0.0)
        self.assertAlmostEqual(
            bank.cash + tech.cash + resources.market_cash, 100000.0
        )

    def test_technology_price_is_a_share_of_the_value_it_creates(self) -> None:
        config = SimulationConfig()
        buyer = Company(
            "b", "Acheteur", "food", 100000.0, 1000.0, profit=1000.0,
            previous_revenue=200000.0, revenue_ema=200000.0,
        )
        provider = Company(
            "p", "Tech", "technology", 1000.0, 1000.0, employees=["w"]
        )
        resources = self._resources()
        trade_technology([buyer, provider], resources, config)
        self.assertGreater(resources.boost_spending, 0.0)
        # la tech capte 30 % de la valeur créée : l'acheteur garde le reste
        self.assertAlmostEqual(
            resources.boost_spending / resources.boost_value_created,
            config.tech_value_capture,
        )
        self.assertLess(config.tech_value_capture, 0.41)
        self.assertGreater(buyer.tech_level, 0.0)

    def test_banks_buy_boosts_and_lend_more_with_technology(self) -> None:
        config = SimulationConfig()
        bank = Company(
            "bank", "Banque", "bank", 1000000.0, 1000.0, profit=1000.0,
            previous_revenue=100000.0, revenue_ema=100000.0,
        )
        provider = Company("p", "Tech", "technology", 1000.0, 1000.0, employees=["w"])
        trade_technology([bank, provider], self._resources(), config)
        self.assertGreater(bank.tech_level, 0.0)
        firm = Company(
            "f", "Emprunteur", "goods", 1000.0, 100.0, target_employees=20,
            previous_revenue=500000.0, risk_tolerance=0.8,
        )
        plain = request_operating_credit([firm], [], 1e9, config)
        firm2 = Company(
            "f", "Emprunteur", "goods", 1000.0, 100.0, target_employees=20,
            previous_revenue=500000.0, risk_tolerance=0.8,
        )
        boosted = request_operating_credit(
            [firm2], [], 1e9, config, credit_multiplier=1.0 + config.bank_credit_gain_per_level * 3
        )
        self.assertGreaterEqual(boosted, plain)

    def test_infrastructure_is_bought_only_if_the_expected_gain_exceeds_the_cost(self) -> None:
        config = SimulationConfig()
        resources = self._resources()
        cheap_labor = Company("a", "A", "food", 1000.0, 10.0)
        costly_labor = Company("b", "B", "food", 1000.0, 1000.0)
        self.assertFalse(infrastructure_is_worthwhile("production", cheap_labor, config, resources))
        self.assertTrue(infrastructure_is_worthwhile("production", costly_labor, config, resources))

    def test_energy_firms_sell_grid_infrastructure_that_cuts_the_buyer_bill(self) -> None:
        config = SimulationConfig()
        provider = Company("e", "Énergie", "energy", 1000.0, 10.0, employees=["w"])
        buyer = Company(
            "b", "Producteur", "food", 500000.0, 10.0, profit=1000.0,
            previous_revenue=100000.0, revenue_ema=100000.0,
            energy_bill_previous=20000.0, target_employees=5,
        )
        resources = self._resources()
        total_before = buyer.cash + provider.cash + resources.market_cash
        spent = trade_infrastructure([buyer, provider], resources, config)
        self.assertGreater(spent, 0.0)
        self.assertGreater(buyer.grid_level, 0.0)
        self.assertGreater(buyer.energy_saving, 0.0)
        self.assertGreater(provider.revenue, 0.0)
        self.assertGreater(buyer.capital_expenditure, 0.0)
        self.assertAlmostEqual(
            buyer.cash + provider.cash + resources.market_cash, total_before
        )

    def test_storage_infrastructure_raises_the_output_multiplier(self) -> None:
        from economy.models import productivity_multiplier

        config = SimulationConfig()
        firm = Company("f", "Firme", "food", 1000.0, 10.0)
        before = productivity_multiplier(firm, config)
        firm.storage_level = 2.0
        self.assertGreater(productivity_multiplier(firm, config), before)


class InnovationTests(unittest.TestCase):
    """Innovation : le stock de connaissances se construit par la recherche."""

    @staticmethod
    def _resources() -> Resources:
        return Resources(raw_materials=0.0, land_units=0, knowledge=1000.0)

    def test_knowledge_grows_with_research_and_erodes_without_it(self) -> None:
        config = SimulationConfig()
        investing, idle = self._resources(), self._resources()
        for _ in range(120):
            update_knowledge(investing, config, 0.03 * 1000.0, 1000.0)  # 3 % du PIB
            update_knowledge(idle, config, 0.0, 1000.0)
        self.assertGreater(investing.knowledge, 1000.0)
        self.assertLess(idle.knowledge, 1000.0)  # obsolescence sans recherche
        self.assertGreater(investing.knowledge_index, 1.0)
        self.assertLess(idle.knowledge_index, 1.0)

    def test_productivity_follows_the_knowledge_index_with_decreasing_returns(self) -> None:
        config = SimulationConfig()
        resources = self._resources()
        resources.knowledge = 4000.0
        resources.knowledge_reference = 1000.0
        index = update_knowledge(resources, config, 0.0, 1000.0)
        # quadrupler les connaissances ne quadruple pas la productivité (élasticité 0,5)
        self.assertTrue(1.5 < index < 2.1)
        from economy.models import productivity_multiplier

        firm = Company("f", "Firme", "food", 1000.0, 10.0)
        config.knowledge_bonus = index - 1.0
        self.assertAlmostEqual(productivity_multiplier(firm, config), index)

    def test_technology_researchers_count_as_research_effort(self) -> None:
        config = SimulationConfig()
        tech = Company("t", "Tech", "technology", 1e5, 100.0, wage_bill=10000.0)
        other = Company("o", "Autre", "food", 1e5, 100.0, wage_bill=50000.0)
        effort = research_effort([tech, other], 500.0, config)
        self.assertAlmostEqual(effort, 500.0 + config.tech_research_share * 10000.0)

    def test_knowledge_is_not_used_up_by_technology_sales(self) -> None:
        config = SimulationConfig()
        self.assertEqual(config.technology_knowledge_use_rate, 0.0)
        buyer = Company(
            "b", "Acheteur", "food", 100000.0, 1000.0, profit=1000.0,
            previous_revenue=200000.0, revenue_ema=200000.0,
        )
        provider = Company("p", "Tech", "technology", 1000.0, 1000.0, employees=["w"])
        resources = self._resources()
        trade_technology([buyer, provider], resources, config)
        self.assertEqual(resources.knowledge, 1000.0)

    def test_long_run_productivity_grows_endogenously(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7))
        records = [simulation.step() for _ in range(240)]
        self.assertGreater(records[-1].tfp_index, 1.15)
        self.assertGreater(records[-1].research_intensity, 0.005)


class PublicBudgetTests(unittest.TestCase):
    """Budget public : emploi, dette, stabilisateurs, règle budgétaire."""

    def _simulation(self, **overrides) -> EconomySimulation:
        simulation = EconomySimulation(
            SimulationConfig(population_size=60, seed=3, **overrides)
        )
        for _ in range(3):
            simulation.step()
        return simulation

    @staticmethod
    def _cash(simulation: EconomySimulation) -> float:
        return (
            sum(p.cash for p in simulation.people)
            + sum(c.cash for c in simulation.companies)
            + simulation.government_balance
            + simulation.resources.market_cash
        )

    def test_deficit_is_financed_by_bank_bonds_that_create_a_public_deposit(self) -> None:
        simulation = self._simulation()
        simulation.public.cash = 0.0
        debt = simulation.public.debt
        held = sum(b.bond_holdings for b in simulation.companies if b.sector == "bank")
        residual = simulation.ledger.residual(simulation)
        money = simulation.ledger.nonbank_cash(simulation)
        funded = simulation._fund_public(50000.0)
        self.assertAlmostEqual(funded, 50000.0)
        self.assertAlmostEqual(simulation.public.debt, debt + 50000.0)
        self.assertAlmostEqual(
            sum(b.bond_holdings for b in simulation.companies if b.sector == "bank"),
            held + 50000.0,
        )
        # la monnaie est créée par les banques, et la comptabilité reste équilibrée
        self.assertAlmostEqual(simulation.ledger.nonbank_cash(simulation), money + 50000.0)
        self.assertAlmostEqual(simulation.ledger.residual(simulation), residual)

    def test_debt_ceiling_stops_borrowing(self) -> None:
        simulation = self._simulation(debt_ceiling_gdp=0.0)
        simulation.public.cash = 0.0
        self.assertEqual(simulation._fund_public(10000.0), 0.0)
        self.assertEqual(simulation.public.debt, 0.0)


class CompetitionAndTradesTests(unittest.TestCase):
    """Étapes 3 et 4 : métiers, monopoles et entrées concurrentielles."""

    def test_only_entrepreneurial_households_with_capital_found_firms(self) -> None:
        config = SimulationConfig(startup_threshold=10000.0, company_founder_probability=1.0)
        timid = Person("timid", "Prudent", 50000.0, entrepreneurial_spirit=0.2)
        bold = Person("bold", "Entrepreneur", 50000.0, entrepreneurial_spirit=0.9)
        poor = Person("poor", "Sans capital", 500.0, entrepreneurial_spirit=0.99)
        startups, _, count = create_household_startups(
            [], [timid, bold, poor], 1, 0.0, config, random.Random(2)
        )
        self.assertEqual(count, 1)
        self.assertEqual(startups[0].founder_id, "bold")



    def test_concentrated_profitable_sector_attracts_more_entry_than_a_competitive_one(self) -> None:
        from economy.finance import sector_entry_signals

        config = SimulationConfig(entry_min_revenue_months=0.0)
        monopoly = [Company("m", "M", "goods", 1e5, 10.0, previous_revenue=100000.0, previous_profit=30000.0)]
        competitive = [
            Company(f"c{i}", "C", "goods", 1e5, 10.0, previous_revenue=25000.0, previous_profit=7500.0)
            for i in range(4)
        ]
        high = sector_entry_signals(monopoly, config, 1000.0, ("goods",))["goods"]
        low = sector_entry_signals(competitive, config, 1000.0, ("goods",))["goods"]
        self.assertGreater(high, low)
        self.assertGreater(low, 0.0)  # la marge supérieure à la cible attire aussi

    def test_specialized_posts_need_the_right_trade_and_generic_posts_take_anyone(self) -> None:
        config = SimulationConfig()
        tech = Company("t", "Tech", "technology", 1e5, 100.0, target_employees=10)
        developer = Person("dev", "Dev", 0.0, trade="developpeur")
        operator = Person("op", "Op", 0.0, trade="operateur")
        choice = pick_hire(tech, None, [operator, developer], {}, 10, config)
        self.assertEqual(choice[0].id, "dev")
        self.assertEqual(choice[1], "developpeur")
        food = Company("f", "Food", "food", 1e5, 100.0, target_employees=2)
        engineer = Person("eng", "Ing", 0.0, trade="ingenieur")
        person, slot = pick_hire(food, None, [engineer], {}, 2, config)
        self.assertEqual(slot, "operateur")  # un surqualifié occupe un poste générique

    def test_trade_wages_and_unemployment_react_to_tension(self) -> None:
        config = SimulationConfig()
        market = TradeMarket(config, random.Random(1))
        tech = Company("t", "Tech", "technology", 1e5, 100.0, target_employees=20)
        people = [
            Person(f"p{i}", "P", 0.0, trade="ingenieur" if i == 0 else "operateur")
            for i in range(30)
        ]
        for _ in range(20):
            market.refresh(people, [tech])
        # un seul ingénieur pour une demande élevée : le métier est en tension
        self.assertGreater(market.tension["ingenieur"], 1.0)
        self.assertGreater(market.wage_index["ingenieur"], market.wage_index["operateur"])
        rows = {row["Métier"]: row for row in market.table(people)}
        self.assertEqual(rows["ingenieur"]["Offre (personnes)"], 1)
        self.assertGreater(rows["ingenieur"]["Coefficient de salaire"], rows["operateur"]["Coefficient de salaire"])
        self.assertAlmostEqual(rows["operateur"]["Chômage"], 1.0)  # personne n'est embauché ici

    def test_retraining_costs_money_and_takes_time(self) -> None:
        config = SimulationConfig(
            retraining_start_month=0, retraining_probability=1.0,
            retraining_min_unemployment=0.0, retraining_months=3,
        )
        market = TradeMarket(config, random.Random(1))
        tech = Company("t", "Tech", "technology", 1e5, 100.0, target_employees=40)
        # la formation est vendue par une entreprise de services qui emploie
        school = Company("services_1", "Formation", "services", 0.0, 100.0, employees=["f"])
        worker = Person("w", "Chômeur", 5000.0, trade="commercial")
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        market.refresh([worker], [tech])
        started = market.retrain([worker], resources, 1000.0, [school])
        self.assertEqual(started, 1)
        self.assertAlmostEqual(worker.cash, 4000.0)
        self.assertAlmostEqual(school.cash, 1000.0)
        self.assertEqual(worker.trade, "commercial")  # pas instantané
        for _ in range(3):
            market.retrain([worker], resources, 1000.0, [school])
        self.assertNotEqual(worker.trade, "commercial")

    def test_households_can_accumulate_homes_without_a_fixed_cap(self) -> None:
        config = SimulationConfig(
            home_investment_probability=1.0, home_investment_cash_multiple=1.0
        )
        seller = Company("re", "Promoteur", "real_estate", 0.0, 10.0, property_inventory=20)
        rich = Person("r", "Riche", 100 * config.housing_price, owns_home=True)
        for _ in range(5):
            buy_homes([seller], [rich], config.housing_price, config.housing_build_cost, config, 0.0, random.Random(1))
        self.assertEqual(rich.investment_homes, 5)  # l'ancien plafond était de 2

    def test_rich_household_brings_its_homes_in_kind_when_founding_a_landlord_firm(self) -> None:
        config = SimulationConfig(startup_threshold=10000.0, company_founder_probability=1.0)
        landlord = Person(
            "l", "Rentier", 50000.0, entrepreneurial_spirit=0.9, investment_homes=3
        )
        # seul le secteur immobilier est attractif : les autres sont saturés de marges nulles
        others = [
            Company(f"{s}_1", s, s, 1e5, 10.0, previous_revenue=100000.0, previous_profit=0.0)
            for s in ("food", "goods", "technology", "materials", "energy")
        ]
        real_estate = [Company("re_1", "RE", "real_estate", 1e5, 10.0, property_inventory=5,
                               previous_revenue=100000.0, previous_profit=40000.0)]
        startups, _, count = create_household_startups(
            [*others, *real_estate], [landlord], 1, 0.0, config, random.Random(4)
        )
        self.assertEqual(count, 1)
        self.assertEqual(startups[0].sector, "real_estate")
        self.assertGreaterEqual(startups[0].property_inventory, 3)
        self.assertEqual(landlord.investment_homes, 0)


class SectorBalanceLongRunTests(unittest.TestCase):
    """Critères d'acceptation 1 à 3 sur un horizon long (graine fixe)."""

    def test_housing_markup_and_land_follow_supply_and_demand(self) -> None:
        simulation = EconomySimulation(SimulationConfig(population_size=60, seed=5))
        for company in simulation.companies:
            if company.sector == "real_estate":
                company.property_inventory = 0
                company.construction_pipeline = []
        start = simulation._housing_markup
        for _ in range(40):
            simulation._update_housing_costs()
        self.assertGreater(simulation._housing_markup, start)  # pénurie : marge de promotion en hausse
        for company in simulation.companies:
            if company.sector == "real_estate":
                company.property_inventory = 500
        peak = simulation._housing_markup
        land_before = simulation.resources.land_units
        for _ in range(40):
            simulation._update_housing_costs()
        self.assertLess(simulation._housing_markup, peak)  # offre abondante : baisse
        self.assertGreater(simulation.resources.land_units, land_before)  # le foncier s'étend

    def test_every_sector_keeps_firms_and_a_significant_share_of_revenue(self) -> None:
        import math

        simulation = EconomySimulation(SimulationConfig(seed=7))
        records = [simulation.step() for _ in range(360)]
        for record in records:
            self.assertTrue(math.isfinite(record.nominal_gdp))
        window = records[-120:]
        sectors = ("food", "goods", "technology", "real_estate", "materials", "energy", "bank")
        total = sum(sum(r.sector_revenue[s] for s in sectors) for r in window)
        for sector in sectors:
            active = [c for c in simulation.companies if c.active and c.sector == sector]
            self.assertGreaterEqual(len(active), 1, sector)
        minimum_share = {"technology": 0.01, "real_estate": 0.05, "materials": 0.03, "energy": 0.03}
        for sector, floor in minimum_share.items():
            share = sum(r.sector_revenue[sector] for r in window) / total
            self.assertGreater(share, floor, sector)
        # les entreprises immobilières détiennent des logements
        self.assertGreater(
            sum(c.property_inventory for c in simulation.companies if c.active and c.sector == "real_estate"),
            0,
        )


class GovernmentPolicyTests(unittest.TestCase):
    """Orientation politique (0 libéral, 100 social), élections et leviers."""

    def test_elections_are_periodic_random_and_reproducible(self) -> None:
        def run() -> list[float]:
            sim = EconomySimulation(
                SimulationConfig(seed=5, population_size=60, election_period_months=4)
            )
            for _ in range(13):
                sim.step()
            return [g.orientation for g in sim.government_history]

        first = run()
        self.assertEqual(len(first), 4)  # gouvernement initial + élections aux mois 5, 9, 13
        self.assertTrue(all(0.0 <= value <= 100.0 for value in first))
        self.assertEqual(first, run())
        self.assertGreater(len(set(first)), 1)

    def test_minimum_wage_floors_pay(self) -> None:
        from economy.labor import _pay

        config = SimulationConfig(minimum_wage=2000.0)
        company = Company("c", "C", "goods", 0.0, 900.0)
        person = Person("p", "P", 0.0)
        self.assertGreaterEqual(_pay(company, person, "Employé", config), 2000.0)


class ReviewFixTests(unittest.TestCase):
    def test_merger_keeps_employees_and_capital(self) -> None:
        buyer = Company("goods_1", "A", "goods", 1000.0, 1000.0)
        target = Company("goods_2", "B", "goods", 500.0, 1000.0)
        for company in (buyer, target):
            company.stock_price, company.shares_outstanding = 10.0, 100.0
        target.employees = ["p1", "p2", "p3", "p4"]
        target.production_capacity, target.productive_capital_value = 40.0, 9000.0
        people = [Person(f"p{i}", f"P{i}", 0.0, employer_id="goods_2") for i in range(1, 5)]
        from economy.consolidation import _merge_into

        _merge_into(buyer, target, people, [buyer, target])
        self.assertEqual(sorted(buyer.employees), ["p1", "p2", "p3", "p4"])
        self.assertEqual(buyer.production_capacity, 40.0)
        self.assertEqual(buyer.productive_capital_value, 9000.0)
        self.assertTrue(all(p.employer_id == "goods_1" for p in people))


    def test_tax_losses_are_carried_forward(self) -> None:
        from economy.government import tax_company_profits

        firm = Company("goods_1", "Biens 1", "goods", 100000.0, 1000.0)
        firm.revenue, firm.expenses = 0.0, 1000.0  # perte de 1000
        self.assertEqual(tax_company_profits([firm], 0.2, carry_months=60), 0.0)
        firm.revenue, firm.expenses = 1500.0, 0.0  # profit de 1500
        collected = tax_company_profits([firm], 0.2, carry_months=60)
        self.assertLess(collected, 0.2 * 1500.0)  # la perte antérieure est imputée
        self.assertGreater(collected, 0.0)

    def test_liquidation_without_outside_shareholders_keeps_money(self) -> None:
        # le fondateur a revendu tous ses titres : le boni de liquidation lui revient
        founder = Person("p1", "Fondateur", 0.0)
        firm = Company("startup_1", "S", "food", 5000.0, 6000.0, founder_id="p1")
        firm.debt, firm.loss_streak, firm.age_months, firm.profit = 3000.0, 10, 3, -1.0
        rival = Company("food_1", "F", "food", 1000.0, 1000.0)
        config = SimulationConfig()
        cash_before = founder.cash + firm.cash + rival.cash
        always = random.Random(0)
        # Cessation de paiements : trésorerie sous le salaire, dette et pertes persistantes.
        resolve_distressed_companies([firm, rival], [founder], 0.0, config, always)
        self.assertFalse(firm.active)
        # seule la dette remboursée (3000) détruit de la monnaie
        self.assertAlmostEqual(founder.cash + rival.cash, cash_before - 3000.0)

    def test_failed_bank_hands_over_its_real_assets(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3, population_size=60))
        for _ in range(3):
            simulation.step()
        bank = next(c for c in simulation.companies if c.sector == "bank")
        bank.technology_asset_value += 5000.0
        bank.bank_equity = -100.0  # faillite au prochain passage
        simulation.ledger.calibrate(simulation)  # référence : bilan de la banque en faillite
        before = simulation.ledger.residual(simulation)
        simulation._write_off_defaulted_bank_loans()
        self.assertFalse(bank.active)
        self.assertAlmostEqual(simulation.ledger.residual(simulation), before, delta=1e-6)

    def test_consumer_credit_is_granted_repaid_and_defaults(self) -> None:
        from economy.household_credit import extend_consumer_credit, service_consumer_credit

        config = SimulationConfig()
        worker = Person("p1", "Salarié", 100.0, current_wage=1000.0)
        lent = extend_consumer_credit([worker], 1e9, config, 0.005)
        self.assertAlmostEqual(lent["p1"], 900.0)  # trésorerie ramenée à un mois de salaire
        self.assertAlmostEqual(worker.cash, 1000.0)
        paid, accrued, defaults = service_consumer_credit([worker], config)
        self.assertGreater(paid, 0.0)
        self.assertLess(worker.consumer_debt, 900.0)
        self.assertEqual(defaults, {})
        # sans trésorerie, les impayés s'accumulent jusqu'au défaut
        worker.cash = 0.0
        for _ in range(config.consumer_default_months):
            _, _, defaults = service_consumer_credit([worker], config)
        self.assertIn("p1", defaults)
        self.assertEqual(worker.consumer_debt, 0.0)

    def test_foreclosure_sells_the_home_and_clears_the_loan(self) -> None:
        from economy.household_credit import foreclose_homes

        config = SimulationConfig()
        owner = Person("p1", "Propriétaire", 0.0, owns_home=True, mortgage_debt=50000.0)
        owner.mortgage_arrears = config.mortgage_default_months
        landlord = Company("real_estate_1", "Immo", "real_estate", 1e6, 1000.0)
        result = foreclose_homes([owner], [landlord], config)
        price = config.housing_price * (1.0 - config.foreclosure_discount)
        self.assertEqual(result["p1"], (50000.0, 50000.0))
        self.assertFalse(owner.owns_home)
        self.assertEqual(owner.mortgage_debt, 0.0)
        self.assertAlmostEqual(owner.cash, price - 50000.0)  # surplus rendu au ménage
        self.assertEqual(landlord.property_inventory, 1)

    def test_bankrupt_assets_are_auctioned_to_a_rival(self) -> None:
        from economy.finance import auction_assets

        config = SimulationConfig()
        failed = Company("goods_2", "B", "goods", 0.0, 1000.0)
        failed.production_capacity, failed.productive_capital_value = 40.0, 10000.0
        rival = Company("goods_1", "A", "goods", 1e6, 1000.0)
        paid = auction_assets(failed, [rival], config)
        self.assertAlmostEqual(paid, 10000.0 * (1.0 - config.asset_auction_discount))
        self.assertAlmostEqual(failed.cash, paid)
        self.assertAlmostEqual(rival.production_capacity, 40.0)
        self.assertAlmostEqual(rival.productive_capital_value, 10000.0)

    def test_price_controls_cap_prices_when_inflation_is_high(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=2, population_size=60))
        for _ in range(14):
            simulation.step()
        simulation.live.price_control_level = 1.0
        with patch.object(simulation, "_annual_inflation_gap", return_value=-0.05):
            self.assertAlmostEqual(simulation._price_control_cap(), simulation.live.inflation_target)
        with patch.object(simulation, "_annual_inflation_gap", return_value=0.0):
            self.assertIsNone(simulation._price_control_cap())

    def test_household_credit_keeps_the_ledger_balanced(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(seed=8, population_size=80, consumer_credit_buffer_months=3.0)
        )
        records = [simulation.step() for _ in range(120)]
        self.assertGreater(max(r.consumer_debt for r in records), 0.0)
        self.assertLess(max(abs(r.ledger_residual) for r in records), 1e-3)

    def test_stock_value_falls_when_interest_rates_rise(self) -> None:
        from economy.stock_market import _fundamental_price

        config = SimulationConfig(stock_book_value_weight=0.0)
        firm = Company("goods_1", "A", "goods", 0.0, 1000.0, profit=10000.0,
                       previous_profit=10000.0, shares_outstanding=1000.0)
        low = _fundamental_price(firm, [firm], config, annual_rate=0.01)
        high = _fundamental_price(firm, [firm], config, annual_rate=0.06)
        self.assertGreater(low, high)

    def test_secondary_market_does_not_force_buybacks(self) -> None:
        # un krach ne fait plus racheter les titres par l'entreprise : sa trésorerie reste intacte
        company = Company("c1", "E", "food", 5000.0, 10.0, profit=-100.0,
                          stock_price=100.0, previous_stock_price=200.0, treasury_shares=0.0)
        holder = Person("p1", "Actionnaire", 0.0, shares={"c1": 100.0})
        update_prices_and_trade([company], [holder])
        self.assertEqual(company.cash, 5000.0)
        self.assertAlmostEqual(holder.shares.get("c1", 0.0) + company.treasury_shares, 100.0)

    def test_households_buy_and_sell_government_bonds_with_banks(self) -> None:
        from economy.stock_market import trade_bonds_with_banks

        bank = Company("bank_1", "B", "bank", 0.0, 1000.0, bond_holdings=1000.0)
        saver = Person("p1", "Épargnant", 800.0)
        seller = Person("p2", "Vendeur", 0.0, bond_holdings=200.0)
        net = trade_bonds_with_banks([(saver, 500.0), (seller, -200.0)], [bank])
        self.assertAlmostEqual(net, 300.0)
        self.assertAlmostEqual(saver.bond_holdings, 500.0)
        self.assertAlmostEqual(saver.cash, 300.0)
        self.assertAlmostEqual(seller.cash, 200.0)
        self.assertAlmostEqual(bank.bond_holdings, 700.0)

    def test_variable_rate_loans_follow_the_policy_rate(self) -> None:
        from economy.finance import service_company_debt

        firm = Company("goods_1", "A", "goods", 100000.0, 1000.0, debt=10000.0,
                       debt_interest_rate=0.01, loan_spread=0.002)
        service_company_debt([firm], SimulationConfig(), policy_rate=0.004)
        self.assertAlmostEqual(firm.debt_interest_rate, 0.006)
        self.assertAlmostEqual(firm.interest_paid_this_turn, 60.0)
        fixed = Company("goods_2", "B", "goods", 100000.0, 1000.0, debt=10000.0,
                        debt_interest_rate=0.01, loan_spread=0.002)
        service_company_debt([fixed], SimulationConfig(variable_rate_loans=False), policy_rate=0.004)
        self.assertAlmostEqual(fixed.debt_interest_rate, 0.01)

    def test_new_loans_go_to_banks_with_free_capital(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=2, population_size=40))
        banks = [c for c in simulation.companies if c.sector == "bank"]
        banks[0].bank_equity = 0.0  # plus aucune capacité propre
        simulation._book_bank_loan("borrower", 900.0)
        self.assertEqual(banks[0].bank_loan_accounts.get("borrower", 0.0), 0.0)
        self.assertAlmostEqual(sum(b.bank_loan_accounts.get("borrower", 0.0) for b in banks), 900.0)

    def test_interbank_lenders_absorb_a_failed_bank_loss(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3, population_size=60))
        simulation.step()
        banks = [c for c in simulation.companies if c.sector == "bank" and c.active]
        failing, lender = banks[0], banks[1]
        lender.interbank_claims[failing.id] = 50000.0
        equity_before = lender.bank_equity
        failing.bank_equity = -20000.0
        simulation._write_off_defaulted_bank_loans()
        self.assertFalse(failing.active)
        # le prêteur interbancaire perd 20 000 (renflouement interne), pas l'État
        self.assertAlmostEqual(lender.bank_equity, equity_before - 20000.0)

    def test_vat_is_paid_by_households_and_collected_by_the_state(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=5, population_size=60))
        records = [simulation.step() for _ in range(6)]
        self.assertTrue(all(r.vat_revenue > 0.0 for r in records))
        self.assertLess(max(abs(r.ledger_residual) for r in records), 1e-3)

    def test_engel_law_lowers_the_food_share_of_richer_households(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=6, population_size=20))
        poor, rich = simulation.people[0], simulation.people[1]
        poor.expected_income = simulation.wage_reference
        rich.expected_income = 4.0 * simulation.wage_reference
        simulation._update_engel_factors()
        self.assertAlmostEqual(poor.engel_food, 1.0)
        self.assertLess(rich.engel_food, 1.0)
        self.assertGreater(rich.engel_services, 1.0)

    def test_housing_is_valued_at_capitalised_rents(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=4, population_size=40))
        simulation.policy_rate = 0.001
        low_rates = simulation._housing_user_cost()
        simulation.policy_rate = 0.008
        self.assertGreater(simulation._housing_user_cost(), low_rates)

    def test_entrant_offers_the_sector_wage(self) -> None:
        from economy.finance import _entrant_wage_offer

        firms = [Company(f"food_{i}", "F", "food", 0.0, wage, employees=[f"worker_{i}"]) for i, wage in enumerate((900.0, 1100.0))]
        config = SimulationConfig()
        self.assertAlmostEqual(_entrant_wage_offer(firms, "food", 1400.0, config), 1000.0)
        # secteur vide : salaire de référence du secteur
        self.assertAlmostEqual(
            _entrant_wage_offer(firms, "energy", 1400.0, config),
            1400.0,
        )


class AuditFixTests(unittest.TestCase):
    """Régressions des défauts reproduits par l'audit du 28 octobre 2026 et nouvelles règles."""

    def test_empty_shelves_keep_the_unmet_demand(self) -> None:
        firm = Company("food_1", "F", "food", 0.0, 10.0, price=100.0, inventory=0.0)
        buyer = Person("p1", "Ménage", 1000.0, income_this_turn=1000.0)
        sold, revenue, unmet = sell_food([firm], [buyer], market_price=100.0)
        self.assertEqual((sold, revenue), (0.0, 0.0))
        self.assertGreater(unmet, 0.0)  # la pénurie reste visible

    def test_an_empty_seller_does_not_block_the_next_one(self) -> None:
        empty = Company("real_estate_1", "Vide", "real_estate", 0.0, 10.0)
        stocked = Company("real_estate_2", "Plein", "real_estate", 0.0, 10.0, property_inventory=1)
        buyer = Person("p1", "Acheteur", 200000.0, income_this_turn=3000.0)
        purchases, _ = buy_homes([empty, stocked], [buyer], 120000.0, 70000.0, SimulationConfig())
        self.assertEqual(purchases, 1)
        self.assertTrue(buyer.owns_home)

    def test_restructured_debt_leaves_no_claim_on_bank_books(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=1, population_size=40))
        bank = next(c for c in simulation.companies if c.sector == "bank")
        firm = next(c for c in simulation.companies if c.sector == "food")
        bank.bank_loan_accounts[firm.id] = 100000.0
        firm.debt, firm.default_loss = 0.0, 100000.0  # dette annulée, entreprise maintenue
        simulation._write_off_defaulted_bank_loans()
        self.assertEqual(sum(b.bank_loan_accounts.get(firm.id, 0.0) for b in simulation.companies), 0.0)

    def test_firms_closed_during_the_month_stay_in_the_month_statistics(self) -> None:
        from economy.records import build_record

        simulation = EconomySimulation(SimulationConfig(seed=2, population_size=40))
        simulation.step()
        firm = next(c for c in simulation.companies if c.sector == "goods")
        firm.revenue, firm.expenses, firm.profit = 1000.0, 1500.0, -500.0
        firm.active, firm.exit_month = False, simulation.month
        others = sum(c.revenue for c in simulation.companies if c.active)
        record = build_record(simulation, MonthFlows(), 0.0)
        self.assertAlmostEqual(record.total_revenue, others + 1000.0)
        # le mois suivant, l'entreprise fermée ne compte plus
        simulation.month += 1
        self.assertAlmostEqual(build_record(simulation, MonthFlows(), 0.0).total_revenue, others)

    def test_technology_orders_are_served_whatever_the_supplier_order(self) -> None:
        from economy.goods import _split_among_suppliers

        even = _split_among_suppliers(100.0, {"a": (60.0, 1.0), "b": (60.0, 1.0)})
        self.assertAlmostEqual(even["a"] + even["b"], 100.0)
        self.assertAlmostEqual(even["a"], even["b"])
        # parts pondérées par le stock ; tout est servi tant qu'il reste du stock
        uneven = _split_among_suppliers(100.0, {"a": (30.0, 1.0), "b": (100.0, 1.0)})
        self.assertAlmostEqual(uneven["a"] + uneven["b"], 100.0)
        self.assertLessEqual(uneven["a"], 30.0)
        swapped = _split_among_suppliers(100.0, {"b": (100.0, 1.0), "a": (30.0, 1.0)})
        self.assertAlmostEqual(swapped["a"], uneven["a"])  # l'ordre des fournisseurs ne compte pas
        short = _split_among_suppliers(100.0, {"a": (30.0, 1.0), "b": (40.0, 1.0)})
        self.assertAlmostEqual(short["a"] + short["b"], 70.0)  # stock total épuisé

    def test_consumer_price_index_includes_vat(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3, population_size=30))
        before = simulation._consumer_price_index()
        simulation.live.vat_rate = simulation.config.vat_rate + 0.20
        self.assertGreater(simulation._consumer_price_index(), before)

    def test_net_worth_can_be_negative(self) -> None:
        debtor = Person("p1", "Endetté", 100.0, consumer_debt=1000.0)
        self.assertAlmostEqual(household_net_worth(debtor, [], SimulationConfig()), -900.0)

    def test_last_firm_can_be_liquidated_when_the_policy_says_so(self) -> None:
        config = SimulationConfig()
        firm = Company("materials_1", "Seule", "materials", 0.0, 10.0, debt=5000.0)
        _, defaults = resolve_distressed_companies([firm], [], 0.0, config, random.Random(0))
        self.assertEqual(defaults, 1)
        self.assertFalse(firm.active)

    def test_energy_rationing_cuts_next_month_production(self) -> None:
        config = SimulationConfig(food_productivity=10.0)

        def produced(fill: float) -> float:
            firm = Company("food_1", "F", "food", 1e6, 10.0, employees=["w1", "w2"], energy_fill=fill)
            resources = Resources(raw_materials=1e6, land_units=0, knowledge=0.0)
            produce_food([firm], resources, config, market_demand=1e6)
            return firm.units_produced

        self.assertAlmostEqual(produced(0.5), produced(1.0) * 0.5)

    def test_energy_capacity_bounds_deliveries(self) -> None:
        from economy.deliveries import open_energy_supply

        producer = Company("energy_1", "E", "energy", 0.0, 10.0, employees=["w"], energy_capacity=40.0)
        buyer = Company("food_1", "F", "food", 1000.0, 10.0)
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        open_energy_supply([producer, buyer], resources, SimulationConfig())
        pay_business_suppliers(buyer, 100.0, {"energy": 1.0}, [producer, buyer], resources)
        self.assertAlmostEqual(buyer.energy_received, 40.0)  # la centrale ne livre que sa capacité
        self.assertAlmostEqual(resources.energy_unmet, 60.0)
        self.assertAlmostEqual(producer.revenue, 40.0)
        self.assertAlmostEqual(buyer.cash + producer.cash, 1000.0)

    def test_books_reconcile_over_a_run(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=9, population_size=80))
        records = [simulation.step() for _ in range(120)]
        self.assertLess(max(r.loan_reconciliation_error for r in records), 1e-3)
        self.assertLess(max(r.public_debt_reconciliation_error for r in records), 1e-3)
        self.assertLess(max(abs(r.ledger_residual) for r in records), 1e-3)


class InvestmentDecisionTests(unittest.TestCase):
    """Décision d'emprunt des entreprises : rendement attendu face au coût du crédit."""

    def test_borrowing_rises_with_the_return_spread(self) -> None:
        from economy.finance import borrowing_share

        config = SimulationConfig()
        loan_cost = 12.0 * (0.004 + config.credit_risk_spread * 0.5)
        self.assertAlmostEqual(
            borrowing_share(loan_cost, 0.004, config), config.credit_financed_share
        )
        self.assertGreater(
            borrowing_share(loan_cost + 0.02, 0.004, config),
            borrowing_share(loan_cost, 0.004, config),
        )
        # un crédit plus cher (taux directeur relevé) réduit l'endettement voulu
        self.assertLess(
            borrowing_share(loan_cost, 0.008, config),
            borrowing_share(loan_cost, 0.004, config),
        )
        self.assertEqual(borrowing_share(1.0, 0.0, config), 0.90)
        self.assertEqual(borrowing_share(-1.0, 0.0, config), 0.05)

    def test_indebted_firms_pay_a_higher_spread(self) -> None:
        from economy.finance import investment_loan_spread

        config = SimulationConfig()
        sober = Company("goods_1", "G", "goods", 1000.0, 10.0, productive_capital_value=9000.0)
        indebted = Company("goods_2", "G", "goods", 1000.0, 10.0, productive_capital_value=9000.0,
                           debt=8000.0)
        self.assertGreater(
            investment_loan_spread(indebted, config), investment_loan_spread(sober, config)
        )

    def test_no_reaction_to_the_inflation_gap(self) -> None:
        """L'ancienne règle crédit/inflation a disparu : seul le prix du crédit compte."""
        self.assertFalse(hasattr(SimulationConfig(), "credit_inflation_gain"))
        self.assertFalse(hasattr(EconomySimulation, "_credit_share"))

    def test_an_illiquid_loss_making_firm_is_resolved(self) -> None:
        config = SimulationConfig(bankruptcy_months=2)
        zombie = Company("food_1", "F", "food", 0.0, 1000.0, productive_capital_value=1e6,
                         debt=5e5, profit=-100.0, loss_streak=1)
        other = Company("food_2", "F", "food", 1e4, 10.0)
        _, defaults = resolve_distressed_companies(
            [zombie, other], [], 0.0, config, random.Random(1)
        )
        self.assertEqual(defaults, 1)
        self.assertFalse(zombie.active)

    def test_books_balance_with_the_investment_decision(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=5, population_size=80))
        records = [simulation.step() for _ in range(120)]
        self.assertLess(max(abs(r.ledger_residual) for r in records), 1e-3)
        self.assertLess(max(r.loan_reconciliation_error for r in records), 1e-3)


class BankingResilienceTests(unittest.TestCase):
    """Sortie de crise bancaire : recapitalisation contre actions, dividendes bloqués."""

    def test_public_resolution_recapitalizes_above_the_minimum(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(seed=3, population_size=60, companies_per_sector=1, bank_bailout_cap_gdp=1.0)
        )
        simulation.step()
        bank = next(c for c in simulation.companies if c.sector == "bank")
        bank.bank_equity = -5000.0
        simulation.ledger.calibrate(simulation)
        simulation._write_off_defaulted_bank_loans()
        exposure = sum(max(0.0, v) for v in bank.bank_loan_accounts.values()) + bank.bond_holdings
        live = simulation.live
        self.assertGreaterEqual(
            bank.bank_equity,
            (live.capital_requirement + live.bank_recap_buffer) * exposure - 1e-6,
        )
        self.assertGreater(simulation._bank_capacity(bank), 0.0)
        self.assertGreater(simulation.public.account.shares.get(bank.id, 0.0), 0.0)
        self.assertIn("bank_recap", [e.kind for e in simulation.resolution_events])
        self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1e-6)

    def test_undercapitalized_bank_pays_no_dividend(self) -> None:
        bank = Company("bank_1", "B", "bank", 1e6, 10.0, profit=5e4, revenue=1e5,
                       bank_equity=10.0, bank_loan_accounts={"food_1": 1e6})
        holder = Person("p1", "P", 0.0, shares={"bank_1": 100.0})
        distribute_dividends([bank], [holder], 0.5, retained=frozenset({"bank_1"}))
        self.assertEqual(bank.dividend_paid, 0.0)
        self.assertEqual(holder.cash, 0.0)

    def test_auction_buyer_keeps_its_working_capital(self) -> None:
        from economy.finance import auction_assets

        config = SimulationConfig()
        failed = Company("food_1", "F", "food", 0.0, 1000.0, productive_capital_value=1e6,
                         production_capacity=100.0)
        buyer = Company("food_2", "F", "food", 1e6, 1000.0, previous_revenue=4e5)
        auction_assets(failed, [buyer], config)
        self.assertGreaterEqual(buyer.cash, config.working_capital_months * 4e5 - 1e-6)


class PublicDebtRuleTests(unittest.TestCase):
    """Règle d'ancrage : déficit sous la cible de dette, excédent au-dessus."""

    def test_repayment_never_leaves_negative_holdings(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=8, population_size=60))
        for _ in range(60):
            simulation.step()
        simulation.repay_government_bonds(simulation.public.debt * 1.5)
        self.assertGreaterEqual(min(p.bond_holdings for p in simulation.people), 0.0)
        # tolérance d'arrondi (phase 3) : un achat de tout le portefeuille d'une banque
        # peut laisser −1e-13 ; le borner changerait les trajectoires existantes
        self.assertGreaterEqual(
            min(b.bond_holdings for b in simulation.companies if b.sector == "bank"), -1e-9
        )


class WageSettingTests(unittest.TestCase):
    """Marché du travail : offres salariales fixées par chaque entreprise (``wage_setting``)."""

    def _offer_after(self, months: int = 1, capital_cost: float = 0.0, **kwargs) -> Company:
        # chiffre d'affaires lissé large : la capacité de payer ne limite pas, sauf test dédié
        values = dict(staffing_need=10, employees=[f"p{i}" for i in range(10)], revenue=1000.0,
                      profit=0.0, revenue_ema=1e9)
        values.update(kwargs)
        firm = Company("food_1", "F", "food", 1_000_000.0, 1000.0, **values)
        for _ in range(months):
            adjust_profit_sharing_wages(
                [firm], [], 1400.0, 0.03, 0.70, 0.03, {"food_1": capital_cost}, SimulationConfig(),
            )
        return firm

    def test_raise_stops_at_what_the_firm_can_pay(self) -> None:
        # 2 salariés pour un besoin de 10 : elle voudrait relever son offre de 2,4 %,
        # mais 12 000 € de recettes − 1 000 € d'autres charges − 1 000 € de coût du
        # capital ne paient que 10 salariés à 1 000 € : elle garde son offre
        firm = self._offer_after(employees=["a", "b"], revenue_ema=12_000.0, expenses=3000.0,
                                 wage_bill=2000.0, capital_cost=1000.0)
        self.assertAlmostEqual(firm.wage_offer, 1000.0)
        # avec 30 000 € de recettes, la hausse passe entière
        richer = self._offer_after(employees=["a", "b"], revenue_ema=30_000.0, expenses=3000.0,
                                   wage_bill=2000.0, capital_cost=1000.0)
        self.assertAlmostEqual(richer.wage_offer, 1000.0 * (1.0 + 0.03 * 0.8))

    def test_offer_follows_the_firms_own_vacancies_surplus_and_losses(self) -> None:
        vacant = self._offer_after(employees=["a", "b"])  # 8 postes vacants sur 10
        self.assertAlmostEqual(vacant.wage_offer, 1000.0 * (1.0 + 0.03 * 0.8))
        crowded = self._offer_after(staffing_need=5)  # 5 salariés de trop
        self.assertAlmostEqual(crowded.wage_offer, 1000.0 * (1.0 - 0.03 * 1.0))
        losing = self._offer_after(profit=-100.0)  # marge de −10 %, effectif complet
        self.assertAlmostEqual(losing.wage_offer, 1000.0 * (1.0 - 0.25 * 0.70 * 0.10))
        # en pertes mais à court de bras : l'offre monte quand même
        short = self._offer_after(profit=-100.0, employees=["a", "b"])
        self.assertAlmostEqual(short.wage_offer, 1000.0 * (1.0 + 0.03 * 0.8))
        balanced = self._offer_after()
        self.assertAlmostEqual(balanced.wage_offer, 1000.0)

    def test_no_sector_base_or_cap_limits_a_firm_that_cannot_hire(self) -> None:
        # ancien mode : au plus 1,6 × 0,65 × 1 400 € = 1 456 € pour l'alimentation
        firm = self._offer_after(months=60, employees=["a"])
        self.assertGreater(firm.wage_offer, 1.6 * 0.65 * 1400.0)

    def test_wage_reference_is_the_observed_average_offer(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(seed=3, population_size=60)
        )
        simulation.step()  # sans chauffe, personne n'est encore employé à la création
        firms = [c for c in simulation.companies if c.active and c.employees]
        self.assertTrue(firms)
        simulation._update_wage_reference()
        expected = sum(c.wage_offer * len(c.employees) for c in firms) / sum(
            len(c.employees) for c in firms
        )
        self.assertAlmostEqual(simulation.wage_reference, expected)

    def test_firm_wage_setting_keeps_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(seed=7, population_size=120)
        )
        for _ in range(150):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
        offers = [c.wage_offer for c in simulation.companies if c.active]
        self.assertTrue(all(math.isfinite(o) and o > 0.0 for o in offers))


class LaborAndB2BDemandTests(unittest.TestCase):
    """Demande de travail au salaire (``labor_demand``) et demande B2B au prix."""

    def _need(self, price: float, wage: float, sector: str = "food") -> int:
        config = SimulationConfig()
        people = [Person(f"p{i}", f"Ménage {i}", 0.0) for i in range(80)]
        firm = Company(
            "f", "Producteur", sector, 100000.0, wage, price=price,
            employees=[f"p{i}" for i in range(10)], target_employees=10,
            age_months=10, demand_ema=1200.0, inventory=0.0, wage_bill=10 * wage,
        )
        update_workforce_targets([firm], people, config)
        return firm.staffing_need

    def test_marginal_product_demand_falls_with_the_wage(self) -> None:
        # une unité vendue 100 € et 24 unités par salarié : un salarié vaut 2 400 €
        full = self._need(100.0, 1000.0)  # rentable : tout le besoin technique
        needs = [self._need(100.0, w) for w in (2000.0, 2600.0, 4000.0, 8000.0)]
        self.assertEqual(needs[0], full)  # 2 000 € < 2 400 € : encore rentable
        self.assertEqual(needs, sorted(needs, reverse=True))
        self.assertLess(needs[-1], needs[1])
        self.assertLess(needs[-1], full)

    def test_marginal_product_demand_falls_with_a_lower_price(self) -> None:
        high = self._need(100.0, 3000.0)
        low = self._need(50.0, 3000.0)
        self.assertLess(low, high)

    def test_b2b_volume_responds_to_the_price_index(self) -> None:
        def paid(elasticity: float, index: float) -> float:
            buyer = Company("b", "Acheteur", "food", 10000.0, 10.0)
            tech = Company("t", "Tech", "technology", 0.0, 10.0, employees=["w"])
            resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
            resources.tech_service_price_index = index
            resources.b2b_price_elasticity = elasticity
            return pay_business_suppliers(
                buyer, 1000.0, {"technology": 1.0}, [buyer, tech], resources
            )
        self.assertAlmostEqual(paid(0.0, 2.0), 2000.0)  # volume fixe : la dépense suit le prix
        self.assertAlmostEqual(paid(1.0, 2.0), 1000.0)  # élasticité 1 : dépense constante
        self.assertLess(paid(1.5, 2.0), 1000.0)  # au-delà : le fournisseur perd des recettes
        self.assertAlmostEqual(paid(1.5, 1.0), 1000.0)  # prix de base : rien ne change

    def test_new_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(
            seed=7, population_size=120, b2b_price_elasticity=1.5,
        ))
        for _ in range(120):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
        self.assertAlmostEqual(simulation.resources.b2b_price_elasticity, 1.5)


class BankDecisionTests(unittest.TestCase):
    """Phase 5 : prêt selon la perte attendue, coussin de fonds propres, taux de dépôt par banque."""

    def _borrower(self, debt: float = 0.0, profit: float = 100.0, age: int = 60) -> Company:
        return Company("c", "Emprunteur", "goods", 10_000.0, 1000.0, debt=debt, profit=profit,
                       revenue=1000.0, age_months=age)

    def test_expected_loss_grows_with_leverage_losses_and_youth(self) -> None:
        from economy.credit_risk import expected_loss
        config = SimulationConfig()
        base = expected_loss(self._borrower(), 1000.0, 50_000.0, config)
        self.assertGreater(expected_loss(self._borrower(debt=200_000.0), 1000.0, 50_000.0, config), base)
        self.assertGreater(expected_loss(self._borrower(profit=-300.0), 1000.0, 50_000.0, config), base)
        self.assertGreater(expected_loss(self._borrower(age=3), 1000.0, 50_000.0, config), base)
        self.assertLess(expected_loss(self._borrower(), 1000.0, 500_000.0, config), base)

    def test_loan_is_reduced_or_refused_when_the_expected_loss_is_too_high(self) -> None:
        from economy.credit_risk import approved_loan, risk_loan_spread
        config = SimulationConfig()
        healthy = self._borrower()
        self.assertEqual(approved_loan(healthy, 1000.0, 50_000.0, config), 1000.0)
        fragile = self._borrower(debt=400_000.0, profit=-400.0, age=3)
        self.assertEqual(approved_loan(fragile, 100_000.0, 20_000.0, config), 0.0)
        # un emprunteur limite voit son prêt réduit, pas refusé
        borderline = self._borrower()
        calm = approved_loan(borderline, 200_000.0, 50_000.0, config)
        # après une vague de faillites (taux de défaut observé de 10 %), les banques réduisent
        stressed_config = SimulationConfig(observed_default_rate=0.10)
        reduced = approved_loan(borderline, 200_000.0, 50_000.0, stressed_config)
        self.assertGreater(reduced, 0.0)
        self.assertLess(reduced, 200_000.0)
        self.assertLess(reduced, calm)
        # la marge couvre la perte attendue : plus chère pour le plus risqué
        self.assertGreater(
            risk_loan_spread(self._borrower(debt=40_000.0), 1000.0, 50_000.0, config, 0.004),
            risk_loan_spread(healthy, 1000.0, 50_000.0, config, 0.004),
        )

    def test_consumer_credit_is_rationed_by_usage_of_the_ceiling(self) -> None:
        from economy.credit_risk import approved_household_loan
        config = SimulationConfig()
        person = Person("p", "Ménage", 0.0, current_wage=1000.0)
        wide = approved_household_loan(person, 2000.0, config)
        person.consumer_debt = config.consumer_credit_income_multiple * 1000.0 * 0.9
        self.assertLess(approved_household_loan(person, 2000.0, config), wide)

    def test_lending_capacity_shrinks_after_a_bank_loses_on_its_loans(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        simulation.step()
        bank = next(c for c in simulation.companies if c.sector == "bank" and c.active)
        before = simulation._bank_capacity(bank)
        bank.loss_rate_cycle += 0.01
        self.assertLess(simulation._bank_capacity(bank), before)

    def test_deposit_rate_differs_with_liquidity_and_shares_move(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        for _ in range(3):
            simulation.step()
        banks = [c for c in simulation.companies if c.sector == "bank" and c.active]
        self.assertGreaterEqual(len(banks), 2)
        simulation.live = simulation.config.fresh_copy()
        simulation.policy_rate = 0.01  # au-dessus de l'écart fixe : le taux de référence est positif
        rich, poor = banks[0], banks[1]
        for bank in banks:
            bank.previous_revenue, bank.previous_profit = 1000.0, 0.0
            bank.cash = 1e9 if bank is rich else 0.0
        rate, charges = simulation._bank_deposit_charges()
        self.assertLess(rich.deposit_rate_offer, poor.deposit_rate_offer)
        self.assertAlmostEqual(sum(w for _, w in charges), 1.0)
        shares = {b.id: b.deposit_share for b in banks}
        self.assertAlmostEqual(sum(shares.values()), 1.0)
        for _ in range(30):  # la banque mieux rémunérée gagne des parts
            simulation._bank_deposit_charges()
        self.assertGreater(poor.deposit_share, shares[poor.id])
        self.assertAlmostEqual(sum(b.deposit_share for b in banks), 1.0)

    def test_new_bank_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(
            seed=7, population_size=120, 
        ))
        for _ in range(120):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
        banks = [c for c in simulation.companies if c.sector == "bank" and c.active]
        self.assertAlmostEqual(sum(b.deposit_share for b in banks), 1.0)




class CapacityValuationTests(unittest.TestCase):
    """R1 (DIAGNOSTIC_AUTONOMIE.md) : capacité évaluée sur les ventes en plus et les salaires payés."""

    CONFIG = dict()

    def _firm(self, **fields) -> Company:
        values = dict(cash=500_000.0, wage_offer=1200.0, price=300.0, unit_input_cost=50.0, energy_fill=1.0)
        values.update(fields)
        return Company("food_x", "Producteur", "food", **values)

    def _invest(self, firm: Company, config: SimulationConfig) -> float:
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        return invest_in_production_capacity(
            [firm, Company("supplier", "Machines", "energy", 0.0, 1000.0, employees=["worker"])],
            {"food": 0.0}, config, resources,
            bank_reserves=0.0, policy_rate=0.004, expected_inflation=0.0,
        )

    def test_no_workers_and_no_unmet_demand_means_no_project(self) -> None:
        from economy.business import planned_capacity_investment

        config = SimulationConfig(**self.CONFIG)
        firm = self._firm(demand_ema=0.0, potential_output=0.0, profit=10_000.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        self.assertEqual(planned_capacity_investment(firm, 50.0, config, resources, 0.004, 0.0), 0.0)
        self.assertEqual(self._invest(firm, config), 0.0)
        self.assertEqual(firm.production_capacity, 0.0)

    def test_unmet_demand_sizes_the_project_even_without_profit(self) -> None:
        config = SimulationConfig(**self.CONFIG)
        firm = self._firm(demand_ema=50.0, potential_output=10.0, profit=-1.0)
        invested = self._invest(firm, config)
        # 40 unités vendables en plus, 2 500 € l'unité, sans crédit disponible
        self.assertAlmostEqual(invested, 100_000.0)
        self.assertAlmostEqual(firm.production_capacity, 40.0)

    def test_only_paid_wages_count_as_savings(self) -> None:
        config = SimulationConfig(**self.CONFIG)
        firm = self._firm(employees=["a", "b"], wage_bill=2400.0, demand_ema=48.0, potential_output=48.0)
        self.assertAlmostEqual(self._invest(firm, config), 48 * 2500.0)  # 50 €/unité : rendement 18 %/an
        cheap = self._firm(employees=["a", "b"], wage_bill=960.0, demand_ema=48.0, potential_output=48.0)
        self.assertEqual(self._invest(cheap, config), 0.0)  # 20 €/unité : sous le coût des fonds propres
        vacant = self._firm(target_employees=5, demand_ema=48.0, potential_output=48.0)
        self.assertEqual(self._invest(vacant, config), 0.0)  # postes vacants : aucun salaire économisé

    def test_equity_is_bounded_by_the_working_capital(self) -> None:
        from economy.treasury import working_capital

        config = SimulationConfig(**self.CONFIG)
        firm = self._firm(cash=30_000.0, expenses=10_000.0, demand_ema=50.0, potential_output=10.0)
        free = firm.cash - working_capital(firm, config)
        self.assertAlmostEqual(self._invest(firm, config), free)

    def test_incremental_mode_keeps_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120, **self.CONFIG))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)




class EntryAndRestartTests(unittest.TestCase):
    """Étape 2 (DIAGNOSTIC_AUTONOMIE.md, R4 et R6) : entrée sur commandes refusées,
    offre d'embauche finançable, devis de reprise."""

    def _dormant_sector(self) -> list[Company]:
        # une seule entreprise d'alimentation, sans salarié ni ventes
        return [Company("food_z", "Dormante", "food", 50_000.0, 1500.0, price=200.0)]

    def test_refused_orders_attract_entrants_only_if_the_price_covers_costs(self) -> None:
        from economy.finance import sector_entry_signals

        firms = self._dormant_sector()
        orders = SimulationConfig()
        unserved = {"food": (5_000.0, 200.0)}
        signal = sector_entry_signals(firms, orders, 1400.0, ("food",), 0.004, unserved)["food"]
        # coût de l'entrant : 1 400 / 24 + 65 ≈ 123 € ; marge espérée ≈ 38 % ; tout est non servi
        self.assertAlmostEqual(signal, orders.entry_profit_gain * (1.0 - (1400.0 / 24.0 + 65.0) / 200.0))
        self.assertLessEqual(signal, orders.entry_profit_gain)  # une part, pas une recette promise
        below_cost = {"food": (5_000.0, 100.0)}
        self.assertEqual(sector_entry_signals(firms, orders, 1400.0, ("food",), 0.004, below_cost)["food"], 0.0)

    def _wage(self, firm: Company, reference: float = 30_000.0) -> float:
        adjust_profit_sharing_wages([firm], [], reference, 0.0, 0.70, 0.03, {}, SimulationConfig())
        return firm.wage_offer

    def test_an_offer_without_staff_stays_within_what_cash_pays(self) -> None:
        def dormant() -> Company:
            return Company("food_z", "Dormante", "food", 100_000.0, 1_200_000.0, staffing_need=1,
                           target_employees=1, demand_ema=70_000.0, price=193.0)

        self.assertAlmostEqual(self._wage(dormant()), 50_000.0)  # moitié de la trésorerie

    def test_a_staffed_firm_never_raises_beyond_its_cash_nor_cuts_current_pay(self) -> None:
        firm = Company("goods_s", "Serrée", "goods", 10_000.0, 2000.0, employees=["a"], wage_bill=2000.0,
                       staffing_need=3, target_employees=3, demand_ema=1_000.0, price=50.0, revenue_ema=50_000.0)
        self.assertEqual(self._wage(firm), 2000.0)

    def test_restart_quote_replaces_a_frozen_price(self) -> None:
        from economy.firm_pricing import restart_quote_cost, update_firm_prices

        def idle() -> Company:
            return Company("food_z", "Dormante", "food", 50_000.0, 2400.0, price=50.0,
                           unit_input_cost=20.0, previous_unmet_units=100.0)

        quote = idle()
        config = SimulationConfig()
        self.assertAlmostEqual(restart_quote_cost(quote, [quote], config), 2400.0 / 24.0 + 20.0)
        update_firm_prices([quote], "food", config, 0.0, 50.0, None, 1e9)
        self.assertAlmostEqual(quote.price, 55.0)  # vers le devis de 120 €, à la vitesse maximale de 10 %

    def test_step_two_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)


class BankEntryAndCapitalTests(unittest.TestCase):
    """Étape 3 (DIAGNOSTIC_AUTONOMIE.md, R3 et n° 32) : fondation de banques, actions
    bancaires nouvelles, coût du capital limité aux fonds propres exigés."""

    class Draws:
        """Tirages imposés : fonder (0), puis choisir le dernier secteur pondéré (0,999)."""

        def __init__(self) -> None:
            self.values = [0.0, 0.999]

        def random(self) -> float:
            return self.values.pop(0) if self.values else 0.999

        def uniform(self, low: float, high: float) -> float:
            return (low + high) / 2.0

    def _prepare(self, config: SimulationConfig) -> tuple[EconomySimulation, Person]:
        """Simulation d'un mois, un fondateur riche, seule la banque attractive. La
        trésorerie du fondateur est fixée directement (hors identité monétaire) : le résidu
        se mesure après cette préparation."""
        simulation = EconomySimulation(config)
        simulation.step()
        founder = simulation.people[0]
        founder.cash, founder.entrepreneurial_spirit = 2_000_000.0, 1.0
        dominant = next(c for c in simulation.companies if c.sector == "bank")
        dominant.previous_revenue, dominant.previous_profit = 500_000.0, 300_000.0
        for company in simulation.companies:
            if company.sector != "bank":
                company.previous_revenue = 0.0  # aucun autre secteur attractif
        return simulation, founder

    def _found(self, simulation: EconomySimulation, founder: Person, config: SimulationConfig) -> list[Company]:
        from economy.finance import create_household_startups

        return create_household_startups(
            simulation.companies, [founder], simulation.month, simulation.credit_capacity(),
            config, self.Draws(), housing_shortage=0, wage_reference=config.starting_wage,
        )[0]

    def test_a_founded_bank_turns_the_founder_deposit_into_equity(self) -> None:
        config = SimulationConfig(seed=7, population_size=60)
        simulation, founder = self._prepare(config)
        residual = simulation.ledger.residual(simulation)
        created = self._found(simulation, founder, config)
        self.assertEqual([c.sector for c in created], ["bank"])
        bank = created[0]
        self.assertAlmostEqual(founder.cash, 2_000_000.0 - bank.bank_equity)
        self.assertGreater(bank.bank_equity, 0.0)
        self.assertEqual(bank.debt, 0.0)
        simulation.companies.extend(created)
        self.assertAlmostEqual(simulation.ledger.residual(simulation), residual, delta=1e-6)


    def test_an_undercapitalised_bank_offers_new_shares_without_moving_money(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60))
        simulation.step()
        bank = next(c for c in simulation.companies if c.sector == "bank" and c.active)
        bank.bank_loan_accounts.clear()
        bank.bank_loan_accounts["x"] = 1_000_000.0
        bank.bond_holdings, bank.bank_equity, bank.stock_price, bank.treasury_shares = 0.0, 50_000.0, 10.0, 0.0
        outstanding, cash, equity = bank.shares_outstanding, bank.cash, bank.bank_equity
        simulation._authorize_bank_rights_issues()
        target = simulation.live.capital_requirement + simulation.live.bank_recap_buffer
        self.assertAlmostEqual(bank.treasury_shares, (target * 1_000_000.0 - 50_000.0) / 10.0)
        self.assertAlmostEqual(bank.shares_outstanding, outstanding + bank.treasury_shares)
        self.assertEqual((bank.cash, bank.bank_equity), (cash, equity))  # rien n'est vendu ici

    def test_a_bank_pays_for_required_capital_only(self) -> None:
        for expect_required in (True,):
            simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60))
            simulation.step()
            bank = next(c for c in simulation.companies if c.sector == "bank" and c.active)
            bank.bank_loan_accounts.clear()
            bank.bank_loan_accounts["x"] = 20_000_000.0
            bank.bond_holdings, bank.bank_equity, bank.cash = 0.0, 10_000_000.0, 10_000_000.0
            monthly = simulation._cost_of_equity() / 12.0
            live = simulation.live
            base = (live.capital_requirement + live.bank_recap_buffer) * 20_000_000.0 if expect_required else 10_000_000.0
            self.assertAlmostEqual(simulation._monthly_capital_costs()[bank.id], monthly * base)



    def test_a_bank_designated_to_exit_without_rival_stays_open(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60, ))
        simulation.step()
        banks = [c for c in simulation.companies if c.sector == "bank" and c.active]
        for bank in banks[1:]:
            bank.active = False  # hors comptes : seul le chemin de sortie est examiné
            bank.bank_loan_accounts.clear()
        last = banks[0]
        last.founder_id, last.age_months, last.profit = simulation.people[0].id, 12, -1.0
        last.loss_streak = simulation.live.bankruptcy_months
        book = dict(last.bank_loan_accounts)

        class Always:
            def random(self) -> float:
                return 0.0

        resolve_distressed_companies(simulation.companies, simulation.people, 0.0, simulation.live,
                                     Always(), (simulation.public.account,),
                                     wind_down_bank=simulation._wind_down_bank)
        self.assertTrue(last.active)
        self.assertEqual(dict(last.bank_loan_accounts), book)

    def test_step_three_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)




class EnergyMarketTests(unittest.TestCase):
    """R8 simplifiée : prix de l'énergie par la rareté, centrales selon les livraisons."""

    def test_losses_no_longer_push_the_price_up_when_capacity_is_idle(self) -> None:
        from economy.firm_pricing import signal_index_change

        config = SimulationConfig()
        self.assertGreater(signal_index_change(0.0, 0.5, -0.1, config, 0.5), 0.0)                # pertes → hausse
        self.assertLess(signal_index_change(0.0, 0.5, -0.1, config, 0.5, scarcity=True), 0.0)    # capacité inutilisée → baisse
        self.assertGreater(signal_index_change(0.3, 0.0, -0.1, config, 0.5, scarcity=True), 0.0)  # rareté → hausse

    def _producer(self, delivered: float) -> Company:
        return Company("energy_1", "Centrale", "energy", 1_000_000.0, 1000.0, energy_capacity=100.0,
                       previous_energy_delivered=delivered, previous_revenue=100_000.0)

    def _plant_owner(self, left: float) -> Company:
        # bilan du mois écoulé : 100 unités livrées, ``left`` restées invendues
        return Company("energy_1", "Centrale", "energy", 1_000_000.0, 1000.0, employees=["a"],
                       energy_capacity=100.0, previous_energy_delivered=100.0, previous_energy_left=left)

    def test_refused_orders_leave_out_what_the_buyer_cannot_pay(self) -> None:
        from economy.deliveries import deliver_energy

        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0, energy_unit_price=1.0)
        supplier = Company("energy_1", "Centrale", "energy", 0.0, 1000.0, energy_available=10.0)
        buyer = Company("food_1", "Ferme", "food", 0.0, 1000.0)
        deliver_energy(buyer, 100.0, 30.0, [supplier, buyer], resources)  # payable : 30, livré : 10
        self.assertAlmostEqual(resources.energy_refused, 20.0)
        self.assertAlmostEqual(resources.energy_unmet, 90.0)
        deliver_energy(buyer, 100.0, 0.0, [supplier, buyer], resources)   # rien de payable
        self.assertAlmostEqual(resources.energy_refused, 20.0)

    def test_forecast_follows_each_producers_own_orders(self) -> None:
        import random
        from economy.energy import update_energy_forecasts

        config = SimulationConfig(demand_forecast_error=0.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0, previous_energy_refused=50.0)
        big = Company("energy_1", "A", "energy", 0.0, 1000.0, previous_energy_delivered=60.0)
        small = Company("energy_2", "B", "energy", 0.0, 1000.0, previous_energy_delivered=40.0)
        entrant = Company("energy_3", "C", "energy", 0.0, 1000.0)  # aucune centrale, aucune livraison
        update_energy_forecasts([big, small, entrant], resources, config, random.Random(1))
        # commandes = livraisons + part égale des commandes refusées faute d'offre
        self.assertAlmostEqual(big.energy_demand_forecast, 60.0 + 50.0 / 3)
        self.assertAlmostEqual(small.energy_demand_forecast, 40.0 + 50.0 / 3)
        self.assertAlmostEqual(entrant.energy_demand_forecast, 50.0 / 3)  # l'entrant voit aussi la demande
        resources.previous_energy_refused = 0.0
        big.previous_energy_delivered = 100.0
        update_energy_forecasts([big, small, entrant], resources, config, random.Random(1))
        a = config.demand_smoothing
        first = 60.0 + 50.0 / 3
        level = first + a * (100.0 - first)
        self.assertAlmostEqual(big.energy_demand_forecast, level + a * (level - first))
        # avec une erreur de prévision, deux tirages différents donnent deux prévisions différentes
        noisy = SimulationConfig(demand_forecast_error=0.2)
        update_energy_forecasts([big], resources, noisy, random.Random(1))
        first = big.energy_demand_forecast
        update_energy_forecasts([big], resources, noisy, random.Random(2))
        self.assertNotAlmostEqual(first, big.energy_demand_forecast)

    def test_forecast_plants_cover_the_forecast_above_worn_capacity_if_profitable(self) -> None:
        from economy.energy import forecast_plant_project

        config = SimulationConfig()
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        owner = self._plant_owner(0.0)
        owner.energy_demand_forecast = 150.0
        decay = config.sector_capital_depreciation.get("energy", config.productive_capacity_depreciation_rate)
        units, annual = forecast_plant_project(owner, 20.0, resources, config, 0.004)
        self.assertAlmostEqual(units, 150.0 - 100.0 * (1.0 - decay))
        self.assertGreater(annual, 12.0 * 0.004 + config.equity_risk_premium)
        owner.energy_demand_forecast = 50.0  # surcapacité prévue : rien
        self.assertEqual(forecast_plant_project(owner, 20.0, resources, config, 0.004), (0.0, 0.0))
        owner.energy_demand_forecast = 150.0
        resources.energy_unit_price = 0.1    # prix sous le coût salarial par unité : rien
        self.assertEqual(forecast_plant_project(owner, 20.0, resources, config, 0.004), (0.0, 0.0))

    def test_forecast_staffing_follows_planned_output_within_installed_plants(self) -> None:
        from economy.labor import update_workforce_targets

        config = SimulationConfig(max_monthly_hiring=10)
        people = [Person(f"p{i}", f"Ménage {i}", 0.0) for i in range(20)]
        producer = Company("energy_1", "Centrale", "energy", 0.0, 1000.0, employees=["p0"], age_months=12,
                           energy_capacity=30_000.0, energy_demand_forecast=20_000.0, revenue_ema=1e7)
        update_workforce_targets([producer], people, config)
        self.assertEqual(producer.staffing_need, math.ceil(20_000.0 / config.energy_output_per_worker))
        producer.energy_capacity = 10_000.0  # pas plus de salariés que les centrales n'en occupent
        update_workforce_targets([producer], people, config)
        self.assertEqual(producer.staffing_need, math.ceil(10_000.0 / config.energy_output_per_worker))

    def test_forecast_market_keeps_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)




class LaborOfferOrderTests(unittest.TestCase):
    """L'index sectoriel doit préserver les égalités et consommer les vacances."""

    def test_equal_effective_offers_keep_global_order_when_own_offer_is_first(self):
        employer = Company("a", "Employeur", "goods", 100000.0, 2000.0,
                           employees=["p"], target_employees=2, wage_bill=1000.0,
                           previous_revenue=5000.0, previous_profit=500.0)
        first = Company("b", "Premier", "services", 100000.0, 1500.0, target_employees=1)
        same_sector = Company("c", "Même secteur", "goods", 100000.0, 1500.0, target_employees=1)
        worker = Person("p", "Salarié", 0.0, employer_id="a", current_wage=500.0,
                        skills={"goods": 3.0, "services": 3.0})
        # Le poste vacant de l'employeur initial est pris par un autre candidat.
        unemployed = Person("u", "Candidat", 0.0, skill=2.0)
        allocate_and_pay_labor([same_sector, first, employer], [worker, unemployed], 1000.0,
                              SimulationConfig(labor_mobility_premium=0.1))
        self.assertEqual(worker.employer_id, "b")

    def test_depleted_offer_yields_to_next_company_in_same_sector(self):
        employer = Company("old", "Employeur", "goods", 100000.0, 1000.0,
                           employees=["p1", "p2"], target_employees=2, wage_bill=2000.0,
                           previous_revenue=5000.0, previous_profit=500.0)
        offers = [Company(cid, cid, "services", 100000.0, 1500.0, target_employees=1)
                  for cid in ("a", "b")]
        workers = [Person(cid, cid, 0.0, employer_id="old", current_wage=1000.0,
                          skill=skill, skills={"services": 3.0})
                   for cid, skill in (("p1", 1.2), ("p2", 1.0))]
        allocate_and_pay_labor([employer, *offers], workers, 1000.0, SimulationConfig())
        self.assertEqual([p.employer_id for p in workers], ["a", "b"])


class LoanAccountsCacheTests(unittest.TestCase):
    """Cache de la somme des encours : toujours égal au calcul direct, bit à bit."""

    @staticmethod
    def _direct(accounts) -> float:
        return sum(max(0.0, balance) for balance in accounts.values())

    def test_every_kind_of_mutation_invalidates_the_cache(self) -> None:
        from copy import deepcopy
        from economy.models import LoanAccounts, positive_loan_book
        accounts = LoanAccounts()
        steps = [
            lambda: accounts.__setitem__("a", 0.1),
            lambda: accounts.__setitem__("b", 0.2),
            lambda: accounts.update({"c": 0.30000000000000004, "d": -5.0}),
            lambda: accounts.__setitem__("a", accounts["a"] * 1.0000001),
            lambda: accounts.pop("b"),
            lambda: accounts.setdefault("e", 7.7),
            lambda: accounts.__delitem__("e"),
            lambda: accounts.__ior__({"f": 3.3}),
            lambda: accounts.popitem(),
            lambda: accounts.clear(),
            lambda: accounts.update(a=1.5),
        ]
        for step in steps:
            self.assertEqual(positive_loan_book(accounts), self._direct(accounts))  # remplit le cache
            step()
            self.assertEqual(positive_loan_book(accounts), self._direct(accounts))
        clone = deepcopy(accounts)
        clone["z"] = 9.9
        self.assertEqual(positive_loan_book(clone), self._direct(clone))
        self.assertEqual(positive_loan_book(accounts), self._direct(accounts))

    def test_plain_dicts_still_work(self) -> None:
        from economy.models import positive_loan_book
        plain = {"a": 1.0, "b": -2.0, "c": 0.5}
        self.assertEqual(positive_loan_book(plain), 1.5)

    def test_banks_use_the_tracked_accounts_and_the_engine_keeps_them_in_sync(self) -> None:
        from economy.models import LoanAccounts, positive_loan_book
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
        for _ in range(60):
            simulation.step()
            for bank in simulation.companies:
                if bank.sector == "bank":
                    self.assertIsInstance(bank.bank_loan_accounts, LoanAccounts)
                    self.assertEqual(
                        positive_loan_book(bank.bank_loan_accounts), self._direct(bank.bank_loan_accounts)
                    )




class WarmupTests(unittest.TestCase):
    """Période de chauffe : l'état initial est construit par le modèle lui-même."""

    def _warmed(self, months: int, seed: int = 4) -> EconomySimulation:
        simulation = EconomySimulation(
            SimulationConfig(seed=seed, population_size=60, warmup_months=months)
        )
        _ORIGINAL_WARM_UP(simulation)
        return simulation

    def test_observation_starts_at_period_zero(self) -> None:
        simulation = self._warmed(30)
        self.assertEqual(simulation.period, 0)
        self.assertEqual(simulation.month, 30)
        self.assertLessEqual(len(simulation.history), 24)
        self.assertEqual(simulation.history[-1].month, 0)
        self.assertEqual(simulation.resolution_events, [])
        # la chauffe n'est pas affichée : l'historique observé part de la période 0
        self.assertEqual([r.month for r in simulation.observed_history], [0])
        record = simulation.step()
        self.assertEqual(record.month, 1)
        self.assertEqual([r.month for r in simulation.observed_history], [0, 1])
        self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1e-3)

    def test_warmup_only_shifts_the_observation(self) -> None:
        """Aucune règle modifiée : la trajectoire est celle d'un départ sans chauffe."""
        warmed = self._warmed(20)
        cold = EconomySimulation(SimulationConfig(seed=4, population_size=60, warmup_months=20))
        for _ in range(20):
            cold.step()
        self.assertEqual(
            repr(warmed.step().consumer_price_index), repr(cold.step().consumer_price_index)
        )

    def test_shocks_are_dated_in_observed_periods(self) -> None:
        from economy.models import Shock

        simulation = self._warmed(12)
        simulation.shocks.append(Shock("taux", start=1, months=1, size=0.05))
        simulation.step()
        self.assertGreater(simulation.live.policy_rate_offset, 0.0)


class TargetRegistryTests(unittest.TestCase):
    """Autonomie, phase 0 : toute cible, plafond, plancher, boost ou probabilité de
    ``SimulationConfig`` est recensé et justifié dans AUTONOMIE.md."""

    WORDS = {"target", "ceiling", "cap", "boost", "floor", "probability"}
    # cibles des agents dont c'est le rôle explicite : exclues du registre
    LEGITIMATE_AGENT_TARGETS = {
        "inflation_target": "banque centrale (règle de Taylor)",
    }

    @staticmethod
    def _registry() -> dict[str, str]:
        """Lignes du tableau « Registre des cibles » : paramètre -> justification."""
        import os
        import re

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Registre des cibles")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        entries = {}
        for match in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE):
            cells = [cell.strip() for cell in match.group(2).split("|") if cell.strip()]
            entries[match.group(1)] = " ".join(cells)
        return entries

    def _target_fields(self) -> set[str]:
        import dataclasses

        # recherche par mot du nom : « capital_requirement » n'est pas un plafond
        return {
            f.name for f in dataclasses.fields(SimulationConfig)
            if self.WORDS & set(f.name.split("_"))
        }

    def test_every_target_field_is_justified(self) -> None:
        registry = self._registry()
        for name in sorted(self._target_fields() - set(self.LEGITIMATE_AGENT_TARGETS)):
            with self.subTest(parametre=name):
                self.assertIn(name, registry, "paramètre absent du registre d'AUTONOMIE.md")
                self.assertGreaterEqual(len(registry[name]), 40, "justification trop courte")

    def test_registry_lists_only_existing_fields(self) -> None:
        fields = self._target_fields()
        for name in self.LEGITIMATE_AGENT_TARGETS:
            self.assertIn(name, fields)
        stale = set(self._registry()) - fields
        self.assertEqual(stale, set(), "paramètres du registre disparus de SimulationConfig")


class StockPriceCeilingTests(unittest.TestCase):
    """Autonomie, phase 1 : plus de plafond de 5000 € sur les cours."""

    def test_price_reaches_a_fundamental_above_5000_progressively(self) -> None:
        # valeur fondamentale = fonds propres comptables / titres = 10 000 € par action
        config = SimulationConfig(stock_book_value_weight=1.0, stock_adjustment_speed=0.18)
        company = Company(
            "firm", "Entreprise", "food", 10_000_000.0, 10.0,
            stock_price=1000.0, previous_stock_price=1000.0,
            shares_outstanding=1000.0, treasury_shares=0.0,
        )
        prices = [company.stock_price]
        for _ in range(60):
            update_prices_and_trade([company], [], config=config)
            prices.append(company.stock_price)
        for before, after in zip(prices, prices[1:]):
            # hausse progressive : jamais plus de 15 % par mois, jamais de recul
            self.assertGreaterEqual(after, before)
            self.assertLessEqual(after, before * 1.15 + 1e-9)
        self.assertGreater(max(prices), 5000.0)
        self.assertAlmostEqual(prices[-1], 10_000.0, delta=100.0)
        self.assertLessEqual(max(prices), 10_000.0 + 1e-6)

    def test_initial_price_is_not_capped(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3))
        landlord = max(
            (c for c in simulation.companies if c.sector == "real_estate"),
            key=lambda c: c.property_inventory,
        )
        expected = (
            landlord.cash + landlord.property_inventory * simulation.live.housing_price
        ) / landlord.shares_outstanding
        self.assertGreater(expected, 5000.0)
        self.assertAlmostEqual(landlord.stock_price, expected, delta=1e-6 * expected)


class TreasuryAndTakeoverTests(unittest.TestCase):
    """Autonomie, phase 2 : emploi de la trésorerie par les rendements, rachats par la valeur."""

    RETURNS = {"cash": 0.02, "distribute": 0.12, "bonds": 0.05, "shares": 0.14}

    def _rich_firm(self, **kwargs) -> Company:
        values = dict(stock_price=100.0, previous_stock_price=100.0, treasury_shares=0.0,
                      wage_bill=10_000.0, expenses=10_000.0, risk_tolerance=0.5)
        values.update(kwargs)
        return Company("rich", "Riche", "goods", 1_000_000.0, 10.0, **values)

    def test_rich_firm_without_project_places_its_cash(self) -> None:
        from economy.treasury import plan_treasury, working_capital

        config = SimulationConfig()
        firm = self._rich_firm()
        plan = plan_treasury(firm, dict(self.RETURNS), 0.0, 0.0, config)
        free = firm.cash - working_capital(firm, config)
        # actions (écart 12 points) > distribution (10) > obligations (3) : tout est employé
        self.assertGreater(plan.equity_order, plan.distribution)
        self.assertGreater(plan.distribution, plan.bond_order)
        self.assertGreater(plan.bond_order, 0.0)
        self.assertAlmostEqual(
            plan.equity_order + plan.distribution + plan.bond_order,
            config.treasury_adjustment_speed * free,
        )
        # l'ordre passe par le carnet commun : la firme achète l'autre titre, jamais le sien
        other = Company("other", "Autre", "food", 50_000.0, 10.0, profit=500.0,
                        stock_price=50.0, previous_stock_price=50.0, treasury_shares=0.0)
        holder = Person("h", "Vendeur", 0.0, shares={"other": 1000.0, "rich": 1000.0})
        update_prices_and_trade([firm, other], [holder], config=config,
                                corporate_orders={"rich": plan.equity_order})
        self.assertGreater(firm.shares.get("other", 0.0), 0.0)
        self.assertNotIn("rich", firm.shares)

    def test_profitable_project_is_financed_first(self) -> None:
        from economy.treasury import plan_treasury, working_capital

        config = SimulationConfig()
        firm = self._rich_firm()
        reserve = firm.cash - working_capital(firm, config)
        plan = plan_treasury(firm, dict(self.RETURNS), 0.0, reserve, config)
        self.assertEqual((plan.distribution, plan.repayment), (0.0, 0.0))
        self.assertLessEqual(plan.bond_order, 0.0)
        self.assertLessEqual(plan.equity_order, 0.0)
        # la réserve est bien le budget de la règle d'investissement
        from economy.business import planned_capacity_investment

        food = Company("f", "Producteur", "food", 400_000.0, 1500.0, profit=20_000.0,
                       revenue=100_000.0, employees=["a", "b"], price=300.0, unit_input_cost=50.0,
                       demand_ema=50.0, potential_output=10.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=100.0)
        self.assertGreater(planned_capacity_investment(food, 10.0, config, resources), 0.0)
        food.demand_ema = 0.0  # sans ventes supplémentaires ni salaires payés à économiser
        self.assertEqual(planned_capacity_investment(food, 10.0, config, resources), 0.0)

    def test_lower_return_leads_to_sales(self) -> None:
        from economy.treasury import plan_treasury

        config = SimulationConfig()
        firm = self._rich_firm(bond_holdings=400_000.0)
        returns = dict(self.RETURNS, bonds=0.01)  # obligations sous le taux des dépôts
        plan = plan_treasury(firm, returns, 0.0, 0.0, config)
        self.assertAlmostEqual(plan.bond_order, -config.treasury_adjustment_speed * 400_000.0)

    def _takeover_case(self, holder_risk: float):
        from economy.consolidation import takeover_by_value

        config = SimulationConfig()
        buyer = Company("goods_a", "A", "goods", 1_000_000.0, 10.0, stock_price=550.0,
                        previous_stock_price=550.0, treasury_shares=0.0)
        # valeur fondamentale de la cible : 0,55 × 100 000 / 1000 = 55 €, cours à 38,5 €
        target = Company("goods_b", "B", "goods", 100_000.0, 10.0, stock_price=38.5,
                         previous_stock_price=38.5, treasury_shares=0.0)
        holder = Person("h", "Actionnaire", 0.0, risk_tolerance=holder_risk,
                        shares={"goods_b": 1000.0})
        owner = Person("o", "Propriétaire de A", 0.0, shares={"goods_a": 1000.0})
        result = takeover_by_value([buyer, target], [holder, owner], config, month=10)
        return result, buyer, target, holder

    def test_offer_fails_when_the_premium_does_not_convince(self) -> None:
        # offre à 50,05 € ; l'actionnaire audacieux estime l'action à 55 × 1,16 = 63,8 €
        (completed, bids), buyer, target, holder = self._takeover_case(0.9)
        self.assertEqual((completed, bids), (0, 1))
        self.assertTrue(target.active)
        self.assertEqual(holder.shares, {"goods_b": 1000.0})
        self.assertEqual(buyer.cash, 1_000_000.0)

    def test_offer_succeeds_when_holders_accept_the_premium(self) -> None:
        # l'actionnaire prudent estime l'action à 55 × 0,84 = 46,2 € < 50,05 € offerts
        (completed, bids), buyer, target, holder = self._takeover_case(0.1)
        self.assertEqual((completed, bids), (1, 1))
        self.assertFalse(target.active)
        offer = 38.5 * 1.3
        # moitié des titres achetée au prix offert, le reste échangé au rapport des cours
        self.assertAlmostEqual(holder.cash, 500.0 * offer)
        self.assertAlmostEqual(holder.shares["goods_a"], 500.0 * 38.5 / 550.0)
        self.assertNotIn("goods_a", buyer.shares)
        self.assertAlmostEqual(buyer.cash, 1_000_000.0 + 100_000.0 - 500.0 * offer)

    def test_cross_holdings_do_not_inflate_prices(self) -> None:
        config = SimulationConfig(stock_book_value_weight=1.0)
        a = Company("a", "A", "goods", 100_000.0, 10.0, stock_price=100.0,
                    previous_stock_price=100.0, treasury_shares=0.0, shares={"b": 400.0})
        b = Company("b", "B", "food", 100_000.0, 10.0, stock_price=100.0,
                    previous_stock_price=100.0, treasury_shares=0.0, shares={"a": 400.0})
        for _ in range(240):
            update_prices_and_trade([a, b], [], config=config)
        # point fixe : p = 100 + 0,7 × 0,4 × p, soit 138,9 € ; sans décote il serait de 166,7 €
        expected = 100.0 / (1.0 - (1.0 - config.holding_discount) * 0.4)
        self.assertAlmostEqual(a.stock_price, expected, delta=0.5)
        self.assertAlmostEqual(b.stock_price, expected, delta=0.5)

    def test_corporate_bonds_are_public_debt_and_interest_is_taxed(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=2, population_size=60))
        simulation.step()
        firm = next(c for c in simulation.companies if c.sector == "goods")
        banks = [c for c in simulation.companies if c.active and c.sector == "bank"]
        # l'État emprunte aux banques (comme dans _fund_public)
        funded = simulation.finance_government(50_000.0)
        simulation.public.cash += funded
        simulation.public.debt += funded
        bank_bonds = sum(b.bond_holdings for b in banks)
        self.assertGreater(bank_bonds, 1000.0)
        debt = simulation.public.debt
        from economy.stock_market import trade_bonds_with_banks

        trade_bonds_with_banks([(firm, 1000.0)], banks)
        simulation._sync_public_debt()
        self.assertAlmostEqual(simulation.public.debt, debt, delta=1e-6)
        self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1e-3)
        cash, public_cash = firm.cash, simulation.public.cash
        residual = simulation.ledger.residual(simulation)
        held = simulation.public.debt  # tous les porteurs
        bank_share = sum(b.bond_holdings for b in banks) / held
        firm_share = firm.bond_holdings / held
        other_firms = sum(
            c.bond_holdings for c in simulation.companies if c.sector != "bank" and c is not firm
        ) / held
        simulation.public.cash -= 100.0
        simulation.pay_bond_interest(100.0)
        tax = simulation.live.corporate_tax_rate
        self.assertAlmostEqual(firm.cash - cash, 100.0 * firm_share * (1.0 - tax))
        self.assertAlmostEqual(
            simulation.public.cash - (public_cash - 100.0),
            100.0 * (firm_share + other_firms) * tax,
        )
        # seule la part des banques sort de la monnaie : elle devient leurs fonds
        # propres à la clôture du mois (hors de ce test)
        self.assertAlmostEqual(
            simulation.ledger.residual(simulation) - residual, -100.0 * bank_share, delta=1e-6
        )

    def test_liquidation_sells_bonds_before_paying_creditors(self) -> None:
        config = SimulationConfig()
        bank = Company("bank_1", "Banque", "bank", 1_000_000.0, 10.0)
        issuer = Company("food_2", "Émetteur", "food", 50_000.0, 10.0, stock_price=10.0,
                         treasury_shares=0.0)
        failing = Company("food_1", "En faillite", "food", 0.0, 10.0, debt=1000.0,
                          bond_holdings=800.0, shares={"food_2": 5.0}, treasury_shares=0.0)
        owner = Person("o", "Actionnaire", 0.0, shares={"food_1": 1000.0})
        resolve_distressed_companies([bank, issuer, failing], [owner], 0.0, config,
                                     random.Random(1))
        self.assertFalse(failing.active)
        self.assertEqual(bank.bond_holdings, 800.0)
        self.assertAlmostEqual(failing.default_loss, 200.0)
        # créanciers non remboursés : les actions détenues retournent à leur émetteur
        self.assertEqual(issuer.treasury_shares, 5.0)
        self.assertNotIn("food_2", owner.shares)

    def test_new_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=4, population_size=120))
        bonds_seen = equity_seen = False
        for _ in range(150):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertLess(record.public_debt_reconciliation_error, 1e-3)
            for company in simulation.companies:
                self.assertNotIn(company.id, company.shares)
            bonds_seen = bonds_seen or record.corporate_bonds > 0.0
            equity_seen = equity_seen or record.corporate_equity > 0.0
        self.assertTrue(bonds_seen and equity_seen)


class FirmPricingTests(unittest.TestCase):
    """Autonomie, phase 3 : prix fixés par chaque entreprise d'après ses signaux."""

    def _firm(self, cid: str = "food_1", price: float = 30.0, **kwargs) -> Company:
        values = dict(price=price, potential_output=100.0, wage_bill=1000.0, unit_input_cost=5.0,
                      previous_units_sold=100.0, inventory=150.0, pricing_aggression=0.5)
        values.update(kwargs)
        return Company(cid, cid, "food", 10_000.0, 10.0, **values)

    CONFIG = dict()

    def _update(self, firms, market_price=30.0):
        from economy.firm_pricing import update_firm_prices

        return update_firm_prices(
            firms, "food", SimulationConfig(**self.CONFIG), 0.0, market_price, None, 1e9
        )

    def test_unmet_demand_raises_and_excess_stock_lowers_the_price(self) -> None:
        # coût variable 1000 / 100 + 5 = 15 ; marge de départ déduite du prix : 100 %
        short = self._firm(previous_unmet_units=50.0)
        self._update([short])
        self.assertAlmostEqual(short.price, 30.0 * (1.0 + 0.04 * 0.5))
        stocked = self._firm(inventory=450.0)  # 4,5 mois de ventes, norme 1,5
        self._update([stocked])
        self.assertAlmostEqual(stocked.price, 30.0 * (1.0 - 0.04 * 2.0))

    def test_price_never_falls_below_variable_cost(self) -> None:
        firm = self._firm(price=15.15, inventory=10_000.0)
        self._update([firm])
        self.assertAlmostEqual(firm.price, 15.0)
        self.assertEqual(firm.price_markup, 0.0)

    def test_competitors_pull_prices_together_and_a_monopolist_is_not_pulled(self) -> None:
        dear, cheap = self._firm("food_1", 30.0), self._firm("food_2", 20.0)
        self._update([dear, cheap])
        # rapprochement du concurrent, plus érosion de 0,06 × (1 − part de 50 %)
        self.assertAlmostEqual(dear.price, 30.0 * (1.0 + 0.06 * (20.0 / 30.0 - 1.0) - 0.03))
        self.assertAlmostEqual(cheap.price, 20.0 * (1.0 + 0.06 * 0.5 - 0.03))
        alone = self._firm("food_1", 30.0)
        self._update([alone])
        self.assertAlmostEqual(alone.price, 30.0)

    def test_competition_erodes_margins_but_a_monopolist_keeps_its_own(self) -> None:
        # deux entreprises qui visent chacune 6 % au-dessus de la moyenne : sans
        # correction du décalage, les deux prix monteraient ensemble chaque mois ;
        # ici la concurrence les ramène ensemble au coût variable (15 €)
        a = self._firm("food_1", 20.0, pricing_aggression=0.0)
        b = self._firm("food_2", 20.0, pricing_aggression=0.0)
        alone = self._firm("food_3", 20.0)
        for _ in range(60):
            self._update([a, b])
            self._update([alone])
        self.assertAlmostEqual(a.price, 15.0)
        self.assertAlmostEqual(b.price, 15.0)
        self.assertAlmostEqual(alone.price, 20.0)

    def test_market_price_is_the_sales_weighted_average(self) -> None:
        a = self._firm("food_1", 30.0, previous_units_sold=300.0, inventory=450.0)
        b = self._firm("food_2", 30.0, previous_units_sold=100.0)
        market = self._update([a, b])
        self.assertAlmostEqual(market, (a.price * 300.0 + b.price * 100.0) / 400.0)

    def test_market_power_emerges_from_fewer_competitors(self) -> None:
        from economy.goods import _allocate_sales

        def sales_after_raise(rivals: int) -> float:
            firms = [Company(f"food_{i}", "F", "food", 0.0, 10.0, price=10.0, inventory=1e6)
                     for i in range(rivals + 1)]
            firms[0].price = 11.0  # hausse de 10 %
            return _allocate_sales(firms, 300.0, "inventory", 1.5, 10.0)[firms[0].id]

        self.assertAlmostEqual(sales_after_raise(0), 300.0)  # le monopole ne perd aucun client
        self.assertLess(sales_after_raise(2), 100.0)          # en concurrence, il en perd

    def test_business_price_indices_follow_market_signals(self) -> None:
        from economy.firm_pricing import signal_index_change

        config = SimulationConfig(**self.CONFIG)
        self.assertAlmostEqual(signal_index_change(0.5, 0.0, 0.1, config), 0.01)
        self.assertAlmostEqual(signal_index_change(0.0, 0.5, 0.1, config), -0.01)
        # capacité inemployée mais secteur en pertes : pas de baisse, le prix remonte
        self.assertAlmostEqual(signal_index_change(0.0, 0.5, -0.05, config), 0.15 * 0.05)
        # sans demande non servie, la concurrence érode le prix ; pas un monopole
        self.assertAlmostEqual(signal_index_change(0.0, 0.0, 0.1, config, 1.0 / 3.0), -0.01)
        self.assertEqual(signal_index_change(0.0, 0.0, 0.1, config, 1.0), 0.0)

    def test_entry_follows_return_on_equity_against_cost_of_capital(self) -> None:
        from economy.finance import sector_entry_signals

        config = SimulationConfig(**self.CONFIG)

        def signal(profit: float) -> float:
            firms = [Company(f"food_{i}", "F", "food", 100_000.0, 10.0, previous_revenue=50_000.0,
                             previous_profit=profit) for i in range(3)]
            return sector_entry_signals(firms, config, 1400.0, ("food",), 0.004)["food"]

        # rendement des fonds propres 60 %/an contre un coût du capital de 12,8 %
        self.assertAlmostEqual(signal(5000.0), 12.0 * 5000.0 / 100_000.0 - (12.0 * 0.004 + 0.08))
        self.assertEqual(signal(500.0), 0.0)  # 6 %/an : sous le coût du capital

    def test_firm_signal_mode_keeps_money_and_bounded_prices(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=6, population_size=120, **self.CONFIG))
        for _ in range(150):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertLess(record.public_debt_reconciliation_error, 1e-3)
        ceiling = simulation._price_ceiling()
        for company in simulation.companies:
            if company.active and company.sector in ("food", "goods"):
                self.assertTrue(1.0 <= company.price <= ceiling)
                # −1 : marge pas encore fixée (entreprise qui n'a encore rien pu produire)
                self.assertTrue(company.price_markup >= 0.0 or company.price_markup == -1.0)
        self.assertTrue(math.isfinite(simulation.resources.tech_price_index))


class LocalExpectationsTests(unittest.TestCase):
    """Autonomie, phase 4 : anticipations locales et imparfaites (``expectations_mode``)."""

    LOCAL = dict()

    def test_demand_signal_is_the_firms_own_demand(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3, population_size=60, **self.LOCAL))
        firm = next(c for c in simulation.companies if c.sector == "food")
        firm.units_sold, firm.unmet_units, firm.demand_ema = 100.0, 20.0, 0.0
        # une forte pénurie sur tout le marché ne change rien à sa demande lissée
        simulation._update_firm_signals(10_000.0, 0.0)
        self.assertAlmostEqual(firm.demand_ema, 120.0)

    def test_production_follows_the_firms_own_forecast(self) -> None:
        config = SimulationConfig(**self.LOCAL)
        firm = Company("food_1", "F", "food", 1_000_000.0, 10.0, employees=["a", "b", "c"],
                       inventory=10.0, demand_forecast=50.0)
        resources = Resources(raw_materials=10_000.0, land_units=0, knowledge=100.0)
        # la demande agrégée transmise (10 000) est ignorée : 50 × 1,5 − 10 = 65 unités
        produce_food([firm], resources, config, 10_000.0)
        self.assertAlmostEqual(firm.units_produced, 65.0)

    def test_forecast_errors_come_from_a_dedicated_generator(self) -> None:
        from economy.expectations import update_demand_forecasts

        def forecasts(seed: int, error: float) -> list[float]:
            firms = [Company(f"food_{i}", "F", "food", 0.0, 10.0, demand_ema=100.0) for i in range(3)]
            update_demand_forecasts(firms, SimulationConfig(demand_forecast_error=error), random.Random(seed))
            return [c.demand_forecast for c in firms]

        self.assertEqual(forecasts(5, 0.05), forecasts(5, 0.05))
        self.assertNotEqual(forecasts(5, 0.05), forecasts(6, 0.05))
        self.assertEqual(forecasts(5, 0.0), [100.0, 100.0, 100.0])
        simulation = EconomySimulation(SimulationConfig(seed=3, population_size=60))
        self.assertIsNot(simulation.forecast_random, simulation.random)

    def test_promoters_plan_from_their_own_demand_and_ignore_rivals(self) -> None:
        from economy.expectations import update_housing_gaps
        from economy.models import ConstructionProject

        config = SimulationConfig(demand_forecast_error=0.0, **self.LOCAL)
        busy = Company("real_estate_1", "A", "real_estate", 0.0, 10.0, occupied_units=40,
                       property_inventory=40)
        rival = Company("real_estate_2", "B", "real_estate", 0.0, 10.0, occupied_units=40,
                        property_inventory=10,
                        construction_pipeline=[ConstructionProject(6, 1.0, 6) for _ in range(32)])
        empty = Company("real_estate_3", "C", "real_estate", 0.0, 10.0, occupied_units=10,
                        property_inventory=40)
        total = update_housing_gaps([busy, rival, empty], config, random.Random(1))
        # 40 occupés + 5 % de réserve : 2 logements à lancer, quels que soient les
        # chantiers du concurrent ; un promoteur aux logements vides ne construit pas
        self.assertEqual((busy.housing_gap, rival.housing_gap, empty.housing_gap), (2, 0, 0))
        self.assertEqual(total, 2)

    def test_cost_expectations_adapt_without_anchor_or_cap(self) -> None:
        from economy.expectations import update_cost_expectations

        config = SimulationConfig(**self.LOCAL)
        firm = Company("goods_1", "G", "goods", 0.0, 1000.0, wage_bill=1000.0, expenses=1000.0,
                       cost_reference=1000.0)
        for _ in range(240):
            firm.wage_offer *= 1.02  # salaires offerts en hausse de 2 %/mois
            update_cost_expectations([firm], config, 0.0, 0.0)
        self.assertAlmostEqual(firm.expected_cost_growth, 0.02, delta=0.001)
        self.assertGreater(firm.expected_cost_growth, 0.005)  # ancien plafond, retiré

    def test_local_mode_keeps_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=5, population_size=120, **self.LOCAL))
        for _ in range(150):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertLess(record.public_debt_reconciliation_error, 1e-3)
        forecasts = [c.demand_forecast for c in simulation.companies if c.active and c.sector == "food"]
        self.assertTrue(any(value > 0.0 for value in forecasts))


class BankLedgerFoundationTests(unittest.TestCase):
    """Fondation isolée : écritures atomiques et rapprochement exact en unités entières."""

    def test_hypotheque_inscrite_avant_vente_reelle(self) -> None:
        from economy.real_estate import buy_homes
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        buyer = sim.people[0]
        buyer.cash, buyer.income_this_turn = 300.0, 1000.0
        buyer.owns_home, buyer.mortgage_debt = False, 0.0
        seller = next(c for c in sim.companies if c.sector == "real_estate")
        seller.property_inventory = 1
        seller_cash = seller.cash
        original = sim._book_bank_loan
        def record_first(account, amount):
            self.assertEqual(seller.cash, seller_cash)
            self.assertEqual(seller.property_inventory, 1)
            self.assertEqual(buyer.mortgage_debt, 0.0)
            original(account, amount)
        with patch.object(sim, "_book_bank_loan", side_effect=record_first):
            purchases, loan = buy_homes([seller], [buyer], 1000.0, 700.0,
                                        sim.live, sim.credit_capacity(),
                                        mortgage_rate=0.004,
                                        mortgage_transaction=sim._mortgage_transaction)
        self.assertEqual(purchases, 1)
        self.assertGreater(loan, 0)
        self.assertAlmostEqual(sum(b.bank_loan_accounts.get(f"mortgage:{buyer.id}", 0)
                                   for b in sim.companies if b.sector == "bank"), loan)
        self.assertTrue(buyer.owns_home)

    def test_echec_inscription_hypotheque_annule_vente_reelle(self) -> None:
        from economy.real_estate import buy_homes
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        buyer = sim.people[0]
        buyer.cash, buyer.income_this_turn = 300.0, 1000.0
        buyer.owns_home, buyer.mortgage_debt = False, 0.0
        seller = next(c for c in sim.companies if c.sector == "real_estate")
        seller.property_inventory = 1
        original = sim._book_bank_loan
        banks = [b for b in sim.companies if b.sector == "bank"]
        before = [b.bank_loan_accounts.copy() for b in banks]
        seller_cash = seller.cash
        def fail_after_booking(account, amount):
            original(account, amount)
            raise RuntimeError("refus")
        with patch.object(sim, "_book_bank_loan", side_effect=fail_after_booking):
            with self.assertRaises(RuntimeError):
                buy_homes([seller], [buyer], 1000.0, 700.0, sim.live, sim.credit_capacity(),
                          mortgage_rate=0.004, mortgage_transaction=sim._mortgage_transaction)
        self.assertEqual([b.bank_loan_accounts for b in banks], before)
        self.assertEqual((buyer.cash, buyer.mortgage_debt, buyer.owns_home), (300.0, 0.0, False))
        self.assertEqual((seller.cash, seller.property_inventory), (seller_cash, 1))

    def test_echec_apres_paiement_restaure_hypotheque_et_logement(self) -> None:
        from economy.real_estate import buy_homes
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        buyer = sim.people[0]
        buyer.cash, buyer.income_this_turn = 300.0, 1000.0
        buyer.owns_home, buyer.mortgage_debt = False, 0.0
        seller = next(c for c in sim.companies if c.sector == "real_estate")
        seller.property_inventory = 1
        buyer_before, seller_before = buyer.__dict__.copy(), seller.__dict__.copy()
        banks = [b for b in sim.companies if b.sector == "bank"]
        accounts_before = [b.bank_loan_accounts.copy() for b in banks]
        with patch("economy.real_estate._mortgage_monthly_payment", side_effect=RuntimeError("panne")):
            with self.assertRaises(RuntimeError):
                buy_homes([seller], [buyer], 1000.0, 700.0, sim.live, sim.credit_capacity(),
                          mortgage_rate=0.004, mortgage_transaction=sim._mortgage_transaction)
        self.assertEqual(buyer.__dict__, buyer_before)
        self.assertEqual(seller.__dict__, seller_before)
        self.assertEqual([b.bank_loan_accounts for b in banks], accounts_before)

    def test_transfert_de_tresorerie_annule_si_credit_impossible(self) -> None:
        from economy.payments import transfer_cash
        class Recipient:
            def __init__(self):
                self._cash = 10.0
            @property
            def cash(self):
                return self._cash
            @cash.setter
            def cash(self, value):
                if value != self._cash:
                    raise ValueError("crédit refusé")
        payer = Company("payer", "Payeur", "food", 100.0, 1.0)
        recipient = Recipient()
        with self.assertRaises(ValueError):
            transfer_cash(payer, recipient, 25.0)
        self.assertEqual(payer.cash, 100.0)
        self.assertEqual(recipient.cash, 10.0)

    def test_contrats_d_un_meme_menage_reconciliables_separement(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        ledger.issue_loan("banque", "menage", 100, 100, contract="hypotheque")
        ledger.issue_loan("banque", "menage", 50, 50, contract="consommation")
        ledger.repay_loan("banque", "menage", 30, contract="consommation")
        self.assertEqual(ledger.balances[("banque", "loan_asset:hypotheque", "menage")], 100)
        self.assertEqual(ledger.balances[("banque", "loan_asset:consommation", "menage")], 20)
        ledger.reconciliation_check()
        before = ledger.balances.copy()
        with self.assertRaises(AccountingError):
            ledger.repay_loan("banque", "menage", 21, contract="consommation")
        self.assertEqual(ledger.balances, before)

    def test_rapprochement_exact_des_comptes_existants(self) -> None:
        from types import SimpleNamespace
        from economy.records import reconciliation_check
        bank = SimpleNamespace(sector="bank", active=True, bank_loan_accounts={"firme": 100.0})
        firm = SimpleNamespace(id="firme", sector="food", active=True, debt=100.0)
        sim = SimpleNamespace(companies=[bank, firm], people=[])
        reconciliation_check(sim)
        bank.bank_loan_accounts["firme"] = math.nextafter(100.0, math.inf)
        with self.assertRaises(ArithmeticError):
            reconciliation_check(sim)

    def test_ouverture_en_centimes_conserve_les_totaux(self) -> None:
        from economy.bank_opening import cents, initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        ledger = initial_bank_ledger(sim)
        ledger.reconciliation_check()
        def total(kind):
            return sum(value for (_, account_kind, _), value in ledger.balances.items()
                       if account_kind == kind)
        self.assertEqual(total("currency"), cents(sim.ledger.currency))
        self.assertEqual(total("reserves"), cents(sim.central_bank.reserves))
        self.assertEqual(total("loan_asset"), cents(sum(
            c.debt for c in sim.companies if c.active and c.sector != "bank")))
        self.assertTrue(all(type(value) is int for value in ledger.balances.values()))

    def test_ouverture_reproductible_sur_les_cinq_graines(self) -> None:
        from economy.bank_opening import initial_bank_ledger
        for seed in (1001, 1002, 1003, 1004, 1005):
            sim = EconomySimulation(SimulationConfig(seed=seed, warmup_months=0))
            first = initial_bank_ledger(sim)
            second = initial_bank_ledger(sim)
            self.assertEqual(first.balances, second.balances)
            first.reconciliation_check()

    def test_ouverture_rejette_une_creance_incoherente(self) -> None:
        from economy.bank_ledger import AccountingError
        from economy.bank_opening import initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        bank = next(c for c in sim.companies if c.sector == "bank")
        account = next(iter(bank.bank_loan_accounts))
        bank.bank_loan_accounts[account] += 10.0
        with self.assertRaises(AccountingError):
            initial_bank_ledger(sim)

    def test_ouverture_rejette_un_etat_deja_simule(self) -> None:
        from economy.bank_ledger import AccountingError
        from economy.bank_opening import initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        sim.step()
        with self.assertRaises(AccountingError):
            initial_bank_ledger(sim)

    def test_ouverture_separe_les_prets_hypothecaire_et_consommation(self) -> None:
        from economy.bank_opening import initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        person = sim.people[0]
        person.mortgage_debt = 300.0
        person.consumer_debt = 200.0
        sim._book_bank_loan(f"mortgage:{person.id}", 300.0)
        sim._book_bank_loan(f"consumer:{person.id}", 200.0)
        sim.central_bank.reserves -= 500.0
        ledger = initial_bank_ledger(sim)
        ledger.reconciliation_check()
        for kind, total in (("loan_asset:mortgage", 30000), ("loan_asset:consumer", 20000)):
            self.assertEqual(sum(v for (_, k, o), v in ledger.balances.items()
                                 if k == kind and o == f"person:{person.id}"), total)

    def test_depot_et_retrait_des_especes_sans_creation_monetaire(self) -> None:
        from economy.bank_opening import initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        ledger = initial_bank_ledger(sim)
        owner = f"person:{sim.people[0].id}"
        bank = f"bank:{next(c.id for c in sim.companies if c.sector == 'bank')}"
        original = ledger.balances[(owner, "currency", "central_bank")]
        ledger.exchange_currency(owner, bank, 100, deposit=True)
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[(owner, "currency", "central_bank")], original - 100)
        self.assertEqual(ledger.balances[(owner, "deposit_asset", bank)], 100)
        ledger.exchange_currency(owner, bank, 100, deposit=False)
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[(owner, "currency", "central_bank")], original)
        self.assertEqual(ledger.balances[(owner, "deposit_asset", bank)], 0)

    def test_refus_de_pret_ne_modifie_aucun_compte(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        with self.assertRaises(AccountingError):
            ledger.issue_loan("banque", "menage", 101, 100)
        self.assertEqual(ledger.balances, {})
        self.assertEqual(ledger.journal, [])

    def test_pret_et_transfert_interbancaire(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger()
        ledger.post([(("a", "reserves", "central_bank"), 1000),
                     (("a", "equity", "initial"), -1000),
                     (("central_bank", "reserve_liability", "a"), -1000),
                     (("central_bank", "opening_asset", "initial"), 1000)])
        ledger.issue_loan("a", "acheteur", 300, 300)
        ledger.transfer("acheteur", "a", "vendeur", "b", 200)
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("acheteur", "deposit_asset", "a")], 100)
        self.assertEqual(ledger.balances[("vendeur", "deposit_asset", "b")], 200)
        self.assertEqual(ledger.balances[("a", "reserves", "central_bank")], 800)
        self.assertEqual(ledger.balances[("b", "reserves", "central_bank")], 200)

    def test_paiement_salaire_et_impot_avec_comptes_explicites(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger()
        ledger.issue_loan("a", "entreprise", 300, 300)
        ledger.transfer("entreprise", "a", "menage", "a", 200,
                        sender_account="wage_expense", receiver_account="wage_income")
        ledger.transfer("menage", "a", "etat", "a", 40,
                        sender_account="tax_expense", receiver_account="tax_income")
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("entreprise", "wage_expense", "menage")], 200)
        self.assertEqual(ledger.balances[("menage", "deposit_asset", "a")], 160)
        self.assertEqual(ledger.balances[("etat", "tax_income", "menage")], -40)

    def test_transfert_sans_reserves_est_entierement_annule(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        ledger.issue_loan("a", "acheteur", 300, 300)
        before = ledger.balances.copy()
        with self.assertRaises(AccountingError):
            ledger.transfer("acheteur", "a", "vendeur", "b", 200)
        self.assertEqual(ledger.balances, before)
        self.assertEqual(len(ledger.journal), 1)

    def test_detecte_une_rupture_meme_d_une_unite(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        ledger.issue_loan("a", "acheteur", 300, 300)
        ledger.balances[("a", "loan_asset", "acheteur")] += 1
        with self.assertRaises(AccountingError):
            ledger.reconciliation_check()

    def test_paiement_finance_sans_reglement_est_annule(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        with self.assertRaises(AccountingError):
            ledger.financed_payment("a", "acheteur", "vendeur", "b", 100, 100, 100)
        self.assertEqual(ledger.balances, {})
        self.assertEqual(ledger.journal, [])

    def test_paiement_finance_reussi_rapproche_dette_et_depot(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger()
        ledger.post([(("a", "reserves", "central_bank"), 100),
                     (("a", "equity", "initial"), -100),
                     (("central_bank", "reserve_liability", "a"), -100),
                     (("central_bank", "opening_asset", "initial"), 100)])
        ledger.financed_payment("a", "acheteur", "vendeur", "b", 100, 100, 100)
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("a", "loan_asset", "acheteur")], 100)
        self.assertEqual(ledger.balances[("vendeur", "deposit_asset", "b")], 100)

    def test_remboursement_detruit_depot_et_creance(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        ledger.issue_loan("a", "client", 300, 300)
        ledger.repay_loan("a", "client", 120)
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("client", "deposit_asset", "a")], 180)
        self.assertEqual(ledger.balances[("a", "loan_asset", "client")], 180)
        before = ledger.balances.copy()
        with self.assertRaises(AccountingError):
            ledger.repay_loan("a", "client", 181)
        self.assertEqual(ledger.balances, before)

    def test_perte_bancaire_ne_detruit_pas_le_depot(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger()
        ledger.issue_loan("banque", "client", 300, 300, contract="consommation")
        ledger.write_off_loan("banque", "client", 100, contract="consommation")
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("banque", "loan_asset:consommation", "client")], 200)
        self.assertEqual(ledger.balances[("client", "deposit_asset", "banque")], 300)
        self.assertEqual(ledger.balances[("banque", "credit_loss", "client")], 100)

    def test_refuse_ecriture_equilibree_globalement_mais_pas_par_agent(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        with self.assertRaises(AccountingError):
            ledger.post([(("a", "equity", "initial"), -100),
                         (("b", "equity", "initial"), 100)])
        self.assertEqual(ledger.balances, {})

    def test_refuse_compte_depot_sans_contrepartie(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger()
        with self.assertRaises(AccountingError):
            ledger.post([(("client", "deposit_asset", "a"), 100),
                         (("client", "equity", "initial"), -100)])
        self.assertEqual(ledger.balances, {})

    def test_repartition_exacte_et_deterministe_des_unites(self) -> None:
        from economy.bank_ledger import AccountingError, allocate_units
        parts = allocate_units(100, {"c": 1.0, "a": 1.0, "b": 1.0})
        self.assertEqual(parts, {"c": 33, "a": 34, "b": 33})
        self.assertEqual(sum(parts.values()), 100)
        self.assertEqual(allocate_units(0, {"a": 0}), {"a": 0})
        with self.assertRaises(AccountingError):
            allocate_units(1, {"a": 0})
        with self.assertRaises(AccountingError):
            allocate_units(100, {"a": float("nan")})

    def test_remboursement_depuis_une_autre_banque(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger()
        ledger.post([(("a", "reserves", "central_bank"), 200),
                     (("a", "equity", "initial"), -200),
                     (("central_bank", "reserve_liability", "a"), -200),
                     (("central_bank", "opening_asset", "initial"), 200)])
        ledger.issue_loan("a", "client", 100, 100)
        ledger.transfer("client", "a", "client", "b", 100)
        ledger.repay_loan("a", "client", 70, deposit_bank="b")
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("a", "loan_asset", "client")], 30)
        self.assertEqual(ledger.balances[("client", "deposit_asset", "b")], 30)
        self.assertEqual(ledger.balances[("b", "reserves", "central_bank")], 30)

    def test_achat_immobilier_atomique_livre_ou_annule(self) -> None:
        from economy.bank_ledger import AccountingError, BankLedger
        ledger = BankLedger(properties={"logement": "vendeur"})
        ledger.post([(("a", "reserves", "central_bank"), 100),
                     (("a", "equity", "initial"), -100),
                     (("central_bank", "reserve_liability", "a"), -100),
                     (("central_bank", "opening_asset", "initial"), 100)])
        before = ledger.balances.copy()
        with self.assertRaises(AccountingError):
            ledger.purchase_property("logement", "a", "client", "vendeur", "b", 100, 99, 100)
        self.assertEqual(ledger.balances, before)
        self.assertEqual(ledger.properties["logement"], "vendeur")
        ledger.purchase_property("logement", "a", "client", "vendeur", "b", 100, 100, 100)
        self.assertEqual(ledger.properties["logement"], "client")
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("a", "loan_asset", "client")], 100)
        with self.assertRaises(AccountingError):
            ledger.purchase_property("logement", "a", "autre", "vendeur", "b", 100, 100, 100)

    def test_achat_immobilier_ouvre_un_contrat_distinct(self) -> None:
        from economy.bank_ledger import BankLedger
        ledger = BankLedger(properties={"logement": "vendeur"})
        ledger.purchase_property("logement", "a", "client", "vendeur", "a",
                                 100, 100, 100, contract="hypotheque")
        ledger.issue_loan("a", "client", 50, 50, contract="consommation")
        ledger.reconciliation_check()
        self.assertEqual(ledger.balances[("a", "loan_asset:hypotheque", "client")], 100)
        self.assertEqual(ledger.balances[("a", "loan_asset:consommation", "client")], 50)

    def test_achat_refuse_annule_aussi_le_depot_d_especes(self) -> None:
        from economy.bank_ledger import AccountingError
        from economy.bank_opening import initial_bank_ledger
        sim = EconomySimulation(SimulationConfig(seed=1001, warmup_months=0))
        ledger = initial_bank_ledger(sim)
        buyer = f"person:{sim.people[0].id}"
        bank = f"bank:{next(c.id for c in sim.companies if c.sector == 'bank')}"
        ledger.properties["maison"] = "vendeur"
        before = ledger.balances.copy()
        journal_length = len(ledger.journal)
        with self.assertRaises(AccountingError):
            ledger.purchase_property("maison", bank, buyer, "vendeur", bank,
                                     500, 0, 600, cash_deposit=100)
        self.assertEqual(ledger.balances, before)
        self.assertEqual(len(ledger.journal), journal_length)
        self.assertEqual(ledger.properties["maison"], "vendeur")






class HouseholdSpendingTests(unittest.TestCase):
    """R10 : budget sur le revenu attendu, réserve de précaution propre à chaque ménage."""

    def test_expected_income_and_risk_follow_the_households_own_history(self) -> None:
        from economy.goods import update_income_expectations

        config = SimulationConfig(income_memory_months=10.0)
        person = Person("p", "Ménage", 0.0, income_this_turn=2000.0, employer_id="firm")
        update_income_expectations([person], config)
        self.assertEqual((person.expected_income, person.unemployment_risk), (2000.0, 0.0))
        person.employer_id, person.income_this_turn = None, 500.0  # licencié : allocation
        update_income_expectations([person], config)
        self.assertAlmostEqual(person.expected_income, 2000.0 + 0.1 * (500.0 - 2000.0))
        self.assertAlmostEqual(person.unemployment_risk, 0.1)

    def test_a_transitory_income_drop_moves_the_budget_less(self) -> None:
        from economy.goods import household_budget, update_income_expectations

        config = SimulationConfig()
        person = Person("p", "Ménage", 0.0, income_this_turn=1850.0,
                        expected_income=1850.0, income_months=12)
        before = household_budget(person, 0.3, 0.1, config)
        person.income_this_turn = 500.0
        update_income_expectations([person], config)
        after = household_budget(person, 0.3, 0.1, config)
        self.assertLess(after, before)
        self.assertGreater(after, 500.0 * 0.3)

    def test_the_reserve_depends_on_own_risk_and_tolerance(self) -> None:
        from economy.goods import household_budget, precautionary_reserve

        config = SimulationConfig()
        safe = Person("a", "Sans chômage", 10_000.0, expected_income=2000.0, unemployment_risk=0.0)
        cautious = Person("b", "Prudent", 10_000.0, expected_income=2000.0, unemployment_risk=0.25,
                          risk_tolerance=0.2)
        bold = Person("c", "Audacieux", 10_000.0, expected_income=2000.0, unemployment_risk=0.25,
                      risk_tolerance=0.8)
        self.assertEqual(precautionary_reserve(safe, config), 0.0)
        self.assertAlmostEqual(precautionary_reserve(cautious, config), 12.0 * 0.25 * 2000.0 * 0.8)
        budgets = [household_budget(p, 0.3, 0.1, config) for p in (safe, bold, cautious)]
        self.assertTrue(budgets[0] > budgets[1] > budgets[2])

    def test_income_risk_keeps_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(
            seed=7, population_size=120,
        ))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertTrue(all(person.cash >= -1e-6 for person in simulation.people))




class StableAutonomyModesTests(unittest.TestCase):
    """Mission « autonomie stable » : contrats locaux des comportements retenus."""

    def test_idle_firms_and_entrants_quote_the_going_wage(self) -> None:
        from economy.finance import _entrant_wage_offer

        def firms():
            employer = Company("goods_1", "Employeur", "goods", 50_000.0, 2000.0, employees=["a", "b", "c"],
                               staffing_need=3, target_employees=3, revenue=10_000.0, revenue_ema=10_000.0)
            shell = Company("goods_2", "Coquille", "goods", 0.0, 1.0, staffing_need=5, target_employees=3)
            return employer, shell

        config = SimulationConfig()
        employer, shell = firms()
        adjust_profit_sharing_wages([employer, shell], [], 1500.0, 0.0, 0.70, 0.03, {}, config)
        self.assertGreaterEqual(shell.wage_offer, employer.wage_offer * 0.99)
        employer, shell = firms()
        self.assertEqual(_entrant_wage_offer([employer, shell], "goods", 1500.0, config), 2000.0)

    def test_delivered_settlement_pays_only_staffed_suppliers(self) -> None:
        buyer = Company("food_1", "Ferme", "food", 10_000.0, 1000.0)
        staffed = Company("services_1", "Prestataire", "services", 0.0, 1000.0, employees=["s"])
        shell = Company("services_2", "Coquille", "services", 0.0, 1000.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        pay_business_suppliers(buyer, 1000.0, {"services": 1.0}, [buyer, staffed, shell], resources)
        self.assertEqual(shell.revenue, 0.0)
        self.assertAlmostEqual(staffed.revenue, 1000.0)
        # aucun fournisseur employeur : la commande n'est ni servie ni payée
        buyer = Company("food_1", "Ferme", "food", 10_000.0, 1000.0)
        shell = Company("services_2", "Coquille", "services", 0.0, 1000.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        paid = pay_business_suppliers(buyer, 1000.0, {"services": 1.0}, [buyer, shell], resources)
        self.assertEqual((paid, buyer.cash, resources.market_cash, shell.revenue), (0.0, 10_000.0, 0.0, 0.0))

    def test_an_unserved_household_purchase_is_not_paid(self) -> None:
        from economy.intersector import pay_suppliers_from_household

        person = Person("p", "Ménage", 1000.0)
        shell = Company("services_2", "Coquille", "services", 0.0, 1000.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        paid = pay_suppliers_from_household(person, 300.0, {"services": 1.0}, [shell], resources)
        self.assertEqual((paid, person.cash, resources.market_cash), (0.0, 1000.0, 0.0))

    def test_commitment_budget_targets_no_debt(self) -> None:
        from economy import diagnostics

        diagnostics.start()
        try:
            simulation = EconomySimulation(SimulationConfig(seed=7, population_size=120))
            for _ in range(36):
                simulation.step()
                self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            flows = diagnostics.snapshot()["flows"]
        finally:
            diagnostics.stop()
        self.assertFalse(hasattr(simulation.public, "tax_adjust"))  # aucune règle fiscale
        for name in ("emprunt_cible_dette", "investissement_keynesien_cible_dette",
                     "excedent_public_redistribue", "transferts_retenus_cible_dette"):
            self.assertNotIn(name, flows)
        self.assertGreater(simulation.public.spending, 0.0)

    def test_credibility_is_learned_and_expectations_are_unbounded(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60))
        for _ in range(14):
            simulation.step()
        self.assertTrue(0.0 <= simulation.central_bank_credibility <= 1.0)
        simulation.central_bank_credibility, simulation.expected_inflation = 0.0, 0.02
        self.assertEqual(simulation._expected_price_growth(), 0.02)  # au-delà de l'ancienne borne 0,5 %
        simulation.central_bank_credibility = 1.0
        self.assertEqual(simulation._expected_price_growth(), simulation.live.inflation_target)

    def test_bank_buffer_follows_cycle_losses_not_recent_ones(self) -> None:
        from economy.credit_risk import voluntary_buffer

        bank = Company("bank_1", "Banque", "bank", 0.0, 1000.0, loss_rate_cycle=0.001)
        cycle = SimulationConfig()
        self.assertAlmostEqual(voluntary_buffer(bank, cycle), cycle.bank_buffer_loss_gain * 12.0 * 0.001)
        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60))
        simulation.step()
        banks = [c for c in simulation.companies if c.sector == "bank"]
        banks[0].default_loss += 50_000.0  # perte nouvelle
        simulation._update_bank_loss_rates()
        self.assertGreater(banks[0].loss_rate_cycle, 0.0)

    def test_an_entrant_starts_at_the_market_price(self) -> None:
        market = {"food": 640.0, "goods": 512.0}
        config = SimulationConfig(seed=7, population_size=60)
        simulation, founder = BankEntryAndCapitalTests()._prepare(config)
        goods = next(c for c in simulation.companies if c.sector == "goods")
        goods.previous_revenue, goods.previous_profit = 500_000.0, 300_000.0
        for company in simulation.companies:
            if company.sector == "bank":
                company.previous_revenue = company.previous_profit = 0.0
        created = create_household_startups(
            simulation.companies, [founder], simulation.month, simulation.credit_capacity(),
            config, BankEntryAndCapitalTests.Draws(), housing_shortage=0,
            wage_reference=config.starting_wage, market_prices=market,
        )[0]
        prices = [(c.sector, c.price) for c in created if c.sector in ("food", "goods")]
        self.assertTrue(prices)
        sector, price = prices[0]
        self.assertEqual(price, market[sector])

    def test_a_sector_of_idle_shells_refusing_orders_attracts_entry(self) -> None:
        from economy.finance import sector_entry_signals

        config = SimulationConfig()
        shells = [Company(f"technology_{i}", "Coquille", "technology", 1000.0, 1500.0) for i in (1, 2)]
        sectors = ("technology",)
        self.assertEqual(sector_entry_signals(shells, config, 1500.0, sectors)["technology"], 0.0)
        self.assertEqual(sector_entry_signals(shells, config, 1500.0, sectors,
                                              refused_orders={"technology": 5000.0})["technology"], 1.0)
        shells[0].employees = ["e"]  # un employeur existe : règle ordinaire
        self.assertNotEqual(sector_entry_signals(shells, config, 1500.0, sectors,
                                                 refused_orders={"technology": 5000.0})["technology"], 1.0)
        # commandes refusées enregistrées par le règlement « delivered »
        buyer = Company("food_1", "Ferme", "food", 10_000.0, 1000.0)
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        pay_business_suppliers(buyer, 1000.0, {"technology": 1.0}, [buyer] + shells[1:], resources)
        self.assertAlmostEqual(resources.refused_orders["technology"], 1000.0)

    def test_households_can_let_their_vacant_homes(self) -> None:
        promoter = Company("real_estate_1", "Bailleur", "real_estate", 0.0, 1000.0, property_inventory=1)
        settled = Person("a", "Locataire en place", 5000.0, income_this_turn=3000.0, rental_owner_id="real_estate_1")
        owner = Person("b", "Propriétaire", 0.0, owns_home=True, investment_homes=1)
        newcomer = Person("c", "Sans logement", 5000.0, income_this_turn=3000.0)
        received = allocate_housing_and_collect_rent([promoter], [settled, owner, newcomer], 500.0, 0.3)
        self.assertIsNotNone(newcomer.rental_owner_id)
        self.assertEqual(owner.cash, 500.0)
        self.assertAlmostEqual(received, 500.0 * 2)

    def test_no_technical_price_bound(self) -> None:
        free = EconomySimulation(SimulationConfig(seed=7, population_size=60))
        self.assertEqual(free._price_ceiling(), float("inf"))

    def test_energy_scarcity_counts_all_unmet_orders(self) -> None:
        import dataclasses

        simulation = EconomySimulation(SimulationConfig(seed=7, population_size=60))
        simulation.step()
        last = simulation.history[-1]
        # toute l'offre (100 unités) est livrée ; autant de demande n'a pas pu être payée (aucun refus)
        simulation.history[-1] = dataclasses.replace(
            last, energy_supply=100.0, energy_delivered=100.0, energy_unmet=100.0,
        )
        simulation.resources.previous_energy_refused = 0.0
        for company in simulation.companies:
            if company.sector == "energy":
                company.previous_revenue = 1000.0  # trois producteurs à parts égales
        before = simulation.resources.energy_price_index
        simulation._signal_business_prices()
        # commandes impayables lues comme une rareté
        self.assertGreater(simulation.resources.energy_price_index / before - 1.0, 0.0)

    def test_stable_modes_keep_money_and_reconciliations(self) -> None:
        simulation = EconomySimulation(SimulationConfig(
            seed=7, population_size=120,
        ))
        for _ in range(80):
            record = simulation.step()
            self.assertAlmostEqual(simulation.ledger.residual(simulation), 0.0, delta=1.0)
            self.assertLess(record.loan_reconciliation_error, 1e-3)
            self.assertLess(record.public_debt_reconciliation_error, 1e-3)
        self.assertEqual(simulation.resources.market_cash, 0.0)


if __name__ == "__main__":
    unittest.main()
