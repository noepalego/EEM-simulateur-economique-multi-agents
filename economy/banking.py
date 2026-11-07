"""Comptes de prêts des banques : inscription, règlement, pertes, marché interbancaire."""

from __future__ import annotations

from contextlib import contextmanager
import math

from economy.consolidation import _transfer_interbank_position
from economy.credit_risk import voluntary_buffer
from economy.models import Company, ResolutionEvent, positive_loan_book


class BankingMixin:
    """Comptes de prêts des banques : inscription, règlement, pertes."""

    @contextmanager
    def _credit_transaction(self, borrower, amount, *, account=None):
        """Autorise et inscrit le crédit avant de créer sa trésorerie.

        Le contexte couvre les champs du contrat et de l'emprunteur ; une erreur
        restaure aussi toutes les créances, même après une inscription partielle.
        """
        banks = [b for b in self.companies if b.sector == "bank"]
        accounts = [(b, b.bank_loan_accounts.copy()) for b in banks]
        before = borrower.__dict__.copy()
        try:
            self._book_bank_loan(account or borrower.id, amount)
            yield
        except Exception:
            for bank, original in accounts:
                bank.bank_loan_accounts.clear()
                bank.bank_loan_accounts.update(original)
            borrower.__dict__.clear()
            borrower.__dict__.update(before)
            raise

    def _consumer_credit_transaction(self, person, amount):
        return self._credit_transaction(person, amount, account=f"consumer:{person.id}")

    @contextmanager
    def _mortgage_transaction(self, person, seller, amount):
        """Inscrit la créance avant la vente ; annule les mutations en cas d'échec."""
        banks = [b for b in self.companies if b.sector == "bank"]
        accounts = [(b, b.bank_loan_accounts.copy()) for b in banks]
        person_before = person.__dict__.copy()
        seller_before = seller.__dict__.copy()
        try:
            if amount > self.credit_capacity() + 1e-7:
                raise ValueError("Capacité bancaire insuffisante pour le prêt immobilier")
            self._book_bank_loan(f"mortgage:{person.id}", amount)
            yield
        except Exception:
            for bank, original in accounts:
                bank.bank_loan_accounts.clear()
                bank.bank_loan_accounts.update(original)
            person.__dict__.clear()
            person.__dict__.update(person_before)
            seller.__dict__.clear()
            seller.__dict__.update(seller_before)
            raise

    # ------------------------------------------------------------------
    # Capacité de crédit et trésorerie propre des banques
    # ------------------------------------------------------------------
    @property
    def bank_liquidity(self) -> float:
        """Trésorerie propre des banques (salaires, dividendes) : pas une réserve de prêt."""
        return sum(b.cash for b in self.companies if b.sector == "bank" and b.active)

    def _bank_capacity(self, bank) -> float:
        """Prêts supplémentaires possibles pour une banque : ses fonds propres / exigence - son encours."""
        exposure = (
            positive_loan_book(bank.bank_loan_accounts)
            + bank.bond_holdings
        )
        requirement = max(self.live.capital_requirement, 1e-6)
        # coussin volontaire : une banque qui a perdu sur ses prêts au cours du cycle exige
        # plus de fonds propres par euro prêté (sa propre décision, pas la règle)
        requirement *= 1.0 + voluntary_buffer(bank, self.live)
        return max(0.0, max(0.0, bank.bank_equity) / requirement - exposure)

    def credit_capacity(self) -> float:
        """Capacité de crédit du système : somme des capacités propres à chaque banque.

        Chaque banque est contrainte par ses propres fonds propres : une banque
        affaiblie ne prête plus, même si ses concurrentes le peuvent encore.
        """
        return sum(
            self._bank_capacity(b) for b in self.companies if b.sector == "bank" and b.active
        )

    def _capacity_weights(self) -> list[tuple]:
        """Répartition d'un nouveau prêt entre banques, au prorata de leur capacité libre."""
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        capacities = [self._bank_capacity(b) for b in banks]
        total = sum(capacities)
        if total <= 0.0:
            return [(b, 1.0 / len(banks)) for b in banks] if banks else []
        return [(b, capacity / total) for b, capacity in zip(banks, capacities)]

    def _bank_weights(self) -> list[tuple]:
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        books = [positive_loan_book(b.bank_loan_accounts) for b in banks]
        total = sum(books)
        if total <= 0.0:
            return [(b, 1.0 / len(banks)) for b in banks] if banks else []
        return [(b, book / total) for b, book in zip(banks, books)]

    def _receive_bank_income(self, amount: float) -> None:
        """Les intérêts encaissés alimentent la trésorerie propre des banques (au prorata des prêts)."""
        if amount <= 0.0:
            return
        for bank, weight in self._bank_weights():
            bank.cash += amount * weight

    def _charge_banks(self, amount: float) -> float:
        """Prélève une charge sur la trésorerie des banques (au prorata des prêts) ; retourne le montant payé."""
        paid = 0.0
        for bank, weight in self._bank_weights():
            part = min(amount * weight, max(0.0, bank.cash))
            bank.cash -= part
            bank.expenses += part
            paid += part
        return paid

    def _pay_deposit_interest(self) -> float:
        """Intérêt versé aux déposants, depuis les fonds propres des banques.

        Seuls les ménages sont rémunérés (les soldes d'exploitation des entreprises ne
        rapportent rien) et seulement sur la part de leur trésorerie qui est un dépôt
        (et non de la monnaie fiduciaire initiale), au taux que fixe chaque banque
        (``_bank_deposit_charges``), payé par chacune au prorata de ses dépôts.
        """
        self._update_bank_loss_rates()
        deposits = max(0.0, self.ledger.deposits_from_cash(self))
        money = self.ledger.nonbank_cash(self)
        rate, bank_charges = self._bank_deposit_charges()
        self.deposit_rate = rate
        if deposits <= 0.0 or money <= 0.0 or rate <= 0.0:
            return 0.0
        share = min(1.0, deposits / money) * rate
        total = sum(p.cash for p in self.people) * share
        paid = 0.0
        for bank, weight in bank_charges:
            part = min(total * weight, max(0.0, bank.cash))
            bank.cash -= part
            bank.expenses += part
            paid += part
        if paid <= 0.0:
            return 0.0
        factor = paid / total
        for person in self.people:
            interest = person.cash * share * factor
            person.cash += interest
            person.income_this_turn += interest
            person.dividend_income_this_turn += interest
            person.lifetime_earnings += interest
        return paid

    def _update_bank_loss_rates(self) -> None:
        """Taux de perte mensuel de chaque banque (pertes nouvelles / encours), lissé sur le
        cycle (``bank_cycle_smoothing``, mémoire d'environ huit ans)."""
        for bank in self.companies:
            if bank.sector != "bank":
                continue
            new_loss = max(0.0, bank.default_loss - bank.last_default_loss)
            bank.last_default_loss = bank.default_loss
            book = positive_loan_book(bank.bank_loan_accounts) + bank.bond_holdings
            bank.loss_rate_cycle += self.live.bank_cycle_smoothing * (
                new_loss / max(book, 1.0) - bank.loss_rate_cycle
            )

    def _bank_deposit_charges(self) -> tuple[float, list[tuple]]:
        """Taux de dépôt propre à chaque banque (``deposit_pricing="bank"``).

        Chaque banque offre le taux de référence (directeur moins écart) relevé de
        ``deposit_liquidity_sensitivity`` × son besoin de financement : l'écart entre sa
        part des prêts et sa part des dépôts (elle prête plus qu'elle ne collecte), plus
        son manque de liquidité interbancaire (trésorerie sous ``bank_liquidity_months``
        de charges). Mesuré : les banques détiennent 80 à 300 fois ce besoin de
        liquidité, ce second terme ne joue presque jamais. Les ménages déplacent chaque mois ``deposit_share_speed``
        de leurs dépôts vers les banques mieux rémunérées que la moyenne, au-delà d'un
        coût de changement ; le déplacement ne crée ni ne détruit de monnaie (parts de
        marché, pas de compte de dépôt par banque).

        Retourne le taux moyen payé aux ménages et, pour chaque banque, la part de la
        charge totale d'intérêts qu'elle supporte.
        """
        live = self.live
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        reference = max(0.0, self.policy_rate - live.deposit_spread)
        if not banks:
            return reference, []
        count = len(banks)
        total_share = sum(max(0.0, b.deposit_share) for b in banks)
        for bank in banks:
            bank.deposit_share = bank.deposit_share / total_share if total_share > 0.0 and bank.deposit_share > 0.0 else 1.0 / count
        norm = sum(b.deposit_share for b in banks)
        for bank in banks:
            bank.deposit_share /= norm
        books = [positive_loan_book(b.bank_loan_accounts) for b in banks]
        total_books = sum(books)
        for bank, book in zip(banks, books):
            # besoin de financement : une banque qui prête plus que sa part de dépôts doit
            # les attirer (ses prêts créent des dépôts que les paiements envoient ailleurs)
            loan_share = book / total_books if total_books > 0.0 else 1.0 / count
            funding_gap = max(-1.0, min(1.0, (loan_share - bank.deposit_share) * count))
            # manque de liquidité du marché interbancaire (saturé quand la banque est
            # largement liquide : seul un manque compte, jamais un excès)
            need = live.bank_liquidity_months * max(0.0, bank.previous_revenue - bank.previous_profit)
            shortage = max(0.0, min(1.0, (need - bank.cash) / need)) if need > 0.0 else 0.0
            tightness = max(-1.0, min(1.0, funding_gap + shortage))
            bank.deposit_rate_offer = max(0.0, reference + live.deposit_liquidity_sensitivity * tightness)
        average = sum(b.deposit_share * b.deposit_rate_offer for b in banks)
        weights = []
        for bank in banks:
            gap = bank.deposit_rate_offer - average
            net = math.copysign(max(0.0, abs(gap) - live.deposit_switching_cost), gap)
            weights.append(bank.deposit_share * math.exp(max(-5.0, min(5.0, live.deposit_share_sensitivity * net))))
        total_weight = sum(weights)
        for bank, weight in zip(banks, weights):
            bank.deposit_share += live.deposit_share_speed * (weight / total_weight - bank.deposit_share)
        average = sum(b.deposit_share * b.deposit_rate_offer for b in banks)
        if average <= 0.0:
            return 0.0, [(b, 0.0) for b in banks]
        return average, [
            (b, b.deposit_share * b.deposit_rate_offer / average) for b in banks
        ]

    def _settle_new_credit(self, debt_before: dict[str, float]) -> float:
        """Inscrit aux livres les prêts accordés (le prêt crée le dépôt, il ne consomme aucune réserve)."""
        total = 0.0
        for company in self.companies:
            extension = max(0.0, company.debt - debt_before.get(company.id, 0.0))
            if extension > 0.0 and company.sector != "bank":
                self._book_bank_loan(company.id, extension)
                total += extension
        return total

    def _book_bank_loan(self, borrower_id: str, amount: float, *, opening_balance=False) -> None:
        """Inscrit un nouveau prêt chez les banques qui ont de la capacité libre."""
        if not math.isfinite(amount) or amount < 0.0:
            raise ValueError("Montant de crédit invalide")
        if amount == 0.0:
            return
        if opening_balance and self.month != 0:
            raise ValueError("Un encours initial ne peut être inscrit qu'au mois zéro")
        capacity = self.credit_capacity()
        # La marge couvre uniquement l'arrondi de la somme des capacités.
        if not opening_balance and (capacity <= 0.0 or amount > capacity + 8 * math.ulp(capacity)):
            raise ValueError("Capacité bancaire insuffisante")
        for bank, weight in self._capacity_weights():
            bank.bank_loan_accounts[borrower_id] = (
                bank.bank_loan_accounts.get(borrower_id, 0.0) + amount * weight
            )

    def _interbank_market(self) -> None:
        """Marché interbancaire de la trésorerie propre des banques.

        Une banque veut garder ``bank_liquidity_months`` mois de charges en
        trésorerie (salaires, intérêts des dépôts, avances). Celle qui en manque
        emprunte à celles qui en ont au-delà du double, au taux directeur. Les
        intérêts sont un revenu du prêteur et une charge de l'emprunteur (non payés,
        ils s'ajoutent à la créance) ; un emprunteur redevenu liquide rembourse.
        Ces prêts circulent entre banques : ils ne créent ni ne détruisent de
        monnaie, mais exposent le prêteur à la faillite de l'emprunteur.
        """
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        if len(banks) < 2:
            return
        by_id = {b.id: b for b in banks}
        rate = max(0.0, self.policy_rate)
        for lender in banks:
            for borrower_id, amount in list(lender.interbank_claims.items()):
                borrower = by_id.get(borrower_id)
                if borrower is None:
                    continue
                interest = amount * rate
                paid = min(interest, max(0.0, borrower.cash))
                borrower.cash -= paid
                borrower.expenses += interest
                lender.cash += paid
                lender.revenue += interest
                lender.interbank_claims[borrower_id] = amount + interest - paid
        need = {
            b.id: self.live.bank_liquidity_months * max(0.0, b.previous_revenue - b.previous_profit)
            for b in banks
        }
        # remboursements des banques redevenues liquides
        for lender in banks:
            for borrower_id, amount in list(lender.interbank_claims.items()):
                borrower = by_id.get(borrower_id)
                if borrower is None:
                    continue
                repaid = min(amount, max(0.0, borrower.cash - 2.0 * need[borrower_id]))
                if repaid > 0.0:
                    borrower.cash -= repaid
                    lender.cash += repaid
                    remaining = amount - repaid
                    if remaining > 1e-9:
                        lender.interbank_claims[borrower_id] = remaining
                    else:
                        lender.interbank_claims.pop(borrower_id, None)
        # nouveaux prêts : des banques excédentaires vers les banques à court
        shortfalls = {b.id: need[b.id] - b.cash for b in banks if b.cash < need[b.id]}
        surpluses = {
            b.id: b.cash - 2.0 * need[b.id] for b in banks if b.cash > 2.0 * need[b.id]
        }
        wanted, offered = sum(shortfalls.values()), sum(surpluses.values())
        volume = min(wanted, offered)
        if volume <= 0.0:
            return
        for borrower_id, short in shortfalls.items():
            borrowed = volume * short / wanted
            by_id[borrower_id].cash += borrowed
            for lender_id, surplus in surpluses.items():
                part = borrowed * surplus / offered
                lender = by_id[lender_id]
                lender.cash -= part
                lender.interbank_claims[borrower_id] = (
                    lender.interbank_claims.get(borrower_id, 0.0) + part
                )

    def _settle_company_loan_accounts(
        self, previous_debts: dict[str, float]
    ) -> None:
        companies_by_id = {company.id: company for company in self.companies}
        for borrower_id, previous_debt in previous_debts.items():
            borrower = companies_by_id.get(borrower_id)
            if borrower is None:
                continue
            if borrower.sector == "bank":
                continue
            lenders = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.bank_loan_accounts.get(borrower_id, 0.0) > 0.0
            ]
            total_booked = sum(
                bank.bank_loan_accounts[borrower_id] for bank in lenders
            )
            if not lenders or total_booked <= 0.0:
                continue
            shares = {
                bank.id: bank.bank_loan_accounts[borrower_id] / total_booked
                for bank in lenders
            }
            interest_paid = borrower.interest_paid_this_turn
            unpaid_interest = max(
                0.0,
                borrower.debt
                - previous_debt
                + borrower.principal_paid_this_turn,
            )
            for bank in lenders:
                share = shares[bank.id]
                bank.revenue += (interest_paid + unpaid_interest) * share
                bank.bank_loan_accounts[borrower_id] = max(
                    0.0, borrower.debt * share
                )

    def _settle_mortgage_accounts(
        self, previous_debts: dict[str, float]
    ) -> None:
        for person in self.people:
            previous_debt = previous_debts.get(person.id, 0.0)
            if previous_debt <= 0.0:
                continue
            interest_accrued = previous_debt * person.mortgage_rate
            lenders = [
                bank
                for bank in self.companies
                if bank.sector == "bank"
                and bank.bank_loan_accounts.get(f"mortgage:{person.id}", 0.0) > 0.0
            ]
            total_booked = sum(
                bank.bank_loan_accounts[f"mortgage:{person.id}"]
                for bank in lenders
            )
            if total_booked <= 0.0:
                continue
            for bank in lenders:
                share = (
                    bank.bank_loan_accounts[f"mortgage:{person.id}"]
                    / total_booked
                )
                bank.bank_loan_accounts[f"mortgage:{person.id}"] = (
                    person.mortgage_debt * share
                )
                bank.revenue += interest_accrued * share

    def _settle_household_accounts(
        self,
        prefix: str,
        accrued: dict[str, float],
        current_debts: dict[str, float],
    ) -> None:
        """Met à jour les comptes de prêts des ménages et inscrit les intérêts courus en revenu."""
        banks = [b for b in self.companies if b.sector == "bank"]
        for person_id, debt in current_debts.items():
            account = f"{prefix}:{person_id}"
            lenders = [b for b in banks if b.bank_loan_accounts.get(account, 0.0) > 0.0]
            booked = sum(b.bank_loan_accounts[account] for b in lenders)
            if booked <= 0.0:
                continue
            for bank in lenders:
                share = bank.bank_loan_accounts[account] / booked
                bank.revenue += accrued.get(person_id, 0.0) * share
                if debt > 0.0:
                    bank.bank_loan_accounts[account] = debt * share
                else:
                    bank.bank_loan_accounts.pop(account, None)

    def _write_off_household_loan(self, account: str, loss: float) -> None:
        """Perte sur un prêt aux ménages : charge des banques prêteuses, compte soldé.

        La charge est inscrite avant la clôture du mois : elle réduit le profit,
        donc les fonds propres bancaires (l'encours a déjà disparu du bilan).
        """
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        lenders = [b for b in banks if b.bank_loan_accounts.get(account, 0.0) > 0.0]
        booked = sum(b.bank_loan_accounts[account] for b in lenders)
        shares = (
            [(b, b.bank_loan_accounts[account] / booked) for b in lenders]
            if booked > 0.0
            else [(b, 1.0 / len(banks)) for b in banks]
        )
        for bank, share in shares:
            if loss > 0.0:
                bank.expenses += loss * share
                bank.default_loss += loss * share
        for bank in self.companies:
            if bank.sector == "bank":
                bank.bank_loan_accounts.pop(account, None)

    def _write_off_defaulted_bank_loans(self) -> None:
        """Pertes sur prêts : toute perte constatée (faillite, restructuration) réduit les fonds propres.

        La perte est répartie entre les prêteurs au prorata de leurs créances (à
        parts égales si aucun compte n'est ouvert) : l'encours de prêts et les fonds
        propres baissent du même montant, sans monnaie créée ni détruite.
        """
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        for borrower in self.companies:
            if borrower.sector == "bank":
                continue
            loss_total = borrower.default_loss
            lenders = [
                bank for bank in banks if bank.bank_loan_accounts.get(borrower.id, 0.0) > 0.0
            ]
            exposure = sum(bank.bank_loan_accounts[borrower.id] for bank in lenders)
            if not borrower.active and exposure > 0.0:
                # perte réellement subie à la faillite : les banques en tirent leur
                # estimation de la perte en cas de défaut (``credit_risk``)
                self._firm_loss_given_default += 0.1 * (
                    min(1.0, loss_total / exposure) - self._firm_loss_given_default
                )
            if loss_total <= 0.0 and (borrower.active or exposure <= 0.0):
                continue
            if lenders:
                shares = [(b, b.bank_loan_accounts[borrower.id] / exposure) for b in lenders]
            else:
                shares = [(b, 1.0 / len(banks)) for b in banks]
            for bank, share in shares:
                bank.default_loss += loss_total * share
                bank.expenses += loss_total * share
                bank.bank_equity -= loss_total * share
            for bank in banks:
                if not borrower.active:
                    bank.bank_loan_accounts.pop(borrower.id, None)
            if borrower.active:
                # emprunteur maintenu (restructuration) : les créances suivent la dette
                # restante, au prorata, pour que dettes et créances restent égales
                for bank, share in shares:
                    remaining = max(0.0, borrower.debt) * share
                    if remaining > 1e-9:
                        bank.bank_loan_accounts[borrower.id] = remaining
                    else:
                        bank.bank_loan_accounts.pop(borrower.id, None)
            borrower.default_loss = 0.0

        # faillites en cascade : les pertes interbancaires peuvent emporter un prêteur
        handled: set[str] = set()
        while True:
            failing = [
                b for b in banks if b.active and b.bank_equity <= 0.0 and b.id not in handled
            ]
            if not failing:
                break
            for failed_bank in failing:
                handled.add(failed_bank.id)
                self._resolve_failed_bank(failed_bank, banks)
        # dernière banque aux fonds propres négatifs : selon la politique de résolution
        for bank in self.companies:
            if bank.sector == "bank" and bank.active and bank.bank_equity < 0.0:
                hole = -bank.bank_equity
                public = self._public_share_of_bank_hole(hole)
                bank.bond_holdings += public
                self._haircut_deposits(hole - public)
                bank.bank_equity = 0.0
                self._log_resolution(bank.id, "bank_last", public_cost=public,
                                     depositor_loss=hole - public)
                handled.add(bank.id)
        if handled:
            self._recapitalize_banks()

    def _undercapitalized_banks(self) -> frozenset:
        """Banques sous ``capital_requirement`` + ``bank_recap_buffer`` : pas de dividende
        (coussin de conservation de Bâle III), le bénéfice reconstitue le capital."""
        ratio = self.live.capital_requirement + self.live.bank_recap_buffer
        return frozenset(
            bank.id
            for bank in self.companies
            if bank.sector == "bank"
            and bank.active
            and bank.bank_equity
            < ratio * (
                positive_loan_book(bank.bank_loan_accounts)
                + bank.bond_holdings
            )
        )

    def _authorize_bank_rights_issues(self) -> None:
        """Augmentation de capital proposée au marché (``bank_equity_issuance="rights"``, R3).

        Une banque sous son exigence de fonds propres plus le coussin autorise des actions
        nouvelles pour le montant manquant, au cours du marché. Elles rejoignent ses actions
        en réserve et ne sont vendues que si des investisseurs les demandent (émission de
        ``stock_market._clear_stock_market``, qui crédite les fonds propres) : aucune
        souscription forcée, aucune monnaie créée.
        """
        target_ratio = self.live.capital_requirement + self.live.bank_recap_buffer
        for bank in self.companies:
            if bank.sector != "bank" or not bank.active:
                continue
            exposure = positive_loan_book(bank.bank_loan_accounts) + bank.bond_holdings
            shortfall = target_ratio * exposure - max(0.0, bank.bank_equity)
            if shortfall <= 0.0:
                continue
            extra = shortfall / max(bank.stock_price, 1.0) - bank.treasury_shares
            if extra > 0.0:
                bank.treasury_shares += extra
                bank.shares_outstanding += extra

    def _recapitalize_banks(self) -> None:
        """Après une résolution, l'État ramène les banques au minimum réglementaire
        plus un coussin (``bank_recap_buffer``), pour qu'elles puissent prêter.

        Combler le trou jusqu'à zéro laisserait des banques sans fonds propres,
        donc incapables de prêter pour toujours. Dans la limite de
        ``bank_bailout_cap_gdp`` du PIB annuel par résolution, l'État apporte des titres
        publics contre des actions nouvelles de la banque : il devient actionnaire, les
        anciens actionnaires sont dilués. Coût consigné comme résolution « bank_recap ».
        """
        for bank in self.companies:
            if bank.sector != "bank" or not bank.active:
                continue
            exposure = (
                positive_loan_book(bank.bank_loan_accounts)
                + bank.bond_holdings
            )
            target_ratio = self.live.capital_requirement + self.live.bank_recap_buffer
            # les titres apportés augmentent eux-mêmes l'exposition :
            # E + x >= r (X + x)  =>  x = (r X - E) / (1 - r)
            shortfall = (
                target_ratio * exposure - max(0.0, bank.bank_equity)
            ) / max(1e-6, 1.0 - target_ratio)
            injection = self._public_share_of_bank_hole(max(0.0, shortfall))
            if injection <= 0.0:
                continue
            new_shares = injection / max(bank.stock_price, 1.0)
            state = self.public.account
            state.shares[bank.id] = state.shares.get(bank.id, 0.0) + new_shares
            bank.shares_outstanding += new_shares
            bank.bond_holdings += injection
            bank.bank_equity += injection
            self._log_resolution(bank.id, "bank_recap", public_cost=injection)

    def _public_share_of_bank_hole(self, hole: float) -> float:
        """Part d'un trou de fonds propres bancaire comblée par l'État : au plus
        ``bank_bailout_cap_gdp`` du PIB annuel par résolution (au-delà, le repreneur, les
        créanciers puis les déposants absorbent la perte)."""
        return min(hole, self.live.bank_bailout_cap_gdp * self._annual_gdp())

    def _haircut_deposits(self, loss: float) -> float:
        """Perte imposée aux déposants (ménages et entreprises, au prorata de leurs dépôts).

        Le dépôt détruit correspond au trou de fonds propres non comblé : la
        monnaie baisse d'autant que le passif bancaire, sans écart comptable.
        """
        if loss <= 0.0:
            return 0.0
        holders = [p for p in self.people if p.cash > 0.0] + [
            c for c in self.companies if c.sector != "bank" and c.cash > 0.0
        ]
        total = sum(h.cash for h in holders)
        if total <= 0.0:
            return 0.0
        ratio = min(1.0, loss / total)
        for holder in holders:
            holder.cash -= holder.cash * ratio
        return total * ratio

    def _log_resolution(self, company_id: str, kind: str, **costs: float) -> None:
        """Consigne une résolution (faillite bancaire, restructuration) et ses coûts."""
        self.resolution_events.append(ResolutionEvent(self.month, company_id, kind, **costs))

    def _wind_down_bank(self, bank) -> None:
        """Sortie d'une jeune banque désignée par la faillite tirée au sort (``startup_failure``).

        Ses fonds propres comptables restants (boni de liquidation) reviennent à ses
        actionnaires, comme un dividende : les fonds propres bancaires et la trésorerie
        de la banque baissent, les dépôts des actionnaires montent d'autant (à défaut
        d'actionnaire extérieur : au fondateur, comme dans ``resolve_distressed_companies``).
        La résolution bancaire transfère ensuite prêts, titres, trésorerie et positions
        interbancaires à une concurrente ; des fonds propres négatifs y sont traités
        comme une faillite bancaire ordinaire.
        """
        banks = [b for b in self.companies if b.sector == "bank" and b.active]
        if not any(b.id != bank.id for b in banks):
            return  # sans repreneur, la banque reste active
        surplus = max(0.0, bank.bank_equity)
        holders = [
            holder for holder in [*self.people, *self.companies, self.public.account]
            if holder.id != bank.id and holder.shares.get(bank.id, 0.0) > 0.0
        ]
        shares_held = sum(holder.shares[bank.id] for holder in holders)
        if surplus > 0.0 and shares_held > 0.0:
            for holder in holders:
                holder.cash += surplus * holder.shares[bank.id] / shares_held
        elif surplus > 0.0 and self.people:
            founder = next((p for p in self.people if p.id == bank.founder_id), None)
            recipients = [founder] if founder is not None else self.people
            for person in recipients:
                person.cash += surplus / len(recipients)
        else:
            surplus = 0.0
        bank.bank_equity -= surplus
        bank.cash -= surplus
        self._resolve_failed_bank(bank, banks, kind="bank_exit" if surplus > 0.0 else "bank")

    def _resolve_failed_bank(self, failed_bank, banks: list, kind: str = "bank") -> None:
        """Résolution d'une banque en faillite : renflouement interne, reprise, État.

        Les banques qui lui ont prêté sur le marché interbancaire absorbent d'abord
        le trou de fonds propres, au prorata de leurs créances et dans leur limite
        (leurs fonds propres baissent d'autant, ce qui peut les mettre en faillite à
        leur tour). Une concurrente reprend ensuite prêts, titres, trésorerie et le
        reste des dettes interbancaires ; l'État comble le trou restant. ``kind`` est
        le type de résolution consigné (« bank_exit » : sortie d'une banque solvable).
        """
        receivers = [b for b in banks if b.active and b.id != failed_bank.id]
        if not receivers:
            return
        hole = max(0.0, -failed_bank.bank_equity)
        claims = {
            lender.id: lender.interbank_claims[failed_bank.id]
            for lender in receivers
            if lender.interbank_claims.get(failed_bank.id, 0.0) > 0.0
        }
        total_claims = sum(claims.values())
        creditor_loss = 0.0
        if total_claims > 0.0 and hole > 0.0:
            loss_ratio = min(1.0, hole / total_claims)
            for lender in receivers:
                claim = claims.get(lender.id, 0.0)
                if claim <= 0.0:
                    continue
                loss = claim * loss_ratio
                lender.interbank_claims[failed_bank.id] = claim - loss
                lender.bank_equity -= loss
                lender.expenses += loss
                lender.default_loss += loss
                failed_bank.bank_equity += loss
                creditor_loss += loss
        receiver = min(
            receivers,
            key=lambda bank: sum(bank.bank_loan_accounts.values()),
        )
        _transfer_interbank_position(failed_bank, receiver, self.companies)
        receiver.cash += failed_bank.cash
        receiver.bond_holdings += failed_bank.bond_holdings
        failed_bank.bond_holdings = 0.0
        # les actifs réels (technologie, capital) passent au repreneur : laissés
        # sur la banque fermée, ils sortiraient du bilan et fausseraient les dépôts
        receiver.technology_asset_value += failed_bank.technology_asset_value
        receiver.productive_capital_value += failed_bank.productive_capital_value
        failed_bank.technology_asset_value = 0.0
        failed_bank.productive_capital_value = 0.0
        # le trou restant : comblé par l'État (titres publics) selon la politique de
        # résolution ; le reste est absorbé par les fonds propres du repreneur (qui
        # peut à son tour faire faillite)
        deficit = max(0.0, -failed_bank.bank_equity)
        public = self._public_share_of_bank_hole(deficit)
        receiver.bond_holdings += public
        receiver.bank_equity += max(0.0, failed_bank.bank_equity) - (deficit - public)
        self._log_resolution(
            failed_bank.id, kind, public_cost=public, creditor_loss=creditor_loss,
            absorbed_by_acquirer=deficit - public,
        )
        for borrower_id, balance in failed_bank.bank_loan_accounts.items():
            receiver.bank_loan_accounts[borrower_id] = (
                receiver.bank_loan_accounts.get(borrower_id, 0.0) + balance
            )
        failed_bank.cash = 0.0
        failed_bank.bank_equity = 0.0
        failed_bank.bank_loan_accounts.clear()
        failed_bank.active = False
        failed_bank.exit_reason = "resolved"
        failed_bank.exit_month = self.month
        failed_bank.stock_price = 0.0
        failed_bank.shares_outstanding = 0.0
        failed_bank.treasury_shares = 0.0
        for owner in [*self.people, *self.companies]:
            owner.shares.pop(failed_bank.id, None)

    # ------------------------------------------------------------------
    # Dette publique : les banques souscrivent les titres de l'État
    # ------------------------------------------------------------------
    def finance_government(self, amount: float) -> float:
        """Les banques souscrivent des titres publics : l'achat crée un dépôt de l'État.

        Plafonné par ``bond_capacity_share`` de la capacité de crédit des
        banques : la dette publique évince le crédit privé. La monnaie est créée
        par les banques (aucune réserve n'est consommée).
        """
        banks = [b for b in self.companies if b.active and b.sector == "bank"]
        amount = min(
            max(0.0, amount), self.credit_capacity() * self.live.bond_capacity_share
        )
        if amount <= 0.0 or not banks:
            return 0.0
        for bank, weight in self._capacity_weights():
            bank.bond_holdings += amount * weight
        return amount

    def _bond_holders(self) -> tuple[list, list, float]:
        """Porteurs de la dette publique : banques, ménages et entreprises, encours total.

        Les entreprises non bancaires porteuses sont ajoutées à la liste des ménages
        (même traitement : un remboursement ou un intérêt est un transfert de l'État).
        """
        banks = [b for b in self.companies if b.active and b.sector == "bank" and b.bond_holdings > 0.0]
        households = [p for p in self.people if p.bond_holdings > 0.0]
        held = sum(b.bond_holdings for b in banks) + sum(p.bond_holdings for p in households)
        firms = [c for c in self.companies if c.sector != "bank" and c.bond_holdings > 0.0]
        if firms:
            households = households + firms
            held += sum(c.bond_holdings for c in firms)
        return banks, households, held

    def repay_government_bonds(self, amount: float) -> float:
        """L'État rembourse des titres au prorata des porteurs.

        Auprès d'une banque, le dépôt public et le titre disparaissent ; auprès
        d'un ménage ou d'une entreprise, le remboursement est un transfert de
        l'État au porteur.
        """
        banks, households, held = self._bond_holders()
        amount = min(max(0.0, amount), held)
        if amount <= 0.0:
            return 0.0
        for bank in banks:
            bank.bond_holdings = max(0.0, bank.bond_holdings - amount * bank.bond_holdings / held)
        for person in households:
            part = min(person.bond_holdings, amount * person.bond_holdings / held)
            person.bond_holdings -= part
            person.cash += part
        return amount

    def pay_bond_interest(self, amount: float) -> float:
        """Intérêts de la dette publique : revenu des banques, des ménages et des entreprises.

        L'intérêt versé à une entreprise non bancaire est imposé à la source au
        taux de l'impôt sur les bénéfices : la part retenue reste à l'État.
        """
        banks, households, held = self._bond_holders()
        if amount <= 0.0 or held <= 0.0:
            return 0.0
        for bank in banks:
            part = amount * bank.bond_holdings / held
            bank.cash += part
            bank.revenue += part
        tax_rate = min(1.0, max(0.0, self.live.corporate_tax_rate))
        for person in households:
            part = amount * person.bond_holdings / held
            if isinstance(person, Company):
                withheld = part * tax_rate
                self.public.cash += withheld
                self.public.revenue += withheld
                person.cash += part - withheld
                person.financial_income += part - withheld
                continue
            person.cash += part
            person.income_this_turn += part
            person.dividend_income_this_turn += part
            person.lifetime_earnings += part
        return amount

    def _prepay_company_loan(self, company: Company, amount: float) -> float:
        """Remboursement anticipé : la dette et le dépôt disparaissent, les créances
        des banques prêteuses baissent au prorata (aucun intérêt en jeu)."""
        amount = min(max(0.0, amount), company.debt, max(0.0, company.cash))
        if amount <= 0.0:
            return 0.0
        before = company.debt
        company.debt -= amount
        company.cash -= amount
        for bank in self.companies:
            if bank.sector == "bank" and company.id in bank.bank_loan_accounts:
                bank.bank_loan_accounts[company.id] *= company.debt / before
        return amount
