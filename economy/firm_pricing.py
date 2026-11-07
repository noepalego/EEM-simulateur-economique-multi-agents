"""Formation des prix par signaux (mode « firm_signals » de ``pricing_mode``).

Aucune marge visée. Chaque entreprise d'alimentation et de biens fixe son prix à
partir de son coût variable unitaire (salaires du mois rapportés à sa production
possible, plus intrants et matières par unité) et d'une marge qui lui est propre.
Cette marge évolue chaque mois avec ce que l'entreprise observe :

- la demande qui lui était adressée et qu'elle n'a pas pu servir (hausse) ;
- son stock rapporté à ses ventes, au-delà de sa norme de stock (baisse) ;
- l'écart avec le prix moyen de ses concurrents pondéré par leurs ventes
  (rapprochement ; son agressivité commerciale décale le prix visé ; sans
  concurrent, aucune pression) ;
- la concurrence elle-même : quand l'entreprise n'est pas à court de capacité
  (aucune demande non servie), elle rogne sa marge en proportion de la part du
  marché que tiennent ses concurrents (1 − sa part de ventes). Sans cette érosion,
  rien ne ramène vers le coût un marché qui s'équilibre à prix élevé (effectifs
  réglés sur une demande faible) ; avec elle, un monopole garde sa marge et un
  secteur disputé la perd : le pouvoir de marché émerge du nombre de concurrents.

La marge ne descend jamais sous zéro : l'entreprise ne vend pas sous son coût
variable. Le coût est calculé sur la production possible et non sur les ventes :
une baisse des ventes ne relève pas le coût unitaire, ce qui évite la spirale
« prix plus haut, ventes plus faibles, coût moyen plus haut ». Le prix suit les
salaires et les intrants observés ; l'anticipation d'inflation commune
(``_expected_price_growth``) n'y entre que comme un facteur de niveau (hausse
attendue du coût d'ici la vente), sans s'accumuler d'un mois à l'autre.

Le pouvoir de marché émerge de la répartition des ventes (``goods._allocate_sales``) :
avec moins de concurrents, une hausse de prix fait perdre moins de clients et
aucun concurrent ne rappelle le prix vers le sien.

Les indices des secteurs B2B (énergie, technologie) suivent les mêmes signaux au
niveau du secteur : demande non servie (hausse), capacité ou stock inemployés
(baisse, seulement si le secteur couvre ses coûts), pertes (hausse).
"""

from __future__ import annotations

from economy import diagnostics
from economy.models import Company, SimulationConfig

# variation mensuelle maximale : celle du prix de marché dans ``update_food_price``
FIRM_PRICE_SPEED = 0.10
# variation mensuelle maximale d'un indice B2B : celle de l'ancienne pression de marge
INDEX_SPEED = 0.01


def variable_unit_cost(company: Company) -> float | None:
    """Coût variable par unité : salaires du mois / production possible + intrants par
    unité ; None si l'entreprise ne peut rien produire."""
    if company.potential_output <= 0.0:
        return None
    return company.wage_bill / company.potential_output + company.unit_input_cost


def restart_quote_cost(company: Company, rivals: list[Company], config: SimulationConfig) -> float:
    """Coût unitaire d'une reprise de production (mode « restart_quote ») : salaire offert /
    production d'un salarié, plus les intrants par unité (les siens, ou à défaut ceux que
    paient ses concurrents, faute de production propre récente)."""
    from economy.business import output_per_worker

    inputs = company.unit_input_cost
    if inputs <= 0.0:
        known = [c.unit_input_cost for c in rivals if c.unit_input_cost > 0.0]
        inputs = sum(known) / len(known) if known else 0.0
    return company.wage_offer / output_per_worker(company, config) + inputs


def _markup(company: Company, config: SimulationConfig) -> float:
    """Décalage du prix visé selon l'agressivité commerciale (comme ``apply_competitive_pricing``)."""
    return 1.0 + config.competitive_pricing_strength * (0.5 - company.pricing_aggression) * 2.0


def update_firm_prices(
    companies: list[Company],
    sector: str,
    config: SimulationConfig,
    anchor_growth: float,
    market_price: float,
    price_cap: float | None,
    ceiling: float,
    regulated: bool = False,
) -> float:
    """Prix de chaque entreprise du secteur ; retourne le prix de marché (moyenne
    des prix pondérée par les ventes du mois précédent, sinon par les stocks).

    ``regulated`` (nouvel État, secteur régulé) : l'érosion concurrentielle de la marge s'applique
    comme si l'entreprise ne tenait pas plus de la moitié du marché."""
    firms = [c for c in companies if c.active and c.sector == sector]
    if not firms:
        return market_price
    stock_field = "inventory" if sector == "food" else "goods_inventory"
    before = {c.id: c.price for c in firms}
    sales = {c.id: c.previous_units_sold for c in firms}
    norm = max(config.target_inventory_months, 0.1)
    for company in firms:
        # anticipation propre à l'entreprise (anticipations locales)
        expected = 1.0 + max(0.0, company.expected_cost_growth)
        price = before[company.id]
        cost = variable_unit_cost(company)
        if cost is None or cost <= 0.0:
            # sans production possible : devis de reprise plutôt qu'un prix figé (R6)
            cost = restart_quote_cost(company, firms, config)
        if cost is None or cost <= 0.0:
            continue  # rien à produire ce mois : le prix reste affiché
        if company.price_markup < 0.0:
            # première fixation : l'entreprise part de son prix actuel (ou, nouvelle,
            # du prix du marché décalé de son agressivité) et en déduit sa marge
            start = price if company.previous_units_sold > 0.0 or price > 1.0 else (
                market_price * _markup(company, config)
            )
            company.price_markup = max(0.0, start / (cost * expected) - 1.0)
        signal = 0.0
        sold = company.previous_units_sold
        unmet_ratio = 0.0
        if sold > 0.0:
            unmet_ratio = min(1.0, company.previous_unmet_units / sold)
            signal += config.firm_price_unmet_sensitivity * unmet_ratio
            excess_stock = max(0.0, getattr(company, stock_field) / sold - norm) / norm
            signal -= config.firm_price_inventory_sensitivity * excess_stock
        elif company.previous_unmet_units > 0.0:
            unmet_ratio = 1.0
            signal += config.firm_price_unmet_sensitivity
        total_sales = sum(sales.values())
        if unmet_ratio <= 0.0 and total_sales > 0.0:
            # érosion concurrentielle de la marge, nulle pour un monopole (sauf régulation)
            share = sales[company.id] / total_sales
            if regulated:
                share = min(share, 0.5)
            signal -= config.firm_price_competition_sensitivity * (1.0 - share)
        rival_sales = sum(sales[o.id] for o in firms if o is not company)
        if rival_sales > 0.0:
            rival_price = sum(before[o.id] * sales[o.id] for o in firms if o is not company) / rival_sales
            # décalage d'agressivité relatif à celui des concurrents : sans cela, si les
            # décalages valent plus de 1 en moyenne, chacun vise au-dessus des autres et
            # tous les prix montent ensemble (cliquet mesuré en phase 3)
            rival_markup = sum(
                _markup(o, config) * sales[o.id] for o in firms if o is not company
            ) / rival_sales
            signal += config.firm_price_competition_sensitivity * (
                rival_price * _markup(company, config) / rival_markup / price - 1.0
            )
        # marge jamais négative : pas de vente sous le coût variable
        factor = max(1.0, (1.0 + company.price_markup) * (1.0 + signal))
        wanted = factor * cost * expected
        new_price = max(price * (1.0 - FIRM_PRICE_SPEED), min(price * (1.0 + FIRM_PRICE_SPEED), wanted))
        diagnostics.bite("vitesse_prix_entreprise", new_price != wanted)
        if price_cap is not None:
            diagnostics.bite("encadrement_prix", new_price > price * (1.0 + price_cap))
            new_price = min(new_price, price * (1.0 + price_cap))
        company.price = max(1.0, min(ceiling, new_price))
        diagnostics.bite("plafond_prix_entreprise", new_price > ceiling)
        # la marge suit le prix réellement affiché (vitesse, encadrement, plafond)
        company.price_markup = max(0.0, company.price / (cost * expected) - 1.0)
    weights = sales if sum(sales.values()) > 0.0 else {
        c.id: max(0.0, getattr(c, stock_field)) for c in firms
    }
    total = sum(weights.values())
    if total <= 0.0:
        return sum(c.price for c in firms) / len(firms)
    return sum(c.price * weights[c.id] for c in firms) / total


def signal_index_change(
    unmet_ratio: float,
    idle_ratio: float,
    sector_margin: float,
    config: SimulationConfig,
    concentration: float = 1.0,
    scarcity: bool = False,
) -> float:
    """Variation mensuelle d'un indice de prix B2B, bornée à ±``INDEX_SPEED``.

    Hausse avec la demande non servie et avec les pertes du secteur (prix sous le
    coût). Baisse, seulement si le secteur couvre ses coûts, avec la capacité ou le
    stock inemployés et, sans demande non servie, avec la concurrence : érosion
    proportionnelle à 1 − indice de Herfindahl du secteur (nulle pour un monopole).

    ``scarcity`` (``energy_market="scarcity"``, R8) : les pertes ne relèvent plus le prix
    et la capacité inemployée le fait baisser même en pertes : seule la rareté le monte.
    """
    up = config.firm_price_unmet_sensitivity * max(0.0, unmet_ratio)
    if not scarcity:
        up += config.firm_price_cost_sensitivity * max(0.0, -sector_margin)
    down = 0.0
    if sector_margin > 0.0 or scarcity:
        down = config.firm_price_inventory_sensitivity * max(0.0, idle_ratio)
        if unmet_ratio <= 0.0:
            down += config.firm_price_competition_sensitivity * max(0.0, 1.0 - concentration)
    change = up - down
    diagnostics.bite("vitesse_indice_b2b", abs(change) > INDEX_SPEED)
    return max(-INDEX_SPEED, min(INDEX_SPEED, change))
