"""État des entreprises d'un secteur, mois par mois (lecture seule).

Usage depuis la racine : python diagnostic/zombie.py graine mois_observes secteur pas
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from economy.models import SimulationConfig  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402
from economy.finance import sector_entry_signals  # noqa: E402

seed, months, sector, stride = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
over = {}


class Sim(EconomySimulation):
    def step(self):
        rec = super().step()
        if self.month % stride == 0:
            live = self.live
            firms = [c for c in self.companies if c.active and c.sector == sector]
            sig = sector_entry_signals(self.companies, live, self.wage_reference,
                                       ("food", "goods", "technology", "real_estate", "materials", "energy", "services"),
                                       self.policy_rate)
            unmet = self._unmet_ratio.get(sector, 0.0)
            print(f"mois {self.month - 120:+4d} non servi {100 * unmet:4.0f}% signal entrée {sig.get(sector, 0):.3f} "
                  f"total {sum(max(0, v) for v in sig.values()):.3f} réf.salaire {self.wage_reference:8.0f} "
                  f"E {self.resources.energy_price_index:6.2f} matières {self.resources.raw_material_price:7.1f} "
                  f"banques {sum(1 for c in self.companies if c.active and c.sector == 'bank')} capcr {self.credit_capacity():9.0f}")
            for c in firms:
                print(f"   {c.id[:22]:22s} âge {c.age_months:4d} cash {c.cash:10.0f} dette {c.debt:9.0f} sal {len(c.employees):3d}"
                      f"/{c.target_employees:3d}/{c.staffing_need:3d} offre {c.wage_offer:7.0f} capa {c.production_capacity:7.1f}"
                      f" pot {c.potential_output:7.1f} prod {c.units_produced:7.1f} vend {c.units_sold:7.1f}"
                      f" nonserv {c.unmet_units:7.1f} prix {c.price:7.1f} marge {c.price_markup:5.2f}"
                      f" prév {c.demand_forecast:7.1f} stock {c.inventory if sector == 'food' else c.goods_inventory:7.1f}"
                      f" fill {c.energy_fill:4.2f} CA {c.revenue:9.0f} profit {c.profit:9.0f} intr/u {c.unit_input_cost:6.2f}")
        return rec


sim = Sim(SimulationConfig(seed=seed, **over))
for _ in range(months):
    sim.step()
