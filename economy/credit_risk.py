"""Évaluation du risque de crédit par les banques (prêt selon la perte attendue).

La banque ne regarde que ce qu'elle observe de l'emprunteur : son endettement rapporté
à ses garanties, sa marge, son âge. Elle en tire une probabilité de défaut annuelle
(logit) et une perte en cas de défaut (ce que les garanties ne couvrent pas). La perte
attendue annuelle = probabilité × perte :

- au-delà de ``bank_loss_tolerance``, le prêt est réduit (le plus gros montant dont la
  perte attendue reste tolérable) ou refusé ;
- sinon, la marge du prêt couvre la perte attendue et le coût du capital immobilisé.

Aucun résultat n'est visé : les paramètres sont des sensibilités, pas des cibles.
"""

from __future__ import annotations

import math

from economy.models import Company, SimulationConfig


REFERENCE_LEVERAGE = 0.5  # endettement (dette / garanties) auquel le défaut vaut le taux observé


def default_probability(
    leverage: float,
    loss_margin: float,
    young: bool,
    config: SimulationConfig,
    base_rate: float | None = None,
) -> float:
    """Probabilité de défaut annuelle d'après l'endettement (dette / garanties), la
    marge négative (perte rapportée au chiffre d'affaires) et l'âge.

    Le niveau de départ est le taux de défaut que les banques observent (lissé,
    ``observed_default_rate``), pas une constante : un emprunteur d'endettement moyen,
    en bénéfice et établi, fait défaut au taux observé ; les écarts s'y ajoutent en
    logit. Après une vague de faillites, tous les emprunteurs deviennent plus risqués."""
    rate = config.observed_default_rate if base_rate is None else base_rate
    rate = max(0.002, min(0.5, rate))
    logit = (
        math.log(rate / (1.0 - rate))
        + config.default_risk_leverage * (max(0.0, leverage) - REFERENCE_LEVERAGE)
        + config.default_risk_loss_margin * max(0.0, loss_margin)
        + (config.default_risk_young if young else 0.0)
    )
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))


def expected_loss(
    company: Company,
    loan: float,
    collateral: float,
    config: SimulationConfig,
) -> float:
    """Perte attendue annuelle, en part du prêt, si l'entreprise empruntait ``loan``.

    ``collateral`` : garanties de l'entreprise avant le prêt (le prêt y ajoute sa
    trésorerie)."""
    total_debt = max(0.0, company.debt) + max(0.0, loan)
    total_collateral = max(0.0, collateral) + max(0.0, loan)
    leverage = total_debt / max(total_collateral, 1.0)
    margin = company.profit / max(company.revenue, 1.0) if company.revenue > 0.0 else 0.0
    probability = default_probability(
        leverage, max(0.0, -margin), company.age_months < 24, config
    )
    # perte en cas de défaut : celle que les banques ont réellement subie à la faillite
    # d'emprunteurs (les garanties comptées au moment du prêt ont fondu au moment du
    # défaut : mesuré, des entrants à garanties 2 fois supérieures à leur dette ont fait
    # faillite en perdant l'essentiel du prêt) ; jamais sous les frais de recouvrement
    return probability * max(config.default_recovery_cost, config.observed_loss_given_default)


def household_expected_loss(person, loan: float, config: SimulationConfig) -> float:
    """Perte attendue annuelle d'un crédit à la consommation (sans garantie : la perte en
    cas de défaut est l'encours entier) : le risque monte avec la part du plafond
    d'endettement du ménage déjà utilisée."""
    ceiling = max(1.0, config.consumer_credit_income_multiple * max(person.current_wage, 1.0))
    usage = (max(0.0, person.consumer_debt) + max(0.0, loan)) / ceiling
    return default_probability(
        usage, 0.0, False, config, base_rate=config.observed_household_default_rate
    )


def approved_household_loan(person, loan: float, config: SimulationConfig) -> float:
    """Crédit à la consommation que les banques acceptent : réduit jusqu'à une perte
    attendue tolérable, ou refusé."""
    amount = max(0.0, loan)
    for _ in range(6):
        if amount <= 0.0 or household_expected_loss(person, amount, config) <= config.bank_loss_tolerance:
            return amount
        amount *= 0.5
    return 0.0


def approved_loan(
    company: Company,
    loan: float,
    collateral: float,
    config: SimulationConfig,
) -> float:
    """Montant que les banques acceptent de prêter : ``loan``, réduit si la perte
    attendue dépasse la tolérance, ou 0 si même un petit prêt la dépasse."""
    if loan <= 0.0:
        return 0.0
    amount = loan
    for _ in range(6):
        if expected_loss(company, amount, collateral, config) <= config.bank_loss_tolerance:
            return amount
        amount *= 0.5
    return 0.0


def risk_loan_spread(
    company: Company,
    loan: float,
    collateral: float,
    config: SimulationConfig,
    policy_rate: float,
) -> float:
    """Marge mensuelle du prêt : perte attendue + rémunération du capital immobilisé
    (exigence de fonds propres × coût des fonds propres)."""
    loss = expected_loss(company, loan, collateral, config)
    cost_of_equity = 12.0 * max(0.0, policy_rate) + config.equity_risk_premium
    return (loss + config.capital_requirement * cost_of_equity) / 12.0


def voluntary_buffer(bank: Company, config: SimulationConfig) -> float:
    """Coussin de fonds propres que la banque garde au-dessus de l'exigence : il monte avec
    son taux de perte moyen sur le cycle (constitué en expansion, qui ne bondit pas en crise)."""
    return config.bank_buffer_loss_gain * 12.0 * max(0.0, bank.loss_rate_cycle)
