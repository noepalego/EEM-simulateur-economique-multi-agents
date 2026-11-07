"""Mesures reproductibles, séparées des contrôles d'identité (aucune tolérance).

Exemple : python performance_probe.py profile --source . --output results/profile
Les sources de référence doivent être copiées avant toute modification.

Mission « optimisation » (results/optimisation/) : cas ``cible_*`` (configuration cible : préréglage
« autonome stable » et État institutionnel dans les sources d'avant la mission, défauts ensuite) ;
variante d'empreinte comparable entre des sources de structures différentes (``--exclusions``,
``--compare ... --variant``) ; parcours ``ui`` en configuration cible jusqu'à 300 périodes. Les modes
``identity`` et ``ui`` se relancent avec ``PYTHONHASHSEED=0`` (voir ``fixed_hash_seed``).
"""
from __future__ import annotations

import argparse
import cProfile
import dataclasses
import hashlib
import json
import platform
import pstats
import random
import sys
import time
from pathlib import Path

PHASES = ("_begin_month", "_credit_and_labor", "_consumer_markets", "_public_sector",
          "_investment_and_housing", "_close_books", "_industry_dynamics", "_record_month")


def _neutral_at_default(value, name, attribute) -> bool:
    """Champ de dataclass ajouté après une référence et marqué ``metadata={"identity":
    "neutre"}`` (``economy.models.NEUTRAL``), à sa valeur par défaut : absent des anciens
    états, il ne change pas le comportement ; il n'entre donc pas dans l'empreinte. À une
    autre valeur, il y entre comme tout attribut."""
    spec = value.__dataclass_fields__.get(name)
    if spec is None or spec.metadata.get("identity") != "neutre":
        return False
    if spec.default is not dataclasses.MISSING:
        return attribute == spec.default
    if spec.default_factory is not dataclasses.MISSING:
        return attribute == spec.default_factory()
    return False


def exact_state(value):
    """Représentation exacte et ordonnée, incluant tous les flux aléatoires.

    Les historiques sont entièrement inclus aux points de contrôle. Les objets
    dictionnaires dérivés sont décrits par leur contenu et leurs attributs. Seuls les
    champs neutres à leur valeur par défaut sont omis (``_neutral_at_default``).
    """
    if isinstance(value, random.Random):
        return ("Random", value.getstate())
    if isinstance(value, float):
        return ("float", value.hex())
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return (type(value).__name__, [(exact_state(k), exact_state(v)) for k, v in value.items()],
                exact_state(vars(value)) if hasattr(value, "__dict__") else None)
    if isinstance(value, (tuple, list)):
        return (type(value).__name__, [exact_state(v) for v in value])
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, sorted((exact_state(v) for v in value), key=repr))
    if hasattr(value, "__dict__"):
        attributes = vars(value)
        if dataclasses.is_dataclass(value):
            attributes = {name: attribute for name, attribute in attributes.items()
                          if not _neutral_at_default(value, name, attribute)}
        return (type(value).__name__, exact_state(attributes))
    raise TypeError(type(value))


def digest(value):
    return hashlib.sha256(repr(exact_state(value)).encode()).hexdigest()


class _VariantFeed:
    """Empreinte en flux (mission « optimisation », results/optimisation/) : même contenu
    qu'``exact_state``, sans en construire la représentation en mémoire."""

    def __init__(self, excluded: dict, keep_config: bool) -> None:
        self.hash = hashlib.sha256()
        self.parts: list[str] = []
        self.excluded = excluded
        self.keep_config = keep_config

    def flush(self) -> None:
        self.hash.update("".join(self.parts).encode())
        self.parts.clear()

    def feed(self, value) -> None:
        parts = self.parts
        if isinstance(value, random.Random):
            parts.append("R" + repr(value.getstate()) + ";")
        elif isinstance(value, bool):
            parts.append("T" if value else "F")
        elif isinstance(value, float):
            parts.append("f" + value.hex() + ";")
        elif isinstance(value, int):
            parts.append("i" + str(value) + ";")
        elif value is None:
            parts.append("N")
        elif isinstance(value, str):
            parts.append("s" + str(len(value)) + ":" + value)
        elif isinstance(value, dict):
            parts.append("d" + type(value).__name__ + "{")
            for key, item in value.items():
                self.feed(key)
                self.feed(item)
            parts.append("}")
            if hasattr(value, "__dict__"):
                self.attributes(value)
        elif isinstance(value, (tuple, list)):
            parts.append(("t" if isinstance(value, tuple) else "l") + type(value).__name__ + "[")
            for item in value:
                self.feed(item)
            parts.append("]")
            if len(parts) > 200_000:
                self.flush()
        elif isinstance(value, (set, frozenset)):
            texts = []
            for item in value:
                sub = _VariantFeed(self.excluded, self.keep_config)
                sub.feed(item)
                texts.append("".join(sub.parts))
            parts.append("S" + type(value).__name__ + "[" + "".join(sorted(texts)) + "]")
        elif hasattr(value, "__dict__"):
            if type(value).__name__ == "SimulationConfig" and not self.keep_config:
                parts.append("C;")
                return
            self.attributes(value)
        else:
            raise TypeError(type(value))

    def attributes(self, value) -> None:
        name = type(value).__name__
        skipped = self.excluded.get(name, ())
        attributes = vars(value)
        self.parts.append("o" + name + "(")
        for attribute in sorted(attributes):
            if attribute not in skipped:
                self.parts.append(attribute + "=")
                self.feed(attributes[attribute])
        self.parts.append(")")


def variant_digest(value, excluded: dict | None = None, keep_config: bool = False) -> str:
    """Variante d'empreinte comparable entre la copie figée et des sources dont des champs ont
    disparu (mission « optimisation ») : aucun champ neutre n'est omis ; ``excluded`` (nom de
    classe -> attributs) retire les attributs supprimés, à lister explicitement ; attributs d'un
    objet triés par nom (l'ordre de création d'un attribut ne change aucun calcul ; l'ordre des
    dictionnaires et des listes est gardé). ``keep_config=False`` : toute instance de
    ``SimulationConfig`` (``config``, ``live``, copie gardée par un autre objet) est exclue ;
    ``True`` : ses champs restants entrent, sauf ceux d'``excluded["SimulationConfig"]``."""
    feed = _VariantFeed(excluded or {}, keep_config)
    feed.feed(value)
    feed.flush()
    return feed.hash.hexdigest()


def variant_digests(value, excluded: dict | None = None) -> dict:
    return {"sans_config": variant_digest(value, excluded, False),
            "config_gardee": variant_digest(value, excluded, True)}


def fixed_hash_seed() -> int | None:
    """Relance le script courant avec ``PYTHONHASHSEED=0`` s'il ne l'est pas déjà et rend son code
    de sortie (None : déjà fixé). L'ordre d'insertion de ``institutions.vacancy_months`` suit
    l'itération d'un ensemble de chaînes (``state_services.py``), donc le hachage aléatoire des
    chaînes : sans effet sur les calculs (lecture triée), mais l'empreinte complète en dépend."""
    import os
    import subprocess
    if os.environ.get("PYTHONHASHSEED") == "0":
        return None
    flags = ["-X", "utf8"] if sys.flags.utf8_mode else []
    return subprocess.call([sys.executable, *flags, *sys.argv], env={**os.environ, "PYTHONHASHSEED": "0"})


def load_exclusions(path: Path | None) -> dict:
    """Attributs exclus de la variante d'empreinte : fichier JSON nom de classe -> liste
    d'attributs (les clés commençant par « _ » sont des commentaires)."""
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: set(values) for name, values in data.items() if not name.startswith("_")}


def target_overrides() -> dict:
    """Configuration cible de la mission « optimisation » : préréglage « autonome stable » et
    État institutionnel dans les sources d'avant la mission, défauts ensuite (le préréglage
    n'existe plus)."""
    from economy import models
    preset = getattr(models, "AUTONOMOUS_STABLE_PRESET", None)
    return {} if preset is None else {**preset, "state_model": "institutions"}


TABS = ("État", "Entreprises & bourse", "Ménages", "Modèle & réglages", "Vue d’ensemble")


def ui_path(args, root, result, excluded, engine, AppTest, EconomySimulation) -> None:
    """Parcours de l'interface en configuration cible (mission « optimisation »), chronométré.

    Ancienne interface (préréglages présents) : défauts, bouton « Autonome stable », État
    institutionnel, puis réinitialisation, qui correspond au premier affichage de la nouvelle
    interface (actions alignées : ``align``). Ensuite, dans les deux : réexécutions, un mois, avance
    de 12 mois, sélections et onglets à la période 13, changement de la graine puis
    réinitialisation, avance jusqu'à 300 périodes, mêmes mesures à 300. L'état de la simulation
    (variante d'empreinte, ``--exclusions``) est relevé après chaque action, hors chronométrage.
    Vérifications : sélections conservées, mois affiché, réglage appliqué seulement à la
    réinitialisation, sessions isolées, et simulation de l'interface identique au moteur lancé
    directement avec la même configuration et le même nombre de mois.
    """
    import copy
    from economy import models
    app = AppTest.from_file(str(root / "main.py"), default_timeout=900)
    app.session_state["seed"] = 1001
    rows = []

    def run(label, action, align=True):
        engine["initialization"] = engine["steps"] = 0.0
        start = time.perf_counter()
        action()
        elapsed = time.perf_counter() - start
        assert not app.exception, (label, str(app.exception))
        sim = app.session_state["simulation"]
        # Le cache de rendu doit suivre l'avance et la réinitialisation, pas seulement le moteur.
        overview_sales = [m for m in app.metric if m.label == "Ventes alimentaires"]
        if overview_sales:
            observed = sim.observed_history
            expected = f"{observed[-1].food_sold:.1f}" if observed else "—"
            assert overview_sales[0].value == expected, (label, "affichage périmé")
        rows.append({"action": label, "align": align, "seconds": elapsed, "period": sim.period,
                     "engine_initialization": engine["initialization"], "engine_steps": engine["steps"],
                     "interface": elapsed - engine["initialization"] - engine["steps"],
                     "state": variant_digest(sim, excluded)})
        print(f"{label:24s} période {sim.period:3d} {elapsed:7.3f} s", flush=True)

    def button(label):
        return next(b for b in app.button if label in b.label)

    def tab(label):
        app.session_state["results_tab"] = label
        app.run()

    def options(kind, key):
        try:
            return list(getattr(app, kind)(key=key).options)
        except KeyError:
            return []

    def interactions(suffix):
        sim = app.session_state["simulation"]
        run(f"tab_state_{suffix}", lambda: tab("État"))
        authors = options("selectbox", "state_journal_author")
        if len(authors) > 1:
            run(f"journal_author_{suffix}",
                lambda: app.selectbox(key="state_journal_author").set_value(authors[1]).run())
        services = options("selectbox", "public_service_filter")
        if len(services) > 1:
            run(f"service_filter_{suffix}",
                lambda: app.selectbox(key="public_service_filter").set_value(services[1]).run())
        run(f"tab_firms_{suffix}", lambda: tab("Entreprises & bourse"))
        choice = [c.id for c in sim.companies if c.active][-1]
        run(f"company_{suffix}", lambda: app.selectbox(key="company_selector").set_value(choice).run())
        stock_choices = [c.id for c in sim.companies if c.active][1:3]
        run(f"stock_companies_{suffix}",
            lambda: app.multiselect(key="stock_chart_companies").set_value(stock_choices).run())
        run(f"tab_households_{suffix}", lambda: tab("Ménages"))
        person = sim.people[-1].id
        run(f"household_{suffix}", lambda: app.selectbox(key="household_selector").set_value(person).run())
        run(f"tab_model_{suffix}", lambda: tab("Modèle & réglages"))
        run(f"tab_overview_{suffix}", lambda: tab("Vue d’ensemble"))
        # sélections conservées après un aller-retour entre onglets (hors chronométrage)
        tab("Entreprises & bourse")
        assert app.selectbox(key="company_selector").value == choice
        assert app.multiselect(key="stock_chart_companies").value == stock_choices
        tab("Ménages")
        assert app.selectbox(key="household_selector").value == person
        tab("Vue d’ensemble")

    def advance_to(period):
        app.number_input(key="run_months").set_value(120).run()
        while app.session_state["simulation"].period + 120 <= period:
            run("advance_120", lambda: button("Avancer").click().run())
        remaining = period - app.session_state["simulation"].period
        if remaining > 0:
            app.number_input(key="run_months").set_value(remaining).run()
            run(f"advance_to_{period}", lambda: button("Avancer").click().run())
        app.number_input(key="run_months").set_value(12).run()

    if hasattr(models, "AUTONOMOUS_STABLE_PRESET"):
        # ancienne interface : configuration cible = préréglage, État institutionnel, réinitialisation
        run("initial_defauts", app.run, align=False)
        run("preset_stable", lambda: next(b for b in app.button if b.label == "Autonome stable").click().run(),
            align=False)
        run("state_model", lambda: app.selectbox(key="state_model").set_value("institutions").run(), align=False)
        run("initial", lambda: button("initialiser la simulation").click().run())
    else:
        run("initial", app.run)
    for key, value in target_overrides().items():
        assert getattr(app.session_state["simulation"].config, key) == value, key
    for _ in range(args.repeats):
        run("rerun", app.run)
    run("one_month", lambda: button("Un mois").click().run())
    run("advance_12", lambda: button("Avancer").click().run())
    for _ in range(args.repeats):
        run("rerun_history", app.run)
    interactions("13")
    run("parameter_seed", lambda: app.number_input(key="seed").set_value(1002).run())
    assert app.session_state["simulation"].config.seed == 1001, "réglage appliqué avant la réinitialisation"
    run("reset_seed", lambda: button("initialiser la simulation").click().run())
    assert app.session_state["simulation"].config.seed == 1002
    run("one_month_after_reset", lambda: button("Un mois").click().run())
    advance_to(300)
    for _ in range(args.repeats):
        run("rerun_300", app.run)
    run("one_month_300", lambda: button("Un mois").click().run())
    sim = app.session_state["simulation"]
    assert next(m for m in app.metric if m.label == "Prix alimentaire").value == f"{sim.food_price:.2f} €"
    run("advance_12_300", lambda: button("Avancer").click().run())
    for _ in range(args.repeats):
        run("rerun_313", app.run)
    interactions("313")
    # l'interface ne modifie jamais la simulation : même état que le moteur lancé directement
    sim = app.session_state["simulation"]
    direct = EconomySimulation(copy.deepcopy(sim.config))
    for _ in range(sim.period):
        direct.step()
    assert variant_digest(direct, excluded, True) == variant_digest(sim, excluded, True), "interface ≠ moteur"
    run("parameter_seed_313", lambda: app.number_input(key="seed").set_value(1003).run())
    assert app.session_state["simulation"].config.seed == 1002, "réglage appliqué avant la réinitialisation"
    run("reset_seed_313", lambda: button("initialiser la simulation").click().run())
    assert app.session_state["simulation"].config.seed == 1003
    second = AppTest.from_file(str(root / "main.py"), default_timeout=900)
    second.session_state["warmup_years"] = 0
    second.run()
    assert not second.exception, str(second.exception)
    assert second.session_state["simulation"] is not app.session_state["simulation"]
    result["actions"] = rows
    result["ui_checks"] = ("navigation, sélections conservées, un mois, avance, réglage différé puis "
                           "réinitialisation, sessions isolées, interface = moteur direct (période "
                           f"{sim.period}) : OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["time", "profile", "identity", "ui"])
    parser.add_argument("--source", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--months", type=int, default=150)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--compare", type=Path, help="Référence JSON pour le mode identity ou ui")
    parser.add_argument("--exclusions", type=Path,
                        help="Attributs exclus de la variante d'empreinte (JSON : classe -> attributs)")
    parser.add_argument("--variant", action="store_true",
                        help="--compare en mode identity : trajectoire et variante d'empreinte seulement")
    args = parser.parse_args()
    if args.mode in ("identity", "ui"):
        code = fixed_hash_seed()
        if code is not None:
            raise SystemExit(code)
    root = args.source.resolve()
    sys.path.insert(0, str(root))
    from economy import models
    from economy.models import SimulationConfig
    from economy.simulation import EconomySimulation
    excluded = load_exclusions(args.exclusions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"python": platform.python_version(), "platform": platform.platform(),
              "source": str(root), "months": args.months, "cases": {},
              "exclusions": {name: sorted(values) for name, values in excluded.items()}}
    # Configuration cible (mission « optimisation ») : graines 1001 à 1003, 960 ménages, sans chauffe
    target = target_overrides()
    cases = {
        "cible_1001": {"seed": 1001, **target},
        "cible_1002": {"seed": 1002, **target},
        "cible_1003": {"seed": 1003, **target},
        "cible_large_1002": {"seed": 1002, "population_size": 960, **target},
        "cible_raw_42": {"seed": 42, "warmup_months": 0, **target},
    }
    if hasattr(models, "AUTONOMOUS_PRESET"):
        # anciens cas (défauts, anticipations locales, préréglage autonome) : sources d'avant la mission
        cases.update({
            "default_1001": {"seed": 1001},
            "default_1003": {"seed": 1003},
            "local_1003": {"seed": 1003, "expectations_mode": "local"},
            "autonomous_1001": {"seed": 1001, **models.AUTONOMOUS_PRESET},
            "large_1002": {"seed": 1002, "population_size": 960},
            "raw_42": {"seed": 42, "warmup_months": 0},
        })
    if args.cases:
        cases = {name: cases[name] for name in args.cases}
    if args.mode == "ui":
        import logging
        logging.getLogger("streamlit").setLevel(logging.ERROR)
        from streamlit.testing.v1 import AppTest
        import streamlit
        result["streamlit"] = streamlit.__version__
        engine = {"initialization": 0.0, "steps": 0.0, "initializing": False}
        original_init, original_step = EconomySimulation.__init__, EconomySimulation.step
        def timed_init(self, *a, **kw):
            start = time.perf_counter()
            engine["initializing"] = True
            try:
                original_init(self, *a, **kw)
            finally:
                engine["initializing"] = False
                engine["initialization"] += time.perf_counter() - start
        def timed_step(self):
            start = time.perf_counter()
            out = original_step(self)
            if not engine["initializing"]:
                engine["steps"] += time.perf_counter() - start
            return out
        EconomySimulation.__init__, EconomySimulation.step = timed_init, timed_step
        ui_path(args, root, result, excluded, engine, AppTest, EconomySimulation)
    else:
        for name, overrides in cases.items():
            if args.mode == "profile" and not args.cases and name not in (
                    "default_1001", "large_1002", "cible_1001", "cible_large_1002"):
                continue
            rows = []
            for repeat in range(args.repeats if args.mode == "time" else 1):
                profiler = cProfile.Profile() if args.mode == "profile" else None
                if profiler:
                    profiler.enable()
                start = time.perf_counter()
                sim = EconomySimulation(SimulationConfig(**overrides))
                init_seconds = time.perf_counter() - start
                if args.mode == "identity":
                    states = {"0": digest(sim)}
                    variant_states = {"0": variant_digests(sim, excluded)}
                    trajectory = hashlib.sha256()
                    phase_states = []
                    variant_phases = []
                    original_methods = {phase: getattr(EconomySimulation, phase) for phase in PHASES}
                    # État et MonthFlows après chacune des huit phases, mois 1 et 2.
                    for phase in PHASES:
                        original = original_methods[phase]
                        def checked(self, flows, original=original, phase=phase):
                            out = original(self, flows)
                            if self.period <= 2:
                                # liste et non tuple : la référence relue en JSON ne contient que des listes
                                phase_states.append([self.period, phase, digest((self, flows))])
                                variant_phases.append([self.period, phase, variant_digests((self, flows), excluded)])
                            return out
                        setattr(EconomySimulation, phase, checked)
                start = time.perf_counter()
                for month in range(1, args.months + 1):
                    record = sim.step()
                    if args.mode == "identity":
                        trajectory.update(repr(record).encode())
                        if month in (1, 2, 12, 60, args.months):
                            states[str(month)] = digest(sim)
                            variant_states[str(month)] = variant_digests(sim, excluded)
                step_seconds = time.perf_counter() - start
                if profiler:
                    profiler.disable()
                    profile_path = str(args.output) + "_" + name + ".prof"
                    profiler.dump_stats(profile_path)
                    with open(profile_path + ".txt", "w", encoding="utf-8") as out:
                        pstats.Stats(profiler, stream=out).strip_dirs().sort_stats("cumulative").print_stats(65)
                row = {"initialization": init_seconds, "steps": step_seconds,
                       "total": init_seconds + step_seconds}
                if args.mode == "identity":
                    row.update(states=states, trajectory=trajectory.hexdigest(), phases=phase_states,
                               variant={"states": variant_states, "phases": variant_phases})
                    # Restaurer les enveloppes pour le scénario suivant.
                    for phase in PHASES:
                        setattr(EconomySimulation, phase, original_methods[phase])
                rows.append(row)
                print(name, repeat, round(row["total"], 3), flush=True)
            result["cases"][name] = {"config": overrides, "runs": rows}
            args.output.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.compare:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        if args.mode == "identity":
            assert result["cases"].keys() == reference["cases"].keys(), "Scénarios différents"
            if args.variant:
                # sources de structures différentes : mêmes exclusions des deux côtés
                assert result["exclusions"] == reference.get("exclusions"), "Exclusions différentes"
            for name, case in result["cases"].items():
                actual, expected = case["runs"][0], reference["cases"][name]["runs"][0]
                if args.variant:
                    assert actual["trajectory"] == expected["trajectory"], (name, "trajectory")
                    for key in ("states", "phases"):
                        assert actual["variant"][key] == expected["variant"][key], (name, "variant", key)
                else:
                    for key in ("states", "trajectory", "phases"):
                        assert actual[key] == expected[key], (name, key)
        elif args.mode == "ui":
            # actions alignées seulement (le préfixe de l'ancienne interface n'a pas d'équivalent)
            actual = [row for row in result["actions"] if row.get("align", True)]
            expected = [row for row in reference["actions"] if row.get("align", True)]
            assert len(actual) == len(expected), "parcours différents"
            for row, reference_row in zip(actual, expected):
                for key in ("action", "period", "state"):
                    assert row[key] == reference_row[key], (row["action"], key)
        else:
            parser.error("--compare exige identity ou ui")
        print("Identité exacte avec", args.compare, flush=True)


if __name__ == "__main__":
    main()
