"""Fonction publique du nouvel État (``state_model="institutions"``) : services, rôles, grille,
recrutement concurrentiel, promotions et effets mesurables.

Cinq services ; chacun a un besoin mesuré, une capacité par agent, un effet borné et une
conséquence visible en sous-effectif (docs/ETAT.md) :

- Finances publiques : ménages suivis (impôt, allocation) ; en sous-effectif, une part des impôts
  est recouvrée plus tard et une part des allocations versée plus tard (arriérés) ;
- Emploi et formation : chômeurs accompagnés ; reconversions financées par l'État (places par
  agent), payées aux entreprises de services ;
- Équipement et infrastructures : travaux suivis (entretien puis investissement, en salaires de
  référence) ; au-delà, les crédits ne sont pas exécutés ; sans entretien, le capital public
  s'use plus vite ;
- Concurrence et participations : secteurs surveillés et entreprises publiques suivies ; la
  capacité d'instruction sert à la politique industrielle (livraison 6) ;
- Éducation (novembre 2026, idée 5) : la génération suivante de chaque ménage à scolariser ; la
  couverture lissée du service (``education_quality``) relève la compétence de départ des
  héritiers et des nouveaux ménages (``DemographyMixin._education_skill_bonus``). En sous-effectif,
  la génération suivante commence moins qualifiée, des années plus tard.

Chaque agent appartient à un seul service, a un métier de poste et un grade ; son salaire suit
la grille : point d'indice × coefficient de métier (``trade_base_wage``) × prime de métier ×
coefficient de grade × ancienneté, jamais sous le salaire minimum. L'effectif résulte du besoin,
de la couverture voulue par la doctrine et des recrutements réussis. Les agents restent en poste
d'un mois à l'autre (ancienneté et carrière avancent, compétence entretenue) ; un employeur privé
peut les attirer avec une offre supérieure d'au moins la prime de mobilité. L'État recrute des
chômeurs et des salariés du privé pour qui l'offre (plus la valeur de la stabilité) vaut mieux
que leur salaire ; ces derniers prennent leur poste le mois suivant (préavis).
"""

from __future__ import annotations

import math
import statistics

from economy.institutions import (
    GENERIC_PUBLIC_TRADE,
    SERVICE_LABELS,
    SERVICE_TRADES,
    SERVICES,
    PublicAgent,
    ServiceState,
)
from economy.intersector import pay_business_suppliers
from economy.payments import transfer_cash


def _apportion(total: int, shares: dict[str, float]) -> dict[str, int]:
    """Répartit ``total`` postes selon ``shares`` (plus forts restes, ordre stable)."""
    weight = sum(shares.values())
    exact = {key: total * share / weight for key, share in shares.items()}
    posts = {key: int(math.floor(value)) for key, value in exact.items()}
    rest = total - sum(posts.values())
    for key in sorted(exact, key=lambda k: (-(exact[k] - posts[k]), list(shares).index(k)))[:rest]:
        posts[key] += 1
    return posts


class StateServicesMixin:
    """Services, grille, recrutement et effets de la fonction publique (nouveau mode)."""

    # -- grille ------------------------------------------------------------------------
    def _grid_salary(self, trade: str, grade: int, months: int) -> float:
        """Salaire de la grille, jamais sous le salaire minimum en vigueur."""
        inst, live = self.institutions, self.live
        coefficient = live.public_grade_coefficients[max(1, min(3, grade)) - 1]
        steps = min(10, max(0, months) // 24)
        salary = (
            inst.point * live.trade_base_wage.get(trade, 1.0) * inst.premiums.get(trade, 1.0)
            * coefficient * (1.0 + live.public_seniority_step * steps)
        )
        return max(salary, live.minimum_wage)

    def _grid_top(self) -> float:
        """Haut de la grille : métier et prime les mieux payés, grade 3, ancienneté maximale."""
        inst, live = self.institutions, self.live
        if inst.point <= 0.0:
            return max(self.wage_reference * live.public_wage_factor, live.minimum_wage) * 1.6 * max(
                live.trade_base_wage.values(), default=1.0
            )
        trades = {trade for mix in SERVICE_TRADES.values() for trade in mix}
        return max(self._grid_salary(trade, 3, 240) for trade in trades)

    def _agent_salary(self, agent: PublicAgent) -> float:
        return self._grid_salary(agent.trade, agent.grade, self.month - agent.since)

    # -- besoins -----------------------------------------------------------------------
    def _service_needs(self) -> dict[str, float]:
        """Besoin du mois de chaque service, en unités du service."""
        inst, live = self.institutions, self.live
        households = len(self.people)
        recent = self.history[-3:]
        unemployed = (
            sum(r.unemployment for r in recent) / len(recent) * households if recent
            else sum(p.employer_id is None for p in self.people)
        )
        law = inst.law
        investment = law.lines.get("investissement", 0.0) / 12.0 if law else 0.0
        maintenance = self.public.public_capital * live.public_capital_maintenance_rate
        works = (investment + maintenance) / max(self.wage_reference, 1.0)
        sectors = len({c.sector for c in self.companies if c.active})
        holdings = sum(1 for q in self.public.account.shares.values() if q > 0.0)
        return {"finances": float(households), "emploi": unemployed, "equipement": works,
                "concurrence": float(sectors + holdings), "education": float(households)}

    def _service_keys(self) -> tuple[str, ...]:
        """Services en activité : tous, sauf l'Éducation quand ``pupils_per_agent`` ≤ 0 (service
        supprimé : neutralisation de l'idée 5)."""
        if self.live.pupils_per_agent > 0.0:
            return SERVICES
        return tuple(key for key in SERVICES if key != "education")

    def _service_capacity_per_agent(self, key: str) -> float:
        live = self.live
        return {
            "finances": live.finance_files_per_agent,
            "emploi": live.employment_cases_per_agent,
            "equipement": live.works_per_agent,
            "concurrence": live.markets_per_agent,
            "education": live.pupils_per_agent,
        }[key]

    def _service_coverage(self) -> float:
        """Couverture minimale des besoins des services (capacité / besoin)."""
        services = self.institutions.services
        if not services:
            return 0.0
        return min(service.coverage for service in services.values())

    def _public_trade_demand(self) -> dict[str, float]:
        """Postes ouverts par métier : comptés dans la demande du marché des métiers."""
        demand: dict[str, float] = {}
        for service in self.institutions.services.values():
            for trade, posts in service.posts.items():
                demand[trade] = demand.get(trade, 0.0) + posts
        return demand

    # -- phase 2 : avant le marché du travail ------------------------------------------------
    def _join_public_recruits(self) -> None:
        """Les salariés du privé recrutés le mois dernier quittent leur employeur et prennent leur
        poste (préavis d'un mois) ; une personne nommée au cabinet entre-temps renonce."""
        inst = self.institutions
        inst.trainings = 0
        if not inst.pending_recruits:
            return
        cabinet = {office.person_id for office in inst.offices.values()}
        people = {p.id: p for p in self.people}
        companies = {c.id: c for c in self.companies}
        for person_id, agent in list(inst.pending_recruits.items()):
            person = people.get(person_id)
            if person is None or person_id in cabinet or person_id in inst.agents:
                continue
            company = companies.get(person.employer_id or "")
            if company is not None and person.id in company.employees:
                company.employees.remove(person.id)
            agent.since = self.month
            self._enter_public_service(person, agent)
        inst.pending_recruits = {}

    def _enter_public_service(self, person, agent: PublicAgent) -> None:
        self.institutions.agents[person.id] = agent
        person.employer_id = "state"
        person.employment_role = agent.role()
        person.job_search_months = 0
        person.employer_tenure = 0
        person.job_trade = agent.trade

    def _training_sponsor(self, person, cost: float) -> bool:
        """Reconversion financée par le service de l'Emploi, dans la limite de ses places : le coût
        est payé aux entreprises de services sur le budget public (le chômeur ne paie rien)."""
        inst = self.institutions
        if inst.training_places <= 0 or cost <= 0.0:
            return False
        available = self._state_funds(cost)
        if available < cost:
            return False
        before = self.public.account.cash
        pay_business_suppliers(self.public.account, cost, {"services": 1.0}, self.companies, self.resources)
        self.public.account.expenses = 0.0
        paid = before - self.public.account.cash
        if paid <= 0.0:
            return False
        inst.book.add("formation", -paid)
        inst.training_places -= 1
        inst.trainings += 1
        return True

    def _public_labor_sets(self) -> tuple[frozenset, frozenset]:
        """(personnes gardées par l'État si le privé ne les embauche pas : agents et cabinet,
        personnes non embauchables par le privé : cabinet) pour ``allocate_and_pay_labor``."""
        inst = self.institutions
        cabinet = frozenset(o.person_id for o in inst.offices.values())
        return cabinet | frozenset(inst.agents), cabinet

    # -- phase 2 : recrutement, promotions, paie ---------------------------------------------
    def _staff_public_services(self, f) -> None:
        """Départs, paie du cabinet, postes ouverts, recrutement, promotions et paie des agents."""
        inst, live, public = self.institutions, self.live, self.public
        people = {p.id: p for p in self.people}
        services = {key: ServiceState(key) for key in self._service_keys()}
        # 1. départs vers le privé (embauchés par une entreprise au marché du travail)
        for person_id, agent in list(inst.agents.items()):
            person = people.get(person_id)
            if person is None or person.employer_id != "state":
                services[agent.service].departures += 1
                del inst.agents[person_id]
        self._pay_arrears(inst.arrears_wages, "salaires")
        inst.cabinet_pay, members = self._pay_cabinet()
        # 2. postes ouverts : besoin / capacité d'un agent × couverture voulue par la doctrine
        needs = self._service_needs()
        coverage_goal = inst.doctrine.coverage_goal
        for key, service in services.items():
            service.need = needs[key]
            service.agents_needed = needs[key] / max(self._service_capacity_per_agent(key), 1e-9)
            service.authorized = max(1, math.ceil(service.agents_needed * coverage_goal - 1e-9))
            service.posts = _apportion(service.authorized, SERVICE_TRADES[key])
        previous = inst.services
        changed = [
            f"{SERVICE_LABELS[k]} {previous[k].authorized}→{s.authorized}"
            for k, s in services.items() if k in previous and previous[k].authorized != s.authorized
        ]
        if changed and len(changed) >= 2:
            self._decide("services", "Postes ouverts dans les services",
                         f"besoins mesurés × couverture voulue {coverage_goal:.0%} : " + ", ".join(changed))
        if (self.month - inst.created) % 3 == 0 and self.month != inst.created:
            self._revise_premiums()
        # 3. licenciement exceptionnel : sureffectif de plus de 30 % sous un gouvernement libéral
        if inst.doctrine.budget_rule == "excedent":
            self._exceptional_layoffs(services, people)
        # 4. recrutement concurrentiel, puis promotions
        self._recruit(services, people)
        self._promote(services, people)
        # 5. paie des agents (grille), arriérés si le financement manque
        wages = 0.0
        for person_id, agent in inst.agents.items():
            person = people[person_id]
            salary = self._agent_salary(agent)
            available = self._state_funds(salary)
            paid = max(0.0, min(salary, available))
            transfer_cash(public, person, paid)
            if salary - paid > 1e-9:
                inst.arrears_wages[person_id] = inst.arrears_wages.get(person_id, 0.0) + salary - paid
            person.income_this_turn += paid
            person.lifetime_earnings += paid
            person.current_wage = salary
            person.employer_tenure += 1
            person.career_months += 1
            person.skill = min(2.0, person.skill + live.skill_learning_rate)
            person.employment_role = agent.role()
            service = services[agent.service]
            service.staff += 1
            service.wage_bill += paid
            wages += paid
        inst.book.add("salaires_agents", -wages)
        filled = [*inst.agents.values(), *inst.pending_recruits.values()]
        for key, service in services.items():
            capacity = service.staff * self._service_capacity_per_agent(key)
            service.coverage = min(2.0, capacity / service.need) if service.need > 0.0 else 2.0
            service.output = min(service.need, capacity)
            for trade, posts in service.posts.items():
                service.vacancies += max(0, posts - sum(1 for a in filled if a.service == key and a.trade == trade))
        inst.services = services
        # qualité de l'éducation reçue par la génération suivante (idée 5) : couverture du service,
        # plafonnée à 1, lissée sur la durée d'une scolarité
        education = services.get("education")
        if education is not None:
            weight = 1.0 / max(1.0, live.education_memory_months)
            self.education_quality += weight * (min(1.0, education.coverage) - self.education_quality)
        inst.training_places = int(services["emploi"].staff * live.training_places_per_agent)
        public.employees = len(inst.agents) + members
        public.wage_bill = wages + inst.cabinet_pay
        f.public_wages = public.wage_bill

    def _candidate_pools(self, people: dict) -> tuple[list, list]:
        """Chômeurs disponibles et salariés du privé, les plus compétents d'abord."""
        inst = self.institutions
        cabinet = {office.person_id for office in inst.offices.values()}
        unemployed, employed = [], []
        for person in self.people:
            if person.id in cabinet or person.id in inst.agents or person.retraining_months > 0:
                continue
            if person.employer_id is None and person.job_search_months <= 0:
                unemployed.append(person)
            elif person.employer_id not in (None, "state"):
                employed.append(person)
        key = lambda p: (-p.skill, p.id)  # noqa: E731
        return sorted(unemployed, key=key), sorted(employed, key=key)

    def _recruit(self, services: dict, people: dict) -> None:
        """Pourvoit les postes vacants au salaire de la grille (grade 1, sans ancienneté).

        Candidats : chômeurs et salariés du privé du métier du poste (poste généraliste : tout
        métier, agents d'abord), pour qui l'offre × (1 + ``public_stability_value``) vaut au moins
        leur salaire × (1 + ``labor_mobility_premium``). Le plus compétent l'emporte ; un salarié du
        privé prend son poste le mois suivant."""
        inst, live = self.institutions, self.live
        unemployed, employed = self._candidate_pools(people)
        taken: set[str] = set(inst.pending_recruits)
        vacant_trades: set[str] = set()
        for key in self._service_keys():
            service = services[key]
            for trade, posts in sorted(service.posts.items(), key=lambda kv: -live.trade_base_wage.get(kv[0], 1.0)):
                held = sum(1 for a in inst.agents.values() if a.service == key and a.trade == trade)
                held += sum(1 for a in inst.pending_recruits.values() if a.service == key and a.trade == trade)
                vacant = posts - held
                if vacant <= 0:
                    continue
                vacant_trades.add(trade)
                offer = self._grid_salary(trade, 1, 0) * (1.0 + live.public_stability_value)

                def eligible(person, from_private: bool) -> bool:
                    if person.id in taken:
                        return False
                    if trade != GENERIC_PUBLIC_TRADE and person.trade != trade:
                        return False
                    return not from_private or offer >= person.current_wage * (1.0 + live.labor_mobility_premium)

                pool = [(p, False) for p in unemployed if eligible(p, False)] + [
                    (p, True) for p in employed if eligible(p, True)
                ]
                if trade == GENERIC_PUBLIC_TRADE:
                    pool.sort(key=lambda item: (item[0].trade != GENERIC_PUBLIC_TRADE, -item[0].skill, item[0].id))
                else:
                    pool.sort(key=lambda item: (-item[0].skill, item[0].id))
                service.applications += len(pool)
                inst.applications_quarter[trade] = inst.applications_quarter.get(trade, 0) + len(pool)
                inst.vacancies_quarter[trade] = inst.vacancies_quarter.get(trade, 0) + vacant
                for person, from_private in pool[:vacant]:
                    taken.add(person.id)
                    agent = PublicAgent(person.id, key, trade, 1, self.month)
                    if from_private:
                        inst.pending_recruits[person.id] = agent
                    else:
                        self._enter_public_service(person, agent)
                    service.hires += 1
        # mois consécutifs avec des postes vacants avant recrutement, par métier (primes de métier) :
        # un poste qui se vide chaque mois (départs vers le privé) compte comme vacant
        for trade in {trade for service in services.values() for trade in service.posts}:
            inst.vacancy_months[trade] = inst.vacancy_months.get(trade, 0) + 1 if trade in vacant_trades else 0

    def _promote(self, services: dict, people: dict) -> None:
        """Promotions par grade selon compétence et ancienneté, dans la limite des postes de grades
        2 et 3 du service (logique de ``assign_ranks`` ; un grade acquis n'est pas retiré)."""
        inst, live = self.institutions, self.live
        share2, share3 = live.public_grade_shares
        tenure2, tenure3 = live.public_grade_tenure
        for key in self._service_keys():
            members = [a for a in inst.agents.values() if a.service == key]
            staff = len(members)
            for grade, share, tenure in ((3, share3, tenure3), (2, share2, tenure2)):
                seats = int(staff * share) - sum(1 for a in members if a.grade >= grade)
                if seats <= 0:
                    continue
                ranked = sorted(
                    (a for a in members if a.grade == grade - 1 and self.month - a.since >= tenure),
                    key=lambda a: (-(people[a.person_id].skill + (self.month - a.since) / 120.0), a.person_id),
                )
                for agent in ranked[:seats]:
                    agent.grade = grade

    def _exceptional_layoffs(self, services: dict, people: dict) -> None:
        """Licenciement exceptionnel et consigné : au-delà de 130 % des postes ouverts d'un service,
        les derniers entrés partent (le non-remplacement des départs fait le reste)."""
        inst = self.institutions
        for key in self._service_keys():
            members = sorted((a for a in inst.agents.values() if a.service == key),
                             key=lambda a: (-a.since, a.person_id))
            excess = len(members) - math.floor(1.3 * services[key].authorized)
            if excess <= 0:
                continue
            for agent in members[:excess]:
                del inst.agents[agent.person_id]
                self._release(people[agent.person_id])
                services[key].layoffs += 1
            self._decide("services", f"Licenciement exceptionnel : {SERVICE_LABELS[key]}",
                         f"sureffectif au-delà de 130 % des {services[key].authorized} postes ouverts",
                         amount=float(excess))

    # -- décisions sur la grille ---------------------------------------------------------------
    def _revise_premiums(self) -> None:
        """Primes de métier (trimestrielles) : des postes vacants trois mois de suite relèvent la
        prime de ``public_premium_step`` si la masse salariale reste dans la loi ; des candidatures
        nombreuses (au moins deux par poste vacant) la gèlent. Comparabilité : la prime ne monte que
        si le salaire d'entrée reste sous le salaire médian du métier dans le privé, prime de
        mobilité comprise (poste généraliste : médian de tout le privé) ; au-delà, ou sans salarié du
        métier dans le privé, la vacance ne tient pas au salaire (sans cette règle, une prime montait
        indéfiniment : × 13,5 en 800 mois)."""
        inst, live = self.institutions, self.live
        law = inst.law
        within_budget = law is None or 12.0 * self.public.wage_bill <= 1.05 * law.lines.get("personnel", 0.0)
        arbitrated = not within_budget and self._coverage_objective_missed()
        if arbitrated:
            # le ministre manque son objectif de couverture : la présidence accorde le dépassement
            within_budget = True
        private: dict[str, list[float]] = {}
        for person in self.people:
            if person.employer_id not in (None, "state") and person.current_wage > 0.0:
                private.setdefault(person.trade, []).append(person.current_wage)
        everyone = [wage for wages in private.values() for wage in wages]
        raised = []
        for trade, months in sorted(inst.vacancy_months.items()):
            applications = inst.applications_quarter.get(trade, 0)
            vacancies = inst.vacancies_quarter.get(trade, 0)
            if months < 3 or applications >= 2 * vacancies or not within_budget:
                continue
            wages = everyone if trade == GENERIC_PUBLIC_TRADE else private.get(trade)
            if not wages or self._grid_salary(trade, 1, 0) >= statistics.median(wages) * (1.0 + live.labor_mobility_premium):
                continue
            inst.premiums[trade] = inst.premiums.get(trade, 1.0) * (1.0 + live.public_premium_step)
            raised.append(f"{trade} × {inst.premiums[trade]:.2f}")
        if raised:
            self._decide("services", "Primes de métier relevées",
                         "postes vacants depuis au moins trois mois, peu de candidatures : " + ", ".join(raised),
                         financing=("dépassement de la masse salariale accordé par la présidence (objectif de "
                                    "couverture manqué)" if arbitrated else "masse salariale de la loi de finances"))
        inst.applications_quarter = {}
        inst.vacancies_quarter = {}

    def _coverage_objective_missed(self) -> bool:
        """Vrai si le ministre des Services publics vise une couverture qu'il n'atteint pas."""
        office = self.institutions.offices.get("services")
        objective = office.objective if office else None
        return (objective is not None and objective.indicator == "couverture_services"
                and not objective.met(objective.value))

    def _wage_growth(self) -> float:
        """Évolution des salaires observée sur un an : salaire médian du privé (moyenne des douze
        derniers mois sur les douze précédents), à défaut salaire de référence sur douze mois."""
        records = self.institutions.history
        medians = [r.median_wage for r in records[-24:]]
        if len(medians) == 24 and all(m > 0.0 for m in medians):
            return sum(medians[12:]) / sum(medians[:12]) - 1.0
        return self.history[-1].wage_reference / max(self.history[-13].wage_reference, 1e-9) - 1.0

    def _revise_index_point(self) -> None:
        """Point d'indice (annuel), décision du ministre des Services publics : indexation sur
        l'évolution des salaires observée selon la doctrine (gel possible), rattrapage du salaire
        médian privé pour un gouvernement social, ou à la demande d'un ministre qui manque son
        objectif de couverture (arbitrage de la présidence) ; les Finances réduisent de moitié
        l'indexation quand la loi doit combler un écart à son solde voulu."""
        inst = self.institutions
        if len(self.history) < 13:
            return
        growth = self._wage_growth()
        doctrine = inst.doctrine
        indexation = doctrine.point_indexation
        law = inst.law
        arbitration = ""
        # écart à combler : déficit prévu au-delà du solde voulu, hors trésorerie rendue dans l'année
        if (law is not None and law.planned_balance + law.excess_treasury < law.balance_goal
                and doctrine.budget_rule != "programme"):
            indexation *= 0.5
            arbitration = " ; arbitrage des Finances : indexation réduite de moitié"
        change = indexation * max(0.0, growth)
        catch_up = 0.0
        requested = not doctrine.point_catch_up and doctrine.budget_rule != "excedent" and self._coverage_objective_missed()
        if doctrine.point_catch_up or requested:
            public = sorted(p.current_wage for p in self.people if p.id in inst.agents and p.current_wage > 0.0)
            private = self._median_private_wage()
            if public and private > 0.0:
                gap = private / public[len(public) // 2] - 1.0
                catch_up = max(0.0, min(0.02, 0.25 * gap))
                if requested and catch_up > 0.0:
                    arbitration += " ; rattrapage demandé (objectif de couverture manqué), accordé par la présidence"
        before = inst.point
        inst.point *= (1.0 + change) * (1.0 + catch_up)
        verb = "Point d'indice gelé" if inst.point == before else "Point d'indice revalorisé"
        self._decide(
            "services", verb,
            f"salaires observés sur douze mois {growth:+.1%}, indexation {indexation:.0%}"
            + (f", rattrapage {catch_up:+.1%}" if catch_up else "") + arbitration,
            amount=inst.point - before, financing="masse salariale de la loi de finances",
        )

    def _record_services(self, record) -> None:
        """Relevé mensuel de la fonction publique (historique propre au nouvel État)."""
        inst = self.institutions
        record.services = {
            key: {
                "need": service.need, "authorized": service.authorized, "staff": service.staff,
                "vacancies": service.vacancies, "applications": service.applications,
                "hires": service.hires, "departures": service.departures, "layoffs": service.layoffs,
                "wage_bill": service.wage_bill, "coverage": service.coverage, "output": service.output,
            }
            for key, service in inst.services.items()
        }
        record.point = inst.point
        record.premiums = dict(inst.premiums)
        record.tax_backlog = sum(inst.tax_backlog.values())
        record.trainings = inst.trainings
        record.median_wage = self._median_private_wage()

    # -- effets : Finances (recouvrement), Équipement (travaux) ------------------------------------
    def _finance_coverage(self) -> float:
        service = self.institutions.services.get("finances")
        return 1.0 if service is None else min(1.0, service.coverage)

    def _works_capacity(self) -> float:
        """Travaux que l'Équipement peut suivre ce mois (montant)."""
        service = self.institutions.services.get("equipement")
        staff = service.staff if service is not None else 0
        return staff * self.live.works_per_agent * self.wage_reference

    def _execute_public_works(self, f, investment_budget: float) -> None:
        """Entretien d'abord, puis investissement, dans la capacité de l'Équipement ; le capital
        public s'use plus vite sans entretien. Les travaux sont payés aux fournisseurs."""
        inst, live, public = self.institutions, self.live, self.public
        book = inst.book
        capacity = self._works_capacity()
        maintenance_need = public.public_capital * live.public_capital_maintenance_rate
        wanted = min(maintenance_need, capacity)
        budget = min(wanted, self._state_funds(wanted))
        book.note(book.unfunded, "entretien", wanted - budget)
        before = public.account.cash
        pay_business_suppliers(public.account, budget, live.public_investment_split, self.companies, self.resources)
        maintained = before - public.account.cash
        book.add("entretien", -maintained)
        room = max(0.0, capacity - maintained)
        executable = min(investment_budget, room)
        book.note(book.lapsed, "investissement", investment_budget - executable)  # faute de maîtrise d'ouvrage
        before = public.account.cash
        pay_business_suppliers(public.account, executable, live.public_investment_split, self.companies, self.resources)
        invested = before - public.account.cash
        public.account.expenses = 0.0
        book.add("investissement", -invested)
        book.note(book.lapsed, "investissement", executable - invested)
        coverage = maintained / maintenance_need if maintenance_need > 0.0 else 1.0
        rate = live.public_capital_depreciation_rate * (1.0 + live.unmaintained_depreciation * (1.0 - coverage))
        public.public_capital = public.public_capital * (1.0 - rate) + invested
        public.investment = invested
        # travaux publics du mois (investissement et entretien) : demande publique du PIB
        f.public_investment = invested + maintained
        if "equipement" in inst.services:
            inst.services["equipement"].output = (invested + maintained) / max(self.wage_reference, 1.0)
