"""Régime de propriété des entreprises selon l'orientation du gouvernement (lot E, novembre 2026).

Réglage ``ownership_regime`` (désactivé par défaut : aucun calcul, aucun tirage). Activé, l'État
porte un **régime de propriété** ``r`` entre 0 (ultra-capitalisme) et 1 (coopératives), qui se
rapproche chaque mois de l'orientation du gouvernement en place (orientation / 100), à la vitesse
``1 / ownership_transition_months`` : un changement de gouvernement s'applique progressivement.

Deux intensités en dérivent, continues :

- laisser-faire ``l = max(0, 1 − 2r)`` (1 à 0, nul dès le centre) ;
- coopérative ``c = max(0, 2r − 1)`` (nulle jusqu'au centre, 1 à 100).

Règles appliquées chaque mois à ``live`` (``apply_ownership_rules``), sans aucun transfert
direct de richesse :

- monopoles : la durée de problème avant l'ouverture d'un dossier de concurrence est
  multipliée par 1 / (1 − l) (jamais d'intervention à l = 1) ; côté coopératif, inchangée ;
- prises de contrôle (``consolidation.py``) : seuil de contrôle abaissé de 40 % × l (OPA
  facilitées), relevé de 40 % × c (borné à 90 %) ; côté coopératif, au moins round(3c) entreprises
  restent dans un secteur (pas de fusion qui réduise le secteur en deçà) ;
- dividendes : taux de distribution des bénéfices × (1 + 0,5 l) (« sans frein ») ou × (1 − 0,5 c)
  (vote des salariés : moitié moins distribuée à 100) ;
- direction : multiplicateur du salaire de direction × (1 − 0,4 c) (vote des salariés), jamais
  sous 1 ; inchangé côté laisser-faire ;
- la fiscalité du capital suit déjà la doctrine (``capital_flat_factor``, ``tax_factors``).

**Parts salariées** (``cooperative_shares``) : côté coopératif, une part minimale
``cooperative_share_max`` × c du capital de chaque entreprise non bancaire est détenue par ses
salariés, à parts égales. Elle s'acquiert progressivement, sans expropriation : l'entreprise
attribue d'abord ses titres auto-détenus, puis rachète au cours, sur sa trésorerie libre, des
titres aux autres actionnaires (au prorata ; jamais à l'État), au plus ``cooperative_purchase_rate``
de son capital par mois. Les nouveaux embauchés reçoivent leur part à mesure que l'entreprise
reconstitue la part visée. Au départ d'un salarié, l'entreprise rachète sa part au cours (titres
auto-détenus, réattribués) ; faute de trésorerie, la part redevient une détention ordinaire. Les
parts salariées ne sont ni vendues en Bourse par le ménage ni apportées à une offre publique. Tous
les flux passent par ``transfer_cash`` : la monnaie est conservée.
"""

from __future__ import annotations

import math

from economy.payments import transfer_cash
from economy.treasury import working_capital


def regime_intensities(regime: float) -> tuple[float, float]:
    """(laisser-faire, coopérative) du régime ``regime`` ∈ [0, 1]."""
    regime = max(0.0, min(1.0, regime))
    return max(0.0, 1.0 - 2.0 * regime), max(0.0, 2.0 * regime - 1.0)


def apply_ownership_rules(live, regime: float) -> None:
    """Règles de propriété interpolées, appliquées à ``live`` (copie de travail du mois)."""
    liberal, cooperative = regime_intensities(regime)
    if liberal >= 1.0:
        live.monopoly_duration_months = 10 ** 9  # aucune intervention contre les monopoles
    elif liberal > 0.0:
        live.monopoly_duration_months = math.ceil(live.monopoly_duration_months / (1.0 - liberal))
    live.control_threshold = min(0.9, live.control_threshold * (1.0 - 0.4 * liberal + 0.4 * cooperative))
    if cooperative > 0.0:
        live.min_firms_per_sector = max(live.min_firms_per_sector, int(round(3.0 * cooperative)))
    live.dividend_payout_rate *= (1.0 + 0.5 * liberal) * (1.0 - 0.5 * cooperative)
    live.executive_wage_multiplier = max(1.0, live.executive_wage_multiplier * (1.0 - 0.4 * cooperative))


class OwnershipMixin:
    """Régime de propriété et parts salariées (lot E)."""

    def _ownership_step(self) -> None:
        """Début de mois (après les décisions publiques durables) : le régime se rapproche de
        l'orientation du gouvernement, ses règles s'appliquent à ``live``, puis les parts
        salariées sont mises à jour. Sans le réglage, rien."""
        live = self.live
        if not live.ownership_regime:
            return
        inst = self.institutions
        target = max(0.0, min(1.0, self.government.orientation / 100.0))
        if inst.ownership_regime is None:
            inst.ownership_regime = target
        else:
            step = 1.0 / max(1.0, live.ownership_transition_months)
            gap = target - inst.ownership_regime
            inst.ownership_regime += max(-step, min(step, gap))
        apply_ownership_rules(live, inst.ownership_regime)
        _, cooperative = regime_intensities(inst.ownership_regime)
        self._cooperative_shares(live.cooperative_share_max * cooperative)

    def _cooperative_shares(self, share: float) -> None:
        """Parts salariées : départs rachetés, puis attribution vers ``share`` du capital."""
        live = self.live
        people = {p.id: p for p in self.people}
        companies = {c.id: c for c in self.companies}
        # 1. départs (et entreprises disparues) : la part salariée est rachetée au cours
        for person in self.people:
            stakes = person.coop_shares
            if not stakes:
                continue
            for company_id in list(stakes):
                company = companies.get(company_id)
                quantity = min(stakes[company_id], person.shares.get(company_id, 0.0))
                if company is None or not company.active or quantity <= 1e-12:
                    stakes.pop(company_id)
                    continue
                stakes[company_id] = quantity
                if person.employer_id == company_id:
                    continue
                price = max(0.0, company.stock_price)
                if company.cash >= quantity * price:
                    transfer_cash(company, person, quantity * price)
                    person.shares[company_id] -= quantity
                    if person.shares[company_id] <= 1e-12:
                        person.shares.pop(company_id)
                    company.treasury_shares += quantity
                stakes.pop(company_id)  # rachetée, ou redevenue une détention ordinaire
        if share <= 0.0:
            return
        # 2. attribution : part visée × capital, à parts égales entre les salariés
        holders = [*self.people, *self.companies]
        for company in self.companies:
            if not company.active or company.sector == "bank" or not company.employees:
                continue
            if company.shares_outstanding <= 0.0 or company.stock_price <= 0.0:
                continue
            staff = [people[pid] for pid in company.employees if pid in people]
            if not staff:
                continue
            each = share * company.shares_outstanding / len(staff)
            gaps = {p.id: max(0.0, each - p.coop_shares.get(company.id, 0.0)) for p in staff}
            wanted = min(sum(gaps.values()), live.cooperative_purchase_rate * company.shares_outstanding)
            if wanted <= 1e-9:
                continue
            granted = min(wanted, max(0.0, company.treasury_shares))
            company.treasury_shares -= granted
            bought = self._buy_for_employees(company, wanted - granted, holders)
            total = granted + bought
            if total <= 0.0:
                continue
            gap_sum = sum(gaps.values())
            for person in staff:
                part = total * gaps[person.id] / gap_sum
                if part <= 0.0:
                    continue
                person.shares[company.id] = person.shares.get(company.id, 0.0) + part
                person.coop_shares[company.id] = person.coop_shares.get(company.id, 0.0) + part

    def _buy_for_employees(self, company, quantity: float, holders: list) -> float:
        """Rachat au cours, sur la trésorerie libre, de ``quantity`` titres aux actionnaires
        ordinaires (au prorata ; ni l'État, ni l'entreprise elle-même, ni une banque, ni les parts
        salariées).
        Retourne la quantité rachetée."""
        price = company.stock_price
        free_cash = company.cash - working_capital(company, self.live)
        quantity = min(quantity, max(0.0, free_cash) / price)
        if quantity <= 1e-9:
            return 0.0
        sellable = []
        for holder in holders:
            # ni l'entreprise elle-même, ni une banque (sa trésorerie propre n'est pas de la monnaie)
            if holder is company or getattr(holder, "sector", None) == "bank":
                continue
            held = holder.shares.get(company.id, 0.0)
            locked = getattr(holder, "coop_shares", None)
            if locked:
                held -= locked.get(company.id, 0.0)
            if held > 1e-12:
                sellable.append((holder, held))
        supply = sum(q for _, q in sellable)
        quantity = min(quantity, supply)
        if quantity <= 1e-9:
            return 0.0
        for holder, held in sellable:
            part = quantity * held / supply
            transfer_cash(company, holder, part * price)
            holder.shares[company.id] -= part
            if holder.shares[company.id] <= 1e-12:
                holder.shares.pop(company.id)
        return quantity
