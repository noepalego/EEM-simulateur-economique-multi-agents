"""Processus auxiliaire : calcule l'état après la chauffe d'une configuration.

Lancé par ``economy.warmup_cache.warm_many`` (``python -m economy.warmup_worker``) : lit une
``SimulationConfig`` sérialisée sur l'entrée standard, écrit l'état après la chauffe, sérialisé,
sur la sortie standard. Même code, mêmes réglages : même état, bit pour bit, que
``EconomySimulation(config)`` dans le processus principal. Rien d'autre n'est écrit sur la
sortie standard (les messages éventuels vont sur la sortie d'erreur).
"""

from __future__ import annotations

import pickle
import sys


def main() -> int:
    sortie = sys.stdout.buffer
    sys.stdout = sys.stderr  # un ``print`` égaré ne doit pas corrompre l'état transmis
    config = pickle.loads(sys.stdin.buffer.read())
    from economy.simulation import EconomySimulation

    sortie.write(pickle.dumps(EconomySimulation(config), protocol=pickle.HIGHEST_PROTOCOL))
    sortie.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
