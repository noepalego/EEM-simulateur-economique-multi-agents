"""Sonde d'autonomie : python autonomy_probe.py [mois] [graines...] [--set parametre=valeur ...]

Mesure, sans rien changer au moteur, si chaque cible, plafond ou tirage au sort
recensé dans AUTONOMIE.md « mord » en pratique. Les enveloppes recopient les
calculs du moteur avant d'appeler la fonction d'origine : l'état et les
générateurs aléatoires ne sont pas touchés (l'inflation moyenne affichée doit
être identique à celle d'``evaluate.py`` pour les mêmes graines).

Les compteurs posés autour des fonctions (Bourse, dividendes) couvrent aussi la
période de chauffe ; ceux relevés après chaque ``step`` ne couvrent que les mois
observés. Les mesures des mécanismes retirés en novembre 2026 (plafond des prix,
ancrage des anticipations, pression de marge, fusions tirées au sort, plancher
d'utilisation des matières) ont disparu avec eux (docs/OPTIMISATION_2026-11.md).
"""
import os
import statistics
import sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import economy.simulation as simmod  # noqa: E402
import economy.stock_market as smmod  # noqa: E402
from economy.models import SimulationConfig  # noqa: E402

STOCK_CAP = 5000.0  # plafond codé en dur dans stock_market._clear_stock_market


def run(args):
    seed, months, overrides = args
    m = defaultdict(float)
    hist = defaultdict(list)

    # Bourse : cours du mois avant le plafond de 5000 €
    orig_clear = smmod._clear_stock_market

    def clear(company, opening, fundamental, buys, sells, config, issue=True):
        demand_value = sum(v for _, v in buys)
        supply = sum(q for _, q in sells)
        imbalance = (demand_value - supply * opening) / max(opening * company.shares_outstanding, 1.0)
        price = opening * (1.0 + config.stock_flow_impact * imbalance)
        price += config.stock_adjustment_speed * (fundamental - price)
        price = max(opening * 0.85, min(opening * 1.15, price))
        m["stock_obs"] += 1
        m["stock_capped"] += price > STOCK_CAP
        m["stock_fund_over"] += fundamental > STOCK_CAP
        m["stock_15pct"] += price >= opening * 1.15 - 1e-9 or price <= opening * 0.85 + 1e-9
        m["stock_max_fund"] = max(m["stock_max_fund"], fundamental)
        treasury = company.treasury_shares
        result = orig_clear(company, opening, fundamental, buys, sells, config, issue)
        hist["price_to_fund"].append(company.stock_price / max(fundamental, 1.0))
        m["issued_value"] += (treasury - company.treasury_shares) * company.stock_price
        return result

    smmod._clear_stock_market = clear

    # dividendes reçus par les ménages
    orig_div = simmod.distribute_dividends

    def div(companies, people, *a, **kw):
        household_cash = sum(p.cash for p in people)
        result = orig_div(companies, people, *a, **kw)
        m["div_households"] += sum(p.cash for p in people) - household_cash
        return result

    simmod.distribute_dividends = div

    sim = simmod.EconomySimulation(SimulationConfig(seed=seed, **overrides))

    record = None
    m["div_households"] = 0.0  # mois observés seulement (la chauffe est passée)
    gdp_total = 0.0
    for _ in range(months):
        record = sim.step()
        live = sim.live
        gdp_total += record.nominal_gdp
        m["bids"] += record.takeover_bids
        m["acq_obs"] += record.acquisitions
        hist["corp_equity_share"].append(record.corporate_equity / max(record.stock_market_value, 1.0))
        hist["corp_bond_share"].append(record.corporate_bonds / max(record.public_debt, 1.0))
        hist["firm_debt_gdp"].append(record.company_debt / max(record.nominal_gdp, 1.0))
        # séries de cycles : stocks / ventes (alimentation et biens), mises en chantier, PIB réel
        sold = record.food_sold + record.goods_sold
        hist["cycle_stock"].append((record.food_inventory + record.goods_inventory) / max(sold, 1.0))
        hist["cycle_starts"].append(record.housing_construction / max(live.housing_build_cost, 1.0))
        hist["cycle_gdp"].append(record.real_gdp)
        hist["vacancy"].append(record.vacancies / max(record.vacancies + record.homes_occupied, 1))
        for sector in ("food", "goods"):
            prices = [c.price for c in sim.companies if c.active and c.sector == sector]
            if len(prices) > 1:
                hist["price_dispersion"].append(statistics.pstdev(prices) / statistics.mean(prices))
        # concentration et marge par secteur (moyennées sur les 300 derniers mois)
        for sector in ("food", "goods", "technology", "real_estate", "materials", "energy", "services"):
            firms = [c for c in sim.companies if c.active and c.sector == sector]
            revenue = sum(c.previous_revenue for c in firms)
            if revenue > 0.0:
                hist[f"hhi_{sector}"].append(sum((c.previous_revenue / revenue) ** 2 for c in firms))
                hist[f"margin_{sector}"].append(sum(c.previous_profit for c in firms) / revenue)
        hist["unmet_food"].append(sim._unmet_ratio.get("food", 0.0))
        hist["unmet_goods"].append(sim._unmet_ratio.get("goods", 0.0))
        m["policy_near_cap"] += sim.policy_rate * 12 >= live.policy_rate_cap - 0.002
        m["deposit_floor0"] += sim.policy_rate < live.deposit_spread
        hist["deposit_gap"].append(12 * (sim.policy_rate - sim.deposit_rate))
        hist["debt_gdp"].append(sim.public.debt / max(sim._annual_gdp(), 1.0))
        for c in sim.companies:
            if not c.active:
                continue
            if c.employees and c.wage_bill > 0:
                m["payroll_obs"] += 1
                m["payroll_cut"] += (c.revenue_ema or c.previous_revenue) / c.wage_bill < live.payroll_coverage_target
    out = defaultdict(float, m)  # un compteur jamais touché vaut 0
    ratios = sorted(hist["price_to_fund"])
    out["ptf_median"] = ratios[len(ratios) // 2]
    out["ptf_p95"] = ratios[int(0.95 * (len(ratios) - 1))]
    prices = {c.id: c.stock_price for c in sim.companies if c.active}
    stocks = sum(q * prices.get(cid, 0.0) for p in sim.people for cid, q in p.shares.items())
    financial = stocks + sum(max(0.0, p.cash) + p.bond_holdings for p in sim.people)
    out["household_stock_share"] = stocks / max(financial, 1.0)
    out["cap_gdp"] = record.stock_market_value / max(sim._annual_gdp(), 1.0)
    out["issued_gdp"] = m["issued_value"] / max(sum(r.nominal_gdp for r in sim.history[-months:]), 1.0)
    out["min_person_cash"] = min(p.cash for p in sim.people)
    out["div_households_gdp"] = m["div_households"] / max(gdp_total, 1.0)
    out["corp_equity_share"] = statistics.mean(hist["corp_equity_share"])
    out["corp_bond_share"] = statistics.mean(hist["corp_bond_share"])
    out["firm_debt_gdp"] = statistics.mean(hist["firm_debt_gdp"])
    for name in ("stock", "starts", "gdp"):
        out[f"cycle_{name}"] = cycle_stats(hist[f"cycle_{name}"])
    out["vacancy"] = (statistics.mean(hist["vacancy"]), max(hist["vacancy"]))
    out["price_dispersion"] =statistics.mean(hist["price_dispersion"]) if hist["price_dispersion"] else 0.0
    out["hhi_margin"] = {
        k[4:]: (statistics.mean(v[-300:]), statistics.mean(hist[f"margin_{k[4:]}"][-300:]))
        for k, v in hist.items() if k.startswith("hhi_") and v
    }
    out["unmet_food"] = statistics.mean(hist["unmet_food"])
    out["unmet_goods"] = statistics.mean(hist["unmet_goods"])
    out["deposit_gap"] = statistics.mean(hist["deposit_gap"])
    out["debt_gdp_max"] = max(hist["debt_gdp"])
    out["max_policy"] = 12 * max(r.policy_rate for r in sim.history[-months:])
    out["seed"] = seed
    out["months"] = months
    out["infl_avg"] = ((record.consumer_price_index / 100) ** (1 / (months / 12)) - 1) * 100
    return out


def cycle_stats(series: list[float], window: int = 61) -> tuple[float, float]:
    """Période dominante (mois) et amplitude d'une série, une fois retirée sa tendance.

    Tendance : moyenne mobile centrée sur ``window`` mois. Amplitude : écart-type des
    écarts à la tendance rapporté à la tendance moyenne. Période : premier pic de
    l'autocorrélation des écarts entre 6 et 120 mois (0 si aucun pic positif).
    """
    half = window // 2
    if len(series) <= window + 12:
        return 0.0, 0.0
    trend = [statistics.mean(series[i - half:i + half + 1]) for i in range(half, len(series) - half)]
    gaps = [series[i + half] - trend[i] for i in range(len(trend))]
    level = statistics.mean(trend) or 1.0
    amplitude = statistics.pstdev(gaps) / abs(level)
    mean = statistics.mean(gaps)
    centred = [g - mean for g in gaps]
    variance = sum(g * g for g in centred) or 1.0

    def acf(lag: int) -> float:
        return sum(centred[i] * centred[i + lag] for i in range(len(centred) - lag)) / variance

    values = [acf(lag) for lag in range(1, min(121, len(centred) // 2))]
    period = 0.0
    for lag in range(6, len(values) - 1):
        if values[lag - 1] > 0.0 and values[lag - 1] >= values[lag - 2] and values[lag - 1] >= values[lag]:
            period = float(lag)
            break
    return period, amplitude


def pct(a, b):
    return 100.0 * a / b if b else 0.0


if __name__ == "__main__":
    args = sys.argv[1:]
    overrides = {}
    if "--set" in args:  # même syntaxe qu'evaluate.py : --set parametre=valeur ...
        i = args.index("--set")
        for kv in args[i + 1:]:
            k, v = kv.split("=")
            try:
                overrides[k] = float(v) if "." in v or "e" in v else int(v)
            except ValueError:
                overrides[k] = v  # paramètre textuel
        args = args[:i]
    months = int(args[0]) if args else 800
    seeds = [int(a) for a in args[1:]] or [1001, 1002, 1003, 1004, 1005]
    with Pool(min(5, len(seeds))) as pool:
        results = pool.map(run, [(s, months, overrides) for s in seeds])
    rows = [
        ("inflation moyenne %/an (= evaluate.py)", lambda o: f"{o['infl_avg']:.2f}"),
        ("Bourse : cours > 5000 avant plafond (% mois-entr.)", lambda o: f"{pct(o['stock_capped'], o['stock_obs']):.0f}"),
        ("Bourse : valeur fondamentale > 5000 (%)", lambda o: f"{pct(o['stock_fund_over'], o['stock_obs']):.0f}"),
        ("Bourse : borne ±15 %/mois atteinte (%)", lambda o: f"{pct(o['stock_15pct'], o['stock_obs']):.0f}"),
        ("Bourse : valeur fondamentale max (€/action)", lambda o: f"{o['stock_max_fund']:.0f}"),
        ("Bourse : cours / valeur fondamentale, médiane", lambda o: f"{o['ptf_median']:.2f}"),
        ("Bourse : cours / valeur fondamentale, 95e centile", lambda o: f"{o['ptf_p95']:.2f}"),
        ("Bourse : capitalisation finale / PIB annuel", lambda o: f"{o['cap_gdp']:.2f}"),
        ("Bourse : émissions d'actions / PIB cumulé (%)", lambda o: f"{100 * o['issued_gdp']:.2f}"),
        ("ménages : part des actions, patrimoine financier (%)", lambda o: f"{100 * o['household_stock_share']:.0f}"),
        ("ménages : trésorerie minimale finale (€)", lambda o: f"{o['min_person_cash']:.0f}"),
        ("demande alimentaire non servie (moyenne %)", lambda o: f"{100 * o['unmet_food']:.0f}"),
        ("demande de biens non servie (moyenne %)", lambda o: f"{100 * o['unmet_goods']:.0f}"),
        ("offres de rachat lancées / réussies (mois observés)", lambda o: f"{o['bids']:.0f}/{o['acq_obs']:.0f}"),
        ("dividendes reçus par les ménages / PIB (%)", lambda o: f"{100 * o['div_households_gdp']:.1f}"),
        ("actions détenues par des entreprises (% capi., moy.)", lambda o: f"{100 * o['corp_equity_share']:.1f}"),
        ("obligations des entreprises (% dette publique, moy.)", lambda o: f"{100 * o['corp_bond_share']:.1f}"),
        ("dette des entreprises / PIB mensuel (moy.)", lambda o: f"{o['firm_debt_gdp']:.2f}"),
        ("taux des dépôts bloqué à 0 (mois)", lambda o: f"{o['deposit_floor0']:.0f}"),
        ("écart directeur - dépôts (points/an)", lambda o: f"{100 * o['deposit_gap']:.2f}"),
        ("taux directeur près de policy_rate_cap (mois)", lambda o: f"{o['policy_near_cap']:.0f}"),
        ("taux directeur max (%/an)", lambda o: f"{100 * o['max_policy']:.2f}"),
        ("dette publique max (PIB annuel)", lambda o: f"{o['debt_gdp_max']:.2f}"),
        ("licenciements payroll_coverage_target (% mois-entr.)", lambda o: f"{pct(o['payroll_cut'], o['payroll_obs']):.2f}"),
    ]
    print(f"{months} mois observés, graines {seeds}")
    width = max(len(label) for label, _ in rows)
    for label, cell in rows:
        print(label.ljust(width), " ".join(cell(o).rjust(8) for o in results))
    print("\nconcentration (Herfindahl) / marge nette par secteur, 300 derniers mois :")
    points = []
    for sector in sorted(results[0]["hhi_margin"]):
        cells = []
        for o in results:
            hhi, margin = o["hhi_margin"].get(sector, (float("nan"), float("nan")))
            cells.append(f"{hhi:.2f}/{100 * margin:5.1f}%")
            if hhi == hhi:
                points.append((hhi, margin))
        print(f"  {sector:12s}", "  ".join(cells))
    if len(points) > 2:
        hs, ms = zip(*points)
        mh, mm = statistics.mean(hs), statistics.mean(ms)
        cov = sum((h - mh) * (m - mm) for h, m in points)
        var = (sum((h - mh) ** 2 for h in hs) * sum((m - mm) ** 2 for m in ms)) ** 0.5
        print(f"  corrélation concentration-marge (secteurs × graines) : {cov / var if var else 0.0:+.2f}")
    print("cycles (période dominante en mois / amplitude hors tendance) :")
    for name, label in (("stock", "stocks / ventes"), ("starts", "mises en chantier"), ("gdp", "PIB réel")):
        print(f"  {label:18s}", "  ".join("{:.0f}/{:.2f}".format(*o[f"cycle_{name}"]) for o in results))
    print("logements vides (moyenne / maximum, % du parc) :",
          "  ".join(f"{100 * o['vacancy'][0]:.0f}/{100 * o['vacancy'][1]:.0f}" for o in results))
    print("dispersion des prix alimentation/biens (écart-type/moyenne) : "
          + " ".join(f"{o['price_dispersion']:.3f}" for o in results))
