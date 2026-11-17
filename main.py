from __future__ import annotations

import pyarrow as pa
import streamlit as st
from altair.utils.schemapi import disable_debug_mode
from inspect import signature
from time import perf_counter
from uuid import uuid4

from economy.models import SimulationConfig
from economy.simulation import EconomySimulation, SECTORS
from economy.sectors import display_sectors, sector_label
from economy.warmup_cache import warm_many, warmed_simulation
from economy.advance_cache import AdvanceCache
from economy import world_views
from economy.world import World, WorldConfig, REGION_PROFILES, region_configs
from economy.institutions import (
    FLOW_LABELS,
    INDICATOR_LABELS,
    LEVER_TEXT,
    OFFICE_LABELS,
    OFFICE_ROLES,
    OFFICES,
    PROGRAM_LABELS,
    RESPONSE_LABELS,
    SERVICE_LABELS,
    SERVICE_TRADES,
    display_name,
    doctrine_summary,
    format_indicator,
)
from economy.power import LAW_EFFECTS, LAW_LABELS, LAW_MAJORITY, LAW_STEP, LAWS, TERM_LIMIT
from economy.interventions import (
    LEVERS,
    MONETARY_BOUNDS,
    MONETARY_LEVERS,
    POLICY_RATE_BOUNDS,
    SKILL_BOUNDS,
    adjust_company_cash,
    adjust_household_cash,
    cancel_orientation,
    clear_lever,
    release_bank_rate,
    release_policy_rate,
    request_orientation,
    set_bank_rate,
    set_household_skill,
    set_lever,
    set_policy_rate,
)
from economy.ownership import regime_intensities
from economy.enterprise import fund_assets
from economy.politics import orientation_label
from economy.wealth import (
    company_balance_sheet,
    company_valuation_metrics,
    household_net_worth,
    share_prices,
    wealth_distribution,
    wealth_leaderboards,
)

# Altair valide chaque objet d'un graphique dès sa création (« mode débogage », actif par
# défaut, coûteux : la moitié du temps d'un ``st.line_chart``). La spécification produite est
# la même sans ; la validation finale du graphique complet par ``to_dict`` reste faite.
disable_debug_mode()


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
    # (seuil d'affichage, unité de division, suffixe) : « k » commence à 10 000 mais divise par 1 000
    for threshold, unit_size, suffix in ((1e9, 1e9, " Md"), (1e6, 1e6, " M"), (1e4, 1e3, " k")):
        if magnitude >= threshold:
            text = f"{magnitude / unit_size:.1f}".replace(".", ",")
            return f"{sign}{text}{suffix}{unit}"
    return f"{sign}{magnitude:,.0f}".replace(",", " ") + unit


def _vega_chart(mark: str, data, x: str, y=None, title: str | None = None) -> None:
    """Graphique Vega-Lite direct (optimisation du 6 novembre 2026). ``st.line_chart`` construisait
    un graphique Altair puis le validait contre le schéma Vega-Lite à chaque rendu (environ 1,5 s
    sur six affichages au profil) ; ici, la spécification est écrite directement, les données
    passées au format long (une ligne par période et par série), sans validation. Mêmes
    arguments que les appels existants : ``x`` (colonne des abscisses) et ``y`` (une colonne, une
    liste, ou toutes les autres colonnes). ``title`` : titre de l'axe vertical ; à donner quand
    un sélecteur change la mesure tracée sans changer les séries : le navigateur ne redessine pas
    un graphique dont la spécification et la forme des données sont restées les mêmes."""
    rows = data if isinstance(data, list) else list(data)
    if y is None:
        names = [key for key in dict.fromkeys(k for row in rows for k in row) if key != x]
    else:
        names = [y] if isinstance(y, str) else list(y)
    # Streamlit peut sérialiser la table Arrow directement, sans dictionnaire par point
    # ni conversion intermédiaire en DataFrame Pandas.
    points = pa.table({
        "x": [row.get(x) for row in rows for _ in names],
        "Série": names * len(rows),
        "Valeur": [row.get(name) for row in rows for name in names],
    })
    nominal = mark == "bar"
    encoding = {
        "x": {"field": "x", "type": "nominal" if nominal else "quantitative", "title": x,
              **({"sort": None, "axis": {"labelAngle": 0}} if nominal else {})},
        "y": {"field": "Valeur", "type": "quantitative",
              "title": title if title is not None else names[0] if len(names) == 1 else None,
              **({"stack": "zero"} if mark == "area" else {"stack": None} if mark == "line" else {})},
        "tooltip": [{"field": "x", "title": x}, {"field": "Série"}, {"field": "Valeur", "format": ",.4~f"}],
    }
    if len(names) > 1:
        encoding["color"] = {"field": "Série", "type": "nominal", "sort": names, "title": None,
                             "legend": {"orient": "bottom"}}
    st.vega_lite_chart(points, {"mark": {"type": mark, "tooltip": True}, "encoding": encoding},
                       use_container_width=True)


def line_chart(data, x: str, y=None, title: str | None = None) -> None:
    _vega_chart("line", data, x, y, title)


def area_chart(data, x: str, y=None) -> None:
    _vega_chart("area", data, x, y)


def bar_chart(data, x: str, y=None) -> None:
    _vega_chart("bar", data, x, y)


def observed_rows(history: list, simulation: EconomySimulation) -> list:
    """Lignes d'un historique financier d'agent limitées aux mois observés (lot F2) : les
    ``period`` dernières lignes, une par mois depuis la fin de la chauffe. Une entreprise créée
    après la chauffe a moins de lignes : toutes sont observées."""
    period = simulation.period
    if period <= 0:
        return []
    return history[-period:]


def _annual_points(percent: float, months: int) -> float:
    """Pourcentage annuel de l'interface -> taux par période de ``months`` mois (1 : annuel,
    12 : mensuel), en passant par les points de base entiers : 1,44 % donne exactement 0,0012
    par mois (``1.44 / 1200`` aussi, mais ``0.96 / 1200`` ne donne pas 0,0008)."""
    return round(float(percent) * 100.0) / (10000.0 * months)


def new_simulation() -> EconomySimulation:
    """Simulation après la chauffe pour les réglages de la barre latérale. L'état après chauffe est
    mémorisé (``economy.warmup_cache`` : mêmes réglages et même code, même état, bit pour bit) :
    une réinitialisation déjà calculée ne refait pas les 120 mois de chauffe."""
    cache = st.session_state.get("_advance_cache")
    if cache is not None:
        cache.invalidate()
    config = SimulationConfig(
            population_size=st.session_state.population_size,
            companies_per_sector=st.session_state.companies_per_sector,
            warmup_months=12 * st.session_state.warmup_years,
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
            startup_threshold=st.session_state.startup_threshold,
            startup_investment_fraction=st.session_state.startup_investment_percent / 100,
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
            stock_book_value_weight=st.session_state.stock_book_value_weight_percent / 100,
            equity_risk_premium=st.session_state.equity_risk_premium_percent / 100,
            b2b_price_elasticity=st.session_state.b2b_price_elasticity,
            control_premium=st.session_state.control_premium_percent / 100,
            vat_rate=st.session_state.vat_percent / 100,
            stock_adjustment_speed=st.session_state.stock_adjustment_speed_percent / 100,
            cpi_food_weight=st.session_state.cpi_food_weight_percent / 100,
            cpi_goods_weight=st.session_state.cpi_goods_weight_percent / 100,
            cpi_rent_weight=st.session_state.cpi_rent_weight_percent / 100,
            natural_unemployment_rate=st.session_state.natural_unemployment_percent / 100,
            job_separation_rate=st.session_state.job_separation_percent / 100,
            working_capital_months=st.session_state.working_capital_months,
            credit_financed_share=st.session_state.credit_financed_percent / 100,
            government_spending_rate=st.session_state.government_spending_percent / 100,
            profit_margin_target=st.session_state.profit_margin_target_percent / 100,
            bank_excess_payout_rate=st.session_state.bank_excess_payout_percent / 100,
            reserve_requirement=st.session_state.reserve_requirement_percent / 100,
            capital_requirement=st.session_state.capital_requirement_percent / 100,
            credit_return_gain=st.session_state.credit_return_gain,
            credit_leverage_spread=st.session_state.credit_leverage_spread_annual / 1200,
            political_cycle_enabled=st.session_state.political_cycle_enabled,
            economic_voting=st.session_state.economic_voting,
            initial_political_orientation=float(st.session_state.initial_political_orientation),
            election_period_months=st.session_state.election_period_years * 12,
            policy_strength=st.session_state.policy_strength_percent / 100,
            policy_noise=st.session_state.policy_noise_percent / 100,
            knowledge_elasticity=st.session_state.knowledge_elasticity,
            knowledge_growth_per_intensity=st.session_state.knowledge_growth_per_intensity,
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
            income_tax_threshold=st.session_state.income_tax_threshold,
            top_marginal_income_tax_rate=st.session_state.top_marginal_income_tax_percent / 100,
            inflation_target=st.session_state.inflation_target_percent / 100,
            taylor_inflation_coefficient=st.session_state.taylor_inflation_coefficient,
            taylor_unemployment_coefficient=st.session_state.taylor_unemployment_coefficient,
            taylor_neutral_real_rate=_annual_points(st.session_state.taylor_neutral_real_rate_percent, 1),
            policy_rate_cap=_annual_points(st.session_state.policy_rate_cap_percent, 1),
            policy_rate_smoothing=st.session_state.policy_rate_smoothing,
            mortgage_spread=_annual_points(st.session_state.mortgage_spread_annual, 12),
            consumer_credit_spread=_annual_points(st.session_state.consumer_credit_spread_annual, 12),
            deposit_spread=_annual_points(st.session_state.deposit_spread_annual, 12),
            bank_margin_offset=_annual_points(st.session_state.bank_margin_offset_annual, 12),
            mortgage_down_payment_rate=st.session_state.mortgage_down_payment_percent / 100,
            mortgage_payment_income_share=st.session_state.mortgage_payment_income_percent / 100,
            mortgage_interest_rate=st.session_state.mortgage_interest_percent / 100,
            mortgage_term_months=st.session_state.mortgage_term_years * 12,
            monthly_population_growth_rate=st.session_state.population_growth_percent / 100,
            maximum_population_growth=st.session_state.maximum_population_growth_percent / 100,
            quality_trait_variance=st.session_state.quality_trait_variance_percent / 100,
            quality_preference_strength=st.session_state.quality_preference_percent / 100,
            productive_capacity_unit_cost=st.session_state.capacity_unit_cost,
            technology_productivity=st.session_state.technology_productivity,
            technology_price_growth=st.session_state.technology_price_growth_percent / 100,
            technology_business_max_level=st.session_state.technology_business_max_level,
            technology_household_budget_share=st.session_state.household_technology_budget_percent / 100,
            household_stock_investment_rate=st.session_state.household_investment_percent / 100,
            manager_wage_multiplier=st.session_state.manager_wage_multiplier / 100,
            specialist_wage_multiplier=st.session_state.specialist_wage_multiplier / 100,
            seed=st.session_state.seed,
            cabinet_refusal_tolerance=st.session_state.cabinet_refusal_tolerance_percent / 100,
            minimum_wage_boost_cap=st.session_state.minimum_wage_boost_cap_percent / 100,
            critical_sectors=tuple(st.session_state.critical_sectors),
            enriched_objectives=bool(st.session_state.enriched_objectives),
            objective_effort=st.session_state.objective_effort_percent / 100,
            volume_discount_max=st.session_state.volume_discount_max_percent / 100,
            fixed_cost_share=st.session_state.fixed_cost_share_percent / 100,
            # lot T : offres publiques réalistes, croissance proportionnelle des effectifs
            synergy_takeovers=bool(st.session_state.synergy_takeovers),
            takeover_share_payment=st.session_state.takeover_share_payment_percent / 100,
            proportional_hiring=bool(st.session_state.proportional_hiring),
            staff_change_share=st.session_state.staff_change_share_percent / 100,
            safeguard_procedure=bool(st.session_state.safeguard_procedure),
            raw_material_scarcity=bool(st.session_state.raw_material_scarcity),
            # lot U : impôt borné par la trésorerie, titres publics pondérés, embauche tirée par la
            # demande, encadrement des prix levé en cas de pénurie
            tax_capped_by_cash=bool(st.session_state.tax_capped_by_cash),
            tax_backlog_months=int(st.session_state.tax_backlog_months),
            sovereign_risk_weight=st.session_state.sovereign_risk_weight_percent / 100,
            demand_led_hiring=bool(st.session_state.demand_led_hiring),
            demand_hiring_share=st.session_state.demand_hiring_share_percent / 100,
            shortage_price_release=bool(st.session_state.shortage_price_release),
            price_control_shortage_limit=st.session_state.price_control_shortage_limit_percent / 100,
            scarcity_rent=st.session_state.scarcity_rent,
            scarcity_exploration_response=st.session_state.scarcity_exploration_response,
            ownership_regime=bool(st.session_state.ownership_regime),
            countercyclical_capital=bool(st.session_state.countercyclical_capital),
            bank_capital_backstop=bool(st.session_state.bank_capital_backstop),
            stock_earnings_smoothing_months=int(st.session_state.stock_earnings_smoothing_months),
            # lot G : huit mécanismes d'entreprise (désactivés par défaut)
            experience_learning=bool(st.session_state.experience_learning),
            experience_gain=st.session_state.experience_gain_percent / 100,
            property_upkeep=bool(st.session_state.property_upkeep),
            property_wear_rate=_annual_points(st.session_state.property_wear_annual, 12),
            consumer_preferences=bool(st.session_state.consumer_preferences),
            preference_strength=st.session_state.preference_strength_percent / 100,
            data_budget_share=_annual_points(st.session_state.data_budget_percent, 1),
            diversification=bool(st.session_state.diversification),
            conglomerate_takeovers=bool(st.session_state.conglomerate_takeovers),
            retail_stores=bool(st.session_state.retail_stores),
            store_opening_cost=float(st.session_state.store_opening_cost),
            store_running_cost=float(st.session_state.store_running_cost),
            store_reach=st.session_state.store_reach_percent / 100,
            group_distribution=bool(st.session_state.group_distribution),
            private_startups=bool(st.session_state.private_startups),
            fund_savings_share=st.session_state.fund_savings_percent / 100,
            fund_fee_rate=_annual_points(st.session_state.fund_fee_annual, 12),
            fund_exit_months=int(st.session_state.fund_exit_years) * 12,
            # lot H : salariés obligatoires, conseil d'actionnaires (désactivés par défaut)
            staffless_closure_months=int(st.session_state.staffless_closure_months),
            shareholder_governance=bool(st.session_state.shareholder_governance),
            # lot J : obligations d'entreprise, commerce extérieur, négociation collective
            corporate_bonds=bool(st.session_state.corporate_bonds),
            corporate_bond_share=st.session_state.corporate_bond_percent / 100,
            foreign_trade=bool(st.session_state.foreign_trade),
            trade_openness=st.session_state.trade_openness_percent / 100,
            collective_bargaining=bool(st.session_state.collective_bargaining),
            union_power=st.session_state.union_power_percent / 100,
            union_max_raise=st.session_state.union_max_raise_percent / 100,
            # lot K : influence, popularité, pouvoir présidentiel, aides et favoritisme
            influence_ranking=bool(st.session_state.influence_ranking),
            personal_popularity=bool(st.session_state.personal_popularity),
            candidate_elections=bool(st.session_state.candidate_elections),
            vote_popularity_weight=float(st.session_state.vote_popularity_weight),
            executive_power_laws=bool(st.session_state.executive_power_laws),
            business_subsidies=bool(st.session_state.business_subsidies),
            business_aid_gdp_share=_annual_points(st.session_state.business_aid_gdp_percent, 1),
            political_favoritism=bool(st.session_state.political_favoritism),
            favoritism_monthly_chance=st.session_state.favoritism_chance_percent / 100,
            audit_detection_chance=st.session_state.audit_detection_percent / 100,
            corporate_lobbying=bool(st.session_state.corporate_lobbying),
            revolving_door=bool(st.session_state.revolving_door),
            political_dynasties=bool(st.session_state.political_dynasties),
            media_sector=bool(st.session_state.media_sector),
            media_outlets=int(st.session_state.media_outlets),
            media_subscription_share=st.session_state.media_subscription_percent / 100,
            media_ad_share=st.session_state.media_ad_percent / 100,
            media_ad_reach=st.session_state.media_ad_reach_percent / 100,
            media_vote_weight=st.session_state.media_vote_percent / 100,
            bank_relationships=bool(st.session_state.bank_relationships),
            bank_review_share=st.session_state.bank_review_percent / 100,
            bank_run_threshold=float(st.session_state.bank_run_threshold),
            bank_specialization=float(st.session_state.bank_specialization),
            adaptive_agents=bool(st.session_state.adaptive_agents),
            learning_period_months=int(st.session_state.learning_period_months),
            learning_step=st.session_state.learning_step_percent / 100,
            imitation_rate=st.session_state.imitation_percent / 100,
            # lot V : douze mécanismes de société (economy/society.py, economy/ventures.py)
            personal_bankruptcy=bool(st.session_state.personal_bankruptcy),
            credit_ban_months=12 * int(st.session_state.credit_ban_years),
            philanthropy=bool(st.session_state.philanthropy),
            philanthropy_wealth_multiple=float(st.session_state.philanthropy_wealth_multiple),
            state_lottery=bool(st.session_state.state_lottery),
            lottery_stake_share=st.session_state.lottery_stake_percent / 100,
            addictive_goods=bool(st.session_state.addictive_goods),
            addiction_excise_rate=st.session_state.addiction_excise_percent / 100,
            addiction_technology_gain=st.session_state.addiction_technology_gain_percent / 100,
            franchising=bool(st.session_state.franchising),
            franchise_royalty_rate=st.session_state.franchise_royalty_percent / 100,
            brand_marketing=bool(st.session_state.brand_marketing),
            marketing_budget_share=st.session_state.marketing_budget_percent / 100,
            worker_buyouts=bool(st.session_state.worker_buyouts),
            buyout_benefit_months=int(st.session_state.buyout_benefit_months),
            short_selling=bool(st.session_state.short_selling),
            short_float_share=st.session_state.short_float_percent / 100,
            mortgage_securitization=bool(st.session_state.mortgage_securitization),
            securitization_share=st.session_state.securitization_percent / 100,
            crowdfunding=bool(st.session_state.crowdfunding),
            crowdfunding_savings_share=st.session_state.crowdfunding_savings_percent / 100,
            social_unrest=bool(st.session_state.social_unrest),
            unrest_unemployment=st.session_state.unrest_unemployment_percent / 100,
            public_works=bool(st.session_state.public_works),
            public_works_gdp_share=st.session_state.public_works_gdp_percent / 100,
        )
    st.session_state.pop("active_region", None)
    if st.session_state.world_enabled:
        world_config = WorldConfig(
            base=config, regions=int(st.session_state.world_regions),
            profiles=None if st.session_state.world_profiles else (),
            integration_month=int(st.session_state.integration_month),
            interregional_trade=bool(st.session_state.interregional_trade),
            transport_cost=st.session_state.transport_percent / 100,
            migration=bool(st.session_state.migration),
            migration_monthly_share=st.session_state.migration_monthly_percent / 100,
            migration_gain=st.session_state.migration_gain_percent / 100,
            commuting=bool(st.session_state.commuting),
            commuting_monthly_share=st.session_state.commuting_monthly_percent / 100,
            commuting_gain=st.session_state.commuting_gain_percent / 100,
            cross_border_takeovers=bool(st.session_state.cross_border_takeovers),
            investment_screening=bool(st.session_state.investment_screening),
            yardstick_voting=bool(st.session_state.yardstick_voting),
            yardstick_weight=float(st.session_state.yardstick_weight),
            tax_competition=bool(st.session_state.tax_competition),
            tax_competition_weight=float(st.session_state.tax_competition_weight),
            clearing_holding_share=st.session_state.clearing_holding_percent / 100,
        )
        # chauffes des régions en parallèle (un processus par région manquante), puis lues ici
        warm_many(region_configs(world_config))
        world = World(world_config, factory=warmed_simulation)
        st.session_state.world = world
        return world.regions[0]
    st.session_state.world = None
    return warmed_simulation(config)


def advance_simulation(months: int | None = None) -> None:
    """Avance avant le rendu : une seule exécution affiche le nouvel état."""
    count = int(st.session_state.run_months) if months is None else months
    world = st.session_state.get("world")
    engine = world or st.session_state.simulation
    cache = st.session_state.get("_advance_cache")
    prepared = (cache.take_progress(engine, st.session_state.get("_advance_revision"), count)
                if cache is not None and st.session_state.get("prepare_advance", True) else None)
    done = 0
    if prepared is not None:
        # avance préparée pendant la lecture : adoptée entière, ou reprise au mois déjà atteint
        engine, done = prepared
        if world is not None:
            st.session_state.world = engine
            st.session_state.simulation = engine.regions[st.session_state.get("active_region", 0)]
        else:
            st.session_state.simulation = engine
        if done >= count:
            return
    progress = st.progress(done / count, text=f"Avance : {done} / {count} mois")
    last_update = perf_counter()
    for index in range(done, count):
        engine.step()
        now = perf_counter()
        if now - last_update >= 0.15 or index + 1 == count:
            progress.progress((index + 1) / count, text=f"Avance : {index + 1} / {count} mois")
            last_update = now
    progress.empty()


def select_region() -> None:
    """Change la région inspectée ; les commandes d'avance font tourner le monde entier."""
    world = st.session_state.world
    st.session_state.simulation = world.regions[st.session_state.active_region]
    for key in ("company_selector", "household_selector", "stock_chart_companies",
                "_intervention_message", "_rate_message"):
        st.session_state.pop(key, None)
    st.session_state._view_id = uuid4().hex


# -- mode intervention (economy/interventions.py) ------------------------------------------
# Les rappels s'exécutent avant le rendu ; un nouvel identifiant de vue invalide les blocs mis
# en cache par (vue, mois), qui affichent ainsi l'état modifié sans attendre le mois suivant.
def _after_intervention(message: str, slot: str = "_intervention_message") -> None:
    """``slot`` : panneau qui affiche le message (taux : ``_rate_message``, lot B)."""
    st.session_state[slot] = message
    st.session_state._view_id = uuid4().hex
    cache = st.session_state.get("_advance_cache")
    if cache is not None:
        cache.invalidate()


def _cash_intervention(kind: str, target_id: str, sign: float, key: str) -> None:
    simulation = st.session_state.simulation
    amount = sign * float(st.session_state.get(key, 0.0))
    adjust = adjust_household_cash if kind == "household" else adjust_company_cash
    applied = adjust(simulation, target_id, amount)
    _after_intervention(
        f"{applied:+,.0f} € appliqués à la trésorerie.".replace(",", " ")
        if applied else "Aucun montant appliqué (trésorerie déjà nulle ou montant nul)."
    )


def _skill_intervention(person_id: str, key: str) -> None:
    value = set_household_skill(st.session_state.simulation, person_id, st.session_state[key])
    _after_intervention(f"Compétence fixée à {value:.2f}.")


def _orientation_intervention() -> None:
    value = float(st.session_state.forced_orientation)
    request_orientation(st.session_state.simulation, value)
    _after_intervention(
        f"Gouvernement {orientation_label(value)} ({value:.0f} / 100) demandé : il sera formé au "
        "début du prochain mois et gouvernera jusqu'à la prochaine élection."
    )


def _cancel_orientation_intervention() -> None:
    cancel_orientation(st.session_state.simulation)
    _after_intervention("Demande de gouvernement annulée.")


def _lever_intervention(name: str, key: str, percent: bool) -> None:
    value = float(st.session_state[key]) / (100.0 if percent else 1.0)
    set_lever(st.session_state.simulation, name, value)
    _after_intervention(f"{LEVERS[name][0]} forcé jusqu'à la prochaine élection (effet dès le prochain mois).")


def _release_lever(name: str | None) -> None:
    simulation = st.session_state.simulation
    operator = getattr(simulation, "interventions", None)
    names = [name] if name else list(operator.levers) if operator else []
    for lever in names:
        clear_lever(simulation, lever)
    _after_intervention("Leviers rendus au gouvernement (effet dès le prochain mois).")


def _policy_rate_intervention() -> None:
    value = set_policy_rate(st.session_state.simulation, float(st.session_state.forced_policy_rate) / 100.0)
    _after_intervention(f"Taux directeur forcé à {value:.2%} jusqu'au relâchement (effet dès le prochain mois).",
                        "_rate_message")


def _release_policy_rate() -> None:
    release_policy_rate(st.session_state.simulation)
    _after_intervention("Taux directeur rendu à la règle de Taylor (elle repart du taux forcé, avec lissage).",
                        "_rate_message")


def _bank_rate_intervention(name: str, key: str) -> None:
    value = set_bank_rate(st.session_state.simulation, name, float(st.session_state[key]) / 100.0)
    _after_intervention(f"{MONETARY_LEVERS[name]} : {value:.2%}, valeur forcée jusqu'au relâchement "
                        "(effet dès le prochain mois).", "_rate_message")


def _release_bank_rate(name: str) -> None:
    release_bank_rate(st.session_state.simulation, name)
    _after_intervention(f"{MONETARY_LEVERS[name]} : valeur de base rétablie (effet dès le prochain mois).",
                        "_rate_message")


def show_intervention_message(slot: str = "_intervention_message") -> None:
    message = st.session_state.pop(slot, None)
    if message:
        st.success(message, icon="🛠")


def render_cash_intervention(kind: str, target_id: str, cash: float) -> None:
    """Boutons ＋ / − sur la trésorerie d'un ménage ou d'une entreprise (changement définitif)."""
    key = f"{kind}_cash_amount"
    columns = st.columns([2, 1, 1])
    columns[0].number_input(
        "Montant (€)", min_value=0.0, value=10000.0, step=1000.0, format="%.0f", key=key,
        help="Ajouté ou retiré immédiatement ; l'agent garde la somme jusqu'à ce qu'il la dépense. "
             "L'argent ajouté est émis comme de la monnaie (billets), un retrait en détruit ; "
             "un retrait est limité à la trésorerie disponible.",
    )
    columns[1].button("＋ Ajouter", key=f"{kind}_cash_plus", use_container_width=True,
                      on_click=_cash_intervention, args=(kind, target_id, 1.0, key))
    columns[2].button("− Retirer", key=f"{kind}_cash_minus", use_container_width=True,
                      on_click=_cash_intervention, args=(kind, target_id, -1.0, key),
                      disabled=cash <= 0.0)


def render_state_interventions(simulation: EconomySimulation) -> None:
    """Panneau de l'onglet État : gouvernement imposé et leviers forcés jusqu'à l'élection."""
    operator = getattr(simulation, "interventions", None)
    government = simulation.government
    imposed = bool(operator and operator.forced_months and operator.forced_months[-1] == government.since_month)
    forced = dict(operator.levers) if operator else {}
    title = "🛠 Intervenir sur l'État (jusqu'à la prochaine élection)"
    if imposed or forced or (operator and operator.pending_orientation is not None):
        title += " · intervention en cours"
    with st.expander("🛠 Intervenir sur l'État (jusqu'à la prochaine élection)",
                     **({"key": "state_intervention_panel"} if LAZY_EXPANDERS else {})):
        if title.endswith("en cours"):
            st.caption("Intervention en cours.")
        show_intervention_message()
        st.markdown("**Couleur du gouvernement**")
        columns = st.columns([3, 1])
        columns[0].slider(
            "Orientation à imposer (0 = libéral, à droite ; 100 = social, à gauche)", 0, 100,
            value=int(round(government.orientation)), key="forced_orientation",
            help="Un nouveau gouvernement de cette orientation est formé au début du prochain mois, "
                 "comme après une élection : doctrine, ministres, loi de finances rectificative. Il "
                 "gouverne jusqu'à la prochaine élection, qui a lieu à la date prévue et se déroule "
                 "normalement. Les leviers forcés sont alors rendus à ses propres lois.",
        )
        columns[0].caption(f"Gouvernement visé : {orientation_label(st.session_state.forced_orientation)}")
        columns[1].button("Imposer ce gouvernement", type="primary", use_container_width=True,
                          on_click=_orientation_intervention)
        if operator and operator.pending_orientation is not None:
            st.info(f"En attente : gouvernement {orientation_label(operator.pending_orientation)} "
                    f"({operator.pending_orientation:.0f} / 100), formé au début du prochain mois.")
            st.button("Annuler la demande", on_click=_cancel_orientation_intervention)
        elif imposed:
            st.caption(f"Le gouvernement en place ({government.orientation:.0f} / 100) a été imposé ; "
                       "la prochaine élection redonne la main aux électeurs.")

        st.markdown("**Leviers forcés**")
        st.caption("La valeur forcée remplace chaque mois celle des lois du gouvernement, jusqu'à la "
                   "prochaine élection ou au prochain gouvernement imposé ; la valeur votée reprend "
                   "alors la main.")
        for name, (label, unit) in LEVERS.items():
            percent = unit == "%"
            scale = 100.0 if percent else 1.0
            voted = operator.voted.get(name) if operator else None
            voted = getattr(simulation.live, name) if voted is None else voted
            row = st.columns([2.2, 1.4, 1.2, 1.2])
            shown = f"{voted * scale:.1f} %" if percent else f"{voted:,.0f} €".replace(",", " ")
            text = f"{label}  \n<span class='section-note'>voté : {shown}</span>"
            if name in forced:
                value = forced[name] * scale
                text += " · **forcé : " + (f"{value:.1f} %" if percent else f"{value:,.0f} €".replace(",", " ")) + "**"
            row[0].markdown(text, unsafe_allow_html=True)
            key = f"lever_{name}"
            row[1].number_input(
                label, min_value=0.0, max_value=100.0 if percent else None,
                value=float(round(forced.get(name, voted) * scale, 2)),
                step=0.5 if percent else 10.0, format="%.1f" if percent else "%.0f",
                key=key, label_visibility="collapsed",
            )
            row[2].button("Forcer", key=f"{key}_set", use_container_width=True,
                          on_click=_lever_intervention, args=(name, key, percent))
            row[3].button("Rendre", key=f"{key}_clear", use_container_width=True,
                          on_click=_release_lever, args=(name,), disabled=name not in forced)
        if forced:
            st.button("Tout rendre au gouvernement", on_click=_release_lever, args=(None,))
        if operator and operator.journal:
            st.markdown("**Journal des interventions**")
            st.dataframe(list(reversed(operator.journal)), hide_index=True, use_container_width=True)
            st.caption(f"Monnaie émise nette par les interventions : "
                       f"{operator.issued_money:,.0f} €".replace(",", " "))


def render_rate_interventions(simulation: EconomySimulation) -> None:
    """Panneau de l'onglet État (lot B) : taux directeur forcé à la place de la règle de Taylor,
    marge des banques commerciales et écart des dépôts, jusqu'au relâchement (pas de fin à
    l'élection). Les actions vont au même journal que celles du panneau de l'État."""
    operator = getattr(simulation, "interventions", None)
    forced_rate = getattr(operator, "forced_policy_rate", None) if operator else None
    monetary = dict(getattr(operator, "monetary", {}) or {}) if operator else {}
    title = "🛠 Intervenir sur les taux d'intérêt (banque centrale et banques)"
    with st.expander(title, **({"key": "rate_intervention_panel"} if LAZY_EXPANDERS else {})):
        show_intervention_message("_rate_message")
        st.markdown("**Taux directeur de la banque centrale**")
        st.caption(
            f"Taux actuel : {12.0 * simulation.policy_rate:.2%} par an "
            + ("· **forcé** : la règle de Taylor est suspendue." if forced_rate is not None
               else "· fixé par la règle de Taylor (inflation, chômage, lissage).")
            + " Un taux forcé reste en place après les élections, jusqu'à ce qu'il soit relâché."
        )
        low, high = POLICY_RATE_BOUNDS
        row = st.columns([2.2, 1.2, 1.2])
        row[0].number_input(
            "Taux directeur annuel (%)", min_value=100.0 * low, max_value=100.0 * high,
            value=float(round(100.0 * (forced_rate if forced_rate is not None else 12.0 * simulation.policy_rate), 2)),
            step=0.25, format="%.2f", key="forced_policy_rate",
        )
        row[1].button("Forcer", key="forced_policy_rate_set", use_container_width=True,
                      on_click=_policy_rate_intervention)
        row[2].button("Relâcher", key="forced_policy_rate_clear", use_container_width=True,
                      on_click=_release_policy_rate, disabled=forced_rate is None)

        st.markdown("**Banques commerciales**")
        st.caption(
            "La marge supplémentaire s'ajoute à tous les nouveaux crédits (entreprises, immobilier, "
            "consommation) ; l'écart des dépôts fixe le taux de référence servi aux déposants "
            "(taux directeur − écart), que chaque banque ajuste selon sa liquidité. Valeurs annuelles."
        )
        for name, label in MONETARY_LEVERS.items():
            base = 12.0 * getattr(simulation.config, name)
            row = st.columns([2.2, 1.4, 1.2, 1.2])
            text = f"{label}  \n<span class='section-note'>base : {base:.2%}</span>"
            if name in monetary:
                text += f" · **forcé : {12.0 * monetary[name]:.2%}**"
            row[0].markdown(text, unsafe_allow_html=True)
            key = f"bank_rate_{name}"
            bound_low, bound_high = MONETARY_BOUNDS[name]
            row[1].number_input(
                label, min_value=100.0 * bound_low, max_value=100.0 * bound_high,
                value=float(round(100.0 * 12.0 * monetary.get(name, base / 12.0), 2)),
                step=0.12, format="%.2f", key=key, label_visibility="collapsed",
            )
            row[2].button("Forcer", key=f"{key}_set", use_container_width=True,
                          on_click=_bank_rate_intervention, args=(name, key))
            row[3].button("Relâcher", key=f"{key}_clear", use_container_width=True,
                          on_click=_release_bank_rate, args=(name,), disabled=name not in monetary)
        st.caption("Le journal des interventions est affiché dans le panneau « Intervenir sur l'État ».")


LAW_LINE_LABELS = {
    "personnel": "Personnel (agents et cabinet)", "allocations": "Allocations chômage",
    "interets": "Intérêts de la dette", "entretien": "Entretien du capital public",
    "formation": "Formations financées", **PROGRAM_LABELS,
}
# ligne de la loi -> catégories du livre de trésorerie qui l'exécutent
LAW_LINE_FLOWS = {
    "personnel": ("salaires_cabinet", "salaires_agents"), "allocations": ("allocations",),
    "interets": ("interets",), "entretien": ("entretien",), "formation": ("formation",),
    "transferts": ("transferts",), "achats": ("achats",), "investissement": ("investissement",),
}


LAZY_TABS = "on_change" in signature(st.tabs).parameters
LAZY_EXPANDERS = "on_change" in signature(st.expander).parameters


def result_tabs(labels: list[str], key: str):
    """Onglets suivis par Streamlit, avec rendu classique sur les anciennes versions."""
    return st.tabs(labels, **({"on_change": "rerun", "key": key} if LAZY_TABS else {}))


def tab_is_open(tab) -> bool:
    return not LAZY_TABS or tab.open


def render_institutional_state(simulation: EconomySimulation) -> None:
    inst = getattr(simulation, "institutions", None)
    if inst is None:
        st.info("Le nouvel État est créé au premier mois simulé.")
        return
    view = (st.session_state._view_id, simulation.month)
    render_state_head(*view, simulation)
    render_state_interventions(simulation)
    render_rate_interventions(simulation)
    government, budget, services, wages, industry, journal, power = result_tabs(
        ["Gouvernement", "Budget", "Fonction publique", "Salaire minimum", "Industrie",
         "Journal", "Pouvoir & influence"],
        "state_section",
    )
    for tab, renderer in (
        (government, render_state_government), (budget, render_state_budget),
        (wages, render_state_minimum_wage), (industry, render_state_industry),
    ):
        if tab_is_open(tab):
            with tab:
                renderer(*view, simulation)
    if tab_is_open(services):
        with services:
            render_state_services(*view, simulation)
            choice = st.selectbox("Service", ["Tous", *SERVICE_LABELS], key="public_service_filter",
                                  format_func=lambda key: key if key == "Tous" else SERVICE_LABELS[key])
            render_state_agents(*view, choice, simulation)
    if tab_is_open(journal):
        with journal:
            authors = ["Tous", *dict.fromkeys(d.author for d in inst.journal)]
            author = st.selectbox("Auteur", authors, key="state_journal_author")
            render_state_journal(*view, author, simulation)
    if tab_is_open(power):
        with power:
            power_tabs = result_tabs(
                ["Influence", "Popularité & élections", "Lois de pouvoir", "Aides & favoritisme", "Réseaux"],
                "power_section",
            )
            for tab, renderer in zip(power_tabs, (
                render_power_influence, render_power_popularity, render_power_laws,
                render_power_aid, render_power_networks,
            )):
                if tab_is_open(tab):
                    with tab:
                        renderer(*view, simulation)


def _state_context(simulation: EconomySimulation):
    """Objets communs aux blocs de l'onglet État."""
    return (
        simulation.institutions,
        simulation.warmup_offset,
        {p.id: p for p in simulation.people},
        {c.id: c for c in simulation.companies},
    )


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_head(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """En-tête de l'onglet État (sans widget)."""
    simulation = _simulation
    inst, offset, people, companies = _state_context(simulation)
    government = simulation.government
    annual_gdp = max(simulation._annual_gdp(), 1.0)
    period = simulation.config.election_period_months
    next_election = (
        (simulation.month - 1) // period * period + 1 + period
        if simulation.config.political_cycle_enabled else None
    )
    median = simulation._median_private_wage()
    st.subheader("L'État : institutions")
    st.markdown(
        '<p class="section-note">Présidence et quatre ministres nommés parmi les ménages, loi de '
        "finances, fonction publique, salaire minimum légal, politique industrielle. Chaque décision "
        "a un auteur, une date, un motif, un montant et un financement (journal).</p>",
        unsafe_allow_html=True,
    )
    head = st.columns(4)
    head[0].metric("Gouvernement", government.label, f"orientation {government.orientation:.0f} / 100", delta_color="off")
    head[1].metric(
        "Prochaine élection",
        f"période {next_election - offset}" if next_election else "aucune",
        f"dans {next_election - simulation.month} mois" if next_election else None, delta_color="off",
    )
    head[2].metric(
        "Dette publique", f"{simulation.public.debt / annual_gdp:.0%} du PIB annuel",
        f"limite légale {simulation.live.debt_ceiling_gdp:.0%}", delta_color="off",
        help="Aucun ratio visé : la dette résulte des lois de finances successives. La limite légale est une règle institutionnelle.",
    )
    head[3].metric(
        "Salaire minimum légal", f"{inst.minimum_wage:,.0f} €".replace(",", " "),
        f"{inst.minimum_wage / median:.0%} du salaire médian du privé" if median > 0 else None, delta_color="off",
    )


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_government(view_id: str, month: int, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        government = simulation.government
        st.markdown(f"**Programme du gouvernement** · {government.label} ({government.orientation:.0f} / 100)"
                    + (" · réélu" if government.reelected else ""))
        st.dataframe(
            [{"Doctrine": key, "Contenu": value} for key, value in doctrine_summary(inst.doctrine).items()],
            hide_index=True,
        )
        rows = []
        goals = []  # lot C : objectifs principal et secondaire, avec leur levier
        for role in OFFICES:
            office = inst.offices.get(role)
            person = people.get(office.person_id) if office else None
            if person is None:
                continue
            objective = office.objective
            rows.append({
                "Fonction": OFFICE_ROLES[role],
                "Nom": f"{display_name(person.id)} ({person.name})",
                "Métier": person.trade,
                "Ancien employeur": office.previous_employer,
                "Patrimoine (€)": simulation.household_wealth(person),
                "Orientation personnelle": round(inst.lean.get(person.id, 50.0)),
                "Objectif": objective.label if objective else "porter le programme, arbitrer",
                "Indicateur": INDICATOR_LABELS.get(objective.indicator, "") if objective else "",
                "Valeur": objective.value if objective else None,
                "Progression": objective.progress if objective else None,
                "Échéance (période)": objective.deadline - offset if objective else None,
                "Salaire (€)": person.current_wage,
            })
            if office.secondary is not None:
                for goal in office.objectives():
                    goals.append({
                        "Ministère": OFFICE_LABELS[role],
                        "Rang": goal.rank,
                        "Objectif": goal.label,
                        "Départ": format_indicator(goal.indicator, goal.baseline),
                        "Cible": format_indicator(goal.indicator, goal.target),
                        "Valeur": format_indicator(goal.indicator, goal.value),
                        "Progression": goal.progress,
                        "Atteint": "oui" if goal.met(goal.value) else "non",
                        "Levier en cas de retard": LEVER_TEXT.get(goal.lever, ""),
                        "Enveloppe annuelle (€)": goal.envelope,
                    })
        st.markdown("**Présidence et ministres**")
        st.dataframe(rows, hide_index=True,
                     column_config={"Progression": st.column_config.NumberColumn(format="percent")})
        if goals:
            st.markdown("**Objectifs des ministres : principal et secondaire**")
            st.caption(
                "Chaque objectif en retard actionne le levier de son ministère (secondaire : "
                f"× {simulation.live.secondary_objective_weight:.2f} de l'effort) ; au vote, le bilan "
                "pondère les secondaires du même coefficient. Une cible déjà remplie au départ est relevée."
            )
            st.dataframe(goals, hide_index=True, use_container_width=True,
                         column_config={"Progression": st.column_config.ProgressColumn(
                             format="percent", min_value=-1.0, max_value=1.0),
                             "Enveloppe annuelle (€)": st.column_config.NumberColumn(format="%.0f")})
        pay = st.columns(3)
        pay[0].metric("Salaire de la présidence", f"{inst.salaries.get('presidence', 0.0):,.0f} €".replace(",", " "))
        pay[1].metric("Salaire d'un ministre", f"{inst.salaries.get('ministre', 0.0):,.0f} €".replace(",", " "))
        pay[2].metric(
            "Hausse décidée (révision suivante)",
            f"{inst.pending_raise['presidence']:,.0f} €".replace(",", " ") if inst.pending_raise else "aucune",
            f"majoration d'attractivité × {inst.salary_markup:.2f}", delta_color="off",
            help="Indexation sur le haut de la grille publique et la rémunération des dirigeants du privé (décote publique) ; des refus de nomination relèvent la majoration.",
        )
        records = [r for r in inst.history if r.month >= offset]
        if len(records) > 1:
            st.caption("Salaires politiques")
            line_chart(
                [{"Période": r.month - offset, "Présidence": r.president_salary, "Ministre": r.minister_salary}
                 for r in records], x="Période", y=["Présidence", "Ministre"],
            )
        st.markdown("**Gouvernements successifs**")
        st.dataframe(
            [
                {
                    "Début (période)": max(0, term.since - offset),
                    "Fin (période)": None if term.until is None else term.until - offset,
                    "Couleur": term.label, "Orientation": round(term.orientation),
                    "Réélu": "oui" if term.reelected else "non",
                    "Objectifs atteints": term.objectives_met if term.until is not None else None,
                    "Bilan": ", ".join(f"{OFFICE_LABELS[r]} : {s}" for r, s in term.record.items())
                    + ("" if not getattr(term, "secondary_record", None) else " ; secondaires : "
                       + ", ".join(f"{OFFICE_LABELS[r]} : {s}" for r, s in term.secondary_record.items())),
                }
                for term in inst.terms
            ],
            hide_index=True,
            column_config={"Objectifs atteints": st.column_config.NumberColumn(format="percent")},
        )
        st.dataframe(
            [
                {
                    "Mandat (période)": max(0, term.since - offset), "Fonction": OFFICE_ROLES[m["role"]],
                    "Nom": m["name"], "Depuis": m["since"] - offset,
                    "Jusqu'à": None if m["until"] is None else m["until"] - offset,
                    "Salaire de début": m["salary_start"], "Salaire de fin": m["salary_end"],
                    "Objectif": m["objective"], "Bilan": m["status"], "Refus avant nomination": m["refusals"],
                    **({"Objectif secondaire": m["secondary"], "Bilan secondaire": m.get("secondary_status", "")}
                       if "secondary" in m else {}),
                }
                for term in inst.terms for m in term.members
            ],
            hide_index=True,
        )


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_budget(view_id: str, month: int, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        observed_history = simulation.observed_history
        law = inst.law
        if law is not None:
            st.markdown(f"**Loi de finances {law.kind}** · votée en période {law.month - offset} · {law.motive}")
            top = st.columns(4)
            top[0].metric("Recettes attendues (an)", compact(law.expected_revenue, " €"))
            top[1].metric("Dépenses prévues (an)", compact(law.spending, " €"))
            top[2].metric("Solde prévu", compact(law.planned_balance, " €"),
                          f"voulu {compact(law.balance_goal, ' €')}", delta_color="off")
            top[3].metric("Besoin de financement", compact(law.financing_need, " €"),
                          help="Couvert par l'emprunt aux banques, dans la limite légale et la capacité des prêteurs.")
            if law.excess_treasury > 0.0:
                st.caption(
                    f"Trésorerie au-delà de la réserve rendue dans l'année : {compact(law.excess_treasury, ' €')}"
                    + (f" par un allègement exceptionnel de {law.tax_relief:.1%} des impôts votés"
                       if law.tax_relief > 0.0 else " en transferts universels exceptionnels")
                    + " (décision de doctrine, jamais gardée oisive)."
                )
            since_law = [r for r in inst.history if r.month >= law.month]
            st.dataframe(
                [
                    {
                        "Ligne": LAW_LINE_LABELS.get(key, key),
                        "Prévu (an)": amount,
                        "Exécuté depuis la loi": -sum(r.flows.get(c, 0.0) for r in since_law for c in LAW_LINE_FLOWS.get(key, ())),
                        "Non financé": sum(r.unfunded_lines.get(key, 0.0) for r in since_law),
                        "Non consommé": sum(r.lapsed_lines.get(key, 0.0) for r in since_law),
                    }
                    for key, amount in law.lines.items()
                ],
                hide_index=True,
            )
            if law.adjustments:
                st.caption("Arbitrage de la présidence : " + " ; ".join(law.adjustments))
        if inst.history:
            last = inst.history[-1]
            st.markdown("**Trésorerie du mois** (recettes positives, dépenses négatives)")
            book = st.columns(4)
            book[0].metric("Emprunts", compact(last.borrowed, " €"))
            book[1].metric("Remboursements", compact(last.repaid, " €"))
            book[2].metric("Arriérés", compact(inst.arrears, " €"),
                           help="Intérêts, salaires et allocations dus non payés faute de financement ou de capacité du service des Finances ; jamais effacés.")
            book[3].metric("Impôt établi non encore recouvré", compact(sum(inst.tax_backlog.values()), " €"))
            st.dataframe(
                [{"Flux": FLOW_LABELS.get(k, k), "Montant (€)": v} for k, v in sorted(last.flows.items(), key=lambda kv: -kv[1])],
                hide_index=True,
            )
            st.caption(
                f"Rapprochements du mois : trésorerie {last.cash_residual:+.2e} €, dette {last.debt_residual:+.2e} € "
                "(variation expliquée par les flux classés et les titres émis ou remboursés)."
            )
        series = [r for r in inst.history if r.month >= offset]
        if len(series) > 1:
            left, right = st.columns(2)
            with left:
                st.caption("Dette publique (% du PIB annuel)")
                line_chart([{"Période": r.month - offset, "Dette / PIB": 100.0 * r.debt_gdp} for r in series],
                              x="Période", y="Dette / PIB")
            with right:
                st.caption("Recettes et dépenses publiques du mois")
                line_chart([{"Période": r.month, "Recettes": r.public_revenue, "Dépenses": r.public_spending}
                               for r in observed_history], x="Période", y=["Recettes", "Dépenses"])


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_services(view_id: str, month: int, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        if inst.services:
            st.dataframe(
                [
                    {
                        "Service": SERVICE_LABELS[key], "Besoin": service.need, "Postes ouverts": service.authorized,
                        "Effectif": service.staff, "Postes vacants": service.vacancies,
                        "Candidatures": service.applications, "Recrutements": service.hires,
                        "Départs": service.departures, "Licenciements": service.layoffs,
                        "Couverture": service.coverage, "Masse salariale (€)": service.wage_bill,
                        "Salaire moyen (€)": service.wage_bill / service.staff if service.staff else 0.0,
                    }
                    for key, service in inst.services.items()
                ],
                hide_index=True,
                column_config={"Couverture": st.column_config.NumberColumn(format="percent")},
            )
        live = simulation.live
        st.markdown(
            f"**Grille** · point d'indice {inst.point:,.0f} € · coefficients de grade "
            f"{', '.join(f'{c:g}' for c in live.public_grade_coefficients)} · ancienneté "
            f"+{live.public_seniority_step:.0%} par 24 mois (dix échelons) · plancher : salaire minimum".replace(",", " ")
        )
        trades = sorted({trade for mix in SERVICE_TRADES.values() for trade in mix})
        st.dataframe(
            [
                {
                    "Métier": trade, "Coefficient de métier": live.trade_base_wage.get(trade, 1.0),
                    "Prime": inst.premiums.get(trade, 1.0),
                    "Grade 1, entrée (€)": simulation._grid_salary(trade, 1, 0),
                    "Grade 3, 20 ans (€)": simulation._grid_salary(trade, 3, 240),
                    "Mois de vacance": inst.vacancy_months.get(trade, 0),
                }
                for trade in trades
            ],
            hide_index=True,
        )


@st.cache_data(show_spinner=False, max_entries=8)
def render_state_agents(view_id: str, month: int, choice: str, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        st.dataframe(
            [
                {
                    "Nom": f"{display_name(pid)} ({people[pid].name})", "Service": SERVICE_LABELS[agent.service],
                    "Métier": agent.trade, "Grade": agent.grade,
                    "Ancienneté (mois)": simulation.month - agent.since,
                    "Salaire (€)": people[pid].current_wage, "Compétence": people[pid].skill,
                }
                for pid, agent in sorted(inst.agents.items(), key=lambda item: (item[1].service, -item[1].grade, item[0]))
                if pid in people and choice in ("Tous", agent.service)
            ],
            hide_index=True,
        )
        series = [r for r in inst.history if r.month >= offset and r.services]
        if len(series) > 1:
            st.caption("Effectif par service")
            line_chart(
                [{"Période": r.month - offset, **{SERVICE_LABELS[k]: v["staff"] for k, v in r.services.items()}}
                 for r in series],
                x="Période", y=[SERVICE_LABELS[k] for k in SERVICE_LABELS],
            )


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_minimum_wage(view_id: str, month: int, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        last = inst.history[-1].minimum_wage if inst.history else {}
        wage = st.columns(4)
        wage[0].metric("Niveau légal", f"{inst.minimum_wage:,.0f} €".replace(",", " "))
        wage[1].metric("Rapport au salaire médian", f"{last.get('ratio_median', 0.0):.0%}")
        wage[2].metric("Salariés du privé au plancher", f"{last.get('at_floor', 0.0):.0%}")
        wage[3].metric("Payés sous le plancher", f"{last.get('below_floor', 0.0):.1%}",
                       help="Salaires payés en dessous du minimum faute de trésorerie de l'employeur : mesurés, jamais masqués.")
        st.caption(
            f"Ambition du gouvernement : {inst.doctrine.minimum_wage_ambition:.0%} du salaire médian observé ; "
            f"indexation légale appliquée à {inst.doctrine.minimum_wage_indexation:.0%} ; coup de pouce d'au plus "
            f"{simulation.live.minimum_wage_boost_cap:.0%} par an ; jamais de baisse par la règle, seulement par "
            f"décision d'un gouvernement non social au-delà de l'ambition + {simulation.live.minimum_wage_cut_margin:.0%} "
            "du médian."
        )
        st.dataframe(
            [
                {
                    "Période": r["month"] - offset, "Nature": r["kind"], "Indexation": r["indexation"],
                    "Coup de pouce": r["boost"], "Baisse décidée": r.get("cut", 0.0), "Niveau (€)": r["level"],
                    "Médian observé (€)": r["median"],
                }
                for r in reversed(inst.minimum_wage_revisions)
            ],
            hide_index=True,
            column_config={"Indexation": st.column_config.NumberColumn(format="percent"),
                           "Coup de pouce": st.column_config.NumberColumn(format="percent"),
                           "Baisse décidée": st.column_config.NumberColumn(format="percent")},
        )
        series = [r for r in inst.history if r.month >= offset and r.minimum_wage]
        if len(series) > 1:
            st.caption("Salaire minimum / médian et part des salariés au plancher (%)")
            line_chart(
                [{"Période": r.month - offset, "Minimum / médian": 100.0 * r.minimum_wage["ratio_median"],
                  "Au plancher": 100.0 * r.minimum_wage["at_floor"]} for r in series],
                x="Période", y=["Minimum / médian", "Au plancher"],
            )


@st.cache_data(show_spinner=False, max_entries=4)
def render_state_industry(view_id: str, month: int, _simulation: EconomySimulation) -> None:
        simulation = _simulation
        inst, offset, people, companies = _state_context(simulation)
        regime = getattr(inst, "ownership_regime", None)
        if regime is not None:
            # lot E : régime de propriété (0 ultra-capitalisme, 1 coopératives)
            liberal, cooperative = regime_intensities(regime)
            capital = sum(c.shares_outstanding for c in simulation.companies if c.active and c.sector != "bank")
            stakes = sum(sum(p.coop_shares.values()) for p in simulation.people)
            live = simulation.live
            st.markdown("**Régime de propriété des entreprises**")
            cols = st.columns(4)
            cols[0].metric("Régime (0 → 100)", f"{100.0 * regime:.0f}",
                           help="Se rapproche de l'orientation du gouvernement, progressivement.")
            cols[1].metric("Parts salariées", f"{stakes / capital:.1%}" if capital > 0 else "—",
                           help=f"Visé : {live.cooperative_share_max * cooperative:.0%} du capital des entreprises non bancaires.")
            cols[2].metric("Seuil de contrôle des OPA", f"{live.control_threshold:.0%}")
            cols[3].metric("Distribution des bénéfices", f"{live.dividend_payout_rate:.0%}")
            st.caption(
                ("Laisser-faire : aucune intervention contre les monopoles. " if liberal >= 1.0 else
                 f"Durée de problème avant un dossier de concurrence : {live.monopoly_duration_months} mois. ")
                + f"Au moins {live.min_firms_per_sector} entreprise(s) par secteur après une fusion ; "
                f"salaire de direction × {live.executive_wage_multiplier:.1f} le salaire de référence."
            )
        st.markdown("**Diagnostic des marchés** (problème : concentration élevée et marge ou demande "
                    "non servie anormales, sans entrant, pendant une durée minimale)")
        st.dataframe(
            [
                {
                    "Secteur": dict(SECTORS).get(sector, sector), "Entreprises": d["firms"],
                    "Herfindahl": d["hhi"], "Marge": d["margin"], "Marge de référence": d["base_margin"],
                    "Demande non servie": d["unmet"], "Entrants récents": d["entrants"],
                    "Mois anormaux": d["months"], "Problème": "oui" if d["problem"] else "non",
                    "Critique": "oui" if d["critical"] else "non", "Régulé": "oui" if d["regulated"] else "non",
                    "Opérateur public": "oui" if d["public_operator"] else "non",
                }
                for sector, d in sorted(inst.diagnostic.items())
            ],
            hide_index=True,
            column_config={key: st.column_config.NumberColumn(format="percent")
                           for key in ("Marge", "Marge de référence", "Demande non servie")},
        )
        state_shares = simulation.public.account.shares
        st.markdown("**Entreprises publiques et participations**")
        st.dataframe(
            [
                {
                    "Entreprise": simulation.company_label(cid), "Origine": h.origin,
                    "Part du capital": state_shares.get(cid, 0.0) / max(companies[cid].shares_outstanding, 1.0) if cid in companies else 0.0,
                    "Contrôle": "oui" if h.controlled else "non", "Mandat": h.mandate,
                    "Dirigeant": display_name(h.director) if h.director else "—",
                    "Investi (€)": h.invested, "Cédé (€)": h.proceeds, "Recapitalisations": h.recapitalizations,
                    "Décision": h.decision, "Motif": h.motive,
                }
                for cid, h in inst.holdings.items()
            ],
            hide_index=True,
            column_config={"Part du capital": st.column_config.NumberColumn(format="percent")},
        )
        if inst.closed_holdings:
            st.caption("Participations terminées")
            st.dataframe(
                [{"Entreprise": h.company_id, "Origine": h.origin, "Résultat": h.result,
                  "Investi (€)": h.invested, "Cédé (€)": h.proceeds} for h in inst.closed_holdings],
                hide_index=True,
            )
        st.markdown("**Dossiers de monopole**")
        st.dataframe(
            [
                {
                    "Secteur": dict(SECTORS).get(c.sector, c.sector), "Ouvert (période)": c.opened - offset,
                    "Statut": c.status, "Réponse": RESPONSE_LABELS.get(c.response, ""),
                    "Instruction (mois de service)": round(c.work, 1), "Motif": c.motive,
                }
                for c in reversed(inst.cases)
            ],
            hide_index=True,
        )
        if inst.regulated:
            st.caption("Secteurs régulés : " + ", ".join(dict(SECTORS).get(s, s) for s in sorted(inst.regulated)))


@st.cache_data(show_spinner=False, max_entries=8)
def render_state_journal(view_id: str, month: int, author: str, _simulation: EconomySimulation) -> None:
        inst, offset, people, companies = _state_context(_simulation)
        entries = [d for d in inst.journal if author in ("Tous", d.author)][-400:]
        st.caption(f"{len(inst.journal)} décisions consignées depuis la fin de la chauffe ; les 400 dernières de la sélection.")
        st.dataframe(
            [
                {
                    "Période": d.month - offset, "Auteur": d.author, "Personne": d.person, "Décision": d.decision,
                    "Motif": d.motive, "Montant (€)": d.amount, "Financement": d.financing, "Note": d.note,
                }
                for d in reversed(entries)
            ],
            hide_index=True,
        )


# --- lot K : sous-onglet « Pouvoir & influence » de l'onglet État (blocs sans widget) ---------

def _power_disabled(simulation: EconomySimulation, setting: str, label: str) -> bool:
    """Message commun quand un mécanisme du lot K n'est pas activé."""
    if getattr(simulation.live, setting) and getattr(simulation, "power", None) is not None:
        return False
    st.info(
        f"Désactivé : cochez « {label} » dans la barre latérale (rubrique « Pouvoir, influence & "
        "corruption »), puis réinitialisez."
    )
    return True


def _euros(value: float) -> str:
    return f"{value:,.0f} €".replace(",", " ")


@st.cache_data(show_spinner=False, max_entries=4)
def render_power_influence(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Les dix ménages les plus influents et le détail de leur score."""
    simulation = _simulation
    st.markdown("#### Les dix personnes les plus influentes")
    st.caption(
        "Influence de 0 à 100 = fortune (au plus 30, atteints à dix fois la fortune moyenne) + fonction "
        "(présidence 35, ministre 22, dirigeant 6 à 28 selon le poids de son entreprise) + capital "
        "contrôlé (au plus 20, participations d'au moins 10 %) + popularité (au plus 15)."
    )
    rows = simulation.influence_table(10)
    st.dataframe(
        [
            {"Rang": r["rang"], "Nom": r["nom"], "Fonction": r["poste"], "Influence": r["influence"],
             "Fortune": r["fortune"], "Poste": r["fonction"], "Capital": r["capital"],
             "Popularité": r["popularite"], "Patrimoine (€)": r["patrimoine"]}
            for r in rows
        ],
        hide_index=True, width="stretch",
        column_config={
            "Influence": st.column_config.ProgressColumn(format="%.1f", min_value=0.0, max_value=100.0),
            **{name: st.column_config.NumberColumn(format="%.1f") for name in ("Fortune", "Poste", "Capital", "Popularité")},
            "Patrimoine (€)": st.column_config.NumberColumn(format="%.0f"),
        },
    )
    st.caption("Ce qui compose l'influence de chacun (points)")
    bar_chart(
        [{"Nom": f"{r['rang']}. {r['nom']}", "Fortune": r["fortune"], "Poste": r["fonction"],
          "Capital": r["capital"], "Popularité": r["popularite"]} for r in rows],
        x="Nom", y=["Fortune", "Poste", "Capital", "Popularité"],
    )
    history = [row for row in simulation.power_history() if "influence_max" in row]
    if len(history) > 1:
        st.caption("Influence de la première personne du classement et moyenne des dix premières")
        line_chart(
            [{"Période": r["periode"], "Première": r["influence_max"], "Dix premières": r["influence_top10"]}
             for r in history], x="Période", y=["Première", "Dix premières"],
        )
    elif not simulation.live.influence_ranking:
        st.caption("Pour suivre le classement dans le temps, cochez « Classement d'influence » dans la "
                   "barre latérale, puis réinitialisez.")


@st.cache_data(show_spinner=False, max_entries=4)
def render_power_popularity(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Traits, notoriété et opinion du cabinet ; candidats ; élections."""
    simulation = _simulation
    inst, offset, people, companies = _state_context(simulation)
    state = getattr(simulation, "power", None)
    st.markdown("#### Popularité")
    if not _power_disabled(simulation, "personal_popularity", "Personnalité et popularité"):
        st.caption(
            "Notoriété : être connu (donnée par la fonction). Opinion : être apprécié (résultats de la "
            "personne dans son rôle, autour d'une référence fixée par son charisme). Popularité = "
            "notoriété × opinion. Les traits sont la vérité du modèle, que les ménages ne voient pas."
        )
        rows = []
        for role in OFFICES:
            office = inst.offices.get(role)
            profile = simulation.power_profile(office.person_id) if office else {}
            if not profile:
                continue
            rows.append({
                "Fonction": OFFICE_LABELS[role], "Nom": display_name(office.person_id),
                "Opinion": profile["opinion"], "Notoriété": profile["notoriete"],
                "Charisme": profile["charisme"], "Intégrité": profile["integrite"],
                "Ambition": profile["ambition"],
            })
        st.markdown("**Présidence et ministres**")
        st.dataframe(rows, hide_index=True, width="stretch",
                     column_config=_percent_columns("Opinion", "Notoriété", "Charisme", "Intégrité", "Ambition"))
        history = [row for row in simulation.power_history() if "opinion_presidence" in row]
        if len(history) > 1:
            st.caption("Opinion sur la présidence et moyenne des ministres (%)")
            line_chart(
                [{"Période": r["periode"], "Présidence": 100.0 * r["opinion_presidence"],
                  "Ministres": 100.0 * r.get("opinion_ministres", 0.0)} for r in history],
                x="Période", y=["Présidence", "Ministres"],
            )
        popular = sorted(simulation.people, key=lambda p: (-simulation._appeal(p.id), p.id))[:10]
        scores = simulation.influence_scores()
        st.markdown("**Les dix personnes les plus populaires**")
        st.dataframe(
            [{"Nom": display_name(p.id), "Fonction": scores[p.id]["poste"],
              "Popularité": simulation._appeal(p.id), "Notoriété": state.notoriety.get(p.id, 0.0),
              "Opinion": simulation._opinion(p.id),
              "Dynastie": "oui" if p.id in state.dynasties else ""} for p in popular],
            hide_index=True, width="stretch",
            column_config=_percent_columns("Popularité", "Notoriété", "Opinion"),
        )
    st.markdown("#### Élection présidentielle")
    if _power_disabled(simulation, "candidate_elections", "Élection présidentielle à candidats"):
        return
    candidates = state.candidates
    if candidates is not None:
        st.markdown(
            f"**Campagne en cours** · {display_name(candidates['sortant'])} "
            f"({'dauphin du sortant' if candidates['dauphin'] else 'sortant'}) contre "
            f"{display_name(candidates['opposant'])} (opposition, programme "
            f"{candidates['programme_opposant']:.0f} / 100)"
        )
        cols = st.columns(2)
        for col, side in zip(cols, ("sortant", "opposant")):
            person_id = candidates[side]
            col.metric(
                display_name(person_id), f"popularité {simulation._appeal(person_id):.0%}",
                f"dons de campagne reçus : {_euros(state.campaign.get(person_id, 0.0))}", delta_color="off",
            )
    else:
        st.caption("Aucune campagne en cours : les candidats se déclarent six mois avant l'élection.")
    elections = [e for e in inst.elections if e["mois"] - offset >= 0 and "president_elu" in e]
    if elections:
        st.markdown("**Élections passées**")
        st.dataframe(
            [{"Période": e["mois"] - offset, "Candidat du sortant": e["candidat_sortant"]
              + (" (dauphin)" if e["dauphin"] else ""), "Opposant": e["candidat_opposant"],
              "Voix du sortant": e["part_sortant"], "Élu": e["president_elu"],
              "Avantage de popularité du sortant": e["ecart_popularite"],
              "Programme élu": round(e["elu"])} for e in reversed(elections)],
            hide_index=True, width="stretch",
            column_config={**_percent_columns("Voix du sortant"),
                           "Avantage de popularité du sortant": st.column_config.NumberColumn(format="%.2f")},
        )


@st.cache_data(show_spinner=False, max_entries=4)
def render_power_laws(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Lois de pouvoir en vigueur, capital politique de la présidence, historique."""
    simulation = _simulation
    inst, offset, people, companies = _state_context(simulation)
    st.markdown("#### Lois de pouvoir présidentiel")
    if _power_disabled(simulation, "executive_power_laws", "Lois de pouvoir présidentiel"):
        return
    state = simulation.power
    head = inst.holder("presidence")
    cols = st.columns(4)
    cols[0].metric("Lois en vigueur", f"{len(state.laws)} / {len(LAWS)}")
    if head is not None:
        profile = simulation.power_profile(head)
        integrity, ambition = profile.get("integrite", 0.5), profile.get("ambition", 0.5)
        capital = simulation._political_capital(head)
        needed = LAW_MAJORITY + LAW_STEP * len(state.laws)
        cols[1].metric("Capital politique", f"{capital:.0%}", f"{needed:.0%} requis pour la prochaine loi",
                       delta_color="off",
                       help="Part des voix du président à son élection, plus la moitié de l'écart de son "
                            "opinion à 50 % (avec la popularité).")
        cols[2].metric("Goût du pouvoir du président", f"{0.6 * ambition + 0.4 * (1.0 - integrity):.0%}",
                       "propose une loi au-delà de 55 %", delta_color="off",
                       help="0,6 × ambition + 0,4 × (1 − intégrité). Un président d'intégrité supérieure à "
                            "60 % abroge au lieu de proposer.")
        cols[3].metric("Mandats consécutifs", f"{state.consecutive_terms}",
                       "sans limite" if "mandats" in state.laws else f"limite : {TERM_LIMIT}", delta_color="off")
    st.dataframe(
        [{"Loi": LAW_LABELS[name], "Effet": LAW_EFFECTS[name],
          "En vigueur depuis (période)": state.laws[name] - offset if name in state.laws else None,
          "État": "en vigueur" if name in state.laws else "non votée"} for name in LAWS],
        hide_index=True, width="stretch",
    )
    events = [e for e in simulation.power_events() if e["type"] == "loi"]
    if events:
        st.markdown("**Votes passés**")
        st.dataframe([{"Période": e["periode"], "Vote": e["texte"]} for e in events[:60]],
                     hide_index=True, width="stretch")
    else:
        st.caption("Aucune loi proposée pour l'instant : il faut un président ambitieux et peu intègre, "
                   "six mois après le début de son mandat.")


@st.cache_data(show_spinner=False, max_entries=4)
def render_power_aid(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Aides aux entreprises, affaires révélées, vue omnisciente du favoritisme."""
    simulation = _simulation
    inst, offset, people, companies = _state_context(simulation)
    st.markdown("#### Aides aux entreprises")
    if _power_disabled(simulation, "business_subsidies", "Aides aux entreprises"):
        return
    state = simulation.power
    labels = simulation.company_labels()
    history = simulation.power_history()
    aid_log = [row for row in state.aid_log if row["mois"] - offset >= 0]
    cols = st.columns(4)
    cols[0].metric("Aides versées", _euros(sum(row["montant"] for row in aid_log)), "depuis la période 0",
                   delta_color="off")
    cols[1].metric("Entreprises aidées ce mois", f"{len(state.aided)}")
    cols[2].metric("Part discrétionnaire", "50 %" if "enveloppe" in state.laws else "0 %",
                   "loi « enveloppe » en vigueur" if "enveloppe" in state.laws else "loi « enveloppe » non votée",
                   delta_color="off")
    revealed = [a for a in state.affairs if a["revele"] is not None and a["revele"] - offset >= 0]
    cols[3].metric("Scandales révélés", f"{len(revealed)}",
                   f"{_euros(sum(a['recouvre'] for a in revealed))} recouvrés", delta_color="off")
    if len(history) > 1:
        st.caption("Aides versées chaque mois (€)")
        line_chart(
            [{"Période": r["periode"], "Sur critères": r.get("aides_criteres", 0.0),
              "Discrétionnaires": r.get("aides_faveur", 0.0)} for r in history],
            x="Période", y=["Sur critères", "Discrétionnaires"],
        )
    if aid_log:
        totals: dict = {}
        for row in aid_log:
            entry = totals.setdefault(row["entreprise"], {"total": 0.0, "mois": 0, "discretionnaire": 0.0})
            entry["total"] += row["montant"]
            entry["mois"] += 1
            if row["type"] != "critères":
                entry["discretionnaire"] += row["montant"]
        st.markdown("**Entreprises aidées**")
        st.dataframe(
            [{"Entreprise": labels.get(company_id, company_id), "Aide totale (€)": entry["total"],
              "Mois aidés": entry["mois"], "Dont discrétionnaire (€)": entry["discretionnaire"]}
             for company_id, entry in sorted(totals.items(), key=lambda item: -item[1]["total"])[:40]],
            hide_index=True, width="stretch",
            column_config={name: st.column_config.NumberColumn(format="%.0f")
                           for name in ("Aide totale (€)", "Dont discrétionnaire (€)")},
        )
    else:
        st.caption("Aucune aide versée : aucune entreprise privée n'est en perte depuis trois mois.")
    st.markdown("#### Favoritisme")
    if _power_disabled(simulation, "political_favoritism", "Favoritisme et scandales"):
        return
    if revealed:
        st.markdown("**Scandales révélés**")
        st.dataframe(
            [{"Révélé (période)": a["revele"] - offset, "Versé (période)": a["mois"] - offset,
              "Entreprise": labels.get(a["entreprise"], a["nom"]), "Patron": display_name(a["patron"]),
              "Président en cause": display_name(a["presidence"]), "Aide indue (€)": a["montant"],
              "Recouvré (€)": a["recouvre"]} for a in reversed(revealed)],
            hide_index=True, width="stretch",
            column_config={name: st.column_config.NumberColumn(format="%.0f")
                           for name in ("Aide indue (€)", "Recouvré (€)")},
        )
    else:
        st.caption("Aucun scandale révélé depuis la période 0.")
    with st.expander("Vue omnisciente : ce que le modèle sait et que les ménages ignorent"):
        affairs = [a for a in state.affairs if a["mois"] - offset >= 0]
        hidden = [a for a in affairs if a["revele"] is None]
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Aides de faveur", f"{len(affairs)}", f"{len(hidden)} encore cachées", delta_color="off")
        cols[1].metric("Montant détourné", _euros(sum(a["montant"] for a in affairs)))
        cols[2].metric("Part des aides détournée (5 ans)", f"{last.get('corruption_reelle', 0.0):.1%}")
        head = inst.holder("presidence")
        if head is not None:
            cols[3].metric("Intégrité du président", f"{simulation.power_profile(head).get('integrite', 0.5):.0%}",
                           "favorise sous 40 % (15 % si le contrôle est intact)", delta_color="off")
        if hidden:
            st.dataframe(
                [{"Versé (période)": a["mois"] - offset, "Entreprise": labels.get(a["entreprise"], a["nom"]),
                  "Patron": display_name(a["patron"]), "Président": display_name(a["presidence"]),
                  "Montant (€)": a["montant"], "Lien": a["lien"]} for a in reversed(hidden)],
                hide_index=True, width="stretch",
                column_config={"Montant (€)": st.column_config.NumberColumn(format="%.0f"),
                               **_percent_columns("Lien")},
            )
        st.caption(
            "Conditions du favoritisme, toutes requises : loi « enveloppe » votée, président peu intègre, "
            "patron d'influence au moins 25, lien d'au moins 50 % avec la présidence (ancien employeur, "
            "titres détenus, proximité politique, don de campagne, ancien du cabinet), puis un tirage."
        )


@st.cache_data(show_spinner=False, max_entries=4)
def render_power_networks(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Dons de campagne, lobbying, conseillers issus du cabinet, dynasties, événements."""
    simulation = _simulation
    inst, offset, people, companies = _state_context(simulation)
    state = getattr(simulation, "power", None)
    live = simulation.live
    st.markdown("#### Réseaux : lobbying, pantouflage, dynasties")
    if state is None or not (live.corporate_lobbying or live.revolving_door or live.political_dynasties):
        st.info(
            "Désactivé : cochez « Lobbying et dons de campagne », « Pantouflage » ou « Dynasties » dans la "
            "barre latérale (rubrique « Pouvoir, influence & corruption »), puis réinitialisez."
        )
        return
    labels = simulation.company_labels()
    history = simulation.power_history()
    total = lambda key: sum(row.get(key, 0.0) for row in history)  # noqa: E731
    cols = st.columns(4)
    cols[0].metric("Dons de campagne", _euros(total("dons_campagne")), "depuis la période 0", delta_color="off")
    cols[1].metric("Dépenses de lobbying", _euros(total("lobbying")),
                   f"{total('dossiers_freines'):.0f} mois d'instruction freinés, "
                   f"{total('dossiers_geles'):.0f} gelés", delta_color="off")
    cols[2].metric("Conseillers issus du cabinet", f"{len(state.advisors)}",
                   f"{_euros(total('honoraires_conseillers'))} d'honoraires versés", delta_color="off")
    cols[3].metric("Héritiers d'un nom connu", f"{len(state.dynasties)}")
    if state.advisors:
        st.markdown("**Conseillers en poste**")
        st.dataframe(
            [{"Entreprise": labels.get(company_id, company_id), "Conseiller": display_name(a["personne"]),
              "Ancienne fonction": OFFICE_LABELS[a["role"]], "Depuis (période)": a["mois"] - offset,
              "Président servi": display_name(a["presidence"]) if a["presidence"] else "—",
              "Lien avec la présidence actuelle": "oui" if a["presidence"] == inst.holder("presidence") else "non"}
             for company_id, a in sorted(state.advisors.items())],
            hide_index=True, width="stretch",
        )
    donors = [(cid, given) for cid, given in state.donations.items() if cid in labels]
    if donors:
        st.markdown("**Entreprises donatrices**")
        st.dataframe(
            [{"Entreprise": labels[cid], "Candidat": display_name(candidate), "Dons cumulés (€)": amount}
             for cid, given in sorted(donors) for candidate, amount in sorted(given.items())][:60],
            hide_index=True, width="stretch",
            column_config={"Dons cumulés (€)": st.column_config.NumberColumn(format="%.0f")},
        )
    events = simulation.power_events()
    if events:
        st.markdown("**Derniers événements**")
        st.dataframe(
            [{"Période": e["periode"], "Type": e["type"], "Événement": e["texte"]} for e in events[:80]],
            hide_index=True, width="stretch",
        )


@st.cache_data(show_spinner=False, max_entries=4)
def render_overview_markets(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Mémorise le rendu sans widgets, par simulation et mois, sans hacher le moteur.

    L'identifiant est renouvelé à chaque réinitialisation et propre à la session.
    Le cache borné ne contient que les messages d'affichage, jamais le moteur.
    """
    simulation = _simulation
    observed_history = simulation.observed_history
    latest = observed_history[-1] if observed_history else None
    employed = sum(person.employer_id is not None for person in simulation.people)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        st.subheader("Les marchés dans le temps")
        st.markdown('<p class="section-note">Prix et revenus moyens par période.</p>', unsafe_allow_html=True)
        if len(observed_history) > 1:
            price_history = [
                {
                    "Période": record.month,
                    "Alimentation": record.food_price,
                    "Biens courants": record.goods_price,
                    "Loyer": record.housing_rent,
                    "Matières premières": record.raw_material_price,
                }
                for record in observed_history
            ]
            line_chart(
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
                        for previous in observed_history[
                            max(0, index - 11) : index + 1
                        ]
                    )
                    / len(
                        observed_history[
                            max(0, index - 11) : index + 1
                        ]
                    ),
                }
                for index, record in enumerate(observed_history)
            ]
            macro_left, macro_right = st.columns(2)
            with macro_left:
                st.caption("Évolution du coût de la vie")
                line_chart(
                    macro_history,
                    x="Période",
                    y="Indice des prix (base 100)",
                )
            with macro_right:
                st.caption("Salaires nominaux et pouvoir d'achat")
                line_chart(
                    macro_history,
                    x="Période",
                    y=["Salaire nominal moyen", "Salaire réel moyen"],
                )
            gdp_column, policy_column = st.columns(2)
            with gdp_column:
                st.caption("PIB réel et PIB courant (le PIB réel inclut une moyenne glissante)")
                line_chart(
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
                line_chart(
                    macro_history,
                    x="Période",
                    y=["Inflation mensuelle (%)", "Taux directeur (%)"],
                )
        elif observed_history:
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
                "Emplois des services aux entreprises",
                f"{latest.sector_employment.get('services', 0)}",
                help="Commerce, transport, entretien, formation : entreprises à part entière, financées par les achats des autres secteurs.",
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
    flow[0].metric("Emplois", str(employed), help="Ménages employés par les entreprises.")
    flow[1].metric("Ventes alimentaires", f"{latest.food_sold:.1f}" if latest else "—", help="Unités achetées par les ménages.")
    flow[2].metric("Ventes de biens", f"{latest.goods_sold:.1f}" if latest else "—", help="Biens courants écoulés.")
    flow[3].metric("Dividendes versés", compact(latest.dividends_paid, " €") if latest else "—")
    if latest:
        st.caption(
            "Part des ventes alimentaires et des biens courants : "
            f"{latest.sector_market_share.get('food', 0.0):.0%} / "
            f"{latest.sector_market_share.get('goods', 0.0):.0%}."
        )



@st.cache_data(show_spinner=False, max_entries=4)
def render_overview_wealth(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    simulation = _simulation
    observed_history = simulation.observed_history
    latest = observed_history[-1] if observed_history else None
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
    income_metrics = st.columns(5)
    income_metrics[0].metric(
        "Gini du revenu disponible",
        f"{latest.disposable_income_gini:.3f}" if latest else "—",
        help=(f"Avant impôt sur le revenu : {latest.income_gini:.3f}. Revenu du mois : salaires, "
              "allocations, transferts, dividendes et loyers." if latest else None),
    )
    income_metrics[1].metric(
        "Part des 1 % les plus riches", f"{latest.household_top1_share:.0%}" if latest else "—",
    )
    income_metrics[2].metric(
        "Taux de pauvreté", f"{latest.poverty_rate:.0%}" if latest else "—",
        help="Ménages dont le revenu disponible du mois est inférieur à 60 % du revenu disponible médian.",
    )
    income_metrics[3].metric(
        "Ménages sans logement", f"{latest.unhoused_households}" if latest else "—",
        help="Ni propriétaires ni locataires : leur demande de location n'a pas été servie ce mois.",
    )
    income_metrics[4].metric(
        "Logement dans la fortune", f"{latest.housing_wealth_share:.0%}" if latest else "—",
        help="Part des logements (au prix du marché) dans la fortune brute des ménages.",
    )
    recent = observed_history[-12:]
    life_metrics = st.columns(5)
    life_metrics[0].metric(
        "Chômage de longue durée", f"{latest.long_term_unemployment:.1%}" if latest else "—",
        help="Part des ménages sans emploi depuis au moins 12 mois ; les entreprises sans poste vacant "
             "modèrent leurs offres quand elle augmente.",
    )
    life_metrics[1].metric(
        "Entreprises sans salarié", f"{latest.idle_companies}" if latest else "—",
        help=(f"Liquidations amiables sur 12 mois : {sum(r.dissolutions for r in recent)}. Une entreprise "
              "sans salarié réembauche si un poste rapporte ce qu'il coûte." if latest else None),
    )
    life_metrics[2].metric(
        "Âge moyen des chefs de ménage", f"{latest.average_age:.1f} ans" if latest else "—",
        help=(f"Décès (successions) sur 12 mois : {sum(r.deaths for r in recent)}." if latest else None),
    )
    life_metrics[3].metric(
        "Droits de succession (12 mois)", compact(sum(r.estate_taxes for r in recent), " €") if latest else "—",
        help="Recouvrés par l'État au-delà de l'abattement, au taux fixé par sa doctrine.",
    )
    life_metrics[4].metric(
        "Qualité de l'éducation", f"{latest.education_quality:.0%}" if latest else "—",
        help="Couverture du service de l'Éducation, lissée sur une scolarité : relève la compétence de "
             "départ des héritiers et des nouveaux ménages.",
    )
    if len(observed_history) > 1:
        wealth_history = [
            {
                "Période": record.month,
                "Gini": record.household_gini,
                "Gini du revenu disponible": record.disposable_income_gini,
                "Part du décile supérieur (%)": record.household_top10_share * 100,
                "Part des 1 % (%)": record.household_top1_share * 100,
            }
            for record in observed_history
        ]
        wealth_left, wealth_right = st.columns(2)
        with wealth_left:
            st.caption("Inégalités de fortune et de revenu disponible (Gini)")
            line_chart(wealth_history, x="Période", y=["Gini", "Gini du revenu disponible"])
        with wealth_right:
            st.caption("Part de la fortune détenue par les 10 % et le 1 % les plus riches")
            line_chart(wealth_history, x="Période", y=["Part du décile supérieur (%)", "Part des 1 % (%)"])
    elif latest:
        st.caption("L'évolution des indicateurs de concentration apparaîtra à partir de la deuxième période.")
    richest_households, richest_companies = st.columns(2, gap="large")
    # les deux palmarès en un seul calcul (auparavant deux fois tous les patrimoines)
    top_households, top_companies = wealth_leaderboards(simulation.people, simulation.companies,
                                                        simulation.live, 5)
    company_labels = simulation.company_labels()
    with richest_households:
        st.markdown("#### Ménages les plus fortunés")
        for row in top_households:
            with st.container(border=True):
                st.markdown(
                    f"**{row['Ménage']}** · **{row['Fortune nette']:,.0f} €**  \n"
                    f"Liquidités {row['Trésorerie']:,.0f} € · Actions {row['Actions']:,.0f} € · "
                    f"Immobilier {row['Immobilier']:,.0f} €"
                )
    with richest_companies:
        st.markdown("#### Entreprises les plus valorisées")
        for row in top_companies:
            with st.container(border=True):
                st.markdown(
                    f"**{simulation.company_label(row['id'], company_labels)}** · **{row['Fortune nette']:,.0f} €**  \n"
                    f"{sector_label(row['Secteur'])} · Trésorerie {row['Trésorerie']:,.0f} € · "
                    f"Dette {row['Dettes']:,.0f} € · Bénéfice {row['Profit']:,.0f} €"
                )



@st.cache_data(show_spinner=False, max_entries=4)
def render_overview_sectors(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    simulation = _simulation
    observed_history = simulation.observed_history
    if len(observed_history) > 1:
        sectors = display_sectors(
            {company.sector for company in simulation.companies}
            | {sector for record in observed_history for sector in record.sector_employment}
        )
        st.subheader("Activité et emploi")
        employment_history = [
            {
                "Période": record.month,
                "Avec emploi": record.employed_count,
                "Sans emploi": max(
                    0, record.population_count - record.employed_count
                ),
                "Population": record.population_count,
            }
            for record in observed_history
        ]
        activity_history = [
            {
                "Période": record.month,
                "Ventes nourriture": record.food_sold,
                "Ventes biens": record.goods_sold,
                "Demande non satisfaite — nourriture": record.unmet_food_demand,
                "Demande non satisfaite — biens": record.unmet_goods_demand,
            }
            for record in observed_history
        ]
        st.caption("Emploi et chômage")
        area_chart(
            employment_history,
            x="Période",
            y=["Avec emploi", "Sans emploi"],
        )
        st.caption("Croissance de la population")
        line_chart(
            employment_history,
            x="Période",
            y="Population",
        )
        st.caption("Ventes réalisées et demande non satisfaite")
        line_chart(activity_history, x="Période")
        inventory_history = [
            {
                "Période": record.month,
                "Stock alimentaire restant": record.food_inventory,
                "Stock de biens restant": record.goods_inventory,
                "Stock technologique restant": record.technology_inventory,
            }
            for record in observed_history
        ]
        st.caption("Stocks invendus en fin de période")
        line_chart(inventory_history, x="Période")
        st.subheader("Évolution de l'emploi par secteur")
        sector_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_employment.get(sector, 0)
                    for sector, label in sectors
                },
            }
            for record in observed_history
        ]
        area_chart(sector_chart_data, x="Période")
        income_chart_data = [
            {
                "Période": record.month,
                "Revenus des ménages": record.household_income,
                "Dépenses des ménages": record.household_spending,
            }
            for record in observed_history
        ]
        line_chart(
            income_chart_data,
            x="Période",
            y=["Revenus des ménages", "Dépenses des ménages"],
        )
        revenue_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_revenue.get(sector, 0.0)
                    for sector, label in sectors
                },
            }
            for record in observed_history
        ]
        profit_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_profit.get(sector, 0.0)
                    for sector, label in sectors
                },
            }
            for record in observed_history
        ]
        sector_finance_left, sector_finance_right = st.columns(2)
        with sector_finance_left:
            st.caption("Chiffre d'affaires par secteur")
            line_chart(revenue_chart_data, x="Période")
        with sector_finance_right:
            st.caption("Bénéfice après charges par secteur")
            line_chart(profit_chart_data, x="Période")
        wage_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_average_wage.get(sector, 0.0)
                    for sector, label in sectors
                },
            }
            for record in observed_history
        ]
        st.caption("Salaire mensuel moyen par secteur")
        line_chart(wage_chart_data, x="Période")
        resource_history = [
            {
                "Période": record.month,
                "Réserves vierges": record.raw_materials_remaining,
                "Matières recyclées": record.recycled_materials_remaining,
            }
            for record in observed_history
        ]
        st.caption("Réserves vierges et matières recyclées")
        line_chart(
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
        for record in reversed(observed_history[-6:]):
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



# Mécanismes optionnels (lots F à I) : case ou curseur de la barre latérale -> valeur activée
OPTIONAL_MECHANISMS = {
    "experience_learning": True, "property_upkeep": True, "consumer_preferences": True,
    "diversification": True, "conglomerate_takeovers": True, "retail_stores": True,
    "group_distribution": True, "private_startups": True, "shareholder_governance": True,
    "staffless_closure_months": 6, "countercyclical_capital": True, "bank_capital_backstop": True,
    "corporate_bonds": True, "foreign_trade": True, "collective_bargaining": True,
    # lot K : influence, popularité, pouvoir présidentiel, aides et favoritisme
    "influence_ranking": True, "personal_popularity": True, "candidate_elections": True,
    "executive_power_laws": True, "business_subsidies": True, "political_favoritism": True,
    "corporate_lobbying": True, "revolving_door": True, "political_dynasties": True,
    "media_sector": True, "bank_relationships": True, "adaptive_agents": True,
    "enriched_objectives": True, "ownership_regime": True,
    # rareté des matières premières (results/analyse_pib_par_menage_20261112)
    "raw_material_scarcity": True,
    # lot U (results/chantiers_20261112/RAPPORT.md) : impôt borné par la trésorerie, encadrement
    # des prix levé en cas de pénurie. L'embauche tirée par la demande est volontairement absente :
    # chômage moyen de 7,0 à 15,5 % mesuré.
    "tax_capped_by_cash": True, "shortage_price_release": True,
    # lot V : douze mécanismes de société (results/lot_v_societe/RESUME.md)
    "personal_bankruptcy": True, "philanthropy": True, "state_lottery": True,
    "addictive_goods": True, "franchising": True, "brand_marketing": True,
    "worker_buyouts": True, "short_selling": True, "mortgage_securitization": True,
    "crowdfunding": True, "social_unrest": True, "public_works": True,
    # lot T (offres publiques réalistes, croissance proportionnelle) volontairement absent :
    # chômage nettement plus élevé mesuré (results/lot_t_rachats/RESUME.md)
}

# Curseurs réglés par « Tout activer » : clé -> (valeur activée, valeur de base rétablie par
# « Tout désactiver »). La valeur de base est celle de ``SimulationConfig``.
OPTIONAL_SETTINGS = {
    # efficacité de la recherche (results/analyse_pib_par_menage_20261112)
    "knowledge_growth_per_intensity": (0.5, 0.25),
    # lot U : titres publics sans fonds propres exigés des banques (results/chantiers_20261112)
    "sovereign_risk_weight_percent": (0, 100),
    # arriérés d'impôt recouvrés sur 24 mois (results/credit_20261112/RAPPORT.md)
    "tax_backlog_months": (24, 0),
}


def _set_optional_mechanisms(enabled: bool) -> None:
    """Coche ou décoche d'un coup les mécanismes optionnels (appliqués à la réinitialisation)."""
    for key, value in OPTIONAL_MECHANISMS.items():
        st.session_state[key] = value if enabled else type(value)()
    for key, (on_value, base_value) in OPTIONAL_SETTINGS.items():
        st.session_state[key] = on_value if enabled else base_value
    st.session_state.world_enabled = enabled


@st.fragment
def render_sidebar() -> None:
    with st.sidebar:
        st.markdown('<div class="eyebrow">Laboratoire économique</div>', unsafe_allow_html=True)
        st.header("Réglages")
        st.caption("Modifiez les conditions de départ, puis réinitialisez pour les appliquer.")
        st.caption(
            "Configuration : **autonomie stable** et **État institutionnel**, la seule du modèle "
            "(voir README.md)."
        )
        st.caption(
            "**Mécanismes optionnels** : groupes, magasins, fonds, expérience, conseil d'actionnaires, "
            "fermeture des entreprises sans salarié (6 mois), exigence de fonds propres contracyclique "
            "et recapitalisation de dernier recours des banques, obligations d'entreprise, commerce "
            "extérieur, négociation collective, médias, apprentissage, relations bancaires et rareté "
            "des matières premières. « Tout activer » ouvre aussi le monde à plusieurs régions et "
            "porte l'efficacité de la recherche à 0,5 (« Tout désactiver » la remet à 0,25)."
        )
        preset = st.columns(2)
        preset[0].button("Tout activer", key="optional_all_on", use_container_width=True,
                         on_click=_set_optional_mechanisms, args=(True,),
                         help="Coche tous les mécanismes optionnels ; réinitialisez ensuite la simulation.")
        preset[1].button("Tout désactiver", key="optional_all_off", use_container_width=True,
                         on_click=_set_optional_mechanisms, args=(False,),
                         help="Revient à la configuration de base ; réinitialisez ensuite la simulation.")
        active = sum(bool(st.session_state.get(key)) for key in OPTIONAL_MECHANISMS)
        st.caption(f"{active} mécanisme(s) optionnel(s) coché(s) sur {len(OPTIONAL_MECHANISMS)}.")
        with st.expander("Monde & régions", expanded=False):
            st.checkbox("Monde à plusieurs régions", key="world_enabled",
                        help="Chaque région a son gouvernement, sa banque centrale, sa monnaie et ses entreprises. "
                             "Les commandes d'avance font progresser toutes les régions ensemble.")
            st.slider("Nombre de régions", 1, 4, 3, key="world_regions")
            st.checkbox("Profils régionaux distincts", value=True, key="world_profiles")
            st.caption(" · ".join(f"{p['nom']} : {p['description']}" for p in REGION_PROFILES))
            st.number_input("Mois d'isolement avant intégration", 0, 1200, 0, key="integration_month")
            st.checkbox("Commerce entre régions", value=True, key="interregional_trade")
            st.slider("Coût de transport", 0, 100, 10, format="%d %%", key="transport_percent")
            st.checkbox("Déménagements entre régions", value=True, key="migration")
            st.slider("Départs maximaux par mois", 0.0, 5.0, 0.5, 0.1,
                      format="%.1f %%", key="migration_monthly_percent")
            st.slider("Gain de revenu requis pour partir", 0, 100, 15,
                      format="%d %%", key="migration_gain_percent")
            st.checkbox("Travailleurs frontaliers", value=True, key="commuting")
            st.slider("Nouveaux contrats frontaliers par mois", 0.0, 5.0, 0.5, 0.1,
                      format="%.1f %%", key="commuting_monthly_percent")
            st.slider("Gain net requis pour travailler ailleurs", 0, 100, 15,
                      format="%d %%", key="commuting_gain_percent")
            st.checkbox("Rachats entre régions", value=True, key="cross_border_takeovers")
            st.checkbox("Filtrage des rachats de secteurs critiques", value=True, key="investment_screening")
            st.checkbox("Vote par comparaison avec les voisins", value=True, key="yardstick_voting")
            st.slider("Poids de la comparaison régionale", 0.0, 1.0, 0.5, 0.05, key="yardstick_weight")
            st.checkbox("Concurrence fiscale entre gouvernements", value=True, key="tax_competition")
            st.slider("Poids de la concurrence fiscale", 0.0, 1.0, 0.25, 0.05, key="tax_competition_weight")
            st.slider("Plafond de compensation (PIB annuel)", 0.0, 10.0, 2.0, 0.5,
                      format="%.1f %%", key="clearing_holding_percent")
            st.caption("La chauffe reste indépendante dans chaque région. L'isolement commence ensuite, "
                       "à la période 0. Quatre régions demandent environ quatre fois plus de calcul.")
        with st.expander("Médias, banques & apprentissage", expanded=False):
            st.checkbox("Entreprises de médias", key="media_sector")
            st.slider("Médias à fonder", 1, 8, 3, key="media_outlets")
            st.slider("Abonnement (budget de services)", 0.0, 20.0, 4.0, 0.5,
                      format="%.1f %%", key="media_subscription_percent")
            st.slider("Publicité (chiffre d'affaires)", 0.0, 10.0, 1.0, 0.1,
                      format="%.1f %%", key="media_ad_percent")
            st.slider("Portée maximale de la publicité", 0, 100, 30,
                      format="%d %%", key="media_ad_reach_percent")
            st.slider("Biais maximal du revenu perçu", 0, 30, 10,
                      format="%d %%", key="media_vote_percent")
            st.checkbox("Relations bancaires individuelles", key="bank_relationships")
            st.slider("Clients comparant les banques chaque mois", 0, 100, 8,
                      format="%d %%", key="bank_review_percent")
            st.slider("Seuil de fuite (solidité de la banque)", 0.0, 1.0, 0.5, 0.05,
                      key="bank_run_threshold")
            st.slider("Avantage de spécialisation bancaire", 0.0, 2.0, 0.5, 0.1,
                      key="bank_specialization")
            st.checkbox("Agents apprenants", key="adaptive_agents")
            st.slider("Durée des essais (mois)", 2, 60, 12, key="learning_period_months")
            st.slider("Pas d'apprentissage", 0.0, 20.0, 5.0, 0.5,
                      format="%.1f %%", key="learning_step_percent")
            st.slider("Part d'imitation des pairs", 0, 100, 25,
                      format="%d %%", key="imitation_percent")
            st.caption("Les agents apprennent à partir de leurs propres résultats ; une même graine "
                       "et les mêmes réglages reproduisent la même trajectoire.")
        with st.expander("Société : dettes, jeux, marques & finance", expanded=False):
            st.caption(
                "Douze mécanismes désactivés par défaut ; ils agissent dès la période de chauffe. "
                "Résultats dans l'onglet « Société »."
            )
            st.checkbox(
                "Surendettement et faillite personnelle", key="personal_bankruptcy",
                help="Une commission efface le crédit à la consommation d'un ménage en impayé dont la "
                     "charge de dette dépasse la moitié du revenu, après saisie de sa trésorerie au-delà "
                     "d'un mois de salaire minimum. La banque passe la perte ; le ménage est interdit de crédit.",
            )
            st.slider("Interdiction de crédit (années)", 1, 10, 5, key="credit_ban_years",
                      disabled=not st.session_state.get("personal_bankruptcy", False))
            st.checkbox(
                "Dons et fondations", key="philanthropy",
                help="Les ménages très riches donnent à trois fondations (école, hôpital, presse) qui "
                     "dépensent auprès des entreprises. Le donateur gagne de la renommée.",
            )
            st.slider("Fortune d'un donateur (× la moyenne)", 2.0, 30.0, 10.0, 1.0,
                      key="philanthropy_wealth_multiple",
                      disabled=not st.session_state.get("philanthropy", False))
            st.checkbox(
                "Loterie d'État", key="state_lottery",
                help="La mise ne dépend pas du revenu et les ménages modestes jouent davantage. La moitié "
                     "des mises va à l'État, le reste à une cagnotte qu'un seul joueur emporte.",
            )
            st.slider("Mise du joueur le plus assidu (salaire de référence)", 0.0, 10.0, 2.0, 0.5,
                      format="%.1f %%", key="lottery_stake_percent",
                      disabled=not st.session_state.get("state_lottery", False))
            st.checkbox(
                "Tabac et alcool", key="addictive_goods",
                help="Des ménages fondent des entreprises de tabac et d'alcool. Les ménages dépendants "
                     "achètent la même quantité quel que soit le prix ; accise et TVA vont à l'État ; "
                     "soins et surmortalité plus tard.",
            )
            st.slider("Accise (prix hors taxes)", 0, 300, 50, 10, format="%d %%",
                      key="addiction_excise_percent",
                      disabled=not st.session_state.get("addictive_goods", False))
            st.slider(
                "Gain addictif maximal de la technologie", 0, 200, 100, 10, format="%d %%",
                key="addiction_technology_gain_percent",
                disabled=not st.session_state.get("addictive_goods", False),
                help="Les équipements réellement achetés renforcent la dépendance quand le produit est "
                     "consommé. Le gain est plafonné, augmente de moins en moins avec la technologie et "
                     "s'érode avec l'obsolescence des équipements. À 100 %, le pouvoir addictif tend vers ×2.",
            )
            st.checkbox(
                "Franchises", key="franchising",
                help="Un producteur rentable qui refuse des commandes loue sa marque et ses méthodes à un "
                     "petit entrepreneur : droit d'entrée, puis redevance sur le chiffre d'affaires.",
            )
            st.slider("Redevance (chiffre d'affaires)", 0.0, 20.0, 5.0, 0.5, format="%.1f %%",
                      key="franchise_royalty_percent",
                      disabled=not st.session_state.get("franchising", False))
            st.checkbox(
                "Marques et agences de marketing", key="brand_marketing",
                help="Des agences fondées par des ménages vendent des campagnes aux producteurs. La "
                     "notoriété d'une marque s'efface lentement et relève son poids dans les achats.",
            )
            st.slider("Budget de marketing (chiffre d'affaires)", 0.0, 10.0, 2.0, 0.5, format="%.1f %%",
                      key="marketing_budget_percent",
                      disabled=not st.session_state.get("brand_marketing", False))
            st.checkbox(
                "Coopératives de chômeurs", key="worker_buyouts",
                help="Les salariés d'une entreprise liquidée la reprennent avec leur épargne et leurs "
                     "allocations chômage versées d'avance par l'État.",
            )
            st.slider("Allocations versées d'avance (mois)", 0, 24, 6, key="buyout_benefit_months",
                      disabled=not st.session_state.get("worker_buyouts", False))
            st.checkbox(
                "Vente à découvert et fonds spéculatifs", key="short_selling",
                help="Des fonds financés par des ménages empruntent les titres d'une entreprise fragile "
                     "et les vendent ; ils gagnent si le cours baisse ou si elle fait faillite.",
            )
            st.slider("Flottant emprunté par mois au plus", 0, 50, 15, format="%d %%",
                      key="short_float_percent",
                      disabled=not st.session_state.get("short_selling", False))
            st.checkbox(
                "Titrisation des prêts immobiliers", key="mortgage_securitization",
                help="Les banques émettent des titres adossés à leurs prêts immobiliers. Intérêts, "
                     "remboursements et pertes passent aux ménages porteurs ; les fonds propres des "
                     "banques sont libérés.",
            )
            st.slider("Part titrisable des prêts immobiliers", 0, 100, 50, 5, format="%d %%",
                      key="securitization_percent",
                      disabled=not st.session_state.get("mortgage_securitization", False))
            st.checkbox(
                "Financement participatif", key="crowdfunding",
                help="Les ménages prêtent directement aux entreprises de moins de trois ans de leur "
                     "région, sans banque et sans création de monnaie.",
            )
            st.slider("Épargne prêtée (ménage audacieux, × 2)", 0.0, 10.0, 2.0, 0.5, format="%.1f %%",
                      key="crowdfunding_savings_percent",
                      disabled=not st.session_state.get("crowdfunding", False))
            st.checkbox(
                "Manifestations et émeutes", key="social_unrest",
                help="La tension monte avec le chômage, les inégalités de patrimoine et la baisse du "
                     "salaire réel. Une manifestation fait perdre du travail ; le gouvernement concède "
                     "des transferts, trois fois au plus.",
            )
            st.slider("Chômage qui fait monter la tension", 0, 30, 10, format="%d %%",
                      key="unrest_unemployment_percent",
                      disabled=not st.session_state.get("social_unrest", False))
            st.checkbox(
                "Grands travaux", key="public_works",
                help="Route, réseau, port : chantiers de plusieurs années payés aux entreprises. Un "
                     "ouvrage achevé relève la productivité de tous et se dégrade sans entretien.",
            )
            st.slider("Coût d'un ouvrage (PIB annuel)", 0.0, 10.0, 2.0, 0.5, format="%.1f %%",
                      key="public_works_gdp_percent",
                      disabled=not st.session_state.get("public_works", False))
        with st.expander("Population & entreprises", expanded=True):
            st.number_input("Ménages", min_value=10, max_value=500, value=240, step=10, key="population_size")
            st.number_input("Entreprises par secteur", min_value=1, max_value=10, value=3, key="companies_per_sector")
            st.slider(
                "Période de chauffe (années, non affichée)",
                min_value=0, max_value=20, value=10, key="warmup_years",
                help="L'économie tourne d'abord ces années avec les mêmes règles pour construire un état initial cohérent (emplois, capital, bilans, logements) ; l'observation commence ensuite. À 0, on observe le démarrage arbitraire depuis les conditions ci-dessous.",
            )
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
            st.number_input(
                "Matières premières", min_value=0.0, max_value=100000000.0, value=300000.0, step=50000.0,
                key="raw_materials",
                help="Stock initial de matières vierges, jusqu'à 100 millions d'unités (300 000 par défaut, "
                     "quelle que soit la population). La rareté des matières premières se mesure par rapport "
                     "à ce stock de départ.",
            )
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
            st.number_input(
                "Élasticité-prix de la demande B2B",
                min_value=0.0, max_value=4.0, value=1.5, step=0.25,
                key="b2b_price_elasticity",
                help="0 : volume d'énergie et de services technologiques fixé par une part du chiffre d'affaires. Au-delà de 1, une hausse de prix réduit la dépense des acheteurs.",
            )
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
                "Taux mensuel des prêts immobiliers (repli)",
                min_value=0.0,
                max_value=2.0,
                value=0.4,
                step=0.1,
                format="%.1f %%",
                key="mortgage_interest_percent",
                help="Utilisé seulement hors simulation complète. Dans la simulation, un nouveau prêt immobilier est au taux directeur + la marge hypothécaire (taux fixe sur toute la durée du prêt).",
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
        with st.expander("État institutionnel", expanded=False):
            st.caption(
                "Présidence et quatre ministres nommés parmi les ménages, loi de finances sans dette "
                "visée, fonction publique, salaire minimum légal, politique industrielle (voir README.md, § 6)."
            )
            st.slider(
                "Rémunération privée au-delà de laquelle une personne refuse une nomination",
                min_value=0, max_value=100, value=25, format="+%d %%", key="cabinet_refusal_tolerance_percent",
                help="Écart toléré entre le salaire actuel d'une personne pressentie et le salaire proposé ; chaque refus relève le salaire à la révision suivante.",
            )
            st.slider(
                "Coup de pouce annuel maximal du salaire minimum",
                min_value=0, max_value=30, value=10, format="%d %%", key="minimum_wage_boost_cap_percent",
                help="Hausse discrétionnaire au-delà de l'indexation légale sur l'inflation, vers l'ambition du gouvernement.",
            )
            st.multiselect(
                "Secteurs critiques",
                options=[sector for sector, _ in SECTORS if sector != "public"],
                default=["energy", "bank", "food"],
                format_func=lambda sector: dict(SECTORS)[sector],
                key="critical_sectors",
                help="Un gouvernement social y crée un opérateur public ou nationalise ; un libéral y régule ; une participation y est stratégique.",
            )
            # lot C : objectifs ministériels enrichis (désactivés par défaut)
            st.checkbox(
                "Objectifs ministériels enrichis (principal + secondaire, avec leviers)",
                value=False, key="enriched_objectives",
                help="Chaque ministre reçoit deux objectifs choisis selon la doctrine, avec des cibles "
                     "relevées quand la situation de départ les remplit déjà ; un objectif en retard "
                     "actionne un levier de son ministère, et le bilan des deux compte au vote.",
            )
            # lot E : régime de propriété selon l'orientation (désactivé par défaut)
            st.checkbox(
                "Régime de propriété selon l'orientation (capitalisme ↔ coopératives)",
                value=False, key="ownership_regime",
                help="Près de 0 : laisser-faire (pas de régulation des monopoles, OPA facilitées, "
                     "dividendes accrus). Près de 100 : parts salariées minimales (30 % du capital), "
                     "OPA plus difficiles, dividendes et salaires de direction réduits. Interpolation "
                     "continue, application progressive (48 mois pour un écart complet).",
            )
            st.slider(
                "Effort d'un objectif en retard sur son levier", min_value=0, max_value=100, value=25,
                format="%d %%", key="objective_effort_percent",
                disabled=not st.session_state.get("enriched_objectives", False),
                help="Intensité du levier actionné par un objectif principal en retard (solde voulu relevé "
                     "de 0,25 point de PIB, couverture d'un service relevée de 25 %, …) ; secondaire : moitié.",
            )
        with st.expander("Politique publique & hasard", expanded=False):
            st.slider("Impôt sur le revenu", min_value=0, max_value=30, value=10, format="%d %%", key="income_tax_percent")
            st.number_input(
                "Seuil mensuel avant tranche supérieure (€ de départ)",
                min_value=0.0,
                max_value=20000.0,
                value=3000.0,
                step=250.0,
                key="income_tax_threshold",
                help="Indexé chaque mois sur le niveau nominal des salaires (salaire de référence / salaire de départ).",
            )
            st.slider(
                "Surtaxe au-dessus du seuil (s'ajoute au taux de base)",
                min_value=8,
                max_value=50,
                value=8,
                format="%d %%",
                key="top_marginal_income_tax_percent",
            )
            st.slider("Impôt sur les bénéfices", min_value=0, max_value=35, value=16, format="%d %%", key="corporate_tax_rate_percent")
            st.slider(
                "TVA (alimentation et biens)", min_value=0, max_value=30, value=10, format="%d %%",
                key="vat_percent",
                help="Payée par les ménages en plus du prix hors taxe, encaissée par l'État ; taux de départ, que la loi de finances ajuste ensuite.",
            )
            st.number_input("Allocation chômage mensuelle (€)", min_value=0.0, max_value=2000.0, value=300.0, step=50.0, key="unemployment_benefit")
            st.number_input("Graine aléatoire", min_value=0, max_value=999999, value=7, key="seed")
        with st.expander("Concurrence, dette & entreprises", expanded=False):
            st.slider("Pression concurrentielle sur les prix", min_value=0, max_value=20, value=6, format="%d %%", key="competitive_pricing_percent")
            # lot F : stabilité (désactivés par défaut)
            st.checkbox(
                "Exigence de fonds propres contracyclique", key="countercyclical_capital",
                help="Quand la capacité de crédit des banques est presque épuisée et que l'activité "
                     "baisse, l'exigence de fonds propres descend progressivement de 8 % à 6 %, puis "
                     "remonte quand la capacité se reconstitue.",
            )
            st.checkbox(
                "Recapitalisation de dernier recours des banques", key="bank_capital_backstop",
                help="Après trois mois sans aucune capacité de crédit, l'État souscrit des actions "
                     "nouvelles des banques, au cours, pour leur rendre une capacité de 5 % de leur encours.",
            )
            st.slider(
                "Bénéfice lissé dans la valeur fondamentale des actions", min_value=0, max_value=24, value=0,
                format="%d mois", key="stock_earnings_smoothing_months",
                help="0 : bénéfice du mois extrapolé (ancienne formule). Au-delà : moyenne des derniers "
                     "mois, qui stabilise la valeur vers laquelle les cours sont rappelés. Non validé : "
                     "à 12 mois, des effondrements tardifs sur certaines graines.",
            )
            # lot D : économies d'échelle (désactivées par défaut)
            st.slider(
                "Remise maximale sur le volume (achats entre entreprises)", min_value=0, max_value=40, value=0,
                format="%d %%", key="volume_discount_max_percent",
                help="Les grosses commandes entre entreprises paient moins cher à l'unité (courbe bornée : "
                     "moitié de la remise pour une commande de 5 000 € de départ), jamais sous le coût "
                     "du fournisseur. 0 : désactivé.",
            )
            st.slider(
                "Part des intrants payée en frais de structure", min_value=0, max_value=60, value=0,
                format="%d %%", key="fixed_cost_share_percent",
                help="Alimentation et biens : cette part des intrants non matériels devient un forfait "
                     "mensuel calé sur les ventes moyennes du secteur ; une entreprise plus grande "
                     "produit moins cher par unité. 0 : désactivé.",
            )
            # lot T : offres publiques réalistes et croissance proportionnelle (désactivées par défaut)
            st.checkbox(
                "Offres publiques réalistes (même secteur)", key="synergy_takeovers",
                help="L'acquéreur offre au moins la valeur fondamentale de la cible, compte ce qu'il lui "
                     "apporte (méthodes, magasins, frais de structure évités) et peut payer une part du "
                     "prix en actions nouvelles ; la productivité fusionnée est pondérée par les effectifs. "
                     "Sans cette case, presque toutes les offres échouent : l'offre reste sous la valeur "
                     "que connaissent les actionnaires.",
            )
            st.slider(
                "Part du prix payée en actions de l'acquéreur", min_value=0, max_value=100, value=50,
                step=5, format="%d %%", key="takeover_share_payment_percent",
                disabled=not st.session_state.get("synergy_takeovers", False),
                help="Offre mixte : la trésorerie libre ne paie que le reste. 0 : offre entièrement en monnaie.",
            )
            st.checkbox(
                "Croissance des effectifs proportionnelle à la taille", key="proportional_hiring",
                help="Embauches et licenciements mensuels bornés à une part de l'effectif, jamais moins "
                     "que les bornes fixes (3 embauches, 2 licenciements par mois).",
            )
            st.slider(
                "Variation mensuelle maximale de l'effectif", min_value=1, max_value=50, value=10,
                format="%d %%", key="staff_change_share_percent",
                disabled=not st.session_state.get("proportional_hiring", False),
            )
            st.checkbox(
                "Procédure de sauvegarde des entreprises en difficulté", key="safeguard_procedure",
                help="Une entreprise d'au moins dix salariés, en pertes, presque sans trésorerie, à qui les "
                     "banques refusent le crédit de sa paie mais dont les recettes paient un noyau viable : "
                     "effectif ramené à ce noyau en un mois, dette en partie abandonnée par les banques, "
                     "prêt de sauvegarde de trois mois de paie. Évite le licenciement en bloc puis la survie "
                     "en coquille des grandes entreprises surendettées.",
            )
            # lot U (12 novembre 2026) : quatre réglages mesurés dans results/chantiers_20261112
            st.checkbox(
                "Impôt sur le revenu borné par la trésorerie du ménage", key="tax_capped_by_cash",
                help="L'impôt recouvré, arriérés compris, ne dépasse pas ce que le ménage possède ; le "
                     "solde reste dû. Sans cette case, un rappel d'impôt peut rendre sa trésorerie "
                     "négative, puis sa demande négative.",
            )
            st.slider(
                "Étalement du recouvrement des arriérés d'impôt (mois)", min_value=0, max_value=60,
                value=0, step=6, key="tax_backlog_months",
                help="L'arriéré d'impôt d'un ménage est appelé par mensualités au lieu d'être exigé en "
                     "une fois. 0 : règle historique. Mesuré : un service des Finances de nouveau pourvu "
                     "recouvrait des années d'arriéré en un mois et déclenchait une crise.",
            )
            st.slider(
                "Poids des titres publics dans les fonds propres exigés des banques", min_value=0,
                max_value=100, value=100, step=10, format="%d %%", key="sovereign_risk_weight_percent",
                help="100 % : un titre public consomme autant de fonds propres qu'un prêt (règle "
                     "historique). 0 % : aucun, comme la pondération nulle des titres souverains. Mesuré : "
                     "quand le crédit est gelé, les titres publics font 64 % de l'exposition des banques.",
            )
            st.checkbox(
                "Embauche tirée par la demande non servie", key="demand_led_hiring",
                help="Alimentation et biens : plafond mensuel d'embauche relevé à une part de l'écart entre "
                     "le besoin et l'effectif, une perte du mois ne bloque plus l'embauche si chaque unité "
                     "vendue couvre ses intrants, et l'offre salariale rejoint celle du marché tant que la "
                     "valeur d'un salarié la couvre.",
            )
            st.slider(
                "Part de l'écart d'effectif comblée par mois", min_value=5, max_value=100, value=25,
                step=5, format="%d %%", key="demand_hiring_share_percent",
                disabled=not st.session_state.get("demand_led_hiring", False),
            )
            st.checkbox(
                "Encadrement des prix levé en cas de pénurie", key="shortage_price_release",
                help="L'encadrement des prix d'un gouvernement social ne s'applique pas à un produit dont "
                     "la demande non servie du mois précédent dépasse le seuil ci-dessous.",
            )
            st.slider(
                "Seuil de pénurie qui lève l'encadrement", min_value=0, max_value=100, value=20,
                step=5, format="%d %%", key="price_control_shortage_limit_percent",
                disabled=not st.session_state.get("shortage_price_release", False),
            )
            # rareté des matières premières (désactivée par défaut)
            st.checkbox(
                "Rareté des matières premières", key="raw_material_scarcity",
                help="Quand le stock de matières vierges baisse sous son niveau de départ, leur prix "
                     "plancher reçoit une rente de rareté et l'exploration remplace une plus grande part "
                     "des volumes extraits. Sans cette case, le stock peut s'épuiser d'un coup et arrêter "
                     "biens et alimentation (results/analyse_pib_par_menage_20261112).",
            )
            st.slider(
                "Rente de rareté", min_value=0.0, max_value=2.0, value=0.5, step=0.1,
                key="scarcity_rent",
                disabled=not st.session_state.get("raw_material_scarcity", False),
                help="Prix plancher × (1 + rente × rareté / (1 − rareté)) ; rareté = part du stock de départ consommée.",
            )
            st.slider(
                "Réponse de l'exploration à la rareté", min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                key="scarcity_exploration_response",
                disabled=not st.session_state.get("raw_material_scarcity", False),
                help="Part remplacée = réserves reconstituées + réponse × rareté.",
            )
            st.slider(
                "Sensibilité des clients aux écarts de prix",
                min_value=0.0,
                max_value=5.0,
                value=1.5,
                step=0.25,
                key="consumer_price_elasticity",
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
                "Réaction du taux directeur à l'inflation (règle de Taylor)",
                min_value=0.0,
                max_value=3.0,
                value=0.5,
                step=0.1,
                key="taylor_inflation_coefficient",
                help="Points de taux ajoutés par point d'inflation au-dessus de la cible (en plus de l'indexation sur l'inflation).",
            )
            st.slider(
                "Réaction du taux directeur au chômage",
                min_value=0.0,
                max_value=3.0,
                value=1.0,
                step=0.1,
                key="taylor_unemployment_coefficient",
                help="Points de taux retirés par point de chômage au-dessus du niveau naturel.",
            )
            # lot B : valeurs de base des taux (annuelles ; converties exactement par
            # ``_annual_points`` pour que les défauts redonnent les valeurs de SimulationConfig)
            st.slider("Taux réel neutre (règle de Taylor)", min_value=0.0, max_value=6.0, value=1.8,
                      step=0.1, format="%.1f %%", key="taylor_neutral_real_rate_percent",
                      help="Taux directeur réel visé quand l'inflation est à la cible et le chômage à son niveau naturel.")
            st.slider("Plafond du taux directeur", min_value=2.0, max_value=30.0, value=12.0, step=0.5,
                      format="%.1f %%", key="policy_rate_cap_percent",
                      help="Taux annuel que la règle de Taylor ne dépasse jamais (un taux forcé en intervention peut aller jusqu'à 20 %).")
            st.slider("Lissage mensuel du taux directeur", min_value=0.01, max_value=1.0, value=0.10, step=0.01,
                      key="policy_rate_smoothing",
                      help="Part de l'écart à la cible de la règle comblée chaque mois (1 : sans inertie).")
            st.slider("Écart du crédit immobilier", min_value=0.0, max_value=6.0, value=0.96, step=0.06,
                      format="%.2f %%", key="mortgage_spread_annual",
                      help="Taux hypothécaire = taux directeur + cet écart (annuel).")
            st.slider("Écart du crédit à la consommation", min_value=0.0, max_value=15.0, value=7.2, step=0.12,
                      format="%.2f %%", key="consumer_credit_spread_annual",
                      help="Taux du crédit à la consommation = taux directeur + cet écart (annuel).")
            st.slider("Écart des dépôts sous le taux directeur", min_value=0.0, max_value=6.0, value=1.44, step=0.12,
                      format="%.2f %%", key="deposit_spread_annual",
                      help="Taux de référence des dépôts = taux directeur − cet écart (annuel) ; chaque banque "
                           "l'ajuste ensuite selon sa liquidité.")
            st.slider("Marge supplémentaire des banques", min_value=-2.0, max_value=10.0, value=0.0, step=0.12,
                      format="%.2f %%", key="bank_margin_offset_annual",
                      help="Points annuels ajoutés à tous les nouveaux crédits (entreprises, immobilier, "
                           "consommation) et au coût du crédit que les entreprises anticipent. 0 : désactivé.")
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
            st.slider("Prime de contrôle offerte", min_value=0, max_value=100, value=30, format="%d %%", key="control_premium_percent", help="Prime sur le cours proposée aux actionnaires de la cible.")
            st.slider(
                "Poids de la valeur comptable en Bourse",
                min_value=0,
                max_value=100,
                value=55,
                format="%d %%",
                key="stock_book_value_weight_percent",
                help="Le reste de l'estimation combine bénéfices observés et perspectives de chiffre d'affaires.",
            )
            st.slider(
                "Prime de risque des actions (par an)",
                min_value=2.0,
                max_value=15.0,
                value=8.0,
                step=0.5,
                format="%.1f %%",
                key="equity_risk_premium_percent",
                help="Les bénéfices anticipés sont actualisés au taux directeur + cette prime - la croissance anticipée : quand les taux montent, la Bourse baisse.",
            )
            st.slider(
                "Vitesse de convergence du cours",
                min_value=1,
                max_value=50,
                value=18,
                format="%d %%",
                key="stock_adjustment_speed_percent",
            )
            st.number_input("Seuil de capital pour créer une entreprise (€)", min_value=25000.0, max_value=2000000.0, value=100000.0, step=25000.0, key="startup_threshold")
            st.slider("Part de l'épargne investie au démarrage", min_value=10, max_value=90, value=65, format="%d %%", key="startup_investment_percent")
            st.slider("Probabilité mensuelle de lancement", min_value=0, max_value=50, value=8, format="%d %%", key="startup_founder_percent")
            st.slider("Part des ménages à l'esprit entrepreneurial", min_value=0, max_value=100, value=25, format="%d %%", key="entrepreneurial_share_percent", help="Seuls ces ménages (et seulement s'ils ont le capital) peuvent créer une entreprise ; la décision dépend de la rentabilité du secteur.")
            st.slider("Mois de pertes avant risque de faillite", min_value=1, max_value=24, value=6, key="bankruptcy_months")
        with st.expander("Groupes, magasins, fonds & expérience", expanded=False):
            st.caption(
                "Huit mécanismes d'entreprise, désactivés par défaut : sans eux, la simulation est "
                "identique à la version précédente. Ils agissent dès la période de chauffe."
            )
            st.slider(
                "Fermer une entreprise restée sans salarié après", min_value=0, max_value=24,
                format="%d mois", key="staffless_closure_months",
                help="0 : jamais (seule la liquidation amiable après 24 mois existe). Au-delà : une "
                     "entreprise non bancaire sans aucun salarié pendant cette durée est fermée, ses "
                     "actifs vendus, ses banques remboursées et le reste versé aux actionnaires. Jamais "
                     "la dernière de son secteur ni une participation de l'État.",
            )
            st.checkbox(
                "Conseil d'actionnaires : les 4 plus gros nomment et révoquent le dirigeant", key="shareholder_governance",
                help="Les quatre plus gros actionnaires votent, selon leurs titres : nomination du "
                     "dirigeant, révocation après un an si l'entreprise perd de l'argent trop longtemps, "
                     "et part des bénéfices réinvestie.",
            )
            st.checkbox(
                "Expérience : fondateurs, négociation des salaires, acquéreurs", key="experience_learning",
                help="Plus on a créé d'entreprises, mieux on en crée (productivité et innovation de "
                     "départ, crédit) ; un salarié qui a souvent changé d'employeur et un employeur qui "
                     "a beaucoup recruté négocient mieux le salaire d'embauche ; un acquéreur "
                     "expérimenté intègre plus vite. Rendements décroissants.",
            )
            st.slider(
                "Effet maximal de l'expérience", min_value=0, max_value=30, value=10, format="%d %%",
                key="experience_gain_percent",
                disabled=not st.session_state.get("experience_learning", False),
                help="Productivité de départ d'une entreprise relevée d'au plus ce pourcentage ; salaire "
                     "d'embauche négocié dans une bande de la moitié, autour de l'offre.",
            )
            st.checkbox(
                "Gros entretien des logements", key="property_upkeep",
                help="En plus de l'entretien courant : l'état de chaque logement s'use, le propriétaire "
                     "paie les travaux s'il en a les moyens, et un logement dégradé se loue moins cher.",
            )
            st.slider(
                "Usure annuelle des logements", min_value=0.0, max_value=10.0, value=2.4, step=0.6,
                format="%.1f %%", key="property_wear_annual",
                disabled=not st.session_state.get("property_upkeep", False),
            )
            st.checkbox(
                "Préférences des ménages et vente de données", key="consumer_preferences",
                help="Chaque ménage a un goût, chaque producteur d'alimentation ou de biens un style. Les "
                     "entreprises technologiques vendent les données des ménages équipés : l'acheteur "
                     "adapte son offre au goût de ses clients.",
            )
            st.slider(
                "Poids du goût dans les achats", min_value=0, max_value=150, value=50, format="%d %%",
                key="preference_strength_percent",
                disabled=not st.session_state.get("consumer_preferences", False),
            )
            st.slider(
                "Budget de données (part du chiffre d'affaires)", min_value=0.0, max_value=5.0, value=1.0,
                step=0.25, format="%.2f %%", key="data_budget_percent",
                disabled=not st.session_state.get("consumer_preferences", False),
            )
            st.checkbox(
                "Diversification : création de filiales", key="diversification",
                help="Une entreprise rentable crée une filiale dans un autre secteur attractif ; le groupe "
                     "vend alors plusieurs produits. Au plus une filiale par mois dans l'économie.",
            )
            st.checkbox(
                "Rachats d'entreprises d'un autre secteur", key="conglomerate_takeovers",
                help="Une entreprise prend le contrôle d'une entreprise plus petite d'un autre secteur, "
                     "qui devient sa filiale (sans fusion), aux règles des offres publiques existantes.",
            )
            st.checkbox(
                "Magasins des producteurs", key="retail_stores",
                help="Un producteur d'alimentation ou de biens ouvre des magasins quand ils rapportent plus "
                     "que le coût de ses fonds propres ; son réseau relève sa part des achats des ménages.",
            )
            st.number_input(
                "Coût d'ouverture d'un magasin (€ de départ)", min_value=5000.0, max_value=500000.0,
                value=40000.0, step=5000.0, key="store_opening_cost",
                disabled=not st.session_state.get("retail_stores", False),
            )
            st.number_input(
                "Charges mensuelles d'un magasin (€ de départ)", min_value=0.0, max_value=10000.0,
                value=600.0, step=100.0, key="store_running_cost",
                disabled=not st.session_state.get("retail_stores", False),
            )
            st.slider(
                "Portée maximale d'un réseau de magasins", min_value=0, max_value=200, value=50,
                format="%d %%", key="store_reach_percent",
                disabled=not st.session_state.get("retail_stores", False),
                help="Hausse maximale du poids du producteur dans les achats des ménages.",
            )
            st.checkbox(
                "Distribution de groupe (les magasins vendent les produits des filiales)", key="group_distribution",
                help="Les magasins d'un groupe vendent les produits de toutes ses entreprises ; un "
                     "acquéreur compte ce gain dans la valeur d'une cible. Demande des magasins et des "
                     "groupes (filiales ou rachats entre secteurs).",
            )
            st.checkbox(
                "Jeunes entreprises non cotées et fonds des banques", key="private_startups",
                help="Une entreprise fondée par un ménage n'est plus cotée dès sa création. Chaque banque "
                     "gère un fonds où les ménages placent une part de leur épargne ; le fonds finance et "
                     "accompagne les non cotées. Introduction en Bourse pour un gros besoin de trésorerie "
                     "ou à la sortie du fonds.",
            )
            st.slider(
                "Épargne visée dans les fonds", min_value=0, max_value=30, value=5, format="%d %%",
                key="fund_savings_percent",
                disabled=not st.session_state.get("private_startups", False),
            )
            st.slider(
                "Frais de gestion annuels des fonds", min_value=0.0, max_value=6.0, value=1.8, step=0.6,
                format="%.1f %%", key="fund_fee_annual",
                disabled=not st.session_state.get("private_startups", False),
            )
            st.slider(
                "Sortie du fonds après", min_value=1, max_value=15, value=5, format="%d ans",
                key="fund_exit_years",
                disabled=not st.session_state.get("private_startups", False),
            )
        with st.expander("Obligations, commerce extérieur & syndicats", expanded=False):
            st.caption("Trois mécanismes désactivés par défaut ; ils agissent dès la période de chauffe.")
            st.checkbox(
                "Obligations d'entreprise", key="corporate_bonds",
                help="Une entreprise rentable emprunte aux ménages et aux fonds des banques au lieu de sa "
                     "banque : coupon et amortissement mensuels, sans création de monnaie. En faillite, "
                     "les obligataires passent après les banques et avant les actionnaires.",
            )
            st.slider(
                "Épargne visée en obligations d'entreprise", min_value=0, max_value=30, value=5,
                format="%d %%", key="corporate_bond_percent",
                disabled=not st.session_state.get("corporate_bonds", False),
            )
            st.checkbox(
                "Commerce extérieur", key="foreign_trade",
                help="Un reste du monde vend aux ménages l'alimentation et les biens que le marché "
                     "intérieur ne sert pas, puis redépense la monnaie gagnée en produits et services "
                     "intérieurs. Taux de change flottant ; aucune monnaie n'entre ni ne sort.",
            )
            st.slider(
                "Ouverture au commerce", min_value=0, max_value=100, value=50, format="%d %%",
                key="trade_openness_percent", disabled=not st.session_state.get("foreign_trade", False),
                help="Part de la demande non servie que les importations peuvent couvrir chaque mois.",
            )
            st.checkbox(
                "Syndicats et négociation collective", key="collective_bargaining",
                help="Une fois par an et par secteur, le syndicat demande le rattrapage de l'inflation et "
                     "une part des marges ; l'accord relève les offres salariales et les empêche de "
                     "baisser pendant douze mois ; un écart trop grand déclenche une grève d'un mois.",
            )
            st.slider(
                "Pouvoir des syndicats (gouvernement du centre)", min_value=0, max_value=100, value=50,
                format="%d %%", key="union_power_percent",
                disabled=not st.session_state.get("collective_bargaining", False),
                help="Part de sa demande qu'un syndicat obtient sans chômage ; multipliée par 0,5 à "
                     "l'extrême droite et 1,5 à l'extrême gauche, réduite par le chômage.",
            )
            st.slider(
                "Hausse annuelle maximale demandée", min_value=0, max_value=15, value=5, format="%d %%",
                key="union_max_raise_percent",
                disabled=not st.session_state.get("collective_bargaining", False),
            )
        with st.expander("Pouvoir, influence & corruption", expanded=False):
            st.caption(
                "Neuf mécanismes désactivés par défaut ; ils agissent dès la période de chauffe. "
                "Résultats dans l'onglet État, sous-onglet « Pouvoir & influence »."
            )
            st.checkbox(
                "Classement d'influence", key="influence_ranking",
                help="Influence de chaque ménage, de 0 à 100 : fortune, fonction (présidence, ministère, "
                     "direction d'entreprise), capital contrôlé, popularité. Mesure seule : l'économie "
                     "ne change pas. Sans cette case, le classement du mois reste affiché, sans historique.",
            )
            st.checkbox(
                "Personnalité et popularité", key="personal_popularity",
                help="Chaque ménage a un charisme, une intégrité et une ambition. Sa notoriété vient de "
                     "sa fonction ; l'opinion suit ses résultats (croissance, chômage et objectifs pour "
                     "la présidence, objectif pour un ministre, marge et effectifs pour un dirigeant).",
            )
            st.checkbox(
                "Élection présidentielle à candidats", key="candidate_elections",
                help="Le sortant affronte le membre le plus fort du camp opposé. L'écart de popularité "
                     "et de campagne décale le vote de chaque ménage ; l'élu devient président.",
            )
            st.slider(
                "Poids de la popularité dans le vote", min_value=0.0, max_value=10.0, value=3.0, step=0.5,
                key="vote_popularity_weight",
                disabled=not st.session_state.get("candidate_elections", False),
                help="À 3, la popularité pèse à peu près autant que l'écart idéologique entre deux programmes.",
            )
            st.checkbox(
                "Lois de pouvoir présidentiel", key="executive_power_laws",
                help="Un président ambitieux et peu intègre fait voter, une par an au plus, des lois qui "
                     "étendent son pouvoir : enveloppe discrétionnaire, nominations, contrôle des comptes "
                     "affaibli, tutelle sur les médias, limite de mandats levée. Un successeur intègre "
                     "les abroge. Sans la loi « mandats », deux mandats consécutifs au plus.",
            )
            st.checkbox(
                "Aides aux entreprises", key="business_subsidies",
                help="Programme légal : aide mensuelle aux entreprises privées en perte depuis trois mois, "
                     "au prorata de leurs salariés, au plus un quart de leur masse salariale.",
            )
            st.slider(
                "Budget annuel des aides (part du PIB)", min_value=0.0, max_value=3.0, value=0.5, step=0.1,
                format="%.1f %%", key="business_aid_gdp_percent",
                disabled=not st.session_state.get("business_subsidies", False),
                help="Au centre ; moitié moins pour une doctrine de laisser-faire, moitié plus pour une "
                     "doctrine d'opérateur public.",
            )
            st.checkbox(
                "Favoritisme et scandales", key="political_favoritism",
                help="Sous la loi « enveloppe », un président peu intègre peut attribuer la moitié des aides "
                     "à une entreprise dont le patron est influent et lié à lui. L'audit ou la presse peut "
                     "révéler l'affaire : opinion, remboursement, amende, remaniement. Demande les aides "
                     "et les lois de pouvoir.",
            )
            st.slider(
                "Passage à l'acte (par mois, conditions réunies)", min_value=0, max_value=100, value=10,
                format="%d %%", key="favoritism_chance_percent",
                disabled=not st.session_state.get("political_favoritism", False),
            )
            st.slider(
                "Détection par l'audit (par mois et par affaire)", min_value=0, max_value=50, value=3,
                format="%d %%", key="audit_detection_percent",
                disabled=not st.session_state.get("political_favoritism", False),
                help="Divisée par quatre sous la loi « contrôle affaibli » ; avec les médias activés, "
                     "les enquêtes dépendent des journalistes et de leur indépendance ; sinon la presse ajoute 1 %.",
            )
            st.checkbox(
                "Lobbying et dons de campagne", key="corporate_lobbying",
                help="Les entreprises bénéficiaires financent le candidat le plus proche de leur dirigeant ; "
                     "une entreprise visée par un dossier de monopole paie du conseil qui freine "
                     "l'instruction.",
            )
            st.checkbox(
                "Pantouflage", key="revolving_door",
                help="Un ancien membre du cabinet devient conseiller rémunéré d'une grande entreprise de "
                     "son ancien domaine, et la relie au président qu'il a servi.",
            )
            st.checkbox(
                "Dynasties", key="political_dynasties",
                help="L'héritier d'une personne connue garde 30 % de sa notoriété. Demande la popularité.",
            )
        with st.expander("Investissement, salaires & compétences", expanded=False):
            st.slider("Bénéfices versés en dividendes", min_value=0, max_value=50, value=12, format="%d %%", key="dividend_payout_percent")
            st.slider("Actions mises en vente à la création", min_value=5, max_value=50, value=25, format="%d %%", key="founder_public_shares_percent", help="Titres offerts par la jeune entreprise : non émis tant qu'aucun investisseur ne les souscrit (le fondateur détient alors tout le capital émis). Pendant ses 12 premiers mois, la demande est servie par ces titres avant les ventes du fondateur ; les achats alimentent directement sa trésorerie. Le fondateur revend ensuite ses titres pour se diversifier ou reconstituer sa trésorerie.")
            st.slider("Rétention moyenne des bénéfices", min_value=0, max_value=100, value=8, format="%d %%", key="investment_retention_percent")
            st.slider("Bonus des employés sur les bénéfices", min_value=0, max_value=20, value=3, format="%d %%", key="executive_bonus_percent")
            st.slider(
                "Vitesse de rééquilibrage de l'épargne",
                min_value=0,
                max_value=20,
                value=15,
                format="%d %%",
                key="household_investment_percent",
                help="Part de l'écart entre le portefeuille visé (dépôts, obligations d'État, actions, selon les rendements) et le portefeuille actuel comblée chaque mois.",
            )
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
                value=1.5,
                step=0.5,
                key="working_capital_months",
                help="La trésorerie au-delà est distribuée aux actionnaires.",
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
                "Part empruntée quand rendement = coût du crédit",
                min_value=0,
                max_value=90,
                value=50,
                format="%d %%",
                key="credit_financed_percent",
            )
            st.slider(
                "Sensibilité de l'emprunt à l'écart rendement - coût",
                min_value=0.0, max_value=20.0, value=8.0, step=0.5,
                key="credit_return_gain",
                help="La monnaie naît du crédit bancaire. Chaque entreprise compare le rendement attendu de son projet (capacité, logement) au taux qu'on lui propose : plus l'écart est favorable, plus elle emprunte. La politique monétaire agit par le prix du crédit.",
            )
            st.slider(
                "Marge bancaire liée à l'endettement (points/an par unité de dette/actifs)",
                min_value=0.0, max_value=12.0, value=3.6, step=0.6,
                key="credit_leverage_spread_annual",
                help="Risque croissant : une entreprise déjà endettée paie un crédit plus cher, ce qui freine l'emballement de l'endettement.",
            )
            st.slider(
                "Réserves obligatoires (en % des dépôts)",
                min_value=0, max_value=30, value=10, format="%d %%",
                key="reserve_requirement_percent",
                help="La banque centrale fournit les réserves exigées sous forme d'avances au taux directeur ; elle ne distribue plus de monnaie.",
            )
            st.slider(
                "Exigence de fonds propres des banques (en % des prêts)",
                min_value=2, max_value=20, value=8, format="%d %%",
                key="capital_requirement_percent",
                help="Plafonne le crédit : prêts et titres publics ≤ fonds propres / exigence.",
            )
            st.toggle(
                "Élections et alternance politique",
                value=True,
                key="political_cycle_enabled",
                help="Une élection a lieu à intervalle fixe. Désactivé : politique centrale constante.",
            )
            st.toggle(
                "Vote économique",
                value=True,
                key="economic_voting",
                help="Le gouvernement sortant est jugé sur le chômage et l'inflation ; s'il est battu, l'électorat se tourne vers l'autre bord. Désactivé : gouvernement tiré au sort.",
            )
            st.slider(
                "Orientation du premier gouvernement (0 libéral · 100 social)",
                min_value=0, max_value=100, value=50, step=5,
                key="initial_political_orientation",
                help="50 = réglages de base inchangés. Vers 0 : impôts bas, État léger, privatisations. Vers 100 : impôts et redistribution élevés, salaire minimum, État actionnaire.",
            )
            st.slider("Durée d'un mandat (années)", min_value=1, max_value=10, value=5, key="election_period_years")
            st.slider(
                "Intensité des politiques",
                min_value=0, max_value=200, value=100, format="%d %%",
                key="policy_strength_percent",
                help="Amplitude des écarts entre un gouvernement et le centre.",
            )
            st.slider(
                "Singularité de chaque gouvernement",
                min_value=0, max_value=50, value=15, format="%d %%",
                key="policy_noise_percent",
                help="Écart aléatoire de chaque levier autour de l'orientation générale.",
            )
            st.slider(
                "Élasticité de la productivité aux connaissances",
                min_value=0.0, max_value=1.0, value=0.5, step=0.05,
                key="knowledge_elasticity",
                help="Les connaissances se construisent par la recherche (R&D et entreprises technologiques) et font croître la productivité.",
            )
            st.slider(
                "Efficacité de la recherche",
                min_value=0.0, max_value=1.0, value=0.25, step=0.05,
                key="knowledge_growth_per_intensity",
                help="Croissance mensuelle du stock de connaissances par point de PIB consacré à la recherche ; "
                     "l'obsolescence retire 0,2 % par mois. 0,25 : réglage de base. Au-delà, la production "
                     "croît plus vite et épuise plus tôt les matières premières vierges : avec « Tout "
                     "activer », 0,5 n'est stable que si les réserves sont reconstituées à 100 % "
                     "(results/analyse_pib_par_menage_20261112).",
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
                "Échelle des dépenses publiques discrétionnaires",
                min_value=0,
                max_value=100,
                value=100,
                format="%d %%",
                key="government_spending_percent",
                help="Échelle des achats, de l'investissement public et des transferts (l'État peut s'endetter dans la limite du plafond).",
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
            st.session_state.income_tax_percent = 10
        st.session_state.income_tax_rate = st.session_state.income_tax_percent / 100
        if st.button("↻  Réinitialiser la simulation", use_container_width=True):
            with st.spinner("Période de chauffe : construction de l'état initial…"):
                st.session_state.simulation = new_simulation()
                st.rerun()


@st.cache_data(show_spinner=False, max_entries=4)
def render_firm_tables(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Résumés par secteur et par entreprise (sans widget), mémorisés par simulation et mois
    comme ``render_overview`` : une sélection dans l'onglet ne les reconstruit pas."""
    simulation = _simulation
    firm_rows = simulation.company_rows()
    active_companies = [company for company in simulation.companies if company.active]
    st.markdown("#### Résumé par secteur")
    sector_summary = []
    revenue_total = sum(c.revenue for c in active_companies) or 1.0
    for sector, label in display_sectors(
        (company.sector for company in simulation.companies), include_public=False,
    ):
        sector_firms = [c for c in simulation.companies if c.sector == sector]
        sector_active = [c for c in sector_firms if c.active]
        sector_revenue = sum(c.revenue for c in sector_active)
        sector_profit = sum(c.profit for c in sector_active)
        sector_summary.append(
            {
                "Secteur": label,
                "Entreprises": len(sector_active),
                "Sans salarié": sum(1 for c in sector_active if not c.employees),
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
        "Une ligne par entreprise en activité (les entreprises fermées ou absorbées ne sont plus "
        "listées ; une entreprise absorbée apparaît dans la fiche de celle qui l'a rachetée). "
        "Le détail s'ouvre dans le sous-onglet « Fiche d'une entreprise »."
    )
    labels = simulation.company_labels()
    by_id = {company.id: company for company in simulation.companies}
    summary_rows = [
        {
            "Entreprise": labels[row["_id"]],
            "Secteur": row["Secteur"].split(" (")[0],
            "Groupe": labels.get(by_id[row["_id"]].parent_id, "—") if by_id[row["_id"]].parent_id else "—",
            "Bourse": "cotée" if by_id[row["_id"]].listed else "non cotée",
            "Dirigeant": row["CEO"] if row["CEO"] != "Aucun" else "—",
            "Fondateur": row["Fondateur"],
            "Part du fondateur": row["Part du fondateur"],
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
        for row in sorted(firm_rows, key=lambda r: (r["Secteur"], labels[r["_id"]]))
        if row["Statut"] == "Active"
    ]
    st.dataframe(
        summary_rows,
        hide_index=True,
        width="stretch",
        height=min(640, 38 + 35 * len(summary_rows)),
        column_config={
            "Entreprise": st.column_config.TextColumn(width="medium"),
            "Fondateur": st.column_config.TextColumn(width="medium", help="Ménage fondateur (« — » : entreprise initiale ou publique)."),
            "Part du fondateur": st.column_config.NumberColumn(
                format="percent", width="small",
                help="Part du capital émis (hors titres en trésorerie) encore détenue par le fondateur.",
            ),
            "Employés": st.column_config.NumberColumn(format="%d", width="small"),
            "Chiffre d’affaires (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Bénéfice (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Marge": st.column_config.NumberColumn(format="percent", width="small"),
            "Salaire moyen (€)": st.column_config.NumberColumn(format="localized", width="medium"),
            "Trésorerie (€)": st.column_config.NumberColumn(format="localized", width="medium"),
        },
    )


def _money_columns(*names: str) -> dict:
    return {name: st.column_config.NumberColumn(format="localized") for name in names}


def _percent_columns(*names: str) -> dict:
    return {name: st.column_config.NumberColumn(format="percent", width="small") for name in names}


@st.cache_data(show_spinner=False, max_entries=4)
def render_groups_panel(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Sous-onglet « Groupes & filiales » : sociétés mères, filiales créées ou rachetées, ce
    qu'elles ont coûté et rapporté, entreprises absorbées, magasins et données (sans widget)."""
    simulation = _simulation
    state = getattr(simulation, "enterprise", None)
    live, offset = simulation.live, simulation.warmup_offset
    labels = simulation.company_labels()
    sectors = {key: label.split(" (")[0] for key, label in display_sectors(
        company.sector for company in simulation.companies
    )}
    active = [c for c in simulation.companies if c.active]
    by_id = {c.id: c for c in simulation.companies}
    history = simulation.enterprise_history()
    subsidiaries = [c for c in active if c.parent_id in by_id]
    heads = sorted({c.parent_id for c in subsidiaries}, key=lambda cid: labels[cid])
    absorbed = [c for c in simulation.companies if c.acquired_by is not None]
    log = state.group_log if state is not None else {}
    st.markdown("#### Groupes et filiales")
    if not (live.diversification or live.conglomerate_takeovers):
        st.info(
            "Aucun groupe ne peut se former : cochez « Diversification : création de filiales » ou "
            "« Rachats d'entreprises d'un autre secteur » dans la barre latérale (rubrique « Groupes, "
            "magasins, fonds & expérience »), puis réinitialisez. Les fusions dans un même secteur "
            "restent listées plus bas."
        )
    firms = [c for c in active if c.sector != "bank"]
    grouped = [c for c in active if c.parent_id in by_id or c.id in heads]
    cols = st.columns(5)
    cols[0].metric("Groupes", f"{len(heads)}", help="Sociétés mères qui contrôlent au moins une filiale.")
    cols[1].metric("Filiales", f"{len(subsidiaries)}")
    cols[2].metric(
        "Emploi dans les groupes",
        f"{sum(len(c.employees) for c in grouped) / max(1, sum(len(c.employees) for c in firms)):.0%}",
        help="Part des salariés des entreprises non bancaires qui travaillent dans un groupe.",
    )
    cols[3].metric(
        "Dividendes remontés (cumul)",
        compact(sum(entry["dividendes"] for entry in log.values())
                + sum(row["dividendes"] for row in (state.group_exits if state is not None else [])), " €"),
        help="Dividendes versés par les filiales à leur société mère depuis leur entrée dans le groupe.",
    )
    cols[4].metric("Entreprises absorbées", f"{len(absorbed)}",
                   help="Fusions dans un même secteur depuis le début de la simulation, chauffe comprise.")
    if len(history) > 1 and any(row.get("filiales", 0) for row in history):
        left, right = st.columns(2)
        with left:
            st.caption("Nombre de groupes et de filiales")
            line_chart(
                [{"Période": row["periode"], "Groupes": row.get("groupes", 0), "Filiales": row.get("filiales", 0)}
                 for row in history], x="Période", y=["Groupes", "Filiales"],
            )
        with right:
            st.caption("Poids des groupes dans l'économie (%, entreprises non bancaires)")
            line_chart(
                [{"Période": row["periode"], "Emploi": 100.0 * row.get("emploi_groupes", 0.0),
                  "Chiffre d'affaires": 100.0 * row.get("ca_groupes", 0.0)} for row in history],
                x="Période", y=["Emploi", "Chiffre d'affaires"],
            )
    if heads:
        st.caption(
            "Arbre des groupes : chaque flèche va d'une société mère à sa filiale, avec la part du "
            "capital émis qu'elle détient (contour plein : cotée ; pointillé : non cotée)."
        )
        lines = ["digraph groupes {", "rankdir=LR;", "bgcolor=transparent;",
                 'node [shape=box, style=rounded, fontname="Helvetica", fontsize=11];',
                 'edge [fontname="Helvetica", fontsize=10, color="#6b7280"];']
        for company in [by_id[cid] for cid in heads] + subsidiaries:
            style = "rounded" if company.listed else "rounded,dashed"
            weight = ', penwidth=2, color="#1d4ed8"' if company.id in heads and company.parent_id not in by_id else ""
            lines.append(
                f'"{company.id}" [label="{labels[company.id].split(" (groupe")[0]}\\n'
                f'{sectors.get(company.sector, company.sector)} · {len(company.employees)} salariés", '
                f'style="{style}"{weight}];'
            )
        for company in subsidiaries:
            issued = max(company.shares_outstanding - company.treasury_shares, 1e-9)
            stake = by_id[company.parent_id].shares.get(company.id, 0.0) / issued
            lines.append(f'"{company.parent_id}" -> "{company.id}" [label="{stake:.0%}"];')
        lines.append("}")
        st.graphviz_chart("\n".join(lines), use_container_width=True)
    for head_id in heads:
        head = by_id[head_id]
        members = [c for c in subsidiaries if c.parent_id == head_id]
        everyone = [head, *members]
        st.markdown(
            f"**Groupe {labels[head_id]}** · {len(everyone)} entreprises · "
            f"{sum(len(c.employees) for c in everyone)} salariés · chiffre d'affaires du mois "
            f"{compact(sum(c.revenue for c in everyone), ' €')} · bénéfice "
            f"{compact(sum(c.profit for c in everyone), ' €')}"
        )
        rows = []
        for company in members:
            entry = log.get(company.id, {})
            issued = max(company.shares_outstanding - company.treasury_shares, 1e-9)
            stake = head.shares.get(company.id, 0.0)
            value, cost = stake * company.stock_price, entry.get("cout", 0.0)
            rows.append({
                "Filiale": labels[company.id],
                "Secteur": sectors.get(company.sector, company.sector),
                "Origine": entry.get("origine", "—"),
                "Depuis la période": entry["mois"] - offset if entry else None,
                "Part détenue": stake / issued,
                "Coût (€)": cost if entry else None,
                "Valeur de la part (€)": value,
                "Dividendes reçus (€)": entry.get("dividendes", 0.0),
                "Gain total": (value + entry.get("dividendes", 0.0) - cost) / cost if cost > 0.0 else None,
                "Productivité à l'entrée": entry.get("productivite"),
                "Productivité": company.productivity_trait,
                "Magasins": company.stores,
                "Salariés": len(company.employees),
                "Bénéfice du mois (€)": company.profit,
            })
        st.dataframe(
            rows, hide_index=True, width="stretch",
            column_config={
                **_percent_columns("Part détenue", "Gain total"),
                **_money_columns("Coût (€)", "Valeur de la part (€)", "Dividendes reçus (€)", "Bénéfice du mois (€)"),
                "Depuis la période": st.column_config.NumberColumn(format="%d", width="small"),
                "Productivité à l'entrée": st.column_config.NumberColumn(format="%.2f", width="small"),
                "Productivité": st.column_config.NumberColumn(format="%.2f", width="small"),
                "Magasins": st.column_config.NumberColumn(format="%.1f", width="small"),
                "Salariés": st.column_config.NumberColumn(format="%d", width="small"),
                "Gain total": st.column_config.NumberColumn(
                    format="percent", width="small",
                    help="(valeur de la part + dividendes reçus − coût) / coût.",
                ),
            },
        )
        merged = [labels[c.id] for c in absorbed if c.acquired_by == head_id]
        if merged:
            st.caption("Entreprises absorbées par la société mère (fusions) : " + ", ".join(merged))
    exits = state.group_exits if state is not None else []
    if exits:
        st.markdown("**Filiales sorties d'un groupe**")
        st.dataframe(
            [
                {
                    "Filiale": labels.get(row["filiale"], row["filiale"]),
                    "Société mère": labels.get(row["mere"], row["mere"]),
                    "Origine": row["origine"],
                    "Entrée (période)": row["mois"] - offset,
                    "Sortie (période)": row["sortie"] - offset,
                    "Issue": row["issue"],
                    "Coût (€)": row["cout"],
                    "Dividendes reçus (€)": row["dividendes"],
                }
                for row in reversed(exits[-40:])
            ],
            hide_index=True, width="stretch",
            column_config=_money_columns("Coût (€)", "Dividendes reçus (€)"),
        )
    if absorbed:
        st.markdown("**Fusions : entreprises absorbées par un concurrent**")
        st.dataframe(
            [
                {
                    "Entreprise absorbée": labels[c.id],
                    "Secteur": sectors.get(c.sector, c.sector),
                    "Absorbée par": labels.get(c.acquired_by, c.acquired_by),
                    "Période": c.exit_month - offset,
                }
                for c in sorted(absorbed, key=lambda c: -c.exit_month)[:40]
            ],
            hide_index=True, width="stretch",
        )
    networks = [c for c in active if c.stores > 0.0 or c.market_insight > 0.0]
    if networks:
        st.markdown("**Magasins et connaissance des clients**")
        st.dataframe(
            [
                {
                    "Entreprise": labels[c.id],
                    "Secteur": sectors.get(c.sector, c.sector),
                    "Magasins": c.stores,
                    "Connaissance des clients": c.market_insight,
                    "Salariés": len(c.employees),
                    "Bénéfice du mois (€)": c.profit,
                }
                for c in sorted(networks, key=lambda c: -c.stores)
            ],
            hide_index=True, width="stretch",
            column_config={
                "Magasins": st.column_config.NumberColumn(format="%.1f", width="small"),
                **_percent_columns("Connaissance des clients"),
                **_money_columns("Bénéfice du mois (€)"),
            },
        )
    if live.experience_learning and history:
        founders = sum(1 for p in simulation.people if p.ventures > 0)
        st.caption(
            f"Expérience : {founders} ménage(s) ont déjà fondé une entreprise ; salaire d'embauche négocié "
            f"en moyenne à {history[-1].get('negociation', 1.0):.1%} de l'offre."
        )


@st.cache_data(show_spinner=False, max_entries=4)
def render_funds_panel(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Sous-onglet « Fonds d'investissement » : fonds gérés par les banques, épargne des ménages,
    participations, sorties et rendement (sans widget)."""
    simulation = _simulation
    state = getattr(simulation, "enterprise", None)
    offset = simulation.warmup_offset
    st.markdown("#### Fonds d'investissement des banques")
    if state is None or not state.funds:
        st.info(
            "Aucun fonds : cochez « Jeunes entreprises non cotées et fonds des banques » dans la barre "
            "latérale (rubrique « Groupes, magasins, fonds & expérience »), puis réinitialisez. Chaque "
            "banque gère alors un fonds où les ménages placent une part de leur épargne ; le fonds "
            "finance les jeunes entreprises non cotées, puis revend ses titres après leur introduction "
            "en Bourse."
        )
        return
    labels = simulation.company_labels()
    sectors = {key: label.split(" (")[0] for key, label in display_sectors(
        company.sector for company in simulation.companies
    )}
    by_id = {c.id: c for c in simulation.companies}
    prices = {c.id: c.stock_price for c in simulation.companies if c.active}
    funds = state.funds
    assets = {fund.id: fund_assets(fund, prices) for fund in funds}
    holders = [p for p in simulation.people if p.fund_units]
    placed = sum(p.fund_value for p in holders)
    cols = st.columns(5)
    cols[0].metric("Actif des fonds", compact(sum(assets.values()), " €"),
                   help="Trésorerie et participations, au cours ou à la valeur estimée.")
    cols[1].metric("Dont trésorerie en attente", compact(sum(f.cash for f in funds), " €"))
    cols[2].metric("Participations", f"{sum(1 for f in funds for cid, q in f.shares.items() if q > 0.0 and cid in prices)}")
    cols[3].metric("Ménages porteurs de parts", f"{len(holders)}",
                   help=f"Sur {len(simulation.people)} ménages.")
    cols[4].metric("Épargne placée par porteur", compact(placed / len(holders), " €") if holders else "—")
    cols = st.columns(5)
    cols[0].metric("Épargne confiée (cumul)", compact(sum(f.subscribed for f in funds), " €"))
    cols[1].metric("Apports aux entreprises (cumul)", compact(sum(f.invested for f in funds), " €"))
    cols[2].metric("Dividendes et cessions encaissés", compact(sum(f.received for f in funds), " €"))
    cols[3].metric("Reversé aux porteurs (cumul)", compact(sum(f.distributed + f.redeemed for f in funds), " €"),
                   help="Distributions de trésorerie et rachats de parts.")
    cols[4].metric("Frais versés aux banques (cumul)", compact(sum(f.fees for f in funds), " €"))
    series = [row for row in state.fund_history if row["mois"] - offset >= 0]
    names = {fund.id: simulation.fund_label(fund.id, labels) for fund in funds}
    if len({row["mois"] for row in series}) > 1:
        by_month: dict[int, dict] = {}
        totals: dict[int, dict] = {}
        for row in series:
            period = row["mois"] - offset
            by_month.setdefault(period, {"Période": period})[names.get(row["fonds"], row["fonds"])] = row["valeur_part"]
            total = totals.setdefault(period, {"Période": period, "Participations": 0.0, "Trésorerie": 0.0})
            total["Participations"] += row["actif"] - row["tresorerie"]
            total["Trésorerie"] += row["tresorerie"]
        left, right = st.columns(2)
        with left:
            st.caption("Valeur d'une part de chaque fonds (1 € à la création ; baisse après une distribution)")
            line_chart([by_month[p] for p in sorted(by_month)], x="Période")
        with right:
            st.caption("Actif des fonds : participations et trésorerie en attente (€)")
            area_chart([totals[p] for p in sorted(totals)], x="Période", y=["Participations", "Trésorerie"])
    history = simulation.enterprise_history()
    if len(history) > 1:
        st.caption("Flux mensuels : épargne confiée, apports aux entreprises, reversements aux porteurs (€)")
        line_chart(
            [{"Période": row["periode"], "Épargne confiée": row.get("fonds_souscriptions", 0.0),
              "Apports aux entreprises": row.get("fonds_apports", 0.0),
              "Reversé aux porteurs": row.get("fonds_distributions", 0.0) + row.get("fonds_rachats", 0.0)}
             for row in history],
            x="Période", y=["Épargne confiée", "Apports aux entreprises", "Reversé aux porteurs"],
        )
    st.markdown("**Les fonds**")
    st.dataframe(
        [
            {
                "Fonds": names[fund.id],
                "Banque gérante": labels.get(fund.bank_id, "—"),
                "Actif (€)": assets[fund.id],
                "Trésorerie (€)": fund.cash,
                "Valeur de la part": assets[fund.id] / fund.units if fund.units > 1e-9 else 1.0,
                "Porteurs": sum(1 for p in holders if p.fund_units.get(fund.id, 0.0) > 0.0),
                "Participations": sum(1 for cid, q in fund.shares.items() if q > 0.0 and cid in prices),
                "Épargne confiée (€)": fund.subscribed,
                "Investi (€)": fund.invested,
                "Encaissé (€)": fund.received,
                "Reversé (€)": fund.distributed + fund.redeemed,
                "Frais (€)": fund.fees,
                "Résultat pour les porteurs (€)": assets[fund.id] + fund.distributed + fund.redeemed - fund.subscribed,
            }
            for fund in funds
        ],
        hide_index=True, width="stretch",
        column_config={
            **_money_columns("Actif (€)", "Trésorerie (€)", "Épargne confiée (€)", "Investi (€)", "Encaissé (€)",
                             "Reversé (€)", "Frais (€)"),
            "Valeur de la part": st.column_config.NumberColumn(format="%.3f", width="small"),
            "Résultat pour les porteurs (€)": st.column_config.NumberColumn(
                format="localized",
                help="Actif actuel + sommes déjà reversées − épargne confiée : ce que les ménages ont "
                     "gagné ou perdu, frais déduits.",
            ),
        },
    )
    positions = []
    for fund in funds:
        for company_id, quantity in fund.shares.items():
            company = by_id.get(company_id)
            if company is None or not company.active or quantity <= 0.0:
                continue
            bought = fund.bought.get(company_id, 0.0)
            cost = fund.cost.get(company_id, 0.0) * min(1.0, quantity / bought) if bought > 0.0 else None
            value = quantity * company.stock_price
            positions.append({
                "Fonds": names[fund.id],
                "Entreprise": labels[company_id],
                "Secteur": sectors.get(company.sector, company.sector),
                "Bourse": "cotée (en cours de cession)" if company.listed else "non cotée",
                "Entrée (période)": fund.entry[company_id] - offset if company_id in fund.entry else None,
                "Part du capital": quantity / max(company.shares_outstanding - company.treasury_shares, 1e-9),
                "Coût des titres détenus (€)": cost,
                "Valeur (€)": value,
                "Plus-value latente": (value - cost) / cost if cost else None,
                "Salariés": len(company.employees),
                "Bénéfice du mois (€)": company.profit,
            })
    st.markdown("**Participations en portefeuille**")
    if positions:
        st.dataframe(
            sorted(positions, key=lambda row: -row["Valeur (€)"]), hide_index=True, width="stretch",
            height=min(480, 38 + 35 * len(positions)),
            column_config={
                **_percent_columns("Part du capital", "Plus-value latente"),
                **_money_columns("Coût des titres détenus (€)", "Valeur (€)", "Bénéfice du mois (€)"),
                "Entrée (période)": st.column_config.NumberColumn(format="%d", width="small"),
                "Salariés": st.column_config.NumberColumn(format="%d", width="small"),
            },
        )
    else:
        st.caption("Aucune participation : aucune entreprise non cotée n'a encore eu besoin de trésorerie.")
    exits = [(fund, row) for fund in funds for row in fund.exits]
    if exits:
        st.markdown("**Participations soldées**")
        st.dataframe(
            [
                {
                    "Fonds": names[fund.id],
                    "Entreprise": labels.get(row["entreprise"], row["entreprise"]),
                    "Entrée (période)": row["entree"] - offset,
                    "Sortie (période)": row["sortie"] - offset,
                    "Durée (mois)": row["sortie"] - row["entree"],
                    "Investi (€)": row["investi"],
                    "Issue": row["issue"],
                }
                for fund, row in sorted(exits, key=lambda item: -item[1]["sortie"])[:60]
            ],
            hide_index=True, width="stretch", column_config=_money_columns("Investi (€)"),
        )
        outcomes: dict[str, int] = {}
        for _, row in exits:
            outcomes[row["issue"]] = outcomes.get(row["issue"], 0) + 1
        st.caption("Issues : " + " · ".join(f"{label} {count}" for label, count in sorted(outcomes.items())))


@st.cache_data(show_spinner=False, max_entries=4)
def render_bonds_panel(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Sous-onglet « Obligations » : dette de marché des entreprises (sans widget)."""
    simulation = _simulation
    st.markdown("#### Obligations d'entreprise")
    if not simulation.live.corporate_bonds:
        st.info(
            "Désactivé : cochez « Obligations d'entreprise » dans la barre latérale (rubrique "
            "« Obligations, commerce extérieur & syndicats »), puis réinitialisez. Les entreprises "
            "rentables emprunteront alors aux ménages et aux fonds, pas seulement aux banques."
        )
        return
    labels = simulation.company_labels()
    sectors = {key: label.split(" (")[0] for key, label in display_sectors(
        company.sector for company in simulation.companies
    )}
    history = simulation.enterprise_history()
    issuers = [c for c in simulation.companies if c.active and c.bond_debt > 0.0]
    outstanding = sum(c.bond_debt for c in issuers)
    bank_debt = sum(c.debt for c in simulation.companies if c.active and c.sector != "bank")
    holders = [p for p in simulation.people if p.corporate_bonds]
    state = getattr(simulation, "enterprise", None)
    fund_held = sum(sum(f.corporate_bonds.values()) for f in (state.funds if state is not None else ()))
    cols = st.columns(5)
    cols[0].metric("Encours obligataire", compact(outstanding, " €"),
                   help=f"Dette bancaire des entreprises : {compact(bank_debt, ' €')}.")
    cols[1].metric("Part de la dette des entreprises", f"{outstanding / (outstanding + bank_debt):.0%}"
                   if outstanding + bank_debt > 0 else "—")
    cols[2].metric("Émetteurs", f"{len(issuers)}")
    cols[3].metric("Ménages porteurs", f"{len(holders)}",
                   help=f"Les fonds des banques détiennent {compact(fund_held, ' €')}.")
    cols[4].metric("Taux moyen annuel",
                   f"{12.0 * sum(c.bond_debt * c.bond_rate for c in issuers) / outstanding:.1%}" if outstanding > 0 else "—",
                   help=f"Taux directeur annuel : {12.0 * simulation.policy_rate:.1%} ; taux des dépôts : "
                        f"{12.0 * simulation.deposit_rate:.1%}.")
    cols = st.columns(4)
    cols[0].metric("Émissions (cumul)", compact(sum(r.get("obligations_emissions", 0.0) for r in history), " €"))
    cols[1].metric("Dont remboursement de prêts bancaires",
                   compact(sum(r.get("obligations_refinancement", 0.0) for r in history), " €"))
    cols[2].metric("Coupons versés (cumul)", compact(sum(r.get("obligations_coupons", 0.0) for r in history), " €"))
    cols[3].metric("Capital remboursé (cumul)",
                   compact(sum(r.get("obligations_remboursements", 0.0) for r in history), " €"))
    if len(history) > 1:
        left, right = st.columns(2)
        with left:
            st.caption("Encours obligataire (€)")
            area_chart([{"Période": r["periode"], "Encours": r.get("obligations_encours", 0.0)} for r in history],
                       x="Période", y=["Encours"])
        with right:
            st.caption("Flux mensuels : émissions, coupons, remboursements (€)")
            line_chart(
                [{"Période": r["periode"], "Émissions": r.get("obligations_emissions", 0.0),
                  "Coupons": r.get("obligations_coupons", 0.0),
                  "Remboursements": r.get("obligations_remboursements", 0.0)} for r in history],
                x="Période", y=["Émissions", "Coupons", "Remboursements"],
            )
    if issuers:
        st.markdown("**Émetteurs**")
        st.dataframe(
            [
                {
                    "Entreprise": labels[c.id],
                    "Secteur": sectors.get(c.sector, c.sector),
                    "Encours obligataire (€)": c.bond_debt,
                    "Taux annuel": 12.0 * c.bond_rate,
                    "Dette bancaire (€)": c.debt,
                    "Taux bancaire annuel": 12.0 * c.debt_interest_rate if c.debt_interest_rate is not None else None,
                    "Coupon du mois (€)": c.bond_debt * c.bond_rate,
                    "Bénéfice du mois (€)": c.profit,
                }
                for c in sorted(issuers, key=lambda c: -c.bond_debt)
            ],
            hide_index=True, width="stretch", height=min(480, 38 + 35 * len(issuers)),
            column_config={
                **_money_columns("Encours obligataire (€)", "Dette bancaire (€)", "Coupon du mois (€)",
                                 "Bénéfice du mois (€)"),
                **_percent_columns("Taux annuel", "Taux bancaire annuel"),
            },
        )
    else:
        st.caption("Aucune obligation en circulation pour l'instant.")


@st.cache_data(show_spinner=False, max_entries=4)
def render_trade_panel(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Sous-onglet « Commerce extérieur » : échanges avec le reste du monde (sans widget)."""
    simulation = _simulation
    st.markdown("#### Commerce extérieur")
    state = getattr(simulation, "enterprise", None)
    if not simulation.live.foreign_trade or state is None or state.foreign is None:
        st.info(
            "Désactivé : cochez « Commerce extérieur » dans la barre latérale (rubrique « Obligations, "
            "commerce extérieur & syndicats »), puis réinitialisez. Le reste du monde vendra alors aux "
            "ménages ce que le marché intérieur ne sert pas, et redépensera cette monnaie en produits "
            "et en services intérieurs."
        )
        return
    history = simulation.enterprise_history()
    last = history[-1] if history else {}
    year = history[-12:]
    gdp = sum(r.nominal_gdp for r in simulation.observed_history[-12:]) or 1.0
    imports, exports = (sum(r.get(key, 0.0) for r in year) for key in ("importations", "exportations"))
    cols = st.columns(5)
    cols[0].metric("Taux de change", f"{state.exchange_rate:.2f}",
                   help="Prix d'une unité de monnaie étrangère : il monte quand les importations dépassent "
                        "les exportations, ce qui renchérit les produits importés.")
    cols[1].metric("Importations (12 mois)", compact(imports, " €"), help=f"{imports / gdp:.1%} du PIB.")
    cols[2].metric("Exportations (12 mois)", compact(exports, " €"), help=f"{exports / gdp:.1%} du PIB.")
    cols[3].metric("Solde (12 mois)", compact(exports - imports, " €"))
    cols[4].metric("Avoirs du reste du monde", compact(state.foreign.cash, " €"),
                   help="Monnaie intérieure gagnée et pas encore redépensée (plafonnée à 2 % du PIB annuel).")
    if len(history) > 1:
        left, right = st.columns(2)
        with left:
            st.caption("Importations et exportations du mois (€)")
            line_chart(
                [{"Période": r["periode"], "Importations": r.get("importations", 0.0),
                  "Exportations": r.get("exportations", 0.0)} for r in history],
                x="Période", y=["Importations", "Exportations"],
            )
        with right:
            st.caption("Taux de change")
            line_chart([{"Période": r["periode"], "Taux de change": r.get("taux_de_change", 1.0)} for r in history],
                       x="Période", y=["Taux de change"])
        st.caption("Quantités importées (unités) et part des exportations en services et technologie (€)")
        line_chart(
            [{"Période": r["periode"], "Alimentation importée": r.get("importations_food", 0.0),
              "Biens importés": r.get("importations_goods", 0.0)} for r in history],
            x="Période", y=["Alimentation importée", "Biens importés"],
        )
    goods = sum(r.get("exportations", 0.0) - r.get("exportations_services", 0.0) for r in year)
    st.caption(
        f"Sur douze mois, le reste du monde a acheté {compact(max(0.0, goods), ' €')} d'alimentation et de "
        f"biens en stock et {compact(sum(r.get('exportations_services', 0.0) for r in year), ' €')} de services "
        f"et de technologie. Prix mondiaux convertis ce mois : alimentation "
        f"{state.world_prices.get('food', 0.0) * state.exchange_rate:,.0f} € (intérieur "
        f"{simulation.food_price:,.0f} €), biens {state.world_prices.get('goods', 0.0) * state.exchange_rate:,.0f} € "
        f"(intérieur {simulation.goods_price:,.0f} €)."
    )


@st.cache_data(show_spinner=False, max_entries=4)
def render_unions_panel(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Sous-onglet « Syndicats » : accords salariaux de secteur et grèves (sans widget)."""
    simulation = _simulation
    st.markdown("#### Syndicats et négociation collective")
    state = getattr(simulation, "enterprise", None)
    if not simulation.live.collective_bargaining or state is None:
        st.info(
            "Désactivé : cochez « Syndicats et négociation collective » dans la barre latérale (rubrique "
            "« Obligations, commerce extérieur & syndicats »), puis réinitialisez. Chaque secteur "
            "négociera alors un accord salarial par an."
        )
        return
    offset = simulation.warmup_offset
    sectors = {key: label.split(" (")[0] for key, label in display_sectors(
        company.sector for company in simulation.companies
    )}
    agreements = [row for row in state.agreements if row["mois"] - offset >= 0]
    strikes = [row for row in agreements if row["greve"]]
    unemployment = simulation.observed_history[-1].unemployment if simulation.observed_history else 0.0
    cols = st.columns(5)
    cols[0].metric("Pouvoir des syndicats", f"{simulation._union_power(unemployment):.0%}",
                   help="Part de sa demande qu'un syndicat obtient aujourd'hui : dépend de l'orientation du "
                        "gouvernement et du chômage.")
    cols[1].metric("Accords conclus", f"{len(agreements)}")
    cols[2].metric("Grèves", f"{len(strikes)}")
    cols[3].metric("Hausse moyenne obtenue",
                   f"{sum(r['accord'] for r in agreements) / len(agreements):.1%}" if agreements else "—",
                   help="Par accord annuel ; demande moyenne : "
                        + (f"{sum(r['demande'] for r in agreements) / len(agreements):.1%}." if agreements else "—"))
    cols[4].metric("Employeurs sous accord", f"{len(state.wage_floors)}",
                   help="Leur offre salariale ne peut pas descendre sous le niveau négocié.")
    if agreements:
        st.caption("Hausse demandée et hausse obtenue à chaque accord (%)")
        line_chart(
            [{"Période": r["mois"] - offset, "Demandée": 100.0 * r["demande"], "Obtenue": 100.0 * r["accord"]}
             for r in agreements],
            x="Période", y=["Demandée", "Obtenue"],
        )
        by_sector = {}
        for row in agreements:
            entry = by_sector.setdefault(row["secteur"], {"accords": 0, "greves": 0, "hausse": 0.0})
            entry["accords"] += 1
            entry["greves"] += int(row["greve"])
            entry["hausse"] += row["accord"]
        st.markdown("**Par secteur**")
        st.dataframe(
            [
                {"Secteur": sectors.get(sector, sector), "Accords": entry["accords"], "Grèves": entry["greves"],
                 "Hausse cumulée obtenue": entry["hausse"],
                 "Hausse moyenne par accord": entry["hausse"] / entry["accords"]}
                for sector, entry in sorted(by_sector.items(), key=lambda item: -item[1]["hausse"])
            ],
            hide_index=True, width="stretch",
            column_config=_percent_columns("Hausse cumulée obtenue", "Hausse moyenne par accord"),
        )
        st.markdown("**Derniers accords**")
        st.dataframe(
            [
                {"Période": r["mois"] - offset, "Secteur": sectors.get(r["secteur"], r["secteur"]),
                 "Salariés couverts": r["salaries"], "Hausse demandée": r["demande"],
                 "Hausse obtenue": r["accord"], "Pouvoir du syndicat": r["pouvoir"],
                 "Grève": "oui" if r["greve"] else "non"}
                for r in reversed(agreements[-40:])
            ],
            hide_index=True, width="stretch",
            column_config=_percent_columns("Hausse demandée", "Hausse obtenue", "Pouvoir du syndicat"),
        )
    else:
        st.caption("Aucun accord pour l'instant : il faut un an d'historique et une demande du syndicat "
                   "(inflation non rattrapée par les salaires, ou marges au-delà de la référence du secteur).")


@st.cache_data(show_spinner=False, max_entries=16)
def render_company_profile(
    view_id: str, month: int, selected_company_id: str, _simulation: EconomySimulation
) -> None:
    """Début du profil détaillé d'une entreprise (indicateurs, bilan, valorisation, relations
    interentreprises), mémorisé par simulation, mois et entreprise ; aucun widget de saisie."""
    simulation = _simulation
    selected_company = (
        simulation.company_profile(selected_company_id) if selected_company_id else None
    )
    if selected_company:
        labels = simulation.company_labels()
        st.subheader(labels[selected_company.id])
        founder_label, founder_share = simulation.founder_stake(selected_company)
        st.caption(
            f"{sector_label(selected_company.sector).split(' (')[0]} · "
            + (f"filiale de {labels[selected_company.parent_id]} · "
               if selected_company.parent_id in labels else "")
            + f"{'cotée en Bourse' if selected_company.listed else 'non cotée'} · "
            f"PDG : {simulation.person_name(selected_company.executive_id)}"
            + (
                f" · Fondateur : {founder_label}"
                + (f", {founder_share:.1%} du capital émis" if founder_share is not None else " (sorti)")
                + f" · titres offerts non souscrits : {selected_company.treasury_shares:,.0f}"
                if selected_company.founder_id is not None else " · Entreprise initiale"
            )
        )
        plans = [e for e in simulation.resolution_events
                 if e.kind == "safeguard" and e.company_id == selected_company.id]
        if plans:
            # lot T3 : plans de sauvegarde (mois observés, comme les autres historiques)
            offset = simulation.month - simulation.period
            st.caption("Procédure de sauvegarde : " + " ; ".join(
                f"période {e.month - offset}, dette abandonnée par les banques {compact(e.debt_written_off, ' €')}"
                for e in plans))
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
                f"{selected_company.property_inventory * simulation.live.housing_price:,.0f} € · "
                f"loyer courant {simulation.rent:,.0f} € / mois · "
                f"logements vendus aux ménages ce mois : {selected_company.homes_sold} · "
                f"demandes de location reçues non servies : {selected_company.unserved_rentals} · "
                f"logements à lancer selon sa prévision : {selected_company.housing_gap} · "
                f"terrains disponibles dans la ville : {simulation.resources.land_units}"
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
                    f"{physical_stock * simulation.live.food_input_cost * simulation.resources.input_cost_index:,.2f} € "
                    "(au coût courant des intrants)."
                )
        # valorisation aux prix courants du mois (prix des logements, coûts indexés)
        balance_sheet = company_balance_sheet(
            selected_company, simulation.companies, simulation.live
        )
        valuation = company_valuation_metrics(
            selected_company, simulation.companies, simulation.live
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
                            f"{simulation.company_label(partner_id)} · "
                            f"{transaction_label} {amount:,.0f} €"
                        )


@st.cache_data(show_spinner=False, max_entries=16)
def render_company_history(
    view_id: str, month: int, selected_company_id: str, _simulation: EconomySimulation
) -> None:
    """Historiques d'une entreprise, mémorisés par simulation, mois et entreprise ; affichés
    seulement quand leur volet est ouvert. Les mêmes séries qu'auparavant, regroupées en quatre
    graphiques au lieu de huit (A4, novembre 2026 : la génération Altair coûtait 0,36 s au premier
    dépliage) : patrimoine et dette, flux du mois, activité et effectifs, cours. Les dividendes
    ont leur propre graphique (lot A1, novembre 2026) : mêlés aux flux du mois, leur échelle
    était écrasée par celle du chiffre d'affaires."""
    selected_company = _simulation.company_profile(selected_company_id)
    # mois observés seulement (lot F2) : l'historique garde aussi les mois de chauffe, numérotés
    # 1 à 120 comme les mois observés ; affichés ensemble, ils formaient des dents de scie
    firm_history = observed_rows(selected_company.financial_history, _simulation)
    history_first, history_second = st.columns(2)
    with history_first:
        st.caption("Trésorerie, valeur nette et dette (€)")
        line_chart(firm_history, x="Période", y=["Trésorerie", "Fortune", "Dettes"])
    with history_second:
        st.caption("Flux du mois : chiffre d'affaires, bénéfice, flux de trésorerie disponible (€)")
        line_chart(
            firm_history,
            x="Période",
            y=["Chiffre d'affaires", "Bénéfice", "Flux de trésorerie disponible"],
        )
    st.caption(
        "Dividendes versés par mois (€). Le modèle ne comporte pas de rachat d'actions par "
        "l'émetteur : les « rachats » de la simulation sont des prises de contrôle d'autres "
        "entreprises, sans effet sur les actions de l'entreprise elle-même."
    )
    # aire plutôt que barres : l'axe des périodes reste continu (des centaines de mois)
    area_chart(firm_history, x="Période", y="Dividendes")
    history_third, history_fourth = st.columns(2)
    with history_third:
        st.caption("Activité : effectifs, production, stocks et capacité ajoutée (unités)")
        line_chart(
            firm_history,
            x="Période",
            y=["Employés", "Production mensuelle", "Stocks alimentaires", "Stocks de biens",
               "Capacité supplémentaire"],
        )
    with history_fourth:
        st.caption("Cours de l'action (€)")
        line_chart(firm_history, x="Période", y="Cours")


@st.cache_data(show_spinner=False, max_entries=16)
def render_company_profile_end(
    view_id: str, month: int, selected_company_id: str, _simulation: EconomySimulation
) -> None:
    """Fin de la fiche : équipe, participations détenues et prêts d'une banque."""
    simulation = _simulation
    companies_by_id = {company.id: company for company in simulation.companies}
    selected_company = simulation.company_profile(selected_company_id)
    labels = simulation.company_labels()
    if selected_company:
        board = simulation.shareholder_rows(selected_company, simulation.live.board_seats)
        if board:
            st.markdown(
                "**Conseil d'actionnaires**" if simulation.live.shareholder_governance
                else "**Principaux actionnaires**"
            )
            st.caption(
                "Les plus gros actionnaires nomment et révoquent le dirigeant, et orientent la part des "
                "bénéfices réinvestie." if simulation.live.shareholder_governance else
                "Le dirigeant est choisi parmi les salariés ; cochez « Conseil d'actionnaires » dans la "
                "barre latérale pour que ces actionnaires le nomment et le révoquent."
            )
            st.dataframe(
                board, hide_index=True, width="stretch",
                column_config={
                    "Titres": st.column_config.NumberColumn(format="%.1f", width="small"),
                    "Part du capital émis": st.column_config.NumberColumn(format="percent", width="small"),
                },
            )
        subsidiaries = [
            c for c in simulation.companies if c.active and c.parent_id == selected_company.id
        ]
        absorbed = simulation.absorbed_companies(selected_company.id)
        if subsidiaries or absorbed or selected_company.parent_id in labels:
            st.markdown("**Groupe**")
            if selected_company.parent_id in labels:
                st.markdown(f"- Société mère : **{labels[selected_company.parent_id]}**")
            for company in subsidiaries:
                issued = max(company.shares_outstanding - company.treasury_shares, 1e-9)
                st.markdown(
                    f"- Filiale : **{labels[company.id]}** · "
                    f"{sector_label(company.sector).split(' (')[0]} · "
                    f"{selected_company.shares.get(company.id, 0.0) / issued:.0%} du capital · "
                    f"{len(company.employees)} salariés · bénéfice du mois {company.profit:,.0f} €"
                )
            if absorbed:
                st.markdown("Entreprises rachetées et absorbées (fusions) :")
                for row in absorbed:
                    st.markdown(
                        f"- **{row['Entreprise']}** · {row['Secteur'].split(' (')[0]} · "
                        f"période {row['Période du rachat']}"
                    )
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
            if issuer is None or not issuer.active:
                continue
            ownership = (
                shares / issuer.shares_outstanding
                if issuer.shares_outstanding > 0.0
                else 0.0
            )
            st.markdown(
                f"- **{labels[issuer.id]}** · {shares:.1f} actions · "
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
                borrower_name = simulation.company_label(borrower_id)
                borrower_type = "Prêt entreprise"
            st.markdown(
                f"- **{borrower_name}** · {borrower_type} · solde "
                f"{balance:,.0f} €"
            )


@st.cache_data(show_spinner=False, max_entries=8)
def render_stock_chart(
    view_id: str, month: int, stock_selection: tuple[str, ...], _simulation: EconomySimulation
) -> None:
    """Courbe des cours des entreprises choisies, mémorisée par simulation, mois et sélection."""
    simulation = _simulation
    observed_history = simulation.observed_history
    companies_by_id = simulation.company_labels()
    stock_history = [
        {
            "Période": record.month,
            **{
                companies_by_id[company_id]: record.company_prices.get(
                    company_id, 0.0
                )
                for company_id in stock_selection
            },
        }
        for record in observed_history
    ]
    line_chart(stock_history, x="Période")


def render_overview(simulation: EconomySimulation) -> None:
    tabs = result_tabs(
        ["Marchés", "Fortunes & concentration", "Secteurs & ressources"], "overview_section",
    )
    for tab, renderer in zip(tabs, (render_overview_markets, render_overview_wealth, render_overview_sectors)):
        if tab_is_open(tab):
            with tab:
                renderer(st.session_state._view_id, simulation.month, simulation)


def render_media_panel(simulation: EconomySimulation) -> None:
    if not simulation.live.media_sector:
        st.info("Activez « Entreprises de médias », puis réinitialisez la simulation.")
        return
    st.caption("Les abonnements et la publicité financent les journalistes. La ligne suit les propriétaires ; "
               "les enquêtes dépendent de l'indépendance vis-à-vis des propriétaires et annonceurs.")
    rows = simulation.media_table()
    if rows:
        labels = {p.id: p.name for p in simulation.people}
        labels.update(simulation.company_labels())
        st.dataframe([{
            "Média": r["nom"], "Propriétaire": labels.get(r["proprietaire"], r["proprietaire"] or "—"),
            "Ligne (0 droite, 100 gauche)": r["ligne"], "Lecteurs": r["lecteurs"],
            "Journalistes": r["journalistes"], "Crédibilité": r["credibilite"],
            "Abonnements (€)": r["abonnements"], "Publicité (€)": r["publicite"],
            "Trésorerie (€)": r["tresorerie"], "Révélations": r["revelations"],
        } for r in rows], hide_index=True, width="stretch")
    else:
        st.info("Aucun média en activité : une fondation exige un ménage ayant assez de capital.")
    history = simulation.media_history()
    if history:
        line_chart([{"Période": r["periode"], "Lecteurs": r["lecteurs"],
                     "Journalistes": r["journalistes"]} for r in history], "Période")
        line_chart([{"Période": r["periode"], "Pluralisme": r["pluralisme"]} for r in history], "Période")
    events = simulation.media_events()
    if events:
        st.dataframe(events[:100], hide_index=True, width="stretch")


def render_bank_relations_panel(simulation: EconomySimulation) -> None:
    if not simulation.live.bank_relationships:
        st.info("Activez « Relations bancaires individuelles », puis réinitialisez la simulation.")
        return
    rows = simulation.bank_relations_table()
    if rows:
        st.dataframe([{
            "Banque": r["nom"], "Ménages clients": r["menages"], "Autres clients": r["autres_clients"],
            "Dépôts (€)": r["depots"], "Prêts (€)": r["prets"],
            "Écart de financement (€)": r["ecart_financement"],
            "Taux annuel des dépôts": r["taux_depots_annuel"], "Solidité": r["solidite"],
            "Spécialité": r["specialite"], "Part de la spécialité": r["part_specialite"],
        } for r in rows], hide_index=True, width="stretch", column_config={
            "Taux annuel des dépôts": st.column_config.NumberColumn(format="percent"),
            "Part de la spécialité": st.column_config.NumberColumn(format="percent"),
        })
    else:
        st.info("Avancez d'un mois pour établir les relations bancaires.")
    history = simulation.bank_relations_history()
    if history:
        line_chart([{"Période": r["periode"], "Changements": r["changements"],
                     "Fuites": r["fuites"]} for r in history], "Période")
        line_chart([{"Période": r["periode"], "Concentration des dépôts": r["concentration_depots"],
                     "Part des prêts à la banque principale": r["part_banque_principale"]}
                    for r in history], "Période")
    state = getattr(simulation, "bankrel", None)
    if state:
        labels = simulation.company_labels()
        with st.expander("Comptes des clients"):
            clients = simulation._bank_clients()
            st.dataframe([{"Client": getattr(agent, "name", agent_id), "Identifiant": agent_id,
                           "Banque": labels.get(state.home.get(agent_id), "—"),
                           "Trésorerie (€)": agent.cash}
                          for agent_id, agent in clients], hide_index=True, width="stretch")


def render_learning_panel(simulation: EconomySimulation) -> None:
    if not simulation.live.adaptive_agents:
        st.info("Activez « Agents apprenants », puis réinitialisez la simulation.")
        return
    st.caption("Les entreprises essaient des prix et des taux de réinvestissement, annulent les essais "
               "décevants et imitent les plus rentables de leur secteur. Les ménages adaptent leur "
               "précaution et leur goût du risque ; les dirigeants emportent leur style.")
    history = simulation.learning_history()
    if not history:
        st.info("Avancez d'un mois pour observer les premières décisions.")
        return
    line_chart([{"Période": r["periode"], "Agressivité des prix": r["agressivite_prix"],
                 "Part réinvestie": r["part_reinvestie"], "Goût du risque": r["gout_du_risque"],
                 "Propension à consommer": r["propension_a_consommer"]} for r in history], "Période")
    st.dataframe([{"Période": r["periode"], "Essais gardés": r["essais_gardes"],
                   "Essais annulés": r["essais_annules"], "Imitations": r["imitations"],
                   "Styles apportés": r["styles_apportes"], "Révisions du risque": r["revisions_risque"]}
                  | {"Essais des banques": r.get("essais_banques", 0),
                     "Essais des ministres": r.get("essais_ministres", 0)}
                  for r in history[-60:]], hide_index=True, width="stretch")


def _society_off(simulation: EconomySimulation, setting: str, label: str) -> bool:
    """Message d'activation d'un mécanisme du lot V ; vrai s'il est désactivé."""
    if getattr(simulation.live, setting):
        return False
    st.info(f"Désactivé : cochez « {label} » dans la barre latérale (rubrique « Société : dettes, "
            "jeux, marques & finance »), puis réinitialisez la simulation.")
    return True


def _society_series(history: list, columns: dict, title: str | None = None) -> None:
    """Courbe des relevés du lot V : ``columns`` associe un libellé à une clé du relevé."""
    rows = [{"Période": r["periode"], **{label: r.get(key, 0.0) for label, key in columns.items()}}
            for r in history]
    if len(rows) >= 2:
        line_chart(rows, "Période", list(columns), title=title)


def render_society_households(simulation: EconomySimulation, history: list) -> None:
    state = getattr(simulation, "society", None)
    labels = {p.id: display_name(p.id) for p in simulation.people}
    st.markdown("#### Surendettement et faillite personnelle")
    if not _society_off(simulation, "personal_bankruptcy", "Surendettement et faillite personnelle"):
        offset = simulation.warmup_offset
        relief = [r for r in state.relief if r["mois"] - offset >= 0] if state is not None else []
        cols = st.columns(4)
        cols[0].metric("Interdits de crédit", f"{len(state.bans) if state is not None else 0}")
        cols[1].metric("Dossiers et défauts", f"{len(relief)}")
        cols[2].metric("Dettes effacées", compact(sum(r["efface"] for r in relief), " €"))
        cols[3].metric("Recouvré par les banques", compact(sum(r["rembourse"] for r in relief), " €"))
        _society_series(history, {"Interdits de crédit": "interdits_en_cours"})
        if relief:
            st.dataframe([{
                "Période": r["mois"] - offset, "Ménage": labels.get(r["menage"], r["menage"]),
                "Motif": r["motif"], "Dette (€)": r["dette"], "Remboursé (€)": r["rembourse"],
                "Effacé (€)": r["efface"],
            } for r in reversed(relief[-100:])], hide_index=True, width="stretch")
        else:
            st.caption("Aucun dossier pour l'instant : il faut un ménage en impayé dont la charge de "
                       "dette dépasse la part de revenu réglée.")
    st.markdown("#### Loterie d'État")
    if not _society_off(simulation, "state_lottery", "Loterie d'État"):
        offset = simulation.warmup_offset
        winners = [w for w in state.winners if w["mois"] - offset >= 0] if state is not None else []
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Cagnotte", compact(last.get("loterie_cagnotte", 0.0), " €"))
        cols[1].metric("Recette de l'État (cumul)", compact(sum(r.get("loterie_etat", 0.0) for r in history), " €"))
        cols[2].metric("Effort des revenus modestes", f"{last.get('loterie_effort_modestes', 0.0):.2%}",
                       help="Mises du mois / revenu du mois, cinquième des joueurs aux revenus les plus bas.")
        cols[3].metric("Effort des revenus aisés", f"{last.get('loterie_effort_aises', 0.0):.2%}",
                       help="Même mesure pour le cinquième aux revenus les plus hauts.")
        _society_series(history, {"Mises (€)": "loterie_mises", "Part de l'État (€)": "loterie_etat"})
        if winners:
            st.markdown("**Gagnants**")
            st.dataframe([{
                "Période": w["mois"] - offset, "Ménage": labels.get(w["menage"], w["menage"]),
                "Gain (€)": w["gain"], "Trésorerie avant (€)": w["tresorerie_avant"],
            } for w in reversed(winners[-100:])], hide_index=True, width="stretch")
    st.markdown("#### Dons et fondations")
    if not _society_off(simulation, "philanthropy", "Dons et fondations"):
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Dons (cumul)", compact(sum(r.get("dons", 0.0) for r in history), " €"))
        cols[1].metric("Trésorerie des fondations", compact(last.get("fondations_tresorerie", 0.0), " €"))
        cols[2].metric("Qualité de l'éducation ajoutée", f"{last.get('effet_ecole', 0.0):.3f}")
        cols[3].metric("Mortalité évitée", f"{last.get('effet_hopital', 0.0):.2%}")
        foundations = simulation.foundations_table()
        if foundations:
            st.dataframe([{"Fondation": r["nom"], "Trésorerie (€)": r["tresorerie"]} for r in foundations],
                         hide_index=True, width="stretch")
        donors = simulation.donors_table()
        if donors:
            st.markdown("**Plus gros donateurs**")
            st.dataframe([{
                "Ménage": r["nom"], "Dons cumulés (€)": r["dons"], "Renommée": r["renommee"], "Cause": r["cause"],
            } for r in donors], hide_index=True, width="stretch",
                column_config=_percent_columns("Renommée"))
        else:
            st.caption("Aucun don pour l'instant : aucun ménage ne dépasse le multiple de fortune réglé.")
    st.markdown("#### Tabac et alcool")
    if not _society_off(simulation, "addictive_goods", "Tabac et alcool"):
        last = history[-1] if history else {}
        firms = [c for c in simulation.companies if c.active and c.sector == "vice"]
        labels = simulation.company_labels()
        business = st.columns(4)
        business[0].metric("Entreprises de tabac et alcool", f"{len(firms)}")
        business[1].metric("Salariés du secteur", f"{sum(len(c.employees) for c in firms)}")
        business[2].metric("Chiffre d'affaires du mois", compact(sum(c.revenue for c in firms), " €"))
        business[3].metric("Bénéfice du secteur", compact(sum(c.profit for c in firms), " €"))
        if firms:
            st.dataframe([{
                "Entreprise": labels[c.id], "Salariés": len(c.employees),
                "Chiffre d'affaires (€)": c.revenue, "Bénéfice (€)": c.profit,
                "Technologie": c.tech_level, "Pouvoir addictif (×)": simulation._vice_addictiveness(c),
            } for c in sorted(firms, key=lambda c: labels[c.id])], hide_index=True, width="stretch",
                column_config={
                    **_money_columns("Chiffre d'affaires (€)", "Bénéfice (€)"),
                    "Technologie": st.column_config.NumberColumn(format="%.2f"),
                    "Pouvoir addictif (×)": st.column_config.NumberColumn(
                        format="%.2f", help="Multiplicateur lié à la technologie ; 1 correspond au niveau de départ.",
                    ),
                })
        else:
            st.caption("Aucune entreprise active : un ménage disposant du capital nécessaire pourra en fonder une.")
        cols = st.columns(5)
        cols[0].metric("Consommateurs", f"{last.get('vice_consommateurs', 0):.0f}")
        cols[1].metric("Dépendance moyenne", f"{last.get('vice_dependance_moyenne', 0.0):.2f}")
        cols[2].metric("Prix / référence", f"{last.get('vice_prix_relatif', 0.0):.2f}",
                       help="Au-delà de 1 : les fabricants ont relevé leurs prix sans perdre leurs volumes.")
        cols[3].metric("Accises (cumul)", compact(sum(r.get("vice_accises", 0.0) for r in history), " €"))
        cols[4].metric("Ménages à soigner", f"{last.get('vice_malades', 0):.0f}")
        _society_series(history, {"Ventes hors taxes (€)": "vice_ventes", "Accises (€)": "vice_accises",
                                  "Soins publics (€)": "vice_soins_publics",
                                  "Soins des ménages (€)": "vice_soins_menages"})
        _society_series(history, {"Dépendance moyenne": "vice_dependance_moyenne",
                                  "Dégâts de santé moyens": "vice_degats_moyens"})


def render_society_firms(simulation: EconomySimulation, history: list) -> None:
    labels = simulation.company_labels()
    st.markdown("#### Marques et agences de marketing")
    if not _society_off(simulation, "brand_marketing", "Marques et agences de marketing"):
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Agences", f"{last.get('agences', 0):.0f}")
        cols[1].metric("Salariés des agences", f"{last.get('agences_salaries', 0):.0f}")
        cols[2].metric("Campagnes du mois", compact(last.get("marques_depenses", 0.0), " €"))
        cols[3].metric("Budgets non servis", compact(last.get("marques_non_servies", 0.0), " €"),
                       help="Budgets que les agences n'ont pas pu livrer faute de salariés.")
        brands = simulation.brands_table()
        if brands:
            st.dataframe([{
                "Entreprise": labels.get(r["id"], r["nom"]), "Secteur": sector_label(r["secteur"]),
                "Notoriété (€)": r["notoriete"], "Poids dans les achats": r["poids"], "Prix (€)": r["prix"],
                "Franchise": "oui" if r["franchise"] else "",
            } for r in brands[:60]], hide_index=True, width="stretch")
        _society_series(history, {"Alimentation": "prime_de_marque_food", "Biens": "prime_de_marque_goods"},
                        title="Écart de prix des producteurs à marque sur les autres")
    st.markdown("#### Franchises")
    if not _society_off(simulation, "franchising", "Franchises"):
        rows = simulation.franchises_table()
        cols = st.columns(3)
        cols[0].metric("Franchises en activité", f"{len(rows)}")
        cols[1].metric("Ouvertures (cumul)", f"{sum(r.get('franchises_ouvertes', 0) for r in history):.0f}")
        cols[2].metric("Redevances (cumul)", compact(sum(r.get("franchises_redevances", 0.0) for r in history), " €"))
        if rows:
            st.dataframe([{
                "Franchisé": labels.get(r["id"], r["nom"]), "Franchiseur": r["franchiseur"],
                "Ouverture (période)": r["periode"], "Salariés": r["salaries"],
                "Droit d'entrée (€)": r["droits"], "Redevances versées (€)": r["redevances"],
                "Trésorerie (€)": r["tresorerie"],
            } for r in rows], hide_index=True, width="stretch")
        else:
            st.caption("Aucune franchise : il faut un producteur rentable qui refuse des commandes et "
                       "un ménage entreprenant doté de deux droits d'entrée.")
    st.markdown("#### Coopératives de chômeurs")
    if not _society_off(simulation, "worker_buyouts", "Coopératives de chômeurs"):
        rows = simulation.cooperatives_table()
        cols = st.columns(3)
        cols[0].metric("Reprises", f"{len(rows)}")
        cols[1].metric("Encore en activité", f"{sum(1 for r in rows if r['active'])}")
        cols[2].metric("Reprises abandonnées", f"{sum(r.get('reprises_abandonnees', 0) for r in history):.0f}",
                       help="Capital réuni inférieur à deux mois de paie.")
        if rows:
            st.dataframe([{
                "Coopérative": r["nom"], "Reprise (période)": r["periode"], "Repreneurs": r["repreneurs"],
                "Capital (€)": r["capital"], "Avance de l'État (€)": r["avance"],
                "En activité": "oui" if r["active"] else "non", "Salariés": r["salaries"],
            } for r in rows], hide_index=True, width="stretch")
        else:
            st.caption("Aucune reprise : il faut la liquidation d'une entreprise qui avait encore des salariés.")


def render_society_finance(simulation: EconomySimulation, history: list) -> None:
    st.markdown("#### Vente à découvert et fonds spéculatifs")
    if not _society_off(simulation, "short_selling", "Vente à découvert et fonds spéculatifs"):
        funds = simulation.hedge_table()
        closed = simulation.shorts_table()
        cols = st.columns(4)
        cols[0].metric("Fonds en activité", f"{len(funds)}")
        cols[1].metric("Positions ouvertes", f"{sum(r['positions'] for r in funds)}")
        cols[2].metric("Positions closes", f"{len(closed)}")
        cols[3].metric("Gains réalisés", compact(sum(r["gain"] for r in closed), " €"))
        if funds:
            st.dataframe([{
                "Fonds": r["nom"], "Capital (€)": r["capital"], "Trésorerie (€)": r["tresorerie"],
                "Souscripteurs": r["souscripteurs"], "Positions": r["positions"],
                "Titres dus (€)": r["engagement"], "Gains (€)": r["gains"],
            } for r in funds], hide_index=True, width="stretch")
        else:
            st.caption("Aucun fonds : il faut des ménages au goût du risque élevé et à la trésorerie suffisante.")
        if closed:
            st.markdown("**Positions closes**")
            st.dataframe([{
                "Période": r["periode"], "Fonds": r["fonds"], "Entreprise": r["entreprise"], "Motif": r["motif"],
                "Cours de vente (€)": r["entree"], "Cours de rachat (€)": r["sortie"], "Gain (€)": r["gain"],
            } for r in closed[:100]], hide_index=True, width="stretch")
    st.markdown("#### Titrisation des prêts immobiliers")
    if not _society_off(simulation, "mortgage_securitization", "Titrisation des prêts immobiliers"):
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Titres en circulation", compact(last.get("titrisation_encours", 0.0), " €"))
        cols[1].metric("Part des prêts immobiliers", f"{last.get('titrisation_part', 0.0):.1%}")
        cols[2].metric("Ménages porteurs", f"{last.get('titrisation_porteurs', 0):.0f}")
        cols[3].metric("Pertes des porteurs (cumul)",
                       compact(sum(r.get("titrisation_pertes", 0.0) for r in history), " €"))
        pools = simulation.pools_table()
        if pools:
            st.dataframe([{
                "Banque": r["banque"], "Titres (€)": r["encours"], "Prêts immobiliers (€)": r["prets_immobiliers"],
                "Part titrisée": r["part"], "Porteurs": r["porteurs"], "Coupons versés (€)": r["coupons"],
                "Pertes transmises (€)": r["pertes"],
            } for r in pools], hide_index=True, width="stretch", column_config=_percent_columns("Part titrisée"))
        _society_series(history, {"Titres en circulation (€)": "titrisation_encours"})
    st.markdown("#### Financement participatif")
    if not _society_off(simulation, "crowdfunding", "Financement participatif"):
        last = history[-1] if history else {}
        rows = simulation.crowd_table()
        cols = st.columns(4)
        cols[0].metric("Prêts en cours", compact(last.get("participatif_encours", 0.0), " €"))
        cols[1].metric("Entreprises financées", f"{len(rows)}")
        cols[2].metric("Prêts levés (cumul)", compact(sum(r["leve"] for r in rows), " €"))
        cols[3].metric("Emprunteurs en faillite", f"{sum(1 for r in rows if r['defaut'])}")
        if rows:
            labels = simulation.company_labels()
            st.dataframe([{
                "Entreprise": labels.get(r["id"], r["nom"]), "Secteur": sector_label(r["secteur"]),
                "Levé (€)": r["leve"], "Encours (€)": r["encours"], "Taux annuel": r["taux_annuel"],
                "État": "faillite" if r["defaut"] else ("active" if r["active"] else "fermée"),
            } for r in rows[-100:]], hide_index=True, width="stretch", column_config=_percent_columns("Taux annuel"))


def render_society_state(simulation: EconomySimulation, history: list) -> None:
    state = getattr(simulation, "society", None)
    st.markdown("#### Manifestations et émeutes")
    if not _society_off(simulation, "social_unrest", "Manifestations et émeutes"):
        offset = simulation.warmup_offset
        protests = [p for p in state.protests if p["mois"] - offset >= 0] if state is not None else []
        last = history[-1] if history else {}
        cols = st.columns(4)
        cols[0].metric("Tension sociale", f"{last.get('tension_sociale', 0.0):.2f}",
                       help="Manifestation à 1, émeute à 2.")
        cols[1].metric("Manifestations", f"{len(protests)}")
        cols[2].metric("Émeutes", f"{sum(1 for p in protests if p['emeute'])}")
        cols[3].metric("Concessions du gouvernement", f"{sum(1 for p in protests if p['concession'] > 0.0)}")
        _society_series(history, {"Tension": "tension_sociale", "Pression": "pression_sociale"})
        if protests:
            st.dataframe([{
                "Période": p["mois"] - offset, "Type": "émeute" if p["emeute"] else "manifestation",
                "Tension": p["tension"], "Chômage": p["chomage"], "Travail perdu": p["travail_perdu"],
                "Concession": p["concession"], "Participants": p["participants"],
            } for p in reversed(protests[-100:])], hide_index=True, width="stretch",
                column_config=_percent_columns("Chômage", "Travail perdu", "Concession"))
    st.markdown("#### Grands travaux")
    if not _society_off(simulation, "public_works", "Grands travaux"):
        last = history[-1] if history else {}
        works = simulation.works_table()
        cols = st.columns(4)
        cols[0].metric("Ouvrages achevés", f"{sum(1 for w in works if w['fin'] is not None)}")
        cols[1].metric("Chantiers en cours", f"{sum(1 for w in works if w['fin'] is None)}")
        cols[2].metric("Gain de productivité", f"{last.get('ouvrages_gain', 0.0):.2%}")
        cols[3].metric("Dépensé (cumul)", compact(
            sum(r.get("travaux_chantiers", 0.0) + r.get("travaux_entretien", 0.0) for r in history), " €"))
        if works:
            st.dataframe([{
                "Ouvrage": w["nom"], "Coût (€)": w["cout"], "Avancement": w["avancement"],
                "Lancé (période)": w["debut"], "Achevé (période)": w["fin"] if w["fin"] is not None else "en chantier",
                "État": w["etat"], "Entretien financé": w["entretien"],
            } for w in works], hide_index=True, width="stretch",
                column_config=_percent_columns("Avancement", "État", "Entretien financé"))
        else:
            st.caption("Aucun chantier : la dette publique dépasse la limite réglée, ou la doctrine du "
                       "gouvernement est hostile à l'investissement public.")
        _society_series(history, {"État moyen des ouvrages": "ouvrages_etat_moyen"})


def render_society(simulation: EconomySimulation) -> None:
    st.subheader("Société : dettes, jeux, marques, finance et contestation")
    st.markdown(
        '<p class="section-note">Douze mécanismes optionnels (lot V). Chacun s\'active dans la barre '
        "latérale, rubrique « Société : dettes, jeux, marques & finance », puis à la réinitialisation.</p>",
        unsafe_allow_html=True,
    )
    history = simulation.society_history()
    households, firms, finance, state, journal = result_tabs(
        ["Ménages", "Entreprises", "Finance", "État & contestation", "Journal"], "society_section",
    )
    for tab, renderer in ((households, render_society_households), (firms, render_society_firms),
                          (finance, render_society_finance), (state, render_society_state)):
        if tab_is_open(tab):
            with tab:
                renderer(simulation, history)
    if tab_is_open(journal):
        with journal:
            events = simulation.society_events()
            if events:
                st.dataframe([{"Période": e["periode"], "Type": e["type"], "Événement": e["texte"]}
                              for e in events[:300]], hide_index=True, width="stretch")
            else:
                st.info("Aucun événement : activez un mécanisme, réinitialisez, puis avancez la simulation.")


def render_world(simulation: EconomySimulation) -> None:
    world = st.session_state.get("world")
    if world is None:
        st.info("Activez « Monde à plusieurs régions » dans les réglages, puis réinitialisez.")
        return
    remaining = max(0, world.config.integration_month - world.month)
    st.caption(f"{len(world.regions)} régions · période {world.month} · "
               + (f"ouverture des échanges dans {remaining} mois" if remaining else "échanges ouverts")
               + ". Les montants sont exprimés dans la monnaie de chaque région ; les taux de change "
                 "donnent sa valeur en unité commune.")
    rows = world.region_table()
    if not rows:
        st.info("Avancez d'un mois pour comparer les régions.")
        return
    labels = {
        "region": "Région", "population": "Ménages", "pib_reel_par_menage": "PIB réel par ménage",
        "chomage": "Chômage", "salaire_reel": "Salaire réel", "orientation": "Orientation",
        "taux_de_change": "Change", "entreprises": "Entreprises", "importations": "Importations",
        "exportations": "Exportations", "arrivees": "Arrivées", "departs": "Départs",
        "controlees_de_l_etranger": "Filiales étrangères",
        "frontaliers_sortants": "Résidents frontaliers", "frontaliers_entrants": "Salariés non résidents",
        "taux_impot_societes": "Impôt sur les sociétés",
    }
    regions_tab, firms_tab, groups_tab, households_tab = result_tabs(
        ["Régions", "Entreprises du monde", "Groupes & filiales", "Fortunes"], "world_section",
    )
    percent = st.column_config.NumberColumn(format="percent")
    amount = st.column_config.NumberColumn(format="%.0f")
    common = ("Montants en unité commune (monnaie de la région × son taux de change), pour comparer "
              "des régions qui n'ont pas la même monnaie.")
    if tab_is_open(regions_tab):
        with regions_tab:
            totals = world_views.world_totals(world)
            cols = st.columns(6)
            cols[0].metric("Ménages du monde", f"{totals['menages']:,}".replace(",", " "))
            cols[1].metric("Entreprises", f"{totals['entreprises']}",
                           help=f"{totals['salaries']} salariés au total.")
            cols[2].metric("PIB mensuel du monde", compact(totals["pib_nominal"]),
                           help="PIB nominal du dernier mois, somme des régions. " + common)
            cols[3].metric("Capitalisation du monde", compact(totals["capitalisation"]), help=common)
            cols[4].metric("Chômage du monde", f"{totals['chomage']:.1%}",
                           help="Moyenne des régions pondérée par leur nombre de ménages.")
            cols[5].metric("Entreprises contrôlées d'ailleurs", f"{totals['controlees']}",
                           help=f"Par {totals['groupes_multiregionaux']} groupe(s) d'une autre région. "
                                f"{totals['frontaliers']} ménage(s) travaillent hors de leur région.")
            st.caption("Poids de chaque région dans le monde")
            st.dataframe(totals["regions"], hide_index=True, width="stretch",
                         column_config={key: percent for key in totals["regions"][0] if key != "Région"})
            st.dataframe([{label: row[key] for key, label in labels.items()} for row in rows],
                         hide_index=True, width="stretch", column_config={"Chômage": percent})
            indicator = st.selectbox("Comparer les régions", (
                "pib_reel_par_menage", "chomage", "salaire_reel", "taux_de_change", "population",
                "entreprises", "importations", "exportations", "orientation",
                "frontaliers_sortants", "controlees_de_l_etranger",
                "taux_impot_societes",
            ), format_func=lambda key: labels[key], key="world_indicator")
            # le titre de l'axe change avec l'indicateur : sans lui, le navigateur gardait la
            # courbe précédente (même spécification, même nombre de points)
            line_chart([{"Période": h["mois"], **{r["region"]: r[indicator] for r in h["regions"]}}
                        for h in world.history], "Période", title=labels[indicator])
            with st.expander("Compensation et contrôle monétaire"):
                residuals = world.money_residuals()
                st.dataframe([{"Région": world.names[i], "Avoirs de la chambre": r["avoirs_chambre"],
                               "Écart de l'identité monétaire": residuals[i]}
                              for i, r in enumerate(rows)], hide_index=True, width="stretch")
            if world.log:
                with st.expander("Journal des échanges et des décisions"):
                    st.dataframe(list(reversed(world.log[-200:])), hide_index=True, width="stretch")
    if tab_is_open(firms_tab):
        with firms_tab:
            st.markdown("#### Classement mondial des entreprises")
            left, right = st.columns([2, 1])
            sort = left.selectbox("Classer par", list(world_views.RANKING_KEYS), key="world_ranking_sort")
            st.session_state.setdefault("world_ranking_limit", 20)
            limit = right.selectbox("Nombre d'entreprises", (10, 20, 50), key="world_ranking_limit")
            ranking = world_views.company_ranking(world, limit, sort)
            if ranking:
                first = ranking[0]
                st.caption(f"Première entreprise du monde par {sort.lower()} : **{first['Entreprise']}** "
                           f"({first['Région']}, {first['Secteur'].split(' (')[0]}). " + common)
            st.dataframe(ranking, hide_index=True, width="stretch", column_config={
                "Capitalisation": amount, "Chiffre d'affaires": amount, "Bénéfice": amount})
            st.markdown("#### Leaders par secteur")
            st.caption("Première entreprise de chaque secteur par chiffre d'affaires du mois, et part de "
                       "chaque région dans le chiffre d'affaires mondial du secteur.")
            leaders = world_views.sector_leaders(world)
            st.dataframe(leaders, hide_index=True, width="stretch", column_config={
                key: percent for key in (leaders[0] if leaders else {}) if key.startswith("Part")})
    if tab_is_open(groups_tab):
        with groups_tab:
            st.markdown("#### Groupes et filiales par région")
            groups, subsidiaries = world_views.group_rows(world)
            if groups:
                st.caption("Une ligne par tête de groupe : filiales créées ou rachetées dans sa région, et "
                           "entreprises contrôlées dans une autre région. " + common)
                st.dataframe(groups, hide_index=True, width="stretch", column_config={
                    "Chiffre d'affaires du groupe": amount, "Capitalisation de la tête": amount})
                st.caption("Qui possède quoi, et où")
                st.dataframe(subsidiaries, hide_index=True, width="stretch", column_config={
                    "Part détenue": percent, "Chiffre d'affaires": amount})
            else:
                st.caption("Aucun groupe pour l'instant : aucune entreprise n'a de filiale, dans sa région ou "
                           "ailleurs. Les filiales locales demandent les mécanismes de groupes (« Tout "
                           "activer ») ; les prises de contrôle entre régions arrivent après l'ouverture "
                           "des échanges.")
            st.markdown("#### Prises de contrôle entre régions")
            bids = world.foreign_groups()
            if bids:
                st.dataframe(bids, hide_index=True, width="stretch")
            else:
                st.caption("Aucune prise de contrôle ni offre en cours.")
            st.markdown("#### Titres détenus d'une région à l'autre")
            st.caption("Valeur des titres d'entreprises d'une région (lignes) détenus par des entreprises ou "
                       "des ménages qui résident ailleurs (colonnes). " + common)
            matrix = world_views.ownership_matrix(world)
            st.dataframe(matrix, hide_index=True, width="stretch", column_config={
                **{key: amount for key in matrix[0] if key.startswith("Porteurs")},
                "Part de la capitalisation": percent})
    if tab_is_open(households_tab):
        with households_tab:
            st.markdown("#### Plus grandes fortunes du monde")
            st.caption(common + " Les titres gardés dans une autre région après un déménagement sont comptés.")
            st.dataframe(world_views.household_ranking(world), hide_index=True, width="stretch",
                         column_config={key: amount for key in (
                             "Fortune nette", "Trésorerie", "Immobilier", "Actions",
                             "dont titres d'une autre région", "Dette immobilière")})


def render_firms(simulation: EconomySimulation) -> None:
    (firms_list, firm_sheet, groups_tab, funds_tab, market_tab, bonds_tab, trade_tab,
     unions_tab, media_tab, banks_tab, learning_tab) = result_tabs(
        ["Toutes les entreprises", "Fiche d'une entreprise", "Groupes & filiales",
         "Fonds d'investissement", "Bourse", "Obligations", "Commerce extérieur", "Syndicats",
         "Médias", "Relations bancaires", "Apprentissage"],
        "firms_section",
    )
    if tab_is_open(firm_sheet) or tab_is_open(market_tab):
        companies_by_id = {company.id: company for company in simulation.companies}
        company_labels = simulation.company_labels()
        company_options = sorted(
            (company.id for company in simulation.companies if company.active),
            key=lambda cid: (companies_by_id[cid].sector, company_labels[cid]),
        )
    if tab_is_open(firms_list):
        with firms_list:
            st.markdown(
                '<p class="section-note">Entreprises en activité, par secteur. Les entreprises fermées ou '
                "absorbées ne sont plus affichées.</p>",
                unsafe_allow_html=True,
            )
            render_firm_tables(st.session_state._view_id, simulation.month, simulation)

    if tab_is_open(firm_sheet):
        with firm_sheet:
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
                        f"{company_labels[company_id]} · "
                        f"{len(companies_by_id[company_id].employees)} salariés · "
                        f"cours {companies_by_id[company_id].stock_price:,.2f} €"
                    ),
                    key="company_selector",
                )
            selected_company = companies_by_id.get(selected_company_id)
            if selected_company:
                view = (st.session_state._view_id, simulation.month, selected_company_id, simulation)
                render_company_profile(*view)
                company = selected_company
                if company.sector != "bank":
                    with st.expander(f"🛠 Intervenir sur la trésorerie de l'entreprise ({company.cash:,.0f} €)".replace(",", " "),
                                     **({"key": "company_intervention_panel"} if LAZY_EXPANDERS else {})):
                        show_intervention_message()
                        render_cash_intervention("company", company.id, company.cash)
                if selected_company.financial_history:
                    # volet fermé par défaut : les courbes ne sont calculées qu'à l'ouverture
                    # (versions de Streamlit sans volet paresseux : toujours calculées)
                    history_panel = st.expander(
                        "Historiques de l'entreprise (trésorerie, activité, dividendes, effectifs, dette, cours)",
                        **({"key": "company_history_panel", "on_change": "rerun"} if LAZY_EXPANDERS else {}),
                    )
                    if not LAZY_EXPANDERS or history_panel.open:
                        with history_panel:
                            render_company_history(*view)
                render_company_profile_end(*view)
            else:
                st.info("Aucune entreprise active à afficher.")

    if tab_is_open(groups_tab):
        with groups_tab:
            render_groups_panel(st.session_state._view_id, simulation.month, simulation)

    if tab_is_open(funds_tab):
        with funds_tab:
            render_funds_panel(st.session_state._view_id, simulation.month, simulation)

    if tab_is_open(bonds_tab):
        with bonds_tab:
            render_bonds_panel(st.session_state._view_id, simulation.month, simulation)

    if tab_is_open(trade_tab):
        with trade_tab:
            render_trade_panel(st.session_state._view_id, simulation.month, simulation)

    if tab_is_open(unions_tab):
        with unions_tab:
            render_unions_panel(st.session_state._view_id, simulation.month, simulation)

    for tab, renderer in ((media_tab, render_media_panel), (banks_tab, render_bank_relations_panel),
                          (learning_tab, render_learning_panel)):
        if tab_is_open(tab):
            with tab:
                renderer(simulation)

    if tab_is_open(market_tab):
        with market_tab:
            st.markdown("#### Cours boursiers")
            listed_options = [cid for cid in company_options if companies_by_id[cid].listed]
            history_ready = len(simulation.observed_history) > 1
            if listed_options:
                if "stock_chart_companies" in st.session_state:
                    st.session_state["stock_chart_companies"] = [
                        company_id for company_id in st.session_state["stock_chart_companies"]
                        if company_id in listed_options
                    ]
                    stock_defaults = None
                else:
                    stock_defaults = listed_options[: min(6, len(listed_options))]
                stock_selection = st.multiselect(
                    "Entreprises à afficher",
                    listed_options,
                    default=stock_defaults,
                    format_func=lambda company_id: company_labels[company_id],
                    key="stock_chart_companies",
                )
                if history_ready and stock_selection:
                    render_stock_chart(
                        st.session_state._view_id, simulation.month, tuple(stock_selection), simulation
                    )
                elif not history_ready:
                    st.caption("La courbe se dessine après deux mois de simulation.")
                else:
                    st.info("Sélectionnez au moins une entreprise pour afficher son cours.")
            else:
                st.info("Aucune entreprise cotée à afficher.")
            st.caption(
                "Seules les entreprises cotées ont un cours de Bourse ; la légende identifie chaque "
                "entreprise ; les cours réagissent aux bénéfices et aux événements."
            )


@st.cache_data(show_spinner=False, max_entries=8)
def household_fortunes(view_id: str, month: int, _simulation: EconomySimulation) -> dict[str, float]:
    simulation = _simulation
    prices = share_prices(simulation.companies)
    return {
        person.id: household_net_worth(person, simulation.companies, simulation.live, prices)
        for person in simulation.people
    }


def render_households(simulation: EconomySimulation) -> None:
    st.subheader("Ménages : revenus, besoins et logement")
    st.markdown(
        '<p class="section-note">Les dépenses dépendent du revenu disponible ; les ressources les plus faibles sont prioritaires à suivre.</p>',
        unsafe_allow_html=True,
    )
    households_overview, household_sheet, households_list = result_tabs(
        ["Vue d'ensemble", "Fiche d'un ménage", "Tous les ménages"],
        "households_section",
    )
    view = (st.session_state._view_id, simulation.month)
    if tab_is_open(household_sheet) or tab_is_open(households_list):
        fortunes = household_fortunes(*view, simulation)
        people_by_wealth = sorted(simulation.people, key=lambda person: fortunes[person.id], reverse=True)
        company_labels = simulation.company_labels()
    if tab_is_open(households_overview):
        with households_overview:
            population = simulation.population_summary()
            observed_history = simulation.observed_history
            household_metrics = st.columns(4)
            household_metrics[0].metric("Trésorerie moyenne", compact(population["average_cash"], " €"), help=f"{population['average_cash']:,.1f} €")
            household_metrics[1].metric("Nourriture achetée", f"{population['food']:.1f} unités")
            household_metrics[2].metric("Biens achetés", f"{population['goods']:.1f} unités")
            household_metrics[3].metric(
                "Accès au logement", f"{population['home_satisfaction']:.0%}",
                help=(f"{observed_history[-1].unhoused_households} ménage(s) sans logement ; taux de pauvreté "
                      f"{observed_history[-1].poverty_rate:.0%}" if observed_history else None),
            )
            if len(observed_history) > 1:
                values = list(household_fortunes(*view, simulation).values())
                # classes adaptatives (lot A2, novembre 2026) : les six tranches fixes de 0 à
                # 250 k€ regroupaient presque tous les ménages dans une ou deux barres
                distribution = wealth_distribution(values)
                st.subheader("Répartition des fortunes")
                ordered = sorted(values)
                st.caption(
                    f"Classes recalculées chaque mois sur 90 % des ménages (quantiles 5 %–95 %), "
                    f"queues dans les classes ouvertes · médiane {compact(ordered[len(ordered) // 2], ' €')} · "
                    f"de {compact(ordered[0], ' €')} à {compact(ordered[-1], ' €')} · "
                    f"{sum(v < -0.5 for v in values)} ménage(s) en patrimoine négatif"
                )
                # barres horizontales : jusqu'à douze libellés de classe sans chevauchement
                st.vega_lite_chart(
                    [{key: row[key] for key in ("Tranche de fortune", "Nombre de ménages")}
                     for row in distribution],
                    {
                        "mark": {"type": "bar", "tooltip": True},
                        "encoding": {
                            "y": {"field": "Tranche de fortune", "type": "nominal", "sort": None,
                                  "title": None},
                            "x": {"field": "Nombre de ménages", "type": "quantitative"},
                        },
                    },
                    use_container_width=True,
                )
    if tab_is_open(household_sheet):
        with household_sheet:
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
                    f"Fortune {fortunes[person_id]:,.0f} € · "
                    f"{simulation.company_label(people_by_id[person_id].employer_id, company_labels)}"
                ),
                key="household_selector",
            )
            st.caption("La liste est triée par fortune nette ; choisissez un ménage pour consulter son historique.")
            selected_person = (
                simulation.household_profile(selected_person_id) if selected_person_id else None
            )
            if selected_person:
                person = selected_person["person"]
                st.subheader(person.name)
                st.caption(
                    f"Employeur : {simulation.company_label(person.employer_id, company_labels)} · "
                    f"Rôle : {person.employment_role} · "
                    f"Logement : {'propriétaire' if person.owns_home else 'locataire' if person.rental_owner_id else 'aucun'} · "
                    f"Compétence : {person.skill:.2f} · ancienneté : {person.employer_tenure} mois"
                )
                st.caption(
                    f"Âge du chef de ménage : {person.age_months // 12} ans · génération {person.generation}"
                    + (f" · patrimoine hérité : {person.inherited_wealth:,.0f} € (droits déduits)"
                       if person.generation > 1 else "")
                    + (f" · mois sans emploi : {person.unemployment_months}" if person.employer_id is None else "")
                )
                institutions = getattr(simulation, "institutions", None)
                if institutions is not None:
                    office = next((OFFICE_ROLES[role] for role, o in institutions.offices.items() if o.person_id == person.id), None)
                    agent = institutions.agents.get(person.id)
                    st.caption(
                        f"Nom d'affichage : {display_name(person.id)} · métier : {person.trade} · proximité politique "
                        f"{institutions.lean.get(person.id, 50.0):.0f} / 100 · "
                        + (f"fonction politique : {office}" if office
                           else f"fonction publique : {agent.role()}, depuis {simulation.month - agent.since} mois" if agent
                           else "ni cabinet ni fonction publique")
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
                with st.expander("🛠 Intervenir sur ce ménage : trésorerie et compétence",
                                 **({"key": "household_intervention_panel"} if LAZY_EXPANDERS else {})):
                    show_intervention_message()
                    st.markdown("**Trésorerie**")
                    render_cash_intervention("household", person.id, person.cash)
                    st.markdown("**Compétence**")
                    skill_row = st.columns([3, 1])
                    skill_key = f"household_skill_{person.id}"
                    skill_row[0].slider(
                        "Compétence", float(SKILL_BOUNDS[0]), float(SKILL_BOUNDS[1]),
                        value=float(min(max(person.skill, SKILL_BOUNDS[0]), SKILL_BOUNDS[1])), step=0.05,
                        key=skill_key, label_visibility="collapsed",
                        help="Productivité et salaire du ménage (1 = moyenne de départ ; 1,10 : spécialiste, "
                             "1,35 : encadrement). Changement définitif : la compétence évolue ensuite "
                             "normalement (apprentissage au travail, érosion au chômage).",
                    )
                    skill_row[1].button("Appliquer", key="household_skill_apply", use_container_width=True,
                                        on_click=_skill_intervention, args=(person.id, skill_key))
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
                if person.fund_units or person.fund_value:
                    st.markdown("**Épargne confiée aux fonds des banques**")
                    for fund_id, units in sorted(person.fund_units.items()):
                        st.markdown(f"- **{simulation.fund_label(fund_id)}** · {units:,.1f} parts".replace(",", " "))
                    st.caption(
                        f"Valeur des parts : {person.fund_value:,.0f} € "
                        f"({person.fund_value / max(float(selected_person['wealth']), 1.0):.1%} de la fortune nette)."
                    )
                if simulation.live.experience_learning or simulation.live.property_upkeep:
                    homes = int(person.owns_home) + person.investment_homes
                    st.caption(
                        f"Expérience : {person.ventures} entreprise(s) fondée(s) · {person.negotiations} "
                        f"embauche(s) négociée(s) · salaire négocié à {person.negotiated_factor:.1%} de l'offre"
                        + (f" · état de ses {homes} logement(s) : {person.home_condition:.0%}"
                           if simulation.live.property_upkeep and homes else "")
                    )
                person_history = observed_rows(person.financial_history, simulation)
                if person_history:
                    st.caption("Fortune nette au fil du temps")
                    line_chart(
                        person_history,
                        x="Période",
                        y=["Fortune", "Fortune réelle"],
                    )
                    st.caption("Liquidités et valeur des actions")
                    line_chart(
                        person_history,
                        x="Période",
                        y=["Trésorerie nette", "Actions"],
                    )
                    st.caption("Revenus et dépenses mensuels")
                    line_chart(
                        person_history,
                        x="Période",
                        y=["Revenus", "Dividendes", "Dépenses"],
                    )
    if tab_is_open(households_list):
        with households_list:
            st.caption(
                "Un ménage par ligne, trié par fortune nette ; cliquez sur un en-tête de colonne pour "
                "trier autrement."
            )
            list_prices = share_prices(simulation.companies)
            st.dataframe(
                [
                    {
                        "Ménage": p.name,
                        "Âge": p.age_months // 12,
                        "Employeur": simulation.company_label(p.employer_id, company_labels) if p.employer_id else "sans emploi",
                        "Métier": p.trade,
                        "Logement": "propriétaire" if p.owns_home else "locataire" if p.rental_owner_id else "aucun",
                        "Revenu du mois (€)": p.income_this_turn,
                        "Trésorerie (€)": p.cash,
                        "Actions (€)": sum(q * list_prices.get(cid, 0.0) for cid, q in p.shares.items()),
                        "Parts de fonds (€)": p.fund_value,
                        "Logements loués": p.investment_homes,
                        "Fortune nette (€)": fortunes[p.id],
                    }
                    for p in people_by_wealth
                ],
                hide_index=True, width="stretch", height=560,
                column_config={
                    name: st.column_config.NumberColumn(format="localized")
                    for name in ("Revenu du mois (€)", "Trésorerie (€)", "Actions (€)", "Parts de fonds (€)",
                                 "Fortune nette (€)")
                },
            )


def render_model(simulation: EconomySimulation) -> None:
    st.subheader("Mécanismes qui font émerger la concentration")
    improvements = [
        ("Agents et patrimoines", "Ménages et entreprises ont des profils, des historiques et des patrimoines nets. Les palmarès, les Gini du patrimoine et du revenu disponible, les parts du décile et du 1 % supérieurs, la pauvreté et les ménages sans logement suivent leur évolution."),
        ("Logement", "Un ménage sans logement dépose une demande de location ; chaque promoteur compte les demandes qu'il n'a pas pu servir dans sa prévision et construit sur du foncier qui s'étend avec la ville. Pénurie et loyers se lisent sur tout le parc locatif. Un ménage n'achète un logement à louer que si son rendement locatif dépasse celui de son épargne, et revend un logement resté vide."),
        ("Prix et anticipations", "Les entreprises prévoient leur demande et ajustent leurs prix selon leurs coûts, stocks, ventes et concurrents. Les anticipations utilisent aussi la crédibilité apprise de la banque centrale."),
        ("Travail et salaires", "Les entreprises proposent des salaires selon leurs signaux locaux et limitent leurs effectifs à un travail jugé rentable. La référence salariale suit les offres du marché ; le minimum légal borne la rémunération due. Une entreprise sans poste vacant modère son offre quand le chômage de longue durée (12 mois et plus) est répandu ; une entreprise dont les recettes ne couvrent pas la paie baisse aussi ses salaires au lieu de seulement licencier, et ne compte pas comme vacants les postes qu'elle vient de supprimer."),
        ("Démographie et successions", "Chaque chef de ménage vieillit ; la mortalité croît avec l'âge (espérance de vie d'environ 74 ans). À son décès, un héritier lui succède : il garde patrimoine, dettes, logement et emploi du ménage, mais repart de sa propre compétence et de ses propres traits, sans l'expérience du défunt. L'État prélève des droits de succession au-delà d'un abattement, à un taux fixé par sa doctrine (nul à l'extrême droite, doublé à l'extrême gauche) ; un patrimoine illiquide paie le reste au fil des mois."),
        ("Éducation publique", "Un service de l'Éducation scolarise la génération suivante des ménages. Sa couverture, lissée sur une quinzaine d'années, relève la compétence de départ des héritiers et des nouveaux ménages : un effet modeste, une fois par génération."),
        ("Élections", "Chaque ménage vote : sa proximité politique, déplacée par ses intérêts selon son rang de fortune, l'évolution de son revenu réel pendant le mandat et ses propres mois sans emploi décident de son soutien au sortant. L'opposition défend la position médiane de son camp ; le résultat figure au journal de l'État."),
        ("Investissement", "Les entreprises évaluent les ventes supplémentaires et les salaires économisés, puis financent les projets par trésorerie et crédit. Les logements sont livrés après un délai et payés au fil du chantier."),
        ("Création et disparition", "Les ménages éligibles peuvent fonder une entreprise, qui porte leur nom. Pendant douze mois, ses titres offerts servent la demande avant les vendeurs. Le fondateur revend ensuite ses titres sur le marché, au cours, pour se diversifier ou reconstituer sa trésorerie. Insolvabilité et cessation de paiements peuvent fermer même le dernier producteur d'un secteur ; il n'existe plus de faillite aléatoire des jeunes entreprises. Une entreprise sans salarié réembauche quand un poste rapporte ce qu'il coûte dans son secteur (toujours si le secteur n'a plus aucun employeur) ; sans perspective pendant deux ans, sans dette ni actif physique, ses actionnaires la liquident à l'amiable et récupèrent sa trésorerie."),
        ("Banques et monnaie", "Un prêt crée un dépôt ; son remboursement détruit de la monnaie. Les banques évaluent le risque, la liquidité et leurs fonds propres. L'aide publique à leur résolution est plafonnée. Le taux directeur suit une règle de Taylor, sauf s'il est forcé en intervention ; une marge supplémentaire réglable s'ajoute à tous les crédits."),
        ("État institutionnel", "Présidence et ministres ont des doctrines et objectifs. Une loi de finances organise impôts et dépenses sans ratio de dette visé. L'impôt sur le revenu a un barème progressif indexé (taux de base, puis surtaxe) et inclut dividendes et loyers : au barème à gauche, au forfait réduit à droite. Les transferts sont d'autant plus ciblés sur les bas revenus que le gouvernement est à gauche ; le minimum légal peut atteindre 80 % du médian à l'extrême gauche. Aucun Gini n'est visé. Services publics, minimum légal et politique industrielle figurent dans l'onglet État."),
        ("Bourse et concurrence", "Les ordres d'achat et de vente font évoluer les cours avec les estimations fondamentales. Les rachats reposent sur une offre de valeur et l'acceptation des actionnaires. Les dividendes transfèrent la trésorerie aux détenteurs des titres."),
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
        "Les achats de services et de matériaux ne rémunèrent que des fournisseurs employeurs. "
        "Sans fournisseur capable de servir la commande, l'acheteur conserve son argent ; "
        "aucun fonds amont orphelin n'est alimenté."
    )
    st.markdown(
        "**État, salaires et monnaie**  \n"
        "Le budget suit la loi votée, les recettes, les engagements et les possibilités de "
        "financement. Les besoins non financés restent visibles. Les entreprises arbitrent "
        "entre investissements, remboursement, placements et distributions. Le PIB récurrent "
        "exclut la construction, comptée séparément à sa valeur ajoutée au fil des chantiers."
    )
    # lot B : valeurs de base des taux (config) et valeurs du mois (live, forçages compris)
    st.subheader("Taux d'intérêt : valeurs de base et en vigueur")
    base, live = simulation.config, simulation.live
    operator = getattr(simulation, "interventions", None)
    forced_rate = getattr(operator, "forced_policy_rate", None) if operator else None
    monetary = getattr(operator, "monetary", {}) if operator else {}
    rate_rows = [
        ("Taux directeur", "règle de Taylor", f"{12.0 * simulation.policy_rate:.2%}"
         + (" (forcé)" if forced_rate is not None else "")),
        ("Taux réel neutre (Taylor)", f"{base.taylor_neutral_real_rate:.2%}", f"{live.taylor_neutral_real_rate:.2%}"),
        ("Inflation visée (annuelle)", f"{(1 + base.inflation_target) ** 12 - 1:.2%}",
         f"{(1 + live.inflation_target) ** 12 - 1:.2%}"),
        ("Plafond du taux directeur", f"{base.policy_rate_cap:.2%}", f"{live.policy_rate_cap:.2%}"),
        ("Lissage mensuel du taux directeur", f"{base.policy_rate_smoothing:.2f}", f"{live.policy_rate_smoothing:.2f}"),
        ("Écart du crédit immobilier", f"{12 * base.mortgage_spread:.2%}", f"{12 * live.mortgage_spread:.2%}"),
        ("Écart du crédit à la consommation", f"{12 * base.consumer_credit_spread:.2%}",
         f"{12 * live.consumer_credit_spread:.2%}"),
        ("Écart des dépôts sous le directeur", f"{12 * base.deposit_spread:.2%}", f"{12 * live.deposit_spread:.2%}"
         + (" (forcé)" if "deposit_spread" in monetary else "")),
        ("Marge supplémentaire des banques", f"{12 * base.bank_margin_offset:.2%}",
         f"{12 * live.bank_margin_offset:.2%}" + (" (forcée)" if "bank_margin_offset" in monetary else "")),
        ("Taux servi sur les dépôts", "—", f"{12.0 * simulation.deposit_rate:.2%}"),
    ]
    st.dataframe(
        [{"Taux (annuel)": name, "Base (réglages)": value, "En vigueur ce mois": current}
         for name, value, current in rate_rows],
        hide_index=True, use_container_width=True,
    )
    st.caption("Base : valeurs de la barre latérale, appliquées à la réinitialisation. En vigueur : "
               "valeurs du mois, forçages du mode intervention compris (onglet État).")
    st.divider()
    st.subheader("Hypothèses et limites")
    st.markdown(
        """
        - Les montants sont des euros nominaux simulés et les périodes des mois. Un ménage représente un consommateur.
        - Les décisions sont des règles heuristiques ; une identité comptable ne prouve ni le réalisme économique ni la stabilité.
        - Le logement est homogène. La capacité physique des fournisseurs n'est pas modélisée uniformément dans tous les secteurs.
        - Les comptes utilisent des flottants. Les prototypes de dépôts bancaires en centimes ne sont pas intégrés au moteur courant.
        - Limite connue : l'État peut conserver des actions d'une banque fermée après résolution. Ce défaut est documenté et n'a pas été corrigé ; son apparition dépend de la trajectoire.
        - Un ménage est une lignée : l'héritier succède sous le même identifiant et garde l'emploi du ménage. Il n'y a ni retraite, ni enfants mineurs, ni partage d'une succession entre plusieurs héritiers.
        - Le calendrier historique de clôture laisse certaines recettes tardives hors du bénéfice mensuel ; il reste inchangé.
        - Les ratios boursiers annualisent le dernier mois. Le Gini borne à zéro les patrimoines nets négatifs.
        - Le recyclage ralentit l'épuisement des matières sans garantir l'absence de pénuries. Le commerce extérieur n'est pas simulé.
        - Aucun résultat de croissance, d'emploi ou de répartition des richesses n'est garanti.
        """
    )
    st.subheader("Ressources disponibles")
    resource_columns = st.columns(3)
    for index, (name, value) in enumerate(simulation.resource_summary().items()):
        resource_columns[index % len(resource_columns)].metric(name, f"{value:,.1f}")


@st.fragment
def render_dashboard() -> None:
    # Chaque nouveau rendu peut consulter des vues qui mémorisent des mesures. La préparation
    # en cours continue, mais elle n'est réutilisable qu'une fois confirmée après ce rendu
    # (``prepare`` compare l'état du moteur à celui dont elle est partie).
    cache = st.session_state.get("_advance_cache")
    if cache is None:
        cache = st.session_state._advance_cache = AdvanceCache()
    cache.hold()
    st.session_state._advance_revision = uuid4().hex
    world = st.session_state.get("world")
    if world is not None:
        st.selectbox("Région observée", range(len(world.regions)),
                     format_func=lambda i: world.names[i], key="active_region", on_change=select_region)
        st.caption("Les commandes d'avance font progresser toutes les régions. "
                   "Les fiches et interventions ci-dessous concernent la région sélectionnée.")
    simulation: EconomySimulation = st.session_state.simulation
    if st.session_state.get("_view_simulation") is not simulation:
        st.session_state._view_simulation = simulation
        st.session_state._view_id = uuid4().hex

    header, controls = st.columns([1.5, 1], vertical_alignment="center")
    with header:
        st.markdown('<div class="eyebrow">Simulation multi-agents · tableau de bord</div>', unsafe_allow_html=True)
        st.title("Économie en mouvement")
        st.caption("Suivez les décisions des entreprises, les arbitrages des ménages et l’évolution des marchés.")
    with controls:
        st.markdown(f'<span class="month-pill">Période {simulation.period}</span>', unsafe_allow_html=True)
        st.write("")
        one_month, run_length = st.columns([1, 1.1])
        with one_month:
            st.button("▶  Un mois", type="primary", use_container_width=True,
                      on_click=advance_simulation, args=(1,))
        with run_length:
            st.number_input("Période", min_value=1, max_value=120, value=12, step=1,
                            label_visibility="collapsed", key="run_months")
            st.button("Avancer", use_container_width=True, on_click=advance_simulation)
        st.checkbox(
            "Préparer l’avance pendant la lecture", value=True, key="prepare_advance",
            help="Prépare l’avance choisie pendant que vous consultez les résultats : au clic, "
                 "les mois déjà prêts sont repris et seuls les mois restants sont calculés. "
                 "Utilise davantage de processeur et de mémoire. Une intervention annule cette "
                 "préparation ; un changement d’onglet la laisse continuer.",
        )

    if simulation.period:
        st.info(f"**À retenir — période {simulation.period} :** {simulation.last_event}")
    elif simulation.warmup_offset:
        st.info(
            f"**Démarrage :** l'économie a déjà tourné {simulation.warmup_offset} mois de chauffe "
            "avec les mêmes règles, pour construire un état initial cohérent (emplois, capital, "
            "bilans, logements). L'observation commence maintenant."
        )
    else:
        st.info("**Démarrage :** avancez d’un mois pour observer les marchés s’ajuster aux décisions des agents.")

    employed = sum(person.employer_id is not None for person in simulation.people)
    employment_rate = employed / max(1, len(simulation.people))
    latest = simulation.history[-1] if simulation.history else None
    metrics = [*st.columns(4), *st.columns(4)]
    metrics[0].metric(
        "Ménages employés",
        f"{employed} / {len(simulation.people)}" if latest else "—",
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
        "Emplois publics",
        f"{latest.public_employment}" if latest else "—",
        help="Agents employés par l'État (fonction publique), payés sur le budget public.",
    )
    banks = [
        company
        for company in simulation.companies
        if company.sector == "bank" and company.active
    ]
    bank_equity = sum(bank.bank_equity for bank in banks)
    credit_metrics[5].metric(
        "Banques actives",
        f"{len(banks)}",
        f"{compact(bank_equity, ' €')} de fonds propres",
        help=(
            f"Fonds propres : {bank_equity:,.0f} €. "
            f"Prêts en cours : {latest.bank_loan_portfolio:,.0f} €. "
            if latest
            else "Les banques sont des entreprises cotées."
        ),
    )

    # Streamlit nettoie les widgets masqués. Conserver le dernier panneau et ses filtres.
    for key in (
        "overview_section", "state_section", "power_section", "firms_section", "households_section",
        "public_service_filter", "state_journal_author", "company_selector", "household_selector",
        "stock_chart_companies", "world_indicator", "world_section", "world_ranking_sort",
        "world_ranking_limit", "society_section",
    ):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]

    tabs = result_tabs(
        ["Vue d’ensemble", "Monde", "État", "Entreprises & bourse", "Ménages", "Société",
         "Modèle & réglages"],
        "results_tab",
    )
    for tab, renderer in zip(tabs, (
        render_overview, render_world, render_institutional_state, render_firms, render_households,
        render_society, render_model,
    )):
        if tab_is_open(tab):
            with tab:
                renderer(simulation)

    if st.session_state.prepare_advance:
        cache.prepare(world or simulation, st.session_state._advance_revision,
                      int(st.session_state.run_months))
    else:
        cache.invalidate()


render_sidebar()

if "simulation" not in st.session_state:
    with st.spinner("Période de chauffe : construction de l'état initial…"):
        st.session_state.simulation = new_simulation()
render_dashboard()
