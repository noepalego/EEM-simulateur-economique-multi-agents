"""Compteurs de diagnostic, hors état de la simulation.

Trois mesures (étape 0 de la mission « autonomie stable ») :

- ``bite(nom, mord)`` : une décision soumise à une borne (plafond de prix, vitesse
  maximale, borne des anticipations, plafond du taux directeur, plafonds d'embauche...)
  et si la borne a changé la valeur décidée ; la morsure est la part des décisions
  bornées ;
- ``flow(nom, montant)`` : montants passés par un chemin de sauvetage ou de garantie
  (fonds orphelin, recette sans salarié, emprunt public dû à la cible de dette...) ;
- ``refusal(nature, motif)`` : pourquoi une entreprise n'embauche pas, pourquoi une
  banque refuse, pourquoi un entrepreneur renonce ; les ``sample_limit`` premiers cas
  de chaque nature gardent leur détail.

Inactifs par défaut : chaque fonction retourne aussitôt tant que ``start`` n'a pas été
appelé. Le moteur ne leur passe que des valeurs déjà calculées : aucun calcul flottant
ni tirage aléatoire ne change, et rien n'est rangé dans la simulation, dont
``performance_probe.py identity`` compare l'état complet. Un processus observe une
simulation à la fois (``realism_probe.run``).
"""

from __future__ import annotations

_bites: dict[str, list[int]] | None = None
_flows: dict[str, float] | None = None
_refusals: dict[str, dict[str, int]] | None = None
_samples: dict[str, list] | None = None
_sample_limit = 0
_month = 0


def start(sample_limit: int = 0) -> None:
    """Active les compteurs (remis à zéro) ; ``sample_limit`` cas détaillés par nature de refus."""
    global _bites, _flows, _refusals, _samples, _sample_limit
    _bites, _flows, _refusals, _samples = {}, {}, {}, {}
    _sample_limit = max(0, int(sample_limit))


def stop() -> None:
    global _bites, _flows, _refusals, _samples
    _bites = _flows = _refusals = _samples = None


def active() -> bool:
    return _bites is not None


def set_month(month: int) -> None:
    """Mois interne courant, joint aux cas détaillés."""
    global _month
    _month = month


def bite(name: str, bitten: bool) -> None:
    if _bites is None:
        return
    counts = _bites.get(name)
    if counts is None:
        counts = _bites[name] = [0, 0]
    counts[0] += 1
    if bitten:
        counts[1] += 1


def flow(name: str, amount: float) -> None:
    if _flows is None or amount == 0.0:
        return
    _flows[name] = _flows.get(name, 0.0) + amount


def refusal(kind: str, reason: str, detail: dict | None = None) -> None:
    if _refusals is None:
        return
    reasons = _refusals.setdefault(kind, {})
    reasons[reason] = reasons.get(reason, 0) + 1
    if detail is not None and _sample_limit:
        samples = _samples.setdefault(kind, [])
        if len(samples) < _sample_limit:
            samples.append({"mois": _month, "motif": reason, **detail})


def snapshot() -> dict:
    """Copie des compteurs cumulés (vide si inactifs)."""
    if _bites is None:
        return {"bites": {}, "flows": {}, "refusals": {}, "samples": {}}
    return {
        "bites": {name: list(counts) for name, counts in _bites.items()},
        "flows": dict(_flows),
        "refusals": {kind: dict(reasons) for kind, reasons in _refusals.items()},
        "samples": {kind: list(cases) for kind, cases in _samples.items()},
    }


def difference(after: dict, before: dict) -> dict:
    """Compteurs accumulés entre deux ``snapshot`` (cas détaillés : ceux d'``after``)."""
    bites = {
        name: [counts[0] - before["bites"].get(name, [0, 0])[0],
               counts[1] - before["bites"].get(name, [0, 0])[1]]
        for name, counts in after["bites"].items()
    }
    flows = {name: value - before["flows"].get(name, 0.0) for name, value in after["flows"].items()}
    refusals = {
        kind: {reason: count - before["refusals"].get(kind, {}).get(reason, 0)
               for reason, count in reasons.items()}
        for kind, reasons in after["refusals"].items()
    }
    return {"bites": bites, "flows": flows, "refusals": refusals, "samples": after["samples"]}
