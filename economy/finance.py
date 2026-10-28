from __future__ import annotations

import random

from economy.models import Company, Person, SimulationConfig
from economy.labor import workforce_targets


def request_operating_credit(
    companies: list[Company],
    people: list[Person],
    bank_reserves: float,
    config: SimulationConfig,
    housing_shortage: int = 0,
    policy_rate: float | None = None,
    credit_multiplier: float = 1.0,
    spread_multiplier: float = 1.0,
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
        collateral = max(
            0.0,
            company.cash
            + company.property_inventory * config.housing_price
            + company.construction_in_progress
            + company.inventory * config.starting_food_price
            + company.goods_inventory * config.starting_goods_price,
        )
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
            + company.inventory * config.starting_food_price
            + company.goods_inventory * config.starting_goods_price
            + company.productive_capital_value
            + company.technology_asset_value,
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
        loan = min(
            max(base_loan, construction_gap, investment_loan),
            borrowing_capacity,
            bank_reserves,
        )
        if loan <= 0.0:
            continue
        experience = max(0.0, min(1.0, experience))
        risk_spread = spread_multiplier * config.credit_risk_spread * (
            0.5 * max(0.0, min(1.0, risk))
            + 0.5 * (1.0 - experience)
        )
        new_loan_rate = current_policy_rate + risk_spread
        previous_debt_rate = company.debt_interest_rate
        if previous_debt_rate is None:
            previous_debt_rate = config.loan_interest_rate
        company.debt_interest_rate = (
            (company.debt * previous_debt_rate + loan * new_loan_rate)
            / (company.debt + loan)
        )
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
) -> float:
    """Prête ``amount`` à ``company`` sur les réserves bancaires (transfert).

    Plafonné par les réserves disponibles et par un levier sur les actifs de
    l'emprunteur. La trésorerie de l'entreprise augmente exactement de ce que
    les banques perdent (aucune création monétaire). Retourne le montant prêté.
    """
    if amount <= 0.0 or bank_reserves <= 0.0 or company.sector == "bank":
        return 0.0
    rate = config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate)
    assets = (
        max(0.0, company.cash)
        + company.property_inventory * config.housing_price
        + company.construction_in_progress
        + company.inventory * config.starting_food_price
        + company.goods_inventory * config.starting_goods_price
        + company.productive_capital_value
        + company.technology_asset_value
    )
    capacity = max(
        0.0,
        assets
        * config.maximum_loan_leverage
        * 0.25
        * (config.entrant_credit_leverage_boost if entrant else 1.0)
        - company.debt,
    )
    loan = min(amount, bank_reserves, capacity)
    if loan <= 0.0:
        return 0.0
    new_rate = rate + config.credit_risk_spread * 0.5
    previous_rate = (
        company.debt_interest_rate
        if company.debt_interest_rate is not None
        else config.loan_interest_rate
    )
    company.debt_interest_rate = (
        company.debt * previous_rate + loan * new_rate
    ) / (company.debt + loan)
    company.cash += loan
    company.debt += loan
    return loan


def service_company_debt(
    companies: list[Company],
    config: SimulationConfig,
) -> float:
    cash_received = 0.0
    for company in companies:
        if not company.active or company.debt <= 0.0:
            continue
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
) -> dict[str, float]:
    """Attrait d'entrée par secteur (0 = aucun intérêt pour un nouvel entrant).

    Deux sources, jamais forcées : (1) des marges supérieures à la cible
    sectorielle ; (2) un secteur concentré dont l'acteur dominant est très
    rentable. Un secteur où le chiffre d'affaires par entreprise est trop faible
    pour faire vivre un nouvel acteur n'attire personne.
    """
    signals: dict[str, float] = {}
    for sector in sectors:
        firms = [c for c in companies if c.active and c.sector == sector]
        target = config.sector_margin_target.get(sector, 0.1)
        revenue = sum(c.previous_revenue for c in firms)
        if not firms or revenue <= 0.0:
            # secteur vide : besoin non servi, entrée prioritaire
            signals[sector] = 1.0 if not firms else 0.0
            continue
        if revenue / len(firms) < config.entry_min_revenue_months * wage_reference:
            signals[sector] = 0.0
            continue
        profit = sum(c.previous_profit for c in firms)
        margin_signal = max(0.0, profit / revenue - target) * config.entry_profit_gain
        shares = [c.previous_revenue / revenue for c in firms]
        concentration = sum(share * share for share in shares)
        dominant = max(firms, key=lambda c: c.previous_revenue)
        dominant_margin = dominant.previous_profit / max(dominant.previous_revenue, 1.0)
        concentration_signal = (
            config.entry_concentration_gain
            * max(0.0, concentration - config.entry_concentration_reference)
            * max(0.0, dominant_margin)
        )
        signals[sector] = margin_signal + concentration_signal
    return signals


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
) -> tuple[list[Company], float, int]:
    """Entrée de nouveaux concurrents, fondés par des ménages entrepreneurs.

    Seuls les foyers qui ont à la fois le trait entrepreneurial et le capital
    minimal peuvent fonder, et seulement avec une probabilité mensuelle : à
    capital égal, la plupart ne créent rien. Le secteur est tiré au sort selon
    son attrait (marges au-dessus de la cible, concentration + rentabilité du
    dominant). L'entrant reçoit un avantage temporaire (crédit facilité,
    clientèle de niche) financé par le capital du fondateur et par un prêt
    bancaire réel ; ensuite il suit les mêmes règles que les autres et peut
    échouer.
    """
    wage_reference = config.starting_wage if wage_reference is None else wage_reference
    created: list[Company] = []
    sectors = ("food", "goods", "technology", "real_estate", "materials", "energy")
    if housing_shortage <= 0 and not config.real_estate_entry_without_shortage:
        # sans pénurie de logements, l'entrée passe par le rachat d'un parc existant
        stock = sum(
            c.property_inventory for c in companies if c.active and c.sector == "real_estate"
        )
        if stock <= 0:
            sectors = tuple(s for s in sectors if s != "real_estate")
    targets = workforce_targets(config)
    signals = sector_entry_signals(companies, config, wage_reference, sectors)
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
            continue
        total_signal = sum(max(0.0, v) for v in signals.values())
        probability = config.company_founder_probability * min(
            1.0, total_signal / max(config.entry_signal_scale, 1e-9)
        )
        if rng.random() >= probability:
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
                continue
        signals[sector] = signals.get(sector, 0.0) * 0.5  # l'attrait baisse après une entrée
        entries_left -= 1
        person.cash -= contribution
        person.founded_company = True
        shares_outstanding = 1000.0
        company = Company(
            id=f"startup_{month}_{len(created) + 1}_{person.id}",
            name=f"Nouvelle {sector} {month}-{len(created) + 1}",
            sector=sector,
            cash=contribution,
            wage_offer=wage_reference
            * 0.8
            * config.sector_wage_factor.get(sector, 1.0),
            target_employees=targets[sector],
            price=config.starting_food_price if sector == "food" else config.starting_goods_price,
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
            entry_boost=1.0 + config.entrant_niche_bonus,
        )
        public_share_fraction = config.founder_public_share_fraction
        founder_shares = shares_outstanding * (1.0 - public_share_fraction)
        company.treasury_shares = shares_outstanding - founder_shares
        person.shares[company.id] = founder_shares
        company.active = True
        # crédit d'entrée : prêt bancaire réel (réserves des banques)
        if config.entrant_credit_multiple > 0.0 and bank_reserves > 0.0:
            loan = extend_credit(
                company,
                contribution * config.entrant_credit_multiple,
                bank_reserves,
                config,
                policy_rate,
                entrant=True,
            )
            bank_reserves -= loan
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
        entrant.cash -= payment
        holder.cash += payment
        holder.revenue += payment
        holder.expenses += units * config.housing_build_cost  # coût d'origine du bien cédé
        holder.property_cost_sold_this_turn += units * config.housing_build_cost
        holder.property_inventory -= units
        entrant.property_inventory += units
        bought += units
    return bought


def resolve_distressed_companies(
    companies: list[Company],
    people: list[Person],
    bank_reserves: float,
    config: SimulationConfig,
    rng: random.Random,
) -> tuple[float, int]:
    defaults = 0
    for company in companies:
        if not company.active:
            continue
        company.loss_streak = company.loss_streak + 1 if company.profit < 0.0 else 0
        assets = (
            company.cash
            + company.property_inventory * config.housing_price
            + company.construction_in_progress
            + company.inventory * config.starting_food_price
            + company.goods_inventory * config.starting_goods_price
            + company.productive_capital_value
            + company.technology_asset_value
        )
        insolvent = company.debt > assets
        startup_failure = (
            company.founder_id is not None
            and company.age_months <= max(24, config.bankruptcy_months)
            and company.loss_streak >= config.bankruptcy_months
            and rng.random()
            < config.startup_failure_probability / config.bankruptcy_months
        )
        if not insolvent and not startup_failure:
            continue
        # Dernier acteur d'un secteur essentiel : restructuré plutôt que liquidé
        # (la dette excédentaire est passée en pertes pour les banques) ; sinon
        # le secteur disparaît (plus de logements, plus d'extraction) et
        # l'économie s'effondre.
        survivors = [
            other
            for other in companies
            if other.active and other.id != company.id and other.sector == company.sector
        ]
        if not survivors:
            if company.debt > 0.0:
                company.default_loss += company.debt
                company.debt = 0.0
            company.loss_streak = 0
            continue
        if company.sector == "real_estate":
            heir = max(survivors, key=lambda other: other.cash)
            heir.property_inventory += company.property_inventory
            heir.construction_in_progress += company.construction_in_progress
            heir.construction_pipeline.extend(company.construction_pipeline)
            company.property_inventory = 0
            company.construction_in_progress = 0.0
            company.construction_pipeline = []

        recovered = min(company.cash, company.debt)
        bank_reserves += recovered
        company.default_loss += max(0.0, company.debt - recovered)
        residual_equity = max(0.0, company.cash - recovered)
        holders = [
            holder for holder in [*people, *companies]
            if holder.id != company.id and holder.shares.get(company.id, 0.0) > 0.0
        ]
        shares_held = sum(holder.shares[company.id] for holder in holders)
        if residual_equity > 0.0 and shares_held > 0.0:
            for holder in holders:
                holder.cash += residual_equity * holder.shares[company.id] / shares_held
        if shares_held > 0.0:
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
        company.cash = 0.0
        company.debt = 0.0
        company.inventory = 0.0
        company.goods_inventory = 0.0
        company.property_inventory = 0
        company.production_capacity = 0.0
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
        for other in companies:
            if other.id != company.id:
                other.shares.pop(company.id, None)
        company.shares.clear()
        defaults += 1
    return bank_reserves, defaults
