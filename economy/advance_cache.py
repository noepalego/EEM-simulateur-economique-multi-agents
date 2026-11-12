"""Préparation facultative d'une avance pendant la lecture du tableau de bord.

Le travail porte sur une copie privée, avec les mêmes générateurs aléatoires.
L'état visible ne change qu'au clic. Une préparation appartient à un état précis du
moteur : son objet, son mois et l'empreinte de sa copie sérialisée, relevée après chaque
rendu. Toute intervention, toute avance et tout état différent l'annulent ; un simple
changement d'onglet, qui ne modifie pas le moteur, la laisse continuer (troisième passe,
11 novembre 2026). Un résultat indisponible n'est jamais attendu par l'interface, sauf
la fin du mois en cours lors d'une reprise partielle (``take_progress``).

Un seul fil. Résultats gardés : le premier mois (bouton « Un mois ») et la copie privée
elle-même, à son mois courant, jusqu'à la période choisie (``MAX_MONTHS`` au plus). Au clic,
une préparation inachevée est reprise là où elle en est : seuls les mois restants sont
calculés. Limites sur la taille des instantanés. Aucun cache global entre sessions et
aucune écriture sur disque.
"""

from __future__ import annotations

import hashlib
import pickle
import threading

from economy import diagnostics

MAX_MONTHS = 120
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_HISTORY_ROWS = 500_000
HANDOVER_WAIT = 3.0  # secondes au plus pour laisser finir le mois en cours avant une reprise

_HELD = object()  # révision d'une préparation à reconfirmer : aucun clic ne peut la prendre


class _Job:
    """Une préparation : un état de départ, une copie privée qui avance."""

    __slots__ = ("key", "digest", "revision", "payload", "target", "cancel", "ready",
                 "done", "frontier", "running", "failed", "finished")

    def __init__(self, key, digest, revision, payload, target) -> None:
        self.key = key              # (objet moteur, mois) de l'état visible
        self.digest = digest        # empreinte de sa copie sérialisée
        self.revision = revision    # dernier rendu qui a confirmé cet état
        self.payload = payload      # copie sérialisée, libérée dès qu'elle est relue
        self.target = target        # nombre de mois à préparer
        self.cancel = threading.Event()
        self.ready: dict[int, object] = {}  # mois -> copie détachée prête (le premier mois)
        self.done = 0               # mois terminés par la copie privée
        self.frontier = None        # copie privée, publiée quand le fil ne la touche plus
        self.running = True         # en attente du fil ou en cours de calcul
        self.failed = False
        self.finished = threading.Event()


class AdvanceCache:
    """Cache propre à une session ; les fils s'arrêtent dès la fin du travail."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread = None
        self._queued: _Job | None = None
        self._job: _Job | None = None

    @staticmethod
    def _state_key(engine):
        return id(engine), engine.month

    def _current(self, engine, revision) -> _Job | None:
        """Préparation valable pour cet état et ce rendu (verrou tenu par l'appelant)."""
        job = self._job
        if (job is None or job.cancel.is_set() or job.revision is _HELD
                or job.revision != revision or job.key != self._state_key(engine)):
            return None
        return job

    def invalidate(self) -> None:
        """Abandonne le travail courant et libère les résultats déjà préparés."""
        with self._lock:
            job, self._job, self._queued = self._job, None, None
            if job is not None:
                job.cancel.set()
                job.ready.clear()
                job.frontier = None
                job.payload = None

    def hold(self) -> None:
        """Début d'un rendu : la préparation continue, mais aucun clic ne peut la prendre tant
        que ``prepare`` n'a pas confirmé, après ce rendu, que l'état visible est resté le même
        (une vue peut mémoriser une mesure dans le moteur)."""
        with self._lock:
            if self._job is not None:
                self._job.revision = _HELD

    def prepare(self, engine, revision, months: int) -> bool:
        """Copie l'état après le rendu, puis prépare l'avance sans toucher au moteur.

        Si l'état copié est exactement celui de la préparation en cours (même objet, même
        mois, même empreinte), elle continue, vers la nouvelle période choisie. Au-delà de
        ``MAX_MONTHS``, seul le bouton « Un mois » est anticipé. Un état trop volumineux ou
        non sérialisable utilise l'avance habituelle.
        """
        target = months if 1 <= months <= MAX_MONTHS else 1
        key = self._state_key(engine)
        if diagnostics.active():
            self.invalidate()
            return False  # un diagnostic observe uniquement les mois demandés
        regions = getattr(engine, "regions", (engine,))
        rows = sum(len(agent.financial_history) for sim in regions
                   for agents in (getattr(sim, "people", ()), getattr(sim, "companies", ()))
                   for agent in agents)
        if rows > MAX_HISTORY_ROWS:
            self.invalidate()
            return False  # éviter aussi de sérialiser une très longue trajectoire
        try:
            payload = pickle.dumps(engine, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            self.invalidate()
            return False
        if len(payload) > MAX_SNAPSHOT_BYTES:
            self.invalidate()
            return False
        digest = hashlib.blake2b(payload, digest_size=16).digest()
        with self._lock:
            job = self._job
            if (job is not None and not job.cancel.is_set() and job.key == key
                    and job.digest == digest and (job.failed or job.done <= target)):
                # même état : la préparation en cours reste valable et continue
                job.revision = revision
                job.target = target
                if not job.running and not job.failed and job.done < target and job.frontier is not None:
                    job.running = True
                    job.finished.clear()
                    self._start(job)
                return True
        self.invalidate()
        with self._lock:
            job = self._job = _Job(key, digest, revision, payload, target)
            self._start(job)
        return True

    def _start(self, job: _Job) -> None:
        """Confie ``job`` au fil de calcul (verrou tenu par l'appelant)."""
        self._queued = job
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True, name="economic-advance")
            self._thread.start()

    def take(self, engine, revision, months: int):
        """Rend une avance complète prête pour cet état, sinon None, sans attente."""
        with self._lock:
            job = self._current(engine, revision)
            prepared = None
            if job is not None:
                prepared = job.ready.get(months)
                if prepared is None and not job.running and job.done == months:
                    prepared = job.frontier
        # Même en cas de repli, ne pas laisser une avance périmée consommer du CPU.
        self.invalidate()
        # Transférer la copie prête évite de désérialiser un long historique au clic.
        return prepared

    def take_progress(self, engine, revision, months: int, wait: float = HANDOVER_WAIT):
        """Rend ``(copie, mois déjà faits)`` pour cet état, ou None.

        Avance complète : rendue sans attente. Préparation inachevée de cette même avance : le
        fil termine le mois en cours (``wait`` secondes au plus), puis sa copie est reprise à
        ce mois ; l'appelant calcule les mois restants. À défaut, le premier mois s'il est prêt.
        """
        with self._lock:
            job = self._current(engine, revision)
            result = None
            if job is not None:
                exact = job.ready.get(months)
                if exact is not None:
                    result = (exact, months)
                else:
                    job.cancel.set()  # le fil s'arrête à la fin du mois en cours
        if job is not None and result is None:
            finished = job.finished.wait(wait)
            with self._lock:
                if finished and job.frontier is not None and 1 <= job.done <= months:
                    result = (job.frontier, job.done)
                elif months >= 1 and job.ready.get(1) is not None:
                    result = (job.ready[1], 1)
        self.invalidate()
        return result

    def _work(self) -> None:
        while True:
            with self._lock:
                job, self._queued = self._queued, None
                if job is None:
                    self._thread = None
                    return
            self._run(job)

    def _run(self, job: _Job) -> None:
        engine = None
        try:
            with self._lock:
                engine, job.frontier = job.frontier, None  # reprise d'une préparation prolongée
                payload, job.payload = job.payload, None
            if engine is None:
                engine = pickle.loads(payload)
            del payload
            while True:
                with self._lock:
                    if job.cancel.is_set() or job.done >= job.target:
                        break
                    month, target = job.done + 1, job.target
                if diagnostics.active():
                    break
                engine.step()
                if month == 1 and target > 1 and not job.cancel.is_set():
                    # Détacher la suite AVANT de publier le premier mois :
                    # une adoption concurrente ne peut modifier le travail.
                    first = engine
                    snapshot = pickle.dumps(first, protocol=pickle.HIGHEST_PROTOCOL)
                    if len(snapshot) > MAX_SNAPSHOT_BYTES:
                        with self._lock:
                            job.done = 1
                        break
                    engine = pickle.loads(snapshot)
                    del snapshot
                    with self._lock:
                        if not job.cancel.is_set():
                            job.ready[1] = first
                    del first
                with self._lock:
                    job.done = month
        except Exception:
            # Cache facultatif : une erreur n'affecte jamais l'état visible.
            # L'avance synchrone garde ses erreurs ordinaires, sans les masquer.
            job.failed = True
            engine = None
        finally:
            with self._lock:
                # Le fil ne touche plus sa copie : elle peut être adoptée à son mois courant.
                if engine is not None and job.done >= 1 and self._job is job:
                    job.frontier = engine
                job.running = False
            job.finished.set()
