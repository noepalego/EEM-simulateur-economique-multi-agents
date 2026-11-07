"""Bourse et épargne des ménages.

Chaque mois, les ménages rééquilibrent leur épargne entre trois placements :
dépôts (rémunérés au taux des dépôts), obligations d'État (taux directeur + prime)
et actions (rendement des bénéfices + croissance anticipée + tendance récente).
La part visée de chaque placement augmente avec son rendement excédentaire sur
les dépôts et dépend du goût du risque ; une réserve de trésorerie couvre la
consommation de quelques mois.

Les cours se forment par les ordres : un excès d'achats fait monter le cours,
un excès de ventes le fait baisser, et un rappel ramène le cours vers la valeur
fondamentale (fonds propres et bénéfices actualisés au taux directeur + prime de
risque : quand les taux montent, la Bourse baisse). Les titres s'échangent entre
investisseurs au cours du mois ; l'excès de demande est servi par une émission
d'actions nouvelles (l'entreprise lève des fonds). Les obligations s'achètent et
se revendent aux banques à leur valeur nominale. Tout est transfert : aucune
monnaie n'est créée, sauf quand une banque rachète une obligation (le dépôt du
ménage est alors créé par la banque, comme un prêt).
"""

from __future__ import annotations

from economy import diagnostics
from economy.models import Company, Person, SimulationConfig, positive_loan_book
from economy.wealth import _net_worth, company_balance_sheet, share_prices


def expected_stock_returns(
    companies: list[Company], config: SimulationConfig
) -> tuple[dict[str, float], dict[str, float], float]:
    """Rendement annuel anticipé de chaque titre coté, capitalisations et rendement du marché.

    Rendement = bénéfices annuels / capitalisation + croissance anticipée + tendance
    récente (variation du cours, bornée à ±15 %, annualisée) ; le marché est la
    moyenne pondérée par les capitalisations. Même formule pour les ménages et les
    entreprises.
    """
    active = [c for c in companies if c.active and c.shares_outstanding > 0.0]
    expected: dict[str, float] = {}
    caps: dict[str, float] = {}
    for c in active:
        opening = max(c.stock_price, 1.0)
        change = max(-0.15, min(0.15, c.stock_price / max(c.previous_stock_price, 1.0) - 1.0))
        earnings_yield = 12.0 * max(0.0, c.profit) / (opening * c.shares_outstanding)
        expected[c.id] = (
            earnings_yield + config.stock_growth_expectation + config.stock_trend_weight * 12.0 * change
        )
        caps[c.id] = opening * c.shares_outstanding
    total_cap = sum(caps.values())
    market_return = (
        sum(caps[cid] * expected[cid] for cid in caps) / total_cap
        if total_cap > 0.0
        else config.stock_growth_expectation
    )
    return expected, caps, market_return


def update_prices_and_trade(
    companies: list[Company],
    people: list[Person],
    event_sector: str | None = None,
    config: SimulationConfig | None = None,
    policy_rate: float | None = None,
    deposit_rate: float = 0.0,
    bond_rate: float | None = None,
    corporate_orders: dict[str, float] | None = None,
    extra_sells: list | None = None,
    no_issue: frozenset = frozenset(),
    founders: dict[str, str] | None = None,
    primary_first: frozenset = frozenset(),
) -> float:
    """Rééquilibre les portefeuilles, forme les cours et exécute les échanges.

    ``policy_rate``, ``deposit_rate`` et ``bond_rate`` sont mensuels. Sans
    ``bond_rate``, les ménages ne traitent pas d'obligations. ``corporate_orders``
    (identifiant d'entreprise -> montant) ajoute les ordres des entreprises
    (``economy.treasury``) au même carnet : un montant positif achète le panier du
    marché hors ses propres titres, un montant négatif vend une part de ses titres.
    ``extra_sells`` (nouvel État) : ordres de vente (porteur, entreprise, quantité) de l'État qui
    privatise, servis par les acheteurs de la séance au cours du mois ; ``no_issue`` : entreprises
    sous contrôle public, qui n'émettent pas d'actions nouvelles (l'État refuse la dilution).

    ``founders`` (entreprise -> fondateur) : décision de vente propre au fondateur, en plus du
    rééquilibrage commun (voir ``_founder_sale``). ``primary_first`` : jeunes entreprises en
    introduction en Bourse, dont les titres offerts servent la demande avant les vendeurs.
    Retourne le volume d'échanges entre investisseurs (marché secondaire).
    """
    config = config or SimulationConfig()
    annual_rate = 12.0 * (config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate))
    for company in companies:
        company.stock_trade_volume = 0.0
    active = [c for c in companies if c.active and c.shares_outstanding > 0.0]
    opening = {c.id: max(c.stock_price, 1.0) for c in active}
    prices = share_prices(companies)
    fundamentals = {
        c.id: _fundamental_price(c, companies, config, annual_rate, prices)
        * (1.02 if event_sector == c.sector else 1.0)
        for c in active
    }
    # rendement anticipé de chaque titre : bénéfices / cours + croissance + tendance
    expected, caps, market_return = expected_stock_returns(companies, config)
    deposit_annual = 12.0 * max(0.0, deposit_rate)
    stock_share = max(
        0.0,
        min(
            config.stock_max_share,
            config.stock_base_share
            + config.stock_return_sensitivity * (market_return - deposit_annual),
        ),
    )
    bond_share = 0.0
    if bond_rate is not None:
        bond_share = max(
            0.0,
            min(
                config.bond_max_share,
                config.bond_base_share
                + config.bond_return_sensitivity * (12.0 * bond_rate - deposit_annual),
            ),
        )
    # répartition des achats : capitalisation × sous-évaluation (valeur / cours)²
    weights = {
        c.id: caps[c.id] * max(0.2, min(3.0, fundamentals[c.id] / opening[c.id])) ** 2
        for c in active
    }
    total_weight = sum(weights.values())
    speed = config.household_stock_investment_rate
    household_buys: list[tuple[Person, float]] = []
    sells: dict[str, list[tuple[Person, float]]] = {cid: [] for cid in opening}
    bond_orders: list[tuple[Person, float]] = []
    for person in people:
        held = [(cid, q) for cid, q in person.shares.items() if cid in opening and q > 0.0]
        stock_value = sum([q * opening[cid] for cid, q in held])
        buffer = config.savings_cash_buffer_months * max(0.0, person.income_this_turn)
        investable = max(0.0, person.cash + stock_value + person.bond_holdings - buffer)
        wanted_stocks = min(1.0, stock_share * (0.5 + person.risk_tolerance))
        wanted_bonds = min(1.0 - wanted_stocks, bond_share * (1.5 - person.risk_tolerance))
        available = max(0.0, person.cash - buffer)
        stock_order = speed * (wanted_stocks * investable - stock_value)
        fraction = 0.0
        if stock_order > 0.0 and total_weight > 0.0:
            budget = min(stock_order, available)
            available -= budget
            if budget > 1.0:
                household_buys.append((person, budget))
        elif stock_order < 0.0 and stock_value > 0.0:
            fraction = min(1.0, -stock_order / stock_value)
            for cid, q in held:
                sells[cid].append((person, q * fraction))
        if founders:
            for cid, q in held:
                if founders.get(cid) == person.id:
                    extra = _founder_sale(person, q, q * fraction, opening[cid], stock_value,
                                          buffer, config, speed)
                    if extra > 1e-9:
                        sells[cid].append((person, extra))
        if bond_rate is not None:
            bond_order = speed * (wanted_bonds * investable - person.bond_holdings)
            if bond_order > 0.0:
                bond_order = min(bond_order, available)
            else:
                bond_order = max(bond_order, -person.bond_holdings)
            if abs(bond_order) > 1.0:
                bond_orders.append((person, bond_order))

    # Construire chaque carnet en une fois évite une recherche et un append par
    # couple ménage/titre. Même ordre des ménages et même multiplication/division.
    buys = {
        cid: [(person, budget * weight / total_weight) for person, budget in household_buys]
        for cid, weight in weights.items()
    }
    # ordres des entreprises (economy.treasury) : jamais leurs propres titres, pas de banques
    by_id = {c.id: c for c in companies}
    for company_id, order in (corporate_orders or {}).items():
        investor = by_id.get(company_id)
        if investor is None or not investor.active or investor.sector == "bank":
            continue
        if order > 0.0:
            basket = total_weight - weights.get(investor.id, 0.0)
            budget = min(order, max(0.0, investor.cash))
            if budget > 1.0 and basket > 0.0:
                for cid, weight in weights.items():
                    if cid != investor.id:
                        buys[cid].append((investor, budget * weight / basket))
        elif order < 0.0:
            held = [
                (cid, q) for cid, q in investor.shares.items()
                if cid in opening and cid != investor.id and q > 0.0
            ]
            value = sum(q * opening[cid] for cid, q in held)
            if value > 0.0:
                fraction = min(1.0, -order / value)
                for cid, q in held:
                    sells[cid].append((investor, q * fraction))
    for holder, cid, quantity in extra_sells or ():
        if cid in sells and quantity > 0.0:
            sells[cid].append((holder, min(quantity, holder.shares.get(cid, 0.0))))

    for company in companies:
        company.previous_stock_price = company.stock_price
        if not company.active:
            company.stock_price = 0.0
    for c in active:
        _clear_stock_market(c, opening[c.id], fundamentals[c.id], buys[c.id], sells[c.id], config,
                            issue=c.id not in no_issue, primary_first=c.id in primary_first)
    if bond_orders:
        trade_bonds_with_banks(
            bond_orders, [c for c in companies if c.active and c.sector == "bank"]
        )
    return sum(company.stock_trade_volume for company in companies)


def _founder_sale(
    person: Person,
    held: float,
    already: float,
    price: float,
    stock_value: float,
    buffer: float,
    config: SimulationConfig,
    speed: float,
) -> float:
    """Titres de sa propre entreprise qu'un fondateur décide de vendre ce mois, au-delà de la part
    déjà vendue par le rééquilibrage commun (``already``).

    - Diversification : la valeur de sa ligne ne doit pas dépasser une part de sa fortune nette
      qui croît avec son goût du risque (``founder_max_stake_share`` + ``founder_stake_risk_weight``
      × goût du risque) ; il vend l'excédent à la vitesse de rééquilibrage des ménages.
    - Trésorerie : sous sa réserve de consommation (``buffer``), il vend de quoi la reconstituer.
    Les ordres passent par le carnet : exécutés au cours du mois, contre de vrais acheteurs.
    """
    if held <= 0.0 or price <= 0.0:
        return 0.0
    worth = max(0.0, _net_worth(person, stock_value, config))
    limit = (config.founder_max_stake_share
             + config.founder_stake_risk_weight * person.risk_tolerance) * worth
    stake = held * price
    diversify = speed * (stake - limit) / price if stake > limit else 0.0
    liquidity = (buffer - person.cash) / price if person.cash < buffer else 0.0
    wanted = max(diversify, liquidity)
    return max(0.0, min(held - already, wanted - already))


def _clear_stock_market(
    company: Company,
    opening: float,
    fundamental: float,
    buys: list[tuple[Person, float]],
    sells: list[tuple[Person, float]],
    config: SimulationConfig,
    issue: bool = True,
    primary_first: bool = False,
) -> None:
    """Fixe le cours du mois par les ordres et exécute les échanges à ce cours ; la demande non
    servie est couverte par des actions nouvelles, sauf ``issue=False`` (contrôle public).

    ``primary_first`` (introduction en Bourse) : les titres offerts par l'entreprise servent la
    demande avant les vendeurs ; l'argent va d'abord à l'entreprise, puis aux vendeurs."""
    demand_value = sum([value for _, value in buys])
    supply = sum([quantity for _, quantity in sells])
    capitalisation = opening * company.shares_outstanding
    imbalance = (demand_value - supply * opening) / max(capitalisation, 1.0)
    price = opening * (1.0 + config.stock_flow_impact * imbalance)
    price += config.stock_adjustment_speed * (fundamental - price)
    # aucun plafond : seuls la vitesse d'ajustement (±15 %/mois) et le plancher de 1 € bornent le cours
    diagnostics.bite("vitesse_bourse", not opening * 0.85 <= price <= opening * 1.15)
    price = max(opening * 0.85, min(opening * 1.15, price))
    price = max(1.0, price)
    company.stock_price = price
    if demand_value <= 0.0:
        return
    demand = demand_value / price
    if primary_first and issue and company.treasury_shares > 1e-9:
        _clear_primary_first(company, price, demand, demand_value, buys, sells, supply)
        return
    matched = min(demand, supply)
    cid = company.id
    if matched > 0.0:
        buy_fill, sell_fill = matched / demand, matched / supply
        for person, quantity in sells:
            sold = quantity * sell_fill
            shares = person.shares
            remaining = shares.get(cid, 0.0) - sold
            if remaining > 1e-9:
                shares[cid] = remaining
            else:
                shares.pop(cid, None)
            person.cash += sold * price
        for person, value in buys:
            bought = value / price * buy_fill
            shares = person.shares
            shares[cid] = shares.get(cid, 0.0) + bought
            person.cash -= bought * price
        company.stock_trade_volume += matched * price
    # demande non servie : émission d'actions nouvelles (levée de fonds)
    issued = min(demand - matched, company.treasury_shares)
    if issued > 1e-9 and issue:
        for person, value in buys:
            bought = issued * value / demand_value
            shares = person.shares
            shares[cid] = shares.get(cid, 0.0) + bought
            person.cash -= bought * price
        company.treasury_shares -= issued
        company.cash += issued * price
        if company.sector == "bank":
            company.bank_equity += issued * price


def _clear_primary_first(
    company: Company,
    price: float,
    demand: float,
    demand_value: float,
    buys: list[tuple[Person, float]],
    sells: list[tuple[Person, float]],
    supply: float,
) -> None:
    """Séance d'une entreprise en introduction : émission d'abord, vendeurs ensuite, au même
    cours ; chaque acheteur reçoit sa part de l'ensemble au prorata de son ordre."""
    issued = min(demand, company.treasury_shares)
    matched = min(demand - issued, supply)
    if matched > 0.0:
        sell_fill = matched / supply
        for person, quantity in sells:
            sold = quantity * sell_fill
            remaining = person.shares.get(company.id, 0.0) - sold
            if remaining > 1e-9:
                person.shares[company.id] = remaining
            else:
                person.shares.pop(company.id, None)
            person.cash += sold * price
        company.stock_trade_volume += matched * price
    served = issued + matched
    for person, value in buys:
        bought = served * value / demand_value
        person.shares[company.id] = person.shares.get(company.id, 0.0) + bought
        person.cash -= bought * price
    company.treasury_shares -= issued
    company.cash += issued * price
    if company.sector == "bank":
        company.bank_equity += issued * price


def trade_bonds_with_banks(orders: list[tuple[Person, float]], banks: list[Company]) -> float:
    """Achats et ventes d'obligations d'État par les ménages, contre les banques, au pair.

    Un achat réduit le portefeuille des banques (au prorata de leurs titres) et
    détruit le dépôt du ménage ; une vente est rachetée par les banques et crée
    un dépôt. Retourne les achats nets des ménages.
    """
    if not banks:
        return 0.0
    held = sum(bank.bond_holdings for bank in banks)
    wanted = sum(amount for _, amount in orders if amount > 0.0)
    fill = min(1.0, held / wanted) if wanted > 0.0 else 0.0
    net = 0.0
    remaining = max(0.0, held)
    for person, amount in orders:
        traded = (
            min(amount * fill, max(0.0, person.cash), remaining)
            if amount > 0.0 else max(amount, -max(0.0, person.bond_holdings))
        )
        if traded > 0.0:
            remaining -= traded
        if traded == 0.0:
            continue
        person.bond_holdings += traded
        person.cash -= traded
        net += traded
    if net == 0.0:
        return 0.0
    if net > 0.0:
        for bank in banks:
            part = min(bank.bond_holdings, net * bank.bond_holdings / held)
            bank.bond_holdings -= part
    else:
        for bank in banks:
            bank.bond_holdings += -net / len(banks)
    return net


def _fundamental_price(
    company: Company,
    companies: list[Company],
    config: SimulationConfig,
    annual_rate: float | None = None,
    prices: dict[str, float] | None = None,
) -> float:
    """Valeur par action : fonds propres et bénéfices anticipés actualisés.

    Les bénéfices annuels anticipés sont divisés par (taux + prime de risque -
    croissance anticipée), borné par ``stock_min_discount`` : quand les taux
    montent, les cours baissent.
    """
    if annual_rate is None:
        annual_rate = 12.0 * config.loan_interest_rate
    balance_sheet = company_balance_sheet(company, companies, config, prices)
    # participations au cours avec une décote de holding : un cours ne peut pas
    # s'auto-entretenir par des participations croisées (facteur de contraction < 1) ;
    # les dividendes reçus ne sont pas dans le bénéfice capitalisé ci-dessous
    holdings = balance_sheet["Participations financières"] - (
        max(0.0, company.bond_holdings) if company.sector != "bank" else 0.0
    )
    book_equity = max(
        0.0, balance_sheet["Capitaux propres"] - config.holding_discount * max(0.0, holdings)
    )
    expected_monthly_profit = company.profit + 0.25 * (
        company.profit - company.previous_profit
    )
    if company.previous_revenue > 0.0:
        revenue_growth = max(
            -0.5,
            min(0.5, (company.revenue - company.previous_revenue) / company.previous_revenue),
        )
        expected_monthly_profit += max(0.0, company.profit) * revenue_growth * 0.25
    discount = max(
        config.stock_min_discount,
        annual_rate + config.equity_risk_premium - config.stock_growth_expectation,
    )
    earnings_value = max(0.0, expected_monthly_profit) * 12.0 / discount
    book_weight = config.stock_book_value_weight
    estimated_equity = (
        book_equity * book_weight + earnings_value * (1.0 - book_weight)
    )
    return max(1.0, estimated_equity / max(company.shares_outstanding, 1.0))


def _buy(owner: Person | Company, company: Company, quantity: float) -> float:
    """Souscription d'actions nouvelles (titres en trésorerie) au cours du jour."""
    quantity = min(quantity, company.treasury_shares)
    cost = quantity * company.stock_price
    if quantity <= 0 or cost > owner.cash:
        return 0.0
    owner.cash -= cost
    owner.shares[company.id] = owner.shares.get(company.id, 0.0) + quantity
    company.treasury_shares -= quantity
    company.cash += cost
    if company.sector == "bank":
        company.bank_equity += cost
    return quantity


def distribute_dividends(
    companies: list[Company],
    people: list[Person],
    payout_rate: float = 0.12,
    working_capital_months: float = 0.0,
    bank_excess_payout_rate: float = 0.0,
    bank_reserve_ratio: float = 0.25,
    extra_holders: tuple = (),
    retained: frozenset = frozenset(),
    planned_payouts: dict[str, float] | None = None,
) -> float:
    """Dividendes : part des bénéfices + distribution de la trésorerie excédentaire.

    La distribution de l'excédent d'une entreprise non bancaire est celle qu'elle a décidée
    en comparant les rendements (``planned_payouts``, ``economy.treasury``) ; sans plan, rien
    au-delà de la part des bénéfices. Les dividendes reçus par une entreprise sont un revenu
    financier. Une banque garde en réserve une fraction de ses prêts et ``working_capital_months``
    mois de charges, et restitue le capital au-delà à ``bank_excess_payout_rate`` par mois. Les
    entreprises de ``retained`` (banques sous leur exigence de fonds propres et son coussin)
    ne distribuent rien : elles reconstituent leur capital.
    """
    planned_payouts = planned_payouts or {}
    total_paid = 0.0
    investors: list[Person | Company] = [*people, *companies, *extra_holders]
    # détenteurs de chaque titre, dans l'ordre des investisseurs : on ne balaie
    # plus toute la population pour chaque entreprise (mêmes sommes, même ordre)
    # (porteur, titres, ménage ?) relevés une fois : les titres ne changent pas pendant la
    # distribution (seules les trésoreries bougent) ; mêmes valeurs, même ordre d'addition
    holders_by_company: dict[str, list[tuple]] = {}
    for investor in investors:
        investor_id = investor.id
        is_person = isinstance(investor, Person)
        for company_id, quantity in investor.shares.items():
            if company_id != investor_id:
                entry = holders_by_company.get(company_id)
                if entry is None:
                    holders_by_company[company_id] = [(investor, quantity, is_person)]
                else:
                    entry.append((investor, quantity, is_person))
    for company in companies:
        holders = holders_by_company.get(company.id, ())
        eligible_shares = sum([quantity for _, quantity, _ in holders])
        if not company.active or eligible_shares <= 0.0 or company.id in retained:
            company.dividend_paid = 0.0
            continue
        leverage = company.debt / max(company.cash + company.debt, 1.0)
        effective_payout_rate = (
            payout_rate
            * (1.0 - company.reinvestment_preference * 0.75)
            * (1.0 - min(0.75, leverage * 0.5))
        )
        operating_reserve = (
            company.target_employees * company.wage_offer * 1.5
            + company.debt
            * (
                company.debt_interest_rate
                if company.debt_interest_rate is not None
                else 0.0
            )
        )
        distributable_cash = max(0.0, company.cash - operating_reserve)
        profit_payout = (
            company.profit * effective_payout_rate if company.profit > 0.0 else 0.0
        )
        excess_payout = 0.0
        if company.sector == "bank" and bank_excess_payout_rate > 0.0:
            # Une banque conserve en réserve une fraction de ses prêts (capacité
            # de refinancement) et un fonds de roulement ; le capital en excès
            # est restitué progressivement à ses actionnaires.
            loan_book = positive_loan_book(company.bank_loan_accounts)
            requirement = bank_reserve_ratio * loan_book + working_capital_months * max(
                company.expenses, company.wage_bill
            )
            excess_payout = max(0.0, company.cash - requirement) * bank_excess_payout_rate
        elif company.sector != "bank":
            excess_payout = max(0.0, planned_payouts.get(company.id, 0.0))
        payout = min(distributable_cash, max(profit_payout, excess_payout))
        company.dividend_paid = payout
        if payout <= 0.0:
            continue
        for investor, shares, is_person in holders:
            if shares > 0.0:
                payment = payout * shares / eligible_shares
                investor.cash += payment
                if is_person:
                    investor.income_this_turn += payment
                    investor.dividend_income_this_turn += payment
                    investor.lifetime_earnings += payment
                else:
                    investor.financial_income += payment
        company.cash -= payout
        total_paid += payout
    return total_paid


def _sell(owner: Person | Company, company: Company, quantity: float) -> None:
    """Rachat de titres par l'entreprise émettrice (privatisation des parts de l'État)."""
    if quantity <= 0:
        return
    affordable = company.cash / max(company.stock_price, 0.01)
    sold = min(quantity, affordable)
    if sold <= 0:
        return
    owner.shares[company.id] -= sold
    if owner.shares[company.id] <= 0.0:
        owner.shares.pop(company.id, None)
    company.treasury_shares += sold
    company.cash -= sold * company.stock_price
    if company.sector == "bank":
        company.bank_equity -= sold * company.stock_price
    owner.cash += sold * company.stock_price
