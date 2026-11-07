"""Formation des prix : plafond, matières premières, indice des prix."""

from __future__ import annotations
from economy import diagnostics
from economy.goods import household_budget, update_food_price
from economy.labor import effective_worker_count
from economy.models import productivity_multiplier


class PricingMixin:
    """Formation des prix : plafond, matières premières, indice des prix (les prix de
    l'alimentation, des biens et les indices B2B sont fixés par ``economy.firm_pricing``)."""

    def _food_demand(self) -> float:
        live = self.live
        return sum(
            min(
                person.cash,
                household_budget(person, live.food_income_budget_share, live.food_cash_draw_rate, live)
                * person.engel_food,
            )
            * person.consumption_propensity
            / max(self.food_price * (1.0 + self.live.vat_rate), 0.01)
            for person in self.people
        )

    def _goods_demand(self) -> float:
        live = self.live
        return sum(
            min(
                person.cash,
                household_budget(person, live.goods_income_budget_share, live.goods_cash_draw_rate, live),
            )
            * person.consumption_propensity
            / max(self.goods_price * (1.0 + self.live.vat_rate), 0.01)
            for person in self.people
        )

    def _update_raw_material_price(self, planned_food: float, planned_goods: float) -> None:
        """Prix des matières premières : demande contre offre, plancher de coût. La demande
        de matières vient des productions prévues par les entreprises (anticipations locales),
        dans la limite de leur capacité."""
        worker_skills = {
            person.id: (person.skill, person.technology_level)
            for person in self.people
        }
        food_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.live.food_productivity
            * company.productivity_trait
            * productivity_multiplier(company, self.live)
            + company.production_capacity
            for company in self.companies
            if company.active and company.sector == "food"
        )
        goods_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.live.goods_productivity
            * company.productivity_trait
            * productivity_multiplier(company, self.live)
            + company.production_capacity
            for company in self.companies
            if company.active and company.sector == "goods"
        )
        extraction_capacity = sum(
            effective_worker_count(company, worker_skills)
            * self.live.raw_material_extraction_productivity
            for company in self.companies
            if company.active and company.sector == "materials"
        )
        raw_demand = min(planned_food, food_capacity) + self.live.goods_material_per_unit * min(
            planned_goods, goods_capacity
        )
        extractable_virgin = min(self.resources.raw_materials, extraction_capacity)
        raw_supply = extractable_virgin + self.resources.recycled_materials
        updated_price = update_food_price(
            self.resources.raw_material_price,
            raw_demand,
            raw_supply,
            self._price_ceiling(),
            market="matieres",
        )
        suppliers = [
            company
            for company in self.companies
            if company.active and company.sector == "materials"
        ]
        virgin_demand = max(0.0, raw_demand - self.resources.recycled_materials)
        minimum_price = 1.0
        # demande de matières vierges non servie : signal d'embauche pour les
        # extracteurs (comme la demande non satisfaite en alimentation/biens)
        self._raw_shortfall = max(0.0, virgin_demand - extractable_virgin)
        if extraction_capacity > 0.0:
            # Plancher de coût moyen à capacité normale d'utilisation (90 %) :
            # les extracteurs sont dimensionnés sur la demande, donc leur coût
            # unitaire ne dépend pas du volume vierge d'un mois donné (le
            # recyclage peut le faire tomber presque à zéro sans que le coût
            # de l'extraction ne devienne infini).
            staffed = [s for s in suppliers if s.employees]
            if staffed:
                # coût variable sur la production possible des extracteurs (comme pour
                # l'alimentation et les biens) : un coût à l'utilisation observée monterait
                # quand la demande baisse, ce qui relèverait le prix et ferait encore baisser
                # la demande (spirale mesurée en phase 3)
                unit_cost = sum(
                    s.wage_offer / self.live.raw_material_extraction_productivity
                    + self.live.raw_material_extraction_cost
                    + self.live.exploration_cost_per_unit
                    * self.live.reserve_replacement_rate
                    for s in staffed
                ) / len(staffed)
                # plancher au coût unitaire, sans marge visée
                minimum_price = max(minimum_price, unit_cost)
        diagnostics.bite("plafond_prix_plancher_matieres", minimum_price > self._price_ceiling())
        minimum_price = min(self._price_ceiling(), minimum_price)
        self.resources.raw_material_price = max(minimum_price, updated_price)

    def _price_ceiling(self) -> float:
        """Plafond technique des prix courants : aucun (+∞ ; retiré par le préréglage « autonome
        stable », seul gardé). Un prix maximal légal resterait une décision publique distincte,
        comme l'encadrement des prix des gouvernements."""
        return float("inf")

    def _services_price_index(self) -> float:
        """Prix des services aux ménages (base 1) : il suit le salaire de référence.

        Effet Baumol : les services sont surtout du travail, leur productivité
        progresse peu, leur prix relatif monte avec les salaires.
        """
        return self.wage_reference / max(self.live.starting_wage, 1.0)

    def _consumer_price_index(self) -> float:
        weights = (
            self.live.cpi_food_weight,
            self.live.cpi_goods_weight,
            self.live.cpi_rent_weight,
            self.live.cpi_services_weight,
        )
        total_weight = sum(weights)
        if total_weight <= 0.0:
            return 100.0
        # prix toutes taxes comprises effectivement payés : une hausse de TVA monte
        # l'indice (base : le taux configuré au départ) ; le loyer n'en supporte pas
        taxes = (1.0 + self.live.vat_rate) / (1.0 + self.config.vat_rate)
        price_ratios = (
            self.food_price / self.live.starting_food_price * taxes,
            self.goods_price / self.live.starting_goods_price * taxes,
            self.rent / self.live.starting_rent,
            self._services_price_index() * taxes,
        )
        return (
            100.0
            * sum(
                weight * ratio
                for weight, ratio in zip(weights, price_ratios)
            )
            / total_weight
        )
