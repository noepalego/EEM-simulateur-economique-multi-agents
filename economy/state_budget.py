"""Budget et dette du nouvel État (``state_model="institutions"``) : loi de finances, exécution,
financement, arriérés et rapprochements.

Aucun ratio de dette n'est visé et aucun excédent n'est redistribué automatiquement. Une loi de
finances annuelle, préparée par le ministre de l'Économie et arbitrée par la présidence, part des
recettes observées avec retard (douze derniers mois, corrigées des taux votés), des engagements
(salaires, allocations, intérêts) et des programmes (transferts, achats, investissement, en part
du PIB des douze derniers mois, votés en montant nominal pour l'année). La doctrine budgétaire
fixe le solde voulu :

- « excedent » (libéral) : équilibre, ou excédent ``surplus_goal`` × PIB tant que la dette est
  positive ; l'écart se comble en réduisant les programmes ; un excédent au-delà baisse les impôts ;
- « stabilisation » (centre) : déficit = dette × croissance nominale observée, ce qui garde le
  ratio hérité, quel qu'il soit ; écarts comblés moitié programmes, moitié impôts ;
- « programme » (social) : le programme est financé tant que le déficit reste sous
  ``deficit_tolerance`` × PIB ; au-delà, hausse d'impôts puis réduction des programmes ; un
  excédent finance davantage de programmes.

Contraintes réelles pour tous : charge d'intérêts au-delà de la tolérance de la doctrine ou dette
à 90 % de la limite légale ``debt_ceiling_gdp`` → plus de déficit voulu. L'emprunt ne couvre qu'un
besoin de financement (paiement que la trésorerie ne couvre pas), dans la limite légale et la
capacité des prêteurs (``_fund_public``). Ce qui ne peut être financé est reporté et visible :
programmes non exécutés, arriérés d'intérêts (jamais effacés), de salaires et d'allocations.

La loi décide sur ses flux (recettes attendues, engagements, programmes). La trésorerie au-delà de
la réserve (``public_reserve_months`` de dépenses) — excédents de l'année, crédits non consommés,
recettes gonflées par l'inflation face à des crédits nominaux — n'est pas gardée oisive : un
libéral endetté rembourse chaque mois ; sinon la loi suivante la rend dans l'année selon la
doctrine (libéral et centre : allègement exceptionnel de tous les impôts votés jusqu'à la loi
suivante ; social : transferts exceptionnels). Sans cette règle, la trésorerie publique atteignait
5 à 37 % du PIB annuel en moyenne (jusqu'à 74 %) et retirait cette monnaie de la circulation.

Chaque mois, ``MonthBook`` rapproche la variation de la trésorerie avec les flux classés et celle
de la dette avec les titres émis, remboursés et apportés lors des résolutions bancaires.
"""

from __future__ import annotations

from economy.government import (
    buy_public_goods,
    pay_public_transfers,
    pay_unemployment_benefits,
    unemployment_benefit_entitlement,
)
from economy.institutions import (
    PROGRAM_LABELS,
    PROGRAMS,
    REVENUE_CATEGORIES,
    TAX_FACTOR_BOUNDS,
    TAX_LEVERS,
    BudgetLaw,
    MonthBook,
    StateRecord,
)
from economy.payments import transfer_cash
from economy.politics import LEVER_LABELS

# composante de recettes -> levier d'impôt dont elle dépend
REVENUE_LEVERS = {"impot_revenu": "income_tax", "tva": "vat", "impot_societes": "corporate_tax"}


def _step(current: float, wanted: float, speed: float) -> float:
    """Rapproche ``current`` de ``wanted`` d'au plus ``speed`` × max(current, wanted) : une baisse
    va au plus de ``speed`` du niveau courant, une hausse au plus de ``speed`` du niveau voulu.
    (Une borne relative au seul niveau courant formait un cliquet : un programme réduit presque à
    rien ne pouvait plus être reconstitué, même par un gouvernement qui le voulait.)"""
    limit = speed * max(current, wanted, 0.0)
    return max(current - limit, min(current + limit, wanted))


class StateBudgetMixin:
    """Loi de finances, exécution mensuelle, financement et rapprochements (nouveau mode)."""

    # -- livre du mois -----------------------------------------------------------------
    def _open_state_book(self) -> None:
        """Début du mois (phase 1) : point de départ des rapprochements et des comptes d'opérations."""
        self.institutions.book = MonthBook(cash_start=self.public.cash, debt_start=self.public.debt)
        self.institutions.industry_month = {}

    def _state_funds(self, amount: float) -> float:
        """Montant disponible pour une dépense : trésorerie, puis emprunt aux banques pour le seul
        besoin de financement, dans la limite légale et la capacité des prêteurs."""
        before = self.public.debt
        available = self._fund_public(max(0.0, amount))
        self.institutions.book.borrowed += self.public.debt - before
        return available

    def _apply_budget_decisions(self) -> None:
        """Impôts et allocation votés, réappliqués chaque mois à ``live`` (recréé depuis ``config``)."""
        inst, live, config = self.institutions, self.live, self.config
        factors = inst.tax_factors
        keep = 1.0 - inst.tax_relief  # allègement exceptionnel de l'année (trésorerie rendue)
        live.income_tax_rate = min(1.0, config.income_tax_rate * factors["income_tax"] * keep)
        live.top_marginal_income_tax_rate = min(1.0, config.top_marginal_income_tax_rate * factors["top_tax"] * keep)
        live.corporate_tax_rate = min(1.0, config.corporate_tax_rate * factors["corporate_tax"] * keep)
        live.vat_rate = min(1.0, config.vat_rate * factors["vat"] * keep)
        live.unemployment_benefit = config.unemployment_benefit * inst.benefit_factor
        # barème indexé : le seuil de la tranche supérieure suit le niveau nominal des salaires
        # (salaire de référence / salaire de départ). Non indexé, il passait sous le salaire
        # médian avec l'inflation et l'impôt devenait proportionnel.
        live.income_tax_threshold = config.income_tax_threshold * max(
            1.0, self.wage_reference / max(config.starting_wage, 1.0)
        )

    # -- loi de finances ---------------------------------------------------------------
    def _budget_law_due(self) -> tuple[str, str] | None:
        inst, live = self.institutions, self.live
        law = inst.law
        if law is None:
            return "provisoire", "première loi du nouvel État (aucune recette observée)"
        if law.kind == "provisoire" and len(inst.history) >= 3:
            return "initiale", "premières recettes observées (trois mois)"
        if inst.terms and inst.terms[-1].since == self.month and law.month != self.month:
            return "rectificative", "programme du gouvernement issu de l'élection"
        if self.month - law.month >= 12:
            return "initiale", "loi de finances annuelle"
        if law.kind == "provisoire" or self.month - law.month < 3 or self.month - inst.last_revision < 6:
            return None
        recent = self.history[-3:]
        if recent:
            unemployment = sum(r.unemployment for r in recent) / len(recent)
            if unemployment > law.unemployment + live.budget_revision_unemployment:
                return "rectificative", f"récession : chômage {unemployment:.1%} contre {law.unemployment:.1%} prévu"
        observed = inst.history[-3:]
        if len(observed) == 3 and law.expected_revenue > 0.0:
            revenue = 4.0 * sum(sum(r.flows.get(c, 0.0) for c in REVENUE_CATEGORIES) for r in observed)
            if revenue < (1.0 - live.budget_revision_revenue) * law.expected_revenue:
                return "rectificative", f"recettes en baisse : {revenue:,.0f} contre {law.expected_revenue:,.0f} prévus".replace(",", " ")
        return None

    def _nominal_growth(self) -> float:
        """Croissance du PIB nominal des douze derniers mois sur les douze précédents (bornée à
        [0 ; 10 %], nulle sans deux ans d'observations)."""
        if len(self.history) < 24:
            return 0.0
        recent = sum(r.nominal_gdp for r in self.history[-12:])
        before = sum(r.nominal_gdp for r in self.history[-24:-12])
        return max(0.0, min(0.10, recent / before - 1.0)) if before > 0.0 else 0.0

    def _observed_revenue(self) -> dict[str, float] | None:
        """Recettes annuelles par composante, observées sur les douze derniers mois du livre
        (au moins trois, annualisés) et corrigées des facteurs d'impôt votés depuis (allègements
        exceptionnels passés retirés : la recette attendue est celle des taux durables) ; None sinon."""
        inst = self.institutions
        records = inst.history[-12:]
        if len(records) < 3:
            return None
        scale = 12.0 / len(records)
        revenue = {}
        for category in REVENUE_CATEGORIES:
            observed = scale * sum(r.flows.get(category, 0.0) for r in records)
            lever = REVENUE_LEVERS.get(category)
            if lever is not None:
                then = sum(r.tax_factors.get(lever, 1.0) * (1.0 - r.tax_relief) for r in records) / len(records)
                observed *= inst.tax_factors[lever] / max(then, 1e-9)
            revenue[category] = observed
        return revenue

    def _vote_budget_law(self, kind: str, motive: str) -> None:
        """Prépare (Économie et Finances), arbitre (présidence) et vote une loi de finances."""
        inst, live, public = self.institutions, self.live, self.public
        doctrine = inst.doctrine
        recent = self.history[-12:]
        gdp = 12.0 * sum(r.nominal_gdp for r in recent) / len(recent) if recent else self._annual_gdp()
        unemployment = (
            sum(r.unemployment for r in recent[-3:]) / len(recent[-3:]) if recent
            else live.natural_unemployment_rate
        )
        adjustments = []
        # 1. impôts et allocation : rapprochés de ceux du programme du gouvernement
        for lever in TAX_LEVERS:
            current = inst.tax_factors.get(lever, doctrine.tax_factors[lever])
            inst.tax_factors[lever] = _step(current, doctrine.tax_factors[lever], live.budget_tax_speed)
        inst.benefit_factor = _step(inst.benefit_factor, doctrine.benefit_factor, live.budget_tax_speed)
        desired = {"transferts": doctrine.transfer_share, "achats": doctrine.purchases_share,
                   "investissement": doctrine.investment_share}
        inst.tax_relief = 0.0
        returned = 0.0
        revenue = self._observed_revenue()
        if revenue is None:
            # aucune recette observée : loi provisoire, programmes de la doctrine, à l'équilibre
            for key, wanted in desired.items():
                inst.programs[key] = _step(inst.programs.get(key, wanted), wanted, live.budget_program_speed)
            lines = self._law_lines(gdp)
            expected = sum(lines.values())
            goal = 0.0
        else:
            # 2. marge des programmes : recettes attendues moins les engagements et le solde voulu
            # par la doctrine (la trésorerie accumulée ne fonde aucune décision durable)
            lines = self._law_lines(gdp)
            expected = sum(revenue.values())
            goal = self._balance_goal(gdp, expected, lines["interets"])
            commitments = sum(amount for key, amount in lines.items() if key not in PROGRAMS)
            room = expected - commitments - goal
            adjustments = self._allocate_programs(room, gdp, desired, revenue)
            lines = self._law_lines(gdp)
            revenue = self._observed_revenue()
            expected = sum(revenue.values())
            # 3. trésorerie au-delà de la réserve : rendue dans l'année selon la doctrine
            reserve = live.public_reserve_months * sum(lines.values()) / 12.0
            excess = max(0.0, public.cash - public.pending_transfers - reserve)
            relief, extra = self._return_excess_treasury(excess, revenue)
            lines["transferts"] += extra
            expected -= relief
            returned = relief + extra
        law = BudgetLaw(self.month, kind, motive, expected, lines, expected - sum(lines.values()),
                        goal, gdp, unemployment, adjustments, returned, inst.tax_relief)
        inst.law = law
        inst.laws.append(law)
        inst.executed = {}
        if kind == "rectificative":
            inst.last_revision = self.month
        self._decide(
            "finances", f"Loi de finances {kind}",
            f"{motive} ; recettes attendues {expected:,.0f}, dépenses {law.spending:,.0f}, "
            f"solde prévu {law.planned_balance:+,.0f} (solde voulu {goal:+,.0f})".replace(",", " "),
            amount=law.planned_balance,
            financing=(f"besoin de financement {law.financing_need:,.0f} couvert par l'emprunt".replace(",", " ")
                       if law.financing_need > 0.0 else "aucun besoin de financement"),
        )
        if adjustments:
            self._decide("presidence", "Arbitrage de la loi de finances", " ; ".join(adjustments),
                         amount=law.planned_balance)

    def _return_excess_treasury(self, excess: float, revenue: dict[str, float]) -> tuple[float, float]:
        """Usage, décidé par la loi, de la trésorerie au-delà de la réserve : rendue dans l'année,
        jamais gardée oisive. Un libéral endetté rembourse déjà chaque mois (``repay_share``) : rien
        à décider ici. Libéral et centre : allègement exceptionnel de tous les impôts votés,
        ``tax_relief`` = trésorerie / recettes fiscales attendues (au plus 100 %), jusqu'à la loi
        suivante ; social : transferts universels exceptionnels sur l'année. Retourne (recettes
        rendues, transferts exceptionnels), en montants annuels."""
        inst, public = self.institutions, self.public
        doctrine = inst.doctrine
        if excess <= 0.0 or (doctrine.repay_share > 0.0 and public.debt > 0.0):
            return 0.0, 0.0
        if doctrine.budget_rule == "programme":
            self._decide(
                "presidence", "Trésorerie excédentaire : transferts exceptionnels",
                "trésorerie au-delà de la réserve, versée en transferts universels sur l'année",
                amount=excess, financing="trésorerie publique",
            )
            return 0.0, excess
        base = sum(revenue.get(category, 0.0) for category in REVENUE_LEVERS)
        if base <= 0.0:
            return 0.0, 0.0
        inst.tax_relief = min(1.0, excess / base)
        relief = inst.tax_relief * base
        self._decide(
            "presidence", "Trésorerie excédentaire : allègement exceptionnel des impôts",
            f"trésorerie au-delà de la réserve rendue aux contribuables : taux votés × "
            f"{1.0 - inst.tax_relief:.3f} jusqu'à la loi suivante",
            amount=relief, financing="trésorerie publique",
        )
        return relief, 0.0

    def _law_lines(self, gdp: float) -> dict[str, float]:
        """Lignes annuelles de la loi : engagements (au niveau courant) et programmes votés."""
        inst, live, public = self.institutions, self.live, self.public
        records = inst.history[-12:]
        if records:
            benefits = 12.0 * sum(-r.flows.get("allocations", 0.0) for r in records) / len(records)
            then = sum(r.benefit_factor for r in records) / len(records)
            benefits *= inst.benefit_factor / max(then, 1e-9)
        else:
            benefits = 0.0
        lines = {
            "personnel": 12.0 * public.wage_bill,
            "allocations": benefits,
            "interets": 12.0 * public.debt * (self.policy_rate + live.bond_spread) + inst.arrears_interest,
            "entretien": 12.0 * public.public_capital * live.public_capital_maintenance_rate,
            "formation": 12.0 * inst.training_places * live.retraining_cost_months * self.wage_reference,
        }
        for key in PROGRAMS:
            lines[key] = inst.programs[key] * gdp
        return lines

    def _executable_programs(self, wanted: dict[str, float]) -> dict[str, float]:
        """Montant annuel que les achats et l'investissement ont pu réellement exécuter (douze
        derniers mois, au moins trois), majoré de 25 %, sans descendre sous le quart du niveau du
        programme (``wanted``) : la demande publique peut se reconstituer quand l'offre revient.
        Les transferts n'ont pas de limite d'exécution."""
        records = self.institutions.history[-12:]
        if len(records) < 3:
            return {}
        scale = 12.0 / len(records)
        return {
            key: max(1.25 * scale * sum(-r.flows.get(key, 0.0) for r in records), 0.25 * wanted[key])
            for key in ("achats", "investissement")
        }

    def _balance_goal(self, gdp: float, revenue: float, interest: float) -> float:
        """Solde voulu par la doctrine budgétaire, sous les contraintes de la dette."""
        inst, live = self.institutions, self.live
        doctrine, debt = inst.doctrine, self.public.debt
        if doctrine.budget_rule == "excedent":
            goal = doctrine.surplus_goal * gdp if debt > 0.0 else 0.0
        elif doctrine.budget_rule == "stabilisation":
            goal = -debt * self._nominal_growth()
        else:
            goal = -doctrine.deficit_tolerance * gdp
        if revenue > 0.0 and interest / revenue > doctrine.interest_tolerance:
            goal = max(goal, 0.0)
        if debt >= 0.9 * live.debt_ceiling_gdp * gdp:
            goal = max(goal, 0.0)
        return goal

    def _allocate_programs(self, room: float, gdp: float, desired: dict[str, float],
                           revenue: dict[str, float]) -> list[str]:
        """Arbitrage de la présidence : la marge ``room`` finance les programmes, répartis comme
        le programme du gouvernement (``desired``, parts du PIB), à vitesse bornée.

        - « excedent » (libéral) : programmes au plus aux niveaux voulus ; la marge au-delà baisse
          les impôts (TVA comprise) ;
        - « programme » (social) : si la marge ne finance pas les niveaux voulus, hausse des impôts
          progressifs d'abord ; toute la marge (déficit toléré compris) va aux programmes ;
        - « stabilisation » (centre) : la marge va aux programmes ; si les programmes ne peuvent
          baisser assez vite, hausse d'impôts (revenu, bénéfices, TVA).
        Chaque facteur d'impôt change au plus une fois par loi. Retourne les décisions (texte).
        """
        inst, live = self.institutions, self.live
        rule = inst.doctrine.budget_rule
        speed_p, speed_t = live.budget_program_speed, live.budget_tax_speed
        decisions = []
        moved: set[str] = set()

        def tax_change(levers, amount: float, sign: float) -> float:
            """Change les facteurs ``levers`` (au plus ``speed_t``) ; retourne la recette visée."""
            levers = [lever for lever in levers if lever not in moved]
            base = sum(revenue.get(c, 0.0) for c, lever in REVENUE_LEVERS.items() if lever in levers)
            if base <= 0.0 or amount <= 0.0:
                return 0.0
            ratio = min(speed_t, amount / base)
            low, high = TAX_FACTOR_BOUNDS
            for lever in levers:
                before = inst.tax_factors[lever]
                inst.tax_factors[lever] = max(low, min(high, before * (1.0 + sign * ratio)))
                moved.add(lever)
                decisions.append(f"{LEVER_LABELS[lever]} × {inst.tax_factors[lever] / before:.3f}")
            return ratio * base

        wanted = {key: share * gdp for key, share in desired.items()}
        total = sum(wanted.values())
        weights = {key: (wanted[key] / total if total > 0.0 else 1.0 / len(wanted)) for key in wanted}
        if rule == "excedent" and room > total:
            tax_change(("income_tax", "corporate_tax", "top_tax", "vat"), room - total, -1.0)
            room = total
        elif rule == "programme" and room < total:
            room += tax_change(("income_tax", "top_tax", "corporate_tax"), total - room, +1.0)
        target = max(0.0, room)
        # capacité d'exécution observée : un ministère ne demande pas plus de 1,25 fois ce qu'il a
        # réellement dépensé sur douze mois (achats limités aux stocks invendus, travaux aux
        # fournisseurs présents) ; la part au-delà revient aux autres programmes
        wanted_amount = {key: target * weights[key] for key in PROGRAMS}
        executable = self._executable_programs(wanted)
        spare = 0.0
        for key, limit in executable.items():
            if wanted_amount[key] > limit:
                spare += wanted_amount[key] - limit
                wanted_amount[key] = limit
        if spare > 0.0:
            open_keys = [key for key in PROGRAMS if key not in executable or wanted_amount[key] < executable[key]]
            share = sum(weights[key] for key in open_keys)
            for key in open_keys:
                wanted_amount[key] += spare * weights[key] / share if share > 0.0 else 0.0
            decisions.append(f"crédits redéployés faute de capacité d'exécution : {spare:,.0f}".replace(",", " "))
        for key in PROGRAMS:
            before = inst.programs[key]
            inst.programs[key] = _step(before, wanted_amount[key] / max(gdp, 1.0), speed_p)
            if abs(inst.programs[key] - before) > 1e-9:
                decisions.append(
                    f"{PROGRAM_LABELS[key]} {(inst.programs[key] - before) * gdp:+,.0f}".replace(",", " ")
                )
        # programmes bornés : l'écart restant passe par l'impôt (le libéral ne relève pas l'impôt ;
        # le centre baisse l'impôt d'une marge que les programmes n'absorbent pas cette année)
        excess = sum(inst.programs[key] for key in PROGRAMS) * gdp - target
        if excess > 0.0 and rule != "excedent":
            levers = ("income_tax", "top_tax", "corporate_tax") if rule == "programme" else (
                "income_tax", "corporate_tax", "vat")
            excess -= tax_change(levers, excess, +1.0)
        elif excess < 0.0 and rule == "stabilisation":
            excess += tax_change(("income_tax", "corporate_tax", "vat"), -excess, -1.0)
        if abs(excess) > 1.0:
            decisions.append(f"écart au solde voulu non comblé cette année : {excess:+,.0f}".replace(",", " "))
        return decisions

    # -- phase 2 : impôts, prestations, transferts -------------------------------------------
    def _pay_arrears(self, arrears: dict, label: str, share: float = 1.0) -> float:
        """Règle la part ``share`` des arriérés dus à des ménages (capacité du service des
        Finances), au prorata si les fonds manquent."""
        due = sum(arrears.values()) * max(0.0, min(1.0, share))
        if due <= 0.0:
            return 0.0
        available = self._state_funds(due)
        ratio = max(0.0, min(1.0, available / due)) * max(0.0, min(1.0, share))
        people = {p.id: p for p in self.people}
        paid = 0.0
        for person_id in list(arrears):
            amount = arrears[person_id] * ratio
            person = people.get(person_id)
            if person is None or amount <= 0.0:
                continue
            transfer_cash(self.public, person, amount)
            person.income_this_turn += amount
            person.lifetime_earnings += amount
            paid += amount
            arrears[person_id] -= amount
            if arrears[person_id] <= 1e-9:
                del arrears[person_id]
        self.institutions.book.add("arrieres_payes", -paid)
        if paid > 0.0:
            self._decide("finances", f"Règlement d'arriérés : {label}", "trésorerie disponible",
                         amount=paid, financing="trésorerie ou emprunt")
        return paid

    def _collect_assessed_taxes(self, coverage: float) -> float:
        """Impôt sur le revenu du mois : le service des Finances recouvre la part ``coverage`` de
        l'impôt établi et du reste dû des mois précédents ; le solde reste dû par le ménage
        (recouvrement retardé, jamais effacé).

        Barème progressif : taux de base jusqu'au seuil (indexé, ``_apply_budget_decisions``), taux
        de base + surtaxe (``top_marginal_income_tax_rate``) au-delà. Auparavant le taux supérieur
        était ``max(taux de base, taux supérieur)`` : 8 % contre 10 %, il ne s'appliquait jamais.
        Assiette : revenu du mois (salaires, arriérés versés) + revenus du capital du mois précédent
        (dividendes, loyers), qui échappaient à l'impôt (versés après le prélèvement). Un
        gouvernement de droite les impose à part, au forfait (``capital_flat_factor``)."""
        live, backlog = self.live, self.institutions.tax_backlog
        rate, top = live.income_tax_rate, live.top_marginal_income_tax_rate
        threshold = max(0.0, live.income_tax_threshold)
        flat = self.institutions.doctrine.capital_flat_factor
        collected = 0.0
        for person in self.people:
            labour = max(0.0, person.income_this_turn)
            capital = max(0.0, person.capital_income_last_month)
            taxable = labour if flat is not None else labour + capital
            assessed = min(taxable, threshold) * rate + max(0.0, taxable - threshold) * (rate + top)
            if flat is not None:
                assessed += capital * rate * flat
            due = assessed + backlog.pop(person.id, 0.0)
            paid = due * coverage
            if due - paid > 1e-9:
                backlog[person.id] = due - paid
            person.taxes_paid = paid
            person.cash -= paid
            person.expenses_this_turn += paid
            person.cumulative_expenses += paid
            collected += paid
        return collected

    def _collect_institutional_taxes(self, f) -> None:
        """Impôt sur le revenu aux taux votés (aucune règle d'ajustement), allocations selon la loi,
        transferts décidés le mois précédent ; arriérés d'allocations réglés d'abord. En
        sous-effectif, le service des Finances ne traite qu'une part des dossiers : impôts recouvrés
        et allocations versées plus tard, visibles (reste dû, arriérés)."""
        inst, live, public = self.institutions, self.live, self.public
        book = inst.book
        coverage = self._finance_coverage()
        self._pay_arrears(inst.arrears_benefits, "allocations", coverage)
        f.household_taxes = self._collect_assessed_taxes(coverage)
        public.cash += f.household_taxes
        book.add("impot_revenu", f.household_taxes)
        per_person = unemployment_benefit_entitlement(
            live.unemployment_benefit, self.price_index, f.unemployment,
            live.unemployment_benefit_cycle_sensitivity,
        )
        unemployed = [p for p in self.people if p.employer_id is None]
        due = per_person * len(unemployed)
        funded = self._state_funds(due * coverage)
        f.benefits_paid = pay_unemployment_benefits(self.people, per_person * coverage, funded)
        public.cash -= f.benefits_paid
        book.add("allocations", -f.benefits_paid)
        if unemployed and f.benefits_paid < due - 1e-9:
            missing = (due - f.benefits_paid) / len(unemployed)
            for person in unemployed:
                inst.arrears_benefits[person.id] = inst.arrears_benefits.get(person.id, 0.0) + missing
            if f.benefits_paid < due * coverage - 1e-9:
                self._decide("travail", "Allocations versées partiellement",
                             "financement impossible (limite légale ou capacité des prêteurs)",
                             amount=due * coverage - f.benefits_paid, financing="arriérés dus aux chômeurs")
        planned = public.pending_transfers
        paid = pay_public_transfers(self.people, min(planned, max(0.0, public.cash)),
                                    targeting=inst.doctrine.transfer_targeting)
        public.cash -= paid
        book.add("transferts", -paid)
        book.note(book.unfunded, "transferts", planned - paid)
        public.pending_transfers = 0.0
        f.public_transfers = paid

    # -- phase 4 : exécution de la loi --------------------------------------------------------
    def _institutional_budget(self, f) -> None:
        """Exécution mensuelle : intérêts (arriérés d'abord), programmes par douzièmes, opérations
        financières, puis usage de la trésorerie au-delà de la réserve selon la doctrine."""
        inst, live, public = self.institutions, self.live, self.public
        book = inst.book
        self._sync_public_debt()
        due = self._budget_law_due()
        if due is not None:
            self._vote_budget_law(*due)
        law = inst.law
        # 1. intérêts : jamais effacés, les arriérés passent avant
        interest = public.debt * (self.policy_rate + live.bond_spread) + inst.arrears_interest
        available = self._state_funds(interest)
        paid = max(0.0, min(interest, available))
        public.cash -= paid
        before = public.cash
        self.pay_bond_interest(paid)
        withheld = public.cash - before
        book.add("interets", -paid)
        book.add("retenue_interets", withheld)
        if interest - paid > 1e-9:
            self._decide("finances", "Intérêts payés partiellement",
                         "financement impossible (limite légale ou capacité des prêteurs)",
                         amount=interest - paid, financing="arriérés d'intérêts, payés en priorité")
        inst.arrears_interest = max(0.0, interest - paid)
        public.interest_paid = paid
        # 2. programmes du mois : un douzième de la loi (× ``government_spending_rate``, échelle des
        # dépenses discrétionnaires de la configuration, que réduit le choc « dépenses »)
        monthly = {key: law.lines[key] / 12.0 * live.government_spending_rate for key in PROGRAMS}
        budget = min(monthly["achats"], self._state_funds(monthly["achats"]))
        book.note(book.unfunded, "achats", monthly["achats"] - budget)
        spent, f.public_food_units, f.public_goods_units = buy_public_goods(self.companies, budget)
        public.cash -= spent
        book.add("achats", -spent)
        book.note(book.lapsed, "achats", budget - spent)  # stocks insuffisants : crédits non consommés
        f.public_spent = spent
        wanted = monthly["investissement"]
        budget = min(wanted, self._state_funds(wanted))
        book.note(book.unfunded, "investissement", wanted - budget)
        # entretien puis investissement, dans la capacité du service de l'Équipement
        self._execute_public_works(f, budget)
        f.keynesian_investment = 0.0
        # transferts décidés maintenant, versés au début du mois suivant
        wanted = monthly["transferts"]
        planned = min(wanted, max(0.0, self._state_funds(wanted)))
        book.note(book.unfunded, "transferts", wanted - planned)
        # 3. opérations financières : décidées par la politique industrielle (phases 6 et 7), plus
        # d'achats de soutien ni de cessions automatiques (``_state_equity_operations``)
        public.equity_invested = public.equity_proceeds = 0.0
        # 4. trésorerie au-delà de la réserve : remboursement selon la doctrine
        reserve = live.public_reserve_months * law.spending / 12.0
        excess = public.cash - planned - reserve
        share = inst.doctrine.repay_share
        if excess > 0.0 and share > 0.0 and public.debt > 0.0:
            repaid = self.repay_government_bonds(min(excess * share, public.debt))
            public.cash -= repaid
            public.debt -= repaid
            book.repaid += repaid
        public.pending_transfers = planned
        self._sync_public_debt()
        for key, amount in (("interets", paid), ("achats", spent), ("investissement", f.public_investment),
                            ("transferts", planned)):
            inst.executed[key] = inst.executed.get(key, 0.0) + amount
        public.purchases = spent
        # formations financées (phase 2) : dépense publique, hors PIB comme lorsque le chômeur paie
        public.spending = (
            f.public_wages + f.benefits_paid + paid + spent + f.public_investment + f.public_transfers
            - book.flows.get("formation", 0.0)
        )
        public.revenue = f.household_taxes + f.vat_revenue + withheld

    # -- phase 8 : rapprochements ---------------------------------------------------------------
    def _close_state_book(self, f, record: StateRecord) -> None:
        """Rapproche la trésorerie avec les flux et la dette avec les titres ; remplit le relevé."""
        inst, public = self.institutions, self.public
        book = inst.book
        book.add("tva", f.vat_revenue)
        book.add("impot_societes", f.corporate_taxes)
        book.add("dividendes", public.equity_dividends)
        net = sum(book.flows.values()) + book.borrowed - book.repaid
        record.cash_residual = public.cash - book.cash_start - net
        resolution = sum(e.public_cost for e in self.resolution_events if e.month == self.month)
        record.debt_residual = public.debt - book.debt_start - (book.borrowed - book.repaid + resolution)
        record.flows = dict(book.flows)
        record.borrowed, record.repaid = book.borrowed, book.repaid
        record.debt = public.debt
        record.cash = public.cash
        annual = self._annual_gdp()
        record.gdp = annual
        record.debt_gdp = public.debt / max(annual, 1.0)
        record.unfunded = sum(book.unfunded.values())
        record.lapsed = sum(book.lapsed.values())
        record.unfunded_lines = dict(book.unfunded)
        record.lapsed_lines = dict(book.lapsed)
        record.arrears = inst.arrears
        record.tax_factors = dict(inst.tax_factors)
        record.tax_relief = inst.tax_relief
        record.programs = dict(inst.programs)
        record.benefit_factor = inst.benefit_factor
