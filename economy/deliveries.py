"""Livraisons physiques : énergie, matériaux et travail des chantiers.

Les unités d'énergie et de travail sont normalisées aux prix de départ ; elles
ne représentent pas des kWh ou des heures observées dans un pays réel.
"""
from __future__ import annotations

from economy.models import Company, Resources, SimulationConfig


def prepare_delivery_month(companies: list[Company], resources: Resources,
                           config: SimulationConfig) -> None:
    """Début de mois : remise à zéro des livraisons, travail de chantier disponible.

    Le travail de chantier est celui des effectifs de services en place. Le taux
    de livraison d'énergie du mois écoulé (``energy_fill`` = reçu / demandé) est
    conservé : une entreprise rationnée produit moins ce mois-ci.
    """
    # bilan complet du mois écoulé, lu par les producteurs qui décident d'investir (R8) :
    # pendant le mois, une partie des achats d'énergie n'a pas encore eu lieu
    resources.previous_energy_unmet = resources.energy_unmet
    resources.previous_energy_refused = resources.energy_refused
    resources.energy_refused = 0.0
    for company in companies:
        if company.sector == "energy":
            company.previous_energy_delivered = company.energy_delivered
            company.previous_energy_left = company.energy_available
    resources.energy_supply = resources.energy_delivered = resources.energy_unmet = 0.0
    resources.energy_unit_price = max(
        1e-9, config.energy_base_price * resources.input_cost_index * resources.energy_price_index
    )
    for company in companies:
        company.energy_fill = (
            min(1.0, company.energy_received / company.energy_requested)
            if company.energy_requested > 1e-9
            else 1.0
        )
        company.energy_available = company.energy_delivered = 0.0
        company.energy_requested = company.energy_received = 0.0
        company.construction_work_available = 0.0
        if company.active and company.sector == "services":
            workers = len(company.employees) * max(0.0, company.productivity_trait)
            company.construction_work_available = workers * config.construction_work_per_worker


def open_energy_supply(companies: list[Company], resources: Resources,
                       config: SimulationConfig) -> None:
    """Énergie produisible ce mois : capacité des centrales, bornée par les effectifs du mois.

    Appelée après les embauches du mois et avant la production.
    """
    resources.energy_supply = 0.0
    for company in companies:
        if not company.active or company.sector != "energy":
            continue
        workers = len(company.employees) * max(0.0, company.productivity_trait)
        company.energy_available = max(0.0, min(
            company.energy_capacity, workers * config.energy_output_per_worker
        ) * resources.energy_capacity_multiplier)
        resources.energy_supply += company.energy_available


def available_energy(companies: list[Company]) -> float:
    return sum(c.energy_available for c in companies if c.active and c.sector == "energy")


def credit_supplier(buyer: Company, supplier: Company, payment: float) -> None:
    supplier.cash += payment
    supplier.revenue += payment
    supplier.customer_revenue[buyer.id] = supplier.customer_revenue.get(buyer.id, 0.0) + payment
    buyer.supplier_spending[supplier.id] = buyer.supplier_spending.get(supplier.id, 0.0) + payment


def deliver_energy(buyer: Company, wanted: float, budget: float,
                   companies: list[Company], resources: Resources) -> float:
    """Livre et crédite les fournisseurs ; le débit de l'acheteur est fait par l'appelant."""
    suppliers = [c for c in companies if c.active and c.sector == "energy" and c.id != buyer.id]
    supply = sum(c.energy_available for c in suppliers)
    wanted = max(0.0, wanted)
    quantity = min(wanted, supply, max(0.0, budget) / resources.energy_unit_price)
    # commandes refusées faute d'offre (pas celles que l'acheteur ne pouvait pas payer)
    resources.energy_refused += max(0.0, min(wanted, max(0.0, budget) / resources.energy_unit_price) - supply)
    buyer.energy_requested += wanted
    buyer.energy_received += quantity
    resources.energy_unmet += wanted - quantity
    resources.energy_delivered += quantity
    if supply > 0.0:
        for supplier in suppliers:
            units = quantity * supplier.energy_available / supply
            supplier.energy_available = max(0.0, supplier.energy_available - units)
            supplier.energy_delivered += units
            credit_supplier(buyer, supplier, units * resources.energy_unit_price)
    return quantity * resources.energy_unit_price


def deliver_capital_work(buyer: Company, reference_value: float, material_share: float,
                         companies: list[Company], resources: Resources,
                         config: SimulationConfig) -> tuple[float, float]:
    """Livre un lot de matériaux et de travail complémentaires, payé simultanément.

    Retourne (ouvrage aux prix de départ, dépense nominale). Aucun fournisseur
    absent n'est remplacé par un transfert au fonds agrégé.
    """
    material_share = max(0.0, min(1.0, material_share))
    materials = [c for c in companies if c.active and c.sector == "materials"]
    services = [c for c in companies if c.active and c.sector == "services"]
    capacities = [max(0.0, len(c.employees) * config.raw_material_extraction_productivity
                      - c.materials_supplied_this_turn) for c in materials]
    material_capacity = min(sum(capacities), max(0.0, resources.raw_materials)
                            + max(0.0, resources.recycled_materials))
    service_capacity = sum(c.construction_work_available for c in services)
    materials_per_value = material_share / max(config.raw_material_unit_price, 1e-9)
    work_per_value = 1.0 - material_share
    unit_cost = (materials_per_value * resources.raw_material_price
                 + work_per_value * resources.input_cost_index)
    delivered = min(max(0.0, reference_value), max(0.0, buyer.cash) / max(unit_cost, 1e-9))
    if materials_per_value > 0.0:
        delivered = min(delivered, material_capacity / materials_per_value)
    if work_per_value > 0.0:
        delivered = min(delivered, service_capacity / work_per_value)
    if delivered <= 1e-9:
        return 0.0, 0.0
    quantity = delivered * materials_per_value
    work = delivered * work_per_value
    spent = delivered * unit_cost
    buyer.cash -= spent
    recycled = min(quantity, max(0.0, resources.recycled_materials))
    resources.recycled_materials -= recycled
    resources.raw_materials -= quantity - recycled
    total_capacity = sum(capacities)
    for supplier, capacity in zip(materials, capacities):
        units = quantity * capacity / total_capacity if total_capacity else 0.0
        supplier.materials_supplied_this_turn += units
        supplier.units_sold += units
        credit_supplier(buyer, supplier, units * resources.raw_material_price)
    for supplier in services:
        units = work * supplier.construction_work_available / service_capacity if service_capacity else 0.0
        supplier.construction_work_available = max(0.0, supplier.construction_work_available - units)
        credit_supplier(buyer, supplier, units * resources.input_cost_index)
    return delivered, spent
