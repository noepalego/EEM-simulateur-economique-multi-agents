from __future__ import annotations

import copy
from dataclasses import dataclass, field



@dataclass
class SimulationConfig:
    population_size: int = 240
    companies_per_sector: int = 3
    # Période de chauffe : mois simulés à la création avec les mêmes règles, avant
    # la période 0 observée ; l'état initial ci-dessous est arbitraire (chômage de
    # 32 %, prix trois fois trop bas face aux salaires), le modèle construit le sien.
    warmup_months: int = 120
    initial_company_cash: float = 100000.0
    initial_person_cash: float = 3000.0
    raw_materials: float = 300000.0
    land_units: int = 250
    starting_food_price: float = 120.0
    starting_goods_price: float = 100.0
    starting_rent: float = 700.0
    starting_wage: float = 1400.0
    income_tax_rate: float = 0.10
    corporate_tax_rate: float = 0.16
    unemployment_benefit: float = 300.0
    food_input_cost: float = 65.0
    goods_input_cost: float = 42.0
    raw_material_unit_price: float = 12.0
    raw_material_extraction_cost: float = 0.5
    raw_material_extraction_productivity: float = 250.0
    material_recovery_rate: float = 0.90
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
    construction_work_per_worker: float = 8000.0  # euros de chantier aux prix initiaux / mois
    energy_initial_capacity: float = 20000.0     # unités physiques normalisées / entreprise
    energy_output_per_worker: float = 8000.0
    energy_capacity_unit_cost: float = 20.0
    energy_base_price: float = 1.0              # une unité n'est pas un kWh
    # Résolutions explicites (voir finance.resolve_distressed_companies et banking.py) : le
    # dernier acteur insolvable d'un secteur est liquidé (le secteur peut disparaître) ; le trou
    # de fonds propres d'une banque est comblé par l'État au plus à ``bank_bailout_cap_gdp`` du
    # PIB annuel, puis par le repreneur et les déposants
    bank_bailout_cap_gdp: float = 0.02
    bank_recap_buffer: float = 0.025              # coussin au-dessus de capital_requirement lors d'une recapitalisation publique (Bâle III : 2,5 %)
    policy_rate_offset: float = 0.0             # écart annuel ajouté au taux directeur visé (choc ou réglage)
    loan_interest_rate: float = 0.004
    credit_risk_spread: float = 0.0025
    maximum_loan_leverage: float = 3.0
    maximum_debt_service_ratio: float = 0.05
    min_firms_per_sector: int = 0
    startup_threshold: float = 100000.0
    startup_max_capital: float = 600000.0
    startup_investment_fraction: float = 0.65
    dividend_payout_rate: float = 0.12
    competitive_pricing_strength: float = 0.06
    investment_retention_rate: float = 0.08
    company_founder_probability: float = 0.08
    trait_variance: float = 0.16
    random_promotion_strength: float = 0.25
    ceo_term_months: int = 12
    bank_initial_reserves: float = 7000000.0
    household_stock_investment_rate: float = 0.15  # vitesse de rééquilibrage mensuel du portefeuille des ménages
    executive_bonus_rate: float = 0.03
    bankruptcy_months: int = 6
    inventory_spoilage_rate: float = 0.04
    target_inventory_months: float = 1.5
    stock_book_value_weight: float = 0.55
    stock_adjustment_speed: float = 0.18        # rappel mensuel du cours vers la valeur fondamentale
    # --- Bourse et épargne des ménages (voir stock_market.py) ---
    # valeur fondamentale : bénéfices annuels / (taux directeur + prime de risque - croissance)
    equity_risk_premium: float = 0.08
    stock_growth_expectation: float = 0.02      # croissance annuelle anticipée des bénéfices
    stock_min_discount: float = 0.04            # taux d'actualisation minimal (PER plafonné à 25)
    stock_flow_impact: float = 0.5              # effet sur le cours d'un déséquilibre d'ordres (en part de la capitalisation)
    stock_trend_weight: float = 0.05            # poids de la tendance récente (annualisée) dans le rendement anticipé
    # parts visées dans l'épargne investissable selon l'écart de rendement avec les dépôts
    stock_base_share: float = 0.20
    stock_return_sensitivity: float = 3.0       # hausse de la part en actions par point de rendement excédentaire (×0,01)
    stock_max_share: float = 0.70
    bond_base_share: float = 0.10
    bond_return_sensitivity: float = 3.0
    bond_max_share: float = 0.50
    savings_cash_buffer_months: float = 3.0     # trésorerie gardée pour consommer (en mois de revenu)
    cpi_food_weight: float = 0.45
    cpi_goods_weight: float = 0.30
    cpi_rent_weight: float = 0.25
    cpi_services_weight: float = 0.15
    # --- Loi d'Engel et services aux ménages ---
    # au revenu réel de référence (salaire de départ), les ménages consacrent
    # ``services_budget_share`` de leur revenu aux services ; quand le revenu réel
    # monte, la part de l'alimentation baisse (élasticité < 1) et celle des services
    # monte (élasticité > 1). Le prix des services suit les salaires (effet Baumol).
    services_budget_share: float = 0.06
    services_cash_draw_rate: float = 0.01
    engel_food_elasticity: float = 0.6
    engel_services_elasticity: float = 1.4
    # Salaires : aucun lien mécanique avec l'IPC ; la référence salariale est la moyenne observée
    # des offres des employeurs (voir EconomySimulation._update_wage_reference).
    natural_unemployment_rate: float = 0.05
    # Emploi réactif à la demande
    demand_smoothing: float = 0.35
    skill_learning_rate: float = 0.0006
    research_profit_share: float = 0.12
    job_separation_rate: float = 0.04
    max_monthly_layoffs: int = 2
    max_monthly_hiring: int = 3
    revenue_smoothing: float = 0.20
    payroll_revenue_share: float = 0.45
    # Services / secteur amont : toutes les recettes du mois sont reversées en salaires
    # échelle des programmes de la loi de finances exécutés chaque mois (choc « dépenses »)
    government_spending_rate: float = 1.0
    # --- Budget public (dette, fonction publique, investissement ; economy/state_budget.py) ---
    public_wage_factor: float = 1.0             # point d'indice de départ / salaire de référence
    public_purchases_gdp_share: float = 0.025   # achats de biens et services d'un centre (part du PIB)
    public_investment_gdp_share: float = 0.015  # infrastructures publiques d'un centre (part du PIB)
    bond_spread: float = 0.0010                 # prime des titres publics sur le taux directeur (mensuel)
    bond_capacity_share: float = 0.50           # part maximale de la capacité bancaire placée en titres publics
    debt_ceiling_gdp: float = 1.2               # plafond légal d'endettement (en PIB annuel)
    public_capital_depreciation_rate: float = 0.004
    public_capital_productivity: float = 0.05   # gain de productivité maximal du capital public
    public_capital_bonus: float = 0.0           # valeur du mois (calculée par le moteur)
    # --- Gouvernements et politique économique (voir economy/politics.py) ---
    political_cycle_enabled: bool = True        # élection d'un nouveau gouvernement à intervalle fixe
    election_period_months: int = 60            # une élection tous les 5 ans
    initial_political_orientation: float = 50.0 # 0 = libéral, 50 = centre, 100 = social
    policy_noise: float = 0.15                  # écart propre à chaque levier d'un gouvernement
    policy_strength: float = 1.0                # intensité globale des politiques menées
    minimum_wage: float = 0.0                   # valeur du mois (salaire minimum légal en vigueur)
    # Vote économique : le gouvernement sortant est jugé sur le chômage et l'inflation
    economic_voting: bool = True
    vote_misery_sensitivity: float = 0.6        # baisse des chances de réélection par point d'indice de misère
    vote_incumbent_advantage: float = 0.3       # avantage du sortant (logit) à bilan neutre
    vote_ideology_pull: float = 6.0             # glissement de l'orientation par point d'écart (chômage → social, inflation → libéral)
    # Encadrement des prix (gouvernements sociaux, quand l'inflation dépasse la cible)
    price_control_threshold: float = 0.01       # dépassement annuel de la cible qui déclenche l'encadrement
    price_control_slack: float = 0.002          # hausse mensuelle tolérée au-delà de la cible
    price_control_level: float = 0.0            # valeur du mois (fixée par le gouvernement)
    # --- Anticipations, concurrence, événements, impôt ---
    inflation_expectation_speed: float = 0.05   # vitesse d'adaptation mensuelle des anticipations
    concentration_margin_reference: float = 0.55  # indice de Herfindahl au-delà duquel le pouvoir de marché joue
    event_effect_size: float = 0.03             # ampleur des chocs d'événements sectoriels
    tax_loss_carry_months: int = 60             # durée de report des pertes pour l'impôt sur les bénéfices
    # Crédit aux ménages : consommation, défauts, saisies, enchères
    consumer_credit_income_multiple: float = 3.0  # encours maximal en mois de salaire
    consumer_credit_buffer_months: float = 1.0  # on emprunte quand la trésorerie passe sous ce nombre de mois de salaire
    consumer_credit_spread: float = 0.006       # taux = directeur + écart (mensuel)
    consumer_credit_term_months: int = 36
    consumer_default_months: int = 6            # mois d'impayés avant défaut
    mortgage_default_months: int = 6           # mois d'impayés avant saisie
    foreclosure_discount: float = 0.30          # décote de la vente forcée d'un logement saisi
    asset_auction_discount: float = 0.50        # décote des actifs d'une entreprise en faillite vendus aux enchères
    # Banques : taux variables, contraintes de capital par banque, marché interbancaire
    variable_rate_loans: bool = True            # prêts aux entreprises au taux directeur + marge (révisés chaque mois)
    bank_liquidity_months: float = 1.0          # trésorerie qu'une banque veut garder (mois de charges)
    # TVA sur l'alimentation et les biens : le ménage paie le prix × (1 + taux), l'État encaisse la taxe.
    # 10 % : taux effectif moyen (5,5 % sur l'alimentation, 20 % sur les biens) ; à 16 %,
    # chômage plus élevé et croissance plus faible sur 5 graines
    vat_rate: float = 0.10
    goods_material_per_unit: float = 1.5        # matières premières par unité de bien
    rent_effort_ratio: float = 0.35             # taux d'effort de référence pour le loyer
    food_stock_price: float = 0.0               # valeur du mois : prix courant servant à valoriser les stocks
    goods_stock_price: float = 0.0
    public_investment_split: dict = field(
        default_factory=lambda: {
            "real_estate": 0.35, "energy": 0.25, "materials": 0.10,
            "technology": 0.10, "services": 0.20,
        }
    )
    # Trésorerie : fonds de roulement conservé ; au-delà, chaque entreprise compare les rendements
    # (distribuer, rembourser, obligations, actions ; economy/treasury.py)
    working_capital_months: float = 1.5
    treasury_adjustment_speed: float = 0.25        # part mensuelle de l'écart à l'allocation visée comblée
    holding_discount: float = 0.30                 # décote des participations dans la valeur fondamentale
    # Prises de contrôle : offre si la valeur attendue dépasse le prix offert, acceptée par les
    # actionnaires selon leur propre estimation (``consolidation.takeover_by_value``)
    control_premium: float = 0.30                  # prime offerte sur le cours
    control_threshold: float = 0.50                # part des actions en circulation qui donne le contrôle
    takeover_valuation_spread: float = 0.40        # dispersion des estimations des ménages actionnaires (± moitié)
    # Crédit : part des investissements financée par emprunt bancaire, décidée
    # par l'entreprise (rendement annuel attendu du projet - coût annuel du crédit)
    credit_financed_share: float = 0.50         # part empruntée quand rendement = coût du crédit
    credit_return_gain: float = 8.0             # hausse de la part empruntée par point d'écart rendement - coût
    credit_leverage_spread: float = 0.003       # marge mensuelle supplémentaire d'un prêt d'investissement par unité d'endettement (dette / actifs)
    # Banque centrale (taux mensuels de la monnaie active)
    # Monnaie endogène : les prêts créent des dépôts ; la banque centrale fournit les réserves
    reserve_requirement: float = 0.10            # réserves exigées / dépôts
    capital_requirement: float = 0.08            # fonds propres minimaux / (prêts + titres publics)
    deposit_spread: float = 0.0012               # taux des dépôts = taux directeur - écart (mensuel)
    mortgage_spread: float = 0.0008              # taux hypothécaire = taux directeur + écart (mensuel)
    investment_risk_premium: float = 0.004       # prime de risque du taux d'actualisation des investissements
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
    income_tax_threshold: float = 3000.0
    top_marginal_income_tax_rate: float = 0.08
    inflation_target: float = 0.002
    # Taux directeur : règle de Taylor sur l'inflation sur 12 mois et le chômage
    taylor_neutral_real_rate: float = 0.018      # taux réel neutre annuel
    taylor_inflation_coefficient: float = 0.5    # réaction à l'écart d'inflation
    taylor_unemployment_coefficient: float = 1.0 # baisse du taux par point de chômage au-dessus du naturel
    policy_rate_smoothing: float = 0.10          # lissage mensuel du taux
    policy_rate_cap: float = 0.12                # plafond annuel du taux directeur
    mortgage_down_payment_rate: float = 0.20
    mortgage_payment_income_share: float = 0.30
    mortgage_interest_rate: float = 0.004
    mortgage_term_months: int = 360
    monthly_population_growth_rate: float = 0.001
    maximum_population_growth: float = 0.50
    quality_trait_variance: float = 0.20
    quality_preference_strength: float = 1.0
    productive_capacity_unit_cost: float = 2500.0
    productive_capacity_depreciation_rate: float = 0.005
    technology_productivity: float = 3.0
    technology_knowledge_use_rate: float = 0.0   # la connaissance est non rivale : elle ne s'use pas
    # --- Innovation et croissance endogène (voir economy/innovation.py) ---
    knowledge_elasticity: float = 0.5             # élasticité de la productivité au stock de connaissances
    # 0,25 : avec ~2 % du PIB en recherche, la productivité croît d'environ 1,5 %/an
    # (ordre de grandeur observé) ; à 0,15 la croissance s'éteignait après 30 ans
    knowledge_growth_per_intensity: float = 0.25  # croissance mensuelle des connaissances par point de PIB de recherche
    knowledge_depreciation_rate: float = 0.002    # obsolescence mensuelle
    tech_research_share: float = 0.40             # part de la masse salariale technologique consacrée à la recherche
    knowledge_bonus: float = 0.0                  # valeur du mois (calculée par le moteur)
    technology_price_growth: float = 0.25
    technology_business_max_level: float = 5.0
    technology_household_budget_share: float = 0.05
    technology_household_savings_share: float = 0.001
    technology_household_price_fraction: float = 0.05
    technology_household_supply_share: float = 0.30
    technology_household_level_gain: float = 0.05
    technology_household_max_level: float = 3.0
    technology_household_depreciation_rate: float = 0.01
    founder_public_share_fraction: float = 0.25
    # décision de vente du fondateur (economy.stock_market._founder_sale) : sa ligne ne dépasse pas
    # (founder_max_stake_share + founder_stake_risk_weight × goût du risque) de sa fortune nette
    founder_max_stake_share: float = 0.25
    founder_stake_risk_weight: float = 0.5
    # introduction en Bourse : pendant ses premiers mois, une entreprise fondée par un ménage
    # sert la demande de ses titres par les titres offerts avant les vendeurs
    ipo_window_months: int = 12
    # --- Concurrence : entrée de nouveaux acteurs ---
    # part approximative des foyers dotés d'un esprit entrepreneurial (trait aléatoire tiré dans [0,1])
    entrepreneurial_trait_threshold: float = 0.75
    entry_min_revenue_months: float = 25.0
    # exposant d'indexation du seuil de capital sur le niveau nominal des salaires
    startup_threshold_scaling: float = 0.25
    entry_concentration_reference: float = 0.55
    entry_concentration_gain: float = 2.0
    entry_profit_gain: float = 1.0
    # attrait total à partir duquel la probabilité de fondation atteint son maximum
    entry_signal_scale: float = 0.20
    max_entries_per_month: int = 2
    real_estate_entry_without_shortage: bool = False
    # crédit d'entrée (en multiple de l'apport du fondateur) et rachat de logements existants
    entrant_credit_multiple: float = 1.0
    entrant_housing_discount: float = 0.10
    entrant_housing_max_share: float = 0.30
    # Hiérarchie : multiplicateurs de poste (débutant = 1,0)
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
    # Salaire d'entrée par secteur (alimentation = 0,80 × référence ≈ 1,0)
    sector_wage_factor: dict = field(
        default_factory=lambda: {
            "food": 0.65, "goods": 0.72, "materials": 0.85,
            "real_estate": 0.97, "technology": 1.33, "bank": 1.20,
            "energy": 1.05, "services": 0.80,
        }
    )
    # Marges nettes cibles par secteur (après impôt)
    sector_margin_target: dict = field(
        default_factory=lambda: {
            "food": 0.04, "goods": 0.06, "materials": 0.11,
            "real_estate": 0.15, "technology": 0.20, "bank": 0.25,
            "energy": 0.12, "services": 0.07,
        }
    )
    # Part du revenu lissé consacrée à la masse salariale (intensité en travail)
    sector_payroll_share: dict = field(
        default_factory=lambda: {
            "technology": 0.68, "materials": 0.68,
            "real_estate": 0.20, "bank": 0.55, "energy": 0.40, "services": 0.90,
        }
    )
    real_estate_depreciation_rate: float = 0.0008
    # --- Demande inter-entreprises (matrice entrées-sorties simple) ---
    # Intrants non matériels de l'alimentation / des biens (par unité produite,
    # indexés sur les prix) : part payée à chaque secteur fournisseur. Le solde
    # ("services") reste le secteur amont agrégé (transport, négoce, etc.).
    input_cost_split: dict = field(
        default_factory=lambda: {
            "energy": 0.26, "technology": 0.16, "real_estate": 0.12,
            "materials": 0.10, "services": 0.36,
        }
    )
    # Autres secteurs acheteurs : part du chiffre d'affaires du mois précédent
    # dépensée chaque mois chez les fournisseurs (énergie, services numériques,
    # locaux, matières premières). Le reste du revenu paie salaires, impôts, profit.
    operating_io: dict = field(
        default_factory=lambda: {
            "materials": {"energy": 0.10, "technology": 0.04, "real_estate": 0.03},
            "technology": {"energy": 0.03, "real_estate": 0.06},
            "real_estate": {"energy": 0.02, "technology": 0.02, "materials": 0.08},
            "bank": {"technology": 0.05, "real_estate": 0.04, "energy": 0.01},
            "energy": {"materials": 0.30, "technology": 0.03, "real_estate": 0.03},
            "services": {"energy": 0.03, "technology": 0.04, "real_estate": 0.06},
        }
    )
    # La construction achète des matières premières (part du coût de chantier)
    construction_materials_share: float = 0.20
    # La recherche des entreprises est en partie sous-traitée au secteur technologique
    research_outsourced_share: float = 0.50
    # Boosts technologiques : prix = part de la valeur créée pour l'acheteur
    tech_value_capture: float = 0.30
    tech_value_base_share: dict = field(
        default_factory=lambda: {
            "food": 0.35, "goods": 0.35, "materials": 0.30, "bank": 0.40,
        }
    )
    # Banques : effet d'un niveau technologique sur la capacité de prêt et sur
    # le risque de crédit (meilleur scoring)
    bank_credit_gain_per_level: float = 0.05
    bank_spread_gain_per_level: float = 0.03
    # --- Énergie et infrastructures ---
    # type : coût de base (en multiple du poste de référence), durée de vie (mois),
    # effet unitaire, niveau maximal. ``cost_basis`` : "capacity" (coût fixe indexé),
    # "energy_bill" (mois de facture énergétique de l'acheteur), "revenue" (mois de CA).
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
    energy_materials_share: float = 0.40
    # amortissement mensuel des installations par secteur (défaut : productive_capacity_depreciation_rate)
    sector_capital_depreciation: dict = field(default_factory=lambda: {"energy": 0.010})
    tech_effect_per_level: float = 0.06
    tech_discount_rate: float = 0.01
    tech_investment_cash_share: float = 0.10
    # Loyer cible : rendement locatif brut mensuel du prix d'un logement
    # Logement : prix tiré vers les loyers capitalisés au coût d'usage
    # (taux hypothécaire annuel + prime de risque - croissance anticipée des loyers)
    housing_risk_premium: float = 0.02
    housing_rent_growth: float = 0.02
    housing_min_user_cost: float = 0.03         # coût d'usage minimal (prix ≤ 33 ans de loyers)
    housing_valuation_speed: float = 0.02       # rappel mensuel de la marge de promotion vers la valeur fondamentale
    rental_yield_weight: float = 0.05
    # Foncier : nouvelles parcelles ouvertes à la construction par mois et par logement existant
    land_expansion_rate: float = 0.004
    # lissage mensuel de l'indice des prix des intrants (renégociation lente des contrats)
    input_index_smoothing: float = 0.08
    # Achats de logements-patrimoine par les ménages (sans plafond de nombre)
    home_investment_cash_multiple: float = 1.5
    home_investment_probability: float = 0.03
    # --- Marché du travail par métiers ---
    trades: tuple = (
        "operateur", "technicien", "ingenieur", "developpeur", "gestionnaire",
        "commercial", "financier", "batiment", "extracteur", "agent",
    )
    # composition de l'offre de travail (part des ménages par métier)
    trade_supply_share: dict = field(
        default_factory=lambda: {
            "operateur": 0.33, "technicien": 0.13, "ingenieur": 0.05,
            "developpeur": 0.04, "gestionnaire": 0.10, "commercial": 0.09,
            "financier": 0.04, "batiment": 0.06, "extracteur": 0.10, "agent": 0.06,
        }
    )
    # mix de métiers par secteur (part des postes)
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
            "services": {"operateur": 0.34, "commercial": 0.20, "technicien": 0.14,
                         "gestionnaire": 0.12, "agent": 0.14, "batiment": 0.06},
            "bank": {"financier": 0.42, "gestionnaire": 0.18, "developpeur": 0.12,
                     "commercial": 0.16, "agent": 0.12},
        }
    )
    # coefficient de salaire de base par métier (rares et qualifiés mieux payés)
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
    profit_margin_target: float = 0.15
    # --- Configuration unique (préréglage « autonome stable » et État institutionnel, seule
    # gardée le 5 novembre 2026 ; docs/AUTONOMIE_STABLE.md, docs/ETAT.md) ---
    # Prix : chaque entreprise d'alimentation et de biens fixe son prix d'après ses signaux
    # (coût variable, stocks, demande non servie, concurrents ; devis de reprise sans
    # production possible) ; les indices de l'énergie et de la technologie suivent des signaux
    # de marché sans marge visée (economy/firm_pricing.py). Sensibilités mensuelles :
    firm_price_unmet_sensitivity: float = 0.04       # hausse par unité de demande non servie / ventes
    firm_price_inventory_sensitivity: float = 0.04   # baisse par unité d'excès de stock (en normes de stock)
    firm_price_competition_sensitivity: float = 0.06  # rapprochement mensuel du prix des concurrents
    firm_price_cost_sensitivity: float = 0.15        # remontée mensuelle vers le coût unitaire
    # Anticipations locales : chaque entreprise prévoit à partir de ce qu'elle observe (ses
    # ventes, ses logements occupés, ses coûts), avec des erreurs de prévision (economy/expectations.py)
    # Salaires : chaque entreprise relève ou baisse sa propre offre selon ses postes vacants, son
    # sureffectif et ses pertes, sans base ni borne, dans la limite de ce qu'elle peut payer ;
    # sans salarié, jamais sous le salaire des employeurs du secteur ; le salaire de référence
    # est la moyenne observée des offres
    wage_vacancy_speed: float = 0.03                 # variation mensuelle de l'offre par unité de postes vacants (ou en trop)
    # Modération salariale (novembre 2026, idée 1, economy/business.py) : sans poste vacant,
    # baisse mensuelle de l'offre par unité de part des ménages au chômage depuis au moins
    # ``long_term_unemployment_months`` mois ; recette lissée sous ``payroll_coverage_target`` ×
    # masse salariale : baisse par unité d'écart relatif de couverture, sans compter de postes
    # vacants. 0 neutralise chaque règle.
    wage_slack_speed: float = 0.03
    wage_concession_speed: float = 0.05
    long_term_unemployment_months: int = 12
    # Entreprises sans salarié (idée 2, EconomySimulation._restart_values / _dissolve_shells) :
    # reprise quand la valeur attendue d'un poste couvre son coût ; liquidation amiable (trésorerie
    # versée aux actionnaires) après ``shell_dissolution_months`` mois sans salarié ni perspective,
    # sans dette ni actif physique, jamais pour la dernière entreprise de son secteur. 0 = jamais.
    # ``shell_restart_on_value`` faux : ancienne règle de reprise (une embauche, trésorerie d'au
    # moins max(3 × salaire de référence, 2 × offre)).
    shell_dissolution_months: int = 24
    shell_restart_on_value: bool = True
    # Démographie (idée 3, economy/demography.py) : âge du chef de ménage, mortalité de Gompertz
    # (taux annuel = ``mortality_level`` × exp(``mortality_growth`` × âge en années) ; ≈ 0,2 % à
    # 40 ans, 1,5 % à 60 ans, 10 % à 80 ans), succession dans le ménage : l'héritier, jeune adulte
    # (``adult_age_years`` à dix ans de plus, comme un nouveau ménage), garde le patrimoine, le
    # logement et l'emploi du ménage, mais repart de sa propre compétence et de ses propres traits.
    # Droits de succession : taux de base × (1 + position « tranche supérieure » de la doctrine),
    # au-delà d'un abattement de ``estate_tax_allowance_years`` années de salaire de référence ;
    # payés sur la trésorerie, le reste dû est recouvré chaque mois sur la trésorerie au-delà de la
    # réserve de consommation. ``mortality_level`` = 0 neutralise toute la démographie.
    mortality_level: float = 0.00005
    mortality_growth: float = 0.095
    adult_age_years: float = 20.0
    estate_tax_rate: float = 0.20
    estate_tax_allowance_years: float = 10.0
    # Éducation publique (idée 5, economy/state_services.py) : un service de l'État scolarise la
    # génération suivante de chaque ménage (``pupils_per_agent`` ménages par agent) ; la qualité
    # de l'éducation est la couverture du service (plafonnée à 1), lissée sur
    # ``education_memory_months`` (une scolarité dure des années) ; un héritier ou un nouveau
    # ménage commence avec ``education_skill_gain`` × qualité de compétence en plus. Effet de
    # niveau, une fois par génération : aucune croissance composée. 0 neutralise l'effet ;
    # ``pupils_per_agent`` ≤ 0 supprime le service lui-même.
    education_skill_gain: float = 0.15
    pupils_per_agent: float = 30.0
    education_memory_months: float = 180.0
    # Demande de travail : un salarié de plus n'est embauché que tant qu'il rapporte (valeur de ce
    # qu'il fait vendre, net des intrants) au moins son salaire (``labor.update_workforce_targets``)
    labor_demand_elasticity: float = 2.0             # élasticité du besoin en personnel au rapport valeur / salaire, quand il est inférieur à 1
    # Demande B2B : élasticité-prix du volume d'énergie et de services technologiques que
    # les entreprises achètent (0 = volume fixé par une part du chiffre d'affaires ; > 1 = une
    # hausse de prix réduit la dépense de l'acheteur, un fournisseur ne peut plus fixer un prix
    # libre sans perdre de recettes)
    b2b_price_elasticity: float = 1.5
    # Investissement de capacité (alimentation, biens, R1) : l'entreprise ne compte que ses ventes
    # supplémentaires et les salaires qu'elle paie réellement, tranche par tranche, contre le coût
    # de ses fonds propres ; apport limité à la trésorerie au-delà de son fonds de roulement.
    # Entrée (R4) : l'entrepreneur voit aussi les commandes refusées ; un ménage peut fonder une
    # banque (R3) ; une banque sous exigence + coussin propose des actions nouvelles ; la capacité
    # de payer d'une banque compte le coût des seuls fonds propres que son activité exige.
    # Énergie (R8) : chaque producteur prévoit ses commandes, investit pour la demande prévue si
    # c'est rentable et dimensionne son personnel sur sa production prévue.
    # Calendrier du bénéfice : les ventes de capacité de production passent par les centrales
    # après la clôture ; le bénéfice qui fixe impôt, offre salariale et dividendes les omet
    # (défaut de comptes connu, docs/AUTONOMIE_STABLE.md § 10).
    # Consommation (R10) : budget sur le revenu attendu (moyenne lissée des revenus propres sur
    # ``income_memory_months``), trésorerie puisée seulement au-delà d'une réserve de précaution
    # (``precautionary_months`` × fréquence de ses mois sans emploi × revenu attendu × (1 −
    # tolérance au risque du ménage)).
    income_memory_months: float = 12.0               # horizon de mémoire des revenus et du chômage d'un ménage (R10)
    precautionary_months: float = 12.0               # mois de revenu exposés au risque de chômage observé, mis de côté (R10)
    # Réserve de trésorerie de la loi de finances : mois de dépenses prévues (economy/state_budget.py)
    public_reserve_months: float = 2.0
    # Règlement des achats aux secteurs sans livraison physique : seules les entreprises qui ont
    # des salariés encaissent, au prorata de leur effectif × productivité ; sans elles, la
    # commande n'est ni servie ni payée (l'acheteur garde son argent, la pénurie est mesurée).
    # Aucun plafond technique des prix (``PricingMixin._price_ceiling``).
    # Anticipation d'inflation commune : poids de la cible égal à la crédibilité de la banque
    # centrale, apprise de son historique (part lissée, à la vitesse ``credibility_speed``, des
    # mois où l'inflation sur 12 mois est restée à moins de ``credibility_band`` de la cible),
    # sans borne
    credibility_band: float = 0.02   # écart annuel toléré autour de la cible
    credibility_speed: float = 0.05  # lissage mensuel de la crédibilité
    # Coussin volontaire des banques : gain × taux de perte lissé sur le cycle
    # (``bank_cycle_smoothing``, mémoire d'environ huit ans)
    bank_cycle_smoothing: float = 0.01
    # Logements-patrimoine des ménages loués au loyer du marché après le parc des promoteurs.
    # Décisions des banques : la banque estime la probabilité de défaut et la perte en cas de
    # défaut de l'emprunteur, réduit ou refuse le prêt quand la perte attendue dépasse
    # ``bank_loss_tolerance``, ajoute la perte attendue et le coût du capital à la marge ; taux
    # de dépôt propre à chaque banque selon son besoin de liquidité
    # taux de défaut annuel que les banques observent (faillites d'entreprises / entreprises
    # actives, défauts de crédit à la consommation / emprunteurs, lissés) : valeur de départ
    # ici, remplacée chaque mois dans ``live`` par l'observation (``EconomySimulation``)
    observed_default_rate: float = 0.03
    observed_household_default_rate: float = 0.03
    default_rate_smoothing: float = 0.02             # lissage mensuel du taux de défaut observé
    default_risk_leverage: float = 3.0               # sensibilité du logit à dette / garanties (autour de 0,5)
    default_risk_loss_margin: float = 6.0            # sensibilité du logit à la marge négative
    default_risk_young: float = 0.7                  # hausse du logit pour une entreprise de moins de 24 mois
    observed_loss_given_default: float = 0.5         # perte réellement subie par les banques à la faillite d'un emprunteur (en part de l'encours), lissée ; valeur de départ, remplacée chaque mois dans ``live``
    default_recovery_cost: float = 0.10              # perte en cas de défaut même quand les garanties couvrent la dette (frais de recouvrement)
    bank_loss_tolerance: float = 0.06               # perte attendue annuelle (en part du prêt) au-delà de laquelle la banque refuse
    bank_buffer_loss_gain: float = 4.0               # coussin de fonds propres volontaire = gain × taux de perte annuel lissé sur le cycle
    # Taux de dépôt : chaque banque fixe le sien selon son besoin de liquidité, les ménages
    # déplacent une part de leurs dépôts vers les mieux rémunérées au-delà d'un coût de
    # changement (parts de marché, pas de comptes de dépôt par banque)
    deposit_liquidity_sensitivity: float = 0.0025    # hausse du taux mensuel offert par unité de manque de liquidité (0,3 point par an)
    deposit_switching_cost: float = 0.0002           # écart de taux mensuel sous lequel un déposant ne change pas de banque
    deposit_share_speed: float = 0.10                # part des dépôts qui se déplace chaque mois vers la cible
    deposit_share_sensitivity: float = 500.0         # poids de l'écart de taux mensuel (net du coût de changement) dans la part visée
    demand_forecast_error: float = 0.05              # écart-type de l'erreur multiplicative de prévision (anticipations locales)
    housing_vacancy_norm: float = 0.05               # réserve de logements vides visée par un promoteur
    seed: int = 7
    # --- État institutionnel (mission « refonte de l'État », docs/ETAT.md) : présidence et quatre
    # ministres nommés parmi les ménages, doctrines, objectifs, journal des décisions
    # (economy/institutions.py) ; seul modèle de l'État depuis le 5 novembre 2026 ---
    # transferts universels du programme d'un gouvernement du centre (part du PIB annuel retardé),
    # modulés par la doctrine comme l'ancien levier « transferts sociaux »
    institution_transfer_gdp_share: float = 0.05
    # Cabinet : une personne pressentie refuse si sa rémunération privée dépasse de plus de
    # ``cabinet_refusal_tolerance`` le salaire proposé ; chaque refus relève le salaire de
    # ``cabinet_refusal_raise`` à la révision annuelle suivante. Salaire de la présidence : haut de
    # la grille publique ou rémunération des dirigeants du privé moins ``cabinet_salary_discount``
    cabinet_refusal_tolerance: float = 0.25
    cabinet_refusal_raise: float = 0.05
    cabinet_salary_discount: float = 0.30
    minister_salary_ratio: float = 0.75       # salaire d'un ministre / présidence
    social_pay_ratio_cap: float = 8.0         # gouvernement social : présidence ≤ 8 × salaire minimum
    cabinet_reshuffle: bool = True            # remaniement à mi-mandat d'un objectif gravement manqué
    vote_record_weight: float = 0.4           # effet du bilan des objectifs sur le logit de réélection
    # Vote individuel des ménages (idée 4, novembre 2026, economy/institutions.individual_election) :
    # chaque ménage compare sa proximité au sortant et à l'opposition (médiane du camp opposé),
    # déplacée par son intérêt anticipé selon son rang de fortune, et juge son propre bilan :
    # évolution de son revenu réel attendu depuis le début du mandat, mois sans emploi récents.
    # Faux : ancien vote national sur l'indice de misère (``elect_orientation``).
    individual_voting: bool = True
    vote_interest_weight: float = 0.30        # glissement maximal de la position selon la fortune (± 15 points)
    vote_ideology_sensitivity: float = 8.0    # poids de l'écart de proximité (sortant − opposition) dans le logit
    vote_income_weight: float = 4.0           # poids de la croissance du revenu réel attendu pendant le mandat
    vote_unemployment_weight: float = 3.0     # poids de la fréquence récente des mois sans emploi du ménage
    vote_swing_sd: float = 0.03               # écart-type de l'écart national de la part des voix (un tirage)
    # secteurs critiques (politique industrielle, objectifs du ministre de l'Industrie)
    critical_sectors: tuple = ("energy", "bank", "food")
    # Loi de finances (economy/state_budget.py) : chaque loi rapproche programmes et impôts de la
    # doctrine puis corrige l'écart au solde voulu, d'au plus ``budget_program_speed`` (relatif)
    # par programme et ``budget_tax_speed`` par facteur d'impôt ; révision en cours d'année si le
    # chômage dépasse de ``budget_revision_unemployment`` celui de la loi ou si les recettes
    # tombent de ``budget_revision_revenue`` sous la prévision. Réserve de trésorerie :
    # ``public_reserve_months`` mois de dépenses prévues
    budget_program_speed: float = 0.25
    budget_tax_speed: float = 0.10
    budget_revision_unemployment: float = 0.03
    budget_revision_revenue: float = 0.10
    # Fonction publique (economy/state_services.py) : capacité d'un agent par service et par mois
    finance_files_per_agent: float = 60.0      # ménages (impôt, allocation) traités
    employment_cases_per_agent: float = 12.0   # chômeurs accompagnés
    training_places_per_agent: float = 1.0     # reconversions financées par mois
    works_per_agent: float = 6.0               # travaux suivis, en salaires de référence
    markets_per_agent: float = 3.0             # secteurs surveillés ou entreprises publiques suivies
    public_capital_maintenance_rate: float = 0.001  # entretien mensuel nécessaire (part du capital public)
    unmaintained_depreciation: float = 0.5     # usure en plus sans entretien (part de l'usure de base)
    # grille : coefficient par grade (1 à 3), part des postes de grades 2 et 3, ancienneté publique
    # minimale pour y accéder (mois), échelon d'ancienneté (+1 % par période de 24 mois, dix au plus),
    # hausse trimestrielle d'une prime de métier en vacance persistante, valeur de la stabilité de
    # l'emploi public dans le choix d'un salarié du privé
    public_grade_coefficients: tuple = (1.0, 1.25, 1.6)
    public_grade_shares: tuple = (0.35, 0.15)
    public_grade_tenure: tuple = (24, 60)
    public_seniority_step: float = 0.01
    public_premium_step: float = 0.03
    public_stability_value: float = 0.05
    # Salaire minimum institutionnel (economy/state_labor.py) : revalorisé chaque année par
    # l'indexation légale sur l'inflation des douze derniers mois, plus un coup de pouce vers
    # l'ambition de la doctrine (part du salaire médian du privé observé), d'au plus
    # ``minimum_wage_boost_cap`` par an ; jamais de baisse automatique. Un gouvernement non social
    # (jusqu'à 65/100) baisse par décision un minimum qui dépasse son ambition de plus de
    # ``minimum_wage_cut_margin`` du médian, au plus de ``minimum_wage_boost_cap`` par an
    minimum_wage_boost_cap: float = 0.10
    minimum_wage_cut_margin: float = 0.25
    # Politique industrielle (economy/state_industry.py). Problème de monopole : indice de Herfindahl
    # des ventes au-delà de ``concentration_margin_reference`` ET (marge au-delà de la marge de
    # référence du secteur × (1 + ``monopoly_margin_excess``) OU demande non servie au-delà de
    # ``monopoly_unmet_share``), sans entrant crédible, pendant ``monopoly_duration_months`` mois.
    # Instruction d'un dossier : ``case_work_months`` mois d'un service de la Concurrence au complet.
    # Une opération coûte au plus ``industry_operation_gdp_share`` du PIB annuel ; une cession met en
    # vente ``privatization_monthly_share`` de la participation par mois, jamais sous la valeur
    # fondamentale moins ``privatization_price_discount`` ; ``max_recapitalizations`` par entreprise
    monopoly_margin_excess: float = 0.5
    monopoly_unmet_share: float = 0.05
    monopoly_duration_months: int = 12
    case_work_months: float = 3.0
    industry_operation_gdp_share: float = 0.05
    privatization_monthly_share: float = 0.10
    privatization_price_discount: float = 0.10
    max_recapitalizations: int = 2

    def fresh_copy(self) -> "SimulationConfig":
        """Copie de travail du mois (``live``) : dictionnaires dupliqués sur deux niveaux.

        Équivaut à ``copy.deepcopy`` pour cette configuration (scalaires, tuples,
        dictionnaires de dictionnaires) mais beaucoup plus rapide : le moteur peut
        modifier ``live`` sans jamais toucher ``config``.
        """
        clone = copy.copy(self)
        for name, value in list(vars(clone).items()):
            if isinstance(value, dict):
                setattr(
                    clone,
                    name,
                    {k: dict(v) if isinstance(v, dict) else v for k, v in value.items()},
                )
        return clone

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
        ):
            setattr(self, field_name, max(0.01, getattr(self, field_name)))
        for field_name in (
            "income_tax_rate",
            "corporate_tax_rate",
            "dividend_payout_rate",
            "company_founder_probability",
            "competitive_pricing_strength",
            "payroll_coverage_target",
            "loss_wage_adjustment_strength",
            "investment_retention_rate",
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
            "natural_unemployment_rate",
            "demand_smoothing",
            "revenue_smoothing",
            "payroll_revenue_share",
            "government_spending_rate",
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
            "top_marginal_income_tax_rate",
            "inflation_target",
            "mortgage_down_payment_rate",
            "mortgage_payment_income_share",
            "mortgage_interest_rate",
            "monthly_population_growth_rate",
            "maximum_population_growth",
            "quality_preference_strength",
            "productive_capacity_depreciation_rate",
            "technology_knowledge_use_rate",
            "technology_price_growth",
            "technology_household_budget_share",
            "technology_household_savings_share",
            "technology_household_price_fraction",
            "technology_household_supply_share",
            "technology_household_level_gain",
            "technology_household_depreciation_rate",
            "founder_public_share_fraction",
            "founder_max_stake_share",
            "founder_stake_risk_weight",
        ):
            value = getattr(self, field_name)
            setattr(self, field_name, max(0.0, min(1.0, value)))
        self.food_stock_price = self.food_stock_price or self.starting_food_price
        self.goods_stock_price = self.goods_stock_price or self.starting_goods_price
        self.inflation_expectation_speed = max(0.0, min(1.0, self.inflation_expectation_speed))
        self.tax_loss_carry_months = max(0, self.tax_loss_carry_months)
        self.event_effect_size = max(0.0, min(0.5, self.event_effect_size))
        self.election_period_months = max(1, self.election_period_months)
        self.ipo_window_months = max(0, self.ipo_window_months)
        self.initial_political_orientation = max(
            0.0, min(100.0, self.initial_political_orientation)
        )
        self.policy_noise = max(0.0, min(1.0, self.policy_noise))
        self.policy_strength = max(0.0, min(2.0, self.policy_strength))
        self.max_monthly_layoffs = max(0, self.max_monthly_layoffs)
        self.max_monthly_hiring = max(1, self.max_monthly_hiring)
        self.working_capital_months = max(0.0, self.working_capital_months)
        self.quality_trait_variance = max(
            0.0, min(0.5, self.quality_trait_variance)
        )
        self.stock_min_discount = max(0.01, self.stock_min_discount)
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
    rented_homes: int = 0                     # logements-patrimoine loués le mois dernier
    rent_income_this_turn: float = 0.0        # loyers reçus ce mois (ménage bailleur)
    capital_income_last_month: float = 0.0    # dividendes et loyers du mois précédent (assiette de l'impôt)
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
    mortgage_arrears: int = 0
    consumer_debt: float = 0.0
    consumer_rate: float = 0.0
    consumer_arrears: int = 0
    bond_holdings: float = 0.0                  # obligations d'État détenues (valeur nominale)
    engel_food: float = 1.0                     # multiplicateur du budget alimentaire (loi d'Engel)
    engel_services: float = 1.0                 # multiplicateur du budget de services
    founded_company: bool = False
    entrepreneurial_spirit: float = 0.0
    trade: str = "operateur"
    job_trade: str = ""
    retraining_months: int = 0
    retraining_target: str = ""
    trade_wage_factor: float = 1.0
    wealth_history: list[float] = field(default_factory=list)
    financial_history: list[dict[str, float]] = field(default_factory=list)
    # R10 : revenu attendu (moyenne lissée de ses
    # revenus), fréquence lissée de ses mois sans emploi, mois observés
    expected_income: float = 0.0
    unemployment_risk: float = 0.0
    income_months: int = 0
    # mois consécutifs sans emploi (fin du marché du travail du mois ; idée 1, novembre 2026)
    unemployment_months: int = 0
    # démographie (idée 3) : âge du chef de ménage en mois ; génération (1 = fondatrice), incrémentée
    # à chaque succession ; patrimoine transmis à la dernière succession
    age_months: int = 0
    generation: int = 1
    inherited_wealth: float = 0.0


@dataclass
class ConstructionProject:
    """Contrat de chantier : la date ne remplace jamais le travail livré."""

    completion_month: int
    total_cost: float
    duration: int
    reference_cost: float = 0.0
    paid: float = 0.0
    work_done: float = 0.0  # fraction physique du logement

    @property
    def remaining_cost(self) -> float:
        return max(0.0, self.total_cost * (1.0 - self.work_done))


SHOCK_KINDS = ("taux", "energie", "tva", "depenses", "credit")


@dataclass
class Shock:
    """Choc exogène programmé, pour comparer une trajectoire à la même graine sans choc.

    ``kind`` : « taux » (écart annuel ajouté au taux directeur visé), « energie »
    (part de la capacité des centrales perdue), « tva » (points ajoutés au taux),
    « depenses » (part des dépenses publiques discrétionnaires supprimée), « credit »
    (points ajoutés à l'exigence de fonds propres des banques).
    """

    kind: str
    start: int
    months: int
    size: float

    def active(self, month: int) -> bool:
        return self.start <= month < self.start + self.months


@dataclass
class ResolutionEvent:
    """Une résolution et qui en supporte le coût.

    ``kind`` : « liquidation » (faillite d'entreprise), « restructure » (dernier
    acteur placé sous administration publique), « bank » (banque reprise par une
    concurrente), « bank_exit » (jeune banque solvable désignée par la faillite tirée
    au sort : boni versé à ses actionnaires, bilan repris par une concurrente),
    « bank_last » (dernière banque recapitalisée ou déposants mis à contribution).
    """

    month: int
    company_id: str
    kind: str
    debt_written_off: float = 0.0       # créances bancaires passées en perte
    public_cost: float = 0.0            # titres publics émis pour combler un trou
    depositor_loss: float = 0.0         # dépôts détruits (renflouement par les déposants)
    creditor_loss: float = 0.0          # pertes des prêteurs interbancaires
    absorbed_by_acquirer: float = 0.0   # perte absorbée par les fonds propres du repreneur
    shareholders_wiped: bool = False


class LoanAccounts(dict):
    """Comptes de prêts d'une banque (identifiant d'emprunteur -> encours).

    Un dictionnaire ordinaire qui compte ses modifications pour mémoriser la somme des
    encours positifs, que la capacité de crédit relit des centaines de fois par mois
    (profil : près de la moitié du temps de simulation avant ce cache). La somme est
    recalculée à l'identique (même expression, même ordre d'addition) dès que le
    contenu change, donc chaque flottant est celui qu'un calcul direct donnerait.
    """

    version = 0
    _book_version = -1
    _book = 0.0

    def __setitem__(self, key, value) -> None:
        self.version += 1
        super().__setitem__(key, value)

    def __delitem__(self, key) -> None:
        self.version += 1
        super().__delitem__(key)

    def __ior__(self, other):
        self.version += 1
        return super().__ior__(other)

    def clear(self) -> None:
        self.version += 1
        super().clear()

    def pop(self, *args):
        self.version += 1
        return super().pop(*args)

    def popitem(self):
        self.version += 1
        return super().popitem()

    def setdefault(self, *args):
        self.version += 1
        return super().setdefault(*args)

    def update(self, *args, **kwargs) -> None:
        self.version += 1
        super().update(*args, **kwargs)

    def positive_book(self) -> float:
        """Somme des encours positifs (``sum(max(0.0, v) for v in values())``)."""
        if self._book_version != self.version:
            self._book = sum(max(0.0, balance) for balance in self.values())
            self._book_version = self.version
        return self._book


def positive_loan_book(accounts) -> float:
    """Somme des encours positifs d'un dictionnaire de comptes, avec cache s'il est suivi."""
    if isinstance(accounts, LoanAccounts):
        return accounts.positive_book()
    return sum(max(0.0, balance) for balance in accounts.values())



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
    tax_loss_carryforward: float = 0.0
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
    secondary_asset_purchases: float = 0.0
    construction_spending: float = 0.0
    paid_worker_count: int = 0
    property_cost_sold_this_turn: float = 0.0
    depreciation_this_turn: float = 0.0
    interest_paid_this_turn: float = 0.0
    principal_paid_this_turn: float = 0.0
    default_loss: float = 0.0
    last_default_loss: float = 0.0              # default_loss au dernier mois (banque : pertes nouvelles = écart)
    # banque : pertes mensuelles / encours, lissées sur le cycle
    loss_rate_cycle: float = 0.0
    deposit_share: float = 0.0                  # banque : part de marché des dépôts (0 = non initialisée)
    deposit_rate_offer: float = 0.0             # banque : taux annuel offert aux déposants ce mois
    materials_supplied_this_turn: float = 0.0
    units_sold: float = 0.0
    units_produced: float = 0.0
    unmet_units: float = 0.0                    # demande adressée à l'entreprise non servie ce mois
    previous_units_sold: float = 0.0
    previous_unmet_units: float = 0.0
    potential_output: float = 0.0               # production possible du mois (travail et capacité, énergie livrée)
    unit_input_cost: float = 0.0                # intrants et matières par unité produite (dernière production)
    price_markup: float = -1.0                  # marge sur le coût variable ; < 0 : à initialiser
    # anticipations locales
    demand_forecast: float = 0.0                # prévision de demande du mois, erreur comprise
    expected_cost_growth: float = 0.0           # hausse mensuelle anticipée de ses coûts (ou des loyers)
    cost_reference: float = 0.0                 # indice de coût du mois précédent (pour la croissance observée)
    occupied_units: int = 0                     # logements loués le mois précédent (promoteurs)
    homes_sold: int = 0                         # logements vendus aux ménages ce mois (promoteurs)
    previous_homes_sold: int = 0
    unserved_rentals: int = 0                   # demandes de location reçues et non servies le mois dernier
    housing_demand_ema: float = 0.0             # ventes mensuelles de logements lissées du promoteur
    housing_gap: int = 0                        # logements à lancer selon sa propre prévision
    target_employees: int = 0
    staffing_need: int = 0
    demand_ema: float = 0.0
    revenue_ema: float = 0.0
    unmet_ema: float = 0.0
    stock_trade_volume: float = 0.0
    dividend_paid: float = 0.0
    debt: float = 0.0
    debt_interest_rate: float | None = None
    loan_spread: float = 0.0                    # marge moyenne des prêts au-dessus du taux directeur
    interbank_claims: dict[str, float] = field(default_factory=dict)  # prêts interbancaires (banque emprunteuse -> montant)
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
    construction_pipeline: list[ConstructionProject] = field(default_factory=list)
    financial_history: list[dict[str, float]] = field(default_factory=list)
    customer_revenue: dict[str, float] = field(default_factory=dict)
    supplier_spending: dict[str, float] = field(default_factory=dict)
    active: bool = True
    founder_id: str | None = None
    bank_loan_accounts: dict[str, float] = field(default_factory=LoanAccounts)
    bank_equity: float = 0.0
    bond_holdings: float = 0.0
    # revenus financiers du mois (dividendes reçus, intérêts d'obligations nets de
    # l'impôt retenu) : hors chiffre d'affaires et hors marge d'exploitation
    financial_income: float = 0.0
    # infrastructures énergétiques achetées (niveaux, avec usure)
    grid_level: float = 0.0
    storage_level: float = 0.0
    energy_bill: float = 0.0
    energy_bill_previous: float = 0.0
    energy_capacity: float = 0.0
    energy_available: float = 0.0
    energy_delivered: float = 0.0
    energy_requested: float = 0.0
    energy_received: float = 0.0                # énergie reçue ce mois (côté client)
    previous_energy_delivered: float = 0.0      # producteur : énergie livrée le mois écoulé (bilan complet)
    previous_energy_left: float = 0.0           # producteur : énergie disponible restée invendue le mois écoulé
    # prévision du producteur d'énergie : niveau et tendance lissés de ses
    # commandes, prévision du mois (erreur comprise), tendance observée de son prix
    energy_demand_level: float = 0.0
    energy_demand_trend: float = 0.0
    energy_demand_forecast: float = 0.0
    energy_price_trend: float = 0.0
    energy_price_reference: float = 0.0
    energy_fill: float = 1.0                    # part de l'énergie demandée livrée le mois précédent
    construction_work_available: float = 0.0
    energy_saving: float = 0.0
    infrastructure_sold_value: float = 0.0
    output_multiplier: float = 1.0
    exit_reason: str = ""
    exit_month: int = -1                        # mois de fermeture (faillite, rachat, résolution)
    idle_months: int = 0                        # mois consécutifs sans salarié (idée 2, novembre 2026)


def productivity_multiplier(company: "Company", config: SimulationConfig) -> float:
    """Multiplicateur de production : boosts technologiques et stockage d'énergie."""
    multiplier = (1.0 + min(company.tech_level, 10.0) * config.tech_effect_per_level) * (
        1.0 + config.public_capital_bonus
    ) * (1.0 + config.knowledge_bonus)
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
    household_technology_revenue: float = 0.0
    household_technology_units: float = 0.0
    recycled_materials: float = 0.0
    recycling_pending: float = 0.0
    # indice des prix (base 1) appliqué aux intrants non matériels des producteurs
    input_cost_index: float = 1.0
    # multiplicateur de prix des secteurs B2B (régulé par l'écart à la marge cible)
    tech_price_index: float = 1.0
    # prix des services technologiques achetés en B2B (intersector) : l'indice ci-dessus si la
    # demande B2B réagit au prix (``b2b_price_elasticity > 0``), neutre (1) sinon
    tech_service_price_index: float = 1.0
    # élasticité-prix du volume d'énergie et de services technologiques achetés en B2B
    # (copie mensuelle de ``SimulationConfig.b2b_price_elasticity``)
    b2b_price_elasticity: float = 0.0
    # commandes de services et B2B non servies du mois, par secteur fournisseur (« delivered »)
    refused_orders: dict = field(default_factory=dict)
    energy_price_index: float = 1.0
    boost_spending: float = 0.0
    knowledge_reference: float = 0.0
    knowledge_index: float = 1.0
    research_intensity: float = 0.0
    boost_value_created: float = 0.0
    tech_unmet_units: float = 0.0
    energy_supply: float = 0.0
    energy_delivered: float = 0.0
    energy_unmet: float = 0.0
    previous_energy_unmet: float = 0.0     # énergie non livrée le mois écoulé (bilan complet)
    energy_refused: float = 0.0            # commandes refusées faute d'offre ce mois (pas faute de budget)
    previous_energy_refused: float = 0.0   # idem, mois écoulé
    energy_unit_price: float = 1.0
    energy_capacity_multiplier: float = 1.0  # choc physique du mois, sans modification des paramètres


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
    investment: float = 0.0
    inventory_change: float = 0.0
    household_financial_services: float = 0.0
    loan_reconciliation_error: float = 0.0
    public_debt_reconciliation_error: float = 0.0
    energy_supply: float = 0.0
    energy_delivered: float = 0.0
    energy_unmet: float = 0.0
    construction_completed: int = 0
    restructurings: int = 0
    resolution_public_cost: float = 0.0
    resolution_depositor_loss: float = 0.0
    resolution_creditor_loss: float = 0.0
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
    # indicateurs d'inégalités et de logement (A3), calculés en fin de mois (economy.wealth)
    household_top1_share: float = 0.0           # part de la fortune des 1 % les plus riches
    income_gini: float = 0.0                    # Gini du revenu du mois (transferts compris, avant impôt)
    disposable_income_gini: float = 0.0         # Gini du revenu après impôt sur le revenu
    poverty_rate: float = 0.0                   # ménages sous 60 % du revenu disponible médian
    unhoused_households: int = 0                # ni propriétaires ni locataires
    housing_wealth_share: float = 0.0           # part du logement dans la fortune brute des ménages
    # novembre 2026, cinq idées (mesures seules, aucune règle ne les vise)
    long_term_unemployment: float = 0.0         # part des ménages sans emploi depuis 12 mois ou plus
    idle_companies: int = 0                     # entreprises actives sans salarié (hors banques)
    dissolutions: int = 0                       # liquidations amiables du mois
    deaths: int = 0                             # décès (successions) du mois
    estate_taxes: float = 0.0                   # droits de succession recouvrés ce mois
    average_age: float = 0.0                    # âge moyen des chefs de ménage (années)
    education_quality: float = 0.0              # qualité de l'éducation publique (couverture lissée)
    company_debt: float = 0.0
    active_companies: int = 0
    acquisitions: int = 0
    takeover_bids: int = 0                       # offres de prise de contrôle lancées (réussies ou non)
    corporate_bonds: float = 0.0                 # obligations d'État détenues par les entreprises non bancaires
    corporate_equity: float = 0.0                # valeur des actions détenues par des entreprises
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
    household_tax_revenue: float = 0.0
    policy_rate: float = 0.0
    mortgage_debt: float = 0.0
    consumer_debt: float = 0.0
    household_bonds: float = 0.0
    stock_market_value: float = 0.0
    foreclosures: int = 0
    consumer_defaults: int = 0
    keynesian_investment: float = 0.0
    price_controls: bool = False
    vat_revenue: float = 0.0
    vat_rate: float = 0.0
    household_services: float = 0.0
    services_price_index: float = 1.0
    food_budget_share: float = 0.0
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
    wage_reference: float = 0.0
    productivity_index: float = 1.0
    public_debt: float = 0.0
    public_deficit: float = 0.0
    public_spending: float = 0.0
    public_revenue: float = 0.0
    public_employment: int = 0
    tax_adjustment: float = 1.0
    public_capital: float = 0.0
    public_interest: float = 0.0
    government_orientation: float = 50.0
    minimum_wage: float = 0.0
    state_equity_value: float = 0.0
    state_equity_flow: float = 0.0
    state_equity_dividends: float = 0.0
    knowledge_stock: float = 0.0
    bank_liquidity: float = 0.0
    deposits: float = 0.0
    credit_capacity: float = 0.0
    cb_advances: float = 0.0
    ledger_residual: float = 0.0
    deposit_rate: float = 0.0
    tfp_index: float = 1.0
    research_spending: float = 0.0
    research_intensity: float = 0.0
