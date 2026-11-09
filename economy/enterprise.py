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
from economy.consolidation import INTEGRATION_MONTHS, _employee_stake, _holder_valuation
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
_SECTOR_LABELS = {key: label.split(" (")[0] for key, label in FIRM_SECTORS}


@dataclass
class InvestmentFund(Company):
    """Fonds d'investissement géré par une banque (G8) : un porteur de titres comme l'État, hors de
    la liste des entreprises. ``units`` : parts émises aux ménages ; ``requests`` : besoins de
    financement reçus, lissés (dimensionne sa trésorerie d'attente)."""

    bank_id: str = ""
    units: float = 0.0
    requests: float = 0.0


@dataclass
class EnterpriseState:
    """État propre au lot G, créé au premier usage d'un mécanisme activé."""

    funds: list = field(default_factory=list)
    random: random.Random | None = None
    homes: dict = field(default_factory=dict)      # propriétaire -> logements le mois précédent (G2)
    bids: dict = field(default_factory=dict)       # acquéreur -> mois de sa dernière offre entre secteurs (G5)
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
            return {c.id: c.stores for c in active}
        roots = roots if roots is not None else self._group_roots()
        totals: dict[str, float] = {}
        for company in active:
            totals[roots[company.id]] = totals.get(roots[company.id], 0.0) + company.stores
        return {c.id: totals[roots[c.id]] for c in active}

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
            monthly = extra_units * margin - running
            if 12.0 * monthly / opening <= required:
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
        decay = max(0.0, min(1.0, live.data_decay_rate))
        sellers = any(c.active and c.sector == "technology" and c.employees for c in self.companies)
        equipped = any(p.technology_level > 0.0 for p in self.people)
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
                company, budget, {"technology": 1.0}, self.companies, self.resources, priced=False
            )
            if paid > 0.0:
                company.market_insight += (1.0 - company.market_insight) * DATA_LEARNING_RATE * paid / full
                self._enterprise_count("donnees", paid)

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
                    self._enterprise_count("fonds_frais", fee)
            targets[fund.id] = dry_powder + 3.0 * fund.requests
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

    def _distribute_fund_cash(self, funds, targets, unit_price, prices) -> None:
        """La trésorerie d'un fonds au-delà de sa réserve d'attente (dividendes reçus, produit des
        cessions) est versée aux porteurs de parts, au prorata : revenu du capital du ménage."""
        for fund in funds:
            excess = fund.cash - targets[fund.id]
            if excess <= 1.0 or fund.units <= 1e-9:
                continue
            holders = [(p, p.fund_units[fund.id]) for p in self.people if p.fund_units.get(fund.id, 0.0) > 0.0]
            held = sum(units for _, units in holders)
            if held <= 0.0:
                continue
            for person, units in holders:
                payment = excess * units / held
                transfer_cash(fund, person, payment)
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
            exit_due = (
                company.fund_backed_month >= 0
                and self.month - company.fund_backed_month >= max(1, live.fund_exit_months)
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
        """Sortie des fonds : chaque mois, ``FUND_EXIT_SPEED`` de leurs titres cotés est mis en
        vente à la séance de Bourse (ordres servis par de vrais acheteurs, au cours)."""
        state = getattr(self, "enterprise", None)
        if state is None or not state.funds:
            return []
        listed = {c.id for c in self.companies if c.active and c.listed}
        return [
            (fund, company_id, quantity * FUND_EXIT_SPEED if quantity > 1.0 else quantity)
            for fund in state.funds
            for company_id, quantity in fund.shares.items()
            if company_id in listed and quantity > 0.0
        ]

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
                surplus = (value - offer) * need
                if value > offer and (best is None or surplus > best[0]):
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
            if signals.get(sector, 0.0) < live.entry_signal_scale:
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
        for company in self.companies:
            if not company.active:
                if company.stores:
                    company.stores = 0.0
                continue
            if company.parent_id is not None:
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
            if company.parent_id is None and not company.listed and company.founder_id is None:
                # ancienne filiale sans société mère : ses titres doivent pouvoir s'échanger
                self._list_company(company)
        state = self._enterprise_state()
        stats = state.month_stats
        prices = {c.id: c.stock_price for c in self.companies if c.active}
        active = [c for c in self.companies if c.active]
        employed = [p for p in self.people if p.employer_id not in (None, "state")]
        stats.update(
            mois=self.month,
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
