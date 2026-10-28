from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimulationConfig:
    population_size: int = 240
    companies_per_sector: int = 3
    initial_company_cash: float = 100000.0
    initial_person_cash: float = 3000.0
    raw_materials: float = 300000.0
    land_units: int = 250
    starting_food_price: float = 120.0
    starting_goods_price: float = 100.0
    starting_rent: float = 700.0
    starting_wage: float = 1400.0
    income_tax_rate: float = 0.08
    corporate_tax_rate: float = 0.12
    unemployment_benefit: float = 300.0
    food_input_cost: float = 65.0
    goods_input_cost: float = 42.0
    raw_material_unit_price: float = 12.0
    raw_material_extraction_cost: float = 0.5
    raw_material_extraction_productivity: float = 250.0
    material_recovery_rate: float = 0.90
    raw_material_floor_utilization: float = 0.30
    raw_material_capacity_buffer: float = 0.15
    reserve_replacement_rate: float = 0.95
    exploration_cost_per_unit: float = 2.0
    food_income_budget_share: float = 0.32
    food_cash_draw_rate: float = 0.12
    goods_income_budget_share: float = 0.22
    goods_cash_draw_rate: float = 0.10
    food_productivity: float = 24.0
    goods_productivity: float = 24.0
    payroll_coverage_target: float = 0.80
    loss_wage_adjustment_strength: float = 0.70
    target_employment_rate: float = 0.60
    tech_unit_price: float = 2500.0
    technology_depreciation_rate: float = 0.02
    housing_build_cost: float = 70000.0
    housing_price: float = 120000.0
    build_time_months: int = 6
    loan_interest_rate: float = 0.004
    credit_risk_spread: float = 0.0025
    maximum_loan_leverage: float = 3.0
    maximum_debt_service_ratio: float = 0.05
    acquisition_probability: float = 0.004
    min_firms_per_sector: int = 0
    merger_size_ratio: float = 2.0
    startup_threshold: float = 100000.0
    startup_max_capital: float = 600000.0
    startup_investment_fraction: float = 0.65
    startup_failure_probability: float = 0.30
    dividend_payout_rate: float = 0.12
    competitive_pricing_strength: float = 0.06
    investment_retention_rate: float = 0.08
    company_founder_probability: float = 0.08
    wage_productivity_share: float = 0.35
    trait_variance: float = 0.16
    random_promotion_strength: float = 0.25
    ceo_term_months: int = 12
    bank_initial_reserves: float = 7000000.0
    household_stock_investment_rate: float = 0.15
    executive_bonus_rate: float = 0.03
    household_saving_rate_base: float = 0.08
    household_saving_rate_income_elasticity: float = 0.12
    bankruptcy_months: int = 6
    inventory_spoilage_rate: float = 0.04
    target_inventory_months: float = 1.5
    market_price_ceiling: float = 350.0
    stock_book_value_weight: float = 0.55
    stock_earnings_multiple: float = 7.0
    stock_adjustment_speed: float = 0.18
    cpi_food_weight: float = 0.45
    cpi_goods_weight: float = 0.30
    cpi_rent_weight: float = 0.25
    # Salaires : aucun lien mÃƒÂ©canique avec l'IPC. La rÃƒÂ©fÃƒÂ©rence salariale ÃƒÂ©volue
    # avec la productivitÃƒÂ© mesurÃƒÂ©e, la tension du marchÃƒÂ© du travail et la
    # rentabilitÃƒÂ© du tissu productif (voir EconomySimulation._update_wage_reference).
    natural_unemployment_rate: float = 0.05
    wage_tension_sensitivity: float = 0.04
    wage_productivity_pass_through: float = 0.60
    wage_profitability_sensitivity: float = 0.02
    max_monthly_wage_change: float = 0.006
    # Emploi rÃƒÂ©actif ÃƒÂ  la demande
    demand_smoothing: float = 0.35
    skill_learning_rate: float = 0.0006
    research_profit_share: float = 0.12
    job_separation_rate: float = 0.04
    max_monthly_layoffs: int = 2
    max_monthly_hiring: int = 3
    revenue_smoothing: float = 0.20
    payroll_revenue_share: float = 0.45
    # Services / secteur amont : toutes les recettes du mois sont reversÃƒÂ©es en salaires
    upstream_wage_cap_multiplier: float = 1.5
    # Ãƒâ€°tat : dÃƒÂ©pense chaque mois ce qu'il collecte
    government_spending_rate: float = 1.0
    # TrÃƒÂ©sorerie : fonds de roulement conservÃƒÂ©, excÃƒÂ©dent distribuÃƒÂ©
    working_capital_months: float = 3.0
    excess_cash_payout_rate: float = 0.25
    # CrÃƒÂ©dit : part des investissements financÃƒÂ©e par emprunt bancaire
    credit_financed_share: float = 0.50
    credit_inflation_gain: float = 0.0
    bank_payout_inflation_gain: float = 0.0
    # Banque centrale (taux mensuels de la monnaie active)
    monetary_trend: float = 0.006
    monetary_inflation_gain: float = 0.05
    monetary_unemployment_gain: float = 0.05
    monetary_min_rate: float = -0.01
    monetary_max_rate: float = 0.02
    debt_amortization_rate: float = 0.003
    initial_household_ownership: float = 0.85
    initial_property_leverage: float = 0.10
    bank_excess_payout_rate: float = 0.01
    bank_reserve_ratio: float = 0.25
    unemployment_benefit_cycle_sensitivity: float = 0.50
    labor_mobility_premium: float = 0.08
    household_rent_income_share: float = 0.35
    rent_affordability_weight: float = 0.25
    housing_maintenance_rate: float = 0.0012
    inventory_holding_cost_rate: float = 0.002
    household_propensity_variance: float = 0.25
    consumer_price_elasticity: float = 1.5
    inventory_discount_strength: float = 0.08
    supplier_wage_payout_rate: float = 1.00
    income_tax_threshold: float = 3000.0
    top_marginal_income_tax_rate: float = 0.05
    inflation_target: float = 0.002
    policy_rate_inflation_sensitivity: float = 0.04
    policy_rate_unemployment_sensitivity: float = 0.004
    mortgage_down_payment_rate: float = 0.20
    mortgage_payment_income_share: float = 0.30
    mortgage_interest_rate: float = 0.004
    mortgage_term_months: int = 360
    monthly_population_growth_rate: float = 0.001
    maximum_population_growth: float = 0.50
    quality_trait_variance: float = 0.20
    quality_preference_strength: float = 1.0
    capacity_expansion_reinvestment_rate: float = 0.10
    productive_capacity_unit_cost: float = 2500.0
    productive_capacity_depreciation_rate: float = 0.005
    technology_productivity: float = 3.0
    technology_knowledge_use_rate: float = 0.01
    technology_price_growth: float = 0.25
    technology_business_budget_share: float = 0.03
    technology_business_profit_share: float = 0.20
    technology_business_max_level: float = 5.0
    technology_household_budget_share: float = 0.05
    technology_household_savings_share: float = 0.001
    technology_household_price_fraction: float = 0.05
    technology_household_supply_share: float = 0.30
    technology_household_level_gain: float = 0.05
    technology_household_max_level: float = 3.0
    technology_household_depreciation_rate: float = 0.01
    founder_public_share_fraction: float = 0.25
    # --- Concurrence : entrÃ©e de nouveaux acteurs ---
    # part approximative des foyers dotÃ©s d'un esprit entrepreneurial (trait alÃ©atoire tirÃ© dans [0,1])
    entrepreneurial_trait_threshold: float = 0.75
    entry_min_revenue_months: float = 25.0
    # exposant d'indexation du seuil de capital sur le niveau nominal des salaires
    startup_threshold_scaling: float = 0.25
    entry_concentration_reference: float = 0.55
    entry_concentration_gain: float = 2.0
    entry_profit_gain: float = 1.0
    # attrait total Ã  partir duquel la probabilitÃ© de fondation atteint son maximum
    entry_signal_scale: float = 0.20
    max_entries_per_month: int = 2
    real_estate_entry_without_shortage: bool = False
    # avantage temporaire des entrants (financÃ© par le capital du fondateur et le crÃ©dit bancaire)
    entrant_boost_months: int = 24
    entrant_niche_bonus: float = 0.20
    entrant_credit_multiple: float = 1.0
    entrant_credit_leverage_boost: float = 2.0
    entrant_housing_discount: float = 0.10
    entrant_housing_max_share: float = 0.30
    # HiÃƒÂ©rarchie : multiplicateurs de poste (dÃƒÂ©butant = 1,0)
    executive_wage_multiplier: float = 5.0
    manager_wage_multiplier: float = 2.4
    specialist_wage_multiplier: float = 1.6
    confirmed_wage_multiplier: float = 1.25
    executive_full_pay_headcount: float = 40.0
    skill_wage_slope: float = 0.06
    confirmed_share: float = 0.35
    senior_share: float = 0.20
    manager_share: float = 0.08
    rank_min_level: tuple = (0.0, 1.8, 2.6, 3.5)
    rank_min_career_months: tuple = (0, 6, 24, 48)
    skill_level_growth: float = 0.004
    sector_switch_penalty: float = 0.60
    transferable_skill_level: float = 2.5
    # Salaire d'entrÃƒÂ©e par secteur (alimentation = 0,80 Ãƒâ€” rÃƒÂ©fÃƒÂ©rence Ã¢â€°Ë† 1,0)
    sector_wage_factor: dict = field(
        default_factory=lambda: {
            "food": 0.65, "goods": 0.72, "materials": 0.85,
            "real_estate": 0.97, "technology": 1.33, "bank": 1.20,
            "energy": 1.05,
        }
    )
    # Marges nettes cibles par secteur (aprÃƒÂ¨s impÃƒÂ´t)
    sector_margin_target: dict = field(
        default_factory=lambda: {
            "food": 0.04, "goods": 0.06, "materials": 0.11,
            "real_estate": 0.15, "technology": 0.20, "bank": 0.25,
            "energy": 0.12,
        }
    )
    # Part du revenu lissÃƒÂ© consacrÃƒÂ©e ÃƒÂ  la masse salariale (intensitÃƒÂ© en travail)
    sector_payroll_share: dict = field(
        default_factory=lambda: {
            "technology": 0.68, "materials": 0.68,
            "real_estate": 0.20, "bank": 0.80, "energy": 0.40,
        }
    )
    real_estate_depreciation_rate: float = 0.0008
    # Rattrapage partiel de l'inflation passÃƒÂ©e par les salaires (avec retard)
    wage_inflation_pass_base: float = 0.70
    wage_inflation_pass_min: float = 0.50
    wage_inflation_pass_max: float = 0.90
    wage_inflation_lag_months: int = 2
    wage_inflation_window_months: int = 6
    sector_inflation_pass_bonus: dict = field(
        default_factory=lambda: {
            "food": -0.10, "goods": -0.05, "materials": 0.0,
            "real_estate": 0.05, "technology": 0.15, "bank": 0.10,
            "energy": 0.05,
        }
    )
    # --- Demande inter-entreprises (matrice entrÃƒÂ©es-sorties simple) ---
    # Intrants non matÃƒÂ©riels de l'alimentation / des biens (par unitÃƒÂ© produite,
    # indexÃƒÂ©s sur les prix) : part payÃƒÂ©e ÃƒÂ  chaque secteur fournisseur. Le solde
    # ("services") reste le secteur amont agrÃƒÂ©gÃƒÂ© (transport, nÃƒÂ©goce, etc.).
    input_cost_split: dict = field(
        default_factory=lambda: {
            "energy": 0.26, "technology": 0.16, "real_estate": 0.12,
            "materials": 0.10, "services": 0.36,
        }
    )
    # Autres secteurs acheteurs : part du chiffre d'affaires du mois prÃƒÂ©cÃƒÂ©dent
    # dÃƒÂ©pensÃƒÂ©e chaque mois chez les fournisseurs (ÃƒÂ©nergie, services numÃƒÂ©riques,
    # locaux, matiÃƒÂ¨res premiÃƒÂ¨res). Le reste du revenu paie salaires, impÃƒÂ´ts, profit.
    operating_io: dict = field(
        default_factory=lambda: {
            "materials": {"energy": 0.10, "technology": 0.04, "real_estate": 0.03},
            "technology": {"energy": 0.03, "real_estate": 0.06},
            "real_estate": {"energy": 0.02, "technology": 0.02, "materials": 0.08},
            "bank": {"technology": 0.05, "real_estate": 0.04, "energy": 0.01},
            "energy": {"materials": 0.30, "technology": 0.03, "real_estate": 0.03},
        }
    )
    # La construction achÃƒÂ¨te des matiÃƒÂ¨res premiÃƒÂ¨res (part du coÃƒÂ»t de chantier)
    construction_materials_share: float = 0.20
    # La recherche des entreprises est en partie sous-traitÃƒÂ©e au secteur technologique
    research_outsourced_share: float = 0.50
    # Boosts technologiques : prix = part de la valeur crÃƒÂ©ÃƒÂ©e pour l'acheteur
    tech_value_capture: float = 0.30
    tech_value_capture_max: float = 0.40
    tech_value_base_share: dict = field(
        default_factory=lambda: {
            "food": 0.35, "goods": 0.35, "materials": 0.30, "bank": 0.40,
        }
    )
    # Banques : effet d'un niveau technologique sur la capacitÃ© de prÃªt et sur
    # le risque de crÃ©dit (meilleur scoring)
    bank_credit_gain_per_level: float = 0.05
    bank_spread_gain_per_level: float = 0.03
    # --- Ã‰nergie et infrastructures ---
    # type : coÃ»t de base (en multiple du poste de rÃ©fÃ©rence), durÃ©e de vie (mois),
    # effet unitaire, niveau maximal. ``cost_basis`` : "capacity" (coÃ»t fixe indexÃ©),
    # "energy_bill" (mois de facture Ã©nergÃ©tique de l'acheteur), "revenue" (mois de CA).
    infrastructure_types: dict = field(
        default_factory=lambda: {
            "production": {"cost_basis": "capacity", "cost": 2500.0, "life": 200,
                           "effect": 1.0, "max_units": 1e9},
            "reseau": {"cost_basis": "energy_bill", "cost": 4.0, "life": 300,
                       "effect": 0.06, "max_units": 3.0},
            "stockage": {"cost_basis": "revenue", "cost": 0.25, "life": 200,
                         "effect": 0.015, "max_units": 4.0},
        }
    )
    infrastructure_cash_share: float = 0.10
    infrastructure_materials_share: float = 0.25
    infrastructure_work_per_worker: float = 8000.0
    energy_capex_share: float = 0.12
    energy_materials_share: float = 0.40
    energy_price_index_bounds: tuple = (0.4, 1.8)
    # amortissement mensuel des installations par secteur (dÃ©faut : productive_capacity_depreciation_rate)
    sector_capital_depreciation: dict = field(default_factory=lambda: {"energy": 0.010})
    tech_effect_per_level: float = 0.06
    tech_discount_rate: float = 0.01
    tech_investment_cash_share: float = 0.10
    # Ajustement lent des prix des secteurs B2B vers leur marge cible
    b2b_price_sensitivity: float = 0.10
    b2b_price_index_bounds: tuple = (0.3, 1.33)
    # Loyer cible : rendement locatif brut mensuel du prix d'un logement
    target_rental_yield: float = 0.0042
    rental_yield_weight: float = 0.05
    # Foncier : nouvelles parcelles ouvertes ÃƒÂ  la construction par mois et par logement existant
    land_expansion_rate: float = 0.004
    # lissage mensuel de l'indice des prix des intrants (renÃ©gociation lente des contrats)
    input_index_smoothing: float = 0.08
    # Marge de promotion (prix / coÃƒÂ»t de construction) : bornes de l'ajustement offre-demande
    housing_markup_bounds: tuple = (1.15, 2.6)
    # Achats de logements-patrimoine par les mÃ©nages (sans plafond de nombre)
    home_investment_cash_multiple: float = 1.5
    home_investment_probability: float = 0.03
    # --- MarchÃ© du travail par mÃ©tiers ---
    trades: tuple = (
        "operateur", "technicien", "ingenieur", "developpeur", "gestionnaire",
        "commercial", "financier", "batiment", "extracteur", "agent",
    )
    # composition de l'offre de travail (part des mÃ©nages par mÃ©tier)
    trade_supply_share: dict = field(
        default_factory=lambda: {
            "operateur": 0.33, "technicien": 0.13, "ingenieur": 0.05,
            "developpeur": 0.04, "gestionnaire": 0.10, "commercial": 0.09,
            "financier": 0.04, "batiment": 0.06, "extracteur": 0.10, "agent": 0.06,
        }
    )
    # mix de mÃ©tiers par secteur (part des postes)
    sector_trade_mix: dict = field(
        default_factory=lambda: {
            "food": {"operateur": 0.56, "technicien": 0.12, "commercial": 0.12,
                     "gestionnaire": 0.10, "ingenieur": 0.02, "agent": 0.08},
            "goods": {"operateur": 0.48, "technicien": 0.15, "ingenieur": 0.05,
                      "commercial": 0.12, "gestionnaire": 0.10, "agent": 0.10},
            "technology": {"developpeur": 0.36, "ingenieur": 0.22, "technicien": 0.12,
                           "gestionnaire": 0.12, "commercial": 0.10, "operateur": 0.08},
            "real_estate": {"batiment": 0.40, "agent": 0.25, "gestionnaire": 0.15,
                            "commercial": 0.12, "operateur": 0.08},
            "materials": {"extracteur": 0.58, "technicien": 0.14, "ingenieur": 0.06,
                          "operateur": 0.12, "gestionnaire": 0.10},
            "energy": {"technicien": 0.28, "ingenieur": 0.22, "extracteur": 0.14,
                       "operateur": 0.16, "gestionnaire": 0.10, "commercial": 0.10},
            "bank": {"financier": 0.42, "gestionnaire": 0.18, "developpeur": 0.12,
                     "commercial": 0.16, "agent": 0.12},
        }
    )
    # coefficient de salaire de base par mÃ©tier (rares et qualifiÃ©s mieux payÃ©s)
    trade_base_wage: dict = field(
        default_factory=lambda: {
            "operateur": 0.92, "technicien": 1.00, "ingenieur": 1.40,
            "developpeur": 1.35, "gestionnaire": 1.12, "commercial": 0.98,
            "financier": 1.25, "batiment": 0.96, "extracteur": 1.00, "agent": 0.90,
        }
    )
    trade_wage_sensitivity: float = 0.02
    trade_wage_bounds: tuple = (0.7, 1.8)
    retraining_months: int = 8
    retraining_start_month: int = 24
    retraining_cost_months: float = 1.0
    retraining_probability: float = 0.05
    retraining_min_unemployment: float = 0.08
    ceo_term_indeterminate: bool = True
    ceo_equity_bonus_rate: float = 0.02
    acquisition_probability_boost: float = 0.0
    profit_margin_target: float = 0.15
    margin_price_sensitivity: float = 0.15
    price_downward_rigidity: float = 1.0
    # sensibilitÃ© du blocage de la baisse des prix Ã  la pÃ©nurie (5 = baisse bloquÃ©e dÃ¨s 20 % de demande non servie)
    shortage_price_gate: float = 2.0
    seed: int = 7

    def __post_init__(self) -> None:
        self.population_size = max(1, self.population_size)
        self.companies_per_sector = max(1, self.companies_per_sector)
        self.land_units = max(0, self.land_units)
        self.build_time_months = max(1, self.build_time_months)
        self.ceo_term_months = max(1, self.ceo_term_months)
        self.bankruptcy_months = max(1, self.bankruptcy_months)
        self.mortgage_term_months = max(1, self.mortgage_term_months)
        self.technology_productivity = max(0.01, self.technology_productivity)
        self.productive_capacity_unit_cost = max(
            1.0, self.productive_capacity_unit_cost
        )
        self.technology_household_max_level = max(
            0.0, self.technology_household_max_level
        )
        self.technology_business_max_level = max(
            0.0, min(10.0, self.technology_business_max_level)
        )
        self.target_inventory_months = max(
            0.0, min(3.0, self.target_inventory_months)
        )
        self.merger_size_ratio = max(1.0, self.merger_size_ratio)
        self.maximum_loan_leverage = max(0.0, self.maximum_loan_leverage)
        self.maximum_debt_service_ratio = max(
            0.0, min(1.0, self.maximum_debt_service_ratio)
        )
        self.loan_interest_rate = max(0.0, min(1.0, self.loan_interest_rate))
        self.credit_risk_spread = max(0.0, min(0.25, self.credit_risk_spread))
        self.trait_variance = max(0.0, min(1.0, self.trait_variance))
        for field_name in (
            "initial_company_cash",
            "initial_person_cash",
            "raw_materials",
            "unemployment_benefit",
            "food_input_cost",
            "goods_input_cost",
            "raw_material_unit_price",
            "raw_material_extraction_cost",
            "startup_threshold",
            "bank_initial_reserves",
            "income_tax_threshold",
        ):
            setattr(self, field_name, max(0.0, getattr(self, field_name)))
        for field_name in (
            "executive_wage_multiplier",
            "manager_wage_multiplier",
            "specialist_wage_multiplier",
        ):
            setattr(
                self,
                field_name,
                max(0.5, min(12.0, getattr(self, field_name))),
            )
        for field_name in (
            "starting_food_price",
            "starting_goods_price",
            "starting_rent",
            "starting_wage",
            "food_productivity",
            "goods_productivity",
            "raw_material_extraction_productivity",
            "tech_unit_price",
            "housing_build_cost",
            "housing_price",
            "market_price_ceiling",
        ):
            setattr(self, field_name, max(0.01, getattr(self, field_name)))
        self.market_price_ceiling = max(
            self.market_price_ceiling,
            self.starting_food_price,
            self.starting_goods_price,
        )
        for field_name in (
            "income_tax_rate",
            "corporate_tax_rate",
            "startup_failure_probability",
            "dividend_payout_rate",
            "acquisition_probability",
            "company_founder_probability",
            "competitive_pricing_strength",
            "payroll_coverage_target",
            "loss_wage_adjustment_strength",
            "investment_retention_rate",
            "wage_productivity_share",
            "random_promotion_strength",
            "household_stock_investment_rate",
            "executive_bonus_rate",
            "inventory_spoilage_rate",
            "startup_investment_fraction",
            "target_employment_rate",
            "technology_depreciation_rate",
            "stock_book_value_weight",
            "stock_adjustment_speed",
            "material_recovery_rate",
            "reserve_replacement_rate",
            "food_income_budget_share",
            "food_cash_draw_rate",
            "goods_income_budget_share",
            "goods_cash_draw_rate",
            "cpi_food_weight",
            "cpi_goods_weight",
            "cpi_rent_weight",
            "job_separation_rate",
            "price_downward_rigidity",
            "natural_unemployment_rate",
            "wage_productivity_pass_through",
            "demand_smoothing",
            "revenue_smoothing",
            "payroll_revenue_share",
            "government_spending_rate",
            "excess_cash_payout_rate",
            "credit_financed_share",
            "debt_amortization_rate",
            "initial_household_ownership",
            "initial_property_leverage",
            "bank_excess_payout_rate",
            "bank_reserve_ratio",
            "unemployment_benefit_cycle_sensitivity",
            "labor_mobility_premium",
            "household_rent_income_share",
            "rent_affordability_weight",
            "housing_maintenance_rate",
            "inventory_holding_cost_rate",
            "household_propensity_variance",
            "inventory_discount_strength",
            "supplier_wage_payout_rate",
            "top_marginal_income_tax_rate",
            "inflation_target",
            "policy_rate_inflation_sensitivity",
            "policy_rate_unemployment_sensitivity",
            "mortgage_down_payment_rate",
            "mortgage_payment_income_share",
            "mortgage_interest_rate",
            "monthly_population_growth_rate",
            "maximum_population_growth",
            "quality_preference_strength",
            "capacity_expansion_reinvestment_rate",
            "productive_capacity_depreciation_rate",
            "technology_knowledge_use_rate",
            "technology_price_growth",
            "technology_business_budget_share",
            "technology_business_profit_share",
            "technology_household_budget_share",
            "technology_household_savings_share",
            "technology_household_price_fraction",
            "technology_household_supply_share",
            "technology_household_level_gain",
            "technology_household_depreciation_rate",
            "founder_public_share_fraction",
        ):
            value = getattr(self, field_name)
            setattr(self, field_name, max(0.0, min(1.0, value)))
        self.max_monthly_layoffs = max(0, self.max_monthly_layoffs)
        self.max_monthly_hiring = max(1, self.max_monthly_hiring)
        self.working_capital_months = max(0.0, self.working_capital_months)
        self.upstream_wage_cap_multiplier = max(
            1.0, self.upstream_wage_cap_multiplier
        )
        self.max_monthly_wage_change = max(
            0.0, min(0.05, self.max_monthly_wage_change)
        )
        self.wage_tension_sensitivity = max(0.0, self.wage_tension_sensitivity)
        self.wage_profitability_sensitivity = max(
            0.0, self.wage_profitability_sensitivity
        )
        self.quality_trait_variance = max(
            0.0, min(0.5, self.quality_trait_variance)
        )
        self.stock_earnings_multiple = max(0.0, min(30.0, self.stock_earnings_multiple))
        self.consumer_price_elasticity = max(
            0.0, min(5.0, self.consumer_price_elasticity)
        )
        self.technology_household_level_gain = max(
            0.001, self.technology_household_level_gain
        )


@dataclass
class Person:
    id: str
    name: str
    cash: float
    employer_id: str | None = None
    shares: dict[str, float] = field(default_factory=dict)
    owns_home: bool = False
    investment_homes: int = 0
    rental_owner_id: str | None = None
    skill: float = 1.0
    income_this_turn: float = 0.0
    dividend_income_this_turn: float = 0.0
    expenses_this_turn: float = 0.0
    last_food_bought: float = 0.0
    last_goods_bought: float = 0.0
    taxes_paid: float = 0.0
    employer_tenure: int = 0
    rank: int = -1
    career_months: int = 0
    skills: dict[str, float] = field(default_factory=dict)
    job_search_months: int = 0
    lifetime_earnings: float = 0.0
    cumulative_expenses: float = 0.0
    leadership_experience: int = 0
    risk_tolerance: float = 0.5
    current_wage: float = 0.0
    consumption_propensity: float = 1.0
    quality_preference: float = 1.0
    technology_level: float = 0.0
    employment_role: str = "Sans emploi"
    mortgage_debt: float = 0.0
    mortgage_rate: float = 0.0
    mortgage_payment: float = 0.0
    mortgage_months_remaining: int = 0
    founded_company: bool = False
    entrepreneurial_spirit: float = 0.0
    trade: str = "operateur"
    job_trade: str = ""
    retraining_months: int = 0
    retraining_target: str = ""
    trade_wage_factor: float = 1.0
    wealth_history: list[float] = field(default_factory=list)
    financial_history: list[dict[str, float]] = field(default_factory=list)


@dataclass
class Company:
    id: str
    name: str
    sector: str
    cash: float
    wage_offer: float
    price: float = 1.0
    inventory: float = 0.0
    goods_inventory: float = 0.0
    tech_level: float = 0.0
    technology_asset_value: float = 0.0
    quality_trait: float = 1.0
    production_capacity: float = 0.0
    productive_capital_value: float = 0.0
    employees: list[str] = field(default_factory=list)
    shares: dict[str, float] = field(default_factory=dict)
    shares_outstanding: float = 1000.0
    treasury_shares: float = 1000.0
    property_inventory: int = 0
    tech_inventory: float = 0.0
    revenue: float = 0.0
    expenses: float = 0.0
    profit: float = 0.0
    previous_profit: float = 0.0
    previous_revenue: float = 0.0
    stock_price: float = 20.0
    previous_stock_price: float = 20.0
    wage_bill: float = 0.0
    capital_expenditure: float = 0.0
    property_cost_sold_this_turn: float = 0.0
    depreciation_this_turn: float = 0.0
    interest_paid_this_turn: float = 0.0
    principal_paid_this_turn: float = 0.0
    default_loss: float = 0.0
    materials_supplied_this_turn: float = 0.0
    units_sold: float = 0.0
    units_produced: float = 0.0
    target_employees: int = 0
    staffing_need: int = 0
    demand_ema: float = 0.0
    revenue_ema: float = 0.0
    unmet_ema: float = 0.0
    stock_trade_volume: float = 0.0
    dividend_paid: float = 0.0
    debt: float = 0.0
    debt_interest_rate: float | None = None
    age_months: int = 0
    loss_streak: int = 0
    construction_in_progress: float = 0.0
    acquisition_count: int = 0
    productivity_trait: float = 1.0
    risk_tolerance: float = 0.5
    pricing_aggression: float = 0.5
    reinvestment_preference: float = 0.5
    innovation: float = 0.5
    executive_id: str | None = None
    ceo_experience: int = 0
    ceo_tenure: int = 0
    last_acquisition_month: int = -1000
    construction_pipeline: list[int] = field(default_factory=list)
    financial_history: list[dict[str, float]] = field(default_factory=list)
    customer_revenue: dict[str, float] = field(default_factory=dict)
    supplier_spending: dict[str, float] = field(default_factory=dict)
    active: bool = True
    founder_id: str | None = None
    bank_loan_accounts: dict[str, float] = field(default_factory=dict)
    bank_equity: float = 0.0
    # infrastructures Ã©nergÃ©tiques achetÃ©es (niveaux, avec usure)
    grid_level: float = 0.0
    storage_level: float = 0.0
    energy_bill: float = 0.0
    energy_bill_previous: float = 0.0
    energy_saving: float = 0.0
    infrastructure_sold_value: float = 0.0
    output_multiplier: float = 1.0
    entry_boost: float = 1.0
    exit_reason: str = ""


def productivity_multiplier(company: "Company", config: SimulationConfig) -> float:
    """Multiplicateur de production : boosts technologiques et stockage d'Ã©nergie."""
    multiplier = 1.0 + min(company.tech_level, 10.0) * config.tech_effect_per_level
    storage = config.infrastructure_types.get("stockage")
    if storage and company.storage_level > 0.0:
        multiplier *= 1.0 + company.storage_level * storage["effect"]
    return multiplier


@dataclass
class Resources:
    raw_materials: float
    land_units: int
    knowledge: float
    market_cash: float = 0.0
    raw_material_price: float = 12.0
    supplier_income: float = 0.0
    supplier_employment: int = 0
    supplier_surplus: float = 0.0
    household_technology_revenue: float = 0.0
    household_technology_units: float = 0.0
    recycled_materials: float = 0.0
    recycling_pending: float = 0.0
    # indice des prix (base 1) appliquÃƒÂ© aux intrants non matÃƒÂ©riels des producteurs
    input_cost_index: float = 1.0
    # multiplicateur de prix des secteurs B2B (rÃƒÂ©gulÃƒÂ© par l'ÃƒÂ©cart ÃƒÂ  la marge cible)
    tech_price_index: float = 1.0
    energy_price_index: float = 1.0
    materials_price_index: float = 1.0
    boost_spending: float = 0.0
    boost_value_created: float = 0.0
    tech_unmet_units: float = 0.0


@dataclass
class MarketRecord:
    month: int
    food_price: float
    average_wage: float
    food_sold: float
    unemployment: float
    housing_rent: float
    total_revenue: float
    total_profit: float
    event: str
    goods_price: float = 6.0
    goods_sold: float = 0.0
    unmet_food_demand: float = 0.0
    unmet_goods_demand: float = 0.0
    tax_revenue: float = 0.0
    benefits_paid: float = 0.0
    dividends_paid: float = 0.0
    secondary_volume: float = 0.0
    homes_occupied: int = 0
    vacancies: int = 0
    company_prices: dict[str, float] = field(default_factory=dict)
    top_household_wealth: float = 0.0
    top_company_wealth: float = 0.0
    household_gini: float = 0.0
    household_wealth_median: float = 0.0
    household_wealth_total: float = 0.0
    household_top10_share: float = 0.0
    company_debt: float = 0.0
    active_companies: int = 0
    acquisitions: int = 0
    startups: int = 0
    bankruptcies: int = 0
    bank_reserves: float = 0.0
    employed_count: int = 0
    household_income: float = 0.0
    household_spending: float = 0.0
    raw_materials_remaining: float = 0.0
    recycled_materials_remaining: float = 0.0
    consumer_price_index: float = 100.0
    inflation_rate: float = 0.0
    real_average_wage: float = 0.0
    real_household_income: float = 0.0
    nominal_gdp: float = 0.0
    real_gdp: float = 0.0
    household_technology_revenue: float = 0.0
    household_technology_units: float = 0.0
    food_inventory: float = 0.0
    goods_inventory: float = 0.0
    technology_inventory: float = 0.0
    food_produced: float = 0.0
    goods_produced: float = 0.0
    production_capacity_investment: float = 0.0
    new_households: int = 0
    population_count: int = 0
    supplier_income: float = 0.0
    supplier_employment: int = 0
    household_tax_revenue: float = 0.0
    policy_rate: float = 0.0
    mortgage_debt: float = 0.0
    sector_market_share: dict[str, float] = field(default_factory=dict)
    sector_employment: dict[str, int] = field(default_factory=dict)
    sector_revenue: dict[str, float] = field(default_factory=dict)
    sector_profit: dict[str, float] = field(default_factory=dict)
    sector_average_wage: dict[str, float] = field(default_factory=dict)
    bank_loan_portfolio: float = 0.0
    bank_equity: float = 0.0
    bank_interest_income: float = 0.0
    housing_construction: float = 0.0
    resource_market_cash: float = 0.0
    raw_material_price: float = 12.0
    recurrent_gdp: float = 0.0
    total_gdp: float = 0.0
    construction_value_added: float = 0.0
    smoothed_investment: float = 0.0
    government_purchases: float = 0.0
    public_transfers: float = 0.0
    money_issued: float = 0.0
    money_supply: float = 0.0
    government_balance: float = 0.0
    upstream_payroll: float = 0.0
    wage_reference: float = 0.0
    productivity_index: float = 1.0
