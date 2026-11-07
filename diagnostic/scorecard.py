"""Critères de réussite de la mission, par variante (résumés de realism_probe).

Usage : python scorecard.py dossier_runs label [label...]   (séries --json)
        python scorecard.py --batch fichier.jsonl [...]      (résumés de batch_probe)
        python scorecard.py --stability fichier.jsonl [...]  (critères de la section 7 de
                                                              la mission « autonomie stable »)

Les seuils sont des critères d'évaluation fixés avant les essais, jamais des bornes du moteur.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import realism_probe  # noqa: E402

SECTORS = realism_probe.SECTORS
# garde-fou dont la morsure dépasse ce pourcentage des décisions : à expliquer (section 7)
BITE_LIMIT = 1.0
# montants qui violent « aucune recette sans livraison ni argent sans origine » (section 7)
NO_DELIVERY_FLOWS = ("fonds_orphelin", "recette_b2b_sans_salarie", "recette_menages_sans_salarie")
# garanties de résultat mesurées (section 4), en % du PIB nominal observé
GUARANTEE_FLOWS = NO_DELIVERY_FLOWS + (
    "fonds_orphelin_reverse", "emprunt_cible_dette", "investissement_keynesien_cible_dette",
    "excedent_public_redistribue", "transferts_retenus_cible_dette",
)


def criteria(s):
    alive = min(s["sector"][x]["alive_emp"] for x in SECTORS)
    return {
        "pib>=0.7": s["gdp_final_peak"] >= 0.7,
        "croiss>0": s["growth"] > 0,
        "chom<10": s["u_mean"] < 10,
        "secteurs_actifs95": min(s["sector"][x]["alive"] for x in SECTORS) > 95,
        "secteurs_employeurs95": alive > 95,
        "2banques_toujours": s["banks_ge2"] == 100,
        "2banques_95mois": s["banks_ge2"] >= 95,
        "infl_ok": s["infl12_max"] < 50 and abs(s["infl_last10y"]) < 10,
        "bulle_chauffe<2": s["max_rise24_W"] < 2,
        "comptes": s["resid_max"] < 1 and s["loan_err_max"] < 1e-6 and s["debt_err_max"] < 1e-6,
    }


def stability_criteria(s):
    """Section 7 de la mission : bloquants, essentiels (``essentiels`` = tous ensemble)."""
    flows = s.get("flows_gdp", {})
    out = {
        "bloquant_comptes": (not s["nan"] and s["resid_max_all"] < 1 and s["loan_err_max_all"] < 1e-6
                             and s["debt_err_max_all"] < 1e-6),
        "bloquant_sans_livraison": all(flows.get(name, 0.0) == 0.0 for name in NO_DELIVERY_FLOWS),
        "coeur": s["u_mean"] < 10 and s["gdp_final_peak"] >= 0.7,
        "pas_effondrement": s.get("collapse_longest_below30", 0) < 60,
        "secteurs_employeurs95": min(s["sector"][x]["alive_emp"] for x in SECTORS) >= 95,
        "inflation": s["infl12_max"] < 50 and -10 < s["infl_last10y"] < 10,
    }
    out["essentiels"] = (out["coeur"] and out["pas_effondrement"] and out["secteurs_employeurs95"]
                         and out["inflation"])
    return out


def line(name, sums):
    n = len(sums)
    crit = [criteria(s) for s in sums]
    keys = list(crit[0])
    counts = {k: sum(c[k] for c in crit) for k in keys}
    med = lambda k: statistics.median(s[k] for s in sums)
    rng = lambda k, f="{:.1f}": f"{f.format(min(s[k] for s in sums))}–{f.format(max(s[k] for s in sums))}"
    weakest = min(SECTORS, key=lambda x: statistics.mean(s["sector"][x]["alive_emp"] for s in sums))
    return (f"{name:12s} n={n} " + " ".join(f"{k} {counts[k]}/{n}" for k in keys)
            + f" | PIB f/pic {rng('gdp_final_peak', '{:.2f}')} chôm {rng('u_mean')} (méd {med('u_mean'):.1f})"
            + f" croiss {rng('growth', '{:+.2f}')} infl {rng('infl')} banques fin {min(s['banks_end'] for s in sums)}–{max(s['banks_end'] for s in sums)}"
            + f" sans-log {rng('homeless_end', '{:.0f}')} entrées {rng('entries', '{:.0f}')}"
            + f" secteur le plus fragile {weakest}")


def _spread(values, higher_is_better=True):
    """Médiane, décile défavorable (quantile 10 % ou 90 %, interpolé) et pire cas."""
    values = sorted(values)
    if len(values) == 1:
        return values[0], values[0], values[0]
    deciles = statistics.quantiles(values, n=10, method="inclusive")
    if higher_is_better:
        return statistics.median(values), deciles[0], values[0]
    return statistics.median(values), deciles[-1], values[-1]


# indicateurs publiés : (clé, libellé, plus haut = mieux, format)
INDICATORS = (
    ("gdp_final_peak", "PIB lissé final / pic", True, "{:.2f}"),
    ("u_mean", "chômage moyen (%)", False, "{:.1f}"),
    ("u_max", "chômage mensuel max après 3 ans (%)", False, "{:.1f}"),
    ("crisis_depth_min", "creux le plus profond / pic", True, "{:.2f}"),
    ("crisis_months_below75", "mois sous 75 % du pic", False, "{:.0f}"),
    ("collapse_longest_below30", "plus longue suite sous 30 % du pic (mois)", False, "{:.0f}"),
    ("infl12_max", "inflation sur 12 mois max (%)", False, "{:.1f}"),
    ("infl_last10y", "inflation des 10 dernières années (%/an)", None, "{:.1f}"),
    ("growth", "croissance réelle (%/an)", True, "{:+.2f}"),
    ("homeless_end", "sans-logement en fin", False, "{:.0f}"),
    ("unmet_food", "alimentation non servie (%)", False, "{:.1f}"),
    ("e_unmet", "énergie non livrée (%)", False, "{:.1f}"),
    ("months_any_sector_without_employer", "mois avec un secteur sans employeur", False, "{:.0f}"),
    ("banks_ge2", "mois avec ≥ 2 banques (%)", True, "{:.0f}"),
    ("banks_min", "banques au minimum", True, "{:.0f}"),
    ("resolution_public_cost_gdp", "coût public des résolutions (% PIB)", False, "{:.2f}"),
    ("restructured_idle_firm_months", "mois-entreprise restructurées sans salarié", False, "{:.0f}"),
)


def stability_report(name, sums) -> str:
    """Bloc de texte : critères de la section 7 et dispersion par graine."""
    n = len(sums)
    crit = [stability_criteria(s) for s in sums]
    lines = [f"== {name} (n={n}) " + " ".join(
        f"{k} {sum(c[k] for c in crit)}/{n}" for k in crit[0])]
    lines.append("   graines qui satisfont ensemble les essentiels : "
                 + ", ".join(str(s["seed"]) for s, c in zip(sums, crit) if c["essentiels"]))
    lines.append(f"   {'indicateur':45s} {'médiane':>9} {'décile déf.':>11} {'pire':>9}")
    for key, label, better, fmt in INDICATORS:
        values = [s[key] for s in sums if key in s]
        if not values:
            continue
        if better is None:  # inflation : le pire est le plus éloigné de zéro
            med = statistics.median(values)
            worst = max(values, key=abs)
            lines.append(f"   {label:45s} {fmt.format(med):>9} {'':>11} {fmt.format(worst):>9}")
            continue
        med, decile, worst = _spread(values, better)
        lines.append(f"   {label:45s} {fmt.format(med):>9} {fmt.format(decile):>11} {fmt.format(worst):>9}")
    # garanties de résultat (section 4) : médiane et maximum en % du PIB
    for key in GUARANTEE_FLOWS:
        values = [s.get("flows_gdp", {}).get(key, 0.0) for s in sums]
        if any(values):
            lines.append(f"   {('garantie : ' + key + ' (% PIB)'):45s} {statistics.median(values):>9.3f}"
                         f" {'':>11} {max(values):>9.3f}")
    restructurings = [s.get("resolutions", {}).get("restructure", 0) for s in sums]
    if any(restructurings):
        lines.append(f"   {'garantie : restructurations du dernier acteur':45s} "
                     f"{statistics.median(restructurings):>9.0f} {'':>11} {max(restructurings):>9.0f}")
    # garde-fous : morsure en % des décisions concernées (médiane, maximum)
    names = sorted({k for s in sums for k in s.get("bites", {})})
    over = []
    for key in names:
        values = [s["bites"].get(key, 0.0) for s in sums if key in s.get("bites", {})]
        med, top = statistics.median(values), max(values)
        decisions = statistics.median(s.get("bites_n", {}).get(key, 0) for s in sums if key in s.get("bites", {}))
        warmup = max((s.get("bites_warmup", {}).get(key, 0.0) for s in sums), default=0.0)
        mark = " > 1 %" if top >= BITE_LIMIT else ""
        if top >= BITE_LIMIT:
            over.append(key)
        lines.append(f"   {('morsure : ' + key + ' (%)'):45s} {med:>9.2f} {'':>11} {top:>9.2f}"
                     f"   ({decisions:.0f} décisions/graine ; chauffe, pire graine {warmup:.2f} %){mark}")
    if names:
        lines.append("   garde-fous qui mordent ≥ 1 % sur au moins une graine : " + (", ".join(over) or "aucun"))
    return "\n".join(lines)


def _read(paths):
    results = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            results += [json.loads(line) for line in fh if line.strip()]
    by = {}
    for r in results:
        if "error" in r:
            print("ERREUR", r["variant"], r["seed"], r["error"])
            continue
        by.setdefault(r["variant"], []).append(r)
    for sums in by.values():
        sums.sort(key=lambda s: s["seed"])
    return by


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "--batch":
        for name, sums in _read(args[1:]).items():
            print(line(name, sums))
    elif args[0] == "--stability":
        for name, sums in _read(args[1:]).items():
            print(stability_report(name, sums))
            print()
    else:
        folder = args[0]
        for label in args[1:]:
            sums = []
            for f in sorted(os.listdir(folder)):
                if f.startswith(label + "_") and f.endswith(".json"):
                    sums.append(realism_probe.summarize(json.load(open(os.path.join(folder, f), encoding="utf-8"))))
            print(line(label, sums))
