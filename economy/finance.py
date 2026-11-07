from __future__ import annotations

import random
from contextlib import nullcontext

from economy import diagnostics
from economy.credit_risk import approved_loan, risk_loan_spread
from economy.models import Company, Person, ResolutionEvent, SimulationConfig
from economy.payments import transfer_cash
from economy.labor import workforce_targets
from economy.institutions import display_name
from economy.sectors import FIRM_SECTORS

_SECTOR_NAMES = {key: label.split(" (")[0] for key, label in FIRM_SECTORS}


def request_operating_credit(
    companies: list[Company],
    people: list[Person],
    bank_reserves: float,
    config: SimulationConfig,
    housing_shortage: int = 0,
    policy_rate: float | None = None,
    credit_multiplier: float = 1.0,
    spread_multiplier: float = 1.0,
    credit_transaction=None,
    credit_capacity=None,
) -> float:
    current_policy_rate = (
        config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate)
    )
    people_by_id = {person.id: person for person in people}
    lent = 0.0
    remaining_housing_shortage = max(0, housing_shortage)
    for company in companies:
        if (
            not company.active
            or company.sector == "bank"
            or bank_reserves <= 0.0
        ):
            continue
        executive = people_by_id.get(company.executive_id or "")
        risk = executive.risk_tolerance if executive else company.risk_tolerance
        experience = min(company.ceo_experience, 10) / 10.0
        leverage_limit = config.maximum_loan_leverage * (0.35 + risk * 0.65)
        operating_reserve = company.target_employees * company.wage_offer * 1.25
        housing_units = (
            min(1, remaining_housing_shortage)
            if company.sector == "real_estate"
            else 0
        )
        # élargir le collatéral aux actifs productifs et technologiques
        collateral = max(
            0.0,
            company.cash
            + company.property_inventory * config.housing_price
            + company.construction_in_progress
            + company.inventory * config.food_stock_price
            + company.goods_inventory * config.goods_stock_price
            + company.productive_capital_value
            + company.technology_asset_value
            + company.bond_holdings,
        )
        collateral_capacity = max(0.0, collateral * leverage_limit - company.debt)
        revenue_capacity = max(
            max(
                2.0 * operating_reserve,
                housing_units * config.housing_build_cost + operating_reserve,
            ),
            company.previous_revenue
            * config.maximum_debt_service_ratio
            / max(current_policy_rate, 0.0001),
        )
        borrowing_capacity = min(
            collateral_capacity,
            max(0.0, revenue_capacity - company.debt),
        ) * credit_multiplier
        operating_gap = max(0.0, operating_reserve - company.cash)
        construction_gap = (
            max(
                0.0,
                housing_units * config.housing_build_cost
                + operating_reserve
                - company.cash,
            )
            if housing_units
            else 0.0
        )
        # besoin d'investissement pour faire face à un sous-emploi de trésorerie
        wage_reserve = company.target_employees * company.wage_offer * 1.5
        cash_buffer_target = wage_reserve + company.debt * 0.02
        investment_need = max(0.0, cash_buffer_target - company.cash)
        # favoriser les entreprises avec demande non satisfaite et faible ratio trésorerie/salaire
        demand_factor = 1.0
        if company.sector in {"food", "goods"}:
            demand_factor = max(1.0, 1.5 - company.cash / max(wage_reserve, 1.0))
        # augmenter la sensibilité au risque pour différencier les prêts
        confidence = max(0.25, min(1.0, 0.45 + experience * 0.3 + risk * 0.35))
        # pénalité pour faible expérience
        risk_adjust = 1.0 + (1.0 - max(0.0, min(1.0, experience))) * 0.15 * risk
        # base loan driven by operating gap and risk, with investment need as supplement
        base_loan = operating_gap * confidence * risk_adjust
        investment_loan = investment_need * demand_factor * 0.3  # réduit pour ne pas capper
        wanted = max(base_loan, construction_gap, investment_loan)
        system = credit_capacity() if credit_capacity is not None else bank_reserves
        loan = min(
            wanted,
            borrowing_capacity,
            bank_reserves,
            system,
        )
        if loan < wanted:
            diagnostics.refusal("credit_exploitation", "garanties_ou_recettes"
                                if borrowing_capacity < min(bank_reserves, system) else "capacite_bancaire")
        if loan <= 0.0:
            if wanted > 0.0:
                diagnostics.refusal("credit_exploitation_issue", "refus")
            continue
        experience = max(0.0, min(1.0, experience))
        risk_spread = spread_multiplier * config.credit_risk_spread * (
            0.5 * max(0.0, min(1.0, risk))
            + 0.5 * (1.0 - experience)
        )
        # la banque juge l'emprunteur sur ce qu'elle observe : prêt réduit ou refusé
        # selon la perte attendue, marge = perte attendue + coût du capital
        approved = approved_loan(company, loan, collateral, config)
        if approved < loan:
            diagnostics.refusal("credit_exploitation", "perte_attendue")
        loan = approved
        if loan <= 0.0:
            diagnostics.refusal("credit_exploitation_issue", "refus")
            continue
        risk_spread = spread_multiplier * risk_loan_spread(
            company, loan, collateral, config, current_policy_rate
        )
        diagnostics.refusal("credit_exploitation_issue", "accord_reduit" if loan < wanted else "accord_complet")
        new_loan_rate = current_policy_rate + risk_spread
        previous_debt_rate = company.debt_interest_rate
        if previous_debt_rate is None:
            previous_debt_rate = config.loan_interest_rate
        with (credit_transaction(company, loan) if credit_transaction else nullcontext()):
            company.debt_interest_rate = (
                (company.debt * previous_debt_rate + loan * new_loan_rate)
                / (company.debt + loan)
            )
            _blend_loan_spread(company, loan, risk_spread)
            company.cash += loan
            company.debt += loan
        bank_reserves -= loan
        lent += loan
        remaining_housing_shortage -= housing_units
    return lent


def extend_credit(
    company: Company,
    amount: float,
    bank_reserves: float,
    config: SimulationConfig,
    policy_rate: float | None = None,
    entrant: bool = False,
    credit_transaction=None,
    credit_capacity=None,
) -> float:
    """Crée un crédit et sa trésorerie, dans les limites de capital et de garantie.

    ``bank_reserves`` est le nom historique de la capacité de crédit disponible.
    Le prêt crée de la monnaie ; il ne prélève pas la trésorerie des banques.
    Dans le moteur, ``credit_transaction`` inscrit la créance avant le dépôt.
    """
    kind = "credit_entree" if entrant else "credit_investissement"
    if amount <= 0.0 or bank_reserves <= 0.0 or company.sector == "bank":
        if amount > 0.0 and company.sector != "bank":
            diagnostics.refusal(kind, "capacite_bancaire")
            diagnostics.refusal(kind + "_issue", "refus")
        return 0.0
    rate = config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate)
    assets = _collateral(company, config)
    # pas de levier de faveur pour les entrants : la banque juge le risque
    capacity = max(
        0.0,
        assets
        * config.maximum_loan_leverage
        * 0.25
        - company.debt,
    )
    system = credit_capacity() if credit_capacity is not None else bank_reserves
    loan = min(amount, bank_reserves, capacity, system)
    if loan < amount:
        diagnostics.refusal(kind, "garanties" if capacity < min(bank_reserves, system) else "capacite_bancaire")
    if loan > 0.0:
        approved = approved_loan(company, loan, assets, config)
        if approved < loan:
            diagnostics.refusal(kind, "perte_attendue")
        loan = approved
    if loan <= 0.0:
        diagnostics.refusal(kind + "_issue", "refus")
        return 0.0
    diagnostics.refusal(kind + "_issue", "accord_reduit" if loan < amount else "accord_complet")
    spread = risk_loan_spread(company, loan, assets, config, rate)
    new_rate = rate + spread
    previous_rate = (
        company.debt_interest_rate
        if company.debt_interest_rate is not None
        else config.loan_interest_rate
    )
    with (credit_transaction(company, loan) if credit_transaction else nullcontext()):
        company.debt_interest_rate = (
            company.debt * previous_rate + loan * new_rate
        ) / (company.debt + loan)
        _blend_loan_spread(company, loan, spread)
        company.cash += loan
        company.debt += loan
    return loan


def _collateral(company: Company, config: SimulationConfig) -> float:
    """Actifs de l'entreprise retenus par la banque comme garantie."""
    return (
        max(0.0, company.cash)
        + company.property_inventory * config.housing_price
        + company.construction_in_progress
        + company.inventory * config.food_stock_price
        + company.goods_inventory * config.goods_stock_price
        + company.productive_capital_value
        + company.technology_asset_value
        # obligations d'État au pair (celles d'une banque relèvent de son bilan propre)
        + (company.bond_holdings if company.sector != "bank" else 0.0)
    )


def investment_loan_spread(company: Company, config: SimulationConfig) -> float:
    """Marge mensuelle d'un prêt d'investissement : elle croît avec l'endettement.

    Risque croissant (Kalecki) : plus l'emprunteur est endetté par rapport à
    ses actifs, plus la banque exige une marge élevée.
    """
    leverage = company.debt / max(_collateral(company, config), 1.0)
    return config.credit_risk_spread * 0.5 + config.credit_leverage_spread * min(2.0, leverage)


def new_loan_rate(
    config: SimulationConfig,
    policy_rate: float | None,
    company: Company | None = None,
) -> float:
    """Taux mensuel d'un nouveau prêt d'investissement (même règle que ``extend_credit``)."""
    rate = config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate)
    if company is None:
        return rate + config.credit_risk_spread * 0.5
    return rate + investment_loan_spread(company, config)


def borrowing_share(
    expected_return: float,
    policy_rate: float | None,
    config: SimulationConfig,
    company: Company | None = None,
) -> float:
    """Part d'un projet financée par emprunt : décision de l'entreprise.

    L'entreprise compare le rendement annuel attendu du projet au coût annuel
    du crédit qu'on lui propose. Plus l'écart est favorable, plus elle s'endette
    (effet de levier) ; un crédit plus cher que le rendement la pousse à
    autofinancer. Aucune référence à l'inflation observée : la politique
    monétaire agit seulement par le prix du crédit.
    """
    spread = expected_return - 12.0 * new_loan_rate(config, policy_rate, company)
    return max(
        0.05,
        min(0.90, config.credit_financed_share + config.credit_return_gain * spread),
    )


def _blend_loan_spread(company: Company, loan: float, spread: float) -> None:
    """Marge moyenne de l'encours après un nouveau prêt (``company.debt`` hors prêt)."""
    total = company.debt + loan
    if total > 0.0:
        company.loan_spread = (company.debt * company.loan_spread + loan * spread) / total


def service_company_debt(
    companies: list[Company],
    config: SimulationConfig,
    policy_rate: float | None = None,
) -> float:
    """Intérêts et amortissement de la dette des entreprises.

    Avec ``variable_rate_loans`` et un ``policy_rate`` fourni, le taux de chaque
    entreprise est révisé chaque mois : taux directeur + marge de ses prêts. La
    politique monétaire se transmet ainsi à tout l'encours, pas seulement aux
    nouveaux prêts.
    """
    cash_received = 0.0
    for company in companies:
        if not company.active or company.debt <= 0.0:
            continue
        if config.variable_rate_loans and policy_rate is not None:
            company.debt_interest_rate = max(0.0, policy_rate) + company.loan_spread
        company.interest_paid_this_turn = 0.0
        company.principal_paid_this_turn = 0.0
        interest_rate = (
            company.debt_interest_rate
            if company.debt_interest_rate is not None
            else config.loan_interest_rate
        )
        interest = company.debt * interest_rate
        interest_paid = min(company.cash, interest)
        company.cash -= interest_paid
        company.expenses += interest
        company.interest_paid_this_turn = interest_paid
        cash_received += interest_paid
        unpaid_interest = interest - interest_paid
        company.debt += unpaid_interest
        free_cash = max(
            0.0,
            company.cash - company.target_employees * company.wage_offer * 1.5,
        )
        # Amortissement contractuel : fraction fixe de l'encours, payée sur la
        # trésorerie libre (le crédit est remboursé progressivement, puis re-prêté).
        principal = min(
            company.debt, company.debt * config.debt_amortization_rate, free_cash
        )
        if principal > 0.0:
            company.debt -= principal
            company.cash -= principal
            company.principal_paid_this_turn = principal
            cash_received += principal
    return cash_received


def sector_entry_signals(
    companies: list[Company],
    config: SimulationConfig,
    wage_reference: float,
    sectors: tuple[str, ...],
    policy_rate: float | None = None,
    unserved: dict[str, tuple[float, float]] | None = None,
    input_cost_index: float = 1.0,
    refused_orders: dict[str, float] | None = None,
) -> dict[str, float]:
    """Attrait d'entrée par secteur (0 = aucun intérêt pour un nouvel entrant).

    ``refused_orders`` (``idle_sector_entry="orders"``) : un secteur dont aucune entreprise
    n'emploie et qui refuse des commandes attire comme un secteur vide (attrait 1) ; sinon des
    coquilles sans recette lui donneraient un attrait nul, sans fin.

    Deux sources, jamais forcées : (1) un rendement des fonds propres des entreprises en
    place au-delà du coût du capital, taux directeur + ``equity_risk_premium`` ; (2) un
    secteur concentré dont l'acteur dominant est très rentable. Un secteur où le
    chiffre d'affaires par entreprise est trop faible pour faire vivre un nouvel
    acteur n'attire personne.

    Commandes refusées visibles (R4) : pour les secteurs de ``unserved``
    (secteur → unités demandées non servies, prix affiché), la recette potentielle
    (ventes + commandes refusées) remplace les ventes dans ce seuil, et une troisième
    source s'ajoute : part non servie de cette recette × marge que l'entrant espère sur
    ses coûts (salaire de référence / production par salarié + intrants au prix des
    fournisseurs), nulle si le prix ne les couvre pas.
    """
    cost_of_capital = (
        12.0 * (config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate))
        + config.equity_risk_premium
    )
    orders = unserved is not None
    signals: dict[str, float] = {}
    for sector in sectors:
        firms = [c for c in companies if c.active and c.sector == sector]
        revenue = sum(c.previous_revenue for c in firms)
        if (refused_orders is not None and refused_orders.get(sector, 0.0) > 0.0
                and not any(c.employees for c in firms)):
            # aucun employeur pour servir des commandes refusées : entrée prioritaire
            signals[sector] = 1.0
            continue
        if orders and sector in unserved and firms:
            signals[sector] = _orders_entry_signal(
                firms, companies, config, wage_reference, revenue, unserved[sector],
                input_cost_index, cost_of_capital,
            )
            continue
        if not firms or revenue <= 0.0:
            # secteur vide : besoin non servi, entrée prioritaire
            signals[sector] = 1.0 if not firms else 0.0
            continue
        if revenue / len(firms) < config.entry_min_revenue_months * wage_reference:
            signals[sector] = 0.0
            continue
        signals[sector] = _incumbent_entry_signal(firms, companies, config, revenue, cost_of_capital)
    return signals


def _orders_entry_signal(
    firms: list[Company],
    companies: list[Company],
    config: SimulationConfig,
    wage_reference: float,
    revenue: float,
    unserved: tuple[float, float],
    input_cost_index: float,
    cost_of_capital: float,
) -> float:
    """Attrait d'un secteur où l'entrepreneur voit les commandes refusées (R4)."""
    units, price = unserved
    unserved_value = max(0.0, units) * max(0.0, price)
    potential = max(0.0, revenue) + unserved_value
    if potential <= 0.0 or potential / len(firms) < config.entry_min_revenue_months * wage_reference:
        return 0.0
    signal = 0.0
    if revenue > 0.0:
        signal = _incumbent_entry_signal(firms, companies, config, revenue, cost_of_capital)
    if unserved_value > 0.0 and price > 0.0:
        sector = firms[0].sector
        productivity = config.food_productivity if sector == "food" else config.goods_productivity
        inputs = (config.food_input_cost if sector == "food" else config.goods_input_cost) * input_cost_index
        entrant_cost = wage_reference / max(productivity, 0.1) + inputs
        margin = max(0.0, 1.0 - entrant_cost / price)
        signal += config.entry_profit_gain * (unserved_value / potential) * margin
    return signal


def _incumbent_entry_signal(
    firms: list[Company],
    companies: list[Company],
    config: SimulationConfig,
    revenue: float,
    cost_of_capital: float,
) -> float:
    """Attrait tiré des entreprises en place : rendement des fonds propres au-delà du coût du
    capital et dominant très rentable dans un secteur concentré."""
    from economy.wealth import company_balance_sheet, share_prices

    prices = share_prices(companies)
    profit = sum(c.previous_profit for c in firms)
    equity = sum(
        max(0.0, company_balance_sheet(c, companies, config, prices)["Capitaux propres"])
        for c in firms
    )
    return_on_equity = 12.0 * profit / equity if equity > 0.0 else 0.0
    margin_signal = max(0.0, return_on_equity - cost_of_capital) * config.entry_profit_gain
    shares = [c.previous_revenue / revenue for c in firms]
    concentration = sum(share * share for share in shares)
    dominant = max(firms, key=lambda c: c.previous_revenue)
    dominant_margin = dominant.previous_profit / max(dominant.previous_revenue, 1.0)
    concentration_signal = (
        config.entry_concentration_gain
        * max(0.0, concentration - config.entry_concentration_reference)
        * max(0.0, dominant_margin)
    )
    return margin_signal + concentration_signal


def _entrant_wage_offer(
    companies: list[Company],
    sector: str,
    wage_reference: float,
    config: SimulationConfig,
) -> float:
    """Offre salariale d'un entrant : le salaire que paient les employeurs du secteur.

    Un entrant qui paie moins que ses concurrents ne recrute personne quand le
    marché du travail est tendu (les salariés ne quittent un poste que pour
    une offre plus élevée) : il resterait une coquille vide. Moyenne des seuls
    employeurs, pondérée par leurs effectifs (une coquille sans salarié n'indique pas le
    salaire du marché) ; sans employeur, la référence salariale observée.
    """
    from economy.business import going_wages  # import local : business importe finance

    sector_firms = [c for c in companies if c.sector == sector]
    if sector_firms:
        return going_wages(sector_firms, wage_reference)[sector]
    return wage_reference


def create_household_startups(
    companies: list[Company],
    people: list[Person],
    month: int,
    bank_reserves: float,
    config: SimulationConfig,
    rng: random.Random,
    housing_shortage: int = 1,
    wage_reference: float | None = None,
    policy_rate: float | None = None,
    credit_transaction=None,
    credit_capacity=None,
    unserved: dict[str, tuple[float, float]] | None = None,
    input_cost_index: float = 1.0,
    market_prices: dict[str, float] | None = None,
    refused_orders: dict[str, float] | None = None,
) -> tuple[list[Company], float, int]:
    """Entrée de nouveaux concurrents, fondés par des ménages entrepreneurs.

    ``unserved`` et ``input_cost_index`` : commandes refusées et prix des intrants
    (``sector_entry_signals``, R4). ``market_prices`` : prix courant de l'alimentation et des
    biens, premier prix de l'entrant de ces secteurs. ``refused_orders`` : commandes refusées
    par secteur, voir ``sector_entry_signals``.

    Seuls les foyers qui ont à la fois le trait entrepreneurial et le capital
    minimal peuvent fonder, et seulement avec une probabilité mensuelle : à
    capital égal, la plupart ne créent rien. Le secteur est tiré au sort selon
    son attrait (rendement au-dessus du coût du capital, concentration + rentabilité
    du dominant). L'entrant apporte son capital et emprunte à une banque (prêt réel,
    sans avantage de faveur) ; il suit ensuite les mêmes règles que les autres et peut
    échouer. Une banque peut aussi être fondée (R3).
    """
    wage_reference = config.starting_wage if wage_reference is None else wage_reference
    created: list[Company] = []
    sectors = ("food", "goods", "technology", "real_estate", "materials", "energy", "services")
    if housing_shortage <= 0 and not config.real_estate_entry_without_shortage:
        # sans pénurie de logements, l'entrée passe par le rachat d'un parc existant
        stock = sum(
            c.property_inventory for c in companies if c.active and c.sector == "real_estate"
        )
        if stock <= 0:
            sectors = tuple(s for s in sectors if s != "real_estate")
    # R3 : une banque peut être fondée, avec les mêmes règles que les autres secteurs
    sectors = sectors + ("bank",)
    targets = workforce_targets(config)
    signals = sector_entry_signals(
        companies, config, wage_reference, sectors, policy_rate, unserved, input_cost_index,
        refused_orders,
    )
    entries_left = config.max_entries_per_month
    # seuils de capital indexés sur le niveau nominal des salaires
    scale = max(1.0, wage_reference / max(config.starting_wage, 1.0))

    for person in sorted(people, key=lambda household: (-household.cash, household.id)):
        if entries_left <= 0:
            break
        if person.entrepreneurial_spirit < config.entrepreneurial_trait_threshold:
            continue
        # un fondateur peut recréer une entreprise s'il est devenu très riche
        # le capital minimal suit partiellement le niveau nominal (la richesse des
        # ménages croît moins vite que les salaires), le capital apporté le suit
        required = (
            config.startup_threshold
            * (scale ** config.startup_threshold_scaling)
            * (3.0 if person.founded_company else 1.0)
        )
        if person.cash < required:
            diagnostics.refusal("entree", "capital_insuffisant")
            continue
        total_signal = sum(max(0.0, v) for v in signals.values())
        probability = config.company_founder_probability * min(
            1.0, total_signal / max(config.entry_signal_scale, 1e-9)
        )
        if rng.random() >= probability:
            diagnostics.refusal("entree", "aucun_secteur_attractif" if total_signal <= 0.0
                                else "pas_de_tentative_ce_mois")
            continue
        # un propriétaire de plusieurs logements est naturellement attiré par la
        # location, à condition que le secteur immobilier soit rentable
        weights = [
            max(0.0, signals.get(s, 0.0))
            * (1.0 + min(person.investment_homes, 5) if s == "real_estate" else 1.0)
            for s in sectors
        ]
        total_weight = sum(weights)
        if total_weight <= 0.0:
            break
        pick = rng.random() * total_weight
        sector = sectors[-1]
        cumulative = 0.0
        for item, weight in zip(sectors, weights):
            cumulative += weight
            if pick <= cumulative:
                sector = item
                break
        contribution = min(
            person.cash * config.startup_investment_fraction,
            config.startup_max_capital * scale,
        )
        if sector == "real_estate":
            # sans pénurie à satisfaire, l'entrant doit pouvoir racheter au moins un
            # logement ; sinon il n'aurait aucune activité (capital immobilisé)
            usable = contribution * (1.0 + config.entrant_credit_multiple) * 0.75
            needed = (
                config.housing_price * (1.0 - config.entrant_housing_discount)
                if housing_shortage <= 0
                else config.housing_build_cost
            )
            if usable < needed and person.investment_homes <= 0:
                diagnostics.refusal("entree", "immobilier_sans_logement_accessible")
                continue
        diagnostics.refusal("entree", f"fondation_{sector}")
        signals[sector] = signals.get(sector, 0.0) * 0.5  # l'attrait baisse après une entrée
        entries_left -= 1
        shares_outstanding = 1000.0
        company = Company(
            id=f"startup_{month}_{len(created) + 1}_{person.id}",
            # nom : secteur et nom de famille d'affichage du fondateur (``display_name``, empreinte
            # de son identifiant, sans tirage), mois de fondation pour distinguer deux homonymes
            name=f"{_SECTOR_NAMES.get(sector, sector)} {display_name(person.id).split(' ', 1)[-1]} ({month})",
            sector=sector,
            cash=contribution,
            wage_offer=_entrant_wage_offer(companies, sector, wage_reference, config),
            target_employees=targets[sector],
            price=(
                market_prices[sector]
                if market_prices is not None and sector in market_prices
                else config.starting_food_price if sector == "food" else config.starting_goods_price
            ),
            stock_price=max(1.0, contribution / shares_outstanding),
            previous_stock_price=max(1.0, contribution / shares_outstanding),
            shares_outstanding=shares_outstanding,
            treasury_shares=0.0,
            productivity_trait=rng.uniform(0.75, 1.35),
            quality_trait=rng.uniform(
                1.0 - config.quality_trait_variance,
                1.0 + config.quality_trait_variance,
            ),
            risk_tolerance=person.risk_tolerance,
            pricing_aggression=rng.random(),
            reinvestment_preference=rng.random(),
            innovation=rng.random(),
            executive_id=person.id,
            ceo_experience=max(1, person.leadership_experience),
            founder_id=person.id,
            construction_pipeline=[],
        )
        public_share_fraction = config.founder_public_share_fraction
        founder_shares = shares_outstanding * (1.0 - public_share_fraction)
        company.treasury_shares = shares_outstanding - founder_shares
        company.active = True
        if sector == "bank":
            # l'apport du fondateur devient les fonds propres de la banque (transfert : le
            # dépôt du fondateur disparaît, les fonds propres bancaires augmentent d'autant)
            company.bank_equity = contribution
        # crédit d'entrée : prêt bancaire réel (réserves des banques)
        if config.entrant_credit_multiple > 0.0 and bank_reserves > 0.0:
            loan = extend_credit(
                company,
                contribution * config.entrant_credit_multiple,
                bank_reserves,
                config,
                policy_rate,
                entrant=True,
                credit_transaction=credit_transaction,
                credit_capacity=credit_capacity,
            )
            bank_reserves -= loan
        # Ne pas débiter le fondateur si l'inscription bancaire échoue.
        person.cash -= contribution
        person.founded_company = True
        person.shares[company.id] = founder_shares
        if sector == "real_estate":
            # le fondateur apporte en nature les logements qu'il détenait : pour
            # louer, un ménage doit passer par une entreprise immobilière
            if person.investment_homes > 0:
                company.property_inventory += person.investment_homes
                company.stock_price = max(
                    1.0,
                    (contribution + person.investment_homes * config.housing_price)
                    / shares_outstanding,
                )
                person.investment_homes = 0
            _buy_existing_housing(company, companies, config)
        created.append(company)

    return created, bank_reserves, len(created)


def _buy_existing_housing(
    entrant: Company,
    companies: list[Company],
    config: SimulationConfig,
) -> int:
    """Un nouvel acteur immobilier rachète une partie du parc d'un incumbent.

    Transaction réelle : l'entrant paie au prix du marché (avec un escompte de
    volume), le vendeur encaisse. Le rachat est plafonné à une fraction du parc
    du plus gros détenteur et par la trésorerie de l'entrant.
    """
    holders = sorted(
        (c for c in companies if c.active and c.sector == "real_estate" and c.property_inventory > 0),
        key=lambda c: -c.property_inventory,
    )
    bought = 0
    unit_price = config.housing_price * (1.0 - config.entrant_housing_discount)
    for holder in holders:
        if unit_price <= 0.0:
            break
        reserve = entrant.cash * 0.25
        affordable = int(max(0.0, entrant.cash - reserve) // unit_price)
        available = int(holder.property_inventory * config.entrant_housing_max_share)
        units = min(affordable, available)
        if units <= 0:
            continue
        payment = units * unit_price
        transfer_cash(entrant, holder, payment)
        holder.revenue += payment
        holder.expenses += units * config.housing_build_cost  # coût d'origine du bien cédé
        holder.property_cost_sold_this_turn += units * config.housing_build_cost
        holder.property_inventory -= units
        entrant.property_inventory += units
        bought += units
    return bought


def auction_assets(
    company: Company,
    survivors: list[Company],
    config: SimulationConfig,
) -> float:
    """Vend aux enchères les actifs d'une entreprise en faillite à un concurrent.

    L'acheteur est l'entreprise du secteur la mieux dotée en trésorerie ; il paie
    la valeur comptable moins ``asset_auction_discount``, dans la limite de sa
    trésorerie libre (au-delà de son fonds de roulement). Le produit entre dans la
    trésorerie de l'entreprise liquidée et rembourse d'abord les banques. La part
    des actifs productifs non payée est détruite ; le parc de logements et les
    chantiers passent en entier au repreneur (ils ne disparaissent pas). Retourne
    le montant payé.
    """
    if not survivors or company.sector == "bank":
        return 0.0
    buyer = max(survivors, key=lambda other: (other.cash, other.id))
    discount = 1.0 - config.asset_auction_discount
    housing_value = (
        company.property_inventory * config.housing_price + company.construction_in_progress
    )
    productive_value = (
        company.productive_capital_value
        + company.technology_asset_value
        + company.inventory * config.food_stock_price
        + company.goods_inventory * config.goods_stock_price
    )
    # le repreneur garde son fonds de roulement (salaires, intrants, intérêts) :
    # ``working_capital_months`` mois de chiffre d'affaires, au moins 1,5 mois de salaires
    working_capital = max(
        buyer.target_employees * buyer.wage_offer * 1.5,
        config.working_capital_months * max(buyer.previous_revenue, buyer.revenue_ema),
    )
    budget = max(0.0, buyer.cash - working_capital)
    paid = min(budget, (housing_value + productive_value) * discount)
    if paid <= 0.0 and company.property_inventory <= 0 and not company.construction_pipeline:
        return 0.0
    transfer_cash(buyer, company, paid)
    # actifs existants : un transfert, pas un investissement (hors PIB)
    buyer.secondary_asset_purchases += paid
    # logements et chantiers : repris en entier (le parc ne doit pas disparaître)
    buyer.property_inventory += company.property_inventory
    buyer.construction_in_progress += company.construction_in_progress
    buyer.construction_pipeline.extend(company.construction_pipeline)
    company.property_inventory = 0
    company.construction_in_progress = 0.0
    company.construction_pipeline = []
    # actifs productifs : la fraction payée change de mains
    productive_paid = max(0.0, paid - housing_value * discount)
    fraction = (
        min(1.0, productive_paid / (productive_value * discount))
        if productive_value > 0.0
        else 0.0
    )
    if fraction > 0.0:
        buyer.production_capacity += company.production_capacity * fraction
        buyer.energy_capacity += company.energy_capacity * fraction
        buyer.productive_capital_value += company.productive_capital_value * fraction
        buyer.technology_asset_value += company.technology_asset_value * fraction
        buyer.tech_level = min(10.0, buyer.tech_level + company.tech_level * fraction * 0.15)
        buyer.inventory += company.inventory * fraction
        buyer.goods_inventory += company.goods_inventory * fraction
    return paid


def can_dissolve(company: Company) -> bool:
    """Une liquidation amiable (``dissolve_company``) n'est possible que pour une entreprise sans
    dette ni actif physique ou obligataire : seule sa trésorerie (et ses titres d'autres
    entreprises) revient aux actionnaires, sans vente aux enchères ni créancier à rembourser."""
    return (
        company.active
        and company.sector != "bank"
        and not company.employees
        and abs(company.debt) <= 1e-9
        and not any(abs(v) > 1e-9 for v in company.bank_loan_accounts.values())
        and company.cash >= 0.0
        and company.bond_holdings <= 1e-9
        and company.property_inventory <= 0
        and company.construction_in_progress <= 1e-9
        and not company.construction_pipeline
        and company.inventory <= 1e-9
        and company.goods_inventory <= 1e-9
        and company.production_capacity <= 1e-6
        and company.energy_capacity <= 1e-6
    )


def dissolve_company(
    company: Company,
    people: list[Person],
    companies: list[Company],
    extra_holders: tuple = (),
    month: int = 0,
    events: list | None = None,
) -> float:
    """Liquidation amiable (idée 2, novembre 2026) : les actionnaires ferment une entreprise
    restée sans salarié, sans perspective de reprise (``EconomySimulation._dissolve_shells``).

    Aucune monnaie n'est créée ni détruite : la trésorerie est versée aux actionnaires au prorata
    de leurs titres (boni de liquidation ; sans actionnaire extérieur, au fondateur, sinon à
    parts égales aux ménages, comme ``resolve_distressed_companies``) ; les titres qu'elle détient
    d'autres entreprises leur reviennent de même (sans actionnaire, à leur émetteur). Les valeurs
    comptables du capital technologique et productif sont passées en perte (aucun acheteur).
    Retourne le boni versé. L'appelant vérifie ``can_dissolve``."""
    holders = [
        holder for holder in [*people, *companies, *extra_holders]
        if holder.id != company.id and holder.shares.get(company.id, 0.0) > 0.0
    ]
    shares_held = sum(holder.shares[company.id] for holder in holders)
    boni = max(0.0, company.cash)
    if boni > 0.0 and shares_held > 0.0:
        for holder in holders:
            holder.cash += boni * holder.shares[company.id] / shares_held
    elif boni > 0.0 and people:
        founder = next((p for p in people if p.id == company.founder_id), None)
        recipients = [founder] if founder is not None else people
        for person in recipients:
            person.cash += boni / len(recipients)
    for asset_company_id, asset_shares in company.shares.items():
        if asset_company_id == company.id or asset_shares <= 0.0:
            continue
        if shares_held > 0.0:
            for holder in holders:
                part = asset_shares * holder.shares[company.id] / shares_held
                if holder.id == asset_company_id and isinstance(holder, Company):
                    holder.treasury_shares += part
                else:
                    holder.shares[asset_company_id] = holder.shares.get(asset_company_id, 0.0) + part
        else:
            issuer = next((c for c in companies if c.id == asset_company_id and c.active), None)
            if issuer is not None:
                issuer.treasury_shares += asset_shares
    company.active = False
    company.exit_reason = "dissolved"
    company.exit_month = month
    if events is not None:
        events.append(ResolutionEvent(month, company.id, "dissolution"))
    company.cash = 0.0
    company.productive_capital_value = 0.0
    company.technology_asset_value = 0.0
    company.tech_level = 0.0
    company.stock_price = 0.0
    company.shares_outstanding = 0.0
    company.treasury_shares = 0.0
    for person in people:
        person.shares.pop(company.id, None)
    for other in [*companies, *extra_holders]:
        if other.id != company.id:
            other.shares.pop(company.id, None)
    company.shares.clear()
    return boni


def resolve_distressed_companies(
    companies: list[Company],
    people: list[Person],
    bank_reserves: float,
    config: SimulationConfig,
    rng: random.Random,
    extra_holders: tuple = (),
    month: int = 0,
    events: list | None = None,
    wind_down_bank=None,
) -> tuple[float, int]:
    """Faillites : liquidation (actifs vendus aux enchères), y compris du dernier acteur d'un
    secteur (le secteur disparaît alors jusqu'à l'arrivée d'un entrant) ; seules l'insolvabilité
    et la cessation de paiements ferment une entreprise. Chaque résolution est consignée dans
    ``events``.

    Une banque n'est jamais liquidée ici : ses prêts, titres et positions interbancaires
    n'ont pas d'acheteur aux enchères. Insolvable, elle est confiée à ``wind_down_bank``
    (résolution bancaire, qui transfère son bilan à une concurrente) ; sans repreneur, elle
    reste active. Elle n'est pas comptée parmi les faillites d'emprunteurs."""
    defaults = 0
    prices = {c.id: c.stock_price for c in companies if c.active}
    for company in companies:
        if not company.active:
            continue
        company.loss_streak = company.loss_streak + 1 if company.profit < 0.0 else 0
        assets = (
            company.cash
            + company.property_inventory * config.housing_price
            + company.construction_in_progress
            + company.inventory * config.food_stock_price
            + company.goods_inventory * config.goods_stock_price
            + company.productive_capital_value
            + company.technology_asset_value
        )
        if company.sector != "bank":
            # portefeuille financier : obligations au pair, actions au cours
            assets += company.bond_holdings + sum(
                q * prices.get(cid, 0.0) for cid, q in company.shares.items()
            )
        # cessation de paiements : endettée, sans trésorerie et en pertes depuis
        # ``bankruptcy_months`` mois, l'entreprise ne peut plus payer salaires ni
        # intrants, même si ses machines (au coût d'achat) couvrent sa dette
        illiquid = (
            company.debt > 0.0
            and company.cash <= company.wage_offer
            and company.loss_streak >= config.bankruptcy_months
        )
        insolvent = company.debt > assets or illiquid
        if not insolvent:
            continue
        survivors = [
            other
            for other in companies
            if other.active and other.id != company.id and other.sector == company.sector
        ]
        if company.sector == "bank":
            if survivors and wind_down_bank is not None:
                wind_down_bank(company)
            continue
        # liquidation, y compris du dernier acteur : le secteur disparaît alors jusqu'à
        # l'arrivée d'un entrant
        auction_assets(company, survivors, config)
        banks = [b for b in companies if b.active and b.sector == "bank"]
        if company.bond_holdings > 0.0 and banks:
            # obligations revendues aux banques au pair (le rachat crée le dépôt),
            # avant de rembourser les créanciers
            for bank in banks:
                bank.bond_holdings += company.bond_holdings / len(banks)
            company.cash += company.bond_holdings
            company.bond_holdings = 0.0

        recovered = min(company.cash, company.debt)
        bank_reserves += recovered
        company.default_loss += max(0.0, company.debt - recovered)
        residual_equity = max(0.0, company.cash - recovered)
        holders = [
            holder for holder in [*people, *companies, *extra_holders]
            if holder.id != company.id and holder.shares.get(company.id, 0.0) > 0.0
        ]
        shares_held = sum(holder.shares[company.id] for holder in holders)
        if residual_equity > 0.0 and shares_held > 0.0:
            for holder in holders:
                holder.cash += residual_equity * holder.shares[company.id] / shares_held
        elif residual_equity > 0.0 and people:
            # aucun actionnaire extérieur (titres rachetés) : le boni de liquidation
            # revient au fondateur, sinon aux ménages ; sans cela la monnaie disparaîtrait
            founder = next((p for p in people if p.id == company.founder_id), None)
            recipients = [founder] if founder is not None else people
            for person in recipients:
                person.cash += residual_equity / len(recipients)
        # les actions détenues ne reviennent aux actionnaires que si les créanciers
        # sont remboursés ; sinon elles retournent en trésorerie de leur émetteur
        # (une banque créancière ne détient pas d'actions)
        if shares_held > 0.0 and recovered >= company.debt:
            for asset_company_id, asset_shares in company.shares.items():
                if asset_company_id == company.id:
                    continue
                for holder in holders:
                    transferred_shares = (
                        asset_shares * holder.shares[company.id] / shares_held
                    )
                    if holder.id == asset_company_id and isinstance(holder, Company):
                        holder.treasury_shares += transferred_shares
                    else:
                        holder.shares[asset_company_id] = (
                            holder.shares.get(asset_company_id, 0.0)
                            + transferred_shares
                        )
        else:
            for asset_company_id, asset_shares in company.shares.items():
                issuer = next(
                    (
                        candidate
                        for candidate in companies
                        if candidate.id == asset_company_id and candidate.active
                    ),
                    None,
                )
                if issuer is not None:
                    issuer.treasury_shares += asset_shares
        company.active = False
        company.exit_reason = "bankrupt"
        company.exit_month = month
        if events is not None:
            events.append(ResolutionEvent(
                month, company.id, "liquidation",
                debt_written_off=max(0.0, company.debt - recovered),
            ))
        company.cash = 0.0
        company.debt = 0.0
        company.inventory = 0.0
        company.goods_inventory = 0.0
        company.property_inventory = 0
        company.production_capacity = 0.0
        company.energy_capacity = 0.0
        company.productive_capital_value = 0.0
        company.technology_asset_value = 0.0
        company.tech_level = 0.0
        company.construction_in_progress = 0.0
        company.construction_pipeline.clear()
        company.stock_price = 0.0
        company.shares_outstanding = 0.0
        company.treasury_shares = 0.0
        for person in people:
            if person.employer_id == company.id:
                person.employer_id = None
                person.employer_tenure = 0
            person.shares.pop(company.id, None)
        for other in [*companies, *extra_holders]:
            if other.id != company.id:
                other.shares.pop(company.id, None)
        company.shares.clear()
        defaults += 1
    return bank_reserves, defaults
