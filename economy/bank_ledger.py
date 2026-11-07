"""Fondation isolée de comptabilité bancaire en unités monétaires entières.

Ce registre n'est pas encore branché sur la simulation. Les actifs sont positifs
et les passifs négatifs ; une écriture est équilibrée sur l'ensemble des bilans.
Le choix et la conversion de l'unité doivent être faits avant son intégration.
"""

from dataclasses import dataclass, field
from fractions import Fraction


Account = tuple[str, str, str]
_PAIRED = {
    "deposit_asset": "deposit_liability",
    "deposit_liability": "deposit_asset",
    "loan_asset": "loan_liability",
    "loan_liability": "loan_asset",
    "reserves": "reserve_liability",
    "reserve_liability": "reserves",
    "currency": "currency_liability",
    "currency_liability": "currency",
}


def _account_kind(kind: str) -> str:
    """Catégorie d'un compte, indépendamment de l'identifiant du contrat."""
    return kind.split(":", 1)[0]


def _counterpart_kind(kind: str) -> str | None:
    base, separator, contract = kind.partition(":")
    inverse = _PAIRED.get(base)
    return inverse + separator + contract if inverse else None


class AccountingError(ValueError):
    """Écriture impossible ou incohérence entre créances et dettes."""


def allocate_units(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Répartit un total entier exactement, selon les plus grands restes.

    Les poids représentent des parts relatives ; à reste égal, l'identifiant
    départage les comptes. Aucun choix d'unité monétaire n'est imposé ici.
    """
    if type(total) is not int or total < 0:
        raise AccountingError("Total invalide")
    try:
        parts = {key: Fraction(str(value)) for key, value in weights.items()}
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise AccountingError("Poids invalide") from exc
    if any(value < 0 for value in parts.values()) or (total and sum(parts.values()) <= 0):
        raise AccountingError("Poids négatif ou somme nulle")
    if not parts:
        return {}
    weight_sum = sum(parts.values())
    if weight_sum == 0:
        return {key: 0 for key in parts}
    exact = {key: total * weight / weight_sum for key, weight in parts.items()}
    result = {key: value.numerator // value.denominator for key, value in exact.items()}
    remaining = total - sum(result.values())
    order = sorted(parts, key=lambda key: (-(exact[key] - result[key]), key))
    for key in order[:remaining]:
        result[key] += 1
    return result


@dataclass
class BankLedger:
    """Journal en unités entières ; aucune écriture partielle en cas d'erreur."""

    balances: dict[Account, int] = field(default_factory=dict)
    journal: list[tuple[tuple[Account, int], ...]] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)  # identifiant de logement -> propriétaire

    def post(self, entries: list[tuple[Account, int]]) -> None:
        """Valide toute l'écriture avant de modifier les comptes."""
        if any(type(amount) is not int for _, amount in entries):
            raise AccountingError("Les montants doivent être des entiers")
        if sum(amount for _, amount in entries) != 0:
            raise AccountingError("Écriture non équilibrée")
        by_owner: dict[str, int] = {}
        for (owner, _, _), amount in entries:
            by_owner[owner] = by_owner.get(owner, 0) + amount
        if any(by_owner.values()):
            raise AccountingError("Écriture non équilibrée par agent")
        changes: dict[Account, int] = {}
        for account, amount in entries:
            changes[account] = changes.get(account, 0) + amount
        updated = {key: self.balances.get(key, 0) + value for key, value in changes.items()}
        for (owner, kind, counterparty), value in updated.items():
            base = _account_kind(kind)
            if base in {"deposit_asset", "loan_asset", "reserves", "currency"} and value < 0:
                raise AccountingError(f"Actif insuffisant : {owner}/{kind}/{counterparty}")
            if base in {"deposit_liability", "loan_liability", "reserve_liability", "currency_liability"} and value > 0:
                raise AccountingError(f"Passif incohérent : {owner}/{kind}/{counterparty}")
            inverse = _counterpart_kind(kind)
            if inverse:
                key = (counterparty, inverse, owner)
                counterpart = updated.get(key, self.balances.get(key, 0))
                if value != -counterpart:
                    raise AccountingError(f"Contrepartie incohérente : {owner}/{kind}/{counterparty}")
        self.balances.update(updated)
        self.journal.append(tuple(entries))

    def issue_loan(self, bank: str, borrower: str, amount: int, approved: int,
                   *, contract: str = "") -> int:
        """Inscrit simultanément créance, dette et dépôt, dans la limite autorisée."""
        if type(amount) is not int or type(approved) is not int or amount <= 0 or approved < amount:
            raise AccountingError("Prêt refusé ou montant invalide")
        suffix = f":{contract}" if contract else ""
        self.post([
            ((bank, "loan_asset" + suffix, borrower), amount),
            ((borrower, "loan_liability" + suffix, bank), -amount),
            ((borrower, "deposit_asset", bank), amount),
            ((bank, "deposit_liability", borrower), -amount),
        ])
        return amount

    def exchange_currency(self, owner: str, bank: str, amount: int, *, deposit: bool) -> None:
        """Dépose ou retire des espèces sans création de monnaie."""
        if type(amount) is not int or amount <= 0:
            raise AccountingError("Montant invalide")
        direction = 1 if deposit else -1
        self.post([
            ((owner, "currency", "central_bank"), -direction * amount),
            ((owner, "deposit_asset", bank), direction * amount),
            ((bank, "currency", "central_bank"), direction * amount),
            ((bank, "deposit_liability", owner), -direction * amount),
            (("central_bank", "currency_liability", owner), direction * amount),
            (("central_bank", "currency_liability", bank), -direction * amount),
        ])

    def repay_loan(self, bank: str, borrower: str, amount: int,
                   deposit_bank: str | None = None, *, contract: str = "") -> None:
        """Remboursement du capital, réglé entre banques si nécessaire."""
        if type(amount) is not int or amount <= 0:
            raise AccountingError("Montant invalide")
        deposit_bank = bank if deposit_bank is None else deposit_bank
        suffix = f":{contract}" if contract else ""
        entries = [
            ((bank, "loan_asset" + suffix, borrower), -amount),
            ((borrower, "loan_liability" + suffix, bank), amount),
            ((borrower, "deposit_asset", deposit_bank), -amount),
            ((deposit_bank, "deposit_liability", borrower), amount),
        ]
        if bank != deposit_bank:
            entries.extend([
                ((deposit_bank, "reserves", "central_bank"), -amount),
                ((bank, "reserves", "central_bank"), amount),
                (("central_bank", "reserve_liability", deposit_bank), amount),
                (("central_bank", "reserve_liability", bank), -amount),
            ])
        self.post(entries)

    def write_off_loan(self, bank: str, borrower: str, amount: int,
                       *, contract: str = "") -> None:
        """Passe une créance en perte sans détruire le dépôt précédemment créé."""
        if type(amount) is not int or amount <= 0:
            raise AccountingError("Montant invalide")
        suffix = f":{contract}" if contract else ""
        self.post([
            ((bank, "loan_asset" + suffix, borrower), -amount),
            ((bank, "credit_loss", borrower), amount),
            ((borrower, "loan_liability" + suffix, bank), amount),
            ((borrower, "debt_relief", bank), -amount),
        ])

    def transfer(self, sender: str, sender_bank: str, receiver: str,
                 receiver_bank: str, amount: int, *,
                 sender_account: str = "payment_contra",
                 receiver_account: str = "payment_contra") -> None:
        """Transfert avec contrepartie transitoire, réglé par les réserves.

        La nature définitive de la contrepartie (bien, charge ou revenu) doit être
        déterminée lors de l'intégration ; ce registre ne modélise pas les biens.
        """
        if type(amount) is not int or amount <= 0:
            raise AccountingError("Montant invalide")
        if not sender_account or not receiver_account or _counterpart_kind(sender_account) or _counterpart_kind(receiver_account):
            raise AccountingError("Contrepartie de paiement invalide")
        entries = [
            ((sender, "deposit_asset", sender_bank), -amount),
            ((sender_bank, "deposit_liability", sender), amount),
            ((receiver, "deposit_asset", receiver_bank), amount),
            ((receiver_bank, "deposit_liability", receiver), -amount),
            ((sender, sender_account, receiver), amount),
            ((receiver, receiver_account, sender), -amount),
        ]
        if sender_bank != receiver_bank:
            entries.extend([
                ((sender_bank, "reserves", "central_bank"), -amount),
                ((receiver_bank, "reserves", "central_bank"), amount),
                (("central_bank", "reserve_liability", sender_bank), amount),
                (("central_bank", "reserve_liability", receiver_bank), -amount),
            ])
        self.post(entries)

    def financed_payment(self, bank: str, borrower: str, seller: str,
                         seller_bank: str, loan: int, approved: int, price: int,
                         *, contract: str = "") -> None:
        """Valide prêt et paiement dans une seule écriture, sans commit partiel.

        La livraison du bien reste à coordonner avec cette écriture par l'appelant :
        ce prototype ne possède pas de registre des biens.
        """
        if any(type(n) is not int for n in (loan, approved, price)) or loan <= 0 or price <= 0 or loan > price or approved < loan:
            raise AccountingError("Prêt ou prix invalide, ou prêt refusé")
        suffix = f":{contract}" if contract else ""
        entries = [
            ((bank, "loan_asset" + suffix, borrower), loan),
            ((borrower, "loan_liability" + suffix, bank), -loan),
            ((borrower, "deposit_asset", bank), loan - price),
            ((bank, "deposit_liability", borrower), price - loan),
            ((seller, "deposit_asset", seller_bank), price),
            ((seller_bank, "deposit_liability", seller), -price),
            ((borrower, "payment_contra", seller), price),
            ((seller, "payment_contra", borrower), -price),
        ]
        if bank != seller_bank:
            entries.extend([((bank, "reserves", "central_bank"), -price),
                            ((seller_bank, "reserves", "central_bank"), price),
                            (("central_bank", "reserve_liability", bank), price),
                            (("central_bank", "reserve_liability", seller_bank), -price)])
        self.post(entries)

    def purchase_property(self, property_id: str, bank: str, buyer: str,
                          seller: str, seller_bank: str, loan: int,
                          approved: int, price: int, *, contract: str = "",
                          cash_deposit: int = 0) -> None:
        """Paie et livre un logement enregistré dans le même registre.

        Il s'agit du registre de biens du prototype, pas encore des logements
        du moteur. Une erreur laisse les comptes et le propriétaire inchangés.
        """
        if buyer == seller or self.properties.get(property_id) != seller:
            raise AccountingError("Logement indisponible ou vendeur invalide")
        old_balances = self.balances.copy()
        old_journal_length = len(self.journal)
        try:
            if cash_deposit:
                self.exchange_currency(buyer, bank, cash_deposit, deposit=True)
            self.financed_payment(bank, buyer, seller, seller_bank, loan, approved, price,
                                  contract=contract)
            self.properties[property_id] = buyer
        except Exception:
            self.balances.clear()
            self.balances.update(old_balances)
            del self.journal[old_journal_length:]
            self.properties[property_id] = seller
            raise

    def reconciliation_check(self) -> None:
        """Vérifie chaque paire de comptes et l'équilibre du bilan de chaque agent."""
        by_owner: dict[str, int] = {}
        for (owner, kind, other), value in self.balances.items():
            if type(value) is not int:
                raise AccountingError(f"Solde non entier : {owner}/{kind}/{other}")
            base = _account_kind(kind)
            if base in {"deposit_asset", "loan_asset", "reserves", "currency"} and value < 0:
                raise AccountingError(f"Actif négatif : {owner}/{kind}/{other}")
            if base in {"deposit_liability", "loan_liability", "reserve_liability", "currency_liability"} and value > 0:
                raise AccountingError(f"Passif positif : {owner}/{kind}/{other}")
            by_owner[owner] = by_owner.get(owner, 0) + value
            inverse = _counterpart_kind(kind)
            if inverse and value != -self.balances.get((other, inverse, owner), 0):
                raise AccountingError(f"Rapprochement impossible : {owner}/{kind}/{other}")
        if any(by_owner.values()):
            raise AccountingError("Bilan d'agent non équilibré")
