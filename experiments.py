"""Laboratoire de chocs : python experiments.py [mois] [--seeds 1001 1002 ...] [--start 240]
    [--jobs 1..8] [--json sortie.json] [--set parametre=valeur ...]

Chaque choc est appliqué à une graine puis comparé à la même graine sans choc
(mêmes tirages, mêmes décisions jusqu'au choc) : l'écart mesure la réponse du
modèle, pas le hasard. Les graines par défaut (1001 à 1005) n'ont servi à aucun
réglage. Les réponses attendues sont écrites ci-dessous AVANT de regarder les
résultats ; le script indique si le signe moyen observé les respecte. Un écart
n'est pas corrigé en retouchant les paramètres : c'est une information sur le
modèle.
"""
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from economy.models import Shock, SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402

# choc : (taille, durée en mois, description, signes attendus à 12-24 mois)
# signes : +1 hausse, -1 baisse, 0 pas d'attente ; variables : PIB réel, chômage,
# inflation sur 12 mois, taux directeur
SHOCKS = {
    "taux": (0.03, 24, "taux directeur +3 points pendant 2 ans",
             {"pib": -1, "chomage": +1, "inflation": -1, "taux": +1}),
    "energie": (0.30, 12, "30 % de la capacité des centrales perdue pendant 1 an",
                {"pib": -1, "chomage": +1, "inflation": +1, "taux": 0}),
    "tva": (0.05, 24, "TVA +5 points pendant 2 ans",
            {"pib": -1, "chomage": 0, "inflation": +1, "taux": 0}),
    "depenses": (0.50, 24, "dépenses publiques discrétionnaires -50 % pendant 2 ans",
                 {"pib": -1, "chomage": +1, "inflation": -1, "taux": 0}),
    "credit": (0.06, 24, "exigence de fonds propres des banques +6 points pendant 2 ans",
               {"pib": -1, "chomage": +1, "inflation": 0, "taux": 0}),
}
HORIZONS = (6, 12, 24, 36)


def _annual_inflation(records, month):
    """Inflation sur 12 mois au mois ``month`` (1 = premier mois)."""
    now = records[month - 1].consumer_price_index
    before = records[month - 13].consumer_price_index if month > 12 else 100.0
    return now / before - 1.0


def run(args):
    seed, months, start, kind, overrides = args
    sim = EconomySimulation(SimulationConfig(seed=seed, **overrides))
    if kind is not None:
        size, duration, _, _ = SHOCKS[kind]
        sim.shocks.append(Shock(kind, start, duration, size))
    records = [sim.step() for _ in range(months)]
    return seed, kind, [
        (r.real_gdp, r.unemployment, _annual_inflation(records, i + 1), 12.0 * r.policy_rate)
        for i, r in enumerate(records)
    ]


def main():
    args = sys.argv[1:]
    overrides = {}
    workers, json_path = 8, None
    for flag in ("--jobs", "--json"):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1]
            del args[i:i + 2]
            if flag == "--jobs":
                workers = int(value)
                if not 1 <= workers <= 8:
                    raise ValueError("--jobs doit être compris entre 1 et 8")
            else:
                json_path = value
    if "--set" in args:  # même syntaxe qu'evaluate.py : --set parametre=valeur ...
        i = args.index("--set")
        for kv in args[i + 1:]:
            k, v = kv.split("=")
            try:
                overrides[k] = float(v) if "." in v else int(v)
            except ValueError:
                overrides[k] = v
        args = args[:i]
    months = int(args[0]) if args and args[0].isdigit() else 300
    seeds = [1001, 1002, 1003, 1004, 1005]
    start = 240
    if "--seeds" in args:
        i = args.index("--seeds")
        seeds = [int(s) for s in args[i + 1:] if s.isdigit()]
    if "--start" in args:
        start = int(args[args.index("--start") + 1])
    months = max(months, start + max(HORIZONS) + 1)
    jobs = [(seed, months, start, kind, overrides) for seed in seeds for kind in (None, *SHOCKS)]
    with Pool(min(workers, len(jobs))) as pool:
        results = pool.map(run, jobs)
    if json_path:
        # Conserver les trajectoires appariées pour auditer les signes par graine.
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"months": months, "start": start, "seeds": seeds,
                       "overrides": overrides, "results": results}, fh)
    baseline = {seed: series for seed, kind, series in results if kind is None}
    print(f"{len(seeds)} graines {seeds}, choc au mois {start}, écarts au scénario sans choc (moyenne [min ; max])")
    for kind, (size, duration, label, expected) in SHOCKS.items():
        print(f"\n== {kind} : {label}")
        for horizon in HORIZONS:
            month = start + horizon
            rows = {"pib": [], "chomage": [], "inflation": [], "taux": []}
            for seed, k, series in results:
                if k != kind:
                    continue
                base, shocked = baseline[seed][month - 1], series[month - 1]
                rows["pib"].append(100.0 * (shocked[0] / max(base[0], 1e-9) - 1.0))
                rows["chomage"].append(100.0 * (shocked[1] - base[1]))
                rows["inflation"].append(100.0 * (shocked[2] - base[2]))
                rows["taux"].append(100.0 * (shocked[3] - base[3]))
            cells = []
            for name, values in rows.items():
                mean = statistics.mean(values)
                sign = expected[name]
                verdict = "" if sign == 0 or horizon < 12 else (" ok" if mean * sign > 0 else " ≠")
                cells.append(f"{name} {mean:+.2f} [{min(values):+.2f} ; {max(values):+.2f}]{verdict}")
            print(f"  +{horizon:2d} mois : " + " | ".join(cells))
    print("\nPIB réel en % ; chômage, inflation et taux en points. « ok » : signe attendu "
          "respecté à 12 mois et plus ; « ≠ » : signe contraire à l'attente.")


if __name__ == "__main__":
    main()
