"""Analyse de results/gauche_droite/runs.jsonl : moyennes par scénario et écarts appariés.

python experiences/gauche_droite_analyse.py  ->  results/gauche_droite/synthese.json et tableau console
"""
import json
import os
import statistics

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", "gauche_droite")
runs = [json.loads(line) for line in open(os.path.join(HERE, "runs.jsonl"), encoding="utf-8")]
by = {(r["seed"], r["scenario"]): r for r in runs}
seeds = sorted({r["seed"] for r in runs})


def mean(years, key):
    return statistics.fmean(y[key] for y in years)


def indicators(run):
    years = run["years"][: run["months"] // 12]  # années complètes seulement (400 mois = 33 ans + 4 mois)
    last = years[-10:]  # dix dernières années
    first_gdp, last_gdp = years[0]["real_gdp_sum"], years[-1]["real_gdp_sum"]
    n = len(years) - 1
    return {
        "PIB réel mensuel (moyenne)": mean(years, "real_gdp"),
        "Croissance réelle annuelle": (last_gdp / first_gdp) ** (1 / n) - 1 if first_gdp > 0 else 0.0,
        "Chômage": mean(years, "unemployment"),
        "Chômage de longue durée": mean(years, "long_term_unemployment"),
        "Inflation annuelle": (years[-1]["cpi_end"] / years[0]["cpi_end"]) ** (1 / n) - 1,
        "Salaire réel moyen": mean(years, "real_average_wage"),
        "Revenu réel des ménages": mean(years, "real_household_income"),
        "Salaire réel moyen (10 dernières années)": mean(last, "real_average_wage"),
        "Gini du patrimoine": mean(years, "household_gini"),
        "Gini du revenu disponible": mean(years, "disposable_income_gini"),
        "Part des 10 % les plus riches": mean(years, "household_top10_share"),
        "Part du 1 % le plus riche": mean(years, "household_top1_share"),
        "Taux de pauvreté": mean(years, "poverty_rate"),
        "Ménages sans logement": mean(years, "unhoused_households"),
        "Patrimoine médian réel (10 dernières années)": statistics.fmean(
            y["household_wealth_median"] * 100.0 / y["cpi_end"] for y in last),
        "Dette publique / PIB (fin)": years[-1]["public_debt_end"] / max(years[-1]["nominal_gdp_sum"], 1.0),
        "Dépenses publiques / PIB": sum(y["public_spending"] for y in years) / sum(y["nominal_gdp"] for y in years),
        "Recettes publiques / PIB": sum(y["public_revenue"] for y in years) / sum(y["nominal_gdp"] for y in years),
        "TVA moyenne": mean(years, "vat_rate"),
        "Salaire minimum / salaire moyen": statistics.fmean(
            y["minimum_wage"] / y["average_wage"] for y in years if y["average_wage"] > 0),
        "Emplois publics": mean(years, "public_employment"),
        "Faillites par an": mean(years, "bankruptcies"),
        "Créations d'entreprises par an": mean(years, "startups"),
        "Entreprises actives": mean(years, "active_companies"),
        "Productivité (PGF, fin)": years[-1]["tfp_index"],
        "Population (fin)": years[-1]["population_count"],
        "Pire année de PIB réel (variation)": min(
            b["real_gdp_sum"] / a["real_gdp_sum"] - 1 for a, b in zip(years, years[1:]) if a["real_gdp_sum"] > 0),
    }


table = {s: {seed: indicators(by[(seed, s)]) for seed in seeds} for s in ("droite", "temoin", "gauche")}
names = list(next(iter(table["droite"].values())))
summary = {}
print(f"{'Indicateur':46s} {'Droite (0)':>12s} {'Témoin':>12s} {'Gauche (100)':>12s}  G>D")
for name in names:
    means = {s: statistics.fmean(table[s][seed][name] for seed in seeds) for s in table}
    wins = sum(table["gauche"][seed][name] > table["droite"][seed][name] for seed in seeds)
    diffs = [table["gauche"][seed][name] - table["droite"][seed][name] for seed in seeds]
    summary[name] = means | {"gauche_superieure_sur": wins, "ecart_min": min(diffs), "ecart_max": max(diffs)}
    print(f"{name:46s} {means['droite']:12.4g} {means['temoin']:12.4g} {means['gauche']:12.4g}  {wins}/{len(seeds)}")
summary["_par_graine"] = table
json.dump(summary, open(os.path.join(HERE, "synthese.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
