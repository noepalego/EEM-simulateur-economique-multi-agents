"""Lectures destinées à l'interface : tableaux, profils, palmarès."""

from __future__ import annotations
from economy.models import Company, Person
from economy.wealth import wealth_leaderboards
from economy.sectors import SECTORS


class ViewsMixin:
    """Lectures destinées à l'interface : tableaux, profils, palmarès."""

    def trade_table(self) -> list[dict[str, object]]:
        """Offre, demande, salaire moyen et chômage par métier."""
        return self.trades.table(self.people)

    def trade_report(self) -> dict[str, tuple[float, float]]:
        """Résumé compact : métier -> (chômage, coefficient de salaire)."""
        return {
            row["Métier"][:5]: (round(row["Chômage"], 2), row["Coefficient de salaire"])
            for row in self.trade_table()
        }

    def company_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        people_by_id = {person.id: person for person in self.people}
        for company in self.companies:
            wage_average = company.wage_bill / len(company.employees) if company.employees else 0.0
            holdings = [
                (
                    f"{self.company_name(company_id)}: {quantity:.1f} actions "
                    f"({quantity / max(self._company_shares(company_id), 1.0):.1%})"
                )
                for company_id, quantity in sorted(company.shares.items())
            ]
            loan_portfolio = sum(company.bank_loan_accounts.values())
            founder, founder_share = self.founder_stake(company, people_by_id)
            rows.append(
                {
                    "_id": company.id,
                    "Entreprise": company.name,
                    "Secteur": dict(SECTORS)[company.sector],
                    "Statut": "Active" if company.active else "Absorbée / fermée",
                    "CEO": self.person_name(company.executive_id),
                    "Fondateur": founder,
                    "Part du fondateur": founder_share,
                    "Employés": len(company.employees),
                    "Salaire moyen": wage_average,
                    "Chiffre d'affaires": company.revenue,
                    "Bénéfice": company.profit,
                    "Trésorerie": company.cash,
                    "Dettes": company.debt,
                    "Dividendes": company.dividend_paid,
                    "Participations": ", ".join(holdings) or "—",
                    "Portefeuille de prêts": loan_portfolio,
                    "Emprunteurs": len(company.bank_loan_accounts),
                    "Cours": company.stock_price,
                    "Fortune nette": self.company_wealth(company),
                    "Qualité": company.quality_trait,
                    "Stock alimentaire": company.inventory if company.sector == "food" else 0.0,
                    "Valeur du stock alimentaire": (
                        company.inventory * self.live.food_input_cost
                        if company.sector == "food"
                        else 0.0
                    ),
                    "Stock de biens": company.goods_inventory if company.sector == "goods" else 0.0,
                    "Stock technologique": company.tech_inventory if company.sector == "technology" else 0.0,
                    "Production mensuelle": company.units_produced,
                    "Unités vendues": company.units_sold,
                    "Capacité supplémentaire": company.production_capacity,
                    "Niveau technologique": company.tech_level,
                    "Traits": (
                        f"Productivité {company.productivity_trait:.2f} · "
                        f"Risque {company.risk_tolerance:.2f} · "
                        f"Prix {company.pricing_aggression:.2f}"
                    ),
                    "Immobilier en stock": company.property_inventory if company.active else 0,
                    "Construction (mois)": (
                        str(min(p.completion_month for p in company.construction_pipeline) - self.month)
                        if company.construction_pipeline
                        else "—"
                    ),
                }
            )
        return rows

    def company_name(self, company_id: str | None) -> str:
        if company_id is None:
            return "—"
        if company_id == "state":
            return "Fonction publique"
        return next((company.name for company in self.companies if company.id == company_id), company_id)

    def person_name(self, person_id: str | None) -> str:
        if person_id is None:
            return "Aucun"
        return next((person.name for person in self.people if person.id == person_id), person_id)

    def company_wealth(self, company: Company) -> float:
        from economy.wealth import company_net_worth

        return company_net_worth(company, self.companies, self.live)

    def household_wealth(self, person: Person) -> float:
        from economy.wealth import household_net_worth

        return household_net_worth(person, self.companies, self.live)

    def household_leaderboard(self, limit: int = 10) -> list[dict[str, object]]:
        households, _ = wealth_leaderboards(
            self.people, self.companies, self.live, limit
        )
        return households

    def company_leaderboard(self, limit: int = 10) -> list[dict[str, object]]:
        _, companies = wealth_leaderboards(
            self.people, self.companies, self.live, limit
        )
        return companies

    def population_summary(self) -> dict[str, float]:
        employed = sum(person.employer_id is not None for person in self.people)
        return {
            "employed": float(employed),
            "unemployed": float(len(self.people) - employed),
            "cash": sum(person.cash for person in self.people),
            "owners": float(sum(person.owns_home for person in self.people)),
            "renters": float(sum(person.rental_owner_id is not None for person in self.people)),
            "food": sum(person.last_food_bought for person in self.people),
            "goods": sum(person.last_goods_bought for person in self.people),
            "average_cash": (
                sum(person.cash for person in self.people) / len(self.people) if self.people else 0.0
            ),
            "home_satisfaction": (
                sum(person.owns_home or person.rental_owner_id is not None for person in self.people)
                / len(self.people)
                if self.people
                else 0.0
            ),
        }

    def household_rows(self, limit: int = 25) -> list[dict[str, object]]:
        return [
            {
                "_id": person.id,
                "Ménage": person.name,
                "Fortune nette": self.household_wealth(person),
                "Trésorerie": person.cash,
                "Employeur": self.company_name(person.employer_id),
                "CEO": any(company.executive_id == person.id for company in self.companies),
                "Revenu du mois": person.income_this_turn,
                "Nourriture achetée": person.last_food_bought,
                "Biens achetés": person.last_goods_bought,
                "Compétence": person.skill,
                "Âge": person.age_months // 12,
                "Génération": person.generation,
                "Expérience (mois)": person.employer_tenure,
                "Dépenses cumulées": person.cumulative_expenses,
                "Logement": "Propriétaire" if person.owns_home else (
                    "Locataire" if person.rental_owner_id else "Sans logement loué"
                ),
                "Dette immobilière": person.mortgage_debt,
                "Mensualité du prêt": person.mortgage_payment,
                "Actions détenues": ", ".join(
                    f"{self.company_name(company_id)} ({shares:.1f})"
                    for company_id, shares in person.shares.items()
                ) or "—",
            }
            for person in sorted(
                self.people,
                key=lambda household: self.household_wealth(household),
                reverse=True,
            )[:limit]
        ]

    def household_profile(self, person_id: str) -> dict[str, object] | None:
        person = next((household for household in self.people if household.id == person_id), None)
        if person is None:
            return None
        return {
            "person": person,
            "wealth": self.household_wealth(person),
            "shares": [
                {
                    "Entreprise": self.company_name(company_id),
                    "Actions": shares,
                    "Part du capital": (
                        shares / self._company_shares(company_id)
                        if self._company_shares(company_id) > 0.0
                        else 0.0
                    ),
                    "Valeur": shares * self._company_price(company_id),
                }
                for company_id, shares in person.shares.items()
            ],
        }

    def company_profile(self, company_id: str) -> Company | None:
        return next((company for company in self.companies if company.id == company_id), None)

    def _company_price(self, company_id: str) -> float:
        company = next((firm for firm in self.companies if firm.id == company_id), None)
        return company.stock_price if company and company.active else 0.0

    def _company_shares(self, company_id: str) -> float:
        """Capital émis : titres en circulation hors titres en trésorerie (non encore souscrits,
        sans dividende ni vote). Rapportée au total, la part d'un fondateur paraissait de 75 %
        alors qu'il détenait 100 % du capital émis."""
        company = next(
            (firm for firm in self.companies if firm.id == company_id), None
        )
        return max(0.0, company.shares_outstanding - company.treasury_shares) if company else 0.0

    def founder_stake(
        self, company: Company, people_by_id: dict[str, Person] | None = None
    ) -> tuple[str, float | None]:
        """Nom du fondateur et part du capital émis qu'il détient encore (None sans fondateur
        ménage, ou fondateur disparu). ``people_by_id`` évite une recherche par entreprise."""
        if company.founder_id is None:
            return "—", None
        founder = (
            people_by_id.get(company.founder_id) if people_by_id is not None
            else next((p for p in self.people if p.id == company.founder_id), None)
        )
        if founder is None:
            return company.founder_id, None
        from economy.institutions import display_name

        issued = max(company.shares_outstanding - company.treasury_shares, 1e-9)
        return (f"{display_name(founder.id)} ({founder.name})",
                founder.shares.get(company.id, 0.0) / issued)

    def resource_summary(self) -> dict[str, float]:
        company_debt = sum(
            company.debt for company in self.companies if company.active
        )
        mortgage_debt = sum(person.mortgage_debt for person in self.people)
        return {
            "Matières premières restantes": self.resources.raw_materials,
            "Matières recyclées disponibles": self.resources.recycled_materials,
            "Matières en cours de récupération": self.resources.recycling_pending,
            "Prix des matières premières": self.resources.raw_material_price,
            "Terrains restants": float(self.resources.land_units),
            "Connaissances restantes": self.resources.knowledge,
            "Actions encore disponibles": sum(
                company.treasury_shares for company in self.companies if company.active
            ),
            "Prix d'un logement": self.live.housing_price,
            "Prix des biens courants": self.goods_price,
            "Trésorerie publique": self.government_balance,
            "Trésorerie propre des banques": self.bank_liquidity,
            "Réserves à la banque centrale": self.central_bank.reserves,
            "Avances de la banque centrale": self.central_bank.advances,
            "Dépôts de la clientèle": max(0.0, self.ledger.deposits_from_cash(self)),
            "Écart comptable (doit être nul)": self.ledger.residual(self),
            "Encours des prêts bancaires": company_debt + mortgage_debt,
            "Prêts aux entreprises": company_debt,
            "Prêts immobiliers": mortgage_debt,
            "Dépenses sans fournisseur actif (reversées aux ménages)": self.resources.market_cash,
            "Fonds propres bancaires": sum(
                company.bank_equity
                for company in self.companies
                if company.sector == "bank" and company.active
            ),
            "Dettes des entreprises": company_debt,
            "Impôts cumulés": self.total_tax_revenue,
            "Aides cumulées": self.total_benefits_paid,
            "Entreprises actives": float(sum(company.active for company in self.companies)),
        }
