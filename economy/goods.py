from __future__ import annotations

from typing import Literal, Mapping

from economy.intersector import pay_business_suppliers
from economy.labor import effective_worker_count
from economy.models import (
    Company,
    Person,
    Resources,
    SimulationConfig,
    productivity_multiplier,
)


def produce_food(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig | None = None,
    market_demand: float | None = None,
    worker_skills: Mapping[str, float | tuple[float, float]] | None = None,
) -> float:
    config = config or SimulationConfig()
    food_firms = [company for company in companies if company.active and company.sector == "food"]
    market_need = (
        max(
            0.0,
            market_demand * config.target_inventory_months
            - sum(company.inventory for company in food_firms),
        )
        if market_demand is not None
        else None
    )
    capacities = {
        company.id: (
            _effective_workers(company, worker_skills)
            * config.food_productivity
            * company.productivity_trait
            * productivity_multiplier(company, config)  # boosts technologiques et stockage
            + company.production_capacity
        )
        for company in food_firms
    }
    total_capacity = sum(capacities.values())
    for company in food_firms:
        wanted = capacities[company.id]
        if market_need is not None and total_capacity > 0.0:
            wanted = min(
                wanted,
                market_need * capacities[company.id] / total_capacity,
            )
        available_materials = _available_raw_materials(
            companies, resources, config, worker_skills
        )
        maximum_production = min(wanted, available_materials)
        produced, input_cost = _affordable_production(
            maximum_production,
            company.cash,
            config.food_input_cost,
            1.0,
            resources,
            config,
        )
        _purchase_raw_materials(
            company,
            produced,
            input_cost,
            companies,
            resources,
            config,
            worker_skills,
        )
        company.inventory += produced
        company.units_produced += produced
    return sum(company.inventory for company in food_firms)


def sell_food(
    companies: list[Company],
    people: list[Person],
    market_price: float,
    config: SimulationConfig | None = None,
) -> tuple[float, float, float]:
    config = config or SimulationConfig()
    food_firms = [company for company in companies if company.active and company.sector == "food"]
    supply = sum(company.inventory for company in food_firms)
    if not food_firms or supply <= 0 or not people:
        for person in people:
            person.last_food_bought = 0.0
        return 0.0, 0.0, 0.0

    desired: dict[str, float] = {}
    for person in people:
        food_budget = min(
            person.cash,
            max(
                0.0,
                person.income_this_turn * config.food_income_budget_share
                + person.cash * config.food_cash_draw_rate,
            ),
        )
        desired[person.id] = (
            food_budget
            * person.consumption_propensity
            / max(market_price * 1.16, 0.01)
        )
    demand = sum(desired.values())
    sales_by_firm = _allocate_sales(
        food_firms,
        demand,
        "inventory",
        config.consumer_price_elasticity,
        market_price,
        desired,
        {person.id: person.quality_preference for person in people},
        config.quality_preference_strength,
    )
    sold = sum(sales_by_firm.values())
    ration = sold / demand if demand else 0.0
    purchases = {person_id: amount * ration for person_id, amount in desired.items()}
    total_revenue = sum(
        quantity * next(firm.price for firm in food_firms if firm.id == firm_id)
        for firm_id, quantity in sales_by_firm.items()
    )
    sales_by_firm, purchases, total_revenue = _limit_sales_to_household_budgets(
        sales_by_firm, purchases, food_firms, people
    )
    sold = sum(sales_by_firm.values())

    for person in people:
        quantity = purchases[person.id]
        person_charge = quantity * (total_revenue / sold) if sold else 0.0
        person.cash -= person_charge
        person.expenses_this_turn += person_charge
        person.cumulative_expenses += person_charge
        person.last_food_bought = quantity
    for company in food_firms:
        quantity = sales_by_firm.get(company.id, 0.0)
        company.inventory -= quantity
        company.units_sold = quantity
        company.revenue += quantity * company.price
        company.cash += quantity * company.price
    return sold, total_revenue, max(0.0, demand - sold)


def produce_consumer_goods(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig | None = None,
    market_demand: float | None = None,
    worker_skills: Mapping[str, float | tuple[float, float]] | None = None,
) -> float:
    config = config or SimulationConfig()
    producers = [company for company in companies if company.active and company.sector == "goods"]
    market_need = (
        max(
            0.0,
            market_demand * config.target_inventory_months
            - sum(company.goods_inventory for company in producers),
        )
        if market_demand is not None
        else None
    )
    capacities = {
        company.id: (
            _effective_workers(company, worker_skills)
            * config.goods_productivity
            * company.productivity_trait
            * productivity_multiplier(company, config)
            + company.production_capacity
        )
        for company in producers
    }
    total_capacity = sum(capacities.values())
    for company in producers:
        capacity = capacities[company.id]
        if market_need is not None and total_capacity > 0.0:
            capacity = min(
                capacity,
                market_need * capacities[company.id] / total_capacity,
            )
        material_per_unit = 1.5
        raw_materials_available = _available_raw_materials(
            companies, resources, config, worker_skills
        )
        maximum_production = min(
            capacity,
            raw_materials_available / material_per_unit,
        )
        produced, input_cost = _affordable_production(
            maximum_production,
            company.cash,
            config.goods_input_cost,
            material_per_unit,
            resources,
            config,
        )
        _purchase_raw_materials(
            company,
            produced * material_per_unit,
            input_cost,
            companies,
            resources,
            config,
            worker_skills,
        )
        company.goods_inventory += produced
        company.units_produced += produced
    return sum(company.goods_inventory for company in producers)


def _affordable_production(
    maximum_quantity: float,
    cash: float,
    base_input_cost_per_unit: float,
    material_per_unit: float,
    resources: Resources,
    config: SimulationConfig,
) -> tuple[float, float]:
    def total_cost(quantity: float) -> float:
        material_quantity = quantity * material_per_unit
        recycled_quantity = min(
            max(0.0, resources.recycled_materials), material_quantity
        )
        virgin_quantity = material_quantity - recycled_quantity
        material_cost = (
            recycled_quantity * config.raw_material_extraction_cost
            + virgin_quantity * resources.raw_material_price
        )
        baseline_material_cost = (
            material_quantity * config.raw_material_unit_price
        )
        return max(
            0.0,
            quantity * base_input_cost_per_unit * resources.input_cost_index
            + material_cost
            - baseline_material_cost,
        )

    upper = max(0.0, maximum_quantity)
    if total_cost(upper) <= max(0.0, cash):
        return upper, total_cost(upper)
    lower = 0.0
    for _ in range(40):
        midpoint = (lower + upper) / 2.0
        if total_cost(midpoint) <= max(0.0, cash):
            lower = midpoint
        else:
            upper = midpoint
    return lower, total_cost(lower)


def _available_raw_materials(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
    worker_skills: Mapping[str, float | tuple[float, float]] | None = None,
) -> float:
    suppliers = [
        company
        for company in companies
        if company.active and company.sector == "materials"
    ]
    if not suppliers:
        return max(0.0, resources.raw_materials) + max(
            0.0, resources.recycled_materials
        )
    remaining_capacity = sum(
        max(
            0.0,
            _effective_workers(company, worker_skills)
            * config.raw_material_extraction_productivity
            - company.materials_supplied_this_turn,
        )
        for company in suppliers
    )
    extractable_virgin = min(max(0.0, resources.raw_materials), remaining_capacity)
    return extractable_virgin + max(0.0, resources.recycled_materials)


def _purchase_raw_materials(
    buyer: Company,
    quantity: float,
    total_input_cost: float,
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
    worker_skills: Mapping[str, float | tuple[float, float]] | None = None,
) -> None:
    if quantity <= 0.0:
        return
    buyer.cash -= total_input_cost
    buyer.expenses += total_input_cost
    recycled_used = min(max(0.0, resources.recycled_materials), quantity)
    resources.recycled_materials -= recycled_used
    virgin_used = min(
        max(0.0, resources.raw_materials),
        quantity - recycled_used,
    )
    resources.raw_materials -= virgin_used

    suppliers = sorted(
        (
            company
            for company in companies
            if company.active and company.sector == "materials"
        ),
        key=lambda company: company.id,
    )
    raw_material_payment = min(
        total_input_cost,
        virgin_used * resources.raw_material_price,
    )
    if not suppliers or virgin_used <= 0.0:
        pay_business_suppliers(
            buyer, total_input_cost, config.input_cost_split, companies, resources, deduct=False
        )
        return

    supplier_capacities = {
        supplier.id: max(
            0.0,
            _effective_workers(supplier, worker_skills)
            * config.raw_material_extraction_productivity
            - supplier.materials_supplied_this_turn,
        )
        for supplier in suppliers
    }
    total_capacity = sum(supplier_capacities.values())
    allocated_quantity = min(virgin_used, total_capacity)
    for supplier in suppliers:
        capacity = supplier_capacities[supplier.id]
        supplied = (
            allocated_quantity * capacity / total_capacity
            if total_capacity > 0.0
            else 0.0
        )
        if supplied <= 0.0:
            continue
        supplier.materials_supplied_this_turn += supplied
        supplier.units_sold += supplied
        payment = raw_material_payment * supplied / virgin_used
        supplier.cash += payment
        supplier.revenue += payment
        supplier.customer_revenue[buyer.id] = (
            supplier.customer_revenue.get(buyer.id, 0.0) + payment
        )
        buyer.supplier_spending[supplier.id] = (
            buyer.supplier_spending.get(supplier.id, 0.0) + payment
        )
        extraction_cost_per_unit = min(
            config.raw_material_extraction_cost,
            resources.raw_material_price,
            raw_material_payment / virgin_used,
        )
        # Exploration : les extracteurs financent la découverte de nouveaux
        # gisements à hauteur de ``reserve_replacement_rate`` des volumes
        # extraits. Sans cela les réserves finies s'épuisent (~tour 220) et
        # l'économie entière s'arrête faute d'intrants.
        explored = supplied * config.reserve_replacement_rate
        exploration_cost = explored * config.exploration_cost_per_unit
        extraction_cost = supplied * extraction_cost_per_unit + exploration_cost
        supplier.cash -= extraction_cost
        supplier.expenses += extraction_cost
        resources.market_cash += extraction_cost
        resources.raw_materials += explored
    # intrants non matériels : achats réels aux secteurs fournisseurs
    pay_business_suppliers(
        buyer,
        total_input_cost - raw_material_payment,
        config.input_cost_split,
        companies,
        resources,
        deduct=False,
    )


def sell_consumer_goods(
    companies: list[Company],
    people: list[Person],
    market_price: float,
    config: SimulationConfig | None = None,
) -> tuple[float, float, float]:
    config = config or SimulationConfig()
    producers = [company for company in companies if company.active and company.sector == "goods"]
    supply = sum(company.goods_inventory for company in producers)
    if not producers or supply <= 0.0 or not people:
        for person in people:
            person.last_goods_bought = 0.0
        return 0.0, 0.0, 0.0

    desired: dict[str, float] = {}
    for person in people:
        budget = min(
            person.cash,
            max(
                0.0,
                person.income_this_turn * config.goods_income_budget_share
                + person.cash * config.goods_cash_draw_rate,
            ),
        )
        desired[person.id] = (
            budget
            * person.consumption_propensity
            / max(market_price * 1.16, 0.01)
        )
    demand = sum(desired.values())
    sales_by_firm = _allocate_sales(
        producers,
        demand,
        "goods_inventory",
        config.consumer_price_elasticity,
        market_price,
        desired,
        {person.id: person.quality_preference for person in people},
        config.quality_preference_strength,
    )
    sold = sum(sales_by_firm.values())
    ration = sold / demand if demand else 0.0
    purchases = {person_id: amount * ration for person_id, amount in desired.items()}
    total_revenue = sum(
        quantity * next(firm.price for firm in producers if firm.id == firm_id)
        for firm_id, quantity in sales_by_firm.items()
    )
    sales_by_firm, purchases, total_revenue = _limit_sales_to_household_budgets(
        sales_by_firm, purchases, producers, people
    )
    sold = sum(sales_by_firm.values())

    for person in people:
        quantity = purchases[person.id]
        person_charge = quantity * (total_revenue / sold) if sold else 0.0
        person.cash -= person_charge
        person.expenses_this_turn += person_charge
        person.cumulative_expenses += person_charge
        person.last_goods_bought = quantity
    for company in producers:
        quantity = sales_by_firm.get(company.id, 0.0)
        company.goods_inventory -= quantity
        company.units_sold += quantity
        company.revenue += quantity * company.price
        company.cash += quantity * company.price
    return sold, total_revenue, max(0.0, demand - sold)


def _limit_sales_to_household_budgets(
    sales_by_firm: dict[str, float],
    purchases: dict[str, float],
    companies: list[Company],
    people: list[Person],
) -> tuple[dict[str, float], dict[str, float], float]:
    sold = sum(sales_by_firm.values())
    total_revenue = sum(
        quantity * next(company.price for company in companies if company.id == firm_id)
        for firm_id, quantity in sales_by_firm.items()
    )
    if sold <= 0.0 or total_revenue <= 0.0:
        return {}, {person.id: 0.0 for person in people}, 0.0

    average_price = total_revenue / sold
    affordable_purchases: dict[str, float] = {}
    for person in people:
        quantity = purchases.get(person.id, 0.0)
        affordable_purchases[person.id] = min(
            quantity, max(0.0, person.cash) / average_price
        )
    affordable_sold = sum(affordable_purchases.values())
    scale = min(1.0, affordable_sold / sold)
    affordable_sales = {
        firm_id: quantity * scale
        for firm_id, quantity in sales_by_firm.items()
    }
    return affordable_sales, affordable_purchases, sum(
        quantity
        * next(company.price for company in companies if company.id == firm_id)
        for firm_id, quantity in affordable_sales.items()
    )


def _allocate_sales(
    companies: list[Company],
    demand: float,
    inventory_field: Literal["inventory", "goods_inventory"],
    price_elasticity: float = 1.0,
    reference_price: float = 100.0,
    household_demands: Mapping[str, float] | None = None,
    household_quality_preferences: Mapping[str, float] | None = None,
    quality_strength: float = 1.0,
) -> dict[str, float]:
    remaining = min(
        max(0.0, demand),
        sum(max(0.0, getattr(company, inventory_field)) for company in companies),
    )
    active = [
        company
        for company in companies
        if getattr(company, inventory_field) > 0.0
    ]
    sales = {company.id: 0.0 for company in companies}
    while remaining > 1e-9 and active:
        weights = {company.id: 0.0 for company in active}
        if household_demands and household_quality_preferences:
            for household_id, household_demand in household_demands.items():
                preference = max(
                    0.0,
                    household_quality_preferences.get(household_id, 1.0),
                )
                household_weights = {
                    company.id: (
                        max(reference_price, 0.01)
                        * max(0.5, company.quality_trait * company.entry_boost)
                        ** (preference * max(0.0, quality_strength))
                        / max(company.price, 0.01)
                    )
                    ** max(0.0, price_elasticity)
                    for company in active
                }
                household_total_weight = sum(household_weights.values())
                if household_total_weight <= 0.0:
                    continue
                for company in active:
                    weights[company.id] += (
                        max(0.0, household_demand)
                        * household_weights[company.id]
                        / household_total_weight
                    )
        else:
            weights = {
                company.id: (
                    max(reference_price, 0.01)
                    * max(0.5, company.quality_trait * company.entry_boost) ** max(0.0, quality_strength)
                    / max(company.price, 0.01)
                )
                ** max(0.0, price_elasticity)
                for company in active
            }
        total_weight = sum(weights.values())
        saturated = [
            company
            for company in active
            if remaining * weights[company.id] / total_weight
            >= getattr(company, inventory_field) - sales[company.id] - 1e-9
        ]
        if not saturated:
            for company in active:
                sales[company.id] += remaining * weights[company.id] / total_weight
            break
        for company in saturated:
            available = max(
                0.0,
                getattr(company, inventory_field) - sales[company.id],
            )
            sales[company.id] += available
            remaining -= available
            active.remove(company)
    return sales


def _effective_workers(
    company: Company,
    worker_skills: Mapping[str, float | tuple[float, float]] | None,
) -> float:
    boost = company.output_multiplier if company.sector == "materials" else 1.0
    if worker_skills is None:
        return float(len(company.employees)) * boost
    return effective_worker_count(company, worker_skills) * boost


def charge_inventory_holding_cost(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    total_paid = 0.0
    for company in companies:
        if not company.active:
            continue
        inventory_value = (
            max(0.0, company.inventory) * config.food_input_cost
            + max(0.0, company.goods_inventory) * config.goods_input_cost
        ) * resources.input_cost_index
        cost = inventory_value * config.inventory_holding_cost_rate
        # entrepôts : loués à des entreprises immobilières (sinon secteur amont)
        total_paid += pay_business_suppliers(
            company, cost, {"real_estate": 1.0}, companies, resources
        )
    return total_paid


def update_food_price(
    previous_price: float,
    demand: float,
    supply: float,
    price_ceiling: float = 2000.0,
    baseline_growth_rate: float = 0.0,
    demand_multiplier: float = 1.0,
) -> float:
    demand = max(0.0, demand) * max(0.0, demand_multiplier)
    baseline_growth_rate = max(-0.05, min(0.05, baseline_growth_rate))
    if demand <= 0.0:
        adjustment = 0.96
    elif supply <= 0:
        adjustment = 1.04
    else:
        pressure = (demand - supply) / max(supply, 1.0)
        adjustment = max(0.90, min(1.10, 1.0 + pressure * 0.04))
    adjustment *= 1.0 + baseline_growth_rate
    return max(1.0, min(price_ceiling, previous_price * adjustment))


def collect_recyclable_materials(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    material_use = sum(
        company.units_sold
        * (1.0 if company.sector == "food" else 1.5)
        for company in companies
        if company.active and company.sector in {"food", "goods"}
    )
    recovered = material_use * config.material_recovery_rate
    resources.recycling_pending += recovered
    return recovered


def trade_technology(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig | None = None,
    people: list[Person] | None = None,
) -> float:
    config = config or SimulationConfig()
    providers = [company for company in companies if company.active and company.sector == "technology"]
    buyers = [
        company
        for company in companies
        if company.active and company.sector != "technology"
    ]
    firm_supply_limits: dict[str, float] = {}
    for provider in providers:
        capacity = (
            _effective_workers(provider, None)
            * config.technology_productivity
            * provider.productivity_trait
            * provider.innovation
        )
        knowledge_cost = capacity * config.technology_knowledge_use_rate
        produced = min(
            capacity,
            resources.knowledge / config.technology_knowledge_use_rate
            if config.technology_knowledge_use_rate > 0.0
            else capacity,
        )
        if config.technology_knowledge_use_rate > 0.0:
            knowledge_cost = produced * config.technology_knowledge_use_rate
            resources.knowledge = max(0.0, resources.knowledge - knowledge_cost)
        provider.tech_inventory = min(
            provider.tech_inventory + produced, 3.0 * max(capacity, 1e-9)
        )
        # une part de la production reste réservée aux ménages
        firm_supply_limits[provider.id] = max(
            0.0,
            provider.tech_inventory
            - produced * config.technology_household_supply_share,
        )

    total_sold = 0.0
    resources.tech_unmet_units = 0.0
    resources.boost_spending = 0.0
    resources.boost_value_created = 0.0
    horizon = 1.0 / max(
        1e-6, config.technology_depreciation_rate + config.tech_discount_rate
    )
    ceiling = min(10.0, config.technology_business_max_level)
    for buyer in buyers:
        base_share = config.tech_value_base_share.get(buyer.sector, 0.0)
        revenue_base = max(buyer.revenue_ema, buyer.previous_revenue)
        if base_share <= 0.0 or revenue_base <= 0.0 or not providers or ceiling <= 0.0:
            continue
        operating_reserve = buyer.target_employees * buyer.wage_offer * 1.5
        free_cash = max(0.0, buyer.cash - operating_reserve)
        # Valeur créée par une unité (+0,03 niveau) : gain mensuel de productivité
        # actualisé sur la durée de vie du boost. La tech ne facture qu'une part
        # (``tech_value_capture``) de cette valeur : l'acheteur y gagne toujours.
        full_value = (
            revenue_base * base_share * 0.03 * config.tech_effect_per_level * horizon
        )
        capture = min(
            config.tech_value_capture_max,
            config.tech_value_capture * resources.tech_price_index,
        )
        unit_price = max(0.01, capture * full_value)
        # L'acheteur achète tant que la valeur marginale (décroissante avec le
        # niveau déjà atteint) dépasse le prix : niveau d'équilibre = plafond ×
        # (1 - part captée).
        level_target = ceiling * max(0.0, 1.0 - capture)
        if buyer.profit <= 0.0 and free_cash < 4.0 * operating_reserve:
            continue
        wanted = max(0.0, level_target - buyer.tech_level) / 0.03
        budget = free_cash * config.tech_investment_cash_share
        quantity_wanted = min(wanted, budget / unit_price)
        if quantity_wanted <= 1e-9:
            continue
        weights = {
            provider.id: firm_supply_limits[provider.id]
            * max(0.25, provider.innovation * provider.productivity_trait)
            for provider in providers
        }
        total_weight = sum(weights.values())
        remaining = quantity_wanted
        if total_weight > 0.0:
            for provider in providers:
                if remaining <= 1e-9:
                    break
                share = remaining * weights[provider.id] / total_weight
                quantity = min(firm_supply_limits[provider.id], share)
                if quantity <= 1e-9:
                    continue
                firm_supply_limits[provider.id] -= quantity
                payment = quantity * unit_price
                provider.tech_inventory -= quantity
                provider.revenue += payment
                provider.cash += payment
                provider.units_sold += quantity
                provider.customer_revenue[buyer.id] = (
                    provider.customer_revenue.get(buyer.id, 0.0) + payment
                )
                buyer.cash -= payment
                buyer.supplier_spending[provider.id] = (
                    buyer.supplier_spending.get(provider.id, 0.0) + payment
                )
                buyer.capital_expenditure += payment
                buyer.technology_asset_value += payment
                buyer.tech_level += quantity * 0.03
                resources.boost_spending += payment
                resources.boost_value_created += quantity * full_value
                total_sold += quantity
                remaining -= quantity
        resources.tech_unmet_units += max(0.0, remaining)

    if people:
        for person in sorted(
            people,
            key=lambda household: (
                household.technology_level,
                -household.income_this_turn,
                household.id,
            ),
        ):
            budget = min(
                max(0.0, person.cash),
                max(
                    0.0,
                    person.income_this_turn
                    * config.technology_household_budget_share
                    + person.cash * config.technology_household_savings_share,
                ),
            )
            for provider in providers:
                if budget <= 0.0 or provider.tech_inventory <= 1e-9:
                    continue
                while (
                    budget > 0.0
                    and provider.tech_inventory > 1e-9
                    and person.technology_level
                    < config.technology_household_max_level
                ):
                    unit_price = _technology_unit_price(
                        config,
                        person.technology_level,
                        config.tech_unit_price
                        * config.technology_household_price_fraction,
                    )
                    quantity = min(
                        provider.tech_inventory,
                        config.technology_household_level_gain,
                        (
                            config.technology_household_max_level
                            - person.technology_level
                        )
                        / config.technology_household_level_gain,
                        budget / unit_price,
                    )
                    if quantity <= 1e-9:
                        break
                    payment = quantity * unit_price
                    provider.tech_inventory -= quantity
                    provider.revenue += payment
                    provider.cash += payment
                    provider.customer_revenue[person.id] = (
                        provider.customer_revenue.get(person.id, 0.0) + payment
                    )
                    person.cash -= payment
                    person.expenses_this_turn += payment
                    person.cumulative_expenses += payment
                    person.technology_level += (
                        quantity * config.technology_household_level_gain
                    )
                    resources.household_technology_revenue += payment
                    resources.household_technology_units += quantity
                    budget -= payment
                    total_sold += quantity
    return total_sold


def _technology_unit_price(
    config: SimulationConfig,
    technology_level: float,
    base_price: float,
) -> float:
    price_multiplier = (
        1.0 + config.technology_price_growth * max(0.0, technology_level)
    ) ** 2
    return max(0.01, base_price * price_multiplier)
