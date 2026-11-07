"""Anticipations locales et imparfaites (mode « local » de ``expectations_mode``).

Chaque entreprise prévoit à partir de ce qu'elle observe elle-même, avec retard et
erreur ; aucune ne connaît la demande agrégée du mois ni les décisions des autres.

- Alimentation et biens : la demande prévue est la demande adressée à l'entreprise
  lissée (ses ventes plus la demande qu'elle n'a pas servie, ``Company.demand_ema``),
  multipliée par une erreur tirée chaque mois (``demand_forecast_error``) par un
  générateur propre. Elle produit de quoi porter son stock à
  ``target_inventory_months`` mois de cette prévision.
- Promoteurs : demande observée = logements loués + demandes de location reçues et non servies
  (ménages sans logement) + ventes prévues aux ménages sur la durée d'un chantier. Seules les ventes sont lissées et incertaines ; parc visé = prévision × (1 +
  ``housing_vacancy_norm``) ; ils ignorent les chantiers des autres promoteurs.
- Coûts : chaque entreprise anticipe la hausse de ses coûts par lissage adaptatif
  de la croissance observée des salaires qu'elle offre (loyers pour un promoteur),
  sans ancrage sur la cible de la banque centrale ni plafond. L'indice des intrants
  n'y entre pas : il suit les prix de vente eux-mêmes (boucle autoréalisatrice).
"""

from __future__ import annotations

import math
import random

from economy.models import Company, SimulationConfig


def _forecast_noise(rng: random.Random, sigma: float) -> float:
    """Erreur multiplicative de moyenne 1 (loi log-normale)."""
    if sigma <= 0.0:
        return 1.0
    return math.exp(rng.gauss(0.0, sigma) - 0.5 * sigma * sigma)


def update_demand_forecasts(
    companies: list[Company], config: SimulationConfig, rng: random.Random
) -> None:
    """Prévision du mois de chaque entreprise d'alimentation et de biens."""
    for company in companies:
        if company.active and company.sector in ("food", "goods"):
            company.demand_forecast = max(0.0, company.demand_ema) * _forecast_noise(
                rng, config.demand_forecast_error
            )


def planned_production(company: Company, config: SimulationConfig) -> float:
    """Production voulue : porter son stock à ``target_inventory_months`` mois de sa prévision."""
    stock = company.inventory if company.sector == "food" else company.goods_inventory
    return max(0.0, company.demand_forecast * config.target_inventory_months - stock)


def update_housing_gaps(
    companies: list[Company], config: SimulationConfig, rng: random.Random
) -> int:
    """Logements que chaque promoteur veut lancer d'après sa propre demande ; retourne le total."""
    total = 0
    for company in companies:
        if not company.active or company.sector != "real_estate":
            continue
        # Les baux en cours sont connus, ce ne sont pas des ventes à prévoir.
        # Bruiter tout le parc loué puis ne réaliser que les écarts positifs
        # transformait une erreur centrée en construction spéculative permanente.
        observed = float(company.previous_homes_sold)
        company.housing_demand_ema = (
            company.housing_demand_ema
            + config.demand_smoothing * (observed - company.housing_demand_ema)
        )
        # baux en cours + demandes reçues non servies (connues, non bruitées) + ventes prévues
        forecast = company.occupied_units + company.unserved_rentals + (
            company.housing_demand_ema * max(1, config.build_time_months)
            * _forecast_noise(rng, config.demand_forecast_error)
        )
        target = forecast * (1.0 + config.housing_vacancy_norm)
        owned = company.property_inventory + len(company.construction_pipeline)
        company.housing_gap = max(0, int(round(target - owned)))
        total += company.housing_gap
    return total


def update_cost_expectations(
    companies: list[Company],
    config: SimulationConfig,
    input_growth: float,
    rent_growth: float,
) -> None:
    """Anticipation adaptative de la hausse mensuelle des coûts de chaque entreprise."""
    speed = config.inflation_expectation_speed
    for company in companies:
        if not company.active:
            continue
        if company.sector == "real_estate":
            observed = rent_growth
        else:
            # salaires qu'elle offre seulement : l'indice des intrants suit les prix de
            # vente eux-mêmes, et l'inclure crée une boucle autoréalisatrice (baisse des
            # prix → baisse anticipée des coûts → baisse des prix ; mesurée en phase 4)
            observed = (
                company.wage_offer / company.cost_reference - 1.0 if company.cost_reference > 0.0 else 0.0
            )
        company.cost_reference = company.wage_offer
        company.expected_cost_growth += speed * (observed - company.expected_cost_growth)


def sector_expected_growth(companies: list[Company], sector: str, default: float) -> float:
    """Moyenne des anticipations d'un secteur pondérée par les ventes (dérive du prix de secteur)."""
    firms = [c for c in companies if c.active and c.sector == sector]
    weights = [max(0.0, c.previous_units_sold) for c in firms]
    if not firms or sum(weights) <= 0.0:
        return default
    return sum(c.expected_cost_growth * w for c, w in zip(firms, weights)) / sum(weights)
