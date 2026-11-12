"""Lot K (novembre 2026) : influence, popularité, pouvoir présidentiel, aides et favoritisme.

Neuf mécanismes, chacun derrière son réglage, désactivés par défaut (aucun calcul, aucun tirage,
aucun attribut ajouté sans eux). L'état vit dans ``simulation.power`` (``PowerState``), créé au
premier usage ; les traits et les tirages viennent d'un générateur dédié.

- **Influence** (``influence_ranking``) : score de 0 à 100 par ménage, somme de quatre parts
  (fortune, fonction, capital contrôlé, popularité). Mesure seule : aucune décision ne le lit
  sans un autre réglage.
- **Popularité** (``personal_popularity``) : trois traits par ménage (charisme, intégrité,
  ambition), une notoriété (être connu) et une opinion (être apprécié) qui suit les résultats de
  la personne dans son rôle.
- **Élection à candidats** (``candidate_elections``) : le sortant affronte un opposant ; l'écart
  de popularité et de campagne entre dans le vote de chaque ménage ; l'élu devient président.
- **Lois de pouvoir** (``executive_power_laws``) : un président ambitieux fait voter des lois qui
  étendent son pouvoir ; un successeur intègre les abroge.
- **Aides aux entreprises** (``business_subsidies``) : programme légal, sur critères.
- **Favoritisme** (``political_favoritism``) : un président peu intègre, doté d'une enveloppe
  discrétionnaire, peut l'attribuer à une entreprise dont le patron est influent et lié à lui ;
  l'audit ou la presse peut révéler l'affaire (scandale, remboursement, amende, remaniement).
- **Lobbying** (``corporate_lobbying``) : dons de campagne, frein à l'instruction d'un dossier
  de monopole.
- **Pantouflage** (``revolving_door``) : un ancien membre du cabinet devient conseiller rémunéré
  d'une grande entreprise et lui apporte son lien avec la présidence.
- **Dynasties** (``political_dynasties``) : l'héritier d'une personne connue garde une part de
  sa notoriété.

Tous les paiements sont de vrais transferts (État, entreprises, ménages) : la monnaie est
conservée et le livre mensuel de l'État reste rapproché.
"""

from __future__ import annotations

import math
from heapq import nsmallest
import random
from dataclasses import dataclass, field

from economy.institutions import (
    OFFICE_LABELS,
    OFFICE_ROLES,
    OFFICE_SECTORS,
    challenger_orientation,
    display_name,
    proximity,
)
from economy.intersector import pay_business_suppliers
from economy.payments import transfer_cash
from economy.wealth import household_net_worth, share_prices

POWER_SEED_OFFSET = 86028121    # générateur dédié (traits, favoritisme, révélations)
SWITCHES = (
    "influence_ranking", "personal_popularity", "candidate_elections", "executive_power_laws",
    "business_subsidies", "political_favoritism", "corporate_lobbying", "revolving_door",
    "political_dynasties",
)
# --- influence : points maximaux de chaque part (total 100) ---
INFLUENCE_WEALTH = 30.0         # fortune
INFLUENCE_OFFICE = 35.0         # fonction (présidence)
INFLUENCE_CAPITAL = 20.0        # capital contrôlé
INFLUENCE_POPULARITY = 15.0     # popularité
WEALTH_MULTIPLE = 10.0          # fortune, en multiples de la fortune moyenne, qui donne le maximum
MINISTER_POINTS = 22.0          # points de fonction d'un ministre
EXECUTIVE_BASE = 6.0            # points de fonction d'un dirigeant de très petite entreprise
EXECUTIVE_SCALE = 22.0          # points ajoutés × racine du poids de l'entreprise dans l'économie
BOARD_POINTS = 3.0              # siège au conseil d'actionnaires (lot H)
CONTROL_STAKE = 0.10            # part du capital à partir de laquelle un actionnaire pèse
CONTROL_FULL = 0.25             # poids contrôlé (part de l'économie) qui donne le maximum
# --- popularité ---
OPINION_SPEED = 0.08            # part de l'écart d'opinion comblée chaque mois
OPINION_RESULTS = 0.30          # déplacement maximal de l'opinion par les résultats
FAME_RISE = 0.10                # vitesse à laquelle on devient connu
FAME_FADE = 0.02                # vitesse à laquelle on est oublié
MINISTER_FAME = 0.7             # notoriété visée d'un ministre (présidence : 1)
STAFF_SMOOTHING = 0.1           # lissage des effectifs d'une entreprise (jugement de son dirigeant)
# --- élection à candidats ---
CAMPAIGN_MONTHS = 6             # durée de la campagne avant l'élection
CAMPAIGN_GAIN = 0.10            # avantage maximal apporté par les dépenses de campagne
CAMPAIGN_HALF_WAGES = 24.0      # dépenses (en salaires mensuels de référence) qui en donnent la moitié
TERM_LIMIT = 2                  # mandats consécutifs d'une même personne (sans la loi « mandats »)
# --- lois de pouvoir ---
LAWS = ("enveloppe", "nominations", "controle", "medias", "mandats")
LAW_LABELS = {
    "enveloppe": "Enveloppe discrétionnaire de la présidence",
    "nominations": "Nominations par la présidence",
    "controle": "Contrôle des comptes affaibli",
    "medias": "Tutelle sur les médias",
    "mandats": "Limite de mandats levée",
}
LAW_EFFECTS = {
    "enveloppe": "la moitié des aides aux entreprises est attribuée par la présidence seule",
    "nominations": "la présidence nomme les dirigeants des entreprises publiques et peut geler "
                   "un dossier de monopole",
    "controle": "un audit découvre quatre fois moins souvent une irrégularité",
    "medias": "l'opinion réagit moitié moins aux mauvais résultats et aux scandales",
    "mandats": "le président peut se représenter sans limite",
}
LAW_MONTH = 6                   # mois du mandat (chaque année) où la présidence légifère
LAW_APPETITE = 0.55             # goût du pouvoir au-delà duquel un président propose une loi
LAW_MAJORITY = 0.52             # capital politique nécessaire à la première loi
LAW_STEP = 0.02                 # capital supplémentaire exigé par loi déjà votée
LAW_FAILURE_COST = 0.03         # opinion perdue par une loi rejetée
REPEAL_INTEGRITY = 0.60         # intégrité au-delà de laquelle un président abroge
# --- aides et favoritisme ---
AID_STANCE = {"laisser_faire": 0.5, "reguler": 1.0, "operateur_public": 1.5}
AID_LOSS_MONTHS = 3             # mois de pertes consécutives qui ouvrent droit à l'aide
AID_PAYROLL_SHARE = 0.25        # aide mensuelle maximale, en part de la masse salariale
DISCRETIONARY_SHARE = 0.5       # part des aides à la main de la présidence (loi « enveloppe »)
FAVOR_INTEGRITY = 0.40          # intégrité sous laquelle un président peut favoriser un proche
BOLD_INTEGRITY = 0.15           # intégrité sous laquelle il ose même face à un contrôle intact
FAVOR_INFLUENCE = 25.0          # influence minimale du patron favorisé
FAVOR_CONNECTION = 0.5          # lien minimal entre l'entreprise et la présidence
LINK_EMPLOYER = 0.35            # le président vient de cette entreprise
LINK_SHARES = 0.25              # le président en détient des titres
LINK_PROXIMITY = 0.20           # proximité politique du patron (au plus)
LINK_DONATION = 0.30            # l'entreprise a financé sa campagne
LINK_ALUMNI = 0.30              # un ancien du cabinet de ce président y est patron ou conseiller
AUDIT_WEAKENED = 0.25           # probabilité d'audit restante sous la loi « contrôle »
PRESS_CHANCE = 0.01             # probabilité mensuelle qu'une enquête de presse révèle une affaire
AFFAIR_MEMORY_MONTHS = 120      # durée pendant laquelle une affaire peut encore être révélée
SCANDAL_HIT = 0.30              # opinion perdue par les personnes mises en cause
FINE_RATE = 0.5                 # amende, en part de l'aide indue (en plus du remboursement)
INDEX_MONTHS = 60               # fenêtre des indices de corruption
# --- lobbying et pantouflage ---
DONATION_SHARE = 0.01           # part du bénéfice mensuel donnée à un candidat pendant la campagne
LOBBY_SHARE = 0.005             # part du bénéfice mensuel dépensée contre un dossier de monopole
LOBBY_DRAG = 0.5                # part maximale de l'instruction freinée par le lobbying
LOBBY_INFLUENCE = 40.0          # influence du dirigeant qui donne le frein maximal
ADVISOR_WINDOW_MONTHS = 12      # délai pendant lequel un ancien du cabinet intéresse une entreprise
ADVISOR_MONTHS = 36             # durée d'un mandat de conseiller
ADVISOR_FEE_SHARE = 0.5         # honoraires mensuels, en part du salaire d'un ministre
# --- dynasties ---
DYNASTY_SHARE = 0.3             # part de la notoriété transmise à l'héritier
DYNASTY_FAME = 0.2              # notoriété minimale pour qu'un nom se transmette


@dataclass
class PowerState:
    """État propre au lot K, créé au premier usage d'un mécanisme activé."""

    random: random.Random
    traits: dict = field(default_factory=dict)      # ménage -> (charisme, intégrité, ambition)
    opinion: dict = field(default_factory=dict)     # ménage -> opinion (0 détesté, 1 adoré)
    notoriety: dict = field(default_factory=dict)   # ménage -> notoriété (0 inconnu, 1 connu de tous)
    staff: dict = field(default_factory=dict)       # entreprise -> effectifs lissés
    scores: dict = field(default_factory=dict)      # ménage -> parts de son influence (mois ``scores_month``)
    scores_month: int = -1
    top_history: list = field(default_factory=list)  # classements mensuels (dix premiers)
    # élection à candidats
    candidates: dict | None = None
    campaign: dict = field(default_factory=dict)    # candidat -> dépenses de campagne reçues
    donations: dict = field(default_factory=dict)   # entreprise -> {candidat: dons cumulés}
    president_elect: str | None = None
    mandate_share: float = 0.5                      # part des voix du président à son élection
    consecutive_terms: int = 1
    term_seen: int = -1
    cabinet: dict = field(default_factory=dict)     # rôle -> ménage, au mois précédent
    # lois de pouvoir
    laws: dict = field(default_factory=dict)        # loi -> mois du vote
    pending_dismissal: str | None = None            # ministre à remplacer au début du mois suivant
    # aides et favoritisme
    aided: set = field(default_factory=set)         # entreprises aidées sur critères le mois dernier
    aid_log: list = field(default_factory=list)     # aides versées
    affairs: list = field(default_factory=list)     # aides de faveur (vérité du modèle)
    lobbied: dict = field(default_factory=dict)     # secteur -> frein acheté ce mois
    # pantouflage et dynasties
    alumni: dict = field(default_factory=dict)      # ancien du cabinet -> rôle, mois, président servi
    advisors: dict = field(default_factory=dict)    # entreprise -> conseiller, mois, président servi
    dynasties: dict = field(default_factory=dict)   # héritier -> notoriété reçue, mois
    log: list = field(default_factory=list)         # événements (lois, candidatures, scandales…)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)     # un relevé par mois (mesures seules)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class PowerMixin:
    """Influence, popularité, candidats, lois de pouvoir, aides, favoritisme (lot K)."""

    # ------------------------------------------------------------------ état
    def _power_enabled(self) -> bool:
        live = self.live
        return bool(
            live.influence_ranking or live.personal_popularity or live.candidate_elections
            or live.executive_power_laws or live.business_subsidies or live.political_favoritism
            or live.corporate_lobbying or live.revolving_door or live.political_dynasties
        )

    def _power_state(self) -> PowerState:
        state = getattr(self, "power", None)
        if state is None:
            state = self.power = PowerState(random.Random(self.config.seed + POWER_SEED_OFFSET))
        return state

    def _power_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._power_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    def _power_log(self, kind: str, text: str, **details) -> None:
        self._power_state().log.append({"mois": self.month, "type": kind, "texte": text, **details})

    def _traits(self, person_id: str) -> tuple:
        """(charisme, intégrité, ambition) d'un ménage, tirés une fois dans [0, 1]."""
        state = self._power_state()
        traits = state.traits.get(person_id)
        if traits is None:
            rng = state.random
            traits = state.traits[person_id] = (rng.random(), rng.random(), rng.random())
        return traits

    def _ensure_traits(self) -> None:
        """Traits tirés dans l'ordre de la population (les nouveaux ménages à leur arrivée)."""
        traits = self._power_state().traits
        for person in self.people:
            if person.id not in traits:
                self._traits(person.id)

    def _opinion(self, person_id: str) -> float:
        """Opinion sur une personne ; à défaut, sa référence (0,3 + 0,4 × charisme). Sans tirage :
        un ménage arrivé ce mois, sans traits encore, est à 0,5."""
        state = self._power_state()
        opinion = state.opinion.get(person_id)
        if opinion is not None:
            return opinion
        traits = state.traits.get(person_id)
        return 0.3 + 0.4 * traits[0] if traits is not None else 0.5

    def _appeal(self, person_id: str) -> float:
        """Popularité d'une personne : notoriété × opinion (un inconnu n'est pas populaire)."""
        state = getattr(self, "power", None)
        if state is None or not self.live.personal_popularity:
            return 0.0
        return state.notoriety.get(person_id, 0.0) * self._opinion(person_id)

    def _standing(self, person_id: str) -> float:
        """Avantage électoral d'un candidat : notoriété × (opinion − 0,5), de −0,5 à +0,5."""
        state = getattr(self, "power", None)
        if state is None or not self.live.personal_popularity:
            return 0.0
        return state.notoriety.get(person_id, 0.0) * (self._opinion(person_id) - 0.5)

    def _private_firms(self) -> list:
        """Entreprises actives non bancaires, avec des salariés, hors contrôle public."""
        excluded = self._public_controlled_ids()
        return [
            c for c in self.companies
            if c.active and c.sector != "bank" and c.employees and c.id not in excluded
        ]

    def _company_weights(self) -> dict[str, float]:
        """Poids de chaque entreprise active dans l'économie : moyenne de sa part des emplois
        privés et de sa part du chiffre d'affaires du mois écoulé."""
        firms = [c for c in self.companies if c.active]
        staff = sum(len(c.employees) for c in firms)
        revenue = sum(max(0.0, c.previous_revenue) for c in firms)
        return {
            c.id: 0.5 * (len(c.employees) / staff if staff else 0.0)
            + 0.5 * (max(0.0, c.previous_revenue) / revenue if revenue > 0.0 else 0.0)
            for c in firms
        }

    # ------------------------------------------------------------------ influence
    def influence_scores(
        self, refresh: bool = False, *, household_wealth: dict[str, float] | None = None,
    ) -> dict[str, dict]:
        """Influence de chaque ménage : ``fortune`` (au plus 30), ``fonction`` (35), ``capital``
        (20), ``popularite`` (15) et ``total`` ; ``poste`` décrit sa fonction. Lecture seule.

        - fortune : 30 × √(fortune / (10 × fortune moyenne)), plafonnée ;
        - fonction : présidence 35, ministre 22, dirigeant 6 + 22 × √(poids de son entreprise),
          plus 3 pour un siège au conseil d'actionnaires ;
        - capital contrôlé : 20 × √(Σ poids × part détenue / 0,25) sur les entreprises dont il
          détient au moins 10 % ;
        - popularité : 15 × notoriété × opinion (0 sans le réglage).

        ``household_wealth`` transmet les fortunes déjà calculées dans la même phase,
        sans cache persistant. L'appelant garantit que leurs déterminants n'ont pas
        changé ; sans ce relevé explicite, la valorisation est toujours refaite.
        """
        state = getattr(self, "power", None)
        if state is not None and not refresh and state.scores_month == self.month:
            return state.scores
        live = self.live
        if household_wealth is None:
            prices = share_prices(self.companies)
            worth = {p.id: max(0.0, household_net_worth(p, self.companies, live, prices)) for p in self.people}
        else:
            # Même ordre de population et même plancher que la valorisation directe.
            worth = {p.id: max(0.0, household_wealth[p.id]) for p in self.people}
        mean = sum(worth.values()) / len(worth) if worth else 0.0
        weights = self._company_weights()
        by_id = {c.id: c for c in self.companies if c.active}
        executives = {c.executive_id: c for c in by_id.values() if c.executive_id}
        inst = getattr(self, "institutions", None)
        roles = {office.person_id: role for role, office in inst.offices.items()} if inst is not None else {}
        enterprise = getattr(self, "enterprise", None)
        seats = set()
        if enterprise is not None and live.shareholder_governance:
            seats = {holder for board in enterprise.boards.values() for holder, _ in board}
        # Capital contrôlé : (titres en circulation, seuil sûr, poids) de chaque entreprise active.
        # Sous le seuil (un peu moins de ``CONTROL_STAKE`` × titres en circulation), la part
        # détenue est certainement inférieure à ``CONTROL_STAKE`` : la ligne est écartée sans
        # division ; au-dessus, le calcul exact historique décide.
        control = {}
        for company_id, target in by_id.items():
            circulating = target.shares_outstanding - target.treasury_shares
            if circulating > 0.0:
                control[company_id] = (circulating, circulating * (CONTROL_STAKE * 0.999),
                                       weights.get(company_id, 0.0))
        scores = {}
        for person in self.people:
            pid = person.id
            fortune = INFLUENCE_WEALTH * min(1.0, math.sqrt(worth[pid] / (WEALTH_MULTIPLE * mean))) if mean > 0.0 else 0.0
            role = roles.get(pid)
            company = executives.get(pid)
            if role == "presidence":
                office, post = INFLUENCE_OFFICE, OFFICE_LABELS[role]
            elif role is not None:
                office, post = MINISTER_POINTS, OFFICE_LABELS[role]
            elif company is not None:
                office = EXECUTIVE_BASE + EXECUTIVE_SCALE * math.sqrt(weights.get(company.id, 0.0))
                post = f"Direction, {self.company_name(company.id)}"
            else:
                office, post = 0.0, person.employment_role
            if pid in seats:
                office = min(INFLUENCE_OFFICE, office + BOARD_POINTS)
            controlled = 0.0
            for company_id, quantity in person.shares.items():
                entry = control.get(company_id)
                if entry is None or quantity < entry[1]:
                    continue
                stake = quantity / entry[0]
                if stake >= CONTROL_STAKE:
                    controlled += entry[2] * min(1.0, stake)
            capital = INFLUENCE_CAPITAL * min(1.0, math.sqrt(controlled / CONTROL_FULL))
            popularity = INFLUENCE_POPULARITY * self._appeal(pid)
            scores[pid] = {
                "fortune": fortune, "fonction": office, "capital": capital, "popularite": popularity,
                "total": fortune + office + capital + popularity, "poste": post,
                "patrimoine": worth[pid],
            }
        if state is not None:
            state.scores, state.scores_month = scores, self.month
        return scores

    def influence_table(self, limit: int = 10) -> list[dict]:
        """Classement des ménages les plus influents (lecture seule, pour l'interface)."""
        scores = self.influence_scores()
        order = sorted(scores, key=lambda pid: (-scores[pid]["total"], pid))[:max(1, limit)]
        state = getattr(self, "power", None)
        rows = []
        for rank, pid in enumerate(order, start=1):
            row = scores[pid]
            rows.append({
                "rang": rank, "id": pid, "nom": display_name(pid), "poste": row["poste"],
                "influence": row["total"], "fortune": row["fortune"], "fonction": row["fonction"],
                "capital": row["capital"], "popularite": row["popularite"],
                "patrimoine": row["patrimoine"],
                "notoriete": state.notoriety.get(pid, 0.0) if state is not None else 0.0,
                "opinion": self._opinion(pid) if state is not None and self.live.personal_popularity else None,
            })
        return rows

    # ------------------------------------------------------------------ popularité
    def _president_signal(self) -> float:
        """Résultats de la présidence vus par l'opinion, de −1 à 1 : croissance réelle sur un an,
        chômage au-delà de son niveau naturel, part des objectifs ministériels atteints."""
        live = self.live
        history = self.history
        recent = history[-12:]
        if not recent:
            return 0.0
        growth = 0.0
        if len(history) >= 24:
            before = sum(r.real_gdp for r in history[-24:-12])
            growth = sum(r.real_gdp for r in recent) / before - 1.0 if before > 0.0 else 0.0
        gap = sum(r.unemployment for r in recent) / len(recent) - live.natural_unemployment_rate
        signal = _clip(4.0 * growth - 6.0 * gap + (self._objectives_met_share() - 0.5), -1.0, 1.0)
        if live.media_sector:
            # lot M : le ton des médias, pondéré par leur audience, remplace l'effet forfaitaire
            # de la loi « médias » (qui rapproche alors leurs lignes du pouvoir)
            return _clip(signal + self._media_spin(), -1.0, 1.0)
        if signal < 0.0 and "medias" in self.power.laws:
            signal *= 0.5
        return signal

    def _update_popularity(self) -> None:
        """Un mois d'opinion. Notoriété : monte vite vers celle de la fonction (présidence 1,
        ministre 0,7, dirigeant 0,15 + 0,6 × √poids de l'entreprise), s'efface lentement.
        Opinion : rejoint de 8 % par mois sa référence (0,3 + 0,4 × charisme) déplacée d'au plus
        ± 0,3 par les résultats : présidence (croissance, chômage, objectifs), ministre
        (avancement de son objectif), dirigeant (marge de l'entreprise, évolution des effectifs)."""
        state = self.power
        inst = self.institutions
        weights = self._company_weights()
        signals: dict[str, tuple] = {}
        for company in self.companies:
            if not company.active:
                continue
            staff = float(len(company.employees))
            smoothed = state.staff.get(company.id, staff)
            if company.executive_id:
                margin = company.previous_profit / company.previous_revenue if company.previous_revenue > 0.0 else 0.0
                growth = (staff - smoothed) / max(smoothed, 1.0)
                signals[company.executive_id] = (
                    0.15 + 0.6 * math.sqrt(weights.get(company.id, 0.0)),
                    _clip(3.0 * margin + 4.0 * growth, -1.0, 1.0),
                )
            state.staff[company.id] = smoothed + STAFF_SMOOTHING * (staff - smoothed)
        for role, office in inst.offices.items():
            if role == "presidence":
                signals[office.person_id] = (1.0, self._president_signal())
            else:
                progress = office.objective.progress if office.objective is not None else 0.0
                signals[office.person_id] = (MINISTER_FAME, _clip(progress, -1.0, 1.0))
        for person in self.people:
            pid = person.id
            fame, signal = signals.get(pid, (0.0, 0.0))
            known = state.notoriety.get(pid, 0.0)
            state.notoriety[pid] = known + (FAME_RISE if fame > known else FAME_FADE) * (fame - known)
            base = 0.3 + 0.4 * self._traits(pid)[0]
            opinion = state.opinion.get(pid, base)
            state.opinion[pid] = _clip(opinion + OPINION_SPEED * (base + OPINION_RESULTS * signal - opinion))

    def _shift_opinion(self, person_id: str, change: float) -> None:
        state = self.power
        state.opinion[person_id] = _clip(self._opinion(person_id) + change)

    # ------------------------------------------------------------------ élection à candidats
    def _candidate_mode(self) -> bool:
        live = self.live
        return bool(live.candidate_elections and live.individual_voting and live.economic_voting
                    and live.political_cycle_enabled)

    def _candidates_valid(self, candidates: dict) -> bool:
        inst = self.institutions
        people = {p.id for p in self.people}
        cabinet = {office.person_id for office in inst.offices.values()}
        president = inst.holder("presidence")
        if candidates["presidence"] != president or candidates["opposant"] not in people:
            return False
        if candidates["opposant"] in cabinet or candidates["sortant"] not in people:
            return False
        return candidates["dauphin"] or candidates["sortant"] == president

    def _declare_candidates(self) -> None:
        """Candidatures, ``CAMPAIGN_MONTHS`` mois avant l'élection.

        Le camp du sortant présente le président, ou un dauphin s'il a atteint ``TERM_LIMIT``
        mandats consécutifs (règle des lois de pouvoir, levée par la loi « mandats »). Le camp
        opposé présente son membre le plus fort hors du cabinet. Force d'un candidat : ambition ×
        (0,25 + popularité) × (0,5 + influence / 100). Le programme de l'opposition reste la
        médiane de son camp (``challenger_orientation``)."""
        live, state, inst = self.live, self.power, self.institutions
        period = live.election_period_months
        if self.month <= 1 or (period - (self.month - 1) % period) % period > CAMPAIGN_MONTHS:
            return
        if state.candidates is not None and self._candidates_valid(state.candidates):
            return
        president = inst.holder("presidence")
        if president is None:
            return
        self._ensure_leans()
        scores = self.influence_scores()
        orientation = self.government.orientation
        social = orientation >= 50.0
        cabinet = {office.person_id for office in inst.offices.values()}

        def strength(person) -> tuple:
            ambition = self._traits(person.id)[2]
            return (ambition * (0.25 + self._appeal(person.id)) * (0.5 + scores[person.id]["total"] / 100.0),
                    person.id)

        free = [p for p in self.people if p.id not in cabinet and p.retraining_months <= 0]
        opposite = [p for p in free if (inst.lean[p.id] < 50.0) == social]
        pool = opposite or free
        if not pool:
            return
        opponent = max(pool, key=strength)
        incumbent, heir = president, False
        if (live.executive_power_laws and "mandats" not in state.laws
                and state.consecutive_terms >= TERM_LIMIT):
            camp = [
                p for p in self.people
                if p.id not in (president, opponent.id) and p.retraining_months <= 0
                and (inst.lean[p.id] >= 50.0) == social
            ]
            if camp:
                incumbent, heir = max(camp, key=strength).id, True
        state.candidates = {
            "mois": self.month, "sortant": incumbent, "opposant": opponent.id, "dauphin": heir,
            "presidence": president,
            "programme_opposant": challenger_orientation(self._voters(), orientation, live),
        }
        state.campaign = {}
        self._power_log(
            "candidature",
            f"{display_name(incumbent)} ({'dauphin du sortant' if heir else 'sortant'}) contre "
            f"{display_name(opponent.id)} (opposition)",
        )

    def _election_shift(self) -> float:
        """Décalage du vote en faveur du sortant : ``vote_popularity_weight`` × (écart d'avantage
        électoral + écart de campagne). 0 sans candidats."""
        state = self.power
        candidates = state.candidates
        if candidates is None:
            return 0.0
        half = CAMPAIGN_HALF_WAGES * max(self.wage_reference, 1.0)

        def campaign(person_id: str) -> float:
            spent = state.campaign.get(person_id, 0.0)
            return spent / (spent + half)

        gap = self._standing(candidates["sortant"]) - self._standing(candidates["opposant"])
        gap += CAMPAIGN_GAIN * (campaign(candidates["sortant"]) - campaign(candidates["opposant"]))
        candidates["ecart"] = gap
        return max(0.0, self.live.vote_popularity_weight) * gap

    def _settle_candidates(self, reelected: bool) -> None:
        """Après le vote : l'élu est le candidat du camp vainqueur ; il sera nommé à la présidence
        par ``_open_term`` (sans refus possible)."""
        state = self.power
        candidates = state.candidates
        if candidates is None:
            return
        winner = candidates["sortant"] if reelected else candidates["opposant"]
        state.president_elect = winner
        self.institutions.elections[-1].update({
            "candidat_sortant": display_name(candidates["sortant"]),
            "candidat_opposant": display_name(candidates["opposant"]),
            "president_elu": display_name(winner),
            "ecart_popularite": candidates.get("ecart", 0.0),
            "dauphin": candidates["dauphin"],
        })
        self._power_log("election", f"{display_name(winner)} remporte l'élection présidentielle")
        state.candidates = None
        state.campaign = {}

    # ------------------------------------------------------------------ phase 1
    def _power_month_start(self) -> None:
        """Avant l'élection éventuelle : traits, opinion du mois, candidatures."""
        state = self._power_state()
        self._ensure_traits()
        if state.pending_dismissal is not None:
            role, state.pending_dismissal = state.pending_dismissal, None
            self._dismiss_minister(role, "scandale : aide de faveur révélée")
        if self.live.personal_popularity:
            self._update_popularity()
        if self._candidate_mode():
            self._declare_candidates()

    def _power_govern(self) -> None:
        """Après l'élection éventuelle : suivi du mandat et du cabinet, lois de pouvoir."""
        state, inst = self.power, self.institutions
        state.president_elect = None
        cabinet = {role: office.person_id for role, office in inst.offices.items()}
        president = cabinet.get("presidence")
        term = inst.terms[-1]
        if term.since == self.month and state.term_seen != term.since:
            state.term_seen = term.since
            last = inst.elections[-1] if inst.elections else None
            if last is not None and last["mois"] == self.month:
                state.mandate_share = last["part_sortant"] if last["reelu"] else 1.0 - last["part_sortant"]
            else:
                state.mandate_share = 0.5
            same = president is not None and president == state.cabinet.get("presidence")
            state.consecutive_terms = state.consecutive_terms + 1 if same else 1
        members = set(cabinet.values())
        for role, person_id in state.cabinet.items():
            if person_id not in members:
                state.alumni[person_id] = {
                    "role": role, "mois": self.month, "presidence": state.cabinet.get("presidence"),
                }
        state.cabinet = cabinet
        if self.live.executive_power_laws:
            self._legislate()

    def _law_relevant(self, name: str) -> bool:
        live = self.live
        if name == "enveloppe":
            return bool(live.business_subsidies)
        if name == "mandats":
            return bool(live.candidate_elections)
        return True

    def _political_capital(self, president: str) -> float:
        """Capital politique de la présidence : sa part des voix à l'élection, plus la moitié de
        l'écart de son opinion à 0,5 (avec la popularité)."""
        capital = self.power.mandate_share
        if self.live.personal_popularity:
            capital += 0.5 * (self._opinion(president) - 0.5)
        return capital

    def _legislate(self) -> None:
        """Une fois par an (``LAW_MONTH`` mois après le début du mandat puis chaque année).

        Un président intègre (intégrité > ``REPEAL_INTEGRITY``) abroge la loi de pouvoir la plus
        récente. Sinon, si son goût du pouvoir (0,6 × ambition + 0,4 × (1 − intégrité)) dépasse
        ``LAW_APPETITE``, il propose la suivante de la liste ; elle passe si son capital
        politique dépasse ``LAW_MAJORITY`` + ``LAW_STEP`` par loi déjà votée. Une loi rejetée lui
        coûte un peu d'opinion. Aucun tirage."""
        state, inst = self.power, self.institutions
        term = inst.terms[-1]
        if self.month == term.since or (self.month - term.since) % 12 != LAW_MONTH:
            return
        president = inst.holder("presidence")
        if president is None:
            return
        _, integrity, ambition = self._traits(president)
        if integrity > REPEAL_INTEGRITY:
            if state.laws:
                name = max(state.laws, key=lambda law: (state.laws[law], law))
                self._repeal_law(name, "un président attaché aux contre-pouvoirs revient sur la loi")
            return
        if 0.6 * ambition + 0.4 * (1.0 - integrity) <= LAW_APPETITE:
            return
        name = next((law for law in LAWS if law not in state.laws and self._law_relevant(law)), None)
        if name is None:
            return
        capital = self._political_capital(president)
        needed = LAW_MAJORITY + LAW_STEP * len(state.laws)
        if capital > needed:
            state.laws[name] = self.month
            self._power_count("lois_votees")
            self._power_log("loi", f"Loi votée : {LAW_LABELS[name]}", loi=name)
            self._decide(
                "presidence", f"Loi de pouvoir votée : {LAW_LABELS[name]}",
                f"{LAW_EFFECTS[name]} ; capital politique {capital:.0%} pour {needed:.0%} requis",
            )
        else:
            self._power_count("lois_rejetees")
            self._power_log("loi", f"Loi rejetée : {LAW_LABELS[name]}", loi=name)
            if self.live.personal_popularity:
                self._shift_opinion(president, -LAW_FAILURE_COST)
            self._decide(
                "presidence", f"Loi de pouvoir rejetée : {LAW_LABELS[name]}",
                f"capital politique {capital:.0%} pour {needed:.0%} requis",
            )

    def _repeal_law(self, name: str, motive: str) -> None:
        state = self.power
        if state.laws.pop(name, None) is None:
            return
        self._power_count("lois_abrogees")
        self._power_log("loi", f"Loi abrogée : {LAW_LABELS[name]}", loi=name)
        self._decide("presidence", f"Loi de pouvoir abrogée : {LAW_LABELS[name]}", motive)

    def _dismiss_minister(self, role: str, motive: str) -> None:
        """Remplace un ministre (même règle que le remaniement de mi-mandat) : le successeur
        reprend ses objectifs."""
        inst = self.institutions
        office = inst.offices.get(role)
        if office is None or not inst.terms:
            return
        term = inst.terms[-1]
        people = {p.id: p for p in self.people}
        self._end_membership(term, office)
        if office.person_id in people:
            self._release(people[office.person_id])
        excluded = {o.person_id for r, o in inst.offices.items() if r != role}
        successor = self._nominate(role, excluded | {office.person_id})
        successor.objective = office.objective
        successor.secondary = office.secondary
        inst.offices[role] = successor
        self._start_membership(term, successor)
        self._decide(
            "presidence", f"Remaniement : {OFFICE_ROLES[role]}", motive,
            amount=self._office_salary(role), financing="budget de l'État (salaire mensuel)",
            note=self._interest_note(successor),
        )

    def _loyal_director(self, pool: list, director):
        """Loi « nominations » : un président peu intègre nomme à la tête d'une entreprise
        publique la personne la plus proche de lui politiquement, plutôt que la plus compétente."""
        state, inst = self.power, self.institutions
        president = inst.holder("presidence")
        if "nominations" not in state.laws or president is None:
            return director
        if self._traits(president)[1] >= FAVOR_INTEGRITY:
            return director
        self._ensure_leans()
        lean = inst.lean[president]
        loyal = max(pool, key=lambda p: (proximity(inst.lean[p.id], lean), p.id))
        if loyal is not director:
            self._power_count("nominations_de_faveur")
            self._power_log("nomination", f"{display_name(loyal.id)} nommé par la présidence à la tête "
                                          "d'une entreprise publique (proximité politique)")
        return loyal

    # ------------------------------------------------------------------ liens et favoritisme
    def _patrons(self, company) -> list[str]:
        """Patrons d'une entreprise : son dirigeant, puis les ménages qui en détiennent au moins
        ``CONTROL_STAKE``."""
        circulating = company.shares_outstanding - company.treasury_shares
        patrons = [company.executive_id] if company.executive_id else []
        if circulating > 0.0:
            for person in self.people:
                if person.id != company.executive_id and person.shares.get(company.id, 0.0) >= CONTROL_STAKE * circulating:
                    patrons.append(person.id)
        return patrons

    def _connection(self, company, president: str, patrons: list[str] | None = None) -> float:
        """Lien entre une entreprise et le président, de 0 à 1, somme de faits :
        le président y travaillait avant sa nomination (0,35), il en détient des titres (0,25),
        proximité politique du patron le plus proche (au plus 0,20), l'entreprise a financé sa
        campagne (0,30), un ancien de son cabinet en est patron ou conseiller (0,30)."""
        state, inst = self.power, self.institutions
        patrons = self._patrons(company) if patrons is None else patrons
        link = 0.0
        office = inst.offices.get("presidence")
        if office is not None and office.person_id == president and office.previous_employer == company.name:
            link += LINK_EMPLOYER
        holder = next((p for p in self.people if p.id == president), None)
        if holder is not None and holder.shares.get(company.id, 0.0) > 0.0:
            link += LINK_SHARES
        lean = inst.lean.get(president)
        if lean is not None:
            near = [proximity(inst.lean[pid], lean) for pid in patrons if pid in inst.lean]
            if near:
                link += LINK_PROXIMITY * max(near)
        if state.donations.get(company.id, {}).get(president, 0.0) > 0.0:
            link += LINK_DONATION
        advisor = state.advisors.get(company.id)
        served = advisor is not None and advisor["presidence"] == president
        served = served or any(
            state.alumni.get(pid, {}).get("presidence") == president for pid in patrons
        )
        if served:
            link += LINK_ALUMNI
        return min(1.0, link)

    def _favored_company(self, firms: list):
        """Entreprise qu'un président peu intègre favorise ce mois, ou ``None``.

        Conditions, toutes requises : intégrité du président sous ``FAVOR_INTEGRITY`` (et sous
        ``BOLD_INTEGRITY`` tant que le contrôle des comptes n'est pas affaibli) ; une entreprise
        privée dont un patron a une influence d'au moins ``FAVOR_INFLUENCE`` et un lien avec la
        présidence d'au moins ``FAVOR_CONNECTION`` ; puis un tirage de probabilité
        ``favoritism_monthly_chance``. Retourne (entreprise, patron, lien)."""
        state, inst = self.power, self.institutions
        president = inst.holder("presidence")
        if president is None:
            return None
        integrity = self._traits(president)[1]
        if integrity >= FAVOR_INTEGRITY or ("controle" not in state.laws and integrity >= BOLD_INTEGRITY):
            return None
        self._ensure_leans()
        scores = self.influence_scores()
        best = None
        for company in firms:
            patrons = [pid for pid in self._patrons(company) if pid != president and pid in scores]
            if not patrons:
                continue
            patron = max(patrons, key=lambda pid: (scores[pid]["total"], pid))
            if scores[patron]["total"] < FAVOR_INFLUENCE:
                continue
            link = self._connection(company, president, patrons)
            if link < FAVOR_CONNECTION:
                continue
            key = (link * scores[patron]["total"], company.id)
            if best is None or key > best[0]:
                best = (key, company, patron, link)
        if best is None:
            return None
        if state.random.random() >= max(0.0, self.live.favoritism_monthly_chance):
            return None
        return best[1], best[2], best[3]

    def _business_aid(self) -> None:
        """Aides aux entreprises du mois (phase 4, après l'exécution de la loi de finances).

        Budget : ``business_aid_gdp_share`` du PIB annuel par an, × 0,5 (laisser faire), 1
        (réguler) ou 1,5 (opérateur public) selon la doctrine industrielle. Aide sur critères :
        entreprise privée non bancaire en perte depuis ``AID_LOSS_MONTHS`` mois, au prorata de
        ses salariés, au plus ``AID_PAYROLL_SHARE`` de sa masse salariale ; le budget non employé
        n'est pas dépensé. Sous la loi « enveloppe », ``DISCRETIONARY_SHARE`` du budget est à la
        main de la présidence : attribuée sur les mêmes critères par un président intègre, à une
        entreprise liée par un président qui favorise (``_favored_company``)."""
        live, state, inst = self.live, self.power, self.institutions
        factor = AID_STANCE.get(inst.doctrine.industry_stance, 1.0)
        budget = max(0.0, live.business_aid_gdp_share) * self._annual_gdp() / 12.0 * factor
        if budget <= 0.0:
            return
        firms = self._private_firms()
        payments = []
        favor = None
        if "enveloppe" in state.laws and live.political_favoritism:
            favor = self._favored_company(firms)
        if favor is not None:
            payments.append((favor[0], DISCRETIONARY_SHARE * budget, favor))
            budget -= DISCRETIONARY_SHARE * budget
        eligible = [
            c for c in firms
            if c.loss_streak >= AID_LOSS_MONTHS and (favor is None or c is not favor[0])
        ]
        staff = sum(len(c.employees) for c in eligible)
        for company in eligible:
            amount = min(budget * len(company.employees) / staff, AID_PAYROLL_SHARE * company.wage_bill)
            if amount > 1.0:
                payments.append((company, amount, None))
        total = sum(amount for _, amount, _ in payments)
        if total <= 0.0:
            state.aided = set()
            return
        ratio = _clip(self._state_funds(total) / total)
        paid_total = 0.0
        aided = set()
        for company, amount, favored in payments:
            paid = amount * ratio
            if paid <= 0.0:
                continue
            transfer_cash(self.public, company, paid)
            paid_total += paid
            financing = "budget de l'État (aides aux entreprises), hors loi de finances"
            if favored is None:
                aided.add(company.id)
                if company.id not in state.aided:
                    self._decide(
                        "industrie", f"Aide à l'entreprise : {company.name}",
                        f"en perte depuis {company.loss_streak} mois, {len(company.employees)} salariés ; "
                        "aide sur critères, versée chaque mois tant que les pertes durent",
                        amount=paid, financing=financing, note=self._target_conflicts(company.id),
                    )
                self._power_count("aides_criteres", paid)
            else:
                _, patron, link = favored
                state.affairs.append({
                    "mois": self.month, "entreprise": company.id, "nom": company.name,
                    "patron": patron, "presidence": inst.holder("presidence"), "montant": paid,
                    "lien": link, "revele": None, "recouvre": 0.0,
                })
                self._decide(
                    "presidence", f"Aide discrétionnaire : {company.name}",
                    "enveloppe discrétionnaire de la présidence ; motif avancé : entreprise jugée stratégique",
                    amount=paid, financing=financing, note=self._target_conflicts(company.id),
                )
                self._power_count("aides_faveur", paid)
            state.aid_log.append({
                "mois": self.month, "entreprise": company.id, "nom": company.name, "montant": paid,
                "type": "critères" if favored is None else "discrétionnaire",
            })
        state.aided = aided
        inst.book.add("aides_entreprises", -paid_total)
        self.public.spending += paid_total
        self._power_count("aides", paid_total)

    def _detect_affairs(self) -> None:
        """Chaque mois, chaque aide de faveur non révélée (depuis moins de
        ``AFFAIR_MEMORY_MONTHS``) peut l'être : par l'audit (``audit_detection_chance``, ×
        ``AUDIT_WEAKENED`` sous la loi « contrôle ») ou par la presse (``PRESS_CHANCE``, moitié
        moins sous la loi « médias »)."""
        state = self.power
        for affair in state.affairs:
            if affair["revele"] is not None or self.month - affair["mois"] > AFFAIR_MEMORY_MONTHS:
                continue
            chance = max(0.0, self.live.audit_detection_chance) * (AUDIT_WEAKENED if "controle" in state.laws else 1.0)
            press = PRESS_CHANCE * (0.5 if "medias" in state.laws else 1.0)
            if self.live.media_sector:
                # lot M : enquêtes des médias, selon leurs journalistes et leur indépendance
                press = self._media_press_chance(affair)
            chance += press
            if state.random.random() < chance:
                if self.live.media_sector:
                    self._media_scoop(affair, press, chance)
                self._reveal_affair(affair)

    def _reveal_affair(self, affair: dict) -> None:
        """Scandale : l'opinion sanctionne le président en cause et le patron (moitié moins pour
        un président en place qui tient les médias) ; l'entreprise rembourse l'aide, plus une
        amende de ``FINE_RATE``, dans la limite de sa trésorerie ; si le président en cause est
        encore en place, le ministre de l'Industrie est remplacé au début du mois suivant et la
        loi « contrôle » est abrogée sous la pression."""
        state, inst = self.power, self.institutions
        affair["revele"] = self.month
        president = affair["presidence"]
        in_office = inst.holder("presidence") == president
        if self.live.personal_popularity:
            hit = SCANDAL_HIT * (0.5 if in_office and "medias" in state.laws else 1.0)
            if any(p.id == president for p in self.people):
                self._shift_opinion(president, -hit)
            if any(p.id == affair["patron"] for p in self.people):
                self._shift_opinion(affair["patron"], -SCANDAL_HIT)
        company = self._company(affair["entreprise"])
        paid = 0.0
        if company is not None:
            paid = min(affair["montant"] * (1.0 + FINE_RATE), max(0.0, company.cash))
            if paid > 0.0:
                transfer_cash(company, self.public, paid)
                company.expenses += paid
                inst.book.add("amendes_favoritisme", paid)
                self.public.revenue += paid
        affair["recouvre"] = paid
        self._power_count("affaires_revelees")
        self._power_count("amendes", paid)
        self._power_log(
            "scandale",
            f"Aide de faveur à {affair['nom']} révélée ({affair['montant']:,.0f} €)".replace(",", " "),
        )
        self._decide(
            "presidence", f"Scandale : aide de faveur révélée ({affair['nom']})",
            f"aide discrétionnaire de {affair['montant']:,.0f} € versée au mois {affair['mois']} à une "
            f"entreprise liée à la présidence ({display_name(president)}) ; remboursement et amende "
            f"recouvrés : {paid:,.0f} €".replace(",", " "),
            amount=paid, financing="recette exceptionnelle",
        )
        if in_office:
            state.pending_dismissal = "industrie"
            self._repeal_law("controle", "scandale : le contrôle des comptes est rétabli sous la pression")

    # ------------------------------------------------------------------ lobbying
    def _campaign_donations(self) -> None:
        """Pendant la campagne, une entreprise privée bénéficiaire d'une taille au moins moyenne
        donne ``DONATION_SHARE`` de son bénéfice du mois écoulé au candidat dont le programme est
        le plus proche de son dirigeant, sans entamer un mois de paie. Le don achète des
        prestations de campagne aux entreprises de services ; il rend le candidat plus visible
        (``_election_shift``) et lie l'entreprise à lui (``_connection``)."""
        state, inst = self.power, self.institutions
        candidates = state.candidates
        if candidates is None or not self._candidate_mode():
            return
        self._ensure_leans()
        orientation = self.government.orientation
        program = candidates["programme_opposant"]
        firms = self._private_firms()
        weights = self._company_weights()
        average = sum(weights.get(c.id, 0.0) for c in firms) / len(firms) if firms else 0.0
        for company in firms:
            lean = inst.lean.get(company.executive_id or "")
            if lean is None or company.previous_profit <= 0.0 or weights.get(company.id, 0.0) < average:
                continue
            side = "sortant" if proximity(lean, orientation) >= proximity(lean, program) else "opposant"
            amount = min(DONATION_SHARE * company.previous_profit, max(0.0, company.cash - company.wage_bill))
            if amount <= 1.0:
                continue
            paid = pay_business_suppliers(
                company, amount, {"services": 1.0}, self.companies, self.resources, priced=False,
            )
            if paid <= 0.0:
                continue
            candidate = candidates[side]
            state.campaign[candidate] = state.campaign.get(candidate, 0.0) + paid
            given = state.donations.setdefault(company.id, {})
            given[candidate] = given.get(candidate, 0.0) + paid
            self._power_count("dons_campagne", paid)

    def _lobby_cases(self) -> None:
        """Une entreprise privée bénéficiaire visée par un dossier de monopole en instruction
        dépense ``LOBBY_SHARE`` de son bénéfice en conseil (payé aux services) ; l'instruction du
        mois est freinée de ``LOBBY_DRAG`` × (influence de son dirigeant / ``LOBBY_INFLUENCE``)."""
        state, inst = self.power, self.institutions
        state.lobbied = {}
        excluded = self._public_controlled_ids()
        scores = None
        for case in inst.cases:
            if case.status != "instruction" or case.target is None:
                continue
            company = self._company(case.target)
            if (company is None or company.sector == "bank" or company.id in excluded
                    or company.previous_profit <= 0.0):
                continue
            amount = min(LOBBY_SHARE * company.previous_profit, max(0.0, company.cash - company.wage_bill))
            if amount <= 1.0:
                continue
            paid = pay_business_suppliers(
                company, amount, {"services": 1.0}, self.companies, self.resources, priced=False,
            )
            if paid <= 0.0:
                continue
            scores = self.influence_scores() if scores is None else scores
            influence = scores.get(company.executive_id or "", {}).get("total", 0.0)
            state.lobbied[case.sector] = LOBBY_DRAG * min(1.0, influence / LOBBY_INFLUENCE)
            self._power_count("lobbying", paid)

    def _case_drag(self, case) -> float:
        """Part de l'instruction d'un dossier de monopole perdue ce mois : le frein acheté par le
        lobbying, ou 1 (dossier gelé) sous la loi « nominations » quand un président peu intègre
        est lié à l'entreprise visée."""
        state, inst = self.power, self.institutions
        drag = state.lobbied.get(case.sector, 0.0) if self.live.corporate_lobbying else 0.0
        president = inst.holder("presidence")
        if "nominations" in state.laws and president is not None and case.target is not None:
            company = self._company(case.target)
            if (company is not None and self._traits(president)[1] < FAVOR_INTEGRITY
                    and self._connection(company, president) >= FAVOR_CONNECTION):
                self._power_count("dossiers_geles")
                return 1.0
        if drag > 0.0:
            self._power_count("dossiers_freines")
        return drag

    # ------------------------------------------------------------------ pantouflage
    def _revolving_door(self) -> None:
        """Un ancien membre du cabinet sorti depuis moins de ``ADVISOR_WINDOW_MONTHS`` mois devient
        conseiller de la plus grande entreprise privée bénéficiaire des secteurs de son ancien
        portefeuille (tous secteurs pour un ancien président) qui n'en a pas. Elle lui verse
        chaque mois ``ADVISOR_FEE_SHARE`` du salaire d'un ministre pendant ``ADVISOR_MONTHS`` mois,
        tant qu'elle est active ; ce conseiller la relie au président qu'il a servi."""
        state, inst = self.power, self.institutions
        people = {p.id: p for p in self.people}
        cabinet = {office.person_id for office in inst.offices.values()}
        fee = ADVISOR_FEE_SHARE * inst.salaries.get("ministre", 0.0)
        for company_id in list(state.advisors):
            advisor = state.advisors[company_id]
            company = self._company(company_id)
            person = people.get(advisor["personne"])
            if (company is None or person is None or person.id in cabinet
                    or self.month - advisor["mois"] >= ADVISOR_MONTHS):
                del state.advisors[company_id]
                continue
            paid = min(fee, max(0.0, company.cash))
            if paid > 0.0:
                transfer_cash(company, person, paid)
                company.expenses += paid
                person.income_this_turn += paid
                person.lifetime_earnings += paid
                self._power_count("honoraires_conseillers", paid)
        advising = {advisor["personne"] for advisor in state.advisors.values()}
        weights = None
        for person_id in sorted(state.alumni):
            alumnus = state.alumni[person_id]
            if (person_id in advising or person_id in cabinet or person_id not in people
                    or self.month - alumnus["mois"] > ADVISOR_WINDOW_MONTHS):
                continue
            sectors = OFFICE_SECTORS.get(alumnus["role"], ())
            weights = self._company_weights() if weights is None else weights
            firms = [
                c for c in self._private_firms()
                if c.id not in state.advisors and c.previous_profit > 0.0
                and (not sectors or c.sector in sectors)
            ]
            if not firms:
                continue
            company = max(firms, key=lambda c: (weights.get(c.id, 0.0), c.id))
            state.advisors[company.id] = {
                "personne": person_id, "mois": self.month, "presidence": alumnus["presidence"],
                "role": alumnus["role"],
            }
            advising.add(person_id)
            self._power_count("pantouflages")
            self._power_log(
                "pantouflage",
                f"{display_name(person_id)} (ancien titulaire : {OFFICE_LABELS[alumnus['role']]}) "
                f"devient conseiller de {company.name}",
            )

    # ------------------------------------------------------------------ phases 4, 7 et 8
    def _power_public_step(self) -> None:
        """Phase 4, après la loi de finances : dons, lobbying, conseillers, aides, révélations."""
        live = self.live
        self._power_state()
        if live.corporate_lobbying:
            self._campaign_donations()
            self._lobby_cases()
        if live.revolving_door:
            self._revolving_door()
        if live.business_subsidies:
            self._business_aid()
        if live.political_favoritism:
            self._detect_affairs()

    def _power_succession(self, person) -> None:
        """Succession d'un ménage : l'héritier a ses propres traits et sa propre opinion ; il
        n'est plus un ancien du cabinet, un conseiller ni un candidat. Avec les dynasties, il
        garde ``DYNASTY_SHARE`` de la notoriété d'un défunt connu (au moins ``DYNASTY_FAME``)."""
        state = self.power
        pid = person.id
        known = state.notoriety.get(pid, 0.0)
        rng = state.random
        state.traits[pid] = (rng.random(), rng.random(), rng.random())
        state.opinion.pop(pid, None)
        inherited = DYNASTY_SHARE * known if self.live.political_dynasties and known >= DYNASTY_FAME else 0.0
        state.notoriety[pid] = inherited
        if inherited > 0.0:
            state.dynasties[pid] = {"mois": self.month, "notoriete": inherited,
                                    "generation": person.generation}
            self._power_count("heritiers_connus")
            self._power_log("dynastie", f"{display_name(pid)} : l'héritier garde une part de la notoriété du nom")
        state.alumni.pop(pid, None)
        state.campaign.pop(pid, None)
        for company_id in [cid for cid, advisor in state.advisors.items() if advisor["personne"] == pid]:
            del state.advisors[company_id]
        if state.candidates is not None and pid in (state.candidates["sortant"], state.candidates["opposant"]):
            state.candidates = None

    def _power_record(self, *, household_wealth: dict[str, float] | None = None) -> None:
        """Phase 8 : relevé mensuel du lot K (mesures seules)."""
        state = self._power_state()
        live = self.live
        inst = self.institutions
        stats = state.month_stats
        stats["mois"] = self.month
        president = inst.holder("presidence")
        stats["lois_en_vigueur"] = len(state.laws)
        if president is not None and (live.personal_popularity or live.executive_power_laws
                                      or live.political_favoritism or live.candidate_elections):
            _, integrity, ambition = self._traits(president)
            stats["integrite_presidence"] = integrity
            stats["ambition_presidence"] = ambition
            if live.personal_popularity:
                stats["opinion_presidence"] = self._opinion(president)
        if live.personal_popularity and inst.offices:
            ministers = [o.person_id for role, o in inst.offices.items() if role != "presidence"]
            if ministers:
                stats["opinion_ministres"] = sum(self._opinion(pid) for pid in ministers) / len(ministers)
        if live.political_favoritism or live.business_subsidies:
            window = [row for row in state.history[-(INDEX_MONTHS - 1):]]
            aid = stats.get("aides", 0.0) + sum(row.get("aides", 0.0) for row in window)
            favor = stats.get("aides_faveur", 0.0) + sum(row.get("aides_faveur", 0.0) for row in window)
            stats["corruption_reelle"] = favor / aid if aid > 0.0 else 0.0
            stats["affaires"] = len(state.affairs)
            stats["affaires_connues"] = sum(1 for a in state.affairs if a["revele"] is not None)
            stats["corruption_percue"] = sum(
                1 for a in state.affairs
                if a["revele"] is not None and self.month - a["revele"] < INDEX_MONTHS
            )
        if live.revolving_door:
            stats["conseillers"] = len(state.advisors)
        if live.influence_ranking:
            scores = self.influence_scores(refresh=True, household_wealth=household_wealth)
            order = nsmallest(10, scores, key=lambda pid: (-scores[pid]["total"], pid))
            state.top_history.append({"mois": self.month, "top": [(pid, scores[pid]["total"]) for pid in order]})
            if order:
                stats["influence_max"] = scores[order[0]]["total"]
                stats["influence_top10"] = sum(scores[pid]["total"] for pid in order) / len(order)
        state.history.append(stats)
        state.month_stats = {}

    # ------------------------------------------------------------------ lectures pour l'interface
    def power_history(self) -> list[dict]:
        """Relevés mensuels du lot K depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "power", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset} for row in state.history if row["mois"] - offset >= 0]

    def power_events(self) -> list[dict]:
        """Événements du lot K depuis la période 0, du plus récent au plus ancien."""
        state = getattr(self, "power", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [
            {**row, "periode": row["mois"] - offset}
            for row in reversed(state.log) if row["mois"] - offset >= 0
        ]

    def power_profile(self, person_id: str) -> dict:
        """Traits, notoriété et opinion d'un ménage (vide sans état)."""
        state = getattr(self, "power", None)
        if state is None or person_id not in state.traits:
            return {}
        charisma, integrity, ambition = state.traits[person_id]
        return {
            "charisme": charisma, "integrite": integrity, "ambition": ambition,
            "notoriete": state.notoriety.get(person_id, 0.0), "opinion": self._opinion(person_id),
        }
