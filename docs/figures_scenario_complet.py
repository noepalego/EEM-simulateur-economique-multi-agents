"""Produit les graphiques et les tableaux du scénario complet à partir des exports JSON."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from statistics import fmean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

from scenario_complet import OUT, ROOT, SEEDS, flat
from economy.institutions import FLOW_LABELS, OFFICE_LABELS, SERVICE_LABELS


INK, MUTED, GRID, PAPER = "#152638", "#465b70", "#dbe5ed", "#f5f9fc"
BLUE, GREEN, PURPLE, AMBER, RED = "#2563a5", "#147967", "#6755a6", "#a35c13", "#ae434b"
SECTORS = {
    "food": "Alimentation", "goods": "Biens courants", "services": "Services",
    "technology": "Technologie", "energy": "Énergie", "materials": "Matières premières",
    "real_estate": "Immobilier", "bank": "Banque", "public": "Fonction publique",
}
SECTOR_COLORS = dict(zip(SECTORS, [BLUE, GREEN, PURPLE, "#0d738f", AMBER, "#887040", RED,
                                  "#526a87", "#667e8a"]))

plt.rcParams.update({
    "savefig.dpi": 145, "figure.facecolor": PAPER, "axes.facecolor": "#ffffff",
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.edgecolor": GRID, "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRID, "grid.linewidth": 0.65, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.labelcolor": MUTED, "axes.titlecolor": INK, "text.color": INK,
    "axes.titleweight": "bold", "axes.titlesize": 11.5, "font.size": 10.5,
    "font.family": "DejaVu Sans", "lines.linewidth": 1.85, "legend.frameon": False,
})


def fr(value, digits=1):
    if round(value, digits) == 0:
        value = 0
    return f"{value:,.{digits}f}".replace(",", "\u202f").replace(".", ",").replace("-", "−")


def ratio(numerator, denominator):
    return numerator / denominator if denominator > 0 else None


def load_runs(source, seeds):
    runs = []
    for seed in seeds:
        run = json.loads((source / f"graine_{seed}.json").read_text(encoding="utf-8"))
        rows = run["rows"]
        if run["seed"] != seed or [r["market"]["month"] for r in rows] != list(range(run["months"] + 1)):
            raise ValueError(f"Périodes ou graine incohérentes : {seed}")
        for i, row in enumerate(rows):
            m, e = row["market"], row["enterprise"]
            row["derived"] = {
                "real_gdp_per_household": m["real_gdp"] / m["population_count"],
                "inflation_12m": (
                    m["consumer_price_index"] / rows[i - 12]["market"]["consumer_price_index"] - 1
                    if i >= 12 else None
                ),
                "food_shortage": ratio(m["unmet_food_demand"], m["food_sold"] + m["unmet_food_demand"]),
                "goods_shortage": ratio(m["unmet_goods_demand"], m["goods_sold"] + m["unmet_goods_demand"]),
                "energy_shortage": ratio(m["energy_unmet"], m["energy_delivered"] + m["energy_unmet"]),
                "housing_gap": m["unhoused_households"] / m["population_count"],
                "trade_balance": e.get("exportations", 0) - e.get("importations", 0),
                "public_spending_share": ratio(m["public_spending"], m["nominal_gdp"]),
                "budget_balance": m["public_revenue"] - m["public_spending"],
                "company_taxes": m["tax_revenue"] - m["household_tax_revenue"] - m["vat_revenue"],
                "wage_bill_estimate": m["average_wage"] * m["employed_count"],
                "rent_estimate": m["housing_rent"] * m["homes_occupied"],
            }
        runs.append(run)
    if len({run["months"] for run in runs}) != 1:
        raise ValueError("Les trajectoires doivent couvrir le même nombre de mois.")
    return runs


@dataclass(frozen=True)
class Metric:
    label: str
    key: str
    unit: str = ""
    scale: float = 1
    digits: int = 1

    def value(self, row):
        record, *keys = self.key.split(".")
        v = row[record]
        for key in keys[:-1]:
            v = v.get(key, {})
        return v.get(keys[-1], 0)

    def display(self, number):
        return "—" if number is None else fr(number * self.scale, self.digits)


def pct(label, key):
    return Metric(label, key, "%", 100)


def money(label, key, millions=False):
    return Metric(label, key, "M u.m." if millions else "k u.m.", 1e-6 if millions else 1e-3, 2)


def count(label, key):
    return Metric(label, key, "", 1, 0)


MACRO = (
    count("Ménages", "market.population_count"),
    count("Ménages employés", "market.employed_count"),
    money("PIB réel mensuel", "market.real_gdp", True),
    money("PIB nominal mensuel", "market.nominal_gdp", True),
    Metric("PIB réel mensuel par ménage", "derived.real_gdp_per_household", "u.m.", 1, 0),
    pct("Chômage", "market.unemployment"),
    pct("Chômage de longue durée · tous ménages", "market.long_term_unemployment"),
    pct("Inflation sur 12 mois", "derived.inflation_12m"),
    Metric("Taux directeur annualisé (× 12)", "market.policy_rate", "%", 1200),
    Metric("Productivité globale des facteurs", "market.tfp_index", "indice", 1, 2),
)
HOUSEHOLDS = (
    Metric("Salaire réel moyen", "market.real_average_wage", "u.m.", 1, 0),
    Metric("Salaire minimum légal", "market.minimum_wage", "u.m.", 1, 0),
    Metric("Loyer de référence", "market.housing_rent", "u.m.", 1, 0),
    pct("Ménages sans logement", "derived.housing_gap"),
    pct("Pauvreté relative", "market.poverty_rate"),
    Metric("Gini du revenu disponible", "market.disposable_income_gini", "indice", 1, 3),
    Metric("Gini du patrimoine", "market.household_gini", "indice", 1, 3),
    pct("Part du patrimoine · 10 % les plus riches", "market.household_top10_share"),
    money("Patrimoine médian nominal", "market.household_wealth_median"),
    Metric("Âge moyen des chefs de ménage", "market.average_age", "ans"),
)
FIRMS = (
    count("Entreprises actives, banques comprises", "market.active_companies"),
    count("Banques actives", "observations.active_by_sector.bank"),
    count("Entreprises cotées actives", "observations.listed"),
    count("Entreprises actives sans salarié, hors banques", "market.idle_companies"),
    count("Groupes (sociétés mères)", "enterprise.groupes"),
    count("Filiales actives", "enterprise.filiales"),
    pct("Part de l’emploi privé dans les groupes", "enterprise.emploi_groupes"),
    count("Magasins", "enterprise.magasins"),
    count("Ménages porteurs de parts de fonds", "enterprise.fonds_porteurs"),
    money("Actif des fonds", "enterprise.fonds_actif", True),
    money("Encours des obligations d’entreprise", "enterprise.obligations_encours", True),
    money("Solde commercial mensuel", "derived.trade_balance"),
    pct("Demande alimentaire non satisfaite", "derived.food_shortage"),
    pct("Demande de biens non satisfaite", "derived.goods_shortage"),
    pct("Demande d’énergie non satisfaite", "derived.energy_shortage"),
)
FINANCE = (
    money("Dépôts", "market.deposits", True),
    money("Portefeuille de prêts bancaires", "market.bank_loan_portfolio", True),
    money("Dette immobilière des ménages", "market.mortgage_debt", True),
    money("Dette de consommation des ménages", "market.consumer_debt", True),
    money("Dette publique", "market.public_debt", True),
    pct("Dette publique / PIB annuel", "state.debt_gdp"),
    pct("Dépenses publiques / PIB mensuel", "derived.public_spending_share"),
    money("Solde budgétaire mensuel (+ = excédent)", "derived.budget_balance"),
    count("Emplois publics", "market.public_employment"),
    pct("Qualité de l’éducation publique", "market.education_quality"),
    money("Arriérés publics", "state.arrears"),
    count("Lois de pouvoir en vigueur", "power.lois_en_vigueur"),
)
GROUPS = (("Activité et emploi", MACRO), ("Ménages et logement", HOUSEHOLDS),
          ("Entreprises et ressources", FIRMS), ("Crédit, budget et institutions", FINANCE))

# Les compteurs épars des relevés entreprise/pouvoir sont nuls quand la clé est absente.
TOTALS = (
    count("Nouveaux ménages", "market.new_households"),
    count("Décès et successions", "market.deaths"),
    money("Droits de succession recouvrés", "market.estate_taxes", True),
    count("Créations par des ménages", "market.startups"),
    count("Faillites", "market.bankruptcies"),
    count("Liquidations amiables", "market.dissolutions"),
    count("Fermetures sans salarié", "enterprise.fermetures_sans_salarie"),
    count("Restructurations", "market.restructurings"),
    count("OPA sur un concurrent du même secteur", "market.takeover_bids"),
    count("Absorptions dans le même secteur", "market.acquisitions"),
    count("Filiales créées", "enterprise.filiales_creees"),
    count("Rachats de groupe", "enterprise.rachats_groupe"),
    count("Reprises par la mère", "enterprise.reprises_par_la_mere"),
    count("Reprises par un concurrent", "enterprise.reprises_par_un_concurrent"),
    count("Entrées en Bourse", "enterprise.introductions"),
    count("Dirigeants révoqués (conseils)", "enterprise.dirigeants_revoques"),
    count("Constructions achevées", "market.construction_completed"),
    count("Saisies immobilières", "market.foreclosures"),
    count("Défauts de crédit à la consommation", "market.consumer_defaults"),
    money("Pertes des déposants en résolution", "market.resolution_depositor_loss", True),
    money("Coût public des résolutions", "market.resolution_public_cost", True),
    money("Émissions obligataires privées", "enterprise.obligations_emissions", True),
    money("Exportations", "enterprise.exportations", True),
    money("Importations", "enterprise.importations", True),
    count("Accords salariaux", "enterprise.accords"),
    count("Grèves", "enterprise.greves"),
    count("Lois de pouvoir votées", "power.lois_votees"),
    count("Lois de pouvoir rejetées", "power.lois_rejetees"),
    count("Lois de pouvoir abrogées", "power.lois_abrogees"),
    money("Aides aux entreprises", "power.aides", True),
    money("Dont aides de faveur", "power.aides_faveur"),
    count("Scandales révélés", "power.affaires_revelees"),
    money("Dons de campagne", "power.dons_campagne"),
    money("Dépenses de lobbying", "power.lobbying"),
    count("Mandats de conseil après le cabinet", "power.pantouflages"),
    count("Héritiers ayant reçu de la notoriété", "power.heritiers_connus"),
)


def values(run, metric):
    return [v for row in run["rows"][1:] if (v := metric.value(row)) is not None]


def table(lines, headings, rows):
    lines.extend(["| " + " | ".join(headings) + " |", "| " + " | ".join(["---"] * len(headings)) + " |"])
    lines.extend("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |" for row in rows)
    lines.append("")


def appendix(runs, target):
    main = runs[0]
    months, seed = main["months"], main["seed"]
    lines = [
        "# Statistiques du scénario complet", "",
        f"**{len(runs)} graines** ({', '.join(str(r['seed']) for r in runs)}), "
        f"120 mois de chauffe et {months} mois observés. Le mois 0 décrit l’état à la fin de la "
        "chauffe. Les moyennes, minimums, maximums et cumuls portent sur les mois observés "
        "et excluent le mois 0. u.m. = unité monétaire simulée. « — » indique une mesure indisponible.", "",
        "[Retour au README et au protocole](../README.md#7-le-scénario-complet-en-chiffres)", "",
        f"## 1. Trajectoire détaillée de la graine {seed}", "",
    ]
    for label, group in GROUPS:
        lines += [f"### {label}", ""]
        rows = []
        for metric in group:
            vs = values(main, metric)
            stats = [min(vs), fmean(vs), max(vs)] if vs else [None] * 3
            rows.append([metric.label, metric.unit, *(metric.display(x) for x in (
                metric.value(main["rows"][0]), *stats, metric.value(main["rows"][-1])
            ))])
        table(lines, ["Indicateur", "Unité", "Mois 0", "Min.", "Moy.", "Max.", f"Mois {months}"], rows)

    lines += ["## 2. Événements et flux cumulés", "",
              "Les absorptions du même secteur et les rachats de groupe sont deux compteurs "
              "distincts : dans le second cas, la cible devient une filiale. Les stocks "
              "et les prix ne sont pas additionnés.", ""]
    table(lines, ["Indicateur", "Unité", *(str(r["seed"]) for r in runs)], (
        [metric.label, metric.unit, *(metric.display(sum(values(run, metric))) for run in runs)]
        for metric in TOTALS
    ))
    kinds = sorted({event["kind"] for run in runs for event in run["resolutions"]})
    tallies = [Counter(event["kind"] for event in run["resolutions"]) for run in runs]
    lines += ["### Événements du registre des résolutions", "",
              "Le tableau compte les événements, qui peuvent concerner plusieurs fois la même banque.", ""]
    table(lines, ["Code du moteur", *(str(r["seed"]) for r in runs)],
          ([kind, *(counter[kind] for counter in tallies)] for kind in kinds))

    lines += ["## 3. Secteurs, flux et services publics", "",
              f"Moyennes des {min(120, months)} derniers mois, graine {seed}. Le chiffre d’affaires "
              "contient des ventes intermédiaires ; la somme des secteurs n’est pas le PIB. "
              "La marge est le bénéfice total de la fenêtre divisé par son chiffre d’affaires.", ""]
    window = main["rows"][-min(120, months):]
    sector_rows = []
    for key, label in SECTORS.items():
        employment = fmean(r["market"]["sector_employment"][key] for r in window)
        if key == "public":
            sector_rows.append([label, "—", fr(employment), "—", "—"])
            continue
        revenue = fmean(r["market"]["sector_revenue"][key] for r in window)
        profit = fmean(r["market"]["sector_profit"][key] for r in window)
        active = fmean(r["observations"]["active_by_sector"][key] for r in window)
        sector_rows.append([label, fr(revenue / 1e3), fr(employment), fr(active),
                            fr(100 * profit / revenue) if revenue > 0 else "—"])
    table(lines, ["Secteur", "CA (k u.m./mois)", "Emplois", "Entreprises actives", "Marge (%)"], sector_rows)
    table(lines, ["Service public", "Agents", "Postes autorisés", "Postes vacants", "Couverture des besoins (%)"], (
        [label, *(fr(fmean(r["state"]["services"][key][f] for r in window) * factor)
                  for f, factor in (("staff", 1), ("authorized", 1), ("vacancies", 1), ("coverage", 100)))]
        for key, label in SERVICE_LABELS.items()
    ))
    lines += ["### Flux du livre de l’État", "", "Signe du livre : recette positive, décaissement négatif.", ""]
    flows = sorted({k for row in window for k in row["state"]["flows"]})
    table(lines, ["Ligne", "Moyenne (k u.m./mois)"], (
        [FLOW_LABELS.get(key, "Droits de succession" if key == "droits_succession" else key),
         fr(fmean(r["state"]["flows"].get(key, 0) for r in window) / 1e3, 2)]
        for key in flows
    ))

    lines += ["## 4. Élections et objectifs", "",
              "Les bilans portent sur les mandats clos ; un mandat entamé en chauffe peut se clore "
              "pendant la période observée. L’élection au mois 0 appartient à la chauffe.", ""]
    table(lines, ["Graine", "Mois", "Voix du sortant (%)", "Sortant réélu", "Orientation de l’élu (/100)"], (
        [run["seed"], e["period"], fr(100 * e["part_sortant"]), "Oui" if e["reelu"] else "Non", fr(e["elu"])]
        for run in runs for e in run["elections"]
    ))
    statuses = ("atteint", "partiel", "manqué")
    outcomes = []
    for run in runs:
        for rank, label in (("principal", "Principaux"), ("secondary", "Secondaires")):
            totals = Counter(status for t in run["terms"] if t["to"] is not None for status in t[rank].values())
            outcomes.append([run["seed"], label, *(totals[s] for s in statuses)])
    table(lines, ["Graine", "Objectifs", *[s.capitalize() for s in statuses]], outcomes)
    objectives = main["rows"][-1]["objectives"]
    lines += [f"Objectifs en cours à la fin du mois {months}, graine {seed} :", ""]
    table(lines, ["Ministère", "Rang", "Indicateur", "Avancement (%)", "État"], (
        [OFFICE_LABELS.get(role, role), rank, goal["label"], fr(100 * goal["progress"]), goal["status"]]
        for role, office in objectives.items() for rank, goal in office.items()
    ))

    lines += ["## 5. Rapprochements comptables", "",
              "Maximum de la valeur absolue sur les mois observés, en unités monétaires. "
              "Ces tests comparent des objets différents ; un écart non nul sur les titres publics "
              "doit être lu séparément du résidu monétaire (voir l’anomalie OPT-BANK-001 dans le README).", ""]
    table(lines, ["Contrôle", *(str(r["seed"]) for r in runs)], (
        [label, *(f"{max(abs(Metric(label, key).value(r)) for r in run['rows'][1:]):.3g}"
                  for run in runs)]
        for label, key in (
            ("Monnaie fiduciaire et dépôts", "market.ledger_residual"),
            ("Dettes des emprunteurs et créances bancaires", "market.loan_reconciliation_error"),
            ("Dette publique et titres détenus", "market.public_debt_reconciliation_error"),
            ("Trésorerie de l’État et flux du mois", "state.cash_residual"),
            ("Variation de la dette publique et financement", "state.debt_residual"),
        )
    ))
    for run in runs:
        peak = max(run["rows"][1:], key=lambda row: abs(row["state"]["cash_residual"]))
        residual = peak["state"]["cash_residual"]
        if abs(residual) >= 1:
            lines.extend([
                f"**Graine {run['seed']}, mois {peak['market']['month']} :** écart de trésorerie de "
                f"**{fr(residual / 1e6, 2)} M u.m.** entre la variation du compte de l’État "
                "et celle expliquée par les flux du livre et le financement. Ce relevé "
                "ne permet pas d’attribuer l’écart à une opération précise.", "",
            ])

    lines += ["## 6. Autres séries du moteur", "",
              f"Graine {seed}. Noms techniques tels qu’ils figurent dans le CSV, hors calculs dérivés ; "
              "les taux bruts sont des fractions (0,01 = 1 %). Les cellules absentes des flux "
              "événementiels sont agrégées comme des zéros. Les cours par entreprise, les objectifs "
              "par portefeuille et les champs textuels restent dans le CSV/JSON.", ""]
    expanded = [dict(flat(row)) for row in main["rows"]]
    documented = {m.key for _, group in GROUPS for m in group} | {m.key for m in TOTALS}
    numeric_keys = sorted({key for row in expanded for key, v in row.items() if type(v) in (int, float)})
    excluded = {"market.month", "state.month", "power.mois", "enterprise.mois"}
    for family, title in (("market", "Marchés"), ("state", "État"), ("enterprise", "Entreprises"),
                          ("power", "Pouvoir"), ("observations", "Structure"), ("derived", "Calculs dérivés")):
        extra = []
        for key in numeric_keys:
            if (not key.startswith(family + ".") or key in excluded or key in documented
                    or key.startswith("market.company_prices.")):
                continue
            vs = [row.get(key, 0) for row in expanded[1:] if row.get(key, 0) is not None]
            if not vs:
                continue
            digits = 4 if max(abs(x) for x in vs) < 10 else 1
            final = expanded[-1].get(key, 0)
            extra.append([f"`{key.split('.', 1)[1]}`", *(fr(v, digits) for v in (min(vs), fmean(vs), max(vs))),
                          "—" if final is None else fr(final, digits)])
        if extra:
            lines += [f"### {title}", ""]
            table(lines, ["Série", "Min.", "Moy.", "Max.", f"Mois {months}"], extra)
    target.write_text("\n".join(lines), encoding="utf-8")


def decorate(fig, number, title, subtitle):
    fig.text(0.06, 0.964, f"ÉCONOMIE EN MOUVEMENT   /   {number:02d}", color=BLUE,
             weight="bold", fontsize=10, va="top")
    fig.text(0.06, 0.922, title, fontsize=22, weight="bold", va="top")
    fig.text(0.06, 0.863, subtitle, fontsize=10.1, color=MUTED, va="top")


def save(fig, output, name):
    fig.savefig(output / f"complet_{name}.png")
    plt.close(fig)


def chart_grid(runs, output, number, name, title, panels):
    months = runs[0]["months"]
    x = list(range(months + 1))
    fig, axes = plt.subplots(3, 2, figsize=(11.6, 8.1), sharex=True)
    fig.subplots_adjust(top=0.80, bottom=0.13, left=0.09, right=0.97, hspace=0.52, wspace=0.27)
    decorate(fig, number, title, f"Mois depuis la fin de la chauffe · {len(runs)} graines · "
             "trait : médiane mensuelle · bande : minimum–maximum")
    for ax, (metric, color, reference, step) in zip(axes.flat, panels):
        series = [[None if (v := metric.value(r)) is None else v * metric.scale
                   for r in run["rows"]] for run in runs]
        cols = list(zip(*series))
        xs = [i for i, col in enumerate(cols) if all(v is not None for v in col)]
        lo, hi = [min(cols[i]) for i in xs], [max(cols[i]) for i in xs]
        ys = [median(cols[i]) for i in xs]
        ax.fill_between(xs, lo, hi, color=color, alpha=0.17, step="post" if step else None, linewidth=0)
        ax.plot(xs, ys, color=color, drawstyle="steps-post" if step else "default")
        if reference is not None:
            ax.axhline(reference, color=MUTED, ls="--", lw=0.9, alpha=0.8)
        ax.set_title(metric.label + (f" · {metric.unit}" if metric.unit else ""), loc="left", pad=8)
        ax.set_xlim(0, x[-1])
        ax.yaxis.set_major_locator(MaxNLocator(4, integer=not metric.unit and metric.digits == 0))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: fr(value, 2).rstrip("0").rstrip(",")))
        ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
        ax.grid(axis="x", visible=False)
        ax.tick_params(axis="both", length=0)
    for ax in axes[-1]:
        ax.set_xlabel("Mois observé", labelpad=9)
    fig.text(0.06, 0.037, "Scénario complet  ·  500 ménages au départ  ·  +0,5 %/mois  ·  plafond : 1 000",
             color=MUTED, fontsize=9.5)
    save(fig, output, name)


def bars(runs, output):
    main = runs[0]
    length = min(120, main["months"])
    window = main["rows"][-length:]
    subtitle = f"Graine {main['seed']} · moyenne des {length} derniers mois observés"
    fig, ax = plt.subplots(figsize=(11.6, 7.4))
    fig.subplots_adjust(top=0.79, bottom=0.16, left=0.35, right=0.93)
    decorate(fig, 2, "Qui paie quoi chaque mois ?", subtitle + " · millions d’unités monétaires")
    flow_specs = [
        ("Salaires versés (estimation)", "derived.wage_bill_estimate", BLUE),
        ("Loyers privés (estimation)", "derived.rent_estimate", GREEN),
        ("Achats de services par les ménages", "market.household_services", GREEN),
        ("Impôt sur le revenu", "market.household_tax_revenue", PURPLE),
        ("TVA", "market.vat_revenue", PURPLE),
        ("Impôt sur les bénéfices", "derived.company_taxes", PURPLE),
        ("Dividendes", "market.dividends_paid", BLUE),
        ("Allocations chômage", "market.benefits_paid", AMBER),
        ("Transferts sociaux", "market.public_transfers", AMBER),
        ("Achats publics", "market.government_purchases", AMBER),
        ("Investissement et construction", "market.investment", BLUE),
        ("Exportations", "enterprise.exportations", RED),
        ("Importations", "enterprise.importations", GREEN),
    ]
    rows = sorted((fmean(Metric(label, key).value(r) for r in window) / 1e6, label, color)
                  for label, key, color in flow_specs)
    amounts, labels, colors = zip(*rows)
    rects = ax.barh(labels, amounts, color=colors, height=0.58)
    ax.bar_label(rects, labels=["< 0,01" if 0 < amount < 0.005 else fr(amount, 2) for amount in amounts],
                 padding=5, fontsize=9.5)
    ax.set_xlim(0, max(amounts) * 1.17 or 1)
    ax.set_xlabel("M u.m. / mois")
    ax.grid(axis="y", visible=False)
    ax.tick_params(length=0)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fr(v, 1)))
    fig.text(0.06, 0.036, "Flux de natures différentes : plusieurs lignes se recoupent. "
             "La somme des barres n’est pas le PIB.", color=MUTED, fontsize=9.5)
    save(fig, output, "flux")

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 5.5), sharey=True)
    fig.subplots_adjust(top=0.77, bottom=0.17, left=0.22, right=0.95, wspace=0.21)
    decorate(fig, 3, "L’emploi et les ventes, secteur par secteur", subtitle)
    keys = list(SECTORS)
    revenue = [fmean(r["market"]["sector_revenue"].get(k, 0) for r in window) / 1e6 for k in keys]
    jobs = [fmean(r["market"]["sector_employment"][k] for r in window) for k in keys]
    for ax, vs, title, digits in zip(axes, (revenue, jobs), ("Chiffre d’affaires · M u.m./mois", "Ménages employés"), (2, 0)):
        bars = ax.barh(range(len(keys)), vs, color=[SECTOR_COLORS[k] for k in keys], height=0.56)
        texts = [fr(v, digits) if v or k != "public" else "—" for k, v in zip(keys, vs)]
        ax.bar_label(bars, labels=texts, padding=5, fontsize=9)
        ax.set_title(title, loc="left", pad=10)
        ax.set_xlim(0, max(vs) * 1.2 or 1)
        ax.grid(axis="y", visible=False)
        ax.tick_params(length=0)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _, d=digits: fr(v, min(d, 1))))
    axes[0].set_yticks(range(len(keys)), list(SECTORS.values()))
    axes[0].invert_yaxis()
    fig.text(0.06, 0.046, "Les ventes intermédiaires font partie du chiffre d’affaires. "
             "La fonction publique n’a pas de chiffre d’affaires.", color=MUTED, fontsize=9.5)
    save(fig, output, "secteurs")


def figures(runs, output):
    inflation_goal = 100 * ((1 + runs[0]["config"]["inflation_target"]) ** 12 - 1)
    chart_grid(runs, output, 1, "chronologie", "De la croissance démographique à l’activité", (
        (MACRO[0], BLUE, 1000, True), (MACRO[2], BLUE, None, False),
        (MACRO[4], GREEN, None, False), (MACRO[5], RED, None, False),
        (MACRO[7], AMBER, inflation_goal, False), (MACRO[8], PURPLE, None, False),
    ))
    bars(runs, output)
    chart_grid(runs, output, 4, "credit", "Dépôts, crédit et financement public", (
        (FINANCE[0], BLUE, None, False), (FINANCE[1], PURPLE, None, False),
        (FINANCE[2], GREEN, None, False), (FINANCE[3], AMBER, None, False),
        (FINANCE[5], RED, None, False), (FINANCE[7], BLUE, 0, False),
    ))
    chart_grid(runs, output, 5, "menages", "Revenus, patrimoine et logement", (
        (HOUSEHOLDS[0], BLUE, None, False), (HOUSEHOLDS[4], RED, None, False),
        (HOUSEHOLDS[5], GREEN, None, False), (HOUSEHOLDS[6], PURPLE, None, False),
        (HOUSEHOLDS[7], AMBER, None, False), (HOUSEHOLDS[3], RED, None, False),
    ))
    chart_grid(runs, output, 6, "entreprises", "Entreprises, groupes et débouchés", (
        (FIRMS[0], BLUE, None, True), (FIRMS[5], GREEN, None, True),
        (FIRMS[9], PURPLE, None, False), (FIRMS[10], BLUE, None, False),
        (FIRMS[11], AMBER, 0, False), (FIRMS[14], RED, None, False),
    ))
    chart_grid(runs, output, 7, "institutions", "Gouvernement, propriété et services publics", (
        (Metric("Orientation du gouvernement", "market.government_orientation", "/100"), BLUE, 50, True),
        (Metric("Régime de propriété effectif", "observations.ownership_regime", "/100", 100), PURPLE, 50, False),
        (FINANCE[8], GREEN, None, True), (FINANCE[9], BLUE, None, False),
        (FINANCE[11], AMBER, None, True), (money("Aides aux entreprises", "power.aides"), RED, None, False),
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=OUT)
    parser.add_argument("--seeds", nargs="+", type=int, default=SEEDS)
    parser.add_argument("--docs", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    runs = load_runs(args.input, args.seeds)
    output = args.docs / "images"
    output.mkdir(parents=True, exist_ok=True)
    figures(runs, output)
    appendix(runs, args.docs / "statistiques_scenario_complet.md")


if __name__ == "__main__":
    main()
