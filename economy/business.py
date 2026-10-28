from __future__ import annotations

import random

from economy.energy import (
    infrastructure_is_worthwhile,
    infrastructure_unit_price,
    pay_infrastructure,
)
from economy.finance import extend_credit
from economy.intersector import pay_business_suppliers
from economy.models import Company, Person, Resources, SimulationConfig


def elect_executives(
    companies: list[Company],
    people: list[Person],
    config: SimulationConfig,
    rng: random.Random,
) -> None:
    people_by_id = {person.id: person for person in people}
    for company in companies:
        if not company.active:
            continue
        current = people_by_id.get(company.executive_id or "")
        current_is_employee = current is not None and current.employer_id == company.id
        # CEO indéterminé : ne jamais forcer un remplacement si le CEO actuel est bon
        if current_is_employee and getattr(config, 'ceo_term_indeterminate', True):
            company.ceo_tenure += 1
            current.leadership_experience += 1
            company.ceo_experience += 1
            # rémunération en actions pour les bons CEO
            if company.profit > 0 and company.cash > company.target_employees * company.wage_offer * 1.0:
                equity_bonus = company.profit * getattr(config, 'ceo_equity_bonus_rate', 0.02)
                equity_bonus = min(equity_bonus, company.cash * 0.05)
                if equity_bonus > 0:
                    # attribuer des actions au CEO via dilution sans mouvement de trésorerie
                    shares_to_grant = equity_bonus / max(company.stock_price, 1.0)
                    current.shares[company.id] = current.shares.get(company.id, 0.0) + shares_to_grant
                    company.shares_outstanding += shares_to_grant
                    # Pas de déduction de cash, la rémunération est par dilution
            continue
        # Ancien système pour les termes limités
        term_expired = company.ceo_tenure >= max(1, config.ceo_term_months)
        if current_is_employee and not term_expired:
            company.ceo_tenure += 1
            current.leadership_experience += 1
            company.ceo_experience += 1
            continue

        candidates = [people_by_id[person_id] for person_id in company.employees if person_id in people_by_id]
        if not candidates:
            company.executive_id = None
            company.ceo_tenure = 0
            continue
        candidates.sort(
            key=lambda person: (
                person.skill * (1.0 + min(person.employer_tenure, 120) / 240.0)
                + person.leadership_experience / 600.0
                + rng.uniform(-config.random_promotion_strength, config.random_promotion_strength),
                person.id,
            ),
            reverse=True,
        )
        company.executive_id = candidates[0].id
        company.ceo_tenure = 0
        company.ceo_experience += 1


def apply_competitive_pricing(
    companies: list[Company],
    food_index: float,
    goods_index: float,
    competition_strength: float = 0.12,
    inventory_discount_strength: float = 0.08,
) -> None:
    """Higher pricing aggression trades per-unit margin for a larger share of demand."""
    for company in companies:
        if not company.active:
            continue
        market_index = food_index if company.sector == "food" else goods_index
        if company.sector not in ("food", "goods"):
            continue
        markup = 1.0 + competition_strength * (0.5 - company.pricing_aggression) * 2.0
        scarcity_adjustment = max(
            0.92,
            min(1.08, 1.0 + company.previous_profit / max(company.cash + 10000.0, 1.0) * 0.08),
        )
        inventory = (
            company.inventory if company.sector == "food" else company.goods_inventory
        )
        monthly_capacity = max(1.0, len(company.employees) * 24.0)
        surplus_stock = max(0.0, inventory / monthly_capacity - 1.0)
        inventory_adjustment = max(
            0.75,
            1.0 - max(0.0, inventory_discount_strength) * surplus_stock,
        )
        company.price = max(
            1.0,
            market_index * markup * scarcity_adjustment * inventory_adjustment,
        )


def adjust_profit_sharing_wages(
    companies: list[Company],
    people: list[Person],
    starting_wage: float,
    bonus_rate: float,
    productivity_share: float = 0.35,
    loss_adjustment_strength: float = 0.70,
    profit_margin_target: float = 0.05,
    sector_factors: dict | None = None,
    sector_margin_targets: dict | None = None,
) -> None:
    """Ajuste l'offre salariale de chaque entreprise autour de la référence.

    ``starting_wage`` désigne ici le salaire de référence du marché (nom
    conservé pour compatibilité) : productivité, tension du marché du travail
    et rentabilité, voir ``EconomySimulation._update_wage_reference``.
    L'offre d'une entreprise s'en écarte selon sa productivité mesurée et sa
    marge au-delà de la marge cible (partage des profits) ; une perte la réduit.
    Aucune indexation sur l'IPC.
    """
    people_by_id = {person.id: person for person in people}
    for company in companies:
        if not company.active:
            continue
        profitability = max(-0.4, min(0.30, company.profit / max(company.revenue, 1.0)))
        measured_productivity = company.productivity_trait * (
            1.0 + min(company.tech_level, 10.0) * 0.04
        )
        productivity_premium = (measured_productivity - 1.0) * productivity_share
        if profitability >= 0.0:
            # on ne partage que la marge au-delà de la cible : un secteur peu
            # margé (extraction, banque) paie le salaire de marché, pas moins
            sharing_premium = max(
                0.0,
                profitability
                - (sector_margin_targets or {}).get(company.sector, profit_margin_target),
            ) * 0.5
        else:
            sharing_premium = profitability * loss_adjustment_strength
        # Postes vacants non pourvus : l'entreprise surenchérit pour recruter
        # (pénurie de main-d'œuvre), et seulement si elle en a besoin.
        vacancy_rate = max(
            0.0,
            (company.target_employees - len(company.employees))
            / max(1, company.target_employees),
        )
        vacancy_premium = min(0.30, vacancy_rate * 0.6)
        # Le travail d'extraction est pénible et critique (sans extraction, la
        # production s'arrête) : prime sectorielle de pénibilité.
        sector_premium = 0.05 if company.sector == "materials" else 0.0
        base_wage = starting_wage * (sector_factors or {}).get(company.sector, 1.0)
        premium = max(
            -0.20,
            min(
                0.45,
                productivity_premium
                + sharing_premium
                + vacancy_premium
                + sector_premium,
            ),
        )
        target_offer = base_wage * (1.0 + premium)
        company.wage_offer = max(
            base_wage * 0.70,
            min(
                base_wage * 1.60,
                company.wage_offer + 0.25 * (target_offer - company.wage_offer),
            ),
        )
        if company.profit <= 0.0 or not company.employees:
            continue
        bonus_pool = min(
            company.cash,
            company.profit * bonus_rate * company.reinvestment_preference,
        )
        if bonus_pool <= 0.0:
            continue
        bonus_per_employee = bonus_pool / len(company.employees)
        for employee_id in company.employees:
            person = people_by_id.get(employee_id)
            if person is not None:
                person.cash += bonus_per_employee
                person.income_this_turn += bonus_per_employee
                person.lifetime_earnings += bonus_per_employee
        company.cash -= bonus_pool
        company.expenses += bonus_pool


def invest_in_innovation(
    companies: list[Company],
    rng: random.Random,
    resources: Resources | None = None,
    research_share: float = 0.12,
    outsourced_share: float = 0.0,
) -> float:
    """Retained profits fund research; innovation improves productivity stochastically."""
    total_spent = 0.0
    for company in companies:
        if not company.active or company.profit <= 0.0 or company.reinvestment_preference <= 0.0:
            continue
        research_budget = min(
            company.cash * 0.08,
            company.profit * company.reinvestment_preference * research_share,
        )
        if research_budget < 1.0:
            continue
        company.cash -= research_budget
        company.expenses += research_budget
        if resources is not None:
            # la recherche est en partie commandée au secteur technologique
            pay_business_suppliers(
                company,
                research_budget,
                {"technology": outsourced_share, "services": 1.0 - outsourced_share},
                companies,
                resources,
                deduct=False,
            )
        total_spent += research_budget
        improvement = research_budget / max(company.cash + research_budget, 1.0)
        success_factor = max(0.0, rng.gauss(company.innovation, 0.12))
        company.productivity_trait = min(
            1.8, company.productivity_trait + min(0.015, improvement * success_factor)
        )
    return total_spent


def invest_in_production_capacity(
    companies: list[Company],
    unmet_demand: dict[str, float],
    config: SimulationConfig,
    resources: Resources,
    bank_reserves: float = 0.0,
    policy_rate: float | None = None,
    credit_share: float | None = None,
) -> float:
    """Reinvest profitable cash in durable monthly output capacity.

    L'investissement est déclenché par une demande non satisfaite, ou par une
    pénurie de main-d'œuvre (postes ouverts non pourvus) : le capital se
    substitue alors au travail. Si des réserves bancaires sont disponibles,
    ``credit_financed_share`` de la dépense est empruntée (la banque perçoit
    ensuite des intérêts) ; la part financée sur fonds propres reste régie par
    les mêmes plafonds de profit et de trésorerie.
    """
    total_investment = 0.0
    borrowed = 0.0
    share = config.credit_financed_share if credit_share is None else credit_share
    credit_share = max(0.0, min(0.9, share)) if bank_reserves > 0.0 else 0.0
    for sector in ("food", "goods"):
        producers = [
            company
            for company in companies
            if company.active and company.sector == sector
        ]
        if not producers:
            continue
        for company in producers:
            labor_shortage = company.target_employees - len(company.employees) >= 1
            if unmet_demand.get(sector, 0.0) <= 0.0 and not labor_shortage:
                continue
            if company.profit <= 0.0:
                continue
            wage_reserve = company.wage_offer * max(1, len(company.employees)) * 1.5
            available_cash = max(0.0, company.cash - wage_reserve)
            profit_margin = company.profit / max(company.revenue, 1.0)
            # relancer l'investissement si la marge est faible mais la demande existe
            target_margin = getattr(config, 'profit_margin_target', 0.05)
            if profit_margin < target_margin and unmet_demand.get(sector, 0.0) > 0:
                investment = min(
                    company.profit * config.capacity_expansion_reinvestment_rate * 2.0,
                    available_cash * 0.15,
                )
            else:
                investment = min(
                    company.profit * config.capacity_expansion_reinvestment_rate,
                    available_cash * 0.05,
                )
            unit_cost = infrastructure_unit_price("production", company, config, resources)
            if investment < unit_cost * 0.05:
                continue
            # même critère que les boosts : gain actualisé attendu supérieur au coût
            if not infrastructure_is_worthwhile("production", company, config, resources):
                continue
            if credit_share > 0.0:
                loan = extend_credit(
                    company,
                    investment * credit_share / (1.0 - credit_share),
                    bank_reserves - borrowed,
                    config,
                    policy_rate,
                )
                borrowed += loan
                investment += loan
            before = company.cash
            pay_infrastructure(company, investment, companies, resources, config)
            investment = before - company.cash
            if investment <= 0.0:
                continue
            company.capital_expenditure += investment
            company.production_capacity += investment / unit_cost
            company.productive_capital_value += investment
            total_investment += investment
    return total_investment
