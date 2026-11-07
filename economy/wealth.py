from __future__ import annotations

from economy.models import Company, MarketRecord, Person, SimulationConfig, positive_loan_book


def household_net_worth(
    person: Person,
    companies: list[Company],
    config: SimulationConfig,
    prices: dict[str, float] | None = None,
) -> float:
    prices = prices if prices is not None else share_prices(companies)
    stock_value = sum([
        shares * prices.get(company_id, 0.0)
        for company_id, shares in person.shares.items()
    ])
    return _net_worth(person, stock_value, config)


def _net_worth(person: Person, stock_value: float, config: SimulationConfig) -> float:
    """Fortune nette d'un ménage, la valeur de son portefeuille étant connue."""
    housing_value = (
        int(person.owns_home) + person.investment_homes
    ) * config.housing_price
    return (
        person.cash
        + stock_value
        + person.bond_holdings
        + housing_value
        - max(0.0, person.mortgage_debt)
        - max(0.0, person.consumer_debt)
    )


def company_net_worth(
    company: Company,
    companies: list[Company],
    config: SimulationConfig,
    prices: dict[str, float] | None = None,
) -> float:
    return company_balance_sheet(company, companies, config, prices)["Capitaux propres"]


def company_balance_sheet(
    company: Company,
    companies: list[Company],
    config: SimulationConfig,
    prices: dict[str, float] | None = None,
) -> dict[str, float]:
    prices = prices if prices is not None else share_prices(companies)
    portfolio_value = sum([
        shares * prices.get(company_id, 0.0)
        for company_id, shares in company.shares.items()
    ])
    cash = max(0.0, company.cash)
    food_inventory = max(0.0, company.inventory) * config.food_input_cost
    goods_inventory = max(0.0, company.goods_inventory) * config.goods_input_cost
    real_estate = max(0, company.property_inventory) * config.housing_price
    construction = max(0.0, company.construction_in_progress)
    technology_inventory = max(0.0, company.tech_inventory) * config.tech_unit_price
    technology = (
        max(0.0, company.technology_asset_value)
        if company.technology_asset_value > 0.0
        else max(0.0, company.tech_level) * config.tech_unit_price / 0.03
    )
    productive_capital = max(0.0, company.productive_capital_value)
    # obligations d'État au pair (les banques gardent leur bilan propre, fondé sur bank_equity)
    bonds = max(0.0, company.bond_holdings) if company.sector != "bank" else 0.0
    financial_investments = max(0.0, portfolio_value) + bonds
    bank_loan_portfolio = (
        positive_loan_book(company.bank_loan_accounts)
        if company.sector == "bank"
        else 0.0
    )
    assets = (
        cash
        + food_inventory
        + goods_inventory
        + real_estate
        + construction
        + technology_inventory
        + technology
        + productive_capital
        + financial_investments
        + bank_loan_portfolio
    )
    bank_debt = max(0.0, company.debt)
    if company.sector == "bank":
        bank_equity = min(assets, max(0.0, company.bank_equity))
        other_liabilities = max(0.0, assets - bank_equity)
    else:
        bank_equity = assets - bank_debt
        other_liabilities = 0.0
    return {
        "Trésorerie": cash,
        "Stocks alimentaires": food_inventory,
        "Stocks de biens": goods_inventory,
        "Immobilier détenu": real_estate,
        "Construction en cours": construction,
        "Stocks technologiques": technology_inventory,
        "Prêts aux ménages et entreprises": bank_loan_portfolio,
        "Actifs technologiques": technology,
        "Capacité de production capitalisée": productive_capital,
        "Participations financières": financial_investments,
        "Prêts aux ménages et entreprises": bank_loan_portfolio,
        "Total des actifs": assets,
        "Dettes bancaires": bank_debt,
        "Autres passifs": other_liabilities,
        "Total des passifs": bank_debt + other_liabilities,
        "Capitaux propres": bank_equity,
    }


def company_valuation_metrics(
    company: Company,
    companies: list[Company],
    config: SimulationConfig,
) -> dict[str, float | None]:
    balance_sheet = company_balance_sheet(company, companies, config)
    market_cap = max(0.0, company.stock_price * company.shares_outstanding)
    annual_earnings = company.profit * 12.0
    annual_dividends = company.dividend_paid * 12.0
    free_cash_flow = (
        company.profit
        + company.depreciation_this_turn
        - company.capital_expenditure
        + company.property_cost_sold_this_turn
    )
    equity = balance_sheet["Capitaux propres"]
    revenue = company.revenue
    return {
        "Capitalisation boursière": market_cap,
        "Valeur comptable": equity,
        "Valeur économique estimée": (
            market_cap + balance_sheet["Dettes bancaires"] - balance_sheet["Trésorerie"]
        ),
        "PER": market_cap / annual_earnings if annual_earnings > 0.0 else None,
        "Cours / valeur comptable": market_cap / equity if equity > 0.0 else None,
        "Rendement du dividende": annual_dividends / market_cap if market_cap > 0.0 else None,
        "Marge nette": company.profit / revenue if revenue > 0.0 else None,
        "Dette / actifs": (
            balance_sheet["Dettes bancaires"] / balance_sheet["Total des actifs"]
            if balance_sheet["Total des actifs"] > 0.0
            else None
        ),
        "Flux de trésorerie disponible": free_cash_flow,
    }


def gini(values: list[float]) -> float:
    positive = sorted(max(0.0, value) for value in values)
    total = sum(positive)
    if not positive or total <= 0.0:
        return 0.0
    count = len(positive)
    weighted_sum = sum((index + 1) * value for index, value in enumerate(positive))
    return max(0.0, min(1.0, (2.0 * weighted_sum / (count * total)) - (count + 1.0) / count))


def wealth_leaderboards(
    people: list[Person],
    companies: list[Company],
    config: SimulationConfig,
    limit: int = 10,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    # cours relevés une fois (auparavant une fois par ménage et par titre détenu : 36 000
    # appels à 300 périodes) ; mêmes valeurs et mêmes sommes
    prices = share_prices(companies)
    households = sorted(
        (
            {
                "id": person.id,
                "Ménage": person.name,
                "Fortune nette": household_net_worth(person, companies, config, prices),
                "Trésorerie": person.cash,
                "Immobilier": (
                    int(person.owns_home) + person.investment_homes
                ) * config.housing_price,
                "Dette immobilière": person.mortgage_debt,
                "Actions": sum(
                    shares * prices.get(company_id, 0.0)
                    for company_id, shares in person.shares.items()
                ),
            }
            for person in people
        ),
        key=lambda row: float(row["Fortune nette"]),
        reverse=True,
    )
    firms = sorted(
        (
            {
                "id": company.id,
                "Entreprise": company.name,
                "Fortune nette": company_net_worth(company, companies, config, prices),
                "Trésorerie": company.cash,
                "Dettes": company.debt,
                "Profit": company.profit,
                "Secteur": company.sector,
            }
            for company in companies
            if company.active
        ),
        key=lambda row: float(row["Fortune nette"]),
        reverse=True,
    )
    return households[:limit], firms[:limit]


def capture_wealth_record(
    people: list[Person],
    companies: list[Company],
    config: SimulationConfig,
    month: int,
) -> tuple[float, float, float, float, float, float]:
    prices = share_prices(companies)
    # valeur du portefeuille calculée une fois par ménage (fortune et historique)
    stock_values = {
        person.id: sum([
            shares * prices.get(company_id, 0.0)
            for company_id, shares in person.shares.items()
        ])
        for person in people
    }
    household_wealth = {
        person.id: _net_worth(person, stock_values[person.id], config)
        for person in people
    }
    firm_wealth = {
        company.id: company_net_worth(company, companies, config, prices)
        for company in companies
        if company.active
    }
    household_values = list(household_wealth.values())
    for person in people:
        person.wealth_history.append(household_wealth[person.id])
        person.financial_history.append(
            {
                "Période": float(month),
                "Trésorerie nette": person.cash,
                "Revenus": person.income_this_turn,
                "Dividendes": person.dividend_income_this_turn,
                "Dépenses": person.expenses_this_turn,
                "Fortune": household_wealth[person.id],
                "Actions": stock_values[person.id],
                "Valeur immobilière": (
                    int(person.owns_home) + person.investment_homes
                ) * config.housing_price,
            }
        )
    for company in companies:
        if company.active:
            company.financial_history.append(
                {
                    "Période": float(month),
                    "Trésorerie": company.cash,
                    "Bénéfice": company.profit,
                    "Dividendes": company.dividend_paid,
                    "Employés": float(len(company.employees)),
                    "Dettes": company.debt,
                    "Fortune": firm_wealth[company.id],
                    "Cours": company.stock_price,
                    "Chiffre d'affaires": company.revenue,
                    "Stocks alimentaires": company.inventory,
                    "Stocks de biens": company.goods_inventory,
                    "Stocks technologiques": company.tech_inventory,
                    "Production mensuelle": company.units_produced,
                    "Capacité supplémentaire": company.production_capacity,
                    "Investissement productif": company.capital_expenditure,
                    "Niveau technologique": company.tech_level,
                    "Flux de trésorerie disponible": (
                        company.profit
                        + company.depreciation_this_turn
                        - company.capital_expenditure
                        + company.property_cost_sold_this_turn
                    ),
                }
            )
    total = sum(household_values)
    median = sorted(household_values)[len(household_values) // 2] if household_values else 0.0
    top_household = max(household_values, default=0.0)
    top_firm = max(firm_wealth.values(), default=0.0)
    top_count = max(1, (len(household_values) + 9) // 10)
    top_ten_share = (
        sum(sorted(household_values, reverse=True)[:top_count]) / total
        if total > 0.0
        else 0.0
    )
    return gini(household_values), median, total, top_household, top_firm, top_ten_share


def enrich_wealth_record(
    record: MarketRecord,
    people: list[Person],
    companies: list[Company],
    config: SimulationConfig,
    price_index: float = 100.0,
) -> None:
    (
        record.household_gini,
        record.household_wealth_median,
        record.household_wealth_total,
        record.top_household_wealth,
        record.top_company_wealth,
        record.household_top10_share,
    ) = capture_wealth_record(people, companies, config, record.month)
    _inequality_indicators(record, people, config)
    record.company_debt = sum(company.debt for company in companies if company.active)
    record.active_companies = sum(company.active for company in companies)
    for person in people:
        person.financial_history[-1]["Fortune réelle"] = (
            person.financial_history[-1]["Fortune"]
            * 100.0
            / max(price_index, 1.0)
        )


def _inequality_indicators(record: MarketRecord, people: list[Person], config: SimulationConfig) -> None:
    """Indicateurs d'inégalités et de logement du mois (mesure seule, aucun effet sur les agents).

    Revenu = ``income_this_turn`` en fin de mois : salaires, allocations, transferts, dividendes et
    loyers reçus ; revenu disponible = revenu − impôt sur le revenu prélevé ce mois. Pauvreté :
    revenu disponible sous 60 % de la médiane. Part du 1 % : au moins un ménage."""
    if not people:
        return
    incomes = [max(0.0, p.income_this_turn) for p in people]
    disposable = [max(0.0, p.income_this_turn - p.taxes_paid) for p in people]
    record.income_gini = gini(incomes)
    record.disposable_income_gini = gini(disposable)
    median = sorted(disposable)[len(disposable) // 2]
    record.poverty_rate = sum(v < 0.6 * median for v in disposable) / len(people) if median > 0 else 0.0
    record.unhoused_households = sum(
        1 for p in people if not p.owns_home and p.rental_owner_id is None
    )
    wealth = sorted((p.wealth_history[-1] for p in people if p.wealth_history), reverse=True)
    total = sum(wealth)
    top = max(1, len(wealth) // 100)
    record.household_top1_share = sum(wealth[:top]) / total if total > 0.0 else 0.0
    housing = sum((int(p.owns_home) + p.investment_homes) * config.housing_price for p in people)
    gross = sum(
        max(0.0, p.cash) + p.bond_holdings
        + (p.financial_history[-1]["Actions"] if p.financial_history else 0.0)
        for p in people
    ) + housing
    record.housing_wealth_share = housing / gross if gross > 0.0 else 0.0


def share_prices(companies: list[Company]) -> dict[str, float]:
    """Cours des entreprises actives, indexés par identifiant (évite une
    recherche linéaire par titre détenu)."""
    prices: dict[str, float] = {}
    for company in companies:
        if company.active:
            prices.setdefault(company.id, company.stock_price)
    return prices


def _share_price(companies: list[Company], company_id: str) -> float:
    return share_prices(companies).get(company_id, 0.0)
