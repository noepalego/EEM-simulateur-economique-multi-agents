import unittest
import random
from unittest.mock import patch

from economy.consolidation import consolidate_market
from economy.business import (
    apply_competitive_pricing,
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
    collect_income_taxes_and_pay_benefits,
    pay_public_transfers,
)
from economy.labor import allocate_and_pay_labor, workforce_targets
from economy.labor import (
    effective_worker_count,
    hire_service_workers,
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
    trade_infrastructure,
)
from economy.intersector import pay_business_suppliers, settle_operating_inputs
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
        self.assertEqual(len(self.simulation.company_rows()), 14)  # 7 secteurs x 2
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
        # 60 % de 80 ménages, à l'arrondi près (l'extraction impose un plancher d'effectif)
        self.assertLessEqual(abs(total_jobs - 48), 3)
        simulation = EconomySimulation(config)
        record = simulation.step()
        self.assertLessEqual(abs(record.employed_count - total_jobs), 3)
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

    def test_temporary_food_demand_event_increases_market_price(self) -> None:
        config = SimulationConfig(seed=23)
        regular_market = EconomySimulation(config)
        shocked_market = EconomySimulation(config)

        with patch.object(
            regular_market,
            "_generate_event",
            return_value=("Aucun événement", None),
        ):
            regular_market.step()
        with patch.object(
            shocked_market,
            "_generate_event",
            return_value=("Pic de demande alimentaire", "food"),
        ):
            shocked_market.step()

        self.assertGreater(shocked_market.food_price, regular_market.food_price)

    def test_household_demand_is_limited_by_budget_not_a_fixed_quantity(self) -> None:
        simulation = EconomySimulation(SimulationConfig(population_size=1))
        household = simulation.people[0]
        household.cash = 5000.0
        household.income_this_turn = 1400.0

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
        expected_food_demand = (
            food_budget
            * household.consumption_propensity
            / (simulation.food_price * 1.16)
        )
        expected_goods_demand = (
            goods_budget
            * household.consumption_propensity
            / (simulation.goods_price * 1.16)
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
            "goods_buyer", "Acheteur", 5000.0, income_this_turn=1400.0
        )
        goods_sold, _, _ = sell_consumer_goods(
            [goods_firm],
            [household],
            market_price=100.0,
            config=SimulationConfig(
                goods_income_budget_share=1.0, goods_cash_draw_rate=0.0
            ),
        )
        self.assertAlmostEqual(goods_sold, 1400.0 / (100.0 * 1.16))

    def test_market_price_discovery_uses_current_production_not_buffer_stock(self) -> None:
        simulation = EconomySimulation()

        with patch(
            "economy.simulation.update_food_price",
            wraps=update_food_price,
        ) as price_update:
            record = simulation.step()

        food_supply_used = price_update.call_args_list[-2].args[2]
        goods_supply_used = price_update.call_args_list[-1].args[2]
        self.assertAlmostEqual(food_supply_used, record.food_produced)
        self.assertAlmostEqual(goods_supply_used, record.goods_produced)

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

        self.assertAlmostEqual(sold, 2.0 * 120.0 / 116.0)
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

                self.assertAlmostEqual(revenue, 100.0)
                self.assertAlmostEqual(company.cash, 100.0)
                self.assertAlmostEqual(household.cash, 0.0)
                self.assertAlmostEqual(household.expenses_this_turn, revenue)
                self.assertAlmostEqual(sold, 0.5)

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

        produced = produce_food([firm], resources, config, market_demand=20.0)
        sold, _, _ = sell_food([firm], [household], 100.0, config)

        self.assertEqual(produced, 10.0)
        self.assertGreater(sold, 0.0)
        self.assertGreater(firm.inventory, 0.0)
        self.assertAlmostEqual(firm.units_produced, 10.0)

    def test_profitable_firm_invests_only_when_demand_is_unmet(self) -> None:
        firm = Company(
            "food",
            "Producteur rentable",
            "food",
            50000.0,
            1000.0,
            employees=["worker"],
            profit=5000.0,
        )
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            capacity_expansion_reinvestment_rate=0.30,
            productive_capacity_unit_cost=2500.0,
        )

        invested = invest_in_production_capacity(
            [firm], {"food": 10.0}, config, resources
        )

        self.assertEqual(invested, 1500.0)
        self.assertAlmostEqual(firm.production_capacity, 0.6)
        self.assertAlmostEqual(firm.productive_capital_value, 1500.0)
        self.assertAlmostEqual(resources.market_cash, 1500.0)
        self.assertEqual(
            invest_in_production_capacity(
                [firm], {"food": 0.0}, config, resources
            ),
            0.0,
        )

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
            "p1", "Ménage 1", 500.0, income_this_turn=1000.0
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
        self.assertAlmostEqual(sold_with_budget, 500.0 / (100.0 * 1.16))

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
            5.0 * 1000.0 / (100.0 * 1.16) / 2.0,
        )
        self.assertGreater(
            cheap_firm_sales(3.0), cheap_firm_sales(0.0)
        )

    def test_inventory_discount_lowers_price_only_when_stock_is_excessive(self) -> None:
        company = Company(
            "food",
            "Producteur",
            "food",
            1000.0,
            100.0,
            price=100.0,
            inventory=48.0,
            employees=["worker"],
        )

        apply_competitive_pricing(
            [company],
            food_index=100.0,
            goods_index=100.0,
            competition_strength=0.0,
            inventory_discount_strength=0.10,
        )

        self.assertAlmostEqual(company.price, 90.0)

    def test_food_production_is_shared_between_competing_producers(self) -> None:
        firms = [
            Company(
                f"food_{index}",
                f"Producteur {index}",
                "food",
                10000.0,
                100.0,
                employees=["p1", "p2"],
            )
            for index in (1, 2)
        ]
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=100.0)

        produce_food(
            firms,
            resources,
            SimulationConfig(food_input_cost=1.0, raw_material_unit_price=1.0),
            market_demand=80.0,
        )

        self.assertAlmostEqual(firms[0].inventory, 48.0)
        self.assertAlmostEqual(firms[1].inventory, 48.0)

    def test_consumer_price_index_uses_configured_market_weights(self) -> None:
        simulation = EconomySimulation(
            SimulationConfig(
                cpi_food_weight=0.5,
                cpi_goods_weight=0.3,
                cpi_rent_weight=0.2,
            )
        )
        simulation.food_price *= 2.0
        simulation.goods_price *= 1.5
        simulation.rent *= 0.5

        self.assertAlmostEqual(simulation._consumer_price_index(), 155.0)

    def test_policy_rate_moves_with_inflation_and_unemployment(self) -> None:
        config = SimulationConfig(
            loan_interest_rate=0.004,
            inflation_target=0.002,
            policy_rate_inflation_sensitivity=0.04,
            policy_rate_unemployment_sensitivity=0.004,
        )
        simulation = EconomySimulation(config)
        initial_rate = simulation.policy_rate

        simulation._adjust_policy_rate(inflation_rate=0.10, unemployment_rate=0.0)
        inflation_response = simulation.policy_rate
        simulation._adjust_policy_rate(inflation_rate=0.002, unemployment_rate=0.50)

        self.assertGreater(inflation_response, initial_rate)
        self.assertLess(simulation.policy_rate, inflation_response)
        self.assertGreaterEqual(simulation.policy_rate, 0.0001)

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

    def test_wage_reference_rises_with_productivity_and_tight_labor_market(self) -> None:
        def reference_after(unemployment: float, productivity_gain: float) -> float:
            simulation = EconomySimulation(
                SimulationConfig(seed=5, max_monthly_wage_change=0.05)
            )
            for _ in range(3):
                simulation.step()
            simulation.history[-1].unemployment = unemployment
            before = simulation.wage_reference
            simulation._smoothed_productivity = (
                simulation._productivity_index() / (1.0 + productivity_gain)
            )
            simulation._update_wage_reference()
            return simulation.wage_reference / before - 1.0

        tight = reference_after(0.0, 0.0)
        slack = reference_after(0.20, 0.0)
        productive = reference_after(0.0, 0.02)
        self.assertGreater(tight, slack)
        self.assertGreater(productive, tight)

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
        _, paid, balance = collect_income_taxes_and_pay_benefits(
            people,
            government_balance=150.0,
            tax_rate=0.0,
            unemployment_benefit=100.0,
            unemployment_rate=0.5,
            price_index=100.0,
            cycle_sensitivity=1.0,
        )

        self.assertAlmostEqual(paid, 150.0)
        self.assertEqual(balance, 0.0)
        self.assertAlmostEqual(people[0].income_this_turn, 75.0)
        self.assertAlmostEqual(people[1].income_this_turn, 75.0)

    def test_progressive_income_tax_only_applies_higher_rate_above_threshold(self) -> None:
        people = [
            Person("p1", "Sous le seuil", 5000.0, income_this_turn=2000.0),
            Person("p2", "Au-dessus du seuil", 5000.0, income_this_turn=4000.0),
        ]

        taxes, benefits, balance = collect_income_taxes_and_pay_benefits(
            people,
            government_balance=0.0,
            tax_rate=0.10,
            unemployment_benefit=0.0,
            progressive_threshold=3000.0,
            top_marginal_rate=0.30,
        )

        self.assertAlmostEqual(taxes, 800.0)
        self.assertEqual(benefits, 0.0)
        self.assertAlmostEqual(balance, 800.0)
        self.assertAlmostEqual(people[0].taxes_paid, 200.0)
        self.assertAlmostEqual(people[1].taxes_paid, 600.0)

    def test_supplier_fund_pays_aggregate_service_workers(self) -> None:
        people = [Person(f"p{index}", f"Ménage {index}", 0.0) for index in range(3)]
        resources = Resources(
            raw_materials=0.0,
            land_units=0,
            knowledge=0.0,
            market_cash=5000.0,
        )

        workers, payroll = hire_service_workers(
            people, resources, monthly_wage=1000.0, payout_rate=0.4
        )

        # 40 % de 5 000 = 2 000 € de recettes reversées : deux salaires de 1 000 €
        self.assertEqual(workers, 2)
        self.assertAlmostEqual(payroll, 2000.0)
        self.assertAlmostEqual(resources.market_cash, 3000.0)
        self.assertEqual(resources.supplier_employment, 2)
        self.assertEqual(sum(person.cash for person in people), 2000.0)
        self.assertEqual(sum(person.employer_id == "services" for person in people), 2)
        self.assertIsNone(people[2].employer_id)

    def test_upstream_sector_pays_out_all_receipts_and_employment_follows_them(self) -> None:
        def run(receipts: float) -> tuple[int, float, float]:
            people = [Person(f"p{i}", f"Ménage {i}", 0.0) for i in range(50)]
            resources = Resources(
                raw_materials=0.0, land_units=0, knowledge=0.0, market_cash=receipts
            )
            workers, payroll = hire_service_workers(
                people, resources, monthly_wage=1000.0, payout_rate=1.0
            )
            return workers, payroll, resources.market_cash

        small = run(10_000.0)
        large = run(30_000.0)
        self.assertEqual(small[0], 10)
        self.assertEqual(large[0], 30)  # l'effectif suit les recettes
        self.assertAlmostEqual(small[2], 0.0)  # aucun stock durable
        self.assertAlmostEqual(large[2], 0.0)

    def test_upstream_surplus_goes_to_households_when_no_labor_is_left(self) -> None:
        employed = [
            Person(f"e{i}", f"Salarié {i}", 0.0, employer_id="firm", current_wage=1000.0)
            for i in range(4)
        ]
        resources = Resources(
            raw_materials=0.0, land_units=0, knowledge=0.0, market_cash=8000.0
        )
        workers, payroll = hire_service_workers(employed, resources, 1000.0, 1.0)

        self.assertEqual(workers, 0)
        self.assertAlmostEqual(resources.market_cash, 0.0)  # rien ne s'accumule
        self.assertAlmostEqual(sum(p.cash for p in employed), 8000.0)  # rien de créé

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
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            housing_price=120000.0, housing_maintenance_rate=0.001
        )

        paid = maintain_housing([landlord], [tenant], resources, config)

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
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        config = SimulationConfig(
            food_input_cost=20.0, inventory_holding_cost_rate=0.01
        )

        paid = charge_inventory_holding_cost([company], resources, config)

        self.assertAlmostEqual(paid, 2.0)
        self.assertAlmostEqual(company.cash, 9998.0)
        self.assertAlmostEqual(resources.market_cash, 2.0)

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

    def test_loss_making_company_cuts_its_wage_offer(self) -> None:
        company = Company(
            "loss",
            "Entreprise déficitaire",
            "food",
            10000.0,
            1000.0,
            revenue=10000.0,
            profit=-2500.0,
        )
        adjust_profit_sharing_wages(
            [company], [], starting_wage=1000.0, bonus_rate=0.0
        )
        self.assertLess(company.wage_offer, 1000.0)

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

    def test_food_production_targets_forecast_demand_plus_inventory_buffer(self) -> None:
        config = SimulationConfig(food_productivity=100.0)
        firm = Company(
            "food", "Producteur", "food", 100000.0, 1000.0, employees=["worker"]
        )
        resources = Resources(raw_materials=1000.0, land_units=0, knowledge=0.0)
        produced = produce_food([firm], resources, config, market_demand=10.0)
        self.assertLessEqual(
            produced, 10.0 * config.target_inventory_months
        )
        self.assertAlmostEqual(firm.expenses, produced * config.food_input_cost)
        self.assertAlmostEqual(resources.market_cash, produced * config.food_input_cost)

    def test_materials_company_supplies_food_producer_and_receives_payment(self) -> None:
        config = SimulationConfig(
            food_productivity=20.0,
            food_input_cost=20.0,
            raw_material_unit_price=8.0,
            raw_material_extraction_cost=2.0,
            raw_material_extraction_productivity=10.0,
            reserve_replacement_rate=0.0,  # l'exploration est testée séparément
            input_cost_split={"services": 1.0},  # isole le paiement des matières premières
        )
        producer = Company("food", "Producteur alimentaire", "food", 1000.0, 10.0, employees=["worker"])
        supplier = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["miner"])
        resources = Resources(
            raw_materials=20.0,
            land_units=0,
            knowledge=0.0,
            raw_material_price=8.0,
        )
        produced = produce_food(
            [producer, supplier], resources, config, market_demand=10.0
        )
        self.assertAlmostEqual(produced, 10.0)
        self.assertAlmostEqual(producer.supplier_spending[supplier.id], 80.0)
        self.assertAlmostEqual(supplier.customer_revenue[producer.id], 80.0)
        self.assertAlmostEqual(supplier.expenses, 20.0)
        self.assertAlmostEqual(producer.cash + supplier.cash + resources.market_cash, 2000.0)

    def test_recycled_materials_can_supply_production_without_an_extractor(self) -> None:
        config = SimulationConfig(
            food_productivity=10.0,
            food_input_cost=20.0,
            raw_material_unit_price=8.0,
            raw_material_extraction_productivity=10.0,
            input_cost_split={"services": 1.0},  # isole les matières recyclées
        )
        producer = Company(
            "food", "Producteur alimentaire", "food", 1000.0, 10.0,
            employees=["worker"],
        )
        idle_supplier = Company(
            "materials", "Extracteur inactif", "materials", 1000.0, 10.0
        )
        resources = Resources(
            raw_materials=0.0,
            land_units=0,
            knowledge=0.0,
            raw_material_price=8.0,
            recycled_materials=10.0,
        )

        produced = produce_food(
            [producer, idle_supplier], resources, config, market_demand=10.0
        )

        self.assertAlmostEqual(produced, 10.0)
        self.assertAlmostEqual(resources.raw_materials, 0.0)
        self.assertAlmostEqual(resources.recycled_materials, 0.0)
        self.assertAlmostEqual(producer.expenses, 125.0)
        self.assertAlmostEqual(idle_supplier.revenue, 0.0)
        self.assertAlmostEqual(resources.market_cash, 125.0)
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
                population_size=1,
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
        _, _, _ = sell_food([company], households, market_price=10.0)
        self.assertAlmostEqual(
            sum(person.expenses_this_turn for person in households),
            company.revenue,
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
        record = simulation.step()
        self.assertEqual(simulation.resources.raw_materials, 0.0)
        self.assertGreater(simulation.resources.raw_material_price, initial_price)
        self.assertEqual(record.raw_material_price, simulation.resources.raw_material_price)

    def test_stock_holdings_match_issued_shares(self) -> None:
        for _ in range(5):
            self.simulation.step()
        for company in self.simulation.companies:
            held = sum(person.shares.get(company.id, 0.0) for person in self.simulation.people)
            held += sum(other.shares.get(company.id, 0.0) for other in self.simulation.companies)
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
        config = SimulationConfig(
            build_time_months=6, housing_build_cost=100.0, starting_wage=10.0
        )
        firm = Company("builder", "Bâtisseur", "real_estate", 500.0, 10.0)
        land = Resources(raw_materials=0.0, land_units=1, knowledge=0.0)
        self.assertEqual(build_housing([firm], land, month=3, config=config), 1)
        self.assertEqual(firm.property_inventory, 0)
        self.assertEqual(complete_construction([firm], month=8, config=config), 0)
        self.assertEqual(complete_construction([firm], month=9, config=config), 1)
        self.assertEqual(firm.property_inventory, 1)

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

    def test_startup_failure_uses_configured_loss_streak(self) -> None:
        company = Company(
            "startup", "Jeune entreprise", "food", 100.0, 10.0,
            founder_id="founder", age_months=2, loss_streak=2, profit=-1.0,
        )
        founder = Person("founder", "Fondateur", 0.0, shares={"startup": 1000.0})
        config = SimulationConfig(bankruptcy_months=2, startup_failure_probability=1.0)
        rival = Company("rival", "Concurrent sain", "food", 1000.0, 10.0)
        reserves, defaults = resolve_distressed_companies(
            [company, rival], [founder], 0.0, config, random.Random(1)
        )
        self.assertEqual(defaults, 1)
        self.assertEqual(reserves, 0.0)
        self.assertEqual(founder.cash, 100.0)
        self.assertFalse(company.active)

    def test_company_default_transfers_financial_assets_to_its_shareholders(self) -> None:
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
        reserves, defaults = resolve_distressed_companies(
            [company, target, rival], [owner], 500.0, SimulationConfig(), random.Random(1)
        )
        self.assertEqual(defaults, 1)
        self.assertEqual(reserves, 500.0)
        self.assertEqual(owner.shares["target"], 20.0)
        self.assertFalse(company.active)

    def test_stock_merger_converts_target_shares_and_preserves_equity_count(self) -> None:
        config = SimulationConfig(
            acquisition_probability=1.0, merger_size_ratio=1.5, min_firms_per_sector=0
        )
        buyer = Company(
            "buyer", "Leader", "food", 400000.0, 1000.0,
            profit=10000.0, stock_price=100.0, property_inventory=2,
            shares_outstanding=1000.0, treasury_shares=1000.0,
        )
        target = Company(
            "target", "Rival", "food", 100000.0, 1000.0,
            profit=100.0, stock_price=50.0, property_inventory=1,
            shares_outstanding=1000.0, treasury_shares=0.0,
        )
        shareholder = Person("holder", "Actionnaire", 1000.0, shares={"target": 1000.0})
        merged = consolidate_market(
            [buyer, target], [shareholder], config, random.Random(1), month=1
        )
        self.assertEqual(merged, 1)
        self.assertFalse(target.active)
        self.assertAlmostEqual(shareholder.shares["buyer"], 500.0)
        self.assertAlmostEqual(
            buyer.treasury_shares + shareholder.shares["buyer"],
            buyer.shares_outstanding,
        )

    def test_long_run_wealth_debt_and_market_values_remain_bounded(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=1))
        initial_money = (
            sum(person.cash for person in simulation.people)
            + sum(
                company.cash
                for company in simulation.companies
                if company.sector != "bank"
            )
            + simulation.bank_reserves
        )
        for _ in range(240):
            simulation.step()
            money_in_circulation = (
                sum(person.cash for person in simulation.people)
                + sum(
                    company.cash
                    for company in simulation.companies
                    if company.sector != "bank"
                )
                + simulation.bank_reserves
                + simulation.government_balance
                + simulation.resources.market_cash
                - simulation.base_money_issued  # monnaie émise par la banque centrale
            )
            self.assertAlmostEqual(money_in_circulation, initial_money, places=5)
            for company in simulation.companies:
                shares_held = sum(
                    person.shares.get(company.id, 0.0)
                    for person in simulation.people
                ) + sum(
                    owner.shares.get(company.id, 0.0)
                    for owner in simulation.companies
                )
                self.assertAlmostEqual(
                    shares_held + company.treasury_shares,
                    company.shares_outstanding,
                    places=5,
                )
        self.assertEqual(simulation.month, 240)
        self.assertTrue(all(person.cash >= 0.0 for person in simulation.people))
        self.assertTrue(all(company.cash >= 0.0 and company.debt >= 0.0 for company in simulation.companies))
        self.assertTrue(all(company.stock_price <= 5000.0 for company in simulation.companies))
        self.assertTrue(all(0.0 <= record.household_gini <= 1.0 for record in simulation.history))
        self.assertGreaterEqual(simulation.bank_reserves, 0.0)

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

        def money_in_circulation() -> float:
            return (
                sum(person.cash for person in simulation.people)
                + sum(
                    company.cash
                    for company in simulation.companies
                    if company.sector != "bank"
                )
                + simulation.bank_reserves
                + simulation.government_balance
                + simulation.resources.market_cash
                - simulation.base_money_issued  # monnaie émise par la banque centrale
            )

        initial_money = money_in_circulation()
        for _ in range(24):
            simulation.step()
            self.assertAlmostEqual(money_in_circulation(), initial_money, places=5)

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
            age_months=10, demand_ema=240.0, inventory=360.0,
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
        for person in simulation.people:
            if person.id in initial:
                self.assertEqual(person.consumption_propensity, initial[person.id])
        mean_before = sum(initial.values()) / len(initial)
        mean_after = sum(
            p.consumption_propensity for p in simulation.people if p.id in initial
        ) / len(initial)
        self.assertAlmostEqual(mean_before, mean_after)

    def test_public_budget_is_spent_not_accumulated(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=4))
        records = [simulation.step() for _ in range(48)]
        monthly_gdp = sum(r.recurrent_gdp for r in records[-12:]) / 12
        self.assertLess(abs(simulation.government_balance), 0.25 * monthly_gdp)
        late_balances = [r.government_balance for r in records[24:]]
        self.assertLess(max(late_balances), 0.5 * monthly_gdp)
        self.assertGreater(
            sum(r.government_purchases + r.public_transfers for r in records[12:]), 0.0
        )

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
        initial = self.money(simulation)
        for _ in range(60):
            simulation.step()
            self.assertAlmostEqual(self.money(simulation), initial, places=4)

    def test_construction_is_paid_progressively_and_conserves_cash(self) -> None:
        config = SimulationConfig(housing_build_cost=60000.0, build_time_months=6)
        builder = Company("builder", "Promoteur", "real_estate", 200000.0, 100.0)
        resources = Resources(raw_materials=0.0, land_units=3, knowledge=0.0)
        build_housing([builder], resources, month=1, config=config)
        self.assertEqual(len(builder.construction_pipeline), 2)
        self.assertEqual(builder.cash, 200000.0)  # rien n'est versé au lancement
        paid = pay_construction_progress([builder], resources, config)
        self.assertAlmostEqual(paid, 2 * 60000.0 / 6)
        self.assertAlmostEqual(builder.cash + resources.market_cash, 200000.0)
        self.assertAlmostEqual(builder.construction_in_progress, paid)

    # --- dividendes, crédit, banques --------------------------------------
    def test_excess_cash_is_distributed_but_working_capital_is_kept(self) -> None:
        holder = Person("p1", "Actionnaire", 0.0, shares={"firm": 100.0})
        firm = Company(
            "firm", "Entreprise", "food", 400000.0, 1000.0,
            expenses=30000.0, wage_bill=20000.0, shares_outstanding=100.0,
            treasury_shares=0.0, profit=0.0,
        )
        paid = distribute_dividends(
            [firm], [holder], 0.12, working_capital_months=3.0, excess_payout_rate=0.25
        )
        self.assertAlmostEqual(paid, 0.25 * (400000.0 - 90000.0))
        self.assertAlmostEqual(holder.cash, paid)
        self.assertAlmostEqual(firm.cash, 400000.0 - paid)
        # sous le fonds de roulement, plus rien n'est distribué
        poor = Company(
            "poor", "Entreprise juste", "food", 80000.0, 1000.0,
            expenses=30000.0, wage_bill=20000.0, shares_outstanding=100.0,
            treasury_shares=0.0, profit=0.0,
        )
        holder2 = Person("p2", "Actionnaire 2", 0.0, shares={"poor": 100.0})
        self.assertEqual(
            distribute_dividends(
                [poor], [holder2], 0.12, working_capital_months=3.0,
                excess_payout_rate=0.25,
            ),
            0.0,
        )

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
        records = [simulation.step() for _ in range(36)]
        banks_profit = [r.sector_profit["bank"] for r in records]
        self.assertGreater(sum(banks_profit) / len(banks_profit), 0.0)
        self.assertGreater(records[-1].bank_loan_portfolio, 0.0)
        self.assertGreater(records[-1].bank_interest_income, 0.0)

    # --- PIB et salaires ---------------------------------------------------
    def test_recurrent_gdp_excludes_construction_and_has_no_spikes(self) -> None:
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
        monthly_work = simulation.config.housing_build_cost / simulation.config.build_time_months
        for record in records:
            started = record.housing_construction / simulation.config.housing_build_cost
            self.assertLessEqual(
                record.construction_value_added,
                (started + 40) * monthly_work,
            )
        window = [r.recurrent_gdp for r in records[12:]]
        self.assertLess(max(window) / min(window), 1.8)

    def test_productivity_index_starts_at_one_and_drives_a_wage_term(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=1))
        simulation.step()
        self.assertAlmostEqual(simulation.productivity_index, 1.0, delta=0.15)
        for _ in range(30):
            simulation.step()
        self.assertGreater(simulation.productivity_index, 1.0)
        self.assertEqual(
            set(simulation.wage_terms),
            {"productivite", "tension", "rentabilite", "inflation"},
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
            exploration_cost_per_unit=1.0, material_recovery_rate=0.0,
        )
        producer = Company("food", "Producteur", "food", 1000.0, 10.0, employees=["w"])
        supplier = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["m"])
        resources = Resources(raw_materials=20.0, land_units=0, knowledge=0.0, raw_material_price=8.0)
        produce_food([producer, supplier], resources, config, market_demand=10.0)
        self.assertAlmostEqual(resources.raw_materials, 20.0)  # reconstituées

        depleting = SimulationConfig(reserve_replacement_rate=0.0, food_productivity=20.0,
                                     raw_material_extraction_productivity=10.0, raw_material_unit_price=8.0)
        resources2 = Resources(raw_materials=20.0, land_units=0, knowledge=0.0, raw_material_price=8.0)
        producer2 = Company("food", "Producteur", "food", 1000.0, 10.0, employees=["w"])
        supplier2 = Company("materials", "Extracteur", "materials", 1000.0, 10.0, employees=["m"])
        produce_food([producer2, supplier2], resources2, depleting, market_demand=10.0)
        self.assertLess(resources2.raw_materials, 20.0)

    def test_central_bank_issues_money_when_inflation_is_below_target_and_withdraws_above(self) -> None:
        def issuance(inflation_per_month: float) -> float:
            simulation = EconomySimulation(SimulationConfig(seed=2, monetary_trend=0.0))
            for _ in range(8):
                simulation.step()
            level = 100.0
            for record in simulation.history[-13:]:
                level *= 1.0 + inflation_per_month
                record.consumer_price_index = level
            simulation.history[-1].unemployment = simulation.config.natural_unemployment_rate
            before = simulation.base_money_issued
            simulation._central_bank_operations()
            return simulation.base_money_issued - before

        self.assertGreater(issuance(0.0), 0.0)
        self.assertLess(issuance(0.02), 0.0)

    def test_monetary_issuance_is_recorded_in_the_money_supply(self) -> None:
        simulation = EconomySimulation(SimulationConfig(seed=3))
        initial = (
            sum(p.cash for p in simulation.people)
            + sum(c.cash for c in simulation.companies)
        )
        for _ in range(30):
            record = simulation.step()
        self.assertAlmostEqual(
            record.money_supply - simulation.base_money_issued, initial, places=4
        )

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

    def test_merger_is_blocked_when_it_would_leave_too_few_competitors(self) -> None:
        config = SimulationConfig(acquisition_probability=1.0, merger_size_ratio=1.5,
                                  min_firms_per_sector=2)
        buyer = Company("buyer", "Leader", "food", 400000.0, 1000.0, profit=10000.0,
                        stock_price=100.0, shares_outstanding=1000.0)
        target = Company("target", "Rival", "food", 100000.0, 1000.0, profit=100.0,
                         stock_price=50.0, shares_outstanding=1000.0, treasury_shares=0.0)
        self.assertEqual(consolidate_market([buyer, target], [], config, random.Random(1), 1), 0)
        self.assertTrue(target.active)

    def test_last_firm_of_a_sector_is_restructured_not_liquidated(self) -> None:
        firm = Company("re", "Dernier promoteur", "real_estate", 10.0, 100.0,
                       debt=10_000_000.0, property_inventory=5)
        resolve_distressed_companies([firm], [], 0.0, SimulationConfig(), random.Random(1))
        self.assertTrue(firm.active)
        self.assertEqual(firm.property_inventory, 5)
        self.assertEqual(firm.debt, 0.0)

    def test_failed_real_estate_firm_hands_its_housing_to_a_surviving_firm(self) -> None:
        failing = Company("re1", "Promoteur A", "real_estate", 10.0, 100.0,
                          debt=10_000_000.0, property_inventory=4)
        heir = Company("re2", "Promoteur B", "real_estate", 500000.0, 100.0)
        resolve_distressed_companies([failing, heir], [], 0.0, SimulationConfig(), random.Random(1))
        self.assertFalse(failing.active)
        self.assertEqual(heir.property_inventory, 4)

    def test_wages_are_partially_and_lately_adjusted_to_past_inflation(self) -> None:
        def reference_change(inflation_per_month: float) -> float:
            simulation = EconomySimulation(SimulationConfig(seed=6, max_monthly_wage_change=0.05))
            for _ in range(12):
                simulation.step()
            for record in simulation.history:
                record.inflation_rate = inflation_per_month
            before = simulation.wage_reference
            simulation._update_wage_reference()
            return simulation.wage_reference / before - 1.0

        calm = reference_change(0.0)
        inflated = reference_change(0.01)
        catch_up = inflated - calm
        self.assertGreater(catch_up, 0.0)
        # rattrapage partiel : entre 50 % et 90 % de l'inflation passée, jamais 100 %
        self.assertTrue(0.004 <= catch_up <= 0.0095)

    def test_no_nan_or_infinite_values_over_a_long_run(self) -> None:
        import math

        simulation = EconomySimulation(SimulationConfig(seed=5))
        for _ in range(150):
            record = simulation.step()
            for value in (record.nominal_gdp, record.average_wage, record.unemployment,
                          record.consumer_price_index, record.total_profit, record.money_supply):
                self.assertTrue(math.isfinite(value))
        self.assertGreater(record.recurrent_gdp, 0.3 * max(r.recurrent_gdp for r in simulation.history))


class InterSectorMarketTests(unittest.TestCase):
    """Étapes 1 et 2 : demande inter-entreprises, boosts, énergie et infrastructures."""

    @staticmethod
    def _resources() -> Resources:
        return Resources(raw_materials=0.0, land_units=0, knowledge=100.0)

    def test_business_inputs_are_real_payments_between_agents(self) -> None:
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0)
        energy = Company("e", "Énergie", "energy", 0.0, 10.0, employees=["w"])
        resources = self._resources()
        paid = pay_business_suppliers(
            buyer, 100.0, {"energy": 0.5, "services": 0.5}, [buyer, energy], resources
        )
        self.assertAlmostEqual(paid, 100.0)
        self.assertAlmostEqual(energy.revenue, 50.0)
        self.assertAlmostEqual(energy.customer_revenue["b"], 50.0)
        self.assertAlmostEqual(buyer.supplier_spending["e"], 50.0)
        self.assertAlmostEqual(resources.market_cash, 50.0)  # reste du secteur amont
        self.assertAlmostEqual(buyer.cash + energy.cash + resources.market_cash, 1000.0)

    def test_sector_without_suppliers_falls_back_to_the_upstream_fund(self) -> None:
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0)
        resources = self._resources()
        pay_business_suppliers(buyer, 100.0, {"energy": 1.0}, [buyer], resources)
        self.assertAlmostEqual(resources.market_cash, 100.0)
        self.assertAlmostEqual(buyer.cash, 900.0)

    def test_grid_saving_lowers_the_energy_bill_without_creating_money(self) -> None:
        buyer = Company("b", "Acheteur", "food", 1000.0, 10.0, energy_saving=0.2)
        energy = Company("e", "Énergie", "energy", 0.0, 10.0, employees=["w"])
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

    def test_energy_plants_are_capitalized_and_paid_to_suppliers(self) -> None:
        config = SimulationConfig()
        energy = Company(
            "e", "Énergie", "energy", 100000.0, 10.0, previous_revenue=50000.0,
        )
        resources = self._resources()
        invested = invest_in_energy_plants([energy], resources, config)
        self.assertAlmostEqual(invested, 50000.0 * config.energy_capex_share)
        self.assertAlmostEqual(energy.productive_capital_value, invested)
        self.assertAlmostEqual(energy.cash, 100000.0 - invested)
        self.assertAlmostEqual(energy.expenses, 0.0)  # capitalisé, amorti ensuite
        self.assertAlmostEqual(resources.market_cash, invested)


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

    def test_entrants_get_a_temporary_boost_financed_by_bank_credit(self) -> None:
        config = SimulationConfig(startup_threshold=10000.0, company_founder_probability=1.0)
        founder = Person("f", "Fondateur", 40000.0, entrepreneurial_spirit=0.9)
        startups, reserves, _ = create_household_startups(
            [], [founder], 1, 100000.0, config, random.Random(3), housing_shortage=0,
            policy_rate=0.004,
        )
        entrant = startups[0]
        self.assertGreater(entrant.entry_boost, 1.0)
        self.assertGreater(entrant.debt, 0.0)
        # le prêt vient des réserves bancaires : rien n'est créé
        self.assertAlmostEqual(reserves, 100000.0 - entrant.debt)
        self.assertAlmostEqual(entrant.cash, 40000.0 * config.startup_investment_fraction + entrant.debt)

    def test_entry_boost_expires_after_the_configured_number_of_months(self) -> None:
        simulation = EconomySimulation(SimulationConfig(population_size=30, seed=3))
        entrant = Company(
            "late", "Entrant", "food", 1000.0, 10.0, entry_boost=1.2,
            age_months=simulation.config.entrant_boost_months,
        )
        simulation.companies.append(entrant)
        simulation.step()
        self.assertEqual(entrant.entry_boost, 1.0)

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

    def test_no_rule_prevents_a_monopoly_but_only_a_more_efficient_firm_absorbs(self) -> None:
        config = SimulationConfig(acquisition_probability=1.0, merger_size_ratio=1.5)
        self.assertEqual(config.min_firms_per_sector, 0)

        def firms(target_profit: float) -> list[Company]:
            buyer = Company(
                "buyer", "Leader", "food", 400000.0, 1000.0, profit=20000.0,
                previous_revenue=100000.0, previous_profit=20000.0,
                stock_price=100.0, shares_outstanding=1000.0,
            )
            target = Company(
                "target", "Rival", "food", 100000.0, 1000.0, profit=100.0,
                previous_revenue=100000.0, previous_profit=target_profit,
                stock_price=50.0, shares_outstanding=1000.0, treasury_shares=0.0,
            )
            return [buyer, target]

        less_efficient = firms(0.0)
        self.assertEqual(consolidate_market(less_efficient, [], config, random.Random(1), 1), 1)
        self.assertEqual(sum(c.active for c in less_efficient), 1)  # monopole
        self.assertEqual(less_efficient[1].exit_reason, "acquired")
        more_efficient = firms(30000.0)
        self.assertEqual(consolidate_market(more_efficient, [], config, random.Random(1), 1), 0)

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
        worker = Person("w", "Chômeur", 5000.0, trade="commercial")
        resources = Resources(raw_materials=0.0, land_units=0, knowledge=0.0)
        market.refresh([worker], [tech])
        started = market.retrain([worker], resources, 1000.0)
        self.assertEqual(started, 1)
        self.assertAlmostEqual(worker.cash, 4000.0)
        self.assertAlmostEqual(resources.market_cash, 1000.0)
        self.assertEqual(worker.trade, "commercial")  # pas instantané
        for _ in range(3):
            market.retrain([worker], resources, 1000.0)
        self.assertNotEqual(worker.trade, "commercial")

    def test_households_cannot_rent_out_homes_they_own(self) -> None:
        owner = Person("o", "Propriétaire", 0.0, owns_home=True, investment_homes=2)
        tenant = Person("t", "Locataire", 1000.0, income_this_turn=1000.0)
        revenue = allocate_housing_and_collect_rent([], [owner, tenant], 500.0)
        self.assertEqual(revenue, 0.0)
        self.assertIsNone(tenant.rental_owner_id)
        self.assertEqual(owner.cash, 0.0)

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


if __name__ == "__main__":
    unittest.main()
