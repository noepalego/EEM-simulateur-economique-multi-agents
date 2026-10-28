from __future__ import annotations

import random

from economy.models import Company, Person, SimulationConfig


def consolidate_market(
    companies: list[Company],
    people: list[Person],
    config: SimulationConfig,
    rng: random.Random,
    month: int,
) -> int:
    """Profitable large firms can acquire smaller same-sector rivals via a stock swap."""
    completed = 0
    for buyer in sorted(companies, key=lambda company: company.cash, reverse=True):
        if not buyer.active or buyer.profit <= 0.0:
            continue
        if month - buyer.last_acquisition_month < 6:
            continue
        acquisition_chance = config.acquisition_probability * (
            0.5 + buyer.risk_tolerance
        )
        # augmenter la probabilité d'acquisition pour booster la croissance
        acquisition_chance += getattr(config, 'acquisition_probability_boost', 0.0)
        if rng.random() >= acquisition_chance:
            continue
        # Garde-fou de concurrence : pas de fusion qui laisse moins de
        # ``min_firms_per_sector`` entreprises actives dans le secteur (sinon
        # monopoles, prix des matières x7, disparition de l'immobilier).
        sector_firms = sum(
            1 for c in companies if c.active and c.sector == buyer.sector
        )
        if sector_firms <= config.min_firms_per_sector:
            continue
        buyer_margin = buyer.previous_profit / max(buyer.previous_revenue, 1.0)
        targets = [
            target for target in companies
            if target.active
            and target.id != buyer.id
            and target.sector == buyer.sector
            # on n'absorbe qu'un rival moins efficace (marge inférieure)
            and target.previous_profit / max(target.previous_revenue, 1.0) <= buyer_margin
            and target.cash + target.property_inventory * config.housing_price
            <= (
                buyer.cash + buyer.property_inventory * config.housing_price
            ) / max(config.merger_size_ratio, 1.0)
        ]
        if not targets:
            continue
        target = min(
            targets,
            key=lambda company: (
                company.cash
                + company.property_inventory * config.housing_price
                + company.construction_in_progress,
                company.id,
            ),
        )
        _merge_into(buyer, target, people, companies)
        buyer.last_acquisition_month = month
        buyer.acquisition_count += 1
        completed += 1
    return completed


def _merge_into(
    buyer: Company,
    target: Company,
    people: list[Person],
    companies: list[Company],
) -> None:
    issued_target_shares = sum(
        holder.shares.get(target.id, 0.0)
        for holder in [*people, *companies]
        if holder.id not in (target.id, buyer.id)
    )
    exchange_ratio = target.stock_price / max(buyer.stock_price, 1.0)
    new_shares = issued_target_shares * exchange_ratio
    buyer.shares_outstanding += new_shares

    buyer.cash += target.cash
    buyer.debt += target.debt
    buyer.bank_equity += target.bank_equity
    if buyer.sector == "bank" and target.sector == "bank":
        for borrower_id, balance in target.bank_loan_accounts.items():
            buyer.bank_loan_accounts[borrower_id] = (
                buyer.bank_loan_accounts.get(borrower_id, 0.0) + balance
            )
    for lender in companies:
        if lender.sector != "bank" or lender.id == target.id:
            continue
        borrower_balance = lender.bank_loan_accounts.pop(target.id, 0.0)
        if borrower_balance > 0.0:
            lender.bank_loan_accounts[buyer.id] = (
                lender.bank_loan_accounts.get(buyer.id, 0.0)
                + borrower_balance
            )
    buyer.inventory += target.inventory
    buyer.goods_inventory += target.goods_inventory
    buyer.tech_level = min(10.0, buyer.tech_level + target.tech_level * 0.15)
    buyer.property_inventory += target.property_inventory
    buyer.construction_in_progress += target.construction_in_progress
    buyer.construction_pipeline.extend(target.construction_pipeline)
    buyer.target_employees += target.target_employees
    buyer.productivity_trait = min(1.8, (buyer.productivity_trait + target.productivity_trait) / 2.0)
    buyer.innovation = min(1.0, (buyer.innovation + target.innovation) / 2.0)

    for holder in [*people, *companies]:
        if holder.id == target.id:
            continue
        target_shares = holder.shares.pop(target.id, 0.0)
        if target_shares:
            if holder.id != buyer.id:
                holder.shares[buyer.id] = holder.shares.get(buyer.id, 0.0) + target_shares * exchange_ratio

    for company_id, shares in target.shares.items():
        if company_id != target.id:
            if company_id == buyer.id:
                buyer.treasury_shares += shares
            else:
                buyer.shares[company_id] = buyer.shares.get(company_id, 0.0) + shares
    target.shares.clear()
    target.active = False
    target.exit_reason = "acquired"
    target.cash = 0.0
    target.bank_loan_accounts.clear()
    target.debt = 0.0
    target.property_inventory = 0
    target.construction_in_progress = 0.0
    target.construction_pipeline.clear()
    target.employees.clear()
    target.treasury_shares = 0.0
    target.shares_outstanding = 0.0
    target.stock_price = 0.0
    for person in people:
        if person.employer_id == target.id:
            person.employer_id = buyer.id
