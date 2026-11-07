"""Salaire minimum institutionnel du nouvel État (``state_model="institutions"``).

Le salaire minimum existe sous tous les gouvernements. Il est institué à la création du nouvel
État par le ministre du Travail (ambition de la doctrine × salaire médian observé), puis revalorisé
une fois par an, à date fixe :

1. indexation légale sur l'inflation des douze derniers mois observés (déjà retardée d'un mois) ;
   un gouvernement libéral peut déroger (gel ou indexation partielle), décision consignée ;
2. coup de pouce du ministre vers l'ambition de sa doctrine (part du salaire médian du privé,
   moyenne des douze derniers mois), borné à ``minimum_wage_boost_cap`` par an (montée
   progressive) ; un gouvernement social dont le minimum dépasse déjà l'ambition gèle
   l'indexation (décision consignée, aucune baisse) ;
3. aucune baisse nominale par la règle : l'inflation négative n'est pas répercutée. Seul un
   gouvernement non social (doctrine libérale ou centriste, jusqu'à 65/100) peut décider une
   baisse, consignée, quand le minimum dépasse son ambition de plus de ``minimum_wage_cut_margin``
   du médian (après une déflation, le minimum nominal finit sinon au niveau du médian) ; baisse
   d'au plus ``minimum_wage_boost_cap`` par an, jusqu'à cette limite. Un gouvernement social ne le
   baisse jamais.

Le niveau légal est réappliqué à ``live.minimum_wage`` chaque mois : il plancher les salaires
payés dans le privé (``labor._pay``) et la grille publique (``_grid_salary``). L'offre salariale
des entreprises n'est pas relevée au minimum (contrairement à l'ancien levier) : seuls les
salaires sous le plancher montent. Les budgets d'embauche comptent la rémunération complète
attendue (``allocate_and_pay_labor``, ``full_pay_hiring``). Les révisions lisent des observations
retardées : pas de boucle instantanée salaire-prix.
"""

from __future__ import annotations

import statistics


class StateLaborMixin:
    """Salaire minimum légal : institution, revalorisation, mesure (nouveau mode)."""

    def _median_wage_proxy(self) -> float:
        """Salaire médian du privé payé le mois écoulé ; à défaut (premier mois), médian des
        offres salariales des entreprises actives."""
        median = self._median_private_wage()
        if median > 0.0:
            return median
        offers = [c.wage_offer for c in self.companies if c.active and c.wage_offer > 0.0]
        return statistics.median(offers) if offers else self.wage_reference

    def _lagged_median_wage(self) -> float:
        """Salaire médian du privé observé : moyenne des douze derniers relevés mensuels."""
        medians = [r.median_wage for r in self.institutions.history[-12:] if r.median_wage > 0.0]
        return sum(medians) / len(medians) if medians else self._median_wage_proxy()

    def _institute_minimum_wage(self) -> None:
        """Création de l'institution, avec le nouvel État : niveau fixé par le ministre du Travail."""
        inst = self.institutions
        median = self._median_wage_proxy()
        inst.minimum_wage = inst.doctrine.minimum_wage_ambition * median
        inst.minimum_wage_revisions.append({
            "month": self.month, "level": inst.minimum_wage, "indexation": 0.0, "boost": 0.0,
            "median": median, "kind": "institution",
        })
        self._decide(
            "travail", "Institution du salaire minimum légal",
            f"{inst.doctrine.minimum_wage_ambition:.0%} du salaire médian observé ({median:,.0f})".replace(",", " "),
            amount=inst.minimum_wage, financing="payé par les employeurs (privé et grille publique)",
        )

    def _revise_minimum_wage(self) -> None:
        """Revalorisation annuelle : indexation légale (ou dérogation libérale consignée), puis
        coup de pouce borné vers l'ambition de la doctrine ; baisse nominale seulement par décision
        d'un gouvernement non social, pour un minimum très au-dessus de son ambition."""
        inst, live = self.institutions, self.live
        if len(self.history) < 13:
            return
        doctrine = inst.doctrine
        inflation = self.history[-1].consumer_price_index / max(self.history[-13].consumer_price_index, 1e-9) - 1.0
        legal = max(0.0, inflation)
        applied = doctrine.minimum_wage_indexation * legal
        before = inst.minimum_wage
        median = self._lagged_median_wage()
        target = doctrine.minimum_wage_ambition * median
        frozen = False
        if doctrine.orientation > 65.0 and before > target > 0.0 and applied > 0.0:
            # gel décidé par un gouvernement social : le minimum dépasse déjà son ambition ; il
            # n'est pas baissé, mais pas indexé non plus (sans cette décision, l'indexation sur les
            # prix et l'absence de baisse le portaient au niveau du médian : 94 % en moyenne sur les
            # 60 derniers mois à l'orientation 90, 39 % des salariés du privé au plancher)
            frozen = True
            applied = 0.0
        level = before * (1.0 + applied)
        boost = 0.0
        if doctrine.orientation >= 50.0 and level < target and level > 0.0:
            # montée progressive : au plus ``minimum_wage_boost_cap`` par an, sans diluer l'écart
            # sur les années de mandat restantes (dilué, un gouvernement social ne rattrapait pas
            # en un mandat le minimum érodé par ses prédécesseurs)
            boost = min(live.minimum_wage_boost_cap, target / level - 1.0)
            level *= 1.0 + boost
        inst.minimum_wage = max(before, level)
        # baisse décidée : un gouvernement non social (libéral ou centre, mêmes seuils que les
        # doctrines) ne garde pas un minimum très au-dessus de son ambition (déflation, salaires en
        # baisse) ; jamais sous ambition + marge
        limit = (doctrine.minimum_wage_ambition + live.minimum_wage_cut_margin) * median
        cut = 0.0
        if doctrine.orientation <= 65.0 and median > 0.0 and inst.minimum_wage > limit:
            cut = min(live.minimum_wage_boost_cap, 1.0 - limit / inst.minimum_wage)
            ratio = inst.minimum_wage / median
            inst.minimum_wage *= 1.0 - cut
            self._decide(
                "travail", "Salaire minimum : baisse décidée",
                f"le minimum atteint {ratio:.0%} du médian ({median:,.0f}), au-delà de l'ambition "
                f"({doctrine.minimum_wage_ambition:.0%}) + {live.minimum_wage_cut_margin:.0%} ; "
                f"baisse de {cut:.1%}".replace(",", " "),
                amount=inst.minimum_wage - before,
                financing="employeurs (coût salarial moindre)",
            )
        inst.minimum_wage_revisions.append({
            "month": self.month, "level": inst.minimum_wage, "indexation": applied, "boost": boost,
            "cut": cut, "median": median, "inflation": inflation, "kind": "revalorisation",
        })
        if frozen:
            self._decide(
                "travail", "Salaire minimum : gel décidé",
                f"le minimum ({before / median:.0%} du médian) dépasse l'ambition du gouvernement "
                f"({doctrine.minimum_wage_ambition:.0%}) ; pas d'indexation ({legal:+.1%}), pas de baisse",
                amount=0.0, financing="employeurs (coût salarial inchangé)",
            )
        elif applied < legal - 1e-12:
            self._decide(
                "travail", "Salaire minimum : dérogation à l'indexation légale",
                f"{'gel' if applied <= 0.0 else 'indexation partielle'} ({applied:+.1%} au lieu de {legal:+.1%}), "
                "doctrine libérale ; le pouvoir d'achat du minimum s'érode",
                amount=(applied - legal) * before,
                financing="employeurs (coût salarial moindre)",
            )
        motive = f"inflation observée {inflation:+.1%}, indexation {applied:+.1%}"
        if boost > 0.0:
            motive += (f", coup de pouce {boost:+.1%} vers {doctrine.minimum_wage_ambition:.0%} du médian"
                       f" ({median:,.0f})").replace(",", " ")
        if median > 0.0:
            motive += f" ; niveau {inst.minimum_wage / median:.0%} du médian"
        self._decide(
            "travail", "Revalorisation annuelle du salaire minimum", motive,
            amount=inst.minimum_wage - before,
            financing="payé par les employeurs (privé et grille publique)",
        )

    def _record_minimum_wage(self, record) -> None:
        """Relevé mensuel : niveau, ratio au médian, salariés du privé au plancher et sous le
        plancher (paiement incomplet faute de trésorerie), agents publics au plancher."""
        inst = self.institutions
        floor = inst.minimum_wage
        private = [
            p.current_wage for p in self.people
            if p.employer_id not in (None, "state") and p.current_wage > 0.0
        ]
        public = [p.current_wage for p in self.people if p.id in inst.agents and p.current_wage > 0.0]
        median = statistics.median(private) if private else 0.0
        record.minimum_wage = {
            "level": floor,
            "ratio_median": floor / median if median > 0.0 else 0.0,
            "at_floor": sum(w <= floor * 1.001 for w in private) / len(private) if private and floor > 0.0 else 0.0,
            "below_floor": sum(w < floor * 0.999 for w in private) / len(private) if private and floor > 0.0 else 0.0,
            "public_at_floor": sum(w <= floor * 1.001 for w in public) / len(public) if public and floor > 0.0 else 0.0,
        }
