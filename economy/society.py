"""Lot V (12 novembre 2026), première partie : ménages, État et société.

Six mécanismes, chacun derrière son réglage, désactivés par défaut (aucun calcul, aucun tirage,
aucun attribut ajouté sans eux). L'état vit dans ``simulation.society`` (``SocietyState``), créé au
premier usage et partagé avec ``economy/ventures.py`` ; les tirages viennent d'un générateur dédié.

- **Surendettement** (``personal_bankruptcy``) : une commission examine les ménages en impayé de
  crédit à la consommation dont la charge de dette dépasse une part de leur revenu. Elle saisit
  leur trésorerie au-delà d'un mois de salaire minimum, efface le reste (perte des banques) et
  les interdit de crédit pendant plusieurs années.
- **Philanthropie** (``philanthropy``) : les ménages très riches donnent à trois fondations (école,
  hôpital, presse), qui dépensent les dons auprès des entreprises ; l'école relève la compétence
  des nouveaux ménages, l'hôpital baisse la mortalité, la presse soutient les médias. Le donateur
  gagne de la renommée (et de la notoriété si le lot K est activé).
- **Loterie d'État** (``state_lottery``) : la mise ne dépend pas du revenu et les ménages modestes
  jouent davantage ; une part des mises va à l'État, le reste à une cagnotte qu'un seul joueur
  emporte.
- **Addictions** (``addictive_goods``) : des entreprises de tabac et d'alcool (secteur « vice »)
  vendent à des ménages dépendants une quantité qui ne dépend pas du prix ; accise et TVA vont à
  l'État ; leurs achats technologiques renforcent le pouvoir addictif des produits consommés.
  Les prix tiennent compte des coûts ; l'exposition cumulée coûte des soins et relève la mortalité.
- **Manifestations** (``social_unrest``) : la tension suit le chômage, les inégalités de patrimoine
  et la baisse du salaire réel ; une manifestation fait perdre du travail, une émeute détruit des
  stocks, et le gouvernement concède des transferts.
- **Grands travaux** (``public_works``) : route, réseau et port, chantiers de plusieurs années payés
  aux entreprises ; un ouvrage achevé relève la productivité de tous selon son état, qui se
  dégrade sans entretien.

Tous les paiements sont de vrais transferts : la monnaie est conservée (les comptes de la loterie,
des fondations et des fonds spéculatifs entrent dans ``Ledger.nonbank_cash``).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from economy.government import unemployment_benefit_entitlement
from economy.institutions import display_name
from economy.intersector import household_suppliers, pay_business_suppliers
from economy.models import Company
from economy.payments import transfer_cash
from economy.wealth import household_net_worth, share_prices

SOCIETY_SEED_OFFSET = 71480051  # générateur dédié du lot V
SWITCHES = (
    "personal_bankruptcy", "philanthropy", "state_lottery", "addictive_goods",
    "social_unrest", "public_works",
)
VENTURE_SWITCHES = (
    "franchising", "brand_marketing", "worker_buyouts", "short_selling",
    "mortgage_securitization", "crowdfunding",
)
ALL_SWITCHES = SWITCHES + VENTURE_SWITCHES

VICE_SECTOR = "vice"
VICE_LABEL = "Tabac & alcool"
MARKETING_SECTOR = "marketing"
MARKETING_LABEL = "Marketing"
SPECIAL_CAPITAL = 60000.0       # capital de départ d'une entreprise de tabac ou d'une agence (indexé sur les salaires)
FOUNDER_STAKE = 0.5             # part de sa trésorerie qu'un fondateur engage au plus
FOUNDER_MINIMUM = 0.25          # part du capital de départ sans laquelle il renonce
REFOUNDING_MONTHS = 12          # délai avant une nouvelle fondation quand une entreprise a disparu
# --- surendettement
LIVING_ALLOWANCE_MONTHS = 1.0   # reste à vivre laissé au ménage, en mois de salaire minimum
# --- philanthropie
CAUSES = ("ecole", "hopital", "presse")
CAUSE_LABELS = {"ecole": "Fondation pour l'école", "hopital": "Fondation hospitalière",
                "presse": "Fondation pour la presse"}
FOUNDATION_SPENDING = 1.0 / 24.0  # part de sa trésorerie qu'une fondation dépense chaque mois
FOUNDATION_HALF = 0.01          # dépense mensuelle par ménage (en salaires de référence) qui donne la moitié de l'effet
SCHOOL_GAIN = 0.3               # qualité de l'éducation ajoutée au plus par les écoles des fondations
HOSPITAL_GAIN = 0.15            # baisse maximale de la mortalité par les hôpitaux des fondations
FAME_FADE = 0.02                # part de la renommée d'un donateur oubliée chaque mois
FAME_YEARS = 10.0               # dons (en années de salaire de référence) qui donnent une renommée complète
FAME_NOTORIETY = 0.5            # notoriété (lot K) d'un donateur de renommée complète
GIFT_CASH_LIMIT = 0.25          # part de sa trésorerie qu'un donateur donne au plus en un mois
FAME_OPINION = 0.02             # hausse mensuelle de l'opinion (lot K) d'un donateur de renommée complète
SCHOOL_SPLIT = {"services": 0.7, "technology": 0.3}
HOSPITAL_SPLIT = {"services": 0.8, "technology": 0.2}
# --- loterie
LOTTERY_CASH_LIMIT = 0.25       # part de sa trésorerie qu'un joueur mise au plus
POOR_PLAY = 1.5                 # goût du jeu du plus modeste / celui du plus riche = POOR_PLAY / (POOR_PLAY − 1)
# --- addictions
VICE_UNITS = 1.0                # unités voulues par mois à dépendance complète
VICE_OUTPUT = 150.0             # unités qu'un salarié vend par mois
VICE_THRESHOLD = 0.3            # penchant en deçà duquel un ménage ne consomme pas au départ
VICE_START = 1.0                # dépendance de départ du ménage au penchant le plus fort
VICE_REINFORCE = 0.02           # part de l'écart entre la dépendance et son niveau d'équilibre comblée par mois
VICE_WITHDRAWAL = 0.05          # baisse mensuelle de la dépendance d'un consommateur privé de produit
VICE_QUIT_PRICE = 0.01          # baisse mensuelle de la dépendance par doublement du prix de référence
VICE_STRESS_MONTHS = 6          # mois de chômage après lesquels un ménage vulnérable commence à consommer
VICE_INITIATION = 0.05          # dépendance d'un ménage qui commence (au plus)
VICE_DAMAGE = 0.002             # dégâts de santé par mois à dépendance complète (0,5 en vingt ans)
VICE_HEALING = 0.001            # dégâts réparés par mois d'abstinence
VICE_CARE_FROM = 0.05           # dégâts à partir desquels des soins sont nécessaires
VICE_CASH_LIMIT = 0.5           # part de sa trésorerie qu'un ménage dépendant y consacre au plus
VICE_PRICE_UP = 0.01            # hausse mensuelle du prix d'un fabricant dont les volumes tiennent
VICE_PRICE_DOWN = 0.03          # baisse mensuelle quand ses volumes reculent
VICE_PRICE_BAND = (0.5, 2.5)    # prix d'un fabricant / prix de référence
# --- manifestations
UNREST_SPEED = 0.15             # part de l'écart entre la pression et la tension comblée par mois
UNREST_UNEMPLOYMENT_SCALE = 0.10  # points de chômage au-delà du seuil qui valent une pression de 1
UNREST_INEQUALITY_SCALE = 0.20  # points de patrimoine des 10 % au-delà du seuil qui valent une pression de 1
UNREST_WAGE_SCALE = 0.10        # baisse du salaire réel sur un an qui vaut une pression de 1
UNREST_PROTEST = 1.0            # tension à partir de laquelle une manifestation éclate
UNREST_RIOT = 2.0               # tension à partir de laquelle elle tourne à l'émeute
UNREST_REST_MONTHS = 6          # délai entre deux manifestations
UNREST_RELIEF = 0.5             # part de la tension qui retombe après une concession
UNREST_MAX_CONCESSIONS = 3      # concessions qu'un même gouvernement accepte
RIOT_DAMAGE = 0.02              # part des stocks d'alimentation et de biens détruite par une émeute
PROTEST_OPINION = 0.05          # baisse de l'opinion sur la présidence (lot K) par manifestation
# --- grands travaux
WORKS = (
    # (clé, nom, poids du coût, durée du chantier en mois)
    ("route", "Route nationale", 1.0, 24),
    ("reseau", "Réseau électrique et numérique", 0.75, 18),
    ("port", "Port de commerce", 1.5, 36),
)
WORKS_SPLIT = {"real_estate": 0.4, "materials": 0.3, "energy": 0.15, "technology": 0.15}
WORKS_UPKEEP_SPLIT = {"real_estate": 0.5, "services": 0.5}
WORKS_UPKEEP_RATE = 0.002       # entretien mensuel nécessaire, en part du coût de l'ouvrage
WORKS_LAUNCH_MONTHS = 12        # délai entre deux lancements
WORKS_RUIN = 0.2                # état en deçà duquel un ouvrage est hors service (à reconstruire)
PORT_TRADE_GAIN = 0.5           # hausse de l'ouverture commerciale (lot J) par un port en parfait état


@dataclass
class SocietyState:
    """État propre au lot V, créé au premier usage d'un mécanisme activé."""

    random: random.Random
    traits: dict = field(default_factory=dict)       # ménage -> (goût du jeu, générosité, penchant)
    # surendettement
    bans: dict = field(default_factory=dict)         # ménage -> mois de fin de l'interdiction de crédit
    relief: list = field(default_factory=list)       # dossiers traités
    # philanthropie
    foundations: dict = field(default_factory=dict)  # cause -> compte de la fondation
    causes: dict = field(default_factory=dict)       # donateur -> cause choisie
    gifts: dict = field(default_factory=dict)        # donateur -> dons cumulés
    fame: dict = field(default_factory=dict)         # donateur -> renommée (0 à 1)
    foundation_effort: dict = field(default_factory=dict)  # cause -> dépense mensuelle lissée par ménage
    # loterie
    lottery: Company | None = None
    winners: list = field(default_factory=list)
    # addictions
    dependence: dict = field(default_factory=dict)   # ménage -> dépendance (0 à 1)
    damage: dict = field(default_factory=dict)       # ménage -> dégâts de santé cumulés (0 à 1)
    vice_units: dict = field(default_factory=dict)   # fabricant -> unités vendues l'avant-dernier mois
    foundings: dict = field(default_factory=dict)    # secteur -> mois de la dernière fondation
    # manifestations
    tension: float = 0.0
    last_protest: int = -10 ** 6
    concessions: int = 0
    concession_term: int = -1                        # gouvernement (mois de formation) qui a concédé
    protests: list = field(default_factory=list)
    # grands travaux
    works: list = field(default_factory=list)        # ouvrages (chantiers et ouvrages achevés)
    last_launch: int = -10 ** 6
    # --- economy/ventures.py
    brands: dict = field(default_factory=dict)       # entreprise -> notoriété de marque (stock, en euros)
    franchises: dict = field(default_factory=dict)   # franchisé -> {franchiseur, mois, droits, redevances}
    cooperatives: dict = field(default_factory=dict)  # coopérative -> {origine, mois, salariés, capital}
    rosters: dict = field(default_factory=dict)      # entreprise -> {salarié: dernier mois d'emploi}
    last_franchise: int = -10 ** 6
    hedge_funds: list = field(default_factory=list)
    hedge_attempt: int = -10 ** 6
    shorts: list = field(default_factory=list)       # positions closes (mesures seules)
    pools: dict = field(default_factory=dict)        # banque -> {"encours": x, "porteurs": {ménage: montant}}
    crowd: dict = field(default_factory=dict)        # entreprise -> prêts participatifs levés (cumul)
    crowd_lost: set = field(default_factory=set)     # emprunteurs en faillite déjà comptés
    log: list = field(default_factory=list)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)

    def accounts(self) -> list:
        """Porteurs de monnaie du lot V hors ménages et entreprises (``Ledger.nonbank_cash``)."""
        holders = list(self.foundations.values())
        if self.lottery is not None:
            holders.append(self.lottery)
        holders.extend(self.hedge_funds)
        return holders

    def securitized(self) -> float:
        """Titres adossés en circulation : passif des banques (``Ledger.deposits``)."""
        return sum(pool["encours"] for pool in self.pools.values())


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class SocietyMixin:
    """Surendettement, philanthropie, loterie, addictions, manifestations, grands travaux (lot V)."""

    # ------------------------------------------------------------------ état
    def _society_enabled(self) -> bool:
        live = self.live
        return any(getattr(live, name) for name in ALL_SWITCHES)

    def _society_state(self) -> SocietyState:
        state = getattr(self, "society", None)
        if state is None:
            state = self.society = SocietyState(random.Random(self.config.seed + SOCIETY_SEED_OFFSET))
        return state

    def _society_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._society_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    def _society_log(self, kind: str, text: str, **details) -> None:
        self._society_state().log.append({"mois": self.month, "type": kind, "texte": text, **details})

    def _society_traits(self, person_id: str) -> tuple:
        """(goût du jeu, générosité, penchant pour le tabac et l'alcool), tirés une fois dans [0, 1]."""
        state = self._society_state()
        traits = state.traits.get(person_id)
        if traits is None:
            rng = state.random
            traits = state.traits[person_id] = (rng.random(), rng.random(), rng.random())
        return traits

    def _society_month_start(self) -> None:
        """Phase 1, après la formation du gouvernement : traits des nouveaux ménages (dans l'ordre
        de la population), fin des interdictions de crédit, fondation des entreprises propres au
        lot (tabac et alcool, agences de marketing, fonds spéculatifs)."""
        live = self.live
        state = self._society_state()
        if live.state_lottery or live.philanthropy or live.addictive_goods:
            traits = state.traits
            for person in self.people:
                if person.id not in traits:
                    self._society_traits(person.id)
        if state.bans:
            people = None
            for person_id in [pid for pid, until in state.bans.items() if self.month >= until]:
                del state.bans[person_id]
                people = people if people is not None else {p.id: p for p in self.people}
                person = people.get(person_id)
                if person is not None:
                    person.credit_banned = False
        if live.addictive_goods:
            self._found_special_firms(VICE_SECTOR, VICE_LABEL, int(live.addiction_firms),
                                      self._vice_reference_price())
        if live.brand_marketing:
            self._found_special_firms(MARKETING_SECTOR, MARKETING_LABEL, int(live.marketing_agencies), 1.0)
        if live.short_selling:
            self._ensure_hedge_funds()

    def _society_live_effects(self) -> None:
        """Phase 1, après le calcul du gain du capital public : les ouvrages achevés relèvent la
        productivité de tous (``public_works_gain`` × leur état), et un port ouvre le commerce
        extérieur (lot J)."""
        state = getattr(self, "society", None)
        if state is None or not state.works:
            return
        live = self.live
        gain = max(0.0, live.public_works_gain)
        for work in state.works:
            if work["fin"] is None:
                continue
            live.public_capital_bonus += gain * work["etat"]
            if work["type"] == "port" and live.foreign_trade:
                live.trade_openness = min(1.0, live.trade_openness * (1.0 + PORT_TRADE_GAIN * work["etat"]))

    def _society_succession(self, person) -> None:
        """Succession d'un ménage : l'héritier a ses propres traits, n'a ni la dépendance ni les
        dégâts de santé du défunt, ni son interdiction de crédit, ni sa renommée de donateur."""
        state = self.society
        pid = person.id
        if pid in state.traits:
            rng = state.random
            state.traits[pid] = (rng.random(), rng.random(), rng.random())
        state.dependence.pop(pid, None)
        state.damage.pop(pid, None)
        state.causes.pop(pid, None)
        state.fame.pop(pid, None)
        if state.bans.pop(pid, None) is not None:
            person.credit_banned = False

    def _society_mortality(self, person, chance: float) -> float:
        """Probabilité mensuelle de décès d'un ménage : relevée par les dégâts du tabac et de
        l'alcool, abaissée par les hôpitaux des fondations."""
        state = self.society
        damage = state.damage.get(person.id, 0.0)
        if damage > 0.0:
            chance *= 1.0 + max(0.0, self.live.addiction_mortality_gain) * damage
        effort = state.foundation_effort.get("hopital", 0.0)
        if effort > 0.0:
            chance *= 1.0 - HOSPITAL_GAIN * effort / (effort + FOUNDATION_HALF)
        return min(1.0, chance)

    def _society_school_quality(self) -> float:
        """Qualité de l'éducation ajoutée par les écoles des fondations (0 sans dons)."""
        state = getattr(self, "society", None)
        if state is None:
            return 0.0
        effort = state.foundation_effort.get("ecole", 0.0)
        return SCHOOL_GAIN * effort / (effort + FOUNDATION_HALF) if effort > 0.0 else 0.0

    def _society_anchored(self) -> set:
        """Ménages qui ne peuvent pas déménager dans une autre région : ils ont prêté des titres
        à un fonds spéculatif, détiennent ses parts ou des titres adossés à des prêts."""
        state = getattr(self, "society", None)
        if state is None:
            return set()
        anchored: set = set()
        for pool in state.pools.values():
            anchored.update(pool["porteurs"])
        for fund in state.hedge_funds:
            anchored.update(fund.units)
            for position in fund.positions.values():
                anchored.update(position["preteurs"])
        return anchored

    # ------------------------------------------------------------------ entreprises propres au lot
    def _found_special_firms(self, sector: str, label: str, wanted: int, price: float) -> None:
        """Des ménages aisés fondent les entreprises d'un secteur propre au lot (tabac et alcool,
        marketing) jusqu'à en compter ``wanted``, chacun avec sa propre trésorerie : le plus riche
        qui n'est ni au gouvernement ni déjà fondateur apporte au plus ``FOUNDER_STAKE`` de sa
        trésorerie, dans la limite du capital de départ, et renonce sous ``FOUNDER_MINIMUM``.
        Une entreprise disparue n'est remplacée qu'après ``REFOUNDING_MONTHS`` mois."""
        state, live = self.society, self.live
        active = sum(1 for c in self.companies if c.active and c.sector == sector)
        if active >= wanted or self.month - state.foundings.get(sector, -10 ** 6) < REFOUNDING_MONTHS:
            return
        state.foundings[sector] = self.month
        capital = SPECIAL_CAPITAL * self._wage_scale()
        cabinet = {office.person_id for office in self.institutions.offices.values()}
        rich = sorted(
            (p for p in self.people if p.id not in cabinet and not p.founded_company),
            key=lambda p: (-p.cash, p.id),
        )
        rng = state.random
        created = sum(1 for c in self.companies if c.sector == sector)
        for founder in rich[: wanted - active]:
            contribution = min(capital, FOUNDER_STAKE * max(0.0, founder.cash))
            if contribution < FOUNDER_MINIMUM * capital:
                break
            created += 1
            shares = 1000.0
            floated = shares * live.founder_public_share_fraction
            company = Company(
                id=f"{sector}_{self.month}_{created}",
                name=f"{label} {display_name(founder.id).split(' ', 1)[-1]} ({self.month})",
                sector=sector,
                cash=contribution,
                wage_offer=max(self.wage_reference, live.minimum_wage, 1.0),
                target_employees=1 if sector == VICE_SECTOR else 2,
                price=max(price, 0.01),
                stock_price=max(1.0, contribution / shares),
                previous_stock_price=max(1.0, contribution / shares),
                shares_outstanding=shares,
                treasury_shares=floated,
                productivity_trait=rng.uniform(0.75, 1.35),
                quality_trait=rng.uniform(
                    1.0 - live.quality_trait_variance, 1.0 + live.quality_trait_variance
                ),
                risk_tolerance=founder.risk_tolerance,
                pricing_aggression=rng.random(),
                reinvestment_preference=rng.random(),
                innovation=rng.random(),
                executive_id=founder.id,
                ceo_experience=max(1, founder.leadership_experience),
                founder_id=founder.id,
                construction_pipeline=[],
            )
            founder.cash -= contribution
            founder.founded_company = True
            founder.shares[company.id] = shares - floated
            self.companies.append(company)
            self._society_count(f"fondations_{sector}")
            self._society_log("fondation", f"{display_name(founder.id)} fonde {company.name}")

    # ------------------------------------------------------------------ V1 : surendettement
    def _ban_from_credit(self, person, motive: str, debt: float, repaid: float) -> None:
        state = self.society
        state.bans[person.id] = self.month + max(1, int(self.live.credit_ban_months))
        person.credit_banned = True
        state.relief.append({
            "mois": self.month, "menage": person.id, "motif": motive,
            "dette": debt, "rembourse": repaid, "efface": debt - repaid,
        })

    def _debt_commission(self, defaults: dict, f) -> None:
        """Commission de surendettement, après les mensualités du crédit à la consommation et
        avant les nouveaux prêts.

        Un ménage qui vient de faire défaut (règle historique : sa dette est déjà passée en perte)
        est interdit de crédit. Un ménage en impayé dont la charge mensuelle (crédit à la
        consommation et prêt immobilier) dépasse ``debt_relief_service_share`` de son revenu
        dépose un dossier : sa trésorerie au-delà d'un mois de salaire minimum rembourse la
        banque, le reste du crédit à la consommation est effacé (perte de la banque), et il est
        interdit de crédit pendant ``credit_ban_months`` mois. Le prêt immobilier garde ses
        propres règles (saisie du logement)."""
        live = self.live
        self._society_state()
        people = {p.id: p for p in self.people} if defaults else {}
        for person_id, debt in defaults.items():
            person = people.get(person_id)
            if person is not None:
                self._ban_from_credit(person, "défaut", debt, 0.0)
                self._society_count("interdits_de_credit")
        term = max(1, live.consumer_credit_term_months)
        share = max(0.0, live.debt_relief_service_share)
        allowance = LIVING_ALLOWANCE_MONTHS * max(live.minimum_wage, 0.0)
        for person in self.people:
            debt = person.consumer_debt
            if debt <= 0.0 or person.consumer_arrears <= 0 or person.credit_banned:
                continue
            income = max(0.0, person.income_this_turn)
            service = debt * (person.consumer_rate + 1.0 / term) + person.mortgage_payment
            if service <= share * income:
                continue
            repaid = min(debt, max(0.0, person.cash - allowance))
            if repaid > 0.0:
                person.cash -= repaid
                person.expenses_this_turn += repaid
                person.cumulative_expenses += repaid
            written = debt - repaid
            person.consumer_debt = 0.0
            person.consumer_arrears = 0
            # les comptes des banques suivent : remboursement, puis perte du solde
            self._settle_household_accounts("consumer", {}, {person.id: written})
            if written > 0.0:
                self._write_off_household_loan(f"consumer:{person.id}", written)
            self._ban_from_credit(person, "dossier", debt, repaid)
            f.consumer_defaults += 1
            self._society_count("dossiers_surendettement")
            self._society_count("interdits_de_credit")
            self._society_count("dettes_effacees", written)
            self._society_count("dettes_recouvrees", repaid)

    # ------------------------------------------------------------------ V2 : philanthropie
    def _foundation(self, cause: str) -> Company:
        state = self.society
        account = state.foundations.get(cause)
        if account is None:
            account = state.foundations[cause] = Company(
                f"fondation_{cause}", CAUSE_LABELS[cause], "foundation", 0.0, 0.0
            )
        return account

    def _philanthropy(self, f) -> None:
        """Dons du mois et dépenses des fondations (phase 4).

        Donateur : ménage dont la fortune nette dépasse ``philanthropy_wealth_multiple`` × la
        fortune moyenne. Il donne ``philanthropy_giving_rate`` × sa générosité (trait) de
        l'excédent, au plus ``GIFT_CASH_LIMIT`` de sa trésorerie, à la cause qu'il a choisie une
        fois pour toutes. Chaque fondation dépense
        ``FOUNDATION_SPENDING`` de sa trésorerie : l'école et l'hôpital achètent des services et
        de la technologie aux entreprises, la presse verse ses fonds aux médias qui paraissent
        (lot M ; sans média, elle garde ses fonds). L'effet d'une fondation suit sa dépense
        lissée par ménage, à rendements décroissants."""
        live = self.live
        state = self._society_state()
        people = self.people
        if not people:
            return
        prices = share_prices(self.companies)
        worth = [household_net_worth(p, self.companies, live, prices) for p in people]
        mean = sum(worth) / len(people)
        threshold = max(0.0, live.philanthropy_wealth_multiple) * max(mean, 0.0)
        rate = max(0.0, live.philanthropy_giving_rate)
        wage = max(self.wage_reference, 1.0)
        power = getattr(self, "power", None) if live.personal_popularity else None
        donors = 0
        for person, fortune in zip(people, worth):
            pid = person.id
            fame = state.fame.get(pid)
            if fame is not None:
                fame *= 1.0 - FAME_FADE
            excess = fortune - threshold
            if threshold > 0.0 and excess > 0.0:
                gift = min(rate * self._society_traits(pid)[1] * excess, GIFT_CASH_LIMIT * max(0.0, person.cash))
                if gift >= 1.0:
                    cause = state.causes.get(pid)
                    if cause is None:
                        cause = state.causes[pid] = state.random.choice(CAUSES)
                    transfer_cash(person, self._foundation(cause), gift)
                    person.expenses_this_turn += gift
                    person.cumulative_expenses += gift
                    state.gifts[pid] = state.gifts.get(pid, 0.0) + gift
                    fame = min(1.0, (fame or 0.0) + gift / (FAME_YEARS * 12.0 * wage))
                    donors += 1
                    self._society_count("dons", gift)
            if fame is None:
                continue
            if fame < 1e-4:
                state.fame.pop(pid, None)
                continue
            state.fame[pid] = fame
            if power is not None:
                # lot K : un donateur connu gagne de la notoriété et de l'opinion
                known = power.notoriety.get(pid, 0.0)
                power.notoriety[pid] = max(known, FAME_NOTORIETY * fame)
                self._shift_opinion(pid, FAME_OPINION * fame)
        self._society_count("donateurs", donors)
        for cause in CAUSES:
            account = state.foundations.get(cause)
            spent = 0.0
            if account is not None and account.cash > 1.0:
                budget = FOUNDATION_SPENDING * account.cash
                before = account.cash
                if cause == "presse":
                    outlets = self._publishing_outlets() if getattr(self, "media", None) is not None else []
                    readers = sum(self.media.outlets[c.id].readers for c in outlets) if outlets else 0
                    for medium in outlets:
                        part = budget * self.media.outlets[medium.id].readers / readers if readers else 0.0
                        if part > 0.0:
                            transfer_cash(account, medium, part)
                            medium.revenue += part
                else:
                    split = SCHOOL_SPLIT if cause == "ecole" else HOSPITAL_SPLIT
                    pay_business_suppliers(account, budget, split, self.companies, self.resources, priced=False)
                    account.expenses = 0.0
                spent = before - account.cash
                # dépense d'institutions sans but lucratif au service des ménages : consommation
                f.household_services += spent
                self._society_count(f"depenses_{cause}", spent)
            effort = state.foundation_effort.get(cause, 0.0)
            effort += (spent / (len(people) * wage) - effort) / 12.0
            if effort > 1e-9 or cause in state.foundation_effort:
                state.foundation_effort[cause] = effort

    # ------------------------------------------------------------------ V3 : loterie d'État
    def _lottery(self, f) -> None:
        """Tirage du mois, après les achats des ménages (phase 3).

        Mise d'un ménage : ``lottery_stake_share`` × le salaire de référence × son goût du jeu ×
        (``POOR_PLAY`` − son rang de trésorerie, de 0 pour le plus modeste à 1 pour le plus
        riche), au plus ``LOTTERY_CASH_LIMIT`` de sa trésorerie : le montant ne dépend pas de son
        revenu, il pèse donc plus sur les revenus modestes. ``lottery_payout_share`` des mises
        rejoint la cagnotte, le reste est une recette de l'État. Avec la chance mensuelle
        ``lottery_draw_chance``, un joueur tiré au prorata de sa mise emporte toute la cagnotte
        (gain non imposé) ; sinon elle grossit."""
        live = self.live
        state = self._society_state()
        if state.lottery is None:
            state.lottery = Company("loterie", "Loterie d'État", "lottery", 0.0, 0.0)
        pot = state.lottery
        people = self.people
        base = max(0.0, live.lottery_stake_share) * max(self.wage_reference, 1.0)
        order = sorted(range(len(people)), key=lambda i: (people[i].cash, people[i].id))
        span = max(1, len(people) - 1)
        tickets = []
        total = 0.0
        for rank, index in enumerate(order):
            person = people[index]
            stake = min(
                base * self._society_traits(person.id)[0] * (POOR_PLAY - rank / span),
                LOTTERY_CASH_LIMIT * max(0.0, person.cash),
            )
            if stake < 0.5:
                continue
            transfer_cash(person, pot, stake)
            person.expenses_this_turn += stake
            person.cumulative_expenses += stake
            tickets.append((person, stake))
            total += stake
        rng = state.random
        drawn = rng.random() < max(0.0, min(1.0, live.lottery_draw_chance))
        pick = rng.random()
        if total <= 0.0:
            return
        levy = total * (1.0 - _clip(live.lottery_payout_share))
        if levy > 0.0:
            transfer_cash(pot, self.public, levy)
            self.institutions.book.add("loterie", levy)
            self.public.revenue += levy
        self._society_count("loterie_mises", total)
        self._society_count("loterie_etat", levy)
        self._society_count("loterie_joueurs", len(tickets))
        self._lottery_effort(tickets)
        if drawn and pot.cash > 1.0:
            target = pick * total
            winner = tickets[-1][0]
            for person, stake in tickets:
                target -= stake
                if target < 0.0:
                    winner = person
                    break
            prize = pot.cash
            before = winner.cash
            transfer_cash(pot, winner, prize)
            state.winners.append({
                "mois": self.month, "menage": winner.id, "gain": prize, "tresorerie_avant": before,
            })
            self._society_count("loterie_gains", prize)
            self._society_log(
                "loterie", f"{display_name(winner.id)} gagne {prize:,.0f} à la loterie".replace(",", " "),
                menage=winner.id, montant=prize,
            )

    def _lottery_effort(self, tickets: list) -> None:
        """Mesure seule : mises rapportées au revenu du mois, pour le cinquième des joueurs aux
        revenus les plus bas et pour le cinquième aux revenus les plus hauts."""
        ranked = sorted(tickets, key=lambda item: (item[0].income_this_turn, item[0].id))
        fifth = len(ranked) // 5
        if fifth == 0:
            return
        for key, group in (("loterie_effort_modestes", ranked[:fifth]), ("loterie_effort_aises", ranked[-fifth:])):
            income = sum(max(0.0, p.income_this_turn) for p, _ in group)
            self.society.month_stats[key] = sum(stake for _, stake in group) / income if income > 0.0 else 0.0

    # ------------------------------------------------------------------ V4 : addictions
    def _vice_addictiveness(self, company: Company) -> float:
        """Pouvoir addictif du produit : équipement payé, effet borné et décroissant à la marge."""
        level = max(0.0, company.tech_level)
        return 1.0 + max(0.0, self.live.addiction_technology_gain) * level / (1.0 + level)

    def _vice_potential_units(self, firms: list) -> dict[str, float]:
        """Débouchés finançables aux prix affichés, y compris pour un fabricant sans salarié.

        Sert aux devis et aux reprises ; aucune vente, aucun paiement ni gain de dépendance.
        Chaque ménage garde la même limite de trésorerie que sur le marché effectif.
        """
        weights = {c.id: self._vice_addictiveness(c) / max(c.price, 0.01) ** 2 for c in firms}
        total = sum(weights.values())
        units = {c.id: 0.0 for c in firms}
        if total <= 0.0:
            return units
        gross = (1.0 + max(0.0, self.live.addiction_excise_rate)) * (1.0 + self.live.vat_rate)
        price = sum(c.price * weights[c.id] / total for c in firms) * gross
        for person in self.people:
            wanted = VICE_UNITS * self._dependence(person)
            affordable = VICE_CASH_LIMIT * max(0.0, person.cash) / max(price, 0.01)
            demand = min(wanted, affordable)
            for company in firms:
                units[company.id] += demand * weights[company.id] / total
        return units

    def _vice_restart_value(self, company: Company, potential: dict) -> float:
        """Recette réalisable par un salarié, nette de l'amortissement, sans reprise à l'infini."""
        capacity = VICE_OUTPUT * max(company.productivity_trait, 0.1)
        revenue = min(capacity, potential.get(company.id, 0.0)) * company.price
        return max(0.0, revenue - company.depreciation_this_turn)

    def _vice_reference_price(self) -> float:
        """Prix hors taxes d'une unité tel qu'un ménage pleinement dépendant y consacre, accise et
        TVA comprises, ``addiction_budget_share`` du salaire de référence."""
        live = self.live
        gross = max(0.0, live.addiction_budget_share) * max(self.wage_reference, 1.0) / VICE_UNITS
        return max(0.01, gross / ((1.0 + max(0.0, live.addiction_excise_rate)) * (1.0 + live.vat_rate)))

    def _dependence(self, person) -> float:
        """Dépendance d'un ménage (0 à 1). Au départ : nulle si son penchant est sous
        ``VICE_THRESHOLD``, sinon proportionnelle au penchant au-delà, jusqu'à ``VICE_START``."""
        state = self.society
        level = state.dependence.get(person.id)
        if level is None:
            level = state.dependence[person.id] = self._vice_goal(self._society_traits(person.id)[2], False)
        return level

    @staticmethod
    def _vice_goal(taste: float, stressed: bool) -> float:
        """Dépendance vers laquelle tend un ménage servi : nulle sous le seuil de penchant, puis
        proportionnelle au penchant au-delà, jusqu'à ``VICE_START``. Un chômeur de longue durée
        a un seuil deux fois plus bas."""
        threshold = VICE_THRESHOLD / 2.0 if stressed else VICE_THRESHOLD
        return VICE_START * max(0.0, taste - threshold) / (1.0 - threshold)

    def _vice_sales(self, f) -> None:
        """Ventes de tabac et d'alcool du mois, après les achats de services (phase 3).

        Demande rigide : un ménage veut ``VICE_UNITS`` × sa dépendance, quel que soit le prix, et
        n'y renonce que faute de trésorerie (au plus ``VICE_CASH_LIMIT`` de celle-ci). Il achète
        aux fabricants qui ont des salariés, au prorata du pouvoir addictif / prix², dans la limite de ce que
        leurs salariés vendent. Il paie le prix, l'accise (``addiction_excise_rate`` du prix hors
        taxes) et la TVA sur le tout ; accise et TVA vont à l'État.

        Prix : un fabricant tient compte des volumes et du coût par unité finançable, avec une
        marge visée, entre ``VICE_PRICE_BAND`` × le prix de référence. Les commandes refusées par un
        fabricant saturé sont redistribuées aux autres. Dépendance : elle rejoint le niveau d'équilibre
        du ménage (``_vice_goal`` : selon son penchant, plus haut après ``VICE_STRESS_MONTHS``
        mois de chômage), relevé par le pouvoir addictif des produits effectivement livrés. Elle
        baisse quand le ménage est privé de produit ou que le prix dépasse la
        référence. Les dégâts de santé s'accumulent avec la consommation."""
        live = self.live
        state = self._society_state()
        reference = self._vice_reference_price()
        firms = [c for c in self.companies if c.active and c.sector == VICE_SECTOR]
        potential = self._vice_potential_units(firms)
        low, high = VICE_PRICE_BAND
        margin = _clip(live.addiction_margin_target, 0.0, 0.8)
        for company in firms:
            before = state.vice_units.get(company.id)
            if company.price <= 0.0 or before is None:
                company.price = max(company.price, low * reference) if before is not None else reference
            elif company.previous_units_sold >= 0.95 * before and company.previous_units_sold > 0.0:
                company.price *= 1.0 + VICE_PRICE_UP
            else:
                company.price *= 1.0 - VICE_PRICE_DOWN
            # Un démarrage à un poste évite de surdimensionner ce petit marché. Le devis
            # d'une entreprise à l'arrêt inclut la paie attendue d'un poste, sans vente fictive.
            capacity = max(1, len(company.employees)) * VICE_OUTPUT * max(company.productivity_trait, 0.1)
            expected = min(capacity, potential.get(company.id, 0.0))
            costs = max(company.expenses, company.wage_offer * 1.35 if not company.employees else 0.0)
            if expected > 1e-9:
                company.price = max(company.price, costs / (expected * (1.0 - margin)))
            company.price = _clip(company.price, low * reference, high * reference)
            state.vice_units[company.id] = company.previous_units_sold
        sellers = [c for c in firms if c.employees]
        left = {c.id: len(c.employees) * VICE_OUTPUT * max(c.productivity_trait, 0.1) for c in sellers}
        potency = {c.id: self._vice_addictiveness(c) for c in firms}
        weights = [(c, potency[c.id] / (c.price * c.price)) for c in sellers]
        total_weight = sum(weight for _, weight in weights)
        all_weights = {c.id: potency[c.id] / (c.price * c.price) for c in firms}
        all_weight = sum(all_weights.values())
        excise_rate = max(0.0, live.addiction_excise_rate)
        vat_rate = live.vat_rate
        gross_factor = (1.0 + excise_rate) * (1.0 + vat_rate)
        average_price = (
            sum(c.price * weight for c, weight in weights) / total_weight if total_weight > 0.0 else reference
        )
        price_gap = max(0.0, average_price / reference - 1.0)
        net_total = excise_total = vat_total = units_total = wanted_total = 0.0
        users = 0
        dependence, damage = state.dependence, state.damage
        for person in self.people:
            pid = person.id
            level = self._dependence(person)
            taste = state.traits[pid][2]
            stressed = person.unemployment_months >= VICE_STRESS_MONTHS
            goal = self._vice_goal(taste, stressed)
            if level <= 0.0:
                if stressed and goal > 0.0:
                    dependence[pid] = min(goal, VICE_INITIATION)
                hurt = damage.get(pid, 0.0)
                if hurt > 0.0:
                    damage[pid] = max(0.0, hurt - VICE_HEALING)
                continue
            users += 1
            wanted = VICE_UNITS * level
            wanted_total += wanted
            served = 0.0
            exposure = 0.0
            if total_weight > 0.0:
                quantities = {c.id: 0.0 for c in sellers}
                remaining = wanted
                available = [(c, weight) for c, weight in weights if left[c.id] > 1e-9]
                while remaining > 1e-9 and available:
                    weight_sum = sum(weight for _, weight in available)
                    allocated = 0.0
                    for company, weight in available:
                        units = min(remaining * weight / weight_sum,
                                    left[company.id] - quantities[company.id])
                        quantities[company.id] += units
                        allocated += units
                    remaining -= allocated
                    available = [(c, w) for c, w in available
                                 if left[c.id] - quantities[c.id] > 1e-9]
                    if allocated <= 1e-9:
                        break
                plan = [(c, quantities[c.id]) for c in sellers if quantities[c.id] > 1e-9]
                cost = 0.0
                for company, units in plan:
                    cost += units * company.price * gross_factor
                limit = VICE_CASH_LIMIT * max(0.0, person.cash)
                scale = min(1.0, limit / cost) if cost > 0.0 else 0.0
                for company, units in plan:
                    units *= scale
                    if units <= 1e-9:
                        continue
                    net = units * company.price
                    excise = net * excise_rate
                    tax = (net + excise) * vat_rate
                    transfer_cash(person, company, net)
                    person.cash -= excise + tax
                    spent = net + excise + tax
                    person.expenses_this_turn += spent
                    person.cumulative_expenses += spent
                    company.revenue += net
                    company.units_sold += units
                    company.units_produced += units  # fabrication à la commande, sans stock
                    left[company.id] -= units
                    net_total += net
                    excise_total += excise
                    vat_total += tax
                    served += units
                    exposure += units * potency[company.id]
            if all_weight > 0.0:
                for company in firms:
                    company.unmet_units += max(0.0, wanted - served) * all_weights[company.id] / all_weight
            units_total += served
            ratio = served / wanted
            strength = exposure / served if served > 0.0 else 1.0
            goal = 1.0 - (1.0 - goal) ** strength
            if goal > level:
                level += min(1.0, VICE_REINFORCE * strength) * ratio * (goal - level)
            else:
                level -= VICE_REINFORCE * (level - goal)
            level -= (VICE_WITHDRAWAL * (1.0 - ratio) + VICE_QUIT_PRICE * price_gap) * level
            dependence[pid] = _clip(level) if level > 1e-3 else 0.0
            damage[pid] = _clip(damage.get(pid, 0.0) + VICE_DAMAGE * level * ratio)
        for company in firms:
            demand = company.units_sold + company.unmet_units
            company.demand_ema = (demand if company.demand_ema <= 0.0 else company.demand_ema
                                  + live.demand_smoothing * (demand - company.demand_ema))
        if excise_total + vat_total > 0.0:
            self.public.cash += excise_total + vat_total
            f.vat_revenue += vat_total
            self.institutions.book.add("accises", excise_total)
            self.public.revenue += excise_total
        f.household_services += net_total
        stats = state.month_stats
        stats["vice_consommateurs"] = users
        stats["vice_unites"] = units_total
        stats["vice_demande"] = wanted_total
        stats["vice_ventes"] = net_total
        stats["vice_accises"] = excise_total
        stats["vice_prix_relatif"] = average_price / reference

    def _vice_health(self, f) -> None:
        """Soins du mois (phase 4) : un ménage dont les dégâts dépassent ``VICE_CARE_FROM`` a
        besoin de soins valant ``addiction_care_cost`` × le salaire de référence × ses dégâts.
        L'État en paie une part égale à l'orientation du gouvernement (0 : rien, 100 : tout), dans
        la limite de ses fonds ; le ménage paie le reste s'il en a les moyens. Les soins sont
        achetés aux entreprises de services."""
        live = self.live
        state = self._society_state()
        if not state.damage:
            return
        rate = max(0.0, live.addiction_care_cost) * max(self.wage_reference, 1.0)
        if rate <= 0.0:
            return
        public_share = _clip(self.government.orientation / 100.0)
        suppliers = household_suppliers(self.companies, ("services",), {"services": 1.0})
        damage = state.damage
        needs = [(person, rate * damage[person.id]) for person in self.people
                 if damage.get(person.id, 0.0) >= VICE_CARE_FROM]
        if not needs:
            return
        wanted = sum(need for _, need in needs)
        funded = self._state_funds(public_share * wanted)
        coverage = funded / wanted if wanted > 0.0 else 0.0
        private = 0.0
        for person, need in needs:
            private += suppliers.pay(person, need * (1.0 - coverage), self.resources)
        public_paid = 0.0
        if funded > 1.0:
            before = self.public.account.cash
            pay_business_suppliers(self.public.account, funded, {"services": 1.0}, self.companies,
                                   self.resources, priced=False)
            self.public.account.expenses = 0.0
            public_paid = before - self.public.account.cash
            self.institutions.book.add("soins_addictions", -public_paid)
            self.public.spending += public_paid
            f.public_spent += public_paid
        f.household_services += private
        stats = state.month_stats
        stats["vice_malades"] = len(needs)
        stats["vice_soins_publics"] = public_paid
        stats["vice_soins_menages"] = private
        stats["vice_soins_non_payes"] = max(0.0, wanted - public_paid - private)

    # ------------------------------------------------------------------ V11 : manifestations
    def _unrest_pressure(self, unemployment: float) -> float:
        """Pression sociale du mois : chômage au-delà de ``unrest_unemployment``, part du
        patrimoine des 10 % les plus riches au-delà de ``unrest_inequality`` (relevé du mois
        précédent), baisse du salaire réel moyen sur douze mois."""
        live = self.live
        pressure = max(0.0, unemployment - live.unrest_unemployment) / UNREST_UNEMPLOYMENT_SCALE
        if self.history:
            last = self.history[-1]
            pressure += max(0.0, last.household_top10_share - live.unrest_inequality) / UNREST_INEQUALITY_SCALE
            if len(self.history) >= 13:
                before = self.history[-13].real_average_wage
                if before > 0.0:
                    pressure += max(0.0, 1.0 - last.real_average_wage / before) / UNREST_WAGE_SCALE
        return pressure

    def _social_unrest(self, f) -> None:
        """Tension sociale et manifestations, à la fin du marché du travail (phase 2).

        La tension rejoint la pression de ``UNREST_SPEED`` par mois. À ``UNREST_PROTEST``, et au
        plus une fois tous les ``UNREST_REST_MONTHS`` mois, une manifestation éclate : tous les
        salariés perdent ``unrest_production_loss`` × min(1, tension) de leur travail du mois
        (production de l'alimentation, des biens et des matières). À ``UNREST_RIOT``, c'est une
        émeute : ``RIOT_DAMAGE`` des stocks d'alimentation et de biens sont détruits. Le
        gouvernement plie : il relève de ``unrest_concession`` les transferts et l'allocation
        chômage de sa doctrine (moitié moins pour un gouvernement libéral), au plus
        ``UNREST_MAX_CONCESSIONS`` fois ; la tension retombe alors de ``UNREST_RELIEF``. Un
        gouvernement qui ne concède plus garde sa tension."""
        live = self.live
        state = self._society_state()
        pressure = self._unrest_pressure(f.unemployment)
        state.tension += UNREST_SPEED * (pressure - state.tension)
        stats = state.month_stats
        stats["pression_sociale"] = pressure
        stats["tension_sociale"] = state.tension
        if state.tension < UNREST_PROTEST or self.month - state.last_protest < UNREST_REST_MONTHS:
            return
        state.last_protest = self.month
        riot = state.tension >= UNREST_RIOT
        loss = _clip(live.unrest_production_loss) * min(1.0, state.tension)
        for person_id, (skill, technology) in list(f.worker_skills.items()):
            f.worker_skills[person_id] = (skill * (1.0 - loss), technology)
        destroyed = 0.0
        if riot:
            for company in self.companies:
                if company.active:
                    destroyed += RIOT_DAMAGE * (company.inventory + company.goods_inventory)
                    company.inventory *= 1.0 - RIOT_DAMAGE
                    company.goods_inventory *= 1.0 - RIOT_DAMAGE
        inst = self.institutions
        doctrine = inst.doctrine
        term = self.government.since_month
        if state.concession_term != term:
            state.concession_term = term
            state.concessions = 0
        conceded = 0.0
        if doctrine is not None and state.concessions < UNREST_MAX_CONCESSIONS:
            conceded = max(0.0, live.unrest_concession) * (0.5 if self.government.orientation < 35.0 else 1.0)
            if conceded > 0.0:
                doctrine.transfer_share *= 1.0 + conceded
                doctrine.benefit_factor *= 1.0 + conceded
                state.concessions += 1
                state.tension *= 1.0 - UNREST_RELIEF
                self._decide(
                    "presidence", "Concession après une manifestation",
                    f"tension sociale de {state.tension / (1.0 - UNREST_RELIEF):.2f} (chômage {f.unemployment:.1%}) ; "
                    f"transferts et allocation chômage du programme relevés de {conceded:.0%}",
                    financing="loi de finances suivante",
                )
        if live.personal_popularity and getattr(self, "power", None) is not None:
            president = inst.holder("presidence")
            if president is not None:
                self._shift_opinion(president, -PROTEST_OPINION * (2.0 if riot else 1.0))
        participants = sum(1 for p in self.people if p.employer_id is None) + int(
            loss * sum(1 for p in self.people if p.employer_id is not None)
        )
        state.protests.append({
            "mois": self.month, "emeute": riot, "tension": state.tension if conceded <= 0.0
            else state.tension / (1.0 - UNREST_RELIEF),
            "chomage": f.unemployment, "travail_perdu": loss, "stocks_detruits": destroyed,
            "concession": conceded, "participants": participants,
        })
        self._society_count("manifestations")
        if riot:
            self._society_count("emeutes")
        self._society_count("manifestants", participants)
        self._society_log(
            "emeute" if riot else "manifestation",
            ("Émeute" if riot else "Manifestation")
            + f" : {loss:.0%} du travail du mois perdu"
            + (f", le gouvernement concède {conceded:.0%}" if conceded > 0.0 else ", le gouvernement ne cède pas"),
        )

    # ------------------------------------------------------------------ V12 : grands travaux
    def _public_works_step(self, f) -> None:
        """Grands travaux du mois (phase 4, après la loi de finances).

        - **Chantier** : une tranche mensuelle (coût / durée) est payée aux entreprises de la
          construction, des matières, de l'énergie et de la technologie, dans la limite des fonds
          de l'État (trésorerie puis emprunt) ; faute de fonds, le chantier prend du retard.
        - **Ouvrage achevé** : son entretien coûte ``WORKS_UPKEEP_RATE`` de son coût par mois ;
          le gouvernement en finance une part selon sa doctrine d'investissement public (de 25 %
          à 100 %) et ses fonds. L'état perd ``public_works_wear_rate`` × la part non entretenue ;
          sous ``WORKS_RUIN``, l'ouvrage est hors service et peut être reconstruit.
        - **Lancement** : au plus un chantier à la fois et un lancement par an, pour le premier
          ouvrage manquant de la liste, si la dette publique ne dépasse pas
          ``public_works_debt_limit`` × le PIB annuel et si la doctrine n'est pas hostile à
          l'investissement public. Coût : ``public_works_gdp_share`` × le PIB annuel × son poids."""
        live = self.live
        state = self._society_state()
        inst = self.institutions
        book = inst.book
        public = self.public
        doctrine = inst.doctrine
        stance = doctrine.positions.get("public_investment", 0.0) if doctrine is not None else 0.0
        built = maintained = 0.0
        for work in list(state.works):
            if work["fin"] is None:
                tranche = min(work["cout"] / work["duree"], work["cout"] - work["paye"])
                funds = self._state_funds(tranche)
                if funds > 1.0:
                    before = public.account.cash
                    pay_business_suppliers(public.account, funds, WORKS_SPLIT, self.companies, self.resources)
                    public.account.expenses = 0.0
                    paid = before - public.account.cash
                    work["paye"] += paid
                    built += paid
                if work["paye"] >= work["cout"] * 0.999:
                    work["fin"] = self.month
                    work["etat"] = 1.0
                    self._society_count("ouvrages_acheves")
                    self._society_log("ouvrage", f"{work['nom']} : ouvrage achevé après "
                                                 f"{self.month - work['debut']} mois de chantier")
                continue
            need = WORKS_UPKEEP_RATE * work["cout"]
            wanted = need * _clip(0.75 + 0.5 * stance, 0.25, 1.0)
            funds = self._state_funds(wanted)
            paid = 0.0
            if funds > 1.0:
                before = public.account.cash
                pay_business_suppliers(public.account, funds, WORKS_UPKEEP_SPLIT, self.companies, self.resources)
                public.account.expenses = 0.0
                paid = before - public.account.cash
                maintained += paid
            coverage = _clip(paid / need) if need > 0.0 else 1.0
            work["etat"] = max(0.0, work["etat"] - max(0.0, live.public_works_wear_rate) * (1.0 - coverage))
            work["entretien"] = coverage
            if work["etat"] < WORKS_RUIN:
                state.works.remove(work)
                self._society_count("ouvrages_perdus")
                self._society_log("ouvrage", f"{work['nom']} : hors service faute d'entretien")
        if built:
            book.add("grands_travaux", -built)
        if maintained:
            book.add("entretien_ouvrages", -maintained)
        if built or maintained:
            public.spending += built + maintained
            f.public_investment += built + maintained
        stats = state.month_stats
        stats["travaux_chantiers"] = built
        stats["travaux_entretien"] = maintained
        # lancement
        if any(work["fin"] is None for work in state.works):
            return
        if self.month - state.last_launch < WORKS_LAUNCH_MONTHS or stance <= -0.5:
            return
        gdp = self._annual_gdp()
        if gdp <= 0.0 or public.debt > max(0.0, live.public_works_debt_limit) * gdp:
            return
        existing = {work["type"] for work in state.works}
        for key, name, weight, months in WORKS:
            if key in existing:
                continue
            cost = max(0.0, live.public_works_gdp_share) * gdp * weight
            if cost <= 1.0:
                return
            state.works.append({
                "type": key, "nom": name, "cout": cost, "duree": months, "paye": 0.0,
                "debut": self.month, "fin": None, "etat": 0.0, "entretien": 1.0,
            })
            state.last_launch = self.month
            self._decide(
                "services", f"Grands travaux : {name}",
                f"chantier de {months} mois ; gain de productivité attendu de {live.public_works_gain:.1%} "
                "une fois l'ouvrage achevé, tant qu'il est entretenu",
                amount=cost, financing="trésorerie de l'État puis emprunt, hors loi de finances",
            )
            self._society_count("chantiers_lances")
            self._society_log("chantier", f"{name} : chantier lancé ({cost:,.0f})".replace(",", " "))
            return

    # ------------------------------------------------------------------ aides : capitalisation de l'allocation
    def _benefit_per_person(self, unemployment: float) -> float:
        """Allocation chômage mensuelle d'un chômeur ce mois."""
        live = self.live
        return unemployment_benefit_entitlement(
            live.unemployment_benefit, self.price_index, unemployment,
            live.unemployment_benefit_cycle_sensitivity,
        )

    # ------------------------------------------------------------------ phase 8 : relevé
    def _society_record(self, f) -> None:
        """Relevé mensuel du lot V (mesures seules)."""
        state = self._society_state()
        live = self.live
        stats = state.month_stats
        stats["mois"] = self.month
        if live.personal_bankruptcy:
            stats["interdits_en_cours"] = len(state.bans)
            stats.setdefault("dossiers_surendettement", 0.0)
        if live.philanthropy:
            stats["fondations_tresorerie"] = sum(a.cash for a in state.foundations.values())
            stats["donateurs_connus"] = len(state.fame)
            stats["effet_ecole"] = self._society_school_quality()
            effort = state.foundation_effort.get("hopital", 0.0)
            stats["effet_hopital"] = HOSPITAL_GAIN * effort / (effort + FOUNDATION_HALF) if effort > 0.0 else 0.0
            stats.setdefault("dons", 0.0)
        if live.state_lottery and state.lottery is not None:
            stats["loterie_cagnotte"] = state.lottery.cash
            stats.setdefault("loterie_mises", 0.0)
        if live.addictive_goods:
            firms = [c for c in self.companies if c.active and c.sector == VICE_SECTOR]
            stats["vice_entreprises"] = len(firms)
            stats["vice_salaries"] = sum(len(c.employees) for c in firms)
            levels = state.dependence.values()
            stats["vice_dependance_moyenne"] = sum(levels) / len(state.dependence) if state.dependence else 0.0
            stats["vice_degats_moyens"] = (
                sum(state.damage.values()) / len(self.people) if self.people else 0.0
            )
        if live.public_works:
            done = [w for w in state.works if w["fin"] is not None]
            stats["ouvrages"] = len(done)
            stats["ouvrages_etat_moyen"] = sum(w["etat"] for w in done) / len(done) if done else 0.0
            stats["ouvrages_gain"] = max(0.0, live.public_works_gain) * sum(w["etat"] for w in done)
            stats["chantiers_en_cours"] = len(state.works) - len(done)
        if any(getattr(live, name) for name in VENTURE_SWITCHES):
            self._ventures_stats(stats)
        state.history.append(stats)
        state.month_stats = {}

    # ------------------------------------------------------------------ lectures pour l'interface
    def society_history(self) -> list[dict]:
        """Relevés mensuels du lot V depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset} for row in state.history if row["mois"] - offset >= 0]

    def society_events(self) -> list[dict]:
        """Événements du lot V depuis la période 0, du plus récent au plus ancien."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset}
                for row in reversed(state.log) if row["mois"] - offset >= 0]

    def society_profile(self, person_id: str) -> dict:
        """Ce que le lot V sait d'un ménage (vide sans état)."""
        state = getattr(self, "society", None)
        if state is None:
            return {}
        profile: dict = {}
        traits = state.traits.get(person_id)
        if traits is not None:
            profile.update(gout_du_jeu=traits[0], generosite=traits[1], penchant=traits[2])
        if person_id in state.bans:
            profile["interdit_jusqu_a"] = state.bans[person_id] - self.warmup_offset
        if person_id in state.dependence:
            profile["dependance"] = state.dependence[person_id]
            profile["degats"] = state.damage.get(person_id, 0.0)
        if person_id in state.gifts:
            profile["dons"] = state.gifts[person_id]
            profile["renommee"] = state.fame.get(person_id, 0.0)
        held = sum(pool["porteurs"].get(person_id, 0.0) for pool in state.pools.values())
        if held > 0.0:
            profile["titres_adosses"] = held
        return profile

    def works_table(self) -> list[dict]:
        """Une ligne par ouvrage : chantier en cours ou ouvrage achevé."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{
            "nom": w["nom"], "cout": w["cout"], "paye": w["paye"], "debut": w["debut"] - offset,
            "fin": None if w["fin"] is None else w["fin"] - offset, "etat": w["etat"],
            "avancement": w["paye"] / w["cout"] if w["cout"] > 0.0 else 0.0,
            "entretien": w.get("entretien", 1.0),
        } for w in state.works]

    def foundations_table(self) -> list[dict]:
        """Une ligne par fondation : trésorerie et effet courant."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        return [{
            "cause": cause, "nom": CAUSE_LABELS[cause], "tresorerie": account.cash,
            "effort": state.foundation_effort.get(cause, 0.0),
        } for cause, account in state.foundations.items()]

    def donors_table(self, limit: int = 20) -> list[dict]:
        """Plus gros donateurs : dons cumulés, renommée, cause."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        order = sorted(state.gifts, key=lambda pid: (-state.gifts[pid], pid))[:limit]
        return [{
            "menage": pid, "nom": display_name(pid), "dons": state.gifts[pid],
            "renommee": state.fame.get(pid, 0.0), "cause": CAUSE_LABELS.get(state.causes.get(pid, ""), "—"),
        } for pid in order]
