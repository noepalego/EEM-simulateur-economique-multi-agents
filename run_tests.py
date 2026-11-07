"""Lance la suite de tests en parallèle, une classe de test par tâche.

    python run_tests.py              # toute la suite sur tous les cœurs (jusqu'à 12 processus)
    python run_tests.py -j 4         # 4 processus
    python run_tests.py LaborAndB2BDemandTests BankDecisionTests   # classes précises

Équivalent de ``python -m unittest discover -s tests`` (mêmes tests, mêmes assertions),
plus rapide : les classes sont indépendantes (chaque simulation a sa graine et ses
générateurs). Code de sortie 0 si tout passe.
"""

from __future__ import annotations

import io
import os
import sys
import time
import unittest
from multiprocessing import Pool


def _classes(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _classes(item)
        else:
            yield type(item)


def _discover() -> dict[str, str]:
    """Nom complet de chaque classe de test -> ses tests."""
    suite = unittest.defaultTestLoader.discover("tests")
    names: dict[str, str] = {}
    for cls in _classes(suite):
        names[f"{cls.__module__}.{cls.__qualname__}"] = cls.__name__
    return names


def _run_class(name: str) -> tuple[str, int, list[str], float]:
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    problems = [f"{test}\n{trace}" for test, trace in result.failures + result.errors]
    problems.extend(f"Réussite inattendue : {test}" for test in result.unexpectedSuccesses)
    for test, _ in result.expectedFailures:
        print(f"ECHEC ATTENDU {test}", flush=True)
    return name, result.testsRun, problems, time.perf_counter() - started


def main(argv: list[str]) -> int:
    workers = min(12, os.cpu_count() or 1)
    if "-j" in argv:
        index = argv.index("-j")
        workers = int(argv[index + 1])
        argv = argv[:index] + argv[index + 2:]
    classes = _discover()
    if argv:
        classes = {full: short for full, short in classes.items() if short in argv}
    # les classes les plus longues d'abord (celles qui simulent longtemps)
    ordered = sorted(classes, key=lambda full: full.endswith(("LongRunTests", "WarmupTests")), reverse=True)
    started = time.perf_counter()
    total, problems = 0, []
    with Pool(max(1, min(workers, len(ordered)))) as pool:
        for name, ran, found, seconds in pool.imap_unordered(_run_class, ordered):
            total += ran
            problems.extend(found)
            print(f"{'ok ' if not found else 'ECHEC'} {name.split('.')[-1]:45s} {ran:3d} tests {seconds:6.1f} s", flush=True)
    print(f"\n{total} tests en {time.perf_counter() - started:.0f} s sur {workers} processus")
    for problem in problems:
        print("\n" + "=" * 70 + "\n" + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
