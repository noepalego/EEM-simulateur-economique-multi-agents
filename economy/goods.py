from __future__ import annotations

from typing import Literal, Mapping

from economy import diagnostics
from economy.expectations import planned_production
from economy.intersector import pay_business_suppliers
from economy.labor import effective_worker_count
from economy.models import (
    Company,
    Person,
    Resources,
    SimulationConfig,
    productivity_multiplier,
)


def spending_income(person: Person, config: SimulationConfig) -> float:
    """Revenu sur lequel un ménage fixe ses budgets : son revenu attendu (R10)."""
    return person.expected_income


def precautionary_reserve(person: Person, config: SimulationConfig) -> float:
    """Trésorerie qu'un ménage garde de côté (R10) : revenu attendu exposé au risque de chômage
    qu'il observe dans sa propre histoire, selon sa tolérance au risque."""
    return (
        config.precautionary_months
        * person.unemployment_risk
        * person.expected_income
        * (1.0 - person.risk_tolerance)
    )


def household_budget(person: Person, income_share: float, cash_draw_rate: float,
                     config: SimulationConfig) -> float:
    """Budget voulu avant la loi d'Engel et la propension (R10) : part du revenu attendu +
    tirage sur la trésorerie au-delà de la réserve de précaution ; l'appelant borne toujours la
    dépense par la trésorerie."""
    cash = max(0.0, person.cash - precautionary_reserve(person, config))
    return max(0.0, person.expected_income * income_share + cash * cash_draw_rate)


def update_income_expectations(people: list[Person], config: SimulationConfig) -> None:
    """R10 : chaque ménage met à jour son revenu attendu et la fréquence de ses mois sans
    emploi (moyennes lissées sur ``income_memory_months``), à partir de sa seule histoire."""
    weight = 1.0 / max(config.income_memory_months, 1.0)
    for person in people:
        unemployed = 1.0 if person.employer_id is None else 0.0
        if person.income_months == 0:
            person.expected_income = person.income_this_turn  # première observation
            person.unemployment_risk = unemployed
        else:
            person.expected_income += weight * (person.income_this_turn - person.expected_income)
            person.unemployment_risk += weight * (unemployed - person.unemployment_risk)
        person.income_months += 1


def produce_food(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig | None = None,
    market_demand: float | None = None,
    worker_skills: Mapping[str, float | tuple[float, float]] | None = None,
) -> float:
    config = config or SimulationConfig()
    food_firms = [company for company in companies if company.active and company.sector == "food"]
    capacities = {
        company.id: (
            _effective_workers(company, worker_skills)
            * config.food_productivity
            * company.productivity_trait
            * productivity_multiplier(company, config)  # boosts technologiques et stockage
            + company.production_capacity
        )
        * company.energy_fill  # sans l'énergie demandée, la production est rationnée
        for company in food_firms
    }
    for company in food_firms:
        # prévision propre de l'entreprise (economy.expectations), pas la demande agrégée
        wanted = min(capacities[company.id], planned_production(company, config))
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
        supplied = _purchase_raw_materials(
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
        # coût variable (signal de prix de l'entreprise) : capacité du mois, intrants par unité
        company.potential_output = capacities[company.id]
        if produced > 0.0:
            company.unit_input_cost = _unit_input_cost(company, produced, input_cost, supplied, resources, config)
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
    desired: dict[str, float] = {}
    for person in people:
        food_budget = min(
            person.cash,
            household_budget(person, config.food_income_budget_share, config.food_cash_draw_rate, config)
            * person.engel_food,  # loi d'Engel : la part de l'alimentation baisse avec le revenu réel
        )
        desired[person.id] = (
            food_budget
            * person.consumption_propensity
            / max(market_price * (1.0 + config.vat_rate), 0.01)
        )
    demand = sum(desired.values())
    preferences = {person.id: person.quality_preference for person in people}
    if not food_firms or supply <= 0 or not people:
        for person in people:
            person.last_food_bought = 0.0
        record_addressed_demand(food_firms, demand, {}, config, market_price, desired, preferences)
        return 0.0, 0.0, demand
    sales_by_firm = _allocate_sales(
        food_firms,
        demand,
        "inventory",
        config.consumer_price_elasticity,
        market_price,
        desired,
        preferences,
        config.quality_preference_strength,
    )
    sold = sum(sales_by_firm.values())
    ration = sold / demand if demand else 0.0
    purchases = {person_id: amount * ration for person_id, amount in desired.items()}
    sales_by_firm, purchases, total_revenue = _limit_sales_to_household_budgets(
        sales_by_firm, purchases, food_firms, people, 1.0 + config.vat_rate
    )
    sold = sum(sales_by_firm.values())
    record_addressed_demand(food_firms, demand, sales_by_firm, config, market_price, desired, preferences)

    # le ménage paie le prix toutes taxes comprises ; l'entreprise encaisse le prix
    # hors taxe et la TVA (``total_revenue`` × taux) revient à l'État
    for person in people:
        quantity = purchases[person.id]
        person_charge = quantity * (total_revenue / sold) * (1.0 + config.vat_rate) if sold else 0.0
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
    capacities = {
        company.id: (
            _effective_workers(company, worker_skills)
            * config.goods_productivity
            * company.productivity_trait
            * productivity_multiplier(company, config)
            + company.production_capacity
        )
        * company.energy_fill
        for company in producers
    }
    for company in producers:
        # prévision propre de l'entreprise (economy.expectations), pas la demande agrégée
        capacity = min(capacities[company.id], planned_production(company, config))
        material_per_unit = config.goods_material_per_unit
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
        supplied = _purchase_raw_materials(
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
        # coût variable (signal de prix de l'entreprise) : capacité du mois, intrants par unité
        company.potential_output = capacities[company.id]
        if produced > 0.0:
            company.unit_input_cost = _unit_input_cost(company, produced, input_cost, supplied, resources, config)
    return sum(company.goods_inventory for company in producers)


def _unit_input_cost(
    company: Company,
    produced: float,
    input_cost: float,
    supplied: float,
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Intrants et matières par unité produite, au prix de base indexé (avant les facteurs de
    prix de l'énergie et de la technologie appliqués au paiement)."""
    return input_cost / produced


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
) -> float:
    """Paie matières et intrants ; retourne la part payée aux secteurs fournisseurs selon
    ``input_cost_split`` (à prix de base, avant leurs facteurs de prix)."""
    if quantity <= 0.0:
        return 0.0
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
        return total_input_cost

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
        # extraction et exploration : prestations de services (forage, logistique)
        pay_business_suppliers(
            supplier, extraction_cost, {"services": 1.0}, companies, resources
        )
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
    return total_input_cost - raw_material_payment


def sell_consumer_goods(
    companies: list[Company],
    people: list[Person],
    market_price: float,
    config: SimulationConfig | None = None,
) -> tuple[float, float, float]:
    config = config or SimulationConfig()
    producers = [company for company in companies if company.active and company.sector == "goods"]
    supply = sum(company.goods_inventory for company in producers)
    desired: dict[str, float] = {}
    for person in people:
        budget = min(
            person.cash,
            household_budget(person, config.goods_income_budget_share, config.goods_cash_draw_rate, config),
        )
        desired[person.id] = (
            budget
            * person.consumption_propensity
            / max(market_price * (1.0 + config.vat_rate), 0.01)
        )
    demand = sum(desired.values())
    preferences = {person.id: person.quality_preference for person in people}
    if not producers or supply <= 0.0 or not people:
        for person in people:
            person.last_goods_bought = 0.0
        record_addressed_demand(producers, demand, {}, config, market_price, desired, preferences)
        return 0.0, 0.0, demand
    sales_by_firm = _allocate_sales(
        producers,
        demand,
        "goods_inventory",
        config.consumer_price_elasticity,
        market_price,
        desired,
        preferences,
        config.quality_preference_strength,
    )
    sold = sum(sales_by_firm.values())
    ration = sold / demand if demand else 0.0
    purchases = {person_id: amount * ration for person_id, amount in desired.items()}
    sales_by_firm, purchases, total_revenue = _limit_sales_to_household_budgets(
        sales_by_firm, purchases, producers, people, 1.0 + config.vat_rate
    )
    sold = sum(sales_by_firm.values())
    record_addressed_demand(producers, demand, sales_by_firm, config, market_price, desired, preferences)

    for person in people:
        quantity = purchases[person.id]
        person_charge = quantity * (total_revenue / sold) * (1.0 + config.vat_rate) if sold else 0.0
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
    tax_factor: float = 1.0,
) -> tuple[dict[str, float], dict[str, float], float]:
    """Ramène les achats à ce que chaque ménage peut payer (prix TTC = HT × ``tax_factor``).

    Retourne les ventes par entreprise, les achats par ménage et le chiffre
    d'affaires hors taxe.
    """
    prices = {company.id: company.price for company in companies}
    sold = sum(sales_by_firm.values())
    total_revenue = sum(
        quantity * prices[firm_id] for firm_id, quantity in sales_by_firm.items()
    )
    if sold <= 0.0 or total_revenue <= 0.0:
        return {}, {person.id: 0.0 for person in people}, 0.0

    average_price = total_revenue / sold * tax_factor
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
        quantity * prices[firm_id] for firm_id, quantity in affordable_sales.items()
    )


def _sales_weights(
    companies: list[Company],
    price_elasticity: float,
    reference_price: float,
    household_demands: Mapping[str, float] | None,
    household_quality_preferences: Mapping[str, float] | None,
    quality_strength: float,
) -> dict[str, float]:
    """Poids de chaque entreprise dans les achats : (qualité / prix relatif) ^ élasticité,
    agrégés ménage par ménage selon sa préférence pour la qualité."""
    if household_demands and household_quality_preferences:
        weights = {company.id: 0.0 for company in companies}
        # termes qui ne dépendent pas du ménage, calculés une fois par entreprise (mêmes
        # opérations, même ordre : résultats identiques bit à bit)
        reference = max(reference_price, 0.01)
        strength = max(0.0, quality_strength)
        elasticity = max(0.0, price_elasticity)
        terms = [
            (company.id, max(0.5, company.quality_trait), max(company.price, 0.01))
            for company in companies
        ]
        for household_id, household_demand in household_demands.items():
            preference = max(
                0.0,
                household_quality_preferences.get(household_id, 1.0),
            )
            exponent = preference * strength
            # liste dans l'ordre des entreprises (auparavant un dictionnaire relu par
            # identifiant) : mêmes termes, même somme, même expression
            household_weights = [
                (reference * quality ** exponent / price) ** elasticity
                for _, quality, price in terms
            ]
            household_total_weight = sum(household_weights)
            if household_total_weight <= 0.0:
                continue
            demand = max(0.0, household_demand)
            for (company_id, _, _), weight in zip(terms, household_weights):
                weights[company_id] += demand * weight / household_total_weight
        return weights
    return {
        company.id: (
            max(reference_price, 0.01)
            * max(0.5, company.quality_trait) ** max(0.0, quality_strength)
            / max(company.price, 0.01)
        )
        ** max(0.0, price_elasticity)
        for company in companies
    }


def record_addressed_demand(
    companies: list[Company],
    demand: float,
    sales: Mapping[str, float],
    config: SimulationConfig,
    market_price: float,
    household_demands: Mapping[str, float] | None = None,
    household_quality_preferences: Mapping[str, float] | None = None,
) -> None:
    """Demande adressée à chaque entreprise (avant rationnement par les stocks) et part
    qu'elle n'a pas servie (``Company.unmet_units``) : signal de prix de l'entreprise."""
    if not companies:
        return
    weights = _sales_weights(
        companies, config.consumer_price_elasticity, market_price, household_demands,
        household_quality_preferences, config.quality_preference_strength,
    )
    total = sum(weights.values())
    for company in companies:
        addressed = demand * weights[company.id] / total if total > 0.0 else demand / len(companies)
        company.unmet_units = max(0.0, addressed - sales.get(company.id, 0.0))


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
        weights = _sales_weights(
            active, price_elasticity, reference_price, household_demands,
            household_quality_preferences, quality_strength,
        )
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
    market: str = "marche",
) -> float:
    """Prix de marché : écart demande-offre (±10 %/mois) et dérive de base (±5 %/mois),
    sous le plafond ; ``market`` nomme le marché dans les compteurs de diagnostic."""
    demand = max(0.0, demand) * max(0.0, demand_multiplier)
    diagnostics.bite(f"borne_derive_{market}", abs(baseline_growth_rate) > 0.05)
    baseline_growth_rate = max(-0.05, min(0.05, baseline_growth_rate))
    if demand <= 0.0:
        adjustment = 0.96
    elif supply <= 0:
        adjustment = 1.04
    else:
        pressure = (demand - supply) / max(supply, 1.0)
        raw_adjustment = 1.0 + pressure * 0.04
        adjustment = max(0.90, min(1.10, raw_adjustment))
        diagnostics.bite(f"vitesse_prix_{market}", adjustment != raw_adjustment)
    adjustment *= 1.0 + baseline_growth_rate
    diagnostics.bite(f"plafond_prix_{market}", previous_price * adjustment > price_ceiling)
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


def _split_among_suppliers(
    wanted: float, suppliers: dict[str, tuple[float, float]]
) -> dict[str, float]:
    """Répartit une commande entre fournisseurs (stock disponible, poids).

    Chaque tour partage le reliquat selon les poids des fournisseurs qui ont
    encore du stock ; un fournisseur saturé sort et sa part revient aux autres.
    Toute la demande servable est servie, quel que soit l'ordre des fournisseurs.
    """
    allocation = {supplier_id: 0.0 for supplier_id in suppliers}
    remaining = max(0.0, wanted)
    open_ids = [sid for sid, (stock, _) in suppliers.items() if stock > 1e-12]
    while remaining > 1e-9 and open_ids:
        weights = {sid: suppliers[sid][0] * suppliers[sid][1] for sid in open_ids}
        total = sum(weights.values())
        if total <= 0.0:
            break
        served = 0.0
        for sid in open_ids:
            room = suppliers[sid][0] - allocation[sid]
            quantity = min(room, remaining * weights[sid] / total)
            allocation[sid] += quantity
            served += quantity
        remaining -= served
        open_ids = [sid for sid in open_ids if suppliers[sid][0] - allocation[sid] > 1e-12]
        if served <= 1e-12:
            break
    return allocation


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
        # sans plafond de la part captée : un prix proche de la valeur créée éteint la
        # demande, ce qui borne l'indice
        capture = config.tech_value_capture * resources.tech_price_index
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
        allocation = _split_among_suppliers(
            quantity_wanted,
            {
                provider.id: (
                    firm_supply_limits[provider.id],
                    max(0.25, provider.innovation * provider.productivity_trait),
                )
                for provider in providers
            },
        )
        remaining = quantity_wanted
        for provider in providers:
            quantity = allocation.get(provider.id, 0.0)
            if quantity <= 1e-12:
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
