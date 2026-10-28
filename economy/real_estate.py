from __future__ import annotations

import random

from economy.finance import extend_credit
from economy.intersector import pay_business_suppliers
from economy.models import Company, Person, Resources, SimulationConfig


UNIT_COST = 70000.0
UNIT_PRICE = 120000.0


def build_housing(
    companies: list[Company],
    resources: Resources,
    people: list[Person] | None = None,
    month: int = 0,
    config: SimulationConfig | None = None,
    housing_demand_gap: int | None = None,
    bank_reserves: float = 0.0,
    policy_rate: float | None = None,
    credit_share: float | None = None,
) -> int:
    config = config or SimulationConfig()
    built = 0
    remaining_gap = housing_demand_gap
    share = config.credit_financed_share if credit_share is None else credit_share
    credit_share = max(0.0, min(0.9, share)) if bank_reserves > 0.0 else 0.0
    borrowed = 0.0
    for company in companies:
        if not company.active or company.sector != "real_estate" or resources.land_units <= 0:
            continue
        # Le chantier est payé au fil des mois (voir pay_construction_progress) :
        # les engagements restants des chantiers déjà lancés réduisent la
        # trésorerie réellement disponible pour en lancer un nouveau.
        monthly_work = config.housing_build_cost / max(1, config.build_time_months)
        committed = (
            sum(max(0, done - month) for done in company.construction_pipeline)
            * monthly_work
        )
        free_cash = company.cash - committed
        # permettre la construction spéculative même sans pénurie immédiate
        if remaining_gap is not None and remaining_gap <= 0:
            # pas de construction spéculative : le parc suit la demande de
            # logement (sinon surproduction, dette et prix qui s'effondrent)
            continue
        own_cost = config.housing_build_cost * (1.0 - credit_share)
        if free_cash < own_cost + config.starting_wage * 2:
            continue
        units = min(
            2 if remaining_gap is None else 1,
            resources.land_units,
            int((free_cash - config.starting_wage * 2) // max(own_cost, 1.0)),
        )
        if remaining_gap is not None:
            units = min(units, remaining_gap)
        if units > 0 and credit_share > 0.0:
            # Le promoteur finance une part du chantier par crédit bancaire
            borrowed += extend_credit(
                company,
                units * config.housing_build_cost * credit_share,
                bank_reserves - borrowed,
                config,
                policy_rate,
            )
            units = min(
                units,
                int(
                    (company.cash - committed - config.starting_wage * 2)
                    // config.housing_build_cost
                ),
            )
        if units <= 0:
            continue
        company.capital_expenditure += units * config.housing_build_cost
        company.construction_pipeline.extend([month + max(1, config.build_time_months)] * units)
        resources.land_units -= units
        built += units
        if remaining_gap is not None:
            remaining_gap -= units
    return built


def pay_construction_progress(
    companies: list[Company],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    """Paie chaque mois l'avancement des chantiers au secteur amont.

    Un logement coûte ``housing_build_cost`` réparti sur ``build_time_months``
    mois de travail (salaires du bâtiment) : le coût n'arrive plus d'un bloc au
    lancement. Les sommes versées sont capitalisées en construction en cours.
    """
    monthly_work = config.housing_build_cost / max(1, config.build_time_months)
    total = 0.0
    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        due = len(company.construction_pipeline) * monthly_work
        paid = min(max(0.0, company.cash), due)
        if paid <= 0.0:
            continue
        company.cash -= paid
        company.construction_in_progress += paid
        # le chantier achète des matériaux ; le reste paie les corps de métier (amont)
        share = config.construction_materials_share
        pay_business_suppliers(
            company,
            paid,
            {"materials": share, "services": 1.0 - share},
            companies,
            resources,
            deduct=False,
        )
        total += paid
    return total


def complete_construction(companies: list[Company], month: int, config: SimulationConfig) -> int:
    completed = 0
    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        due = [completion_month for completion_month in company.construction_pipeline if completion_month <= month]
        if due:
            company.property_inventory += len(due)
            company.construction_in_progress = max(
                0.0, company.construction_in_progress - len(due) * config.housing_build_cost
            )
            company.construction_pipeline = [
                completion_month for completion_month in company.construction_pipeline
                if completion_month > month
            ]
            completed += len(due)
    return completed


def allocate_housing_and_collect_rent(
    companies: list[Company],
    people: list[Person],
    monthly_rent: float,
    rent_income_share: float = 1.0,
) -> float:
    property_firms = [company for company in companies if company.active and company.sector == "real_estate"]
    # Seules les entreprises immobilières louent : un logement détenu par un
    # ménage est un patrimoine ou une résidence, il n'entre pas dans l'offre locative.
    landlords: list[tuple[str, int, Company | Person]] = []
    for company in property_firms:
        landlords.append((company.id, company.property_inventory, company))

    tenants = [person for person in people if not person.owns_home]
    occupancy = {owner_id: 0 for owner_id, _, _ in landlords}
    for tenant in tenants:
        owner_id = tenant.rental_owner_id
        if owner_id not in occupancy:
            tenant.rental_owner_id = None
            owner_id = None
        if owner_id is None or occupancy[owner_id] >= next(
            (capacity for landlord_id, capacity, _ in landlords if landlord_id == owner_id), 0
        ):
            owner_id = next(
                (landlord_id for landlord_id, capacity, _ in landlords
                 if occupancy[landlord_id] < capacity),
                None,
            )
            tenant.rental_owner_id = owner_id
        if owner_id is not None:
            occupancy[owner_id] += 1

    revenue = 0.0
    landlord_by_id = {owner_id: owner for owner_id, _, owner in landlords}
    for tenant in tenants:
        owner_id = tenant.rental_owner_id
        if owner_id is None:
            continue
        payment = min(
            tenant.cash,
            monthly_rent,
            max(0.0, tenant.income_this_turn * rent_income_share),
        )
        tenant.cash -= payment
        tenant.expenses_this_turn += payment
        tenant.cumulative_expenses += payment
        owner = landlord_by_id[owner_id]
        owner.cash += payment
        revenue += payment
        if isinstance(owner, Company):
            owner.revenue += payment
    return revenue


def maintain_housing(
    companies: list[Company],
    people: list[Person],
    resources: Resources,
    config: SimulationConfig,
) -> float:
    maintenance_per_home = (
        config.housing_price * config.housing_maintenance_rate
    )
    total_paid = 0.0

    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        homes = company.property_inventory
        maintenance = homes * maintenance_per_home
        paid = min(max(0.0, company.cash), maintenance)
        if paid <= 0.0:
            continue
        company.cash -= paid
        company.expenses += paid
        resources.market_cash += paid
        total_paid += paid

    for person in people:
        homes = int(person.owns_home) + person.investment_homes
        maintenance = homes * maintenance_per_home
        paid = min(max(0.0, person.cash), maintenance)
        if paid <= 0.0:
            continue
        person.cash -= paid
        person.expenses_this_turn += paid
        person.cumulative_expenses += paid
        resources.market_cash += paid
        total_paid += paid

    return total_paid


def buy_homes(
    companies: list[Company],
    people: list[Person],
    unit_price: float = UNIT_PRICE,
    unit_cost: float = UNIT_COST,
    config: SimulationConfig | None = None,
    bank_reserves: float = 0.0,
    rng: random.Random | None = None,
) -> tuple[int, float]:
    config = config or SimulationConfig(
        housing_price=unit_price,
        housing_build_cost=unit_cost,
    )
    sellers = [
        company for company in companies
        if company.active and company.sector == "real_estate"
    ]
    purchases = 0
    loans = 0.0
    for person in people:
        for seller in sellers:
            if seller.property_inventory <= 0:
                break
            loan = 0.0
            primary_purchase = (
                not person.owns_home
                and person.mortgage_debt <= 0.0
                and person.income_this_turn > 0.0
            )
            if primary_purchase and person.cash < unit_price:
                payment_budget = (
                    person.income_this_turn
                    * config.mortgage_payment_income_share
                )
                loan = min(
                    max(0.0, bank_reserves - loans),
                    _mortgage_principal_for_payment(
                        payment_budget,
                        config.mortgage_interest_rate,
                        config.mortgage_term_months,
                    ),
                    unit_price * (1.0 - config.mortgage_down_payment_rate),
                )
                primary_purchase = (
                    loan > 0.0
                    and person.cash >= unit_price - loan
                )
            elif primary_purchase:
                loan = 0.0
            if person.cash >= unit_price:
                primary_purchase = not person.owns_home
                loan = 0.0
            # patrimoine : pas de plafond arbitraire, seulement le capital disponible
            investment_purchase = (
                person.cash >= unit_price * config.home_investment_cash_multiple
                and (rng is None or rng.random() < config.home_investment_probability)
            )
            if not primary_purchase and not investment_purchase:
                continue
            down_payment = unit_price - loan
            person.cash -= down_payment
            person.cumulative_expenses += unit_price
            person.expenses_this_turn += unit_price
            seller.cash += unit_price
            seller.revenue += unit_price
            seller.expenses += unit_cost
            seller.property_cost_sold_this_turn += unit_cost
            seller.property_inventory -= 1
            if primary_purchase:
                person.owns_home = True
                person.rental_owner_id = None
                person.mortgage_debt = loan
                person.mortgage_rate = config.mortgage_interest_rate
                person.mortgage_months_remaining = (
                    config.mortgage_term_months if loan > 0.0 else 0
                )
                person.mortgage_payment = _mortgage_monthly_payment(
                    loan,
                    person.mortgage_rate,
                    person.mortgage_months_remaining,
                )
                loans += loan
            else:
                person.investment_homes += 1
            purchases += 1
    return purchases, loans


def service_mortgages(
    people: list[Person],
) -> float:
    received = 0.0
    for person in people:
        if person.mortgage_debt <= 0.0:
            continue
        interest_due = person.mortgage_debt * person.mortgage_rate
        scheduled_payment = min(
            person.mortgage_payment,
            person.mortgage_debt + interest_due,
        )
        payment = min(max(0.0, person.cash), scheduled_payment)
        person.cash -= payment
        person.expenses_this_turn += payment
        person.cumulative_expenses += payment
        person.mortgage_debt = max(
            0.0,
            person.mortgage_debt + interest_due - payment,
        )
        person.mortgage_months_remaining = max(
            0, person.mortgage_months_remaining - 1
        )
        received += payment
        if person.mortgage_debt <= 0.01:
            person.mortgage_debt = 0.0
            person.mortgage_payment = 0.0
            person.mortgage_months_remaining = 0
        elif person.mortgage_months_remaining > 0:
            person.mortgage_payment = _mortgage_monthly_payment(
                person.mortgage_debt,
                person.mortgage_rate,
                person.mortgage_months_remaining,
            )
        else:
            person.mortgage_payment = person.mortgage_debt + (
                person.mortgage_debt * person.mortgage_rate
            )
    return received


def _mortgage_principal_for_payment(
    payment: float,
    monthly_rate: float,
    months: int,
) -> float:
    if payment <= 0.0 or months <= 0:
        return 0.0
    if monthly_rate <= 0.0:
        return payment * months
    return payment * (1.0 - (1.0 + monthly_rate) ** -months) / monthly_rate


def _mortgage_monthly_payment(
    principal: float,
    monthly_rate: float,
    months: int,
) -> float:
    if principal <= 0.0 or months <= 0:
        return 0.0
    if monthly_rate <= 0.0:
        return principal / months
    return principal * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -months)
