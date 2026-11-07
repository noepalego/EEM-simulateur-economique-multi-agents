"""Politique industrielle du nouvel État (``state_model="institutions"``) : diagnostic des marchés,
régulation, entreprise publique, nationalisation, recapitalisation, cessions selon des règles.

Phase 7 (``_industry_dynamics``) : avant les faillites, le ministre de l'Industrie peut
recapitaliser une entreprise publique en difficulté (règle explicite) ; après elles, l'État
prend en charge les participations reçues (restructuration du dernier acteur, résolution
bancaire), diagnostique les marchés, ouvre et instruit les dossiers de monopole (capacité du
service de la Concurrence) et exécute les réponses ; une fois par an, il réexamine chaque
participation. Phase 6 (``_close_books``) : les cessions décidées passent par le carnet d'ordres
de la Bourse, de vrais acheteurs paient les titres.

Conventions (docs/ETAT.md) : contrôle à ``control_threshold`` des titres en circulation (titres en
trésorerie sans droit de vote, comme ``consolidation.py``) ; nationalisation par achat de titres
existants au cours × (1 + ``control_premium``), les vendeurs sont payés ; recapitalisation par
titres nouveaux, dilution explicite ; capital d'une entreprise publique, achats et
recapitalisations sont des opérations financières (ni consommation publique ni PIB), financées
par la trésorerie ou l'emprunt. Le mandat public change des décisions réelles : prix modérés
(agressivité commerciale maximale), bénéfices réinvestis (distribution limitée), aucune
acquisition, protection contre les offres publiques ; il ne rend l'entreprise ni plus
productive ni immortelle (faillite selon les règles, sauf recapitalisation décidée).
"""

from __future__ import annotations

from economy.finance import _entrant_wage_offer
from economy.institutions import RESPONSE_LABELS, MonopolyCase, PublicHolding, display_name
from economy.labor import skill_level, workforce_targets
from economy.models import Company
from economy.payments import transfer_cash
from economy.sectors import SECTORS
from economy.stock_market import _fundamental_price

# la régulation agit où le prix répond à la concentration (marge visée ou érosion concurrentielle)
REGULABLE_SECTORS = ("food", "goods", "energy", "technology")
# une entreprise publique peut y démarrer comme un entrant (ni centrales ni parc de logements à bâtir)
CREATABLE_SECTORS = ("food", "goods", "services", "materials", "technology", "bank")
SECTOR_LABELS = dict(SECTORS)


class StateIndustryMixin:
    """Diagnostic, dossiers de monopole, entreprises publiques et participations (nouveau mode)."""

    # -- lectures (aussi appelées hors du nouveau mode : aucun état créé) -------------------
    def _regulated(self, sector: str) -> bool:
        inst = getattr(self, "institutions", None)
        return inst is not None and sector in inst.regulated

    def _regulated_concentration(self, sector: str, concentration: float) -> float:
        """Concentration retenue par les prix : un secteur régulé est traité comme concurrentiel."""
        if self._regulated(sector):
            return min(concentration, self.live.concentration_margin_reference)
        return concentration

    def _public_controlled_ids(self) -> frozenset:
        inst = getattr(self, "institutions", None)
        if inst is None:
            return frozenset()
        return frozenset(cid for cid, holding in inst.holdings.items() if holding.controlled)

    def _operation_ceiling(self) -> float:
        return self.live.industry_operation_gdp_share * self._annual_gdp()

    # -- diagnostic --------------------------------------------------------------------------
    def _sector_unmet(self, sector: str, firms: list) -> float:
        last = self.history[-1] if self.history else None
        if last is None:
            return 0.0
        if sector == "food":
            return last.unmet_food_demand / max(1.0, last.food_sold + last.unmet_food_demand)
        if sector == "goods":
            return last.unmet_goods_demand / max(1.0, last.goods_sold + last.unmet_goods_demand)
        if sector == "energy":
            return last.energy_unmet / max(1.0, last.energy_delivered + last.energy_unmet)
        if sector == "technology":
            unmet = self.resources.tech_unmet_units
            return unmet / max(1.0, sum(c.previous_units_sold for c in firms) + unmet)
        return 0.0

    def _diagnose_markets(self) -> dict:
        """Diagnostic mensuel de chaque secteur : concentration, marge, demande non servie,
        entrants, opérateur public ; problème de monopole confirmé après une durée minimale."""
        inst, live = self.institutions, self.live
        diagnostic = {}
        for sector in sorted({c.sector for c in self.companies if c.active}):
            firms = [c for c in self.companies if c.active and c.sector == sector]
            revenue = sum(c.previous_revenue for c in firms)
            hhi = sum((c.previous_revenue / revenue) ** 2 for c in firms) if revenue > 0.0 else 1.0
            margin = sum(c.previous_profit for c in firms) / revenue if revenue > 0.0 else 0.0
            base = live.sector_margin_target.get(sector, live.profit_margin_target)
            unmet = self._sector_unmet(sector, firms)
            entrants = sum(1 for c in firms if c.founder_id is not None and c.age_months <= 24 and c.previous_revenue > 0.0)
            abnormal = (
                hhi >= live.concentration_margin_reference
                and (margin >= base * (1.0 + live.monopoly_margin_excess) or unmet >= live.monopoly_unmet_share)
                and entrants == 0
            )
            months = inst.monopoly_months.get(sector, 0) + 1 if abnormal else 0
            inst.monopoly_months[sector] = months
            diagnostic[sector] = {
                "firms": len(firms), "hhi": hhi, "margin": margin, "base_margin": base, "unmet": unmet,
                "entrants": entrants, "dominant": max(firms, key=lambda c: (c.previous_revenue, c.id)).id,
                "public_operator": any(self._state_controls(c) for c in firms),
                "regulated": sector in inst.regulated, "critical": sector in live.critical_sectors,
                "abnormal": abnormal, "months": months,
                "problem": months >= live.monopoly_duration_months,
            }
        inst.diagnostic = diagnostic
        return diagnostic

    # -- phase 7, avant les faillites : recapitalisations ------------------------------------
    def _rescue_public_companies(self) -> None:
        """Une entreprise publique (non bancaire) au bord de la faillite n'est soutenue que par une
        recapitalisation décidée : secteur critique ou doctrine d'opérateur public, au plus
        ``max_recapitalizations`` fois, financée ; sinon elle suit les règles de faillite."""
        inst, live = self.institutions, self.live
        for company_id, holding in list(inst.holdings.items()):
            company = self._company(company_id)
            if company is None or not holding.controlled or company.sector == "bank":
                continue
            streak = company.loss_streak + 1 if company.profit < 0.0 else 0
            assets = (
                company.cash + company.property_inventory * live.housing_price + company.construction_in_progress
                + company.inventory * live.food_stock_price + company.goods_inventory * live.goods_stock_price
                + company.productive_capital_value + company.technology_asset_value
            )
            at_risk = company.debt > assets or (
                company.debt > 0.0 and company.cash <= company.wage_offer and streak >= live.bankruptcy_months
            )
            if not at_risk:
                continue
            allowed = (
                holding.recapitalizations < live.max_recapitalizations
                and (company.sector in live.critical_sectors or inst.doctrine.industry_stance == "operateur_public")
            )
            amount = min(
                self._operation_ceiling(),
                max(0.0, company.debt - assets)
                + 3.0 * max(company.wage_bill, company.wage_offer * max(1, len(company.employees))),
            )
            if allowed and self._recapitalize(company, holding, amount, "difficulté de paiement d'une entreprise publique"):
                continue
            if holding.motive != "faillite laissée aux règles":
                holding.motive = "faillite laissée aux règles"
                self._decide(
                    "industrie", f"Pas de recapitalisation : {company.name}",
                    "règle : " + ("nombre maximal de recapitalisations atteint" if holding.recapitalizations
                                  >= live.max_recapitalizations else "secteur non critique, doctrine sans opérateur public"
                                  if not allowed else "financement impossible"),
                    note=self._target_conflicts(company.id),
                )

    # -- phase 7, après les faillites -------------------------------------------------------
    def _industrial_policy(self) -> None:
        inst = self.institutions
        self._register_holdings()
        diagnostic = self._diagnose_markets()
        self._lift_regulations(diagnostic)
        self._open_cases(diagnostic)
        self._progress_cases()
        if (self.month - inst.created) % 12 == 0 and self.month != inst.created:
            self._review_holdings()
        self._sync_public_debt()  # opérations financées par emprunt

    def _company(self, company_id: str):
        return next((c for c in self.companies if c.id == company_id and c.active), None)

    def _count(self, key: str) -> None:
        month = self.institutions.industry_month
        month[key] = month.get(key, 0) + 1

    def _book_acquisition(self, amount: float) -> None:
        """Opération financière : titres ou capital acquis (livre du mois, flux de participations)."""
        public = self.public
        self.institutions.book.add("acquisitions_financieres", -amount)
        public.equity_invested += amount
        public.total_equity_invested += amount

    def _register_holdings(self) -> None:
        """Participations reçues (restructuration, résolution bancaire, échange de titres), fins de
        participation (faillite, absorption, cession totale) et changements de contrôle."""
        inst, state = self.institutions, self.public.account
        restructured = {e.company_id for e in self.resolution_events if e.month == self.month and e.kind == "restructure"}
        for company_id, quantity in list(state.shares.items()):
            if quantity <= 0.0 or company_id in inst.holdings:
                continue
            company = self._company(company_id)
            if company is None:
                continue
            origin = ("restructuration" if company_id in restructured
                      else "résolution bancaire" if company.sector == "bank" else "participation")
            holding = PublicHolding(company_id, origin, self.month)
            inst.holdings[company_id] = holding
            self._decide("industrie", f"Prise en charge d'une participation : {company.name}",
                         f"origine : {origin} ; {quantity / max(company.shares_outstanding, 1.0):.0%} du capital")
        for company_id, holding in list(inst.holdings.items()):
            company = next((c for c in self.companies if c.id == company_id), None)
            held = state.shares.get(company_id, 0.0)
            if company is None or not company.active or held <= 0.0:
                reason = (
                    "privatisée" if company is not None and company.active
                    else {"bankrupt": "faillite", "acquired": "absorbée", "resolved": "résolue",
                       "dissolved": "dissoute"}.get(
                        company.exit_reason if company is not None else "", "fermée")
                )
                holding.result = reason
                holding.controlled = False
                inst.closed_holdings.append(holding)
                del inst.holdings[company_id]
                self._decide("industrie", f"Fin de participation : {company.name if company else company_id}",
                             f"résultat : {reason} ; investi {holding.invested:,.0f}, cédé {holding.proceeds:,.0f}".replace(",", " "))
                continue
            controls = self._state_controls(company)
            if controls and not holding.controlled:
                self._apply_mandate(company, holding)
                if holding.origin == "restructuration":
                    holding.mandate = "administration provisoire : " + holding.mandate
                    self._appoint_director(company, holding, internal=True)
            elif holding.controlled and not controls:
                self._end_mandate(company, holding)

    def _lift_regulations(self, diagnostic: dict) -> None:
        """Une régulation est levée après 24 mois sans problème de concentration dans le secteur."""
        inst = self.institutions
        for sector in list(inst.regulated):
            calm = not diagnostic.get(sector, {}).get("abnormal", False)
            inst.regulation_clear_months[sector] = inst.regulation_clear_months.get(sector, 0) + 1 if calm else 0
            if inst.regulation_clear_months[sector] >= 24:
                del inst.regulated[sector]
                inst.regulation_clear_months.pop(sector, None)
                self._decide("industrie", f"Levée de la régulation : {SECTOR_LABELS.get(sector, sector)}",
                             "24 mois sans concentration anormale")

    def _open_cases(self, diagnostic: dict) -> None:
        inst = self.institutions
        open_sectors = {case.sector for case in inst.cases if case.status != "décidé"}
        for sector, data in diagnostic.items():
            if not data["problem"] or data["regulated"] or data["public_operator"] or sector in open_sectors:
                continue
            motive = (
                f"concentration {data['hhi']:.2f}, marge {data['margin']:.1%} (référence {data['base_margin']:.1%}), "
                f"demande non servie {data['unmet']:.1%}, {data['months']} mois sans entrant"
            )
            inst.cases.append(MonopolyCase(sector, self.month, target=data["dominant"], motive=motive))
            self._decide("industrie", f"Dossier de monopole ouvert : {SECTOR_LABELS.get(sector, sector)}", motive)

    def _progress_cases(self) -> None:
        """Instruction par le service de la Concurrence (capacité = sa couverture, plafonnée à un
        mois de service complet par mois), du dossier le plus ancien au plus récent ; un dossier
        non financé est repris chaque mois sans nouvelle instruction."""
        inst, live = self.institutions, self.live
        service = inst.services.get("concurrence")
        capacity = min(1.0, service.coverage) if service is not None else 0.0
        for case in inst.cases:
            if case.status == "décidé":
                continue
            if case.status == "instruction":
                if capacity <= 0.0:
                    continue
                step = min(capacity, live.case_work_months - case.work)
                case.work += step
                capacity -= step
                if case.work + 1e-9 < live.case_work_months:
                    continue
            self._decide_case(case)

    def _case_response(self, sector: str, data: dict) -> str:
        """Réponse selon la doctrine industrielle, la criticité du secteur et la faisabilité."""
        stance = self.institutions.doctrine.industry_stance
        priced = sector in REGULABLE_SECTORS
        if stance == "laisser_faire":
            return "regulation" if data["critical"] and priced else "attente"
        if stance == "reguler" or not data["critical"]:
            return "regulation" if priced else "attente"
        target = self._company(data["dominant"])
        if target is not None and self._nationalization_cost(target) is not None:
            return "nationalisation"
        if sector in CREATABLE_SECTORS:
            return "entreprise_publique"
        return "regulation" if priced else "attente"

    def _decide_case(self, case: MonopolyCase) -> None:
        inst = self.institutions
        data = inst.diagnostic.get(case.sector)
        label = SECTOR_LABELS.get(case.sector, case.sector)
        if data is None or not data["problem"] or data["public_operator"] or data["regulated"]:
            case.status, case.response, case.decided = "décidé", "attente", self.month
            self._decide("industrie", f"Dossier classé : {label}", "le problème a disparu pendant l'instruction")
            return
        response = self._case_response(case.sector, data)
        case.response = response
        done = True
        if response == "regulation":
            inst.regulated[case.sector] = self.month
            self._count("regulations")
            self._decide("industrie", f"Régulation des prix : {label}",
                         f"{case.motive} ; les prix n'intègrent plus de marge de monopole",
                         note=self._target_conflicts(data["dominant"]))
        elif response == "entreprise_publique":
            done = self._create_public_company(case.sector, case.motive)
        elif response == "nationalisation":
            target = self._company(data["dominant"])
            done = target is not None and self._nationalize(target, case.motive)
        else:
            inst.monopoly_months[case.sector] = 0
            self._decide("industrie", f"Monopole : attendre l'entrée de concurrents ({label})",
                         f"{case.motive} ; doctrine « {inst.doctrine.industry_stance} »")
        if done:
            case.status, case.decided = "décidé", self.month
        elif case.status != "non financé":
            case.status = "non financé"
            self._decide("industrie", f"Opération non financée : {RESPONSE_LABELS[response]} ({label})",
                         "trésorerie et emprunt insuffisants (limite légale ou capacité des prêteurs)")

    # -- opérations ---------------------------------------------------------------------------
    def _target_conflicts(self, company_id: str) -> str:
        notes = [self._interest_note(office, target=company_id) for office in self.institutions.offices.values()]
        return " ; ".join(note for note in notes if note)

    def _apply_mandate(self, company, holding: PublicHolding) -> None:
        """Mandat public : prix modérés, bénéfices réinvestis, aucune acquisition, protection."""
        if not holding.previous_traits:
            holding.previous_traits = {
                "pricing_aggression": company.pricing_aggression,
                "reinvestment_preference": company.reinvestment_preference,
            }
        company.pricing_aggression = 1.0
        company.reinvestment_preference = 1.0
        holding.controlled = True
        holding.mandate = "prix modérés, bénéfices réinvestis, aucune acquisition, continuité du service"

    def _end_mandate(self, company, holding: PublicHolding) -> None:
        for trait, value in holding.previous_traits.items():
            setattr(company, trait, value)
        holding.controlled = False
        self._decide("industrie", f"Fin du contrôle public : {company.name}",
                     "part de l'État sous le seuil de contrôle : le mandat public prend fin")

    def _appoint_director(self, company, holding: PublicHolding, internal: bool):
        """Dirigeant nommé par le ministre de l'Industrie : compétence dans le secteur, expérience
        de direction, carrière ; parmi les salariés (reprise) ou parmi les ménages (création)."""
        inst = self.institutions
        if internal:
            pool = [p for p in self.people if p.id in company.employees]
        else:
            excluded = {o.person_id for o in inst.offices.values()} | set(inst.agents) | set(inst.pending_recruits)
            pool = [p for p in self.people if p.id not in excluded and p.retraining_months <= 0]
        if not pool:
            return None
        director = max(pool, key=lambda p: (
            skill_level(p, company.sector) + p.leadership_experience / 120.0 + p.career_months / 240.0 + p.skill,
            p.id,
        ))
        if not internal:
            employer = next((c for c in self.companies if c.id == director.employer_id), None)
            if employer is not None and director.id in employer.employees:
                employer.employees.remove(director.id)
            director.employer_id = company.id
            director.employment_role = "Direction"
            director.job_search_months = 0
            director.employer_tenure = 0
            company.employees.append(director.id)
        company.executive_id = director.id
        company.ceo_tenure = 0
        holding.director = director.id
        return director

    def _create_public_company(self, sector: str, motive: str) -> bool:
        """Entreprise publique : vraie ``Company`` du secteur, capital apporté par l'État (opération
        financière), dirigeant nommé, recrutement sur le marché du travail comme un entrant."""
        inst, live = self.institutions, self.live
        firms = [c for c in self.companies if c.active and c.sector == sector]
        scale = max(1.0, self.wage_reference / max(live.starting_wage, 1.0))
        revenue = sum(c.previous_revenue for c in firms) / len(firms) if firms else 0.0
        capital = min(self._operation_ceiling(), max(live.startup_threshold * scale, 3.0 * revenue))
        if capital <= 0.0 or self._state_funds(capital) < capital:
            return False
        number = 1 + sum(1 for c in self.companies if c.id.startswith("public_"))
        company = Company(
            id=f"public_{self.month}_{sector}",
            name=f"{SECTOR_LABELS.get(sector, sector)} publique {number}",
            sector=sector,
            cash=0.0,
            wage_offer=_entrant_wage_offer(self.companies, sector, self.wage_reference, live),
            target_employees=workforce_targets(live)[sector],
            price=(self.food_price if sector == "food" else self.goods_price if sector == "goods"
                   else live.starting_goods_price),
            stock_price=max(1.0, capital / 1000.0),
            previous_stock_price=max(1.0, capital / 1000.0),
            shares_outstanding=1000.0,
            treasury_shares=0.0,
        )
        transfer_cash(self.public, company, capital)
        if sector == "bank":
            company.bank_equity = capital
        self.public.account.shares[company.id] = 1000.0
        self.companies.append(company)
        holding = PublicHolding(company.id, "création", self.month, invested=capital)
        inst.holdings[company.id] = holding
        self._apply_mandate(company, holding)
        director = self._appoint_director(company, holding, internal=False)
        self._book_acquisition(capital)
        self._count("creations")
        self._decide(
            "industrie", f"Création d'une entreprise publique : {company.name}",
            f"{motive} ; mandat : {holding.mandate} ; dirigeant : "
            + (display_name(director.id) if director is not None else "aucun candidat"),
            amount=capital, financing="apport en capital (trésorerie ou emprunt), opération financière",
        )
        return True

    def _nationalization_cost(self, company) -> float | None:
        """Coût de la prise de contrôle au cours × (1 + prime) ; None si l'entreprise est en
        difficulté, déjà contrôlée ou trop chère pour une opération."""
        live = self.live
        circulating = company.shares_outstanding - company.treasury_shares
        if circulating <= 0.0 or company.loss_streak >= live.bankruptcy_months or self._state_controls(company):
            return None
        held = self.public.account.shares.get(company.id, 0.0)
        need = live.control_threshold * circulating * 1.000001 - held
        cost = max(0.0, need) * company.stock_price * (1.0 + live.control_premium)
        return cost if cost <= self._operation_ceiling() else None

    def _nationalize(self, company, motive: str) -> bool:
        """Achat de titres existants aux détenteurs (ménages, entreprises), au prorata, au cours
        majoré de la prime de contrôle : l'argent rémunère les vendeurs, pas l'entreprise."""
        inst, live = self.institutions, self.live
        state = self.public.account
        circulating = company.shares_outstanding - company.treasury_shares
        held = state.shares.get(company.id, 0.0)
        need = live.control_threshold * circulating * 1.000001 - held
        holders = [h for h in [*self.people, *self.companies] if h is not company and h.shares.get(company.id, 0.0) > 0.0]
        supply = sum(h.shares[company.id] for h in holders)
        need = min(max(0.0, need), supply)
        price = company.stock_price * (1.0 + live.control_premium)
        cost = need * price
        if need <= 0.0 or self._state_funds(cost) < cost:
            return False
        for holder in holders:
            bought = need * holder.shares[company.id] / supply
            remaining = holder.shares[company.id] - bought
            if remaining > 1e-9:
                holder.shares[company.id] = remaining
            else:
                holder.shares.pop(company.id, None)
            state.shares[company.id] = state.shares.get(company.id, 0.0) + bought
            transfer_cash(self.public, holder, bought * price)
        holding = inst.holdings.get(company.id) or PublicHolding(company.id, "nationalisation", self.month)
        holding.origin = "nationalisation"
        holding.invested += cost
        inst.holdings[company.id] = holding
        self._apply_mandate(company, holding)
        director = self._appoint_director(company, holding, internal=True)
        self._book_acquisition(cost)
        self._count("nationalisations")
        self._decide(
            "industrie", f"Nationalisation : {company.name}",
            f"{motive} ; {len(holders)} vendeurs payés au cours + {live.control_premium:.0%} de prime de "
            f"contrôle ; part de l'État {state.shares[company.id] / circulating:.0%} des titres en circulation"
            + (f" ; dirigeant : {display_name(director.id)}" if director is not None else ""),
            amount=cost, financing="achat de titres existants (trésorerie ou emprunt), opération financière",
            note=self._target_conflicts(company.id),
        )
        return True

    def _recapitalize(self, company, holding: PublicHolding, amount: float, motive: str) -> bool:
        """Titres nouveaux souscrits par l'État : l'argent va à l'entreprise, dilution explicite."""
        if amount <= 0.0 or self._state_funds(amount) < amount:
            return False
        state = self.public.account
        circulating = company.shares_outstanding - company.treasury_shares
        new_shares = amount / max(company.stock_price, 1.0)
        company.shares_outstanding += new_shares
        state.shares[company.id] = state.shares.get(company.id, 0.0) + new_shares
        transfer_cash(self.public, company, amount)
        if company.sector == "bank":
            company.bank_equity += amount
        holding.recapitalizations += 1
        holding.invested += amount
        self._book_acquisition(amount)
        self._count("recapitalisations")
        dilution = circulating / (circulating + new_shares) if circulating + new_shares > 0.0 else 0.0
        self._decide(
            "industrie", f"Recapitalisation : {company.name}",
            f"{motive} ; {new_shares:,.0f} titres nouveaux, les autres actionnaires gardent {dilution:.0%} "
            f"de leur part (dilution)".replace(",", " "),
            amount=amount, financing="titres nouveaux souscrits (trésorerie ou emprunt), opération financière",
            note=self._target_conflicts(company.id),
        )
        return True

    # -- cessions ------------------------------------------------------------------------------
    def _review_holdings(self) -> None:
        """Revue annuelle de chaque participation selon une règle explicite : caractère
        stratégique, concurrence suffisante, prix au regard de la valeur fondamentale (pas de vente
        bradée), rendement des dividendes contre le coût de la dette, besoin de financement."""
        inst, live = self.institutions, self.live
        service = inst.services.get("concurrence")
        if inst.holdings and (service is None or service.staff == 0):
            self._decide("industrie", "Revue des participations reportée", "aucun agent au service de la Concurrence")
            return
        doctrine, state = inst.doctrine, self.public.account
        law = inst.law
        need = (law is not None and law.financing_need > 0.0) or (doctrine.budget_rule == "excedent" and self.public.debt > 0.0)
        debt_cost = 12.0 * (self.policy_rate + live.bond_spread)
        annual_rate = 12.0 * max(0.0, self.policy_rate)
        for company_id, holding in inst.holdings.items():
            company = self._company(company_id)
            quantity = state.shares.get(company_id, 0.0)
            if company is None or quantity <= 0.0:
                continue
            data = inst.diagnostic.get(company.sector, {})
            strategic = holding.controlled and company.sector in live.critical_sectors
            competitors = data.get("firms", 1) - 1
            competition = competitors >= 2 and data.get("hhi", 1.0) < live.concentration_margin_reference
            fundamental = _fundamental_price(company, self.companies, live, annual_rate)
            fair = company.stock_price >= (1.0 - live.privatization_price_discount) * fundamental
            dividend_yield = 12.0 * company.dividend_paid / max(company.stock_price * company.shares_outstanding, 1.0)
            profitable = dividend_yield >= debt_cost
            losing = company.loss_streak >= 12
            if holding.origin == "restructuration":
                sell = competitors >= 2 and fair
                rule = "administration provisoire : cession dès que la concurrence existe et que le prix est juste"
            elif doctrine.privatization_stance == "vendre_si_regle":
                sell = not strategic and competition and fair and (not profitable or need)
                rule = "vendre si non stratégique, concurrence suffisante, prix juste et (peu rentable ou besoin de financement)"
            elif doctrine.privatization_stance == "vendre_si_besoin":
                sell = not strategic and competition and fair and need and not profitable
                rule = "vendre si non stratégique, concurrence suffisante, prix juste, besoin de financement et peu rentable"
            else:
                sell = not strategic and competition and fair and losing
                rule = "garder, sauf participation non stratégique durablement déficitaire en marché concurrentiel"
            facts = (
                f"stratégique {'oui' if strategic else 'non'}, {competitors} concurrent(s), "
                f"cours {company.stock_price:,.0f} / valeur {fundamental:,.0f}, rendement {dividend_yield:.1%} "
                f"contre dette {debt_cost:.1%}, besoin de financement {'oui' if need else 'non'}"
            ).replace(",", " ")
            holding.motive = f"{rule} ; {facts}"
            if sell and holding.decision != "vendre":
                holding.decision, holding.to_sell = "vendre", quantity
                holding.sell_lot = quantity * live.privatization_monthly_share
                self._decide("industrie", f"Cession décidée : {company.name}", holding.motive,
                             amount=quantity * company.stock_price, financing="vente en Bourse à de vrais acheteurs",
                             note=self._target_conflicts(company_id))
            elif not sell:
                verb = "Cession suspendue" if holding.decision == "vendre" else "Participation conservée"
                holding.decision, holding.to_sell = "garder", 0.0
                self._decide("industrie", f"{verb} : {company.name}", holding.motive,
                             amount=quantity * company.stock_price)

    def _privatization_orders(self) -> list:
        """Ordres de vente de l'État pour la séance de Bourse du mois (phase 6), par lots ; une
        vente est suspendue tant que le cours est sous la valeur fondamentale décotée."""
        inst, live = self.institutions, self.live
        state = self.public.account
        annual_rate = 12.0 * max(0.0, self.policy_rate)
        orders = []
        for company_id, holding in inst.holdings.items():
            if holding.decision != "vendre" or holding.to_sell <= 0.0:
                continue
            company = self._company(company_id)
            if company is None:
                continue
            fundamental = _fundamental_price(company, self.companies, live, annual_rate)
            if company.stock_price < (1.0 - live.privatization_price_discount) * fundamental:
                continue
            quantity = min(holding.to_sell, holding.sell_lot, state.shares.get(company_id, 0.0))
            if quantity > 0.0:
                orders.append((state, company_id, quantity))
        return orders

    def _settle_privatizations(self, shares_before: dict, cash_before: float) -> None:
        """Après la séance : titres vendus, produits encaissés, fin du contrôle éventuelle."""
        inst, state = self.institutions, self.public.account
        proceeds = self.public.cash - cash_before
        inst.book.add("cessions_titres", proceeds)
        self.public.equity_proceeds += proceeds
        self.public.total_equity_proceeds += proceeds
        for company_id, before in shares_before.items():
            holding = inst.holdings.get(company_id)
            sold = before - state.shares.get(company_id, 0.0)
            if holding is None or sold <= 0.0:
                continue
            company = next((c for c in self.companies if c.id == company_id), None)
            holding.to_sell = max(0.0, holding.to_sell - sold)
            holding.proceeds += sold * (company.stock_price if company is not None else 0.0)
            self._count("cessions")

    # -- relevé ----------------------------------------------------------------------------------
    def _record_industry(self, record) -> None:
        inst = self.institutions
        record.industry = {
            "holdings": len(inst.holdings),
            "controlled": sum(1 for h in inst.holdings.values() if h.controlled),
            "value": self.public.portfolio_value(self.companies),
            "regulated": sorted(inst.regulated),
            "problems": sorted(s for s, d in inst.diagnostic.items() if d["problem"]),
            "cases_open": sum(1 for c in inst.cases if c.status != "décidé"),
            "operations": dict(inst.industry_month),
        }
