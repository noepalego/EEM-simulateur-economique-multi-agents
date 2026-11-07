"""Comptabilité en partie double : bilans sectoriels et cohérence stocks-flux.

Le système monétaire est celui d'une économie à monnaie endogène :

* un prêt bancaire crée un dépôt (la trésorerie de l'emprunteur augmente, les
  réserves de la banque ne bougent pas) ; un remboursement détruit le dépôt ;
* un intérêt versé est un transfert de dépôts de l'emprunteur vers la banque :
  les dépôts diminuent, les fonds propres bancaires augmentent ;
* la banque centrale fournit les réserves exigées (avances) au taux directeur.

Bilan consolidé du secteur bancaire (les dépôts détenus par les banques
elles-mêmes s'annulent) :

    réserves + prêts + titres publics  =  dépôts de la clientèle + fonds propres + avances

``Ledger.residual`` mesure l'écart à cette identité : la trésorerie de tous les
agents non bancaires moins les dépôts déduits du bilan bancaire doit rester égale
à la monnaie fiduciaire initiale. Un écart non nul révèle une fuite : de la
monnaie créée ou détruite en dehors des banques et de la banque centrale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from economy.simulation import EconomySimulation


@dataclass
class SectorBalance:
    """Bilan d'un secteur institutionnel (actifs, passifs, fonds propres)."""

    name: str
    assets: dict[str, float] = field(default_factory=dict)
    liabilities: dict[str, float] = field(default_factory=dict)

    @property
    def total_assets(self) -> float:
        return sum(self.assets.values())

    @property
    def total_liabilities(self) -> float:
        return sum(self.liabilities.values())

    @property
    def net_worth(self) -> float:
        return self.total_assets - self.total_liabilities


@dataclass
class Ledger:
    """Livre de comptes consolidé de la simulation."""

    currency: float = 0.0  # monnaie fiduciaire initiale (constante)

    # -- agrégats -----------------------------------------------------------
    @staticmethod
    def nonbank_cash(sim: "EconomySimulation") -> float:
        return (
            sum(p.cash for p in sim.people)
            + sum(c.cash for c in sim.companies if c.sector != "bank")
            + sim.public.cash
            + sim.resources.market_cash
        )

    @staticmethod
    def banking(sim: "EconomySimulation") -> dict[str, float]:
        banks = [c for c in sim.companies if c.sector == "bank" and c.active]
        loans = sum(c.debt for c in sim.companies if c.active and c.sector != "bank") + sum(
            p.mortgage_debt + p.consumer_debt for p in sim.people
        )
        return {
            "reserves": sim.central_bank.reserves,
            "advances": sim.central_bank.advances,
            "loans": loans,
            "bonds": sum(b.bond_holdings for b in banks),
            "equity": sum(b.bank_equity for b in banks),
            "own_cash": sum(b.cash for b in banks),
            # actifs réels des banques (technologie, capital) : ils s'amortissent en charge
            "real_assets": sum(
                b.technology_asset_value + b.productive_capital_value for b in banks
            ),
        }

    def deposits(self, sim: "EconomySimulation") -> float:
        """Dépôts de la clientèle déduits du bilan consolidé des banques."""
        b = self.banking(sim)
        return (
            b["reserves"] + b["loans"] + b["bonds"] + b["real_assets"]
            - b["equity"] - b["advances"]
        )

    def deposits_from_cash(self, sim: "EconomySimulation") -> float:
        """Dépôts déduits de la trésorerie (monnaie totale moins monnaie fiduciaire)."""
        return self.nonbank_cash(sim) - self.currency

    def calibrate(self, sim: "EconomySimulation") -> None:
        """Fixe la monnaie fiduciaire initiale (appelé une fois la simulation construite)."""
        self.currency = self.nonbank_cash(sim) - self.deposits(sim)

    def residual(self, sim: "EconomySimulation") -> float:
        """Écart à l'identité comptable (0 si aucune monnaie n'apparaît ou ne disparaît)."""
        return self.deposits_from_cash(sim) - self.deposits(sim)

    # -- bilans sectoriels ----------------------------------------------------
    def balance_sheets(self, sim: "EconomySimulation") -> list[SectorBalance]:
        """Bilans consolidés : ménages, entreprises, banques, État, banque centrale."""
        live = sim.live
        b = self.banking(sim)
        firms = [c for c in sim.companies if c.active and c.sector != "bank"]
        deposits = max(0.0, self.deposits(sim))
        household = SectorBalance(
            "Ménages",
            assets={
                "Dépôts et monnaie": sum(p.cash for p in sim.people),
                "Actions": sum(
                    q * _price(sim, cid) for p in sim.people for cid, q in p.shares.items()
                ),
                "Obligations d'État": sum(p.bond_holdings for p in sim.people),
                "Logements": sum(
                    (int(p.owns_home) + p.investment_homes) * live.housing_price
                    for p in sim.people
                ),
            },
            liabilities={
                "Prêts immobiliers": sum(p.mortgage_debt for p in sim.people),
                "Crédit à la consommation": sum(p.consumer_debt for p in sim.people),
            },
        )
        corporate = SectorBalance(
            "Entreprises",
            assets={
                "Dépôts et monnaie": sum(c.cash for c in firms),
                "Stocks": sum(
                    c.inventory * live.food_input_cost + c.goods_inventory * live.goods_input_cost
                    for c in firms
                )
                * sim.resources.input_cost_index,
                "Logements détenus": sum(c.property_inventory for c in firms) * live.housing_price,
                "Capital productif et technologique": sum(
                    c.productive_capital_value + c.technology_asset_value for c in firms
                ),
                "Chantiers en cours": sum(c.construction_in_progress for c in firms),
                "Obligations d'État": sum(c.bond_holdings for c in firms),
                "Actions détenues": sum(
                    q * _price(sim, cid) for c in firms for cid, q in c.shares.items()
                ),
            },
            liabilities={"Prêts bancaires": sum(c.debt for c in firms)},
        )
        banks = SectorBalance(
            "Banques",
            assets={
                "Réserves à la banque centrale": b["reserves"],
                "Prêts": b["loans"],
                "Titres publics": b["bonds"],
                "Actifs réels (technologie, capital)": b["real_assets"],
            },
            liabilities={
                "Dépôts de la clientèle": deposits,
                "Avances de la banque centrale": b["advances"],
                "Fonds propres": b["equity"],
            },
        )
        government = SectorBalance(
            "État",
            assets={
                "Dépôts": sim.public.cash,
                "Capital public": sim.public.public_capital,
            },
            liabilities={"Titres publics émis": sim.public.debt},
        )
        central = SectorBalance(
            "Banque centrale",
            assets={"Avances aux banques": b["advances"]},
            liabilities={"Réserves des banques": b["reserves"]},
        )
        return [household, corporate, banks, government, central]


def _price(sim: "EconomySimulation", company_id: str) -> float:
    company = next((c for c in sim.companies if c.id == company_id), None)
    return company.stock_price if company and company.active else 0.0
