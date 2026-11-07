"""Génère cinq graphiques courants (results/figures/*.png) à partir d'une simulation de 800 mois.

Usage : python docs/make_charts.py [graine]     (nécessite matplotlib)
"""

import os
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from economy.models import SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 7
MONTHS = 800
OUT = os.path.join(ROOT, "results", "figures")
os.makedirs(OUT, exist_ok=True)
LABELS = {
    "food": "Alimentation", "goods": "Biens courants", "technology": "Technologie",
    "real_estate": "Immobilier", "materials": "Matières premières", "energy": "Énergie",
    "bank": "Banque", "services": "Services",
}
COLORS = {
    "food": "#4C9F70", "goods": "#7FB77E", "technology": "#3B6FB6", "real_estate": "#C97B4A",
    "materials": "#8C7A6B", "energy": "#E2B33C", "bank": "#8E5DA8", "services": "#5BA8A0",
}
plt.rcParams.update({
    "figure.dpi": 140, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25, "font.size": 10,
})

sim = EconomySimulation(SimulationConfig(seed=SEED))
records = [sim.step() for _ in range(MONTHS)]
months = [r.month for r in records]
years = [m / 12 for m in months]


def smooth(values, window=12):
    return [statistics.mean(values[max(0, i - window + 1): i + 1]) for i in range(len(values))]


# 1. Répartition du chiffre d'affaires par secteur
fig, ax = plt.subplots(figsize=(9, 4.2))
series = []
for key in LABELS:
    series.append(smooth([r.sector_revenue.get(key, 0.0) for r in records]))
totals = [sum(col) or 1.0 for col in zip(*series)]
shares = [[100 * v / t for v, t in zip(s, totals)] for s in series]
ax.stackplot(years, shares, labels=list(LABELS.values()), colors=[COLORS[k] for k in LABELS], alpha=0.92)
ax.set_xlim(0, MONTHS / 12); ax.set_ylim(0, 100)
ax.set_xlabel("Années simulées"); ax.set_ylabel("Part du chiffre d'affaires (%)")
ax.set_title("Part du chiffre d’affaires par secteur")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=4, frameon=False)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "secteurs.png")); plt.close(fig)

# 2. Macro : PIB réel, chômage, inflation
fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
axes[0].plot(years, smooth([r.real_gdp for r in records]), color="#3B6FB6")
axes[0].set_ylabel("PIB réel (lissé)")
axes[1].plot(years, [100 * r.unemployment for r in records], color="#C0504D", lw=0.9)
axes[1].set_ylabel("Chômage (%)")
infl = [None] * 12 + [
    100 * (records[i].consumer_price_index / records[i - 12].consumer_price_index - 1)
    for i in range(12, MONTHS)
]
axes[2].plot(years[12:], infl[12:], color="#E2B33C", lw=0.9)
axes[2].axhline(2.4, color="grey", ls="--", lw=0.8); axes[2].text(1, 3.0, "cible ≈ 2,4 %", color="grey", fontsize=8)
axes[2].set_ylabel("Inflation sur 12 mois (%)"); axes[2].set_xlabel("Années simulées")
axes[0].set_title("PIB réel, chômage et inflation")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "macro.png")); plt.close(fig)

# 3. Marges par secteur vs cible
window = records[-300:]
fig, ax = plt.subplots(figsize=(9, 4))
keys = [k for k in LABELS if k != "bank"]
margins = []
for k in keys:
    rev = sum(r.sector_revenue.get(k, 0.0) for r in window)
    prof = sum(r.sector_profit.get(k, 0.0) for r in window)
    margins.append(100 * prof / max(rev, 1.0))
targets = [100 * sim.config.sector_margin_target[k] for k in keys]
x = range(len(keys))
ax.bar(x, margins, color=[COLORS[k] for k in keys], width=0.6, label="Marge nette observée (25 dernières années)")
ax.scatter(x, targets, color="black", zorder=3, marker="_", s=900, label="Cible de la config")
ax.set_xticks(list(x)); ax.set_xticklabels([LABELS[k] for k in keys], rotation=15)
ax.set_ylabel("Marge nette (%)"); ax.set_title("Marges sectorielles observées et cibles")
ax.legend(frameon=False)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "marges.png")); plt.close(fig)

# 4. Marché du travail par métier
table = sim.trade_table()
fig, axes = plt.subplots(1, 2, figsize=(10, 4))
names = [row["Métier"] for row in table]
axes[0].barh(names, [row["Coefficient de salaire"] for row in table], color="#3B6FB6")
axes[0].axvline(1.0, color="grey", ls="--", lw=0.8)
axes[0].set_title("Coefficient de salaire par métier"); axes[0].invert_yaxis()
axes[1].barh(names, [100 * row["Chômage"] for row in table], color="#C0504D")
axes[1].set_title("Chômage par métier (%)"); axes[1].invert_yaxis()
fig.tight_layout(); fig.savefig(os.path.join(OUT, "metiers.png")); plt.close(fig)

# 5. Monnaie bancaire, dette publique et croissance endogène
fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
axes[0].plot(years, [r.money_supply for r in records], color="#3B6FB6", label="Monnaie (trésorerie des agents)")
axes[0].plot(years, [r.deposits for r in records], color="#8E5DA8", label="Dépôts créés par le crédit")
axes[0].plot(years, [r.bank_loan_portfolio for r in records], color="#C97B4A", label="Prêts bancaires")
axes[0].legend(frameon=False, fontsize=8); axes[0].set_ylabel("Euros simulés")
axes[0].set_title("Monnaie bancaire, dette publique et productivité")
axes[1].plot(years, [100 * r.public_debt / max(12 * r.nominal_gdp, 1) for r in records], color="#C0504D")
axes[1].set_ylabel("Dette publique (% PIB annuel)")
axes[2].plot(years, [r.tfp_index for r in records], color="#4C9F70", label="Indice de productivité (connaissances)")
axes[2].set_ylabel("Productivité (base 1)"); axes[2].set_xlabel("Années simulées")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "monnaie_etat_innovation.png")); plt.close(fig)
print("graphiques écrits dans", OUT)