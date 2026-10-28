"""Evaluation longue multi-graines : python evaluate.py MOIS [graines...] [--set parametre=valeur ...]

Tableau par graine (inflation, chomage, PIB, salaire reel), par secteur (part du CA, marge,
entreprises actives, plus longue serie de pertes), monopoles, entrees concurrentielles,
chutes du PIB et metiers."""
import sys, math, statistics
from multiprocessing import Pool
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from economy.models import SimulationConfig
from economy.simulation import EconomySimulation, SECTORS

KEYS = [s for s, _ in SECTORS if s != "services"]


def top_share(sim, sector, exclude=None):
    firms = [c for c in sim.companies if c.active and c.sector == sector and c.id != exclude]
    rv = sum(c.previous_revenue for c in firms)
    return (max((c.previous_revenue for c in firms), default=0) / rv) if rv > 0 else (1.0 if firms else 0.0), len(firms)


def run(args):
    seed, months, overrides = args
    sim = EconomySimulation(SimulationConfig(seed=seed, **overrides))
    rec = []
    mono = {s: 0 for s in KEYS}
    entry_events = []
    seen = set()
    for _ in range(months):
        rec.append(sim.step())
        for s in KEYS:
            top, n = top_share(sim, s)
            if n == 1 or top >= 0.6:
                mono[s] += 1
        for c in sim.companies:
            if c.id.startswith("startup") and c.id not in seen:
                seen.add(c.id)
                top, n = top_share(sim, c.sector, exclude=c.id)
                entry_events.append((len(rec), c.sector, round(top, 2), n, c.id))
    out = {"seed": seed}
    out["nan"] = any(not math.isfinite(v) for r in rec for v in (r.nominal_gdp, r.average_wage, r.unemployment, r.consumer_price_index, r.total_profit))
    win = rec[-300:]  # fenêtre longue : les marges oscillent avec le cycle
    tot = sum(sum(r.sector_revenue.get(s, 0) for s in KEYS) for r in win)
    sec = {}
    for s in KEYS:
        rev = sum(r.sector_revenue.get(s, 0) for r in win)
        prof = sum(r.sector_profit.get(s, 0) for r in win)
        act = sum(1 for c in sim.companies if c.active and c.sector == s)
        best = cur = 0
        for r in rec:
            if r.sector_profit.get(s, 0) < 0 and r.sector_revenue.get(s, 0) > 0:
                cur += 1; best = max(best, cur)
            else:
                cur = 0
        sec[s] = dict(share=100 * rev / max(tot, 1), margin=100 * prof / max(rev, 1), active=act, loss_streak=best)
    out["sec"] = sec
    out["infl_avg"] = ((rec[-1].consumer_price_index / 100) ** (1 / (len(rec) / 12)) - 1) * 100
    out["unemp_mean"] = 100 * statistics.mean(r.unemployment for r in rec)
    out["unemp_max"] = 100 * max(r.unemployment for r in (rec[36:] or rec))
    out["gdp_ratio"] = rec[-1].real_gdp / max(r.real_gdp for r in rec)
    ma = [statistics.mean(r.real_gdp for r in rec[max(0, i - 11):i + 1]) for i in range(len(rec))]
    peak = 0.0; episodes = []; low = None
    for i, v in enumerate(ma):
        if v > peak: peak = v
        if v < 0.75 * peak and low is None: low = [i, peak, v]
        if low is not None:
            low[2] = min(low[2], v)
            if v >= 0.9 * low[1]:
                episodes.append((low[0], round(low[2] / low[1], 2), i - low[0])); low = None
    out["crashes"] = episodes + ([(low[0], round(low[2] / low[1], 2), None)] if low else [])
    rw = [statistics.mean(r.real_average_wage for r in rec[i:i + 12]) for i in range(0, len(rec) - 11, 12)]
    out["real_wage"] = (round(rw[0]), round(rw[len(rw) // 2]), round(rw[-1]))
    out["re_props"] = [c.property_inventory for c in sim.companies if c.active and c.sector == "real_estate"]
    out["firms"] = sum(1 for c in sim.companies if c.active)
    out["homeless"] = sum(not p.owns_home and p.rental_owner_id is None for p in sim.people)
    out["mono_months"] = {k: v for k, v in mono.items() if v}
    cmap = {c.id: c for c in sim.companies}
    out["entry_events"] = len(entry_events)
    out["entry_after_mono"] = sum(1 for e in entry_events if e[2] >= 0.6 or e[3] <= 1)
    out["entry_survived"] = sum(1 for e in entry_events if cmap[e[4]].active and cmap[e[4]].age_months >= 24)
    out["entry_failed"] = sum(1 for e in entry_events if cmap[e[4]].exit_reason == "bankrupt")
    out["entry_acquired"] = sum(1 for e in entry_events if cmap[e[4]].exit_reason == "acquired")
    out["entry_by_sector"] = {}
    for e in entry_events:
        out["entry_by_sector"][e[1]] = out["entry_by_sector"].get(e[1], 0) + 1
    out["owners"] = sum(p.owns_home for p in sim.people)
    out["invest_homes"] = sum(p.investment_homes for p in sim.people)
    thr = sim.config.startup_threshold
    rich = [p for p in sim.people if p.cash + p.investment_homes * sim.config.housing_price >= thr]
    re_founders = {c.founder_id for c in sim.companies if c.id.startswith("startup") and c.sector == "real_estate"}
    any_founders = {c.founder_id for c in sim.companies if c.id.startswith("startup")}
    out["rich"] = (len(rich), sum(p.id in re_founders for p in rich), sum(p.id in any_founders for p in rich), sum(p.founded_company for p in sim.people))
    out["spirit_rich"] = (sum(p.entrepreneurial_spirit >= sim.config.entrepreneurial_trait_threshold for p in rich),)
    out["pop"] = len(sim.people)
    out["trades"] = getattr(sim, "trade_report", lambda: None)()
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    overrides = {}
    if "--set" in args:
        i = args.index("--set")
        for kv in args[i + 1:]:
            k, v = kv.split("=")
            overrides[k] = float(v) if "." in v else int(v)
        args = args[:i]
    months = int(args[0])
    seeds = [int(a) for a in args[1:]] or [7, 11, 23, 42, 101]
    with Pool(min(5, len(seeds))) as p:
        results = p.map(run, [(s, months, overrides) for s in seeds])
    print(f"{months} mois")
    print(f"{'seed':>4} {'nan':>3} {'inf%':>5} {'ch moy':>6} {'ch max':>6} {'PIB f/pic':>9} {'salaire reel':>20} {'firmes':>6} {'sans log':>8} {'proprio':>7}")
    for o in results:
        print(f"{o['seed']:4d} {str(o['nan'])[0]:>3} {o['infl_avg']:5.2f} {o['unemp_mean']:6.1f} {o['unemp_max']:6.1f} {o['gdp_ratio']:9.2f} {str(o['real_wage']):>20} {o['firms']:6d} {o['homeless']:8d} {o['owners']:7d}")
    print("\nsecteur : part du CA % / marge % / actives / plus longue serie de pertes (mois)")
    for s in KEYS:
        row = []
        for o in results:
            d = o["sec"][s]
            row.append(f"{d['share']:4.1f}/{d['margin']:5.1f}/{d['active']}/{d['loss_streak']:3d}")
        print(f"{s:12s} " + "  ".join(row))
    print("biens RE par firme :", [o["re_props"] for o in results])
    print("chutes du PIB reel (<75 % du pic): (mois debut, creux/pic, mois pour retrouver 90 % ; None = en cours) :", [o["crashes"] for o in results])
    print("mois en monopole/forte dominance :", [o["mono_months"] for o in results])
    print("entrees (total, en reaction a monopole/dominant, survivantes>=24m, faillites, rachetees) :", [(o["entry_events"], o["entry_after_mono"], o["entry_survived"], o["entry_failed"], o["entry_acquired"]) for o in results])
    print("entrees par secteur :", [o["entry_by_sector"] for o in results])
    print("logements-patrimoine des menages :", [o["invest_homes"] for o in results], " | menages riches (n, fondateurs immo, fondateurs tous secteurs, ayant deja fonde) :", [o["rich"] for o in results], "avec esprit entrepreneurial :", [o["spirit_rich"][0] for o in results])
    if results[0]["trades"]:
        for o in results:
            print("metiers", o["seed"], o["trades"])
