"""Trace du logement (lecture seule) : foncier, parc, sans-logement, chantiers, loyers.

Usage : python diagnostic/housing_trace.py graine mois_observés pas [paramètre=valeur ...]
Configuration unique ; les paramètres donnés la modifient.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from economy.models import SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402

seed, months, stride = (int(a) for a in sys.argv[1:4])
overrides = {}
for kv in sys.argv[4:]:
    k, v = kv.split("=")
    overrides[k] = v


class Sim(EconomySimulation):
    def step(self):
        record = super().step()
        if self.month % stride == 0:
            promoters = [c for c in self.companies if c.active and c.sector == "real_estate"]
            stock = sum(c.property_inventory for c in promoters)
            pipeline = sum(len(c.construction_pipeline) for c in promoters)
            homeless = [p for p in self.people if not p.owns_home and p.rental_owner_id is None]
            renters = sum(p.rental_owner_id is not None for p in self.people)
            owners = sum(p.owns_home for p in self.people)
            gap = sum(c.housing_gap for c in promoters)
            cash_homeless = sorted(p.cash for p in homeless)
            print(f"mois {self.month - 120:+5d} pop {len(self.people)} propriétaires {owners} locataires {renters} sans-log {len(homeless)} "
                  f"parc locatif {stock} chantiers {pipeline} terrains {self.resources.land_units} écart prévu {gap} promoteurs {len(promoters)} "
                  f"loyer/salaire {self.rent / max(self.wage_reference, 1):.2f} coût chantier {self.live.housing_build_cost:9.0f} "
                  f"prix logement {self.live.housing_price:9.0f} trésorerie médiane des sans-log {cash_homeless[len(cash_homeless) // 2] if cash_homeless else 0:8.0f}")
        return record


sim = Sim(SimulationConfig(seed=seed, **overrides))
for _ in range(months):
    sim.step()
