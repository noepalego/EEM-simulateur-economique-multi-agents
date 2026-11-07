"""Gouvernement nommé du nouvel État (``state_model="institutions"``) : présidence, ministres,
objectifs, salaires politiques, journal des décisions.

Méthodes de ``EconomySimulation`` appelées seulement dans le nouveau mode :

- phase 1 (``_update_government``, après ``_update_housing_costs``) : ``_govern_institutions``
  crée l'État au premier usage, tient l'élection au calendrier habituel, suit les objectifs,
  remanie à mi-mandat, révise les salaires politiques une fois par an et réapplique à ``live``
  les décisions durables ;
- phase 2 (``_hire_public_workers``) : ``_staff_public_services`` paie le cabinet ;
- phase 8 (``_record_month``) : ``_record_institutions`` tient l'historique propre au mode.

Les membres du cabinet sont des ménages : ils quittent leur emploi à la nomination (un PDG est
remplacé par le mécanisme de son entreprise), sont payés par l'État, ne sont pas embauchables
par le privé pendant leur mandat et redeviennent candidats sur le marché du travail à la fin,
sauf reconduction. Le cabinet compte dans l'emploi public.
"""

from __future__ import annotations

import random
import statistics

from economy.institutions import (
    OFFICE_LABELS,
    OFFICE_ROLES,
    OFFICES,
    PORTFOLIOS,
    STATE_SEED_OFFSET,
    Decision,
    GovernmentTerm,
    Office,
    StateInstitutions,
    StateRecord,
    candidate_score,
    display_name,
    Voter,
    elect_orientation,
    individual_election,
    make_doctrine,
    make_objective,
    political_lean,
)
from economy.payments import transfer_cash
from economy.politics import Government


class StateGovernmentMixin:
    """Présidence, ministres, objectifs, salaires politiques et journal (nouveau mode)."""

    # -- création au premier usage -------------------------------------------------
    def _institutions(self) -> StateInstitutions:
        """État du nouveau mode, créé au premier appel (jamais dans les autres modes)."""
        inst = getattr(self, "institutions", None)
        if inst is not None:
            return inst
        inst = self.institutions = StateInstitutions(
            rng=random.Random(self.config.seed + STATE_SEED_OFFSET), created=self.month
        )
        # le gouvernement en place reçoit ses doctrines (écarts propres tirés une fois)
        current = self.government
        inst.doctrine = make_doctrine(current.orientation, inst.rng, self.live)
        self.government = Government(
            current.orientation, current.since_month, dict(inst.doctrine.positions), current.reelected
        )
        self.government_history[-1] = self.government
        # décisions durables de départ : impôts, allocation et programmes du programme en place
        inst.tax_factors = dict(inst.doctrine.tax_factors)
        inst.benefit_factor = inst.doctrine.benefit_factor
        inst.programs = {
            "transferts": inst.doctrine.transfer_share,
            "achats": inst.doctrine.purchases_share,
            "investissement": inst.doctrine.investment_share,
        }
        inst.executive_pay = self._executive_pay()
        # grille de la fonction publique : point d'indice de départ = ancien salaire public unique
        inst.point = self.wage_reference * self.live.public_wage_factor
        self._set_initial_salaries()
        self._open_term(reelected=False, motive="institution du nouvel État")
        self._institute_minimum_wage()
        return inst

    # -- phase 1 -------------------------------------------------------------------
    def _govern_institutions(self) -> None:
        live = self.live
        first = getattr(self, "institutions", None) is None
        inst = self._institutions()
        self._open_state_book()
        period = live.election_period_months
        election = not first and live.political_cycle_enabled and self.month > 1 and (self.month - 1) % period == 0
        if election:
            self._institutional_election()
        elif not first and live.cabinet_reshuffle and inst.terms:
            term = inst.terms[-1]
            if self.month - term.since == period // 2:
                self._reshuffle()
        # mode intervention (economy/interventions.py) : absent sans intervention, donc neutre
        operator = getattr(self, "interventions", None)
        if operator is not None:
            if election and operator.pending_orientation is None:
                # (gouvernement imposé ce mois : il remplace l'élu, les leviers forcés après la
                # demande valent pour son mandat)
                operator.end_mandate(self.month, "élection")
            if operator.pending_orientation is not None and not first:
                self._impose_government(operator)
        self._track_objectives()
        if not first and (self.month - inst.created) % 12 == 0:
            # date fixe annuelle : salaire minimum, point d'indice, salaires politiques
            self._revise_minimum_wage()
            self._revise_index_point()
            self._revise_political_salaries()
        self._apply_institutional_policy()

    def _apply_institutional_policy(self) -> None:
        """Décisions durables réappliquées à ``live`` chaque mois (``live`` est recréé chaque mois
        à partir de ``config``) : impôts et allocation votés par les lois de finances, salaire
        minimum légal (``state_labor.py`` ; l'offre salariale des entreprises n'est pas relevée),
        encadrement des prix de la doctrine. L'emploi public ne suit plus une part de la population
        (``state_services.py``) ; les participations relèvent de la politique industrielle
        (``state_industry.py``), plus du levier historique (``state_equity_position`` reste nul)."""
        live, government = self.live, self.government
        strength = max(0.0, live.policy_strength)
        self._apply_budget_decisions()
        live.minimum_wage = self.institutions.minimum_wage
        live.price_control_level = min(1.0, strength * max(0.0, government.position("price_controls")))
        operator = getattr(self, "interventions", None)
        if operator is not None:
            # leviers forcés par l'opérateur : remplacent les valeurs votées jusqu'à l'élection
            operator.apply_levers(live)

    def _impose_government(self, operator) -> None:
        """Gouvernement imposé par l'opérateur (mode intervention), formé comme après une
        alternance : bilan du mandat interrompu, doctrine de l'orientation choisie, nouveau
        cabinet ; la loi de finances rectificative suit en phase 4. Les leviers forcés avant la
        demande ont été rendus à la demande (``request_orientation``) ; ceux forcés depuis
        s'appliquent pendant le mandat imposé.

        Les tirages (écarts de doctrine, proximité des nouveaux ménages nommables) viennent d'un
        générateur dédié, substitué le temps de la formation : ``inst.rng`` n'avance pas."""
        from economy.interventions import doctrine_random

        inst = self.institutions
        orientation = operator.pending_orientation
        operator.pending_orientation = None
        engine_rng, inst.rng = inst.rng, doctrine_random(self)
        try:
            self._close_term()
            inst.doctrine = make_doctrine(orientation, inst.rng, self.live)
            self.government = Government(orientation, self.month, dict(inst.doctrine.positions), False)
            self.government_history.append(self.government)
            self._open_term(False, "gouvernement imposé par l'opérateur jusqu'à la prochaine élection")
        finally:
            inst.rng = engine_rng
        operator.forced_months.append(self.month)
        operator.log(self.month, "Gouvernement imposé", "État", None, orientation,
                     "jusqu'à la prochaine élection")

    # -- élection et mandats ---------------------------------------------------------
    def _institutional_election(self) -> None:
        live, inst = self.live, self.institutions
        recent = self.history[-12:]
        unemployment_gap = (
            sum(r.unemployment for r in recent) / len(recent) - live.natural_unemployment_rate
            if recent else 0.0
        )
        record = self._objectives_met_share()
        if live.individual_voting and live.economic_voting and self.people:
            incumbent = self.government.orientation
            orientation, reelected, share, challenger = individual_election(
                self.politics_random, incumbent, self._voters(), live, record,
            )
            inst.elections.append({
                "mois": self.month, "sortant": incumbent, "opposition": challenger,
                "part_sortant": share, "elu": orientation, "reelu": reelected,
            })
            self._decide(
                "presidence", "Élection : résultat du vote des ménages",
                f"{share:.1%} des voix pour le sortant (orientation {incumbent:.0f}) contre "
                f"l'opposition (programme {challenger:.0f}) ; "
                + ("sortant réélu" if reelected else "alternance"),
                amount=share,
            )
        else:
            orientation, reelected = elect_orientation(
                self.politics_random,
                self.government.orientation,
                live,
                unemployment_gap,
                -self._annual_inflation_gap(),
                record,
            )
        self._close_term()
        inst.doctrine = make_doctrine(orientation, inst.rng, live)
        self.government = Government(orientation, self.month, dict(inst.doctrine.positions), reelected)
        self.government_history.append(self.government)
        motive = (
            f"réélection du sortant (bilan : {record:.0%} des objectifs atteints)"
            if reelected else "alternance"
        )
        self._open_term(reelected, motive)

    def _voters(self) -> list[Voter]:
        """Électeurs (idée 4) : un par ménage, dans l'ordre de la population ; proximité politique
        tirée une fois, rang de fortune nette, revenu réel attendu depuis le début du mandat,
        fréquence lissée de ses mois sans emploi (``unemployment_risk``)."""
        from economy.wealth import household_net_worth, share_prices

        inst = self.institutions
        self._ensure_leans()
        prices = share_prices(self.companies)
        worth = {p.id: household_net_worth(p, self.companies, self.live, prices) for p in self.people}
        order = sorted(self.people, key=lambda p: (worth[p.id], p.id))
        count = len(order)
        rank = {p.id: (i / (count - 1) if count > 1 else 0.5) for i, p in enumerate(order)}
        deflator = max(self.price_index, 1e-9)
        voters = []
        for person in self.people:
            start = inst.term_income.get(person.id)
            now = person.expected_income / deflator
            growth = now / start - 1.0 if start and start > 0.0 else 0.0
            voters.append(Voter(inst.lean[person.id], rank[person.id], growth, person.unemployment_risk))
        return voters

    def _objectives_met_share(self) -> float:
        """Part des objectifs ministériels du mandat en cours atteints à ce jour."""
        offices = [o for o in self.institutions.offices.values() if o.objective is not None]
        if not offices:
            return 0.5
        return sum(o.objective.met(o.objective.value) for o in offices) / len(offices)

    def _close_term(self) -> None:
        """Bilan des objectifs à la fin du mandat, conservé dans l'historique des gouvernements."""
        inst = self.institutions
        term = inst.terms[-1]
        statuses = {}
        for role, office in inst.offices.items():
            if office.objective is not None:
                statuses[role] = office.objective.close()
            self._end_membership(term, office)
        term.until = self.month
        term.record = statuses
        term.objectives_met = (
            sum(status == "atteint" for status in statuses.values()) / len(statuses) if statuses else 0.0
        )
        self._decide(
            "presidence", "Bilan du gouvernement",
            ", ".join(f"{OFFICE_LABELS[r]} : {s}" for r, s in statuses.items()) or "aucun objectif",
            amount=term.objectives_met,
        )

    def _open_term(self, reelected: bool, motive: str) -> None:
        """Forme le gouvernement : présidence puis quatre ministres, chacun avec un objectif.

        Réélection : la présidence et les ministres dont l'objectif n'est pas manqué sont
        reconduits (nouvel objectif) ; les autres retournent sur le marché du travail.
        """
        inst, live = self.institutions, self.live
        # revenu réel attendu de chaque ménage au début du mandat : base de son bilan personnel
        deflator = max(self.price_index, 1e-9)
        inst.term_income = {p.id: p.expected_income / deflator for p in self.people}
        previous = dict(inst.offices)
        kept = {}
        if reelected:
            kept = {
                role: office for role, office in previous.items()
                if role == "presidence" or office.objective is None or office.objective.status != "manqué"
            }
        people = {p.id: p for p in self.people}
        for role, office in previous.items():
            if role not in kept and office.person_id in people:
                self._release(people[office.person_id])
        inst.offices = {}
        term = GovernmentTerm(self.month, self.government.orientation, reelected, inst.doctrine)
        inst.terms.append(term)
        doctrine = inst.doctrine
        self._decide(
            "finances", "Fiscalité des revenus du capital et ciblage des transferts",
            ("dividendes et loyers imposés au barème avec les salaires"
             if doctrine.capital_flat_factor is None else
             f"dividendes et loyers imposés au forfait : taux de base × {doctrine.capital_flat_factor:.2f}")
            + f" ; {doctrine.transfer_targeting:.0%} des transferts sous condition de ressources",
            amount=0.0, financing="impôt sur le revenu (assiette élargie au capital)",
        )
        values = self._indicator_values()
        deadline = self.month + live.election_period_months - 1
        excluded = {office.person_id for office in kept.values()}
        envelopes = self._ministry_envelopes()
        for role in OFFICES:
            if role in kept:
                office = kept[role]
                office.since = self.month
                office.refusals = ()
                reason = "reconduction"
            else:
                office = self._nominate(role, excluded)
                reason = "nomination"
            if role != "presidence":
                office.objective = make_objective(
                    role, inst.doctrine, values, self.month, deadline, live, envelopes.get(role, 0.0)
                )
            inst.offices[role] = office
            excluded.add(office.person_id)
            self._start_membership(term, office)
            person = people.get(office.person_id)
            objective = office.objective.label if office.objective else "porter le programme, arbitrer les enveloppes"
            note = self._interest_note(office)
            self._decide(
                "presidence" if role != "presidence" else "presidence",
                f"{reason.capitalize()} : {OFFICE_ROLES[role]}",
                f"{motive} ; objectif : {objective}"
                + (f" ; {len(office.refusals)} refus avant acceptation" if office.refusals else ""),
                amount=self._office_salary(role),
                financing="budget de l'État (salaire mensuel)",
                note=note,
                person=display_name(person.id) if person else office.person_id,
            )

    def _reshuffle(self) -> None:
        """Remaniement à mi-mandat : un ministre dont l'objectif s'éloigne gravement (progression
        ≤ −0,5) est remplacé ; son successeur reprend le même objectif."""
        inst = self.institutions
        term = inst.terms[-1]
        people = {p.id: p for p in self.people}
        for role in PORTFOLIOS:
            office = inst.offices.get(role)
            if office is None or office.objective is None or office.objective.progress > -0.5:
                continue
            self._end_membership(term, office)
            if office.person_id in people:
                self._release(people[office.person_id])
            excluded = {o.person_id for r, o in inst.offices.items() if r != role}
            successor = self._nominate(role, excluded | {office.person_id})
            successor.objective = office.objective
            inst.offices[role] = successor
            self._start_membership(term, successor)
            self._decide(
                "presidence", f"Remaniement : {OFFICE_ROLES[role]}",
                f"objectif gravement manqué à mi-mandat ({office.objective.label} ; "
                f"progression {office.objective.progress:+.0%})",
                amount=self._office_salary(role), financing="budget de l'État (salaire mensuel)",
                note=self._interest_note(successor),
            )

    # -- nominations -----------------------------------------------------------------
    def _ensure_leans(self) -> None:
        """Trait de proximité politique : tiré une fois par ménage, dans l'ordre de la population."""
        inst = self.institutions
        for person in self.people:
            if person.id not in inst.lean:
                inst.lean[person.id] = political_lean(inst.rng)

    def _nominate(self, role: str, excluded: set) -> Office:
        """Choisit le titulaire d'une fonction : score de proximité, direction, métier, carrière ;
        une personne pressentie dont la rémunération dépasse nettement le salaire proposé refuse."""
        inst, live = self.institutions, self.live
        self._ensure_leans()
        orientation = inst.doctrine.orientation
        executives = {c.executive_id for c in self.companies if c.active and c.executive_id}
        candidates = [
            p for p in self.people if p.id not in excluded and p.retraining_months <= 0
        ]
        candidates.sort(key=lambda p: (
            -candidate_score(role, p, inst.lean[p.id], orientation, p.id in executives), p.id
        ))
        salary = self._office_salary(role)
        limit = salary * (1.0 + live.cabinet_refusal_tolerance)
        refusals = []
        chosen = None
        for person in candidates:
            if person.current_wage > limit:
                refusals.append(person.id)
                continue
            chosen = person
            break
        if chosen is None:  # tous refusent : le mieux classé accepte malgré tout
            chosen = candidates[0]
        if refusals:
            inst.refusals_since_revision += len(refusals)
            names = ", ".join(display_name(pid) for pid in refusals[:3])
            self._decide(
                "presidence", f"Refus de {len(refusals)} personne(s) pressentie(s) : {OFFICE_ROLES[role]}",
                f"rémunération privée supérieure de plus de {live.cabinet_refusal_tolerance:.0%} au "
                f"salaire proposé ({names}{'…' if len(refusals) > 3 else ''})",
                amount=salary,
            )
        office = Office(
            role, chosen.id, self.month,
            previous_employer=self.company_name(chosen.employer_id) if chosen.employer_id else "Sans emploi",
            holdings=tuple(sorted(cid for cid, q in chosen.shares.items() if q > 0.0)),
            refusals=tuple(refusals),
        )
        self._appoint(chosen, role)
        return office

    def _appoint(self, person, role: str) -> None:
        """La personne quitte son emploi (entreprise ou service public) et entre au cabinet."""
        employer = person.employer_id
        if employer not in (None, "state"):
            company = next((c for c in self.companies if c.id == employer), None)
            if company is not None and person.id in company.employees:
                company.employees.remove(person.id)
        inst = self.institutions
        inst.agents.pop(person.id, None)
        inst.pending_recruits.pop(person.id, None)
        person.employer_id = "state"
        person.employment_role = OFFICE_ROLES[role]
        person.job_search_months = 0
        person.employer_tenure = 0

    @staticmethod
    def _release(person) -> None:
        """Fin de fonction : la personne redevient candidate sur le marché du travail."""
        person.employer_id = None
        person.employment_role = "Sans emploi"
        person.current_wage = 0.0
        person.employer_tenure = 0
        person.job_search_months = 0

    def _start_membership(self, term: GovernmentTerm, office: Office) -> None:
        objective = office.objective
        term.members.append({
            "role": office.role, "person_id": office.person_id, "name": display_name(office.person_id),
            "since": self.month, "until": None, "previous_employer": office.previous_employer,
            "salary_start": self._office_salary(office.role), "salary_end": None,
            "objective": objective.label if objective else "", "status": "en cours" if objective else "",
            "refusals": len(office.refusals),
        })

    def _end_membership(self, term: GovernmentTerm, office: Office) -> None:
        for member in reversed(term.members):
            if member["person_id"] == office.person_id and member["role"] == office.role and member["until"] is None:
                member["until"] = self.month
                member["salary_end"] = self._office_salary(office.role)
                if office.objective is not None:
                    member["status"] = office.objective.status
                    member["value"] = office.objective.value
                break

    def _interest_note(self, office: Office, target: str | None = None) -> str:
        """Conflit d'intérêts consigné : titres détenus à la nomination (les trois premières lignes
        en valeur) ou, pour une décision visant ``target``, titres de cette entreprise."""
        person = next((p for p in self.people if p.id == office.person_id), None)
        if person is None:
            return ""
        prices = {c.id: c.stock_price for c in self.companies if c.active}
        if target is not None:
            quantity = person.shares.get(target, 0.0)
            if quantity <= 0.0:
                return ""
            return (f"conflit d'intérêts : {display_name(person.id)} détient {quantity:.1f} titres de "
                    f"{self.company_name(target)} ({quantity * prices.get(target, 0.0):,.0f} €)").replace(",", " ")
        values = sorted(
            ((q * prices.get(cid, 0.0), cid) for cid, q in person.shares.items() if q > 0.0), reverse=True
        )
        if not values:
            return ""
        top = ", ".join(f"{self.company_name(cid)} ({value:,.0f} €)".replace(",", " ") for value, cid in values[:3])
        return f"titres de {len(values)} entreprise(s), principales lignes : {top}"

    # -- objectifs -------------------------------------------------------------------
    def _median_private_wage(self) -> float:
        wages = [
            p.current_wage for p in self.people
            if p.employer_id not in (None, "state") and p.current_wage > 0.0
        ]
        return statistics.median(wages) if wages else 0.0

    def _state_controls(self, company) -> bool:
        circulating = company.shares_outstanding - company.treasury_shares
        held = self.public.account.shares.get(company.id, 0.0)
        return circulating > 0.0 and held >= self.live.control_threshold * circulating

    def _dominated_sectors(self) -> list[str]:
        """Secteurs où un problème de monopole est confirmé (diagnostic du mois écoulé, voir
        ``state_industry.py``) sans opérateur public ni régulation."""
        return [
            sector for sector, data in sorted(self.institutions.diagnostic.items())
            if data["problem"] and not data["regulated"] and not data["public_operator"]
        ]

    def _non_strategic_portfolio(self) -> float:
        """Valeur des participations non stratégiques : hors secteur critique ou sans contrôle."""
        companies = {c.id: c for c in self.companies if c.active}
        critical = set(self.live.critical_sectors)
        return sum(
            quantity * companies[cid].stock_price
            for cid, quantity in self.public.account.shares.items()
            if cid in companies and not (companies[cid].sector in critical and self._state_controls(companies[cid]))
        )

    def _indicator_values(self) -> dict[str, float]:
        """Valeur courante des indicateurs des objectifs (observations du mois écoulé)."""
        recent = self.history[-12:]
        gdp = sum(r.nominal_gdp for r in recent)
        revenue = sum(r.public_revenue for r in recent)
        spending = sum(r.public_spending for r in recent)
        interest = sum(r.public_interest for r in recent)
        median = self._median_private_wage()
        dominated = self._dominated_sectors()
        critical = set(self.live.critical_sectors)
        public = self.public
        return {
            "dette_pib": public.debt / max(self._annual_gdp(), 1.0),
            "solde_pib": (revenue - spending) / gdp if gdp > 0.0 else 0.0,
            "interets_recettes": interest / revenue if revenue > 0.0 else 0.0,
            "smic_median": self.live.minimum_wage / median if median > 0.0 else 0.0,
            "chomage": (
                sum(r.unemployment for r in recent) / len(recent) if recent
                else self.live.natural_unemployment_rate
            ),
            "couverture_services": self._service_coverage(),
            "masse_salariale_pib": 12.0 * public.wage_bill / max(self._annual_gdp(), 1.0),
            "monopoles_critiques": float(sum(s in critical for s in dominated)),
            "monopoles": float(len(dominated)),
            "portefeuille_non_strategique": self._non_strategic_portfolio(),
        }

    def _track_objectives(self) -> None:
        inst = self.institutions
        values = self._indicator_values()
        for office in inst.offices.values():
            if office.objective is not None:
                office.objective.update(values[office.objective.indicator])

    def _ministry_envelopes(self) -> dict[str, float]:
        """Enveloppes annuelles des ministères (livraison 2 : dépenses observées sur douze mois)."""
        recent = self.history[-12:]
        scale = 12.0 / len(recent) if recent else 0.0
        return {
            "finances": scale * sum(r.public_interest for r in recent),
            "travail": scale * sum(r.benefits_paid + r.public_transfers for r in recent),
            "industrie": scale * sum(max(0.0, r.state_equity_flow) for r in recent),
            "services": scale * sum(r.government_purchases for r in recent) + 12.0 * self.public.wage_bill,
        }

    # -- salaires politiques -----------------------------------------------------------
    def _executive_pay(self) -> float:
        """Rémunération moyenne des dirigeants d'entreprise payés ce mois (référence du privé)."""
        pays = [
            p.current_wage for p in self.people
            if p.employment_role == "Direction" and p.employer_id not in (None, "state") and p.current_wage > 0.0
        ]
        return sum(pays) / len(pays) if pays else 0.0

    def _salary_rule(self) -> tuple[float, bool]:
        """Salaire de la présidence selon la règle et la doctrine du gouvernement en place, et si
        le plafond social s'applique.

        Comparables observés avec retard : haut de la grille publique, rémunération lissée des
        dirigeants du privé moins une décote publique (réduite au tiers si le gouvernement
        s'aligne sur le privé), le tout multiplié par la majoration d'attractivité née des refus ;
        un gouvernement social plafonne l'écart au salaire minimum.
        """
        inst, live = self.institutions, self.live
        discount = live.cabinet_salary_discount
        if inst.doctrine.salary_policy == "aligne_prive":
            discount /= 3.0
        salary = max(self._grid_top(), inst.executive_pay * (1.0 - discount)) * inst.salary_markup
        if inst.doctrine.salary_policy == "plafond_smic" and live.minimum_wage > 0.0:
            cap = live.social_pay_ratio_cap * live.minimum_wage
            if salary > cap:
                return cap, True
        return salary, False

    def _set_initial_salaries(self) -> None:
        inst, live = self.institutions, self.live
        president, _ = self._salary_rule()
        inst.salaries = {"presidence": president, "ministre": president * live.minister_salary_ratio}
        inst.pending_raise = {}

    def _office_salary(self, role: str) -> float:
        salaries = self.institutions.salaries
        return salaries.get("presidence" if role == "presidence" else "ministre", 0.0)

    def _revise_political_salaries(self) -> None:
        """Révision annuelle des salaires de la présidence et des ministres.

        La hausse décidée à la révision précédente s'applique ; puis le gouvernement en place
        décide le salaire de l'an prochain selon la règle. Des refus dans l'année relèvent
        durablement la majoration d'attractivité (``cabinet_refusal_raise`` par refus, au plus
        +25 % par révision) ; sans refus, elle revient lentement vers 1 (5 % de l'écart par an).
        Une hausse ne s'applique qu'à la révision suivante ; la règle ne baisse jamais le salaire
        nominal (gel) ; seul le plafond d'un gouvernement social le baisse, aussitôt.
        """
        inst, live = self.institutions, self.live
        current = inst.salaries.get("presidence", 0.0)
        if inst.pending_raise:
            inst.salaries = dict(inst.pending_raise)
            inst.pending_raise = {}
            self._decide(
                "presidence", "Salaires politiques : hausse appliquée",
                "hausse décidée à la révision précédente",
                amount=inst.salaries["presidence"] - current, financing="budget de l'État",
            )
            current = inst.salaries["presidence"]
        refusals = inst.refusals_since_revision
        inst.refusals_since_revision = 0
        if refusals:
            inst.salary_markup *= 1.0 + min(0.25, live.cabinet_refusal_raise * refusals)
        else:
            inst.salary_markup = 1.0 + 0.95 * (inst.salary_markup - 1.0)
        wanted, capped = self._salary_rule()
        decided = {"presidence": wanted, "ministre": wanted * live.minister_salary_ratio}
        motive = (
            f"comparables publics et privés retardés, politique « {inst.doctrine.salary_policy} », "
            f"majoration d'attractivité × {inst.salary_markup:.2f}"
            + (f" ({refusals} refus dans l'année)" if refusals else "")
        )
        if capped and wanted < current:
            inst.salaries = decided
            self._decide(
                "presidence", "Salaires politiques : plafonnement social immédiat",
                f"présidence au plus {live.social_pay_ratio_cap:g} fois le salaire minimum",
                amount=wanted - current, financing="budget de l'État",
            )
        elif wanted > current:
            inst.pending_raise = decided
            self._decide("presidence", "Salaires politiques : hausse à la révision suivante",
                         motive, amount=wanted - current, financing="budget de l'État")
        elif wanted < current:
            self._decide("presidence", "Salaires politiques : gel", motive + " ; règle sous le salaire en vigueur",
                         amount=0.0, financing="budget de l'État")

    # -- phase 2 : paie du cabinet -------------------------------------------------------
    def _pay_cabinet(self) -> tuple[float, int]:
        """Salaires de la présidence et des ministres, payés par le budget public (emprunt si la
        trésorerie manque) ; retourne (montant payé, membres payés)."""
        inst = self.institutions
        people = {p.id: p for p in self.people}
        paid_total, count = 0.0, 0
        for role in OFFICES:
            office = inst.offices.get(role)
            person = people.get(office.person_id) if office else None
            if person is None:
                continue
            salary = self._office_salary(role)
            available = self._state_funds(salary)
            paid = max(0.0, min(salary, available))
            transfer_cash(self.public, person, paid)
            if salary - paid > 1e-9:
                # financement impossible : salaire dû, réglé en priorité les mois suivants
                inst.arrears_wages[person.id] = inst.arrears_wages.get(person.id, 0.0) + salary - paid
            person.income_this_turn += paid
            person.lifetime_earnings += paid
            person.current_wage = salary
            person.employer_tenure += 1
            person.career_months += 1
            paid_total += paid
            count += 1
        inst.book.add("salaires_cabinet", -paid_total)
        return paid_total, count

    # -- journal et historique -------------------------------------------------------------
    def _decide(self, role: str, decision: str, motive: str, amount: float = 0.0,
                financing: str = "", note: str = "", person: str | None = None) -> None:
        """Consigne une décision : auteur (fonction et personne), date, motif, montant, financement."""
        inst = self.institutions
        author = OFFICE_LABELS.get(role, role)
        if person is None:
            holder = inst.holder(role)
            person = display_name(holder) if holder else author
        inst.journal.append(Decision(self.month, author, person, decision, motive, amount, financing, note))

    def _record_institutions(self, f) -> None:
        """Phase 8 : historique mensuel propre au nouvel État, rapprochements du mois et référence
        de rémunération du privé."""
        inst = self.institutions
        executive = self._executive_pay()
        if executive > 0.0:
            inst.executive_pay = executive if inst.executive_pay <= 0.0 else (
                inst.executive_pay + (executive - inst.executive_pay) / 12.0
            )
        progress = {
            role: office.objective.progress
            for role, office in inst.offices.items() if office.objective is not None
        }
        record = StateRecord(
            month=self.month,
            orientation=self.government.orientation,
            president_salary=inst.salaries.get("presidence", 0.0),
            minister_salary=inst.salaries.get("ministre", 0.0),
            cabinet_pay=inst.cabinet_pay,
            refusals=inst.refusals_since_revision,
            progress=progress,
            indicators={
                role: office.objective.value
                for role, office in inst.offices.items() if office.objective is not None
            },
        )
        self._close_state_book(f, record)
        self._record_services(record)
        self._record_minimum_wage(record)
        self._record_industry(record)
        inst.history.append(record)

    def _institutions_after_warmup(self) -> None:
        """Fin de la chauffe, comme ``government_history`` et les événements de résolution : le
        journal des décisions repart vide, l'historique des gouvernements ne garde que le mandat
        en cours, l'historique mensuel ses 24 derniers mois (observations retardées)."""
        inst = self.institutions
        inst.journal = []
        inst.terms = inst.terms[-1:]
        inst.history = inst.history[-24:]
        inst.laws = inst.laws[-1:]
        inst.minimum_wage_revisions = inst.minimum_wage_revisions[-1:]
        inst.elections = inst.elections[-1:]
        # dossiers en cours et participations détenues gardés ; dossiers clos et participations
        # terminées pendant la chauffe retirés, comme les événements de résolution
        inst.cases = [case for case in inst.cases if case.status != "décidé"]
        inst.closed_holdings = []
