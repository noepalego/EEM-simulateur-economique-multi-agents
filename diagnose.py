"""Diagnostic reproductible : 120 mois sur plusieurs seeds, bilan annuel + critères.

Usage :
    python diagnose.py                    # 5 seeds, 120 mois, config par défaut
    python diagnose.py --seeds 1 2 3 --months 60 --households 80
    python diagnose.py --quiet            # uniquement le tableau des critères
"""
from __future__ import annotations

import argparse
import statistics
import sys

from economy.models import SimulationConfig
from economy.simulation import SECTORS, EconomySimulation

DEFAULT_SEEDS = (7, 11, 23, 42, 101)
YEARS_SHOWN = (1, 5, 10)


def holder_cash(sim: EconomySimulation) -> dict[str, float]:
    firms = sum(c.cash for c in sim.companies if c.active and c.sector != "bank")
    banks = sum(c.cash for c in sim.companies if c.active and c.sector == "bank")
    return {
        "menages": sum(p.cash for p in sim.people),
        "entreprises": firms,
        "banques": banks,
        "etat": sim.government_balance,
        "fonds_amont": sim.resources.market_cash,
    }


OVERRIDES: dict[str, float] = {}


def run_seed(seed: int, months: int, households: int | None):
    kwargs = {"seed": seed, **OVERRIDES}
    if households:
        kwargs["population_size"] = households
    sim = EconomySimulation(SimulationConfig(**kwargs))
    rows = []
    for _ in range(months):
        record = sim.step()
        n_firms = max(
            1, sum(1 for c in sim.companies if c.active and c.sector != "bank")
        )
        n_banks = max(
            1, sum(1 for c in sim.companies if c.active and c.sector == "bank")
        )
        profit_firms = sum(
            c.profit for c in sim.companies if c.active and c.sector != "bank"
        )
        profit_banks = sum(
            c.profit for c in sim.companies if c.active and c.sector == "bank"
        )
        recurrent = getattr(record, "recurrent_gdp", None)
        if recurrent is None:
            recurrent = record.nominal_gdp - record.housing_construction
        total = getattr(record, "total_gdp", None)
        if total is None:
            total = record.nominal_gdp
        cash = holder_cash(sim)
        rows.append(
            {
                "month": record.month,
                "recurrent": recurrent,
                "total": total,
                "wage": record.average_wage,
                "real_wage": record.real_average_wage,
                "unemp": record.unemployment,
                "profit_firm": profit_firms / n_firms,
                "profit_bank": profit_banks / n_banks,
                "cpi": record.consumer_price_index,
                "infl": record.inflation_rate,
                "emp_sector": dict(record.sector_employment),
                "cash": cash,
                "gov": sim.government_balance,
                "firm_cash": cash["entreprises"],
                "payroll_year": record.average_wage * record.employed_count * 12,
                "active": record.active_companies,
                "pop": record.population_count,
            }
        )
    return rows


def window(rows, end_month: int, key: str, width: int = 12) -> float:
    chunk = [r[key] for r in rows if end_month - width < r["month"] <= end_month]
    return sum(chunk) / len(chunk) if chunk else float("nan")


def yearly_report(seed: int, rows) -> None:
    print(f"\n=== SEED {seed} ===")
    print(
        f"{'an':>3} {'PIBrec':>9} {'PIBtot':>9} {'sal.nom':>8} {'sal.reel':>8} "
        f"{'chom%':>6} {'prof/ent':>9} {'prof.bq':>9} {'IPC':>6} {'infl.an%':>8}"
    )
    for year in YEARS_SHOWN:
        end = year * 12
        if end > rows[-1]["month"]:
            continue
        start_cpi = next((r["cpi"] for r in rows if r["month"] == end - 12), 100.0)
        end_cpi = next(r["cpi"] for r in rows if r["month"] == end)
        print(
            f"{year:>3} {window(rows, end, 'recurrent'):>9.0f} "
            f"{window(rows, end, 'total'):>9.0f} {window(rows, end, 'wage'):>8.0f} "
            f"{window(rows, end, 'real_wage'):>8.0f} "
            f"{100 * window(rows, end, 'unemp'):>6.1f} "
            f"{window(rows, end, 'profit_firm'):>9.0f} "
            f"{window(rows, end, 'profit_bank'):>9.0f} {end_cpi:>6.1f} "
            f"{100 * (end_cpi / start_cpi - 1):>8.1f}"
        )
        emp = {
            s: sum(r["emp_sector"].get(s, 0) for r in rows if end - 12 < r["month"] <= end)
            / 12
            for s, _ in SECTORS
        }
        print("    emploi: " + ", ".join(f"{s}={v:.0f}" for s, v in emp.items()))
        cash = {
            k: sum(r["cash"][k] for r in rows if end - 12 < r["month"] <= end) / 12
            for k in rows[-1]["cash"]
        }
        print("    tréso : " + ", ".join(f"{k}={v:,.0f}" for k, v in cash.items()))


def criteria(rows) -> dict[str, tuple[bool, str]]:
    last = rows[-1]["month"]
    years = range(1, last // 12 + 1)
    out: dict[str, tuple[bool, str]] = {}
    # 1. salaires nominaux (moyenne glissante 12 mois) croissants sur chaque période de 12 mois
    wage_w = [window(rows, y * 12, "wage") for y in years]
    growth = [b / a - 1 for a, b in zip(wage_w, wage_w[1:])]
    out["1 salaires"] = (
        all(g > 0 for g in growth),
        "g min=%+.2f%% fin=%.0f" % (100 * min(growth), wage_w[-1]),
    )
    # 2. PIB récurrent >= 400k, sans rechute > 20 %
    gdp_w = [window(rows, y * 12, "recurrent") for y in years]
    drops = [b / a - 1 for a, b in zip(gdp_w, gdp_w[1:])]
    out["2 PIB"] = (
        gdp_w[-1] >= 400_000 and min(drops) > -0.20,
        "fin=%.0f pire=%+.0f%%" % (gdp_w[-1], 100 * min(drops)),
    )
    # 3. chômage 3-8 % (moyenne) après mois 24, jamais > 15 %
    late = [r["unemp"] for r in rows if r["month"] > 24]
    mean_u = statistics.mean(late)
    out["3 chômage"] = (
        0.03 <= mean_u <= 0.08 and max(late) <= 0.15,
        "moy=%.1f%% max=%.1f%%" % (100 * mean_u, 100 * max(late)),
    )
    # 4. profit moyen par entreprise > 0 sur chaque fenêtre de 12 mois, tendance haussière ; banques > 0
    pf = [window(rows, y * 12, "profit_firm") for y in years]
    pb = [window(rows, y * 12, "profit_bank") for y in years]
    out["4 profits"] = (
        min(pf) > 0 and pf[-1] >= pf[0] and min(pb) > 0,
        "ent min=%.0f fin=%.0f | banques min=%.0f" % (min(pf), pf[-1], min(pb)),
    )
    # 5. inflation modérée : IPC final 120-150
    cpi = rows[-1]["cpi"]
    out["5 IPC"] = (120 <= cpi <= 150, "IPC=%.1f" % cpi)
    # 7. argent circulant
    month_flow = max(1.0, statistics.mean(r["recurrent"] for r in rows[-12:]))
    fund_max = max(abs(r["cash"]["fonds_amont"]) for r in rows[24:])
    gov_end = abs(rows[-1]["gov"])
    firm_ratio = rows[-1]["firm_cash"] / max(1.0, rows[-1]["payroll_year"])
    out["7 monnaie"] = (
        fund_max < 1.5 * month_flow and gov_end < 3 * month_flow and firm_ratio < 3.0,
        "fonds_max=%.0f etat=%.0f tréso_ent/masse_sal=%.2f" % (fund_max, gov_end, firm_ratio),
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="*", default=list(DEFAULT_SEEDS))
    parser.add_argument("--months", type=int, default=120)
    parser.add_argument("--households", type=int, default=None)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--set",
        nargs="*",
        default=[],
        metavar="PARAM=VALEUR",
        help="surcharge de SimulationConfig, ex. --set initial_person_cash=2000",
    )
    args = parser.parse_args()
    for item in args.set:
        name, value = item.split("=", 1)
        OVERRIDES[name] = float(value) if "." in value or "e" in value else int(value)

    all_rows = {}
    for seed in args.seeds:
        rows = run_seed(seed, args.months, args.households)
        all_rows[seed] = rows
        if not args.quiet:
            yearly_report(seed, rows)

    print("\n=== CRITÈRES (par seed) ===")
    verdicts = {seed: criteria(rows) for seed, rows in all_rows.items()}
    for name in next(iter(verdicts.values())):
        for seed in args.seeds:
            ok, text = verdicts[seed][name]
            print(f"{'OK ' if ok else 'KO '} {name:<12} seed={seed:<4} {text}")
    finals = [window(rows, args.months, "recurrent") for rows in all_rows.values()]
    ratio = max(finals) / max(1.0, min(finals))
    print(f"{'OK ' if ratio <= 2.0 else 'KO '} 6 écart PIB max/min = {ratio:.2f}")
    passed = sum(ok for v in verdicts.values() for ok, _ in v.values())
    total = sum(len(v) for v in verdicts.values())
    print(f"\nCritères tenus : {passed}/{total} (+ stabilité PIB {'OK' if ratio <= 2 else 'KO'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
