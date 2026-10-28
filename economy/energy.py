"""Secteur de l'énergie : infrastructures vendues aux entreprises et investissement.

Les entreprises d'énergie vendent de l'énergie (achats courants, voir
``economy.intersector``) et des infrastructures :

* ``production`` : capacité de production supplémentaire (substitue du capital
  au travail) ;
* ``reseau`` : raccordement et efficacité énergétique, qui réduit la facture
  d'énergie de l'acheteur ;
* ``stockage`` : continuité d'approvisionnement, qui augmente la production.

Chaque type a un coût, une durée de vie et un effet mesurable. L'acheteur
l'achète quand le gain actualisé attendu dépasse le prix. Les paiements sont de
vrais transferts entre agents : aucune création de monnaie.
"""

from __future__ import annotations

from economy.intersector import pay_business_suppliers
from economy.models import Company, Resources, SimulationConfig

INFRASTRUCTURE_BUYER_SECTORS = ("food", "goods", "materials")


def present_value_factor(config: SimulationConfig, life_months: float) -> float:
    """Facteur d'actualisation d'un flux mensuel constant sur la durée de vie."""
    rate = max(1e-6, config.tech_discount_rate)
    return (1.0 - (1.0 + rate) ** -max(1.0, life_months)) / rate


def infrastructure_unit_price(
    kind: str,
    buyer: Company,
    config: SimulationConfig,
    resources: Resources,
) -> float:
    spec = config.infrastructure_types[kind]
    basis = spec["cost_basis"]
    if basis == "capacity":
        unit_cost = (
            config.productive_capacity_unit_cost if kind == "production" else spec["cost"]
        )
        base = unit_cost * resources.input_cost_index
    elif basis == "energy_bill":
        base = spec["cost"] * max(0.0, buyer.energy_bill_previous)
    else:
        base = spec["cost"] * max(buyer.revenue_ema, buyer.previous_revenue)
    return base * resources.energy_price_index


def infrastructure_unit_gain(
    kind: str,
    buyer: Company,
    config: SimulationConfig,
) -> float:
    """Gain mensuel attendu d'une unité d'infrastructure pour cet acheteur."""
    spec = config.infrastructure_types[kind]
    if kind == "production":
        # travail économisé : une unité de capacité remplace 1/productivité d'un salarié
        return buyer.wage_offer / max(config.food_productivity, 1.0) * spec["effect"]
    if kind == "reseau":
        gross_bill = buyer.energy_bill_previous / max(1e-6, 1.0 - buyer.energy_saving)
        return gross_bill * spec["effect"]
    revenue_base = max(buyer.revenue_ema, buyer.previous_revenue)
    return revenue_base * config.tech_value_base_share.get(buyer.sector, 0.30) * spec["effect"]


def infrastructure_is_worthwhile(
    kind: str,
    buyer: Company,
    config: SimulationConfig,
    resources: Resources,
) -> bool:
    """Gain attendu (actualisé) supérieur au coût : même critère que les boosts."""
    price = infrastructure_unit_price(kind, buyer, config, resources)
    if price <= 0.0:
        return False
    life = config.infrastructure_types[kind]["life"]
    return infrastructure_unit_gain(kind, buyer, config) * present_value_factor(
        config, life
    ) > price


def pay_infrastructure(
    buyer: Company,
    amount: float,
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Le prix va aux entreprises d'énergie, qui achètent elles-mêmes des matériaux."""
    amount = min(amount, max(0.0, buyer.cash))
    if amount <= 0.0:
        return 0.0
    providers = [c for c in companies if c.active and c.sector == "energy"]
    before = {p.id: p.customer_revenue.get(buyer.id, 0.0) for p in providers}
    paid = pay_business_suppliers(
        buyer, amount, {"energy": 1.0}, companies, resources, priced=False
    )
    buyer.expenses -= amount  # achat capitalisé (amorti ensuite), pas une charge
    for provider in providers:
        # part de la vente reversée en matériaux
        share = provider.customer_revenue.get(buyer.id, 0.0) - before[provider.id]
        if share > 0.0:
            pay_business_suppliers(
                provider,
                share * config.infrastructure_materials_share,
                {"materials": 1.0},
                companies,
                resources,
            )
            provider.infrastructure_sold_value += share
    return paid


def trade_infrastructure(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Ventes de réseau et de stockage aux producteurs (la capacité est traitée à part)."""
    providers = [c for c in companies if c.active and c.sector == "energy"]
    total = 0.0
    if not providers:
        return 0.0
    work_capacity = sum(
        (len(c.employees) + 0.5) * config.infrastructure_work_per_worker
        for c in providers
    ) * resources.input_cost_index
    for buyer in companies:
        if not buyer.active or buyer.sector not in INFRASTRUCTURE_BUYER_SECTORS:
            continue
        reserve = buyer.target_employees * buyer.wage_offer * 1.5
        budget = max(0.0, buyer.cash - reserve) * config.infrastructure_cash_share
        if budget <= 0.0 or buyer.profit <= 0.0:
            continue
        for kind in ("reseau", "stockage"):
            spec = config.infrastructure_types.get(kind)
            if spec is None:
                continue
            level = buyer.grid_level if kind == "reseau" else buyer.storage_level
            room = max(0.0, spec["max_units"] - level)
            price = infrastructure_unit_price(kind, buyer, config, resources)
            if room <= 1e-6 or price <= 0.0:
                continue
            if not infrastructure_is_worthwhile(kind, buyer, config, resources):
                continue
            units = min(room, budget / price, work_capacity / price)
            if units <= 1e-6:
                continue
            payment = units * price
            before = buyer.cash
            pay_infrastructure(buyer, payment, companies, resources, config)
            spent = before - buyer.cash
            if spent <= 0.0:
                continue
            units = spent / price
            buyer.capital_expenditure += spent
            buyer.productive_capital_value += spent
            budget -= spent
            work_capacity -= spent
            total += spent
            if kind == "reseau":
                buyer.grid_level += units
                buyer.energy_saving = min(0.5, buyer.grid_level * spec["effect"])
            else:
                buyer.storage_level += units
    return total


def decay_infrastructure(company: Company, config: SimulationConfig) -> None:
    """Usure des infrastructures (durée de vie)."""
    grid_life = config.infrastructure_types.get("reseau", {}).get("life", 300)
    storage_life = config.infrastructure_types.get("stockage", {}).get("life", 200)
    company.grid_level *= 1.0 - 1.0 / max(1.0, grid_life)
    company.storage_level *= 1.0 - 1.0 / max(1.0, storage_life)
    spec = config.infrastructure_types.get("reseau")
    company.energy_saving = min(
        0.5, company.grid_level * (spec["effect"] if spec else 0.0)
    )


def storage_output_multiplier(company: Company, config: SimulationConfig) -> float:
    spec = config.infrastructure_types.get("stockage")
    if not spec or company.storage_level <= 0.0:
        return 1.0
    return 1.0 + company.storage_level * spec["effect"]


def invest_in_energy_plants(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Les entreprises d'énergie réinvestissent dans leurs installations (secteur capitalistique)."""
    total = 0.0
    for company in companies:
        if not company.active or company.sector != "energy":
            continue
        reserve = company.target_employees * company.wage_offer * 1.5
        amount = min(
            company.previous_revenue * config.energy_capex_share,
            max(0.0, company.cash - reserve) * 0.5,
        )
        if amount <= 1.0:
            continue
        share = config.energy_materials_share
        pay_business_suppliers(
            company,
            amount,
            {"materials": share, "services": 1.0 - share},
            companies,
            resources,
            deduct=True,
        )
        # l'achat est capitalisé : l'usure passe ensuite en charge d'amortissement
        company.expenses -= amount
        company.capital_expenditure += amount
        company.productive_capital_value += amount
        total += amount
    return total
