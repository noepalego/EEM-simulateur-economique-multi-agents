"""Lots L, N, O, P et Q (novembre 2026) : un monde de plusieurs régions.

Un ``World`` porte plusieurs ``EconomySimulation`` complètes, une par région : chacune a ses
ménages, ses entreprises, ses banques, sa banque centrale, son gouvernement élu et sa monnaie.
Le moteur d'une région n'est pas modifié : un monde d'une seule région, sans profil, est
identique à ``EconomySimulation(base)``. Chaque mois, toutes les régions avancent d'un mois, puis
une phase entre régions s'exécute (après ``integration_month`` mois d'isolement).

- **Lot L, régions** : profils (population, productivités, ressources, orientation politique de
  départ, nombre d'entreprises) qui donnent à chaque région son tissu propre.
- **Lot N, commerce** : une région achète aux autres l'alimentation et les biens que son marché
  n'a pas servis, commandés sur la production du mois suivant, au prix converti et
  majoré du transport.
- **Lot O, mobilité** : un ménage peut travailler ailleurs tout en gardant sa résidence, par
  un mandataire salarié, ou déménager après vente de son logement et remboursement de ses
  crédits. Il compare les revenus après impôt et conserve ses titres dans le registre du monde.
- **Lot P, groupes multirégionaux** : une entreprise rentable ramasse mois après mois, auprès
  des ménages d'une autre région, les titres d'un concurrent plus petit et moins productif ;
  au-delà de la moitié du capital, elle le contrôle, lui transmet un quart de son avance de
  productivité et en reçoit les dividendes.
- **Lot Q, politique** : un gouvernement social bloque le rachat étranger d'une entreprise d'un
  secteur critique ; les électeurs comparent la croissance de leur région à celle des autres ;
  les gouvernements non sociaux tiennent compte de la fiscalité de leurs voisins.

**Monnaie.** Aucune monnaie ne passe d'une région à l'autre. Chaque région a une chambre de
compensation : le payeur verse dans celle de sa région, le bénéficiaire est payé par celle de la
sienne, au taux de change. Une chambre ne détient pas plus de ``clearing_holding_share`` du PIB
annuel de sa région au-delà de ce qu'elle doit reverser : une région ne peut durablement payer à
l'extérieur que ce qu'elle en reçoit. Le taux de change d'une région baisse quand elle paie plus
qu'elle ne reçoit. Les titres détenus hors de leur région sont portés par un compte de
non-résidents de la région de l'entreprise, qui encaisse les dividendes et les reverse.
"""

from __future__ import annotations

import dataclasses
import math
import os
import random
import sys
import threading
from dataclasses import dataclass, field

from economy import diagnostics
from economy.goods import household_budget
from economy.models import Company, Person, SimulationConfig
from economy.payments import transfer_cash
from economy.treasury import working_capital

WORLD_SEED_OFFSET = 160481183
REGION_SEED_STEP = 104729
TRADED = (("food", "inventory", "unmet_food_demand"), ("goods", "goods_inventory", "unmet_goods_demand"))
RATE_BOUNDS = (0.2, 5.0)
MIGRANT_MAX_AGE_MONTHS = 45 * 12    # au-delà, un ménage ne change plus de région
MINIMUM_POPULATION = 40             # une région ne se vide pas en dessous
BID_PREMIUM = 0.30                  # prime offerte aux actionnaires de la cible
BID_SPEND = 0.25                    # part de sa trésorerie libre qu'un acquéreur engage par mois
BID_MONTHS = 36                     # durée après laquelle un ramassage inabouti est abandonné
BID_MIN_AGE = 24                    # ancienneté d'un acquéreur
BID_SIZE = 0.5                      # capitalisation maximale de la cible, en part de celle de l'acquéreur
CONTROL = 0.5                       # part du capital qui donne le contrôle
PRODUCTIVITY_TRANSFER = 0.25        # part de l'avance de productivité transmise à l'entreprise contrôlée
BID_MIN_GAP = 0.10                  # avance de productivité relative sans laquelle l'acquéreur n'apporte rien
BID_REST_MONTHS = 24                # délai entre deux opérations d'un même acquéreur
SCREENING_ORIENTATION = 65.0        # orientation au-delà de laquelle un gouvernement filtre les rachats
YARDSTICK_BOUND = 0.2               # écart de croissance maximal pris en compte par les électeurs

REGION_PROFILES = (
    {"nom": "Nord", "description": "industriel, au centre droit",
     "population": 1.0, "set": {"initial_political_orientation": 45.0},
     "scale": {"goods_productivity": 1.15}},
    {"nom": "Sud", "description": "agricole, au centre gauche",
     "population": 0.8, "set": {"initial_political_orientation": 60.0},
     "scale": {"food_productivity": 1.20, "land_units": 1.3}},
    {"nom": "Littoral", "description": "technologique, libéral",
     "population": 0.7, "set": {"initial_political_orientation": 30.0},
     "scale": {"technology_productivity": 1.25}},
    {"nom": "Bassin", "description": "minier et énergétique, social",
     "population": 0.6, "set": {"initial_political_orientation": 70.0},
     "scale": {"raw_materials": 2.0, "energy_output_per_worker": 1.2}},
)


_REGION_PHASES = ("_credit_and_labor", "_consumer_markets", "_public_sector",
                  "_investment_and_housing", "_close_books", "_industry_dynamics")
_pool = None
_pool_lock = threading.Lock()


def parallel_regions() -> bool:
    """Les régions avancent-elles chacune dans son fil ?

    Oui sur un Python sans verrou global (« free-threaded », 3.13t et suivants) : les fils
    calculent alors réellement en même temps, un cœur par région. Non sur un Python ordinaire,
    où des fils n'apporteraient rien. ``ECONOMY_PARALLEL_REGIONS`` force le choix (``1`` ou
    ``0``), pour les tests d'identité et les mesures. Jamais pendant un diagnostic, dont les
    compteurs sont communs au processus. Les résultats sont les mêmes dans les deux cas :
    pendant leurs phases, les régions n'échangent rien."""
    forced = os.environ.get("ECONOMY_PARALLEL_REGIONS")
    if diagnostics.active():
        return False
    if forced in ("0", "1"):
        return forced == "1"
    gil_enabled = getattr(sys, "_is_gil_enabled", None)
    return gil_enabled is not None and not gil_enabled()


def _region_pool(count: int):
    """Fils partagés par les mondes du processus (jamais rangés dans un ``World``, qui doit
    rester sérialisable), ou ``None`` : calcul l'une après l'autre."""
    global _pool
    if count < 2 or not parallel_regions():
        return None
    with _pool_lock:
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="region")  # quatre régions au plus
        return _pool


def _begin_region(sim, flows) -> None:
    sim._begin_month(flows)


def _finish_region(sim, flows):
    """Phases 2 à 8 d'une région, dans leur ordre (après le rapprochement des frontaliers)."""
    for phase in _REGION_PHASES:
        getattr(sim, phase)(flows)
    return sim._record_month(flows)


def _step_region(sim):
    return sim.step()


@dataclass
class WorldConfig:
    """Réglages du monde. ``base`` : réglages communs à toutes les régions."""

    base: SimulationConfig = field(default_factory=SimulationConfig)
    regions: int = 3
    profiles: tuple | None = None           # ``None`` : ``REGION_PROFILES`` ; ``()`` : régions identiques
    integration_month: int = 0              # mois d'isolement avant l'ouverture des échanges
    interregional_trade: bool = True
    transport_cost: float = 0.10            # majoration du prix d'un produit venu d'une autre région
    migration: bool = True
    migration_monthly_share: float = 0.005  # part maximale des ménages d'une région qui partent par mois
    migration_gain: float = 0.15            # gain de revenu réel attendu qui décide un départ
    commuting: bool = True                 # travailler ailleurs sans changer de résidence
    commuting_monthly_share: float = 0.005 # nouveaux contrats par mois, en part des résidents
    commuting_gain: float = 0.15           # gain net requis, coût du trajet et inertie compris
    cross_border_takeovers: bool = True
    investment_screening: bool = True
    yardstick_voting: bool = True
    yardstick_weight: float = 0.5           # part de l'écart de croissance ajoutée au revenu perçu par l'électeur
    tax_competition: bool = True
    tax_competition_weight: float = 0.25    # rapprochement maximal vers le taux des voisins
    clearing_holding_share: float = 0.02    # avoirs maximaux d'une chambre, en part du PIB annuel de sa région

    def __post_init__(self) -> None:
        self.regions = max(1, min(4, int(self.regions)))
        self.integration_month = max(0, int(self.integration_month))
        self.transport_cost = max(0.0, self.transport_cost)
        self.migration_monthly_share = max(0.0, min(0.05, self.migration_monthly_share))
        self.commuting_monthly_share = max(0.0, min(0.05, self.commuting_monthly_share))
        self.commuting_gain = max(0.0, self.commuting_gain)
        self.tax_competition_weight = max(0.0, min(1.0, self.tax_competition_weight))
        self.clearing_holding_share = max(0.0, self.clearing_holding_share)


def region_config(base: SimulationConfig, profile: dict | None, index: int) -> SimulationConfig:
    """Réglages d'une région : ceux de ``base``, une graine propre (celle de ``base`` pour la
    première région) et les écarts du profil (``population`` et ``scale`` multiplient, ``set``
    remplace)."""
    changes: dict = {"seed": base.seed + REGION_SEED_STEP * index}
    if profile:
        factor = profile.get("population", 1.0)
        if factor != 1.0:
            changes["population_size"] = max(MINIMUM_POPULATION, round(base.population_size * factor))
        for name, scale in profile.get("scale", {}).items():
            value = getattr(base, name)
            changes[name] = type(value)(round(value * scale)) if isinstance(value, int) else value * scale
        changes.update(profile.get("set", {}))
    return dataclasses.replace(base, **changes)


def region_configs(config: WorldConfig) -> list[SimulationConfig]:
    """Réglages de chaque région d'un monde, dans l'ordre où ``World`` les crée (lecture seule :
    sert à préparer leurs chauffes en parallèle, ``economy.warmup_cache.warm_many``)."""
    profiles = REGION_PROFILES if config.profiles is None else tuple(config.profiles)
    return [
        region_config(config.base, profiles[index] if index < len(profiles) else None, index)
        for index in range(config.regions)
    ]


@dataclass
class Intent:
    """Paiement d'une région à une autre : ``amount`` dans la monnaie de ``src``.

    ``debit(montant)`` fait verser le payeur dans la chambre de sa région et rend ce qui a été
    réellement versé ; ``credit(montant)`` paie le bénéficiaire depuis la chambre de la sienne,
    dans la monnaie de ``dst``."""

    src: int
    dst: int
    amount: float
    debit: object
    credit: object
    kind: str = ""
    collected: float = 0.0
    prepaid: bool = False


class World:
    """Plusieurs régions et les échanges entre elles."""

    def __init__(self, config: WorldConfig | None = None, factory=None) -> None:
        from economy.simulation import EconomySimulation

        self.config = config or WorldConfig()
        factory = factory or EconomySimulation
        base = self.config.base
        profiles = REGION_PROFILES if self.config.profiles is None else tuple(self.config.profiles)
        count = self.config.regions
        self.profiles = [profiles[i] if i < len(profiles) else None for i in range(count)]
        self.names = [
            profile["nom"] if profile else f"Région {index + 1}"
            for index, profile in enumerate(self.profiles)
        ]
        self.regions = [
            factory(region_config(base, profile, index)) for index, profile in enumerate(self.profiles)
        ]
        self.random = random.Random(base.seed + WORLD_SEED_OFFSET)
        self.month = 0
        self.rates = [1.0] * count                  # valeur de chaque monnaie en unités communes
        self.pools: list = []                       # chambre de compensation de chaque région
        self.nonresidents: list = []                # compte des non-résidents de chaque région
        self.holdings = [dict() for _ in range(count)]   # région -> entreprise -> {(région, porteur): titres}
        self.seen_prices = [dict() for _ in range(count)]
        self.controlled: dict = {}                  # (région, entreprise) -> (région, acquéreur, mois)
        self.bids: dict = {}                        # (région, acquéreur) -> ramassage en cours
        self.claims: list = []                      # trésorerie de migrants restant à convertir
        self.commuters: dict = {}                   # (résidence, ménage) -> contrat de travail
        self.remittances: list = []                 # comptes salariaux, y compris contrats terminés
        self.commuting_progress = [0.0] * count
        self.next_commuter = 0
        self.shipments: dict = {}                   # (région vendeuse, stock) -> {région acheteuse: unités}
        self.departure_progress = [0.0] * count     # départs permis, fractions accumulées
        self.last_bids: dict = {}                   # (région, acquéreur) -> mois de sa dernière opération
        self.history: list = []
        self.log: list = []
        self._stats: list = []
        if count > 1:
            for sim in self.regions:
                pool = Company("chambre", "Chambre de compensation", "foreign", 0.0, 0.0)
                holder = Company("non_residents", "Non-résidents", "foreign", 0.0, 0.0)
                sim.world_accounts = [pool, holder]
                sim.nonresident = holder
                sim.world_revenue = {}
                sim.world_exports = 0.0
                sim.world_orders = {}
                sim.world_dock = {}
                self.pools.append(pool)
                self.nonresidents.append(holder)

    # ------------------------------------------------------------------ un mois
    def step(self) -> list:
        """Avance toutes les régions d'un mois, puis exécute la phase entre régions."""
        if len(self.regions) > 1 and self.config.commuting and self.month >= self.config.integration_month:
            # Gouvernements et nominations de toutes les régions d'abord : un agent nommé au
            # cabinet quitte son contrat frontalier avant que son mandataire puisse travailler.
            from economy.records import MonthFlows
            flows = [MonthFlows() for _ in self.regions]
            pool = _region_pool(len(self.regions))
            if pool is not None:
                # Régions en parallèle : mêmes appels, chacune dans son fil. Elles ne se
                # lisent ni ne s'écrivent entre elles pendant ces phases.
                list(pool.map(_begin_region, self.regions, flows))
                self._prepare_commuters()
                records = list(pool.map(_finish_region, self.regions, flows))
            else:
                for sim, f in zip(self.regions, flows):
                    sim._begin_month(f)
                self._prepare_commuters()
                for phase in _REGION_PHASES:
                    for sim, f in zip(self.regions, flows):
                        getattr(sim, phase)(f)
                records = [sim._record_month(f) for sim, f in zip(self.regions, flows)]
        else:
            pool = _region_pool(len(self.regions))
            if pool is not None:
                records = list(pool.map(_step_region, self.regions))
            else:
                records = [sim.step() for sim in self.regions]
        self.month += 1
        self._stats = [
            {"importations": 0.0, "exportations": 0.0, "arrivees": 0, "departs": 0,
             "dividendes_recus": 0.0, "dividendes_verses": 0.0, "rachats": 0.0,
             "salaires_recus": 0.0, "salaires_verses": 0.0}
            for _ in self.regions
        ]
        if len(self.regions) > 1 and self.month > self.config.integration_month:
            self._interregional()
        self._record(records)
        return records

    def _interregional(self) -> None:
        config = self.config
        intents: list[Intent] = []
        if config.migration:
            self._migration()
        intents.extend(self._holding_intents())
        intents.extend(self._claim_intents())
        if config.commuting:
            intents.extend(self._wage_intents())
        if config.interregional_trade:
            intents.extend(self._trade_intents())
        if config.cross_border_takeovers:
            self._open_bid()
            intents.extend(self._bid_intents())
        self._settle(intents)
        self.claims = [claim for claim in self.claims if claim["montant"] > 0.01]
        if config.interregional_trade:
            self._return_docks()
            self._plan_exports()
        if config.cross_border_takeovers:
            self._close_bids()
        if config.yardstick_voting:
            self._yardstick()
        if config.tax_competition:
            self._fiscal_competition()
        if config.commuting:
            self._recruit_commuters()

    def _event(self, kind: str, text: str) -> None:
        self.log.append({"mois": self.month, "type": kind, "texte": text})

    # ------------------------------------------------------------------ compensation
    def _convert(self, amount: float, src: int, dst: int) -> float:
        return amount * self.rates[src] / self.rates[dst]

    def _funded_amounts(self, intents: list[Intent], values: list[float], ceiling: list[float]) -> list[float]:
        """Compense les paiements par une circulation à capacité finie, en unité commune.

        Un paiement ordinaire alimente la chambre de départ et prélève celle d'arrivée :
        l'arc de liquidité va donc de l'arrivée au départ. Un paiement prépayé prélève
        seulement la chambre d'arrivée. Un nœud de réserve représente les avoirs initiaux
        et la place libre dans chaque chambre. Les cycles de coût négatif augmentent le
        volume payé ; les arcs inverses permettent de réaffecter une première solution.

        Quatre régions donnent cinq nœuds au plus. Les intentions d'un même couple sont
        agrégées, puis servies au prorata : la taille du graphe ne dépend pas du nombre de
        salariés ou d'actionnaires. Toute solution reste financée, y compris avec des
        chaînes asymétriques mêlant créances prépayées et nouveaux versements.
        """
        count = len(self.regions)
        reserve_node = count
        groups = {}
        for i, (intent, value) in enumerate(zip(intents, values)):
            key = (intent.dst, reserve_node if intent.prepaid else intent.src)
            groups.setdefault(key, []).append(i)
        edges = []

        def edge(src, dst, capacity, cost, initial=0.0):
            index = len(edges)
            edges.append([src, dst, max(0.0, capacity - initial), cost, index + 1])
            edges.append([dst, src, initial, -cost, index])
            return index

        pointers = {}
        for (src, dst), indices in groups.items():
            capacity = sum(values[i] * self.rates[intents[i].src] for i in indices)
            pointers[(src, dst)] = edge(src, dst, capacity, -1.0), capacity
        for r in range(count):
            cash = max(0.0, self.pools[r].cash) * self.rates[r]
            limit = max(cash, ceiling[r] * self.rates[r])
            edge(r, reserve_node, limit, 0.0, cash)
        # Bellman-Ford depuis une source virtuelle reliée à tous les nœuds : un changement
        # au dernier passage identifie un cycle de coût négatif dans le graphe résiduel.
        nodes = count + 1
        while True:
            distances, previous = [0.0] * nodes, [-1] * nodes
            changed = -1
            for _ in range(nodes):
                changed = -1
                for i, (src, dst, capacity, cost, _) in enumerate(edges):
                    if capacity > 1e-9 and distances[dst] > distances[src] + cost:
                        distances[dst] = distances[src] + cost
                        previous[dst] = i
                        changed = dst
                if changed < 0:
                    break
            if changed < 0:
                break
            node = changed
            for _ in range(nodes):
                node = edges[previous[node]][0]
            start, cycle = node, []
            while True:
                index = previous[node]
                cycle.append(index)
                node = edges[index][0]
                if node == start:
                    break
            amount = min(edges[index][2] for index in cycle)
            for index in cycle:
                forward = edges[index]
                forward[2] = max(0.0, forward[2] - amount)
                edges[forward[4]][2] += amount
        funded = [0.0] * len(values)
        for key, indices in groups.items():
            index, capacity = pointers[key]
            fraction = min(1.0, max(0.0, 1.0 - edges[index][2] / capacity)) if capacity > 0.0 else 0.0
            for i in indices:
                funded[i] = values[i] * fraction
        return funded

    def _settle(self, intents: list[Intent]) -> None:
        """Exécute les paiements du mois entre régions.

        Réserve les fonds réels de chaque payeur, résout la circulation sous les contraintes
        de liquidité et de plafond de chaque chambre, puis exécute les débits et les crédits.
        Le taux de change varie de ``exchange_rate_speed`` × (entrées − sorties) / leur somme.
        Les créances prépayées ne sont jamais comptées comme un nouveau versement à la chambre.
        """
        count = len(self.regions)
        intents = [i for i in intents if i.amount > 1e-9 and i.src != i.dst]
        if not intents:
            return
        # Réserver les fonds réels avant de compenser : plusieurs achats peuvent viser le
        # même ménage. Une promesse non finançable ne doit jamais financer un autre paiement.
        reservations = {}
        values = []
        for intent in intents:
            reserve = getattr(intent.debit, "reserve", None)
            values.append(max(0.0, min(intent.amount, reserve(intent.amount, reservations)
                                      if reserve else intent.amount)))
        ceiling = [
            self.config.clearing_holding_share * max(0.0, sim._annual_gdp()) for sim in self.regions
        ]
        values = self._funded_amounts(intents, values, ceiling)
        paid_out = [0.0] * count
        received = [0.0] * count
        for intent, value in zip(intents, values):
            intent.collected = max(0.0, intent.debit(value)) if value > 1e-9 else 0.0
            paid_out[intent.src] += intent.collected
        # Les réservations rendent ces montants exécutables sans décote après le débit.
        due = [self._convert(intent.collected, intent.src, intent.dst) for intent in intents]
        totals = [0.0] * count
        for intent, value in zip(intents, due):
            totals[intent.dst] += value
        budget = [max(0.0, pool.cash) for pool in self.pools]
        for intent, value in zip(intents, due):
            if value <= 0.0:
                continue
            if totals[intent.dst] > budget[intent.dst] + 1e-6:
                raise RuntimeError("Compensation non financée : aucun paiement ne doit être perdu")
            paid = value
            intent.credit(paid)
            received[intent.dst] += paid
        speed = max(0.0, self.config.base.exchange_rate_speed)
        for r in range(count):
            flows = paid_out[r] + received[r]
            if flows > 0.0:
                self.rates[r] = max(RATE_BOUNDS[0], min(RATE_BOUNDS[1], self.rates[r] * (
                    1.0 + speed * (received[r] - paid_out[r]) / flows
                )))
        # seuls les rapports entre monnaies comptent : la moyenne géométrique reste à 1
        logs = [math.log(rate) for rate in self.rates]
        low, high = map(math.log, RATE_BOUNDS)
        left, right = min(logs) - high, max(logs) - low
        for _ in range(64):
            shift = (left + right) / 2.0
            if sum(max(low, min(high, value - shift)) for value in logs) > 0.0:
                left = shift
            else:
                right = shift
        shift = (left + right) / 2.0
        self.rates = [math.exp(max(low, min(high, value - shift))) for value in logs]

    # ------------------------------------------------------------------ titres détenus hors région
    def _resolve_owner(self, region: int, owner_id: str):
        """Porteur d'un paiement dans sa région : l'entreprise (ou celle qui l'a absorbée), le
        ménage, sinon l'État de la région (titres sans porteur vivant)."""
        sim = self.regions[region]
        by_id = {c.id: c for c in sim.companies}
        company = by_id.get(owner_id)
        seen = set()
        while company is not None and not company.active and company.acquired_by in by_id and company.id not in seen:
            seen.add(company.id)
            company = by_id[company.acquired_by]
        if company is not None and company.active:
            return company
        person = next((p for p in sim.people if p.id == owner_id), None)
        return person if person is not None else sim.public

    def _add_holding(self, region: int, company_id: str, owner: tuple, shares: float, price: float) -> None:
        holder = self.nonresidents[region]
        holder.shares[company_id] = holder.shares.get(company_id, 0.0) + shares
        book = self.holdings[region].setdefault(company_id, {})
        book[owner] = book.get(owner, 0.0) + shares
        self.seen_prices[region][company_id] = price

    def _holding_intents(self) -> list[Intent]:
        """Rapproche le registre des porteurs hors région avec les comptes de non-résidents, puis
        reverse leur trésorerie : d'abord le produit des titres disparus du compte (rachat,
        liquidation) à leurs porteurs, au dernier cours connu ; le reste (dividendes) au prorata
        de la valeur des titres détenus."""
        intents = []
        for region, sim in enumerate(self.regions):
            holder = self.nonresidents[region]
            books = self.holdings[region]
            prices = {c.id: c.stock_price for c in sim.companies if c.active}
            claims: dict[tuple, float] = {}
            for company_id in list(books):
                owners = books[company_id]
                recorded = sum(owners.values())
                actual = holder.shares.get(company_id, 0.0) if company_id in prices else 0.0
                if recorded <= 1e-12:
                    del books[company_id]
                    continue
                if actual < recorded - 1e-9:
                    kept = max(0.0, actual) / recorded
                    price = self.seen_prices[region].get(company_id, 0.0)
                    for owner, quantity in owners.items():
                        claims[owner] = claims.get(owner, 0.0) + quantity * (1.0 - kept) * price
                        owners[owner] = quantity * kept
                    if actual <= 1e-9:
                        del books[company_id]
                        holder.shares.pop(company_id, None)
                        self.controlled.pop((region, company_id), None)
                elif actual > recorded + 1e-9:
                    for owner in owners:
                        owners[owner] *= actual / recorded
            cash = max(0.0, holder.cash)
            if cash <= 1e-6:
                continue
            payouts: dict[tuple, float] = {}
            claimed = sum(claims.values())
            first = min(cash, claimed)
            if claimed > 0.0:
                for owner, claim in claims.items():
                    payouts[owner] = first * claim / claimed
            weights: dict[tuple, float] = {}
            for company_id, owners in books.items():
                price = prices.get(company_id, 0.0)
                self.seen_prices[region][company_id] = price
                for owner, quantity in owners.items():
                    weights[owner] = weights.get(owner, 0.0) + quantity * price
            rest, total = cash - first, sum(weights.values())
            if rest > 0.0 and total > 0.0:
                for owner, weight in weights.items():
                    payouts[owner] = payouts.get(owner, 0.0) + rest * weight / total
            elif rest > 0.0 and not payouts:
                transfer_cash(holder, sim.public, rest)  # titres sans porteur : à l'État de la région
                self._public_receipt(region, rest, "dividendes")
            by_region: dict[int, dict] = {}
            for (owner_region, owner_id), amount in payouts.items():
                by_region.setdefault(owner_region, {})[owner_id] = amount
            for owner_region, group in by_region.items():
                total = sum(group.values())
                if total <= 1e-9:
                    continue
                if owner_region == region:
                    for owner_id, amount in group.items():
                        owner = self._resolve_owner(region, owner_id)
                        transfer_cash(holder, owner, amount)
                        if owner is sim.public:
                            self._public_receipt(region, amount, "dividendes")
                    continue
                intents.append(Intent(
                    region, owner_region, total,
                    debit=self._pay_into_pool(region, holder, "dividendes_verses"),
                    credit=self._pay_owners(owner_region, group, total),
                    kind="dividendes",
                ))
        return intents

    def _pay_into_pool(self, region: int, payer, stat: str | None = None):
        def debit(amount: float) -> float:
            amount = min(amount, max(0.0, payer.cash))
            transfer_cash(payer, self.pools[region], amount)
            if stat:
                self._stats[region][stat] = self._stats[region].get(stat, 0.0) + amount
            return amount
        debit.reserve = lambda amount, reserved: self._reserve_cash(payer, amount, reserved)
        return debit

    @staticmethod
    def _reserve_cash(payer, amount: float, reserved: dict) -> float:
        """Réserve une partie de la trésorerie d'un payeur, partagée entre toutes ses demandes."""
        key = id(payer)
        cash = reserved.get(key, max(0.0, payer.cash))
        amount = min(max(0.0, amount), cash)
        reserved[key] = cash - amount
        return amount

    def _pay_owners(self, region: int, group: dict, total: float):
        def credit(amount: float) -> None:
            pool = self.pools[region]
            for owner_id, part in group.items():
                value = min(amount * part / total, max(0.0, pool.cash))
                owner = self._resolve_owner(region, owner_id)
                transfer_cash(pool, owner, value)
                if owner is self.regions[region].public:
                    self._public_receipt(region, value, "dividendes")
            self._stats[region]["dividendes_recus"] += amount
        return credit

    def _public_receipt(self, region: int, amount: float, category: str) -> None:
        """Inscrit une recette publique tardive, après le paiement et la clôture régionale.

        Les lignes du livre sont explicites : le résidu est recalculé, jamais comblé par un
        montant inexpliqué. La TVA importée entre aussi dans les impôts et le PIB du mois.
        """
        if amount <= 0.0:
            return
        sim = self.regions[region]
        sim.public.revenue += amount
        book = sim.institutions.book
        book.add(category, amount)
        if sim.institutions.history:
            state = sim.institutions.history[-1]
            state.flows = dict(book.flows)
            state.cash = sim.public.cash
            state.cash_residual = sim.public.cash - book.cash_start - (
                sum(book.flows.values()) + book.borrowed - book.repaid)
        if sim.history:
            record = sim.history[-1]
            record.government_balance = sim.public.cash
            if category == "tva":
                sim.total_tax_revenue += amount
                record.tax_revenue += amount
                record.vat_revenue += amount
                record.nominal_gdp += amount
                record.total_gdp += amount
                record.recurrent_gdp += amount
                record.real_gdp += amount * 100.0 / max(sim.price_index, 1.0)

    def _claim_intents(self) -> list[Intent]:
        """Trésorerie de migrants déjà versée à la chambre de leur région de départ et pas
        encore convertie : elle est reversée dès que la compensation le permet."""
        intents = []
        for claim in self.claims:
            def debit(amount: float, claim=claim) -> float:
                claim["montant"] -= amount
                return amount

            def credit(amount: float, claim=claim) -> None:
                pool = self.pools[claim["vers"]]
                owner = self._resolve_owner(claim["vers"], claim["menage"])
                paid = min(amount, max(0.0, pool.cash))
                transfer_cash(pool, owner, paid)
                if owner is self.regions[claim["vers"]].public:
                    self._public_receipt(claim["vers"], paid, "transferts_interregionaux")

            intents.append(Intent(claim["de"], claim["vers"], claim["montant"], debit, credit,
                                  "migrants", prepaid=True))
        return intents

    # ------------------------------------------------------------------ lot N : commerce
    def _plan_exports(self) -> None:
        """Commandes entre régions pour le mois suivant.

        Les producteurs vendent tout ce qu'ils produisent : il n'y a pas de stock à exporter en
        fin de mois. Une exportation est donc une part de la production du mois suivant, mise de
        côté avant les ventes intérieures. Elle n'a lieu que si elle rapporte : le prix du marché
        de la région acheteuse doit dépasser le prix du vendeur converti et majoré de
        ``transport_cost``. Avantage a = prix acheteur / prix rendu − 1.

        - La région acheteuse commande ``trade_openness`` × sa demande non servie × min(1,
          ``trade_price_elasticity`` × a), répartie entre les régions vendeuses selon a.
        - Une région vendeuse ne détourne pas plus de ``trade_openness`` × ses ventes du mois ×
          min(1, élasticité × a) vers l'ensemble de ses clientes.
        - La commande est répartie entre ses producteurs au prorata de leurs ventes du mois."""
        base = self.config.base
        openness = max(0.0, min(1.0, base.trade_openness))
        elasticity = max(0.0, base.trade_price_elasticity)
        self.shipments = {}
        for sim in self.regions:
            sim.world_orders = {}
        for sector, stock_field, unmet_field in TRADED:
            prices = [max(sim.food_price if sector == "food" else sim.goods_price, 0.01) for sim in self.regions]
            wanted: dict[tuple, float] = {}
            for d, buyer in enumerate(self.regions):
                unmet = getattr(buyer.history[-1], unmet_field) if buyer.history else 0.0
                if unmet <= 1e-6:
                    continue
                edges = {}
                for s in range(len(self.regions)):
                    if s == d:
                        continue
                    landed = self._convert(prices[s], s, d) * (1.0 + self.config.transport_cost)
                    advantage = prices[d] / landed - 1.0
                    if advantage > 0.0:
                        edges[s] = advantage
                total = sum(edges.values())
                for s, advantage in edges.items():
                    wanted[(s, d)] = openness * unmet * min(1.0, elasticity * advantage) * advantage / total
            for s, seller in enumerate(self.regions):
                orders = {d: units for (origin, d), units in wanted.items() if origin == s and units > 1e-6}
                if not orders:
                    continue
                firms = [c for c in seller.companies if c.active and c.sector == sector and c.units_sold > 0.0]
                sold = sum(c.units_sold for c in firms)
                best = max(
                    min(1.0, elasticity * (prices[d] / (self._convert(prices[s], s, d)
                                                         * (1.0 + self.config.transport_cost)) - 1.0))
                    for d in orders
                )
                capacity = openness * sold * best
                asked = sum(orders.values())
                if capacity <= 0.0 or asked <= 0.0:
                    continue
                scale = min(1.0, capacity / asked)
                self.shipments[(s, stock_field)] = {d: units * scale for d, units in orders.items()}
                seller.world_orders[stock_field] = {
                    c.id: asked * scale * c.units_sold / sold for c in firms
                }

    def _trade_intents(self) -> list[Intent]:
        """Règlement des exportations mises à quai pendant le mois : les unités d'une région
        vendeuse sont partagées entre ses clientes selon les commandes, au prix moyen des
        producteurs qui les ont livrées, converti et majoré du transport."""
        intents = []
        for (s, stock_field), plan in getattr(self, "shipments", {}).items():
            seller = self.regions[s]
            dock = seller.world_dock.get(stock_field, {})
            docked = sum(dock.values())
            planned = sum(plan.values())
            if docked <= 1e-9 or planned <= 0.0:
                continue
            by_id = {c.id: c for c in seller.companies}
            price = sum(by_id[cid].price * units for cid, units in dock.items()) / docked
            sector = "food" if stock_field == "inventory" else "goods"
            for d, units in plan.items():
                share = units / planned
                landed = self._convert(price, s, d) * (1.0 + self.config.transport_cost)
                order = {"unites": 0.0}
                intents.append(Intent(
                    d, s, docked * share * landed,
                    debit=self._buy_imports(d, sector, landed, order),
                    credit=self._sell_exports(s, stock_field, dict(dock), docked, order),
                    kind=f"commerce_{sector}",
                ))
        return intents

    def _return_docks(self) -> None:
        """Les unités restées à quai (commande non payée) reviennent au stock de leur producteur."""
        for sim in self.regions:
            by_id = {c.id: c for c in sim.companies}
            for stock_field, dock in sim.world_dock.items():
                for company_id, units in dock.items():
                    company = by_id.get(company_id)
                    if company is not None and units > 1e-9:
                        setattr(company, stock_field, getattr(company, stock_field) + units)
            sim.world_dock = {}

    def _buy_imports(self, region: int, sector: str, landed: float, order: dict):
        """Les ménages de la région paient l'importation au prorata de leur budget pour ce
        produit, TVA comprise (la TVA va à leur État, le reste à la chambre)."""
        def reserve(amount: float, reserved: dict) -> float:
            sim = self.regions[region]
            live = sim.live
            if sector == "food":
                shares = (live.food_income_budget_share, live.food_cash_draw_rate)
            else:
                shares = (live.goods_income_budget_share, live.goods_cash_draw_rate)
            tax_factor = 1.0 + live.vat_rate
            budgets = []
            for person in sim.people:
                budget = household_budget(person, *shares, live) * person.consumption_propensity
                if sector == "food":
                    budget *= person.engel_food
                cash = reserved.get(id(person), max(0.0, person.cash))
                budgets.append(min(cash, max(0.0, budget)))
            total = sum(budgets)
            paid = min(amount, total / tax_factor) if total > 0.0 else 0.0
            allocations = []
            for person, budget in zip(sim.people, budgets):
                value = paid * tax_factor * budget / total if total > 0.0 else 0.0
                allocations.append((person, self._reserve_cash(person, value, reserved)))
            order["budgets"] = allocations
            return sum(budget for _, budget in allocations) / tax_factor

        def debit(amount: float) -> float:
            sim = self.regions[region]
            live = sim.live
            if "budgets" not in order:
                reserve(amount, {})
            allocations = order["budgets"]
            total = sum(budget for _, budget in allocations)
            tax_factor = 1.0 + live.vat_rate
            paid = min(amount, total / tax_factor)
            if paid <= 0.0:
                return 0.0
            pool = self.pools[region]
            vat = 0.0
            for person, budget in allocations:
                net = paid * budget / total
                if net <= 0.0:
                    continue
                tax = net * live.vat_rate
                transfer_cash(person, pool, net)
                person.cash -= tax
                person.expenses_this_turn += net + tax
                person.cumulative_expenses += net + tax
                bought = net / landed
                if sector == "food":
                    person.last_food_bought += bought
                else:
                    person.last_goods_bought += bought
                vat += tax
            sim.public.cash += vat
            self._public_receipt(region, vat, "tva")
            order["unites"] = paid / landed
            self._stats[region]["importations"] += paid
            return paid
        debit.reserve = reserve
        return debit

    def _sell_exports(self, region: int, stock_field: str, shares: dict, docked: float, order: dict):
        """Les producteurs sont payés par la chambre de leur région, au prorata des unités qu'ils
        ont mises à quai ; les unités livrées quittent le quai. La recette entre dans leurs
        comptes du mois suivant."""
        def credit(amount: float) -> None:
            sim = self.regions[region]
            pool = self.pools[region]
            dock = sim.world_dock.get(stock_field, {})
            by_id = {c.id: c for c in sim.companies}
            for company_id, units in shares.items():
                share = units / docked
                company = by_id[company_id]
                value = min(amount * share, max(0.0, pool.cash))
                transfer_cash(pool, company, value)
                dock[company_id] = max(0.0, dock.get(company_id, 0.0) - order["unites"] * share)
                sim.world_revenue[company_id] = sim.world_revenue.get(company_id, 0.0) + value
            sim.world_exports += amount
            self._stats[region]["exportations"] += amount
        return credit

    # ------------------------------------------------------------------ lot O : mobilité des ménages
    def _attractiveness(self) -> list[float]:
        """Revenu réel attendu d'un salarié moyen dans chaque région : salaire moyen × taux
        d'emploi, loyer déduit, rapporté au niveau des prix (les indices partent de 100 partout)."""
        values = []
        for sim in self.regions:
            last = sim.history[-1]
            price = max(sim.price_index, 1.0) / 100.0
            values.append((self._net_wage(sim, last.average_wage) * (1.0 - last.unemployment) - sim.rent) / price)
        return values

    def _protected(self, sim) -> set:
        """Ménages qu'une fonction retient : cabinet, direction ou fondation d'une entreprise,
        candidature, mandat de conseiller."""
        protected = {office.person_id for office in sim.institutions.offices.values()}
        protected.update(getattr(sim, "world_commuters", {}))
        for company in sim.companies:
            if company.active:
                protected.add(company.executive_id)
                protected.add(company.founder_id)
        power = getattr(sim, "power", None)
        if power is not None:
            if power.candidates:
                protected.update((power.candidates["sortant"], power.candidates["opposant"]))
            protected.update(advisor["personne"] for advisor in power.advisors.values())
            protected.add(power.president_elect)
        return protected

    @staticmethod
    def _mobile(person, protected: set) -> bool:
        """Un ménage peut partir s'il est assez jeune et n'a ni fonction, ni emploi public, ni
        logement mis en location, ni placement qu'il ne peut pas solder en partant (parts de
        fonds, obligations d'entreprise, parts salariées)."""
        return (
            person.id not in protected
            and person.employer_id != "state"
            and person.age_months < MIGRANT_MAX_AGE_MONTHS
            and person.investment_homes == 0
            and not person.fund_units and not person.corporate_bonds and not person.coop_shares
            and not person.founded_company and person.retraining_months == 0
        )

    @staticmethod
    def _home_buyer(sim):
        """Promoteur de la région le mieux doté, qui rachète le logement d'un partant."""
        buyers = [c for c in sim.companies if c.active and c.sector == "real_estate"]
        return max(buyers, key=lambda c: (c.cash, c.id)) if buyers else None

    def _departure_proceeds(self, sim, person) -> float | None:
        """Trésorerie qu'un ménage emporterait après avoir tout soldé (``None`` : il ne peut pas
        partir) : ses obligations d'État revendues aux banques, son logement vendu au promoteur
        le mieux doté avec la décote d'une vente rapide (``foreclosure_discount``), ses crédits
        remboursés."""
        cash = max(0.0, person.cash)
        if person.bond_holdings > 0.0:
            if not any(c.active and c.sector == "bank" for c in sim.companies):
                return None
            cash += person.bond_holdings
        if person.owns_home:
            buyer = self._home_buyer(sim)
            price = sim.live.housing_price * (1.0 - sim.live.foreclosure_discount)
            if buyer is None or buyer.cash < price:
                return None
            cash += price
        cash -= max(0.0, person.mortgage_debt) + max(0.0, person.consumer_debt)
        return cash if cash >= 0.0 else None

    def _settle_departure(self, sim, person) -> None:
        """Exécute le solde de départ vérifié par ``_departure_proceeds`` : revente des
        obligations d'État aux banques (elles créent le dépôt, comme en Bourse), vente du
        logement, remboursement des crédits (le dépôt est détruit, les comptes de prêt soldés)."""
        if person.bond_holdings > 0.0:
            banks = [c for c in sim.companies if c.active and c.sector == "bank"]
            for bank in banks:
                bank.bond_holdings += person.bond_holdings / len(banks)
            person.cash += person.bond_holdings
            person.bond_holdings = 0.0
        if person.owns_home:
            buyer = self._home_buyer(sim)
            price = sim.live.housing_price * (1.0 - sim.live.foreclosure_discount)
            transfer_cash(buyer, person, price)
            buyer.property_inventory += 1
            person.owns_home = False
        for kind, debt in (("mortgage", person.mortgage_debt), ("consumer", person.consumer_debt)):
            if debt > 0.0:
                person.cash -= debt
                sim._write_off_household_loan(f"{kind}:{person.id}", 0.0)
        person.mortgage_debt = person.mortgage_payment = 0.0
        person.mortgage_months_remaining = person.mortgage_arrears = 0
        person.consumer_debt = 0.0
        person.consumer_arrears = 0

    def _migration(self) -> None:
        """Départs du mois. Dans chaque région, les ménages mobiles comparent leur revenu réel,
        loyer déduit, à celui qu'ils attendent dans la région la plus attrayante : son revenu
        attendu × leur salaire relatif (entre 0,6 et 1,5 ; 0,6 sans emploi). Partent ceux dont le
        gain dépasse ``migration_gain``, les plus gros gains d'abord, dans la limite de
        ``migration_monthly_share`` de la population."""
        config = self.config
        appeal = self._attractiveness()
        for o, origin in enumerate(self.regions):
            if len(origin.people) <= MINIMUM_POPULATION:
                continue
            d = max((r for r in range(len(self.regions)) if r != o), key=lambda r: (appeal[r], -r))
            if appeal[d] <= 0.0:
                continue
            # les fractions de départ s'accumulent d'un mois à l'autre (petites populations)
            self.departure_progress[o] = min(
                self.departure_progress[o] + config.migration_monthly_share * len(origin.people),
                1.0 + config.migration_monthly_share * len(origin.people),
            )
            quota = min(int(self.departure_progress[o]), len(origin.people) - MINIMUM_POPULATION)
            if quota <= 0:
                continue
            last = origin.history[-1]
            price = max(origin.price_index, 1.0) / 100.0
            average = max(last.average_wage, 1.0)
            protected = self._protected(origin)
            movers = []
            for person in origin.people:
                if not self._mobile(person, protected):
                    continue
                employed = person.employer_id is not None
                income = self._net_wage(origin, person.current_wage) if employed else person.expected_income
                own = (income - origin.rent) / price
                relative = max(0.6, min(1.5, person.current_wage / average)) if employed else 0.6
                expected = appeal[d] * relative
                floor = 0.1 * max(appeal[o], 1.0)
                if expected > max(own, floor) * (1.0 + config.migration_gain):
                    movers.append((expected / max(own, floor), person))
            movers.sort(key=lambda item: (-item[0], item[1].id))
            moved = 0
            for _, person in movers:
                if moved >= quota:
                    break
                # il ne part que s'il peut tout solder (logement vendu, crédits remboursés)
                if self._departure_proceeds(origin, person) is None:
                    continue
                self._settle_departure(origin, person)
                self._move(person, o, d)
                moved += 1
            self.departure_progress[o] -= moved

    def _move(self, person, o: int, d: int) -> None:
        """Déménage un ménage : il quitte son emploi et son logement loué, arrive sans emploi
        dans la nouvelle région avec ses compétences et ses traits, et garde ses actions (portées
        par le compte de non-résidents de sa région de départ). Sa trésorerie est versée à la
        chambre de départ et convertie par la compensation."""
        origin, destination = self.regions[o], self.regions[d]
        old_id = person.id
        for company in origin.companies:
            if old_id in company.employees:
                company.employees.remove(old_id)
        origin.people.remove(person)
        origin._departed = getattr(origin, "_departed", 0) + 1
        taken = {p.id for p in destination.people}
        number = len(destination.people) + getattr(destination, "_departed", 0)
        while f"p{number:04d}" in taken:
            destination._departed = getattr(destination, "_departed", 0) + 1
            number += 1
        new_id = f"p{number:04d}"
        for claim in self.claims:
            if claim["vers"] == o and claim["menage"] == old_id:
                claim["vers"], claim["menage"] = d, new_id
        for payment in self.remittances:
            if payment["home"] == o and payment["person"] == old_id:
                payment["home"], payment["person"] = d, new_id
        prices = {c.id: c.stock_price for c in origin.companies}
        for company_id, quantity in person.shares.items():
            if quantity > 0.0:
                self._add_holding(o, company_id, (d, new_id), quantity, prices.get(company_id, 0.0))
        for books in self.holdings:
            for owners in books.values():
                if (o, old_id) in owners:
                    owners[(d, new_id)] = owners.get((d, new_id), 0.0) + owners.pop((o, old_id))
        cash = max(0.0, person.cash)
        transfer_cash(person, self.pools[o], cash)
        newcomer = dataclasses.replace(
            person, id=new_id, name=f"{person.name} (venu de {self.names[o]})", cash=0.0,
            employer_id=None, shares={}, rental_owner_id=None, rented_homes=0,
            current_wage=0.0, employment_role="Sans emploi", employer_tenure=0, rank=-1,
            income_this_turn=0.0, dividend_income_this_turn=0.0, rent_income_this_turn=0.0,
            capital_income_last_month=0.0, expenses_this_turn=0.0, taxes_paid=0.0,
            job_search_months=0, unemployment_months=0, negotiated_factor=1.0,
            wealth_history=[], financial_history=[],
        )
        lean = origin.institutions.lean.get(old_id)
        if lean is not None:
            destination.institutions.lean[new_id] = lean
        destination.people.append(newcomer)
        bankrel = getattr(origin, "bankrel", None)
        if bankrel is not None:
            bankrel.home.pop(old_id, None)
        self._stats[o]["departs"] += 1
        self._stats[d]["arrivees"] += 1
        if cash > 0.0:
            self.claims.append({"de": o, "vers": d, "menage": new_id, "montant": cash})

    # ------------------------------------------------------------------ lot O : frontaliers
    @staticmethod
    def _net_wage(sim, wage: float) -> float:
        """Salaire après le barème de résidence, sans anticiper les revenus du capital."""
        threshold = max(0.0, sim.live.income_tax_threshold)
        tax = wage * sim.live.income_tax_rate + max(0.0, wage - threshold) * sim.live.top_marginal_income_tax_rate
        return max(0.0, wage - tax)

    def _prepare_commuters(self) -> None:
        for sim in self.regions:
            sim.world_workers = []
            sim.world_commuters = {}
            if not hasattr(sim, "world_income"):
                sim.world_income = {}
        if not self.commuters:
            return
        # Population, entreprises et fonctions sont fixes pendant ce rapprochement.
        people = [{p.id: p for p in sim.people} for sim in self.regions]
        companies = [{c.id: c for c in sim.companies if c.active} for sim in self.regions]
        protected = [self._protected(sim) for sim in self.regions]
        for key, contract in list(self.commuters.items()):
            home, pid = key
            origin = self.regions[home]
            person = people[home].get(pid)
            host = self.regions[contract["host"]]
            company = companies[contract["host"]].get(contract["company"])
            if person is None or person.generation != contract["generation"] or company is None or pid in protected[home]:
                del self.commuters[key]
                if person is not None and person.employment_role == "Frontalier":
                    person.employer_id = None
                continue
            contract["local_wage"] = self._convert(max(contract["proxy"].current_wage, company.wage_offer),
                                                   contract["host"], home)
            origin.world_commuters[pid] = contract
            host.world_workers.append((contract["proxy"], company.id))

    def _recruit_commuters(self) -> None:
        from economy.labor import _employment_role, _pay
        # Les postes locaux sont pourvus en premier. Un contrat frontalier est rompu si le
        # poste a disparu ; son salaire déjà gagné reste rapatriable dans remittances.
        people = [{p.id: p for p in sim.people} for sim in self.regions] if self.commuters else []
        for key, contract in list(self.commuters.items()):
            proxy = contract["proxy"]
            person = people[key[0]].get(key[1])
            if person is not None and person.generation == contract["generation"]:
                person.skill = max(person.skill, proxy.skill)
                person.career_months = max(person.career_months, proxy.career_months)
                for sector, level in proxy.skills.items():
                    person.skills[sector] = max(person.skills.get(sector, 1.0), level)
            if proxy.employer_id is None and contract["since"] < self.month:
                self.commuters.pop(key)
                self.regions[key[0]].world_commuters.pop(key[1], None)
        offers = []
        for host_index, sim in enumerate(self.regions):
            reserved = {}
            for contract in self.commuters.values():
                if contract["host"] == host_index and contract["since"] == self.month:
                    cid = contract["company"]
                    reserved[cid] = reserved.get(cid, 0) + 1
            for company in sim.companies:
                vacancies = company.target_employees - len(company.employees) - reserved.get(company.id, 0)
                if company.active and company.sector not in ("bank", "media") and vacancies > 0:
                    offers.append([host_index, company, vacancies])
        for home, sim in enumerate(self.regions):
            self.commuting_progress[home] = min(1.0 + self.config.commuting_monthly_share * len(sim.people),
                self.commuting_progress[home] + self.config.commuting_monthly_share * len(sim.people))
            quota = int(self.commuting_progress[home])
            protected = self._protected(sim)
            candidates = sorted((p for p in sim.people if p.id not in protected and p.employer_id != "state"
                                 and p.retraining_months == 0 and (home, p.id) not in self.commuters),
                                key=lambda p: (-p.skill, p.id))
            for person in candidates:
                if quota <= 0:
                    break
                best = None
                baseline = self._net_wage(sim, person.current_wage) if person.employer_id else max(
                    person.expected_income, sim.live.unemployment_benefit)
                for offer in offers:
                    host_index, company, available = offer
                    if host_index == home or available <= 0:
                        continue
                    host = self.regions[host_index]
                    wage = _pay(company, person, _employment_role(company, person), host.live)
                    local = self._convert(wage, host_index, home)
                    gain = self._net_wage(sim, local) / max(1.0, baseline)
                    if company.cash < wage + 0.25 * host.wage_reference or gain <= 1.0 + self.config.commuting_gain:
                        continue
                    if best is None or (gain, -host_index, company.id) > best[0]:
                        best = ((gain, -host_index, company.id), offer, local)
                if best is None:
                    continue
                _, offer, local_wage = best
                host_index, company, _ = offer
                self.next_commuter += 1
                proxy = Person(f"frontalier_{self.next_commuter}", person.name, 0.0,
                    skill=person.skill, skills=dict(person.skills), trade=person.trade, job_trade=person.job_trade,
                    technology_level=person.technology_level, trade_wage_factor=person.trade_wage_factor,
                    career_months=person.career_months, age_months=person.age_months)
                contract = dict(home=home, person=person.id, host=host_index, company=company.id,
                                proxy=proxy, generation=person.generation, since=self.month, local_wage=local_wage)
                self.commuters[(home, person.id)] = contract
                self.remittances.append(contract)
                self.regions[host_index].world_accounts.append(proxy)
                sim.world_commuters[person.id] = contract
                for employer in sim.companies:
                    if person.id in employer.employees:
                        employer.employees.remove(person.id)
                person.employer_id = f"region:{host_index}:{company.id}"
                person.employment_role = "Frontalier"
                offer[2] -= 1
                quota -= 1
                self.commuting_progress[home] -= 1.0
                self._event("emploi_frontalier", f"{person.name} ({self.names[home]}) travaillera chez "
                            f"{company.name} ({self.names[host_index]})")

    def _wage_intents(self) -> list[Intent]:
        intents = []
        for contract in self.remittances:
            proxy, home, host = contract["proxy"], contract["home"], contract["host"]
            if proxy.cash <= 1e-9:
                continue
            def credit(amount, contract=contract, home=home):
                sim = self.regions[home]
                person = self._resolve_owner(home, contract["person"])
                transfer_cash(self.pools[home], person, amount)
                sim.world_income[contract["person"]] = sim.world_income.get(contract["person"], 0.0) + amount
                self._stats[home]["salaires_recus"] = self._stats[home].get("salaires_recus", 0.0) + amount
            intents.append(Intent(host, home, proxy.cash, self._pay_into_pool(host, proxy, "salaires_verses"),
                                  credit, "salaires"))
        return intents

    # ------------------------------------------------------------------ lot P : rachats entre régions
    def _free_cash(self, sim, company) -> float:
        return max(0.0, company.cash - working_capital(company, sim.live))

    @staticmethod
    def _capitalization(company) -> float:
        return company.stock_price * max(0.0, company.shares_outstanding - company.treasury_shares)

    @staticmethod
    def _productivity(company) -> float:
        return company.productivity_trait * company.output_multiplier

    def _open_bid(self) -> None:
        """Au plus un nouveau ramassage par mois dans le monde.

        Acquéreur : entreprise non bancaire, cotée, bénéficiaire, d'au moins ``BID_MIN_AGE``
        mois, hors contrôle public, qui n'est ni une filiale ni contrôlée de l'étranger, n'a pas
        d'opération en cours et n'en a pas lancé depuis ``BID_REST_MONTHS`` mois. Cible :
        entreprise du même secteur dans une autre région, cotée, avec des salariés, hors contrôle
        public, d'une capitalisation convertie d'au plus ``BID_SIZE`` de la sienne. L'acquéreur
        doit lui apporter quelque chose (productivité supérieure d'au moins ``BID_MIN_GAP``) et
        y trouver son compte : le bénéfice annuel de la cible, relevé du gain de productivité
        attendu, rapporté au prix payé, doit couvrir le coût de ses fonds propres.
        Avec le filtrage, un gouvernement d'orientation supérieure à ``SCREENING_ORIENTATION``
        refuse le rachat d'une entreprise d'un secteur critique. Le couple retenu est celui où
        l'avance de productivité est la plus grande."""
        best = None
        # Aucun titre ni contrôle ne change pendant la recherche du meilleur
        # couple. Relever une fois les cibles admissibles, dans l'ordre régional.
        public_ids = [sim._public_controlled_ids() for sim in self.regions]
        under_bid = {(bid["region"], bid["cible"]) for bid in self.bids.values()}
        targets = []
        for region, sim in enumerate(self.regions):
            sectors = {}
            for company in sim.companies:
                if (company.active and company.listed and company.employees
                        and company.id not in public_ids[region]
                        and (region, company.id) not in self.controlled
                        and (region, company.id) not in under_bid):
                    sectors.setdefault(company.sector, []).append(company)
            targets.append(sectors)
        for s, home in enumerate(self.regions):
            excluded = public_ids[s]
            for acquirer in home.companies:
                if (
                    not acquirer.active or acquirer.sector in ("bank", "media") or not acquirer.listed
                    or acquirer.id in excluded or acquirer.parent_id is not None
                    or acquirer.age_months < BID_MIN_AGE or acquirer.previous_profit <= 0.0
                    or (s, acquirer.id) in self.bids or (s, acquirer.id) in self.controlled
                    or self.month - self.last_bids.get((s, acquirer.id), -10 ** 6) < BID_REST_MONTHS
                    or self._free_cash(home, acquirer) <= 0.0
                ):
                    continue
                required = home._cost_of_equity()
                size = self._capitalization(acquirer) * self.rates[s]
                for d, host in enumerate(self.regions):
                    if d == s:
                        continue
                    blocked = (
                        self.config.investment_screening
                        and host.government.orientation > SCREENING_ORIENTATION
                        and acquirer.sector in host.live.critical_sectors
                    )
                    for target in targets[d].get(acquirer.sector, ()):
                        if self._capitalization(target) * self.rates[d] > BID_SIZE * size:
                            continue
                        gap = self._productivity(acquirer) / max(self._productivity(target), 1e-9) - 1.0
                        if gap < BID_MIN_GAP:
                            continue
                        # la cible, une fois redressée, doit rapporter le coût des fonds propres
                        cost = self._capitalization(target) * (1.0 + BID_PREMIUM)
                        earnings = 12.0 * max(0.0, target.previous_profit) * (1.0 + PRODUCTIVITY_TRANSFER * gap)
                        if cost <= 0.0 or earnings / cost < required:
                            continue
                        if blocked:
                            self._stats[d]["rachats_refuses"] = self._stats[d].get("rachats_refuses", 0) + 1
                            continue
                        key = (gap, -s, -d, acquirer.id, target.id)
                        if best is None or key > best[0]:
                            best = (key, s, acquirer, d, target)
        if best is None:
            return
        _, s, acquirer, d, target = best
        self.bids[(s, acquirer.id)] = {"region": d, "cible": target.id, "depuis": self.month, "verse": 0.0}
        self.last_bids[(s, acquirer.id)] = self.month
        self._event("offre", f"{acquirer.name} ({self.names[s]}) commence à racheter "
                             f"{target.name} ({self.names[d]})")

    def _foreign_stake(self, region: int, company_id: str, owner: tuple) -> float:
        return self.holdings[region].get(company_id, {}).get(owner, 0.0)

    def _bid_intents(self) -> list[Intent]:
        """Achats du mois de chaque ramassage : l'acquéreur engage au plus ``BID_SPEND`` de sa
        trésorerie libre pour acheter aux ménages de la région de la cible leurs titres, au cours
        majoré de ``BID_PREMIUM``, jusqu'à ``CONTROL`` du capital en circulation."""
        intents = []
        for (s, acquirer_id), bid in list(self.bids.items()):
            home, host = self.regions[s], self.regions[bid["region"]]
            acquirer = next((c for c in home.companies if c.id == acquirer_id and c.active), None)
            target = next((c for c in host.companies if c.id == bid["cible"] and c.active), None)
            if acquirer is None or target is None or self.month - bid["depuis"] > BID_MONTHS:
                del self.bids[(s, acquirer_id)]
                continue
            d = bid["region"]
            if (self.config.investment_screening and host.government.orientation > SCREENING_ORIENTATION
                    and target.sector in host.live.critical_sectors):
                del self.bids[(s, acquirer_id)]
                self._event("offre_refusee", f"{self.names[d]} bloque le rachat de {target.name}")
                continue
            floated = target.shares_outstanding - target.treasury_shares
            needed = CONTROL * floated - self._foreign_stake(d, target.id, (s, acquirer_id))
            sellers = [(p, p.shares.get(target.id, 0.0)) for p in host.people]
            sellers = [(p, q) for p, q in sellers if q > 0.0]
            available = sum(q for _, q in sellers)
            wanted = min(needed, available)
            price = target.stock_price * (1.0 + BID_PREMIUM)
            if wanted <= 1e-9 or price <= 0.0:
                continue
            budget = min(self._convert(wanted * price, d, s), BID_SPEND * self._free_cash(home, acquirer))
            if budget <= 1.0:
                continue

            def credit(amount: float, d=d, target=target, sellers=sellers, available=available,
                       wanted=wanted, price=price, owner=(s, acquirer_id), bid=bid) -> None:
                pool = self.pools[d]
                bought = min(wanted, amount / price)
                paid = min(bought * price, max(0.0, pool.cash))
                for person, quantity in sellers:
                    share = quantity / available
                    person.shares[target.id] = quantity - bought * share
                    if person.shares[target.id] <= 1e-12:
                        del person.shares[target.id]
                    transfer_cash(pool, person, paid * share)
                self._add_holding(d, target.id, owner, bought, target.stock_price)
                self._stats[d]["rachats"] += paid

            def debit(amount: float, acquirer=acquirer, s=s, bid=bid) -> float:
                amount = min(amount, max(0.0, acquirer.cash))
                transfer_cash(acquirer, self.pools[s], amount)
                bid["verse"] += amount
                return amount

            debit.reserve = lambda amount, reserved, acquirer=acquirer: self._reserve_cash(acquirer, amount, reserved)
            intents.append(Intent(s, d, budget, debit, credit, "rachat"))
        return intents

    def _close_bids(self) -> None:
        """Un ramassage qui atteint ``CONTROL`` du capital en circulation donne le contrôle : la
        cible rattrape ``PRODUCTIVITY_TRANSFER`` de l'écart de productivité avec son acquéreur."""
        for (s, acquirer_id), bid in list(self.bids.items()):
            d = bid["region"]
            host = self.regions[d]
            target = next((c for c in host.companies if c.id == bid["cible"] and c.active), None)
            acquirer = next((c for c in self.regions[s].companies if c.id == acquirer_id and c.active), None)
            if target is None or acquirer is None:
                continue
            floated = target.shares_outstanding - target.treasury_shares
            if floated <= 0.0 or self._foreign_stake(d, target.id, (s, acquirer_id)) < CONTROL * floated - 1e-6:
                continue
            gap = acquirer.productivity_trait - target.productivity_trait
            if gap > 0.0:
                target.productivity_trait += PRODUCTIVITY_TRANSFER * gap
            self.controlled[(d, target.id)] = (s, acquirer_id, self.month)
            del self.bids[(s, acquirer_id)]
            self._event("controle", f"{acquirer.name} ({self.names[s]}) prend le contrôle de "
                                    f"{target.name} ({self.names[d]})")

    # ------------------------------------------------------------------ lot Q : vote comparatif
    def _fiscal_competition(self) -> None:
        """Les gouvernements non sociaux comparent le taux des sociétés à celui des voisins.

        La prochaine loi de finances peut rapprocher son objectif de doctrine du taux moyen
        des voisins moins imposés, à proportion du libéralisme du gouvernement. La contrainte
        budgétaire reste appliquée après cette décision : la baisse n'est jamais garantie.
        """
        rates = [sim.live.corporate_tax_rate for sim in self.regions]
        for i, sim in enumerate(self.regions):
            others = [rate for j, rate in enumerate(rates) if j != i]
            average = sum(others) / len(others) if others else rates[i]
            liberal = max(0.0, 1.0 - sim.government.orientation / SCREENING_ORIENTATION)
            weight = self.config.tax_competition_weight * liberal
            gap = max(0.0, 1.0 - average / rates[i]) if rates[i] > 0.0 else 0.0
            sim.world_corporate_tax_factor = 1.0 - weight * gap

    def _yardstick(self) -> None:
        """Chaque région compare la croissance sur un an de son PIB réel par ménage à la moyenne
        des autres ; l'écart (borné à ``YARDSTICK_BOUND``) × ``yardstick_weight`` s'ajoute au
        revenu réel que perçoit chaque électeur de la région (``_voters``)."""
        growth = []
        for sim in self.regions:
            history = sim.history
            if len(history) < 24:
                growth.append(None)
                continue
            now = sum(r.real_gdp / max(1, r.population_count) for r in history[-12:])
            before = sum(r.real_gdp / max(1, r.population_count) for r in history[-24:-12])
            growth.append(now / before - 1.0 if before > 0.0 else None)
        for index, sim in enumerate(self.regions):
            others = [g for i, g in enumerate(growth) if i != index and g is not None]
            if growth[index] is None or not others:
                sim.yardstick_bonus = 0.0
                continue
            gap = growth[index] - sum(others) / len(others)
            sim.yardstick_bonus = self.config.yardstick_weight * max(-YARDSTICK_BOUND, min(YARDSTICK_BOUND, gap))

    # ------------------------------------------------------------------ relevé et lectures
    def _record(self, records: list) -> None:
        rows = []
        for index, (sim, record) in enumerate(zip(self.regions, records)):
            stats = self._stats[index]
            population = max(1, len(sim.people))
            rows.append({
                "region": self.names[index],
                "population": len(sim.people),
                "pib_reel": record.real_gdp,
                "pib_reel_par_menage": record.real_gdp / population,
                "chomage": record.unemployment,
                "salaire_reel": record.average_wage / max(sim.price_index, 1.0) * 100.0,
                "prix": sim.price_index,
                "orientation": sim.government.orientation,
                "taux_impot_societes": sim.live.corporate_tax_rate,
                "taux_de_change": self.rates[index],
                "entreprises": sum(1 for c in sim.companies if c.active),
                "controlees_de_l_etranger": sum(1 for key in self.controlled if key[0] == index),
                "avoirs_chambre": self.pools[index].cash if self.pools else 0.0,
                "frontaliers_sortants": sum(home == index for home, _ in self.commuters),
                "frontaliers_entrants": sum(c["host"] == index for c in self.commuters.values()),
                **stats,
            })
        self.history.append({"mois": self.month, "regions": rows})

    def region_table(self) -> list[dict]:
        """Dernier relevé de chaque région (vide avant le premier mois)."""
        return list(self.history[-1]["regions"]) if self.history else []

    def series(self, key: str) -> dict[str, list]:
        """Série mensuelle d'un indicateur du relevé, par région."""
        return {
            name: [row["regions"][index].get(key, 0.0) for row in self.history]
            for index, name in enumerate(self.names)
        }

    def foreign_groups(self) -> list[dict]:
        """Entreprises contrôlées depuis une autre région, et ramassages en cours."""
        rows = []
        for (d, company_id), (s, acquirer_id, month) in self.controlled.items():
            target = next((c for c in self.regions[d].companies if c.id == company_id), None)
            acquirer = next((c for c in self.regions[s].companies if c.id == acquirer_id), None)
            rows.append({
                "entreprise": target.name if target else company_id, "region": self.names[d],
                "acquereur": acquirer.name if acquirer else acquirer_id, "region_acquereur": self.names[s],
                "etat": "contrôlée", "mois": month,
            })
        for (s, acquirer_id), bid in self.bids.items():
            d = bid["region"]
            target = next((c for c in self.regions[d].companies if c.id == bid["cible"]), None)
            acquirer = next((c for c in self.regions[s].companies if c.id == acquirer_id), None)
            floated = (target.shares_outstanding - target.treasury_shares) if target else 0.0
            stake = self._foreign_stake(d, bid["cible"], (s, acquirer_id))
            rows.append({
                "entreprise": target.name if target else bid["cible"], "region": self.names[d],
                "acquereur": acquirer.name if acquirer else acquirer_id, "region_acquereur": self.names[s],
                "etat": f"ramassage ({stake / floated:.0%})" if floated > 0.0 else "ramassage",
                "mois": bid["depuis"],
            })
        return rows

    def money_residuals(self) -> list[float]:
        """Écart à l'identité monétaire de chaque région (0 : aucune monnaie créée ni détruite)."""
        return [sim.ledger.residual(sim) for sim in self.regions]
