"""Emploi de la trésorerie des entreprises (mode « returns » de ``corporate_cash_policy``).

Chaque entreprise non bancaire compare des rendements annuels qu'elle calcule
elle-même avec ce qu'elle observe :

- garder sa trésorerie : taux des dépôts (la référence) ;
- distribuer à ses actionnaires : coût des fonds propres (taux directeur +
  prime de risque des actions), ce que ses actionnaires obtiendraient ailleurs ;
- rembourser sa dette par anticipation : le taux qu'elle paie ;
- acheter des obligations d'État : leur taux, net de l'impôt retenu à la source ;
- acheter des actions d'autres entreprises : même rendement anticipé que les
  ménages (bénéfices / cours + croissance + tendance), panier hors ses titres.

Le poids d'un emploi est son écart de rendement avec les dépôts ; les actions
sont pondérées par le goût du risque, comme chez les ménages. Les obligations et
actions visées sont une part des fonds libres (trésorerie au-delà du fonds de
roulement et de la réserve d'investissement, plus les titres détenus) ; chaque
mois l'entreprise comble ``treasury_adjustment_speed`` de l'écart, et distribue
ou rembourse la même fraction de la part qui leur revient. Une cible en baisse
(rendement plus faible, fonds libres négatifs) fait vendre. L'investissement de
capacité rentable passe d'abord : son budget du mois est réservé.

Rien n'est créé ni détruit ici : ce module ne fait que décider des montants ;
les transferts passent par ``distribute_dividends``, le carnet d'ordres de
``update_prices_and_trade``, les banques (obligations au pair, remboursements).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from economy.models import Company, SimulationConfig
from economy.stock_market import expected_stock_returns


@dataclass
class TreasuryPlan:
    """Décisions du mois d'une entreprise (montants en euros)."""

    distribution: float = 0.0
    repayment: float = 0.0
    bond_order: float = 0.0     # > 0 achat, < 0 vente
    equity_order: float = 0.0   # > 0 achat du panier, < 0 vente de ses titres
    reserve: float = 0.0        # budget d'investissement de capacité réservé
    returns: dict[str, float] = field(default_factory=dict)


def working_capital(company: Company, config: SimulationConfig) -> float:
    """Fonds de roulement conservé (même définition que ``distribute_dividends``)."""
    rate = company.debt_interest_rate if company.debt_interest_rate is not None else 0.0
    return config.working_capital_months * max(company.expenses, company.wage_bill) + company.debt * rate


def treasury_returns(
    company: Company,
    shares_return: float,
    config: SimulationConfig,
    policy_rate: float,
    deposit_rate: float,
    bond_rate: float,
    tax_rate: float,
) -> dict[str, float]:
    """Rendements annuels comparés par l'entreprise (taux d'entrée mensuels)."""
    returns = {
        "cash": 12.0 * max(0.0, deposit_rate),
        "distribute": 12.0 * max(0.0, policy_rate) + config.equity_risk_premium,
        "bonds": 12.0 * max(0.0, bond_rate) * (1.0 - tax_rate),
        "shares": shares_return,
    }
    if company.debt > 0.0:
        rate = company.debt_interest_rate
        returns["repay"] = 12.0 * (rate if rate is not None else config.loan_interest_rate)
    return returns


def plan_treasury(
    company: Company,
    returns: dict[str, float],
    equity_value: float,
    reserve: float,
    config: SimulationConfig,
) -> TreasuryPlan:
    """Allocation du mois selon les écarts de rendement (voir l'en-tête du module)."""
    plan = TreasuryPlan(reserve=reserve, returns=returns)
    speed = max(0.0, min(1.0, config.treasury_adjustment_speed))
    cash_free = company.cash - working_capital(company, config) - reserve
    free_funds = cash_free + company.bond_holdings + equity_value
    gaps = {
        use: max(0.0, returns[use] - returns["cash"])
        for use in ("distribute", "repay", "bonds", "shares")
        if use in returns
    }
    gaps["shares"] *= 0.5 + company.risk_tolerance
    total = sum(gaps.values())
    if free_funds <= 0.0 or total <= 0.0:
        # plus rien à placer : on revend progressivement les titres
        plan.bond_order = -speed * company.bond_holdings
        plan.equity_order = -speed * equity_value
        return plan
    share = {use: gap / total for use, gap in gaps.items()}
    plan.bond_order = speed * (share["bonds"] * free_funds - company.bond_holdings)
    plan.equity_order = speed * (share["shares"] * free_funds - equity_value)
    plan.distribution = speed * share["distribute"] * free_funds
    plan.repayment = min(company.debt, speed * share.get("repay", 0.0) * free_funds)
    # emplois financés par la trésorerie libre et par les ventes d'obligations du mois
    available = max(0.0, cash_free) + max(0.0, -plan.bond_order)
    needs = (
        plan.distribution + plan.repayment
        + max(0.0, plan.bond_order) + max(0.0, plan.equity_order)
    )
    if needs > available:
        scale = available / needs
        plan.distribution *= scale
        plan.repayment *= scale
        if plan.bond_order > 0.0:
            plan.bond_order *= scale
        if plan.equity_order > 0.0:
            plan.equity_order *= scale
    return plan


def plan_corporate_treasury(
    companies: list[Company],
    config: SimulationConfig,
    policy_rate: float,
    deposit_rate: float,
    bond_rate: float,
    tax_rate: float,
    reserves: dict[str, float],
) -> dict[str, TreasuryPlan]:
    """Plans du mois des entreprises non bancaires actives."""
    expected, caps, market_return = expected_stock_returns(companies, config)
    total_cap = sum(caps.values())
    weighted = sum(caps[cid] * expected[cid] for cid in caps)
    prices = {c.id: max(c.stock_price, 1.0) for c in companies if c.active and c.shares_outstanding > 0.0}
    plans: dict[str, TreasuryPlan] = {}
    for company in companies:
        if not company.active or company.sector == "bank":
            continue
        # rendement du panier de marché sans ses propres titres
        own_cap = caps.get(company.id, 0.0)
        shares_return = (
            (weighted - own_cap * expected.get(company.id, 0.0)) / (total_cap - own_cap)
            if total_cap - own_cap > 0.0
            else market_return
        )
        equity_value = sum(
            q * prices[cid] for cid, q in company.shares.items() if cid in prices and cid != company.id
        )
        returns = treasury_returns(
            company, shares_return, config, policy_rate, deposit_rate, bond_rate, tax_rate
        )
        plans[company.id] = plan_treasury(
            company, returns, equity_value, reserves.get(company.id, 0.0), config
        )
    return plans
