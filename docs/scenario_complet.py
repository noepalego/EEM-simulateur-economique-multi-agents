"""Exécute et exporte le scénario documenté dans le README (bibliothèque standard)."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "readme_complet"
SEEDS = (1001, 1002, 1003)
MONTHS = 400
sys.path.insert(0, str(ROOT))

from economy.models import SimulationConfig
from economy.sectors import FIRM_SECTORS
from economy.simulation import EconomySimulation


def configuration(seed: int) -> SimulationConfig:
    """Les 27 cases de la barre latérale, plus les réglages numériques du scénario."""
    return SimulationConfig(
        seed=seed, population_size=500, companies_per_sector=3, warmup_months=120,
        monthly_population_growth_rate=0.005, maximum_population_growth=1.0,
        political_cycle_enabled=True, economic_voting=True,
        enriched_objectives=True, ownership_regime=True,
        countercyclical_capital=True, bank_capital_backstop=True,
        experience_learning=True, property_upkeep=True, consumer_preferences=True,
        diversification=True, conglomerate_takeovers=True, retail_stores=True,
        group_distribution=True, private_startups=True, staffless_closure_months=6,
        shareholder_governance=True, corporate_bonds=True, foreign_trade=True,
        collective_bargaining=True, influence_ranking=True, personal_popularity=True,
        candidate_elections=True, executive_power_laws=True, business_subsidies=True,
        political_favoritism=True, corporate_lobbying=True, revolving_door=True,
        political_dynasties=True,
        # Leviers expérimentaux activés par un curseur plutôt que par une case.
        volume_discount_max=0.22, fixed_cost_share=0.10, stock_earnings_smoothing_months=3,
    )


def snapshot(sim, record):
    firms = [c for c in sim.companies if c.active]
    state = sim.institutions
    objectives = {}
    for role, office in state.offices.items():
        for rank, objective in (("principal", office.objective), ("secondaire", office.secondary)):
            if objective is not None:
                objectives.setdefault(role, {})[rank] = asdict(objective)

    return {
        "market": asdict(record),
        "state": asdict(state.history[-1]),
        "enterprise": dict(sim.enterprise.history[-1]),
        "power": dict(sim.power.history[-1]),
        "observations": {
            "active_by_sector": {s: sum(c.sector == s for c in firms) for s, _ in FIRM_SECTORS},
            "listed": sum(c.listed for c in firms),
            "all_companies": len(sim.companies),
            "ownership_regime": state.ownership_regime,
            "unemployment_long_share_of_unemployed": (
                record.long_term_unemployment * record.population_count
                / (record.population_count - record.employed_count)
                if record.population_count > record.employed_count else 0.0
            ),
        },
        "objectives": objectives,
    }


def flat(row, prefix=""):
    for key, value in row.items():
        name = prefix + key
        if isinstance(value, dict):
            yield from flat(value, name + ".")
        else:
            yield name, json.dumps(value, ensure_ascii=False) if isinstance(value, (list, tuple)) else value


def run(job):
    seed, months, output = job
    config = configuration(seed)
    sim = EconomySimulation(config)
    print(f"Graine {seed} : chauffe terminée ({len(sim.people)} ménages)", flush=True)
    rows = [snapshot(sim, sim.history[-1])]
    for i in range(1, months + 1):
        rows.append(snapshot(sim, sim.step()))
        if i % 50 == 0 or i == months:
            print(f"Graine {seed} : période {i}/{months}", flush=True)
    offset = sim.warmup_offset
    data = {
        "seed": seed, "months": months, "config": asdict(config),
        "rows": rows,
        "elections": [{**e, "period": e["mois"] - offset}
                      for e in sim.institutions.elections if e["mois"] > offset],
        "terms": [{"from": t.since - offset, "to": t.until - offset if t.until is not None else None,
                   "orientation": t.orientation, "principal": t.record, "secondary": t.secondary_record}
                  for t in sim.institutions.terms],
        "resolutions": [{**asdict(e), "period": e.month - offset} for e in sim.resolution_events],
    }
    path = output / f"graine_{seed}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    flattened = [dict(flat(row)) for row in rows]
    columns = list(dict.fromkeys(key for row in flattened for key in row))
    with path.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(flattened)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", nargs="+", type=int, default=SEEDS)
    parser.add_argument("--months", type=int, default=MONTHS)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.months < 1 or args.jobs < 1 or len(set(args.seeds)) != len(args.seeds):
        parser.error("Il faut au moins un mois, un processus et des graines distinctes.")
    args.output.mkdir(parents=True, exist_ok=True)
    jobs = [(seed, args.months, args.output) for seed in args.seeds]
    if args.jobs == 1:
        for job in jobs:
            print(run(job))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for path in pool.map(run, jobs):
                print(path)


if __name__ == "__main__":
    main()
