from __future__ import annotations

from economy.models import Company, Person, SimulationConfig
from economy.payments import transfer_cash
from economy.stock_market import _fundamental_price
from economy.treasury import working_capital

# délai d'intégration entre deux opérations d'un même acquéreur (mois)
INTEGRATION_MONTHS = 6


def _holder_valuation(holder: Person | Company, base: float, config: SimulationConfig) -> float:
    """Estimation par un actionnaire de la valeur d'une action visée par une offre.

    Un ménage part du plus haut du cours et de la valeur fondamentale, corrigé
    selon son goût du risque (les plus audacieux attendent davantage) ; l'État et
    les entreprises s'en tiennent à cette base.
    """
    if isinstance(holder, Person):
        return base * (1.0 + config.takeover_valuation_spread * (holder.risk_tolerance - 0.5))
    return base


def takeover_by_value(
    companies: list[Company],
    people: list[Person],
    config: SimulationConfig,
    month: int,
    extra_holders: tuple = (),
    policy_rate: float | None = None,
    locked: tuple = (),
    excluded: frozenset = frozenset(),
) -> tuple[int, int]:
    """Prises de contrôle décidées par la valeur (seul mode de rachat depuis le 5 novembre 2026).

    Les porteurs de ``locked`` (l'État) n'apportent jamais leurs titres (ils ne cèdent que par
    décision de privatisation) ; les entreprises de ``excluded`` (sous contrôle public) ne
    lancent ni ne reçoivent d'offre.

    Une entreprise non bancaire évalue chaque concurrente de son secteur : valeur
    fondamentale par action. Elle lance une offre au cours
    × (1 + ``control_premium``) si cette valeur dépasse le prix offert et si sa
    trésorerie libre paie les titres manquants pour atteindre ``control_threshold``
    des actions en circulation ; elle choisit la cible où le surplus attendu est le
    plus grand. Les actionnaires apportent leurs titres si le prix offert dépasse
    leur propre estimation. Seuil atteint : l'acquéreur achète les titres manquants
    au prorata des apports et absorbe la cible (``_merge_into``) ; sinon l'offre
    échoue sans échange. Retourne (rachats réussis, offres lancées).
    """
    annual_rate = 12.0 * (config.loan_interest_rate if policy_rate is None else max(0.0, policy_rate))
    holders = [*people, *companies, *extra_holders]
    fundamentals: dict[str, float] = {}

    def fundamental(company: Company) -> float:
        if company.id not in fundamentals:
            fundamentals[company.id] = _fundamental_price(company, companies, config, annual_rate)
        return fundamentals[company.id]

    completed = bids = 0
    for buyer in sorted(companies, key=lambda company: company.cash, reverse=True):
        if not buyer.active or buyer.sector == "bank" or buyer.id in excluded:
            continue
        if month - buyer.last_acquisition_month < INTEGRATION_MONTHS:
            continue
        free_cash = buyer.cash - working_capital(buyer, config)
        sector_firms = [c for c in companies if c.active and c.sector == buyer.sector]
        if len(sector_firms) <= max(1, config.min_firms_per_sector):
            continue
        best = None
        for target in sector_firms:
            circulating = target.shares_outstanding - target.treasury_shares
            if target is buyer or circulating <= 0.0 or target.stock_price <= 0.0 or target.id in excluded:
                continue
            held = buyer.shares.get(target.id, 0.0)
            need = max(0.0, config.control_threshold * circulating - held)
            offer = target.stock_price * (1.0 + config.control_premium)
            if need * offer > max(0.0, free_cash):
                continue
            # valeur fondamentale par action (sans marge visée, une fusion ne promet aucun
            # gain de pouvoir de prix)
            value = fundamental(target)
            # déjà au-delà du seuil : l'absorption vaut d'être faite si la valeur dépasse le cours
            price = offer if need > 0.0 else target.stock_price
            surplus = (value - price) * (need if need > 0.0 else circulating - held)
            if value > price and (best is None or surplus > best[0]):
                best = (surplus, target, need, offer, circulating)
        if best is None:
            continue
        _, target, need, offer, circulating = best
        if need > 0.0:
            bids += 1
            base = max(target.stock_price, fundamental(target))
            tendered = [
                (holder, holder.shares[target.id])
                for holder in holders
                if holder is not buyer and holder is not target
                and holder.shares.get(target.id, 0.0) > 0.0
                and offer >= _holder_valuation(holder, base, config)
                and not any(holder is locked_holder for locked_holder in locked)
            ]
            supply = sum(q for _, q in tendered)
            if supply < need:
                # offre ratée : pas d'échange, l'acquéreur attend avant une nouvelle offre
                buyer.last_acquisition_month = month
                continue
            for holder, quantity in tendered:
                bought = need * quantity / supply
                remaining = holder.shares[target.id] - bought
                if remaining > 1e-9:
                    holder.shares[target.id] = remaining
                else:
                    holder.shares.pop(target.id, None)
                transfer_cash(buyer, holder, bought * offer)
                buyer.shares[target.id] = buyer.shares.get(target.id, 0.0) + bought
            # achat de titres existants : un transfert, pas un investissement (hors PIB)
            buyer.secondary_asset_purchases += need * offer
        _merge_into(buyer, target, people, companies, extra_holders)
        target.exit_month = month
        buyer.last_acquisition_month = month
        buyer.acquisition_count += 1
        completed += 1
    return completed, bids


def _transfer_interbank_position(old: Company, new: Company, companies: list[Company]) -> None:
    """Créances et dettes interbancaires de ``old`` reprises par ``new``.

    Une créance de ``new`` sur ``old`` (ou l'inverse) s'éteint : le repreneur se
    doit à lui-même.
    """
    for borrower_id, amount in old.interbank_claims.items():
        if borrower_id != new.id:
            new.interbank_claims[borrower_id] = new.interbank_claims.get(borrower_id, 0.0) + amount
    old.interbank_claims.clear()
    new.interbank_claims.pop(old.id, None)
    for lender in companies:
        if lender is new or lender is old:
            continue
        owed = lender.interbank_claims.pop(old.id, 0.0)
        if owed > 0.0:
            lender.interbank_claims[new.id] = lender.interbank_claims.get(new.id, 0.0) + owed


def _merge_into(
    buyer: Company,
    target: Company,
    people: list[Person],
    companies: list[Company],
    extra_holders: tuple = (),
) -> None:
    issued_target_shares = sum(
        holder.shares.get(target.id, 0.0)
        for holder in [*people, *companies, *extra_holders]
        if holder.id not in (target.id, buyer.id)
    )
    exchange_ratio = target.stock_price / max(buyer.stock_price, 1.0)
    new_shares = issued_target_shares * exchange_ratio
    buyer.shares_outstanding += new_shares

    buyer.cash += target.cash
    buyer.bond_holdings += target.bond_holdings
    target.bond_holdings = 0.0
    total_debt = buyer.debt + target.debt
    if total_debt > 0.0:
        # marge et taux moyens de l'encours fusionné
        buyer.loan_spread = (
            buyer.debt * buyer.loan_spread + target.debt * target.loan_spread
        ) / total_debt
        if target.debt_interest_rate is not None:
            buyer.debt_interest_rate = (
                buyer.debt * (buyer.debt_interest_rate or 0.0)
                + target.debt * target.debt_interest_rate
            ) / total_debt
    buyer.debt = total_debt
    buyer.bank_equity += target.bank_equity
    if buyer.sector == "bank" and target.sector == "bank":
        for borrower_id, balance in target.bank_loan_accounts.items():
            buyer.bank_loan_accounts[borrower_id] = (
                buyer.bank_loan_accounts.get(borrower_id, 0.0) + balance
            )
        _transfer_interbank_position(target, buyer, companies)
    for lender in companies:
        if lender.sector != "bank" or lender.id == target.id:
            continue
        borrower_balance = lender.bank_loan_accounts.pop(target.id, 0.0)
        if borrower_balance > 0.0:
            lender.bank_loan_accounts[buyer.id] = (
                lender.bank_loan_accounts.get(buyer.id, 0.0)
                + borrower_balance
            )
    buyer.inventory += target.inventory
    buyer.goods_inventory += target.goods_inventory
    buyer.tech_level = min(10.0, buyer.tech_level + target.tech_level * 0.15)
    buyer.property_inventory += target.property_inventory
    buyer.construction_in_progress += target.construction_in_progress
    buyer.construction_pipeline.extend(target.construction_pipeline)
    buyer.target_employees += target.target_employees
    # les salariés repris comptent comme salariés en place de l'acquéreur
    # (sans quoi seules ``max_monthly_hiring`` embauches par mois seraient possibles)
    # et le capital de production de la cible est repris avec elle
    known = set(buyer.employees)
    buyer.employees.extend(pid for pid in target.employees if pid not in known)
    buyer.production_capacity += target.production_capacity
    buyer.energy_capacity += target.energy_capacity
    buyer.productive_capital_value += target.productive_capital_value
    buyer.technology_asset_value += target.technology_asset_value
    buyer.grid_level = max(buyer.grid_level, target.grid_level)
    buyer.storage_level = max(buyer.storage_level, target.storage_level)
    buyer.productivity_trait = min(1.8, (buyer.productivity_trait + target.productivity_trait) / 2.0)
    buyer.innovation = min(1.0, (buyer.innovation + target.innovation) / 2.0)

    for holder in [*people, *companies, *extra_holders]:
        if holder.id == target.id:
            continue
        target_shares = holder.shares.pop(target.id, 0.0)
        if target_shares:
            if holder.id != buyer.id:
                holder.shares[buyer.id] = holder.shares.get(buyer.id, 0.0) + target_shares * exchange_ratio

    for company_id, shares in target.shares.items():
        if company_id != target.id:
            if company_id == buyer.id:
                buyer.treasury_shares += shares
            else:
                buyer.shares[company_id] = buyer.shares.get(company_id, 0.0) + shares
    target.shares.clear()
    target.active = False
    target.exit_reason = "acquired"
    target.cash = 0.0
    target.bank_loan_accounts.clear()
    target.debt = 0.0
    target.property_inventory = 0
    target.construction_in_progress = 0.0
    target.construction_pipeline.clear()
    target.employees.clear()
    target.production_capacity = 0.0
    target.energy_capacity = 0.0
    target.productive_capital_value = 0.0
    target.technology_asset_value = 0.0
    target.treasury_shares = 0.0
    target.shares_outstanding = 0.0
    target.stock_price = 0.0
    for person in people:
        if person.employer_id == target.id:
            person.employer_id = buyer.id
