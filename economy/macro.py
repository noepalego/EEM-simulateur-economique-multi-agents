"""Lot J (novembre 2026) : obligations d'entreprise, commerce extérieur, négociation collective.

Trois mécanismes, chacun derrière son réglage, désactivés par défaut (aucun calcul, aucun tirage,
aucun attribut ajouté sans eux). L'état et les relevés sont ceux du lot G
(``simulation.enterprise``, ``enterprise_history``).

- **Obligations d'entreprise** (``corporate_bonds``) : une entreprise non bancaire rentable
  emprunte aux ménages et aux fonds des banques au lieu de sa banque. Un achat d'obligation est un
  transfert de dépôt de l'épargnant à l'émetteur : aucune monnaie n'est créée (un prêt bancaire en
  crée). Coupon et amortissement mensuels ; en faillite, les obligataires passent après les
  banques et avant les actionnaires.
- **Commerce extérieur** (``foreign_trade``) : un reste du monde vend de l'alimentation et des
  biens aux ménages quand le marché intérieur ne les sert pas, et achète les stocks excédentaires
  des producteurs. Il ne dépense que la monnaie intérieure qu'il a gagnée : aucune monnaie n'entre
  ni ne sort. Un taux de change flottant renchérit les importations quand elles dépassent les
  exportations.
- **Négociation collective** (``collective_bargaining``) : une fois par an, le syndicat de chaque
  secteur demande le rattrapage de l'inflation et une part des marges ; son pouvoir dépend de
  l'orientation du gouvernement et du chômage. L'accord relève les offres salariales du secteur
  et les empêche de baisser pendant douze mois ; un écart trop grand déclenche une grève d'un mois.
"""

from __future__ import annotations

from economy.business import going_wages
from economy.goods import household_budget
from economy.intersector import pay_business_suppliers
from economy.models import Company
from economy.payments import transfer_cash
from economy.sectors import FIRM_SECTORS
from economy.treasury import working_capital
from economy.wealth import company_balance_sheet

TRADED_SECTORS = ("food", "goods")
UNION_SECTORS = tuple(sector for sector, _ in FIRM_SECTORS if sector != "bank")
BOND_MIN_AGE_MONTHS = 24        # ancienneté d'un émetteur d'obligations
BOND_FUND_SPEED = 0.10          # part de la trésorerie excédentaire d'un fonds placée en obligations par mois
BOND_MINIMUM_ISSUE = 10000.0    # plus petite émission, en euros de départ (indexée sur les salaires)
STRIKE_GAP = 0.025              # écart entre la demande du syndicat et l'offre qui déclenche une grève
STRIKE_LOSS = 0.5               # part du travail perdue pendant le mois de grève
STRIKE_REST_MONTHS = 36         # délai entre deux grèves d'un même secteur (caisse de grève à reconstituer)
STRIKE_CONCESSION = 0.5         # part de l'écart que les employeurs concèdent après une grève
AGREEMENT_MONTHS = 12           # durée d'un accord salarial
UNEMPLOYMENT_WEAKNESS = 0.20    # chômage auquel un syndicat n'a plus aucun pouvoir
PROFIT_SHARE_CLAIM = 0.5        # part de la marge au-delà de la référence du secteur réclamée en salaires
FOREIGN_HOLDING_SHARE = 0.02    # avoirs maximaux du reste du monde en monnaie intérieure, en part du PIB annuel
FOREIGN_SPENDING_RATE = 0.5     # part de ses avoirs que le reste du monde redépense chaque mois


class MacroMixin:
    """Obligations d'entreprise, commerce extérieur et négociation collective (lot J)."""

    # ------------------------------------------------------------------ obligations d'entreprise
    def _corporate_bond_holders(self, issuer_id: str) -> list:
        """Porteurs des obligations d'un émetteur : ménages, puis fonds des banques."""
        state = getattr(self, "enterprise", None)
        funds = state.funds if state is not None else ()
        return [h for h in (*self.people, *funds) if h.corporate_bonds.get(issuer_id, 0.0) > 0.0]

    def _corporate_bond_rate(self, company) -> float:
        """Taux mensuel d'une émission : taux directeur + prime de crédit, qui croît avec
        l'endettement total de l'émetteur (banques et obligations) rapporté à ses actifs."""
        live = self.live
        assets = max(company_balance_sheet(company, self.companies, live)["Total des actifs"], 1.0)
        leverage = min(2.0, (company.debt + company.bond_debt) / assets)
        return max(0.0, self.policy_rate) + live.credit_risk_spread * (0.5 + leverage)

    def _corporate_bond_market(self) -> None:
        """Un mois du marché obligataire, avant la clôture des comptes : obligations d'entreprises
        absorbées reprises par l'acquéreur, service de la dette, puis émissions nouvelles."""
        self._transfer_absorbed_bonds()
        self._service_corporate_bonds()
        self._issue_corporate_bonds()

    def _transfer_absorbed_bonds(self) -> None:
        """Les obligations d'une entreprise absorbée deviennent celles de son acquéreur."""
        by_id = {c.id: c for c in self.companies}
        for company in self.companies:
            if company.active or company.bond_debt <= 0.0:
                continue
            buyer = by_id.get(company.acquired_by or "")
            holders = self._corporate_bond_holders(company.id)
            if buyer is not None and buyer.active:
                total = buyer.bond_debt + company.bond_debt
                buyer.bond_rate = (buyer.bond_debt * buyer.bond_rate + company.bond_debt * company.bond_rate) / total
                buyer.bond_debt = total
                for holder in holders:
                    amount = holder.corporate_bonds.pop(company.id)
                    holder.corporate_bonds[buyer.id] = holder.corporate_bonds.get(buyer.id, 0.0) + amount
            else:
                # émetteur fermé sans repreneur : créance perdue (réglée à la liquidation)
                for holder in holders:
                    holder.corporate_bonds.pop(company.id)
            company.bond_debt = 0.0

    def _service_corporate_bonds(self) -> None:
        """Coupon et amortissement du mois, payés aux porteurs au prorata de leurs titres.

        Coupon = encours × taux de l'émetteur (charge du mois, revenu du capital du porteur) ;
        amortissement = encours / ``corporate_bond_term_months``. L'émetteur paie ce que sa
        trésorerie permet, coupon d'abord ; le coupon impayé s'ajoute à l'encours."""
        term = max(1, self.live.corporate_bond_term_months)
        for company in self.companies:
            if not company.active or company.bond_debt <= 0.0:
                continue
            holders = self._corporate_bond_holders(company.id)
            held = sum(h.corporate_bonds[company.id] for h in holders)
            if held <= 0.0:
                company.bond_debt = 0.0
                continue
            coupon = company.bond_debt * company.bond_rate
            paid_coupon = min(coupon, max(0.0, company.cash))
            principal = min(company.bond_debt / term, max(0.0, company.cash - paid_coupon))
            company.expenses += coupon
            growth = (company.bond_debt + coupon - paid_coupon - principal) / company.bond_debt
            for holder in holders:
                share = holder.corporate_bonds[company.id] / held
                transfer_cash(company, holder, (paid_coupon + principal) * share)
                income = paid_coupon * share
                if hasattr(holder, "income_this_turn"):
                    holder.income_this_turn += income
                    holder.dividend_income_this_turn += income
                    holder.lifetime_earnings += income
                else:
                    holder.financial_income += income
                left = holder.corporate_bonds[company.id] * growth
                if left > 1e-6:
                    holder.corporate_bonds[company.id] = left
                else:
                    holder.corporate_bonds.pop(company.id)
            company.bond_debt = sum(h.corporate_bonds.get(company.id, 0.0) for h in holders)
            self._enterprise_count("obligations_coupons", paid_coupon)
            self._enterprise_count("obligations_remboursements", principal)

    def _bond_orders(self) -> list[tuple]:
        """Ordres d'achat d'obligations du mois : (porteur, montant).

        Un ménage vise ``corporate_bond_share`` × (1,5 − goût du risque) de son épargne
        investissable (les prudents préfèrent les obligations aux actions) et comble chaque mois
        une part de l'écart, sans entamer sa réserve de consommation. Un fonds y place
        ``BOND_FUND_SPEED`` de sa trésorerie au-delà de sa réserve d'attente."""
        live = self.live
        speed = live.household_stock_investment_rate
        share = max(0.0, live.corporate_bond_share)
        orders = []
        for person in self.people:
            held = sum(person.corporate_bonds.values()) if person.corporate_bonds else 0.0
            buffer = live.savings_cash_buffer_months * max(0.0, person.income_this_turn)
            investable = max(0.0, person.cash + held - buffer)
            wanted = min(1.0, share * (1.5 - person.risk_tolerance)) * investable
            order = min(speed * (wanted - held), max(0.0, person.cash - buffer))
            if order > 1.0:
                orders.append((person, order))
        state = getattr(self, "enterprise", None)
        for fund in (state.funds if state is not None else ()):
            order = BOND_FUND_SPEED * (fund.cash - fund.reserve)
            if order > 1.0:
                orders.append((fund, order))
        return orders

    def _issue_corporate_bonds(self) -> None:
        """Émissions du mois.

        Émetteur : entreprise non bancaire, bénéficiaire, d'au moins ``BOND_MIN_AGE_MONTHS`` mois,
        hors contrôle public. Elle émet pour couvrir son besoin de trésorerie
        (``_financing_need`` : réserve d'exploitation et projet de capacité rentable) et pour
        rembourser par anticipation sa dette bancaire quand l'obligation coûte moins cher, sans
        que son encours obligataire dépasse ``corporate_bond_leverage`` × ses fonds propres. Les
        épargnants n'achètent que si le taux dépasse celui des dépôts. Émissions servies au
        prorata des ordres ; chaque acheteur reçoit sa part de chaque émission."""
        live = self.live
        excluded = self._public_controlled_ids()
        minimum = BOND_MINIMUM_ISSUE * self._wage_scale()
        issues = []
        for company in self.companies:
            if (
                not company.active or company.sector == "bank" or company.id in excluded
                or company.age_months < BOND_MIN_AGE_MONTHS or company.previous_profit <= 0.0
                or not company.employees
            ):
                continue
            rate = self._corporate_bond_rate(company)
            if rate <= self.deposit_rate:
                continue
            equity = company_balance_sheet(company, self.companies, live)["Capitaux propres"]
            room = max(0.0, live.corporate_bond_leverage) * max(0.0, equity) - company.bond_debt
            need = self._financing_need(company)
            bank_rate = company.debt_interest_rate if company.debt_interest_rate is not None else 0.0
            refinancing = company.debt if company.debt > 0.0 and rate < bank_rate else 0.0
            wanted = min(need + refinancing, room)
            if wanted >= minimum:
                issues.append((company, wanted, rate, min(need, wanted)))
        if not issues:
            return
        orders = self._bond_orders()
        demand = sum(amount for _, amount in orders)
        supply = sum(wanted for _, wanted, _, _ in issues)
        if demand <= 1.0:
            return
        placed = min(demand, supply)
        buyer_fill, issue_fill = placed / demand, placed / supply
        for company, wanted, rate, need in issues:
            amount = wanted * issue_fill
            for holder, order in orders:
                part = order * buyer_fill * wanted / supply
                transfer_cash(holder, company, part)
                holder.corporate_bonds[company.id] = holder.corporate_bonds.get(company.id, 0.0) + part
            total = company.bond_debt + amount
            company.bond_rate = (company.bond_debt * company.bond_rate + amount * rate) / total
            company.bond_debt = total
            # ce qui dépasse le besoin de trésorerie rembourse la banque (dette moins chère)
            repaid = self._prepay_company_loan(company, max(0.0, amount - need * issue_fill))
            self._enterprise_count("obligations_emissions", amount)
            self._enterprise_count("obligations_refinancement", repaid)

    # ------------------------------------------------------------------ commerce extérieur
    def _foreign_account(self) -> Company:
        """Compte du reste du monde : la monnaie intérieure qu'il détient (dépôts)."""
        state = self._enterprise_state()
        if state.foreign is None:
            state.foreign = Company("monde", "Reste du monde", "foreign", 0.0, 0.0)
        return state.foreign

    def _foreign_trade(self, f) -> None:
        """Importations puis exportations d'alimentation et de biens, après les ventes intérieures.

        Prix mondial d'un produit : son prix intérieur à l'ouverture du commerce, relevé chaque
        mois de la cible d'inflation, converti au taux de change (prix d'une unité de monnaie
        étrangère).

        - **Importations** : ``trade_openness`` de la demande que le marché intérieur n'a pas
          servie, × (prix intérieur / prix importé) ^ ``trade_price_elasticity`` si l'importation
          est plus chère. Les ménages l'achètent au prorata de leur budget pour ce produit, TVA
          comprise (la TVA va à l'État, le reste au compte du reste du monde). Le reste du monde
          n'accepte de détenir que ``FOREIGN_HOLDING_SHARE`` du PIB annuel en monnaie intérieure :
          au-delà, il ne vend plus tant qu'il n'a pas redépensé.
        - **Exportations** : le reste du monde redépense chaque mois ``FOREIGN_SPENDING_RATE`` de
          ses avoirs en produits intérieurs, répartis entre les deux marchés selon leurs prix
          (× (prix mondial / prix intérieur) ^ élasticité si le produit intérieur est plus cher).
          Il achète les stocks des producteurs, à leur prix, au prorata de ces stocks ; la part
          qu'aucun stock ne sert s'ajoute à leur demande non servie (ils voient ces commandes), et
          son montant achète des services et de la technologie aux entreprises qui ont des
          salariés.
        - **Taux de change** : il monte de ``exchange_rate_speed`` × (importations − exportations)
          / (leur somme) : un déficit renchérit les importations et aide les exportations."""
        live = self.live
        state = self._enterprise_state()
        foreign = self._foreign_account()
        openness = max(0.0, min(1.0, live.trade_openness))
        elasticity = max(0.0, live.trade_price_elasticity)
        domestic = {"food": self.food_price, "goods": self.goods_price}
        unmet = {"food": f.unmet_food_demand, "goods": f.unmet_goods_demand}
        shares = {"food": (live.food_income_budget_share, live.food_cash_draw_rate),
                  "goods": (live.goods_income_budget_share, live.goods_cash_draw_rate)}
        tax_factor = 1.0 + live.vat_rate
        ceiling = FOREIGN_HOLDING_SHARE * max(0.0, self._annual_gdp())
        landed = {}
        for sector in TRADED_SECTORS:
            price = max(domestic[sector], 0.01)
            world = state.world_prices.setdefault(sector, price) * (1.0 + live.inflation_target)
            state.world_prices[sector] = world
            landed[sector] = world * state.exchange_rate
        # --- exportations : le reste du monde redépense ses avoirs
        exports = 0.0
        budget = FOREIGN_SPENDING_RATE * max(0.0, foreign.cash)
        appeal = {s: min(1.0, (landed[s] / max(domestic[s], 0.01)) ** elasticity) for s in TRADED_SECTORS}
        total_appeal = sum(appeal.values())
        for sector in TRADED_SECTORS:
            if budget <= 1.0 or total_appeal <= 0.0:
                break
            stock_field = "inventory" if sector == "food" else "goods_inventory"
            firms = [c for c in self.companies if c.active and c.sector == sector and c.price > 0.0]
            if not firms:
                continue
            order_value = budget * appeal[sector] * appeal[sector] / total_appeal
            stock = sum(max(0.0, getattr(c, stock_field)) for c in firms)
            average = sum(c.price for c in firms) / len(firms)
            wanted_units = order_value / average
            served = min(1.0, stock / wanted_units) if wanted_units > 0.0 and stock > 0.0 else 0.0
            for company in firms:
                share = max(0.0, getattr(company, stock_field)) / stock if stock > 0.0 else 1.0 / len(firms)
                units = wanted_units * served * share
                units = min(units, max(0.0, getattr(company, stock_field)), max(0.0, foreign.cash) / company.price)
                if units > 1e-9:
                    value = units * company.price
                    transfer_cash(foreign, company, value)
                    setattr(company, stock_field, getattr(company, stock_field) - units)
                    company.units_sold += units
                    company.revenue += value
                    exports += value
                    self._enterprise_count(f"exportations_{sector}", units)
                # commandes étrangères non servies : visibles du producteur
                company.unmet_units += wanted_units * (1.0 - served) * share
        # ce que les stocks n'ont pas servi achète des services et de la technologie (prestations
        # livrées par les entreprises qui ont des salariés, au prorata de leurs effectifs)
        left = budget - exports
        if left > 1.0:
            paid = pay_business_suppliers(
                foreign, left, {"services": 0.5, "technology": 0.5}, self.companies, self.resources,
                priced=False,
            )
            foreign.expenses = 0.0
            exports += paid
            self._enterprise_count("exportations_services", paid)
        # --- importations : ce que le marché intérieur n'a pas servi
        imports = 0.0
        for sector in TRADED_SECTORS:
            room = ceiling - foreign.cash
            price = max(domestic[sector], 0.01)
            wanted = openness * unmet[sector] * min(1.0, (price / landed[sector]) ** elasticity)
            wanted = min(wanted, max(0.0, room) / landed[sector])
            if wanted <= 1e-6:
                continue
            budgets = {}
            for person in self.people:
                amount = household_budget(person, *shares[sector], live) * person.consumption_propensity
                if sector == "food":
                    amount *= person.engel_food
                budgets[person.id] = min(max(0.0, person.cash), max(0.0, amount))
            total = sum(budgets.values())
            if total <= 0.0:
                continue
            quantity = min(wanted, total / (landed[sector] * tax_factor))
            vat = 0.0
            for person in self.people:
                units = quantity * budgets[person.id] / total
                if units <= 0.0:
                    continue
                net = units * landed[sector]
                tax = net * live.vat_rate
                transfer_cash(person, foreign, net)
                person.cash -= tax
                person.expenses_this_turn += net + tax
                person.cumulative_expenses += net + tax
                if sector == "food":
                    person.last_food_bought += units
                else:
                    person.last_goods_bought += units
                vat += tax
            self.public.cash += vat
            f.vat_revenue += vat
            imports += quantity * landed[sector]
            self._enterprise_count(f"importations_{sector}", quantity)
        f.exports += exports
        flows = imports + exports
        if flows > 0.0:
            state.exchange_rate = max(0.2, min(5.0, state.exchange_rate * (
                1.0 + max(0.0, live.exchange_rate_speed) * (imports - exports) / flows
            )))
        self._enterprise_count("importations", imports)
        self._enterprise_count("exportations", exports)

    # ------------------------------------------------------------------ négociation collective
    def _union_power(self, unemployment: float) -> float:
        """Pouvoir de négociation d'un syndicat, de 0 à 1 : ``union_power`` × (0,5 + orientation
        du gouvernement / 100), réduit par le chômage (nul à ``UNEMPLOYMENT_WEAKNESS``)."""
        orientation = max(0.0, min(100.0, self.government.orientation))
        slack = max(0.0, 1.0 - max(0.0, unemployment) / UNEMPLOYMENT_WEAKNESS)
        return max(0.0, min(1.0, self.live.union_power * (0.5 + orientation / 100.0) * slack))

    def _union_demand(self, sector: str) -> float:
        """Hausse annuelle demandée par le syndicat du secteur : rattrapage de l'inflation des
        douze derniers mois que les salaires du secteur n'ont pas suivie, plus
        ``PROFIT_SHARE_CLAIM`` de la marge du secteur au-delà de sa référence ; au plus
        ``union_max_raise``. 0 sans treize mois d'historique."""
        if len(self.history) < 13:
            return 0.0
        live = self.live
        now, before = self.history[-1], self.history[-13]
        inflation = now.consumer_price_index / max(before.consumer_price_index, 1e-9) - 1.0
        old_wage = before.sector_average_wage.get(sector, 0.0)
        wage_growth = now.sector_average_wage.get(sector, 0.0) / old_wage - 1.0 if old_wage > 0.0 else inflation
        year = self.history[-12:]
        revenue = sum(r.sector_revenue.get(sector, 0.0) for r in year)
        margin = sum(r.sector_profit.get(sector, 0.0) for r in year) / revenue if revenue > 0.0 else 0.0
        claim = max(0.0, inflation - wage_growth) + PROFIT_SHARE_CLAIM * max(
            0.0, margin - live.sector_margin_target.get(sector, live.profit_margin_target)
        )
        return max(0.0, min(max(0.0, live.union_max_raise), claim))

    def _collective_bargaining(self, f) -> None:
        """Négociation annuelle d'un secteur par mois (chacun le sien, à tour de rôle), après le
        marché du travail.

        L'accord vaut la demande × le pouvoir du syndicat. Si l'écart avec la demande dépasse
        ``STRIKE_GAP`` et que le syndicat pèse (pouvoir d'au moins 0,25), le secteur fait grève ce
        mois : ``STRIKE_LOSS`` du travail de ses salariés est perdu (production de
        l'alimentation, des biens et des matières), et les employeurs concèdent
        ``STRIKE_CONCESSION`` de l'écart. L'accord relève l'offre salariale de chaque employeur
        du secteur, qui ne peut plus la baisser pendant ``AGREEMENT_MONTHS`` mois."""
        sector = UNION_SECTORS[self.month % AGREEMENT_MONTHS] if self.month % AGREEMENT_MONTHS < len(UNION_SECTORS) else None
        if sector is None:
            return
        employers = [c for c in self.companies if c.active and c.sector == sector and c.employees]
        demand = self._union_demand(sector)
        if not employers or demand <= 0.0:
            return
        state = self._enterprise_state()
        power = self._union_power(f.unemployment)
        agreed = demand * power
        gap = demand - agreed
        last_strike = max(
            (row["mois"] for row in state.agreements if row["secteur"] == sector and row["greve"]),
            default=-10 ** 6,
        )
        # une grève par secteur tous les STRIKE_REST_MONTHS mois au plus
        strike = gap > STRIKE_GAP and power >= 0.25 and self.month - last_strike >= STRIKE_REST_MONTHS
        if strike:
            agreed += STRIKE_CONCESSION * gap
            strikers = {pid for company in employers for pid in company.employees}
            for person_id in strikers:
                skill = f.worker_skills.get(person_id)
                if skill is not None:
                    f.worker_skills[person_id] = (skill[0] * (1.0 - STRIKE_LOSS), skill[1])
            self._enterprise_count("greves")
            self._enterprise_count("grevistes", len(strikers))
        for company in employers:
            company.wage_offer *= 1.0 + agreed
            state.wage_floors[company.id] = (company.wage_offer, self.month + AGREEMENT_MONTHS)
        state.agreements.append({
            "mois": self.month, "secteur": sector, "demande": demand, "accord": agreed,
            "pouvoir": power, "greve": strike, "salaries": sum(len(c.employees) for c in employers),
        })
        self._enterprise_count("accords")
        self._enterprise_count("hausse_negociee", agreed)

    def _apply_wage_agreements(self) -> None:
        """Après la révision mensuelle des offres salariales : une offre couverte par un accord en
        vigueur ne descend pas sous le niveau négocié (les accords échus sont oubliés)."""
        floors = self._enterprise_state().wage_floors
        if not floors:
            return
        by_id = {c.id: c for c in self.companies}
        for company_id in list(floors):
            level, until = floors[company_id]
            company = by_id.get(company_id)
            if company is None or not company.active or self.month >= until:
                del floors[company_id]
                continue
            if company.wage_offer < level:
                company.wage_offer = level

    # ------------------------------------------------------------------ relevé du mois
    def _macro_stats(self, stats: dict) -> None:
        """Grandeurs du lot J ajoutées au relevé mensuel du lot G (mesures seules)."""
        live = self.live
        state = self._enterprise_state()
        if live.corporate_bonds:
            outstanding = sum(c.bond_debt for c in self.companies if c.active)
            stats["obligations_encours"] = outstanding
            stats["obligations_emetteurs"] = sum(1 for c in self.companies if c.active and c.bond_debt > 0.0)
            stats["obligations_porteurs"] = sum(1 for p in self.people if p.corporate_bonds)
            stats["obligations_taux"] = (
                12.0 * sum(c.bond_debt * c.bond_rate for c in self.companies if c.active) / outstanding
                if outstanding > 0.0 else 0.0
            )
        if live.foreign_trade and state.foreign is not None:
            stats["taux_de_change"] = state.exchange_rate
            stats["avoirs_du_monde"] = state.foreign.cash
        if live.collective_bargaining:
            going = going_wages(self.companies, self.wage_reference)
            stats["offres_sous_accord"] = len(state.wage_floors)
            stats["salaire_negocie_moyen"] = (
                sum(level for level, _ in state.wage_floors.values()) / len(state.wage_floors)
                if state.wage_floors else 0.0
            )
            stats["salaire_pratique_moyen"] = sum(going.values()) / len(going) if going else 0.0


def settle_bondholders(company, holders, cash: float) -> float:
    """Liquidation d'un émetteur : ses obligataires se partagent ``cash`` (ce qui reste après les
    banques) au prorata de leurs titres, dans la limite de leur créance ; le reste de la créance
    est perdu. Retourne le montant versé. Appelé par ``finance.resolve_distressed_companies``."""
    owners = [h for h in holders if getattr(h, "corporate_bonds", None) and h.corporate_bonds.get(company.id, 0.0) > 0.0]
    owed = sum(h.corporate_bonds[company.id] for h in owners)
    paid = min(max(0.0, cash), owed)
    for holder in owners:
        amount = holder.corporate_bonds.pop(company.id)
        if paid > 0.0:
            holder.cash += paid * amount / owed
    company.bond_debt = 0.0
    return paid
