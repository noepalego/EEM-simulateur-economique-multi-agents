"""Identité exacte d'un lot d'achats ménagers et du chemin scalaire historique."""

import copy
import random
import unittest

from economy import diagnostics
from economy.intersector import household_suppliers, pay_suppliers_from_household
from economy.models import Company, Person, Resources
from performance_probe import digest


def fixture():
    companies = [
        Company("s2", "Service 2", "services", 123.125, 0.0,
                employees=["a", "b", "c"], productivity_trait=1.00000000000001),
        Company("empty", "Sans salarié", "services", 0.0, 0.0),
        Company("m1", "Matières", "materials", 1e12, 0.0,
                employees=["d"], productivity_trait=0.1),
        Company("s1", "Service 1", "services", 1e-7, 0.0,
                employees=["e"], productivity_trait=0.3333333333333333),
        Company("closed", "Fermée", "materials", 55.0, 0.0,
                active=False, employees=["f"]),
        Company("s3", "Service 3", "services", 50.0, 0.0,
                employees=["g"], productivity_trait=2**40 + 0.125),
    ]
    people = [Person(f"p{i}", str(i), cash, expenses_this_turn=1.0 / 3.0,
                     cumulative_expenses=1e12 + i)
              for i, cash in enumerate((0.0, -3.0, 1e-14, 0.125, 93.75, 1e9, 11.0))]
    resources = Resources(raw_materials=1000.0, land_units=20, knowledge=50.0,
                          refused_orders={"ancien": 0.125, "absent": 1e-14})
    return companies, people, resources


class HouseholdSupplierBatchTests(unittest.TestCase):
    def tearDown(self):
        diagnostics.stop()

    def compare_batch(self, split, amounts, *, tracking=False, scalar_index=True, data=None):
        data = fixture() if data is None else data
        expected = copy.deepcopy(data)
        actual = copy.deepcopy(data)

        def run(data, prepared):
            companies, people, resources = data
            if tracking:
                diagnostics.start(sample_limit=10)
                diagnostics.set_month(17)
            index = household_suppliers(companies, tuple(split), split) if prepared else (
                household_suppliers(companies, tuple(split)) if scalar_index else None
            )
            results = []
            for index_person, amount in enumerate(amounts):
                person = people[index_person % len(people)]
                results.append(index.pay(person, amount, resources) if prepared else
                               pay_suppliers_from_household(person, amount, split, companies,
                                                            resources, index))
            snapshots = diagnostics.snapshot()
            diagnostics.stop()
            return digest((data, results, snapshots))

        self.assertEqual(run(actual, True), run(expected, False), (split, amounts, tracking))

    def test_amount_limits_and_missing_suppliers_match_both_scalar_paths(self):
        amounts = [-1e10, 0.0, 1e-15, 100.0, 13.125, 1e12, 1e-10, 57.0, 2e12]
        for index in (True, False):
            for tracking in (True, False):
                self.compare_batch({"services": 0.7, "absent": 0.2, "materials": 0.1},
                                   amounts, scalar_index=index, tracking=tracking)

    def test_float_order_zero_negative_and_tiny_splits_are_exact(self):
        for split in ({}, {"services": 1.0}, {"services": 0.0, "absent": -0.0},
                      {"absent": -4.0, "services": -0.125, "materials": 0.0},
                      {"services": 1e-15, "materials": 2e-15, "absent": 3e-15},
                      {"services": float(2**53), "absent": 1.0, "materials": 1.0},
                      {"services": 1.0, "materials": float(2**53), "absent": 1.0}):
            self.compare_batch(split, [41.25] * 21, tracking=True)

    def test_preparation_keeps_public_couples_and_does_not_mutate_agents(self):
        companies, people, resources = fixture()
        original = digest((companies, people, resources))
        plain = household_suppliers(companies, ("services", "materials", "absent"))
        batch = household_suppliers(companies, tuple(plain), {"services": 0.7, "materials": 0.3})
        self.assertIs(type(plain), dict)
        self.assertIsInstance(batch, dict)
        self.assertEqual(batch, plain)
        self.assertEqual(digest((companies, people, resources)), original)
        self.assertEqual([company.id for company in batch["services"][0]], ["s2", "s1", "s3"])

    def test_new_plan_captures_its_split_without_changing_scalar_api(self):
        companies, people, resources = fixture()
        split = {"services": 0.7, "materials": 0.3}
        original_split = dict(split)
        batch = household_suppliers(companies, tuple(split), split)
        scalar = copy.deepcopy((companies, people, resources))
        split.clear()
        split.update(materials=1.0, services=-5.0)
        paid = batch.pay(people[-1], 9.999999, resources)
        e_companies, e_people, e_resources = scalar
        expected_paid = pay_suppliers_from_household(e_people[-1], 9.999999, original_split,
                                                     e_companies, e_resources)
        self.assertEqual(digest((companies, people, resources, paid)),
                         digest((e_companies, e_people, e_resources, expected_paid)))
        # Un dictionnaire préparé peut aussi servir d'index au chemin public,
        # qui lit alors la répartition fournie, comme un dictionnaire ordinaire.
        expected = copy.deepcopy((companies, people, resources))
        expected_index = household_suppliers(expected[0], tuple(split))
        paid = pay_suppliers_from_household(people[-2], 1.0, split, companies, resources, batch)
        expected_paid = pay_suppliers_from_household(expected[1][-2], 1.0, split,
                                                     expected[0], expected[2], expected_index)
        self.assertEqual(digest((companies, people, resources, paid)),
                         digest((*expected, expected_paid)))

    def test_zero_supplier_and_zero_share_keep_refusals_and_trace_order(self):
        companies, people, resources = fixture()
        for company in companies:
            company.employees.clear()
        self.compare_batch({"services": 0.0, "materials": 1.0, "absent": -2.0},
                           [25.5] * 21, tracking=True, data=(companies, people, resources))

    def test_random_batches_match_scalar_state_and_diagnostics(self):
        rng = random.Random(827)
        for sample in range(40):
            companies, people, resources = fixture()
            for company in companies:
                company.productivity_trait = rng.choice((0.1, 0.25, 1.0, 1e-12, rng.random() * 4.0))
            split = dict(rng.sample([("services", rng.uniform(-0.1, 1.0)),
                                     ("materials", rng.uniform(-0.1, 1.0)),
                                     ("absent", rng.uniform(-0.1, 1.0))], 3))
            amounts = [rng.uniform(-10.0, 150.0) for _ in range(30)]
            self.compare_batch(split, amounts, tracking=sample % 2 == 0,
                               data=(companies, people, resources))


if __name__ == "__main__":
    unittest.main()
