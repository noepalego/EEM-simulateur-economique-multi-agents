from __future__ import annotations

from dataclasses import dataclass

from economy.models import Company, Person


def buy_public_goods(
    companies: list[Company],
    budget: float,
    max_stock_share: float = 0.5,
) -> tuple[float, float, float]:
    """L'État achète des biens et services aux entreprises avec son solde.

    Achats proportionnels aux stocks invendus d'alimentation et de biens
    courants, au prix de chaque entreprise (consommation publique). La monnaie
    passe de l'État aux entreprises : rien n'est créé et rien n'est mis de côté.
    Retourne (montant dépensé, unités alimentaires, unités de biens).
    """
    sellers = [
        company
        for company in companies
        if company.active and company.sector in ("food", "goods")
    ]

    def stock(company: Company) -> float:
        quantity = (
            company.inventory if company.sector == "food" else company.goods_inventory
        )
        return max(0.0, quantity) * max(0.0, min(1.0, max_stock_share))

    available_value = sum(stock(company) * company.price for company in sellers)
    spend = min(max(0.0, budget), available_value)
    if spend <= 0.0 or available_value <= 0.0:
        return 0.0, 0.0, 0.0
    fraction = spend / available_value
    food_units = goods_units = 0.0
    for company in sellers:
        quantity = stock(company) * fraction
        if quantity <= 0.0:
            continue
        payment = quantity * company.price
        if company.sector == "food":
            company.inventory -= quantity
            food_units += quantity
        else:
            company.goods_inventory -= quantity
            goods_units += quantity
        company.cash += payment
        company.revenue += payment
        company.units_sold += quantity
    return spend, food_units, goods_units


def pay_public_transfers(people: list[Person], budget: float, targeting: float = 0.0) -> float:
    """Verse les transferts du programme aux ménages. Transfert pur : ni création ni destruction
    de monnaie.

    Une part ``1 − targeting`` est universelle (égale par ménage) ; la part ``targeting`` est
    versée sous condition de ressources : aux ménages dont le revenu (revenu du mois + revenus du
    capital du mois précédent) est sous le revenu médian, au prorata de leur écart à ce médian.
    Sans ménage sous le médian, tout est universel.
    """
    if budget <= 0.0 or not people:
        return 0.0
    targeting = max(0.0, min(1.0, targeting))
    weights: list[float] = []
    if targeting > 0.0:
        incomes = [p.income_this_turn + p.capital_income_last_month for p in people]
        median = sorted(incomes)[len(incomes) // 2]
        weights = [max(0.0, median - income) for income in incomes]
        if sum(weights) <= 0.0:
            targeting, weights = 0.0, []
    universal = budget * (1.0 - targeting) / len(people)
    total_weight = sum(weights)
    for index, person in enumerate(people):
        share = universal + (budget * targeting * weights[index] / total_weight if weights else 0.0)
        person.cash += share
        person.income_this_turn += share
        person.lifetime_earnings += share
    return budget


def tax_company_profits(
    companies: list[Company], tax_rate: float, carry_months: int = 0
) -> float:
    """Impôt sur les bénéfices, avec report des pertes (déficits imputés sur les profits suivants).

    ``carry_months`` = 0 : pas de report (impôt mensuel sur le résultat positif).
    Sinon un déficit reste imputable et s'érode sur ``carry_months`` mois.
    """
    collected = 0.0
    for company in companies:
        if not company.active:
            continue
        result = company.revenue - company.expenses
        taxable_profit = max(0.0, result)
        if carry_months > 0:
            company.tax_loss_carryforward *= 1.0 - 1.0 / carry_months
            used = min(company.tax_loss_carryforward, taxable_profit)
            company.tax_loss_carryforward += max(0.0, -result) - used
            taxable_profit -= used
        tax = min(max(0.0, company.cash), taxable_profit * tax_rate)
        company.cash -= tax
        company.expenses += tax
        collected += tax
    return collected

@dataclass
class PublicSector:
    """État : trésorerie, dette, capital public, emploi, participations.

    Le compte de l'État est une entreprise fictive (``account``), hors de la liste
    des entreprises : sa trésorerie est la trésorerie publique, ce qui permet
    de réutiliser les paiements entre agents (``pay_business_suppliers``).
    """

    account: Company
    debt: float = 0.0
    public_capital: float = 0.0
    employees: int = 0
    wage_bill: float = 0.0
    revenue: float = 0.0
    spending: float = 0.0
    interest_paid: float = 0.0
    investment: float = 0.0
    purchases: float = 0.0
    equity_invested: float = 0.0      # titres achetés ce mois
    equity_proceeds: float = 0.0      # titres cédés ce mois (privatisations)
    equity_dividends: float = 0.0     # dividendes reçus ce mois
    pending_transfers: float = 0.0    # transferts décidés en fin de mois, versés au début du suivant
    total_equity_invested: float = 0.0
    total_equity_proceeds: float = 0.0
    total_equity_dividends: float = 0.0

    def portfolio_value(self, companies: list[Company]) -> float:
        """Valeur de marché des participations de l'État."""
        prices = {c.id: c.stock_price for c in companies if c.active}
        return sum(q * prices.get(cid, 0.0) for cid, q in self.account.shares.items())

    @property
    def cash(self) -> float:
        return self.account.cash

    @cash.setter
    def cash(self, value: float) -> None:
        self.account.cash = value

    def productivity_bonus(self, annual_gdp: float, ceiling: float) -> float:
        """Gain de productivité du capital public (rendements décroissants)."""
        capital = max(0.0, self.public_capital)
        return ceiling * capital / (capital + max(annual_gdp, 1.0))


def unemployment_benefit_entitlement(
    unemployment_benefit: float,
    price_index: float,
    unemployment_rate: float,
    cycle_sensitivity: float,
) -> float:
    """Allocation mensuelle par chômeur (indexée sur les prix, contracyclique)."""
    return (
        unemployment_benefit
        * max(0.0, price_index)
        / 100.0
        * (1.0 + max(0.0, cycle_sensitivity) * max(0.0, min(1.0, unemployment_rate)))
    )


def pay_unemployment_benefits(
    people: list[Person], per_person: float, funds: float
) -> float:
    """Verse l'allocation aux chômeurs, au prorata si les fonds manquent."""
    unemployed = [person for person in people if person.employer_id is None]
    if not unemployed or per_person <= 0.0:
        return 0.0
    entitlement = per_person * len(unemployed)
    share = min(max(0.0, funds), entitlement) / len(unemployed)
    for person in unemployed:
        person.cash += share
        person.income_this_turn += share
        person.lifetime_earnings += share
    return share * len(unemployed)
