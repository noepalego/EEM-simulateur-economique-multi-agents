"""État institutionnel (``state_model="institutions"``) : personnes, doctrines, objectifs, journal.

L'orientation 0–100 reste l'échelle commune des gouvernements, mais elle se traduit en
doctrines explicites (budget, travail, services publics, industrie), avec un écart propre à
chaque gouvernement. Une présidence et quatre ministres sont nommés parmi les ménages ; chaque
ministre reçoit un objectif (indicateur, niveau, échéance, enveloppe) qui oriente ses décisions
sans jamais fixer un résultat. Chaque décision publique a un auteur, une date, un motif, un
montant et un financement, consignés dans le journal des décisions.

Ce module ne contient que des données et des règles pures ; l'orchestration est dans
``state_government.py`` (et les modules ``state_*`` des livraisons suivantes). Tout l'état
vit dans ``StateInstitutions``, créé au premier usage du mode, jamais dans les autres modes.
Les hypothèses chiffrées sont décrites dans docs/ETAT.md.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

from economy.politics import ALL_LEVERS, MULTIPLIER_LEVERS, orientation_label

# décalage de graine du générateur dédié (nominations, traits de proximité, écarts de doctrine)
STATE_SEED_OFFSET = 27644437

PORTFOLIOS = ("finances", "travail", "industrie", "services")
OFFICES = ("presidence", *PORTFOLIOS)
OFFICE_LABELS = {
    "presidence": "Présidence",
    "finances": "Économie et Finances",
    "travail": "Travail et Solidarités",
    "industrie": "Industrie et Participations publiques",
    "services": "Services publics",
}
# intitulé du poste (rôle d'emploi du ménage nommé)
OFFICE_ROLES = {
    "presidence": "Présidence de la République",
    "finances": "Ministre de l'Économie et des Finances",
    "travail": "Ministre du Travail et des Solidarités",
    "industrie": "Ministre de l'Industrie et des Participations publiques",
    "services": "Ministre des Services publics",
}
# métiers et domaines de compétence (secteurs) utiles à chaque fonction
OFFICE_TRADES = {
    "presidence": (),
    "finances": ("financier", "gestionnaire"),
    "travail": ("gestionnaire", "agent", "commercial"),
    "industrie": ("ingenieur", "technicien", "gestionnaire"),
    "services": ("gestionnaire", "agent", "ingenieur"),
}
OFFICE_SECTORS = {
    "presidence": (),
    "finances": ("bank",),
    "travail": ("services",),
    "industrie": ("energy", "materials", "goods"),
    "services": ("services", "real_estate"),
}

# Noms d'affichage : dérivés de l'identifiant par une empreinte, sans consommer de tirage.
# Prénoms épicènes : aucun genre n'est attribué aux ménages.
FIRST_NAMES = (
    "Camille", "Dominique", "Claude", "Alix", "Sacha", "Morgan", "Charlie", "Lou", "Andréa",
    "Eden", "Noa", "Maxime", "Ange", "Gaël", "Loïs", "Swann", "Marion", "Jules", "Yaël", "Céleste",
)
LAST_NAMES = (
    "Martin", "Bernard", "Thomas", "Petit", "Robert", "Richard", "Durand", "Dubois", "Moreau",
    "Laurent", "Simon", "Michel", "Lefebvre", "Leroy", "Roux", "David", "Bertrand", "Morel",
    "Fournier", "Girard", "Bonnet", "Dupont", "Lambert", "Fontaine", "Rousseau", "Vincent",
    "Muller", "Faure", "André", "Mercier", "Blanc", "Guérin", "Boyer", "Garnier", "Chevalier",
    "François", "Legrand", "Gauthier", "Garcia", "Perrin", "Robin", "Clément", "Morin",
    "Nicolas", "Henry", "Roussel", "Mathieu", "Masson", "Marchand", "Duval",
)


def display_name(person_id: str) -> str:
    """Nom d'affichage déterministe d'un ménage (empreinte de son identifiant)."""
    digest = hashlib.sha256(person_id.encode()).digest()
    first = FIRST_NAMES[digest[0] % len(FIRST_NAMES)]
    last = LAST_NAMES[int.from_bytes(digest[1:3], "big") % len(LAST_NAMES)]
    return f"{first} {last}"


def reflect(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Ramène une orientation dans [low, high] par réflexion aux bornes (pas d'écrêtage).

    Écrêter une réélection à 0 + N(0, 5) laisse l'orientation collée à la borne la moitié du
    temps ; réfléchir garde la même dispersion sans masse de probabilité sur la borne.
    """
    span = high - low
    position = (value - low) % (2.0 * span)
    return low + (position if position <= span else 2.0 * span - position)


def minimum_wage_ambition(orientation: float) -> float:
    """Salaire minimum visé par un gouvernement, en part du salaire médian observé.

    Hypothèse documentée (docs/ETAT.md) : 50 % du médian au plus à droite du centre (le
    minimum existe, sans coup de pouce), 55 à 60 % au centre, 60 à 68 % au centre gauche
    (60–80), 68 à 80 % pour un gouvernement social (80–100 ; novembre 2026 : 80 % au lieu de
    75 % à l'extrême gauche). Courbe continue, linéaire par morceaux.
    """
    points = ((0.0, 0.50), (50.0, 0.50), (60.0, 0.60), (80.0, 0.68), (100.0, 0.80))
    orientation = max(0.0, min(100.0, orientation))
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if orientation <= x1:
            return y0 + (y1 - y0) * (orientation - x0) / max(x1 - x0, 1e-9)
    return points[-1][1]


@dataclass
class Doctrine:
    """Programme d'un gouvernement : l'orientation commune traduite en doctrines explicites.

    ``positions`` : position de chaque levier historique dans [−1, 1] (−1 libéral, +1 social),
    orientation + écart propre au gouvernement ; les autres champs en dérivent (voir
    ``make_doctrine``) et chacun a son propre écart.
    """

    orientation: float
    positions: dict[str, float]
    # doctrine budgétaire
    budget_rule: str = "stabilisation"   # « excedent », « stabilisation », « programme »
    surplus_goal: float = 0.0            # excédent visé (part du PIB annuel) tant que la dette est positive
    deficit_tolerance: float = 0.0       # déficit accepté (part du PIB) pour financer le programme
    interest_tolerance: float = 0.10     # charge d'intérêts / recettes au-delà de laquelle le déficit est refusé
    surplus_use: str = "equilibre"       # « impots », « equilibre » (programmes puis impôts), « programmes »
    repay_share: float = 0.0             # part de la trésorerie au-delà de la réserve qui rembourse la dette
    tax_factors: dict[str, float] = field(default_factory=dict)  # impôts du programme / réglages de base
    # doctrine du travail
    minimum_wage_ambition: float = 0.5   # salaire minimum visé / salaire médian (coup de pouce)
    minimum_wage_indexation: float = 1.0  # part de l'indexation légale appliquée (0 : gel)
    benefit_factor: float = 1.0          # allocation chômage / réglage de base
    transfer_share: float = 0.0          # transferts du programme (part du PIB retardé)
    transfer_targeting: float = 0.0      # part des transferts versée sous condition de ressources
    # revenus du capital (dividendes, loyers) : None = au barème avec les salaires ; sinon
    # prélèvement forfaitaire au taux de base × ce facteur (gouvernements de droite)
    capital_flat_factor: float | None = None
    # doctrine des services publics
    purchases_share: float = 0.0         # achats de fonctionnement (part du PIB retardé)
    investment_share: float = 0.0        # investissement public (part du PIB retardé)
    coverage_goal: float = 1.0           # couverture voulue des besoins de chaque service
    point_indexation: float = 1.0        # indexation du point d'indice sur l'inflation retardée
    point_catch_up: bool = False         # rattrapage du salaire public médian sur le privé
    # doctrine industrielle
    industry_stance: str = "reguler"     # « laisser_faire », « reguler », « operateur_public »
    privatization_stance: str = "vendre_si_besoin"  # « vendre_si_regle », « vendre_si_besoin », « garder_sauf_regle »
    # rémunération du cabinet
    salary_policy: str = "decote"        # « aligne_prive », « decote », « plafond_smic »

    @property
    def label(self) -> str:
        return orientation_label(self.orientation)


BUDGET_RULE_LABELS = {
    "excedent": "équilibre ou excédent, remboursement de la dette",
    "stabilisation": "stabiliser le ratio de dette hérité",
    "programme": "financer le programme, déficit accepté sous tolérance",
}
STANCE_LABELS = {
    "laisser_faire": "attendre l'entrée de concurrents, réguler un secteur critique",
    "reguler": "réguler les prix d'un monopole durable",
    "operateur_public": "opérateur public dans les secteurs critiques, régulation ailleurs",
}
PRIVATIZATION_LABELS = {
    "vendre_si_regle": "vendre ce que la règle permet (non stratégique, concurrence, prix)",
    "vendre_si_besoin": "vendre si la règle le permet et qu'un besoin de financement existe",
    "garder_sauf_regle": "garder, sauf participation non stratégique durablement déficitaire",
}


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def make_doctrine(orientation: float, rng: random.Random, config) -> Doctrine:
    """Doctrines d'un gouvernement d'orientation donnée (hypothèses : docs/ETAT.md).

    Tirages, dans cet ordre et tous sur le générateur dédié : un écart par levier historique
    (``ALL_LEVERS``, écart-type ``policy_noise``), puis un écart par paramètre continu. Les
    catégories (règle budgétaire, attitude industrielle) suivent l'orientation : moins de 35
    libéral, 35 à 65 centre, plus de 65 social.
    """
    noise = config.policy_noise
    strength = max(0.0, config.policy_strength)
    base = (orientation - 50.0) / 50.0
    positions = {
        lever: max(-1.0, min(1.0, base + rng.gauss(0.0, noise))) for lever in ALL_LEVERS
    }

    def deviation(scale: float) -> float:
        return rng.gauss(0.0, noise) * scale

    # impôts du programme : mêmes amplitudes que les leviers historiques
    tax_factors = {
        lever: max(0.0, 1.0 + strength * MULTIPLIER_LEVERS[lever] * positions[lever])
        for lever in ("income_tax", "top_tax", "corporate_tax", "vat")
    }
    liberal, social = orientation < 35.0, orientation > 65.0
    doctrine = Doctrine(orientation=orientation, positions=positions, tax_factors=tax_factors)
    doctrine.budget_rule = "excedent" if liberal else "programme" if social else "stabilisation"
    doctrine.surplus_goal = (
        _bounded(0.005 + 0.01 * (-base - 0.3) / 0.7 + deviation(0.02), 0.0, 0.02) if liberal else 0.0
    )
    doctrine.deficit_tolerance = (
        _bounded(0.01 + 0.03 * (base - 0.3) / 0.7 + deviation(0.04), 0.0, 0.05) if social else 0.0
    )
    doctrine.interest_tolerance = _bounded(0.08 + 0.06 * orientation / 100.0 + deviation(0.04), 0.04, 0.20)
    # usage de la trésorerie au-delà de la réserve : le libéral rembourse, les autres la
    # réemploient par la loi suivante (le centre garde ainsi le ratio dont il hérite)
    doctrine.surplus_use = "impots" if liberal else "programmes" if social else "equilibre"
    doctrine.repay_share = 1.0 if liberal else 0.0
    # écart propre d'au plus 3 points ; dès 60/100, au moins 60 % du médian (minimum contraignant)
    curve = minimum_wage_ambition(orientation)
    doctrine.minimum_wage_ambition = _bounded(
        curve + deviation(0.1), max(curve - 0.03, 0.60 if orientation >= 60.0 else 0.0), curve + 0.03
    )
    doctrine.minimum_wage_indexation = (
        _bounded(0.6 * orientation / 35.0 + deviation(1.0), 0.0, 1.0) if liberal else 1.0
    )
    doctrine.benefit_factor = max(0.0, 1.0 + strength * MULTIPLIER_LEVERS["benefits"] * positions["benefits"])
    doctrine.transfer_share = max(
        0.0, config.institution_transfer_gdp_share
        * (1.0 + strength * MULTIPLIER_LEVERS["social_transfers"] * positions["social_transfers"])
    )
    # dérivés des positions déjà tirées (aucun tirage nouveau) : ciblage des transferts d'autant
    # plus fort que la position « transferts » est à gauche ; à droite du centre, les revenus du
    # capital quittent le barème pour un prélèvement forfaitaire d'autant plus bas que la position
    # « tranche supérieure » est à droite
    doctrine.transfer_targeting = _bounded(strength * positions["social_transfers"], 0.0, 1.0)
    doctrine.capital_flat_factor = (
        max(0.0, 1.0 + strength * positions["top_tax"]) if orientation < 50.0 else None
    )
    doctrine.purchases_share = max(
        0.0, config.public_purchases_gdp_share
        * (1.0 + strength * MULTIPLIER_LEVERS["public_purchases"] * positions["public_purchases"])
    )
    doctrine.investment_share = max(
        0.0, config.public_investment_gdp_share
        * (1.0 + strength * MULTIPLIER_LEVERS["public_investment"] * positions["public_investment"])
    )
    doctrine.coverage_goal = _bounded(
        0.95 + 0.15 * positions["public_employment"] + deviation(0.3), 0.70, 1.15
    )
    if liberal:
        doctrine.point_indexation = _bounded(0.3 * orientation / 35.0 + deviation(1.0), 0.0, 1.0)
    elif social:
        doctrine.point_indexation = 1.0
    else:
        doctrine.point_indexation = _bounded(0.7 + deviation(1.0), 0.3, 1.0)
    doctrine.point_catch_up = social
    doctrine.industry_stance = "laisser_faire" if liberal else "operateur_public" if social else "reguler"
    doctrine.privatization_stance = (
        "vendre_si_regle" if liberal else "garder_sauf_regle" if social else "vendre_si_besoin"
    )
    doctrine.salary_policy = "aligne_prive" if liberal else "plafond_smic" if social else "decote"
    return doctrine


def doctrine_summary(doctrine: Doctrine) -> dict[str, str]:
    """Résumé lisible des quatre doctrines (interface, historique des gouvernements)."""
    budget = BUDGET_RULE_LABELS[doctrine.budget_rule]
    if doctrine.budget_rule == "excedent":
        budget += f" (excédent visé {doctrine.surplus_goal:.1%} du PIB)"
    elif doctrine.budget_rule == "programme":
        budget += f" (déficit toléré {doctrine.deficit_tolerance:.1%} du PIB)"
    budget += f" ; intérêts tolérés {doctrine.interest_tolerance:.0%} des recettes"
    labor = (
        f"salaire minimum visé {doctrine.minimum_wage_ambition:.0%} du médian, indexation "
        f"{doctrine.minimum_wage_indexation:.0%} ; allocation × {doctrine.benefit_factor:.2f} ; "
        f"transferts {doctrine.transfer_share:.1%} du PIB, dont {doctrine.transfer_targeting:.0%} "
        f"sous condition de ressources ; revenus du capital "
        + ("au barème" if doctrine.capital_flat_factor is None
           else f"au forfait (taux de base × {doctrine.capital_flat_factor:.2f})")
    )
    services = (
        f"couverture des besoins {doctrine.coverage_goal:.0%} ; point d'indice indexé à "
        f"{doctrine.point_indexation:.0%}{' avec rattrapage' if doctrine.point_catch_up else ''} ; "
        f"achats {doctrine.purchases_share:.1%}, investissement {doctrine.investment_share:.1%} du PIB"
    )
    industry = f"{STANCE_LABELS[doctrine.industry_stance]} ; {PRIVATIZATION_LABELS[doctrine.privatization_stance]}"
    return {"Budget": budget, "Travail": labor, "Services publics": services, "Industrie": industry}


@dataclass
class Objective:
    """Objectif d'un ministre : un indicateur, un niveau, une échéance, une enveloppe.

    ``comparison`` : « <= » ou « >= » (niveau à atteindre), « ~ » (rester à moins de
    ``tolerance`` du niveau de départ : stabiliser). L'objectif guide les décisions du ministre ;
    il ne fixe jamais un résultat.
    """

    indicator: str
    label: str
    comparison: str
    target: float
    baseline: float
    deadline: int
    envelope: float = 0.0
    tolerance: float = 0.0
    value: float = 0.0
    progress: float = 0.0
    status: str = "en cours"

    def met(self, value: float) -> bool:
        if self.comparison == "<=":
            return value <= self.target
        if self.comparison == ">=":
            return value >= self.target
        return abs(value - self.baseline) <= self.tolerance

    def update(self, value: float) -> None:
        """Progression : 0 au point de départ, 1 objectif atteint (négative si l'écart se creuse)."""
        self.value = value
        if self.comparison == "~":
            # 1 dans la bande de tolérance, 0 à deux fois la tolérance, −1 à trois fois
            excess = abs(value - self.baseline) - self.tolerance
            self.progress = 1.0 if excess <= 0.0 else max(-1.0, 1.0 - excess / max(self.tolerance, 1e-9))
            return
        if self.met(value):
            self.progress = 1.0
            return
        span = self.target - self.baseline
        if abs(span) < 1e-12:
            self.progress = 0.0
            return
        self.progress = max(-1.0, min(1.0, (value - self.baseline) / span))

    def close(self) -> str:
        """Bilan à l'échéance : atteint, partiel (au moins la moitié du chemin) ou manqué."""
        if self.met(self.value):
            self.status = "atteint"
        elif self.progress >= 0.5:
            self.status = "partiel"
        else:
            self.status = "manqué"
        return self.status


INDICATOR_LABELS = {
    "dette_pib": "dette / PIB annuel",
    "solde_pib": "solde public / PIB (12 mois)",
    "interets_recettes": "intérêts / recettes (12 mois)",
    "smic_median": "salaire minimum / salaire médian du privé",
    "chomage": "chômage moyen (12 mois)",
    "couverture_services": "couverture minimale des besoins des services",
    "masse_salariale_pib": "masse salariale publique / PIB (12 mois)",
    "monopoles_critiques": "secteurs critiques dominés sans opérateur public ni régulation",
    "monopoles": "secteurs dominés sans réponse publique",
    "portefeuille_non_strategique": "valeur des participations non stratégiques",
}


def make_objective(role: str, doctrine: Doctrine, values: dict[str, float], month: int,
                   deadline: int, config, envelope: float = 0.0) -> Objective:
    """Objectif principal d'un ministre, tiré du programme (hypothèses : docs/ETAT.md)."""

    def objective(indicator: str, comparison: str, target: float, text: str, tolerance: float = 0.0) -> Objective:
        return Objective(indicator, text, comparison, target, values[indicator], deadline,
                         envelope=envelope, tolerance=tolerance, value=values[indicator])

    if role == "finances":
        if doctrine.budget_rule == "excedent":
            debt = values["dette_pib"]
            if debt > 0.03:
                return objective("dette_pib", "<=", debt - 0.03,
                                 f"réduire la dette de 3 points de PIB (de {debt:.0%} à {debt - 0.03:.0%})")
            return objective("solde_pib", ">=", 0.0, "budget à l'équilibre ou en excédent")
        if doctrine.budget_rule == "stabilisation":
            return objective("dette_pib", "~", values["dette_pib"],
                             f"stabiliser la dette héritée ({values['dette_pib']:.0%} du PIB, ± 3 points)", 0.03)
        return objective("interets_recettes", "<=", doctrine.interest_tolerance,
                         f"financer le programme avec des intérêts sous {doctrine.interest_tolerance:.0%} des recettes")
    if role == "travail":
        if doctrine.orientation >= 55.0:
            return objective("smic_median", ">=", doctrine.minimum_wage_ambition,
                             f"porter le salaire minimum à {doctrine.minimum_wage_ambition:.0%} du salaire médian")
        target = config.natural_unemployment_rate + 0.01
        return objective("chomage", "<=", target, f"ramener le chômage sous {target:.0%}")
    if role == "industrie":
        if doctrine.industry_stance == "operateur_public":
            return objective("monopoles_critiques", "<=", 0.0,
                             "aucun secteur critique dominé sans opérateur public ni régulation")
        if doctrine.industry_stance == "reguler":
            return objective("monopoles", "<=", 0.0, "aucun monopole durable sans réponse publique")
        portfolio = values["portefeuille_non_strategique"]
        if portfolio > 0.0:
            return objective("portefeuille_non_strategique", "<=", 0.5 * portfolio,
                             "réduire de moitié le portefeuille non stratégique, sans vente à perte")
        return objective("monopoles", "<=", values["monopoles"],
                         "ne laisser apparaître aucun nouveau monopole sans réponse")
    # services publics
    if doctrine.coverage_goal >= 0.9:
        goal = min(1.0, doctrine.coverage_goal)
        return objective("couverture_services", ">=", goal,
                         f"couvrir au moins {goal:.0%} des besoins de chaque service")
    return objective("masse_salariale_pib", "<=", values["masse_salariale_pib"],
                     "même service avec une masse salariale contenue (part du PIB non accrue)")


@dataclass
class Office:
    """Une fonction du cabinet (présidence ou ministère) et la personne qui l'occupe."""

    role: str
    person_id: str
    since: int
    previous_employer: str = "Sans emploi"
    objective: Objective | None = None
    holdings: tuple = ()        # entreprises dont la personne détenait des titres à sa nomination
    refusals: tuple = ()        # personnes pressenties qui ont refusé avant elle


@dataclass
class GovernmentTerm:
    """Un mandat : doctrine, membres successifs, objectifs et bilan (historique des gouvernements)."""

    since: int
    orientation: float
    reelected: bool
    doctrine: Doctrine
    until: int | None = None
    members: list = field(default_factory=list)   # dictionnaires : rôle, personne, dates, salaire, objectif
    objectives_met: float = 0.0
    record: dict = field(default_factory=dict)    # bilan par portefeuille (« atteint », « partiel », « manqué »)

    @property
    def label(self) -> str:
        return orientation_label(self.orientation)


@dataclass
class Decision:
    """Une entrée du journal des décisions de l'État."""

    month: int
    author: str           # fonction (Présidence, ministère, service)
    person: str           # nom d'affichage de l'auteur, ou du service
    decision: str
    motive: str
    amount: float = 0.0
    financing: str = ""
    note: str = ""        # conflit d'intérêts éventuel


SERVICES = ("finances", "emploi", "equipement", "concurrence", "education")
SERVICE_LABELS = {
    "finances": "Finances publiques",
    "emploi": "Emploi et formation",
    "equipement": "Équipement et infrastructures",
    "concurrence": "Concurrence et participations",
    "education": "Éducation",
}
# besoin mesuré de chaque service, en unités par mois (docs/ETAT.md)
SERVICE_NEEDS = {
    "finances": "ménages à suivre (impôt, allocation)",
    "emploi": "chômeurs à accompagner",
    "equipement": "travaux (entretien et investissement), en salaires de référence",
    "concurrence": "secteurs à surveiller et entreprises publiques à suivre",
    "education": "ménages dont la génération suivante est à scolariser",
}
# composition des postes de chaque service par métier ; « agent » : poste généraliste (les
# enseignants de l'éducation publique sont surtout des agents généralistes, novembre 2026)
SERVICE_TRADES = {
    "finances": {"gestionnaire": 0.45, "financier": 0.35, "agent": 0.20},
    "emploi": {"agent": 0.40, "gestionnaire": 0.35, "commercial": 0.25},
    "equipement": {"ingenieur": 0.35, "technicien": 0.35, "batiment": 0.30},
    "concurrence": {"financier": 0.40, "gestionnaire": 0.30, "ingenieur": 0.30},
    "education": {"agent": 0.55, "ingenieur": 0.15, "technicien": 0.15, "gestionnaire": 0.15},
}
GENERIC_PUBLIC_TRADE = "agent"


@dataclass
class PublicAgent:
    """Un agent de la fonction publique : un seul service, un métier de poste, un grade."""

    person_id: str
    service: str
    trade: str
    grade: int
    since: int            # entrée dans la fonction publique (mois interne)

    def role(self) -> str:
        return f"{SERVICE_LABELS[self.service]} · {self.trade} · grade {self.grade}"


@dataclass
class ServiceState:
    """Situation mensuelle d'un service : besoin, postes, recrutements, départs, production."""

    key: str
    need: float = 0.0             # besoin du mois (unités du service)
    agents_needed: float = 0.0    # besoin en agents (besoin / capacité d'un agent)
    authorized: int = 0           # postes ouverts par le ministre des Services publics
    posts: dict = field(default_factory=dict)  # métier -> postes ouverts
    staff: int = 0
    vacancies: int = 0
    applications: int = 0
    hires: int = 0
    departures: int = 0
    layoffs: int = 0
    wage_bill: float = 0.0
    coverage: float = 0.0         # capacité / besoin (borné à 2)
    output: float = 0.0           # production du mois (unités servies)


@dataclass
class PublicHolding:
    """Participation de l'État dans une entreprise : origine, mandat, contrôle, décisions."""

    company_id: str
    origin: str               # « création », « nationalisation », « restructuration », « résolution bancaire », « participation »
    since: int
    controlled: bool = False
    mandate: str = ""
    director: str | None = None
    previous_traits: dict = field(default_factory=dict)  # traits de l'entreprise avant le mandat public
    invested: float = 0.0     # capital apporté et titres achetés (cumul)
    proceeds: float = 0.0     # produits de cession (cumul)
    recapitalizations: int = 0
    decision: str = "garder"  # « garder », « vendre »
    motive: str = ""
    to_sell: float = 0.0      # titres restant à vendre
    sell_lot: float = 0.0     # titres mis en vente par mois
    result: str = "en cours"  # « en cours », « privatisée », « faillite », « absorbée »


@dataclass
class MonopolyCase:
    """Dossier d'un problème de monopole instruit par le service de la Concurrence."""

    sector: str
    opened: int
    work: float = 0.0         # instruction accomplie (mois d'un service au complet)
    status: str = "instruction"  # « instruction », « non financé », « décidé »
    response: str = ""        # « attente », « regulation », « entreprise_publique », « nationalisation »
    target: str | None = None
    motive: str = ""
    decided: int | None = None


RESPONSE_LABELS = {
    "attente": "attendre l'entrée de concurrents",
    "regulation": "réguler les prix (pas de marge de monopole)",
    "entreprise_publique": "créer une entreprise publique",
    "nationalisation": "prendre le contrôle de l'entreprise dominante",
}


TAX_LEVERS = ("income_tax", "top_tax", "corporate_tax", "vat")
PROGRAMS = ("transferts", "achats", "investissement")
PROGRAM_LABELS = {"transferts": "Transferts aux ménages", "achats": "Achats de fonctionnement",
                  "investissement": "Investissement public"}
# garde-fou technique des facteurs d'impôt votés (taux = réglage de base × facteur, au plus 100 %)
TAX_FACTOR_BOUNDS = (0.3, 2.5)
# catégories du livre mensuel de trésorerie (recettes > 0, dépenses < 0)
REVENUE_CATEGORIES = ("impot_revenu", "tva", "impot_societes", "retenue_interets", "dividendes",
                      "profit_banque_centrale")
FLOW_LABELS = {
    "impot_revenu": "Impôt sur le revenu", "tva": "TVA", "impot_societes": "Impôt sur les bénéfices",
    "retenue_interets": "Retenue sur intérêts", "dividendes": "Dividendes reçus",
    "profit_banque_centrale": "Profit de la banque centrale",
    "cessions_offres_et_boni": "Offres publiques, boni de liquidation", "cessions_titres": "Cessions de titres",
    "salaires_cabinet": "Salaires du cabinet", "salaires_agents": "Salaires des agents",
    "allocations": "Allocations chômage", "transferts": "Transferts", "achats": "Achats",
    "investissement": "Investissement", "entretien": "Entretien du capital public",
    "formation": "Formations financées", "interets": "Intérêts de la dette",
    "acquisitions_financieres": "Acquisitions financières (titres, capital)",
    "arrieres_payes": "Arriérés réglés",
}


@dataclass
class BudgetLaw:
    """Loi de finances : prévue par le ministre de l'Économie, arbitrée par la présidence.

    Montants annuels, exécutés par douzièmes ; ``lines`` : personnel, prestations, intérêts et
    programmes ; le solde prévu exclut les opérations financières (titres, capital).
    """

    month: int
    kind: str                   # « provisoire », « initiale », « rectificative »
    motive: str
    expected_revenue: float
    lines: dict
    planned_balance: float
    balance_goal: float
    gdp: float
    unemployment: float
    adjustments: list = field(default_factory=list)   # décisions d'arbitrage (texte)
    excess_treasury: float = 0.0     # trésorerie au-delà de la réserve, rendue dans l'année
    tax_relief: float = 0.0          # allègement exceptionnel des impôts votés (libéral, centre)

    @property
    def financing_need(self) -> float:
        """Déficit prévu que la trésorerie rendue ne couvre pas : à emprunter."""
        return max(0.0, -self.planned_balance - self.excess_treasury)

    @property
    def spending(self) -> float:
        return sum(self.lines.values())


@dataclass
class MonthBook:
    """Livre de trésorerie du mois : rapproche la variation de la trésorerie avec les flux et
    celle de la dette avec les titres émis et remboursés."""

    cash_start: float = 0.0
    debt_start: float = 0.0
    flows: dict = field(default_factory=dict)        # catégorie -> montant (recettes > 0)
    borrowed: float = 0.0
    repaid: float = 0.0
    unfunded: dict = field(default_factory=dict)     # ligne -> dépense prévue non financée ce mois
    lapsed: dict = field(default_factory=dict)       # ligne -> crédits financés mais non consommés

    def add(self, category: str, amount: float) -> None:
        if amount:
            self.flows[category] = self.flows.get(category, 0.0) + amount

    def note(self, store: dict, line: str, amount: float) -> None:
        if amount > 1e-9:
            store[line] = store.get(line, 0.0) + amount


@dataclass
class StateRecord:
    """Indicateurs mensuels propres au nouvel État (historique séparé des ``MarketRecord``)."""

    month: int
    orientation: float
    president_salary: float = 0.0
    minister_salary: float = 0.0
    cabinet_pay: float = 0.0
    refusals: int = 0
    progress: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    # budget et dette (livraison 3)
    flows: dict = field(default_factory=dict)
    borrowed: float = 0.0
    repaid: float = 0.0
    debt: float = 0.0
    debt_gdp: float = 0.0
    cash: float = 0.0
    unfunded: float = 0.0
    lapsed: float = 0.0
    unfunded_lines: dict = field(default_factory=dict)  # ligne -> dépense prévue non financée
    lapsed_lines: dict = field(default_factory=dict)    # ligne -> crédits non consommés
    arrears: float = 0.0
    cash_residual: float = 0.0
    debt_residual: float = 0.0
    tax_factors: dict = field(default_factory=dict)
    tax_relief: float = 0.0           # allègement exceptionnel en vigueur (part des impôts votés)
    programs: dict = field(default_factory=dict)
    benefit_factor: float = 1.0
    gdp: float = 0.0
    # fonction publique (livraison 4)
    services: dict = field(default_factory=dict)      # service -> indicateurs du mois
    point: float = 0.0
    premiums: dict = field(default_factory=dict)
    tax_backlog: float = 0.0
    trainings: int = 0
    median_wage: float = 0.0          # salaire médian du privé (fin du mois)
    minimum_wage: dict = field(default_factory=dict)  # niveau, ratio au médian, parts au plancher
    industry: dict = field(default_factory=dict)      # participations, régulations, dossiers, opérations


@dataclass
class StateInstitutions:
    """Tout l'état du nouvel État, créé au premier usage du mode ``institutions``."""

    rng: random.Random
    created: int
    doctrine: Doctrine | None = None
    lean: dict = field(default_factory=dict)          # proximité politique de chaque ménage (0–100)
    offices: dict = field(default_factory=dict)       # rôle -> Office
    terms: list = field(default_factory=list)         # GovernmentTerm
    journal: list = field(default_factory=list)       # Decision
    salaries: dict = field(default_factory=dict)      # « presidence », « ministre » : salaire mensuel en vigueur
    pending_raise: dict = field(default_factory=dict)  # hausse décidée, appliquée à la révision suivante
    refusals_since_revision: int = 0
    salary_markup: float = 1.0                        # majoration d'attractivité née des refus
    executive_pay: float = 0.0                        # rémunération moyenne lissée des dirigeants du privé
    cabinet_pay: float = 0.0                          # salaires du cabinet payés ce mois
    history: list = field(default_factory=list)       # StateRecord
    # budget (livraison 3) : décisions durables réappliquées à ``live`` chaque mois
    law: BudgetLaw | None = None
    laws: list = field(default_factory=list)          # lois votées (historique)
    tax_factors: dict = field(default_factory=dict)   # impôts votés / réglages de base
    tax_relief: float = 0.0                           # allègement exceptionnel jusqu'à la loi suivante
    benefit_factor: float = 1.0                       # allocation votée / réglage de base
    programs: dict = field(default_factory=dict)      # programmes votés (part du PIB retardé)
    book: MonthBook = field(default_factory=MonthBook)
    last_revision: int = -1000
    arrears_interest: float = 0.0                     # intérêts dus non payés (jamais effacés)
    arrears_wages: dict = field(default_factory=dict)     # ménage -> salaires dus non payés
    arrears_benefits: dict = field(default_factory=dict)  # ménage -> allocations dues non payées
    executed: dict = field(default_factory=dict)      # exécution cumulée de la loi en cours, par ligne
    # fonction publique (livraison 4)
    agents: dict = field(default_factory=dict)        # ménage -> PublicAgent
    services: dict = field(default_factory=dict)      # service -> ServiceState du mois
    pending_recruits: dict = field(default_factory=dict)  # salariés du privé recrutés, en poste le mois suivant
    point: float = 0.0                                # point d'indice (salaire de base de la grille)
    premiums: dict = field(default_factory=dict)      # métier -> prime (multiplicateur)
    vacancy_months: dict = field(default_factory=dict)  # métier -> mois consécutifs avec postes vacants
    applications_quarter: dict = field(default_factory=dict)  # métier -> candidatures du trimestre
    vacancies_quarter: dict = field(default_factory=dict)     # métier -> postes vacants cumulés du trimestre
    tax_backlog: dict = field(default_factory=dict)   # ménage -> impôt établi non encore recouvré
    estate_backlog: dict = field(default_factory=dict)  # ménage -> droits de succession restant dus (idée 3)
    # vote individuel (idée 4) : revenu réel attendu de chaque ménage au début du mandat ; résultats
    term_income: dict = field(default_factory=dict)
    elections: list = field(default_factory=list)       # dictionnaires : mois, sortant, opposition, part, élu
    training_places: int = 0                          # reconversions financées encore possibles ce mois
    trainings: int = 0                                # reconversions financées ce mois
    # salaire minimum institutionnel (livraison 5)
    minimum_wage: float = 0.0                         # niveau légal mensuel en vigueur
    minimum_wage_revisions: list = field(default_factory=list)  # revalorisations (dictionnaires)
    # politique industrielle (livraison 6)
    holdings: dict = field(default_factory=dict)      # entreprise -> PublicHolding (participations en cours)
    closed_holdings: list = field(default_factory=list)  # participations terminées (privatisées, faillite…)
    cases: list = field(default_factory=list)         # MonopolyCase (dossiers ouverts et décidés)
    diagnostic: dict = field(default_factory=dict)    # secteur -> diagnostic du mois
    monopoly_months: dict = field(default_factory=dict)  # secteur -> mois consécutifs de problème
    regulated: dict = field(default_factory=dict)     # secteur -> mois de début de la régulation
    regulation_clear_months: dict = field(default_factory=dict)  # secteur régulé -> mois sans problème
    industry_month: dict = field(default_factory=dict)  # opérations du mois (comptes)

    @property
    def arrears(self) -> float:
        return self.arrears_interest + sum(self.arrears_wages.values()) + sum(self.arrears_benefits.values())

    def holder(self, role: str) -> str | None:
        office = self.offices.get(role)
        return office.person_id if office else None


def political_lean(rng: random.Random) -> float:
    """Proximité politique d'un ménage (0 libéral, 100 social) : loi triangulaire centrée."""
    return 100.0 * (rng.random() + rng.random()) / 2.0


def proximity(lean: float, orientation: float) -> float:
    return 1.0 - abs(lean - orientation) / 100.0


def candidate_score(role: str, person, lean: float, orientation: float, is_executive: bool) -> float:
    """Score d'un ménage pour une fonction (hypothèse de choix : docs/ETAT.md).

    Proximité politique, expérience de direction (mois de PDG, plafonnée à dix ans ; un PDG en
    poste compte double), métier et compétences liés au portefeuille, carrière.
    """
    leadership = min(1.0, person.leadership_experience / 120.0)
    if is_executive:
        leadership = min(1.0, 2.0 * leadership + 0.1)
    career = min(1.0, person.career_months / 240.0)
    near = proximity(lean, orientation)
    if role == "presidence":
        return 0.45 * near + 0.30 * leadership + 0.15 * career + 0.10 * min(1.0, person.skill / 2.0)
    trades = OFFICE_TRADES[role]
    trade = 1.0 if person.trade in trades else 0.5 if (person.job_trade or "") in trades else 0.0
    skills = max((person.skills.get(sector, 1.0) for sector in OFFICE_SECTORS[role]), default=1.0) / 5.0
    return 0.35 * near + 0.20 * leadership + 0.25 * trade + 0.10 * skills + 0.10 * career


def vote_logit(config, unemployment_gap: float, inflation_gap: float, record: float | None) -> float:
    """Logit de réélection du sortant : indice de misère (comme ``elect_government``), plus un
    effet modeste du bilan des objectifs (``vote_record_weight`` × (part atteinte − 0,5))."""
    misery = 100.0 * (unemployment_gap + inflation_gap)
    logit = config.vote_incumbent_advantage - config.vote_misery_sensitivity * misery
    if record is not None:
        logit += config.vote_record_weight * (record - 0.5)
    return logit


@dataclass
class Voter:
    """Ce qu'un ménage sait au moment de voter (idée 4, novembre 2026) : sa proximité politique,
    son rang de fortune (0 le plus pauvre, 1 le plus riche), l'évolution de son revenu réel
    attendu depuis le début du mandat et la fréquence récente de ses mois sans emploi."""

    lean: float
    wealth_rank: float
    income_growth: float
    unemployment: float


def effective_lean(voter: Voter, config) -> float:
    """Position du ménage une fois ses intérêts anticipés pris en compte : un ménage aisé penche
    vers la droite (impôts sur le capital plus bas), un ménage modeste vers la gauche (transferts,
    salaire minimum), de ``vote_interest_weight`` × 100 × (0,5 − rang de fortune) points. Aucun
    déterminisme : la proximité tirée pour chaque ménage reste le premier facteur."""
    return voter.lean + config.vote_interest_weight * 100.0 * (0.5 - voter.wealth_rank)


def challenger_orientation(voters: list[Voter], incumbent: float, config) -> float:
    """Programme de l'opposition : position médiane des ménages du camp opposé au sortant."""
    leans = sorted(effective_lean(v, config) for v in voters)
    camp = [x for x in leans if x < 50.0] if incumbent >= 50.0 else [x for x in leans if x >= 50.0]
    if not camp:
        return 30.0 if incumbent >= 50.0 else 70.0
    return camp[len(camp) // 2] if len(camp) % 2 else 0.5 * (camp[len(camp) // 2 - 1] + camp[len(camp) // 2])


def incumbent_support(voter: Voter, incumbent: float, challenger: float, config, record: float | None) -> float:
    """Probabilité qu'un ménage vote pour le sortant : logit = avantage du sortant + sensibilité ×
    (proximité au sortant − proximité à l'opposition) + poids × croissance de son revenu réel
    (bornée à ±50 %) − poids × fréquence de ses mois sans emploi + bilan des objectifs."""
    lean = effective_lean(voter, config)
    logit = (
        config.vote_incumbent_advantage
        + config.vote_ideology_sensitivity * (proximity(lean, incumbent) - proximity(lean, challenger))
        + config.vote_income_weight * max(-0.5, min(0.5, voter.income_growth))
        - config.vote_unemployment_weight * max(0.0, min(1.0, voter.unemployment))
    )
    if record is not None:
        logit += config.vote_record_weight * (record - 0.5)
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))


def individual_election(rng: random.Random, incumbent: float, voters: list[Voter], config,
                        record: float | None) -> tuple[float, bool, float, float]:
    """Élection par les ménages (idée 4) : part des voix du sortant = moyenne des probabilités
    individuelles, plus un écart national (``vote_swing_sd``, un tirage) ; réélu au-delà de la
    moitié. L'orientation élue est celle du sortant ou de l'opposition, plus un écart propre au
    nouveau gouvernement (un tirage), réfléchie aux bornes. Retourne (orientation, réélection,
    part des voix du sortant, programme de l'opposition)."""
    challenger = challenger_orientation(voters, incumbent, config)
    share = sum(incumbent_support(v, incumbent, challenger, config, record) for v in voters) / max(1, len(voters))
    share = max(0.0, min(1.0, share + rng.gauss(0.0, config.vote_swing_sd)))
    reelected = share > 0.5
    base = incumbent if reelected else challenger
    return reflect(base + rng.gauss(0.0, 5.0)), reelected, share, challenger


def elect_orientation(rng: random.Random, incumbent_orientation: float | None, config,
                      unemployment_gap: float, inflation_gap: float,
                      record: float | None) -> tuple[float, bool]:
    """Orientation élue et réélection du sortant (nouveau mode).

    Même vote économique que ``politics.elect_government`` (mêmes paramètres), avec deux
    différences : le bilan des objectifs entre modestement dans le logit, et l'orientation est
    réfléchie aux bornes au lieu d'être écrêtée (une réélection libérale ne reste pas collée à 0).
    Sans sortant ou sans vote économique : loi triangulaire, comme l'ancien tirage.
    """
    if incumbent_orientation is None or not config.economic_voting:
        return 100.0 * (rng.random() + rng.random()) / 2.0, False
    logit = vote_logit(config, unemployment_gap, inflation_gap, record)
    if rng.random() < 1.0 / (1.0 + math.exp(-logit)):
        return reflect(incumbent_orientation + rng.gauss(0.0, 5.0)), True
    desired = 50.0 + config.vote_ideology_pull * 100.0 * (unemployment_gap - inflation_gap)
    side = -1.0 if incumbent_orientation >= 50.0 else 1.0
    return reflect(desired + 15.0 * side + rng.gauss(0.0, 12.0)), False
