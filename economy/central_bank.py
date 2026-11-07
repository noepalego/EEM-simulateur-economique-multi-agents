"""Banque centrale : politique monétaire et taux directeur."""

from __future__ import annotations

from dataclasses import dataclass

from economy import diagnostics


@dataclass
class CentralBank:
    """État de la banque centrale : réserves des banques et avances consenties."""

    initial_reserves: float = 0.0
    reserves: float = 0.0
    advances: float = 0.0


class CentralBankMixin:
    """Banque centrale : politique monétaire et taux directeur."""

    @property
    def base_money_issued(self) -> float:
        """Monnaie de base fournie aux banques (avances de la banque centrale)."""
        return self.central_bank.advances

    def _annual_inflation_gap(self) -> float:
        """Cible d'inflation annuelle moins inflation observée (12 mois glissants)."""
        config = self.live
        target = (1.0 + config.inflation_target) ** 12 - 1.0
        if len(self.history) < 6:
            return 0.0
        window = self.history[-12:]
        elapsed = len(window)
        base = (
            self.history[-elapsed - 1].consumer_price_index
            if len(self.history) > elapsed
            else 100.0
        )
        inflation = (
            window[-1].consumer_price_index / max(base, 1.0)
        ) ** (12.0 / elapsed) - 1.0
        return target - inflation

    def _central_bank_operations(self) -> float:
        """Réserves obligatoires et avances de la banque centrale.

        La banque centrale ne distribue plus de monnaie aux agents : la monnaie
        naît du crédit bancaire. Elle fournit seulement les réserves exigées
        (``reserve_requirement`` × dépôts) sous forme d'avances au taux directeur ;
        les intérêts correspondants sont une charge des banques, restituée à
        l'État (profit de la banque centrale). Retourne la variation des avances.
        """
        central_bank = self.central_bank
        deposits = max(0.0, self.ledger.deposits_from_cash(self))
        required = self.live.reserve_requirement * deposits
        advances = max(0.0, required - central_bank.initial_reserves)
        issued = advances - central_bank.advances
        central_bank.advances = advances
        central_bank.reserves = central_bank.initial_reserves + advances
        return issued

    def _settle_central_bank_interest(self) -> float:
        """Intérêts des avances : charge des banques, profit de la banque centrale reversé à l'État."""
        interest = self.central_bank.advances * self.policy_rate
        if interest <= 0.0:
            return 0.0
        paid = self._charge_banks(interest)
        self.public.cash += paid
        return paid

    def _adjust_policy_rate(
        self,
        inflation_rate: float,
        unemployment_rate: float,
    ) -> None:
        """Taux directeur : règle de Taylor lissée.

        Taux annuel visé = taux réel neutre + inflation sur 12 mois + coefficient ×
        (inflation - cible) - coefficient × (chômage - chômage naturel). Le taux
        mensuel suit sa cible avec inertie : plus de dérive vers le plafond comme
        avec une règle purement incrémentale.
        """
        live = self.live
        target = (1.0 + live.inflation_target) ** 12 - 1.0
        # inflation sur 12 mois, ou annualisée sur les mois déjà observés : la
        # banque centrale ne fait pas comme si l'inflation était à la cible
        # pendant la première année
        inflation = target - self._annual_inflation_gap()
        annual = (
            live.taylor_neutral_real_rate
            + inflation
            + live.taylor_inflation_coefficient * (inflation - target)
            - live.taylor_unemployment_coefficient
            * (unemployment_rate - live.natural_unemployment_rate)
        )
        diagnostics.bite("plafond_taux_directeur", annual + live.policy_rate_offset > live.policy_rate_cap)
        diagnostics.bite("plancher_taux_directeur", annual + live.policy_rate_offset < 0.0012)
        annual = max(0.0012, min(live.policy_rate_cap, annual + live.policy_rate_offset))
        self.policy_rate += live.policy_rate_smoothing * (annual / 12.0 - self.policy_rate)
