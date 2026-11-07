"""Innovation et croissance endogène.

Le stock de connaissances (``resources.knowledge``) est un bien non rival : il ne
s'épuise pas quand on l'utilise, il se construit par la recherche. Il croît avec
l'intensité de recherche de l'économie (R&D des entreprises et travail de
recherche des entreprises technologiques, en part du PIB) et s'érode par
obsolescence. La productivité de toutes les entreprises de production suit ce
stock avec une élasticité inférieure à 1 (rendements décroissants) :

    croissance des connaissances = γ × intensité de recherche - obsolescence
    indice de productivité       = (connaissances / connaissances initiales) ^ ε

Une économie qui investit durablement dans la recherche croît ; sans recherche
elle stagne puis recule (obsolescence).
"""

from __future__ import annotations

from economy.models import Company, Resources, SimulationConfig


def research_effort(
    companies: list[Company],
    firm_research_spending: float,
    config: SimulationConfig,
) -> float:
    """Effort de recherche du mois : R&D des entreprises + chercheurs de la technologie."""
    tech_payroll = sum(
        c.wage_bill for c in companies if c.active and c.sector == "technology"
    )
    return max(0.0, firm_research_spending) + config.tech_research_share * tech_payroll


def update_knowledge(
    resources: Resources,
    config: SimulationConfig,
    research_spending: float,
    monthly_gdp: float,
) -> float:
    """Fait évoluer le stock de connaissances ; retourne l'indice de productivité."""
    if resources.knowledge_reference <= 0.0:
        resources.knowledge_reference = max(resources.knowledge, 1.0)
    intensity = research_spending / max(monthly_gdp, 1.0)
    resources.research_intensity += 0.10 * (intensity - resources.research_intensity)
    growth = (
        config.knowledge_growth_per_intensity * resources.research_intensity
        - config.knowledge_depreciation_rate
    )
    resources.knowledge = max(1.0, resources.knowledge * (1.0 + growth))
    resources.knowledge_index = (
        resources.knowledge / resources.knowledge_reference
    ) ** config.knowledge_elasticity
    return resources.knowledge_index
