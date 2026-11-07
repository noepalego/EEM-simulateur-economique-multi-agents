"""Gouvernements : orientation politique et positions sur les leviers de politique économique.

Une orientation est un point d'une ligne de 0 à 100 :
  0 = libéral (impôts bas, État léger, pas de salaire minimum, privatisations),
 50 = centre (les réglages de ``SimulationConfig`` tels quels),
100 = social (impôts et redistribution élevés, salaire minimum, État actionnaire).

Le nouvel État (``economy/institutions.py``) donne à chaque gouvernement des doctrines dont les
positions sur ces leviers et leurs amplitudes reprennent celles de l'ancien État à leviers ;
l'élection et l'application des leviers de cet ancien État (``elect_government``,
``apply_government_policy``) ont été retirées le 5 novembre 2026 (mission « optimisation »).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# levier -> amplitude : valeur appliquée = réglage de base × (1 + amplitude × position)
# où la position, dans [-1, 1], est négative à droite et positive à gauche.
MULTIPLIER_LEVERS: dict[str, float] = {
    "income_tax": 0.50,          # impôt sur le revenu
    "top_tax": 0.90,             # tranche marginale supérieure
    "corporate_tax": 0.50,       # impôt sur les bénéfices
    "benefits": 0.50,            # allocation chômage
    "public_employment": 0.40,   # fonction publique
    "public_purchases": 0.40,    # achats de biens et services
    "social_transfers": 0.80,    # transferts sociaux universels
    "public_investment": 0.50,   # infrastructures publiques
    "vat": -0.30,                # TVA : plus basse à gauche (impôt sur la consommation, peu progressif)
}
# leviers à valeur propre (pas des multiplicateurs)
OTHER_LEVERS = ("minimum_wage", "state_equity", "price_controls")
ALL_LEVERS = (*MULTIPLIER_LEVERS, *OTHER_LEVERS)

LEVER_LABELS = {
    "income_tax": "Impôt sur le revenu",
    "top_tax": "Tranche supérieure de l'impôt",
    "corporate_tax": "Impôt sur les bénéfices",
    "benefits": "Allocation chômage",
    "public_employment": "Emploi public",
    "public_purchases": "Achats publics",
    "social_transfers": "Transferts sociaux",
    "public_investment": "Investissement public",
    "vat": "TVA",
    "minimum_wage": "Salaire minimum",
    "state_equity": "Participations de l'État",
    "price_controls": "Encadrement des prix",
}


def orientation_label(orientation: float) -> str:
    if orientation < 20.0:
        return "Libéral"
    if orientation < 40.0:
        return "Centre droit"
    if orientation < 60.0:
        return "Centre"
    if orientation < 80.0:
        return "Centre gauche"
    return "Social"


@dataclass
class Government:
    """Le gouvernement en place : orientation (0-100) et position de chaque levier."""

    orientation: float = 50.0
    since_month: int = 0
    # position de chaque levier dans [-1, 1] (-1 = libéral, +1 = social)
    positions: dict[str, float] = field(default_factory=dict)
    reelected: bool = False

    @property
    def label(self) -> str:
        return orientation_label(self.orientation)

    def position(self, lever: str) -> float:
        return self.positions.get(lever, 0.0)
