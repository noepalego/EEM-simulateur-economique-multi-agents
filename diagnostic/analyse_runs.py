"""Analyse fine des séries mensuelles écrites par realism_probe.py --json.

Usage : python analyse_runs.py dossier label [graine_detail]
"""
import json
import math
import os
import statistics
import sys
from collections import Counter

SECTORS = ["food", "goods", "technology", "real_estate", "materials", "energy", "bank", "services"]


def load(folder, label):
    runs = []
    for name in sorted(os.listdir(folder)):
        if name.startswith(label + "_") and name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                runs.append(json.load(fh))
    return runs


def ma(xs, n=12):
    return [statistics.mean(xs[max(0, i - n + 1):i + 1]) for i in range(len(xs))]


def corr(a, b):
    ma_, mb = statistics.mean(a), statistics.mean(b)
    cov = sum((x - ma_) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma_) ** 2 for x in a)); vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return cov / (va * vb) if va and vb else float("nan")


def slope(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    vx = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / vx if vx else float("nan")


def household_behaviour(run):
    rows = run["rows"]; w = run["warmup"]; obs = rows[w:]
    yd = [r["y"] - r["tax"] for r in obs]; c = [r["c"] for r in obs]
    # sensibilité de la consommation au revenu (annuelle, en log) et au taux des dépôts
    ya = [sum(yd[i:i + 12]) for i in range(0, len(yd) - 11, 12)]
    ca = [sum(c[i:i + 12]) for i in range(0, len(c) - 11, 12)]
    dly = [math.log(ya[i] / ya[i - 1]) for i in range(1, len(ya)) if ya[i] > 0 and ya[i - 1] > 0]
    dlc = [math.log(ca[i] / ca[i - 1]) for i in range(1, len(ca)) if ca[i] > 0 and ca[i - 1] > 0]
    n = min(len(dly), len(dlc))
    elasticity = slope(dly[:n], dlc[:n])
    # MPC mensuelle : variation de la consommation / variation du revenu disponible
    dy = [yd[i] - yd[i - 1] for i in range(1, len(yd))]
    dc = [c[i] - c[i - 1] for i in range(1, len(c))]
    mpc_month = slope(dy, dc)
    sav = [1 - c[i] / yd[i] if yd[i] > 0 else 0 for i in range(len(c))]
    dep = [r["dep"] for r in obs]
    sav_a = [statistics.mean(sav[i:i + 12]) for i in range(0, len(sav) - 11, 12)]
    dep_a = [statistics.mean(dep[i:i + 12]) for i in range(0, len(dep) - 11, 12)]
    u_a = [statistics.mean(r["u"] for r in obs[i:i + 12]) for i in range(0, len(obs) - 11, 12)]
    return {
        "elast_C_Y_annual": elasticity,
        "mpc_month": mpc_month,
        "corr_saving_deposit_rate": corr(sav_a, dep_a),
        "corr_saving_unemployment": corr(sav_a, u_a),
        "saving_sd": 100 * statistics.pstdev(sav_a),
    }


def timeline(run, step=24, upto=None):
    rows = run["rows"]; w = run["warmup"]
    g = ma([r["rgdp"] for r in rows])
    lines = []
    for i in range(11, len(rows) if upto is None else min(upto, len(rows)), step):
        r = rows[i]
        sec = r["sec"]
        lines.append(
            f"m{i + 1 - w:+5d} PIB {g[i] / g[11]:5.2f} ch {100 * r['u']:5.1f} "
            f"IPC {r['cpi']:9.1f} tx {100 * r['pol']:4.1f} "
            f"alim {sec['food']['n']}/{sec['food']['n_emp']}/{sec['food']['emp']:3d} "
            f"biens {sec['goods']['n']}/{sec['goods']['n_emp']}/{sec['goods']['emp']:3d} "
            f"serv {sec['services']['emp']:3d} banq {r['nb']} capcr {r['ccap'] / max(1, r['ngdp']):5.2f} "
            f"nonserv {100 * r['unmet_food']:4.0f}/{100 * r['unmet_goods']:3.0f} "
            f"p/cvm {r['pr_food']['avc']:4.2f}/{r['pr_goods']['avc']:4.2f} "
            f"p/cm {r['pr_food']['mc']:4.2f}/{r['pr_goods']['mc']:4.2f} "
            f"p/cc {r['pr_food']['full']:4.2f}/{r['pr_goods']['full']:4.2f} "
            f"mach {r['pr_food']['machine']:4.2f}/{r['pr_goods']['machine']:4.2f} "
            f"epar {100 * (1 - r['c'] / max(1, r['y'] - r['tax'])):5.1f} "
            f"E {r['e_idx']:5.2f} T {r['t_idx']:5.2f} entr {r['startups']} fail {r['bankr']}"
        )
    return lines


def dynamics(run):
    rows = run["rows"]; w = run["warmup"]
    g = ma([r["rgdp"] for r in rows])
    peak_w = max(range(w), key=lambda i: g[i])
    # première chute sous 75 % du pic courant, chauffe comprise
    peak, first = 0.0, None
    for i, v in enumerate(g):
        peak = max(peak, v)
        if v < 0.75 * peak and first is None:
            first = i
    return {"warmup_peak_month": peak_w + 1 - w, "warmup_peak_ratio": g[peak_w] / g[11],
            "first_crash_month": None if first is None else first + 1 - w,
            "gdp_end_warmup_over_start": g[w - 1] / g[11]}


def exits(run):
    w = run["warmup"]
    ex = [e for e in run["exits"] if e[0] > w]
    by = Counter((e[1], e[3]) for e in ex)
    young = sum(1 for e in ex if e[2] < 24)
    return {"n": len(ex), "young_share": young / max(1, len(ex)), "by": dict(by)}


if __name__ == "__main__":
    folder, label = sys.argv[1], sys.argv[2]
    detail = int(sys.argv[3]) if len(sys.argv) > 3 else None
    runs = load(folder, label)
    print(f"== {label} : {len(runs)} graines")
    for run in runs:
        hb = household_behaviour(run); dy = dynamics(run); ex = exits(run)
        print(run["seed"], {k: round(v, 3) if isinstance(v, float) else v for k, v in {**hb, **dy}.items()},
              "sorties", ex["n"], f"jeunes {100 * ex['young_share']:.0f}%")
    for run in runs:
        if detail is None or run["seed"] == detail:
            if detail is not None:
                print(f"\n-- chronologie {run['seed']}")
                print("\n".join(timeline(run)))
                print("sorties par (secteur, motif) :", exits(run)["by"])
