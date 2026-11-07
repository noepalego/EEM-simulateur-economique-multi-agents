"""Sonde de réalisme : python realism_probe.py MOIS [graines...] [--set parametre=valeur ...]
                                             [--label nom] [--json dossier]

Mesure, sans rien changer au moteur, ce qui s'écarte d'une économie réelle ou d'un
comportement cohérent : ménages (consommation, épargne, crédit), entreprises (marges,
rendement des fonds propres, prix rapportés aux coûts, entrées et sorties), banques
(nombre, fonds propres, crédit, pertes), marchés (demande non servie, stocks, part des
salaires, vélocité de la monnaie) et dynamique (bulles, chutes, chauffe).

La chauffe est couverte : une sous-classe d'``EconomySimulation`` relève l'état après
chaque ``step`` (chauffe comprise) et garde les flux du mois (``MonthFlows``) dans
``_record_month``. Elle ne lit que des attributs et des fonctions pures
(``wealth.company_balance_sheet``) : aucun générateur aléatoire n'est consommé, aucun
état n'est modifié. Contrôle : l'inflation et le chômage moyens des mois observés sont
identiques à ceux d'``evaluate.py`` pour les mêmes graines.

``--json dossier`` écrit les séries mensuelles de chaque graine
(``<label>_<graine>.json``) pour une analyse plus fine.

Les compteurs d'``economy.diagnostics`` sont activés pendant la simulation observée :
morsure de chaque borne (part des décisions dont la valeur a été bornée), montants
passés par les chemins de sauvetage ou de garantie (fonds orphelin, recettes B2B d'une
entreprise sans salarié, emprunt public dû à la cible de dette...), motifs de refus
(embauche, crédit, entrée) et résolutions consignées (restructurations, coûts publics).
Ils ne lisent que des valeurs déjà calculées : les trajectoires sont inchangées.
"""
import json
import math
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from economy import diagnostics  # noqa: E402
from economy.models import (  # noqa: E402
    SimulationConfig, positive_loan_book,
)
from economy.sectors import FIRM_SECTORS  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402
from economy.wealth import company_balance_sheet  # noqa: E402

SECTORS = [s for s, _ in FIRM_SECTORS]


def source_fingerprint() -> str:
    """Empreinte SHA-256 (16 caractères) des sources du moteur et de cette sonde : relie un
    résultat à la version du code qui l'a produit."""
    import hashlib

    root = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    paths = sorted(
        os.path.join("economy", name) for name in os.listdir(os.path.join(root, "economy"))
        if name.endswith(".py")
    ) + ["realism_probe.py"]
    for path in paths:
        digest.update(path.replace(os.sep, "/").encode())
        with open(os.path.join(root, path), "rb") as handle:
            digest.update(handle.read())
    return digest.hexdigest()[:16]


def _firm_pricing(firms: list) -> dict:
    """Prix rapportés aux coûts unitaires (alimentation, biens), pondérés par les ventes.

    - coût variable moyen : salaires / production possible + intrants par unité (la
      base du prix en mode ``firm_signals``) ;
    - coût marginal : salaires / production due au travail + intrants par unité (une
      unité de plus demande du travail ; la capacité installée produit sans salarié) ;
    - coût complet : toutes les charges du mois / unités vendues.
    """
    sold = sum(c.units_sold for c in firms)
    out = {"avc": 0.0, "mc": 0.0, "full": 0.0, "machine": 0.0, "markup": 0.0, "w": sold}
    if sold <= 0.0:
        return out
    for c in firms:
        w = c.units_sold / sold
        if w <= 0.0 or c.potential_output <= 0.0:
            continue
        fill = max(c.energy_fill, 1e-9)
        gross = c.potential_output / fill
        labor_part = max(0.0, gross - c.production_capacity)
        avc = c.wage_bill / c.potential_output + c.unit_input_cost
        mc = (c.wage_bill / (labor_part * fill) if labor_part > 0.0 else 0.0) + c.unit_input_cost
        full = c.expenses / c.units_sold
        out["avc"] += w * (c.price / avc if avc > 0.0 else 0.0)
        out["mc"] += w * (c.price / mc if mc > 0.0 else 0.0)
        out["full"] += w * (c.price / full if full > 0.0 else 0.0)
        out["machine"] += w * min(1.0, c.production_capacity / gross if gross > 0.0 else 0.0)
        out["markup"] += w * max(0.0, c.price_markup)
    return out


def observe(sim, record, f) -> dict:
    """Relevé de fin de mois (lecture seule)."""
    live = sim.live
    people = sim.people
    companies = sim.companies
    prices = {c.id: c.stock_price for c in companies if c.active}
    vat = 1.0 + live.vat_rate
    row = {
        "rgdp": record.real_gdp, "ngdp": record.nominal_gdp, "u": record.unemployment,
        "cpi": record.consumer_price_index, "pol": 12.0 * record.policy_rate,
        "dep": 12.0 * record.deposit_rate, "money": record.money_supply,
        "resid": record.ledger_residual, "loan_err": record.loan_reconciliation_error,
        "debt_err": record.public_debt_reconciliation_error, "pubdebt": record.public_debt,
        "pop": len(people), "employed": record.employed_count, "inv": record.investment,
        "cap_inv": record.production_capacity_investment, "startups": record.startups,
        "bankr": record.bankruptcies, "acq": record.acquisitions,
        "rwage": record.real_average_wage, "wref": record.wage_reference,
        "vac": record.vacancies / max(record.vacancies + record.homes_occupied, 1),
        "homeless": sum(not p.owns_home and p.rental_owner_id is None for p in people),
        "owners": sum(p.owns_home for p in people),
        "food_p": record.food_price, "rent": record.housing_rent,
    }
    # ménages : revenu, impôt, consommation (TTC, loyers payés compris), patrimoine
    row["y"] = record.household_income
    row["tax"] = sum(p.taxes_paid for p in people)
    row["div"] = sum(p.dividend_income_this_turn for p in people)
    row["c_food"] = f.food_revenue * vat
    row["c_goods"] = f.goods_revenue * vat
    row["c_serv"] = f.household_services * vat
    row["c_tech"] = sim.resources.household_technology_revenue
    row["c_rent"] = f.rental_revenue
    row["c"] = row["c_food"] + row["c_goods"] + row["c_serv"] + row["c_tech"] + row["c_rent"]
    row["cash"] = sum(max(0.0, p.cash) for p in people)
    row["bonds"] = sum(p.bond_holdings for p in people)
    row["stocks"] = sum(q * prices.get(cid, 0.0) for p in people for cid, q in p.shares.items())
    row["mort"] = record.mortgage_debt
    row["cdebt"] = record.consumer_debt
    row["n_cborrow"] = sum(p.consumer_debt > 0.0 for p in people)
    employed = [p for p in people if p.employer_id is not None]
    # contrainte de liquidité : salarié avec moins d'un mois de salaire en trésorerie
    row["n_lowcash"] = sum(p.cash < p.current_wage for p in employed)
    incomes = sorted(max(0.0, p.income_this_turn) for p in people)
    row["y_top10"] = sum(incomes[int(0.9 * len(incomes)):]) / max(sum(incomes), 1.0)
    cash_sorted = sorted(max(0.0, p.cash) for p in people)
    row["cash_top10"] = sum(cash_sorted[int(0.9 * len(cash_sorted)):]) / max(sum(cash_sorted), 1.0)
    # demande non servie, stocks, énergie, indices B2B
    row["unmet_food"] = f.unmet_food_demand / max(1.0, f.sold_food + f.unmet_food_demand)
    row["unmet_goods"] = f.unmet_goods_demand / max(1.0, f.sold_goods + f.unmet_goods_demand)
    row["inv_sales"] = (record.food_inventory + record.goods_inventory) / max(1.0, record.food_sold + record.goods_sold)
    wanted = record.energy_delivered + record.energy_unmet
    row["e_unmet"] = record.energy_unmet / wanted if wanted > 0.0 else 0.0
    row["e_util"] = record.energy_delivered / record.energy_supply if record.energy_supply > 0.0 else 0.0
    row["e_idx"] = sim.resources.energy_price_index
    row["t_idx"] = sim.resources.tech_price_index
    # entreprises par secteur (flux des entreprises fermées pendant le mois compris)
    operated = [c for c in companies if c.active or c.exit_month == sim.month]
    row["wagebill"] = sum(c.wage_bill for c in operated) + f.public_wages
    sec = {}
    for s in SECTORS:
        firms = [c for c in companies if c.active and c.sector == s]
        revenue_prev = sum(c.previous_revenue for c in firms)
        equity = 0.0
        if s != "bank":
            equity = sum(company_balance_sheet(c, companies, live, prices)["Capitaux propres"] for c in firms)
        sec[s] = {
            "n": len(firms),
            "n_emp": sum(1 for c in firms if c.employees),
            "emp": sum(len(c.employees) for c in firms),
            "rev": record.sector_revenue.get(s, 0.0),
            "prof": record.sector_profit.get(s, 0.0),
            "eq": equity,
            "cash": sum(c.cash for c in firms),
            "debt": sum(c.debt for c in firms),
            "hhi": (sum((c.previous_revenue / revenue_prev) ** 2 for c in firms) if revenue_prev > 0.0 else (1.0 if firms else 0.0)),
            # coquilles : actives sans salarié, sans production ni ventes ce mois
            "n_idle": sum(1 for c in firms if not c.employees and c.units_produced <= 0.0 and c.revenue <= 0.0),
            # offre salariale des employeurs, pondérée par les effectifs (en salaires de référence)
            "offer": (sum(c.wage_offer * len(c.employees) for c in firms)
                      / max(1, sum(len(c.employees) for c in firms)) / max(sim.wage_reference, 1.0)),
            "produced": sum(c.units_produced for c in firms),
        }
    row["sec"] = sec
    # offre salariale la plus haute de l'alimentation et des biens, en salaires de référence
    offers = [c.wage_offer for c in companies if c.active and c.sector in ("food", "goods")]
    row["offer_max"] = max(offers) / max(sim.wage_reference, 1.0) if offers else 0.0
    for s in ("food", "goods"):
        row[f"pr_{s}"] = _firm_pricing([c for c in companies if c.active and c.sector == s])
    # banques : nombre, fonds propres, prêts, ratio de fonds propres, pertes cumulées
    banks = [c for c in companies if c.active and c.sector == "bank"]
    ratios = [b.bank_equity / max(positive_loan_book(b.bank_loan_accounts), 1.0) for b in banks]
    row["nb"] = len(banks)
    row["b_eq"] = sum(b.bank_equity for b in banks)
    row["b_loans"] = record.bank_loan_portfolio
    row["b_capmin"] = min(ratios) if ratios else 0.0
    row["b_loss_cum"] = sum(c.default_loss for c in companies if c.sector == "bank")
    row["ccap"] = record.credit_capacity
    row["firm_debt"] = record.company_debt
    return row


class ProbeSimulation(EconomySimulation):
    """Simulation qui relève son état après chaque mois, chauffe comprise."""

    def __init__(self, config: SimulationConfig):
        self._probe_rows: list[dict] = []
        self._probe_exits: list[tuple] = []
        self._probe_active: dict[str, tuple] = {}
        self._probe_flows = None
        self._probe_events_seen = 0
        self._probe_money = {}
        super().__init__(config)

    def _record_month(self, f):
        self._probe_flows = f
        return super()._record_month(f)

    def step(self):
        diagnostics.set_month(self.month)
        record = super().step()
        row = observe(self, record, self._probe_flows)
        # résolutions du mois (la liste est vidée à la fin de la chauffe)
        events = self.resolution_events
        if len(events) < self._probe_events_seen:
            self._probe_events_seen = 0
        new = events[self._probe_events_seen:]
        self._probe_events_seen = len(events)
        row["res"] = {}
        for event in new:
            row["res"][event.kind] = row["res"].get(event.kind, 0) + 1
        row["res_public"] = sum(e.public_cost for e in new)
        row["res_depositors"] = sum(e.depositor_loss for e in new)
        row["res_written_off"] = sum(e.debt_written_off for e in new)
        # montants du mois passés par les chemins de sauvetage ou de garantie
        money = diagnostics.snapshot()["flows"]
        row["fl"] = {k: v - self._probe_money.get(k, 0.0) for k, v in money.items()
                     if v != self._probe_money.get(k, 0.0)}
        self._probe_money = money
        self._probe_rows.append(row)
        # sorties : (mois, secteur, âge, motif) des entreprises fermées ce mois
        now = {c.id: (c.sector, c.age_months) for c in self.companies if c.active}
        by_id = {c.id: c for c in self.companies}
        for cid, (sector, age) in self._probe_active.items():
            if cid not in now:
                reason = by_id[cid].exit_reason if cid in by_id else "retirée"
                self._probe_exits.append((len(self._probe_rows), sector, age, reason))
        self._probe_active = now
        return record


def run(args):
    seed, months, overrides, label, json_dir = args
    # compteurs de diagnostic : chauffe, puis période observée (différence des cumuls)
    diagnostics.start(sample_limit=20)
    try:
        sim = ProbeSimulation(SimulationConfig(seed=seed, **overrides))
        warmup = len(sim._probe_rows)
        after_warmup = diagnostics.snapshot()
        for _ in range(months):
            sim.step()
        final = diagnostics.snapshot()
    finally:
        diagnostics.stop()
    out = {"seed": seed, "label": label, "warmup": warmup, "rows": sim._probe_rows,
           "exits": sim._probe_exits, "erp": sim.config.equity_risk_premium,
           "capital_requirement": sim.config.capital_requirement,
           "diagnostics": {"chauffe": after_warmup,
                           "observation": diagnostics.difference(final, after_warmup)}}
    if json_dir:
        with open(os.path.join(json_dir, f"{label}_{seed}.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh)
    return summarize(out)


def _ma(series: list[float], n: int = 12) -> list[float]:
    return [statistics.mean(series[max(0, i - n + 1):i + 1]) for i in range(len(series))]


def summarize(out: dict) -> dict:
    """Indicateurs par graine : chauffe (« W ») et observation (« O »)."""
    rows = out["rows"]
    w = out["warmup"]
    obs = rows[w:]
    s = {"seed": out["seed"], "months": len(obs), "warmup": w}
    g = [r["rgdp"] for r in rows]
    ma = _ma(g)
    o_ma = ma[w:]
    s["gdp_final_peak"] = o_ma[-1] / max(o_ma)
    s["gdp_peak_warmup_over_start"] = max(ma[:w]) / ma[11] if w > 12 else float("nan")
    # croissance réelle annualisée de l'observation (24 premiers mois → 24 derniers)
    first, last = statistics.mean(g[w:w + 24]), statistics.mean(g[-24:])
    s["growth"] = 100.0 * ((last / first) ** (12.0 / (len(obs) - 24)) - 1.0)
    # bulle : plus forte hausse du PIB lissé sur 24 mois, puis chute éventuelle
    s["max_rise24_W"] = max((ma[i] / ma[i - 24] for i in range(36, w)), default=float("nan"))
    s["max_rise24_O"] = max(ma[i] / ma[i - 24] for i in range(w + 24, len(ma)))
    s["start_jump"] = statistics.mean(g[w + 12:w + 36]) / statistics.mean(g[w:w + 12])
    peak, crashes, low = 0.0, [], None
    for i, v in enumerate(o_ma):
        peak = max(peak, v)
        if v < 0.75 * peak and low is None:
            low = [i, v / peak]
        if low is not None:
            low[1] = min(low[1], v / peak)
            if v >= 0.9 * peak:
                crashes.append(tuple(low)); low = None
    s["crashes"] = crashes + ([tuple(low) + ("en cours",)] if low else [])
    s["u_mean"] = 100 * statistics.mean(r["u"] for r in obs)
    s["u_max"] = 100 * max(r["u"] for r in obs[36:])
    s["u_mean_W"] = 100 * statistics.mean(r["u"] for r in rows[:w]) if w else float("nan")
    s["infl"] = 100 * ((obs[-1]["cpi"] / rows[w - 1]["cpi"]) ** (12 / len(obs)) - 1) if w else float("nan")
    # formule d'evaluate.py (IPC de base 100 au tout premier mois de la chauffe) : contrôle d'identité
    s["infl_eval"] = 100 * ((obs[-1]["cpi"] / 100) ** (12 / len(obs)) - 1)
    infl12 = [obs[i]["cpi"] / obs[i - 12]["cpi"] - 1 for i in range(12, len(obs))]
    s["infl12_max"] = 100 * max(infl12)
    s["infl_last10y"] = 100 * ((obs[-1]["cpi"] / obs[-121]["cpi"]) ** 0.1 - 1)
    # ménages
    def ratio(key_a, key_b, rs):
        b = sum(r[key_b] for r in rs)
        return sum(r[key_a] for r in rs) / b if b else float("nan")
    yd = sum(r["y"] - r["tax"] for r in obs)
    s["saving_rate"] = 100 * (1 - sum(r["c"] for r in obs) / yd) if yd else float("nan")
    s["saving_rate_W"] = 100 * (1 - sum(r["c"] for r in rows[:w]) / max(1.0, sum(r["y"] - r["tax"] for r in rows[:w])))
    s["cash_months"] = statistics.mean(r["cash"] / max(1.0, r["y"] - r["tax"]) for r in obs)
    s["fin_wealth_months"] = statistics.mean((r["cash"] + r["bonds"] + r["stocks"]) / max(1.0, r["y"] - r["tax"]) for r in obs)
    s["c_over_cash"] = 100 * statistics.mean(r["c"] / max(1.0, r["cash"]) for r in obs)
    s["cdebt_income"] = 100 * statistics.mean(r["cdebt"] / max(1.0, 12 * r["y"]) for r in obs)
    s["mort_income"] = 100 * statistics.mean(r["mort"] / max(1.0, 12 * r["y"]) for r in obs)
    s["borrowers"] = 100 * statistics.mean(r["n_cborrow"] / r["pop"] for r in obs)
    s["lowcash"] = 100 * statistics.mean(r["n_lowcash"] / max(1, r["employed"]) for r in obs)
    s["div_share_y"] = 100 * ratio("div", "y", obs)
    s["cash_top10"] = 100 * statistics.mean(r["cash_top10"] for r in obs)
    s["c_share"] = 100 * ratio("c", "ngdp", obs)
    s["homeless_end"] = obs[-1]["homeless"]
    # entreprises
    s["inv_gdp"] = 100 * ratio("inv", "ngdp", obs)
    s["wage_share"] = 100 * ratio("wagebill", "ngdp", obs)
    s["velocity"] = statistics.mean(12 * r["ngdp"] / max(1.0, r["money"]) for r in obs)
    s["entries"] = sum(r["startups"] for r in obs)
    s["bankruptcies"] = sum(r["bankr"] for r in obs)
    exits_o = [e for e in out["exits"] if e[0] > w]
    ages = sorted(e[2] for e in exits_o)
    s["exit_age_median"] = ages[len(ages) // 2] if ages else float("nan")
    s["firms_end"] = sum(obs[-1]["sec"][x]["n"] for x in SECTORS)
    sector = {}
    coc = 100 * statistics.mean(r["pol"] for r in obs) + 100 * out["erp"]
    for x in SECTORS:
        rev = sum(r["sec"][x]["rev"] for r in obs[-300:])
        prof = sum(r["sec"][x]["prof"] for r in obs[-300:])
        eq = statistics.mean(r["sec"][x]["eq"] for r in obs[-300:])
        sector[x] = {
            "margin": 100 * prof / rev if rev > 0 else float("nan"),
            "roe": 100 * 12 * prof / 300 / eq if eq > 0 and x != "bank" else float("nan"),
            "share": 100 * rev / max(1.0, sum(sum(r["sec"][k]["rev"] for k in SECTORS) for r in obs[-300:])),
            "alive": 100 * statistics.mean(r["sec"][x]["n"] >= 1 for r in obs),
            "alive_emp": 100 * statistics.mean(r["sec"][x]["n_emp"] >= 1 for r in obs),
            "hhi": statistics.mean(r["sec"][x]["hhi"] for r in obs[-300:]),
            "n_end": obs[-1]["sec"][x]["n"],
        }
    s["sector"] = sector
    s["coc"] = coc
    for x in ("food", "goods"):
        rs = [r[f"pr_{x}"] for r in obs if r[f"pr_{x}"]["w"] > 0]
        s[f"price_{x}"] = {k: statistics.mean(r[k] for r in rs) if rs else float("nan")
                           for k in ("avc", "mc", "full", "machine", "markup")}
    s["unmet_food"] = 100 * statistics.mean(r["unmet_food"] for r in obs)
    s["unmet_goods"] = 100 * statistics.mean(r["unmet_goods"] for r in obs)
    s["unmet_food_W_end"] = 100 * statistics.mean(r["unmet_food"] for r in rows[max(0, w - 12):w]) if w else float("nan")
    s["inv_sales"] = statistics.mean(r["inv_sales"] for r in obs)
    s["e_unmet"] = 100 * statistics.mean(r["e_unmet"] for r in obs)
    s["e_util"] = 100 * statistics.mean(r["e_util"] for r in obs)
    s["e_idx_end"] = obs[-1]["e_idx"]
    s["t_idx_end"] = obs[-1]["t_idx"]
    # banques
    s["banks_end"] = obs[-1]["nb"]
    s["banks_W_end"] = rows[w - 1]["nb"] if w else float("nan")
    s["banks_ge2"] = 100 * statistics.mean(r["nb"] >= 2 for r in obs)
    s["credit_gdp"] = statistics.mean(r["b_loans"] / max(1.0, 12 * r["ngdp"]) for r in obs)
    s["firm_debt_gdp"] = statistics.mean(r["firm_debt"] / max(1.0, 12 * r["ngdp"]) for r in obs)
    s["bank_capmin_p10"] = sorted(r["b_capmin"] for r in obs)[len(obs) // 10]
    s["ccap_zero"] = 100 * statistics.mean(r["ccap"] <= 1.0 for r in obs)
    s["bank_loss_gdp"] = 100 * (obs[-1]["b_loss_cum"] - rows[w - 1]["b_loss_cum"]) / max(1.0, sum(r["ngdp"] for r in obs))
    s["dep_zero"] = 100 * statistics.mean(r["dep"] <= 1e-9 for r in obs)
    # comptes
    s["resid_max"] = max(abs(r["resid"]) for r in obs)
    s["loan_err_max"] = max(r["loan_err"] for r in obs)
    s["debt_err_max"] = max(r["debt_err"] for r in obs)
    # Contrôles complémentaires incluant la chauffe ; les indicateurs
    # historiques de la seule période observée restent inchangés.
    s["resid_max_all"] = max(abs(r["resid"]) for r in rows)
    s["loan_err_max_all"] = max(abs(r["loan_err"]) for r in rows)
    s["debt_err_max_all"] = max(abs(r["debt_err"]) for r in rows)
    s["banks_min"] = min(r["nb"] for r in obs)
    s["banks_min_all"] = min(r["nb"] for r in rows)
    s["infl1_max_all"] = 100 * max(
        rows[i]["cpi"] / rows[i - 1]["cpi"] - 1 for i in range(1, len(rows))
    )
    # coquilles (mois-entreprise sans salarié, production ni ventes) et offres extrêmes
    s["idle_firm_months"] = sum(sum(r["sec"][x].get("n_idle", 0) for x in SECTORS) for r in obs)
    s["vacancy_mean"] = 100 * statistics.mean(r["vac"] for r in obs)
    s["offer_ratio_max"] = max(r.get("offer_max", 0.0) for r in obs)
    s["nan"] = any(not math.isfinite(r[k]) for r in rows for k in ("rgdp", "ngdp", "u", "cpi"))
    s.update(_crisis_summary(o_ma))
    s.update(_rescue_summary(out, obs))
    return s


def _crisis_summary(o_ma: list[float]) -> dict:
    """Crises du PIB lissé (mission « autonomie stable », section 7) : épisodes sous 75 % du
    pic courant, profondeur, durée jusqu'au retour à 90 % du pic (None : non rétabli), mois
    sous 75 %, et plus longue suite de mois sous 30 % du pic (effondrement si ≥ 60)."""
    episodes, peak, start, low, run30, longest30, below75 = [], 0.0, None, 1.0, 0, 0, 0
    for i, v in enumerate(o_ma):
        peak = max(peak, v)
        ratio = v / peak if peak > 0.0 else 1.0
        below75 += ratio < 0.75
        run30 = run30 + 1 if ratio < 0.30 else 0
        longest30 = max(longest30, run30)
        if start is None:
            if ratio < 0.75:
                start, low = i, ratio
        else:
            low = min(low, ratio)
            if ratio >= 0.9:
                episodes.append((start, low, i - start))
                start = None
    if start is not None:
        episodes.append((start, low, None))
    return {
        "crisis_episodes": episodes,
        "crisis_count": len(episodes),
        "crisis_depth_min": min((e[1] for e in episodes), default=1.0),
        "crisis_months_below75": below75,
        "crisis_longest": max((len(o_ma) - e[0] if e[2] is None else e[2] for e in episodes), default=0),
        "crisis_unrecovered": sum(e[2] is None for e in episodes),
        "collapse_longest_below30": longest30,
    }


def _rescue_summary(out: dict, obs: list[dict]) -> dict:
    """Secteurs sans employeur, besoins non servis, sauvetages et garde-fous de la période
    observée. Les anciens fichiers ``--json`` sans compteurs donnent des valeurs vides."""
    gdp = max(1.0, sum(r["ngdp"] for r in obs))
    without = {x: sum(r["sec"][x]["n_emp"] < 1 for r in obs) for x in SECTORS}
    resolutions: dict[str, int] = {}
    for r in obs:
        for kind, count in r.get("res", {}).items():
            resolutions[kind] = resolutions.get(kind, 0) + count
    diag = out.get("diagnostics", {}).get("observation", {})
    bites = diag.get("bites", {})
    warmup_bites = out.get("diagnostics", {}).get("chauffe", {}).get("bites", {})
    years = max(1.0, (len(obs) - 12) / 12.0)
    first_w = statistics.mean(r["wref"] for r in obs[:12])
    last_w = statistics.mean(r["wref"] for r in obs[-12:])
    first_rw = statistics.mean(r["rwage"] for r in obs[:12])
    last_rw = statistics.mean(r["rwage"] for r in obs[-12:])
    return {
        # croissance annuelle du salaire de référence (nominal) et du salaire réel moyen
        "wage_ref_growth": 100 * ((last_w / max(first_w, 1e-9)) ** (1.0 / years) - 1.0),
        "real_wage_growth": 100 * ((last_rw / max(first_rw, 1e-9)) ** (1.0 / years) - 1.0),
        "employment_share": {x: 100 * statistics.mean(
            r["sec"][x]["emp"] / max(1, r["employed"]) for r in obs) for x in SECTORS},
        "offer_relative": {x: statistics.mean(r["sec"][x].get("offer", 0.0) for r in obs) for x in SECTORS},
        "sector_months_without_employer": without,
        "months_any_sector_without_employer": sum(
            any(r["sec"][x]["n_emp"] < 1 for x in SECTORS) for r in obs),
        "unmet_food_max": 100 * max(r["unmet_food"] for r in obs),
        "e_unmet_max": 100 * max(r["e_unmet"] for r in obs),
        "months_food_unmet50": sum(r["unmet_food"] > 0.5 for r in obs),
        "months_energy_unmet50": sum(r["e_unmet"] > 0.5 for r in obs),
        "resolutions": resolutions,
        "resolution_public_cost_gdp": 100 * sum(r.get("res_public", 0.0) for r in obs) / gdp,
        "resolution_depositor_loss_gdp": 100 * sum(r.get("res_depositors", 0.0) for r in obs) / gdp,
        "flows_gdp": {name: 100 * value / gdp for name, value in diag.get("flows", {}).items()},
        "bites": {name: 100 * bitten / n for name, (n, bitten) in bites.items() if n},
        "bites_n": {name: n for name, (n, _) in bites.items()},
        "bites_warmup": {name: 100 * bitten / n for name, (n, bitten) in warmup_bites.items() if n},
        "refusals": diag.get("refusals", {}),
        "refusal_samples": diag.get("samples", {}),
    }


def _cell(v, fmt="{:.1f}"):
    if isinstance(v, float) and not math.isfinite(v):
        return "nan"
    return fmt.format(v)


def main():
    args = sys.argv[1:]
    overrides, label, json_dir = {}, "run", None
    if "--label" in args:
        i = args.index("--label"); label = args[i + 1]; del args[i:i + 2]
    if "--json" in args:
        i = args.index("--json"); json_dir = args[i + 1]; del args[i:i + 2]
        os.makedirs(json_dir, exist_ok=True)
    if "--set" in args:  # même syntaxe qu'evaluate.py
        i = args.index("--set")
        for kv in args[i + 1:]:
            k, v = kv.split("=")
            try:
                overrides[k] = float(v) if "." in v else int(v)
            except ValueError:
                overrides[k] = v
        args = args[:i]
    months = int(args[0]) if args else 800
    seeds = [int(a) for a in args[1:]] or [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010]
    with Pool(min(8, len(seeds))) as pool:
        results = pool.map(run, [(s, months, overrides, label, json_dir) for s in seeds])
    print(f"{label} : {months} mois observés, réglages {overrides or 'par défaut'}")
    rows = [
        ("PIB réel final / pic (lissé 12 mois)", "gdp_final_peak", "{:.2f}"),
        ("croissance réelle de l'observation (%/an)", "growth", "{:+.2f}"),
        ("chauffe : pic du PIB / PIB de la 1re année", "gdp_peak_warmup_over_start", "{:.2f}"),
        ("hausse max du PIB sur 24 mois, chauffe", "max_rise24_W", "{:.2f}"),
        ("hausse max du PIB sur 24 mois, observation", "max_rise24_O", "{:.2f}"),
        ("saut initial (années 2-3 / année 1)", "start_jump", "{:.2f}"),
        ("chômage moyen (%)", "u_mean", "{:.1f}"),
        ("chômage max après 3 ans (%)", "u_max", "{:.1f}"),
        ("chômage moyen pendant la chauffe (%)", "u_mean_W", "{:.1f}"),
        ("inflation moyenne de l'observation (%/an)", "infl", "{:.2f}"),
        ("inflation, formule d'evaluate.py (contrôle)", "infl_eval", "{:.2f}"),
        ("inflation sur 12 mois, max (%)", "infl12_max", "{:.1f}"),
        ("inflation des 10 dernières années (%/an)", "infl_last10y", "{:.2f}"),
        ("ménages : taux d'épargne (% du revenu disponible)", "saving_rate", "{:.1f}"),
        ("ménages : taux d'épargne pendant la chauffe", "saving_rate_W", "{:.1f}"),
        ("ménages : trésorerie (mois de revenu disponible)", "cash_months", "{:.1f}"),
        ("ménages : patrimoine financier (mois de revenu)", "fin_wealth_months", "{:.1f}"),
        ("ménages : consommation / trésorerie (%/mois)", "c_over_cash", "{:.1f}"),
        ("ménages : crédit conso / revenu annuel (%)", "cdebt_income", "{:.2f}"),
        ("ménages : crédit immobilier / revenu annuel (%)", "mort_income", "{:.1f}"),
        ("ménages : emprunteurs à la conso (% pop.)", "borrowers", "{:.1f}"),
        ("ménages : salariés sous 1 mois de salaire (%)", "lowcash", "{:.1f}"),
        ("ménages : dividendes et intérêts / revenu (%)", "div_share_y", "{:.1f}"),
        ("ménages : part de la trésorerie des 10 % (%)", "cash_top10", "{:.0f}"),
        ("consommation / PIB (%)", "c_share", "{:.1f}"),
        ("sans logement en fin de période", "homeless_end", "{:.0f}"),
        ("investissement / PIB (%)", "inv_gdp", "{:.1f}"),
        ("salaires / PIB (%)", "wage_share", "{:.1f}"),
        ("vélocité de la monnaie (PIB annuel / M)", "velocity", "{:.2f}"),
        ("entrées / faillites (observation)", None, None),
        ("âge médian à la sortie (mois)", "exit_age_median", "{:.0f}"),
        ("entreprises actives en fin de période", "firms_end", "{:.0f}"),
        ("coût des fonds propres moyen (%/an)", "coc", "{:.1f}"),
        ("demande alimentaire non servie (%)", "unmet_food", "{:.1f}"),
        ("  idem, dernière année de chauffe (%)", "unmet_food_W_end", "{:.1f}"),
        ("demande de biens non servie (%)", "unmet_goods", "{:.1f}"),
        ("stocks / ventes (mois)", "inv_sales", "{:.2f}"),
        ("énergie non livrée (%) / utilisation (%)", None, None),
        ("indice énergie / technologie en fin", None, None),
        ("banques en fin de chauffe / en fin", None, None),
        ("mois avec au moins 2 banques (%)", "banks_ge2", "{:.0f}"),
        ("crédit total / PIB annuel", "credit_gdp", "{:.2f}"),
        ("dette des entreprises / PIB annuel", "firm_debt_gdp", "{:.2f}"),
        ("fonds propres / prêts, banque la plus faible (10e centile)", "bank_capmin_p10", "{:.3f}"),
        ("capacité de crédit nulle (% des mois)", "ccap_zero", "{:.0f}"),
        ("pertes bancaires / PIB cumulé (%)", "bank_loss_gdp", "{:.2f}"),
        ("taux des dépôts à zéro (% des mois)", "dep_zero", "{:.0f}"),
        ("écart comptable max (€)", "resid_max", "{:.2f}"),
        ("rapprochement prêts / dette max", None, None),
        ("résidu max, chauffe comprise (€)", "resid_max_all", "{:.3e}"),
        ("rapprochement prêts, chauffe comprise", "loan_err_max_all", "{:.3e}"),
        ("rapprochement dette, chauffe comprise", "debt_err_max_all", "{:.3e}"),
        ("banques : minimum pendant l'observation", "banks_min", "{:.0f}"),
        ("banques : minimum chauffe comprise", "banks_min_all", "{:.0f}"),
        ("inflation mensuelle max, chauffe comprise (%)", "infl1_max_all", "{:.2f}"),
    ]
    width = max(len(r[0]) for r in rows)
    print("graine".ljust(width), " ".join(f"{o['seed']:>9}" for o in results))
    for name, key, fmt in rows:
        if key is None:
            if name.startswith("entrées"):
                cells = [f"{o['entries']}/{o['bankruptcies']}" for o in results]
            elif name.startswith("énergie"):
                cells = [f"{o['e_unmet']:.1f}/{o['e_util']:.0f}" for o in results]
            elif name.startswith("indice"):
                cells = [f"{o['e_idx_end']:.2f}/{o['t_idx_end']:.2f}" for o in results]
            elif name.startswith("banques"):
                cells = [f"{o['banks_W_end']}/{o['banks_end']}" for o in results]
            else:
                cells = [f"{o['loan_err_max']:.0e}/{o['debt_err_max']:.0e}" for o in results]
        else:
            cells = [_cell(o[key], fmt) for o in results]
        print(name.ljust(width), " ".join(c.rjust(9) for c in cells))
    print("\npar secteur, 300 derniers mois : part du CA % / marge % / rendement des fonds propres %/an")
    for x in SECTORS:
        print(f"  {x:12s}", "  ".join(
            f"{_cell(o['sector'][x]['share'])}/{_cell(o['sector'][x]['margin'])}/{_cell(o['sector'][x]['roe'], '{:.0f}')}"
            for o in results))
    print("par secteur : mois avec ≥ 1 entreprise / ≥ 1 entreprise avec salariés (%), entreprises en fin")
    for x in SECTORS:
        print(f"  {x:12s}", "  ".join(
            f"{o['sector'][x]['alive']:.0f}/{o['sector'][x]['alive_emp']:.0f}/{o['sector'][x]['n_end']}"
            for o in results))
    print("prix / coût variable moyen, / coût marginal, / coût complet ; part de la capacité installée ; marge")
    for x in ("food", "goods"):
        print(f"  {x:12s}", "  ".join(
            "{:.2f}/{:.2f}/{:.2f}/{:.2f}/{:.2f}".format(*(o[f'price_{x}'][k] for k in ('avc', 'mc', 'full', 'machine', 'markup')))
            for o in results))
    print("chutes du PIB lissé sous 75 % du pic (mois d'observation, creux/pic) :",
          [o["crashes"] for o in results])
    print("valeurs non finies :", [o["nan"] for o in results])


if __name__ == "__main__":
    main()
