from __future__ import annotations

import streamlit as st

from economy.models import SimulationConfig
from economy.simulation import EconomySimulation, SECTORS
from economy.wealth import company_balance_sheet, company_valuation_metrics


st.set_page_config(
    page_title="Économie en mouvement",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --ink: #17243b;
        --muted: #64748b;
        --line: #e4e9f1;
        --paper: #f5f7fb;
        --blue: #315ee8;
        --mint: #dff5eb;
    }
    [data-testid="stAppViewContainer"] { background: var(--paper); color: var(--ink); }
    [data-testid="stHeader"] { background: rgba(245,247,251,.94); }
    [data-testid="stSidebar"] { background: #fff; border-right: 1px solid var(--line); }
    .block-container { max-width: 1440px; padding-top: 1.8rem; padding-bottom: 3rem; }
    h1, h2, h3 { color: var(--ink); letter-spacing: -.025em; }
    h1 { font-weight: 760; }
    [data-testid="stCaptionContainer"] { color: var(--muted); }
    [data-testid="stMetric"] {
        background: #fff; border: 1px solid var(--line); border-radius: 14px;
        padding: 16px 18px; box-shadow: 0 3px 12px rgba(23,36,59,.035);
    }
    [data-testid="stMetricLabel"] { color: var(--muted); font-size: .82rem; }
    [data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] div {
        white-space: normal; overflow: visible; text-overflow: clip; line-height: 1.25;
    }
    [data-testid="stMetricValue"] { color: var(--ink); font-weight: 730; }
    [data-testid="stMetricValue"], [data-testid="stMetricValue"] div {
        white-space: normal; overflow: visible; text-overflow: clip;
        font-size: clamp(1.15rem, 1.7vw, 1.65rem); word-break: break-word;
    }
    [data-testid="stMetricDelta"], [data-testid="stMetricDelta"] div {
        white-space: normal; overflow: visible; text-overflow: clip;
    }
    [data-testid="stMetric"] { min-height: 108px; }
    [data-testid="stTabs"] button { font-weight: 650; }
    [data-testid="stTabs"] button[aria-selected="true"] { color: var(--blue); }
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: var(--line); border-radius: 14px; background: #fff;
    }
    [data-testid="stAlert"] { border-radius: 12px; }
    [data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 12px; background: #fff; }
    div.stButton > button[kind="primary"] {
        background: var(--blue); border-color: var(--blue); border-radius: 10px;
        font-weight: 700; min-height: 2.8rem;
    }
    div.stButton > button { border-radius: 10px; }
    .eyebrow {
        color: var(--blue); text-transform: uppercase; letter-spacing: .12em;
        font-size: .74rem; font-weight: 750; margin-bottom: .35rem;
    }
    .month-pill {
        display: inline-block; border-radius: 999px; padding: 8px 13px;
        background: var(--mint); color: #176844; font-size: .88rem; font-weight: 700;
    }
    .section-note { color: var(--muted); margin-top: -.5rem; margin-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def compact(value: float, unit: str = "") -> str:
    """Grands nombres lisibles : 12,3 k, 1,2 M, 3,4 Md (valeur complète au survol)."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    for threshold, suffix in ((1e9, " Md"), (1e6, " M"), (1e4, " k")):
        if magnitude >= threshold:
            text = f"{magnitude / threshold:.1f}".replace(".", ",")
            return f"{sign}{text}{suffix}{unit}"
    return f"{sign}{magnitude:,.0f}".replace(",", " ") + unit


def new_simulation() -> EconomySimulation:
    return EconomySimulation(
        SimulationConfig(
            population_size=st.session_state.population_size,
            companies_per_sector=st.session_state.companies_per_sector,
            initial_company_cash=st.session_state.initial_company_cash,
            initial_person_cash=st.session_state.initial_person_cash,
            raw_materials=st.session_state.raw_materials,
            land_units=st.session_state.land_units,
            starting_food_price=st.session_state.starting_food_price,
            starting_goods_price=st.session_state.starting_goods_price,
            starting_rent=st.session_state.starting_rent,
            starting_wage=st.session_state.starting_wage,
            food_input_cost=st.session_state.food_input_cost,
            goods_input_cost=st.session_state.goods_input_cost,
            raw_material_unit_price=st.session_state.raw_material_unit_price,
            raw_material_extraction_cost=st.session_state.raw_material_extraction_cost,
            raw_material_extraction_productivity=st.session_state.raw_material_extraction_productivity,
            material_recovery_rate=st.session_state.material_recovery_percent / 100,
            food_income_budget_share=st.session_state.food_income_budget_percent / 100,
            food_cash_draw_rate=st.session_state.food_cash_draw_percent / 100,
            goods_income_budget_share=st.session_state.goods_income_budget_percent / 100,
            goods_cash_draw_rate=st.session_state.goods_cash_draw_percent / 100,
            food_productivity=st.session_state.food_productivity,
            goods_productivity=st.session_state.goods_productivity,
            target_employment_rate=st.session_state.target_employment_percent / 100,
            technology_depreciation_rate=st.session_state.technology_depreciation_percent / 100,
            income_tax_rate=st.session_state.income_tax_rate,
            corporate_tax_rate=st.session_state.corporate_tax_rate_percent / 100,
            unemployment_benefit=st.session_state.unemployment_benefit,
            loan_interest_rate=st.session_state.loan_interest_percent / 100,
            credit_risk_spread=st.session_state.credit_risk_spread_percent / 100,
            maximum_loan_leverage=st.session_state.maximum_loan_leverage,
            acquisition_probability=st.session_state.acquisition_probability,
            merger_size_ratio=st.session_state.merger_size_ratio,
            startup_threshold=st.session_state.startup_threshold,
            startup_investment_fraction=st.session_state.startup_investment_percent / 100,
            startup_failure_probability=st.session_state.startup_failure_percent / 100,
            company_founder_probability=st.session_state.startup_founder_percent / 100,
            founder_public_share_fraction=st.session_state.founder_public_shares_percent / 100,
            dividend_payout_rate=st.session_state.dividend_payout_percent / 100,
            investment_retention_rate=st.session_state.investment_retention_percent / 100,
            competitive_pricing_strength=st.session_state.competitive_pricing_percent / 100,
            housing_build_cost=st.session_state.housing_build_cost,
            housing_price=st.session_state.housing_price,
            build_time_months=st.session_state.build_time_months,
            random_promotion_strength=st.session_state.random_promotion_strength,
            bank_initial_reserves=st.session_state.bank_initial_reserves,
            trait_variance=st.session_state.trait_variance,
            executive_bonus_rate=st.session_state.executive_bonus_percent / 100,
            ceo_term_months=st.session_state.ceo_term_months,
            bankruptcy_months=st.session_state.bankruptcy_months,
            inventory_spoilage_rate=st.session_state.inventory_spoilage_percent / 100,
            target_inventory_months=st.session_state.target_inventory_months,
            market_price_ceiling=st.session_state.market_price_ceiling,
            stock_book_value_weight=st.session_state.stock_book_value_weight_percent / 100,
            stock_earnings_multiple=st.session_state.stock_earnings_multiple,
            stock_adjustment_speed=st.session_state.stock_adjustment_speed_percent / 100,
            cpi_food_weight=st.session_state.cpi_food_weight_percent / 100,
            cpi_goods_weight=st.session_state.cpi_goods_weight_percent / 100,
            cpi_rent_weight=st.session_state.cpi_rent_weight_percent / 100,
            wage_productivity_pass_through=st.session_state.wage_productivity_pass_percent / 100,
            wage_profitability_sensitivity=st.session_state.wage_profit_sensitivity_percent / 100,
            wage_tension_sensitivity=st.session_state.wage_tension_percent / 100,
            natural_unemployment_rate=st.session_state.natural_unemployment_percent / 100,
            job_separation_rate=st.session_state.job_separation_percent / 100,
            working_capital_months=st.session_state.working_capital_months,
            excess_cash_payout_rate=st.session_state.excess_cash_payout_percent / 100,
            credit_financed_share=st.session_state.credit_financed_percent / 100,
            government_spending_rate=st.session_state.government_spending_percent / 100,
            profit_margin_target=st.session_state.profit_margin_target_percent / 100,
            bank_excess_payout_rate=st.session_state.bank_excess_payout_percent / 100,
            monetary_trend=st.session_state.monetary_trend_permille / 1000,
            monetary_inflation_gain=st.session_state.monetary_inflation_gain,
            wage_inflation_pass_base=st.session_state.wage_inflation_pass_percent / 100,
            executive_wage_multiplier=st.session_state.executive_pay_multiple,
            skill_wage_slope=st.session_state.skill_wage_slope_percent / 100,
            entrepreneurial_trait_threshold=1.0 - st.session_state.entrepreneurial_share_percent / 100,
            reserve_replacement_rate=st.session_state.reserve_replacement_percent / 100,
            unemployment_benefit_cycle_sensitivity=st.session_state.benefit_cycle_sensitivity_percent / 100,
            labor_mobility_premium=st.session_state.labor_mobility_premium_percent / 100,
            household_rent_income_share=st.session_state.household_rent_share_percent / 100,
            rent_affordability_weight=st.session_state.rent_affordability_weight_percent / 100,
            housing_maintenance_rate=st.session_state.housing_maintenance_percent / 100,
            inventory_holding_cost_rate=st.session_state.inventory_holding_cost_percent / 100,
            household_propensity_variance=st.session_state.household_propensity_variance_percent / 100,
            consumer_price_elasticity=st.session_state.consumer_price_elasticity,
            inventory_discount_strength=st.session_state.inventory_discount_percent / 100,
            supplier_wage_payout_rate=st.session_state.supplier_wage_payout_percent / 100,
            income_tax_threshold=st.session_state.income_tax_threshold,
            top_marginal_income_tax_rate=st.session_state.top_marginal_income_tax_percent / 100,
            inflation_target=st.session_state.inflation_target_percent / 100,
            policy_rate_inflation_sensitivity=st.session_state.policy_inflation_sensitivity_percent / 100,
            policy_rate_unemployment_sensitivity=st.session_state.policy_unemployment_sensitivity_percent / 100,
            mortgage_down_payment_rate=st.session_state.mortgage_down_payment_percent / 100,
            mortgage_payment_income_share=st.session_state.mortgage_payment_income_percent / 100,
            mortgage_interest_rate=st.session_state.mortgage_interest_percent / 100,
            mortgage_term_months=st.session_state.mortgage_term_years * 12,
            monthly_population_growth_rate=st.session_state.population_growth_percent / 100,
            maximum_population_growth=st.session_state.maximum_population_growth_percent / 100,
            quality_trait_variance=st.session_state.quality_trait_variance_percent / 100,
            quality_preference_strength=st.session_state.quality_preference_percent / 100,
            capacity_expansion_reinvestment_rate=st.session_state.capacity_reinvestment_percent / 100,
            productive_capacity_unit_cost=st.session_state.capacity_unit_cost,
            technology_productivity=st.session_state.technology_productivity,
            technology_price_growth=st.session_state.technology_price_growth_percent / 100,
            technology_business_budget_share=st.session_state.technology_business_budget_percent / 100,
            technology_business_profit_share=st.session_state.technology_business_profit_percent / 100,
            technology_business_max_level=st.session_state.technology_business_max_level,
            technology_household_budget_share=st.session_state.household_technology_budget_percent / 100,
            household_stock_investment_rate=st.session_state.household_investment_percent / 100,
            wage_productivity_share=st.session_state.wage_productivity_percent / 100,
            manager_wage_multiplier=st.session_state.manager_wage_multiplier / 100,
            specialist_wage_multiplier=st.session_state.specialist_wage_multiplier / 100,
            seed=st.session_state.seed,
        )
    )


with st.sidebar:
    st.markdown('<div class="eyebrow">Laboratoire économique</div>', unsafe_allow_html=True)
    st.header("Réglages")
    st.caption("Modifiez les conditions de départ, puis réinitialisez pour les appliquer.")
    with st.expander("Population & entreprises", expanded=True):
        st.number_input("Ménages", min_value=10, max_value=500, value=240, step=10, key="population_size")
        st.number_input("Entreprises par secteur", min_value=1, max_value=10, value=3, key="companies_per_sector")
        st.number_input(
            "Trésorerie initiale des entreprises (€)",
            min_value=10000.0,
            max_value=2000000.0,
            value=100000.0,
            step=10000.0,
            key="initial_company_cash",
        )
        st.number_input(
            "Épargne initiale par ménage (€)",
            min_value=0.0,
            max_value=50000.0,
            value=3000.0,
            step=500.0,
            key="initial_person_cash",
        )
        st.slider(
            "Objectif d'emplois",
            min_value=30,
            max_value=95,
            value=60,
            step=5,
            format="%d %%",
            key="target_employment_percent",
            help="Cible initiale des postes proposés. Les postes ajoutés au socle des secteurs vont à l'immobilier ; l'embauche reste limitée par la trésorerie et la rentabilité.",
        )
        st.slider(
            "Diversité des habitudes de consommation",
            min_value=0,
            max_value=80,
            value=25,
            format="%d %%",
            key="household_propensity_variance_percent",
            help="Fait varier la propension à consommer entre les ménages plutôt que d'imposer un panier identique.",
        )
        st.slider(
            "Croissance démographique mensuelle",
            min_value=0.0,
            max_value=0.5,
            value=0.1,
            step=0.05,
            format="%.2f %%",
            key="population_growth_percent",
            help="Ajoute graduellement de nouveaux ménages sans patrimoine initial.",
        )
        st.slider(
            "Plafond de croissance de la population",
            min_value=0,
            max_value=100,
            value=50,
            format="%d %%",
            key="maximum_population_growth_percent",
            help="Limite la croissance maximale par rapport à la population de départ.",
        )
    with st.expander("Ressources & prix", expanded=False):
        st.number_input("Matières premières", min_value=0.0, max_value=1000000.0, value=300000.0, step=10000.0, key="raw_materials")
        st.number_input("Terrains disponibles", min_value=5, max_value=5000, value=250, step=10, key="land_units")
        st.number_input("Salaire de référence mensuel (€)", min_value=500.0, max_value=5000.0, value=1400.0, step=100.0, key="starting_wage")
        st.number_input("Prix alimentaire initial (€)", min_value=10.0, max_value=500.0, value=120.0, step=10.0, key="starting_food_price")
        st.number_input("Prix des biens initiaux (€)", min_value=10.0, max_value=500.0, value=100.0, step=10.0, key="starting_goods_price")
        st.number_input("Coût des intrants alimentaires (€ / unité)", min_value=0.0, max_value=200.0, value=65.0, step=5.0, key="food_input_cost")
        st.number_input("Coût des intrants des biens (€ / unité)", min_value=0.0, max_value=200.0, value=42.0, step=5.0, key="goods_input_cost")
        st.number_input("Prix des matières premières (€ / unité)", min_value=0.0, max_value=100.0, value=12.0, step=1.0, key="raw_material_unit_price")
        st.number_input("Coût d'extraction (€ / unité)", min_value=0.0, max_value=100.0, value=0.5, step=0.5, key="raw_material_extraction_cost")
        st.number_input("Extraction par salarié et par mois", min_value=1.0, max_value=2000.0, value=250.0, step=25.0, key="raw_material_extraction_productivity")
        st.slider(
            "Part des matières récupérées après consommation",
            min_value=0,
            max_value=99,
            value=90,
            format="%d %%",
            key="material_recovery_percent",
            help="Les matières récupérées redeviennent disponibles le mois suivant ; elles restent distinctes des réserves vierges.",
        )
        st.number_input("Production alimentaire par salarié", min_value=1.0, max_value=100.0, value=24.0, step=1.0, key="food_productivity")
        st.number_input("Production de biens par salarié", min_value=1.0, max_value=100.0, value=24.0, step=1.0, key="goods_productivity")
        st.slider("Amortissement mensuel de la technologie", min_value=0, max_value=20, value=2, format="%d %%", key="technology_depreciation_percent")
        st.number_input("Loyer initial mensuel (€)", min_value=100.0, max_value=3000.0, value=700.0, step=100.0, key="starting_rent")
        st.number_input("Plafond des prix courants (€)", min_value=100.0, max_value=10000.0, value=350.0, step=50.0, key="market_price_ceiling")
        st.number_input("Coût de construction d'un logement (€)", min_value=10000.0, max_value=300000.0, value=70000.0, step=10000.0, key="housing_build_cost")
        st.number_input("Valeur d'un logement (€)", min_value=20000.0, max_value=600000.0, value=120000.0, step=10000.0, key="housing_price")
        st.slider(
            "Apport minimal du ménage",
            min_value=5,
            max_value=50,
            value=20,
            format="%d %%",
            key="mortgage_down_payment_percent",
        )
        st.slider(
            "Mensualité maximale / revenu",
            min_value=10,
            max_value=60,
            value=30,
            format="%d %%",
            key="mortgage_payment_income_percent",
        )
        st.slider(
            "Taux mensuel des prêts immobiliers",
            min_value=0.0,
            max_value=2.0,
            value=0.4,
            step=0.1,
            format="%.1f %%",
            key="mortgage_interest_percent",
        )
        st.slider(
            "Durée du prêt immobilier (années)",
            min_value=5,
            max_value=40,
            value=30,
            key="mortgage_term_years",
        )
        st.slider("Délai de construction (mois)", min_value=1, max_value=18, value=6, key="build_time_months")
        st.slider(
            "Entretien mensuel des logements",
            min_value=0.0,
            max_value=2.0,
            value=0.12,
            step=0.01,
            format="%.2f %%",
            key="housing_maintenance_percent",
            help="Charge payée pour entretenir chaque logement détenu, loué ou vacant.",
        )
        st.slider(
            "Coût mensuel de stockage des stocks",
            min_value=0.0,
            max_value=2.0,
            value=0.2,
            step=0.1,
            format="%.1f %%",
            key="inventory_holding_cost_percent",
            help="Coût de financement et d'entreposage calculé sur la valeur des stocks invendus.",
        )
    with st.expander("Politique publique & hasard", expanded=False):
        st.slider("Impôt sur le revenu", min_value=0, max_value=30, value=8, format="%d %%", key="income_tax_percent")
        st.number_input(
            "Seuil mensuel avant tranche supérieure (€)",
            min_value=0.0,
            max_value=20000.0,
            value=3000.0,
            step=250.0,
            key="income_tax_threshold",
        )
        st.slider(
            "Taux marginal au-dessus du seuil",
            min_value=8,
            max_value=50,
            value=8,
            format="%d %%",
            key="top_marginal_income_tax_percent",
        )
        st.slider("Impôt sur les bénéfices", min_value=0, max_value=35, value=12, format="%d %%", key="corporate_tax_rate_percent")
        st.number_input("Allocation chômage mensuelle (€)", min_value=0.0, max_value=2000.0, value=300.0, step=50.0, key="unemployment_benefit")
        st.slider(
            "Part des recettes du secteur amont reversée en salaires",
            min_value=0,
            max_value=100,
            value=100,
            format="%d %%",
            key="supplier_wage_payout_percent",
            help="Le secteur amont agrégé (intrants, construction, entretien, recherche, investissement) reverse ses recettes du mois en salaires à des chômeurs : l'emploi suit l'activité, il n'y a pas de fonds épargné. Si plus aucun chômeur n'est disponible, le surplus est versé aux ménages actionnaires.",
        )
        st.number_input("Graine aléatoire", min_value=0, max_value=999999, value=7, key="seed")
    with st.expander("Concurrence, dette & entreprises", expanded=False):
        st.slider("Pression concurrentielle sur les prix", min_value=0, max_value=20, value=6, format="%d %%", key="competitive_pricing_percent")
        st.slider(
            "Sensibilité des clients aux écarts de prix",
            min_value=0.0,
            max_value=5.0,
            value=1.5,
            step=0.25,
            key="consumer_price_elasticity",
        )
        st.slider(
            "Rabais maximal pour les stocks excédentaires",
            min_value=0,
            max_value=30,
            value=8,
            format="%d %%",
            key="inventory_discount_percent",
        )
        st.slider(
            "Hausse salariale quand le chômage passe sous son niveau naturel",
            min_value=0,
            max_value=20,
            value=4,
            format="%d %%",
            key="wage_tension_percent",
            help="Sensibilité de la référence salariale à (chômage naturel − chômage observé). Aucune indexation sur l'IPC.",
        )
        st.slider(
            "Chômage naturel",
            min_value=1,
            max_value=15,
            value=5,
            format="%d %%",
            key="natural_unemployment_percent",
        )
        st.slider(
            "Séparations d'emploi mensuelles (chômage frictionnel)",
            min_value=0,
            max_value=10,
            value=4,
            format="%d %%",
            key="job_separation_percent",
            help="Part des salariés qui quittent ou perdent leur poste chaque mois et cherchent un mois avant d'être réembauchés.",
        )
        st.slider("Taux mensuel du crédit", min_value=0.0, max_value=2.0, value=0.4, step=0.1, format="%.1f %%", key="loan_interest_percent")
        st.slider(
            "Inflation mensuelle visée par la banque",
            min_value=0.0,
            max_value=1.0,
            value=0.2,
            step=0.1,
            format="%.1f %%",
            key="inflation_target_percent",
            help="Alimente aussi la tendance nominale des prix alimentaires et des biens à hauteur de 50 % de cette cible. Les chocs de demande peuvent relever temporairement les prix ; une offre excédentaire peut les faire baisser.",
        )
        st.slider(
            "Réaction du taux à l'inflation",
            min_value=0,
            max_value=20,
            value=4,
            format="%d %%",
            key="policy_inflation_sensitivity_percent",
        )
        st.slider(
            "Réaction du taux au chômage",
            min_value=0.0,
            max_value=2.0,
            value=0.4,
            step=0.1,
            format="%.1f %%",
            key="policy_unemployment_sensitivity_percent",
        )
        st.slider(
            "Marge de crédit liée au risque",
            min_value=0.0,
            max_value=1.0,
            value=0.25,
            step=0.05,
            format="%.2f %%",
            key="credit_risk_spread_percent",
            help="Ajout au taux de base selon le risque choisi par le dirigeant et son expérience.",
        )
        st.slider("Levier de dette maximal", min_value=0.5, max_value=6.0, value=3.0, step=0.5, key="maximum_loan_leverage")
        st.number_input("Réserves initiales de la banque (€)", min_value=0.0, max_value=50000000.0, value=7000000.0, step=500000.0, key="bank_initial_reserves")
        st.slider("Probabilité mensuelle d'acquisition", min_value=0.0, max_value=25.0, value=0.4, step=0.1, format="%.1f %%", key="acquisition_probability_percent", help="Seule une entreprise plus efficace (marge supérieure) absorbe un rival ; aucun plafond de part de marché.")
        st.slider(
            "Poids de la valeur comptable en Bourse",
            min_value=0,
            max_value=100,
            value=55,
            format="%d %%",
            key="stock_book_value_weight_percent",
            help="Le reste de l'estimation combine bénéfices observés et perspectives de chiffre d'affaires.",
        )
        st.number_input(
            "Multiple des bénéfices anticipés",
            min_value=0.0,
            max_value=30.0,
            value=7.0,
            step=0.5,
            key="stock_earnings_multiple",
        )
        st.slider(
            "Vitesse de convergence du cours",
            min_value=1,
            max_value=50,
            value=18,
            format="%d %%",
            key="stock_adjustment_speed_percent",
        )
        st.number_input("Seuil de taille pour une fusion", min_value=1.0, max_value=10.0, value=2.0, step=0.5, key="merger_size_ratio")
        st.number_input("Seuil de capital pour créer une entreprise (€)", min_value=25000.0, max_value=2000000.0, value=100000.0, step=25000.0, key="startup_threshold")
        st.slider("Part de l'épargne investie au démarrage", min_value=10, max_value=90, value=65, format="%d %%", key="startup_investment_percent")
        st.slider("Échec des jeunes entreprises (par an)", min_value=0, max_value=80, value=30, format="%d %%", key="startup_failure_percent")
        st.slider("Probabilité mensuelle de lancement", min_value=0, max_value=50, value=8, format="%d %%", key="startup_founder_percent")
        st.slider("Part des ménages à l'esprit entrepreneurial", min_value=0, max_value=100, value=25, format="%d %%", key="entrepreneurial_share_percent", help="Seuls ces ménages (et seulement s'ils ont le capital) peuvent créer une entreprise ; la décision dépend de la rentabilité du secteur.")
        st.slider("Mois de pertes avant risque de faillite", min_value=1, max_value=24, value=6, key="bankruptcy_months")
    with st.expander("Investissement, salaires & compétences", expanded=False):
        st.slider("Bénéfices versés en dividendes", min_value=0, max_value=50, value=12, format="%d %%", key="dividend_payout_percent")
        st.slider("Actions mises en vente à la création", min_value=5, max_value=50, value=25, format="%d %%", key="founder_public_shares_percent", help="Le fondateur conserve les autres actions. Les achats d'actions nouvellement émises alimentent directement la trésorerie de la jeune entreprise.")
        st.slider("Rétention moyenne des bénéfices", min_value=0, max_value=100, value=8, format="%d %%", key="investment_retention_percent")
        st.slider("Bonus des employés sur les bénéfices", min_value=0, max_value=20, value=3, format="%d %%", key="executive_bonus_percent")
        st.slider(
            "Investissement boursier des ménages",
            min_value=0,
            max_value=20,
            value=15,
            format="%d %%",
            key="household_investment_percent",
            help="Part des liquidités disponibles au-delà d'une réserve de demi-salaire investie chaque mois. Une participation plus large permet aux ménages de recevoir une part des dividendes.",
        )
        st.slider("Part du salaire liée à la productivité", min_value=0, max_value=100, value=35, format="%d %%", key="wage_productivity_percent")
        st.slider("Salaire des managers (× salaire d'entrée)", min_value=100, max_value=400, value=240, format="%d %%", key="manager_wage_multiplier")
        st.slider("Salaire des spécialistes seniors (× salaire d'entrée)", min_value=100, max_value=300, value=160, format="%d %%", key="specialist_wage_multiplier")
        st.slider("Variabilité des traits d'entreprise", min_value=0.0, max_value=0.5, value=0.16, step=0.02, key="trait_variance")
        st.slider(
            "Variabilité de qualité des produits",
            min_value=0,
            max_value=50,
            value=20,
            format="%d %%",
            key="quality_trait_variance_percent",
        )
        st.slider(
            "Importance de la qualité dans le choix des ménages",
            min_value=0,
            max_value=100,
            value=100,
            format="%d %%",
            key="quality_preference_percent",
        )
        st.slider(
            "Bénéfice réinvesti en capacité de production",
            min_value=0,
            max_value=60,
            value=10,
            format="%d %%",
            key="capacity_reinvestment_percent",
            help="Investissement activé pour les producteurs rentables quand la demande dépasse l'offre ou que la main-d'œuvre manque ; une part est financée par crédit bancaire.",
        )
        st.number_input(
            "Coût d'une unité de capacité mensuelle (€)",
            min_value=250.0,
            max_value=25000.0,
            value=2500.0,
            step=250.0,
            key="capacity_unit_cost",
        )
        st.number_input(
            "Solutions tech produites par salarié/mois",
            min_value=0.5,
            max_value=20.0,
            value=3.0,
            step=0.5,
            key="technology_productivity",
        )
        st.slider(
            "Part du revenu consacrée aux outils numériques",
            min_value=0,
            max_value=15,
            value=5,
            format="%d %%",
            key="household_technology_budget_percent",
        )
        st.slider(
            "Hausse du prix des niveaux tech avancés",
            min_value=0,
            max_value=100,
            value=25,
            format="%d %%",
            key="technology_price_growth_percent",
        )
        st.slider(
            "Part de trésorerie maximale pour la technologie d'entreprise",
            min_value=0,
            max_value=5,
            value=3,
            format="%d %% / mois",
            key="technology_business_budget_percent",
        )
        st.slider(
            "Part du bénéfice réinvestissable en technologie",
            min_value=0,
            max_value=50,
            value=20,
            format="%d %%",
            key="technology_business_profit_percent",
        )
        st.slider(
            "Niveau technologique maximal des entreprises",
            min_value=1.0,
            max_value=10.0,
            value=5.0,
            step=0.5,
            key="technology_business_max_level",
        )
        st.slider("Aléa dans les promotions de CEO", min_value=0.0, max_value=1.0, value=0.25, step=0.05, key="random_promotion_strength")
        st.slider("Durée du mandat du CEO (mois)", min_value=3, max_value=48, value=12, key="ceo_term_months")
        st.slider("Pertes de stock mensuelles", min_value=0, max_value=25, value=4, format="%d %%", key="inventory_spoilage_percent")
        st.slider(
            "Stock cible (mois de demande)",
            min_value=0.0,
            max_value=3.0,
            value=1.5,
            step=0.25,
            key="target_inventory_months",
            help="Les producteurs essaient de garder un tampon de stocks pour éviter les ruptures et les envolées persistantes de prix.",
        )
    with st.expander("Ménages & indicateurs macroéconomiques", expanded=False):
        st.slider(
            "Budget alimentaire pris sur le revenu",
            min_value=0,
            max_value=80,
            value=32,
            format="%d %%",
            key="food_income_budget_percent",
        )
        st.slider(
            "Épargne alimentaire mobilisée",
            min_value=0,
            max_value=50,
            value=12,
            format="%d %%",
            key="food_cash_draw_percent",
        )
        st.slider(
            "Budget biens courants pris sur le revenu",
            min_value=0,
            max_value=80,
            value=22,
            format="%d %%",
            key="goods_income_budget_percent",
        )
        st.slider(
            "Épargne mobilisée pour les biens",
            min_value=0,
            max_value=50,
            value=10,
            format="%d %%",
            key="goods_cash_draw_percent",
        )
        st.caption("Pondérations de l'indice des prix à la consommation")
        st.slider("Alimentation dans l'indice", 0, 100, 45, format="%d %%", key="cpi_food_weight_percent")
        st.slider("Biens courants dans l'indice", 0, 100, 30, format="%d %%", key="cpi_goods_weight_percent")
        st.slider("Loyers dans l'indice", 0, 100, 25, format="%d %%", key="cpi_rent_weight_percent")
        st.slider(
            "Transmission de la productivité aux salaires",
            min_value=0,
            max_value=100,
            value=60,
            format="%d %%",
            key="wage_productivity_pass_percent",
            help="Part de la croissance lissée de la productivité (compétence, technologie, capital) reversée au salaire de référence. Les salaires ne sont pas indexés sur l'IPC.",
        )
        st.slider(
            "Partage des profits dans les salaires",
            min_value=0,
            max_value=10,
            value=2,
            format="%d %%",
            key="wage_profit_sensitivity_percent",
            help="Hausse mensuelle du salaire de référence par point de marge au-dessus de la marge cible.",
        )
        st.slider(
            "Marge bénéficiaire cible des producteurs",
            min_value=0,
            max_value=40,
            value=15,
            format="%d %%",
            key="profit_margin_target_percent",
            help="Au-dessus, la concurrence tire les prix et les salaires suivent ; en dessous, les coûts sont répercutés dans les prix.",
        )
        st.slider(
            "Fonds de roulement conservé (mois de charges)",
            min_value=0.0,
            max_value=12.0,
            value=3.0,
            step=0.5,
            key="working_capital_months",
            help="La trésorerie au-delà est distribuée aux actionnaires.",
        )
        st.slider(
            "Distribution mensuelle de la trésorerie excédentaire",
            min_value=0,
            max_value=100,
            value=25,
            format="%d %%",
            key="excess_cash_payout_percent",
        )
        st.slider(
            "Distribution mensuelle du capital bancaire excédentaire",
            min_value=0.0,
            max_value=5.0,
            value=1.0,
            step=0.1,
            format="%.1f %%",
            key="bank_excess_payout_percent",
            help="Base du taux ; la règle monétaire l'augmente quand l'inflation est sous la cible.",
        )
        st.slider(
            "Part des investissements financée par crédit",
            min_value=0,
            max_value=90,
            value=50,
            format="%d %%",
            key="credit_financed_percent",
        )
        st.slider(
            "Croissance monétaire de tendance (‰ par mois)",
            min_value=0.0, max_value=8.0, value=6.0, step=0.5,
            key="monetary_trend_permille",
            help="Banque centrale : émission de monnaie de base en fonction de la tendance, de l'écart d'inflation (cible ~2 %/an) et du chômage.",
        )
        st.slider(
            "Réactivité de la banque centrale à l'inflation",
            min_value=0.0, max_value=0.5, value=0.05, step=0.01,
            key="monetary_inflation_gain",
        )
        st.slider(
            "Part de l'inflation passée rattrapée par les salaires",
            min_value=0, max_value=100, value=70, format="%d %%",
            key="wage_inflation_pass_percent",
            help="Rattrapage partiel et retardé (50 à 90 % selon le marché du travail et le secteur) ; jamais une indexation mécanique.",
        )
        st.slider(
            "Rémunération maximale de la direction (× salaire d'entrée)",
            min_value=2.0, max_value=10.0, value=5.0, step=0.5,
            key="executive_pay_multiple",
            help="Appliquée à pleine valeur dans les grandes entreprises, réduite pour les petites.",
        )
        st.slider(
            "Effet de la compétence sur le salaire",
            min_value=0, max_value=20, value=6, format="%d %%",
            key="skill_wage_slope_percent",
            help="Écart de salaire par niveau de compétence (1 à 5) dans le secteur.",
        )
        st.slider(
            "Réserves de matières reconstituées par l'exploration",
            min_value=0, max_value=100, value=95, format="%d %%",
            key="reserve_replacement_percent",
            help="Sans renouvellement, les réserves vierges s'épuisent (~tour 220) et l'économie s'arrête.",
        )
        st.slider(
            "Part du solde public dépensée chaque mois",
            min_value=0,
            max_value=100,
            value=100,
            format="%d %%",
            key="government_spending_percent",
            help="Achats de biens aux entreprises puis allocations : le budget est équilibré à long terme.",
        )
        st.slider(
            "Renfort des allocations en cas de chômage",
            min_value=0,
            max_value=100,
            value=50,
            format="%d %%",
            key="benefit_cycle_sensitivity_percent",
        )
        st.slider(
            "Hausse de salaire nécessaire pour changer d'employeur",
            min_value=0,
            max_value=30,
            value=8,
            format="%d %%",
            key="labor_mobility_premium_percent",
        )
        st.slider(
            "Part maximale du revenu consacrée au loyer",
            min_value=10,
            max_value=80,
            value=35,
            format="%d %%",
            key="household_rent_share_percent",
        )
        st.slider(
            "Réaction des loyers à l'abordabilité",
            min_value=0,
            max_value=100,
            value=25,
            format="%d %%",
            key="rent_affordability_weight_percent",
        )
    if "income_tax_percent" not in st.session_state:
        st.session_state.income_tax_percent = 8
    st.session_state.income_tax_rate = st.session_state.income_tax_percent / 100
    st.session_state.acquisition_probability = st.session_state.acquisition_probability_percent / 100
    if st.button("↻  Réinitialiser la simulation", use_container_width=True):
        st.session_state.simulation = new_simulation()

if "simulation" not in st.session_state:
    st.session_state.simulation = new_simulation()
simulation: EconomySimulation = st.session_state.simulation

header, controls = st.columns([1.5, 1], vertical_alignment="center")
with header:
    st.markdown('<div class="eyebrow">Simulation multi-agents · tableau de bord</div>', unsafe_allow_html=True)
    st.title("Économie en mouvement")
    st.caption("Suivez les décisions des entreprises, les arbitrages des ménages et l’évolution des marchés.")
with controls:
    st.markdown(f'<span class="month-pill">Période {simulation.month}</span>', unsafe_allow_html=True)
    st.write("")
    one_month, run_length = st.columns([1, 1.1])
    with one_month:
        if st.button("▶  Un mois", type="primary", use_container_width=True):
            simulation.step()
            st.rerun()
    with run_length:
        months = st.number_input("Période", min_value=1, max_value=120, value=12, step=1, label_visibility="collapsed")
        if st.button("Avancer", use_container_width=True):
            for _ in range(int(months)):
                simulation.step()
            st.rerun()

if simulation.month:
    st.info(f"**À retenir — période {simulation.month} :** {simulation.last_event}")
else:
    st.info("**Démarrage :** avancez d’un mois pour observer les marchés s’ajuster aux décisions des agents.")

population = simulation.population_summary()
employment_rate = population["employed"] / max(1, len(simulation.people))
latest = simulation.history[-1] if simulation.history else None
metrics = [*st.columns(4), *st.columns(4)]
metrics[0].metric(
    "Ménages employés",
    f"{int(population['employed'])} / {len(simulation.people)}" if latest else "—",
    f"{employment_rate:.0%} du total" if latest else None,
    help="Nombre de ménages employés et part de la population.",
)
metrics[1].metric("Prix alimentaire", f"{simulation.food_price:.2f} €")
metrics[2].metric("Prix des biens", f"{simulation.goods_price:.2f} €")
metrics[3].metric("Loyer mensuel", f"{simulation.rent:.2f} €")
metrics[4].metric(
    "Prix des matières premières",
    f"{simulation.resources.raw_material_price:.2f} €",
)
metrics[5].metric(
    "Réserve vierge",
    compact(simulation.resources.raw_materials),
    help=(
        f"{simulation.resources.raw_materials:,.0f} unités, reconstituées par "
        f"l'exploration. Matières recyclées disponibles : "
        f"{simulation.resources.recycled_materials:,.1f}."
    ),
)
metrics[6].metric(
    "Indice des prix",
    f"{latest.consumer_price_index:.1f}" if latest else "100,0",
    f"{latest.inflation_rate:+.1%} ce mois-ci" if latest else None,
    help="Indice des prix à la consommation, base 100 au démarrage.",
)
metrics[7].metric(
    "Salaire réel moyen",
    f"{latest.real_average_wage:,.0f} €" if latest else "—",
    help="Salaire moyen corrigé de l'évolution des prix, en euros de la période de départ.",
)
credit_metrics = [*st.columns(3), *st.columns(3)]
credit_metrics[0].metric(
    "PIB réel mensuel",
    compact(latest.real_gdp, " €") if latest else "—",
    help=(
        f"{latest.real_gdp:,.0f} € — ventes finales, loyers, achats publics et "
        "investissement valorisés aux prix de départ."
        if latest
        else "Valorisé aux prix de départ."
    ),
)
credit_metrics[1].metric(
    "PIB nominal mensuel",
    compact(latest.nominal_gdp, " €") if latest else "—",
    f"dont récurrent {compact(latest.recurrent_gdp, ' €')}" if latest else None,
    help=(
        f"{latest.nominal_gdp:,.0f} € (récurrent {latest.recurrent_gdp:,.0f} € + "
        f"construction {latest.construction_value_added:,.0f} €)."
        if latest
        else "PIB récurrent + construction à sa valeur ajoutée."
    ),
)
credit_metrics[2].metric(
    "Taux directeur simulé",
    f"{latest.policy_rate:.2%}" if latest else f"{simulation.policy_rate:.2%}",
    help="Ajusté progressivement selon l'inflation et le chômage observés.",
)
credit_metrics[3].metric(
    "Crédits immobiliers",
    compact(latest.mortgage_debt, " €") if latest else "—",
    help=f"{latest.mortgage_debt:,.0f} €" if latest else None,
)
credit_metrics[4].metric(
    "Emplois du secteur amont",
    f"{latest.supplier_employment}" if latest else "—",
    help="Emplois payés par les recettes du mois du secteur amont agrégé (intrants, construction, entretien, investissement). Ils suivent l'activité ; aucun fonds n'est conservé.",
)
banks = [
    company
    for company in simulation.companies
    if company.sector == "bank" and company.active
]
credit_metrics[5].metric(
    "Banques actives",
    f"{len(banks)}",
    f"{compact(sum(bank.bank_equity for bank in banks), ' €')} de fonds propres",
    help=(
        f"Fonds propres : {sum(bank.bank_equity for bank in banks):,.0f} €. "
        f"Prêts en cours : {latest.bank_loan_portfolio:,.0f} €. "
        if latest
        else "Les banques sont des entreprises cotées."
    ),
)

overview, firms_tab, households_tab, model_tab = st.tabs(
    ["Vue d’ensemble", "Entreprises & bourse", "Ménages", "Modèle & réglages"]
)

with overview:
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        st.subheader("Les marchés dans le temps")
        st.markdown('<p class="section-note">Prix et revenus moyens par période.</p>', unsafe_allow_html=True)
        if len(simulation.history) > 1:
            price_history = [
                {
                    "Période": record.month,
                    "Alimentation": record.food_price,
                    "Biens courants": record.goods_price,
                    "Loyer": record.housing_rent,
                    "Matières premières": record.raw_material_price,
                }
                for record in simulation.history
            ]
            st.line_chart(
                price_history,
                x="Période",
                y=["Alimentation", "Biens courants", "Loyer", "Matières premières"],
            )
            macro_history = [
                {
                    "Période": record.month,
                    "Indice des prix (base 100)": record.consumer_price_index,
                    "Salaire nominal moyen": record.average_wage,
                    "Salaire réel moyen": record.real_average_wage,
                    "Inflation mensuelle (%)": record.inflation_rate * 100,
                    "PIB réel": record.real_gdp,
                    "PIB nominal": record.nominal_gdp,
                    "Taux directeur (%)": record.policy_rate * 100,
                    "PIB réel · moyenne 12 mois": sum(
                        previous.real_gdp
                        for previous in simulation.history[
                            max(0, index - 11) : index + 1
                        ]
                    )
                    / len(
                        simulation.history[
                            max(0, index - 11) : index + 1
                        ]
                    ),
                }
                for index, record in enumerate(simulation.history)
            ]
            macro_left, macro_right = st.columns(2)
            with macro_left:
                st.caption("Évolution du coût de la vie")
                st.line_chart(
                    macro_history,
                    x="Période",
                    y="Indice des prix (base 100)",
                )
            with macro_right:
                st.caption("Salaires nominaux et pouvoir d'achat")
                st.line_chart(
                    macro_history,
                    x="Période",
                    y=["Salaire nominal moyen", "Salaire réel moyen"],
                )
            gdp_column, policy_column = st.columns(2)
            with gdp_column:
                st.caption("PIB réel et PIB courant (le PIB réel inclut une moyenne glissante)")
                st.line_chart(
                    macro_history,
                    x="Période",
                    y=[
                        "PIB réel",
                        "PIB réel · moyenne 12 mois",
                        "PIB nominal",
                    ],
                )
            with policy_column:
                st.caption("Inflation et taux directeur")
                st.line_chart(
                    macro_history,
                    x="Période",
                    y=["Inflation mensuelle (%)", "Taux directeur (%)"],
                )
            employment_history = [
                {
                    "Période": record.month,
                    "Avec emploi": record.employed_count,
                    "Sans emploi": max(
                        0, record.population_count - record.employed_count
                    ),
                    "Population": record.population_count,
                }
                for record in simulation.history
            ]
            activity_history = [
                {
                    "Période": record.month,
                    "Ventes nourriture": record.food_sold,
                    "Ventes biens": record.goods_sold,
                    "Demande non satisfaite — nourriture": record.unmet_food_demand,
                    "Demande non satisfaite — biens": record.unmet_goods_demand,
                }
                for record in simulation.history
            ]
            st.caption("Emploi et chômage")
            st.area_chart(
                employment_history,
                x="Période",
                y=["Avec emploi", "Sans emploi"],
            )
            st.caption("Croissance de la population")
            st.line_chart(
                employment_history,
                x="Période",
                y="Population",
            )
            st.caption("Ventes réalisées et demande non satisfaite")
            st.line_chart(activity_history, x="Période")
            inventory_history = [
                {
                    "Période": record.month,
                    "Stock alimentaire restant": record.food_inventory,
                    "Stock de biens restant": record.goods_inventory,
                    "Stock technologique restant": record.technology_inventory,
                }
                for record in simulation.history
            ]
            st.caption("Stocks invendus en fin de période")
            st.line_chart(inventory_history, x="Période")
        elif simulation.history:
            st.caption("Les courbes se dessinent à partir de la deuxième période.")
        else:
            st.caption("Les graphiques apparaîtront dès la première période.")
    with right:
        st.subheader("En bref")
        st.markdown('<p class="section-note">Ce qui s’est passé dans la dernière période.</p>', unsafe_allow_html=True)
        if latest:
            st.metric("Chiffre d’affaires total", compact(latest.total_revenue, " €"), help=f"{latest.total_revenue:,.0f} €")
            st.metric("Bénéfice cumulé des entreprises", compact(latest.total_profit, " €"), help=f"{latest.total_profit:,.0f} €")
            st.metric("Demande alimentaire non satisfaite", f"{latest.unmet_food_demand:.1f} unités")
            st.metric("Demande de biens non satisfaite", f"{latest.unmet_goods_demand:.1f} unités")
            st.metric("Logements occupés / vacants", f"{latest.homes_occupied} / {latest.vacancies}")
            st.metric(
                "Emplois du secteur amont",
                f"{latest.supplier_employment}",
                help="Le secteur amont agrégé reverse l’intégralité de ses recettes du mois en salaires.",
            )
            st.metric(
                "Dette hypothécaire des ménages",
                compact(latest.mortgage_debt, " €"),
                help=f"{latest.mortgage_debt:,.0f} €",
            )
            st.metric(
                "Investissement productif",
                compact(latest.production_capacity_investment, " €"),
                help="Capacité de production ajoutée par les entreprises rentables.",
            )
            st.metric(
                "Ventes tech aux ménages",
                compact(latest.household_technology_revenue, " €"),
                help="Abonnements et outils numériques achetés par les ménages.",
            )
        else:
            st.write("Les indicateurs apparaîtront après le premier mois.")

    st.subheader("Le circuit économique")
    flow = st.columns(4)
    flow[0].metric("Emplois", f"{population['employed']:.0f}", help="Ménages employés par les entreprises.")
    flow[1].metric("Ventes alimentaires", f"{latest.food_sold:.1f}" if latest else "—", help="Unités achetées par les ménages.")
    flow[2].metric("Ventes de biens", f"{latest.goods_sold:.1f}" if latest else "—", help="Biens courants écoulés.")
    flow[3].metric("Dividendes versés", compact(latest.dividends_paid, " €") if latest else "—")
    if latest:
        st.caption(
            "Part des ventes alimentaires et des biens courants : "
            f"{latest.sector_market_share.get('food', 0.0):.0%} / "
            f"{latest.sector_market_share.get('goods', 0.0):.0%}."
        )

    st.divider()
    st.subheader("Fortunes & concentration")
    st.markdown(
        '<p class="section-note">Fortune nette = liquidités + actions + immobilier - dettes.</p>',
        unsafe_allow_html=True,
    )
    inequality_metrics = st.columns(4)
    inequality_metrics[0].metric(
        "Indice de Gini",
        f"{latest.household_gini:.3f}" if latest else "—",
        help="0 correspond à une répartition égale ; 1 à une concentration maximale.",
    )
    inequality_metrics[1].metric(
        "Part du décile supérieur",
        f"{latest.household_top10_share:.0%}" if latest else "—",
    )
    inequality_metrics[2].metric(
        "Fortune médiane",
        compact(latest.household_wealth_median, " €") if latest else "—",
        help=f"{latest.household_wealth_median:,.0f} €" if latest else None,
    )
    inequality_metrics[3].metric(
        "Dette des entreprises",
        compact(latest.company_debt, " €") if latest else "—",
        help=f"{latest.company_debt:,.0f} €" if latest else None,
    )
    if len(simulation.history) > 1:
        wealth_history = [
            {
                "Période": record.month,
                "Gini": record.household_gini,
                "Part du décile supérieur (%)": record.household_top10_share * 100,
            }
            for record in simulation.history
        ]
        wealth_left, wealth_right = st.columns(2)
        with wealth_left:
            st.caption("Évolution des inégalités de fortune")
            st.line_chart(wealth_history, x="Période", y="Gini")
        with wealth_right:
            st.caption("Part de fortune détenue par les 10 % les plus riches")
            st.line_chart(wealth_history, x="Période", y="Part du décile supérieur (%)")
    elif latest:
        st.caption("L'évolution des indicateurs de concentration apparaîtra à partir de la deuxième période.")
    richest_households, richest_companies = st.columns(2, gap="large")
    with richest_households:
        st.markdown("#### Ménages les plus fortunés")
        for row in simulation.household_leaderboard(5):
            with st.container(border=True):
                st.markdown(
                    f"**{row['Ménage']}** · **{row['Fortune nette']:,.0f} €**  \n"
                    f"Liquidités {row['Trésorerie']:,.0f} € · Actions {row['Actions']:,.0f} € · "
                    f"Immobilier {row['Immobilier']:,.0f} €"
                )
    with richest_companies:
        st.markdown("#### Entreprises les plus valorisées")
        for row in simulation.company_leaderboard(5):
            with st.container(border=True):
                st.markdown(
                    f"**{row['Entreprise']}** · **{row['Fortune nette']:,.0f} €**  \n"
                    f"{dict(SECTORS)[row['Secteur']]} · Trésorerie {row['Trésorerie']:,.0f} € · "
                    f"Dette {row['Dettes']:,.0f} € · Bénéfice {row['Profit']:,.0f} €"
                )

    if len(simulation.history) > 1:
        st.subheader("Évolution de l'emploi par secteur")
        sector_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_employment.get(sector, 0)
                    for sector, label in SECTORS
                },
            }
            for record in simulation.history
        ]
        st.area_chart(sector_chart_data, x="Période")
        income_chart_data = [
            {
                "Période": record.month,
                "Revenus des ménages": record.household_income,
                "Dépenses des ménages": record.household_spending,
            }
            for record in simulation.history
        ]
        st.line_chart(
            income_chart_data,
            x="Période",
            y=["Revenus des ménages", "Dépenses des ménages"],
        )
        revenue_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_revenue.get(sector, 0.0)
                    for sector, label in SECTORS
                },
            }
            for record in simulation.history
        ]
        profit_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_profit.get(sector, 0.0)
                    for sector, label in SECTORS
                },
            }
            for record in simulation.history
        ]
        sector_finance_left, sector_finance_right = st.columns(2)
        with sector_finance_left:
            st.caption("Chiffre d'affaires par secteur")
            st.line_chart(revenue_chart_data, x="Période")
        with sector_finance_right:
            st.caption("Bénéfice après charges par secteur")
            st.line_chart(profit_chart_data, x="Période")
        wage_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_average_wage.get(sector, 0.0)
                    for sector, label in SECTORS
                },
            }
            for record in simulation.history
        ]
        st.caption("Salaire mensuel moyen par secteur")
        st.line_chart(wage_chart_data, x="Période")
        resource_history = [
            {
                "Période": record.month,
                "Réserves vierges": record.raw_materials_remaining,
                "Matières recyclées": record.recycled_materials_remaining,
            }
            for record in simulation.history
        ]
        st.caption("Réserves vierges et matières recyclées")
        st.line_chart(
            resource_history,
            x="Période",
            y=["Réserves vierges", "Matières recyclées"],
        )

    with st.expander("Marché du travail par métiers", expanded=False):
        trade_rows = [
            {
                "Métier": row["Métier"],
                "Offre (personnes)": row["Offre (personnes)"],
                "Demande (postes)": row["Demande (postes)"],
                "Tension": row["Tension"],
                "Salaire moyen (€)": row["Salaire moyen"],
                "Coefficient de salaire": row["Coefficient de salaire"],
                "Chômage": row["Chômage"],
            }
            for row in simulation.trade_table()
        ]
        st.dataframe(
            trade_rows,
            hide_index=True,
            width="stretch",
            column_config={
                "Salaire moyen (€)": st.column_config.NumberColumn(format="localized"),
                "Chômage": st.column_config.NumberColumn(format="percent"),
            },
        )
        st.caption(
            "Un poste spécialisé ne peut être pourvu que par le bon métier ; les postes "
            "d'opérateur sont ouverts à tous. Le coefficient de salaire suit la tension "
            "offre / demande ; la reconversion coûte de l'argent et prend du temps."
        )

    with st.expander("Historique récent", expanded=False):
        for record in reversed(simulation.history[-6:]):
            with st.container(border=True):
                st.markdown(
                    f"**Période {record.month}** · "
                    f"{record.employed_count}/{len(simulation.people)} ménages employés · "
                    f"{record.event}"
                )
                st.caption(
                    f"Alimentation {record.food_price:,.2f} € · Biens {record.goods_price:,.2f} € · "
                    f"Loyer {record.housing_rent:,.2f} € · Bénéfice total "
                    f"{record.total_profit:,.0f} € · Impôts {record.tax_revenue:,.0f} €"
                )

with firms_tab:
    st.subheader("Performance des entreprises")
    st.markdown(
        '<p class="section-note">Chaque fiche résume une entreprise. Choisissez-en une pour ouvrir son historique.</p>',
        unsafe_allow_html=True,
    )
    firm_rows = simulation.company_rows()
    active_companies = [company for company in simulation.companies if company.active]
    companies_by_id = {company.id: company for company in simulation.companies}
    company_options = [company.id for company in active_companies]
    selected_company_id = None
    if company_options:
        previous_selection = st.session_state.get("company_selector")
        default_selection = (
            previous_selection
            if previous_selection in company_options
            else company_options[0]
        )
        st.session_state["company_selector"] = default_selection
        selected_company_id = st.selectbox(
            "Entreprise à détailler",
            company_options,
            format_func=lambda company_id: (
                f"{companies_by_id[company_id].name} · "
                f"{dict(SECTORS)[companies_by_id[company_id].sector]} · "
                f"Cours {companies_by_id[company_id].stock_price:,.2f} €"
            ),
            key="company_selector",
        )

    st.markdown("#### Résumé par secteur")
    sector_summary = []
    revenue_total = sum(c.revenue for c in active_companies) or 1.0
    for sector, label in SECTORS:
        if sector == "services":
            continue
        sector_firms = [c for c in simulation.companies if c.sector == sector]
        sector_active = [c for c in sector_firms if c.active]
        sector_revenue = sum(c.revenue for c in sector_active)
        sector_profit = sum(c.profit for c in sector_active)
        sector_summary.append(
            {
                "Secteur": label,
                "Actives": len(sector_active),
                "Fermées": len(sector_firms) - len(sector_active),
                "Employés": sum(len(c.employees) for c in sector_active),
                "Chiffre d’affaires (€)": sector_revenue,
                "Part du CA": sector_revenue / revenue_total,
                "Bénéfice (€)": sector_profit,
                "Marge": sector_profit / sector_revenue if sector_revenue > 0 else None,
            }
        )
    st.dataframe(
        sector_summary,
        hide_index=True,
        width="stretch",
        column_config={
            "Chiffre d’affaires (€)": st.column_config.NumberColumn(format="localized"),
            "Bénéfice (€)": st.column_config.NumberColumn(format="localized"),
            "Part du CA": st.column_config.NumberColumn(format="percent"),
            "Marge": st.column_config.NumberColumn(format="percent"),
        },
    )

    st.markdown("#### Résumé des entreprises")
    st.caption(
        "Une ligne par entreprise (survolez une valeur pour la lire en entier ; "
        "le détail s'ouvre via le sélecteur ci-dessus)."
    )
    summary_rows = [
        {
            "Entreprise": row["Entreprise"],
            "Secteur": row["Secteur"],
            "Statut": "Active" if row["Statut"] == "Active" else "Fermée",
            "Employés": row["Employés"],
            "Chiffre d’affaires (€)": row["Chiffre d'affaires"],
            "Bénéfice (€)": row["Bénéfice"],
            "Marge": (
                row["Bénéfice"] / row["Chiffre d'affaires"]
                if row["Chiffre d'affaires"] > 0
                else None
            ),
            "Salaire moyen (€)": row["Salaire moyen"],
            "Trésorerie (€)": row["Trésorerie"],
        }
        for row in sorted(firm_rows, key=lambda r: (r["Statut"] != "Active", r["Secteur"], r["Entreprise"]))
    ]
    st.dataframe(
        summary_rows,
        hide_index=True,
        width="stretch",
        height=min(640, 38 + 35 * len(summary_rows)),
        column_config={
            "Entreprise": st.column_config.TextColumn(width="medium"),
            "Employés": st.column_config.NumberColumn(format="%d", width="small"),
            "Chiffre d’affaires (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Bénéfice (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Marge": st.column_config.NumberColumn(format="percent", width="small"),
            "Salaire moyen (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Trésorerie (€)": st.column_config.NumberColumn(format="localized", width="medium"),
        },
    )

    selected_company = (
        simulation.company_profile(selected_company_id) if selected_company_id else None
    )
    if selected_company:
        st.divider()
        st.subheader(f"Profil détaillé · {selected_company.name}")
        st.caption(
            f"{dict(SECTORS)[selected_company.sector]} · "
            f"{'Active' if selected_company.active else 'Absorbée / fermée'} · "
            f"PDG : {simulation.person_name(selected_company.executive_id)}"
        )
        executive = next(
            (person for person in simulation.people if person.id == selected_company.executive_id),
            None,
        )
        firm_headline = st.columns(4)
        firm_headline[0].metric("Employés", f"{len(selected_company.employees)}")
        firm_headline[1].metric(
            "Chiffre d’affaires du mois",
            compact(selected_company.revenue, " €"),
            help=f"{selected_company.revenue:,.0f} €",
        )
        firm_headline[2].metric(
            "Bénéfice du mois",
            compact(selected_company.profit, " €"),
            help=f"{selected_company.profit:,.0f} €",
        )
        firm_headline[3].metric(
            "Marge nette",
            f"{selected_company.profit / selected_company.revenue:.1%}"
            if selected_company.revenue > 0
            else "—",
            help=f"Cible du secteur : {simulation.config.sector_margin_target.get(selected_company.sector, 0.0):.0%}",
        )
        firm_profile_metrics = st.columns(5)
        firm_profile_metrics[0].metric("Trésorerie", compact(selected_company.cash, " €"), help=f"{selected_company.cash:,.0f} €")
        firm_profile_metrics[1].metric(
            "Fortune nette",
            compact(simulation.company_wealth(selected_company), " €"),
            help=f"{simulation.company_wealth(selected_company):,.0f} €",
        )
        firm_profile_metrics[2].metric(
            "Dette", compact(selected_company.debt, " €"), help=f"{selected_company.debt:,.0f} €"
        )
        firm_profile_metrics[3].metric("Expérience CEO", f"{selected_company.ceo_experience} mois")
        firm_profile_metrics[4].metric(
            "Risque du CEO",
            f"{executive.risk_tolerance:.2f}"
            if executive else f"{selected_company.risk_tolerance:.2f} (entreprise)",
        )
        st.caption(
            f"Traits : productivité {selected_company.productivity_trait:.2f} · "
            f"qualité {selected_company.quality_trait:.2f} · "
            f"agressivité tarifaire {selected_company.pricing_aggression:.2f} · "
            f"réinvestissement {selected_company.reinvestment_preference:.0%} · "
            f"innovation {selected_company.innovation:.2f} · "
            f"technologie {selected_company.tech_level:.2f} · "
            f"capacité additionnelle {selected_company.production_capacity:.1f}/mois · "
            f"acquisitions {selected_company.acquisition_count}"
        )
        if selected_company.sector == "real_estate":
            housed = sum(
                person.rental_owner_id == selected_company.id for person in simulation.people
            )
            property_metrics = st.columns(4)
            property_metrics[0].metric("Logements détenus", f"{selected_company.property_inventory}")
            property_metrics[1].metric("Loués", f"{housed}")
            property_metrics[2].metric(
                "Vacants", f"{max(0, selected_company.property_inventory - housed)}"
            )
            property_metrics[3].metric(
                "En construction", f"{len(selected_company.construction_pipeline)}"
            )
            st.caption(
                f"Valeur du parc au prix courant : "
                f"{selected_company.property_inventory * simulation.config.housing_price:,.0f} € · "
                f"loyer courant {simulation.rent:,.0f} € / mois"
            )
        if selected_company.sector == "energy":
            st.caption(
                f"Infrastructures vendues ce mois : {selected_company.infrastructure_sold_value:,.0f} € · "
                f"installations capitalisées : {selected_company.productive_capital_value:,.0f} €"
            )
        physical_stock = {
            "food": selected_company.inventory,
            "goods": selected_company.goods_inventory,
            "technology": selected_company.tech_inventory,
        }.get(selected_company.sector)
        if physical_stock is not None:
            st.metric(
                "Stock invendu en fin de mois",
                f"{physical_stock:.1f} unités",
                f"Produit {selected_company.units_produced:.1f} · vendu {selected_company.units_sold:.1f}",
                help="Stock physique restant après les ventes. Un stock faible signifie que presque toute la production a été vendue.",
            )
            if selected_company.sector == "food":
                st.caption(
                    f"Valeur comptable du stock alimentaire : "
                    f"{physical_stock * simulation.config.food_input_cost:,.2f} € "
                    "(au coût des intrants)."
                )
        balance_sheet = company_balance_sheet(
            selected_company, simulation.companies, simulation.config
        )
        valuation = company_valuation_metrics(
            selected_company, simulation.companies, simulation.config
        )
        balance_assets, balance_liabilities = st.columns(2, gap="large")
        with balance_assets:
            st.markdown("#### Actifs au bilan")
            balance_labels = [
                "Trésorerie",
                "Stocks alimentaires",
                "Stocks de biens",
                "Immobilier détenu",
                "Construction en cours",
                "Stocks technologiques",
                "Actifs technologiques",
                "Capacité de production capitalisée",
                "Participations financières",
            ]
            if selected_company.sector == "bank":
                balance_labels.append("Prêts aux ménages et entreprises")
            for label in balance_labels:
                st.write(f"**{label}** · {balance_sheet[label]:,.2f} €")
            if selected_company.sector == "food":
                st.caption(
                    f"Quantité en stock : {selected_company.inventory:.2f} unités. "
                    "La valeur au bilan est calculée au coût des intrants, pas au prix de vente."
                )
            st.metric("Total des actifs", f"{balance_sheet['Total des actifs']:,.0f} €")
        with balance_liabilities:
            st.markdown("#### Passifs et valeur")
            st.write(f"**Dettes bancaires** · {balance_sheet['Dettes bancaires']:,.0f} €")
            st.write(f"**Autres passifs** · {balance_sheet['Autres passifs']:,.0f} €")
            st.metric("Total des passifs", f"{balance_sheet['Total des passifs']:,.0f} €")
            st.metric("Capitaux propres", f"{balance_sheet['Capitaux propres']:,.0f} €")
            st.caption(
                "Capitaux propres = total des actifs − total des passifs. "
                "Les stocks sont valorisés au coût d'intrants et l'immobilier au prix configuré."
            )

        st.markdown("#### Indicateurs boursiers et financiers")
        valuation_labels = (
            "Capitalisation boursière",
            "Valeur économique estimée",
            "PER",
            "Cours / valeur comptable",
            "Rendement du dividende",
            "Marge nette",
            "Dette / actifs",
            "Flux de trésorerie disponible",
        )
        valuation_columns = st.columns(4)
        for index, label in enumerate(valuation_labels):
            value = valuation[label]
            if value is None:
                formatted_value = "N/D"
            elif label in (
                "Capitalisation boursière",
                "Valeur économique estimée",
                "Flux de trésorerie disponible",
            ):
                formatted_value = f"{value:,.0f} €"
            elif label in ("Rendement du dividende", "Marge nette", "Dette / actifs"):
                formatted_value = f"{value:.1%}"
            else:
                formatted_value = f"{value:.2f}×"
            valuation_columns[index % 4].metric(label, formatted_value)
        st.caption(
            "Le cours converge vers une estimation mêlant actifs nets, bénéfices anticipés "
            "et croissance récente du chiffre d'affaires ; un événement sectoriel ne produit "
            "qu'un effet temporaire."
        )

        transaction_partners = [
            (
                "Clients entreprises",
                selected_company.customer_revenue,
                "Chiffre d'affaires",
            ),
            (
                "Fournisseurs entreprises",
                selected_company.supplier_spending,
                "Achats",
            ),
        ]
        if any(values for _, values, _ in transaction_partners):
            st.markdown("#### Relations interentreprises · matières premières et technologie")
            partner_columns = st.columns(2)
            for index, (title, values, transaction_label) in enumerate(transaction_partners):
                with partner_columns[index]:
                    st.markdown(f"**{title}**")
                    for partner_id, amount in sorted(
                        values.items(), key=lambda item: item[1], reverse=True
                    ):
                        st.write(
                            f"{simulation.company_name(partner_id)} · "
                            f"{transaction_label} {amount:,.0f} €"
                        )
        if selected_company.financial_history:
            firm_history = selected_company.financial_history
            history_first, history_second = st.columns(2)
            with history_first:
                st.caption("Trésorerie et valeur nette")
                st.line_chart(
                    firm_history,
                    x="Période",
                    y=["Trésorerie", "Fortune"],
                )
            with history_second:
                st.caption("Chiffre d'affaires et bénéfice mensuel")
                st.line_chart(
                    firm_history,
                    x="Période",
                    y=["Chiffre d'affaires", "Bénéfice"],
                )
            st.caption("Effectifs")
            st.line_chart(firm_history, x="Période", y="Employés")
            debt_and_dividends_left, debt_and_dividends_right = st.columns(2)
            with debt_and_dividends_left:
                st.caption("Dette")
                st.line_chart(firm_history, x="Période", y="Dettes")
            with debt_and_dividends_right:
                st.caption("Dividendes versés")
                st.line_chart(firm_history, x="Période", y="Dividendes")
            st.caption("Flux de trésorerie disponible")
            st.line_chart(firm_history, x="Période", y="Flux de trésorerie disponible")
            st.caption("Production, stocks et capacité de production ajoutée")
            st.line_chart(
                firm_history,
                x="Période",
                y=[
                    "Production mensuelle",
                    "Stocks alimentaires",
                    "Stocks de biens",
                    "Capacité supplémentaire",
                ],
            )
            st.caption("Cours de l'action")
            st.line_chart(firm_history, x="Période", y="Cours")
        if selected_company.employees:
            with st.expander(f"Voir l'équipe ({len(selected_company.employees)} personnes)"):
                for employee_id in selected_company.employees:
                    employee = next(
                        person for person in simulation.people if person.id == employee_id
                    )
                    role = " · PDG" if employee.id == selected_company.executive_id else ""
                    st.markdown(
                        f"- **{employee.name}**{role} · Compétence {employee.skill:.2f} · "
                        f"Ancienneté {employee.employer_tenure} mois · "
                        f"Rôle : {employee.employment_role} · "
                        f"Revenu du mois {employee.income_this_turn:,.0f} €"
                    )

    if selected_company and selected_company.shares:
        st.markdown("**Participations détenues par cette entreprise**")
        for issuer_id, shares in sorted(selected_company.shares.items()):
            issuer = companies_by_id.get(issuer_id)
            if issuer is None:
                continue
            ownership = (
                shares / issuer.shares_outstanding
                if issuer.shares_outstanding > 0.0
                else 0.0
            )
            st.markdown(
                f"- **{issuer.name}** · {shares:.1f} actions · "
                f"{ownership:.1%} du capital · valeur estimée "
                f"{shares * issuer.stock_price:,.0f} €"
            )
    if selected_company and selected_company.sector == "bank":
        st.markdown("**Prêts et emprunteurs de la banque**")
        accounts = sorted(
            selected_company.bank_loan_accounts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if not accounts:
            st.caption("Aucun prêt en cours.")
        for borrower_id, balance in accounts:
            if borrower_id.startswith("mortgage:"):
                borrower_name = simulation.person_name(
                    borrower_id.removeprefix("mortgage:")
                )
                borrower_type = "Prêt immobilier"
            else:
                borrower_name = simulation.company_name(borrower_id)
                borrower_type = "Prêt entreprise"
            st.markdown(
                f"- **{borrower_name}** · {borrower_type} · solde "
                f"{balance:,.0f} €"
            )

    st.subheader("Cours boursiers")
    if company_options:
        stock_selection = st.multiselect(
            "Entreprises à afficher",
            company_options,
            default=company_options[: min(6, len(company_options))],
            format_func=lambda company_id: companies_by_id[company_id].name,
            key="stock_chart_companies",
        )
        if len(simulation.history) > 1 and stock_selection:
            stock_history = [
                {
                    "Période": record.month,
                    **{
                        companies_by_id[company_id].name: record.company_prices.get(
                            company_id, 0.0
                        )
                        for company_id in stock_selection
                    },
                }
                for record in simulation.history
            ]
            st.line_chart(stock_history, x="Période")
        elif len(simulation.history) <= 1:
            st.caption("La courbe se dessine après deux mois de simulation.")
        else:
            st.info("Sélectionnez au moins une entreprise pour afficher son cours.")
    else:
        st.info("Aucune entreprise active à afficher.")
    st.caption("La légende identifie chaque entreprise ; les cours réagissent aux bénéfices et aux événements.")

with households_tab:
    st.subheader("Ménages : revenus, besoins et logement")
    st.markdown(
        '<p class="section-note">Les dépenses dépendent du revenu disponible ; les ressources les plus faibles sont prioritaires à suivre.</p>',
        unsafe_allow_html=True,
    )
    household_metrics = st.columns(4)
    household_metrics[0].metric("Trésorerie moyenne", compact(population["average_cash"], " €"), help=f"{population['average_cash']:,.1f} €")
    household_metrics[1].metric("Nourriture achetée", f"{population['food']:.1f} unités")
    household_metrics[2].metric("Biens achetés", f"{population['goods']:.1f} unités")
    household_metrics[3].metric("Accès au logement", f"{population['home_satisfaction']:.0%}")
    people_by_wealth = sorted(
        simulation.people,
        key=simulation.household_wealth,
        reverse=True,
    )
    person_options = [person.id for person in people_by_wealth]
    people_by_id = {person.id: person for person in simulation.people}
    previous_person = st.session_state.get("household_selector")
    default_person = previous_person if previous_person in person_options else person_options[0]
    st.session_state["household_selector"] = default_person
    selected_person_id = st.selectbox(
        "Ménage à détailler",
        person_options,
        format_func=lambda person_id: (
            f"{people_by_id[person_id].name} · "
            f"Fortune {simulation.household_wealth(people_by_id[person_id]):,.0f} € · "
            f"{simulation.company_name(people_by_id[person_id].employer_id)}"
        ),
        key="household_selector",
    )
    st.caption("La liste est triée par fortune nette ; choisissez un ménage pour consulter son historique.")
    selected_person = (
        simulation.household_profile(selected_person_id) if selected_person_id else None
    )
    if selected_person:
        person = selected_person["person"]
        st.divider()
        st.subheader(f"Profil détaillé · {person.name}")
        st.caption(
            f"Employeur : {simulation.company_name(person.employer_id)} · "
            f"Rôle : {person.employment_role} · "
            f"Logement : {'propriétaire' if person.owns_home else 'locataire' if person.rental_owner_id else 'aucun'} · "
            f"Compétence : {person.skill:.2f} · ancienneté : {person.employer_tenure} mois"
        )
        person_metrics = st.columns(6)
        person_metrics[0].metric(
            "Fortune nette", f"{float(selected_person['wealth']):,.0f} €"
        )
        person_metrics[1].metric("Trésorerie", f"{person.cash:,.0f} €")
        person_metrics[2].metric("Revenu du mois", f"{person.income_this_turn:,.0f} €")
        person_metrics[3].metric("Dépenses du mois", f"{person.expenses_this_turn:,.0f} €")
        person_metrics[4].metric("Dette immobilière", f"{person.mortgage_debt:,.0f} €")
        person_metrics[5].metric("Mensualité du prêt", f"{person.mortgage_payment:,.0f} €")
        st.caption(
            f"Dividendes reçus ce mois-ci : "
            f"{person.dividend_income_this_turn:,.0f} €."
        )
        st.caption(
            "Pouvoir d'achat de la fortune "
            f"(euros de départ) : "
            f"{float(selected_person['wealth']) * 100.0 / max(simulation.price_index, 1.0):,.0f} €."
        )
        st.markdown("**Actions détenues**")
        if selected_person["shares"]:
            for share in selected_person["shares"]:
                st.markdown(
                    f"- **{share['Entreprise']}** · {share['Actions']:.1f} actions · "
                    f"{share['Part du capital']:.1%} du capital · "
                    f"Valeur estimée {share['Valeur']:,.0f} €"
                )
        else:
            st.caption("Ce ménage ne détient pas d'actions actuellement.")
        if person.financial_history:
            st.caption("Fortune nette au fil du temps")
            st.line_chart(
                person.financial_history,
                x="Période",
                y=["Fortune", "Fortune réelle"],
            )
            st.caption("Liquidités et valeur des actions")
            st.line_chart(
                person.financial_history,
                x="Période",
                y=["Trésorerie nette", "Actions"],
            )
            st.caption("Revenus et dépenses mensuels")
            st.line_chart(
                person.financial_history,
                x="Période",
                y=["Revenus", "Dividendes", "Dépenses"],
            )
    if len(simulation.history) > 1:
        wealth_bands = [
            ("0–10 k€", 0.0, 10000.0),
            ("10–25 k€", 10000.0, 25000.0),
            ("25–50 k€", 25000.0, 50000.0),
            ("50–100 k€", 50000.0, 100000.0),
            ("100–250 k€", 100000.0, 250000.0),
            ("250 k€ et +", 250000.0, float("inf")),
        ]
        distribution = [
            {
                "Tranche de fortune": label,
                "Nombre de ménages": sum(
                    lower <= simulation.household_wealth(person) < upper
                    for person in simulation.people
                ),
            }
            for label, lower, upper in wealth_bands
        ]
        st.subheader("Répartition des fortunes")
        st.bar_chart(distribution, x="Tranche de fortune", y="Nombre de ménages")

with model_tab:
    st.subheader("Mécanismes qui font émerger la concentration")
    improvements = [
        ("1 · Profils détaillés des agents", "Le sélecteur de ménage ou d'entreprise ouvre des graphiques historiques lisibles."),
        ("2 · Inégalités et palmarès", "Le Gini, la part du décile supérieur, les fortunes médianes et maximales évoluent dans le temps."),
        ("3 · PDG issus des équipes", "Le score de compétence, d'ancienneté et d'expérience de direction est tempéré par le hasard ; chaque dirigeant a sa tolérance au risque."),
        ("4 · Trois leviers de compétition", "A. Guerre des prix : les firmes agressives gagnent des ventes au prix d'une marge réduite. B. Réinvestissement contre dividendes : les entreprises expansionnistes gardent davantage leurs bénéfices. C. Fusions-acquisitions : les firmes rentables absorbent parfois des rivales via un échange d'actions."),
        ("5 · Banque, dette et consolidation", "Les entreprises empruntent dans la limite des réserves bancaires et d'un levier lié au PDG ; les intérêts impayés sont passés en charges et capitalisés, et le bilan bancaire estime réserves + prêts."),
        ("6 · Expansion des entreprises", "Les logements sont livrés après le délai configurable (six mois par défaut) et le chantier est payé mois par mois, en partie à crédit. Le secteur technologique vend ses solutions."),
        ("7 · Traits aléatoires", "Productivité, risque, innovation, stratégie tarifaire et réinvestissement varient à la création des firmes."),
        ("8 · Création et échec d'entreprises", "Les ménages fortunés peuvent financer une nouvelle firme ; les jeunes entreprises fragiles ont un risque d'échec configurable."),
        ("9 · Salaires et dividendes", "Le salaire de référence suit productivité, tension du marché du travail et rentabilité (aucune indexation sur l'IPC) ; l'offre de chaque entreprise s'en écarte selon sa marge, ses postes vacants et sa productivité. Les dividendes distribuent bénéfices et trésorerie excédentaire proportionnellement aux actions."),
        ("10 · Cinq compléments", "1. Apprentissage et perte graduelle de compétences selon l'emploi. 2. Bonus des salariés indexés sur les bénéfices. 3. Pertes de stocks et coût du capital emprunté. 4. Défaut, sortie des firmes insolvables et recouvrement limité aux liquidités disponibles. 5. Patrimoine calculé à partir des liquidités, actions et immobilier nets des dettes."),
    ]
    for title, description in improvements:
        st.markdown(f"**{title}**  \n{description}")
    st.markdown(
        "**Bilan et valorisation**  \n"
        "Les fiches distinguent actifs, dettes, capitaux propres et capitalisation. "
        "Le cours converge graduellement vers une estimation mêlant actifs nets, "
        "bénéfices anticipés et croissance récente des ventes ; les principaux ratios "
        "sont affichés lorsqu'ils sont définis."
    )
    st.markdown(
        "**Flux entre agents**  \n"
        "Les extracteurs vendent des ressources finies aux entreprises alimentaires et "
        "industrielles ; les entreprises technologiques vendent leurs solutions à d'autres "
        "firmes. Une fraction configurable des matières incorporées aux biens vendus est "
        "récupérée le mois suivant et rejoint un stock recyclé distinct des réserves vierges. "
        "Le paiement des intrants recyclés, des matériaux de construction, de la transformation, "
        "de l'entretien, de la recherche et de l'investissement va au secteur amont agrégé : "
        "il reverse chaque mois l'intégralité de ses recettes en salaires à des chômeurs "
        "(l'emploi suit l'activité, aucun fonds ne s'accumule). Quand il n'y a plus de chômeur "
        "à embaucher, le surplus est versé aux ménages actionnaires."
    )
    st.markdown(
        "**État, salaires et monnaie**  \n"
        "L'État dépense chaque mois son solde : allocations chômage indexées sur les prix, "
        "puis achats de biens aux entreprises, puis allocation égale par ménage ; le solde "
        "public reste proche de zéro. Le salaire de référence suit la productivité lissée, la "
        "tension du marché du travail et la rentabilité des producteurs (jamais l'IPC). "
        "L'emploi des producteurs suit la demande lissée, avec au plus 2 licenciements et 3 "
        "embauches par entreprise et par mois ; une fraction des salariés se sépare de son "
        "employeur chaque mois (chômage frictionnel). Les entreprises distribuent la trésorerie "
        "au-delà d'un fonds de roulement ; les banques restituent progressivement leur capital "
        "excédentaire (plus vite quand l'inflation est sous la cible) et prêtent pour "
        "l'investissement et la construction. Le PIB récurrent exclut la construction, comptée "
        "à part à sa valeur ajoutée (chantiers payés mois par mois)."
    )
    st.divider()
    st.subheader("Hypothèses et limites")
    st.markdown(
        """
        - Hypothèse d'échelle : les montants sont des euros nominaux simulés et les périodes des mois. Le salaire de référence initial est de 1 400 €/mois, volontairement inférieur à 14 000 €.
        - Un ménage représente un consommateur ; le panier comprend nourriture et biens courants.
        - Les entreprises suivent des règles heuristiques, sans prévisions complexes.
        - Le logement est homogène ; les ménages achètent au comptant ou par hypothèque (apport et mensualité plafonnés), sans expulsion.
        - Le bilan ne comprend que les actifs suivis et les dettes bancaires ; créances clients, dettes fournisseurs, obligations et autres passifs ne sont pas modélisés.
        - Les banques prêtent à partir de réserves finies (aucune création monétaire) ; dépôts et réglementation prudentielle ne sont pas modélisés. L'inflation est pilotée par la restitution du capital bancaire excédentaire, pas par un indexage des salaires ou des prix.
        - Le secteur amont est agrégé : pas de stock durable, pas de prix propres ; en plein emploi il verse son surplus aux ménages actionnaires.
        - Le cours est une estimation lissée, pas un carnet d'ordres ; les ratios annualisent le dernier mois. Les dividendes réduisent la trésorerie et donc les actifs comptables.
        - Les relations de fourniture couvrent les matières premières et la technologie, mais les chaînes agricoles, industrielles, énergétiques et logistiques restent à compléter.
        - Les actifs non liquides d'une entreprise défaillante sont dépréciés faute d'un marché de liquidation ; la récupération bancaire ne porte que sur la trésorerie.
        - Les fusions se font par échange d'actions au cours courant, sans examen antitrust, prime d'offre ou frais.
        - Le Gini se calcule sur la richesse nette des ménages, bornée à zéro lorsque la dette dépasse les actifs connus.
        - Les réserves vierges de connaissances et de matières premières sont épuisables ; une fraction configurable des intrants consommés peut être récupérée et réutilisée après un mois. Le recyclage ralentit l'épuisement mais ne garantit pas une économie stable après la raréfaction ; pénuries, hausses de prix et pertes restent possibles. Commerce extérieur et politique monétaire ne sont pas simulés.
        """
    )
    st.subheader("Ressources disponibles")
    resource_columns = st.columns(3)
    for index, (name, value) in enumerate(simulation.resource_summary().items()):
        resource_columns[index % len(resource_columns)].metric(name, f"{value:,.1f}")
