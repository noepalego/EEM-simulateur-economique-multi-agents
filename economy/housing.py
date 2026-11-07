"""Marché du logement : coût de construction, prix, loyer, foncier."""

from __future__ import annotations


class HousingMixin:
    """Marché du logement : coût de construction, prix, loyer, foncier."""

    def _rental_stock(self) -> int:
        """Parc locatif de la ville : logements des promoteurs et logements-patrimoine des ménages
        (tous deux loués au loyer du marché). Le seul parc des promoteurs comptait les locataires
        des ménages bailleurs comme sans logement : pression et pénurie fictives, bulle des prix."""
        return sum(
            company.property_inventory
            for company in self.companies
            if company.active and company.sector == "real_estate"
        ) + sum(person.investment_homes for person in self.people)

    def _adjust_rent(self) -> None:
        rental_supply = self._rental_stock()
        tenants = sum(not person.owns_home for person in self.people)
        housing_pressure = tenants / max(rental_supply, 1)
        incomes = sorted(
            person.income_this_turn
            for person in self.people
            if person.income_this_turn > 0.0
        )
        median_income = incomes[len(incomes) // 2] if incomes else self.wage_reference
        affordability_ratio = self.rent / max(median_income, 1.0)
        affordability_adjustment = (
            self.live.rent_effort_ratio - affordability_ratio
        ) * self.live.rent_affordability_weight
        # rendement locatif : le loyer rejoint peu à peu le coût d'usage du logement
        # (taux hypothécaire + prime - croissance des loyers), cohérent avec la
        # valorisation du prix (aucune marge visée ne module ce rendement)
        yield_gap = (
            self.live.housing_price
            * self._housing_user_cost()
            / 12.0
            / max(self.rent, 1.0)
            - 1.0
        )
        yield_adjustment = max(-0.01, min(0.01, yield_gap * self.live.rental_yield_weight))
        rent_change = max(
            -0.035,
            min(
                0.035,
                (housing_pressure - 0.85) * 0.025
                + max(-0.03, min(0.03, affordability_adjustment))
                + yield_adjustment,
            ),
        )
        # plafond mobile : le loyer peut suivre le revenu nominal à long terme
        rent_ceiling = self.live.starting_rent * max(
            4.0, 1.5 * self.wage_reference / self.live.starting_wage
        )
        self.rent = max(
            self.live.starting_rent * 0.4,
            min(rent_ceiling, self.rent * (1.0 + rent_change)),
        )

    # Un prix de réserve du promoteur (« louer plutôt que vendre » : loyer net capitalisé à son coût
    # de financement) a été essayé puis retiré le 6 novembre 2026 : sur 800 mois, il gardait des
    # logements vacants invendus à taux directeur nul (jusqu'à 33 ans de loyer) et portait le chômage
    # de long terme de 6,7 % à 8,9 % (dix graines), même corrigé de l'occupation du parc
    # (docs/AMELIORATIONS_2026-11.md).

    def _household_housing_market(self) -> tuple[float, float, dict[str, int]]:
        """Décisions patrimoniales des ménages (A1) : rendement locatif net attendu d'un logement
        acheté au prix du marché, rendement exigé de l'épargne et logements mis en vente.

        Le rendement attendu tient compte de la vacance observée le mois dernier sur tout le parc
        locatif (logements des promoteurs et des ménages). Le rendement de l'épargne est celui des
        obligations d'État (taux directeur + écart). Un ménage bailleur met en vente ses logements
        restés vides quand ce rendement attendu est inférieur à ce qu'il exige (épargne + prime de
        risque du logement selon son goût du risque), ou un logement quand sa trésorerie est
        négative. Sans ces règles, les ménages achetaient des logements vides sans fin, jamais
        revendus (jusqu'à 117 logements-patrimoine, presque tous vacants)."""
        live = self.live
        stock = self._rental_stock()
        housed = sum(
            c.occupied_units for c in self.companies if c.active and c.sector == "real_estate"
        ) + sum(p.rented_homes for p in self.people if p.investment_homes > 0)
        vacancy = max(0.0, 1.0 - housed / stock) if stock > 0 else 0.0
        price = max(live.housing_price, 1.0)
        expected_yield = 12.0 * (
            self.rent * (1.0 - vacancy) - live.housing_price * live.housing_maintenance_rate
        ) / price
        required = 12.0 * (max(0.0, self.policy_rate) + live.bond_spread)
        offers: dict[str, int] = {}
        for person in self.people:
            if person.investment_homes <= 0:
                continue
            vacant = max(0, person.investment_homes - person.rented_homes)
            wanted = required + live.housing_risk_premium * (1.5 - person.risk_tolerance)
            count = vacant if expected_yield < wanted else 0
            if person.cash < 0.0:
                count = max(count, 1)
            if count > 0:
                offers[person.id] = min(count, person.investment_homes)
        return expected_yield, required, offers

    def _housing_user_cost(self) -> float:
        """Coût d'usage annuel du logement : taux hypothécaire + prime de risque - croissance des loyers."""
        live = self.live
        mortgage_rate = 12.0 * (max(0.0, self.policy_rate) + live.mortgage_spread)
        return max(
            live.housing_min_user_cost,
            mortgage_rate + live.housing_risk_premium - live.housing_rent_growth,
        )

    def _update_housing_costs(self) -> None:
        """Coût de construction indexé ; prix = coût × marge d'offre, de demande et de taux.

        Le coût de chantier suit salaires et prix (moyenne). La marge de
        promotion monte quand des ménages restent sans logement (pénurie) et
        baisse quand le parc est excédentaire. Elle tend aussi vers la valeur
        fondamentale du logement, les loyers annuels capitalisés au coût d'usage
        (taux hypothécaire + prime - croissance des loyers) : quand les taux
        baissent, les prix montent, et inversement. Le foncier s'étend avec le parc
        (étalement urbain) pour que la construction reste possible.
        """
        self.live = config = self.config.fresh_copy()
        wage_index = self.wage_reference / max(config.starting_wage, 1.0)
        cost_index = 0.6 * wage_index + 0.4 * self.resources.input_cost_index
        stock = self._rental_stock()
        tenants = sum(not person.owns_home for person in self.people)
        pipeline = sum(
            len(c.construction_pipeline)
            for c in self.companies
            if c.active and c.sector == "real_estate"
        )
        shortage = (tenants - stock - pipeline) / max(tenants, 1)
        build_cost = self._base_build_cost * cost_index
        markup = self._housing_markup * (1.0 + max(-0.004, min(0.004, 0.02 * shortage)))
        fundamental = 12.0 * self.rent / self._housing_user_cost()
        markup += config.housing_valuation_speed * (fundamental / max(build_cost, 1.0) - markup)
        # aucune borne imposée : seul un prix strictement positif est garanti (un promoteur ne
        # construit plus quand le prix passe sous le coût de chantier)
        self._housing_markup = max(0.2, markup)
        config.housing_build_cost = build_cost
        config.housing_price = config.housing_build_cost * self._housing_markup
        # le foncier s'étend avec la ville (étalement urbain) : tout le parc, logements occupés par
        # leurs propriétaires compris. Lié au seul bilan des promoteurs, il cessait de croître dès
        # qu'ils avaient vendu leur parc aux ménages (foncier épuisé, aucun chantier possible).
        homes = stock + sum(person.owns_home for person in self.people)
        self._land_progress += homes * config.land_expansion_rate
        new_plots = int(self._land_progress)
        if new_plots > 0:
            self.resources.land_units += new_plots
            self._land_progress -= new_plots
