"""Point de passage préparatoire pour les transferts de trésorerie.

Les montants restent ceux du moteur historique. Ce module n'effectue pas encore
d'écriture dans le registre bancaire en centimes.
"""

import math


def transfer_cash(payer, recipient, amount: float) -> None:
    """Transfère le montant et restaure le débit si le crédit échoue."""
    if not math.isfinite(amount) or amount < 0.0:
        raise ValueError("Montant de paiement invalide")
    if payer is recipient or amount == 0.0:
        return
    payer_before = payer.cash
    recipient_before = recipient.cash
    try:
        payer.cash -= amount
        recipient.cash += amount
    except Exception:
        payer.cash = payer_before
        recipient.cash = recipient_before
        raise
