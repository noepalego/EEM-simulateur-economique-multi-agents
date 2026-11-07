"""Cache de l'état après la chauffe (optimisation du 6 novembre 2026).

La chauffe (``warmup_months``, 120 mois par défaut) refait les mêmes calculs à chaque création
d'une simulation : c'était l'essentiel du temps d'une réinitialisation dans l'interface. L'état
obtenu ne dépend que de la configuration et du code du moteur ; on le garde donc une fois calculé.

- Clé : ``repr`` de la configuration (tous les paramètres, graine comprise) + empreinte des sources
  du paquet ``economy`` (contenu de chaque fichier ``.py``) + version de Python. Toute modification
  du moteur ou d'un réglage donne une autre clé : un état périmé n'est jamais réutilisé.
- Stockage : en mémoire (processus courant, ``MEMORY_ENTRIES`` états au plus) et sur disque
  (``.cache/warmup/``, ``DISK_ENTRIES`` fichiers au plus, les plus anciens effacés ; effacer ce
  dossier est toujours sûr), au format
  ``pickle`` du processus qui l'a écrit. Un fichier illisible est ignoré et recalculé.
- Chaque appel rend une **copie indépendante** (désérialisée) : deux sessions ne partagent rien.

Une simulation rechargée est identique, bit pour bit, à une simulation neuve (générateurs
aléatoires compris) et continue la même trajectoire (``tests/test_optimisation.py``).
"""

from __future__ import annotations

import hashlib
import os
import pickle
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from economy.models import SimulationConfig

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "warmup"
MEMORY_ENTRIES = 6
DISK_ENTRIES = 12  # environ 6 Mo par état de 240 ménages : 80 Mo au plus

_memory: "OrderedDict[str, bytes]" = OrderedDict()
_lock = threading.Lock()
_source_digest: str | None = None


def source_fingerprint() -> str:
    """Empreinte du code du moteur (fichiers ``economy/*.py``, triés par nom)."""
    global _source_digest
    if _source_digest is None:
        digest = hashlib.sha256()
        package = Path(__file__).resolve().parent
        for path in sorted(package.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        digest.update(sys.version.encode())
        _source_digest = digest.hexdigest()
    return _source_digest


def cache_key(config: SimulationConfig) -> str:
    return hashlib.sha256((repr(config) + source_fingerprint()).encode()).hexdigest()[:32]


def _store_disk(key: str, payload: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = CACHE_DIR / f"{key}.{os.getpid()}.tmp"
        temporary.write_bytes(payload)
        os.replace(temporary, CACHE_DIR / f"{key}.pkl")
        files = sorted(CACHE_DIR.glob("*.pkl"), key=lambda p: p.stat().st_mtime)
        for old in files[:-DISK_ENTRIES]:
            old.unlink(missing_ok=True)
    except OSError:
        pass  # cache facultatif : un disque plein ou en lecture seule ne bloque rien


def _load_disk(key: str) -> bytes | None:
    path = CACHE_DIR / f"{key}.pkl"
    try:
        return path.read_bytes()
    except OSError:
        return None


def warmed_simulation(config: SimulationConfig, use_disk: bool = True):
    """Simulation après la chauffe pour ``config`` : copie d'un état mémorisé, sinon calculée
    (puis mémorisée). Équivaut exactement à ``EconomySimulation(config)``."""
    from economy.simulation import EconomySimulation

    key = cache_key(config)
    with _lock:
        payload = _memory.get(key)
        if payload is not None:
            _memory.move_to_end(key)
    if payload is None and use_disk:
        payload = _load_disk(key)
    if payload is not None:
        try:
            simulation = pickle.loads(payload)
        except Exception:  # fichier corrompu ou d'une autre version : on recalcule
            payload = None
        else:
            _remember(key, payload)
            return simulation
    simulation = EconomySimulation(config)
    payload = pickle.dumps(simulation, protocol=pickle.HIGHEST_PROTOCOL)
    _remember(key, payload)
    if use_disk:
        _store_disk(key, payload)
    return simulation


def _remember(key: str, payload: bytes) -> None:
    with _lock:
        _memory[key] = payload
        _memory.move_to_end(key)
        while len(_memory) > MEMORY_ENTRIES:
            _memory.popitem(last=False)


def clear_memory() -> None:
    with _lock:
        _memory.clear()
