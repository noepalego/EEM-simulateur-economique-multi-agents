"""Crédit aux ménages : crédit à la consommation, impayés, défauts et saisies.

Un ménage salarié dont la trésorerie passe sous ``consumer_credit_buffer_months``
mois de salaire emprunte à sa banque (le prêt crée un dépôt), dans la limite de
``consumer_credit_income_multiple`` mois de salaire d'encours. Il rembourse chaque
mois les intérêts et une fraction ``1 / consumer_credit_term_months`` du capital.

Un ménage qui ne paie pas sa mensualité accumule des impayés : au-delà de
``consumer_default_months`` (crédit à la consommation) ou de
``mortgage_default_months`` (prêt immobilier), il fait défaut. Le logement saisi
est vendu à une entreprise immobilière avec une décote ; le produit rembourse la
banque, qui passe le reste en pertes.
"""

from __future__ import annotations

from contextlib import nullcontext

from economy.credit_risk import approved_household_loan, household_expected_loss
from economy.models import Company, Person, SimulationConfig


def extend_consumer_credit(
    people: list[Person],
    capacity: float,
    config: SimulationConfig,
    rate: float,
    credit_transaction=None,
    credit_capacity=None,
) -> dict[str, float]:
    """Accorde les crédits à la consommation du mois ; retourne les montants par ménage."""
    lent: dict[str, float] = {}
    remaining = max(0.0, capacity)
    system_capacity = None  # relue seulement après un prêt inscrit (la fonction est pure)
    for person in people:
        if remaining <= 0.0:
            break
        wage = person.current_wage
        if wage <= 0.0 or person.consumer_arrears > 0 or person.mortgage_arrears > 0:
            continue
        limit = config.consumer_credit_income_multiple * wage - person.consumer_debt
        need = config.consumer_credit_buffer_months * wage - person.cash
        loan = min(limit, need, remaining)
        if loan > 1.0 and credit_capacity is not None:
            # la capacité ne peut que réduire un prêt : inutile de la lire si les autres
            # bornes le refusent déjà
            if system_capacity is None:
                system_capacity = credit_capacity()
            loan = min(loan, system_capacity)
        loan_rate = rate
        if loan > 1.0:
            # la banque réduit ou refuse selon la perte attendue, qu'elle fait payer
            loan = approved_household_loan(person, loan, config)
            loan_rate = rate + household_expected_loss(person, loan, config) / 12.0
        if loan <= 1.0:
            continue
        # taux moyen pondéré de l'encours
        with (credit_transaction(person, loan) if credit_transaction else nullcontext()):
            person.consumer_rate = (
                person.consumer_debt * person.consumer_rate + loan * loan_rate
            ) / (person.consumer_debt + loan)
            person.consumer_debt += loan
            person.cash += loan
        remaining -= loan
        lent[person.id] = loan
        system_capacity = None  # le prêt vient d'être inscrit chez les banques
    return lent


def service_consumer_credit(
    people: list[Person],
    config: SimulationConfig,
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Mensualités du crédit à la consommation.

    Retourne (intérêts payés, intérêts courus par ménage, encours en défaut par ménage).
    Les intérêts impayés s'ajoutent à l'encours.
    """
    interest_paid = 0.0
    accrued: dict[str, float] = {}
    defaults: dict[str, float] = {}
    term = max(1, config.consumer_credit_term_months)
    for person in people:
        if person.consumer_debt <= 0.0:
            continue
        interest = person.consumer_debt * person.consumer_rate
        scheduled = interest + person.consumer_debt / term
        payment = min(max(0.0, person.cash), scheduled)
        person.cash -= payment
        person.expenses_this_turn += payment
        person.cumulative_expenses += payment
        interest_paid += min(payment, interest)
        accrued[person.id] = interest
        person.consumer_debt = max(0.0, person.consumer_debt + interest - payment)
        person.consumer_arrears = (
            person.consumer_arrears + 1 if payment < scheduled * 0.999 else 0
        )
        if person.consumer_debt == 0.0:
            person.consumer_debt = 0.0
            person.consumer_arrears = 0
        elif person.consumer_arrears >= config.consumer_default_months:
            defaults[person.id] = person.consumer_debt
            person.consumer_debt = 0.0
            person.consumer_arrears = 0
    return interest_paid, accrued, defaults


def foreclose_homes(
    people: list[Person],
    companies: list[Company],
    config: SimulationConfig,
) -> dict[str, tuple[float, float]]:
    """Saisit les logements des ménages en impayés prolongés.

    Le logement est vendu à l'entreprise immobilière la mieux dotée, avec
    ``foreclosure_discount`` de décote, payée sur sa trésorerie. Le produit
    rembourse le prêt ; un éventuel surplus revient au ménage. Retourne, par
    ménage saisi, (encours avant saisie, montant remboursé).
    """
    foreclosed: dict[str, tuple[float, float]] = {}
    buyers = [c for c in companies if c.active and c.sector == "real_estate"]
    if not buyers:
        return foreclosed
    price = config.housing_price * (1.0 - config.foreclosure_discount)
    for person in people:
        if (
            person.mortgage_debt <= 0.0
            or not person.owns_home
            or person.mortgage_arrears < config.mortgage_default_months
        ):
            continue
        buyer = max(buyers, key=lambda c: (c.cash, c.id))
        paid = min(price, max(0.0, buyer.cash))
        buyer.cash -= paid
        buyer.property_inventory += 1
        debt = person.mortgage_debt
        repaid = min(paid, debt)
        person.cash += paid - repaid
        person.owns_home = False
        person.rental_owner_id = None
        person.mortgage_debt = 0.0
        person.mortgage_payment = 0.0
        person.mortgage_months_remaining = 0
        person.mortgage_arrears = 0
        foreclosed[person.id] = (debt, repaid)
    return foreclosed
