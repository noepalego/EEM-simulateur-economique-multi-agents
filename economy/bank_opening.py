"""Construction expérimentale d'un bilan initial bancaire en centimes.

Cette conversion est une lecture de l'état initial ; elle ne modifie pas le moteur
et ne convient pas à un état après chauffe, où les dépôts ne sont pas attribués.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

from economy.bank_ledger import AccountingError, BankLedger, allocate_units


def cents(value: float) -> int:
    """Arrondit un montant en euros au centime le plus proche, à égalité au pair."""
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise AccountingError("Montant non fini")
        return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AccountingError("Montant invalide") from exc


def initial_bank_ledger(sim) -> BankLedger:
    """Construit un bilan initial uniquement si les comptes d'origine le permettent.

    Répartit les centimes d'un même total par les plus grands restes, sans
    modifier le total arrondi. Rejette un dépôt déjà existant ou un désaccord
    significatif entre les dettes et les créances initiales.
    """
    if sim.month != 0 or sim.ledger.deposits(sim) != 0:
        raise AccountingError("Conversion limitée à l'état initial sans dépôts")
    banks = [c for c in sim.companies if c.active and c.sector == "bank"]
    if not banks:
        raise AccountingError("Aucune banque active")
    bank_ids = {b.id: f"bank:{b.id}" for b in banks}
    if len(bank_ids) != len(banks):
        raise AccountingError("Identifiants bancaires dupliqués")
    if sim.central_bank.advances or sim.public.debt or any(
        b.bond_holdings or b.technology_asset_value or b.productive_capital_value
        or b.interbank_claims for b in banks
    ):
        raise AccountingError("Bilan bancaire initial non pris en charge")
    debts = {c.id: (f"company:{c.id}", c.debt, "") for c in sim.companies
             if c.active and c.sector != "bank" and c.debt > 0}
    for person in sim.people:
        if person.mortgage_debt > 0:
            debts[f"mortgage:{person.id}"] = (f"person:{person.id}", person.mortgage_debt, "mortgage")
        if person.consumer_debt > 0:
            debts[f"consumer:{person.id}"] = (f"person:{person.id}", person.consumer_debt, "consumer")
    accounts = {key for b in banks for key, amount in b.bank_loan_accounts.items() if amount != 0}
    if accounts != set(debts):
        raise AccountingError("Comptes de prêts initiaux incomplets ou inconnus")
    if sum(cents(amount) for _, amount, _ in debts.values()) != cents(sum(amount for _, amount, _ in debts.values())):
        raise AccountingError("Arrondi individuel incompatible avec le total des dettes")
    entries = []
    loan_totals = {b.id: 0 for b in banks}
    for key, (borrower, amount, contract) in debts.items():
        amount_cents = cents(amount)
        weights = {b.id: b.bank_loan_accounts.get(key, 0.0) for b in banks}
        if cents(sum(weights.values())) != amount_cents:
            raise AccountingError(f"Créances initiales incohérentes : {key}")
        parts = allocate_units(amount_cents, weights)
        for bank_id, part in parts.items():
            if part:
                bank = bank_ids[bank_id]
                suffix = f":{contract}" if contract else ""
                entries.extend([((bank, "loan_asset" + suffix, borrower), part),
                                ((borrower, "loan_liability" + suffix, bank), -part),
                                ((borrower, "opening_equity", "initial"), part)])
                loan_totals[bank_id] += part
    equities = allocate_units(cents(sum(b.bank_equity for b in banks)),
                              {b.id: b.bank_equity for b in banks})
    reserves = {b.id: equities[b.id] - loan_totals[b.id] for b in banks}
    if any(value < 0 for value in reserves.values()) or sum(reserves.values()) != cents(sim.central_bank.reserves):
        raise AccountingError("Réserves initiales incompatibles avec les fonds propres")
    for bank in banks:
        name = bank_ids[bank.id]
        entries.extend([((name, "reserves", "central_bank"), reserves[bank.id]),
                        ((name, "opening_equity", "initial"), -equities[bank.id]),
                        (("central_bank", "reserve_liability", name), -reserves[bank.id]),
                        (("central_bank", "opening_asset", "initial"), reserves[bank.id])])
    holders = [(f"person:{p.id}", p.cash) for p in sim.people]
    holders += [(f"company:{c.id}", c.cash) for c in sim.companies if c.sector != "bank"]
    holders += [("public:state", sim.public.cash), ("resources:market", sim.resources.market_cash)]
    cash = {name: cents(value) for name, value in holders}
    if len(cash) != len(holders) or any(value < 0 for value in cash.values()):
        raise AccountingError("Trésorerie initiale invalide")
    if sum(cash.values()) != cents(sim.ledger.currency):
        raise AccountingError("Monnaie initiale incompatible avec les trésoreries")
    for name, value in cash.items():
        entries.extend([((name, "currency", "central_bank"), value),
                        ((name, "opening_equity", "initial"), -value),
                        (("central_bank", "currency_liability", name), -value),
                        (("central_bank", "opening_asset", "initial"), value)])
    ledger = BankLedger()
    ledger.post(entries)
    ledger.reconciliation_check()
    return ledger
