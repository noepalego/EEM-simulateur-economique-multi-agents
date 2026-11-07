from __future__ import annotations

import random
from contextlib import nullcontext

from economy.finance import borrowing_share, extend_credit, new_loan_rate
from economy.intersector import pay_business_suppliers, pay_suppliers_from_household, household_suppliers
from economy.models import Company, ConstructionProject, Person, Resources, SimulationConfig
from economy.payments import transfer_cash
from economy.deliveries import deliver_capital_work


UNIT_COST = SimulationConfig.housing_build_cost  # défauts de la config (une seule source)
UNIT_PRICE = SimulationConfig.housing_price


def build_housing(
    companies: list[Company],
    resources: Resources,
    people: list[Person] | None = None,
    month: int = 0,
    config: SimulationConfig | None = None,
    bank_reserves: float = 0.0,
    policy_rate: float | None = None,
    expected_inflation: float | None = None,
    monthly_rent: float | None = None,
    credit_transaction=None,
    credit_capacity=None,
) -> int:
    """Lance des chantiers selon la pénurie de logements.

    Avec ``expected_inflation`` et ``monthly_rent``, la part empruntée est la
    décision du promoteur : rendement locatif attendu d'un logement neuf
    (loyer net d'entretien rapporté au coût de construction, plus l'inflation
    anticipée) comparé au coût du crédit (``finance.borrowing_share``) ; sinon
    ``credit_financed_share``.
    """
    config = config or SimulationConfig()
    built = 0
    housing_return = None
    if expected_inflation is not None and monthly_rent is not None:
        net_rent = monthly_rent - config.housing_price * config.housing_maintenance_rate
        housing_return = 12.0 * (
            net_rent / max(config.housing_build_cost, 1.0) + expected_inflation
        )
    borrowed = 0.0
    # chaque promoteur suit sa propre prévision (``Company.housing_gap``, economy.expectations)
    # et ses propres anticipations de loyers ; il ne voit pas le déficit total ni les chantiers
    # des autres (surinvestissement possible)
    for company in companies:
        if not company.active or company.sector != "real_estate" or resources.land_units <= 0:
            continue
        company_return = housing_return
        if housing_return is not None:
            company_return = 12.0 * (
                net_rent / max(config.housing_build_cost, 1.0) + company.expected_cost_growth
            )
        if (
            company.housing_gap <= 0
            or (company_return is not None
                and company_return <= 12.0 * new_loan_rate(config, policy_rate, company))
        ):
            continue
        credit_share = 0.0
        if bank_reserves > 0.0:
            credit_share = (
                config.credit_financed_share
                if company_return is None
                else borrowing_share(company_return, policy_rate, config, company)
            )
        # Le chantier est payé au fil des mois (voir pay_construction_progress) :
        # les engagements restants des chantiers déjà lancés réduisent la
        # trésorerie réellement disponible pour en lancer un nouveau.
        committed = sum(project.remaining_cost for project in company.construction_pipeline)
        free_cash = company.cash - committed
        own_cost = config.housing_build_cost * (1.0 - credit_share)
        if free_cash < own_cost + config.starting_wage * 2:
            continue
        units = min(
            company.housing_gap,
            resources.land_units,
            int((free_cash - config.starting_wage * 2) // max(own_cost, 1.0)),
        )
        if units > 0 and credit_share > 0.0:
            # Le promoteur finance une part du chantier par crédit bancaire
            borrowed += extend_credit(
                company,
                units * config.housing_build_cost * credit_share,
                bank_reserves - borrowed,
                config,
                policy_rate,
                credit_transaction=credit_transaction,
                credit_capacity=credit_capacity,
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
        company.construction_pipeline.extend(
            ConstructionProject(month + max(1, config.build_time_months),
                                config.housing_build_cost, max(1, config.build_time_months),
                                reference_cost=config.housing_build_cost / max(resources.input_cost_index, 1e-9))
            for _ in range(units)
        )
        resources.land_units -= units
        built += units
        company.housing_gap -= units
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
    total = 0.0
    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        for project in company.construction_pipeline:
            reference = project.reference_cost or project.total_cost
            wanted = min(1.0 / project.duration, max(0.0, 1.0 - project.work_done))
            delivered, paid = deliver_capital_work(
                company, wanted * reference, config.construction_materials_share,
                companies, resources, config,
            )
            project.work_done = min(1.0, project.work_done + delivered / max(reference, 1e-9))
            project.paid += paid
            company.construction_in_progress += paid
            company.capital_expenditure += paid
            company.construction_spending += paid
            total += paid
    return total


def complete_construction(companies: list[Company], month: int, config: SimulationConfig) -> int:
    completed = 0
    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        due = [p for p in company.construction_pipeline
               if p.completion_month <= month and p.work_done >= 1.0 - 1e-9]
        if due:
            company.property_inventory += len(due)
            company.construction_in_progress = max(
                0.0, company.construction_in_progress - sum(p.paid for p in due)
            )
            company.construction_pipeline = [
                project for project in company.construction_pipeline if project not in due
            ]
            completed += len(due)
    return completed


def allocate_housing_and_collect_rent(
    companies: list[Company],
    people: list[Person],
    monthly_rent: float,
    rent_income_share: float = 1.0,
) -> float:
    """Attribue les logements locatifs et encaisse les loyers ; retourne les loyers reçus.

    Un ménage qui détient des logements-patrimoine les loue aussi au loyer du marché, après le
    parc des promoteurs ; le loyer est un revenu du ménage bailleur.
    """
    property_firms = [company for company in companies if company.active and company.sector == "real_estate"]
    # parc des promoteurs d'abord, puis logements-patrimoine des ménages
    landlords: list[tuple[str, int, Company | Person]] = []
    for company in property_firms:
        landlords.append((company.id, company.property_inventory, company))
    for person in people:
        if person.investment_homes > 0:
            landlords.append((person.id, person.investment_homes, person))

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
    for company in property_firms:
        company.occupied_units = occupancy.get(company.id, 0)  # signal du promoteur
    for person in people:
        person.rented_homes = occupancy.get(person.id, 0) if person.investment_homes > 0 else 0
    # demandes de location : un ménage resté sans logement dépose une demande, réparties à parts
    # égales entre les promoteurs dans l'ordre de la liste (aucun tirage). Chaque promoteur ne
    # voit que les demandes qu'il a reçues sans pouvoir les servir : c'est sa demande non servie,
    # comme celle des biens (sans ce signal, un ménage sans logement était invisible).
    unhoused = sum(1 for tenant in tenants if tenant.rental_owner_id is None)
    if property_firms:
        base, extra = divmod(unhoused, len(property_firms))
        for index, company in enumerate(property_firms):
            company.unserved_rentals = base + (1 if index < extra else 0)

    revenue = 0.0
    landlord_by_id = {owner_id: owner for owner_id, _, owner in landlords}
    for tenant in tenants:
        owner_id = tenant.rental_owner_id
        if owner_id is None:
            continue
        payment = min(
            max(0.0, tenant.cash),
            monthly_rent,
            max(0.0, tenant.income_this_turn * rent_income_share),
        )
        owner = landlord_by_id[owner_id]
        transfer_cash(tenant, owner, payment)
        tenant.expenses_this_turn += payment
        tenant.cumulative_expenses += payment
        revenue += payment
        if isinstance(owner, Company):
            owner.revenue += payment
        else:
            owner.income_this_turn += payment
            owner.rent_income_this_turn += payment
            owner.lifetime_earnings += payment
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
        # l'entretien est une commande de services (corps de métier) et de matériaux
        total_paid += pay_business_suppliers(
            company, maintenance, {"services": 0.7, "materials": 0.3}, companies, resources
        )

    suppliers = household_suppliers(companies, ("services", "materials"))
    for person in people:
        homes = int(person.owns_home) + person.investment_homes
        maintenance = homes * maintenance_per_home
        total_paid += pay_suppliers_from_household(
            person, maintenance, {"services": 0.7, "materials": 0.3}, companies, resources, suppliers
        )

    return total_paid


def buy_homes(
    companies: list[Company],
    people: list[Person],
    unit_price: float = UNIT_PRICE,
    unit_cost: float = UNIT_COST,
    config: SimulationConfig | None = None,
    bank_reserves: float = 0.0,
    rng: random.Random | None = None,
    mortgage_rate: float | None = None,
    mortgage_transaction=None,
    credit_capacity=None,
    investment_yield: float | None = None,
    required_return: float = 0.0,
    household_offers: dict[str, int] | None = None,
) -> tuple[int, float]:
    """Achats de logements par les ménages (résidence principale, patrimoine).

    Achat patrimonial (A1) : avec ``investment_yield`` (rendement locatif net attendu d'un logement
    acheté au prix du marché, vacance observée comprise), un ménage n'investit que si ce rendement
    dépasse ``required_return`` (rendement de son épargne) majoré de la prime de risque du logement
    selon son goût du risque. ``household_offers`` (ménage -> logements mis en vente) : logements-
    patrimoine revendus par des ménages, après le parc des promoteurs, au même prix ; le vendeur
    encaisse le prix (échange d'actifs, pas un revenu)."""
    config = config or SimulationConfig(
        housing_price=unit_price,
        housing_build_cost=unit_cost,
    )
    sellers = [
        company for company in companies
        if company.active and company.sector == "real_estate"
    ]
    offers = dict(household_offers or {})
    if offers:
        sellers.extend(person for person in people if offers.get(person.id, 0) > 0)
    purchases = 0
    loans = 0.0
    # capacité de crédit du système, relue seulement après une vente : sans achat, rien ne
    # change les comptes des banques d'un vendeur ou d'un ménage au suivant (fonction pure)
    system_capacity = None
    for person in people:
        for seller in sellers:
            household_seller = isinstance(seller, Person)
            if household_seller:
                if seller is person or offers.get(seller.id, 0) <= 0:
                    continue
            elif seller.property_inventory <= 0:
                continue
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
                if credit_capacity is not None and system_capacity is None:
                    system_capacity = credit_capacity()
                loan = min(
                    max(0.0, system_capacity if credit_capacity is not None else bank_reserves - loans),
                    _mortgage_principal_for_payment(
                        payment_budget,
                        mortgage_rate if mortgage_rate is not None else config.mortgage_interest_rate,
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
                and (
                    investment_yield is None
                    or investment_yield > required_return
                    + config.housing_risk_premium * (1.5 - person.risk_tolerance)
                )
                and (rng is None or rng.random() < config.home_investment_probability)
            )
            if not primary_purchase and not investment_purchase:
                continue
            transaction = (mortgage_transaction(person, seller, loan)
                           if mortgage_transaction is not None and loan > 0.0 else nullcontext())
            with transaction:
                down_payment = unit_price - loan
                person.cash -= down_payment
                person.cumulative_expenses += unit_price
                # l'achat d'un logement est un échange d'actifs, pas une dépense courante
                seller.cash += unit_price
                if household_seller:
                    seller.investment_homes -= 1
                    offers[seller.id] -= 1
                else:
                    seller.revenue += unit_price
                    seller.expenses += unit_cost
                    seller.property_cost_sold_this_turn += unit_cost
                    seller.property_inventory -= 1
                    seller.homes_sold += 1
                if primary_purchase:
                    person.owns_home = True
                    person.rental_owner_id = None
                    person.mortgage_debt = loan
                    person.mortgage_rate = (
                        mortgage_rate if mortgage_rate is not None else config.mortgage_interest_rate
                    )
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
            system_capacity = None  # la vente a pu modifier les comptes des banques
    return purchases, loans


def service_mortgages(
    people: list[Person],
    report: dict[str, float] | None = None,
) -> float:
    received = 0.0
    interest_total = 0.0
    for person in people:
        if person.mortgage_debt <= 0.0:
            continue
        interest_due = person.mortgage_debt * person.mortgage_rate
        scheduled_payment = min(
            person.mortgage_payment,
            person.mortgage_debt + interest_due,
        )
        payment = min(max(0.0, person.cash), scheduled_payment)
        person.mortgage_arrears = (
            person.mortgage_arrears + 1 if payment < scheduled_payment * 0.999 else 0
        )
        person.cash -= payment
        person.expenses_this_turn += payment
        person.cumulative_expenses += payment
        interest_total += min(payment, interest_due)
        person.mortgage_debt = max(
            0.0,
            person.mortgage_debt + interest_due - payment,
        )
        person.mortgage_months_remaining = max(
            0, person.mortgage_months_remaining - 1
        )
        received += payment
        if person.mortgage_debt == 0.0:
            person.mortgage_debt = 0.0
            person.mortgage_arrears = 0
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
    if report is not None:
        report["interest"] = interest_total
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
