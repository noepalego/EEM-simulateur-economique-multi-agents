"""Lot R (novembre 2026) : agents apprenants.

Un réglage, ``adaptive_agents``, désactivé par défaut (aucun calcul, aucun tirage, aucun attribut
ajouté sans lui). L'état vit dans ``simulation.learning`` (``LearningState``), créé au premier
usage ; les tirages viennent d'un générateur dédié.

Sans ce réglage, les traits de comportement sont tirés une fois et ne bougent plus. Avec lui, les
agents les ajustent d'après ce qu'ils vivent, sans rien connaître du modèle :

- **Entreprises, essais et erreurs** : tous les ``learning_period_months`` mois, une entreprise
  compare sa marge moyenne de la période à celle de la période précédente. Si elle a monté, elle
  refait le même pas sur le trait qu'elle venait de bouger ; sinon elle l'inverse. Les deux
  traits, à tour de rôle : agressivité de prix (``pricing_aggression``) et part des bénéfices
  réinvestie (``reinvestment_preference``).
- **Entreprises, imitation** : une entreprise moins rentable que la meilleure de son secteur
  comble ``imitation_rate`` de l'écart de traits avec elle.
- **Style des dirigeants** : un dirigeant retient les traits de l'entreprise qu'il dirige ; nommé
  ailleurs, il y apporte la moitié de ce style.
- **Ménages, précaution** : la propension à consommer d'un ménage baisse avec la fréquence de
  ses propres mois sans emploi, et remonte quand elle s'éloigne.
- **Ménages, goût du risque** : il monte après une période où les titres que le ménage détient
  ont pris de la valeur, baisse après une perte ; un ménage se rapproche aussi du goût du risque
  du collègue le plus riche de son entreprise.
- **Banques** : essais d'un coussin prudentiel supplémentaire d'après le rendement observé
  des fonds propres, sans relâcher les exigences réglementaires.
- **Ministres** : avec les objectifs enrichis, essais d'effort d'après la progression de
  l'objectif principal ; une nouvelle personne, génération ou cible ouvre une nouvelle mémoire.

Aucun de ces apprentissages ne vise un résultat : chaque agent suit son propre signal, et
l'ensemble peut aussi bien converger que s'emballer.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from economy.wealth import share_prices

LEARNING_SEED_OFFSET = 141650939  # générateur dédié (premier sens d'essai, taille des pas)
TRAITS = ("pricing_aggression", "reinvestment_preference")
TRAIT_BOUNDS = {"pricing_aggression": (0.0, 1.0), "reinvestment_preference": (0.05, 1.0)}
STYLE_TRANSFER = 0.5            # part du style d'un nouveau dirigeant appliquée à l'entreprise
PRECAUTION = 0.30               # baisse maximale de la propension à consommer d'un ménage exposé au chômage
PRECAUTION_FULL_RISK = 0.5      # fréquence de mois sans emploi qui donne la précaution maximale
RISK_BOUNDS = (0.02, 0.98)
RETURN_FULL = 0.5               # variation de valeur des titres qui donne le pas maximal du goût du risque
PEER_SHARE = 0.5                # part de ``imitation_rate`` appliquée à l'imitation d'un collègue


@dataclass
class LearningState:
    """État propre au lot R, créé au premier usage."""

    random: random.Random
    firms: dict = field(default_factory=dict)       # entreprise -> mémoire d'apprentissage
    styles: dict = field(default_factory=dict)      # dirigeant -> traits de l'entreprise qu'il dirigeait
    executives: dict = field(default_factory=dict)  # entreprise -> dirigeant du mois précédent
    base_propensity: dict = field(default_factory=dict)  # ménage -> (génération, propension de départ)
    prices: list = field(default_factory=list)      # cours des derniers mois (un dictionnaire par mois)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    banks: dict = field(default_factory=dict)       # banque -> essai de coussin volontaire
    ministers: dict = field(default_factory=dict)   # (personne, génération, rôle) -> essai d'effort


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class LearningMixin:
    """Apprentissage par essais et erreurs, imitation, style des dirigeants (lot R)."""

    def _learning_state(self) -> LearningState:
        state = getattr(self, "learning", None)
        if state is None:
            state = self.learning = LearningState(random.Random(self.config.seed + LEARNING_SEED_OFFSET))
        return state

    def _learning_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._learning_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    # ------------------------------------------------------------------ phase 8
    def _learning_step(self) -> None:
        """Fin de mois, comptes clos : apprentissage des entreprises puis des ménages, relevé."""
        state = self._learning_state()
        period = max(2, int(self.live.learning_period_months))
        learners = self._learning_firms()
        self._transfer_styles(learners)
        self._learn_firms(learners, period)
        prices = share_prices(self.companies)
        state.prices.append(prices)
        del state.prices[:-(period + 1)]
        self._learn_households(period, prices)
        self._learn_policy_agents(period)
        self._learning_record(learners)

    def _policy_trial(self, memory: dict, signal: float, bounds: tuple, period: int) -> bool:
        """Essai borné sur une période : garder le pas s'il améliore le signal, sinon l'annuler.

        Les signaux proviennent uniquement des résultats observés de l'agent. Aucun résultat
        agrégé attendu n'est injecté ; les nouveaux essais utilisent le générateur du lot R.
        """
        memory["sum"] = memory.get("sum", 0.0) + signal
        memory["months"] = memory.get("months", 0) + 1
        if memory["months"] < period:
            return False
        score = memory["sum"] / memory["months"]
        if "before" in memory and score < memory["before"]:
            memory["level"] = _clip(memory["level"] - memory.get("step", 0.0), *bounds)
            memory["direction"] = -memory.get("direction", 1.0)
        direction = memory.get("direction")
        if direction is None:
            direction = 1.0 if self.learning.random.random() < 0.5 else -1.0
        current = memory["level"]
        if (current <= bounds[0] and direction < 0.0) or (current >= bounds[1] and direction > 0.0):
            direction = -direction
        change = max(0.0, self.live.learning_step) * (0.5 + self.learning.random.random()) * direction
        memory["level"] = _clip(current + change, *bounds)
        memory.update(step=memory["level"] - current, direction=direction, before=score, sum=0.0, months=0)
        return True

    def _learn_policy_agents(self, period: int) -> None:
        state = self.learning
        for bank in self.companies:
            if bank.active and bank.sector == "bank":
                memory = state.banks.setdefault(bank.id, {"level": 0.0})
                # Rendement des fonds propres, pertes de crédit déjà comprises dans le profit.
                signal = bank.profit / max(abs(bank.bank_equity), bank.wage_bill, 1.0)
                if self._policy_trial(memory, signal, (0.0, 1.0), period):
                    self._learning_count("essais_banques")
        if not self.live.enriched_objectives:
            return
        people = {p.id: p for p in self.people}
        for role, office in self.institutions.offices.items():
            person = people.get(office.person_id)
            if person is None or office.objective is None:
                continue
            key = (person.id, person.generation, role)
            # Un objectif renouvelé constitue une nouvelle expérience, pas une dégradation
            # artificielle du bilan du ministre précédent.
            stamp = (office.objective.deadline, office.objective.indicator, office.objective.target)
            memory = state.ministers.get(key)
            if memory is None or memory.get("objective") != stamp:
                memory = state.ministers[key] = {"level": 1.0, "objective": stamp}
            if self._policy_trial(memory, office.objective.progress, (0.5, 1.5), period):
                self._learning_count("essais_ministres")

    def _learned_bank_buffer(self, bank_id: str) -> float:
        state = getattr(self, "learning", None)
        return state.banks.get(bank_id, {}).get("level", 0.0) if state else 0.0

    def _learned_minister_effort(self, office, role: str) -> float:
        state = getattr(self, "learning", None)
        person = next((p for p in self.people if p.id == office.person_id), None)
        if state is None or person is None:
            return 1.0
        return state.ministers.get((person.id, person.generation, role), {}).get("level", 1.0)

    def _learning_firms(self) -> list:
        """Entreprises qui apprennent : en activité, non bancaires, hors contrôle public (leur
        direction applique une consigne) et hors régulation des prix."""
        excluded = self._public_controlled_ids()
        return [
            c for c in self.companies
            if c.active and c.sector != "bank" and c.id not in excluded
            and not (c.pricing_aggression == 1.0 and c.reinvestment_preference == 1.0)
        ]

    # ------------------------------------------------------------------ dirigeants
    def _transfer_styles(self, learners: list) -> None:
        """Un dirigeant nouvellement nommé apporte ``STYLE_TRANSFER`` du style retenu dans
        l'entreprise qu'il dirigeait ; chaque dirigeant en place retient les traits de la
        sienne."""
        state = self.learning
        for company in learners:
            executive = company.executive_id
            previous = state.executives.get(company.id)
            if executive is not None and previous is not None and executive != previous:
                style = state.styles.get(executive)
                if style is not None:
                    for trait, value in zip(TRAITS, style):
                        low, high = TRAIT_BOUNDS[trait]
                        current = getattr(company, trait)
                        setattr(company, trait, _clip(current + STYLE_TRANSFER * (value - current), low, high))
                    self._learning_count("styles_apportes")
            state.executives[company.id] = executive
        for company in learners:
            if company.executive_id is not None:
                state.styles[company.executive_id] = tuple(getattr(company, trait) for trait in TRAITS)

    # ------------------------------------------------------------------ entreprises
    def _learn_firms(self, learners: list, period: int) -> None:
        """Essais et erreurs, puis imitation, à l'anniversaire de chaque entreprise (tous les
        ``period`` mois de son âge : les entreprises n'apprennent pas toutes le même mois)."""
        state = self.learning
        live = self.live
        step = max(0.0, live.learning_step)
        rng = state.random
        due = []
        for company in learners:
            memory = state.firms.get(company.id)
            if memory is None:
                memory = state.firms[company.id] = {
                    "somme": 0.0, "mois": 0, "avant": None, "trait": 0, "pas": 0.0, "marge": 0.0,
                    "sens": [0.0, 0.0],
                }
            scale = max(company.revenue, company.wage_bill, 1.0)
            memory["somme"] += company.profit / scale
            memory["mois"] += 1
            if company.employees and company.age_months >= period and company.age_months % period == 0:
                due.append((company, memory))
        if not due:
            return
        for company, memory in due:
            margin = memory["somme"] / max(1, memory["mois"])
            previous = memory["avant"]
            tried = memory["trait"]
            trait = TRAITS[tried]
            low, high = TRAIT_BOUNDS[trait]
            if previous is not None and memory["pas"] != 0.0:
                if margin < previous:
                    # le dernier pas a coûté : il est repris, et ce trait repartira dans l'autre sens
                    setattr(company, trait, _clip(getattr(company, trait) - memory["pas"], low, high))
                    memory["sens"][tried] = -memory["sens"][tried]
                    self._learning_count("essais_annules")
                else:
                    self._learning_count("essais_gardes")
            # trait suivant, nouvel essai dans le sens retenu pour lui (tiré la première fois)
            tried = memory["trait"] = (tried + 1) % len(TRAITS)
            if memory["sens"][tried] == 0.0:
                memory["sens"][tried] = 1.0 if rng.random() < 0.5 else -1.0
            direction = memory["sens"][tried]
            trait = TRAITS[tried]
            low, high = TRAIT_BOUNDS[trait]
            before = getattr(company, trait)
            after = _clip(before + direction * step * (0.5 + rng.random()), low, high)
            setattr(company, trait, after)
            memory["pas"] = after - before
            memory["avant"] = margin
            memory["marge"] = margin
            memory["somme"], memory["mois"] = 0.0, 0
        # imitation : la plus rentable du secteur sur sa dernière période close
        rate = _clip(live.imitation_rate, 0.0, 1.0)
        if rate <= 0.0:
            return
        best: dict[str, tuple] = {}
        for company in learners:
            memory = state.firms[company.id]
            if memory["avant"] is None or not company.employees:
                continue
            leader = best.get(company.sector)
            if leader is None or (memory["marge"], company.id) > (leader[1], leader[0].id):
                best[company.sector] = (company, memory["marge"])
        for company, memory in due:
            leader = best.get(company.sector)
            if leader is None or leader[0] is company or leader[1] <= memory["marge"]:
                continue
            for trait in TRAITS:
                low, high = TRAIT_BOUNDS[trait]
                current = getattr(company, trait)
                setattr(company, trait, _clip(current + rate * (getattr(leader[0], trait) - current), low, high))
            memory["pas"] = 0.0  # l'essai en cours est remplacé par la copie
            self._learning_count("imitations")

    # ------------------------------------------------------------------ ménages
    def _learn_households(self, period: int, prices: dict) -> None:
        """Chaque ménage révise ses habitudes une fois par ``period`` mois, à tour de rôle."""
        state = self.learning
        live = self.live
        turn = self.month % period
        step = max(0.0, live.learning_step)
        rate = _clip(live.imitation_rate, 0.0, 1.0) * PEER_SHARE
        old_prices = state.prices[0] if len(state.prices) > period else None
        richest: dict[str, object] = {}
        for person in self.people:
            employer = person.employer_id
            if employer is None:
                continue
            leader = richest.get(employer)
            if leader is None or (person.cash, person.id) > (leader.cash, leader.id):
                richest[employer] = person
        for index, person in enumerate(self.people):
            known = state.base_propensity.get(person.id)
            if known is None or known[0] != person.generation:
                # premier passage, ou héritier : la propension tirée est sa référence
                known = state.base_propensity[person.id] = (person.generation, person.consumption_propensity)
            if index % period != turn:
                continue
            # précaution : d'après la fréquence de ses propres mois sans emploi
            exposure = min(1.0, max(0.0, person.unemployment_risk) / PRECAUTION_FULL_RISK)
            target = known[1] * (1.0 - PRECAUTION * exposure)
            person.consumption_propensity += 0.5 * (target - person.consumption_propensity)
            # goût du risque : d'après ce que ses titres ont rapporté sur la période
            if old_prices is not None and person.shares:
                then = sum(q * old_prices.get(cid, 0.0) for cid, q in person.shares.items())
                now = sum(q * prices.get(cid, 0.0) for cid, q in person.shares.items())
                if then > 0.0:
                    gain = _clip((now / then - 1.0) / RETURN_FULL, -1.0, 1.0)
                    person.risk_tolerance = _clip(person.risk_tolerance + step * gain, *RISK_BOUNDS)
                    self._learning_count("revisions_risque")
            # imitation du collègue le plus riche
            leader = richest.get(person.employer_id) if person.employer_id is not None else None
            if rate > 0.0 and leader is not None and leader is not person:
                person.risk_tolerance = _clip(
                    person.risk_tolerance + rate * (leader.risk_tolerance - person.risk_tolerance), *RISK_BOUNDS
                )
                self._learning_count("imitations_menages")

    # ------------------------------------------------------------------ relevé
    def _learning_record(self, learners: list) -> None:
        """Relevé mensuel du lot R (mesures seules)."""
        state = self.learning
        stats = state.month_stats
        stats["mois"] = self.month
        count = len(learners)
        for trait, key in zip(TRAITS, ("agressivite_prix", "part_reinvestie")):
            values = [getattr(c, trait) for c in learners]
            mean = sum(values) / count if count else 0.0
            stats[key] = mean
            stats[f"{key}_dispersion"] = (
                (sum((v - mean) ** 2 for v in values) / count) ** 0.5 if count else 0.0
            )
        people = len(self.people)
        stats["gout_du_risque"] = sum(p.risk_tolerance for p in self.people) / people if people else 0.0
        stats["propension_a_consommer"] = (
            sum(p.consumption_propensity for p in self.people) / people if people else 0.0
        )
        for key in ("essais_gardes", "essais_annules", "imitations", "styles_apportes",
                    "revisions_risque", "imitations_menages", "essais_banques", "essais_ministres"):
            stats.setdefault(key, 0.0)
        state.history.append(stats)
        state.month_stats = {}

    def learning_history(self) -> list[dict]:
        """Relevés mensuels du lot R depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "learning", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset} for row in state.history if row["mois"] - offset >= 0]
