"""Lot S (novembre 2026) : relations bancaires individuelles.

Un réglage, ``bank_relationships``, désactivé par défaut (aucun calcul, aucun tirage, aucun
attribut ajouté sans lui). L'état vit dans ``simulation.bankrel`` (``BankRelState``), créé au
premier usage ; les tirages viennent d'un générateur dédié.

Sans ce réglage, un nouveau prêt est réparti entre toutes les banques selon leur capacité libre
et les dépôts sont des parts de marché agrégées. Avec lui :

- **Une banque par agent** : chaque ménage, entreprise non bancaire, fonds et l'État a une banque
  principale. Les dépôts d'une banque sont la trésorerie de ses clients (la part qui est un
  dépôt, pas de la monnaie fiduciaire).
- **Choix du prêteur** : l'emprunteur s'adresse d'abord à sa banque, qui prête dans la limite de
  sa capacité ; le reste va aux autres banques, davantage à celles qui connaissent déjà son
  secteur (``bank_specialization``). Les risques se concentrent : une banque porte ses clients.
- **Taux des dépôts par banque** : chaque ménage reçoit le taux de sa banque, payé par elle.
- **Changements de banque** : une part de la clientèle compare chaque mois les banques. Un
  ménage part pour un taux meilleur que le coût de changement ; une entreprise suit son prêteur
  principal. Une banque dont les fonds propres passent sous ``bank_run_threshold`` fois
  l'exigence est fuie : ses clients la quittent, y compris hors de leur tour.
- **Règlements entre banques** : une banque qui prête plus que ses fonds propres et les dépôts
  de ses clients emprunte l'écart aux banques excédentaires, au taux directeur. La fuite des
  dépôts a donc un coût pour la banque quittée et un revenu pour celles qui les reçoivent.
- **Faillite localisée** : les clients d'une banque résolue passent au repreneur ; quand aucun
  repreneur n'existe, la décote des dépôts frappe d'abord les clients de la banque en cause.

Ce module ne branche pas le prototype en centimes (``bank_ledger.py``) : les soldes restent la
trésorerie en flottants du moteur, et les dépôts par banque en sont une lecture par client.
Aucune monnaie n'est créée ni détruite par ces règles (transferts entre banques).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from economy.models import positive_loan_book

BANKREL_SEED_OFFSET = 122949829  # générateur dédié (première banque d'un agent, fuite)
HOUSEHOLD_BOOKS = {"mortgage": "credit_immobilier", "consumer": "credit_consommation"}
LENDER_LOCK = 2.0               # coût de changement d'un emprunteur, en multiples de celui d'un déposant
RUN_SHARE = 0.5                 # part maximale des clients hors tour qui fuient une banque fragile en un mois


@dataclass
class BankRelState:
    """État propre au lot S, créé au premier usage."""

    random: random.Random
    home: dict = field(default_factory=dict)        # agent -> banque principale
    successor: dict = field(default_factory=dict)   # banque résolue -> repreneur
    sectors: dict = field(default_factory=dict)     # entreprise -> secteur (cache du mois)
    books: dict = field(default_factory=dict)       # banque -> {secteur: encours} (cache du mois)
    books_month: int = -1
    positions: dict = field(default_factory=dict)   # banque -> écart de financement du mois
    deposits: dict = field(default_factory=dict)    # banque -> dépôts de ses clients
    log: list = field(default_factory=list)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)


def account_owner(account: str) -> tuple[str, str | None]:
    """Titulaire d'un compte de prêt et, pour un ménage, la nature du crédit :
    ``mortgage:p0001`` -> (``p0001``, ``mortgage``) ; ``food_1`` -> (``food_1``, ``None``)."""
    kind, _, owner = account.partition(":")
    return (owner, kind) if owner and kind in HOUSEHOLD_BOOKS else (account, None)


class BankRelationsMixin:
    """Banque principale, choix du prêteur, changements de banque, règlements (lot S)."""

    # ------------------------------------------------------------------ état
    def _bankrel_state(self) -> BankRelState:
        state = getattr(self, "bankrel", None)
        if state is None:
            state = self.bankrel = BankRelState(random.Random(self.config.seed + BANKREL_SEED_OFFSET))
        return state

    def _bankrel_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._bankrel_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    def _active_banks(self) -> list:
        return [b for b in self.companies if b.sector == "bank" and b.active]

    def _bank_clients(self) -> list[tuple]:
        """(identifiant, porteur de trésorerie) de chaque client possible d'une banque : ménages,
        entreprises non bancaires en activité, État, fonds des banques."""
        clients = [(p.id, p) for p in self.people]
        clients.extend((c.id, c) for c in self.companies if c.active and c.sector != "bank")
        clients.append((self.public.account.id, self.public))
        enterprise = getattr(self, "enterprise", None)
        if enterprise is not None:
            clients.extend((fund.id, fund) for fund in enterprise.funds)
        clients.extend((account.id, account) for account in getattr(self, "world_accounts", ()))
        return clients

    def _home_bank(self, agent_id: str, banks: list | None = None) -> str | None:
        """Banque principale d'un agent. À défaut (nouvel agent, banque disparue sans repreneur
        connu), il en choisit une au hasard, selon les parts de marché des dépôts."""
        state = self._bankrel_state()
        banks = banks if banks is not None else self._active_banks()
        if not banks:
            return None
        ids = {b.id for b in banks}
        home = state.home.get(agent_id)
        seen = set()
        while home is not None and home not in ids and home in state.successor and home not in seen:
            seen.add(home)
            home = state.successor[home]
        if home in ids:
            state.home[agent_id] = home
            return home
        weights = [max(b.deposit_share, 0.0) for b in banks]
        total = sum(weights)
        if total <= 0.0:
            weights, total = [1.0] * len(banks), float(len(banks))
        draw = state.random.random() * total
        chosen = banks[-1]
        for bank, weight in zip(banks, weights):
            draw -= weight
            if draw < 0.0:
                chosen = bank
                break
        state.home[agent_id] = chosen.id
        return chosen.id

    def _bank_solidity(self, bank) -> float:
        """Fonds propres d'une banque rapportés à ceux qu'exige son encours (1 : juste conforme)."""
        bonds = bank.bond_holdings
        if self.live.sovereign_risk_weight != 1.0:
            bonds *= max(0.0, self.live.sovereign_risk_weight)  # lot U, comme ``_bank_capacity``
        exposure = positive_loan_book(bank.bank_loan_accounts) + bonds
        required = max(self.live.capital_requirement, 1e-6) * exposure
        if required <= 0.0:
            return float("inf")
        return max(0.0, bank.bank_equity) / required

    # ------------------------------------------------------------------ choix du prêteur
    def _sector_books(self) -> dict:
        """Encours de chaque banque par secteur d'emprunteur, recalculé une fois par mois puis
        tenu à jour à chaque prêt inscrit."""
        state = self._bankrel_state()
        if state.books_month != self.month:
            state.books_month = self.month
            state.sectors = {c.id: c.sector for c in self.companies}
            state.books = {}
            for bank in self._active_banks():
                book: dict[str, float] = {}
                for account, balance in bank.bank_loan_accounts.items():
                    if balance <= 0.0:
                        continue
                    key = self._account_sector(account)
                    book[key] = book.get(key, 0.0) + balance
                state.books[bank.id] = book
        return state.books

    def _account_sector(self, account: str) -> str:
        owner, kind = account_owner(account)
        if kind is not None:
            return HOUSEHOLD_BOOKS[kind]
        return self.bankrel.sectors.get(owner, "autre")

    def _lender_appetites(self, banks: list, account: str) -> tuple:
        """Appétit de chaque banque pour un nouveau prêt, banque principale de l'emprunteur et
        capacités libres. Appétit : capacité libre × (1 + ``bank_specialization`` × la part de
        son encours déjà prêtée au secteur de l'emprunteur)."""
        capacities = {b.id: self._bank_capacity(b) for b in banks}
        owner, _ = account_owner(account)
        home = self._home_bank(owner, banks)
        books = self._sector_books()
        sector = self._account_sector(account)
        gain = max(0.0, self.live.bank_specialization)
        appetite = {}
        for bank in banks:
            book = books.get(bank.id, {})
            total = sum(book.values())
            familiarity = book.get(sector, 0.0) / total if total > 0.0 else 0.0
            appetite[bank.id] = capacities[bank.id] * (1.0 + gain * familiarity)
        return [(bank, appetite[bank.id]) for bank in banks], home, capacities

    def _allocate_loan(self, account: str, amount: float) -> list[tuple]:
        """Montant du prêt porté par chaque banque : la banque principale de l'emprunteur
        d'abord, dans la limite de sa capacité, puis les autres selon leur appétit, chacune dans
        sa capacité (la capacité du système est vérifiée par l'appelant). Sans aucune capacité
        (encours initial), le prêt est réparti également, comme sans le réglage."""
        banks = self._active_banks()
        if not banks:
            return []
        appetites, home, capacities = self._lender_appetites(banks, account)
        if sum(capacities.values()) <= 0.0:
            return [(bank, amount / len(banks)) for bank in banks]
        by_id = {bank.id: bank for bank, _ in appetites}
        shares: dict[str, float] = {}
        remaining = amount
        if home in by_id:
            taken = min(remaining, capacities[home])
            if taken > 0.0:
                shares[home] = taken
                remaining -= taken
        candidates = {bank.id: weight for bank, weight in appetites if bank.id != home and weight > 0.0}
        room = {bank_id: capacities[bank_id] for bank_id in candidates}
        while remaining > 1e-9 and candidates:
            total = sum(candidates.values())
            saturated = []
            granted = 0.0
            for bank_id, weight in candidates.items():
                part = min(remaining * weight / total, room[bank_id])
                if part > 0.0:
                    shares[bank_id] = shares.get(bank_id, 0.0) + part
                    room[bank_id] -= part
                    granted += part
                if room[bank_id] <= 1e-9:
                    saturated.append(bank_id)
            remaining -= granted
            for bank_id in saturated:
                del candidates[bank_id]
            if granted <= 0.0:
                break
        if remaining > 0.0:
            # arrondi de la somme des capacités : le solde va à la banque la plus capable
            best = max(by_id.values(), key=lambda b: (capacities[b.id], b.id))
            shares[best.id] = shares.get(best.id, 0.0) + remaining
        main = shares.get(home, 0.0)
        self._bankrel_count("prets_inscrits", amount)
        self._bankrel_count("prets_banque_principale", main)
        sector = self._account_sector(account)
        for bank_id, part in shares.items():
            book = self.bankrel.books.setdefault(bank_id, {})
            book[sector] = book.get(sector, 0.0) + part
        return [(by_id[bank_id], part) for bank_id, part in shares.items()]

    # ------------------------------------------------------------------ dépôts par banque
    def _deposit_ratio(self) -> float:
        """Part de la trésorerie des agents qui est un dépôt (le reste : monnaie fiduciaire)."""
        money = self.ledger.nonbank_cash(self)
        deposits = max(0.0, self.ledger.deposits_from_cash(self))
        return min(1.0, deposits / money) if money > 0.0 else 0.0

    def bank_deposits(self) -> dict[str, float]:
        """Dépôts de chaque banque en activité : trésorerie de ses clients × part en dépôts."""
        banks = self._active_banks()
        totals = {b.id: 0.0 for b in banks}
        if not banks:
            return totals
        ratio = self._deposit_ratio()
        for agent_id, holder in self._bank_clients():
            home = self._home_bank(agent_id, banks)
            totals[home] += max(0.0, holder.cash) * ratio
        return totals

    def _relationship_offers(self, banks: list, reference: float) -> None:
        """Part de marché et taux de dépôt de chaque banque, d'après les dépôts de ses clients :
        même règle que ``_bank_deposit_charges`` (taux de référence relevé de
        ``deposit_liquidity_sensitivity`` × son besoin de financement), sans glissement des parts
        puisque ce sont les clients qui se déplacent."""
        live = self.live
        deposits = self.bank_deposits()
        self.bankrel.deposits = deposits
        total = sum(deposits.values())
        count = len(banks)
        books = [positive_loan_book(b.bank_loan_accounts) for b in banks]
        total_books = sum(books)
        for bank, book in zip(banks, books):
            bank.deposit_share = deposits[bank.id] / total if total > 0.0 else 1.0 / count
            loan_share = book / total_books if total_books > 0.0 else 1.0 / count
            funding_gap = max(-1.0, min(1.0, (loan_share - bank.deposit_share) * count))
            need = live.bank_liquidity_months * max(0.0, bank.previous_revenue - bank.previous_profit)
            shortage = max(0.0, min(1.0, (need - bank.cash) / need)) if need > 0.0 else 0.0
            tightness = max(-1.0, min(1.0, funding_gap + shortage))
            bank.deposit_rate_offer = max(0.0, reference + live.deposit_liquidity_sensitivity * tightness)

    def _pay_relationship_interest(self) -> float:
        """Intérêts des dépôts quand chaque ménage a sa banque : il reçoit le taux qu'elle
        offre, sur la part de sa trésorerie qui est un dépôt, payé par elle dans la limite de sa
        trésorerie propre. ``deposit_rate`` : taux moyen pondéré par les dépôts."""
        banks = self._active_banks()
        reference = max(0.0, self.policy_rate - self.live.deposit_spread)
        if not banks:
            self.deposit_rate = reference
            return 0.0
        self._relationship_offers(banks, reference)
        self.deposit_rate = sum(b.deposit_share * b.deposit_rate_offer for b in banks)
        ratio = self._deposit_ratio()
        if ratio <= 0.0 or self.deposit_rate <= 0.0:
            return 0.0
        home = self.bankrel.home
        savers: dict[str, list] = {b.id: [] for b in banks}
        for person in self.people:
            savers[home[person.id]].append(person)
        paid_total = 0.0
        for bank in banks:
            rate = bank.deposit_rate_offer * ratio
            due = sum(p.cash for p in savers[bank.id]) * rate
            paid = min(due, max(0.0, bank.cash))
            if due <= 0.0 or paid <= 0.0:
                continue
            bank.cash -= paid
            bank.expenses += paid
            factor = paid / due
            for person in savers[bank.id]:
                interest = person.cash * rate * factor
                person.cash += interest
                person.income_this_turn += interest
                person.dividend_income_this_turn += interest
                person.lifetime_earnings += interest
            paid_total += paid
        return paid_total

    # ------------------------------------------------------------------ phase 1 : clientèle
    def _bank_relations_month(self) -> None:
        """Début de mois, après le marché interbancaire : rattachement des nouveaux agents,
        changements de banque, fuite des banques fragiles, règlement des écarts de financement."""
        state = self._bankrel_state()
        banks = self._active_banks()
        if not banks:
            return
        for bank in self.companies:
            if bank.sector == "bank" and not bank.active and bank.acquired_by and bank.id not in state.successor:
                state.successor[bank.id] = bank.acquired_by  # banque absorbée par une concurrente
        self._sector_books()
        clients = self._bank_clients()
        existing = {agent_id for agent_id, _ in clients}
        state.home = {agent_id: bank for agent_id, bank in state.home.items() if agent_id in existing}
        for agent_id, _ in clients:
            self._home_bank(agent_id, banks)
        if len(banks) > 1:
            self._review_banks(banks, clients)
        self._settle_funding(banks)
        state.successor = {old: new for old, new in state.successor.items()
                           if any(home == old for home in state.home.values())}

    def _main_lender(self, agent_id: str, banks: list) -> str | None:
        """Banque qui porte le plus gros encours d'un emprunteur (``None`` sans dette)."""
        accounts = (agent_id, f"mortgage:{agent_id}", f"consumer:{agent_id}")
        best, largest = None, 0.0
        for bank in banks:
            balance = sum(bank.bank_loan_accounts.get(account, 0.0) for account in accounts)
            if balance > largest:
                best, largest = bank.id, balance
        return best

    def _review_banks(self, banks: list, clients: list) -> None:
        """Changements de banque du mois.

        Chaque client compare les banques à son tour (``bank_review_share`` de la clientèle par
        mois, sans tirage). Une banque fragile (solidité sous ``bank_run_threshold``) n'attire
        personne et ses clients la quittent : tous ceux dont c'est le tour, et les autres avec
        une probabilité de ``RUN_SHARE`` × (1 − solidité / seuil). Hors fuite, un ménage part
        pour la banque solide au meilleur taux si le gain dépasse le coût de changement
        (``deposit_switching_cost``, doublé s'il y est emprunteur) ; une entreprise, un fonds ou
        l'État, dont les dépôts ne sont pas rémunérés, rejoint son prêteur principal."""
        live = self.live
        state = self.bankrel
        threshold = max(0.0, live.bank_run_threshold)
        solidity = {b.id: self._bank_solidity(b) for b in banks}
        solid = [b for b in banks if solidity[b.id] >= threshold]
        if not solid:
            return
        best = max(solid, key=lambda b: (b.deposit_rate_offer, b.id))
        offers = {b.id: b.deposit_rate_offer for b in banks}
        period = max(1, round(1.0 / max(live.bank_review_share, 1e-6)))
        turn = self.month % period
        households = {p.id for p in self.people}
        rng = state.random
        for index, (agent_id, _) in enumerate(clients):
            home = state.home[agent_id]
            reviewing = index % period == turn
            fragile = solidity[home] < threshold
            if fragile:
                panic = RUN_SHARE * (1.0 - solidity[home] / threshold) if threshold > 0.0 else 0.0
                if reviewing or rng.random() < panic:
                    state.home[agent_id] = best.id
                    self._bankrel_count("fuites")
                continue
            if not reviewing:
                continue
            lender = self._main_lender(agent_id, banks)
            if agent_id in households:
                cost = live.deposit_switching_cost * (LENDER_LOCK if lender == home else 1.0)
                if best.id != home and offers[best.id] - offers[home] > cost:
                    state.home[agent_id] = best.id
                    self._bankrel_count("changements")
            elif lender is not None and lender != home and solidity[lender] >= threshold:
                state.home[agent_id] = lender
                self._bankrel_count("changements")

    def _settle_funding(self, banks: list) -> None:
        """Règlement entre banques des écarts de financement.

        Besoin de financement d'une banque : ses prêts et ses titres, moins ses fonds propres.
        Écart : ce besoin moins les dépôts de ses clients. Les banques dont l'écart est positif
        empruntent aux banques excédentaires, au prorata, dans la limite de ce qu'elles offrent,
        et leur paient l'intérêt du mois au taux directeur (charge de l'une, revenu de l'autre,
        dans la limite de la trésorerie propre de l'emprunteuse). Le reste d'un écart est couvert
        par les avances de la banque centrale, comme dans le moteur."""
        state = self.bankrel
        deposits = self.bank_deposits()
        state.deposits = deposits
        gaps = {}
        for bank in banks:
            need = positive_loan_book(bank.bank_loan_accounts) + bank.bond_holdings - max(0.0, bank.bank_equity)
            gaps[bank.id] = need - deposits[bank.id]
        state.positions = gaps
        wanted = sum(gap for gap in gaps.values() if gap > 0.0)
        offered = -sum(gap for gap in gaps.values() if gap < 0.0)
        matched = min(wanted, offered)
        rate = max(0.0, self.policy_rate)
        self._bankrel_count("ecart_financement", wanted)
        if matched <= 0.0 or rate <= 0.0:
            return
        by_id = {b.id: b for b in banks}
        collected = 0.0
        for bank_id, gap in gaps.items():
            if gap <= 0.0:
                continue
            borrower = by_id[bank_id]
            paid = min(gap * matched / wanted * rate, max(0.0, borrower.cash))
            borrower.cash -= paid
            borrower.expenses += paid
            collected += paid
        for bank_id, gap in gaps.items():
            if gap >= 0.0:
                continue
            lender = by_id[bank_id]
            part = collected * (-gap) / offered
            lender.cash += part
            lender.revenue += part
        self._bankrel_count("interets_entre_banques", collected)

    # ------------------------------------------------------------------ faillite d'une banque
    def _transfer_clients(self, failed_bank, receiver) -> None:
        """Résolution : les clients de la banque fermée suivront leurs comptes chez le repreneur."""
        state = self._bankrel_state()
        state.successor[failed_bank.id] = receiver.id
        moved = sum(1 for home in state.home.values() if home == failed_bank.id)
        self._bankrel_count("clients_transferes", moved)
        state.log.append({
            "mois": self.month, "type": "reprise",
            "texte": f"{failed_bank.name} fermée : {moved} clients repris par {receiver.name}",
        })

    def _localized_haircut(self, loss: float, bank) -> float:
        """Décote des dépôts d'une banque sans repreneur : ses clients d'abord, au prorata de
        leurs dépôts ; ce que leurs dépôts ne couvrent pas retombe sur les autres déposants
        (la monnaie détruite égale toujours le trou comblé). Retourne le montant détruit."""
        state = self._bankrel_state()
        holders = [(agent_id, holder) for agent_id, holder in self._bank_clients()
                   if holder.cash > 0.0 and agent_id != self.public.account.id]
        own = [holder for agent_id, holder in holders if state.home.get(agent_id) == bank.id]
        others = [holder for agent_id, holder in holders if state.home.get(agent_id) != bank.id]
        destroyed = 0.0
        for group in (own, others):
            remaining = loss - destroyed
            total = sum(h.cash for h in group)
            if remaining <= 0.0 or total <= 0.0:
                continue
            ratio = min(1.0, remaining / total)
            for holder in group:
                holder.cash -= holder.cash * ratio
            destroyed += total * ratio
            if group is own:
                self._bankrel_count("decote_clients", total * ratio)
        return destroyed

    # ------------------------------------------------------------------ phase 8 : relevé
    def _bankrel_record(self) -> None:
        """Relevé mensuel du lot S (mesures seules)."""
        state = self._bankrel_state()
        stats = state.month_stats
        banks = self._active_banks()
        deposits = state.deposits or {b.id: 0.0 for b in banks}
        total = sum(deposits.get(b.id, 0.0) for b in banks)
        stats["mois"] = self.month
        stats["banques"] = len(banks)
        stats["concentration_depots"] = (
            sum((deposits.get(b.id, 0.0) / total) ** 2 for b in banks) if total > 0.0 else 0.0
        )
        booked = stats.get("prets_inscrits", 0.0)
        stats["part_banque_principale"] = stats.get("prets_banque_principale", 0.0) / booked if booked > 0.0 else 0.0
        stats["banques_fragiles"] = sum(
            1 for b in banks if self._bank_solidity(b) < self.live.bank_run_threshold
        )
        for key in ("changements", "fuites", "ecart_financement", "interets_entre_banques"):
            stats.setdefault(key, 0.0)
        state.history.append(stats)
        state.month_stats = {}

    # ------------------------------------------------------------------ lectures pour l'interface
    def bank_relations_table(self) -> list[dict]:
        """Une ligne par banque en activité : clients, dépôts, prêts, écart, taux, spécialité."""
        state = getattr(self, "bankrel", None)
        if state is None:
            return []
        banks = self._active_banks()
        households = {p.id for p in self.people}
        rows = []
        for bank in banks:
            clients = [agent for agent, home in state.home.items() if home == bank.id]
            book: dict[str, float] = {}
            for account, balance in bank.bank_loan_accounts.items():
                if balance > 0.0:
                    owner, kind = account_owner(account)
                    key = HOUSEHOLD_BOOKS[kind] if kind else state.sectors.get(owner, "autre")
                    book[key] = book.get(key, 0.0) + balance
            loans = sum(book.values())
            top = max(book, key=lambda k: (book[k], k)) if book else ""
            solidity = self._bank_solidity(bank)
            rows.append({
                "id": bank.id, "nom": bank.name,
                "menages": sum(1 for agent in clients if agent in households),
                "autres_clients": sum(1 for agent in clients if agent not in households),
                "depots": state.deposits.get(bank.id, 0.0), "prets": loans,
                "ecart_financement": state.positions.get(bank.id, 0.0),
                "taux_depots_annuel": 12.0 * bank.deposit_rate_offer,
                "solidite": solidity if solidity != float("inf") else None,
                "specialite": top, "part_specialite": book[top] / loans if loans > 0.0 else 0.0,
            })
        return rows

    def bank_relations_history(self) -> list[dict]:
        """Relevés mensuels du lot S depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "bankrel", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset} for row in state.history if row["mois"] - offset >= 0]
