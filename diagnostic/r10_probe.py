"""R10 : réponse de la consommation à un choc transitoire de revenu, épargne volontaire et chômage.

Usage : python diagnostic/r10_probe.py sortie.json [graines...] [--jobs N]
Configuration unique (budget R10 : revenu attendu et précaution), avec et sans le choc
« depenses » d'``experiments.py`` (dépenses publiques −50 % pendant 24 mois) au mois observé 240.
La comparaison à l'ancien budget (« A3 », retiré en novembre 2026) a disparu avec lui.

Mesures (écarts au même scénario sans choc, même graine ; mêmes définitions que
``realism_probe.py`` : consommation TTC des flux du mois, loyers et technologie compris, revenu
disponible = revenus − impôts) :
- ΔC / ΔY cumulés sur 6 et 12 mois ;
- corrélation, sur les 240 premiers mois observés sans choc, entre le chômage et le taux
  d'épargne volontaire (revenu disponible − consommation − achats refusés, rapporté au revenu
  disponible).
"""
import json
import os
import statistics as st
import sys
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from economy.models import Shock, SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402
from experiments import SHOCKS  # noqa: E402

START = 240
MONTHS = START + 24


class FlowSimulation(EconomySimulation):
    """Garde les flux du mois (lecture seule, comme ``realism_probe.ProbeSimulation``)."""

    def _record_month(self, f):
        self._flows = f
        return super()._record_month(f)


def run(args):
    seed, mode, shocked = args
    sim = FlowSimulation(SimulationConfig(seed=seed))
    if shocked:
        size, duration = SHOCKS["depenses"][:2]
        sim.shocks.append(Shock("depenses", START, duration, size))
    rows = []
    for _ in range(MONTHS):
        r = sim.step()
        f = sim._flows
        vat = 1.0 + r.vat_rate
        consumption = ((f.food_revenue + f.goods_revenue + f.household_services) * vat
                       + sim.resources.household_technology_revenue + f.rental_revenue)
        income = r.household_income - sum(p.taxes_paid for p in sim.people)
        refused = (r.unmet_food_demand * r.food_price + r.unmet_goods_demand * r.goods_price) * vat
        rows.append((income, consumption, r.unemployment, refused))
    return seed, mode, shocked, rows


def correlation(xs, ys):
    mx, my = st.fmean(xs), st.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx > 0 and sy > 0 else 0.0


def report(results, seeds):
    for mode in ("R10",):
        print(f"== {mode}")
        ratios = {6: [], 12: []}
        smoother = {6: 0, 12: 0}
        corr = []
        corr_observed = []
        saving = []
        for seed in seeds:
            base, shock = results[(seed, mode, False)], results[(seed, mode, True)]
            for h in (6, 12):
                dy = sum(shock[START + k][0] - base[START + k][0] for k in range(h))
                dc = sum(shock[START + k][1] - base[START + k][1] for k in range(h))
                ratios[h].append(dc / dy if dy else float("nan"))
                smoother[h] += abs(dc) < abs(dy)
            window = base[:START]
            rates = [(y - c - refused) / y for y, c, _, refused in window if y > 0]
            observed = [(y - c) / y for y, c, _, _ in window if y > 0]
            unemployment = [u for y, _, u, _ in window if y > 0]
            corr.append(correlation(unemployment, rates))
            corr_observed.append(correlation(unemployment, observed))
            # taux d'épargne observé sur la fenêtre, défini comme dans realism_probe (sommes)
            saving.append(100 * (1 - sum(c for _, c, _, _ in window) / sum(y for y, _, _, _ in window)))
        for h in (6, 12):
            print(f"  ΔC/ΔY à {h:2d} mois : médiane {st.median(ratios[h]):+.2f} "
                  f"[{min(ratios[h]):+.2f} ; {max(ratios[h]):+.2f}], |ΔC| < |ΔY| sur {smoother[h]}/{len(seeds)}")
        print(f"  corrélation chômage / épargne volontaire : médiane {st.median(corr):+.2f} "
              f"[{min(corr):+.2f} ; {max(corr):+.2f}], positive sur {sum(c > 0 for c in corr)}/{len(seeds)}")
        print(f"  corrélation chômage / épargne observée : médiane {st.median(corr_observed):+.2f} "
              f"[{min(corr_observed):+.2f} ; {max(corr_observed):+.2f}], "
              f"positive sur {sum(c > 0 for c in corr_observed)}/{len(seeds)}")
        print(f"  taux d'épargne observé, 240 premiers mois (% du revenu disponible) : médiane {st.median(saving):.1f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    jobs_count = 8
    if "--jobs" in args:  # nombre de processus (sans effet sur les résultats)
        index = args.index("--jobs")
        jobs_count = int(args[index + 1])
        del args[index:index + 2]
    out = args[0]
    seeds = [int(s) for s in args[1:]] or list(range(1001, 1011))
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            saved = json.load(fh)
        results = {(s, m, bool(k)): rows for s, m, k, rows in saved}
    else:
        jobs = [(seed, mode, shocked) for seed in seeds for mode in ("R10",) for shocked in (False, True)]
        with Pool(jobs_count, maxtasksperchild=1) as pool:
            done = pool.map(run, jobs)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(done, fh)
        results = {(s, m, k): rows for s, m, k, rows in done}
    report(results, seeds)
