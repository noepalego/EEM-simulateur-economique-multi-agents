"""Lectures communes aux régions d'un monde (onglet « Monde »).

Totaux du monde, classement mondial des entreprises, groupes et filiales par région, titres
détenus d'une région à l'autre, leaders de secteur et plus grandes fortunes.

Lectures seules : aucune fonction ne modifie le monde ni ne tire de nombre aléatoire. Les
montants sont convertis en unité commune (monnaie de la région × ``world.rates``), seule façon
de comparer des entreprises ou des ménages de régions différentes.
"""

from __future__ import annotations

from economy.institutions import display_name
from economy.models import Company
from economy.sectors import SECTOR_LABELS, sector_label
from economy.wealth import wealth_leaderboards

# Comptes techniques (chambre, non-résidents, fonds) : jamais classés parmi les entreprises
_TECHNICAL_SECTORS = ("foreign", "fund")
LOCAL_LINK = "filiale dans la région"
FOREIGN_LINK = "contrôlée depuis une autre région"
RANKING_KEYS = {
    "Capitalisation": "Capitalisation",
    "Chiffre d'affaires": "Chiffre d'affaires",
    "Salariés": "Salariés",
    "Bénéfice": "Bénéfice",
}


def _capitalization(company: Company) -> float:
    return company.stock_price * max(0.0, company.shares_outstanding - company.treasury_shares)


def _firms(world) -> list[tuple[int, Company]]:
    """Entreprises en activité de toutes les régions, dans l'ordre des régions."""
    return [
        (region, company)
        for region, sim in enumerate(world.regions)
        for company in sim.companies
        if company.active and company.sector not in _TECHNICAL_SECTORS
    ]


def _labels(world) -> dict[tuple[int, str], str]:
    """Nom court de chaque entreprise, sans la mention « (groupe …) » : le groupe a sa colonne."""
    return {
        (region, company_id): label.split(" (groupe")[0]
        for region, sim in enumerate(world.regions)
        for company_id, label in sim.company_labels().items()
    }


def group_links(world) -> dict[tuple[int, str], dict]:
    """Société mère directe de chaque entreprise contrôlée : ``(région, entreprise)`` ->
    ``{"mere": (région, entreprise), "lien": …, "part": part du capital en circulation}``.

    Deux origines : une filiale créée ou rachetée dans sa région (``Company.parent_id``) et une
    entreprise contrôlée depuis une autre région (``World.controlled``), qui l'emporte. Un
    acquéreur absorbé depuis est remplacé par l'entreprise qui l'a absorbé."""
    links: dict[tuple[int, str], dict] = {}
    for region, sim in enumerate(world.regions):
        by_id = {company.id: company for company in sim.companies}
        for company in sim.companies:
            parent = by_id.get(company.parent_id) if company.parent_id else None
            if not company.active or parent is None or not parent.active:
                continue
            floated = company.shares_outstanding - company.treasury_shares
            links[(region, company.id)] = {
                "mere": (region, parent.id), "lien": LOCAL_LINK,
                "part": parent.shares.get(company.id, 0.0) / floated if floated > 0.0 else 0.0,
            }
    for (region, company_id), (home, acquirer_id, _month) in world.controlled.items():
        target = next((c for c in world.regions[region].companies if c.id == company_id and c.active), None)
        acquirer = world._resolve_owner(home, acquirer_id)
        if target is None or not isinstance(acquirer, Company) or not acquirer.active:
            continue
        floated = target.shares_outstanding - target.treasury_shares
        stake = world.holdings[region].get(company_id, {}).get((home, acquirer_id), 0.0)
        links[(region, company_id)] = {
            "mere": (home, acquirer.id), "lien": FOREIGN_LINK,
            "part": stake / floated if floated > 0.0 else 0.0,
        }
    return links


def _roots(links: dict) -> dict[tuple[int, str], tuple[int, str]]:
    """Tête de groupe de chaque entreprise contrôlée (on remonte les sociétés mères)."""
    roots = {}
    for key in links:
        head, seen = key, {key}
        while head in links and links[head]["mere"] not in seen:
            head = links[head]["mere"]
            seen.add(head)
        roots[key] = head
    return roots


def world_totals(world) -> dict:
    """Agrégats du monde au dernier mois, en unité commune pour les montants."""
    firms = _firms(world)
    population = [len(sim.people) for sim in world.regions]
    records = [sim.history[-1] if sim.history else None for sim in world.regions]
    gdp = [
        (record.nominal_gdp * world.rates[region] if record is not None else 0.0)
        for region, record in enumerate(records)
    ]
    capitalization = [0.0] * len(world.regions)
    for region, company in firms:
        capitalization[region] += _capitalization(company) * world.rates[region]
    total_gdp, total_cap, households = sum(gdp), sum(capitalization), sum(population)
    return {
        "menages": households,
        "entreprises": len(firms),
        "salaries": sum(len(company.employees) for _, company in firms),
        "pib_nominal": total_gdp,
        "capitalisation": total_cap,
        # moyenne des régions pondérée par leur nombre de ménages
        "chomage": sum(
            record.unemployment * count for record, count in zip(records, population) if record is not None
        ) / max(1, households),
        "controlees": len(world.controlled),
        "groupes_multiregionaux": len({(home, acquirer) for home, acquirer, _ in world.controlled.values()}),
        "frontaliers": len(world.commuters),
        "regions": [
            {
                "Région": name,
                "Part des ménages": population[region] / max(1, households),
                "Part du PIB": gdp[region] / total_gdp if total_gdp > 0.0 else 0.0,
                "Part de la capitalisation": capitalization[region] / total_cap if total_cap > 0.0 else 0.0,
                "Part des entreprises": sum(1 for r, _ in firms if r == region) / max(1, len(firms)),
            }
            for region, name in enumerate(world.names)
        ],
    }


def company_ranking(world, limit: int | None = 20, sort: str = "Capitalisation") -> list[dict]:
    """Classement mondial des entreprises en activité, selon ``sort`` (clé de ``RANKING_KEYS``).

    Chaque ligne dit de quelle région est l'entreprise, à quel groupe elle appartient et dans
    quelles régions elle a des filiales (directes ou non)."""
    labels = _labels(world)
    links = group_links(world)
    roots = _roots(links)
    subsidiaries: dict[tuple[int, str], list[int]] = {}
    for key, head in roots.items():
        subsidiaries.setdefault(head, []).append(key[0])
    rows = []
    for region, company in _firms(world):
        key = (region, company.id)
        rate = world.rates[region]
        head = roots.get(key)
        regions = subsidiaries.get(key, [])
        rows.append({
            "Entreprise": labels[key],
            "Région": world.names[region],
            "Secteur": sector_label(company.sector),
            "Capitalisation": _capitalization(company) * rate,
            "Chiffre d'affaires": company.revenue * rate,
            "Bénéfice": company.profit * rate,
            "Salariés": len(company.employees),
            "Groupe": (
                f"{labels[head]} ({world.names[head[0]]})" if head is not None
                else "tête de groupe" if regions else "indépendante"
            ),
            "Filiales": len(regions),
            "Régions des filiales": " · ".join(
                f"{name} {regions.count(index)}" for index, name in enumerate(world.names) if index in regions
            ) or "—",
        })
    rows.sort(key=lambda row: row[RANKING_KEYS.get(sort, "Capitalisation")], reverse=True)
    rows = rows if limit is None else rows[:limit]
    return [{"Rang": rank, **row} for rank, row in enumerate(rows, start=1)]


def group_rows(world) -> tuple[list[dict], list[dict]]:
    """Groupes du monde : ``(groupes, filiales)``.

    ``groupes`` : une ligne par tête de groupe, avec le nombre de ses filiales dans chaque
    région ; les groupes présents dans le plus de régions d'abord. ``filiales`` : une ligne par
    entreprise contrôlée, avec sa société mère directe et la part qu'elle détient."""
    labels = _labels(world)
    links = group_links(world)
    roots = _roots(links)
    companies = {(region, company.id): company for region, company in _firms(world)}
    members: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for key, head in roots.items():
        if key in companies and head in companies:
            members.setdefault(head, []).append(key)
    groups, details = [], []
    for head, keys in members.items():
        company = companies[head]
        everyone = [head] + keys
        groups.append({
            "Groupe": labels[head],
            "Siège": world.names[head[0]],
            "Secteur": sector_label(company.sector),
            **{f"Filiales · {name}": sum(1 for region, _ in keys if region == index)
               for index, name in enumerate(world.names)},
            "Régions d'implantation": len({region for region, _ in everyone}),
            "Salariés du groupe": sum(len(companies[key].employees) for key in everyone),
            "Chiffre d'affaires du groupe": sum(
                companies[key].revenue * world.rates[key[0]] for key in everyone
            ),
            "Capitalisation de la tête": _capitalization(company) * world.rates[head[0]],
        })
        for key in keys:
            link, member = links[key], companies[key]
            details.append({
                "Groupe": labels[head],
                "Siège": world.names[head[0]],
                "Filiale": labels[key],
                "Région": world.names[key[0]],
                "Secteur": sector_label(member.sector),
                "Lien": link["lien"],
                "Société mère": f"{labels[link['mere']]} ({world.names[link['mere'][0]]})",
                "Part détenue": link["part"],
                "Salariés": len(member.employees),
                "Chiffre d'affaires": member.revenue * world.rates[key[0]],
            })
    groups.sort(key=lambda row: (row["Régions d'implantation"], row["Salariés du groupe"]), reverse=True)
    order = {row["Groupe"] + row["Siège"]: index for index, row in enumerate(groups)}
    details.sort(key=lambda row: (order[row["Groupe"] + row["Siège"]], row["Région"], row["Filiale"]))
    return groups, details


def ownership_matrix(world) -> list[dict]:
    """Titres détenus hors de la région de l'entreprise (registre ``World.holdings``) : une ligne
    par région des entreprises, une colonne par région des porteurs, en unité commune.

    Les porteurs sont des entreprises qui ramassent ou contrôlent une cible, et des ménages
    partis vivre ailleurs en gardant leurs titres."""
    rows = []
    for region, sim in enumerate(world.regions):
        rate = world.rates[region]
        prices = {c.id: c.stock_price for c in sim.companies if c.active}
        held = [0.0] * len(world.regions)
        for company_id, owners in world.holdings[region].items():
            price = prices.get(company_id, 0.0)
            for (owner_region, _owner), quantity in owners.items():
                held[owner_region] += quantity * price * rate
        capitalization = sum(
            _capitalization(c) for c in sim.companies if c.active and c.sector not in _TECHNICAL_SECTORS
        ) * rate
        rows.append({
            "Entreprises de": world.names[region],
            **{f"Porteurs · {name}": held[index] for index, name in enumerate(world.names)},
            "Part de la capitalisation": sum(held) / capitalization if capitalization > 0.0 else 0.0,
        })
    return rows


def sector_leaders(world) -> list[dict]:
    """Par secteur : la première entreprise du monde par chiffre d'affaires, sa part du chiffre
    d'affaires mondial du secteur, et la part de chaque région."""
    by_sector: dict[str, list[tuple[int, Company]]] = {}
    for region, company in _firms(world):
        by_sector.setdefault(company.sector, []).append((region, company))
    labels = _labels(world)
    rows = []
    for sector in [s for s in SECTOR_LABELS if s in by_sector] + [s for s in by_sector if s not in SECTOR_LABELS]:
        firms = by_sector[sector]
        revenue = [0.0] * len(world.regions)
        for region, company in firms:
            revenue[region] += company.revenue * world.rates[region]
        total = sum(revenue)
        region, leader = max(firms, key=lambda item: item[1].revenue * world.rates[item[0]])
        rows.append({
            "Secteur": sector_label(sector),
            "Entreprises": len(firms),
            "Leader mondial": labels[(region, leader.id)],
            "Région du leader": world.names[region],
            "Part du leader": leader.revenue * world.rates[region] / total if total > 0.0 else 0.0,
            **{f"Part · {name}": revenue[index] / total if total > 0.0 else 0.0
               for index, name in enumerate(world.names)},
        })
    return rows


def household_ranking(world, limit: int = 15) -> list[dict]:
    """Plus grandes fortunes du monde, en unité commune. Les titres qu'un ménage a gardés dans
    une autre région (registre du monde) s'ajoutent à sa fortune dans sa région de résidence."""
    abroad: dict[tuple[int, str], float] = {}
    for region, sim in enumerate(world.regions):
        prices = {c.id: c.stock_price for c in sim.companies if c.active}
        for company_id, owners in world.holdings[region].items():
            value = prices.get(company_id, 0.0) * world.rates[region]
            for owner, quantity in owners.items():
                abroad[owner] = abroad.get(owner, 0.0) + quantity * value
    rows = []
    for region, sim in enumerate(world.regions):
        rate = world.rates[region]
        households, _ = wealth_leaderboards(sim.people, sim.companies, sim.live, len(sim.people))
        for row in households:
            outside = abroad.get((region, row["id"]), 0.0)
            rows.append({
                "Ménage": f"{display_name(row['id'])} ({row['Ménage']})",
                "Région": world.names[region],
                "Fortune nette": row["Fortune nette"] * rate + outside,
                "Trésorerie": row["Trésorerie"] * rate,
                "Immobilier": row["Immobilier"] * rate,
                "Actions": row["Actions"] * rate + outside,
                "dont titres d'une autre région": outside,
                "Dette immobilière": row["Dette immobilière"] * rate,
            })
    rows.sort(key=lambda row: row["Fortune nette"], reverse=True)
    return [{"Rang": rank, **row} for rank, row in enumerate(rows[:limit], start=1)]
