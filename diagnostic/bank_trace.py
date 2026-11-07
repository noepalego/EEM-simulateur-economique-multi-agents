"""Trace des banques (lecture seule) : effectifs, offres, fonds propres, pertes, résolutions.

Usage : python diagnostic/bank_trace.py graine mois_observés pas [paramètre=valeur ...]
Configuration unique ; les paramètres donnés la modifient.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from economy.models import SimulationConfig, positive_loan_book  # noqa: E402
from economy.simulation import EconomySimulation  # noqa: E402

seed, months, stride = (int(a) for a in sys.argv[1:4])
overrides = {}
for kv in sys.argv[4:]:
    k, v = kv.split("=")
    overrides[k] = v


class Sim(EconomySimulation):
    def step(self):
        record = super().step()
        for event in self.resolution_events:
            if event.month == self.month and event.kind.startswith("bank"):
                print(f"   ** mois {self.month - 120:+d} résolution {event.kind} {event.company_id} "
                      f"perte créanciers {event.creditor_loss:.0f} coût public {event.public_cost:.0f} "
                      f"repris {event.absorbed_by_acquirer:.0f}")
        if self.month % stride == 0:
            banks = [c for c in self.companies if c.sector == "bank" and (c.active or c.exit_month > self.month - stride)]
            employed = sum(p.employer_id is not None for p in self.people)
            print(f"mois {self.month - 120:+5d} réf.salaire {self.wage_reference:9.0f} chômage {100 * record.unemployment:4.1f} "
                  f"taux {1200 * self.policy_rate:4.1f} capcr {self.credit_capacity():12.0f} employés {employed}")
            for b in banks:
                loans = positive_loan_book(b.bank_loan_accounts)
                print(f"   {b.id:10s} {'actif' if b.active else 'fermé':5s} sal {len(b.employees):2d}/{b.target_employees:2d}/{b.staffing_need:2d} "
                      f"offre/réf {b.wage_offer / max(self.wage_reference, 1):5.2f} fonds propres {b.bank_equity:12.0f} prêts {loans:12.0f} "
                      f"ratio {b.bank_equity / max(loans + b.bond_holdings, 1):.3f} pertes cumulées {b.default_loss:11.0f} "
                      f"CA {b.revenue:9.0f} charges {b.expenses:9.0f} résultat {b.profit:9.0f} trésorerie {b.cash:11.0f}")
        return record


sim = Sim(SimulationConfig(seed=seed, **overrides))
for _ in range(months):
    sim.step()
