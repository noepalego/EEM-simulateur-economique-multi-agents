from __future__ import annotations

from economy import diagnostics
from economy.payments import transfer_cash

import math
import random
from collections.abc import Mapping

from economy.trades import pick_hire
from economy.models import (
    Company,
    Person,
    Resources,
    SimulationConfig,
    productivity_multiplier,
)


def allocate_and_pay_labor(
    companies: list[Company],
    people: list[Person],
    starting_wage: float,
    config: SimulationConfig | None = None,
    rng: random.Random | None = None,
    trade_factors: Mapping[str, float] | None = None,
    public_staff: frozenset = frozenset(),
    unavailable: frozenset = frozenset(),
    public_stability: float = 0.0,
    full_pay_hiring: bool = False,
    restart_values: Mapping[str, float] | None = None,
) -> tuple[float, float]:
    """Affecte les travailleurs aux meilleures offres de salaire, dans la limite de la trésorerie des entreprises.

    Avec ``rng``, une fraction ``job_separation_rate`` des salariés en place
    quitte ou perd son poste chaque mois et doit chercher un mois (chômage
    frictionnel : appariement non instantané). Sans ``rng``, aucune rupture.

    Nouvel État (``state_model="institutions"``) : ``public_staff`` = personnes au service de
    l'État, qui le restent si aucune entreprise ne les embauche (sinon, comme dans l'ancien
    modèle, elles sont remises au chômage chaque mois) ; ``unavailable`` = personnes que le privé
    ne peut pas embaucher ce mois (membres du cabinet pendant leur mandat) ; ``public_stability``
    = valeur de la stabilité de l'emploi public : une offre privée doit la compenser en plus de la
    prime de mobilité pour attirer un agent public ; ``full_pay_hiring`` = les postes finançables se
    comptent à la rémunération complète attendue (salaire moyen payé le mois dernier, multiplicateurs
    de poste, de compétence et de métier compris, jamais sous le salaire minimum) et non à l'offre
    d'entrée. Vides, nulle et faux par défaut.

    ``restart_values`` (idée 2, novembre 2026) : valeur mensuelle attendue d'un poste pour chaque
    entreprise sans salarié (``EconomySimulation._restart_values``). Une entreprise sans salarié
    reprend si cette valeur couvre le coût d'un poste : jusqu'à ``max_monthly_hiring`` embauches,
    dans la limite de ce que sa trésorerie paie (règle commune des postes finançables) ; sinon elle
    n'embauche pas. Sans ce dictionnaire, ancienne règle : une seule embauche, et seulement avec
    une trésorerie d'au moins max(3 × salaire de départ, 2 × offre).
    """
    stay_factor = 1.0 + public_stability
    position_costs: dict[str, float] = {}

    def position_cost(company: Company) -> float:
        """Coût d'un poste pour compter les embauches finançables."""
        return position_costs[company.id] if full_pay_hiring else max(company.wage_offer, 1.0)

    config = config or SimulationConfig(starting_wage=starting_wage)
    previous_employers = {person.id: person.employer_id for person in people}
    searching: set[str] = set()
    for person in people:
        if person.job_search_months > 0:
            person.job_search_months -= 1
        elif (
            rng is not None
            and previous_employers[person.id] not in (None, "state")
            and rng.random() < config.job_separation_rate
        ):
            person.job_search_months = 1
            searching.add(person.id)
    companies_by_id = {company.id: company for company in companies}
    previous_employee_ids = {
        company.id: set(company.employees) - searching for company in companies
    }
    previous_counts = {
        company.id: len(previous_employee_ids[company.id]) for company in companies
    }
    desired_employees: dict[str, int] = {}
    for company in companies:
        if full_pay_hiring:
            staff = len(company.employees)
            average = company.wage_bill / staff if staff and company.wage_bill > 0.0 else company.wage_offer
            position_costs[company.id] = max(average, config.minimum_wage, 1.0)
        desired = company.target_employees
        if not desired:
            desired = {
                "technology": 3,
                "food": 10,
                "goods": 7,
            }.get(company.sector, 5)
        previous_employee_count = len(company.employees)
        if previous_employee_count > 0 and company.wage_bill > 0.0:
            # Le revenu est lissé sur plusieurs mois : un à-coup de ventes ne
            # déclenche ni embauches ni licenciements massifs.
            smoothed_revenue = company.revenue_ema or company.previous_revenue
            payroll_coverage = smoothed_revenue / company.wage_bill
            if payroll_coverage < config.payroll_coverage_target:
                target_drop = int(previous_employee_count * max(0.0, payroll_coverage))
                if desired > max(previous_employee_count - config.max_monthly_layoffs, target_drop):
                    diagnostics.refusal("embauche", "couverture_masse_salariale",
                                        {"entreprise": company.id, "voulu": desired,
                                         "salaries": previous_employee_count,
                                         "couverture": payroll_coverage})
                desired = min(
                    desired,
                    max(
                        previous_employee_count - config.max_monthly_layoffs,
                        target_drop,
                    ),
                )
            elif company.previous_profit < 0.0:
                if desired > previous_employee_count:
                    diagnostics.refusal("embauche", "pertes_du_mois",
                                        {"entreprise": company.id, "voulu": desired,
                                         "salaries": previous_employee_count})
                desired = min(desired, previous_employee_count)
            else:
                diagnostics.bite("plafond_embauche_affectation",
                                 desired > previous_employee_count + config.max_monthly_hiring)
                desired = min(
                    desired, previous_employee_count + config.max_monthly_hiring
                )
        elif previous_employee_count == 0 and company.age_months > 1 and restart_values is not None:
            # reprise : un poste vaut-il ce qu'il coûte ? (valeur attendue contre coût du poste)
            worth = restart_values.get(company.id, 0.0) >= position_cost(company)
            if company.active and desired > 0 and not worth:
                diagnostics.refusal("embauche", "reprise_sans_perspective",
                                    {"entreprise": company.id,
                                     "valeur_poste": restart_values.get(company.id, 0.0),
                                     "cout_poste": position_cost(company)})
            desired = min(desired, config.max_monthly_hiring) if worth else 0
        elif previous_employee_count == 0 and company.age_months > 1:
            restart_reserve = max(starting_wage * 3.0, company.wage_offer * 2.0)
            if company.active and desired > 0 and company.cash < restart_reserve:
                diagnostics.refusal("embauche", "reserve_de_reprise",
                                    {"entreprise": company.id, "tresorerie": company.cash,
                                     "reserve": restart_reserve})
            desired = min(desired, 1) if company.cash >= restart_reserve else 0
        desired_employees[company.id] = desired
        company.employees.clear()
        company.wage_bill = 0.0
        if not company.active:
            continue
    for person in people:
        person.income_this_turn = 0.0
        person.dividend_income_this_turn = 0.0
        person.expenses_this_turn = 0.0

    job_offers: list[tuple[float, str, Company]] = []
    for company in companies:
        if not company.active:
            continue
        desired = desired_employees[company.id]
        affordable = int(max(0.0, company.cash - starting_wage * 0.25) // position_cost(company))
        if affordable < desired and desired > previous_counts[company.id]:
            diagnostics.refusal("embauche", "tresorerie",
                                {"entreprise": company.id, "voulu": desired,
                                 "finançable": affordable, "salaries": previous_counts[company.id]})
        vacant_positions = max(
            0, min(desired, affordable) - min(previous_counts[company.id], desired)
        )
        for _ in range(vacant_positions):
            job_offers.append((company.wage_offer, company.id, company))

    job_offers.sort(key=lambda offer: (-offer[0], offer[1]))
    ranked_people = sorted(
        people,
        key=lambda person: (-person.skill, person.id),
    )
    available_people = [
        person for person in ranked_people
        if person.id not in searching and person.id not in unavailable
    ]
    offers_by_employer: dict[str, list[float]] = {}
    for wage, company_id, _ in job_offers:
        offers_by_employer.setdefault(company_id, []).append(wage)

    eligible_incumbents: set[str] = set()
    people_by_id = {person.id: person for person in people}
    for company in companies:
        if not company.active:
            continue
        affordable_positions = min(
            desired_employees[company.id],
            int(
                max(0.0, company.cash - starting_wage * 0.25)
                // position_cost(company)
            ),
        )
        incumbents = sorted(
            (
                people_by_id[person_id]
                for person_id in previous_employee_ids[company.id]
                if person_id in people_by_id
            ),
            key=lambda person: (-person.skill, person.id),
        )
        eligible_incumbents.update(
            person.id for person in incumbents[:affordable_positions]
        )

    employed_ids: set[str] = set()
    # Mobilité : un poste vacant ailleurs n'attire qu'UN travailleur. Chaque
    # offre concurrente est consommée par le travailleur qui la saisit (les
    # plus qualifiés d'abord, comme dans l'embauche) ; les autres restent.
    unclaimed_offers: dict[str, list[float]] = {
        company_id: sorted(wages, reverse=True)
        for company_id, wages in offers_by_employer.items()
    }
    # Les offres sont déjà classées par salaire décroissant puis identifiant.
    # La décote de mobilité dépend du secteur : seule sa première offre encore
    # libre (hors employeur actuel) peut gagner. Garder le rang global pour les
    # égalités entre secteurs, exactement comme le parcours antérieur.
    offers_by_sector: dict[str, list[tuple[int, str, list[float]]]] = {}
    for rank, (company_id, wages) in enumerate(unclaimed_offers.items()):
        offers_by_sector.setdefault(companies_by_id[company_id].sector, []).append(
            (rank, company_id, wages)
        )
    claims: dict[str, list[Person]] = {}
    for person in ranked_people:
        employer_id = previous_employers[person.id]
        employer = companies_by_id.get(employer_id or "")
        if (
            employer is None
            or not employer.active
            or person.id not in eligible_incumbents
        ):
            continue
        reservation_wage = person.current_wage or employer.wage_offer
        threshold = reservation_wage * (1.0 + config.labor_mobility_premium)
        best_other: str | None = None
        best_wage = 0.0
        best_rank = len(unclaimed_offers)
        for other_sector, offers in offers_by_sector.items():
            for rank, other_id, wages in offers:
                if other_id != employer.id and wages:
                    break
            else:
                continue
            # Changer de domaine coûte une reconversion : l'offre d'un secteur
            # où le salarié n'a aucune compétence est décotée.
            effective = wages[0] * person.trade_wage_factor / (
                1.0
                if other_sector == employer.sector
                or skill_level(person, other_sector) >= config.transferable_skill_level
                else 1.0 + config.sector_switch_penalty
            )
            if effective > best_wage or (
                best_other is not None and effective == best_wage and rank < best_rank
            ):
                best_other, best_wage = other_id, effective
                best_rank = rank
        if best_other is not None and best_wage >= threshold:
            unclaimed_offers[best_other].pop(0)
            claims.setdefault(best_other, []).append(person)
            continue
        if len(employer.employees) >= min(
            desired_employees[employer.id],
            int(
                max(0.0, employer.cash - starting_wage * 0.25)
                // position_cost(employer)
            ),
        ):
            continue
        role = _employment_role(employer, person)
        offered = _pay(employer, person, role, config)
        wage = min(
            offered,
            max(starting_wage * 0.55, employer.cash),
        )
        person.job_trade = person.job_trade or person.trade
        transfer_cash(employer, person, wage)
        employer.wage_bill += wage
        employer.expenses += wage
        person.income_this_turn = wage
        person.lifetime_earnings += wage
        person.current_wage = wage
        person.employment_role = role
        person.employer_tenure += 1
        person.skill = min(2.0, person.skill + config.skill_learning_rate)
        person.career_months += 1
        person.skills[employer.sector] = min(
            5.0, skill_level(person, employer.sector) + config.skill_level_growth
        )
        employer.employees.append(person.id)
        employed_ids.add(person.id)

    for _, _, company in job_offers:
        if not company.active:
            continue
        affordable_positions = min(
            desired_employees[company.id],
            int(
                max(0.0, company.cash - starting_wage * 0.25)
                // position_cost(company)
            ),
        )
        if len(company.employees) >= affordable_positions:
            continue
        eligible_people = [
            person
            for person in available_people
            if person.id not in employed_ids
            and (
                previous_employers[person.id] is None
                or (
                    person.id not in eligible_incumbents
                    and previous_employers[person.id] != "state"  # emploi public stable
                )
                or company.wage_offer * person.trade_wage_factor
                >= (person.current_wage or starting_wage)
                * (1.0 + config.labor_mobility_premium)
                * (stay_factor if previous_employers[person.id] == "state" else 1.0)
            )
        ]
        claimant = None
        while claims.get(company.id):
            candidate = claims[company.id].pop(0)
            if candidate.id not in employed_ids:
                claimant = candidate
                break
        if claimant is None and not eligible_people:
            continue
        choice = pick_hire(
            company, claimant, eligible_people, people_by_id,
            desired_employees[company.id], config,
        )
        if choice is None:
            continue
        person, slot_trade = choice
        person.job_trade = slot_trade
        person.trade_wage_factor = (trade_factors or {}).get(
            slot_trade, config.trade_base_wage.get(slot_trade, 1.0)
        )
        role = _employment_role(company, person)
        wage = min(
            _pay(company, person, role, config),
            max(starting_wage * 0.55, company.cash),
        )
        transfer_cash(company, person, wage)
        company.wage_bill += wage
        company.expenses += wage
        company.employees.append(person.id)
        person.employer_id = company.id
        person.employer_tenure = (
            person.employer_tenure + 1
            if previous_employers[person.id] == company.id
            else 1
        )
        person.income_this_turn = wage
        person.lifetime_earnings += wage
        person.skill = min(2.0, person.skill + config.skill_learning_rate)
        person.career_months += 1
        person.skills[company.sector] = min(
            5.0, skill_level(person, company.sector) + config.skill_level_growth
        )
        person.current_wage = wage
        person.employment_role = role
        employed_ids.add(person.id)

    for person in people:
        if person.id not in employed_ids and person.id not in public_staff:
            person.employer_id = None
            person.employer_tenure = 0
            person.skill = max(0.5, person.skill - 0.0005)
            person.current_wage = 0.0
            person.employment_role = "Sans emploi"
            person.unemployment_months += 1
        else:
            person.unemployment_months = 0

    wages = sum(company.wage_bill for company in companies if company.active)
    hired = len(employed_ids)
    average_wage = wages / hired if hired else starting_wage
    return average_wage, (len(people) - hired) / len(people) if people else 0.0


RANK_NAMES = ("Production", "Confirmé", "Spécialiste", "Management", "Direction")


def skill_level(person: Person, sector: str) -> float:
    """Niveau de compétence (1 à 5) dans le domaine d'un secteur."""
    return person.skills.get(sector, 1.0)


def initial_skills(skill: float, rng: random.Random, sectors: tuple[str, ...]) -> dict[str, float]:
    """Domaine principal tiré au hasard ; niveau dérivé de la compétence générale."""
    level = 1.0 + 4.0 * max(0.0, min(1.0, (skill - 0.5) / 1.5))
    return {rng.choice(sectors): level}


def _employment_role(company: Company, person: Person) -> str:
    if person.id == company.executive_id:
        return "Direction"
    if person.rank >= 0:
        return RANK_NAMES[min(person.rank, 3)]
    # rang pas encore attribué (premier mois) : seuils sur la compétence générale
    if person.skill >= 1.35:
        return "Management"
    if person.skill >= 1.1:
        return "Spécialiste"
    return "Production"


def _role_wage_multiplier(role: str, config: SimulationConfig) -> float:
    return {
        "Direction": config.executive_wage_multiplier,
        "Management": config.manager_wage_multiplier,
        "Spécialiste": config.specialist_wage_multiplier,
        "Confirmé": config.confirmed_wage_multiplier,
    }.get(role, 1.0)


def _pay(company: Company, person: Person, role: str, config: SimulationConfig) -> float:
    """Salaire = offre (référence × secteur) × poste × compétence dans le secteur."""
    level = skill_level(person, company.sector)
    skill_factor = 1.0 + config.skill_wage_slope * (level - 2.0)
    multiplier = _role_wage_multiplier(role, config)
    if role == "Direction":
        # La prime de direction suit la taille : le dirigeant d'une entreprise
        # d'une ou deux personnes n'est pas payé comme celui d'un groupe.
        size = max(company.target_employees, len(company.employees) + 1)
        scale = min(1.0, size / max(1.0, config.executive_full_pay_headcount))
        multiplier = 1.0 + (multiplier - 1.0) * scale
    wage = company.wage_offer * multiplier * skill_factor * person.trade_wage_factor
    return max(wage, config.minimum_wage)


def assign_ranks(companies: list[Company], people: list[Person], config: SimulationConfig) -> None:
    """Promotions : pyramide de postes selon compétence, ancienneté et places libres."""
    by_id = {person.id: person for person in people}
    for company in companies:
        if not company.active or not company.employees:
            continue
        members = [by_id[pid] for pid in company.employees if pid in by_id]
        count = len(members)
        seats = {
            3: int(count * config.manager_share),
            2: int(count * config.senior_share),
            1: int(count * config.confirmed_share),
        }
        ranked = sorted(
            (p for p in members if p.id != company.executive_id),
            key=lambda p: (-(skill_level(p, company.sector) + p.career_months / 120.0), p.id),
        )
        for person in members:
            if person.id == company.executive_id:
                person.rank = 4
        for person in ranked:
            level = skill_level(person, company.sector)
            person.rank = 0
            for rank in (3, 2, 1):
                if (
                    seats[rank] > 0
                    and level >= config.rank_min_level[rank]
                    and person.career_months >= config.rank_min_career_months[rank]
                ):
                    person.rank = rank
                    seats[rank] -= 1
                    break


def redistribute_orphan_fund(people: list[Person], resources: Resources) -> float:
    """Reverse aux ménages les dépenses restées sans fournisseur actif.

    Si un secteur fournisseur n'a plus aucune entreprise active, le paiement
    atterrit dans ``resources.market_cash`` ; il est redistribué à parts égales
    (surplus d'exploitation versé aux ménages actionnaires) au lieu de dormir.
    En régime normal ce fonds reste proche de zéro : les services sont de
    vraies entreprises.
    """
    leftover = max(0.0, resources.market_cash)
    if leftover <= 1.0 or not people:
        return 0.0
    diagnostics.flow("fonds_orphelin_reverse", leftover)
    share = leftover / len(people)
    for person in people:
        person.cash += share
        person.income_this_turn += share
        person.dividend_income_this_turn += share
        person.lifetime_earnings += share
    resources.market_cash -= leftover
    return leftover


def effective_worker_count(
    company: Company,
    worker_skills: Mapping[str, float | tuple[float, float]],
) -> float:
    effective_workers = 0.0
    for employee_id in company.employees:
        worker_data = worker_skills.get(employee_id)
        if worker_data is None:
            continue
        if isinstance(worker_data, tuple):
            skill, technology_level = worker_data
        else:
            skill, technology_level = worker_data, 0.0
        effective_workers += max(0.25, min(2.0, skill)) * (
            1.0 + min(0.45, max(0.0, technology_level) * 0.15)
        )
    return effective_workers


NON_PRODUCTION_PAYROLL_FACTOR = {
    "technology": 1.0,
    "materials": 1.0,
    "real_estate": 0.5,
    "bank": 1.0,
}


def _labor_worth_factor(
    company: Company, config: SimulationConfig, per_worker: float | None
) -> float:
    """Part du besoin technique que le salaire laisse rentable (mode ``marginal_product``).

    Un salarié de plus vaut ce qu'il fait vendre, net des intrants : alimentation et
    biens, ``per_worker`` unités × (prix − intrants par unité) ; autres secteurs
    productifs, recette lissée moins leurs autres charges, rapportée à l'effectif.
    Il coûte le salaire moyen effectivement payé. Tant que la valeur couvre le coût,
    l'entreprise embauche tout son besoin ; en dessous, elle n'en embauche que le
    rapport valeur / coût élevé à ``labor_demand_elasticity``. Plus le salaire offert
    monte, moins elle demande de travail : sans cela le besoin ne dépend que de la
    demande en unités, et toutes les entreprises surenchérissent ensemble.
    """
    staff = len(company.employees)
    pay = company.wage_offer
    if staff > 0 and company.wage_bill > 0.0:
        pay = company.wage_bill / staff
    elif company.sector not in ("food", "goods"):
        pay = company.wage_offer * 1.35
    if per_worker is not None:
        value = per_worker * max(0.0, company.price - company.unit_input_cost)
    elif staff > 0:
        other_costs = max(0.0, company.expenses - company.wage_bill)
        value = max(0.0, company.revenue_ema - other_costs) / staff
    else:
        return 1.0
    if pay <= 0.0 or value >= pay:
        return 1.0
    return max(0.0, value / pay) ** config.labor_demand_elasticity


def update_workforce_targets(
    companies: list[Company],
    people: list[Person],
    config: SimulationConfig,
) -> None:
    """Rend ``target_employees`` réactif à la demande, à la capacité et au revenu.

    - Alimentation / biens : effectif nécessaire pour produire la demande lissée
      (ventes + demande non satisfaite) et reconstituer le stock cible, une fois
      déduite la capacité d'investissement déjà installée.
    - Autres secteurs : effectif finançable par le revenu lissé
      (``payroll_revenue_share`` du revenu).
    Le changement par mois est borné (+max_monthly_hiring / -max_monthly_layoffs).
    """
    people_by_id = {person.id: person for person in people}
    population_cap = max(1, int(len(people) * 0.9))
    # Besoin brut de matières (nourriture 1 u., biens 1,5 u.) : le recyclage
    # n'en couvre qu'une partie (taux de récupération, délai d'un mois). Les
    # extracteurs gardent une capacité de sécurité pour le solde, sinon un
    # creux de recyclage arrête la production et le recyclage suivant (spirale).
    raw_usage = sum(
        c.demand_ema * (1.0 if c.sector == "food" else 1.5)
        for c in companies
        if c.active and c.sector in ("food", "goods")
    )
    extractors = sum(1 for c in companies if c.active and c.sector == "materials")
    safety_workers = math.ceil(
        (1.0 - config.material_recovery_rate + config.raw_material_capacity_buffer)
        * raw_usage
        / max(0.1, config.raw_material_extraction_productivity)
        / max(1, extractors)
    )
    for company in companies:
        if not company.active:
            continue
        current = len(company.employees)
        if current == 0 and company.age_months <= 1:
            continue  # démarrage : effectif initial de workforce_targets
        if company.sector in ("food", "goods"):
            base = (
                config.food_productivity
                if company.sector == "food"
                else config.goods_productivity
            )
            skills = [
                max(0.25, min(2.0, people_by_id[pid].skill))
                * (1.0 + min(0.45, max(0.0, people_by_id[pid].technology_level) * 0.15))
                for pid in company.employees
                if pid in people_by_id
            ]
            average_skill = sum(skills) / len(skills) if skills else 1.0
            per_worker = max(
                0.1,
                base
                * company.productivity_trait
                * productivity_multiplier(company, config)
                * average_skill,
            )
            inventory = (
                company.inventory
                if company.sector == "food"
                else company.goods_inventory
            )
            restock = (
                config.target_inventory_months * company.demand_ema - inventory
            ) / 6.0
            needed_units = max(0.0, company.demand_ema + restock)
            needed = math.ceil(
                max(0.0, needed_units - company.production_capacity) / per_worker
            )
        elif company.sector == "materials":
            # capacité d'extraction ajustée à la demande lissée (+10 % de marge)
            # et du travail nécessaire pour honorer les commandes B2B (biens
            # intermédiaires, construction) financées par le revenu lissé
            order_workers = round(
                company.revenue_ema
                * config.sector_payroll_share.get("materials", config.payroll_revenue_share)
                / (max(company.wage_offer, 1.0) * 1.35)
            )
            needed = max(
                safety_workers,
                math.ceil(
                    company.demand_ema
                    * 1.10
                    / max(0.1, config.raw_material_extraction_productivity)
                ),
                order_workers,
            )
        elif company.sector == "technology":
            # capacité de production numérique ajustée à la demande lissée
            # (ventes + demande de boosts non servie), comme l'extraction
            per_worker = max(
                0.1,
                config.technology_productivity
                * company.productivity_trait
                * max(0.25, company.innovation),
            )
            needed = math.ceil(company.demand_ema * 1.15 / per_worker)
        elif company.sector == "energy" and company.energy_demand_forecast > 0.0:
            # R8 (prévision) : effectif pour produire la demande prévue, dans la
            # limite des centrales installées
            per_worker = config.energy_output_per_worker * max(company.productivity_trait, 0.1)
            workers = min(company.energy_demand_forecast, company.energy_capacity) / max(per_worker, 1e-9)
            needed = math.ceil(workers)
        else:
            share = config.sector_payroll_share.get(
                company.sector, config.payroll_revenue_share
            )
            # coût moyen d'un salarié : offre × pyramide des postes
            average_pay = max(company.wage_offer, 1.0) * 1.35
            needed = round(company.revenue_ema * share / average_pay)
        # un salarié de plus n'est embauché que s'il rapporte au moins son salaire ; les autres
        # secteurs dimensionnent déjà l'effectif sur recette / salaire moyen
        if company.sector in ("food", "goods", "materials", "technology"):
            worth = _labor_worth_factor(
                company, config,
                per_worker if company.sector in ("food", "goods") else None,
            )
            technical = needed
            needed = math.ceil(needed * worth)
            if needed < technical:
                diagnostics.refusal("besoin_de_personnel", "valeur_marginale_sous_salaire",
                                    {"entreprise": company.id, "besoin_technique": technical,
                                     "besoin_retenu": needed, "salaries": current})
        needed = max(1, min(needed, population_cap))
        company.staffing_need = needed
        hires, layoffs = monthly_staff_bounds(current, config)
        diagnostics.bite("plafond_embauche", needed > current + hires)
        diagnostics.bite("plafond_licenciement", needed < current - layoffs)
        company.target_employees = max(current - layoffs, min(needed, current + hires))
        company.target_employees = max(1, company.target_employees)


def monthly_staff_bounds(current: int, config: SimulationConfig) -> tuple[int, int]:
    """Embauches et licenciements mensuels maximaux d'une entreprise : ``max_monthly_hiring``
    et ``max_monthly_layoffs``, quelle que soit sa taille (``current`` salariés)."""
    return config.max_monthly_hiring, config.max_monthly_layoffs


def workforce_targets(config: SimulationConfig) -> dict[str, int]:
    # cibler 80% de taux d'emploi total des ménages (= emplois firms + services)
    population = config.population_size
    firm_target = int(round(population * config.target_employment_rate * 0.55))
    total_companies = (len({
        "food":1,"goods":1,"technology":1,"real_estate":1,"materials":1,"bank":1,"energy":1,"services":1
    }) * config.companies_per_sector)
    avg_per_company = max(1, firm_target // total_companies)
    baseline = {
        "food": avg_per_company + 3,
        "goods": avg_per_company + 2,
        "technology": avg_per_company,
        "real_estate": avg_per_company,
        "materials": avg_per_company,
        "bank": max(1, avg_per_company // 2),
        "energy": max(1, avg_per_company // 2),
        "services": avg_per_company,
    }
    base_jobs = sum(baseline.values()) * config.companies_per_sector
    target_jobs = round(config.population_size * config.target_employment_rate)
    additional_groups = max(
        0, target_jobs - base_jobs
    ) // config.companies_per_sector
    group_weights = {
        "food": 4,
        "goods": 3,
        "technology": 1,
        "real_estate": 1,
    }
    group_allocations = {sector: 0 for sector in group_weights}
    total_weight = sum(group_weights.values())
    for _ in range(additional_groups):
        sector = max(
            group_weights,
            key=lambda key: (
                additional_groups * group_weights[key] / total_weight
                - group_allocations[key],
                -list(group_weights).index(key),
            ),
        )
        group_allocations[sector] += 1
    targets: dict[str, int] = {}
    for sector, base_per_company in baseline.items():
        extra = group_allocations.get(sector, 0)
        targets[sector] = base_per_company + extra
    # Extraction : dimensionnée sur la demande initiale de matières premières
    # (budget en trésorerie + revenu d'un mois, nourriture 1 u., biens 1,5 u.)
    food_units = population * (
        config.food_cash_draw_rate * config.initial_person_cash
        + config.food_income_budget_share * config.starting_wage * 0.6
    ) / (config.starting_food_price * (1.0 + config.vat_rate))
    goods_units = population * (
        config.goods_cash_draw_rate * config.initial_person_cash
        + config.goods_income_budget_share * config.starting_wage * 0.6
    ) / (config.starting_goods_price * (1.0 + config.vat_rate))
    raw_units = food_units + 1.5 * goods_units
    needed_workers = math.ceil(
        1.1 * raw_units / max(0.1, config.raw_material_extraction_productivity)
    )
    targets["materials"] = max(
        targets["materials"],
        math.ceil(needed_workers / config.companies_per_sector),
    )
    return targets
