"""Décomposition de la pénurie d'énergie (lecture seule).

Usage : python diagnostic/energy_trace.py mois_observés graine [graine ...] [paramètre=valeur ...]
Configuration unique ; les paramètres donnés la modifient.

Des enveloppes recopient les arguments et les résultats des fonctions d'origine, sans
rien modifier ni consommer de tirage aléatoire :
- ``intersector.deliver_energy`` : chaque commande se livre au plus
  min(commande, offre disponible, budget / prix). La part non livrée se répartit en
  pénurie d'**offre** (offre épuisée) et en commande **non payable** (budget de
  l'acheteur) ;
- ``energy.forecast_plant_project`` et ``energy._finance_plant_project`` : projets calculés, motifs de
  refus (marge ou rendement insuffisants, aucun besoin observé), montant financé contre
  montant voulu.
Chaque mois : ce qui borne l'offre de chaque producteur (centrales ou personnel), la
croissance des commandes, le taux de livraison des producteurs d'alimentation et de biens,
l'utilisation des centrales, l'erreur de la prévision des producteurs
(somme des prévisions du mois contre commandes payables du mois suivant).
"""
import statistics as st
import sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(args):
    seed, months, extra = args
    import economy.energy as energy
    import economy.intersector as intersector
    from economy.models import SimulationConfig
    from economy.simulation import EconomySimulation

    month = {"wanted": 0.0, "delivered": 0.0, "short_supply": 0.0, "short_budget": 0.0}
    plan = {"projects": 0, "candidates": 0, "no_need": 0, "low_return": 0, "wanted_cost": 0.0, "spent": 0.0}
    original_deliver = intersector.deliver_energy

    def deliver(buyer, wanted, budget, companies, resources):
        suppliers = [c for c in companies if c.active and c.sector == "energy" and c.id != buyer.id]
        supply = sum(c.energy_available for c in suppliers)
        wanted_ = max(0.0, wanted)
        affordable = min(wanted_, max(0.0, budget) / resources.energy_unit_price)
        month["wanted"] += wanted_
        month["short_budget"] += wanted_ - affordable
        month["short_supply"] += max(0.0, affordable - supply)
        paid = original_deliver(buyer, wanted, budget, companies, resources)
        month["delivered"] += paid / resources.energy_unit_price
        return paid

    intersector.deliver_energy = deliver
    original_forecast_project = energy.forecast_plant_project

    def forecast_project(company, unit_cost, resources, config, policy_rate):
        units, annual = original_forecast_project(company, unit_cost, resources, config, policy_rate)
        plan["candidates"] += 1
        if units > 0.0:
            plan["projects"] += 1
            plan["wanted_cost"] += units * unit_cost
        else:
            decay = config.sector_capital_depreciation.get("energy", config.productive_capacity_depreciation_rate)
            if company.energy_demand_forecast - company.energy_capacity * (1.0 - decay) <= 0.0:
                plan["no_need"] += 1
            else:
                plan["low_return"] += 1
        return units, annual

    energy.forecast_plant_project = forecast_project
    # financement des projets
    original_financing = energy._finance_plant_project

    def financing(*a, **k):
        amount, loan = original_financing(*a, **k)
        plan["spent"] += amount
        return amount, loan

    energy._finance_plant_project = financing
    overrides = dict(extra)
    rows = []

    class Sim(EconomySimulation):
        def step(self):
            for key in month:
                month[key] = 0.0
            for key in plan:
                plan[key] = 0 if isinstance(plan[key], int) else 0.0
            record = super().step()
            live = self.live
            producers = [c for c in self.companies if c.active and c.sector == "energy"]
            plant_bound = staff_bound = 0
            capacity = staff_limit = 0.0
            for c in producers:
                limit = len(c.employees) * max(0.0, c.productivity_trait) * live.energy_output_per_worker
                capacity += c.energy_capacity
                staff_limit += limit
                if c.energy_capacity <= limit:
                    plant_bound += 1
                else:
                    staff_bound += 1
            buyers = [c for c in self.companies if c.active and c.sector in ("food", "goods")]
            rows.append(dict(
                month=self.month, **month, **plan,
                capacity=capacity, staff_limit=staff_limit, supply=self.resources.energy_supply,
                plant_bound=plant_bound, staff_bound=staff_bound,
                fill=st.mean(c.energy_fill for c in buyers) if buyers else 1.0,
                index=self.resources.energy_price_index, gdp=record.real_gdp,
                forecast=sum(c.energy_demand_forecast for c in producers),
                staff=sum(len(c.employees) for c in producers),
            ))
            return record

    sim = Sim(SimulationConfig(seed=seed, **overrides))
    for _ in range(months):
        sim.step()
    return seed, rows, sim.warmup_offset


def summarize(seed, rows, warmup):
    obs = [r for r in rows if r["month"] > warmup]
    wanted = sum(r["wanted"] for r in obs)
    short_supply = sum(r["short_supply"] for r in obs)
    short_budget = sum(r["short_budget"] for r in obs)
    # pénurie d'offre comparée à la croissance des commandes d'un mois sur l'autre
    growth = [max(0.0, obs[i]["wanted"] - obs[i - 1]["wanted"]) for i in range(1, len(obs))]
    lagged = sum(min(g, obs[i]["short_supply"]) for i, g in enumerate(growth, 1))
    months_short = [r for r in obs if r["short_supply"] > 0.01 * max(r["wanted"], 1.0)]
    staff_months = sum(1 for r in months_short if r["staff_limit"] < r["capacity"])
    candidates = sum(r["candidates"] for r in obs)
    print(f"== graine {seed} ({len(obs)} mois observés)")
    print(f"  commandes non livrées : {100 * (short_supply + short_budget) / max(wanted, 1):.1f} % des commandes"
          f" ; dont offre épuisée {100 * short_supply / max(wanted, 1):.1f} %, non payables {100 * short_budget / max(wanted, 1):.1f} %")
    print(f"  pénurie d'offre expliquable par la hausse des commandes d'un mois : {100 * lagged / max(short_supply, 1):.0f} %"
          f" (croissance mensuelle médiane des commandes {100 * st.median((obs[i]['wanted'] / max(obs[i-1]['wanted'], 1) - 1) for i in range(1, len(obs))):+.2f} %)")
    print(f"  mois avec pénurie d'offre > 1 % : {len(months_short)} ; dont offre bornée par le personnel (capacité > personnel) : {staff_months}")
    print(f"  producteurs-mois bornés par les centrales / par le personnel : {sum(r['plant_bound'] for r in obs)} / {sum(r['staff_bound'] for r in obs)}")
    print(f"  décisions d'investissement : {candidates} examens, {sum(r['projects'] for r in obs)} projets,"
          f" refus pour rendement ou marge {sum(r['low_return'] for r in obs)}, sans besoin observé {sum(r['no_need'] for r in obs)}")
    wanted_cost = sum(r["wanted_cost"] for r in obs)
    print(f"  montant des projets voulus / financés : {wanted_cost:.0f} / {sum(r['spent'] for r in obs):.0f}"
          f" ({100 * sum(r['spent'] for r in obs) / max(wanted_cost, 1):.0f} %)")
    print(f"  livraison moyenne des producteurs d'alimentation et de biens : {100 * st.mean(r['fill'] for r in obs):.1f} %"
          f" ; indice de l'énergie fin {obs[-1]['index']:.2f}")
    utilization = [r["delivered"] / r["capacity"] for r in obs if r["capacity"] > 0.0]
    print(f"  utilisation des centrales : médiane {100 * st.median(utilization):.0f} % ;"
          f" mois < 80 % : {sum(1 for u in utilization if u < 0.8)} ; salariés de l'énergie médiane {st.median(r['staff'] for r in obs):.0f}")
    errors = [obs[i]["forecast"] / (obs[i + 1]["wanted"] - obs[i + 1]["short_budget"]) - 1.0
              for i in range(len(obs) - 1)
              if obs[i]["forecast"] > 0.0 and obs[i + 1]["wanted"] - obs[i + 1]["short_budget"] > 0.0]
    if errors:
        print(f"  erreur de prévision (prévision / commandes payables du mois suivant − 1) : médiane {100 * st.median(errors):+.1f} %,"
              f" écart absolu médian {100 * st.median(abs(e) for e in errors):.1f} % ;"
              f" mois surestimés > 10 % : {sum(1 for e in errors if e > 0.1)}, sous-estimés > 10 % : {sum(1 for e in errors if e < -0.1)}")


if __name__ == "__main__":
    months = int(sys.argv[1])
    seeds, extra = [], {}
    for arg in sys.argv[2:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            extra[k] = v
        else:
            seeds.append(int(arg))
    with Pool(min(8, len(seeds))) as pool:
        for seed, rows, warmup in pool.map(run, [(s, months, extra) for s in seeds]):
            summarize(seed, rows, warmup)
