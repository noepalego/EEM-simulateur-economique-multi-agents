"""Flux d'un mois et construction de l'enregistrement de marché.

``MonthFlows`` rassemble les grandeurs calculées par les phases successives de
``EconomySimulation.step`` ; ``build_record`` les agrège en ``MarketRecord``
(PIB, revenus par secteur, indicateurs) sans modifier l'état de la simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING

from economy.models import MarketRecord
from economy.sectors import FIRM_SECTORS

if TYPE_CHECKING:
    from economy.simulation import EconomySimulation


@dataclass
class MonthFlows:
    """Résultats intermédiaires d'un mois, transmis d'une phase à la suivante."""

    money_issued: float = 0.0
    spoiled_food: float = 0.0
    spoiled_goods: float = 0.0
    event_sector: str | None = None
    housing_shortage: int = 0
    worker_skills: dict = field(default_factory=dict)
    average_wage: float = 0.0
    unemployment: float = 0.0
    household_taxes: float = 0.0
    benefits_paid: float = 0.0
    sold_food: float = 0.0
    food_revenue: float = 0.0
    unmet_food_demand: float = 0.0
    sold_goods: float = 0.0
    goods_revenue: float = 0.0
    unmet_goods_demand: float = 0.0
    public_spent: float = 0.0
    public_food_units: float = 0.0
    public_goods_units: float = 0.0
    public_transfers: float = 0.0
    public_wages: float = 0.0
    research_spending: float = 0.0
    public_investment: float = 0.0
    housing_units_built: int = 0
    rental_revenue: float = 0.0
    inflation_rate: float = 0.0
    corporate_taxes: float = 0.0
    research_investment: float = 0.0
    dividends_paid: float = 0.0
    secondary_volume: float = 0.0
    acquisitions: int = 0
    takeover_bids: int = 0
    bankruptcies: int = 0
    startups: int = 0
    production_capacity_investment: float = 0.0
    foreclosures: int = 0
    consumer_defaults: int = 0
    keynesian_investment: float = 0.0
    price_controls: bool = False
    vat_revenue: float = 0.0
    household_services: float = 0.0
    construction_completed: int = 0
    # novembre 2026, cinq idées : liquidations amiables, décès, droits de succession recouvrés
    dissolutions: int = 0
    deaths: int = 0
    estate_taxes: float = 0.0


def _sector_sum(sim: "EconomySimulation", sector: str, attribute: str) -> float:
    """Stock de fin de mois d'un secteur (entreprises actives)."""
    return sum(
        getattr(company, attribute)
        for company in sim.companies
        if company.active and company.sector == sector
    )


def _flow_sum(operated: list, sector: str, attribute: str) -> float:
    """Flux du mois d'un secteur, y compris les entreprises fermées pendant le mois."""
    return sum(getattr(c, attribute) for c in operated if c.sector == sector)


def _household_financial_services(sim: "EconomySimulation") -> float:
    """Services d'intermédiation consommés par les ménages (SIFIM simplifiés).

    Marge des banques par rapport au taux directeur : sur les prêts aux ménages
    (taux du prêt - taux directeur) et sur leurs dépôts (taux directeur - taux des
    dépôts). Les intérêts payés par les entreprises sont une consommation
    intermédiaire, déjà dans leurs prix ; ceux de la dette publique, un transfert.
    """
    rate = max(0.0, sim.policy_rate)
    loans = sum(
        max(0.0, p.mortgage_rate - rate) * p.mortgage_debt
        + max(0.0, p.consumer_rate - rate) * p.consumer_debt
        for p in sim.people
    )
    deposits = max(0.0, rate - sim.deposit_rate) * sum(max(0.0, p.cash) for p in sim.people)
    return loans + deposits


def _loan_reconciliation_error(sim: "EconomySimulation") -> float:
    """Écart entre les dettes des emprunteurs et les créances inscrites par les banques."""
    booked: dict[str, float] = {}
    for bank in sim.companies:
        if bank.sector == "bank" and bank.active:
            for account, balance in bank.bank_loan_accounts.items():
                booked[account] = booked.get(account, 0.0) + balance
    owed: dict[str, float] = {
        c.id: c.debt for c in sim.companies if c.active and c.sector != "bank" and c.debt > 0.0
    }
    for p in sim.people:
        if p.mortgage_debt > 0.0:
            owed[f"mortgage:{p.id}"] = p.mortgage_debt
        if p.consumer_debt > 0.0:
            owed[f"consumer:{p.id}"] = p.consumer_debt
    return sum(abs(booked.get(key, 0.0) - owed.get(key, 0.0)) for key in set(booked) | set(owed))


def reconciliation_check(sim: "EconomySimulation") -> None:
    """Contrôle exact des prêts existants, sans modifier les bilans.

    Chaque flottant est interprété selon sa valeur binaire exacte. Ce contrôle
    n'est pas encore appelé à la clôture : les calculs existants produisent des
    écarts d'arrondi non nuls et doivent être migrés avant son activation.
    """
    from fractions import Fraction

    booked: dict[str, Fraction] = {}
    for bank in sim.companies:
        if bank.sector == "bank" and bank.active:
            for account, balance in bank.bank_loan_accounts.items():
                booked[account] = booked.get(account, Fraction()) + Fraction(balance)
    owed: dict[str, Fraction] = {
        c.id: Fraction(c.debt) for c in sim.companies
        if c.active and c.sector != "bank" and c.debt > 0.0
    }
    for person in sim.people:
        if person.mortgage_debt > 0.0:
            owed[f"mortgage:{person.id}"] = Fraction(person.mortgage_debt)
        if person.consumer_debt > 0.0:
            owed[f"consumer:{person.id}"] = Fraction(person.consumer_debt)
    for account in sorted(booked.keys() | owed.keys()):
        difference = booked.get(account, Fraction()) - owed.get(account, Fraction())
        if difference:
            raise ArithmeticError(f"Prêt {account} non rapproché : {difference} euro")


def validate_financial_accounts(sim: "EconomySimulation") -> None:
    """Contrôle mensuel des comptes historiques, avec borne d'arrondi explicite.

    N'effectue aucune correction. La tolérance dépend de l'unité représentable
    et du nombre de termes, pas d'un pourcentage économique de l'encours.
    ``reconciliation_check`` reste le contrôle distinct d'égalité exacte.
    """
    claims = {}
    bonds = []
    for company in sim.companies:
        if company.sector == "bank":
            for account, value in company.bank_loan_accounts.items():
                if not math.isfinite(value) or value < 0:
                    raise ArithmeticError(f"Créance invalide : {company.id}/{account}")
                if not company.active and value != 0:
                    raise ArithmeticError(f"Créance dans une banque fermée : {company.id}/{account}")
                claims.setdefault(account, []).append(value)
        bonds.append(company.bond_holdings)
    debts = {c.id: c.debt for c in sim.companies if c.sector != "bank"}
    for person in sim.people:
        debts[f"mortgage:{person.id}"] = person.mortgage_debt
        debts[f"consumer:{person.id}"] = person.consumer_debt
        bonds.append(person.bond_holdings)

    def compare(label, owed, parts):
        if not math.isfinite(owed) or owed < 0 or any(not math.isfinite(v) or v < 0 for v in parts):
            raise ArithmeticError(f"Solde invalide : {label}")
        booked = math.fsum(parts)
        tolerance = 64 * (len(parts) + 1) * math.ulp(max(1.0, owed, booked))
        if abs(booked - owed) > tolerance:
            raise ArithmeticError(f"Compte {label} non rapproché : {booked - owed!r} euro")

    for account in claims.keys() | debts.keys():
        compare(account, debts.get(account, 0.0), claims.get(account, []))
    compare("dette publique", sim.public.debt, bonds)


def build_record(
    sim: "EconomySimulation",
    f: MonthFlows,
    smoothed_investment: float,
    business_investment: float | None = None,
    construction_work: float = 0.0,
    operated: list | None = None,
) -> MarketRecord:
    """Agrège les flux du mois en indicateurs (PIB, secteurs, prix, monnaie).

    PIB par la dépense, aux prix du marché : consommation des ménages (dont loyers
    imputés et services d'intermédiation), dépense publique, investissement brut
    du mois (équipement, R&D, travaux de construction livrés), variation des stocks
    et TVA. Les flux comprennent les entreprises fermées pendant le mois.
    """
    live = sim.live
    resources = sim.resources
    people = sim.people
    companies = sim.companies
    share_prices = {c.id: c.stock_price for c in companies if c.active}
    if operated is None:
        operated = [c for c in companies if c.active or c.exit_month == sim.month]
    if business_investment is None:
        business_investment = smoothed_investment
    total_revenue = sum(c.revenue for c in operated)
    total_profit = sum(c.profit for c in operated)
    bank_interest_income = _flow_sum(operated, "bank", "revenue")
    occupied = sum(p.rental_owner_id is not None for p in people)
    owner_occupied = sum(p.owns_home for p in people)
    household_fisim = _household_financial_services(sim)
    services_price = sim._services_price_index()
    deflator = 100.0 / max(sim.price_index, 1.0)
    # variation des stocks (alimentation, biens), au coût des intrants
    stock_units = (
        sum(c.inventory for c in companies if c.active),
        sum(c.goods_inventory for c in companies if c.active),
    )
    stock_value = (
        stock_units[0] * live.food_input_cost + stock_units[1] * live.goods_input_cost
    ) * resources.input_cost_index
    previous_units, previous_value = getattr(sim, "_previous_stocks", (stock_units, stock_value))
    inventory_change = stock_value - previous_value
    real_inventory_change = (
        (stock_units[0] - previous_units[0]) * live.food_input_cost
        + (stock_units[1] - previous_units[1]) * live.goods_input_cost
    )
    sim._previous_stocks = (stock_units, stock_value)
    consumption = (
        f.food_revenue
        + f.goods_revenue
        + f.household_services
        + resources.household_technology_revenue
        + f.rental_revenue
        + owner_occupied * sim.rent
        + household_fisim
    )
    public_demand = f.public_spent + f.public_wages + f.public_investment
    construction_value_added = construction_work
    nominal_gdp = (
        consumption + public_demand + business_investment + construction_work
        + inventory_change + f.vat_revenue
    )
    recurrent_gdp = nominal_gdp - construction_value_added
    real_consumption_goods = (
        f.sold_food * live.starting_food_price
        + f.sold_goods * live.starting_goods_price
        + f.household_services / max(services_price, 0.01)
    )
    real_gdp = (
        real_consumption_goods
        + (
            f.public_food_units * live.starting_food_price
            + f.public_goods_units * live.starting_goods_price
        )
        + resources.household_technology_units
        * live.tech_unit_price
        * live.technology_household_price_fraction
        + f.rental_revenue * live.starting_rent / max(sim.rent, 1.0)
        + owner_occupied * live.starting_rent
        + household_fisim * deflator
        + business_investment * deflator
        + (f.public_wages + f.public_investment) * deflator
        + construction_value_added * deflator
        + real_inventory_change
        # taxes sur les produits à prix constants : taux de départ × volumes taxés
        + sim.config.vat_rate * real_consumption_goods
    )
    events = [e for e in sim.resolution_events if e.month == sim.month]
    consumer_revenue = f.food_revenue + f.goods_revenue
    active_housing = _sector_sum(sim, "real_estate", "property_inventory")
    household_income = sum(p.income_this_turn for p in people)
    sector_keys = [sector for sector, _ in FIRM_SECTORS]
    return MarketRecord(
        month=sim.period,
        food_price=sim.food_price,
        average_wage=f.average_wage,
        food_sold=f.sold_food,
        unemployment=f.unemployment,
        housing_rent=sim.rent,
        total_revenue=total_revenue,
        total_profit=total_profit,
        event=sim.last_event,
        goods_price=sim.goods_price,
        goods_sold=f.sold_goods,
        unmet_food_demand=f.unmet_food_demand,
        unmet_goods_demand=f.unmet_goods_demand,
        tax_revenue=f.household_taxes + f.corporate_taxes + f.vat_revenue,
        benefits_paid=f.benefits_paid,
        dividends_paid=f.dividends_paid,
        secondary_volume=f.secondary_volume,
        homes_occupied=occupied,
        vacancies=max(0, active_housing - occupied),
        acquisitions=f.acquisitions,
        takeover_bids=f.takeover_bids,
        corporate_bonds=sum(c.bond_holdings for c in companies if c.sector != "bank"),
        corporate_equity=sum(
            q * share_prices.get(cid, 0.0)
            for holder in companies
            for cid, q in holder.shares.items()
        ),
        startups=f.startups,
        bankruptcies=f.bankruptcies,
        bank_reserves=sim.central_bank.reserves,
        bank_liquidity=sim.bank_liquidity,
        deposits=max(0.0, sim.ledger.deposits_from_cash(sim)),
        credit_capacity=sim.credit_capacity(),
        cb_advances=sim.central_bank.advances,
        ledger_residual=sim.ledger.residual(sim),
        deposit_rate=sim.deposit_rate,
        bank_loan_portfolio=sum(c.debt for c in companies if c.active)
        + sum(p.mortgage_debt + p.consumer_debt for p in people),
        bank_equity=_sector_sum(sim, "bank", "bank_equity"),
        bank_interest_income=bank_interest_income,
        housing_construction=f.housing_units_built * live.housing_build_cost,
        resource_market_cash=resources.market_cash,
        employed_count=sum(p.employer_id is not None for p in people),
        household_income=household_income,
        household_spending=sum(p.expenses_this_turn for p in people),
        raw_materials_remaining=resources.raw_materials,
        recycled_materials_remaining=resources.recycled_materials,
        raw_material_price=resources.raw_material_price,
        consumer_price_index=sim.price_index,
        inflation_rate=f.inflation_rate,
        real_average_wage=f.average_wage * 100.0 / max(sim.price_index, 1.0),
        real_household_income=household_income * 100.0 / max(sim.price_index, 1.0),
        nominal_gdp=nominal_gdp,
        real_gdp=real_gdp,
        recurrent_gdp=recurrent_gdp,
        total_gdp=nominal_gdp,
        construction_value_added=construction_value_added,
        smoothed_investment=smoothed_investment,
        government_purchases=f.public_spent,
        public_transfers=f.public_transfers,
        money_issued=f.money_issued,
        money_supply=sim.ledger.nonbank_cash(sim),
        government_balance=sim.government_balance,
        wage_reference=sim.wage_reference,
        public_debt=sim.public.debt,
        public_deficit=sim.public.spending - sim.public.revenue,
        public_spending=sim.public.spending,
        public_revenue=sim.public.revenue,
        public_employment=sim.public.employees,
        tax_adjustment=1.0,  # plus de règle fiscale automatique : champ gardé, constant
        public_capital=sim.public.public_capital,
        public_interest=sim.public.interest_paid,
        government_orientation=sim.government.orientation,
        minimum_wage=live.minimum_wage,
        state_equity_value=sim.public.portfolio_value(companies),
        state_equity_flow=sim.public.equity_invested - sim.public.equity_proceeds,
        state_equity_dividends=sim.public.equity_dividends,
        knowledge_stock=resources.knowledge,
        tfp_index=resources.knowledge_index,
        research_spending=f.research_spending,
        research_intensity=resources.research_intensity,
        productivity_index=sim.productivity_index,
        household_technology_revenue=resources.household_technology_revenue,
        household_technology_units=resources.household_technology_units,
        food_inventory=_sector_sum(sim, "food", "inventory"),
        goods_inventory=_sector_sum(sim, "goods", "goods_inventory"),
        technology_inventory=_sector_sum(sim, "technology", "tech_inventory"),
        food_produced=_sector_sum(sim, "food", "units_produced"),
        goods_produced=_sector_sum(sim, "goods", "units_produced"),
        production_capacity_investment=f.production_capacity_investment,
        new_households=sim.new_households_this_turn,
        population_count=len(people),
        household_tax_revenue=f.household_taxes,
        policy_rate=sim.policy_rate,
        mortgage_debt=sum(p.mortgage_debt for p in people),
        consumer_debt=sum(p.consumer_debt for p in people),
        household_bonds=sum(p.bond_holdings for p in people),
        stock_market_value=sum(
            c.stock_price * c.shares_outstanding for c in companies if c.active
        ),
        foreclosures=f.foreclosures,
        consumer_defaults=f.consumer_defaults,
        keynesian_investment=f.keynesian_investment,
        price_controls=f.price_controls,
        vat_revenue=f.vat_revenue,
        vat_rate=live.vat_rate,
        household_services=f.household_services,
        services_price_index=services_price,
        food_budget_share=(
            f.food_revenue / max(1.0, f.food_revenue + f.goods_revenue + f.household_services)
        ),
        investment=business_investment + construction_work,
        inventory_change=inventory_change,
        household_financial_services=household_fisim,
        loan_reconciliation_error=_loan_reconciliation_error(sim),
        public_debt_reconciliation_error=abs(
            sim.public.debt
            - sum(b.bond_holdings for b in companies if b.active and b.sector == "bank")
            - sum(c.bond_holdings for c in companies if c.sector != "bank")
            - sum(p.bond_holdings for p in people)
        ),
        energy_supply=resources.energy_supply,
        energy_delivered=resources.energy_delivered,
        energy_unmet=resources.energy_unmet,
        construction_completed=f.construction_completed,
        restructurings=sum(e.kind == "restructure" for e in events),
        resolution_public_cost=sum(e.public_cost for e in events),
        resolution_depositor_loss=sum(e.depositor_loss for e in events),
        resolution_creditor_loss=sum(e.creditor_loss for e in events),
        company_prices={
            c.id: c.stock_price if c.active else 0.0 for c in companies
        },
        sector_employment={
            **{s: sum(len(c.employees) for c in companies if c.active and c.sector == s)
               for s in sector_keys},
            "public": sim.public.employees,
        },
        sector_revenue={
            **{s: _flow_sum(operated, s, "revenue") for s in sector_keys},
        },
        sector_profit={s: _flow_sum(operated, s, "profit") for s in sector_keys},
        sector_average_wage={
            **{
                s: _sector_sum(sim, s, "wage_bill")
                / max(1, sum(len(c.employees) for c in companies if c.active and c.sector == s))
                for s in sector_keys
            },
        },
        sector_market_share={
            "food": f.food_revenue / consumer_revenue if consumer_revenue > 0.0 else 0.0,
            "goods": f.goods_revenue / consumer_revenue if consumer_revenue > 0.0 else 0.0,
        },
    )
