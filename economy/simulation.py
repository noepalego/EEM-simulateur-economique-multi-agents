from __future__ import annotations

import copy
import math
import random

from economy.business import (
    adjust_profit_sharing_wages,
    elect_executives,
    invest_in_innovation,
    invest_in_production_capacity,
    planned_capacity_investment,
)
from economy.consolidation import takeover_by_value
from economy.energy import (
    decay_infrastructure,
    invest_in_energy_plants,
    trade_infrastructure,
)
from economy.intersector import pay_suppliers_from_household, settle_operating_inputs, household_suppliers
from economy.finance import (
    create_household_startups,
    request_operating_credit,
    resolve_distressed_companies,
    service_company_debt,
)
from economy.goods import (
    charge_inventory_holding_cost,
    collect_recyclable_materials,
    household_budget,
    produce_consumer_goods,
    produce_food,
    sell_consumer_goods,
    sell_food,
    spending_income,
    trade_technology,
    update_income_expectations,
)
from economy.labor import (
    assign_ranks,
    initial_skills,
    allocate_and_pay_labor,
    redistribute_orphan_fund,
    update_workforce_targets,
    workforce_targets,
)
from economy.models import (
    Company,
    MarketRecord,
    Person,
    Resources,
    SimulationConfig,
    positive_loan_book,
    productivity_multiplier,
)
from economy.real_estate import (
    allocate_housing_and_collect_rent,
    build_housing,
    buy_homes,
    complete_construction,
    maintain_housing,
    pay_construction_progress,
    service_mortgages,
)
from economy.trades import TradeMarket, assign_trade
from economy.deliveries import open_energy_supply, prepare_delivery_month
from economy.household_credit import (
    extend_consumer_credit,
    foreclose_homes,
    service_consumer_credit,
)
from economy.stock_market import (
    distribute_dividends,
    trade_bonds_with_banks,
    update_prices_and_trade,
)
from economy.treasury import plan_corporate_treasury, working_capital
from economy.firm_pricing import signal_index_change, update_firm_prices
from economy.expectations import (
    planned_production,
    update_cost_expectations,
    update_demand_forecasts,
    update_housing_gaps,
)
from economy.wealth import company_balance_sheet, enrich_wealth_record, share_prices
from economy.banking import BankingMixin
from economy.central_bank import CentralBankMixin
from economy.pricing import PricingMixin
from economy.housing import HousingMixin
from economy.wages import WageMixin
from economy.views import ViewsMixin
from economy.central_bank import CentralBank
from economy.government import PublicSector
from economy.ledger import Ledger
from economy.innovation import research_effort, update_knowledge
from economy.politics import ALL_LEVERS, Government
from economy.public_finance import PublicFinanceMixin
from economy.records import MonthFlows, build_record
from economy.sectors import SECTORS, SKILL_SECTORS
from economy.state_budget import StateBudgetMixin
from economy.state_government import StateGovernmentMixin
from economy.state_industry import StateIndustryMixin
from economy.state_labor import StateLaborMixin
from economy.state_services import StateServicesMixin
from economy.demography import DEMOGRAPHY_SEED_OFFSET, DemographyMixin


class EconomySimulation(PublicFinanceMixin, StateGovernmentMixin, StateBudgetMixin, StateServicesMixin, StateLaborMixin, StateIndustryMixin, BankingMixin, CentralBankMixin, PricingMixin, HousingMixin, WageMixin, DemographyMixin, ViewsMixin):
    def __init__(self, config: SimulationConfig | None = None) -> None:
        # ``config`` : paramètres choisis par l'utilisateur, jamais modifiés par le moteur.
        # ``live`` : copie de travail du mois, où vivent les prix de marché dérivés
        # (prix et coût de construction des logements).
        self.config = config or SimulationConfig()
        self.live = copy.copy(self.config)
        self.random = random.Random(self.live.seed)
        self.labor_random = random.Random(self.live.seed + 7919)
        self.trait_random = random.Random(self.live.seed + 104729)
        self.politics_random = random.Random(self.live.seed + 32452843)
        self.event_random = random.Random(self.live.seed + 49979687)
        # erreurs de prévision des entreprises (mode « local ») : générateur propre
        self.forecast_random = random.Random(self.live.seed + 67867967)
        # âges, décès et traits des héritiers (idée 3) : générateur propre, aucun autre tirage déplacé
        self.demography_random = random.Random(self.live.seed + DEMOGRAPHY_SEED_OFFSET)
        self.education_quality = 0.0  # qualité de l'éducation publique reçue (idée 5)
        self.trades = TradeMarket(self.live, random.Random(self.live.seed + 15485863))
        self.month = 0
        self.food_price = self.live.starting_food_price
        self.goods_price = self.live.starting_goods_price
        self.rent = self.live.starting_rent
        self.price_index = 100.0
        self.wage_reference = self.live.starting_wage
        self.policy_rate = self.live.loan_interest_rate
        self.expected_inflation = self.live.inflation_target  # anticipations adaptatives
        self.people = [
            Person(
                f"p{i:04d}",
                f"Ménage {i + 1}",
                self.live.initial_person_cash,
                skill=self.random.uniform(0.65, 1.35),
                risk_tolerance=self.random.random(),
                consumption_propensity=self.random.uniform(
                    1.0 - self.live.household_propensity_variance,
                    1.0 + self.live.household_propensity_variance,
                ),
                quality_preference=self.random.uniform(0.65, 1.35),
            )
            for i in range(self.live.population_size)
        ]
        for person in self.people:
            person.skills = initial_skills(person.skill, self.labor_random, SKILL_SECTORS)
            person.entrepreneurial_spirit = self.trait_random.random()
            person.trade = assign_trade(self.trait_random, self.live)
            person.job_trade = person.trade
        self._seed_ages()
        self.initial_population_size = len(self.people)
        self.population_growth_progress = 0.0
        self.population_job_progress = 0.0
        self.new_households_this_turn = 0
        self.companies = self._create_companies()
        for company in self.companies:
            if company.sector == "energy":
                # parc de centrales initial (capacité physique en unités normalisées / mois)
                company.energy_capacity = self.live.energy_initial_capacity
        initial_housing = min(
            self.live.land_units,
            max(1, int(len(self.people) * 0.9)),
        )
        property_firms = [company for company in self.companies if company.sector == "real_estate"]
        for index in range(initial_housing):
            property_firms[index % len(property_firms)].property_inventory += 1
        for company in self.companies:
            initial_assets = (
                company.cash + company.property_inventory * self.live.housing_price
            )
            company.stock_price = max(
                1.0, initial_assets / max(company.shares_outstanding, 1.0)
            )
            company.previous_stock_price = company.stock_price
        self.resources = Resources(
            raw_materials=max(0.0, self.live.raw_materials),
            land_units=max(0, self.live.land_units - initial_housing),
            knowledge=max(100.0, self.live.raw_materials * 0.25),
            raw_material_price=self.live.raw_material_unit_price,
        )
        self.history: list[MarketRecord] = []
        self.last_event = "Aucun événement"
        self.public = PublicSector(account=Company("state", "État", "state", 0.0, 0.0))
        orientation = self.live.initial_political_orientation
        # gouvernement initial : sans écart propre, tous les leviers suivent l'orientation
        self.government = Government(
            orientation, 0, {k: (orientation - 50.0) / 50.0 for k in ALL_LEVERS}
        )
        self.government_history = [self.government]
        self.total_tax_revenue = 0.0
        self.total_benefits_paid = 0.0
        self.total_defaults = 0
        # taux de défaut annuels observés par les banques (lissés) ; départ = configuration
        self._firm_default_rate = self.config.observed_default_rate
        self._household_default_rate = self.config.observed_household_default_rate
        self._firm_loss_given_default = self.config.observed_loss_given_default
        self._investment_history: list[float] = []
        self._smoothed_productivity = 0.0
        self.productivity_index = 1.0
        self.wage_terms: dict[str, float] = {}
        self.central_bank = CentralBank()
        self.resolution_events: list = []  # faillites, restructurations, résolutions bancaires
        self.shocks: list = []  # chocs exogènes programmés (voir models.Shock, experiments.py)
        self.ledger = Ledger()
        self.deposit_rate = 0.0
        self._unmet_ratio = {"food": 0.0, "goods": 0.0}
        self._raw_shortfall = 0.0
        self._last_refused_orders: dict = {}
        self._restart_value_by_id: dict = {}
        self._base_build_cost = self.live.housing_build_cost
        self._housing_markup = self.live.housing_price / max(
            self.live.housing_build_cost, 1.0
        )
        self._land_progress = 0.0
        self._input_index_ema = 1.0
        self._seed_property_financing()
        self._seed_household_ownership()
        self._calibrate_money()
        self.warmup_offset = 0  # mois de chauffe écoulés avant la période 0
        self._warm_up()

    @property
    def observed_history(self) -> list[MarketRecord]:
        """Mois affichés : depuis la période 0 (fin de la chauffe), sans les mois de chauffe
        que ``history`` garde pour les moyennes glissantes."""
        return [record for record in self.history if record.month >= 0]

    @property
    def period(self) -> int:
        """Mois observé : 0 à la fin de la période de chauffe (horloge interne ``month``)."""
        return self.month - self.warmup_offset

    def _warm_up(self) -> None:
        """Période de chauffe : l'économie construit elle-même son état initial.

        L'état de départ (paramètres de ``SimulationConfig``) est arbitraire et
        loin du régime du modèle : chômage de 32 %, prix alimentaires trois fois
        trop bas face aux salaires, services presque absents, ni capital ni
        crédit immobilier. Plutôt que de régler à la main chaque stock (emplois,
        capital, bilans, portefeuilles, logements), on laisse tourner
        ``warmup_months`` mois avec les mêmes règles, puis l'observation commence :
        aucun mécanisme n'est modifié, seul le début de l'affichage change.
        On garde les 24 derniers mois d'historique (moyennes glissantes de la
        banque centrale, des salaires et du budget).
        """
        months = max(0, int(self.config.warmup_months))
        if months == 0:
            return
        for _ in range(months):
            self.step()
        self.warmup_offset = self.month
        self.history = self.history[-24:]
        for record in self.history:
            record.month -= self.warmup_offset
        self.resolution_events = []
        self.government_history = [self.government]
        self.total_tax_revenue = 0.0
        self.total_benefits_paid = 0.0
        self.total_defaults = 0
        if getattr(self, "institutions", None) is not None:
            # nouvel État : journaux traités comme l'historique des gouvernements
            self._institutions_after_warmup()

    def _calibrate_money(self) -> None:
        """Bilan bancaire initial : les réserves complètent les fonds propres après les prêts.

        Sans dépôt initial (la trésorerie des agents est de la monnaie fiduciaire),
        ``réserves = fonds propres - prêts`` ; la comptabilité part de l'équilibre.
        """
        banks = [c for c in self.companies if c.sector == "bank" and c.active]
        equity = sum(b.bank_equity for b in banks)
        loans = sum(c.debt for c in self.companies if c.sector != "bank") + sum(
            p.mortgage_debt for p in self.people
        )
        reserves = max(0.0, equity - loans)
        self.central_bank.initial_reserves = reserves
        self.central_bank.reserves = reserves
        self.ledger.calibrate(self)

    def _seed_household_ownership(self) -> None:
        """Les ménages détiennent à parts égales l'essentiel du capital initial.

        Sans actionnaires, les dividendes (bénéfices, trésorerie excédentaire,
        capital bancaire en excès) ne retourneraient à personne : l'argent
        resterait dans les entreprises. Une fraction
        ``initial_household_ownership`` des actions de chaque entreprise est donc
        répartie également entre les ménages ; le reste est en trésorerie
        (émission future). Aucun mouvement de trésorerie.
        """
        fraction = self.live.initial_household_ownership
        if fraction <= 0.0 or not self.people:
            return
        for company in self.companies:
            held = company.treasury_shares * fraction
            if held <= 0.0:
                continue
            per_person = held / len(self.people)
            company.treasury_shares -= held
            for person in self.people:
                person.shares[company.id] = person.shares.get(company.id, 0.0) + per_person

    def _seed_property_financing(self) -> None:
        """Le parc locatif initial est en partie financé par la dette bancaire.

        Condition initiale (aucune monnaie créée) : les promoteurs détiennent
        déjà des logements achetés à crédit, ce qui donne aux banques un
        portefeuille de prêts dès le premier mois.
        """
        leverage = self.live.initial_property_leverage
        if leverage <= 0.0:
            return
        rate = self.policy_rate + self.live.credit_risk_spread * 0.5
        for company in self.companies:
            if company.sector != "real_estate" or company.property_inventory <= 0:
                continue
            debt = company.property_inventory * self.live.housing_price * leverage
            company.debt += debt
            company.debt_interest_rate = rate
            company.loan_spread = self.live.credit_risk_spread * 0.5
            self._book_bank_loan(company.id, debt, opening_balance=True)


    def _create_companies(self) -> list[Company]:
        companies: list[Company] = []
        targets = workforce_targets(self.live)
        for sector, label in SECTORS:
            if sector == "public":
                continue
            for index in range(self.live.companies_per_sector):
                company_id = f"{sector}_{index + 1}"
                initial_cash = (
                    self.live.bank_initial_reserves
                    / self.live.companies_per_sector
                    if sector == "bank"
                    else self.live.initial_company_cash
                )
                companies.append(
                    Company(
                        id=company_id,
                        name=f"{label} {index + 1}",
                        sector=sector,
                        cash=initial_cash,
                        wage_offer=self.live.starting_wage
                        * self.live.sector_wage_factor.get(sector, 1.0)
                        * self.random.uniform(0.92, 1.08),
                        target_employees=targets[sector],
                        price=(
                            self.live.starting_food_price
                            if sector == "food"
                            else self.live.raw_material_unit_price
                            if sector == "materials"
                            else self.live.starting_goods_price
                        ),
                        stock_price=max(1.0, self.live.initial_company_cash / 1000.0),
                        previous_stock_price=max(1.0, self.live.initial_company_cash / 1000.0),
                        productivity_trait=self.random.uniform(
                            1.0 - self.live.trait_variance,
                            1.0 + self.live.trait_variance,
                        ),
                        quality_trait=self.random.uniform(
                            1.0 - self.live.quality_trait_variance,
                            1.0 + self.live.quality_trait_variance,
                        ),
                        risk_tolerance=self.random.random(),
                        pricing_aggression=self.random.random(),
                        reinvestment_preference=max(
                            0.05,
                            min(
                                1.0,
                                self.live.investment_retention_rate
                                + self.random.uniform(-0.3, 0.3),
                            ),
                        ),
                        innovation=self.random.uniform(0.4, 1.0),
                        bank_equity=initial_cash if sector == "bank" else 0.0,
                    )
                )
        return companies


    def step(self) -> MarketRecord:
        """Avance d'un mois. Les phases s'enchaînent dans un ordre fixe : chacune
        lit et complète les flux du mois (``MonthFlows``)."""
        flows = MonthFlows()
        self._begin_month(flows)
        self._credit_and_labor(flows)
        self._consumer_markets(flows)
        self._public_sector(flows)
        self._investment_and_housing(flows)
        self._close_books(flows)
        self._industry_dynamics(flows)
        return self._record_month(flows)

    # ------------------------------------------------------------------
    # Phase 1 : début de mois (indices, banque centrale, remise à zéro)
    # ------------------------------------------------------------------
    def _begin_month(self, f: MonthFlows) -> None:
        self.month += 1
        # lecture seule jusqu'à _update_housing_costs, qui recrée ``live`` (copie complète)
        self.live = copy.copy(self.config)
        self.resources.b2b_price_elasticity = self.config.b2b_price_elasticity
        # commandes refusées du mois écoulé, gardées pour juger la reprise d'une entreprise sans
        # salarié (idée 2) avant d'être remises à zéro
        self._last_refused_orders = dict(self.resources.refused_orders)
        if self.resources.refused_orders:
            self.resources.refused_orders = {}
        self.new_households_this_turn = 0
        self.resources.household_technology_revenue = 0.0
        self.resources.household_technology_units = 0.0
        self._update_wage_reference()
        # intrants indexés sur le niveau des prix des producteurs (pas sur le loyer)
        producer_weights = self.live.cpi_food_weight + self.live.cpi_goods_weight
        raw_index = max(
            0.5,
            (
                self.live.cpi_food_weight * self.food_price / self.live.starting_food_price
                + self.live.cpi_goods_weight * self.goods_price / self.live.starting_goods_price
            )
            / max(producer_weights, 1e-9),
        )
        # lissage : les contrats d'intrants se renégocient lentement
        self._input_index_ema += self.live.input_index_smoothing * (raw_index - self._input_index_ema)
        self.resources.input_cost_index = self._input_index_ema
        self._update_housing_costs()  # recrée ``live`` : les décisions publiques s'appliquent après
        # présidence, ministres, décisions durables (economy/state_government.py) ; la TVA est
        # celle que vote la loi de finances, sans baisse automatique
        self._govern_institutions()
        self._apply_shocks()
        # valeur des stocks (garanties, solvabilité) aux prix courants
        self.live.observed_default_rate = self._firm_default_rate
        self.live.observed_household_default_rate = self._household_default_rate
        self.live.observed_loss_given_default = self._firm_loss_given_default
        self.live.food_stock_price = self.food_price
        self.live.goods_stock_price = self.goods_price
        self.live.public_capital_bonus = self.public.productivity_bonus(
            self._annual_gdp(), self.live.public_capital_productivity
        )
        # productivité : stock de connaissances (calculé le mois précédent)
        self.live.knowledge_bonus = self.resources.knowledge_index - 1.0
        # investissements : le taux d'actualisation suit le taux directeur
        self.live.tech_discount_rate = max(
            0.003, self.policy_rate + self.live.investment_risk_premium
        )
        f.money_issued = self._central_bank_operations()
        self.resources.recycled_materials += self.resources.recycling_pending
        self.resources.recycling_pending = 0.0
        self._roll_over_firms(f)
        self._interbank_market()
        prepare_delivery_month(self.companies, self.resources, self.live)
        # Pas de cliquet de consommation : propension inchangée
        for person in self.people:
            # revenus du capital du mois écoulé : versés après le prélèvement du mois (dividendes en
            # phase 6, loyers en phase 5), ils entrent dans l'assiette de l'impôt du mois suivant
            person.capital_income_last_month = (
                person.dividend_income_this_turn + person.rent_income_this_turn
            )
            person.dividend_income_this_turn = 0.0
            person.rent_income_this_turn = 0.0
        for person in self.people:
            person.technology_level *= (
                1.0 - self.live.technology_household_depreciation_rate
            )
        charge_inventory_holding_cost(self.companies, self.resources, self.live)
        pay_construction_progress(self.companies, self.resources, self.live)
        f.construction_completed = complete_construction(self.companies, self.month, self.live)
        self.last_event, f.event_sector = self._generate_event()
        self._apply_event_effects()
        if self.last_event.startswith("Choc de matières premières"):
            self.resources.raw_materials *= 0.9

    def _apply_shocks(self) -> None:
        """Applique les chocs programmés actifs ce mois (``self.shocks``) à ``live``.

        Les chocs passent par les mêmes canaux que les politiques : ils ne fixent
        aucun résultat, seulement un paramètre ou une capacité physique du mois.
        """
        live = self.live
        self.resources.energy_capacity_multiplier = 1.0
        for shock in self.shocks:
            if not shock.active(self.period):
                continue
            if shock.kind == "taux":
                live.policy_rate_offset += shock.size
            elif shock.kind == "energie":
                self.resources.energy_capacity_multiplier *= max(0.0, 1.0 - shock.size)
            elif shock.kind == "tva":
                live.vat_rate = max(0.0, live.vat_rate + shock.size)
            elif shock.kind == "depenses":
                live.government_spending_rate *= max(0.0, 1.0 - shock.size)
            elif shock.kind == "credit":
                live.capital_requirement += shock.size

    def _roll_over_firms(self, f: MonthFlows) -> None:
        """Clôture comptable du mois précédent : remise à zéro des flux, usure du
        capital, péremption des stocks."""
        live = self.live
        spoiled_food = spoiled_goods = 0.0
        for company in self.companies:
            if not company.active:
                continue
            # résultat de clôture : les ventes de capacité des centrales tombent après la clôture
            # (défaut de comptes connu, docs/AUTONOMIE_STABLE.md § 10)
            company.previous_profit = company.profit
            company.previous_revenue = company.revenue
            company.revenue = 0.0
            company.expenses = 0.0
            company.financial_income = 0.0
            company.previous_units_sold = company.units_sold
            company.previous_unmet_units = company.unmet_units
            company.unmet_units = 0.0
            company.previous_homes_sold = company.homes_sold
            company.homes_sold = 0
            company.units_sold = 0.0
            company.units_produced = 0.0
            company.stock_trade_volume = 0.0
            company.dividend_paid = 0.0
            company.capital_expenditure = 0.0
            company.secondary_asset_purchases = 0.0
            company.construction_spending = 0.0
            company.property_cost_sold_this_turn = 0.0
            company.depreciation_this_turn = 0.0
            company.interest_paid_this_turn = 0.0
            company.principal_paid_this_turn = 0.0
            company.materials_supplied_this_turn = 0.0
            company.customer_revenue.clear()
            company.supplier_spending.clear()
            company.energy_bill_previous = company.energy_bill
            company.energy_bill = 0.0
            company.infrastructure_sold_value = 0.0
            decay_infrastructure(company, live)
            company.output_multiplier = productivity_multiplier(company, live)
            company.age_months += 1
            spoiled_food += company.inventory * live.inventory_spoilage_rate
            spoiled_goods += company.goods_inventory * live.inventory_spoilage_rate
            company.inventory *= 1.0 - live.inventory_spoilage_rate
            company.goods_inventory *= 1.0 - live.inventory_spoilage_rate
            previous_technology_asset = company.technology_asset_value
            technology_depreciation = company.tech_level * live.technology_depreciation_rate
            company.tech_level -= technology_depreciation
            company.technology_asset_value *= 1.0 - live.technology_depreciation_rate
            technology_asset_depreciation = (
                previous_technology_asset - company.technology_asset_value
                if previous_technology_asset > 0.0
                else technology_depreciation * live.tech_unit_price / 0.03
            )
            previous_productive_capital = company.productive_capital_value
            capital_decay = live.sector_capital_depreciation.get(
                company.sector, live.productive_capacity_depreciation_rate
            )
            company.production_capacity *= 1.0 - live.productive_capacity_depreciation_rate
            company.energy_capacity *= 1.0 - capital_decay  # usure des centrales
            company.productive_capital_value *= 1.0 - capital_decay
            productive_capital_depreciation = (
                previous_productive_capital - company.productive_capital_value
            )
            company.depreciation_this_turn = (
                technology_asset_depreciation + productive_capital_depreciation
            )
            if company.sector == "real_estate":
                # le parc locatif s'use : charge d'amortissement (non monétaire)
                company.depreciation_this_turn += (
                    company.property_inventory
                    * live.housing_price
                    * live.real_estate_depreciation_rate
                )
            company.expenses += company.depreciation_this_turn
        f.spoiled_food = spoiled_food
        f.spoiled_goods = spoiled_goods

    # ------------------------------------------------------------------
    # Phase 2 : crédit d'exploitation, marché du travail, impôts des ménages
    # ------------------------------------------------------------------
    def _credit_and_labor(self, f: MonthFlows) -> None:
        live = self.live
        # recrues du privé de l'État : préavis terminé, elles quittent leur employeur
        self._join_public_recruits()
        # chaque promoteur prévoit sa propre demande : le « déficit » qui guide le crédit des
        # promoteurs et l'entrée dans l'immobilier est la somme de leurs projets, pas le déficit
        # total de logements (qu'aucun ne connaît)
        f.housing_shortage = update_housing_gaps(self.companies, live, self.forecast_random)
        if not any(c.active and c.sector == "real_estate" for c in self.companies):
            # sans promoteur, les demandes de location des ménages sans logement ne sont reçues
            # par personne : elles signalent directement le besoin à un entrant
            f.housing_shortage += sum(
                1 for p in self.people if not p.owns_home and p.rental_owner_id is None
            )
        banks_now = [c for c in self.companies if c.active and c.sector == "bank"]
        bank_level = (
            sum(c.tech_level for c in banks_now) / len(banks_now) if banks_now else 0.0
        )
        request_operating_credit(
            self.companies,
            self.people,
            self.credit_capacity(),
            live,
            f.housing_shortage,
            self.policy_rate,
            credit_multiplier=1.0 + live.bank_credit_gain_per_level * bank_level,
            spread_multiplier=max(0.3, 1.0 - live.bank_spread_gain_per_level * bank_level),
            credit_transaction=self._credit_transaction,
            credit_capacity=self.credit_capacity,
        )
        update_workforce_targets(self.companies, self.people, live)
        # postes publics ouverts comptés dans la demande de métiers ; reconversions financées
        self.trades.refresh(self.people, self.companies, self._public_trade_demand())
        self.trades.retrain(self.people, self.resources, self.wage_reference, self.companies,
                            sponsor=self._training_sponsor)
        # agents et cabinet restent au service de l'État
        public_staff, unavailable = self._public_labor_sets()
        average_wage, _ = allocate_and_pay_labor(
            self.companies,
            self.people,
            self.wage_reference,
            live,
            self.labor_random,
            self.trades.factors(),
            public_staff=public_staff,
            unavailable=unavailable,
            public_stability=live.public_stability_value,
            full_pay_hiring=True,
            restart_values=self._restart_values() if live.shell_restart_on_value else None,
        )
        firm_employment = sum(person.employer_id not in (None, "state") for person in self.people)
        # postes par service, recrutement concurrentiel, paie de la grille et du cabinet
        self._staff_public_services(f)
        # énergie produisible ce mois : centrales et effectifs du mois
        open_energy_supply(self.companies, self.resources, live)
        redistribute_orphan_fund(self.people, self.resources)
        total_employed_after_services = sum(
            person.employer_id is not None for person in self.people
        )
        if total_employed_after_services:
            average_wage = (
                average_wage * firm_employment + f.public_wages
            ) / total_employed_after_services
        f.average_wage = average_wage
        f.unemployment = (
            sum(person.employer_id is None for person in self.people) / len(self.people)
            if self.people
            else 0.0
        )
        # entreprises sous contrôle public : pas de rémunération en actions nouvelles
        elect_executives(self.companies, self.people, live, self.random,
                         no_dilution=self._public_controlled_ids())
        assign_ranks(self.companies, self.people, live)
        # taux votés, aucun ajustement fiscal automatique (economy/state_budget.py)
        self._collect_institutional_taxes(f)
        mortgage_debt_before_service = {
            person.id: person.mortgage_debt for person in self.people
        }
        report: dict[str, float] = {}
        service_mortgages(self.people, report)
        # le remboursement détruit un dépôt ; seuls les intérêts nourrissent les banques
        self._receive_bank_income(report.get("interest", 0.0))
        self._settle_mortgage_accounts(mortgage_debt_before_service)
        # saisies : logement vendu avec décote, la banque passe le reste en pertes
        foreclosed = foreclose_homes(self.people, self.companies, live)
        for person_id, (debt, repaid) in foreclosed.items():
            self._write_off_household_loan(f"mortgage:{person_id}", debt - repaid)
        f.foreclosures = len(foreclosed)
        # crédit à la consommation : mensualités, défauts, puis nouveaux prêts
        paid, accrued, defaults = service_consumer_credit(self.people, live)
        self._receive_bank_income(paid)
        # les encours en défaut restent inscrits le temps d'être passés en pertes
        current = {
            p.id: defaults.get(p.id, p.consumer_debt) for p in self.people if p.id in accrued
        }
        self._settle_household_accounts("consumer", accrued, current)
        for person_id, debt in defaults.items():
            self._write_off_household_loan(f"consumer:{person_id}", debt)
        f.consumer_defaults = len(defaults)
        borrowers = sum(p.consumer_debt > 0.0 for p in self.people) + len(defaults)
        self._household_default_rate += live.default_rate_smoothing * (
            12.0 * len(defaults) / max(1, borrowers) - self._household_default_rate
        )
        extend_consumer_credit(
            self.people,
            self.credit_capacity(),
            live,
            self.policy_rate + live.consumer_credit_spread,
            credit_transaction=self._consumer_credit_transaction,
            credit_capacity=self.credit_capacity,
        )
        f.worker_skills = {
            person.id: (person.skill, person.technology_level) for person in self.people
        }

    # ------------------------------------------------------------------
    # Phase 3 : marchés de consommation (alimentation, biens courants)
    # ------------------------------------------------------------------
    def _expected_price_growth(self) -> float:
        """Hausse mensuelle des prix anticipée par les entreprises : cible de la banque centrale
        à proportion de sa crédibilité apprise, le reste suivant l'inflation passée, sans borne.
        La même croyance sert à fixer les prix et à juger les investissements.
        """
        live = self.live
        credibility = getattr(self, "central_bank_credibility", 1.0)
        return credibility * live.inflation_target + (1.0 - credibility) * self.expected_inflation

    def _learn_credibility(self) -> None:
        """Crédibilité de la banque centrale : part lissée des mois où l'inflation sur 12 mois
        publiée est restée à moins de ``credibility_band`` de la cible annuelle. Attribut créé
        au premier usage (13 mois d'historique)."""
        live = self.live
        if len(self.history) < 13:
            return
        target = (1.0 + live.inflation_target) ** 12 - 1.0
        inflation = self.history[-1].consumer_price_index / max(self.history[-13].consumer_price_index, 1e-9) - 1.0
        hit = 1.0 if abs(inflation - target) < live.credibility_band else 0.0
        credibility = getattr(self, "central_bank_credibility", 1.0)
        self.central_bank_credibility = credibility + live.credibility_speed * (hit - credibility)

    def _price_control_cap(self) -> float | None:
        """Hausse mensuelle maximale des prix à la consommation, ou None sans encadrement.

        Un gouvernement social (levier « encadrement des prix ») plafonne la hausse
        mensuelle de l'alimentation et des biens quand l'inflation annuelle dépasse
        la cible de ``price_control_threshold``. Plus le levier est fort, plus la
        hausse tolérée se rapproche de la cible. Le revers est réel : à prix bloqué,
        la demande non servie (pénurie) augmente.
        """
        live = self.live
        level = live.price_control_level
        if level <= 0.0 or -self._annual_inflation_gap() <= live.price_control_threshold:
            return None
        return live.inflation_target + live.price_control_slack * (1.0 - level)

    def _consumer_markets(self, f: MonthFlows) -> None:
        live = self.live
        # R10 : revenu attendu et risque de chômage de chaque ménage, avant ses achats
        update_income_expectations(self.people, live)
        self._update_engel_factors()
        food_demand = self._food_demand()
        # chaque entreprise prévoit sa propre demande ; la demande de matières est
        # la somme des productions prévues (pas la demande agrégée des ménages)
        update_demand_forecasts(self.companies, live, self.forecast_random)
        planned = {
            sector: sum(
                planned_production(c, live)
                for c in self.companies if c.active and c.sector == sector
            )
            for sector in ("food", "goods")
        }
        self._update_raw_material_price(planned["food"], planned["goods"])
        anchor_growth = self._expected_price_growth()
        self._signal_business_prices()
        self._firm_priced_markets(f, food_demand, anchor_growth)

    def _firm_priced_markets(self, f: MonthFlows, food_demand: float, anchor_growth: float) -> None:
        """Marchés de l'alimentation et des biens quand chaque entreprise fixe son prix
        (``economy.firm_pricing``) : le prix de marché est la moyenne de leurs prix."""
        live = self.live
        price_cap = self._price_control_cap()
        f.price_controls = price_cap is not None
        produce_food(self.companies, self.resources, live, food_demand, f.worker_skills)
        self.food_price = update_firm_prices(
            self.companies, "food", live, anchor_growth, self.food_price, price_cap, self._price_ceiling(),
            regulated=self._regulated("food"),
        )
        f.sold_food, f.food_revenue, f.unmet_food_demand = sell_food(
            self.companies, self.people, self.food_price, live
        )
        goods_demand = self._goods_demand()
        produce_consumer_goods(self.companies, self.resources, live, goods_demand, f.worker_skills)
        self.goods_price = update_firm_prices(
            self.companies, "goods", live, anchor_growth, self.goods_price, price_cap, self._price_ceiling(),
            regulated=self._regulated("goods"),
        )
        f.sold_goods, f.goods_revenue, f.unmet_goods_demand = sell_consumer_goods(
            self.companies, self.people, self.goods_price, live
        )
        f.vat_revenue = (f.food_revenue + f.goods_revenue) * live.vat_rate
        self.public.cash += f.vat_revenue
        self._buy_household_services(f)

    def _signal_business_prices(self) -> None:
        """Indices de prix de l'énergie et de la technologie formés par des signaux de
        marché (mois précédent), sans marge visée ni plafond ; vitesse bornée."""
        live = self.live

        def sector_margin(sector: str) -> float:
            firms = [c for c in self.companies if c.active and c.sector == sector]
            revenue = sum(c.previous_revenue for c in firms)
            return sum(c.previous_profit for c in firms) / revenue if revenue > 0.0 else 0.0

        def concentration(sector: str) -> float:
            firms = [c for c in self.companies if c.active and c.sector == sector]
            revenue = sum(c.previous_revenue for c in firms)
            if revenue <= 0.0:
                return 1.0
            return sum((c.previous_revenue / revenue) ** 2 for c in firms)

        last = self.history[-1] if self.history else None
        if last is not None:
            wanted = last.energy_delivered + last.energy_unmet
            unmet = last.energy_unmet / wanted if wanted > 0.0 else 0.0
            idle = max(0.0, 1.0 - last.energy_delivered / last.energy_supply) if last.energy_supply > 0.0 else 0.0
            self.resources.energy_price_index = max(0.01, self.resources.energy_price_index * (
                1.0 + signal_index_change(
                    unmet, idle, sector_margin("energy"), live,
                    self._regulated_concentration("energy", concentration("energy")),
                    scarcity=True,
                )
            ))
        tech = [c for c in self.companies if c.active and c.sector == "technology"]
        sold = sum(c.previous_units_sold for c in tech)
        unmet_units = self.resources.tech_unmet_units
        unmet = unmet_units / (sold + unmet_units) if sold + unmet_units > 0.0 else 0.0
        norm = max(live.target_inventory_months, 0.1)
        stock = sum(c.tech_inventory for c in tech)
        # part du stock au-delà de la norme (1 si plus rien ne se vend)
        idle = max(0.0, stock - norm * sold) / stock if stock > 0.0 else 0.0
        self.resources.tech_price_index = max(0.01, self.resources.tech_price_index * (
            1.0 + signal_index_change(
                unmet, idle, sector_margin("technology"), live,
                self._regulated_concentration("technology", concentration("technology")),
            )
        ))
        if live.b2b_price_elasticity > 0.0:
            # la demande de services technologiques réagit au prix : l'indice peut les
            # renchérir sans rente (sinon ils restent à prix neutre dans ce mode)
            self.resources.tech_service_price_index = self.resources.tech_price_index

    def _update_engel_factors(self) -> None:
        """Loi d'Engel : budgets d'alimentation et de services selon le revenu réel.

        Revenu réel du ménage rapporté au salaire de départ (``y``) : le budget
        alimentaire est multiplié par y^(e_alim - 1) (élasticité < 1, sa part
        baisse quand le revenu monte), celui des services par y^(e_serv - 1).
        """
        live = self.live
        deflator = max(self.price_index / 100.0, 0.01)
        reference = max(live.starting_wage, 1.0)
        food_exponent = live.engel_food_elasticity - 1.0
        services_exponent = live.engel_services_elasticity - 1.0
        for person in self.people:
            real_income = max(0.2, min(20.0, spending_income(person, live) / deflator / reference))
            person.engel_food = real_income ** food_exponent
            person.engel_services = real_income ** services_exponent

    def _buy_household_services(self, f: MonthFlows) -> None:
        """Consommation de services (loisirs, santé, restauration, réparation).

        Budget = revenu × ``services_budget_share`` + trésorerie ×
        ``services_cash_draw_rate``, modulé par la loi d'Engel ; payé toutes taxes
        comprises aux entreprises de services, la TVA revenant à l'État.
        """
        live = self.live
        tax_factor = 1.0 + live.vat_rate
        spent = vat = 0.0
        suppliers = household_suppliers(self.companies, ("services",))
        for person in self.people:
            budget = min(
                max(0.0, person.cash),
                household_budget(person, live.services_budget_share, live.services_cash_draw_rate, live)
                * person.engel_services
                * person.consumption_propensity,
            )
            if budget <= 1.0:
                continue
            net = budget / tax_factor
            paid = pay_suppliers_from_household(
                person, net, {"services": 1.0}, self.companies, self.resources, suppliers
            )
            tax = min(paid * live.vat_rate, max(0.0, person.cash))
            person.cash -= tax
            person.expenses_this_turn += tax
            person.cumulative_expenses += tax
            spent += paid
            vat += tax
        self.public.cash += vat
        f.vat_revenue += vat
        f.household_services = spent

    # ------------------------------------------------------------------
    # Phase 4 : secteur public
    # ------------------------------------------------------------------
    def _public_sector(self, f: MonthFlows) -> None:
        # loi de finances, exécution, financement, arriérés (economy/state_budget.py)
        self._institutional_budget(f)
        collect_recyclable_materials(self.companies, self.resources, self.live)

    # ------------------------------------------------------------------
    # Phase 5 : technologie, énergie, logement, loyers, prix à la consommation
    # ------------------------------------------------------------------
    def _investment_and_housing(self, f: MonthFlows) -> None:
        live = self.live
        trade_technology(self.companies, self.resources, live, self.people)
        trade_infrastructure(self.companies, self.resources, live)
        invest_in_energy_plants(
            self.companies, self.resources, live,
            bank_reserves=self.credit_capacity(),
            policy_rate=self.policy_rate,
            expected_inflation=self._expected_price_growth(),
            credit_transaction=self._credit_transaction,
            credit_capacity=self.credit_capacity,
            rng=self.forecast_random,
        )
        f.housing_units_built = build_housing(
            self.companies,
            self.resources,
            self.people,
            self.month,
            live,
            bank_reserves=self.credit_capacity(),
            policy_rate=self.policy_rate,
            expected_inflation=self._expected_price_growth(),
            monthly_rent=self.rent,
            credit_transaction=self._credit_transaction,
            credit_capacity=self.credit_capacity,
        )
        investment_yield, required_return, household_offers = self._household_housing_market()
        _, mortgage_loans = buy_homes(
            self.companies,
            self.people,
            live.housing_price,
            live.housing_build_cost,
            live,
            self.credit_capacity(),
            self.random,
            self.policy_rate + live.mortgage_spread,
            mortgage_transaction=self._mortgage_transaction,
            credit_capacity=self.credit_capacity,
            investment_yield=investment_yield,
            required_return=required_return,
            household_offers=household_offers,
        )
        f.rental_revenue = allocate_housing_and_collect_rent(
            self.companies, self.people, self.rent, live.household_rent_income_share,
        )
        maintain_housing(self.companies, self.people, self.resources, live)
        settle_operating_inputs(self.companies, self.resources, live)
        self._adjust_rent()
        previous_price_index = self.price_index
        self.price_index = self._consumer_price_index()
        f.inflation_rate = self.price_index / max(previous_price_index, 1.0) - 1.0

    # ------------------------------------------------------------------
    # Phase 6 : clôture des comptes (dette, salaires, impôts, dividendes, Bourse)
    # ------------------------------------------------------------------
    def _close_books(self, f: MonthFlows) -> None:
        live = self.live
        company_debt_before_service = {company.id: company.debt for company in self.companies}
        service_company_debt(self.companies, live, self.policy_rate)
        self._receive_bank_income(
            sum(c.interest_paid_this_turn for c in self.companies if c.active)
        )
        self._settle_company_loan_accounts(company_debt_before_service)
        self._pay_deposit_interest()
        central_bank_profit = self._settle_central_bank_interest()
        # profit de la banque centrale reversé, recette classée du budget
        self.institutions.book.add("profit_banque_centrale", central_bank_profit)
        self.public.revenue += central_bank_profit
        self._refresh_profits()
        adjust_profit_sharing_wages(
            self.companies,
            self.people,
            self.wage_reference,
            live.executive_bonus_rate,
            live.loss_wage_adjustment_strength,
            live.wage_vacancy_speed,
            self._monthly_capital_costs(),
            live,
            long_term_unemployment=self.long_term_unemployment_share(),
        )
        f.research_investment = invest_in_innovation(
            self.companies,
            self.random,
            self.resources,
            live.research_profit_share,
            live.research_outsourced_share,
        )
        f.research_spending = research_effort(self.companies, f.research_investment, live)
        update_knowledge(
            self.resources, live, f.research_spending, self._annual_gdp() / 12.0
        )
        self._tax_company_profits(f)
        self._refresh_profits()
        plans = self._plan_treasury(f)
        cash_before_dividends = self.public.cash
        f.dividends_paid = distribute_dividends(
            self.companies,
            self.people,
            live.dividend_payout_rate,
            live.working_capital_months,
            max(0.0, min(0.05, live.bank_excess_payout_rate)),
            live.bank_reserve_ratio,
            (self.public.account,),
            retained=self._undercapitalized_banks(),
            planned_payouts={cid: p.distribution for cid, p in plans.items()},
        )
        self.public.equity_dividends = self.public.cash - cash_before_dividends
        self.public.total_equity_dividends += self.public.equity_dividends
        self.public.revenue += self.public.equity_dividends
        for company in self.companies:
            if company.active:
                company.profit = company.revenue - company.expenses
                if company.sector == "bank":
                    company.bank_equity += company.profit - company.dividend_paid
        self._execute_treasury(plans)
        # R3 : actions bancaires nouvelles proposées avant la séance de Bourse
        self._authorize_bank_rights_issues()
        # cessions décidées, vendues à de vrais acheteurs pendant la séance
        sales = self._privatization_orders()
        shares_before = dict(self.public.account.shares)
        cash_before_sales = self.public.cash
        f.secondary_volume = update_prices_and_trade(
            self.companies,
            self.people,
            f.event_sector,
            live,
            policy_rate=self.policy_rate,
            deposit_rate=self.deposit_rate,
            bond_rate=self.policy_rate + live.bond_spread,
            corporate_orders={cid: p.equity_order for cid, p in plans.items()},
            extra_sells=sales,
            no_issue=self._public_controlled_ids(),
            founders={c.id: c.founder_id for c in self.companies
                      if c.active and c.founder_id is not None},
            primary_first=frozenset(
                c.id for c in self.companies
                if c.active and c.founder_id is not None and c.age_months <= live.ipo_window_months
            ),
        )
        self._settle_privatizations(shares_before, cash_before_sales)
        self._sync_public_debt()

    def _restart_values(self) -> dict[str, float]:
        """Valeur mensuelle attendue d'un poste pour chaque entreprise sans salarié (idée 2).

        Ce qu'un poste rapporte dans son secteur le mois écoulé : valeur ajoutée par salarié des
        employeurs du secteur (bénéfice + masse salariale, rapportés à leurs effectifs) ; sans
        aucun employeur, valeur infinie : le secteur refuse toute sa demande (mesuré : sans cette
        règle, une économie perdait ses deux derniers salariés de l'énergie et ne produisait plus,
        faute d'énergie). Alimentation et
        biens : au moins la demande qui lui est adressée et qu'un salarié produirait, au prix
        affiché net des intrants. Lu avant le marché du travail (comptes du mois écoulé)."""
        from economy.business import output_per_worker

        live = self.live
        sector_value: dict[str, float] = {}
        sector_staff: dict[str, int] = {}
        for company in self.companies:
            if company.active and company.employees:
                sector_value[company.sector] = (
                    sector_value.get(company.sector, 0.0) + company.previous_profit + company.wage_bill
                )
                sector_staff[company.sector] = sector_staff.get(company.sector, 0) + len(company.employees)
        values: dict[str, float] = {}
        for company in self.companies:
            if not company.active:
                continue
            if company.employees or company.age_months <= 1:
                continue
            staff = sector_staff.get(company.sector, 0)
            if staff:
                value = max(0.0, sector_value[company.sector]) / staff
            else:
                # aucun employeur dans le secteur : toute sa demande est refusée (énergie, intrants,
                # services) ; la reprise est tentée, dans la limite de ce que la trésorerie paie
                value = math.inf
            if company.sector in ("food", "goods"):
                own = min(company.demand_ema, output_per_worker(company, live)) * max(
                    0.0, company.price - company.unit_input_cost
                )
                value = max(value, own)
            values[company.id] = value
        # gardé pour la liquidation amiable de la phase 7 (même mois)
        self._restart_value_by_id = values
        return values

    def _dissolve_shells(self) -> int:
        """Liquidation amiable (idée 2) : une entreprise restée ``shell_dissolution_months`` mois
        sans salarié, dont un poste ne rapporterait pas son coût, sans dette ni actif physique, est
        fermée par ses actionnaires ; sa trésorerie leur est versée (``finance.dissolve_company``).
        Jamais une banque, une participation publique, ni la dernière entreprise de son secteur."""
        from economy.finance import can_dissolve, dissolve_company

        live = self.live
        for company in self.companies:
            if company.active:
                company.idle_months = 0 if company.employees else company.idle_months + 1
        months = live.shell_dissolution_months
        if months <= 0:
            return 0
        inst = getattr(self, "institutions", None)
        protected = set(inst.holdings) if inst is not None else set()
        values = getattr(self, "_restart_value_by_id", {})
        dissolved = 0
        for company in self.companies:
            if (
                not company.active
                or company.idle_months < months
                or company.id in protected
                or values.get(company.id, 0.0) >= max(company.wage_offer, live.minimum_wage, 1.0)
                or not can_dissolve(company)
            ):
                continue
            if not any(o.active and o.id != company.id and o.sector == company.sector for o in self.companies):
                continue
            dissolve_company(company, self.people, self.companies, (self.public.account,),
                             self.month, self.resolution_events)
            dissolved += 1
        return dissolved

    def long_term_unemployment_share(self) -> float:
        """Part des ménages sans emploi depuis au moins ``long_term_unemployment_months`` mois
        (statistique publique du mois, lue par les offres salariales)."""
        if not self.people:
            return 0.0
        months = max(1, self.live.long_term_unemployment_months)
        return sum(p.unemployment_months >= months for p in self.people) / len(self.people)

    def _cost_of_equity(self) -> float:
        """Coût annuel des fonds propres : taux directeur annuel + prime de risque des actions."""
        return 12.0 * max(0.0, self.policy_rate) + self.live.equity_risk_premium

    def _monthly_capital_costs(self) -> dict[str, float]:
        """Rémunération mensuelle des fonds propres de chaque entreprise au coût du capital."""
        monthly_cost = self._cost_of_equity() / 12.0
        live = self.live
        costs = {}
        prices = share_prices(self.companies)
        for company in self.companies:
            if not company.active:
                continue
            equity = max(0.0, company_balance_sheet(company, self.companies, live, prices)["Capitaux propres"])
            if company.sector == "bank":
                # n° 32 : seuls les fonds propres qu'exige son activité, pas les bénéfices
                # accumulés au-delà
                exposure = positive_loan_book(company.bank_loan_accounts) + company.bond_holdings
                equity = min(equity, (live.capital_requirement + live.bank_recap_buffer) * exposure)
            costs[company.id] = monthly_cost * equity
        return costs

    def _plan_treasury(self, f: MonthFlows) -> dict:
        """Plans d'emploi de la trésorerie des entreprises (``economy.treasury``)."""
        live = self.live
        unmet = {"food": f.unmet_food_demand, "goods": f.unmet_goods_demand}
        expected_inflation = self._expected_price_growth()
        reserves = {
            c.id: planned_capacity_investment(
                c, unmet.get(c.sector, 0.0), live, self.resources, self.policy_rate, expected_inflation
            )
            for c in self.companies
            if c.active and c.sector in unmet
        }
        return plan_corporate_treasury(
            self.companies,
            live,
            self.policy_rate,
            self.deposit_rate,
            self.policy_rate + live.bond_spread,
            min(1.0, max(0.0, live.corporate_tax_rate)),
            reserves,
        )

    def _execute_treasury(self, plans: dict) -> None:
        """Remboursements anticipés et obligations ; les actions passent par le carnet.

        Les montants sont bornés par la trésorerie libre restant après les
        dividendes (fonds de roulement et réserve d'investissement préservés).
        """
        live = self.live
        by_id = {c.id: c for c in self.companies}
        bond_orders = []
        for company_id, plan in plans.items():
            company = by_id[company_id]
            free = company.cash - working_capital(company, live) - plan.reserve
            repaid = self._prepay_company_loan(company, min(plan.repayment, max(0.0, free)))
            free -= repaid
            if plan.bond_order > 0.0:
                order = min(plan.bond_order, max(0.0, free))
            else:
                order = max(plan.bond_order, -company.bond_holdings)
            if abs(order) > 1.0:
                bond_orders.append((company, order))
        banks = [c for c in self.companies if c.active and c.sector == "bank"]
        if bond_orders and banks:
            trade_bonds_with_banks(bond_orders, banks)

    def _refresh_profits(self) -> None:
        for company in self.companies:
            if company.active:
                company.profit = company.revenue - company.expenses

    # ------------------------------------------------------------------
    # Phase 7 : dynamique industrielle (fusions, faillites, entrées, capacité)
    # ------------------------------------------------------------------
    def _industry_dynamics(self, f: MonthFlows) -> None:
        live = self.live
        state = (self.public.account,)
        # recapitalisations décidées avant les faillites ; l'État n'apporte pas ses titres aux
        # offres publiques, les entreprises qu'il contrôle n'en lancent ni n'en reçoivent
        self._rescue_public_companies()
        cash_before = self.public.cash  # boni de liquidation reçus par l'État
        f.acquisitions, f.takeover_bids = takeover_by_value(
            self.companies, self.people, live, self.month, state, self.policy_rate,
            locked=state, excluded=self._public_controlled_ids(),
        )
        _, f.bankruptcies = resolve_distressed_companies(
            self.companies, self.people, 0.0, live, self.random, state,
            month=self.month, events=self.resolution_events,
            wind_down_bank=self._wind_down_bank,
        )
        self._write_off_defaulted_bank_loans()
        self._sync_public_debt()  # titres émis pour recapitaliser une banque
        # entreprises sans salarié ni perspective : liquidation amiable (boni compris ci-dessous)
        f.dissolutions = self._dissolve_shells()
        self.institutions.book.add("cessions_offres_et_boni", self.public.cash - cash_before)
        # diagnostic des marchés, dossiers de monopole, entreprises publiques, revue annuelle
        self._industrial_policy()
        self.total_defaults += f.bankruptcies
        firms = sum(1 for c in self.companies if c.active and c.sector != "bank") + f.bankruptcies
        self._firm_default_rate += live.default_rate_smoothing * (
            12.0 * f.bankruptcies / max(1, firms) - self._firm_default_rate
        )
        new_companies, _, f.startups = create_household_startups(
            self.companies,
            self.people,
            self.month,
            self.credit_capacity(),
            live,
            self.random,
            f.housing_shortage,
            self.wage_reference,
            self.policy_rate,
            credit_transaction=self._credit_transaction,
            credit_capacity=self.credit_capacity,
            # commandes refusées par l'alimentation et les biens, visibles des entrepreneurs (R4)
            unserved={"food": (f.unmet_food_demand, self.food_price),
                      "goods": (f.unmet_goods_demand, self.goods_price)},
            input_cost_index=self.resources.input_cost_index,
            # premier prix d'un entrant de l'alimentation ou des biens : le prix courant du marché
            market_prices={"food": self.food_price, "goods": self.goods_price},
            # un secteur sans employeur qui refuse des commandes attire l'entrée comme un secteur vide
            refused_orders={**self.resources.refused_orders,
                            "food": f.unmet_food_demand, "goods": f.unmet_goods_demand},
        )
        self.companies.extend(new_companies)
        # ventes de capacité après la clôture (défaut de comptes connu, voir ``_roll_over_firms``)
        f.production_capacity_investment = self._invest_in_capacity(f)
        # vieillissement, décès et successions (idée 3, economy/demography.py)
        self._demography(f)
        self._grow_population()

    def _invest_in_capacity(self, f: MonthFlows) -> float:
        """Achats de capacité de production, payés aux centrales (infrastructure « production »)."""
        return invest_in_production_capacity(
            self.companies,
            {"food": f.unmet_food_demand, "goods": f.unmet_goods_demand},
            self.live,
            self.resources,
            bank_reserves=self.credit_capacity(),
            policy_rate=self.policy_rate,
            expected_inflation=self._expected_price_growth(),
            credit_transaction=self._credit_transaction,
            credit_capacity=self.credit_capacity,
        )

    # ------------------------------------------------------------------
    # Phase 8 : enregistrement du mois
    # ------------------------------------------------------------------
    def _record_month(self, f: MonthFlows) -> MarketRecord:
        from economy.records import validate_financial_accounts
        validate_financial_accounts(self)
        live = self.live
        # anticipations de coûts : croissance observée par chaque entreprise
        input_index = self.resources.input_cost_index
        previous_input = getattr(self, "_previous_input_index", input_index)
        previous_rent = getattr(self, "_previous_rent", self.rent)
        update_cost_expectations(
            self.companies,
            live,
            input_index / previous_input - 1.0 if previous_input > 0.0 else 0.0,
            self.rent / previous_rent - 1.0 if previous_rent > 0.0 else 0.0,
        )
        self._previous_input_index = input_index
        self._previous_rent = self.rent
        self.total_tax_revenue += f.household_taxes + f.corporate_taxes + f.vat_revenue + f.estate_taxes
        self.total_benefits_paid += f.benefits_paid
        self._update_firm_signals(f.unmet_food_demand, f.unmet_goods_demand)
        self._unmet_ratio = {
            "food": f.unmet_food_demand / max(1.0, f.sold_food + f.unmet_food_demand),
            "goods": f.unmet_goods_demand / max(1.0, f.sold_goods + f.unmet_goods_demand),
        }
        # Investissement brut du mois (flux réel, enregistré tel quel dans le PIB) :
        # dépenses d'équipement de toutes les entreprises ayant opéré ce mois
        # (capacité, technologie, infrastructures, centrales) et R&D ; les travaux
        # de construction effectivement livrés sont comptés à part. La moyenne
        # mobile sur 12 mois n'est qu'une série d'affichage.
        operated = [c for c in self.companies if c.active or c.exit_month == self.month]
        construction_work = sum(c.construction_spending for c in operated)
        business_investment = (
            max(0.0, sum(c.capital_expenditure for c in operated) - construction_work)
            + f.research_investment
        )
        self._investment_history.append(business_investment)
        smoothed_investment = sum(self._investment_history[-12:]) / len(
            self._investment_history[-12:]
        )
        record = build_record(
            self, f, smoothed_investment, business_investment, construction_work, operated
        )
        enrich_wealth_record(record, self.people, self.companies, live, self.price_index)
        # mesures des cinq idées de novembre 2026 (aucune décision ne les lit)
        record.long_term_unemployment = self.long_term_unemployment_share()
        record.idle_companies = sum(
            1 for c in self.companies if c.active and not c.employees and c.sector != "bank"
        )
        record.dissolutions = f.dissolutions
        record.deaths = f.deaths
        record.estate_taxes = f.estate_taxes
        if self.people:
            record.average_age = sum(p.age_months for p in self.people) / len(self.people) / 12.0
        record.education_quality = getattr(self, "education_quality", 0.0)
        self.history.append(record)
        self._adjust_policy_rate(f.inflation_rate, f.unemployment)
        self.expected_inflation += live.inflation_expectation_speed * (
            f.inflation_rate - self.expected_inflation
        )
        self._learn_credibility()
        self._record_institutions(f)
        return record

    def _apply_event_effects(self) -> None:
        """Les événements sectoriels déplacent réellement le secteur concerné."""
        size = self.live.event_effect_size
        event = self.last_event
        if event == "Innovation technologique":
            self.resources.knowledge *= 1.0 + size
        elif event == "Variation du prix de l'énergie":
            self.resources.energy_price_index *= 1.0 + size * self.event_random.choice((-1, 1))
        elif event == "Variation de la demande en matières premières":
            self.resources.raw_material_price *= 1.0 + size * self.event_random.choice((-1, 1))
        elif event == "Demande immobilière soutenue":
            self.rent *= 1.0 + size


    def _grow_population(self) -> None:
        population_cap = int(
            self.initial_population_size
            * (1.0 + self.live.maximum_population_growth)
        )
        if (
            self.live.monthly_population_growth_rate <= 0.0
            or len(self.people) >= population_cap
        ):
            return
        self.population_growth_progress += (
            len(self.people) * self.live.monthly_population_growth_rate
        )
        while (
            self.population_growth_progress >= 1.0
            and len(self.people) < population_cap
        ):
            person_number = len(self.people) + 1
            newcomer = Person(
                f"p{person_number - 1:04d}",
                f"Nouveau ménage {person_number}",
                0.0,
                skill=self.random.uniform(0.55, 1.05),
                risk_tolerance=self.random.random(),
                consumption_propensity=self.random.uniform(
                    1.0 - self.live.household_propensity_variance,
                    1.0 + self.live.household_propensity_variance,
                ),
                quality_preference=self.random.uniform(0.65, 1.35),
                technology_level=0.0,
            )
            newcomer.skills = initial_skills(
                newcomer.skill, self.labor_random, SKILL_SECTORS
            )
            newcomer.entrepreneurial_spirit = self.trait_random.random()
            newcomer.trade = assign_trade(self.trait_random, self.live)
            newcomer.job_trade = newcomer.trade
            # jeune adulte, scolarisé par l'éducation publique (idées 3 et 5 ; aucun tirage déplacé)
            newcomer.age_months = self._newcomer_age()
            newcomer.skill += self._education_skill_bonus()
            self.people.append(newcomer)
            self.population_growth_progress -= 1.0
            self.population_job_progress += self.live.target_employment_rate
            self.new_households_this_turn += 1

            while self.population_job_progress >= 1.0:
                production_firms = [
                    company
                    for company in self.companies
                    if company.active and company.sector in {"food", "goods"}
                ]
                if production_firms:
                    best_candidate = max(
                        production_firms,
                        key=lambda company: (
                            company.previous_profit
                            / max(1, company.target_employees),
                            -len(company.employees),
                            company.id,
                        ),
                    )
                    best_candidate.target_employees += 1
                self.population_job_progress -= 1.0


    def _generate_event(self) -> tuple[str, str | None]:
        # générateur propre aux chocs exogènes : les décisions des agents (fusions,
        # créations, faillites) ne déplacent pas la suite des événements
        if self.event_random.random() >= 0.12:
            return "Aucun événement", None
        sector, _ = self.event_random.choice(
            tuple(item for item in SECTORS if item[0] not in {"bank", "services", "public"})
        )
        descriptions = {
            "food": "Pic de demande alimentaire",
            "technology": "Innovation technologique",
            "goods": "Lancement de nouveaux produits",
            "real_estate": "Demande immobilière soutenue",
            "materials": "Variation de la demande en matières premières",
            "energy": "Variation du prix de l'énergie",
        }
        if sector == "food" and self.event_random.random() < 0.5:
            return "Choc de matières premières", sector
        return descriptions[sector], sector


