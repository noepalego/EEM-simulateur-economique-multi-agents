"""Demande inter-entreprises (B2B) : matrice entrées-sorties simple.

Chaque entreprise achète à d'autres entreprises (énergie, services
numériques, locaux, matières premières) une part de son activité. Ces achats
sont de vrais paiements entre agents : la trésorerie de l'acheteur diminue
exactement de ce que reçoit le fournisseur (aucune création de monnaie). Seuls les
fournisseurs qui ont des salariés livrent et encaissent ; sans eux, la commande n'est ni servie
ni payée (l'acheteur garde son argent, la pénurie est mesurée dans ``resources.refused_orders``).
"""

from __future__ import annotations

from economy import diagnostics
from economy.models import Company, Resources, SimulationConfig
from economy.deliveries import deliver_energy


def _delivering_suppliers(suppliers: list[Company]) -> tuple[list[Company], list[float]]:
    """Fournisseurs qui peuvent livrer : ceux qui ont des salariés, au prorata de leur
    effectif × productivité ; une entreprise sans salarié ne rend aucun service et n'encaisse
    rien."""
    staffed = [c for c in suppliers if c.employees]
    return staffed, [len(c.employees) * max(0.25, c.productivity_trait) for c in staffed]


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

    ``amount`` est le montant à prix de base. Avec ``resources.b2b_price_elasticity``
    > 0, le volume acheté d'énergie et de services technologiques varie comme
    (indice de prix)^(−élasticité) : au-delà de 1, une hausse de prix réduit la
    dépense de l'acheteur, et le fournisseur ne peut plus élever son prix sans perdre
    de recettes. À 0, volume fixe (ancien comportement).
    """
    price_elasticity = resources.b2b_price_elasticity
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
        # aucun fournisseur désigné : rien n'est livré, l'acheteur garde son argent
        buyer.cash += amount
        buyer.expenses -= amount
        diagnostics.flow("commande_b2b_non_servie", amount)
        return 0.0
    paid_total = 0.0
    for sector, share in split.items():
        part = amount * max(0.0, share) / total_share
        if part <= 0.0:
            continue
        if sector == "energy" and priced:
            desired_payment = part * resources.energy_price_index * (1.0 - buyer.energy_saving)
            if price_elasticity > 0.0:
                desired_payment *= resources.energy_price_index ** -price_elasticity
            budget = min(desired_payment, part + max(0.0, buyer.cash))
            paid = deliver_energy(buyer, desired_payment / resources.energy_unit_price,
                                  budget, companies, resources)
            # Le montant de base a déjà été débité ; ne payer que l'énergie livrée.
            buyer.cash += part - paid
            buyer.expenses += paid - part
            buyer.energy_bill += paid
            paid_total += paid
            continue
        # prix du secteur fournisseur (régulé par sa marge) et économies de réseau
        factor = {
            "energy": resources.energy_price_index * (1.0 - buyer.energy_saving),
            "technology": resources.tech_service_price_index,
        }.get(sector, 1.0) if priced else 1.0
        if price_elasticity > 0.0 and sector in ("energy", "technology") and priced:
            index = resources.energy_price_index if sector == "energy" else resources.tech_service_price_index
            factor *= index ** -price_elasticity
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
        ]
        suppliers, weights = _delivering_suppliers(suppliers)
        if not suppliers:
            # commande non servie : pas de paiement, l'acheteur garde son argent
            buyer.cash += part
            buyer.expenses -= part
            if sector == "energy":
                buyer.energy_bill -= part
            resources.refused_orders[sector] = resources.refused_orders.get(sector, 0.0) + part
            diagnostics.flow("commande_b2b_non_servie", part)
            continue
        paid_total += part
        total_weight = sum(weights)
        tracking = diagnostics.active()  # compteurs inactifs : aucun appel dans la boucle
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
            if tracking:
                diagnostics.flow("recette_b2b_au_poids", payment)
                if not supplier.employees:
                    diagnostics.flow("recette_b2b_sans_salarie", payment)
    return paid_total


def household_suppliers(companies: list[Company], sectors) -> dict:
    """Index local à un lot sans embauche ni fermeture ; ordre des fournisseurs inchangé."""
    return {sector: _delivering_suppliers(
        [c for c in companies if c.active and c.sector == sector]
    ) for sector in sectors}


def pay_suppliers_from_household(
    person,
    amount: float,
    split: dict[str, float],
    companies: list[Company],
    resources: Resources,
    suppliers_by_sector: dict | None = None,
) -> float:
    """Un ménage achète des services (formation, entretien) à des entreprises."""
    amount = min(max(0.0, amount), max(0.0, person.cash))
    if amount <= 0.0:
        return 0.0
    person.cash -= amount
    person.expenses_this_turn += amount
    person.cumulative_expenses += amount
    total_share = sum(max(0.0, s) for s in split.values())
    unserved = 0.0
    for sector, share in split.items():
        part = amount * max(0.0, share) / max(total_share, 1e-9)
        if suppliers_by_sector is None:
            suppliers = [c for c in companies if c.active and c.sector == sector]
            suppliers, weights = _delivering_suppliers(suppliers)
        else:
            suppliers, weights = suppliers_by_sector[sector]
        if not suppliers:
            # service non rendu : le ménage garde son argent
            person.cash += part
            person.expenses_this_turn -= part
            person.cumulative_expenses -= part
            unserved += part
            resources.refused_orders[sector] = resources.refused_orders.get(sector, 0.0) + part
            diagnostics.flow("achat_menage_non_servi", part)
            continue
        total_weight = sum(weights)
        tracking = diagnostics.active()  # compteurs inactifs : aucun appel dans la boucle
        person_id = person.id
        for supplier, weight in zip(suppliers, weights):
            payment = part * weight / total_weight
            supplier.cash += payment
            supplier.revenue += payment
            revenue = supplier.customer_revenue
            revenue[person_id] = revenue.get(person_id, 0.0) + payment
            if tracking:
                diagnostics.flow("recette_menages_au_poids", payment)
                if not supplier.employees:
                    diagnostics.flow("recette_menages_sans_salarie", payment)
    return amount - unserved


def settle_operating_inputs(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Achats courants des secteurs hors alimentation / biens : part du chiffre d'affaires du
    mois précédent, ventes de machines et d'infrastructures comprises."""
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
