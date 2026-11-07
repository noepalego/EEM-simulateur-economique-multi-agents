"""Salaires, productivité et signaux de demande des entreprises."""

from __future__ import annotations
from economy.labor import effective_worker_count
from economy.models import productivity_multiplier


class WageMixin:
    """Salaires, productivité et signaux de demande des entreprises."""

    def _productivity_index(self) -> float:
        """Production potentielle par travailleur (alimentation + biens), base 1.0.

        Combine le trait de productivité des entreprises, leur niveau
        technologique, la compétence des salariés et le capital installé.
        """
        skills = {
            person.id: (person.skill, person.technology_level)
            for person in self.people
        }
        potential = 0.0
        workers = 0
        base = self.live.food_productivity
        for company in self.companies:
            if not company.active or company.sector not in ("food", "goods"):
                continue
            n = len(company.employees)
            if n == 0:
                continue
            per_worker = (
                self.live.food_productivity
                if company.sector == "food"
                else self.live.goods_productivity
            )
            potential += (
                effective_worker_count(company, skills)
                * per_worker
                * company.productivity_trait
                * productivity_multiplier(company, self.live)
                + company.production_capacity
            )
            workers += n
        if workers == 0:
            return self._smoothed_productivity or 1.0
        return potential / (workers * base)

    def _update_wage_reference(self) -> None:
        """Salaire de référence observé : moyenne des offres des employeurs, pondérée par leurs
        effectifs (aucune règle macroéconomique : les entreprises fixent leurs offres). L'indice
        de productivité lissé est mis à jour au passage (relevé mensuel)."""
        index = self._productivity_index()
        previous = self._smoothed_productivity
        if previous <= 0.0:
            self._smoothed_productivity = index
        else:
            self._smoothed_productivity = previous + 0.10 * (index - previous)
        self.productivity_index = self._smoothed_productivity
        staffed = [c for c in self.companies if c.active and c.employees]
        headcount = sum(len(c.employees) for c in staffed)
        if headcount > 0:
            observed = sum(c.wage_offer * len(c.employees) for c in staffed) / headcount
            self.wage_terms = {"marché": observed / self.wage_reference - 1.0}
            self.wage_reference = observed

    def _update_firm_signals(
        self, unmet_food_demand: float, unmet_goods_demand: float
    ) -> None:
        """Mémorise demande et revenus lissés, base de l'emploi réactif."""
        alpha = self.live.demand_smoothing
        beta = self.live.revenue_smoothing
        for sector, unmet in (
            ("food", unmet_food_demand),
            ("goods", unmet_goods_demand),
            ("materials", self._raw_shortfall),
            ("technology", self.resources.tech_unmet_units),
        ):
            firms = [
                company
                for company in self.companies
                if company.active and company.sector == sector
            ]
            if not firms:
                continue
            sold_total = sum(company.units_sold for company in firms)
            local = sector in ("food", "goods")
            for company in firms:
                sales_share = (
                    company.units_sold / sold_total if sold_total > 0.0 else 0.0
                )
                if local:
                    # demande adressée à l'entreprise elle-même : ses ventes et ce
                    # qu'elle n'a pas pu servir (pas la pénurie de tout le marché)
                    signal = company.units_sold + company.unmet_units
                else:
                    signal = company.units_sold + unmet * (
                        0.5 * sales_share + 0.5 / len(firms)
                    )
                company.demand_ema = (
                    signal
                    if company.demand_ema <= 0.0
                    else company.demand_ema + alpha * (signal - company.demand_ema)
                )
        for company in self.companies:
            if not company.active:
                continue
            company.revenue_ema = (
                company.revenue
                if company.revenue_ema <= 0.0
                else company.revenue_ema + beta * (company.revenue - company.revenue_ema)
            )
