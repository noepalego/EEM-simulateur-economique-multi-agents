from __future__ import annotations

from economy.models import Company, Person, SimulationConfig
from economy.wealth import company_balance_sheet


def update_prices_and_trade(
    companies: list[Company],
    people: list[Person],
    event_sector: str | None = None,
    config=None,
) -> float:
    from economy.models import SimulationConfig

    config = config or SimulationConfig()
    for company in companies:
        company.stock_trade_volume = 0.0
    fundamentals = {
        company.id: _fundamental_price(company, companies, config)
        for company in companies
        if company.active
    }
    for company in companies:
        company.previous_stock_price = company.stock_price
        if not company.active:
            company.stock_price = 0.0
            continue
        fair_price = fundamentals[company.id]
        if event_sector == company.sector:
            fair_price *= 1.02
        relative_gap = fair_price / max(company.stock_price, 1.0) - 1.0
        change = max(
            -0.15,
            min(0.15, relative_gap * config.stock_adjustment_speed),
        )
        company.stock_price = max(
            1.0,
            min(5000.0, company.stock_price * (1.0 + change)),
        )

    for person in people:
        for company in companies:
            if not company.active:
                continue
            if company.stock_price < company.previous_stock_price * 0.82:
                _sell(person, company, company.shares.get(company.id, 0.0))
        candidates = sorted(
            (company for company in companies if company.active),
            key=lambda company: (
                company.profit / max(500.0, company.cash + 500.0)
                + (0.08 if event_sector == company.sector else 0.0),
                company.id,
            ),
            reverse=True,
        )
        investable = max(0.0, person.cash - config.starting_wage * 0.5) * config.household_stock_investment_rate
        for company in candidates:
            if (
                company.sector == "real_estate"
                and not person.owns_home
                and person.cash > config.housing_price * 1.1
            ):
                continue
            if company.profit <= 0 or investable < company.stock_price or company.treasury_shares <= 0:
                if company.profit <= 0 or investable < company.stock_price:
                    continue
            quantity = min(company.treasury_shares, investable / company.stock_price, 5.0)
            bought = _buy(person, company, quantity)
            investable -= bought * company.stock_price
            if investable >= company.stock_price:
                secondary = _buy_from_holders(
                    person, company, investable / company.stock_price, people, companies
                )
                investable -= secondary * company.stock_price

    for investor in companies:
        for company in companies:
            if not company.active:
                continue
            if (
                investor.id != company.id
                and company.stock_price < company.previous_stock_price * 0.82
            ):
                _sell(investor, company, investor.shares.get(company.id, 0.0))
        # Les banques ne prennent pas de participations dans les entreprises
        # non financières : leur trésorerie est de la capacité de prêt.
        if investor.sector == "bank" or investor.cash < 100.0:
            continue
        investable = (investor.cash - config.starting_wage) * 0.02
        candidates = sorted(
            (company for company in companies if company.active),
            key=lambda company: company.profit / max(500.0, company.cash + 500.0),
            reverse=True,
        )
        for target in candidates:
            if target.id == investor.id or target.profit <= 0:
                continue
            if target.stock_price > investable:
                continue
            quantity = min(target.treasury_shares, investable / target.stock_price, 10.0)
            bought = _buy(investor, target, quantity)
            investable -= bought * target.stock_price
            if investable >= target.stock_price:
                secondary = _buy_from_holders(
                    investor, target, investable / target.stock_price, people, companies
                )
                investable -= secondary * target.stock_price

    return sum(company.stock_trade_volume for company in companies)


def _fundamental_price(
    company: Company,
    companies: list[Company],
    config: SimulationConfig,
) -> float:
    balance_sheet = company_balance_sheet(company, companies, config)
    book_equity = max(0.0, balance_sheet["Capitaux propres"])
    expected_monthly_profit = company.profit + 0.25 * (
        company.profit - company.previous_profit
    )
    if company.previous_revenue > 0.0:
        revenue_growth = max(
            -0.5,
            min(0.5, (company.revenue - company.previous_revenue) / company.previous_revenue),
        )
        expected_monthly_profit += max(0.0, company.profit) * revenue_growth * 0.25
    earnings_value = (
        max(0.0, expected_monthly_profit)
        * 12.0
        * config.stock_earnings_multiple
    )
    book_weight = config.stock_book_value_weight
    estimated_equity = (
        book_equity * book_weight + earnings_value * (1.0 - book_weight)
    )
    return max(1.0, estimated_equity / max(company.shares_outstanding, 1.0))


def _buy(owner: Person | Company, company: Company, quantity: float) -> float:
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


def _buy_from_holders(
    buyer: Person | Company,
    company: Company,
    quantity: float,
    people: list[Person],
    companies: list[Company],
) -> float:
    remaining = min(quantity, max(0.0, buyer.cash) / company.stock_price)
    traded = 0.0
    sellers: list[Person | Company] = [
        *people,
        *(investor for investor in companies if investor.id != buyer.id),
    ]
    for seller in sellers:
        if seller is buyer:
            continue
        held = seller.shares.get(company.id, 0.0)
        sellable = held * 0.02
        transfer = min(remaining, sellable)
        if transfer <= 0.0:
            continue
        seller.shares[company.id] -= transfer
        if seller.shares[company.id] <= 0.0:
            seller.shares.pop(company.id, None)
        buyer.shares[company.id] = buyer.shares.get(company.id, 0.0) + transfer
        value = transfer * company.stock_price
        seller.cash += value
        buyer.cash -= value
        company.stock_trade_volume += value
        remaining -= transfer
        traded += transfer
        if remaining <= 0.000001:
            break
    return traded


def distribute_dividends(
    companies: list[Company],
    people: list[Person],
    payout_rate: float = 0.12,
    working_capital_months: float = 0.0,
    excess_payout_rate: float = 0.0,
    bank_excess_payout_rate: float = 0.0,
    bank_reserve_ratio: float = 0.25,
) -> float:
    """Dividendes : part des bénéfices + distribution de la trésorerie excédentaire.

    Le fonds de roulement conservé est ``working_capital_months`` mois de
    charges (salaires, intrants, impôts, intérêts). La trésorerie au-delà est
    distribuée à raison de ``excess_payout_rate`` par mois : l'argent qui dort
    dans les entreprises retourne aux ménages actionnaires. Les banques gardent
    leur trésorerie, qui est leur capacité de prêt.
    """
    total_paid = 0.0
    investors: list[Person | Company] = [*people, *companies]
    for company in companies:
        eligible_shares = sum(
            investor.shares.get(company.id, 0.0)
            for investor in investors
            if investor.id != company.id
        )
        if not company.active or eligible_shares <= 0.0:
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
            loan_book = sum(max(0.0, v) for v in company.bank_loan_accounts.values())
            requirement = bank_reserve_ratio * loan_book + working_capital_months * max(
                company.expenses, company.wage_bill
            )
            excess_payout = max(0.0, company.cash - requirement) * bank_excess_payout_rate
        elif excess_payout_rate > 0.0 and company.sector != "bank":
            working_capital = working_capital_months * max(
                company.expenses, company.wage_bill
            ) + (
                company.debt
                * (
                    company.debt_interest_rate
                    if company.debt_interest_rate is not None
                    else 0.0
                )
            )
            excess_payout = max(0.0, company.cash - working_capital) * excess_payout_rate
        payout = min(distributable_cash, max(profit_payout, excess_payout))
        company.dividend_paid = payout
        if payout <= 0.0:
            continue
        for investor in investors:
            if investor.id == company.id:
                continue
            shares = investor.shares.get(company.id, 0.0)
            if shares > 0.0:
                payment = payout * shares / eligible_shares
                investor.cash += payment
                if isinstance(investor, Person):
                    investor.income_this_turn += payment
                    investor.dividend_income_this_turn += payment
                    investor.lifetime_earnings += payment
        company.cash -= payout
        total_paid += payout
    return total_paid


def _sell(owner: Person | Company, company: Company, quantity: float) -> None:
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
        company.bank_equity = max(0.0, company.bank_equity - sold * company.stock_price)
    owner.cash += sold * company.stock_price
