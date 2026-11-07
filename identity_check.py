"""Garde-fou d'identité : une optimisation ou une refactorisation ne doit changer aucun résultat.

    python identity_check.py             # compare à la référence, mois par mois
    python identity_check.py --record    # réenregistre la référence (jamais pour faire passer
                                         # un nettoyage : seulement après un changement voulu
                                         # du modèle, consigné dans la documentation)

Chaque cas simule 150 mois après la chauffe et résume la trajectoire par un hachage de
``repr`` de chaque enregistrement mensuel plus de l'état final (trésoreries, dettes,
fonds propres, comptes de prêts). La moindre différence d'arrondi change le hachage : le
système est chaotique, une divergence d'un bit se voit donc en quelques mois.

Depuis la mission « optimisation » de novembre 2026, le modèle n'a qu'une configuration
(autonomie stable et État institutionnel, ``SimulationConfig()``) : les cas sont cinq graines
ou tailles de cette configuration. Leurs valeurs attendues ont été calculées sur la copie figée
des sources d'avant la mission (``results/optimisation/outils/references_identite.py``) ;
``cible_1001`` est l'ancienne référence ``etat_institutions_stable_1001``. Les 18 références
précédentes sont archivées dans ``results/optimisation/identity_reference_avant.json``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

from economy.models import SimulationConfig
from economy.simulation import EconomySimulation

REFERENCE = Path(__file__).with_name("identity_reference.json")
MONTHS = 150

# graine et réglages ajoutés à la configuration unique
CASES: dict[str, tuple[int, dict]] = {
    "cible_1001": (1001, {}),
    "cible_1002": (1002, {}),
    "cible_1003": (1003, {}),
    "cible_960_1002": (1002, {"population_size": 960}),
    "cible_sans_chauffe_42": (42, {"warmup_months": 0}),
}


def fingerprint(case: str) -> tuple[str, str]:
    seed, overrides = CASES[case]
    simulation = EconomySimulation(SimulationConfig(seed=seed, **overrides))
    digest = hashlib.sha256()
    for _ in range(MONTHS):
        digest.update(repr(simulation.step()).encode())
    state = hashlib.sha256()
    for company in simulation.companies:
        state.update(repr((
            company.id, company.active, company.cash, company.debt, company.bank_equity,
            company.bond_holdings, sorted(company.bank_loan_accounts.items()),
        )).encode())
    for person in simulation.people:
        state.update(repr((person.id, person.cash, person.consumer_debt, person.mortgage_debt)).encode())
    return case, digest.hexdigest()[:16] + ":" + state.hexdigest()[:16]


def main() -> int:
    started = time.perf_counter()
    with Pool(min(len(CASES), 8)) as pool:
        results = dict(pool.map(fingerprint, list(CASES)))
    print(f"{len(CASES)} cas x {MONTHS} mois en {time.perf_counter() - started:.0f} s")
    if "--record" in sys.argv:
        REFERENCE.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        print("référence enregistrée :", REFERENCE.name)
        return 0
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    failures = [case for case in CASES if reference.get(case) != results[case]]
    for case in CASES:
        print(f"{'OK ' if case not in failures else 'DIFF'} {case}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
