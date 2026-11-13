"""Lot T3 (novembre 2026) : procédure de sauvegarde des entreprises en difficulté.

Un réglage, ``safeguard_procedure``, désactivé par défaut : sans lui, rien n'est calculé et la
simulation est identique au bit près.

Le diagnostic du lot T (``results/lot_t_rachats/DIAGNOSTIC.md``) montre qu'une grande entreprise en
pertes ne peut licencier que ``max_monthly_layoffs`` personnes par mois, se voit refuser tout crédit
d'exploitation (la perte du mois fait monter la perte attendue des banques), épuise sa trésorerie,
puis perd tous ses salariés en quelques mois et survit en coquille avec sa dette.

Une entreprise obtient un plan de sauvegarde quand elle remplit toutes ces conditions :

- elle emploie au moins ``SAFEGUARD_MIN_STAFF`` salariés (une procédure collective vise l'emploi) ;
- elle perd de l'argent depuis au moins ``SAFEGUARD_LOSS_MONTHS`` mois, mois courant compris ;
- sa trésorerie couvre moins de ``SAFEGUARD_CASH_MONTHS`` mois de masse salariale (avant la
  cessation de paiements, comme une vraie sauvegarde) ;
- les banques refusent, par leur propre test de perte attendue (``credit_risk.approved_loan``), le
  crédit qui lui rendrait ``SAFEGUARD_BRIDGE_MONTHS`` mois de masse salariale ;
- un noyau viable existe : ses recettes, une fois les autres charges payées, financent au moins
  ``SAFEGUARD_MIN_CORE`` de sa masse salariale ;
- elle n'a pas eu de plan depuis ``SAFEGUARD_INTERVAL_MONTHS`` mois.

Le plan a trois volets :

1. **Effectifs** : l'entreprise ramène en un mois son effectif à la part que ses recettes paient
   (``safeguard_staff``, lu par ``labor`` le mois suivant), au lieu de licencier deux personnes par
   mois jusqu'à l'épuisement de sa trésorerie.
2. **Dette** : les banques abandonnent la dette au-delà de ``safeguard_debt_share`` de ce que les
   recettes permettent de servir (recette × ``maximum_debt_service_ratio`` / taux de la dette de
   l'entreprise). C'est une perte sur prêts ordinaire (``Company.default_loss``, traitée par
   ``_write_off_defaulted_bank_loans`` comme pour un emprunteur maintenu), qui peut affaiblir ou
   emporter une banque.
3. **Argent frais** : un prêt de sauvegarde, remboursé en priorité et donc accordé sans le test de
   perte attendue, porte la trésorerie à ``SAFEGUARD_BRIDGE_MONTHS`` mois de la masse salariale
   réduite, dans la limite de la capacité de crédit des banques (``EconomySimulation``).

Le plan remet à zéro le décompte des mois de pertes qui mène à la cessation de paiements. Les
obligations d'entreprise (lot J) ne sont pas touchées et les actionnaires gardent leurs titres.
Aucune monnaie n'est créée par la remise : une créance disparaît avec la dette correspondante ; le
prêt de sauvegarde crée un dépôt, comme tout crédit.
"""

from __future__ import annotations

import math

from economy.credit_risk import approved_loan
from economy.finance import _collateral
from economy.models import Company, SimulationConfig

SAFEGUARD_MIN_STAFF = 10            # salariés au moins : une procédure collective vise l'emploi
SAFEGUARD_LOSS_MONTHS = 3           # mois de pertes consécutifs, mois courant compris
SAFEGUARD_CASH_MONTHS = 3.0         # trésorerie sous ce nombre de mois de masse salariale
SAFEGUARD_BRIDGE_MONTHS = 3.0       # trésorerie que le prêt de sauvegarde rétablit, en mois de paie
SAFEGUARD_MIN_CORE = 0.25           # part de l'effectif que les recettes doivent pouvoir payer
SAFEGUARD_INTERVAL_MONTHS = 24      # délai minimal entre deux plans d'une même entreprise


def serviceable_debt(company: Company, config: SimulationConfig, policy_rate: float) -> float:
    """Dette dont les intérêts restent sous ``maximum_debt_service_ratio`` de la recette lissée,
    au taux de la dette de l'entreprise (jamais sous le taux directeur)."""
    rate = company.debt_interest_rate if company.debt_interest_rate is not None else config.loan_interest_rate
    rate = max(rate, policy_rate, 0.0001)
    return max(0.0, company.revenue_ema) * config.maximum_debt_service_ratio / rate


def bridge_need(company: Company, payroll: float) -> float:
    """Prêt voulu : ce qui manque pour ``SAFEGUARD_BRIDGE_MONTHS`` mois de ``payroll``."""
    return max(0.0, SAFEGUARD_BRIDGE_MONTHS * payroll - company.cash)


def safeguard_plans(
    companies: list[Company],
    config: SimulationConfig,
    policy_rate: float,
    month: int,
    excluded: frozenset = frozenset(),
) -> list[tuple[Company, float, float]]:
    """Accorde les plans de sauvegarde du mois ; retourne (entreprise, dette abandonnée, prêt de
    sauvegarde voulu). Effectif visé et dette sont modifiés ici ; l'appelant inscrit la perte des
    banques et le prêt. ``excluded`` : entreprises sous contrôle public, que l'État recapitalise
    selon ses propres règles."""
    plans = []
    for company in companies:
        if company.safeguard_staff:
            company.safeguard_staff = 0  # le plan du mois précédent a été appliqué
        staff = len(company.employees)
        if (
            not company.active or company.sector == "bank" or company.id in excluded
            or staff < SAFEGUARD_MIN_STAFF
            or company.profit >= 0.0 or company.loss_streak + 1 < SAFEGUARD_LOSS_MONTHS
            or company.wage_bill <= 0.0
            or company.cash >= SAFEGUARD_CASH_MONTHS * company.wage_bill
            or month - company.safeguard_month < SAFEGUARD_INTERVAL_MONTHS
        ):
            continue
        need = bridge_need(company, company.wage_bill)
        if need <= 0.0 or approved_loan(company, need, _collateral(company, config), config) >= need:
            continue  # le crédit ordinaire suffit : pas de procédure
        # noyau viable : recettes moins les charges autres que la paie, intérêts de la dette
        # conservée compris (la remise allège les intérêts)
        rate = company.debt_interest_rate if company.debt_interest_rate is not None else config.loan_interest_rate
        kept = min(company.debt, config.safeguard_debt_share * serviceable_debt(company, config, policy_rate))
        other_costs = max(0.0, company.expenses - company.wage_bill - company.debt * rate) + kept * rate
        affordable = max(0.0, company.revenue_ema - other_costs) / company.wage_bill
        if affordable < SAFEGUARD_MIN_CORE:
            continue  # rien de viable à sauvegarder : la faillite ordinaire s'applique
        planned = max(SAFEGUARD_MIN_STAFF, min(staff, math.floor(staff * affordable)))
        written = company.debt - kept
        company.debt = kept
        company.default_loss += written
        company.loss_streak = 0
        company.safeguard_month = month
        company.safeguard_staff = planned
        payroll = company.wage_bill * planned / staff
        plans.append((company, written, bridge_need(company, payroll)))
    return plans
