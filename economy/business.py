from __future__ import annotations

import random

from economy.energy import (
    infrastructure_unit_gain,
    infrastructure_unit_price,
    pay_infrastructure,
)
from economy.finance import borrowing_share, extend_credit
from economy.intersector import pay_business_suppliers
from economy.models import Company, Person, Resources, SimulationConfig, productivity_multiplier
from economy.treasury import working_capital


def output_per_worker(company: Company, config: SimulationConfig) -> float:
    """Production mensuelle d'un salarié de l'entreprise (alimentation, biens), hors compétences."""
    base = config.food_productivity if company.sector == "food" else config.goods_productivity
    return max(0.1, base * company.productivity_trait * productivity_multiplier(company, config))


def elect_executives(
    companies: list[Company],
    people: list[Person],
    config: SimulationConfig,
    rng: random.Random,
    no_dilution: frozenset = frozenset(),
) -> None:
    """Direction des entreprises. ``no_dilution`` (nouvel État) : entreprises sous contrôle public,
    dont le dirigeant n'est pas rémunéré en actions nouvelles (l'État refuse la dilution)."""
    people_by_id = {person.id: person for person in people}
    for company in companies:
        if not company.active:
            continue
        current = people_by_id.get(company.executive_id or "")
        current_is_employee = current is not None and current.employer_id == company.id
        # CEO indéterminé : ne jamais forcer un remplacement si le CEO actuel est bon
        if current_is_employee and config.ceo_term_indeterminate:
            company.ceo_tenure += 1
            current.leadership_experience += 1
            company.ceo_experience += 1
            # rémunération en actions pour les bons CEO
            if (
                company.profit > 0
                and company.cash > company.target_employees * company.wage_offer * 1.0
                and company.id not in no_dilution
            ):
                equity_bonus = company.profit * config.ceo_equity_bonus_rate
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


def adjust_profit_sharing_wages(
    companies: list[Company],
    people: list[Person],
    wage_reference: float,
    bonus_rate: float,
    loss_adjustment_strength: float,
    wage_vacancy_speed: float,
    capital_costs: dict[str, float],
    config: SimulationConfig,
    long_term_unemployment: float = 0.0,
) -> None:
    """Offre salariale de chaque entreprise, fixée d'après ses propres signaux, puis primes.

    Pas de base sectorielle ni de bornes : l'entreprise relève son offre de
    ``wage_vacancy_speed`` × part de postes vacants par rapport à son besoin réel
    (``staffing_need``), la baisse du même coefficient × part de sureffectif, et la baisse si
    elle perd de l'argent sans manquer de personnel (0,25 × ``loss_adjustment_strength`` ×
    marge négative).

    Modération salariale (novembre 2026, idée 1) :
    - une entreprise dont la recette lissée ne couvre pas sa masse salariale
      (``payroll_coverage_target``, la même règle qui la fait licencier dans
      ``labor.allocate_and_pay_labor``) ne compte pas comme vacants les postes qu'elle vient de
      supprimer faute de recettes ; elle baisse son offre de ``wage_concession_speed`` × écart
      relatif de couverture (concession plutôt que licenciements seuls) ;
    - une entreprise sans poste vacant baisse son offre de ``wage_slack_speed`` ×
      ``long_term_unemployment`` (part des ménages sans emploi depuis au moins
      ``long_term_unemployment_months`` mois, statistique publique du mois) : candidats
      abondants, elle n'a pas besoin de payer autant pour garder ou trouver ses salariés. Nul en
      régime normal (chômage frictionnel court).
    Le salaire versé reste au moins le salaire minimum (``labor._pay``).

    Une hausse ne dépasse jamais la capacité de payer : recette attendue
    (pour l'alimentation et les biens, seulement ce que l'effectif visé et la capacité installée
    produisent, R6) moins ses autres charges et la rémunération de ses fonds propres
    (``capital_costs``), ni ce que la trésorerie paie. Sans salarié, l'offre n'est jamais sous
    le salaire que paient les employeurs du secteur (``going_wages``). Les primes de partage
    des profits sont versées à part. Aucune indexation sur l'IPC.
    """
    people_by_id = {person.id: person for person in people}
    going = going_wages(companies, wage_reference)
    for company in companies:
        if not company.active:
            continue
        profitability = max(-0.4, min(0.30, company.profit / max(company.revenue, 1.0)))
        # signaux propres : postes vacants ou en trop face au besoin réel (le
        # nombre visé ce mois est borné par les embauches possibles), pertes
        need = max(1, company.staffing_need, company.target_employees)
        staff = len(company.employees)
        vacant = max(0, need - staff)
        # couverture de la masse salariale par la recette lissée (règle de licenciement)
        shortfall = 0.0
        if staff > 0 and company.wage_bill > 0.0 and config.payroll_coverage_target > 0.0:
            coverage = (company.revenue_ema or company.previous_revenue) / company.wage_bill
            shortfall = max(0.0, config.payroll_coverage_target - coverage) / config.payroll_coverage_target
        if shortfall > 0.0 and config.wage_concession_speed > 0.0:
            # postes supprimés faute de recettes : pas une vacance à pourvoir
            vacant = 0
        change = wage_vacancy_speed * (vacant - max(0, staff - need)) / need
        change -= config.wage_concession_speed * shortfall
        if vacant == 0 and staff > 0:
            change -= config.wage_slack_speed * max(0.0, long_term_unemployment)
        # des pertes font baisser l'offre seulement si l'entreprise ne manque pas de
        # bras : sinon elle ne pourrait plus produire (mesuré : sans cette condition,
        # l'alimentation en pertes au sortir de la chauffe ne recrutait plus)
        if profitability < 0.0 and staff >= need:
            change += 0.25 * loss_adjustment_strength * profitability
        offer = company.wage_offer * (1.0 + change)
        if change > 0.0:
            # capacité de payer : jamais au-delà de l'offre qui la laisserait à
            # l'équilibre avec l'effectif dont elle a besoin (chiffre d'affaires
            # lissé moins ses autres charges ; salaire payé / offre = postes et
            # métiers). Sans cette limite, la demande de travail ne dépend pas du
            # salaire et toutes les entreprises surenchérissent sans fin.
            pay_ratio = (
                company.wage_bill / (company.wage_offer * staff)
                if staff > 0 and company.wage_offer > 0.0 and company.wage_bill > 0.0
                else 1.0
            )
            # autres charges, plus la rémunération de ses fonds propres au coût du
            # capital (``capital_costs``) : sans elle, toutes surenchérissaient jusqu'à
            # un profit nul (mesuré : plus d'investissement, de recherche ni d'entrées)
            other_costs = max(0.0, company.expenses - company.wage_bill) + (
                capital_costs.get(company.id, 0.0)
            )
            # recette attendue : son chiffre d'affaires, ou la demande qui lui est
            # adressée au prix qu'elle affiche (sans salariés, elle ne vend rien
            # mais voit la demande qu'elle ne sert pas)
            expected_revenue = company.revenue_ema
            if company.sector in ("food", "goods"):
                # seulement ce que l'effectif visé et la capacité installée produisent
                # (sinon un salarié unique « rapportait » toute la pénurie du secteur)
                demand = min(company.demand_ema, need * output_per_worker(company, config)
                             + company.production_capacity)
                expected_revenue = max(expected_revenue, demand * company.price)
            break_even = max(0.0, expected_revenue - other_costs) / (need * pay_ratio)
            offer = min(offer, max(company.wage_offer, break_even))
            if staff > 0:
                # pas de hausse que la trésorerie ne paie pas (réserve d'embauche de
                # ``labor.allocate_and_pay_labor``), sans baisser les salaires en cours
                payable = (company.cash - wage_reference * 0.25) / need
                offer = min(offer, max(company.wage_offer, payable))
            else:
                # aucun contrat : le devis d'embauche respecte la condition de
                # réembauche (trésorerie ≥ 2 × offre)
                offer = min(offer, company.cash / 2.0)
        company.wage_offer = max(1.0, offer)
        if not company.employees:
            # sans salarié : jamais moins que le salaire que paient les employeurs du secteur
            # (personne ne viendrait) ; l'embauche reste bornée par ce que la trésorerie paie
            company.wage_offer = max(company.wage_offer, going[company.sector])
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


def going_wages(companies: list[Company], wage_reference: float) -> dict[str, float]:
    """Salaire pratiqué dans chaque secteur : offres des entreprises qui emploient, pondérées
    par leurs effectifs ; sans employeur, la référence salariale observée de l'économie (en
    mode « firm_signals », moyenne des offres des employeurs ; aucun facteur sectoriel fixé)."""
    paid: dict[str, float] = {}
    staff: dict[str, int] = {}
    for company in companies:
        if company.active and company.employees:
            paid[company.sector] = paid.get(company.sector, 0.0) + company.wage_offer * len(company.employees)
            staff[company.sector] = staff.get(company.sector, 0) + len(company.employees)
    sectors = {company.sector for company in companies}
    return {
        sector: paid[sector] / staff[sector] if staff.get(sector) else wage_reference
        for sector in sectors
    }


def invest_in_innovation(
    companies: list[Company],
    rng: random.Random,
    resources: Resources | None = None,
    research_share: float = 0.12,
    outsourced_share: float = 0.0,
) -> float:
    """Les bénéfices conservés financent la recherche ; l'innovation améliore la productivité de façon aléatoire."""
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


def capacity_return(
    company: Company,
    unit_cost: float,
    config: SimulationConfig,
    expected_inflation: float,
    gain: float | None = None,
) -> float:
    """Rendement annuel nominal attendu d'une unité de capacité de production.

    Gain mensuel de l'unité (par défaut, travail économisé au salaire offert) rapporté
    au prix de l'unité, moins l'usure, plus l'inflation anticipée (les flux suivent les
    prix). ``gain`` : valeur mensuelle d'une tranche (mode « incremental_cashflow »).
    """
    if gain is None:
        gain = infrastructure_unit_gain("production", company, config)
    return 12.0 * (
        gain / max(unit_cost, 1.0)
        - config.productive_capacity_depreciation_rate
        + expected_inflation
    )


def expected_growth_for(
    company: Company, config: SimulationConfig, expected_inflation: float | None
) -> float | None:
    """Hausse anticipée qui entre dans les rendements de l'entreprise : la sienne
    (anticipations locales) ; aucune sans anticipation commune (``None``)."""
    if expected_inflation is not None:
        return company.expected_cost_growth
    return expected_inflation


def capacity_tranches(
    company: Company,
    config: SimulationConfig,
    resources: Resources,
) -> list[tuple[float, float]]:
    """Tranches d'un projet de capacité (mode « incremental_cashflow ») : (unités par mois,
    valeur mensuelle d'une unité), de la plus rentable à la moins rentable.

    Une unité de capacité produit une unité par mois sans salarié. Elle vaut :
    - une vente de plus, tant que la demande lissée de l'entreprise (``demand_ema``)
      dépasse sa production possible : son prix moins ses intrants par unité, au prorata
      de l'énergie livrée ;
    - un salaire évité, pour les unités que ses salariés produisent aujourd'hui : salaires
      payés / unités produites par le travail. Sans salarié, rien n'est économisé.
    Au-delà, une unité ne rapporte rien (ni débouché ni travail à remplacer).
    """
    fill = max(0.0, company.energy_fill)
    tranches = []
    extra_sales = max(0.0, company.demand_ema - company.potential_output)
    input_cost = company.unit_input_cost
    if input_cost <= 0.0:
        # jamais produit : coût des intrants au prix des fournisseurs
        base = config.food_input_cost if company.sector == "food" else config.goods_input_cost
        input_cost = base * resources.input_cost_index
    margin = (company.price - input_cost) * fill
    if extra_sales > 0.0 and margin > 0.0:
        tranches.append((extra_sales, margin))
    labor_units = (
        max(0.0, company.potential_output / fill - company.production_capacity)
        if fill > 0.0 else 0.0
    )
    if labor_units > 0.0 and company.wage_bill > 0.0:
        tranches.append((labor_units, company.wage_bill / labor_units))
    return sorted(tranches, key=lambda tranche: -tranche[1])


def incremental_capacity_project(
    company: Company,
    unit_cost: float,
    config: SimulationConfig,
    resources: Resources,
    policy_rate: float | None,
    expected_inflation: float | None,
) -> tuple[float, float]:
    """Unités de capacité à acheter et rendement annuel de la dernière tranche retenue.

    Une tranche est retenue tant que son rendement (``capacity_return`` avec sa valeur)
    dépasse le coût des fonds propres de l'entreprise (taux directeur + prime de risque).
    """
    rate = config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate)
    required = 12.0 * rate + config.equity_risk_premium
    expected = expected_growth_for(company, config, expected_inflation) or 0.0
    units = marginal = 0.0
    for tranche_units, value in capacity_tranches(company, config, resources):
        annual = capacity_return(company, unit_cost, config, expected, gain=value)
        if annual <= required:
            break
        units += tranche_units
        marginal = annual
    return units, marginal


def _incremental_plan(
    company: Company,
    config: SimulationConfig,
    resources: Resources,
    policy_rate: float | None,
    expected_inflation: float | None,
    credit_available: bool = True,
) -> tuple[float, float, float]:
    """(apport sur fonds propres, part empruntée, prix d'une unité) du projet de capacité
    en mode « incremental_cashflow » ; apport limité à la trésorerie au-delà du fonds de
    roulement (``treasury.working_capital``)."""
    unit_cost = infrastructure_unit_price("production", company, config, resources)
    units, marginal = incremental_capacity_project(
        company, unit_cost, config, resources, policy_rate, expected_inflation
    )
    if units <= 0.0:
        return 0.0, 0.0, unit_cost
    credit_share = 0.0
    if credit_available:
        credit_share = (
            config.credit_financed_share
            if expected_inflation is None
            else borrowing_share(marginal, policy_rate, config, company)
        )
    available = max(0.0, company.cash - working_capital(company, config))
    equity = min(units * unit_cost * (1.0 - credit_share), available)
    return (equity if equity >= unit_cost * 0.05 else 0.0), credit_share, unit_cost


def planned_capacity_investment(
    company: Company,
    unmet_demand: float,
    config: SimulationConfig,
    resources: Resources,
    policy_rate: float | None = None,
    expected_inflation: float | None = None,
) -> float:
    """Ce que ``invest_in_production_capacity`` dépensera sur fonds propres ce mois
    (mêmes conditions), ou 0 : la trésorerie le réserve avant tout autre emploi."""
    if not company.active or company.sector not in ("food", "goods"):
        return 0.0
    return _incremental_plan(company, config, resources, policy_rate, expected_inflation)[0]


def invest_in_production_capacity(
    companies: list[Company],
    unmet_demand: dict[str, float],
    config: SimulationConfig,
    resources: Resources,
    bank_reserves: float = 0.0,
    policy_rate: float | None = None,
    expected_inflation: float | None = None,
    credit_transaction=None,
    credit_capacity=None,
) -> float:
    """Réinvestit la trésorerie rentable dans une capacité de production durable.

    L'investissement est déclenché par une demande non satisfaite, ou par une
    pénurie de main-d'œuvre (postes ouverts non pourvus) : le capital se
    substitue alors au travail. Si des réserves bancaires sont disponibles,
    une part de la dépense est empruntée : avec ``expected_inflation`` (mensuelle),
    chaque entreprise la choisit en comparant le rendement attendu de la
    capacité au coût du crédit (``finance.borrowing_share``) ; sans,
    ``credit_financed_share``. La part financée sur fonds propres reste régie
    par les mêmes plafonds de profit et de trésorerie.
    """
    total_investment = 0.0
    borrowed = 0.0
    for sector in ("food", "goods"):
        producers = [
            company
            for company in companies
            if company.active and company.sector == sector
        ]
        if not producers:
            continue
        for company in producers:
            # projet évalué sur ses ventes supplémentaires et ses salaires réels (R1)
            investment, credit_share, unit_cost = _incremental_plan(
                company, config, resources, policy_rate, expected_inflation,
                credit_available=bank_reserves > borrowed,
            )
            if investment <= 0.0:
                continue
            if credit_share > 0.0:
                loan = extend_credit(
                    company,
                    investment * credit_share / (1.0 - credit_share),
                    bank_reserves - borrowed,
                    config,
                    policy_rate,
                    credit_transaction=credit_transaction,
                    credit_capacity=credit_capacity,
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
