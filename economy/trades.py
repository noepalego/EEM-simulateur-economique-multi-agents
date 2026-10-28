"""Marché du travail par métiers.

Chaque ménage a un métier (sa qualification). Chaque secteur a un mix de métiers
différent : une entreprise ouvre des postes par métier à partir de ce mix. Un
poste spécialisé ne peut être pourvu que par une personne de ce métier ; les
postes d'opérateur (non qualifiés) peuvent être pourvus par n'importe qui. Le
salaire d'un poste suit un coefficient de métier qui s'ajuste partiellement à la
tension offre / demande de ce métier. Un chômeur peut se reconvertir vers un
métier en tension : cela coûte de l'argent et prend du temps.
"""

from __future__ import annotations

import random

from economy.models import Company, Person, Resources, SimulationConfig

GENERIC_TRADE = "operateur"


def assign_trade(rng: random.Random, config: SimulationConfig) -> str:
    """Tire un métier selon la composition de l'offre de travail."""
    draw = rng.random() * sum(config.trade_supply_share.values())
    cumulative = 0.0
    for trade in config.trades:
        cumulative += config.trade_supply_share.get(trade, 0.0)
        if draw <= cumulative:
            return trade
    return GENERIC_TRADE


def trade_deficits(
    company: Company,
    people_by_id: dict[str, Person],
    desired_total: int,
    config: SimulationConfig,
) -> dict[str, int]:
    """Postes encore à pourvoir par métier (mix sectoriel × effectif visé)."""
    mix = config.sector_trade_mix.get(company.sector, {GENERIC_TRADE: 1.0})
    counts: dict[str, int] = {}
    for employee_id in company.employees:
        person = people_by_id.get(employee_id)
        if person is not None:
            counts[person.job_trade] = counts.get(person.job_trade, 0) + 1
    return {
        trade: int(round(desired_total * share)) - counts.get(trade, 0)
        for trade, share in mix.items()
    }


def pick_hire(
    company: Company,
    claimant: Person | None,
    eligible: list[Person],
    people_by_id: dict[str, Person],
    desired_total: int,
    config: SimulationConfig,
) -> tuple[Person, str] | None:
    """Choisit la personne et le poste (métier) pour une embauche."""
    deficits = trade_deficits(company, people_by_id, desired_total, config)
    open_trades = [t for t, d in deficits.items() if d > 0 and t != GENERIC_TRADE]
    if claimant is not None:
        slot = claimant.trade if deficits.get(claimant.trade, 0) > 0 else GENERIC_TRADE
        return claimant, slot
    # postes spécialisés d'abord, du plus rare (mieux payé) au moins rare
    for trade in sorted(open_trades, key=lambda t: -config.trade_base_wage.get(t, 1.0)):
        for person in eligible:
            if person.trade == trade and person.retraining_months <= 0:
                return person, trade
    candidates = [p for p in eligible if p.retraining_months <= 0]
    if not candidates:
        return None
    # postes génériques : en priorité les opérateurs, sinon un surqualifié
    for person in candidates:
        if person.trade == GENERIC_TRADE:
            return person, GENERIC_TRADE
    return candidates[0], GENERIC_TRADE


class TradeMarket:
    """État du marché par métier : coefficients salariaux et reconversions."""

    def __init__(self, config: SimulationConfig, rng: random.Random) -> None:
        self.config = config
        self.rng = rng
        self.wage_index = {trade: 1.0 for trade in config.trades}
        self.tension = {trade: 1.0 for trade in config.trades}
        self.unemployment = {trade: 0.0 for trade in config.trades}
        self.supply = {trade: 0 for trade in config.trades}
        self.demand = {trade: 0.0 for trade in config.trades}
        self.retrained_total = 0
        self.refreshes = 0
        self.norm = 1.0

    def factors(self) -> dict[str, float]:
        return {trade: self.wage_factor(trade) for trade in self.config.trades}

    def wage_factor(self, trade: str) -> float:
        # normalisé : la moyenne des coefficients, pondérée par les postes, vaut 1
        # (les métiers se répartissent la masse salariale, ils ne la gonflent pas)
        return (
            self.config.trade_base_wage.get(trade, 1.0)
            * self.wage_index.get(trade, 1.0)
            / self.norm
        )

    def refresh(self, people: list[Person], companies: list[Company]) -> None:
        """Recalcule offre, demande, tension et coefficients (appelé chaque mois)."""
        config = self.config
        self.refreshes += 1
        supply = {trade: 0 for trade in config.trades}
        unemployed = {trade: 0 for trade in config.trades}
        for person in people:
            supply[person.trade] = supply.get(person.trade, 0) + 1
            if person.employer_id is None:
                unemployed[person.trade] = unemployed.get(person.trade, 0) + 1
        demand = {trade: 0.0 for trade in config.trades}
        for company in companies:
            if not company.active:
                continue
            mix = config.sector_trade_mix.get(company.sector)
            if not mix:
                continue
            jobs = max(company.target_employees, len(company.employees))
            for trade, share in mix.items():
                demand[trade] += jobs * share
        total_demand = sum(demand.values())
        total_supply = sum(supply.values())
        for trade in config.trades:
            self.supply[trade] = supply[trade]
            self.demand[trade] = demand[trade]
            self.unemployment[trade] = (
                unemployed[trade] / supply[trade] if supply[trade] else 0.0
            )
            if trade == GENERIC_TRADE:
                tension = 1.0
            else:
                # part des postes demandés rapportée à la part de l'offre (le taux
                # d'emploi global est neutralisé : seule la composition compte)
                demand_share = demand[trade] / max(1.0, total_demand)
                supply_share = supply[trade] / max(1.0, total_supply)
                tension = demand_share / max(supply_share, 1e-6)
            self.tension[trade] = tension
            low, high = config.trade_wage_bounds
            pressure = config.trade_wage_sensitivity * (tension - 1.0)
            pressure -= config.trade_wage_sensitivity * self.unemployment[trade]
            self.wage_index[trade] = max(
                low,
                min(high, self.wage_index[trade] * (1.0 + max(-0.01, min(0.01, pressure)))),
            )
        weighted = sum(
            demand[t] * config.trade_base_wage.get(t, 1.0) * self.wage_index[t]
            for t in config.trades
        )
        self.norm = max(0.5, weighted / max(1.0, total_demand))
        for person in people:
            person.trade_wage_factor = self.wage_factor(person.job_trade or person.trade)

    def retrain(
        self,
        people: list[Person],
        resources: Resources,
        wage_reference: float,
    ) -> int:
        """Reconversions : un chômeur d'un métier saturé apprend un métier en tension."""
        config = self.config
        started = 0
        if self.refreshes < config.retraining_start_month:
            return 0
        for person in people:
            if person.retraining_months > 0:
                person.retraining_months -= 1
                if person.retraining_months == 0 and person.retraining_target:
                    person.trade = person.retraining_target
                    person.retraining_target = ""
                    self.retrained_total += 1
                continue
            if person.employer_id is not None:
                continue
            if self.unemployment.get(person.trade, 0.0) < config.retraining_min_unemployment:
                continue
            if self.rng.random() >= config.retraining_probability:
                continue
            target = max(
                (t for t in config.trades if t != person.trade and t != GENERIC_TRADE),
                key=lambda t: self.tension.get(t, 1.0) * self.wage_factor(t)
                * (1.0 - self.unemployment.get(t, 0.0)),
                default=None,
            )
            if target is None or self.tension.get(target, 1.0) <= 1.0:
                continue
            cost = config.retraining_cost_months * wage_reference
            if person.cash < cost:
                continue
            person.cash -= cost
            person.expenses_this_turn += cost
            resources.market_cash += cost  # organismes de formation (secteur amont)
            person.retraining_months = config.retraining_months
            person.retraining_target = target
            started += 1
        return started

    def table(self, people: list[Person]) -> list[dict[str, object]]:
        """Tableau par métier : offre, demande, salaire moyen, chômage."""
        rows = []
        for trade in self.config.trades:
            employed = [
                p for p in people
                if p.employer_id not in (None, "services") and (p.job_trade or p.trade) == trade
            ]
            average = sum(p.current_wage for p in employed) / len(employed) if employed else 0.0
            rows.append(
                {
                    "Métier": trade,
                    "Offre (personnes)": self.supply[trade],
                    "Demande (postes)": round(self.demand[trade], 1),
                    "Tension": round(self.tension[trade], 2),
                    "Salaire moyen": average,
                    "Coefficient de salaire": round(self.wage_factor(trade), 3),
                    "Chômage": self.unemployment[trade],
                }
            )
        return rows
