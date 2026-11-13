"""Lot V (12 novembre 2026), seconde partie : entreprises et finance.

Six mécanismes, chacun derrière son réglage, désactivés par défaut. L'état et le générateur sont
ceux de ``economy/society.py`` (``simulation.society``).

- **Franchises** (``franchising``) : un producteur d'alimentation ou de biens rentable, dont le
  marché refuse des commandes, loue sa marque et ses méthodes à un petit entrepreneur : droit
  d'entrée, puis redevance sur le chiffre d'affaires. Le franchisé est une entreprise à part
  entière, qui peut échouer.
- **Marques** (``brand_marketing``) : des agences de marketing (secteur « marketing », fondées par
  des ménages) vendent des campagnes aux producteurs. La notoriété d'une marque est un stock
  durable : elle relève le poids du producteur dans les achats des ménages, donc ce qu'il peut
  vendre à un prix donné.
- **Coopératives de chômeurs** (``worker_buyouts``) : les salariés d'une entreprise liquidée la
  relancent avec leur épargne et leurs allocations chômage versées d'avance ; ils en détiennent
  tout le capital, à parts égales et incessibles.
- **Vente à découvert** (``short_selling``) : des fonds spéculatifs financés par des ménages
  empruntent les titres d'une entreprise fragile et les vendent à la séance de Bourse ; ils
  gagnent si le cours baisse ou si l'entreprise fait faillite.
- **Titrisation** (``mortgage_securitization``) : une banque émet des titres adossés à ses prêts
  immobiliers. Le prêt reste inscrit chez elle (elle le gère), mais intérêts, remboursements et
  pertes passent aux porteurs, et les fonds propres correspondants sont libérés : elle peut
  prêter davantage, et le risque est chez les ménages porteurs.
- **Financement participatif** (``crowdfunding``) : les ménages prêtent directement aux jeunes
  entreprises de leur région, sans banque et sans création de monnaie ; en faillite ils passent
  après les banques.

Comptabilité de la titrisation : un titre adossé est un passif des banques. L'identité du bilan
bancaire devient réserves + prêts + titres publics = dépôts + fonds propres + avances + titres
adossés (``Ledger.deposits``). Un achat de titre détruit un dépôt ; un remboursement en recrée un.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from economy.finance import extend_credit
from economy.institutions import display_name
from economy.models import Company
from economy.payments import transfer_cash
from economy.society import MARKETING_SECTOR, VENTURE_SWITCHES, VICE_SECTOR
from economy.treasury import working_capital
from economy.wealth import company_balance_sheet, share_prices

# --- marques
BRAND_SECTORS = ("food", "goods")
CAMPAIGN_OUTPUT = 4.0           # campagnes livrées par un salarié d'agence et par mois, en salaires de référence
BRAND_HALF = 20.0               # notoriété (en salaires de référence) qui donne la moitié de l'effet
# --- franchises
FRANCHISE_SECTORS = ("food", "goods")
FRANCHISE_MIN_AGE = 24          # ancienneté d'un franchiseur, en mois
FRANCHISE_CAPITAL = 2.0         # apport minimal du franchisé, en droits d'entrée
FRANCHISE_SPIRIT = 0.5          # esprit d'entreprise requis, en part du seuil d'un fondateur
FRANCHISE_METHODS = 0.95        # productivité de départ du franchisé / celle du franchiseur
FRANCHISE_CATCH_UP = 0.05       # part de l'écart de productivité comblée chaque mois
FRANCHISE_STAFF = 3             # salariés visés à l'ouverture
FRANCHISE_NETWORK = 3           # franchises en activité par franchiseur au plus
FRANCHISE_INTERVAL = 6          # mois entre deux ouvertures dans l'économie
BUYOUT_MEMORY_MONTHS = 12       # un salarié licencié depuis moins longtemps peut encore reprendre l'entreprise
# --- coopératives
BUYOUT_SECTORS = ("food", "goods", "materials", "technology", "services", VICE_SECTOR, MARKETING_SECTOR)
BUYOUT_SAVINGS_SHARE = 0.5      # part de sa trésorerie qu'un salarié apporte au plus
BUYOUT_SAVINGS_MONTHS = 3.0     # apport maximal d'un salarié, en mois de salaire
BUYOUT_PAYROLL_MONTHS = 2.0     # capital minimal de la reprise, en mois de paie
BUYOUT_WAGE = 0.9               # salaire accepté par les repreneurs / offre de l'entreprise fermée
BUYOUT_CLIENTS = 0.5            # part de la clientèle de l'entreprise fermée que la coopérative garde
# --- fonds spéculatifs
HEDGE_RISK = 0.7                # goût du risque minimal d'un souscripteur
HEDGE_ENTRY = 20.0              # trésorerie minimale d'un souscripteur, en salaires de référence
HEDGE_STAKE = 0.10              # part de sa trésorerie qu'il confie au fonds
HEDGE_BACKERS = 10              # souscripteurs par fonds au plus
HEDGE_MINIMUM = 30.0            # capital minimal d'un fonds, en salaires de référence
HEDGE_RETRY_MONTHS = 12         # délai entre deux tentatives de création
SHORT_LOSS_MONTHS = 3           # mois de pertes à partir desquels une entreprise est une cible
SHORT_POSITIONS = 3             # positions ouvertes par fonds au plus
SHORT_BUDGET = 0.25             # valeur d'une nouvelle position / trésorerie du fonds
SHORT_LENDABLE = 0.5            # part des titres libres des ménages qu'un fonds peut emprunter
SHORT_STOP = 0.25               # hausse du cours qui fait racheter à perte
SHORT_HORIZON = 24              # durée maximale d'une position, en mois
SHORT_RECOVERY_MONTHS = 6       # ancienneté à partir de laquelle un redressement fait racheter
HEDGE_PAYOUT = 1.2              # trésorerie / capital au-delà de laquelle le fonds distribue
HEDGE_RUIN = 0.25               # trésorerie / capital en deçà de laquelle il est dissous
# --- titrisation
ABS_MINIMUM = 5.0               # plus petite émission, en salaires de référence
# --- financement participatif
CROWD_LEVERAGE = 1.0            # encours maximal / fonds propres de l'emprunteur
CROWD_MINIMUM = 2.0             # plus petit prêt, en salaires de référence


@dataclass
class HedgeFund(Company):
    """Fonds spéculatif : un porteur de monnaie hors de la liste des entreprises. ``units`` :
    apports des souscripteurs ; ``positions`` : entreprise -> titres dus aux prêteurs."""

    units: dict = field(default_factory=dict)
    capital: float = 0.0
    positions: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)     # titres empruntés pour la séance du mois
    session_cash: float = 0.0
    gains: float = 0.0
    closed: int = 0


class VenturesMixin:
    """Franchises, marques, coopératives, vente à découvert, titrisation, prêts participatifs."""

    def _ventures_enabled(self) -> bool:
        live = self.live
        return any(getattr(live, name) for name in VENTURE_SWITCHES)

    def _escheat(self, amount: float) -> None:
        """Somme due à un ménage qui n'est plus dans la région : elle revient à l'État."""
        if amount > 0.0:
            self.public.cash += amount
            self.institutions.book.add("desherence", amount)
            self.public.revenue += amount

    def _ventures_operations(self) -> None:
        """Phase 6, avant la clôture des comptes : campagnes, redevances, prêts participatifs,
        titres adossés, distributions des fonds spéculatifs."""
        live = self.live
        self._society_state()
        if live.brand_marketing:
            self._brand_campaigns()
        if live.franchising:
            self._franchise_royalties()
        if live.crowdfunding:
            self._crowdfunding()
        if live.mortgage_securitization:
            self._abs_market()
        if live.short_selling:
            self._hedge_distributions()

    # ------------------------------------------------------------------ V6 : marques
    def _brand_stock(self, company_id: str) -> float:
        """Notoriété dont une entreprise profite : la sienne, ou celle de son franchiseur."""
        state = self.society
        stock = state.brands.get(company_id, 0.0)
        link = state.franchises.get(company_id)
        if link is not None:
            stock = max(stock, state.brands.get(link["franchiseur"], 0.0))
        return stock

    def _brand_factor(self, company_id: str) -> float:
        """Poids dans les achats : 1 + ``brand_reach`` × s / (s + ``BRAND_HALF``), où s est la
        notoriété en salaires mensuels de référence."""
        scaled = self._brand_stock(company_id) / max(self.wage_reference, 1.0)
        return 1.0 + max(0.0, self.live.brand_reach) * scaled / (scaled + BRAND_HALF)

    def _brand_affinity(self, sector: str, base):
        """Poids des producteurs de ``sector`` dans les achats de chaque ménage, marques
        comprises : ``base`` (lots G et M, ou ``None``) × le facteur de chaque marque."""
        state = getattr(self, "society", None)
        if state is None or not state.brands:
            return base
        factors = {}
        for company in self.companies:
            if company.active and company.sector == sector and self._brand_stock(company.id) > 0.0:
                factors[company.id] = self._brand_factor(company.id)
        if not factors:
            return base
        if base is None:
            return {person.id: factors for person in self.people}
        merged = {}
        for person in self.people:
            own = base.get(person.id) or {}
            row = dict(own)
            for company_id, factor in factors.items():
                row[company_id] = own.get(company_id, 1.0) * factor
            merged[person.id] = row
        return merged

    def _brand_campaigns(self) -> None:
        """Campagnes du mois.

        Un producteur d'alimentation ou de biens (ou un fabricant de tabac et d'alcool),
        bénéficiaire le mois écoulé et doté de salariés, y consacre ``marketing_budget_share`` de
        son chiffre d'affaires du mois écoulé, sans entamer son fonds de roulement. Les agences
        livrent ce que leurs salariés produisent (``CAMPAIGN_OUTPUT`` salaires de référence par
        salarié et par mois) ; au-delà, les budgets sont servis au prorata et le reste n'est pas
        dépensé. Chaque euro payé ajoute à la notoriété de l'annonceur, selon la qualité de
        l'agence (0,75 + 0,5 × son innovation). La notoriété s'efface de ``brand_decay_rate`` par
        mois."""
        live = self.live
        state = self.society
        decay = max(0.0, min(1.0, live.brand_decay_rate))
        for company_id in list(state.brands):
            state.brands[company_id] *= 1.0 - decay
            if state.brands[company_id] < 1.0:
                del state.brands[company_id]
        wage = max(self.wage_reference, 1.0)
        agencies = [c for c in self.companies if c.active and c.sector == MARKETING_SECTOR and c.employees]
        capacity = [len(c.employees) * CAMPAIGN_OUTPUT * wage * max(c.productivity_trait, 0.1) for c in agencies]
        total_capacity = sum(capacity)
        share = max(0.0, live.marketing_budget_share)
        if total_capacity <= 0.0 or share <= 0.0:
            return
        budgets = []
        for company in self.companies:
            if (
                not company.active or company.previous_profit <= 0.0 or not company.employees
                or (company.sector not in BRAND_SECTORS and company.sector != VICE_SECTOR)
            ):
                continue
            budget = min(
                share * max(0.0, company.previous_revenue),
                max(0.0, company.cash - working_capital(company, live)),
            )
            if budget > 1.0:
                budgets.append((company, budget))
        wanted = sum(budget for _, budget in budgets)
        if wanted <= 0.0:
            return
        ratio = min(1.0, total_capacity / wanted)
        for company, budget in budgets:
            spend = budget * ratio
            gained = 0.0
            for agency, room in zip(agencies, capacity):
                part = spend * room / total_capacity
                transfer_cash(company, agency, part)
                agency.revenue += part
                agency.units_sold += part / wage
                gained += part * (0.75 + 0.5 * agency.innovation)
            company.expenses += spend
            state.brands[company.id] = state.brands.get(company.id, 0.0) + gained
        if ratio < 1.0:
            for agency, room in zip(agencies, capacity):
                agency.unmet_units += (wanted - total_capacity) * room / total_capacity / wage
        self._society_count("marques_depenses", wanted * ratio)
        self._society_count("marques_non_servies", wanted * (1.0 - ratio))

    # ------------------------------------------------------------------ V5 : franchises
    def _open_franchises(self, f) -> None:
        """Ouverture d'une franchise, au plus une tous les ``FRANCHISE_INTERVAL`` mois (phase 7,
        après les créations) ; un franchiseur a au plus ``FRANCHISE_NETWORK`` franchises.

        Franchiseur : producteur d'alimentation ou de biens d'au moins ``FRANCHISE_MIN_AGE`` mois,
        bénéficiaire, doté de salariés, hors groupe et hors contrôle public, qui a lui-même refusé
        des commandes le mois écoulé dans un marché qui en refuse encore ; le plus connu (notoriété de marque), puis le plus rentable.
        Franchisé : le ménage au plus fort esprit d'entreprise qui n'a pas encore fondé, n'est
        pas au gouvernement, et dont l'apport (la part de sa trésorerie qu'un fondateur engage)
        vaut au moins ``FRANCHISE_CAPITAL`` droits d'entrée. Il apporte ce capital, emprunte
        comme tout entrant, paie le droit d'entrée au franchiseur, et démarre avec le prix, la
        qualité et ``FRANCHISE_METHODS`` de la productivité du franchiseur."""
        live = self.live
        state = self._society_state()
        if self.month - state.last_franchise < FRANCHISE_INTERVAL:
            return
        unmet = {"food": f.unmet_food_demand, "goods": f.unmet_goods_demand}
        excluded = self._public_controlled_ids()
        network: dict[str, int] = {}
        for link in state.franchises.values():
            network[link["franchiseur"]] = network.get(link["franchiseur"], 0) + 1
        candidates = [
            c for c in self.companies
            if c.active and c.sector in FRANCHISE_SECTORS and c.employees
            and c.age_months >= FRANCHISE_MIN_AGE and c.previous_profit > 0.0
            and c.id not in state.franchises and c.id not in excluded and c.parent_id is None
            and unmet[c.sector] > 0.0 and c.previous_unmet_units > 0.0
            and network.get(c.id, 0) < FRANCHISE_NETWORK
        ]
        if not candidates:
            return
        franchisor = max(candidates, key=lambda c: (state.brands.get(c.id, 0.0), c.previous_profit, c.id))
        scale = self._wage_scale()
        fee = max(0.0, live.franchise_entry_fee) * scale
        cabinet = {office.person_id for office in self.institutions.offices.values()}
        spirit = FRANCHISE_SPIRIT * live.entrepreneurial_trait_threshold
        fraction = live.startup_investment_fraction
        founder = None
        for person in self.people:
            if (
                person.founded_company or person.id in cabinet or person.employer_id == "state"
                or person.entrepreneurial_spirit < spirit
                or person.cash * fraction < FRANCHISE_CAPITAL * fee
            ):
                continue
            if founder is None or (person.entrepreneurial_spirit, person.id) > (
                founder.entrepreneurial_spirit, founder.id
            ):
                founder = person
        if founder is None:
            return
        contribution = min(founder.cash * fraction, live.startup_max_capital * scale)
        if contribution < FRANCHISE_CAPITAL * fee:
            return
        shares = 1000.0
        company = Company(
            id=f"franchise_{self.month}_{founder.id}",
            name=f"{franchisor.name} · franchise {display_name(founder.id).split(' ', 1)[-1]}",
            sector=franchisor.sector,
            cash=contribution,
            wage_offer=franchisor.wage_offer,
            target_employees=FRANCHISE_STAFF,
            price=franchisor.price,
            stock_price=max(1.0, contribution / shares),
            previous_stock_price=max(1.0, contribution / shares),
            shares_outstanding=shares,
            treasury_shares=0.0,
            productivity_trait=franchisor.productivity_trait * FRANCHISE_METHODS,
            quality_trait=franchisor.quality_trait,
            risk_tolerance=founder.risk_tolerance,
            pricing_aggression=franchisor.pricing_aggression,
            reinvestment_preference=franchisor.reinvestment_preference,
            innovation=franchisor.innovation,
            executive_id=founder.id,
            ceo_experience=max(1, founder.leadership_experience),
            founder_id=founder.id,
            construction_pipeline=[],
        )
        company.product_style = franchisor.product_style
        capacity = self.credit_capacity()
        if live.entrant_credit_multiple > 0.0 and capacity > 0.0:
            extend_credit(
                company, contribution * live.entrant_credit_multiple, capacity, live, self.policy_rate,
                entrant=True, credit_transaction=self._credit_transaction,
                credit_capacity=self.credit_capacity,
            )
        founder.cash -= contribution
        founder.founded_company = True
        founder.shares[company.id] = shares
        paid = min(fee, max(0.0, company.cash))
        transfer_cash(company, franchisor, paid)
        company.expenses += paid
        franchisor.revenue += paid
        self.companies.append(company)
        if self._enterprise_enabled():
            self._after_startups([company])
        state.last_franchise = self.month
        state.franchises[company.id] = {
            "franchiseur": franchisor.id, "mois": self.month, "droits": paid, "redevances": 0.0,
        }
        self._society_count("franchises_ouvertes")
        self._society_count("franchises_droits", paid)
        self._society_log("franchise", f"{display_name(founder.id)} ouvre une franchise de {franchisor.name}")

    def _franchise_royalties(self) -> None:
        """Redevances du mois : ``franchise_royalty_rate`` du chiffre d'affaires du franchisé, dans
        la limite de sa trésorerie ; il rattrape ``FRANCHISE_CATCH_UP`` de son écart de
        productivité avec le franchiseur. Le contrat s'éteint quand l'un des deux disparaît (le
        franchisé survivant devient indépendant)."""
        state = self.society
        if not state.franchises:
            return
        rate = max(0.0, self.live.franchise_royalty_rate)
        by_id = {c.id: c for c in self.companies}
        for company_id in list(state.franchises):
            link = state.franchises[company_id]
            franchisee = by_id.get(company_id)
            franchisor = by_id.get(link["franchiseur"])
            if franchisee is None or not franchisee.active:
                del state.franchises[company_id]
                self._society_count("franchises_fermees")
                continue
            if franchisor is None or not franchisor.active:
                del state.franchises[company_id]
                self._society_count("franchises_independantes")
                continue
            royalty = min(rate * max(0.0, franchisee.revenue), max(0.0, franchisee.cash))
            if royalty > 0.0:
                transfer_cash(franchisee, franchisor, royalty)
                franchisee.expenses += royalty
                franchisor.revenue += royalty
                link["redevances"] += royalty
                self._society_count("franchises_redevances", royalty)
            target = franchisor.productivity_trait * FRANCHISE_METHODS
            if franchisee.productivity_trait < target:
                franchisee.productivity_trait += FRANCHISE_CATCH_UP * (target - franchisee.productivity_trait)

    # ------------------------------------------------------------------ V7 : coopératives
    def _worker_buyouts(self, f) -> None:
        """Reprise en coopérative des entreprises liquidées ce mois (phase 7, après les faillites).

        Les salariés du mois et ceux licenciés depuis ``BUYOUT_MEMORY_MONTHS`` mois au plus,
        désormais sans emploi (au moins ``buyout_min_workers``), apportent
        chacun au plus ``BUYOUT_SAVINGS_SHARE`` de leur trésorerie et ``BUYOUT_SAVINGS_MONTHS``
        mois de salaire ; l'État leur verse d'avance ``buyout_benefit_months`` mois d'allocation
        chômage, dans la limite de ses fonds. La reprise n'a lieu que si le capital réuni paie
        ``BUYOUT_PAYROLL_MONTHS`` mois de paie. La coopérative garde le secteur, le prix, la
        productivité et la qualité de l'entreprise fermée, ``BUYOUT_CLIENTS`` de sa clientèle, et
        paie ``BUYOUT_WAGE`` de son offre salariale ; les actifs de l'entreprise fermée ont été
        vendus aux enchères, elle repart sans capacité installée. Chaque repreneur reçoit une
        part égale du capital, incessible (parts salariées)."""
        live = self.live
        state = self._society_state()
        rosters = state.rosters
        people = None
        for company in list(self.companies):
            if company.sector not in BUYOUT_SECTORS:
                continue
            if company.active:
                if company.employees:
                    roster = rosters.setdefault(company.id, {})
                    for person_id in company.employees:
                        roster[person_id] = self.month
                continue
            roster = rosters.pop(company.id, None)
            if roster is None or company.exit_month != self.month or company.exit_reason != "bankrupt":
                continue
            for person_id in company.employees:
                roster[person_id] = self.month
            if people is None:
                people = {p.id: p for p in self.people}
            staff = [people[pid] for pid, seen in roster.items()
                     if self.month - seen <= BUYOUT_MEMORY_MONTHS
                     and pid in people and people[pid].employer_id is None]
            if len(staff) < max(1, int(live.buyout_min_workers)):
                continue
            wage = max(company.wage_offer * BUYOUT_WAGE, live.minimum_wage, 1.0)
            own = [min(BUYOUT_SAVINGS_SHARE * max(0.0, p.cash), BUYOUT_SAVINGS_MONTHS * wage) for p in staff]
            wanted = self._benefit_per_person(f.unemployment) * max(0, int(live.buyout_benefit_months)) * len(staff)
            need = BUYOUT_PAYROLL_MONTHS * wage * len(staff)
            if sum(own) + wanted < need:
                self._society_count("reprises_abandonnees")
                continue
            advance = self._state_funds(wanted) if wanted > 0.0 else 0.0
            if sum(own) + advance < need:
                self._society_count("reprises_abandonnees")
                continue
            shares = 1000.0
            leader = max(staff, key=lambda p: (p.leadership_experience, p.skill, p.id))
            coop = Company(
                id=f"coop_{self.month}_{company.id}",
                name=f"Coopérative {company.name}",
                sector=company.sector,
                cash=0.0,
                wage_offer=wage,
                target_employees=len(staff),
                price=company.price,
                stock_price=1.0,
                previous_stock_price=1.0,
                shares_outstanding=shares,
                treasury_shares=0.0,
                productivity_trait=company.productivity_trait,
                quality_trait=company.quality_trait,
                risk_tolerance=company.risk_tolerance,
                pricing_aggression=company.pricing_aggression,
                reinvestment_preference=company.reinvestment_preference,
                innovation=company.innovation,
                executive_id=leader.id,
                ceo_experience=max(1, leader.leadership_experience),
                construction_pipeline=[],
            )
            coop.product_style = company.product_style
            coop.demand_ema = company.demand_ema * BUYOUT_CLIENTS
            coop.revenue_ema = company.revenue_ema * BUYOUT_CLIENTS
            for person, amount in zip(staff, own):
                if amount > 0.0:
                    transfer_cash(person, coop, amount)
            if advance > 0.0:
                transfer_cash(self.public, coop, advance)
                self.institutions.book.add("aide_reprise", -advance)
                self.public.spending += advance
            part = shares / len(staff)
            for person in staff:
                person.shares[coop.id] = part
                person.coop_shares[coop.id] = part
                person.employer_id = coop.id
                person.employer_tenure = 0
                coop.employees.append(person.id)
            coop.stock_price = coop.previous_stock_price = max(1.0, coop.cash / shares)
            self.companies.append(coop)
            state.cooperatives[coop.id] = {
                "origine": company.id, "nom_origine": company.name, "mois": self.month,
                "salaries": len(staff), "capital": coop.cash, "avance": advance,
            }
            self._decide(
                "travail", f"Reprise en coopérative : {company.name}",
                f"{len(staff)} anciens salariés reprennent l'entreprise liquidée ; "
                f"{live.buyout_benefit_months} mois d'allocation chômage versés d'avance",
                amount=advance, financing="budget de l'État (aide à la reprise), hors loi de finances",
            )
            self._society_count("cooperatives_creees")
            self._society_count("cooperatives_salaries", len(staff))
            self._society_count("cooperatives_avances", advance)
            self._society_log("cooperative", f"{len(staff)} salariés reprennent {company.name} en coopérative")

    # ------------------------------------------------------------------ V8 : vente à découvert
    def _ensure_hedge_funds(self) -> None:
        """Création des fonds spéculatifs manquants, au plus une tentative par an : des ménages
        au goût du risque d'au moins ``HEDGE_RISK`` et à la trésorerie d'au moins ``HEDGE_ENTRY``
        salaires de référence confient ``HEDGE_STAKE`` de leur trésorerie ; un fonds naît s'il
        réunit ``HEDGE_MINIMUM`` salaires de référence."""
        state = self.society
        wanted = max(0, int(self.live.hedge_funds))
        missing = wanted - len(state.hedge_funds)
        if missing <= 0 or self.month - state.hedge_attempt < HEDGE_RETRY_MONTHS:
            return
        state.hedge_attempt = self.month
        wage = max(self.wage_reference, 1.0)
        invested = {pid for fund in state.hedge_funds for pid in fund.units}
        backers = sorted(
            (p for p in self.people
             if p.risk_tolerance >= HEDGE_RISK and p.cash >= HEDGE_ENTRY * wage and p.id not in invested),
            key=lambda p: (-p.cash, p.id),
        )
        for index in range(missing):
            group = backers[index::missing][:HEDGE_BACKERS]
            total = sum(HEDGE_STAKE * p.cash for p in group)
            if total < HEDGE_MINIMUM * wage:
                continue
            number = int(state.foundings.get("hedge", 0)) + 1
            state.foundings["hedge"] = number
            fund = HedgeFund(f"hedge_{number}", f"Fonds spéculatif {number}", "hedge", 0.0, 0.0)
            for person in group:
                amount = HEDGE_STAKE * person.cash
                transfer_cash(person, fund, amount)
                fund.units[person.id] = amount
            fund.capital = fund.cash
            state.hedge_funds.append(fund)
            self._society_count("fonds_speculatifs_crees")
            self._society_log("fonds", f"{fund.name} : {len(group)} souscripteurs, "
                                       f"{fund.capital:,.0f} de capital".replace(",", " "))

    def _short_target(self, taken: set, excluded: frozenset):
        """Entreprise la plus fragile : cotée, non bancaire, hors contrôle public, endettée, en
        pertes depuis ``SHORT_LOSS_MONTHS`` mois ; classée par mois de pertes × dette / recettes
        annuelles lissées."""
        best, best_score = None, 0.0
        for company in self.companies:
            if (
                not company.active or not company.listed or company.sector == "bank"
                or company.id in taken or company.id in excluded
                or company.loss_streak < SHORT_LOSS_MONTHS or company.debt <= 0.0
                or company.stock_price <= 1.0 or company.shares_outstanding <= 0.0
            ):
                continue
            score = company.loss_streak * company.debt / (12.0 * max(company.revenue_ema, 0.0) + 1.0)
            if score > best_score or (score == best_score and best is not None and company.id < best.id):
                best, best_score = company, score
        return best

    def _short_orders(self) -> list:
        """Avant la séance de Bourse : frais et clôtures des positions, puis nouvelles ventes à
        découvert. Retourne les ordres de vente (fonds, entreprise, quantité).

        Ouverture : le fonds emprunte aux ménages, au prorata de leurs titres libres (hors parts
        salariées), au plus ``short_float_share`` du flottant, ``SHORT_LENDABLE`` de ces titres
        et ``SHORT_BUDGET`` de sa trésorerie, et les met en vente à la séance.
        Frais : ``short_fee_rate`` de la valeur empruntée par mois, payés aux prêteurs.
        Clôture : le fonds paie aux prêteurs la valeur des titres au cours du jour (rachat de gré
        à gré) quand le cours a monté de ``SHORT_STOP``, après ``SHORT_HORIZON`` mois, ou quand
        l'entreprise s'est redressée ; il ne doit rien si elle a fait faillite."""
        live = self.live
        state = self.society
        orders: list = []
        if not state.hedge_funds:
            return orders
        by_id = {c.id: c for c in self.companies}
        people = {p.id: p for p in self.people}
        fee_rate = max(0.0, live.short_fee_rate)
        excluded = self._public_controlled_ids()
        taken = {cid for fund in state.hedge_funds for cid in fund.positions}
        wage = max(self.wage_reference, 1.0)
        for fund in state.hedge_funds:
            for company_id in list(fund.positions):
                position = fund.positions[company_id]
                company = by_id.get(company_id)
                alive = company is not None and company.active
                if alive:
                    position["cours"] = company.stock_price
                    fee = min(fee_rate * position["titres"] * company.stock_price, max(0.0, fund.cash))
                    self._pay_lenders(fund, position, fee, people, income=True)
                    position["frais"] += fee
                reason = None
                price = position["cours"]
                if not alive:
                    bankrupt = company is not None and company.exit_reason == "bankrupt"
                    reason = "faillite" if bankrupt else "sortie"
                    price = 0.0 if bankrupt else price
                elif price >= position["entree"] * (1.0 + SHORT_STOP):
                    reason = "rachat à perte"
                elif self.month - position["mois"] >= SHORT_HORIZON:
                    reason = "échéance"
                elif (
                    company.loss_streak == 0 and company.previous_profit > 0.0
                    and self.month - position["mois"] >= SHORT_RECOVERY_MONTHS
                ):
                    reason = "redressement"
                if reason is not None:
                    self._close_short(fund, company_id, price, reason, people)
            fund.pending = {}
            fund.session_cash = fund.cash
            if len(fund.positions) >= SHORT_POSITIONS or fund.cash <= 0.0:
                continue
            target = self._short_target(taken, excluded)
            if target is None:
                continue
            price = target.stock_price
            floated = target.shares_outstanding - target.treasury_shares
            lenders = []
            free_total = 0.0
            for person in self.people:
                free = person.shares.get(target.id, 0.0)
                if free > 0.0 and person.coop_shares:
                    free -= person.coop_shares.get(target.id, 0.0)
                if free > 1e-9:
                    lenders.append((person, free))
                    free_total += free
            quantity = min(
                max(0.0, live.short_float_share) * floated,
                SHORT_LENDABLE * free_total,
                SHORT_BUDGET * fund.cash / price,
            )
            if quantity * price < wage:
                continue
            lent = {}
            for person, free in lenders:
                part = quantity * free / free_total
                person.shares[target.id] -= part
                lent[person.id] = part
            fund.shares[target.id] = quantity
            fund.pending[target.id] = {"titres": quantity, "preteurs": lent}
            taken.add(target.id)
            orders.append((fund, target.id, quantity))
        return orders

    def _pay_lenders(self, fund, position: dict, amount: float, people: dict, income: bool = False) -> None:
        """Verse ``amount`` aux prêteurs d'une position, au prorata des titres prêtés ; la part
        d'un prêteur qui n'est plus dans la région revient à l'État."""
        total = sum(position["preteurs"].values())
        if amount <= 0.0 or total <= 0.0:
            return
        for lender_id, quantity in position["preteurs"].items():
            part = amount * quantity / total
            lender = people.get(lender_id)
            fund.cash -= part
            if lender is None:
                self._escheat(part)
                continue
            lender.cash += part
            if income:
                lender.income_this_turn += part
                lender.dividend_income_this_turn += part
                lender.lifetime_earnings += part

    def _close_short(self, fund, company_id: str, price: float, reason: str, people: dict) -> None:
        position = fund.positions.pop(company_id)
        owed = position["titres"] * price
        paid = min(owed, max(0.0, fund.cash))
        self._pay_lenders(fund, position, paid, people)
        gain = position["produit"] - paid - position["frais"]
        fund.gains += gain
        fund.closed += 1
        self.society.shorts.append({
            "mois": self.month, "fonds": fund.name, "entreprise": company_id, "motif": reason,
            "ouverture": position["mois"], "titres": position["titres"], "entree": position["entree"],
            "sortie": price, "gain": gain, "impaye": owed - paid,
        })
        self._society_count("decouvert_clotures")
        self._society_count("decouvert_gains", gain)
        if reason == "faillite":
            self._society_count("decouvert_faillites")

    def _settle_short_sales(self) -> None:
        """Après la séance : les titres invendus retournent aux prêteurs ; les titres vendus
        ouvrent une position au prix moyen obtenu."""
        state = getattr(self, "society", None)
        if state is None:
            return
        people = None
        by_id = None
        for fund in state.hedge_funds:
            for company_id, pending in fund.pending.items():
                if people is None:
                    people = {p.id: p for p in self.people}
                    by_id = {c.id: c for c in self.companies}
                left = fund.shares.pop(company_id, 0.0)
                borrowed = pending["titres"]
                sold = borrowed - left
                kept = sold / borrowed if borrowed > 0.0 else 0.0
                lenders = {}
                for lender_id, quantity in pending["preteurs"].items():
                    lender = people.get(lender_id)
                    if lender is not None and left > 1e-12:
                        lender.shares[company_id] = lender.shares.get(company_id, 0.0) + quantity * (1.0 - kept)
                    if quantity * kept > 1e-12:
                        lenders[lender_id] = quantity * kept
                proceeds = fund.cash - fund.session_cash
                if sold <= 1e-9 or proceeds <= 0.0:
                    continue
                company = by_id[company_id]
                fund.positions[company_id] = {
                    "titres": sold, "preteurs": lenders, "entree": proceeds / sold,
                    "cours": company.stock_price, "mois": self.month, "produit": proceeds, "frais": 0.0,
                }
                self._society_count("decouvert_ouvertures")
                self._society_count("decouvert_ventes", proceeds)
                self._society_log("decouvert", f"{fund.name} vend à découvert {company.name} "
                                               f"({company.loss_streak} mois de pertes)")
            fund.pending = {}

    def _hedge_distributions(self) -> None:
        """Une fois par an et sans position ouverte : un fonds distribue à ses souscripteurs sa
        trésorerie au-delà de ``HEDGE_PAYOUT`` × son capital ; sous ``HEDGE_RUIN`` × son capital,
        il est dissous et leur rend ce qui reste."""
        state = self.society
        if self.month % 12 != 0 or not state.hedge_funds:
            return
        people = {p.id: p for p in self.people}
        for fund in list(state.hedge_funds):
            if fund.positions or fund.pending:
                continue
            ruined = fund.cash < HEDGE_RUIN * fund.capital
            payout = fund.cash if ruined else fund.cash - HEDGE_PAYOUT * fund.capital
            units = sum(fund.units.values())
            if payout > 0.0 and units > 0.0:
                for person_id, held in fund.units.items():
                    part = payout * held / units
                    person = people.get(person_id)
                    fund.cash -= part
                    if person is None:
                        self._escheat(part)
                        continue
                    person.cash += part
                    if not ruined:
                        person.income_this_turn += part
                        person.dividend_income_this_turn += part
                        person.lifetime_earnings += part
                self._society_count("fonds_distributions", payout)
            if ruined:
                if fund.cash > 0.0:
                    self._escheat(fund.cash)
                    fund.cash = 0.0
                state.hedge_funds.remove(fund)
                self._society_count("fonds_speculatifs_dissous")
                self._society_log("fonds", f"{fund.name} : dissous après ses pertes")

    # ------------------------------------------------------------------ V9 : titrisation
    def _securitized(self, bank) -> float:
        """Titres adossés émis par une banque (0 sans titrisation)."""
        pool = self.society.pools.get(bank.id)
        return pool["encours"] if pool is not None else 0.0

    @staticmethod
    def _mortgage_book(bank) -> float:
        return sum(v for account, v in bank.bank_loan_accounts.items()
                   if v > 0.0 and account.startswith("mortgage:"))

    def _abs_snapshot(self) -> dict | None:
        """Avant les mensualités : prêts immobiliers inscrits chez chaque banque (``None`` tant
        qu'aucun titre adossé n'existe)."""
        state = getattr(self, "society", None)
        if state is None or not state.pools:
            return None
        return {
            bank.id: {account: value for account, value in bank.bank_loan_accounts.items()
                      if value > 0.0 and account.startswith("mortgage:")}
            for bank in self.companies if bank.sector == "bank"
        }

    def _abs_pay(self, pool: dict, coupon: float, redeemed: float, written: float, people: dict) -> None:
        """Verse coupon et remboursement aux porteurs d'un fonds de titres, au prorata, et réduit
        leurs titres du remboursement et des pertes."""
        total = pool["encours"]
        if total <= 0.0:
            return
        cash = coupon + redeemed
        keep = max(0.0, 1.0 - (redeemed + written) / total)
        holders = pool["porteurs"]
        for person_id in list(holders):
            amount = holders[person_id]
            share = amount / total
            person = people.get(person_id)
            if person is None:
                self._escheat(cash * share)
            else:
                person.cash += cash * share
                income = coupon * share
                person.income_this_turn += income
                person.dividend_income_this_turn += income
                person.lifetime_earnings += income
            amount *= keep
            if amount > 1e-6:
                holders[person_id] = amount
            else:
                del holders[person_id]
        pool["encours"] = sum(holders.values())
        pool["coupons"] = pool.get("coupons", 0.0) + coupon
        pool["pertes"] = pool.get("pertes", 0.0) + written

    def _abs_pass_through(self, snapshot: dict, debts_before: dict, foreclosed: dict, interest_paid: float) -> None:
        """Après les mensualités et les saisies : chaque banque transmet aux porteurs de ses
        titres la part titrisée (titres / prêts immobiliers de la banque, au plus 1) de ce que ses
        prêts ont produit.

        - **Intérêts** : part des intérêts courus, × la part effectivement payée par les
          emprunteurs ce mois, moins ``securitization_fee_rate`` que la banque garde pour sa
          gestion ; charge de la banque, revenu du capital du porteur.
        - **Remboursements** : le capital remboursé (mensualités et produit des saisies) rembourse
          les titres au pair : un dépôt est recréé pour le porteur.
        - **Pertes** : la perte passée par la banque sur un prêt saisi est reprise à hauteur de la
          part titrisée, et les titres sont réduits d'autant : c'est le porteur qui la subit."""
        state = self.society
        people = {p.id: p for p in self.people}
        by_id = {c.id: c for c in self.companies}
        totals: dict[str, float] = {}
        for accounts in snapshot.values():
            for account, value in accounts.items():
                totals[account] = totals.get(account, 0.0) + value
        accrued = sum(
            debt * people[pid].mortgage_rate for pid, debt in debts_before.items()
            if debt > 0.0 and pid in people
        )
        paid_ratio = min(1.0, interest_paid / accrued) if accrued > 0.0 else 0.0
        fee = max(0.0, min(1.0, self.live.securitization_fee_rate))
        for bank_id, pool in state.pools.items():
            accounts = snapshot.get(bank_id)
            bank = by_id.get(bank_id)
            if pool["encours"] <= 0.0 or not accounts or bank is None:
                continue
            book = sum(accounts.values())
            share = min(1.0, pool["encours"] / book)
            interest = principal = loss = 0.0
            for account, value in accounts.items():
                person_id = account.split(":", 1)[1]
                person = people.get(person_id)
                before = debts_before.get(person_id, 0.0)
                if person is None or before <= 0.0:
                    continue
                part = value / totals[account]
                interest += part * before * person.mortgage_rate
                seized = foreclosed.get(person_id)
                if seized is not None:
                    debt, repaid = seized
                    principal += part * repaid
                    loss += part * (debt - repaid)
                else:
                    principal += part * max(0.0, before - person.mortgage_debt)
            coupon = min(share * interest * paid_ratio * (1.0 - fee), max(0.0, bank.cash))
            bank.cash -= coupon
            bank.expenses += coupon
            written = min(share * loss, pool["encours"])
            if written > 0.0:
                # la perte déjà passée par la banque est reprise : les porteurs la subissent
                bank.expenses -= written
                bank.default_loss -= written
            redeemed = min(share * principal, pool["encours"] - written)
            self._abs_pay(pool, coupon, redeemed, written, people)
            self._society_count("titrisation_coupons", coupon)
            self._society_count("titrisation_remboursements", redeemed)
            self._society_count("titrisation_pertes", written)

    def _abs_orders(self) -> list[tuple]:
        """Ordres d'achat de titres adossés du mois : un ménage vise
        ``securitization_savings_share`` × (1,5 − goût du risque) de son épargne investissable
        (les prudents les croient sûrs) et comble chaque mois une part de l'écart, sans entamer
        sa réserve de consommation."""
        live = self.live
        state = self.society
        held: dict[str, float] = {}
        for pool in state.pools.values():
            for person_id, amount in pool["porteurs"].items():
                held[person_id] = held.get(person_id, 0.0) + amount
        speed = live.household_stock_investment_rate
        share = max(0.0, live.securitization_savings_share)
        orders = []
        for person in self.people:
            own = held.get(person.id, 0.0)
            buffer = live.savings_cash_buffer_months * max(0.0, person.income_this_turn)
            investable = max(0.0, person.cash + own - buffer)
            wanted = min(1.0, share * (1.5 - person.risk_tolerance)) * investable
            order = min(speed * (wanted - own), max(0.0, person.cash - buffer))
            if order > 1.0:
                orders.append((person, order))
        return orders

    def _abs_market(self) -> None:
        """Un mois du marché des titres adossés (phase 6).

        Les titres d'une banque disparue sont repris par la banque active qui a le plus de prêts
        immobiliers. Une banque dont les titres dépassent ses prêts immobiliers rembourse l'écart
        au pair. Puis chaque banque émet jusqu'à ``securitization_share`` de ses prêts
        immobiliers, si le rendement net des titres dépasse celui des dépôts ; les émissions sont
        servies au prorata des ordres des ménages. Un achat détruit le dépôt de l'acheteur ; la
        banque y gagne de la capacité de crédit (``_bank_risk_exposure``), pas de trésorerie."""
        live = self.live
        state = self.society
        banks = [b for b in self.companies if b.active and b.sector == "bank"]
        if not banks:
            return
        books = {b.id: self._mortgage_book(b) for b in banks}
        people = None
        for bank_id in list(state.pools):
            pool = state.pools[bank_id]
            if bank_id not in books:
                heir = max(banks, key=lambda b: (books[b.id], b.id))
                target = state.pools.setdefault(heir.id, {"encours": 0.0, "porteurs": {}})
                for person_id, amount in pool["porteurs"].items():
                    target["porteurs"][person_id] = target["porteurs"].get(person_id, 0.0) + amount
                target["encours"] = sum(target["porteurs"].values())
                del state.pools[bank_id]
        for bank_id, pool in state.pools.items():
            excess = pool["encours"] - books.get(bank_id, 0.0)
            if excess > 1e-6:
                people = people if people is not None else {p.id: p for p in self.people}
                self._abs_pay(pool, 0.0, excess, 0.0, people)
                self._society_count("titrisation_remboursements", excess)
        rates = {p.id: p.mortgage_rate for p in self.people if p.mortgage_debt > 0.0}
        total_book = sum(books.values())
        if total_book <= 0.0:
            return
        weighted = sum(
            value * rates.get(account.split(":", 1)[1], 0.0)
            for bank in banks for account, value in bank.bank_loan_accounts.items()
            if value > 0.0 and account.startswith("mortgage:")
        )
        net_rate = weighted / total_book * (1.0 - max(0.0, min(1.0, live.securitization_fee_rate)))
        if net_rate <= self.deposit_rate:
            return
        minimum = ABS_MINIMUM * max(self.wage_reference, 1.0)
        limit = max(0.0, min(1.0, live.securitization_share))
        issues = []
        for bank in banks:
            pool = state.pools.get(bank.id)
            room = limit * books[bank.id] - (pool["encours"] if pool is not None else 0.0)
            if room >= minimum:
                issues.append((bank, room))
        if not issues:
            return
        orders = self._abs_orders()
        demand = sum(amount for _, amount in orders)
        supply = sum(room for _, room in issues)
        if demand <= 1.0:
            return
        placed = min(demand, supply)
        buyer_fill, issue_fill = placed / demand, placed / supply
        for bank, room in issues:
            pool = state.pools.setdefault(bank.id, {"encours": 0.0, "porteurs": {}})
            holders = pool["porteurs"]
            for person, order in orders:
                part = order * buyer_fill * room / supply
                person.cash -= part
                holders[person.id] = holders.get(person.id, 0.0) + part
            pool["encours"] = sum(holders.values())
            pool["emis"] = pool.get("emis", 0.0) + room * issue_fill
            self._society_count("titrisation_emissions", room * issue_fill)

    # ------------------------------------------------------------------ V10 : financement participatif
    def _crowd_orders(self) -> list[tuple]:
        """Ordres de prêt participatif du mois : un ménage vise ``crowdfunding_savings_share`` ×
        2 × son goût du risque de son épargne investissable, et comble chaque mois une part de
        l'écart avec ses prêts participatifs en cours, sans entamer sa réserve de consommation."""
        live = self.live
        crowd = self.society.crowd
        speed = live.household_stock_investment_rate
        share = max(0.0, live.crowdfunding_savings_share)
        orders = []
        for person in self.people:
            held = 0.0
            if person.corporate_bonds:
                held = sum(amount for issuer, amount in person.corporate_bonds.items() if issuer in crowd)
            buffer = live.savings_cash_buffer_months * max(0.0, person.income_this_turn)
            investable = max(0.0, person.cash + held - buffer)
            wanted = min(1.0, share * 2.0 * person.risk_tolerance) * investable
            order = min(speed * (wanted - held), max(0.0, person.cash - buffer))
            if order > 1.0:
                orders.append((person, order))
        return orders

    def _crowdfunding(self) -> None:
        """Prêts participatifs du mois (phase 6).

        Emprunteur : entreprise non bancaire de ``crowdfunding_max_age_months`` mois au plus,
        fondée par un ménage (ou coopérative, ou franchise), dotée de salariés, hors contrôle
        public, à qui il manque de la trésorerie après le crédit bancaire du mois
        (``_financing_need``), sans que ses prêts participatifs dépassent ``CROWD_LEVERAGE`` × ses
        fonds propres. Taux mensuel : taux directeur + ``crowdfunding_premium`` ; les ménages ne
        prêtent que s'il dépasse celui des dépôts. Un prêt participatif est une obligation au sens
        du lot J (coupon mensuel, amortissement, rang après les banques en faillite) : prêter
        déplace un dépôt, sans créer de monnaie. Sans le lot J, le service de ces prêts est fait
        ici."""
        live = self.live
        state = self.society
        if not live.corporate_bonds:
            self._transfer_absorbed_bonds()
            self._service_corporate_bonds()
        for company_id in state.crowd:
            if company_id not in state.crowd_lost:
                company = next((c for c in self.companies if c.id == company_id), None)
                if company is not None and not company.active and company.exit_reason == "bankrupt":
                    state.crowd_lost.add(company_id)
                    self._society_count("participatif_defauts")
        rate = max(0.0, self.policy_rate) + max(0.0, live.crowdfunding_premium)
        if rate <= self.deposit_rate:
            return
        excluded = self._public_controlled_ids()
        minimum = CROWD_MINIMUM * max(self.wage_reference, 1.0)
        max_age = max(0, int(live.crowdfunding_max_age_months))
        issues = []
        prices = None
        for company in self.companies:
            if (
                not company.active or company.sector == "bank" or company.id in excluded
                or company.age_months > max_age or not company.employees
                or (company.founder_id is None and company.id not in state.cooperatives)
            ):
                continue
            need = self._financing_need(company)
            if need < minimum:
                continue
            if prices is None:
                prices = share_prices(self.companies)
            equity = company_balance_sheet(company, self.companies, live, prices)["Capitaux propres"]
            wanted = min(need, CROWD_LEVERAGE * max(0.0, equity) - company.bond_debt)
            if wanted >= minimum:
                issues.append((company, wanted))
        if not issues:
            return
        orders = self._crowd_orders()
        demand = sum(amount for _, amount in orders)
        supply = sum(wanted for _, wanted in issues)
        if demand <= 1.0:
            return
        placed = min(demand, supply)
        buyer_fill, issue_fill = placed / demand, placed / supply
        for company, wanted in issues:
            amount = wanted * issue_fill
            for person, order in orders:
                part = order * buyer_fill * wanted / supply
                transfer_cash(person, company, part)
                person.corporate_bonds[company.id] = person.corporate_bonds.get(company.id, 0.0) + part
            total = company.bond_debt + amount
            company.bond_rate = (company.bond_debt * company.bond_rate + amount * rate) / total
            company.bond_debt = total
            state.crowd[company.id] = state.crowd.get(company.id, 0.0) + amount
            self._society_count("participatif_prets", amount)
            self._society_count("participatif_emprunteurs")

    # ------------------------------------------------------------------ relevé et lectures
    def _ventures_stats(self, stats: dict) -> None:
        """Grandeurs de la seconde partie du lot V ajoutées au relevé mensuel (mesures seules)."""
        live = self.live
        state = self.society
        if live.brand_marketing:
            agencies = [c for c in self.companies if c.active and c.sector == MARKETING_SECTOR]
            stats["agences"] = len(agencies)
            stats["agences_salaries"] = sum(len(c.employees) for c in agencies)
            stats["marques"] = len(state.brands)
            stats["marques_notoriete"] = sum(state.brands.values())
            for sector in BRAND_SECTORS:
                firms = [c for c in self.companies if c.active and c.sector == sector and c.price > 0.0]
                branded = [c for c in firms if state.brands.get(c.id, 0.0) > 0.0]
                plain = [c for c in firms if state.brands.get(c.id, 0.0) <= 0.0]
                if branded and plain:
                    stats[f"prime_de_marque_{sector}"] = (
                        sum(c.price for c in branded) / len(branded)
                    ) / (sum(c.price for c in plain) / len(plain)) - 1.0
        if live.franchising:
            stats["franchises"] = len(state.franchises)
        if live.worker_buyouts:
            active = {c.id for c in self.companies if c.active}
            stats["cooperatives"] = sum(1 for cid in state.cooperatives if cid in active)
        if live.short_selling:
            stats["fonds_speculatifs"] = len(state.hedge_funds)
            stats["fonds_speculatifs_tresorerie"] = sum(f.cash for f in state.hedge_funds)
            stats["decouvert_positions"] = sum(len(f.positions) for f in state.hedge_funds)
            stats["decouvert_engagement"] = sum(
                p["titres"] * p["cours"] for f in state.hedge_funds for p in f.positions.values()
            )
        if live.mortgage_securitization:
            stats["titrisation_encours"] = state.securitized()
            stats["titrisation_porteurs"] = len({pid for pool in state.pools.values() for pid in pool["porteurs"]})
            mortgages = sum(p.mortgage_debt for p in self.people)
            stats["titrisation_part"] = state.securitized() / mortgages if mortgages > 0.0 else 0.0
        if live.crowdfunding:
            active = {c.id: c for c in self.companies if c.active}
            stats["participatif_encours"] = sum(active[cid].bond_debt for cid in state.crowd if cid in active)
            stats["participatif_entreprises"] = sum(
                1 for cid in state.crowd if cid in active and active[cid].bond_debt > 0.0
            )

    def brands_table(self) -> list[dict]:
        """Une ligne par marque : notoriété, poids dans les achats, prix."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        rows = []
        for company in self.companies:
            if company.active and self._brand_stock(company.id) > 0.0:
                rows.append({
                    "id": company.id, "nom": company.name, "secteur": company.sector,
                    "notoriete": self._brand_stock(company.id), "poids": self._brand_factor(company.id),
                    "prix": company.price, "franchise": company.id in state.franchises,
                })
        return sorted(rows, key=lambda row: (-row["notoriete"], row["id"]))

    def franchises_table(self) -> list[dict]:
        """Une ligne par franchise en activité."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        by_id = {c.id: c for c in self.companies}
        offset = self.warmup_offset
        rows = []
        for company_id, link in state.franchises.items():
            company = by_id.get(company_id)
            franchisor = by_id.get(link["franchiseur"])
            if company is None or not company.active:
                continue
            rows.append({
                "id": company_id, "nom": company.name,
                "franchiseur": franchisor.name if franchisor is not None else link["franchiseur"],
                "periode": link["mois"] - offset, "droits": link["droits"], "redevances": link["redevances"],
                "salaries": len(company.employees), "tresorerie": company.cash,
            })
        return rows

    def cooperatives_table(self) -> list[dict]:
        """Une ligne par coopérative créée, en activité ou non."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        by_id = {c.id: c for c in self.companies}
        offset = self.warmup_offset
        rows = []
        for company_id, entry in state.cooperatives.items():
            company = by_id.get(company_id)
            rows.append({
                "id": company_id, "nom": company.name if company is not None else company_id,
                "origine": entry["nom_origine"], "periode": entry["mois"] - offset,
                "repreneurs": entry["salaries"], "capital": entry["capital"], "avance": entry["avance"],
                "active": bool(company is not None and company.active),
                "salaries": len(company.employees) if company is not None and company.active else 0,
            })
        return rows

    def hedge_table(self) -> list[dict]:
        """Une ligne par fonds spéculatif en activité."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        return [{
            "nom": fund.name, "capital": fund.capital, "tresorerie": fund.cash,
            "souscripteurs": len(fund.units), "positions": len(fund.positions),
            "engagement": sum(p["titres"] * p["cours"] for p in fund.positions.values()),
            "gains": fund.gains, "clotures": fund.closed,
        } for fund in state.hedge_funds]

    def shorts_table(self) -> list[dict]:
        """Positions à découvert closes, de la plus récente à la plus ancienne."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        offset = self.warmup_offset
        labels = {c.id: c.name for c in self.companies}
        return [{**row, "entreprise": labels.get(row["entreprise"], row["entreprise"]),
                 "periode": row["mois"] - offset}
                for row in reversed(state.shorts) if row["mois"] - offset >= 0]

    def pools_table(self) -> list[dict]:
        """Une ligne par banque émettrice de titres adossés."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        by_id = {c.id: c for c in self.companies}
        rows = []
        for bank_id, pool in state.pools.items():
            bank = by_id.get(bank_id)
            book = self._mortgage_book(bank) if bank is not None else 0.0
            rows.append({
                "banque": bank.name if bank is not None else bank_id, "encours": pool["encours"],
                "prets_immobiliers": book, "part": pool["encours"] / book if book > 0.0 else 0.0,
                "porteurs": len(pool["porteurs"]), "emis": pool.get("emis", 0.0),
                "coupons": pool.get("coupons", 0.0), "pertes": pool.get("pertes", 0.0),
            })
        return rows

    def crowd_table(self) -> list[dict]:
        """Une ligne par entreprise financée par des prêts participatifs."""
        state = getattr(self, "society", None)
        if state is None:
            return []
        by_id = {c.id: c for c in self.companies}
        rows = []
        for company_id, raised in state.crowd.items():
            company = by_id.get(company_id)
            if company is None:
                continue
            rows.append({
                "id": company_id, "nom": company.name, "secteur": company.sector, "leve": raised,
                "encours": company.bond_debt if company.active else 0.0,
                "taux_annuel": 12.0 * company.bond_rate, "active": company.active,
                "defaut": company_id in state.crowd_lost,
            })
        return rows
