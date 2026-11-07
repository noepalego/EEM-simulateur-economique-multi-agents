"""Finances publiques communes : PIB de référence, dette, financement, impôt sur les bénéfices.

Le budget lui-même (loi de finances, exécution, emprunt, arriérés) relève du nouvel État
(``economy/state_budget.py``) ; l'État à leviers (orientation, multiplicateurs, dette visée,
règle fiscale, TVA contracyclique) a été retiré le 5 novembre 2026 (mission « optimisation »).
L'État encaisse des impôts et se finance auprès des banques (titres publics), dans la limite de
``debt_ceiling_gdp`` ; aucune monnaie n'est créée.
"""

from __future__ import annotations

from economy.government import tax_company_profits
from economy.records import MonthFlows


class PublicFinanceMixin:
    """Méthodes de l'État de ``EconomySimulation``."""

    @property
    def government_balance(self) -> float:
        return self.public.cash

    @government_balance.setter
    def government_balance(self, value: float) -> None:
        self.public.cash = value

    def _annual_gdp(self) -> float:
        """PIB annuel de référence : 12 × moyenne mobile du PIB mensuel."""
        window = [r.nominal_gdp for r in self.history[-12:]]
        if window:
            return 12.0 * sum(window) / len(window)
        return 12.0 * len(self.people) * self.live.starting_wage * 0.9

    def _sync_public_debt(self) -> None:
        """Dette publique = titres détenus par les banques, les entreprises et les ménages."""
        self.public.debt = sum(
            b.bond_holdings for b in self.companies if b.active and b.sector == "bank"
        ) + sum(p.bond_holdings for p in self.people) + sum(
            c.bond_holdings for c in self.companies if c.sector != "bank"
        )

    def _debt_room(self) -> float:
        ceiling = self.live.debt_ceiling_gdp * self._annual_gdp()
        return max(0.0, ceiling - self.public.debt)

    def _fund_public(self, amount: float) -> float:
        """Rend disponible ``amount`` en empruntant aux banques si la trésorerie manque.

        Retourne le montant réellement finançable (trésorerie + emprunt, dans
        la limite du plafond de dette et des réserves bancaires).
        """
        public = self.public
        if amount <= public.cash:
            return amount
        borrowed = self.finance_government(min(amount - public.cash, self._debt_room()))
        public.cash += borrowed
        public.debt += borrowed
        return min(amount, public.cash)

    def _tax_company_profits(self, f: MonthFlows) -> None:
        collected = tax_company_profits(
            self.companies,
            self.live.corporate_tax_rate,
            self.live.tax_loss_carry_months,
        )
        self.public.cash += collected
        f.corporate_taxes = collected
        self.public.revenue += collected
