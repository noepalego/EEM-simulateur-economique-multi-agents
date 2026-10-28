from __future__ import annotations

from economy.models import Company, Person


def collect_income_taxes_and_pay_benefits(
    people: list[Person],
    government_balance: float,
    tax_rate: float,
    unemployment_benefit: float,
    unemployment_rate: float = 0.0,
    price_index: float = 100.0,
    cycle_sensitivity: float = 0.0,
    progressive_threshold: float = 0.0,
    top_marginal_rate: float = 0.0,
) -> tuple[float, float, float]:
    taxes_collected = 0.0
    benefits_paid = 0.0
    for person in people:
        taxable_income = max(0.0, person.income_this_turn)
        threshold = max(0.0, progressive_threshold)
        person.taxes_paid = (
            min(taxable_income, threshold) * tax_rate
            + max(0.0, taxable_income - threshold)
            * max(tax_rate, top_marginal_rate)
        )
        person.cash -= person.taxes_paid
        person.expenses_this_turn += person.taxes_paid
        person.cumulative_expenses += person.taxes_paid
        taxes_collected += person.taxes_paid
    available_funds = government_balance + taxes_collected
    benefit_per_person = (
        unemployment_benefit
        * max(0.0, price_index)
        / 100.0
        * (1.0 + max(0.0, cycle_sensitivity) * max(0.0, min(1.0, unemployment_rate)))
    )
    unemployed = [person for person in people if person.employer_id is None]
    total_entitlement = benefit_per_person * len(unemployed)
    benefit_per_recipient = (
        min(max(0.0, available_funds), total_entitlement) / len(unemployed)
        if unemployed
        else 0.0
    )
    for person in unemployed:
        person.cash += benefit_per_recipient
        person.income_this_turn += benefit_per_recipient
        person.lifetime_earnings += benefit_per_recipient
        benefits_paid += benefit_per_recipient
    return taxes_collected, benefits_paid, government_balance + taxes_collected - benefits_paid


def buy_public_goods(
    companies: list[Company],
    budget: float,
    max_stock_share: float = 0.5,
) -> tuple[float, float, float]:
    """L'État achète des biens et services aux entreprises avec son solde.

    Achats proportionnels aux stocks invendus d'alimentation et de biens
    courants, au prix de chaque entreprise (consommation publique). La monnaie
    passe de l'État aux entreprises : rien n'est créé et rien n'est mis de côté.
    Retourne (montant dépensé, unités alimentaires, unités de biens).
    """
    sellers = [
        company
        for company in companies
        if company.active and company.sector in ("food", "goods")
    ]

    def stock(company: Company) -> float:
        quantity = (
            company.inventory if company.sector == "food" else company.goods_inventory
        )
        return max(0.0, quantity) * max(0.0, min(1.0, max_stock_share))

    available_value = sum(stock(company) * company.price for company in sellers)
    spend = min(max(0.0, budget), available_value)
    if spend <= 0.0 or available_value <= 0.0:
        return 0.0, 0.0, 0.0
    fraction = spend / available_value
    food_units = goods_units = 0.0
    for company in sellers:
        quantity = stock(company) * fraction
        if quantity <= 0.0:
            continue
        payment = quantity * company.price
        if company.sector == "food":
            company.inventory -= quantity
            food_units += quantity
        else:
            company.goods_inventory -= quantity
            goods_units += quantity
        company.cash += payment
        company.revenue += payment
        company.units_sold += quantity
    return spend, food_units, goods_units


def pay_public_transfers(people: list[Person], budget: float) -> float:
    """Redistribue le solde public disponible en allocation égale par ménage.

    Dernier poste du budget annuel équilibré : ce que l'État n'a pu ni verser
    en allocations chômage ni dépenser en achats de biens est reversé aux
    ménages (prestations sociales), au lieu de s'accumuler. Transfert pur :
    ni création ni destruction de monnaie.
    """
    if budget <= 0.0 or not people:
        return 0.0
    share = budget / len(people)
    for person in people:
        person.cash += share
        person.lifetime_earnings += share
    return budget


def tax_company_profits(companies: list[Company], tax_rate: float) -> float:
    collected = 0.0
    for company in companies:
        if not company.active:
            continue
        taxable_profit = max(0.0, company.revenue - company.expenses)
        tax = min(max(0.0, company.cash), taxable_profit * tax_rate)
        company.cash -= tax
        company.expenses += tax
        collected += tax
    return collected
