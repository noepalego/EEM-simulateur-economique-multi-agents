"""Audit reproductible du moteur, séparant mesure, profilage et mémoire.

    python benchmark_simulation.py profile --output results/audit/profil
    python benchmark_simulation.py profile --start-month 480 --months 60 --output results/audit/tardif
    python benchmark_simulation.py compare --baseline results/audit/avant --output results/audit/mesures

Les comparaisons tournent séquentiellement dans des processus neufs, en alternant
l'ordre avant/après. Ne pas lancer d'autres campagnes pendant les chronométrages.
Le profilage et tracemalloc servent à expliquer les coûts, jamais à chiffrer un gain.
Les contrôles d'identité restent séparés (performance_probe.py, identity_check.py).
"""
from __future__ import annotations

import argparse
import cProfile
import functools
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import statistics
import subprocess
import sys
import time

PHASES = ("_begin_month", "_credit_and_labor", "_consumer_markets", "_public_sector",
          "_investment_and_housing", "_close_books", "_industry_dynamics", "_record_month")


def source_digest(root):
    digest = hashlib.sha256()
    for path in sorted((root / "economy").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def dimensions(sim):
    return {
        "month": sim.month, "people": len(sim.people), "companies": len(sim.companies),
        "active_companies": sum(c.active for c in sim.companies),
        "share_positions": sum(len(h.shares) for h in [*sim.people, *sim.companies]),
        "household_history_rows": sum(len(p.financial_history) for p in sim.people),
        "company_history_rows": sum(len(c.financial_history) for c in sim.companies),
        "customer_positions": sum(len(c.customer_revenue) for c in sim.companies),
    }


def summary(values):
    median = statistics.median(values)
    return {"median": median, "min": min(values), "max": max(values),
            "mad": statistics.median(abs(v - median) for v in values)}


def write_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def worker(args):
    root = args.source.resolve()
    fingerprint = source_digest(root)
    # La source choisie doit précéder le répertoire contenant cet outil.
    sys.path.insert(0, str(root))
    from economy.models import SimulationConfig
    from economy.simulation import EconomySimulation
    config = dict(seed=args.seed, population_size=args.population, warmup_months=args.warmup)
    config.update(json.loads(args.settings))
    phases = {name: [] for name in PHASES}
    stage = "initialization"
    if args.mode == "profile":
        for name in PHASES:
            original = getattr(EconomySimulation, name)
            @functools.wraps(original)
            def timed(self, *a, _original=original, _name=name, **kw):
                start = time.perf_counter()
                try:
                    return _original(self, *a, **kw)
                finally:
                    phases[_name].append((stage, time.perf_counter() - start))
            setattr(EconomySimulation, name, timed)
    if args.mode == "memory":
        import tracemalloc
        tracemalloc.start()
    profiler = cProfile.Profile() if args.mode == "profile" else None
    if profiler:
        profiler.enable()
    wall, cpu = time.perf_counter(), time.process_time()
    sim = EconomySimulation(SimulationConfig(**config))
    initialization = {"wall": time.perf_counter() - wall, "cpu": time.process_time() - cpu}
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(args.output) + "_init.prof")
        profiler.clear()
    stage = "advance"
    for _ in range(args.start_month):
        sim.step()
    before = dimensions(sim)
    if args.mode == "memory":
        memory_before = tracemalloc.take_snapshot()
        tracemalloc.reset_peak()
    stage = "measured"
    if profiler:
        profiler.enable()
    rows = []
    wall, cpu = time.perf_counter(), time.process_time()
    for _ in range(args.months):
        tick = time.perf_counter()
        sim.step()
        rows.append(time.perf_counter() - tick)
    measured = {"wall": time.perf_counter() - wall, "cpu": time.process_time() - cpu}
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(args.output) + "_steps.prof")
        with args.output.with_suffix(".txt").open("w", encoding="utf-8") as stream:
            stats = pstats.Stats(profiler, stream=stream).strip_dirs()
            stats.sort_stats("cumulative").print_stats(80)
            stats.sort_stats("tottime").print_stats(60)
    result = {
        "mode": args.mode, "source": str(root), "source_digest": fingerprint,
        "python": sys.version, "platform": platform.platform(), "config": config,
        "hashseed": os.environ.get("PYTHONHASHSEED"), "gc_enabled": gc.isenabled(),
        "start_month": args.start_month, "months": args.months,
        "initialization": initialization, "measured": measured,
        "month_seconds": rows, "month_summary": summary(rows),
        "before": before, "after": dimensions(sim),
    }
    if profiler:
        result["phases"] = {
            stage_name: {name: sum(t for s, t in values if s == stage_name)
                         for name, values in phases.items()}
            for stage_name in ("initialization", "advance", "measured")
        }
    if args.mode == "memory":
        current, peak = tracemalloc.get_traced_memory()
        memory_after = tracemalloc.take_snapshot()
        result["memory"] = {"current_bytes": current, "peak_bytes": peak,
            "growth": [{"location": str(s.traceback), "bytes": s.size_diff, "count": s.count_diff}
                       for s in memory_after.compare_to(memory_before, "lineno")[:40]]}
        tracemalloc.stop()
    if source_digest(root) != fingerprint:
        raise RuntimeError("Les sources ont changé pendant la mesure")
    write_result(args.output, result)
    print(f"{args.mode}: init={initialization['wall']:.3f}s, "
          f"{args.months} mois={measured['wall']:.3f}s", flush=True)
    return result


def compare(args):
    if args.baseline is None:
        raise ValueError("compare exige --baseline")
    paths = {"before": args.baseline.resolve(), "after": args.source.resolve()}
    fingerprints = {name: source_digest(path) for name, path in paths.items()}
    result = {"runs": [], "source_digests": fingerprints, "note": "Exécutions séquentielles, ordre alterné"}
    for repeat in range(args.repeats):
        order = ("before", "after") if repeat % 2 == 0 else ("after", "before")
        for name in order:
            output = args.output.parent / f"{args.output.name}_{repeat}_{name}"
            command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "measure",
                       "--source", str(paths[name]), "--population", str(args.population),
                       "--seed", str(args.seed), "--warmup", str(args.warmup),
                       "--start-month", str(args.start_month), "--months", str(args.months),
                       "--settings", args.settings, "--output", str(output)]
            subprocess.run(command, env={**os.environ, "PYTHONHASHSEED": "0"}, check=True)
            row = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
            if row["source_digest"] != fingerprints[name]:
                raise RuntimeError("Les sources ont changé pendant la comparaison")
            result["runs"].append({"version": name, "repeat": repeat, **row})
            write_result(args.output, result)
    result["summary"] = {}
    for phase in ("initialization", "measured"):
        before = [r[phase]["wall"] for r in result["runs"] if r["version"] == "before"]
        after = [r[phase]["wall"] for r in result["runs"] if r["version"] == "after"]
        result["summary"][phase] = {"before": summary(before), "after": summary(after),
            "paired_time_reduction": [1.0 - a / b for a, b in zip(after, before)]}
    write_result(args.output, result)
    print(json.dumps(result["summary"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("measure", "profile", "memory", "compare"))
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--population", type=int, default=240)
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--start-month", type=int, default=0)
    parser.add_argument("--months", type=int, default=60)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--settings", default="{}", help="Paramètres supplémentaires en JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.months < 1 or args.repeats < 1 or args.start_month < 0:
        parser.error("months/repeats doivent être positifs ; start-month doit être positif ou nul")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv],
                                        env={**os.environ, "PYTHONHASHSEED": "0"}))
    compare(args) if args.mode == "compare" else worker(args)


if __name__ == "__main__":
    main()
