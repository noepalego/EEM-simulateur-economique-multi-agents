"""Demande inter-entreprises (B2B) : matrice entrées-sorties simple.

Chaque entreprise achète à d'autres entreprises (énergie, services
numériques, locaux, matières premières) une part de son activité. Ces achats
sont de vrais paiements entre agents : la trésorerie de l'acheteur diminue
exactement de ce que reçoit le fournisseur (aucune création de monnaie). Un
secteur fournisseur sans entreprise active renvoie le paiement au secteur amont
agrégé (``resources.market_cash``), qui le reverse en salaires.
"""

from __future__ import annotations

from economy.models import Company, Resources, SimulationConfig


def _supplier_weight(company: Company) -> float:
    return (
        (len(company.employees) + 1.0)
        * max(0.25, company.productivity_trait)
        * company.entry_boost
    )


def pay_business_suppliers(
    buyer: Company,
    amount: float,
    split: dict[str, float],
    companies: list[Company],
    resources: Resources,
    deduct: bool = True,
    priced: bool = True,
) -> float:
    """Répartit ``amount`` entre les secteurs fournisseurs selon ``split``.

    Retourne le montant total payé (entreprises fournisseuses et secteur amont).
    Avec ``deduct=False`` l'appelant a déjà débité l'acheteur.
    """
    if amount <= 0.0:
        return 0.0
    if deduct:
        amount = min(amount, max(0.0, buyer.cash))
        if amount <= 0.0:
            return 0.0
        buyer.cash -= amount
        buyer.expenses += amount
    total_share = sum(max(0.0, share) for share in split.values())
    if total_share <= 0.0:
        resources.market_cash += amount
        return amount
    paid_total = 0.0
    for sector, share in split.items():
        part = amount * max(0.0, share) / total_share
        if part <= 0.0:
            continue
        # prix du secteur fournisseur (régulé par sa marge) et économies de réseau
        factor = {
            "energy": resources.energy_price_index * (1.0 - buyer.energy_saving),
            "technology": resources.tech_price_index,
        }.get(sector, 1.0) if priced else 1.0
        if factor != 1.0:
            delta = part * (factor - 1.0)
            if delta > 0.0:
                delta = min(delta, max(0.0, buyer.cash))
            buyer.cash -= delta
            buyer.expenses += delta
            part += delta
        if sector == "energy":
            buyer.energy_bill += part
        suppliers = [
            c for c in companies
            if c.active and c.sector == sector and c.id != buyer.id
        ] if sector != "services" else []
        paid_total += part
        if not suppliers:
            resources.market_cash += part
            continue
        weights = [_supplier_weight(c) for c in suppliers]
        total_weight = sum(weights)
        for supplier, weight in zip(suppliers, weights):
            payment = part * weight / total_weight
            supplier.cash += payment
            supplier.revenue += payment
            supplier.customer_revenue[buyer.id] = (
                supplier.customer_revenue.get(buyer.id, 0.0) + payment
            )
            buyer.supplier_spending[supplier.id] = (
                buyer.supplier_spending.get(supplier.id, 0.0) + payment
            )
    return paid_total


def settle_operating_inputs(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Achats courants des secteurs hors alimentation / biens (part du CA du mois précédent)."""
    total = 0.0
    for buyer in companies:
        if not buyer.active:
            continue
        shares = config.operating_io.get(buyer.sector)
        if not shares or buyer.previous_revenue <= 0.0:
            continue
        amount = buyer.previous_revenue * sum(shares.values())
        total += pay_business_suppliers(buyer, amount, shares, companies, resources)
    return total
