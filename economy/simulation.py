from __future__ import annotations

import random

from economy.business import (
    adjust_profit_sharing_wages,
    apply_competitive_pricing,
    elect_executives,
    invest_in_innovation,
    invest_in_production_capacity,
)
from economy.consolidation import consolidate_market
from economy.energy import (
    decay_infrastructure,
    invest_in_energy_plants,
    trade_infrastructure,
)
from economy.intersector import settle_operating_inputs
from economy.finance import (
    create_household_startups,
    request_operating_credit,
    resolve_distressed_companies,
    service_company_debt,
)
from economy.goods import (
    charge_inventory_holding_cost,
    collect_recyclable_materials,
    produce_consumer_goods,
    produce_food,
    sell_consumer_goods,
    sell_food,
    trade_technology,
    update_food_price,
)
from economy.government import (
    buy_public_goods,
    collect_income_taxes_and_pay_benefits,
    pay_public_transfers,
    tax_company_profits,
)
from economy.labor import (
    assign_ranks,
    initial_skills,
    allocate_and_pay_labor,
    effective_worker_count,
    hire_service_workers,
    update_workforce_targets,
    workforce_targets,
)
from economy.models import (
    Company,
    MarketRecord,
    Person,
    Resources,
    SimulationConfig,
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
from economy.stock_market import distribute_dividends, update_prices_and_trade
from economy.wealth import enrich_wealth_record, wealth_leaderboards


SKILL_SECTORS = ("food", "goods", "technology", "real_estate", "materials", "bank", "energy")

SECTORS = (
    ("food", "Alimentation"),
    ("goods", "Biens courants"),
    ("technology", "Technologie"),
    ("real_estate", "Immobilier"),
    ("materials", "Matières premières"),
    ("energy", "Énergie"),
    ("bank", "Banque"),
    ("services", "Services aux entreprises"),
)


class EconomySimulation:
    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.random = random.Random(self.config.seed)
        self.labor_random = random.Random(self.config.seed + 7919)
        self.trait_random = random.Random(self.config.seed + 104729)
        self.trades = TradeMarket(self.config, random.Random(self.config.seed + 15485863))
        self.month = 0
        self.food_price = self.config.starting_food_price
        self.goods_price = self.config.starting_goods_price
        self.rent = self.config.starting_rent
        self.price_index = 100.0
        self.wage_reference = self.config.starting_wage
        self.policy_rate = self.config.loan_interest_rate
        self.people = [
            Person(
                f"p{i:04d}",
                f"Ménage {i + 1}",
                self.config.initial_person_cash,
                skill=self.random.uniform(0.65, 1.35),
                risk_tolerance=self.random.random(),
                consumption_propensity=self.random.uniform(
                    1.0 - self.config.household_propensity_variance,
                    1.0 + self.config.household_propensity_variance,
                ),
                quality_preference=self.random.uniform(0.65, 1.35),
            )
            for i in range(self.config.population_size)
        ]
        for person in self.people:
            person.skills = initial_skills(person.skill, self.labor_random, SKILL_SECTORS)
            person.entrepreneurial_spirit = self.trait_random.random()
            person.trade = assign_trade(self.trait_random, self.config)
            person.job_trade = person.trade
        self.initial_population_size = len(self.people)
        self.population_growth_progress = 0.0
        self.population_job_progress = 0.0
        self.new_households_this_turn = 0
        self.companies = self._create_companies()
        initial_housing = min(
            self.config.land_units,
            max(1, int(len(self.people) * 0.9)),
        )
        property_firms = [company for company in self.companies if company.sector == "real_estate"]
        for index in range(initial_housing):
            property_firms[index % len(property_firms)].property_inventory += 1
        for company in self.companies:
            initial_assets = (
                company.cash + company.property_inventory * self.config.housing_price
            )
            company.stock_price = max(
                1.0,
                min(
                    5000.0,
                    initial_assets / max(company.shares_outstanding, 1.0),
                ),
            )
            company.previous_stock_price = company.stock_price
        self.resources = Resources(
            raw_materials=max(0.0, self.config.raw_materials),
            land_units=max(0, self.config.land_units - initial_housing),
            knowledge=max(100.0, self.config.raw_materials * 0.25),
            raw_material_price=self.config.raw_material_unit_price,
        )
        self.history: list[MarketRecord] = []
        self.last_event = "Aucun événement"
        self.government_balance = 0.0
        self.total_tax_revenue = 0.0
        self.total_benefits_paid = 0.0
        self.bank_reserves = max(0.0, self.config.bank_initial_reserves)
        self.total_defaults = 0
        self._investment_history: list[float] = []
        self._smoothed_productivity = 0.0
        self.productivity_index = 1.0
        self.wage_terms: dict[str, float] = {}
        self.base_money_issued = 0.0
        self._sector_wage_base = dict(self.config.sector_wage_factor)
        self.sector_wage_index = dict(self.config.sector_wage_factor)
        self._unmet_ratio = {"food": 0.0, "goods": 0.0}
        self._raw_shortfall = 0.0
        self._base_build_cost = self.config.housing_build_cost
        self._housing_markup = self.config.housing_price / max(
            self.config.housing_build_cost, 1.0
        )
        self._land_progress = 0.0
        self._raw_util_ema = self.config.raw_material_floor_utilization
        self._materials_price_adjust = 1.0
        self._rent_margin_adjust = 1.0
        self._input_index_ema = 1.0
        self._seed_property_financing()
        self._seed_household_ownership()

    def _seed_household_ownership(self) -> None:
        """Les ménages détiennent à parts égales l'essentiel du capital initial.

        Sans actionnaires, les dividendes (bénéfices, trésorerie excédentaire,
        capital bancaire en excès) ne retourneraient à personne : l'argent
        resterait dans les entreprises. Une fraction
        ``initial_household_ownership`` des actions de chaque entreprise est donc
        répartie également entre les ménages ; le reste est en trésorerie
        (émission future). Aucun mouvement de trésorerie.
        """
        fraction = self.config.initial_household_ownership
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
        leverage = self.config.initial_property_leverage
        if leverage <= 0.0:
            return
        rate = self.policy_rate + self.config.credit_risk_spread * 0.5
        for company in self.companies:
            if company.sector != "real_estate" or company.property_inventory <= 0:
                continue
            debt = company.property_inventory * self.config.housing_price * leverage
            company.debt += debt
            company.debt_interest_rate = rate
            self._book_bank_loan(company.id, debt)

    def _annual_inflation_gap(self) -> float:
        """Cible d'inflation annuelle moins inflation observée (12 mois glissants)."""
        config = self.config
        target = (1.0 + config.inflation_target) ** 12 - 1.0
        if len(self.history) < 6:
            return 0.0
        window = self.history[-12:]
        elapsed = len(window)
        base = (
            self.history[-elapsed - 1].consumer_price_index
            if len(self.history) > elapsed
            else 100.0
        )
        inflation = (
            window[-1].consumer_price_index / max(base, 1.0)
        ) ** (12.0 / elapsed) - 1.0
        return target - inflation

    def _credit_share(self) -> float:
        """Part des investissements financée par crédit (base + réaction à l'inflation)."""
        config = self.config
        return max(
            0.05,
            min(
                0.90,
                config.credit_financed_share
                + config.credit_inflation_gain * self._annual_inflation_gap(),
            ),
        )

    def _central_bank_operations(self) -> float:
        """Banque centrale : émet ou retire de la monnaie de base (réserves bancaires).

        Règle de croissance monétaire : tendance + réaction à l'écart
        d'inflation (cible - observée, 12 mois) et au chômage au-dessus du
        niveau naturel. Sans création monétaire, l'offre de monnaie est fixe
        alors que la production réelle croît : les prix déflatent, les marges
        s'effondrent et l'économie s'arrête (crash tardif observé). La monnaie
        émise est suivie dans ``base_money_issued`` (conservation testée).
        """
        config = self.config
        if len(self.history) < 6:
            return 0.0
        unemployment = self.history[-1].unemployment
        rate = (
            config.monetary_trend
            + config.monetary_inflation_gain * self._annual_inflation_gap()
            + config.monetary_unemployment_gain
            * (unemployment - config.natural_unemployment_rate)
        )
        rate = max(config.monetary_min_rate, min(config.monetary_max_rate, rate))
        active = sum(p.cash for p in self.people) + sum(
            c.cash for c in self.companies if c.active and c.sector != "bank"
        )
        amount = rate * active
        banks = [c for c in self.companies if c.active and c.sector == "bank"]
        if not banks or amount == 0.0:
            return 0.0
        if amount < 0.0:
            amount = -min(-amount, 0.5 * sum(max(0.0, b.cash) for b in banks))
        for bank in banks:
            bank.cash += amount / len(banks)
        self.base_money_issued += amount
        return amount

    def _bank_payout_rate(self) -> float:
        """Taux mensuel de restitution du capital bancaire excédentaire.

        Règle de politique monétaire : sous la cible d'inflation, les banques
        distribuent plus vite leurs réserves excédentaires à leurs actionnaires
        (la monnaie dormante retourne aux ménages et à la dépense) ; au-dessus,
        elles les conservent. Aucune création de monnaie : transfert pur.
        """
        config = self.config
        return max(
            0.0,
            min(
                0.05,
                config.bank_excess_payout_rate
                + config.bank_payout_inflation_gain * self._annual_inflation_gap(),
            ),
        )

    def _margin_price_pressure(self, sector: str) -> float:
        """Pression mensuelle sur le prix d'un secteur selon l'écart de marge.

        Marge observée le mois précédent (bénéfice / chiffre d'affaires) :
        au-dessus de ``profit_margin_target`` la concurrence baisse les prix,
        en dessous les entreprises répercutent leurs coûts. Borné à ±1 %/mois.
        """
        revenue = sum(
            c.previous_revenue
            for c in self.companies
            if c.active and c.sector == sector
        )
        if revenue <= 0.0:
            return 0.0
        profit = sum(
            c.previous_profit
            for c in self.companies
            if c.active and c.sector == sector
        )
        gap = (
            self.config.sector_margin_target.get(
                sector, self.config.profit_margin_target
            )
            - profit / revenue
        )
        # Asymétrie : les prix se répercutent vite vers le haut quand les coûts
        # rognent la marge, et baissent lentement (rigidité à la baisse). La
        # concurrence ne baisse pas les prix tant qu'il y a pénurie.
        shortage = self._unmet_ratio.get(sector, 0.0)
        sensitivity = self.config.margin_price_sensitivity * (
            1.0
            if gap > 0.0
            else self.config.price_downward_rigidity * max(0.0, 1.0 - self.config.shortage_price_gate * shortage)
        )
        return max(-0.01, min(0.01, gap * sensitivity))

    def _productivity_index(self) -> float:
        """Production potentielle par travailleur (alimentation + biens), base 1.0.

        Combine le trait de productivité des entreprises, leur niveau
        technologique, la compétence des salariés et le capital installé.
        """
        skills = {
            person.id: (person.skill, person.technology_level)
            for person in self.people
        }
        potential = 0.0
        workers = 0
        base = self.config.food_productivity
        for company in self.companies:
            if not company.active or company.sector not in ("food", "goods"):
                continue
            n = len(company.employees)
            if n == 0:
                continue
            per_worker = (
                self.config.food_productivity
                if company.sector == "food"
                else self.config.goods_productivity
            )
            potential += (
                effective_worker_count(company, skills)
                * per_worker
                * company.productivity_trait
                * productivity_multiplier(company, self.config)
                + company.production_capacity
            )
            workers += n
        if workers == 0:
            return self._smoothed_productivity or 1.0
        return potential / (workers * base)

    def _update_wage_reference(self) -> None:
        """Le salaire de référence suit productivité, tension et rentabilité.

        Variation mensuelle = part de la croissance lissée de la productivité
        + sensibilité × (chômage naturel − chômage observé)
        + sensibilité × (marge moyenne − marge cible).
        Jamais indexée sur l'IPC ; variation mensuelle bornée par
        ``max_monthly_wage_change`` (ajustement graduel).
        """
        config = self.config
        index = self._productivity_index()
        previous = self._smoothed_productivity
        if previous <= 0.0:
            self._smoothed_productivity = index
            productivity_growth = 0.0
        else:
            self._smoothed_productivity = previous + 0.10 * (index - previous)
            productivity_growth = self._smoothed_productivity / previous - 1.0
        self.productivity_index = self._smoothed_productivity
        if not self.history:
            return
        last = self.history[-1]
        tension = max(
            -0.15, min(0.10, config.natural_unemployment_rate - last.unemployment)
        )
        revenue = sum(
            company.previous_revenue
            for company in self.companies
            if company.active and company.sector != "bank"
        )
        profit = sum(
            company.previous_profit
            for company in self.companies
            if company.active and company.sector != "bank"
        )
        margin = profit / revenue if revenue > 0.0 else 0.0
        # cible de marge = moyenne des cibles sectorielles pondérée par le CA
        weighted_target = (
            sum(
                company.previous_revenue
                * config.sector_margin_target.get(
                    company.sector, config.profit_margin_target
                )
                for company in self.companies
                if company.active and company.sector != "bank"
            )
            / revenue
            if revenue > 0.0
            else config.profit_margin_target
        )
        margin_gap = max(-0.10, min(0.10, margin - weighted_target))
        # Rattrapage partiel et retardé de l'inflation passée (pas l'IPC du mois)
        lag = max(1, config.wage_inflation_lag_months)
        window = [
            record.inflation_rate
            for record in self.history[-(lag + config.wage_inflation_window_months):-lag]
        ]
        past_inflation = max(0.0, sum(window) / len(window)) if window else 0.0
        # le rapport de force joue sur la part rattrapée : marché tendu = plus
        pass_through = max(
            config.wage_inflation_pass_min,
            min(
                config.wage_inflation_pass_max,
                config.wage_inflation_pass_base + 2.0 * tension,
            ),
        )
        for sector, base in self._sector_wage_base.items():
            sector_pass = max(
                config.wage_inflation_pass_min,
                min(
                    config.wage_inflation_pass_max,
                    pass_through
                    + config.sector_inflation_pass_bonus.get(sector, 0.0),
                ),
            )
            self.sector_wage_index[sector] *= 1.0 + (sector_pass - pass_through) * past_inflation
        change = (
            config.wage_productivity_pass_through
            * max(-0.003, min(0.003, productivity_growth))
            + config.wage_tension_sensitivity * tension
            + config.wage_profitability_sensitivity * margin_gap
            + pass_through * past_inflation
        )
        limit = config.max_monthly_wage_change
        self.wage_terms = {
            "productivite": config.wage_productivity_pass_through
            * max(-0.003, min(0.003, productivity_growth)),
            "tension": config.wage_tension_sensitivity * tension,
            "rentabilite": config.wage_profitability_sensitivity * margin_gap,
            "inflation": pass_through * past_inflation,
        }
        self.wage_reference *= 1.0 + max(-limit, min(limit, change))

    def _update_firm_signals(
        self, unmet_food_demand: float, unmet_goods_demand: float
    ) -> None:
        """Mémorise demande et revenus lissés, base de l'emploi réactif."""
        alpha = self.config.demand_smoothing
        beta = self.config.revenue_smoothing
        for sector, unmet in (
            ("food", unmet_food_demand),
            ("goods", unmet_goods_demand),
            ("materials", self._raw_shortfall),
            ("technology", self.resources.tech_unmet_units),
        ):
            firms = [
                company
                for company in self.companies
                if company.active and company.sector == sector
            ]
            if not firms:
                continue
            sold_total = sum(company.units_sold for company in firms)
            for company in firms:
                sales_share = (
                    company.units_sold / sold_total if sold_total > 0.0 else 0.0
                )
                signal = company.units_sold + unmet * (
                    0.5 * sales_share + 0.5 / len(firms)
                )
                company.demand_ema = (
                    signal
                    if company.demand_ema <= 0.0
                    else company.demand_ema + alpha * (signal - company.demand_ema)
                )
        extractors = [
            c for c in self.companies if c.active and c.sector == "materials"
        ]
        capacity = sum(len(c.employees) for c in extractors) * (
            self.config.raw_material_extraction_productivity
        )
        if capacity > 0.0:
            utilization = sum(c.units_sold for c in extractors) / capacity
            self._raw_util_ema += 0.10 * (utilization - self._raw_util_ema)
        for company in self.companies:
            if not company.active:
                continue
            company.revenue_ema = (
                company.revenue
                if company.revenue_ema <= 0.0
                else company.revenue_ema + beta * (company.revenue - company.revenue_ema)
            )

    def _create_companies(self) -> list[Company]:
        companies: list[Company] = []
        targets = workforce_targets(self.config)
        for sector, label in SECTORS:
            if sector == "services":
                continue
            for index in range(self.config.companies_per_sector):
                company_id = f"{sector}_{index + 1}"
                initial_cash = (
                    self.config.bank_initial_reserves
                    / self.config.companies_per_sector
                    if sector == "bank"
                    else self.config.initial_company_cash
                )
                companies.append(
                    Company(
                        id=company_id,
                        name=f"{label} {index + 1}",
                        sector=sector,
                        cash=initial_cash,
                        wage_offer=self.config.starting_wage
                        * self.config.sector_wage_factor.get(sector, 1.0)
                        * self.random.uniform(0.92, 1.08),
                        target_employees=targets[sector],
                        price=(
                            self.config.starting_food_price
                            if sector == "food"
                            else self.config.raw_material_unit_price
                            if sector == "materials"
                            else self.config.starting_goods_price
                        ),
                        stock_price=max(1.0, self.config.initial_company_cash / 1000.0),
                        previous_stock_price=max(1.0, self.config.initial_company_cash / 1000.0),
                        productivity_trait=self.random.uniform(
                            1.0 - self.config.trait_variance,
                            1.0 + self.config.trait_variance,
                        ),
                        quality_trait=self.random.uniform(
                            1.0 - self.config.quality_trait_variance,
                            1.0 + self.config.quality_trait_variance,
                        ),
                        risk_tolerance=self.random.random(),
                        pricing_aggression=self.random.random(),
                        reinvestment_preference=max(
                            0.05,
                            min(
                                1.0,
                                self.config.investment_retention_rate
                                + self.random.uniform(-0.3, 0.3),
                            ),
                        ),
                        innovation=self.random.uniform(0.4, 1.0),
                        bank_equity=initial_cash if sector == "bank" else 0.0,
                    )
                )
        return companies

    @property
    def bank_reserves(self) -> float:
        return sum(
            bank.cash
            for bank in self.companies
            if bank.sector == "bank" and bank.active
        )

    @bank_reserves.setter
    def bank_reserves(self, balance: float) -> None:
        banks = [
            bank
            for bank in self.companies
            if bank.sector == "bank" and bank.active
        ]
        if not banks:
            return
        total = max(0.0, balance)
        current_total = sum(max(0.0, bank.cash) for bank in banks)
        for bank in banks:
            weight = (
                max(0.0, bank.cash) / current_total
                if current_total > 0.0
                else 1.0 / len(banks)
            )
            bank.cash = total * weight

    def step(self) -> MarketRecord:
        self.month += 1
        self.new_households_this_turn = 0
        self.resources.supplier_income = 0.0
        self.resources.supplier_employment = 0
        self.resources.household_technology_revenue = 0.0
        self.resources.household_technology_units = 0.0
        self._update_wage_reference()
        # intrants indexés sur le niveau des prix des producteurs (pas sur le loyer)
        producer_weights = self.config.cpi_food_weight + self.config.cpi_goods_weight
        raw_index = max(
            0.5,
            (
                self.config.cpi_food_weight * self.food_price / self.config.starting_food_price
                + self.config.cpi_goods_weight * self.goods_price / self.config.starting_goods_price
            )
            / max(producer_weights, 1e-9),
        )
        # lissage : les contrats d'intrants se renégocient lentement
        self._input_index_ema += self.config.input_index_smoothing * (raw_index - self._input_index_ema)
        self.resources.input_cost_index = self._input_index_ema
        self._update_housing_costs()
        money_issued = self._central_bank_operations()
        self.resources.recycled_materials += self.resources.recycling_pending
        self.resources.recycling_pending = 0.0
        spoiled_food = spoiled_goods = 0.0
        for company in self.companies:
            if not company.active:
                continue
            company.previous_profit = company.profit
            company.previous_revenue = company.revenue
            company.revenue = 0.0
            company.expenses = 0.0
            company.units_sold = 0.0
            company.units_produced = 0.0
            company.stock_trade_volume = 0.0
            company.dividend_paid = 0.0
            company.capital_expenditure = 0.0
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
            decay_infrastructure(company, self.config)
            company.output_multiplier = productivity_multiplier(company, self.config)
            company.age_months += 1
            if (
                company.entry_boost > 1.0
                and company.age_months > self.config.entrant_boost_months
            ):
                company.entry_boost = 1.0  # fin de l'avantage temporaire d'entrant
            spoiled_food += company.inventory * self.config.inventory_spoilage_rate
            spoiled_goods += (
                company.goods_inventory * self.config.inventory_spoilage_rate
            )
            company.inventory *= 1.0 - self.config.inventory_spoilage_rate
            company.goods_inventory *= 1.0 - self.config.inventory_spoilage_rate
            previous_technology_asset = company.technology_asset_value
            technology_depreciation = company.tech_level * self.config.technology_depreciation_rate
            company.tech_level -= technology_depreciation
            company.technology_asset_value *= (
                1.0 - self.config.technology_depreciation_rate
            )
            technology_asset_depreciation = (
                previous_technology_asset - company.technology_asset_value
                if previous_technology_asset > 0.0
                else technology_depreciation
                * self.config.tech_unit_price
                / 0.03
            )
            previous_productive_capital = company.productive_capital_value
            capital_decay = self.config.sector_capital_depreciation.get(
                company.sector, self.config.productive_capacity_depreciation_rate
            )
            company.production_capacity *= (
                1.0 - self.config.productive_capacity_depreciation_rate
            )
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
                    * self.config.housing_price
                    * self.config.real_estate_depreciation_rate
                )
            company.expenses += company.depreciation_this_turn
        # Pas de cliquet de consommation : propension inchangée
        for person in self.people:
            person.dividend_income_this_turn = 0.0
        for person in self.people:
            person.technology_level *= (
                1.0 - self.config.technology_household_depreciation_rate
            )

        charge_inventory_holding_cost(
            self.companies, self.resources, self.config
        )
        pay_construction_progress(self.companies, self.resources, self.config)
        complete_construction(self.companies, self.month, self.config)
        self.last_event, event_sector = self._generate_event()
        if self.last_event.startswith("Choc de matières premières"):
            self.resources.raw_materials *= 0.9

        tenant_demand = sum(not person.owns_home for person in self.people)
        rental_supply = sum(
            company.property_inventory
            for company in self.companies
            if company.active and company.sector == "real_estate"
        )
        construction_pipeline = sum(
            len(company.construction_pipeline)
            for company in self.companies
            if company.active and company.sector == "real_estate"
        )
        housing_shortage = max(
            0, tenant_demand - rental_supply - construction_pipeline
        )
        company_debt_before_credit = {
            company.id: company.debt for company in self.companies
        }
        banks_now = [c for c in self.companies if c.active and c.sector == "bank"]
        bank_level = (
            sum(c.tech_level for c in banks_now) / len(banks_now) if banks_now else 0.0
        )
        lent = request_operating_credit(
            self.companies,
            self.people,
            self.bank_reserves,
            self.config,
            housing_shortage,
            self.policy_rate,
            credit_multiplier=1.0 + self.config.bank_credit_gain_per_level * bank_level,
            spread_multiplier=max(0.3, 1.0 - self.config.bank_spread_gain_per_level * bank_level),
        )
        self.bank_reserves = max(0.0, self.bank_reserves - lent)
        for company in self.companies:
            extension = max(
                0.0,
                company.debt - company_debt_before_credit.get(company.id, 0.0),
            )
            if extension > 0.0:
                self._book_bank_loan(company.id, extension)
        update_workforce_targets(self.companies, self.people, self.config)
        self.trades.refresh(self.people, self.companies)
        self.trades.retrain(self.people, self.resources, self.wage_reference)
        average_wage, unemployment = allocate_and_pay_labor(
            self.companies,
            self.people,
            self.wage_reference,
            self.config,
            self.labor_random,
            self.trades.factors(),
        )
        firm_employment = sum(
            person.employer_id is not None for person in self.people
        )
        supplier_employment, supplier_payroll = hire_service_workers(
            self.people,
            self.resources,
            self.wage_reference,
            self.config.supplier_wage_payout_rate,
            self.config.upstream_wage_cap_multiplier,
        )
        total_employed_after_services = sum(
            person.employer_id is not None for person in self.people
        )
        if total_employed_after_services:
            average_wage = (
                average_wage * firm_employment + supplier_payroll
            ) / total_employed_after_services
        unemployment = (
            sum(person.employer_id is None for person in self.people)
            / len(self.people)
            if self.people
            else 0.0
        )
        elect_executives(
            self.companies, self.people, self.config, self.random
        )
        assign_ranks(self.companies, self.people, self.config)
        household_taxes, benefits_paid, self.government_balance = (
            collect_income_taxes_and_pay_benefits(
                self.people,
                self.government_balance,
                self.config.income_tax_rate,
                self.config.unemployment_benefit,
                unemployment,
                self.price_index,
                self.config.unemployment_benefit_cycle_sensitivity,
                self.config.income_tax_threshold,
                self.config.top_marginal_income_tax_rate,
            )
        )
        mortgage_debt_before_service = {
            person.id: person.mortgage_debt for person in self.people
        }
        mortgage_payments = service_mortgages(self.people)
        self.bank_reserves += mortgage_payments
        self._settle_mortgage_accounts(mortgage_debt_before_service)
        worker_skills = {
            person.id: (person.skill, person.technology_level)
            for person in self.people
        }

        food_demand = self._food_demand()
        forecast_goods_demand = self._goods_demand()
        self._update_raw_material_price(food_demand, forecast_goods_demand)
        # Dérive de base : ancrage des anticipations (cible d'inflation), plus un
        # ajustement de marge par secteur — concurrence si les marges dépassent
        # la cible, répercussion des coûts (salaires) si elles sont sous la cible.
        anchor_growth = min(0.005, self.config.inflation_target)
        # Prix des boosts technologiques : régulés par l'écart de marge du
        # secteur (sorties → moins d'offre → marges rétablies, et inversement).
        low, high = self.config.b2b_price_index_bounds
        self.resources.tech_price_index = max(
            low,
            min(
                high,
                self.resources.tech_price_index
                * (1.0 + self._margin_price_pressure("technology")),
            ),
        )
        energy_low, energy_high = self.config.energy_price_index_bounds
        self.resources.energy_price_index = max(
            energy_low,
            min(
                energy_high,
                self.resources.energy_price_index
                * (1.0 + self._margin_price_pressure("energy")),
            ),
        )
        self.resources.materials_price_index = self._materials_price_adjust
        baseline_price_growth = anchor_growth + self._margin_price_pressure("food")
        goods_baseline_growth = anchor_growth + self._margin_price_pressure("goods")
        food_demand_multiplier = (
            1.25 if self.last_event == "Pic de demande alimentaire" else 1.0
        )
        produce_food(
            self.companies,
            self.resources,
            self.config,
            food_demand,
            worker_skills,
        )
        food_supply = sum(
            company.units_produced
            for company in self.companies
            if company.active and company.sector == "food"
        )
        # La production qui ne fait que remplacer le stock périmé n'est pas une
        # offre nette : sans cette correction l'offre dépasse structurellement
        # la demande et pousse les prix à la baisse.
        self.food_price = update_food_price(
            self.food_price,
            food_demand,
            max(0.0, food_supply - spoiled_food),
            self._price_ceiling(),
            baseline_growth_rate=baseline_price_growth,
            demand_multiplier=food_demand_multiplier,
        )
        apply_competitive_pricing(
            self.companies,
            self.food_price,
            self.goods_price,
            self.config.competitive_pricing_strength,
            self.config.inventory_discount_strength,
        )
        sold_food, food_revenue, unmet_food_demand = sell_food(
            self.companies, self.people, self.food_price, self.config
        )

        goods_demand = self._goods_demand()
        produce_consumer_goods(
            self.companies,
            self.resources,
            self.config,
            goods_demand,
            worker_skills,
        )
        goods_supply = sum(
            company.units_produced
            for company in self.companies
            if company.active and company.sector == "goods"
        )
        self.goods_price = update_food_price(
            self.goods_price,
            goods_demand,
            max(0.0, goods_supply - spoiled_goods),
            self._price_ceiling(),
            baseline_growth_rate=goods_baseline_growth,
            demand_multiplier=(
                1.15 if self.last_event == "Lancement de nouveaux produits" else 1.0
            ),
        )
        apply_competitive_pricing(
            self.companies,
            self.food_price,
            self.goods_price,
            self.config.competitive_pricing_strength,
            self.config.inventory_discount_strength,
        )
        sold_goods, goods_revenue, unmet_goods_demand = sell_consumer_goods(
            self.companies, self.people, self.goods_price, self.config
        )
        # L'État dépense chaque mois ce qu'il détient : achats de biens et
        # services aux entreprises (après les allocations déjà versées).
        public_budget = (
            max(0.0, self.government_balance) * self.config.government_spending_rate
        )
        public_spent, public_food_units, public_goods_units = buy_public_goods(
            self.companies, public_budget
        )
        self.government_balance -= public_spent
        public_transfers = pay_public_transfers(
            self.people, max(0.0, public_budget - public_spent)
        )
        self.government_balance -= public_transfers
        collect_recyclable_materials(
            self.companies, self.resources, self.config
        )

        trade_technology(
            self.companies, self.resources, self.config, self.people
        )
        trade_infrastructure(self.companies, self.resources, self.config)
        invest_in_energy_plants(self.companies, self.resources, self.config)
        debt_before_construction = {c.id: c.debt for c in self.companies}
        housing_units_built = build_housing(
            self.companies,
            self.resources,
            self.people,
            self.month,
            self.config,
            housing_shortage,
            bank_reserves=self.bank_reserves,
            policy_rate=self.policy_rate,
            credit_share=self._credit_share(),
        )
        self._settle_new_credit(debt_before_construction)
        mortgage_debt_before_loans = {
            person.id: person.mortgage_debt for person in self.people
        }
        _, mortgage_loans = buy_homes(
            self.companies,
            self.people,
            self.config.housing_price,
            self.config.housing_build_cost,
            self.config,
            self.bank_reserves,
            self.random,
        )
        self.bank_reserves = max(0.0, self.bank_reserves - mortgage_loans)
        for person in self.people:
            extension = max(
                0.0,
                person.mortgage_debt
                - mortgage_debt_before_loans.get(person.id, 0.0),
            )
            if extension > 0.0:
                self._book_bank_loan(f"mortgage:{person.id}", extension)
        rental_revenue = allocate_housing_and_collect_rent(
            self.companies,
            self.people,
            self.rent,
            self.config.household_rent_income_share,
        )
        maintain_housing(
            self.companies, self.people, self.resources, self.config
        )
        settle_operating_inputs(self.companies, self.resources, self.config)
        self._adjust_rent()
        previous_price_index = self.price_index
        self.price_index = self._consumer_price_index()
        inflation_rate = (
            self.price_index / max(previous_price_index, 1.0) - 1.0
        )

        company_debt_before_service = {
            company.id: company.debt for company in self.companies
        }
        interest_received = service_company_debt(self.companies, self.config)
        self.bank_reserves += interest_received
        self._settle_company_loan_accounts(company_debt_before_service)
        for company in self.companies:
            if company.active:
                company.profit = company.revenue - company.expenses
        adjust_profit_sharing_wages(
            self.companies,
            self.people,
            self.wage_reference,
            self.config.executive_bonus_rate,
            self.config.wage_productivity_share,
            self.config.loss_wage_adjustment_strength,
            self.config.profit_margin_target,
            self.sector_wage_index,
            self.config.sector_margin_target,
        )
        research_investment = invest_in_innovation(
            self.companies,
            self.random,
            self.resources,
            self.config.research_profit_share,
            self.config.research_outsourced_share,
        )
        corporate_taxes = tax_company_profits(
            self.companies, self.config.corporate_tax_rate
        )
        self.government_balance += corporate_taxes
        for company in self.companies:
            if company.active:
                company.profit = company.revenue - company.expenses
        # dividend floor pour éviter l'effondrement complet
        dividends_paid = distribute_dividends(
            self.companies,
            self.people,
            self.config.dividend_payout_rate,
            self.config.working_capital_months,
            self.config.excess_cash_payout_rate,
            self._bank_payout_rate(),
            self.config.bank_reserve_ratio,
        )
        for company in self.companies:
            if company.active:
                company.profit = company.revenue - company.expenses
                if company.sector == "bank":
                    company.bank_equity = max(
                        0.0,
                        company.bank_equity
                        + company.profit
                        - company.dividend_paid,
                    )

        secondary_volume = update_prices_and_trade(
            self.companies, self.people, event_sector, self.config
        )
        acquisitions = consolidate_market(
            self.companies, self.people, self.config, self.random, self.month
        )
        self.bank_reserves, bankruptcies = resolve_distressed_companies(
            self.companies,
            self.people,
            self.bank_reserves,
            self.config,
            self.random,
        )
        self._write_off_defaulted_bank_loans()
        self.total_defaults += bankruptcies
        new_companies, reserves_after_entry, startups = create_household_startups(
            self.companies,
            self.people,
            self.month,
            self.bank_reserves,
            self.config,
            self.random,
            housing_shortage,
            self.wage_reference,
            self.policy_rate,
        )
        for entrant in new_companies:
            if entrant.debt > 0.0:
                self._book_bank_loan(entrant.id, entrant.debt)
        self.bank_reserves = max(0.0, reserves_after_entry)
        self.companies.extend(new_companies)
        debt_before_investment = {c.id: c.debt for c in self.companies}
        production_capacity_investment = invest_in_production_capacity(
            self.companies,
            {"food": unmet_food_demand, "goods": unmet_goods_demand},
            self.config,
            self.resources,
            bank_reserves=self.bank_reserves,
            policy_rate=self.policy_rate,
            credit_share=self._credit_share(),
        )
        self._settle_new_credit(debt_before_investment)
        self._grow_population()

        self.total_tax_revenue += household_taxes + corporate_taxes
        self.total_benefits_paid += benefits_paid
        total_revenue = sum(company.revenue for company in self.companies if company.active)
        total_profit = sum(company.profit for company in self.companies if company.active)
        bank_interest_income = sum(
            company.revenue
            for company in self.companies
            if company.active and company.sector == "bank"
        )
        occupied = sum(person.rental_owner_id is not None for person in self.people)
        owner_occupied = sum(person.owns_home for person in self.people)
        self._update_firm_signals(unmet_food_demand, unmet_goods_demand)
        self._unmet_ratio = {
            "food": unmet_food_demand / max(1.0, sold_food + unmet_food_demand),
            "goods": unmet_goods_demand / max(1.0, sold_goods + unmet_goods_demand),
        }
        # Investissement hors construction (capacité, R&D, technologie des
        # entreprises), lissé sur 12 mois pour ne pas importer sa volatilité.
        construction_spend = housing_units_built * self.config.housing_build_cost
        firm_capex = sum(
            company.capital_expenditure
            for company in self.companies
            if company.active
        )
        tech_capex = max(
            0.0, firm_capex - construction_spend - production_capacity_investment
        )
        self._investment_history.append(
            production_capacity_investment + research_investment + tech_capex
        )
        smoothed_investment = sum(self._investment_history[-12:]) / len(
            self._investment_history[-12:]
        )
        # Construction comptée à sa valeur ajoutée réelle : l'ouvrage réalisé
        # chaque mois sur les chantiers en cours, pas la mise de fonds initiale.
        construction_value_added = sum(
            len(company.construction_pipeline)
            for company in self.companies
            if company.active and company.sector == "real_estate"
        ) * self.config.housing_build_cost / max(1, self.config.build_time_months)
        recurrent_gdp = (
            food_revenue
            + goods_revenue
            + self.resources.household_technology_revenue
            + rental_revenue
            + owner_occupied * self.rent
            + bank_interest_income
            + public_spent
            + smoothed_investment
        )
        nominal_gdp = recurrent_gdp + construction_value_added
        real_gdp = (
            sold_food * self.config.starting_food_price
            + sold_goods * self.config.starting_goods_price
            + (public_food_units * self.config.starting_food_price
               + public_goods_units * self.config.starting_goods_price)
            + self.resources.household_technology_units
            * self.config.tech_unit_price
            * self.config.technology_household_price_fraction
            + rental_revenue
            * self.config.starting_rent
            / max(self.rent, 1.0)
            + owner_occupied * self.config.starting_rent
            + bank_interest_income * 100.0 / max(self.price_index, 1.0)
            + smoothed_investment * 100.0 / max(self.price_index, 1.0)
            + construction_value_added
        )
        consumer_revenue = food_revenue + goods_revenue
        active_housing = sum(
            company.property_inventory
            for company in self.companies
            if company.active and company.sector == "real_estate"
        )
        record = MarketRecord(
            month=self.month,
            food_price=self.food_price,
            average_wage=average_wage,
            food_sold=sold_food,
            unemployment=unemployment,
            housing_rent=self.rent,
            total_revenue=total_revenue,
            total_profit=total_profit,
            event=self.last_event,
            goods_price=self.goods_price,
            goods_sold=sold_goods,
            unmet_food_demand=unmet_food_demand,
            unmet_goods_demand=unmet_goods_demand,
            tax_revenue=household_taxes + corporate_taxes,
            benefits_paid=benefits_paid,
            dividends_paid=dividends_paid,
            secondary_volume=secondary_volume,
            homes_occupied=occupied,
            vacancies=max(0, active_housing - occupied),
            acquisitions=acquisitions,
            startups=startups,
            bankruptcies=bankruptcies,
            bank_reserves=self.bank_reserves,
            bank_loan_portfolio=sum(
                company.debt for company in self.companies if company.active
            )
            + sum(person.mortgage_debt for person in self.people),
            bank_equity=sum(
                company.bank_equity
                for company in self.companies
                if company.active and company.sector == "bank"
            ),
            bank_interest_income=bank_interest_income,
            housing_construction=(
                housing_units_built * self.config.housing_build_cost
            ),
            resource_market_cash=self.resources.market_cash,
            employed_count=sum(person.employer_id is not None for person in self.people),
            household_income=sum(person.income_this_turn for person in self.people),
            household_spending=sum(person.expenses_this_turn for person in self.people),
            raw_materials_remaining=self.resources.raw_materials,
            recycled_materials_remaining=self.resources.recycled_materials,
            raw_material_price=self.resources.raw_material_price,
            consumer_price_index=self.price_index,
            inflation_rate=inflation_rate,
            real_average_wage=average_wage * 100.0 / max(self.price_index, 1.0),
            real_household_income=sum(
                person.income_this_turn for person in self.people
            )
            * 100.0
            / max(self.price_index, 1.0),
            nominal_gdp=nominal_gdp,
            real_gdp=real_gdp,
            recurrent_gdp=recurrent_gdp,
            total_gdp=nominal_gdp,
            construction_value_added=construction_value_added,
            smoothed_investment=smoothed_investment,
            government_purchases=public_spent,
            public_transfers=public_transfers,
            money_issued=money_issued,
            money_supply=(
                sum(p.cash for p in self.people)
                + sum(c.cash for c in self.companies)
                + self.government_balance
                + self.resources.market_cash
            ),
            government_balance=self.government_balance,
            upstream_payroll=supplier_payroll,
            wage_reference=self.wage_reference,
            productivity_index=self.productivity_index,
            household_technology_revenue=self.resources.household_technology_revenue,
            household_technology_units=self.resources.household_technology_units,
            food_inventory=sum(
                company.inventory
                for company in self.companies
                if company.active and company.sector == "food"
            ),
            goods_inventory=sum(
                company.goods_inventory
                for company in self.companies
                if company.active and company.sector == "goods"
            ),
            technology_inventory=sum(
                company.tech_inventory
                for company in self.companies
                if company.active and company.sector == "technology"
            ),
            food_produced=sum(
                company.units_produced
                for company in self.companies
                if company.active and company.sector == "food"
            ),
            goods_produced=sum(
                company.units_produced
                for company in self.companies
                if company.active and company.sector == "goods"
            ),
            production_capacity_investment=production_capacity_investment,
            new_households=self.new_households_this_turn,
            population_count=len(self.people),
            supplier_income=supplier_payroll,
            supplier_employment=supplier_employment,
            household_tax_revenue=household_taxes,
            policy_rate=self.policy_rate,
            mortgage_debt=sum(person.mortgage_debt for person in self.people),
            company_prices={
                company.id: company.stock_price if company.active else 0.0
                for company in self.companies
            },
            sector_employment={
                **{
                    sector: sum(
                        len(company.employees)
                        for company in self.companies
                        if company.active and company.sector == sector
                    )
                    for sector, _ in SECTORS
                    if sector != "services"
                },
                "services": supplier_employment,
            },
            sector_revenue={
                **{
                    sector: sum(
                        company.revenue
                        for company in self.companies
                        if company.active and company.sector == sector
                    )
                    for sector, _ in SECTORS
                    if sector != "services"
                },
                "services": supplier_payroll,
            },
            sector_profit={
                sector: sum(
                    company.profit
                    for company in self.companies
                    if company.active and company.sector == sector
                )
                for sector, _ in SECTORS
                if sector != "services"
            },
            sector_average_wage={
                **{
                    sector: (
                        sum(
                            company.wage_bill
                            for company in self.companies
                            if company.active and company.sector == sector
                        )
                        / max(
                            1,
                            sum(
                                len(company.employees)
                                for company in self.companies
                                if company.active and company.sector == sector
                            ),
                        )
                    )
                    for sector, _ in SECTORS
                    if sector != "services"
                },
                "services": (
                    supplier_payroll / supplier_employment
                    if supplier_employment
                    else 0.0
                ),
            },
            sector_market_share={
                "food": food_revenue / consumer_revenue
                if consumer_revenue > 0.0
                else 0.0,
                "goods": goods_revenue / consumer_revenue
                if consumer_revenue > 0.0
                else 0.0,
            },
        )
        enrich_wealth_record(
            record,
            self.people,
            self.companies,
            self.config,
            self.price_index,
        )
        self.history.append(record)
        self._adjust_policy_rate(inflation_rate, unemployment)
        return record

    def _settle_new_credit(self, debt_before: dict[str, float]) -> float:
        """Débite les réserves bancaires des prêts accordés et les inscrit aux livres."""
        total = 0.0
        for company in self.companies:
            extension = max(0.0, company.debt - debt_before.get(company.id, 0.0))
            if extension > 0.0 and company.sector != "bank":
                self._book_bank_loan(company.id, extension)
                total += extension
        if total > 0.0:
            self.bank_reserves = max(0.0, self.bank_reserves - total)
        return total

    def _book_bank_loan(self, borrower_id: str, amount: float) -> None:
        banks = [
            company
            for company in self.companies
            if company.active and company.sector == "bank"
        ]
        if amount <= 0.0 or not banks:
            return
        banks.sort(
            key=lambda bank: (
                sum(bank.bank_loan_accounts.values()),
                bank.id,
            )
        )
        for bank in banks:
            allocation = amount / len(banks)
            bank.bank_loan_accounts[borrower_id] = (
                bank.bank_loan_accounts.get(borrower_id, 0.0) + allocation
            )

    def _settle_company_loan_accounts(
        self, previous_debts: dict[str, float]
    ) -> None:
        companies_by_id = {company.id: company for company in self.companies}
        for borrower_id, previous_debt in previous_debts.items():
            borrower = companies_by_id.get(borrower_id)
            if borrower is None:
                continue
            if borrower.sector == "bank":
                continue
            lenders = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.bank_loan_accounts.get(borrower_id, 0.0) > 0.0
            ]
            total_booked = sum(
                bank.bank_loan_accounts[borrower_id] for bank in lenders
            )
            if not lenders or total_booked <= 0.0:
                continue
            shares = {
                bank.id: bank.bank_loan_accounts[borrower_id] / total_booked
                for bank in lenders
            }
            interest_paid = borrower.interest_paid_this_turn
            unpaid_interest = max(
                0.0,
                borrower.debt
                - previous_debt
                + borrower.principal_paid_this_turn,
            )
            for bank in lenders:
                share = shares[bank.id]
                bank.revenue += (interest_paid + unpaid_interest) * share
                bank.bank_loan_accounts[borrower_id] = max(
                    0.0, borrower.debt * share
                )

    def _settle_mortgage_accounts(
        self, previous_debts: dict[str, float]
    ) -> None:
        for person in self.people:
            previous_debt = previous_debts.get(person.id, 0.0)
            if previous_debt <= 0.0:
                continue
            interest_accrued = previous_debt * person.mortgage_rate
            lenders = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.bank_loan_accounts.get(f"mortgage:{person.id}", 0.0) > 0.0
            ]
            total_booked = sum(
                bank.bank_loan_accounts[f"mortgage:{person.id}"]
                for bank in lenders
            )
            if total_booked <= 0.0:
                continue
            for bank in lenders:
                share = (
                    bank.bank_loan_accounts[f"mortgage:{person.id}"]
                    / total_booked
                )
                bank.bank_loan_accounts[f"mortgage:{person.id}"] = (
                    person.mortgage_debt * share
                )
                bank.revenue += interest_accrued * share

    def _write_off_defaulted_bank_loans(self) -> None:
        defaulted_ids = {
            company.id
            for company in self.companies
            if company.sector != "bank" and not company.active
        }
        for borrower_id in defaulted_ids:
            lenders = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.bank_loan_accounts.get(borrower_id, 0.0) > 0.0
            ]
            exposure = sum(
                bank.bank_loan_accounts[borrower_id] for bank in lenders
            )
            if exposure <= 0.0:
                continue
            borrower = next(
                (company for company in self.companies if company.id == borrower_id),
                None,
            )
            loss = min(exposure, borrower.default_loss if borrower else exposure)
            for bank in lenders:
                share = bank.bank_loan_accounts[borrower_id] / exposure
                bank.default_loss += loss * share
                bank.expenses += loss * share
                bank.bank_equity -= loss * share
                bank.bank_loan_accounts.pop(borrower_id, None)

        banks = [
            bank
            for bank in self.companies
            if bank.sector == "bank" and bank.active and bank.bank_equity <= 0.0
        ]
        for failed_bank in banks:
            receivers = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.active
                and bank.id != failed_bank.id
            ]
            if not receivers:
                continue
            receiver = min(
                receivers,
                key=lambda bank: sum(bank.bank_loan_accounts.values()),
            )
            receiver.cash += failed_bank.cash
            receiver.bank_equity += max(0.0, failed_bank.bank_equity)
            for borrower_id, balance in failed_bank.bank_loan_accounts.items():
                receiver.bank_loan_accounts[borrower_id] = (
                    receiver.bank_loan_accounts.get(borrower_id, 0.0) + balance
                )
            failed_bank.cash = 0.0
            failed_bank.bank_loan_accounts.clear()
            failed_bank.active = False
            failed_bank.stock_price = 0.0
            failed_bank.shares_outstanding = 0.0
            failed_bank.treasury_shares = 0.0
            for owner in [*self.people, *self.companies]:
                owner.shares.pop(failed_bank.id, None)

    def _adjust_policy_rate(
        self,
        inflation_rate: float,
        unemployment_rate: float,
    ) -> None:
        inflation_gap = inflation_rate - self.config.inflation_target
        unemployment_gap = max(0.0, unemployment_rate - 0.08)
        self.policy_rate = max(
            0.0001,
            min(
                0.04,
                self.policy_rate
                + self.config.policy_rate_inflation_sensitivity * inflation_gap
                - self.config.policy_rate_unemployment_sensitivity
                * unemployment_gap,
            ),
        )

    def _food_demand(self) -> float:
        return sum(
            min(
                person.cash,
                max(
                    0.0,
                    person.income_this_turn
                    * self.config.food_income_budget_share
                    + person.cash * self.config.food_cash_draw_rate,
                ),
            )
            * person.consumption_propensity
            / max(self.food_price * 1.16, 0.01)
            for person in self.people
        )

    def _goods_demand(self) -> float:
        return sum(
            min(
                person.cash,
                max(
                    0.0,
                    person.income_this_turn
                    * self.config.goods_income_budget_share
                    + person.cash * self.config.goods_cash_draw_rate,
                ),
            )
            * person.consumption_propensity
            / max(self.goods_price * 1.16, 0.01)
            for person in self.people
        )

    def _grow_population(self) -> None:
        population_cap = int(
            self.initial_population_size
            * (1.0 + self.config.maximum_population_growth)
        )
        if (
            self.config.monthly_population_growth_rate <= 0.0
            or len(self.people) >= population_cap
        ):
            return
        self.population_growth_progress += (
            len(self.people) * self.config.monthly_population_growth_rate
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
                    1.0 - self.config.household_propensity_variance,
                    1.0 + self.config.household_propensity_variance,
                ),
                quality_preference=self.random.uniform(0.65, 1.35),
                technology_level=0.0,
            )
            newcomer.skills = initial_skills(
                newcomer.skill, self.labor_random, SKILL_SECTORS
            )
            newcomer.entrepreneurial_spirit = self.trait_random.random()
            newcomer.trade = assign_trade(self.trait_random, self.config)
            newcomer.job_trade = newcomer.trade
            self.people.append(newcomer)
            self.population_growth_progress -= 1.0
            self.population_job_progress += self.config.target_employment_rate
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

    def _update_raw_material_price(
        self,
        food_demand: float,
        goods_demand: float,
    ) -> None:
        worker_skills = {
            person.id: (person.skill, person.technology_level)
            for person in self.people
        }
        food_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.config.food_productivity
            * company.productivity_trait
            * productivity_multiplier(company, self.config)
            + company.production_capacity
            for company in self.companies
            if company.active and company.sector == "food"
        )
        goods_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.config.goods_productivity
            * company.productivity_trait
            * productivity_multiplier(company, self.config)
            + company.production_capacity
            for company in self.companies
            if company.active and company.sector == "goods"
        )
        extraction_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.config.raw_material_extraction_productivity
            for company in self.companies
            if company.active and company.sector == "materials"
        )
        raw_demand = min(food_demand * 1.05, food_capacity) + 1.5 * min(
            goods_demand * 1.05, goods_capacity
        )
        extractable_virgin = min(self.resources.raw_materials, extraction_capacity)
        raw_supply = extractable_virgin + self.resources.recycled_materials
        updated_price = update_food_price(
            self.resources.raw_material_price,
            raw_demand,
            raw_supply,
            self._price_ceiling(),
        )
        suppliers = [
            company
            for company in self.companies
            if company.active and company.sector == "materials"
        ]
        virgin_demand = max(0.0, raw_demand - self.resources.recycled_materials)
        minimum_price = 1.0
        # demande de matières vierges non servie : signal d'embauche pour les
        # extracteurs (comme la demande non satisfaite en alimentation/biens)
        self._raw_shortfall = max(0.0, virgin_demand - extractable_virgin)
        if extraction_capacity > 0.0:
            # Plancher de coût moyen à capacité normale d'utilisation (90 %) :
            # les extracteurs sont dimensionnés sur la demande, donc leur coût
            # unitaire ne dépend pas du volume vierge d'un mois donné (le
            # recyclage peut le faire tomber presque à zéro sans que le coût
            # de l'extraction ne devienne infini).
            staffed = [s for s in suppliers if s.employees]
            if staffed:
                utilization = max(
                    self.config.raw_material_floor_utilization,
                    min(1.0, self._raw_util_ema),
                )
                unit_cost = sum(
                    s.wage_offer
                    / (
                        self.config.raw_material_extraction_productivity
                        * utilization
                    )
                    + self.config.raw_material_extraction_cost
                    + self.config.exploration_cost_per_unit
                    * self.config.reserve_replacement_rate
                    for s in staffed
                ) / len(staffed)
                minimum_price = max(
                    minimum_price,
                    unit_cost
                    / max(
                        0.5,
                        1.0
                        - self.config.sector_margin_target.get("materials", 0.1)
                        / max(0.3, 1.0 - self.config.corporate_tax_rate),
                    ),
                )
        # Correction lente par l'écart de marge du secteur (sorties du marché si
        # pertes durables, entrées si marges élevées)
        self._materials_price_adjust = max(
            0.5,
            min(
                2.0,
                self._materials_price_adjust
                * (1.0 + self._margin_price_pressure("materials")),
            ),
        )
        minimum_price *= self._materials_price_adjust
        minimum_price = min(self._price_ceiling(), minimum_price)
        self.resources.raw_material_price = max(minimum_price, updated_price)

    def _adjust_rent(self) -> None:
        rental_supply = sum(
            company.property_inventory
            for company in self.companies
            if company.active and company.sector == "real_estate"
        )
        tenants = sum(not person.owns_home for person in self.people)
        housing_pressure = tenants / max(rental_supply, 1)
        incomes = sorted(
            person.income_this_turn
            for person in self.people
            if person.income_this_turn > 0.0
        )
        median_income = incomes[len(incomes) // 2] if incomes else self.wage_reference
        affordability_ratio = self.rent / max(median_income, 1.0)
        affordability_adjustment = (
            0.35 - affordability_ratio
        ) * self.config.rent_affordability_weight
        # rendement locatif : le loyer rejoint peu à peu un rendement cible du
        # prix du logement (sinon les promoteurs ne couvrent plus leurs coûts)
        self._rent_margin_adjust = max(
            0.6,
            min(1.5, self._rent_margin_adjust * (1.0 + self._margin_price_pressure("real_estate"))),
        )
        yield_gap = (
            self.config.housing_price
            * self.config.target_rental_yield
            * self._rent_margin_adjust
            / max(self.rent, 1.0)
            - 1.0
        )
        yield_adjustment = max(-0.01, min(0.01, yield_gap * self.config.rental_yield_weight))
        rent_change = max(
            -0.035,
            min(
                0.035,
                (housing_pressure - 0.85) * 0.025
                + max(-0.03, min(0.03, affordability_adjustment))
                + yield_adjustment,
            ),
        )
        # plafond mobile : le loyer peut suivre le revenu nominal à long terme
        rent_ceiling = self.config.starting_rent * max(
            4.0, 1.5 * self.wage_reference / self.config.starting_wage
        )
        self.rent = max(
            self.config.starting_rent * 0.4,
            min(rent_ceiling, self.rent * (1.0 + rent_change)),
        )

    def trade_table(self) -> list[dict[str, object]]:
        """Offre, demande, salaire moyen et chômage par métier."""
        return self.trades.table(self.people)

    def trade_report(self) -> dict[str, tuple[float, float]]:
        """Résumé compact : métier -> (chômage, coefficient de salaire)."""
        return {
            row["Métier"][:5]: (round(row["Chômage"], 2), row["Coefficient de salaire"])
            for row in self.trade_table()
        }

    def _price_ceiling(self) -> float:
        """Plafond des prix courants, indexé sur les salaires (le plafond nominal initial
        devient bloquant après quelques décennies de croissance nominale)."""
        return self.config.market_price_ceiling * max(
            1.0, self.wage_reference / max(self.config.starting_wage, 1.0)
        )

    def _update_housing_costs(self) -> None:
        """Coût de construction indexé ; prix = coût × marge d'offre et de demande.

        Le coût de chantier suit salaires et prix (moyenne). La marge de
        promotion monte quand des ménages restent sans logement (pénurie) et
        baisse quand le parc est excédentaire : les achats des ménages
        n'assèchent donc pas durablement l'immobilier, et les prix s'ajustent
        lentement à l'offre et à la demande. Le foncier s'étend avec le parc
        (étalement urbain) pour que la construction reste possible.
        """
        config = self.config
        wage_index = self.wage_reference / max(config.starting_wage, 1.0)
        cost_index = 0.6 * wage_index + 0.4 * self.resources.input_cost_index
        stock = sum(
            c.property_inventory
            for c in self.companies
            if c.active and c.sector == "real_estate"
        )
        tenants = sum(not person.owns_home for person in self.people)
        pipeline = sum(
            len(c.construction_pipeline)
            for c in self.companies
            if c.active and c.sector == "real_estate"
        )
        shortage = (tenants - stock - pipeline) / max(tenants, 1)
        self._housing_markup = max(
            config.housing_markup_bounds[0],
            min(
                config.housing_markup_bounds[1],
                self._housing_markup
                * (1.0 + max(-0.004, min(0.004, 0.02 * shortage))),
            ),
        )
        config.housing_build_cost = self._base_build_cost * cost_index
        config.housing_price = config.housing_build_cost * self._housing_markup
        self._land_progress += stock * config.land_expansion_rate
        new_plots = int(self._land_progress)
        if new_plots > 0:
            self.resources.land_units += new_plots
            self._land_progress -= new_plots

    def _consumer_price_index(self) -> float:
        weights = (
            self.config.cpi_food_weight,
            self.config.cpi_goods_weight,
            self.config.cpi_rent_weight,
        )
        total_weight = sum(weights)
        if total_weight <= 0.0:
            return 100.0
        price_ratios = (
            self.food_price / self.config.starting_food_price,
            self.goods_price / self.config.starting_goods_price,
            self.rent / self.config.starting_rent,
        )
        return (
            100.0
            * sum(
                weight * ratio
                for weight, ratio in zip(weights, price_ratios)
            )
            / total_weight
        )

    def _generate_event(self) -> tuple[str, str | None]:
        if self.random.random() >= 0.12:
            return "Aucun événement", None
        sector, _ = self.random.choice(
            tuple(item for item in SECTORS if item[0] not in {"bank", "services"})
        )
        descriptions = {
            "food": "Pic de demande alimentaire",
            "technology": "Innovation technologique",
            "goods": "Lancement de nouveaux produits",
            "real_estate": "Demande immobilière soutenue",
            "materials": "Variation de la demande en matières premières",
            "energy": "Variation du prix de l'énergie",
        }
        if sector == "food" and self.random.random() < 0.5:
            return "Choc de matières premières", sector
        return descriptions[sector], sector

    def company_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for company in self.companies:
            wage_average = company.wage_bill / len(company.employees) if company.employees else 0.0
            holdings = [
                (
                    f"{self.company_name(company_id)}: {quantity:.1f} actions "
                    f"({quantity / max(self._company_shares(company_id), 1.0):.1%})"
                )
                for company_id, quantity in sorted(company.shares.items())
            ]
            loan_portfolio = sum(company.bank_loan_accounts.values())
            rows.append(
                {
                    "_id": company.id,
                    "Entreprise": company.name,
                    "Secteur": dict(SECTORS)[company.sector],
                    "Statut": "Active" if company.active else "Absorbée / fermée",
                    "CEO": self.person_name(company.executive_id),
                    "Employés": len(company.employees),
                    "Salaire moyen": wage_average,
                    "Chiffre d'affaires": company.revenue,
                    "Bénéfice": company.profit,
                    "Trésorerie": company.cash,
                    "Dettes": company.debt,
                    "Dividendes": company.dividend_paid,
                    "Participations": ", ".join(holdings) or "—",
                    "Portefeuille de prêts": loan_portfolio,
                    "Emprunteurs": len(company.bank_loan_accounts),
                    "Cours": company.stock_price,
                    "Fortune nette": self.company_wealth(company),
                    "Qualité": company.quality_trait,
                    "Stock alimentaire": company.inventory if company.sector == "food" else 0.0,
                    "Valeur du stock alimentaire": (
                        company.inventory * self.config.food_input_cost
                        if company.sector == "food"
                        else 0.0
                    ),
                    "Stock de biens": company.goods_inventory if company.sector == "goods" else 0.0,
                    "Stock technologique": company.tech_inventory if company.sector == "technology" else 0.0,
                    "Production mensuelle": company.units_produced,
                    "Unités vendues": company.units_sold,
                    "Capacité supplémentaire": company.production_capacity,
                    "Niveau technologique": company.tech_level,
                    "Traits": (
                        f"Productivité {company.productivity_trait:.2f} · "
                        f"Risque {company.risk_tolerance:.2f} · "
                        f"Prix {company.pricing_aggression:.2f}"
                    ),
                    "Immobilier en stock": company.property_inventory if company.active else 0,
                    "Construction (mois)": (
                        str(min(company.construction_pipeline) - self.month)
                        if company.construction_pipeline
                        else "—"
                    ),
                }
            )
        return rows

    def company_name(self, company_id: str | None) -> str:
        if company_id is None:
            return "—"
        if company_id == "services":
            return "Services aux entreprises"
        return next((company.name for company in self.companies if company.id == company_id), company_id)

    def person_name(self, person_id: str | None) -> str:
        if person_id is None:
            return "Aucun"
        return next((person.name for person in self.people if person.id == person_id), person_id)

    def company_wealth(self, company: Company) -> float:
        from economy.wealth import company_net_worth

        return company_net_worth(company, self.companies, self.config)

    def household_wealth(self, person: Person) -> float:
        from economy.wealth import household_net_worth

        return household_net_worth(person, self.companies, self.config)

    def household_leaderboard(self, limit: int = 10) -> list[dict[str, object]]:
        households, _ = wealth_leaderboards(
            self.people, self.companies, self.config, limit
        )
        return households

    def company_leaderboard(self, limit: int = 10) -> list[dict[str, object]]:
        _, companies = wealth_leaderboards(
            self.people, self.companies, self.config, limit
        )
        return companies

    def population_summary(self) -> dict[str, float]:
        employed = sum(person.employer_id is not None for person in self.people)
        return {
            "employed": float(employed),
            "unemployed": float(len(self.people) - employed),
            "cash": sum(person.cash for person in self.people),
            "owners": float(sum(person.owns_home for person in self.people)),
            "renters": float(sum(person.rental_owner_id is not None for person in self.people)),
            "food": sum(person.last_food_bought for person in self.people),
            "goods": sum(person.last_goods_bought for person in self.people),
            "average_cash": (
                sum(person.cash for person in self.people) / len(self.people) if self.people else 0.0
            ),
            "home_satisfaction": (
                sum(person.owns_home or person.rental_owner_id is not None for person in self.people)
                / len(self.people)
                if self.people
                else 0.0
            ),
        }

    def household_rows(self, limit: int = 25) -> list[dict[str, object]]:
        return [
            {
                "_id": person.id,
                "Ménage": person.name,
                "Fortune nette": self.household_wealth(person),
                "Trésorerie": person.cash,
                "Employeur": self.company_name(person.employer_id),
                "CEO": any(company.executive_id == person.id for company in self.companies),
                "Revenu du mois": person.income_this_turn,
                "Nourriture achetée": person.last_food_bought,
                "Biens achetés": person.last_goods_bought,
                "Compétence": person.skill,
                "Expérience (mois)": person.employer_tenure,
                "Dépenses cumulées": person.cumulative_expenses,
                "Logement": "Propriétaire" if person.owns_home else (
                    "Locataire" if person.rental_owner_id else "Sans logement loué"
                ),
                "Dette immobilière": person.mortgage_debt,
                "Mensualité du prêt": person.mortgage_payment,
                "Actions détenues": ", ".join(
                    f"{self.company_name(company_id)} ({shares:.1f})"
                    for company_id, shares in person.shares.items()
                ) or "—",
            }
            for person in sorted(
                self.people,
                key=lambda household: self.household_wealth(household),
                reverse=True,
            )[:limit]
        ]

    def household_profile(self, person_id: str) -> dict[str, object] | None:
        person = next((household for household in self.people if household.id == person_id), None)
        if person is None:
            return None
        return {
            "person": person,
            "wealth": self.household_wealth(person),
            "shares": [
                {
                    "Entreprise": self.company_name(company_id),
                    "Actions": shares,
                    "Part du capital": (
                        shares / self._company_shares(company_id)
                        if self._company_shares(company_id) > 0.0
                        else 0.0
                    ),
                    "Valeur": shares * self._company_price(company_id),
                }
                for company_id, shares in person.shares.items()
            ],
        }

    def company_profile(self, company_id: str) -> Company | None:
        return next((company for company in self.companies if company.id == company_id), None)

    def _company_price(self, company_id: str) -> float:
        company = next((firm for firm in self.companies if firm.id == company_id), None)
        return company.stock_price if company and company.active else 0.0

    def _company_shares(self, company_id: str) -> float:
        company = next(
            (firm for firm in self.companies if firm.id == company_id), None
        )
        return company.shares_outstanding if company else 0.0

    def resource_summary(self) -> dict[str, float]:
        company_debt = sum(
            company.debt for company in self.companies if company.active
        )
        mortgage_debt = sum(person.mortgage_debt for person in self.people)
        return {
            "Matières premières restantes": self.resources.raw_materials,
            "Matières recyclées disponibles": self.resources.recycled_materials,
            "Matières en cours de récupération": self.resources.recycling_pending,
            "Prix des matières premières": self.resources.raw_material_price,
            "Terrains restants": float(self.resources.land_units),
            "Connaissances restantes": self.resources.knowledge,
            "Actions encore disponibles": sum(
                company.treasury_shares for company in self.companies if company.active
            ),
            "Prix d'un logement": self.config.housing_price,
            "Prix des biens courants": self.goods_price,
            "Trésorerie publique": self.government_balance,
            "Trésorerie bancaire": self.bank_reserves,
            "Encours des prêts bancaires": company_debt + mortgage_debt,
            "Prêts aux entreprises": company_debt,
            "Prêts immobiliers": mortgage_debt,
            "Recettes du mois du secteur amont (reversées en salaires)": self.resources.market_cash,
            "Fonds propres bancaires": sum(
                company.bank_equity
                for company in self.companies
                if company.sector == "bank" and company.active
            ),
            "Dettes des entreprises": company_debt,
            "Impôts cumulés": self.total_tax_revenue,
            "Aides cumulées": self.total_benefits_paid,
            "Entreprises actives": float(sum(company.active for company in self.companies)),
        }
