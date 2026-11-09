"""Campagne gauche/droite : gouvernement imposé à 0 (libéral) ou 100 (social) pendant N mois.

python experiences/gauche_droite_campagne.py MOIS GRAINE... [--jobs J] [--set param=valeur ...]
                                              [--output DOSSIER]   (README : 400 1001 ... 1010)

Même configuration (``SimulationConfig(seed)``) et même chauffe pour les trois scénarios d'une
graine : « droite » (orientation 0 imposée au premier mois observé puis à chaque élection),
« gauche » (100), « temoin » (élections normales). Le gouvernement est imposé par le mode
intervention (economy/interventions.py) ; aucun autre paramètre n'est changé.
Sortie : results/gauche_droite/runs.jsonl (une ligne par graine et scénario, séries annuelles).

Lot E (novembre 2026) : ``--set`` ajoute des réglages à la configuration des trois scénarios (par
exemple ``--set ownership_regime=1``) ; ``--output`` choisit le dossier de sortie sous ``results/``
(par défaut ``gauche_droite``, inchangé). Chaque année porte aussi la concentration des ventes par
entreprise (Herfindahl moyen des secteurs, alimentation, biens) et la part du capital des
entreprises non bancaires détenue en parts salariées (0 sans régime de propriété).
"""
import json
import math
import os
import statistics
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from economy.interventions import request_orientation  # noqa: E402
from economy.models import SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402

SCENARIOS = {"droite": 0.0, "gauche": 100.0, "temoin": None}
FIELDS = (
    "real_gdp", "nominal_gdp", "unemployment", "inflation_rate", "real_average_wage",
    "real_household_income", "household_gini", "disposable_income_gini", "poverty_rate",
    "unhoused_households", "household_top10_share", "household_top1_share", "public_debt",
    "public_deficit", "public_revenue", "public_spending", "bankruptcies", "startups",
    "active_companies", "long_term_unemployment", "stock_market_value", "minimum_wage",
    "average_wage", "vat_rate", "public_employment", "population_count", "consumer_price_index",
    "household_wealth_median", "government_orientation", "ledger_residual", "idle_companies",
    "investment", "tfp_index", "total_profit",
)


def _parse(value: str):
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _concentration(sim) -> dict:
    """Herfindahl des ventes par entreprise (moyenne des secteurs, alimentation, biens) et part du
    capital des entreprises non bancaires détenue en parts salariées (lot E)."""
    sectors = {}
    for company in sim.companies:
        if company.active:
            sectors.setdefault(company.sector, []).append(company)
    hhi = {}
    for sector, firms in sectors.items():
        revenue = sum(max(0.0, c.previous_revenue) for c in firms)
        hhi[sector] = sum((max(0.0, c.previous_revenue) / revenue) ** 2 for c in firms) if revenue > 0 else 1.0
    capital = sum(c.shares_outstanding for c in sim.companies if c.active and c.sector != "bank")
    stakes = sum(sum(p.coop_shares.values()) for p in sim.people)
    return {"hhi_mean": statistics.fmean(hhi.values()) if hhi else 1.0, "hhi_food": hhi.get("food", 1.0),
            "hhi_goods": hhi.get("goods", 1.0), "employee_share": stakes / capital if capital > 0 else 0.0}


def run(args):
    seed, months, scenario, overrides = args
    orientation = SCENARIOS[scenario]
    sim = EconomySimulation(SimulationConfig(seed=seed, **overrides))
    period = sim.config.election_period_months
    records = []
    for _ in range(months):
        upcoming = sim.month + 1
        if orientation is not None and (not records or (upcoming - 1) % period == 0):
            request_orientation(sim, orientation)  # remplace aussi l'élu d'un mois d'élection
        record = sim.step()
        records.append({name: getattr(record, name) for name in FIELDS} | _concentration(sim))
    nan = any(not math.isfinite(v) for r in records for v in r.values() if isinstance(v, float))
    years = []
    for start in range(0, months, 12):
        chunk = records[start:start + 12]
        years.append({k: statistics.fmean(r[k] for r in chunk) for k in FIELDS} | {
            "bankruptcies": sum(r["bankruptcies"] for r in chunk),
            "startups": sum(r["startups"] for r in chunk),
            "real_gdp_sum": sum(r["real_gdp"] for r in chunk),
            "nominal_gdp_sum": sum(r["nominal_gdp"] for r in chunk),
            "public_debt_end": chunk[-1]["public_debt"],
            "cpi_end": chunk[-1]["consumer_price_index"],
            **{k: statistics.fmean(r[k] for r in chunk)
               for k in ("hhi_mean", "hhi_food", "hhi_goods", "employee_share")},
        })
    orientations = sorted({round(r["government_orientation"]) for r in records})
    return {
        "seed": seed, "scenario": scenario, "months": months, "nan": nan, "settings": overrides,
        "orientations": orientations[:12], "residual_max": max(abs(r["ledger_residual"]) for r in records),
        "years": years,
    }


def main():
    argv = sys.argv[1:]
    jobs = 8
    if "--jobs" in argv:
        i = argv.index("--jobs")
        jobs = int(argv[i + 1])
        del argv[i:i + 2]
    folder = "gauche_droite"
    if "--output" in argv:
        i = argv.index("--output")
        folder = argv[i + 1]
        del argv[i:i + 2]
    overrides = {}
    if "--set" in argv:
        i = argv.index("--set")
        for pair in argv[i + 1:]:
            key, value = pair.split("=")
            overrides[key] = _parse(value)
        argv = argv[:i]
    months, seeds = int(argv[0]), [int(s) for s in argv[1:]]
    tasks = [(seed, months, scenario, overrides) for seed in seeds for scenario in SCENARIOS]
    out_dir = os.path.join(ROOT, "results", folder)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "runs.jsonl")
    with Pool(jobs) as pool, open(out, "w", encoding="utf-8") as handle:
        for result in pool.imap_unordered(run, tasks):
            handle.write(json.dumps(result) + "\n")
            handle.flush()
            print(result["seed"], result["scenario"], "nan" if result["nan"] else "ok",
                  result["orientations"], flush=True)


if __name__ == "__main__":
    main()
