"""Graphiques du README (docs/images/*.png).

python docs/figures_readme.py            (nécessite matplotlib : requirements-docs.txt)

Deux sources :
- la campagne gauche/droite (``experiences/gauche_droite_campagne.py`` puis
  ``experiences/gauche_droite_analyse.py``), lue dans results/gauche_droite/ :
  10 graines × 3 scénarios × 400 mois ;
- une simulation témoin mensuelle (graine 1001, 400 mois après la chauffe), mise en cache
  dans results/readme/temoin_1001.json : flux mensuels, secteurs, chronologie.
Aucun paramètre du moteur n'est modifié.
"""

import json
import os
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "images")
CACHE = os.path.join(ROOT, "results", "readme")
RUNS = os.path.join(ROOT, "results", "gauche_droite", "runs.jsonl")
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

# Palette : droite en bleu, gauche en rouge (convention politique française), témoin en gris.
COL = {"droite": "#2a78d6", "temoin": "#52514e", "gauche": "#e34948"}
NOM = {"droite": "Droite (0/100)", "temoin": "Témoin (élections)", "gauche": "Gauche (100/100)"}
ORDRE = ("droite", "temoin", "gauche")
SECTEURS = {
    "food": "Alimentation", "goods": "Biens courants", "services": "Services",
    "technology": "Technologie", "energy": "Énergie", "materials": "Matières premières",
    "real_estate": "Immobilier", "bank": "Banque",
}
COL_SECT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"

plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 150, "figure.facecolor": "#fcfcfb",
    "axes.facecolor": "#fcfcfb", "savefig.facecolor": "#fcfcfb",
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "xtick.color": MUTED, "ytick.color": MUTED,
    "font.size": 9.5, "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "lines.linewidth": 2.0, "legend.frameon": False,
})


def fr(x, nd=1):
    """Nombre au format français (virgule décimale, espace fine pour les milliers)."""
    s = f"{x:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s


def save(fig, name):
    fig.savefig(os.path.join(OUT, name), bbox_inches="tight")
    plt.close(fig)
    print("écrit", name)


# ----------------------------------------------------------------------------------------------
# 1. Simulation témoin mensuelle (cache)
# ----------------------------------------------------------------------------------------------
CHAMPS = (
    "month", "real_gdp", "nominal_gdp", "unemployment", "inflation_rate", "consumer_price_index",
    "government_orientation", "policy_rate", "household_income", "household_spending",
    "household_services", "vat_revenue", "household_tax_revenue", "tax_revenue", "benefits_paid",
    "public_transfers", "government_purchases", "public_spending", "public_revenue",
    "dividends_paid", "bank_interest_income", "investment", "housing_rent", "homes_occupied",
    "public_employment", "employed_count", "population_count", "money_supply", "deposits",
    "bank_loan_portfolio", "public_debt", "mortgage_debt", "consumer_debt", "company_debt",
    "stock_market_value", "average_wage", "minimum_wage", "poverty_rate", "active_companies",
    "real_average_wage", "estate_taxes", "public_interest",
)


def temoin_mensuel(seed=1001, months=400):
    path = os.path.join(CACHE, f"temoin_{seed}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    from economy.models import SimulationConfig
    from economy.simulation import EconomySimulation

    sim = EconomySimulation(SimulationConfig(seed=seed))
    rows = []
    for _ in range(months):
        r = sim.step()
        row = {k: getattr(r, k) for k in CHAMPS}
        row["sector_revenue"] = dict(r.sector_revenue)
        row["sector_employment"] = dict(r.sector_employment)
        rows.append(row)
    data = {"seed": seed, "months": months, "rows": rows}
    json.dump(data, open(path, "w", encoding="utf-8"))
    return data


tm = temoin_mensuel()
rows = tm["rows"]
annees = [i / 12 for i in range(len(rows))]
dernier = rows[-120:]  # dix dernières années


def moy(key, sample=dernier):
    return statistics.fmean(r[key] for r in sample)


# --- Flux mensuels moyens (10 dernières années)
masse_salariale = statistics.fmean(r["average_wage"] * r["employed_count"] for r in dernier)
ca = {k: statistics.fmean(r["sector_revenue"].get(k, 0.0) for r in dernier) for k in SECTEURS}
loyers = statistics.fmean(r["housing_rent"] * r["homes_occupied"] for r in dernier)
impot_societes = statistics.fmean(
    r["tax_revenue"] - r["household_tax_revenue"] - r["vat_revenue"] for r in dernier)
flux = [
    ("Salaires versés (≈ salaire moyen × emplois)", masse_salariale, "Entreprises & État → ménages"),
    ("Achats d'alimentation", ca["food"], "Ménages → entreprises"),
    ("Achats de biens courants", ca["goods"], "Ménages → entreprises"),
    ("Chiffre d'affaires des services", ca["services"], "Ménages & entreprises → entreprises"),
    ("Loyers (≈ loyer × logements loués)", loyers, "Ménages → bailleurs"),
    ("Dividendes", moy("dividends_paid"), "Entreprises → actionnaires"),
    ("Investissement (entreprises + construction)", moy("investment"), "Entreprises → fournisseurs"),
    ("TVA", moy("vat_revenue"), "Ménages → État"),
    ("Impôt sur le revenu", moy("household_tax_revenue"), "Ménages → État"),
    ("Impôt sur les bénéfices", impot_societes, "Entreprises → État"),
    ("Allocations chômage", moy("benefits_paid"), "État → ménages"),
    ("Transferts sociaux", moy("public_transfers"), "État → ménages"),
    ("Achats publics", moy("government_purchases"), "État → entreprises"),
    ("Revenus des banques (intérêts, frais)", moy("bank_interest_income"), "Emprunteurs → banques"),
]
json.dump({"flux_mensuels_moyens": {n: v for n, v, _ in flux},
           "chiffre_affaires_par_secteur": ca,
           "pib_nominal_mensuel": moy("nominal_gdp"),
           "emplois_par_secteur": {k: statistics.fmean(r["sector_employment"].get(k, 0) for r in dernier)
                                    for k in SECTEURS},
           "emploi_public": moy("public_employment"),
           "population": moy("population_count")},
          open(os.path.join(CACHE, "flux_temoin_1001.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

couleur_flux = {"Ménages": "#1baf7a", "Entreprises": "#2a78d6", "État": "#6250d6",
                "Emprunteurs": "#eda100"}
fig, ax = plt.subplots(figsize=(9, 5.6))
flux_tri = sorted(flux, key=lambda t: t[1])
y = range(len(flux_tri))
for i, (nom, val, sens) in enumerate(flux_tri):
    payeur = sens.split(" ")[0]
    ax.barh(i, val / 1e6, color=couleur_flux.get(payeur, MUTED), height=0.62)
    ax.text(val / 1e6, i, f"  {fr(val / 1e6, 2)} M  ·  {sens}", va="center", fontsize=8.2, color=INK2)
ax.set_yticks(list(y), [t[0] for t in flux_tri])
ax.set_xlim(0, max(t[1] for t in flux) / 1e6 * 1.75)
ax.set_xlabel("Montant mensuel moyen (millions d'unités monétaires simulées)")
ax.grid(axis="y", visible=False)
ax.set_title("Qui paie qui ? Flux mensuels moyens (témoin, graine 1001, 10 dernières années)",
             loc="left")
handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in couleur_flux.values()]
ax.legend(handles, [f"payé par : {k}" for k in couleur_flux], loc="lower right", fontsize=8)
save(fig, "flux_mensuels.png")

# --- Secteurs : chiffre d'affaires et emplois
emplois = {k: statistics.fmean(r["sector_employment"].get(k, 0) for r in dernier) for k in SECTEURS}
emplois_public = moy("public_employment")
fig, axes = plt.subplots(1, 2, figsize=(10, 3.9))
keys = list(SECTEURS)
noms = [SECTEURS[k] for k in keys]
axes[0].barh(noms, [ca[k] / 1000 for k in keys], color=COL_SECT, height=0.62)
axes[0].set_title("Chiffre d'affaires mensuel (k)", loc="left")
axes[0].invert_yaxis()
axes[1].barh(noms + ["Fonction publique"], [emplois[k] for k in keys] + [emplois_public],
             color=list(COL_SECT) + [MUTED], height=0.62)
axes[1].set_title("Emplois (ménages salariés)", loc="left")
axes[1].invert_yaxis()
for a in axes:
    a.grid(axis="y", visible=False)
fig.suptitle("Les huit secteurs (témoin, graine 1001, moyenne des 10 dernières années)",
             x=0.01, ha="left", fontsize=11, fontweight="bold")
fig.tight_layout()
save(fig, "secteurs.png")


# --- Chronologie d'une simulation témoin
def lisse(v, w=12):
    return [statistics.fmean(v[max(0, i - w + 1): i + 1]) for i in range(len(v))]


fig, axes = plt.subplots(4, 1, figsize=(9.5, 8.4), sharex=True)
axes[0].plot(annees, lisse([r["real_gdp"] / 1e6 for r in rows]), color="#2a78d6")
axes[0].set_ylabel("PIB réel (M, lissé 12 mois)")
axes[1].plot(annees, [100 * r["unemployment"] for r in rows], color="#eb6834", lw=1.2)
axes[1].set_ylabel("Chômage (%)")
infl = [None] * 12 + [100 * (rows[i]["consumer_price_index"] / rows[i - 12]["consumer_price_index"] - 1)
                      for i in range(12, len(rows))]
axes[2].plot(annees[12:], infl[12:], color="#1baf7a", lw=1.2)
axes[2].axhline(100 * (1.002 ** 12 - 1), color=MUTED, ls="--", lw=0.9)
axes[2].text(0.3, 100 * (1.002 ** 12 - 1) + 0.6, "cible ≈ 2,4 %", color=MUTED, fontsize=8)
axes[2].set_ylabel("Inflation sur 12 mois (%)")
orient = [r["government_orientation"] for r in rows]
axes[3].fill_between(annees, 50, orient, where=[o >= 50 for o in orient], color="#e34948",
                     alpha=0.35, step="post", lw=0)
axes[3].fill_between(annees, 50, orient, where=[o < 50 for o in orient], color="#2a78d6",
                     alpha=0.35, step="post", lw=0)
axes[3].step(annees, orient, where="post", color=INK2, lw=1.4)
axes[3].set_ylim(0, 100)
axes[3].axhline(50, color=MUTED, lw=0.8)
axes[3].set_yticks([0, 25, 50, 75, 100], ["0 libéral", "25", "50 centre", "75", "100 social"])
axes[3].set_ylabel("Gouvernement élu")
axes[3].set_xlabel("Années observées (après 10 ans de chauffe)")
axes[0].set_title("Une économie sans intervention : 33 ans, élections tous les 5 ans (graine 1001)",
                  loc="left")
fig.tight_layout()
save(fig, "chronologie_temoin.png")

# --- Monnaie et dettes
fig, ax = plt.subplots(figsize=(9.5, 4.2))
for key, nom, c in (("deposits", "Dépôts bancaires (monnaie créée par le crédit)", "#2a78d6"),
                    ("bank_loan_portfolio", "Prêts bancaires", "#eb6834"),
                    ("mortgage_debt", "Dette immobilière des ménages", "#1baf7a"),
                    ("public_debt", "Dette publique", "#6250d6")):
    ax.plot(annees, [r[key] / 1e6 for r in rows], color=c, label=nom)
ax.set_ylabel("Millions (nominal)")
ax.set_xlabel("Années observées")
ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2)
ax.set_title("La monnaie naît du crédit : dépôts, prêts et dettes (témoin, graine 1001)", loc="left")
save(fig, "monnaie_credit.png")

# ----------------------------------------------------------------------------------------------
# 2. Campagne gauche / droite / témoin
# ----------------------------------------------------------------------------------------------
runs = [json.loads(line) for line in open(RUNS, encoding="utf-8")]
by = {(r["seed"], r["scenario"]): r for r in runs}
seeds = sorted({r["seed"] for r in runs})
N_ANS = runs[0]["months"] // 12  # 33 années complètes


def serie(scen, fn):
    """Matrice graines × années d'un indicateur annuel."""
    return [[fn(y) for y in by[(s, scen)]["years"][:N_ANS]] for s in seeds]


def bande(mat):
    cols = list(zip(*mat))
    med = [statistics.median(c) for c in cols]
    q = [statistics.quantiles(c, n=4) for c in cols]
    return med, [x[0] for x in q], [x[2] for x in q]


x = list(range(1, N_ANS + 1))
panneaux = [
    ("PIB réel mensuel (M)", lambda y: y["real_gdp"] / 1e6),
    ("Chômage (%)", lambda y: 100 * y["unemployment"]),
    ("Salaire réel moyen", lambda y: y["real_average_wage"]),
    ("Taux de pauvreté (%)", lambda y: 100 * y["poverty_rate"]),
    ("Gini du revenu disponible", lambda y: y["disposable_income_gini"]),
    ("Part des 10 % les plus riches (%)", lambda y: 100 * y["household_top10_share"]),
    ("Dépenses publiques / PIB (%)", lambda y: 100 * y["public_spending"] / max(y["nominal_gdp"], 1)),
    ("Dette publique / PIB annuel (%)", lambda y: 100 * y["public_debt_end"] / max(y["nominal_gdp_sum"], 1)),
    ("Entreprises actives", lambda y: y["active_companies"]),
]
fig, axes = plt.subplots(3, 3, figsize=(12, 9.4))
for ax, (titre, fn) in zip(axes.flat, panneaux):
    for scen in ORDRE:
        med, lo, hi = bande(serie(scen, fn))
        ax.fill_between(x, lo, hi, color=COL[scen], alpha=0.13, lw=0)
        ax.plot(x, med, color=COL[scen], label=NOM[scen], lw=1.8)
    ax.set_title(titre, loc="left", fontsize=10)
    ax.set_xlim(1, N_ANS)
for ax in axes[-1]:
    ax.set_xlabel("Année")
axes[0][0].legend(fontsize=8, loc="upper left")
fig.suptitle("Gouvernements toujours de droite, toujours de gauche, ou élus : trajectoires sur 33 ans\n"
             "médiane de 10 graines, bande = quartiles (25 %–75 %)",
             x=0.01, ha="left", fontsize=12, fontweight="bold")
fig.tight_layout()
save(fig, "gd_trajectoires.png")

# --- Indicateurs moyens (barres) : lit la synthèse de l'analyse existante
synth = json.load(open(os.path.join(ROOT, "results", "gauche_droite", "synthese.json"), encoding="utf-8"))
choix = [
    ("PIB réel mensuel (moyenne)", "PIB réel mensuel (M)", 1e-6, 2),
    ("Productivité (PGF, fin)", "Productivité (indice, fin)", 1, 2),
    ("Salaire réel moyen (10 dernières années)", "Salaire réel moyen\n(10 dernières années)", 1, 0),
    ("Chômage", "Chômage (%)", 100, 1),
    ("Inflation annuelle", "Inflation annuelle (%)", 100, 1),
    ("Taux de pauvreté", "Taux de pauvreté (%)", 100, 1),
    ("Gini du revenu disponible", "Gini du revenu disponible", 1, 2),
    ("Gini du patrimoine", "Gini du patrimoine", 1, 2),
    ("Patrimoine médian réel (10 dernières années)", "Patrimoine médian réel (k)\n(10 dernières années)", 1e-3, 0),
    ("Dépenses publiques / PIB", "Dépenses publiques / PIB (%)", 100, 1),
    ("Faillites par an", "Faillites par an", 1, 1),
    ("Entreprises actives", "Entreprises actives", 1, 0),
]
fig, axes = plt.subplots(3, 4, figsize=(12, 8.2))
for ax, (cle, titre, k, nd) in zip(axes.flat, choix):
    vals = [synth[cle][s] * k for s in ORDRE]
    bars = ax.bar(range(3), vals, color=[COL[s] for s in ORDRE], width=0.62)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, fr(v, nd), ha="center", va="bottom",
                fontsize=8.5, color=INK)
    ax.set_xticks(range(3), ["Droite", "Témoin", "Gauche"])
    ax.set_ylim(0, max(vals) * 1.2)
    ax.set_title(titre, loc="left", fontsize=9.5)
    ax.grid(axis="x", visible=False)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
fig.suptitle("Moyennes sur 33 ans et 10 graines — droite (0/100), témoin élu, gauche (100/100)",
             x=0.01, ha="left", fontsize=12, fontweight="bold")
fig.tight_layout()
save(fig, "gd_indicateurs.png")

# --- Écarts appariés graine par graine (gauche − droite, en % de la droite)
par = synth["_par_graine"]
appar = [
    ("PIB réel mensuel (moyenne)", "PIB réel"),
    ("Productivité (PGF, fin)", "Productivité (fin)"),
    ("Salaire réel moyen (10 dernières années)", "Salaire réel (10 dern. ans)"),
    ("Revenu réel des ménages", "Revenu réel des ménages"),
    ("Chômage", "Chômage"),
    ("Taux de pauvreté", "Taux de pauvreté"),
    ("Gini du revenu disponible", "Gini revenu disponible"),
    ("Patrimoine médian réel (10 dernières années)", "Patrimoine médian réel"),
    ("Entreprises actives", "Entreprises actives"),
    ("Faillites par an", "Faillites par an"),
]
fig, ax = plt.subplots(figsize=(9.5, 5.2))
for i, (cle, nom) in enumerate(appar):
    ecarts = []
    for s in seeds:
        d, g = par["droite"][str(s)][cle], par["gauche"][str(s)][cle]
        ecarts.append(100 * (g - d) / abs(d) if abs(d) > 1e-12 else 0.0)
    ys = [i + (j - 4.5) * 0.035 for j in range(len(ecarts))]
    ax.scatter(ecarts, ys, s=26, color=["#e34948" if e > 0 else "#2a78d6" for e in ecarts],
               edgecolor="#fcfcfb", linewidth=0.8, zorder=3)
    med = statistics.median(ecarts)
    ax.plot([med, med], [i - 0.3, i + 0.3], color=INK, lw=2, zorder=4)
    ax.text(185, i, f"médiane {'+' if med > 0 else ''}{fr(med, 0)} %", va="center", fontsize=8, color=INK2)
ax.axvline(0, color="#c3c2b7", lw=1)
ax.set_yticks(range(len(appar)), [n for _, n in appar])
ax.invert_yaxis()
ax.set_xlim(-110, 230)
ax.set_xlabel("Écart gauche − droite, en % de la valeur à droite (un point = une graine)")
ax.grid(axis="y", visible=False)
ax.set_title("Même graine, même départ : ce que change un gouvernement de gauche plutôt que de droite",
             loc="left")
ax.text(-108, len(appar) - 0.2, "← plus bas à gauche", color="#2a78d6", fontsize=8.5, va="top")
ax.text(5, len(appar) - 0.2, "plus haut à gauche →", color="#e34948", fontsize=8.5, va="top")
save(fig, "gd_ecarts_apparies.png")

# --- Orientation des gouvernements élus dans le témoin (10 graines)
fig, ax = plt.subplots(figsize=(9.5, 3.4))
for s in seeds:
    o = [y["government_orientation"] for y in by[(s, "temoin")]["years"][:N_ANS]]
    ax.step(x, o, where="mid", color=MUTED, lw=1.1, alpha=0.8)
ax.axhline(0, color=COL["droite"], lw=2.2, label="Droite imposée (0)")
ax.axhline(100, color=COL["gauche"], lw=2.2, label="Gauche imposée (100)")
ax.set_ylim(-5, 105)
ax.set_xlim(1, N_ANS)
ax.set_ylabel("Orientation")
ax.set_xlabel("Année")
ax.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
ax.set_title("Témoin : orientation des gouvernements élus par les ménages (une ligne grise = une graine)",
             loc="left")
save(fig, "gd_orientations_temoin.png")
print("terminé")
