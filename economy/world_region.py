"""Crochets d'une région dans un monde à plusieurs régions (``economy/world.py``).

Sans monde, aucun de ces attributs n'existe et rien ici n'est exécuté : le moteur est inchangé.
"""

from __future__ import annotations


class WorldRegionMixin:
    """Ce qu'une région fait pendant son mois pour le compte du monde."""

    def _world_pay_workers(self) -> None:
        """Pourvoit les postes restés vacants après l'embauche locale avec les frontaliers.

        Les mandataires restent hors de ``people`` : ils ne consomment, ne votent et ne
        détiennent aucun logement dans la région d'emploi. Leur trésorerie fait partie des
        comptes du monde et attend la compensation de fin de mois.
        """
        from economy.labor import _employment_role, _pay
        from economy.payments import transfer_cash
        from economy.trades import pick_hire

        workers = self.world_workers
        people = {p.id: p for p in self.people}
        people.update((proxy.id, proxy) for proxy, _ in workers)
        companies = {c.id: c for c in self.companies if c.active}
        for proxy, company_id in workers:
            proxy.employer_id = None
            proxy.current_wage = proxy.income_this_turn = 0.0
            company = companies.get(company_id)
            if company is None or len(company.employees) >= company.target_employees:
                continue
            choice = pick_hire(company, None, [proxy], people, company.target_employees, self.live)
            if choice is None:
                continue
            proxy.job_trade = choice[1]
            proxy.trade_wage_factor = self.trades.factors().get(proxy.job_trade, 1.0)
            role = _employment_role(company, proxy)
            wage = _pay(company, proxy, role, self.live)
            if company.cash < wage + 0.25 * self.wage_reference:
                continue
            transfer_cash(company, proxy, wage)
            company.wage_bill += wage
            company.expenses += wage
            company.employees.append(proxy.id)
            proxy.employer_id = company.id
            proxy.current_wage = proxy.income_this_turn = wage
            proxy.lifetime_earnings += wage
            proxy.employer_tenure += 1
            proxy.career_months += 1
            proxy.skill = min(2.0, proxy.skill + self.live.skill_learning_rate)
            proxy.skills[company.sector] = min(5.0, proxy.skills.get(company.sector, 1.0) + self.live.skill_level_growth)

    def _world_resident_labor(self) -> float:
        """Déclare les salaires rapatriés le mois précédent dans la région de résidence.

        La trésorerie a déjà été créditée par la compensation ; seuls les flux sont déclarés
        ici, avant l'impôt et la consommation. Un salaire bloqué à la compensation reste une
        créance du ménage, jamais un revenu imposé avant encaissement.
        """
        for person in self.people:
            income = self.world_income.pop(person.id, 0.0)
            person.income_this_turn += income
            person.lifetime_earnings += income
            contract = self.world_commuters.get(person.id)
            if contract is not None:
                person.employer_id = f"region:{contract['host']}:{contract['company']}"
                person.current_wage = contract["local_wage"]
                person.employment_role = "Frontalier"
                person.unemployment_months = 0
                person.employer_tenure += 1
        employed = [p for p in self.people if p.employer_id not in (None, "state")]
        return sum(p.current_wage for p in employed) / len(employed) if employed else self.wage_reference

    def _ship_world_orders(self, stock_field: str) -> None:
        """Après la production, avant les ventes intérieures : chaque producteur met de côté les
        unités commandées par les autres régions (``world_orders``), dans la limite de son
        stock. Elles attendent à quai (``world_dock``) le règlement de fin de mois ; ce qui n'est
        pas payé revient au stock."""
        orders = self.world_orders.pop(stock_field, None)
        if not orders:
            return
        dock = self.world_dock.setdefault(stock_field, {})
        for company in self.companies:
            wanted = orders.get(company.id, 0.0)
            if wanted <= 0.0 or not company.active:
                continue
            shipped = min(wanted, max(0.0, getattr(company, stock_field)))
            if shipped > 0.0:
                setattr(company, stock_field, getattr(company, stock_field) - shipped)
                dock[company.id] = dock.get(company.id, 0.0) + shipped
