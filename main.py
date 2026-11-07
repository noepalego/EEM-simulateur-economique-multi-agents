from __future__ import annotations

import streamlit as st
from altair.utils.schemapi import disable_debug_mode
from inspect import signature
from uuid import uuid4

from economy.models import SimulationConfig
from economy.simulation import EconomySimulation, SECTORS
from economy.warmup_cache import warmed_simulation
from economy.institutions import (
    FLOW_LABELS,
    INDICATOR_LABELS,
    OFFICE_LABELS,
    OFFICE_ROLES,
    OFFICES,
    PROGRAM_LABELS,
    RESPONSE_LABELS,
    SERVICE_LABELS,
    SERVICE_TRADES,
    display_name,
    doctrine_summary,
)
from economy.interventions import (
    LEVERS,
    SKILL_BOUNDS,
    adjust_company_cash,
    adjust_household_cash,
    cancel_orientation,
    clear_lever,
    request_orientation,
    set_household_skill,
    set_lever,
)
from economy.politics import orientation_label
from economy.wealth import (
    company_balance_sheet,
    company_valuation_metrics,
    household_net_worth,
    share_prices,
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


def _vega_chart(mark: str, data, x: str, y=None) -> None:
    """Graphique Vega-Lite direct (optimisation du 6 novembre 2026). ``st.line_chart`` construisait
    un graphique Altair puis le validait contre le schéma Vega-Lite à chaque rendu (environ 1,5 s
    sur six affichages au profil) ; ici, la spécification est écrite directement, les données
    passées au format long (une ligne par période et par série), sans validation. Mêmes
    arguments que les appels existants : ``x`` (colonne des abscisses) et ``y`` (une colonne, une
    liste, ou toutes les autres colonnes)."""
    rows = list(data)
    if y is None:
        names = [key for key in dict.fromkeys(k for row in rows for k in row) if key != x]
    else:
        names = [y] if isinstance(y, str) else list(y)
    long = [
        {"x": row.get(x), "Série": name, "Valeur": row.get(name)}
        for row in rows for name in names
    ]
    nominal = mark == "bar"
    encoding = {
        "x": {"field": "x", "type": "nominal" if nominal else "quantitative", "title": x,
              **({"sort": None, "axis": {"labelAngle": 0}} if nominal else {})},
        "y": {"field": "Valeur", "type": "quantitative",
              "title": names[0] if len(names) == 1 else None,
              **({"stack": "zero"} if mark == "area" else {"stack": None} if mark == "line" else {})},
        "tooltip": [{"field": "x", "title": x}, {"field": "Série"}, {"field": "Valeur", "format": ",.4~f"}],
    }
    if len(names) > 1:
        encoding["color"] = {"field": "Série", "type": "nominal", "sort": names, "title": None,
                             "legend": {"orient": "bottom"}}
    st.vega_lite_chart(long, {"mark": {"type": mark, "tooltip": True}, "encoding": encoding},
                       use_container_width=True)


def line_chart(data, x: str, y=None) -> None:
    _vega_chart("line", data, x, y)


def area_chart(data, x: str, y=None) -> None:
    _vega_chart("area", data, x, y)


def bar_chart(data, x: str, y=None) -> None:
    _vega_chart("bar", data, x, y)


def new_simulation() -> EconomySimulation:
    """Simulation après la chauffe pour les réglages de la barre latérale. L'état après chauffe est
    mémorisé (``economy.warmup_cache`` : mêmes réglages et même code, même état, bit pour bit) :
    une réinitialisation déjà calculée ne refait pas les 120 mois de chauffe."""
    return warmed_simulation(
        SimulationConfig(
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
        )
    )


def advance_simulation(months: int | None = None) -> None:
    """Avance avant le rendu : une seule exécution affiche le nouvel état."""
    count = int(st.session_state.run_months) if months is None else months
    for _ in range(count):
        st.session_state.simulation.step()


# -- mode intervention (economy/interventions.py) ------------------------------------------
# Les rappels s'exécutent avant le rendu ; un nouvel identifiant de vue invalide les blocs mis
# en cache par (vue, mois), qui affichent ainsi l'état modifié sans attendre le mois suivant.
def _after_intervention(message: str) -> None:
    st.session_state._intervention_message = message
    st.session_state._view_id = uuid4().hex


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


def show_intervention_message() -> None:
    message = st.session_state.pop("_intervention_message", None)
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
                     **({"key": "state_intervention_panel"} if lazy_expanders else {})):
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


def render_institutional_state(simulation: EconomySimulation, observed_history: list) -> None:
    """Onglet « État » (État institutionnel) : gouvernement, budget,
    fonction publique, salaire minimum, industrie et journal des décisions.

    Les blocs sans widget sont mémorisés par simulation et mois (``_view_id`` + mois interne,
    comme la vue d'ensemble) ; les deux sélecteurs (service, auteur) restent hors cache et
    entrent dans la clé des blocs qu'ils filtrent (A4, novembre 2026 : 0,29 à 0,37 s par
    interaction avant)."""
    inst = getattr(simulation, "institutions", None)
    if inst is None:
        st.info("Le nouvel État est créé au premier mois simulé.")
        return
    view = (st.session_state._view_id, simulation.month)
    render_state_head(*view, simulation)
    render_state_interventions(simulation)
    sections = st.tabs(["Gouvernement", "Budget", "Fonction publique", "Salaire minimum", "Industrie", "Journal"])
    with sections[0]:
        render_state_government(*view, simulation)
    with sections[1]:
        render_state_budget(*view, simulation)
    with sections[2]:
        render_state_services(*view, simulation)
        choice = st.selectbox("Service", ["Tous", *SERVICE_LABELS], key="public_service_filter",
                              format_func=lambda key: key if key == "Tous" else SERVICE_LABELS[key])
        render_state_agents(*view, choice, simulation)
    with sections[3]:
        render_state_minimum_wage(*view, simulation)
    with sections[4]:
        render_state_industry(*view, simulation)
    with sections[5]:
        authors = ["Tous", *dict.fromkeys(d.author for d in inst.journal)]
        author = st.selectbox("Auteur", authors, key="state_journal_author")
        render_state_journal(*view, author, simulation)


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
        st.markdown("**Présidence et ministres**")
        st.dataframe(rows, hide_index=True,
                     column_config={"Progression": st.column_config.NumberColumn(format="percent")})
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
                    "Bilan": ", ".join(f"{OFFICE_LABELS[r]} : {s}" for r, s in term.record.items()),
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
                    "Entreprise": companies[cid].name if cid in companies else cid, "Origine": h.origin,
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


@st.cache_data(show_spinner=False, max_entries=4)
def render_overview(view_id: str, month: int, _simulation: EconomySimulation) -> None:
    """Mémorise le rendu sans widgets, par simulation et mois, sans hacher le moteur.

    L'identifiant est renouvelé à chaque réinitialisation et propre à la session.
    Le cache borné ne contient que les messages d'affichage, jamais le moteur.
    """
    simulation = _simulation
    observed_history = simulation.observed_history
    latest = observed_history[-1] if observed_history else None
    population = simulation.population_summary()
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
                    f"**{row['Entreprise']}** · **{row['Fortune nette']:,.0f} €**  \n"
                    f"{dict(SECTORS)[row['Secteur']]} · Trésorerie {row['Trésorerie']:,.0f} € · "
                    f"Dette {row['Dettes']:,.0f} € · Bénéfice {row['Profit']:,.0f} €"
                )

    if len(observed_history) > 1:
        st.subheader("Évolution de l'emploi par secteur")
        sector_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_employment.get(sector, 0)
                    for sector, label in SECTORS
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
                    for sector, label in SECTORS
                },
            }
            for record in observed_history
        ]
        profit_chart_data = [
            {
                "Période": record.month,
                **{
                    label: record.sector_profit.get(sector, 0.0)
                    for sector, label in SECTORS
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
                    for sector, label in SECTORS
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
    for sector, label in SECTORS:
        if sector == "public":
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
        for row in sorted(firm_rows, key=lambda r: (r["Statut"] != "Active", r["Secteur"], r["Entreprise"]))
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
        st.divider()
        st.subheader(f"Profil détaillé · {selected_company.name}")
        founder_label, founder_share = simulation.founder_stake(selected_company)
        st.caption(
            f"{dict(SECTORS)[selected_company.sector]} · "
            f"{'Active' if selected_company.active else 'Absorbée / fermée'} · "
            f"PDG : {simulation.person_name(selected_company.executive_id)}"
            + (
                f" · Fondateur : {founder_label}"
                + (f", {founder_share:.1%} du capital émis" if founder_share is not None else " (sorti)")
                + f" · titres offerts non souscrits : {selected_company.treasury_shares:,.0f}"
                if selected_company.founder_id is not None else " · Entreprise initiale"
            )
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
                            f"{simulation.company_name(partner_id)} · "
                            f"{transaction_label} {amount:,.0f} €"
                        )


@st.cache_data(show_spinner=False, max_entries=16)
def render_company_history(
    view_id: str, month: int, selected_company_id: str, _simulation: EconomySimulation
) -> None:
    """Historiques d'une entreprise, mémorisés par simulation, mois et entreprise ; affichés
    seulement quand leur volet est ouvert. Les mêmes séries qu'auparavant, regroupées en quatre
    graphiques au lieu de huit (A4, novembre 2026 : la génération Altair coûtait 0,36 s au premier
    dépliage) : patrimoine et dette, flux du mois, activité et effectifs, cours."""
    selected_company = _simulation.company_profile(selected_company_id)
    firm_history = selected_company.financial_history
    history_first, history_second = st.columns(2)
    with history_first:
        st.caption("Trésorerie, valeur nette et dette (€)")
        line_chart(firm_history, x="Période", y=["Trésorerie", "Fortune", "Dettes"])
    with history_second:
        st.caption("Flux du mois : chiffre d'affaires, bénéfice, dividendes, flux de trésorerie disponible (€)")
        line_chart(
            firm_history,
            x="Période",
            y=["Chiffre d'affaires", "Bénéfice", "Dividendes", "Flux de trésorerie disponible"],
        )
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
    if selected_company:
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


@st.cache_data(show_spinner=False, max_entries=8)
def render_stock_chart(
    view_id: str, month: int, stock_selection: tuple[str, ...], _simulation: EconomySimulation
) -> None:
    """Courbe des cours des entreprises choisies, mémorisée par simulation, mois et sélection."""
    simulation = _simulation
    observed_history = simulation.observed_history
    companies_by_id = {company.id: company for company in simulation.companies}
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
        for record in observed_history
    ]
    line_chart(stock_history, x="Période")


render_sidebar()

if "simulation" not in st.session_state:
    with st.spinner("Période de chauffe : construction de l'état initial…"):
        st.session_state.simulation = new_simulation()
simulation: EconomySimulation = st.session_state.simulation
if st.session_state.get("_view_simulation") is not simulation:
    st.session_state._view_simulation = simulation
    st.session_state._view_id = uuid4().hex
# mois affichés : depuis la période 0, sans la période de chauffe
observed_history = simulation.observed_history

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

population = simulation.population_summary()
employment_rate = population["employed"] / max(1, len(simulation.people))
latest = observed_history[-1] if observed_history else None
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
    "Emplois publics",
    f"{latest.public_employment}" if latest else "—",
    help="Agents employés par l'État (fonction publique), payés sur le budget public.",
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

# Les sélections survivent au nettoyage des widgets des onglets non rendus.
for selection_key in ("company_selector", "household_selector", "stock_chart_companies"):
    if selection_key in st.session_state:
        st.session_state[selection_key] = st.session_state[selection_key]

# Les conteneurs dynamiques existent depuis Streamlit 1.55 ; conserver le
# rendu complet sur les versions antérieures autorisées par requirements.txt.
lazy_tabs = "on_change" in signature(st.tabs).parameters
lazy_expanders = "on_change" in signature(st.expander).parameters
overview, state_tab, firms_tab, households_tab, model_tab = st.tabs(
    ["Vue d’ensemble", "État", "Entreprises & bourse", "Ménages", "Modèle & réglages"],
    **({"on_change": "rerun", "key": "results_tab"} if lazy_tabs else {}),
)

if not lazy_tabs or overview.open:
    with overview:
        render_overview(st.session_state._view_id, simulation.month, simulation)

if not lazy_tabs or state_tab.open:
    with state_tab:
        render_institutional_state(simulation, observed_history)

if not lazy_tabs or firms_tab.open:
    with firms_tab:
        st.subheader("Performance des entreprises")
        st.markdown(
            '<p class="section-note">Chaque fiche résume une entreprise. Choisissez-en une pour ouvrir son historique.</p>',
            unsafe_allow_html=True,
        )
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

        render_firm_tables(st.session_state._view_id, simulation.month, simulation)

        selected_company = simulation.company_profile(selected_company_id) if selected_company_id else None
        if selected_company:
            view = (st.session_state._view_id, simulation.month, selected_company_id, simulation)
            render_company_profile(*view)
            company = companies_by_id[selected_company_id]
            if company.sector != "bank":
                with st.expander(f"🛠 Intervenir sur la trésorerie de l'entreprise ({company.cash:,.0f} €)".replace(",", " "),
                                 **({"key": "company_intervention_panel"} if lazy_expanders else {})):
                    show_intervention_message()
                    render_cash_intervention("company", company.id, company.cash)
            if selected_company.financial_history:
                # volet fermé par défaut : les huit courbes ne sont calculées qu'à l'ouverture
                # (versions de Streamlit sans volet paresseux : toujours calculées)
                history_panel = st.expander(
                    "Historiques de l'entreprise (trésorerie, activité, effectifs, dette, cours)",
                    **({"key": "company_history_panel", "on_change": "rerun"} if lazy_expanders else {}),
                )
                if not lazy_expanders or history_panel.open:
                    with history_panel:
                        render_company_history(*view)
            render_company_profile_end(*view)


        st.subheader("Cours boursiers")
        if company_options:
            if "stock_chart_companies" in st.session_state:
                st.session_state["stock_chart_companies"] = [
                    company_id for company_id in st.session_state["stock_chart_companies"]
                    if company_id in company_options
                ]
                stock_defaults = None
            else:
                stock_defaults = company_options[: min(6, len(company_options))]
            stock_selection = st.multiselect(
                "Entreprises à afficher",
                company_options,
                default=stock_defaults,
                format_func=lambda company_id: companies_by_id[company_id].name,
                key="stock_chart_companies",
            )
            if len(observed_history) > 1 and stock_selection:
                render_stock_chart(
                    st.session_state._view_id, simulation.month, tuple(stock_selection), simulation
                )
            elif len(observed_history) <= 1:
                st.caption("La courbe se dessine après deux mois de simulation.")
            else:
                st.info("Sélectionnez au moins une entreprise pour afficher son cours.")
        else:
            st.info("Aucune entreprise active à afficher.")
        st.caption("La légende identifie chaque entreprise ; les cours réagissent aux bénéfices et aux événements.")

if not lazy_tabs or households_tab.open:
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
        household_metrics[3].metric(
            "Accès au logement", f"{population['home_satisfaction']:.0%}",
            help=(f"{observed_history[-1].unhoused_households} ménage(s) sans logement ; taux de pauvreté "
                  f"{observed_history[-1].poverty_rate:.0%}" if observed_history else None),
        )
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
                             **({"key": "household_intervention_panel"} if lazy_expanders else {})):
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
            if person.financial_history:
                st.caption("Fortune nette au fil du temps")
                line_chart(
                    person.financial_history,
                    x="Période",
                    y=["Fortune", "Fortune réelle"],
                )
                st.caption("Liquidités et valeur des actions")
                line_chart(
                    person.financial_history,
                    x="Période",
                    y=["Trésorerie nette", "Actions"],
                )
                st.caption("Revenus et dépenses mensuels")
                line_chart(
                    person.financial_history,
                    x="Période",
                    y=["Revenus", "Dividendes", "Dépenses"],
                )
        if len(observed_history) > 1:
            wealth_bands = [
                ("0–10 k€", 0.0, 10000.0),
                ("10–25 k€", 10000.0, 25000.0),
                ("25–50 k€", 25000.0, 50000.0),
                ("50–100 k€", 50000.0, 100000.0),
                ("100–250 k€", 100000.0, 250000.0),
                ("250 k€ et +", 250000.0, float("inf")),
            ]
            # fortunes calculées une fois (auparavant une fois par tranche et par ménage)
            prices = share_prices(simulation.companies)
            fortunes = [household_net_worth(p, simulation.companies, simulation.live, prices)
                        for p in simulation.people]
            distribution = [
                {
                    "Tranche de fortune": label,
                    "Nombre de ménages": sum(lower <= value < upper for value in fortunes),
                }
                for label, lower, upper in wealth_bands
            ]
            st.subheader("Répartition des fortunes")
            bar_chart(distribution, x="Tranche de fortune", y="Nombre de ménages")

if not lazy_tabs or model_tab.open:
    with model_tab:
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
            ("Banques et monnaie", "Un prêt crée un dépôt ; son remboursement détruit de la monnaie. Les banques évaluent le risque, la liquidité et leurs fonds propres. L'aide publique à leur résolution est plafonnée."),
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
