"""Lot G (novembre 2026) : huit mécanismes d'entreprise, chacun derrière son réglage.

Tous sont désactivés par défaut : sans réglage activé, aucun calcul, aucun tirage et aucun attribut
n'est ajouté (l'état ``simulation.enterprise`` n'est créé qu'au premier usage).

- **G1 expérience** (``experience_learning``) : compétence acquise par la pratique, n / (n +
  ``experience_half_count``). Fondateurs (``Person.ventures``), négociation des salaires
  d'embauche (``labor.negotiate_hire``), acquéreurs (``consolidation``).
- **G2 gros entretien** (``property_upkeep``) : l'état du parc de chaque propriétaire s'use ; les
  travaux le rétablissent, payés aux services et aux matières ; un parc dégradé se loue moins cher.
- **G3 préférences et données** (``consumer_preferences``) : goût de chaque ménage, style de chaque
  producteur d'alimentation ou de biens ; les entreprises technologiques vendent les données des
  ménages équipés, qui rapprochent l'offre de l'acheteur du goût de ses clients.
- **G4 diversification** (``diversification``) : une entreprise rentable fonde une filiale dans un
  autre secteur attractif. Une entreprise n'a qu'un secteur : un groupe vend plusieurs produits
  par ses filiales, dont les dividendes remontent à la société mère.
- **G5 rachats entre secteurs** (``conglomerate_takeovers``) : prise de contrôle d'une entreprise
  plus petite d'un autre secteur, qui devient une filiale (pas de fusion).
- **G6 magasins** (``retail_stores``) : réseau de vente d'un producteur, qui relève son poids dans
  les achats des ménages (la demande totale ne change pas : les magasins déplacent des ventes).
- **G7 distribution de groupe** (``group_distribution``) : les magasins d'un groupe vendent les
  produits de toutes ses entreprises ; l'acquéreur compte ce gain dans la valeur d'une cible.
- **G8 non cotées et fonds** (``private_startups``) : une entreprise fondée par un ménage reste
  hors de la Bourse ; chaque banque gère un fonds d'investissement où les ménages placent une part
  de leur épargne ; le fonds finance et accompagne les non cotées ; l'introduction en Bourse
  n'arrive que pour un gros besoin de trésorerie ou à la sortie du fonds.

Comptabilité : tout flux passe par ``transfer_cash`` ou par les paiements entre agents
(``intersector``). Un fonds est un agent non bancaire : sa trésorerie est de la monnaie
(``Ledger.nonbank_cash``) ; ses frais de gestion sont une recette de la banque gérante, comme un
intérêt. Les charges du mois (magasins, données, frais) sont payées avant la clôture des comptes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from economy.business import incremental_capacity_project
import math

from economy.consolidation import INTEGRATION_MONTHS, _employee_stake, _holder_valuation, _merge_into
from economy.energy import infrastructure_unit_price
from economy.finance import _entrant_wage_offer, sector_entry_signals
from economy.intersector import (
    household_suppliers,
    pay_business_suppliers,
    pay_suppliers_from_household,
)
from economy.labor import experience_skill, workforce_targets
from economy.models import Company
from economy.payments import transfer_cash
from economy.sectors import FIRM_SECTORS
from economy.stock_market import _fundamental_price
from economy.treasury import working_capital
from economy.wealth import company_balance_sheet

PREFERENCE_SEED_OFFSET = 53781811
# marchés où chaque entreprise a son propre poids dans les achats des ménages
CONSUMER_SECTORS = ("food", "goods")
# secteurs où une filiale peut être fondée (ni banque ni immobilier : règles d'entrée propres)
SUBSIDIARY_SECTORS = ("food", "goods", "technology", "materials", "energy", "services")
GROUP_MIN_AGE_MONTHS = 24       # ancienneté d'une entreprise avant de bâtir un groupe
GROUP_SIZE_RATIO = 0.5          # une cible vaut au plus la moitié de la capitalisation de l'acquéreur
CONTROL_RETENTION = 2.0 / 3.0   # part du seuil de contrôle sous laquelle une filiale quitte son groupe
MANAGEMENT_TRANSFER = 0.25      # part de l'écart de productivité qu'une société mère transmet à une cible rachetée
DATA_LEARNING_RATE = 0.2        # part de l'écart de connaissance comblée par un budget de données complet
REPAIR_SPLIT = {"services": 0.6, "materials": 0.4}
STORE_BUILD_SPLIT = {"real_estate": 0.5, "services": 0.3, "materials": 0.2}
STORE_RUNNING_SPLIT = {"real_estate": 0.5, "services": 0.5}
MINIMUM_RENT_CONDITION = 0.3    # un logement très dégradé se loue encore à 30 % du loyer
FUND_DRY_POWDER_SHARE = 0.5     # trésorerie d'attente d'un fonds, en apports maximaux d'un fondateur
FUND_EXIT_SPEED = 0.10          # part des titres cotés d'un fonds mise en vente chaque mois
IPO_DILUTION_LIMIT = 0.5        # titres nouveaux offerts à l'introduction / titres existants
# --- lot I (étape 3) ---
STORE_STAFF = 2.0               # vendeurs nécessaires par magasin
STORE_DEMAND_GAIN = 0.05        # hausse maximale des quantités voulues par les ménages, par la commodité d'achat
STORE_DENSITY = 50.0            # ménages par magasin qui donnent la moitié de cette hausse
TASTE_DRIFT = 0.01              # écart-type mensuel de la dérive du goût d'un ménage (cercle de longueur 1)
STYLE_SPEED = 0.02              # déplacement mensuel maximal du style d'un producteur pleinement informé
FUND_MARKET_SPEED = 0.25        # part de la trésorerie excédentaire d'un fonds placée en actions cotées par mois
FUND_EXIT_MULTIPLE = 2.0        # valeur / coût d'une participation à partir de laquelle le fonds demande la cotation
SUBSIDIARY_SUPPORT_SHARE = 0.25  # part de la trésorerie libre d'une société mère apportée à une filiale par mois
SUBSIDIARY_LOSS_MONTHS = 12     # mois de pertes après lesquels une société mère met sa filiale en vente
RESCUE_INTERVAL_MONTHS = 24     # délai entre deux reprises d'une même filiale sans salarié par sa société mère
EXECUTIVE_LEARNING = 0.005      # gain de productivité d'un premier anniversaire de mandat (décroît ensuite)
BOARD_HURDLE = 0.4              # écart d'exigence entre un conseil prudent et un conseil audacieux
_SECTOR_LABELS = {key: label.split(" (")[0] for key, label in FIRM_SECTORS}


@dataclass
class InvestmentFund(Company):
    """Fonds d'investissement géré par une banque (G8) : un porteur de titres comme l'État, hors de
    la liste des entreprises. ``units`` : parts émises aux ménages ; ``requests`` : besoins de
    financement reçus, lissés (dimensionne sa trésorerie d'attente)."""

    bank_id: str = ""
    units: float = 0.0
    requests: float = 0.0
    # suivi (mesures seules) : cumuls depuis la création du fonds
    subscribed: float = 0.0       # épargne confiée par les ménages
    redeemed: float = 0.0         # parts rachetées aux ménages
    distributed: float = 0.0      # trésorerie reversée aux porteurs
    fees: float = 0.0             # frais de gestion payés à la banque
    invested: float = 0.0         # apports aux entreprises non cotées
    received: float = 0.0         # dividendes et produits de cession encaissés (nets)
    cost: dict = field(default_factory=dict)      # entreprise -> apports cumulés
    bought: dict = field(default_factory=dict)    # entreprise -> titres reçus contre ces apports
    entry: dict = field(default_factory=dict)     # entreprise -> mois du premier apport
    exits: list = field(default_factory=list)     # participations soldées
    reserve: float = 0.0          # trésorerie d'attente visée ce mois (lot I)


@dataclass
class EnterpriseState:
    """État propre au lot G, créé au premier usage d'un mécanisme activé."""

    funds: list = field(default_factory=list)
    random: random.Random | None = None
    homes: dict = field(default_factory=dict)      # propriétaire -> logements le mois précédent (G2)
    bids: dict = field(default_factory=dict)       # acquéreur -> mois de sa dernière offre entre secteurs (G5)
    # suivi des groupes (mesures seules) : filiale -> origine, mois, coût, dividendes remontés
    group_log: dict = field(default_factory=dict)
    group_exits: list = field(default_factory=list)   # filiales sorties d'un groupe
    fund_history: list = field(default_factory=list)  # un relevé par fonds et par mois
    boards: dict = field(default_factory=dict)        # entreprise -> conseil du mois (lot H)
    retention: dict = field(default_factory=dict)     # entreprise -> part réinvestie avant le conseil (lot H)
    appetite: dict = field(default_factory=dict)      # entreprise -> goût du risque de son conseil (lot I)
    divesting: set = field(default_factory=set)       # filiales mises en vente par leur société mère (lot I)
    rescues: dict = field(default_factory=dict)       # filiale -> mois de sa dernière reprise par la mère (lot I)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)    # un relevé par mois (mesures seules)


def taste_fit(taste: float, style: float) -> float:
    """Affinité entre un goût et un style placés sur un cercle : 1 identiques, 0 opposés."""
    distance = abs(taste - style)
    distance = min(distance, 1.0 - distance)
    return max(0.0, 1.0 - 2.0 * distance)


def store_factor(stores: float, config) -> float:
    """Poids dans les achats des ménages d'un producteur qui dispose de ``stores`` magasins :
    1 + ``store_reach`` × n / (n + ``store_half_count``) (rendements décroissants)."""
    stores = max(0.0, stores)
    return 1.0 + max(0.0, config.store_reach) * stores / (stores + max(config.store_half_count, 1e-9))


def fund_assets(fund: InvestmentFund, prices: dict[str, float]) -> float:
    """Actif d'un fonds : trésorerie et titres au cours (valeur estimée pour une non cotée)."""
    return fund.cash + sum(q * prices.get(cid, 0.0) for cid, q in fund.shares.items())


class EnterpriseMixin:
    """Expérience, entretien, préférences, groupes, magasins, non cotées et fonds (lot G)."""

    # ------------------------------------------------------------------ état et porteurs
    def _enterprise_enabled(self) -> bool:
        live = self.live
        return bool(
            live.experience_learning or live.property_upkeep or live.consumer_preferences
            or live.diversification or live.conglomerate_takeovers or live.retail_stores
            or live.group_distribution or live.private_startups
            or live.staffless_closure_months > 0 or live.shareholder_governance
        )

    def _enterprise_state(self) -> EnterpriseState:
        state = getattr(self, "enterprise", None)
        if state is None:
            state = self.enterprise = EnterpriseState()
        return state

    def _enterprise_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._enterprise_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    def _outside_shareholders(self) -> tuple:
        """Porteurs de titres hors ménages et entreprises : l'État, puis les fonds (G8)."""
        state = getattr(self, "enterprise", None)
        if state is None or not state.funds:
            return (self.public.account,)
        return (self.public.account, *state.funds)

    def _wage_scale(self) -> float:
        """Niveau nominal des salaires rapporté au départ (indexation des coûts en euros)."""
        return max(1.0, self.wage_reference / max(self.live.starting_wage, 1.0))

    def _group_roots(self) -> dict[str, str]:
        """Entreprise active -> tête de son groupe (elle-même si elle n'a pas de société mère)."""
        by_id = {c.id: c for c in self.companies if c.active}
        roots: dict[str, str] = {}
        for company in by_id.values():
            root, seen = company, {company.id}
            while root.parent_id in by_id and root.parent_id not in seen:
                root = by_id[root.parent_id]
                seen.add(root.id)
            roots[company.id] = root.id
        return roots

    def _strategic_holdings(self) -> dict[str, frozenset] | None:
        """Société mère -> filiales (participations durables), ou ``None`` sans aucun groupe."""
        groups: dict[str, set] = {}
        for company in self.companies:
            if company.active and company.parent_id is not None:
                groups.setdefault(company.parent_id, set()).add(company.id)
        return {parent: frozenset(subs) for parent, subs in groups.items()} if groups else None

    def _unlisted_ids(self) -> frozenset:
        return frozenset(c.id for c in self.companies if not c.listed)

    def _undiluted_ids(self) -> frozenset:
        """Entreprises qui n'émettent pas de titres nouveaux : sous contrôle public (l'État refuse
        la dilution), et filiales d'un groupe (leur société mère aussi)."""
        controlled = self._public_controlled_ids()
        subsidiaries = [c.id for c in self.companies if c.active and c.parent_id is not None]
        return controlled | frozenset(subsidiaries) if subsidiaries else controlled

    def _recent_listings(self) -> frozenset:
        """Entreprises introduites en Bourse depuis moins de ``ipo_window_months`` mois (G8) :
        leurs titres offerts servent la demande avant les vendeurs."""
        window = self.live.ipo_window_months
        return frozenset(
            c.id for c in self.companies
            if c.active and c.listed_month >= 0 and self.month - c.listed_month <= window
        )

    # ------------------------------------------------------------------ G3, G6, G7 : poids dans les achats
    def _network_stores(self, roots: dict[str, str] | None = None) -> dict[str, float]:
        """Magasins dont dispose chaque entreprise active : les siens, ou ceux de tout son groupe
        avec ``group_distribution`` (G7)."""
        active = [c for c in self.companies if c.active]
        if not self.live.group_distribution:
            return {c.id: self._staffed_stores(c) for c in active}
        roots = roots if roots is not None else self._group_roots()
        totals: dict[str, float] = {}
        for company in active:
            totals[roots[company.id]] = totals.get(roots[company.id], 0.0) + self._staffed_stores(company)
        return {c.id: totals[roots[c.id]] for c in active}

    @staticmethod
    def _staffed_stores(company) -> float:
        """Magasins réellement ouverts (lot I) : il faut ``STORE_STAFF`` salariés par magasin ;
        un réseau en sous-effectif ne sert qu'au prorata de son personnel."""
        if company.stores <= 0.0:
            return 0.0
        return company.stores * min(1.0, len(company.employees) / (company.stores * STORE_STAFF))

    def _store_staffing(self) -> None:
        """Vendeurs des magasins (lot I) : ``STORE_STAFF`` postes par magasin s'ajoutent au besoin
        en personnel du producteur, dans la limite des embauches mensuelles."""
        hiring = self.live.max_monthly_hiring
        for company in self.companies:
            if company.active and company.stores > 0.0:
                extra = math.ceil(company.stores * STORE_STAFF)
                company.staffing_need += extra
                company.target_employees = min(
                    company.target_employees + extra, len(company.employees) + hiring
                )

    def _store_demand_boost(self) -> float:
        """Commodité d'achat (lot I) : plus il y a de magasins ouverts par ménage, plus les
        ménages veulent acheter d'alimentation et de biens, d'au plus ``STORE_DEMAND_GAIN`` (un
        magasin pour ``STORE_DENSITY`` ménages en donne la moitié). 0 sans le réglage."""
        if not self.live.retail_stores or not self.people:
            return 0.0
        stores = sum(self._staffed_stores(c) for c in self.companies if c.active)
        if stores <= 0.0:
            return 0.0
        density = stores * STORE_DENSITY / len(self.people)
        return STORE_DEMAND_GAIN * density / (density + 1.0)

    def _sales_affinity(self, sector: str):
        """Facteur du poids de chaque producteur de ``sector`` dans les achats de chaque ménage
        (``goods._sales_weights``) : affinité de goût, relevée par les données achetées (G3), ×
        portée des magasins (G6, G7). ``None`` sans réglage ou sans effet."""
        live = self.live
        if not (live.consumer_preferences or live.retail_stores):
            return None
        firms = [c for c in self.companies if c.active and c.sector == sector]
        if not firms:
            return None
        reach = None
        if live.retail_stores:
            network = self._network_stores()
            reach = {c.id: store_factor(network[c.id], live) for c in firms}
        if not live.consumer_preferences:
            if all(factor == 1.0 for factor in reach.values()):
                return None
            return {person.id: reach for person in self.people}
        self._draw_tastes(firms)
        strength = max(0.0, live.preference_strength)
        level_max = max(live.technology_household_max_level, 1e-9)
        affinity = {}
        for person in self.people:
            # un ménage équipé laisse des données : part de son goût que l'on peut connaître
            known = min(1.0, max(0.0, person.technology_level) / level_max)
            row = {}
            for company in firms:
                fit = taste_fit(person.taste, company.product_style)
                if known > 0.0 and company.market_insight > 0.0:
                    fit += company.market_insight * known * (1.0 - fit)
                factor = 1.0 + strength * fit
                row[company.id] = factor * reach[company.id] if reach is not None else factor
            affinity[person.id] = row
        return affinity

    def _draw_tastes(self, firms: list) -> None:
        """Goûts et styles manquants, tirés d'un générateur propre (aucun autre tirage déplacé).
        Un héritier garde le goût de son ménage."""
        state = self._enterprise_state()
        if state.random is None:
            state.random = random.Random(self.config.seed + PREFERENCE_SEED_OFFSET)
        rng = state.random
        for person in self.people:
            if person.taste < 0.0:
                person.taste = rng.random()
        for company in firms:
            if company.product_style < 0.0:
                company.product_style = rng.random()

    # ------------------------------------------------------------------ G2 : gros entretien
    def _rent_conditions(self) -> dict[str, float] | None:
        """État du parc de chaque bailleur, pour le loyer dû (``None`` sans le réglage)."""
        if not self.live.property_upkeep:
            return None
        conditions = {
            c.id: max(MINIMUM_RENT_CONDITION, min(1.0, c.property_condition))
            for c in self.companies if c.active and c.sector == "real_estate"
        }
        for person in self.people:
            if person.investment_homes > 0:
                conditions[person.id] = max(MINIMUM_RENT_CONDITION, min(1.0, person.home_condition))
        return conditions

    def _worn_condition(self, owner_id: str, homes: int, condition: float, wear: float) -> float:
        """État après un mois d'usure ; un logement acquis ce mois entre à l'état neuf."""
        known = self._enterprise_state().homes
        previous = known.get(owner_id, homes)
        known[owner_id] = homes
        if homes <= 0:
            return 1.0
        if homes > previous:
            condition = (condition * previous + (homes - previous)) / homes
        return max(0.0, min(1.0, condition) * (1.0 - wear))

    def _property_upkeep(self) -> None:
        """Usure du mois puis travaux : le propriétaire rétablit l'état de son parc s'il en a les
        moyens (entreprise : trésorerie au-delà du fonds de roulement ; ménage : au-delà d'un mois
        de revenu attendu). Commande réelle aux services et aux matières ; faute de fournisseur,
        rien n'est payé ni réparé."""
        live = self.live
        wear = max(0.0, min(1.0, live.property_wear_rate))
        unit_cost = live.housing_build_cost * max(0.0, live.property_repair_cost_share)
        spent = homes_total = condition_total = 0.0
        for company in self.companies:
            if not company.active or company.sector != "real_estate":
                continue
            homes = company.property_inventory
            company.property_condition = self._worn_condition(company.id, homes, company.property_condition, wear)
            if homes <= 0:
                continue
            deficit = 1.0 - company.property_condition
            cost = deficit * homes * unit_cost
            budget = min(cost, max(0.0, company.cash - working_capital(company, live)))
            if budget > 1.0:
                paid = pay_business_suppliers(company, budget, REPAIR_SPLIT, self.companies, self.resources)
                company.property_condition += deficit * paid / cost
                spent += paid
            homes_total += homes
            condition_total += homes * company.property_condition
        suppliers = household_suppliers(self.companies, tuple(REPAIR_SPLIT))
        for person in self.people:
            homes = int(person.owns_home) + person.investment_homes
            person.home_condition = self._worn_condition(person.id, homes, person.home_condition, wear)
            if homes <= 0:
                continue
            deficit = 1.0 - person.home_condition
            cost = deficit * homes * unit_cost
            budget = min(cost, max(0.0, person.cash - max(0.0, person.expected_income)))
            if budget > 1.0:
                paid = pay_suppliers_from_household(
                    person, budget, REPAIR_SPLIT, self.companies, self.resources, suppliers
                )
                person.home_condition += deficit * paid / cost
                spent += paid
            homes_total += homes
            condition_total += homes * person.home_condition
        self._enterprise_count("travaux", spent)
        stats = self._enterprise_state().month_stats
        stats["etat_logements"] = condition_total / homes_total if homes_total > 0.0 else 1.0

    # ------------------------------------------------------------------ phase 6, avant la clôture
    def _enterprise_operations(self) -> None:
        """Charges et opérations du mois payées avant la clôture des comptes : magasins (G6),
        données clients (G3), fonds des banques (G8)."""
        live = self.live
        if live.retail_stores:
            self._run_stores()
        if live.consumer_preferences:
            self._trade_customer_data()
        if live.private_startups:
            self._fund_operations()

    def _run_stores(self) -> None:
        """Magasins : usure et charges des réseaux existants, puis ouvertures rentables.

        Un magasin s'use comme la capacité de production ; ses charges (loyer, services) sont
        payées chaque mois, et la part que la trésorerie ne paie pas ferme. Une entreprise ouvre
        un magasin de plus quand les ventes qu'il ajouterait (dans la limite de ce qu'elle peut
        livrer), à sa marge sur intrants, nettes des charges, rapportent plus que le coût de ses
        fonds propres, et que sa trésorerie libre paie l'ouverture (investissement capitalisé)."""
        live = self.live
        scale = self._wage_scale()
        opening = max(1.0, live.store_opening_cost * scale)
        running = max(0.0, live.store_running_cost * scale)
        decay = live.productive_capacity_depreciation_rate
        required = 12.0 * max(0.0, self.policy_rate) + live.equity_risk_premium
        network = self._network_stores()
        for company in self.companies:
            if not company.active:
                continue
            hurdle = required * self._board_hurdle(company)
            if company.stores > 0.0:
                company.stores *= 1.0 - decay
                due = company.stores * running
                affordable = min(due, max(0.0, company.cash))
                if due > 0.0 and affordable < due:
                    company.stores *= affordable / due
                if affordable > 0.0:
                    self._enterprise_count("charges_magasins", pay_business_suppliers(
                        company, affordable, STORE_RUNNING_SPLIT, self.companies, self.resources
                    ))
                if company.stores < 0.05:
                    company.stores = 0.0
            if company.sector not in CONSUMER_SECTORS or not company.employees:
                continue
            margin = company.price - company.unit_input_cost
            if margin <= 0.0 or company.previous_profit <= 0.0:
                continue
            reach = network.get(company.id, company.stores)
            gain = store_factor(reach + 1.0, live) / store_factor(reach, live) - 1.0
            stock = company.inventory if company.sector == "food" else company.goods_inventory
            deliverable = stock + max(0.0, company.potential_output - company.units_produced)
            extra_units = min(gain * max(company.units_sold, company.previous_units_sold), deliverable)
            # un magasin de plus coûte ses charges et ses vendeurs
            monthly = extra_units * margin - running - STORE_STAFF * max(company.wage_offer, live.minimum_wage)
            if 12.0 * monthly / opening <= hurdle:
                continue
            if company.cash - working_capital(company, live) < opening:
                continue
            paid = pay_business_suppliers(company, opening, STORE_BUILD_SPLIT, self.companies, self.resources)
            if paid <= 0.0:
                continue
            company.expenses -= paid  # achat capitalisé (amorti ensuite), pas une charge
            company.capital_expenditure += paid
            company.productive_capital_value += paid
            company.stores += paid / opening
            self._enterprise_count("magasins_ouverts", paid / opening)

    def _trade_customer_data(self) -> None:
        """Vente de données par les entreprises technologiques (G3).

        La connaissance des clients d'un producteur s'oublie de ``data_decay_rate`` par mois. Un
        producteur bénéficiaire, qui a des salariés, consacre ``data_budget_share`` de son chiffre
        d'affaires du mois précédent (dans la limite de sa trésorerie libre) à l'achat de données,
        payé aux entreprises technologiques qui ont des salariés ; un budget complet comble
        ``DATA_LEARNING_RATE`` de ce qui lui reste à apprendre. Sans vendeur ou sans ménage
        équipé, rien n'est vendu."""
        live = self.live
        self._evolve_preferences()
        decay = max(0.0, min(1.0, live.data_decay_rate))
        vendors = [c for c in self.companies if c.active and c.sector == "technology" and c.employees]
        sellers = bool(vendors)
        equipped = any(p.technology_level > 0.0 for p in self.people)
        # lot I : des vendeurs plus innovants et plus productifs livrent des données plus utiles
        # (moitié de l'effet garantie, l'autre selon leur niveau moyen, pondéré par leurs effectifs)
        staff = sum(len(c.employees) for c in vendors)
        quality = 0.5 + 0.5 * min(1.0, sum(
            len(c.employees) * c.innovation * c.productivity_trait for c in vendors
        ) / staff) if staff else 0.0
        # prix du marché des services technologiques : à budget égal, des données chères en
        # livrent moins
        price = max(self.resources.tech_service_price_index, 1e-9)
        for company in self.companies:
            if not company.active or company.sector not in CONSUMER_SECTORS:
                continue
            company.market_insight *= 1.0 - decay
            if not sellers or not equipped or not company.employees or company.previous_profit <= 0.0:
                continue
            full = live.data_budget_share * company.previous_revenue
            budget = min(full, max(0.0, company.cash - working_capital(company, live)))
            if budget <= 1.0:
                continue
            paid = pay_business_suppliers(
                company, budget, {"technology": 1.0}, self.companies, self.resources
            )
            if paid > 0.0:
                volume = min(1.0, paid / price / full)
                company.market_insight += (1.0 - company.market_insight) * DATA_LEARNING_RATE * quality * volume
                self._enterprise_count("donnees", paid)

    def _evolve_preferences(self) -> None:
        """Lot I : une fois par mois, le goût de chaque ménage dérive un peu (marche aléatoire du
        générateur des préférences) et chaque producteur informé déplace son style vers le goût
        moyen des ménages dont il est le producteur le plus proche (son segment), d'au plus
        ``STYLE_SPEED`` × sa connaissance des clients : sans données, il ne bouge pas."""
        state = self._enterprise_state()
        rng = state.random
        if rng is None:
            return  # goûts pas encore tirés (premier mois)
        for person in self.people:
            if person.taste >= 0.0:
                person.taste = (person.taste + rng.gauss(0.0, TASTE_DRIFT)) % 1.0
        for sector in CONSUMER_SECTORS:
            firms = [c for c in self.companies if c.active and c.sector == sector and c.product_style >= 0.0]
            if len(firms) < 1 or not any(c.market_insight > 0.0 for c in firms):
                continue
            sums = {c.id: [0.0, 0.0] for c in firms}
            for person in self.people:
                if person.taste < 0.0:
                    continue
                closest = max(firms, key=lambda c: (taste_fit(person.taste, c.product_style), c.id))
                angle = 2.0 * math.pi * person.taste
                sums[closest.id][0] += math.cos(angle)
                sums[closest.id][1] += math.sin(angle)
            for company in firms:
                x, y = sums[company.id]
                if company.market_insight <= 0.0 or (abs(x) < 1e-9 and abs(y) < 1e-9):
                    continue
                target = (math.atan2(y, x) / (2.0 * math.pi)) % 1.0
                gap = (target - company.product_style + 0.5) % 1.0 - 0.5   # plus court chemin sur le cercle
                step = STYLE_SPEED * company.market_insight
                company.product_style = (company.product_style + max(-step, min(step, gap))) % 1.0

    # ------------------------------------------------------------------ G8 : fonds des banques
    def _ensure_funds(self) -> list:
        """Un fonds par banque active ; le fonds d'une banque fermée est repris par la première
        banque active (sans banque, il n'est plus géré : aucun frais)."""
        state = self._enterprise_state()
        banks = [b for b in self.companies if b.active and b.sector == "bank"]
        active_ids = {b.id for b in banks}
        managed = {fund.bank_id for fund in state.funds}
        for bank in banks:
            if bank.id not in managed:
                state.funds.append(InvestmentFund(
                    id=f"fonds_{bank.id}", name=f"Fonds {bank.name}", sector="fund",
                    cash=0.0, wage_offer=0.0, bank_id=bank.id,
                ))
        for fund in state.funds:
            if fund.bank_id not in active_ids and banks:
                fund.bank_id = banks[0].id
        return state.funds

    def _financing_need(self, company) -> float:
        """Besoin de trésorerie d'une entreprise non cotée : ce qui manque à sa réserve
        d'exploitation (1,5 mois de salaires visés), plus, pour l'alimentation et les biens, la
        part du projet de capacité rentable que sa trésorerie libre ne paie pas."""
        live = self.live
        need = max(0.0, company.target_employees * company.wage_offer * 1.5 - company.cash)
        if company.sector in CONSUMER_SECTORS:
            unit_cost = infrastructure_unit_price("production", company, live, self.resources)
            units, _ = incremental_capacity_project(
                company, unit_cost, live, self.resources, self.policy_rate, self._expected_price_growth()
            )
            free = max(0.0, company.cash - working_capital(company, live))
            need += max(0.0, units * unit_cost - free)
        return need

    def _fund_operations(self) -> None:
        """Un mois des fonds : frais de gestion, distributions, épargne des ménages, apports aux
        entreprises non cotées, décisions d'introduction en Bourse."""
        live = self.live
        funds = self._ensure_funds()
        if not funds:
            return
        banks = {b.id: b for b in self.companies if b.active and b.sector == "bank"}
        prices = {c.id: c.stock_price for c in self.companies if c.active}
        dry_powder = FUND_DRY_POWDER_SHARE * live.startup_max_capital * self._wage_scale()
        unit_price: dict[str, float] = {}
        targets: dict[str, float] = {}
        for fund in funds:
            self._close_fund_positions(fund, prices)
            assets = fund_assets(fund, prices)
            bank = banks.get(fund.bank_id)
            if bank is not None and assets > 0.0:
                # frais de gestion : recette de la banque gérante, comme un intérêt encaissé
                fee = min(max(0.0, fund.cash), max(0.0, live.fund_fee_rate) * assets)
                if fee > 0.0:
                    fund.cash -= fee
                    bank.cash += fee
                    bank.revenue += fee
                    assets -= fee
                    fund.fees += fee
                    self._enterprise_count("fonds_frais", fee)
            targets[fund.id] = fund.reserve = dry_powder + 3.0 * fund.requests
            unit_price[fund.id] = assets / fund.units if fund.units > 1e-9 and assets > 0.0 else 1.0
        self._distribute_fund_cash(funds, targets, unit_price, prices)
        self._household_fund_flows(funds, targets, unit_price)
        self._fund_private_companies(funds, prices)
        # valeur des parts en fin de mois
        prices = {c.id: c.stock_price for c in self.companies if c.active}
        by_id = {fund.id: fund for fund in funds}
        final = {
            fund.id: fund_assets(fund, prices) / fund.units if fund.units > 1e-9 else 1.0
            for fund in funds
        }
        for person in self.people:
            if person.fund_units:
                person.fund_value = sum(
                    units * final[fund_id] for fund_id, units in person.fund_units.items() if fund_id in by_id
                )
            elif person.fund_value:
                person.fund_value = 0.0
        history = self._enterprise_state().fund_history
        for fund in funds:
            # dividendes et produits de cession encaissés, nets : ce qui explique sa trésorerie
            # une fois comptés l'épargne confiée, les rachats, les distributions, les frais et
            # les apports (identité de trésorerie, exacte par construction)
            fund.received = fund.cash - (
                fund.subscribed - fund.redeemed - fund.distributed - fund.fees - fund.invested
            )
            history.append({
                "mois": self.month, "fonds": fund.id, "actif": fund_assets(fund, prices),
                "tresorerie": fund.cash, "valeur_part": final[fund.id], "parts": fund.units,
                "participations": sum(1 for cid, q in fund.shares.items() if q > 0.0 and cid in prices),
                "porteurs": sum(1 for p in self.people if p.fund_units.get(fund.id, 0.0) > 0.0),
            })

    def _close_fund_positions(self, fund, prices: dict[str, float]) -> None:
        """Participations soldées d'un fonds (titres vendus, entreprise fermée ou absorbée) :
        retirées du suivi et consignées dans ``fund.exits`` (mesure seule)."""
        companies = {c.id: c for c in self.companies}
        for company_id in list(fund.cost):
            if fund.shares.get(company_id, 0.0) > 1e-9 and company_id in prices:
                continue
            company = companies.get(company_id)
            if company is None or not company.active:
                outcome = {"bankrupt": "faillite", "acquired": "absorbée", "dissolved": "liquidée",
                           "idle": "fermée sans salarié"}.get(company.exit_reason if company else "", "fermée")
            else:
                outcome = "cédée en Bourse" if company.listed else "cédée"
            fund.exits.append({
                "entreprise": company_id, "entree": fund.entry.pop(company_id, self.month),
                "sortie": self.month, "investi": fund.cost.pop(company_id), "issue": outcome,
            })
            fund.bought.pop(company_id, None)

    def _distribute_fund_cash(self, funds, targets, unit_price, prices) -> None:
        """La trésorerie d'un fonds au-delà de sa réserve d'attente (dividendes reçus, produit des
        cessions) est versée aux porteurs de parts, au prorata : revenu du capital du ménage."""
        for fund in funds:
            # lot I : la trésorerie au-delà de la réserve est d'abord placée en actions cotées
            # (``_fund_buy_orders``) ; seul ce qui dépasse deux fois la réserve est reversé
            excess = fund.cash - 2.0 * targets[fund.id]
            if excess <= 1.0 or fund.units <= 1e-9:
                continue
            holders = [(p, p.fund_units[fund.id]) for p in self.people if p.fund_units.get(fund.id, 0.0) > 0.0]
            held = sum(units for _, units in holders)
            if held <= 0.0:
                continue
            for person, units in holders:
                payment = excess * units / held
                transfer_cash(fund, person, payment)
                fund.distributed += payment
                person.income_this_turn += payment
                person.dividend_income_this_turn += payment
                person.lifetime_earnings += payment
            self._enterprise_count("fonds_distributions", excess)
            unit_price[fund.id] = max(0.0, fund_assets(fund, prices)) / fund.units

    def _household_fund_flows(self, funds, targets, unit_price) -> None:
        """Épargne confiée aux fonds. Chaque ménage vise ``fund_savings_share`` × (0,5 + goût du
        risque) de son épargne investissable (trésorerie et parts au-delà de sa réserve de
        consommation) et comble chaque mois une part de l'écart (vitesse de rééquilibrage des
        portefeuilles). Une souscription est bornée par ce que le fonds peut employer, un rachat
        par sa trésorerie ; les parts sont émises et rachetées à leur valeur du mois."""
        live = self.live
        speed = live.household_stock_investment_rate
        share = max(0.0, live.fund_savings_share)
        by_id = {fund.id: fund for fund in funds}
        for index, person in enumerate(self.people):
            held = [(by_id[fid], units) for fid, units in person.fund_units.items() if fid in by_id and units > 0.0]
            value = sum(units * unit_price[fund.id] for fund, units in held)
            buffer = live.savings_cash_buffer_months * max(0.0, person.income_this_turn)
            investable = max(0.0, person.cash + value - buffer)
            wanted = min(1.0, share * (0.5 + person.risk_tolerance)) * investable
            order = speed * (wanted - value)
            if order > 1.0:
                fund = held[0][0] if held else funds[index % len(funds)]
                room = max(0.0, targets[fund.id] - fund.cash)
                amount = min(order, max(0.0, person.cash - buffer), room)
                if amount > 1.0 and unit_price[fund.id] > 0.0:
                    transfer_cash(person, fund, amount)
                    units = amount / unit_price[fund.id]
                    fund.units += units
                    person.fund_units[fund.id] = person.fund_units.get(fund.id, 0.0) + units
                    fund.subscribed += amount
                    self._enterprise_count("fonds_souscriptions", amount)
            elif order < -1.0 and held:
                remaining = -order
                for fund, units in held:
                    amount = min(remaining, units * unit_price[fund.id], max(0.0, fund.cash))
                    if amount <= 1.0:
                        continue
                    redeemed = amount / unit_price[fund.id]
                    transfer_cash(fund, person, amount)
                    fund.units = max(0.0, fund.units - redeemed)
                    left = units - redeemed
                    if left > 1e-9:
                        person.fund_units[fund.id] = left
                    else:
                        person.fund_units.pop(fund.id, None)
                    remaining -= amount
                    fund.redeemed += amount
                    self._enterprise_count("fonds_rachats", amount)

    def _fund_private_companies(self, funds, prices) -> None:
        """Apports des fonds aux entreprises non cotées qui ont besoin de trésorerie, puis
        décision d'introduction en Bourse.

        Le fonds déjà actionnaire (sinon le plus liquide) apporte le besoin, au plus
        ``fund_ticket_share`` de son actif, contre des titres à la valeur estimée (titres en
        réserve de l'entreprise d'abord, titres nouveaux ensuite) ; il n'apporte rien à une
        entreprise en pertes depuis ``bankruptcy_months`` mois. Son premier apport s'accompagne
        d'un appui de gestion (productivité × (1 + ``fund_support_gain``)). L'entreprise demande
        son introduction en Bourse si le besoin restant dépasse ``ipo_need_share`` de ses fonds
        propres, ou quand le fonds entré depuis ``fund_exit_months`` mois veut sortir."""
        live = self.live
        requests = {fund.id: 0.0 for fund in funds}
        for company in self.companies:
            if not company.active or company.listed or company.sector == "bank" or company.parent_id is not None:
                continue
            need = self._financing_need(company)
            backer = next((f for f in funds if f.cost.get(company.id, 0.0) > 0.0), None)
            # sortie décidée sur la valeur (lot I) : la participation vaut ``FUND_EXIT_MULTIPLE``
            # fois ce qu'elle a coûté ; au plus tard après ``fund_exit_months``
            exit_due = company.fund_backed_month >= 0 and (
                self.month - company.fund_backed_month >= max(1, live.fund_exit_months)
                or (backer is not None and backer.shares.get(company.id, 0.0) * company.stock_price
                    >= FUND_EXIT_MULTIPLE * backer.cost[company.id])
            )
            remaining = need
            if need > 1.0 and company.loss_streak < live.bankruptcy_months:
                fund = next((f for f in funds if f.shares.get(company.id, 0.0) > 0.0), None)
                if fund is None:
                    fund = max(funds, key=lambda f: (f.cash, f.id))
                requests[fund.id] += need
                amount = min(need, max(0.0, live.fund_ticket_share) * fund_assets(fund, prices), max(0.0, fund.cash))
                if amount > 1.0:
                    price = max(company.stock_price, 1.0)
                    quantity = amount / price
                    reserved = min(quantity, max(0.0, company.treasury_shares))
                    company.treasury_shares -= reserved
                    company.shares_outstanding += quantity - reserved
                    fund.shares[company.id] = fund.shares.get(company.id, 0.0) + quantity
                    transfer_cash(fund, company, amount)
                    if company.fund_backed_month < 0:
                        company.fund_backed_month = self.month
                        company.productivity_trait = min(
                            1.8, company.productivity_trait * (1.0 + max(0.0, live.fund_support_gain))
                        )
                    remaining = need - amount
                    fund.invested += amount
                    fund.cost[company.id] = fund.cost.get(company.id, 0.0) + amount
                    fund.bought[company.id] = fund.bought.get(company.id, 0.0) + quantity
                    fund.entry.setdefault(company.id, self.month)
                    self._enterprise_count("fonds_apports", amount)
            equity = max(1.0, company_balance_sheet(company, self.companies, live)["Capitaux propres"])
            if exit_due or remaining > max(0.0, live.ipo_need_share) * equity:
                self._list_company(company, remaining)
        for fund in funds:
            fund.requests += 0.25 * (requests[fund.id] - fund.requests)

    def _list_company(self, company, need: float = 0.0) -> None:
        """Introduction en Bourse : l'entreprise devient cotée et offre des titres nouveaux pour
        lever ``need`` (en plus de ses titres en réserve, au plus ``IPO_DILUTION_LIMIT`` de son
        capital) ; la séance les vend à de vrais acheteurs, au cours (``_clear_primary_first``)."""
        company.listed = True
        company.listed_month = self.month
        price = max(company.stock_price, 1.0)
        offered = min(max(0.0, need) / price, IPO_DILUTION_LIMIT * company.shares_outstanding)
        extra = max(0.0, offered - company.treasury_shares)
        company.treasury_shares += extra
        company.shares_outstanding += extra
        self._enterprise_count("introductions")

    def _fund_sell_orders(self) -> list:
        """Ordres de vente de la séance de Bourse hors ménages et État (servis par de vrais
        acheteurs, au cours), ``FUND_EXIT_SPEED`` de la ligne par mois :

        - un fonds vend un titre coté quand son cours atteint sa valeur fondamentale (il garde un
          titre sous-évalué), ou quand sa trésorerie passe sous la moitié de sa réserve (lot I) ;
        - une société mère vend la filiale cotée qu'elle a mise en vente (lot I)."""
        state = getattr(self, "enterprise", None)
        if state is None or not (state.funds or state.divesting):
            return []
        live = self.live
        annual_rate = 12.0 * max(0.0, self.policy_rate)
        listed = {c.id: c for c in self.companies if c.active and c.listed}
        values: dict[str, float] = {}
        orders = []
        for fund in state.funds:
            short = fund.cash < 0.5 * fund.reserve
            for company_id, quantity in fund.shares.items():
                company = listed.get(company_id)
                if company is None or quantity <= 0.0:
                    continue
                if not short:
                    if company_id not in values:
                        values[company_id] = _fundamental_price(company, self.companies, live, annual_rate)
                    if company.stock_price < values[company_id]:
                        continue
                orders.append((fund, company_id, quantity * FUND_EXIT_SPEED if quantity > 1.0 else quantity))
        by_id = {c.id: c for c in self.companies}
        for company_id in sorted(state.divesting):
            company = listed.get(company_id)
            parent = by_id.get(company.parent_id) if company is not None and company.parent_id else None
            if parent is None:
                continue
            quantity = parent.shares.get(company_id, 0.0)
            if quantity > 0.0:
                orders.append((parent, company_id, quantity * FUND_EXIT_SPEED if quantity > 1.0 else quantity))
        return orders

    def _fund_buy_orders(self) -> list | None:
        """Placements cotés des fonds (lot I) : chaque mois, ``FUND_MARKET_SPEED`` de la trésorerie
        d'un fonds au-delà de sa réserve d'attente achète le panier du marché, comme un ménage
        (``stock_market.update_prices_and_trade``, ``extra_buyers``). ``None`` sans fonds."""
        state = getattr(self, "enterprise", None)
        if state is None or not state.funds or not self.live.private_startups:
            return None
        orders = [
            (fund, FUND_MARKET_SPEED * (fund.cash - fund.reserve))
            for fund in state.funds if fund.cash - fund.reserve > 1.0
        ]
        return orders or None

    def _value_private_companies(self) -> None:
        """Valeur estimée d'une action non cotée : rappelée vers sa valeur fondamentale à la vitesse
        des cours de Bourse (même limite de ± 15 % par mois), sans carnet d'ordres."""
        live = self.live
        annual_rate = 12.0 * max(0.0, self.policy_rate)
        for company in self.companies:
            if not company.active or company.listed or company.shares_outstanding <= 0.0:
                continue
            opening = max(company.stock_price, 1.0)
            fundamental = _fundamental_price(company, self.companies, live, annual_rate)
            price = opening + live.stock_adjustment_speed * (fundamental - opening)
            company.stock_price = max(1.0, opening * 0.85, min(opening * 1.15, price))

    # ------------------------------------------------------------------ phase 7 : groupes
    def _distribution_synergy(self, buyer, target, network: dict[str, float]) -> float:
        """Hausse des ventes qu'une cible de l'alimentation ou des biens tirerait des magasins de
        l'acquéreur (G7) ; nulle sans magasins ni distribution de groupe."""
        live = self.live
        if not (live.group_distribution and live.retail_stores) or target.sector not in CONSUMER_SECTORS:
            return 0.0
        own = network.get(target.id, target.stores)
        added = network.get(buyer.id, buyer.stores)
        if added <= 0.0:
            return 0.0
        return store_factor(own + added, live) / store_factor(own, live) - 1.0

    def _management_synergy(self, buyer, target) -> float:
        """Gain qu'un acquéreur mieux géré attend d'une cible : la part de l'écart de productivité
        qu'il lui transmettra (``MANAGEMENT_TRANSFER``), écart borné à 50 % ; nul s'il n'est pas
        plus productif qu'elle."""
        if buyer.productivity_trait <= target.productivity_trait:
            return 0.0
        gap = buyer.productivity_trait / max(target.productivity_trait, 1e-9) - 1.0
        return MANAGEMENT_TRANSFER * min(0.5, gap)

    def _board_hurdle(self, company) -> float:
        """Exigence du conseil d'actionnaires sur un investissement (lot I) : 1 sans conseil ; de
        1 + ``BOARD_HURDLE`` / 2 pour un conseil prudent à 1 − ``BOARD_HURDLE`` / 2 pour un
        conseil audacieux (goût du risque moyen de ses membres, pondéré par leurs titres)."""
        if not self.live.shareholder_governance:
            return 1.0
        state = getattr(self, "enterprise", None)
        appetite = state.appetite.get(company.id, 0.5) if state is not None else 0.5
        return 1.0 + BOARD_HURDLE * (0.5 - appetite)

    def _integration_months(self, buyer) -> int:
        if self.live.experience_learning:
            return max(2, INTEGRATION_MONTHS - buyer.acquisition_count)
        return INTEGRATION_MONTHS

    def _conglomerate_takeovers(self) -> int:
        """Rachats entre secteurs (G5) : la cible devient une filiale, sans fusion.

        Mêmes règles que ``consolidation.takeover_by_value`` : offre au cours × (1 +
        ``control_premium``) pour atteindre ``control_threshold`` des titres en circulation, payée
        par la trésorerie libre, lancée si la valeur estimée dépasse le prix, acceptée par les
        actionnaires selon leur propre estimation. L'acquéreur est une entreprise indépendante,
        rentable, d'au moins ``GROUP_MIN_AGE_MONTHS`` mois ; la cible est indépendante, d'un autre
        secteur (ni banque), et vaut au plus ``GROUP_SIZE_RATIO`` de sa capitalisation.

        Ce que l'acquéreur apporte fait la valeur de l'opération : ses méthodes de gestion s'il
        est plus productif que la cible (elle rattrape ``MANAGEMENT_TRANSFER`` de l'écart une fois
        rachetée) et, avec la distribution de groupe (G7), les ventes que ses magasins apportent à
        un producteur. Il ne lance pas d'offre sous la valeur fondamentale du titre, qu'un
        actionnaire informé refuserait : sans rien à apporter, aucune offre. L'État n'apporte pas
        ses titres ; les entreprises sous contrôle public ne lancent ni ne reçoivent d'offre."""
        live, month = self.live, self.month
        annual_rate = 12.0 * max(0.0, self.policy_rate)
        excluded = self._public_controlled_ids()
        state_account = self.public.account
        holders = [*self.people, *self.companies, *self._outside_shareholders()]
        parents = {c.parent_id for c in self.companies if c.active and c.parent_id is not None}
        network = self._network_stores()
        bids = self._enterprise_state().bids
        fundamentals: dict[str, float] = {}
        completed = 0
        for buyer in sorted(self.companies, key=lambda c: (-c.cash, c.id)):
            if (
                not buyer.active or buyer.sector == "bank" or buyer.id in excluded
                or buyer.parent_id is not None or buyer.age_months < GROUP_MIN_AGE_MONTHS
                or buyer.previous_profit <= 0.0
                or month - buyer.last_acquisition_month < self._integration_months(buyer)
                # une offre ratée fait attendre avant la suivante, sans geler ses autres opérations
                or month - bids.get(buyer.id, -1000) < INTEGRATION_MONTHS
            ):
                continue
            free_cash = buyer.cash - working_capital(buyer, live)
            if free_cash <= 0.0:
                continue
            limit = GROUP_SIZE_RATIO * buyer.stock_price * buyer.shares_outstanding
            best = None
            for target in self.companies:
                if (
                    not target.active or target is buyer or target.sector in ("bank", buyer.sector)
                    or target.id in excluded or target.parent_id is not None or target.id in parents
                    or target.stock_price <= 0.0
                    or target.stock_price * target.shares_outstanding > limit
                ):
                    continue
                circulating = target.shares_outstanding - target.treasury_shares
                need = live.control_threshold * circulating - buyer.shares.get(target.id, 0.0)
                if circulating <= 0.0 or need <= 0.0:
                    continue
                offer = target.stock_price * (1.0 + live.control_premium)
                if need * offer > free_cash:
                    continue
                if target.id not in fundamentals:
                    fundamentals[target.id] = _fundamental_price(target, self.companies, live, annual_rate)
                if offer < fundamentals[target.id]:
                    # un actionnaire qui connaît la valeur de son titre refuserait : pas d'offre
                    continue
                value = fundamentals[target.id] * (
                    1.0 + self._distribution_synergy(buyer, target, network)
                    + self._management_synergy(buyer, target)
                )
                # le conseil de l'acquéreur vote l'offre : prudent, il exige une marge sur le prix
                hurdle = offer * self._board_hurdle(buyer)
                surplus = (value - hurdle) * need
                if value > hurdle and (best is None or surplus > best[0]):
                    best = (surplus, target, need, offer)
            if best is None:
                continue
            _, target, need, offer = best
            self._enterprise_count("offres_groupe")
            base = max(target.stock_price, fundamentals[target.id])
            tendered = [
                (holder, holder.shares[target.id] - _employee_stake(holder, target.id))
                for holder in holders
                if holder is not buyer and holder is not target and holder is not state_account
                and holder.shares.get(target.id, 0.0) - _employee_stake(holder, target.id) > 0.0
                and offer >= _holder_valuation(holder, base, live)
            ]
            supply = sum(quantity for _, quantity in tendered)
            bids[buyer.id] = month
            if supply < need:
                continue  # offre ratée : pas d'échange
            buyer.last_acquisition_month = month
            for holder, quantity in tendered:
                bought = need * quantity / supply
                left = holder.shares[target.id] - bought
                if left > 1e-9:
                    holder.shares[target.id] = left
                else:
                    holder.shares.pop(target.id, None)
                transfer_cash(buyer, holder, bought * offer)
                buyer.shares[target.id] = buyer.shares.get(target.id, 0.0) + bought
            # achat de titres existants : un transfert, pas un investissement (hors PIB)
            buyer.secondary_asset_purchases += need * offer
            target.parent_id = buyer.id
            self._enterprise_state().group_log[target.id] = {
                "mere": buyer.id, "mois": month, "origine": "rachetée", "cout": need * offer,
                "productivite": target.productivity_trait, "dividendes": 0.0,
            }
            if buyer.productivity_trait > target.productivity_trait:
                # la société mère transmet ses méthodes : part de l'écart de productivité comblée
                share = MANAGEMENT_TRANSFER
                if live.experience_learning:
                    share += (1.0 - share) * experience_skill(buyer.acquisition_count, live)
                target.productivity_trait = min(
                    1.8, target.productivity_trait + share * (buyer.productivity_trait - target.productivity_trait)
                )
            parents.add(buyer.id)
            buyer.acquisition_count += 1
            completed += 1
        if completed:
            self._enterprise_count("rachats_groupe", completed)
        return completed

    def _after_startups(self, created: list) -> None:
        """Entreprises fondées ce mois par des ménages : expérience du fondateur (G1) et statut
        non coté (G8, sauf une banque, qui lève ses fonds propres en Bourse)."""
        live = self.live
        if not created or not (live.experience_learning or live.private_startups):
            return
        people = {p.id: p for p in self.people}
        for company in created:
            founder = people.get(company.founder_id or "")
            if live.experience_learning and founder is not None:
                skill = experience_skill(founder.ventures, live)
                if skill > 0.0:
                    lift = 1.0 + max(0.0, live.experience_gain) * skill
                    company.productivity_trait = min(1.8, company.productivity_trait * lift)
                    company.innovation = min(1.0, company.innovation * lift)
                    # expérience de direction reconnue par la banque (crédit d'exploitation)
                    company.ceo_experience += int(round(10.0 * skill))
                founder.ventures += 1
            if live.private_startups and company.sector != "bank":
                company.listed = False

    def _create_subsidiaries(self, entry: dict) -> int:
        """Diversification (G4) : au plus une filiale par mois dans l'économie.

        Une entreprise indépendante, rentable, d'au moins ``GROUP_MIN_AGE_MONTHS`` mois, dont la
        trésorerie libre vaut deux fois le capital minimal d'une fondation, crée une filiale dans
        le secteur le plus attractif autre que le sien (``finance.sector_entry_signals``, les
        signaux que voient les ménages fondateurs), si cet attrait atteint ``entry_signal_scale``.
        Elle lui apporte la moitié de sa trésorerie libre (au plus l'apport maximal d'un
        fondateur) et détient tout son capital ; la filiale, non cotée, reprend ses traits de
        gestion et suit ensuite les règles communes (elle peut échouer). Aucun tirage."""
        live, month = self.live, self.month
        excluded = self._public_controlled_ids()
        scale = self._wage_scale()
        required = live.startup_threshold * scale ** live.startup_threshold_scaling
        signals = None
        targets = None
        for parent in sorted(self.companies, key=lambda c: (-c.cash, c.id)):
            if (
                not parent.active or parent.sector == "bank" or parent.id in excluded
                or parent.parent_id is not None or not parent.employees
                or parent.age_months < GROUP_MIN_AGE_MONTHS or parent.previous_profit <= 0.0
                or month - parent.last_acquisition_month < self._integration_months(parent)
            ):
                continue
            free = parent.cash - working_capital(parent, live)
            if free < 2.0 * required:
                break  # classées par trésorerie : les suivantes en ont moins
            if signals is None:
                signals = sector_entry_signals(
                    self.companies, live, self.wage_reference, SUBSIDIARY_SECTORS, self.policy_rate,
                    entry.get("unserved"), entry.get("input_cost_index", 1.0), entry.get("refused_orders"),
                )
                targets = workforce_targets(live)
            present = {parent.sector} | {
                c.sector for c in self.companies if c.active and c.parent_id == parent.id
            }
            options = [s for s in SUBSIDIARY_SECTORS if s not in present]
            if not options:
                continue
            sector = max(options, key=lambda s: (signals.get(s, 0.0), s))
            # le conseil vote la création : prudent, il attend un attrait plus fort
            if signals.get(sector, 0.0) < live.entry_signal_scale * self._board_hurdle(parent):
                continue
            contribution = min(free * 0.5, live.startup_max_capital * scale)
            market_prices = entry.get("market_prices") or {}
            subsidiary = Company(
                id=f"filiale_{month}_{parent.id}",
                name=f"{_SECTOR_LABELS.get(sector, sector)} {parent.name} (filiale)",
                sector=sector,
                cash=0.0,
                wage_offer=_entrant_wage_offer(self.companies, sector, self.wage_reference, live),
                target_employees=targets[sector],
                price=(
                    market_prices[sector] if sector in market_prices
                    else live.starting_food_price if sector == "food" else live.starting_goods_price
                ),
                stock_price=max(1.0, contribution / 1000.0),
                previous_stock_price=max(1.0, contribution / 1000.0),
                shares_outstanding=1000.0,
                treasury_shares=0.0,
                productivity_trait=parent.productivity_trait,
                quality_trait=parent.quality_trait,
                risk_tolerance=parent.risk_tolerance,
                pricing_aggression=parent.pricing_aggression,
                reinvestment_preference=parent.reinvestment_preference,
                innovation=parent.innovation,
                construction_pipeline=[],
                parent_id=parent.id,
                listed=False,
            )
            transfer_cash(parent, subsidiary, contribution)
            parent.shares[subsidiary.id] = 1000.0
            self._enterprise_state().group_log[subsidiary.id] = {
                "mere": parent.id, "mois": month, "origine": "créée", "cout": contribution,
                "productivite": subsidiary.productivity_trait, "dividendes": 0.0,
            }
            parent.last_acquisition_month = month
            self.companies.append(subsidiary)
            self._enterprise_count("filiales_creees")
            return 1
        return 0

    # ------------------------------------------------------------------ fin de mois
    def _enterprise_housekeeping(self) -> None:
        """Liens de groupe revus (une société mère fermée, rachetée ou passée sous le seuil de
        contrôle perd sa filiale ; le nouvel actionnaire de contrôle la reprend), filiales
        orphelines introduites en Bourse, puis relevé du mois (mesures seules)."""
        live = self.live
        by_id = {c.id: c for c in self.companies}
        log = self._enterprise_state().group_log
        upstream = 0.0
        for company in self.companies:
            if not company.active:
                if company.stores:
                    company.stores = 0.0
                if company.id in log:
                    self._close_group_link(company, log.pop(company.id))
                continue
            if company.parent_id is not None:
                entry = log.get(company.id)
                holder = by_id.get(company.parent_id)
                held = company.shares_outstanding - company.treasury_shares
                if entry is not None and holder is not None and held > 0.0 and company.dividend_paid > 0.0:
                    # dividende du mois remonté à la société mère (mesure seule)
                    paid = company.dividend_paid * min(1.0, holder.shares.get(company.id, 0.0) / held)
                    entry["dividendes"] += paid
                    upstream += paid
                circulating = company.shares_outstanding - company.treasury_shares
                control = live.control_threshold * circulating - 1e-9
                parent = by_id.get(company.parent_id)
                # le lien tient tant que la société mère garde les deux tiers du seuil de contrôle
                # (une émission de titres ou une attribution aux salariés ne le rompt pas aussitôt)
                if (parent is None or not parent.active
                        or parent.shares.get(company.id, 0.0) < CONTROL_RETENTION * control):
                    owner = max(
                        (c for c in self.companies
                         if c.active and c is not company and c.sector != "bank"
                         and c.shares.get(company.id, 0.0) >= max(control, 1e-9)),
                        key=lambda c: (c.shares[company.id], c.id), default=None,
                    )
                    company.parent_id = owner.id if owner is not None else None
                    if company.id in log:
                        self._close_group_link(company, log.pop(company.id))
                    if owner is not None:
                        log[company.id] = {
                            "mere": owner.id, "mois": self.month, "origine": "reprise", "cout": 0.0,
                            "productivite": company.productivity_trait, "dividendes": 0.0,
                        }
            if company.parent_id is None and not company.listed and company.founder_id is None:
                # ancienne filiale sans société mère : ses titres doivent pouvoir s'échanger
                self._list_company(company)
        state = self._enterprise_state()
        stats = state.month_stats
        prices = {c.id: c.stock_price for c in self.companies if c.active}
        active = [c for c in self.companies if c.active]
        employed = [p for p in self.people if p.employer_id not in (None, "state")]
        heads = {c.parent_id for c in active if c.parent_id is not None}
        grouped = [c for c in active if c.parent_id is not None or c.id in heads]
        firms = [c for c in active if c.sector != "bank"]
        stats.update(
            mois=self.month,
            groupes=len(heads),
            emploi_groupes=(
                sum(len(c.employees) for c in grouped) / max(1, sum(len(c.employees) for c in firms))
            ),
            ca_groupes=sum(c.revenue for c in grouped) / max(1.0, sum(c.revenue for c in firms)),
            dividendes_filiales=upstream,
            sans_salarie=sum(1 for c in firms if not c.employees),
            magasins=sum(c.stores for c in active),
            filiales=sum(1 for c in active if c.parent_id is not None),
            non_cotees=sum(1 for c in active if not c.listed),
            connaissance_clients=(
                sum(c.market_insight for c in active if c.sector in CONSUMER_SECTORS)
                / max(1, sum(1 for c in active if c.sector in CONSUMER_SECTORS))
            ),
            fonds_actif=sum(fund_assets(fund, prices) for fund in state.funds),
            fonds_tresorerie=sum(fund.cash for fund in state.funds),
            fonds_porteurs=sum(1 for p in self.people if p.fund_units),
            negociation=(
                sum(p.negotiated_factor for p in employed) / len(employed) if employed else 1.0
            ),
        )
        state.history.append(stats)
        state.month_stats = {}

    def _close_group_link(self, company, entry: dict) -> None:
        """Filiale sortie de son groupe : consignée avec son issue (mesure seule)."""
        state = self._enterprise_state()
        if not company.active:
            outcome = {"bankrupt": "faillite", "acquired": "absorbée", "dissolved": "liquidée",
                       "idle": "fermée sans salarié"}.get(company.exit_reason, "fermée")
        else:
            outcome = "vendue en Bourse" if company.id in state.divesting else "contrôle perdu"
        state.divesting.discard(company.id)
        self._enterprise_state().group_exits.append(
            {**entry, "filiale": company.id, "sortie": self.month, "issue": outcome}
        )

    # ------------------------------------------------------------------ lot H : salariés obligatoires
    def _staffless_closures(self, holders: tuple) -> frozenset:
        """Entreprises non bancaires restées ``staffless_closure_months`` mois sans salarié.

        Sans dette ni actif physique, elle est liquidée à l'amiable tout de suite (trésorerie
        versée aux actionnaires, ``finance.dissolve_company``). Sinon son identifiant est rendu :
        ``finance.resolve_distressed_companies`` la liquide comme une entreprise insolvable (actifs
        vendus à un concurrent, banques remboursées, reste aux actionnaires). Jamais une banque,
        une participation de l'État, ni la dernière entreprise active de son secteur."""
        from economy.finance import can_dissolve, dissolve_company

        months = self.live.staffless_closure_months
        inst = getattr(self, "institutions", None)
        protected = set(inst.holdings) if inst is not None else set()
        forced = []
        for company in self.companies:
            if (
                not company.active or company.sector == "bank" or company.employees
                or company.idle_months < months or company.id in protected
            ):
                continue
            if not any(o.active and o.id != company.id and o.sector == company.sector for o in self.companies):
                continue
            # lot I : reprise par la société mère, sinon vente à un concurrent ; liquidation en
            # dernier recours
            if self._rescue_by_parent(company) or self._sell_to_competitor(company, holders, protected):
                continue
            if can_dissolve(company):
                dissolve_company(company, self.people, self.companies, holders,
                                 self.month, self.resolution_events)
                company.exit_reason = "idle"
                self._enterprise_count("fermetures_sans_salarie")
            else:
                forced.append(company.id)
        return frozenset(forced)

    def _rescue_by_parent(self, company) -> bool:
        """Filiale sans salarié reprise par sa société mère : la mère souscrit des titres nouveaux,
        au cours, pour trois mois de la paie visée (réserve d'embauche), si sa trésorerie libre
        les paie ; la filiale a de nouveau ``staffless_closure_months`` mois pour embaucher. Une
        seule reprise par filiale tous les ``RESCUE_INTERVAL_MONTHS`` mois."""
        parent = next((c for c in self.companies if c.id == company.parent_id and c.active), None)
        if parent is None:
            return False
        rescues = self._enterprise_state().rescues
        if self.month - rescues.get(company.id, -10 ** 6) < RESCUE_INTERVAL_MONTHS:
            return False
        amount = 3.0 * max(1, company.target_employees) * max(company.wage_offer, self.live.minimum_wage, 1.0)
        if parent.cash - working_capital(parent, self.live) < amount:
            return False
        self._subscribe_subsidiary_shares(parent, company, amount)
        rescues[company.id] = self.month
        company.idle_months = 0
        self._enterprise_count("reprises_par_la_mere")
        return True

    def _subscribe_subsidiary_shares(self, parent, company, amount: float) -> None:
        """Augmentation de capital d'une filiale souscrite par sa société mère, au cours."""
        quantity = amount / max(company.stock_price, 1.0)
        company.shares_outstanding += quantity
        parent.shares[company.id] = parent.shares.get(company.id, 0.0) + quantity
        transfer_cash(parent, company, amount)
        entry = self._enterprise_state().group_log.get(company.id)
        if entry is not None:
            entry["cout"] += amount

    def _sell_to_competitor(self, company, holders: tuple, protected: set) -> bool:
        """Entreprise sans salarié vendue à un concurrent : l'entreprise du même secteur qui a des
        salariés et le plus de trésorerie l'absorbe (``consolidation._merge_into`` : actifs,
        dettes et trésorerie repris, actionnaires payés en titres de l'acquéreur au rapport des
        cours), si les actifs de la cible couvrent ses dettes. Ni acquéreur ni secteur sous
        contrôle public ; le secteur garde au moins ``min_firms_per_sector`` entreprises."""
        live = self.live
        if company_balance_sheet(company, self.companies, live)["Capitaux propres"] < 0.0:
            return False
        rivals = [
            c for c in self.companies
            if c.active and c is not company and c.sector == company.sector and c.employees
            and c.id not in protected
        ]
        sector_size = sum(1 for c in self.companies if c.active and c.sector == company.sector)
        if not rivals or sector_size <= max(1, live.min_firms_per_sector):
            return False
        buyer = max(rivals, key=lambda c: (c.cash, c.id))
        _merge_into(buyer, company, self.people, self.companies, holders)
        company.exit_month = self.month
        buyer.acquisition_count += 1
        self._enterprise_count("reprises_par_un_concurrent")
        return True

    # ------------------------------------------------------------------ lot I : vie des groupes
    def _manage_subsidiaries(self) -> None:
        """Chaque mois, une société mère décide du sort de chaque filiale :

        - **mise en vente** : la filiale perd de l'argent depuis ``SUBSIDIARY_LOSS_MONTHS`` mois, ou
          la mère elle-même manque de trésorerie (sous son fonds de roulement) ; la filiale est
          introduite en Bourse si besoin et la mère vend ses titres à la séance, par dixièmes
          (``_fund_sell_orders``) ; le lien de groupe tombe quand elle passe sous le contrôle ;
        - **recapitalisation** : sinon, si la filiale a un besoin de trésorerie
          (``_financing_need``) et n'est pas en pertes durables, la mère souscrit des titres
          nouveaux au cours, au plus ``SUBSIDIARY_SUPPORT_SHARE`` de sa trésorerie libre."""
        live = self.live
        state = self._enterprise_state()
        by_id = {c.id: c for c in self.companies}
        for company in self.companies:
            if not company.active or company.parent_id is None:
                continue
            parent = by_id.get(company.parent_id)
            if parent is None or not parent.active:
                continue
            free = parent.cash - working_capital(parent, live)
            if company.id in state.divesting:
                continue
            if company.loss_streak >= SUBSIDIARY_LOSS_MONTHS or free < 0.0:
                state.divesting.add(company.id)
                if not company.listed:
                    self._list_company(company)
                self._enterprise_count("filiales_mises_en_vente")
                continue
            if company.loss_streak >= live.bankruptcy_months or company.sector == "bank":
                continue
            need = self._financing_need(company)
            amount = min(need, SUBSIDIARY_SUPPORT_SHARE * max(0.0, free))
            if amount > 1.0:
                self._subscribe_subsidiary_shares(parent, company, amount)
                self._enterprise_count("recapitalisations", amount)

    # ------------------------------------------------------------------ lot I : dirigeants
    def _executive_learning(self) -> None:
        """Expérience du dirigeant (G1, lot I) : à chaque anniversaire de mandat, l'entreprise
        gagne en productivité ``EXECUTIVE_LEARNING`` × ce qui reste à apprendre au dirigeant
        (1 − compétence acquise, années de mandat / (années + ``experience_half_count``)) :
        environ +0,4 % la première année, de moins en moins ensuite ; productivité bornée à 1,8.
        Un dirigeant remplacé repart de zéro (``ceo_tenure``)."""
        live = self.live
        for company in self.companies:
            if not company.active or company.executive_id is None or company.ceo_tenure <= 0:
                continue
            if company.ceo_tenure % 12:
                continue
            years = company.ceo_tenure / 12.0
            gain = EXECUTIVE_LEARNING * (1.0 - experience_skill(years, live))
            company.productivity_trait = min(1.8, company.productivity_trait * (1.0 + gain))

    def _mark_staffless_closures(self, closures: frozenset) -> None:
        """Motif de fermeture des entreprises liquidées d'office (affichage et relevés)."""
        for company in self.companies:
            if company.id in closures and not company.active:
                company.exit_reason = "idle"
                self._enterprise_count("fermetures_sans_salarie")

    # ------------------------------------------------------------------ lot H : conseil d'actionnaires
    def shareholder_board(self, company, seats: int | None = None) -> list[tuple]:
        """Les plus gros actionnaires d'une entreprise : (porteur, titres, part du capital émis),
        du plus gros au plus petit (ménages, entreprises, État, fonds). Lecture seule."""
        seats = self.live.board_seats if seats is None else seats
        held = [
            (holder, holder.shares[company.id])
            for holder in (*self.people, *self.companies, *self._outside_shareholders())
            if holder is not company and holder.shares.get(company.id, 0.0) > 0.0
        ]
        total = sum(quantity for _, quantity in held)
        held.sort(key=lambda item: (-item[1], item[0].id))
        return [(holder, quantity, quantity / total) for holder, quantity in held[:max(1, seats)]]

    def _shareholder_governance(self) -> None:
        """Conseil d'actionnaires de chaque entreprise active (avant ``elect_executives``).

        - **Révocation** : après douze mois de mandat, chaque membre vote la révocation du
          dirigeant si l'entreprise perd de l'argent depuis plus longtemps que sa patience
          (``board_patience_months`` ; un ménage : × (0,5 + son goût du risque)). Majorité des
          titres du conseil.
        - **Nomination** (poste vacant ou dirigeant révoqué) : un membre salarié de l'entreprise
          vote pour lui-même ; les autres pour le salarié le mieux noté (compétence, ancienneté,
          expérience de direction, sans tirage). Le plus de titres l'emporte.
        - **Bénéfices** : la part réinvestie se rapproche de 5 % par mois de celle que veut le
          conseil : la préférence de l'entreprise avant le conseil × (0,5 + goût du risque moyen
          pondéré de ses membres ; 0,5 pour l'État, une entreprise ou un fonds : neutre). Elle
          reste donc entre la moitié et une fois et demie de sa valeur de départ.
        Sans salarié, le poste reste vacant."""
        from economy.models import Person

        live = self.live
        people = {p.id: p for p in self.people}
        patience = max(1, live.board_patience_months)
        boards = self._enterprise_state().boards
        boards.clear()
        retention = self._enterprise_state().retention
        holdings: dict[str, list] = {}
        for holder in (*self.people, *self.companies, *self._outside_shareholders()):
            for company_id, quantity in holder.shares.items():
                if quantity > 0.0 and company_id != holder.id:
                    holdings.setdefault(company_id, []).append((holder, quantity))
        seats = max(1, live.board_seats)
        for company in self.companies:
            held = holdings.get(company.id)
            if not company.active or not held:
                continue
            held.sort(key=lambda item: (-item[1], item[0].id))
            board = held[:seats]
            weight = sum(quantity for _, quantity in board)
            boards[company.id] = [(holder.id, quantity) for holder, quantity in board]
            current = people.get(company.executive_id or "")
            in_office = current is not None and current.employer_id == company.id
            dismissed = False
            if in_office and company.ceo_tenure >= 12 and company.loss_streak > 0:
                against = sum(
                    quantity for holder, quantity in board
                    if company.loss_streak >= (
                        max(1, round(patience * (0.5 + holder.risk_tolerance)))
                        if isinstance(holder, Person) else patience
                    )
                )
                dismissed = against > 0.5 * weight
            if dismissed or not in_office:
                candidates = [
                    people[pid] for pid in company.employees
                    if pid in people and not (dismissed and pid == current.id)
                ]
                if candidates:
                    best = max(candidates, key=lambda p: (
                        p.skill * (1.0 + min(p.employer_tenure, 120) / 240.0)
                        + p.leadership_experience / 600.0, p.id,
                    ))
                    eligible = {p.id for p in candidates}
                    votes: dict[str, float] = {}
                    for holder, quantity in board:
                        choice = holder.id if holder.id in eligible else best.id
                        votes[choice] = votes.get(choice, 0.0) + quantity
                    company.executive_id = max(votes, key=lambda pid: (votes[pid], pid))
                    company.ceo_tenure = 0
                    self._enterprise_count("dirigeants_nommes")
                    if dismissed:
                        self._enterprise_count("dirigeants_revoques")
            appetite = sum(
                quantity * (holder.risk_tolerance if isinstance(holder, Person) else 0.5)
                for holder, quantity in board
            ) / weight
            self._enterprise_state().appetite[company.id] = appetite
            wanted = max(0.05, min(1.0, retention.setdefault(
                company.id, company.reinvestment_preference) * (0.5 + appetite)))
            company.reinvestment_preference += 0.05 * (wanted - company.reinvestment_preference)

    def enterprise_history(self) -> list[dict]:
        """Relevés mensuels du lot G depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "enterprise", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [
            {**row, "periode": row["mois"] - offset}
            for row in state.history if row["mois"] - offset >= 0
        ]
