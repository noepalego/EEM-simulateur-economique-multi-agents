"""Identité des avances préparées, interventions, monde et replis du cache."""

import copy
import pickle
import math
import random
import threading
import time
import unittest
from unittest.mock import patch

from economy import advance_cache, diagnostics
from economy.advance_cache import AdvanceCache
from economy.enterprise import _StyleIndex, taste_fit
from economy.interventions import adjust_household_cash
from economy.models import Company, LoanAccounts, Person, SimulationConfig
from economy.real_estate import allocate_housing_and_collect_rent
from economy.simulation import EconomySimulation
from economy.world import World, WorldConfig
from performance_probe import digest


class BrokenEngine:
    month = 0

    def step(self):
        raise RuntimeError("échec de l'avance")


class GatedEngine:
    entered = threading.Event()
    released = threading.Event()
    observed = None

    def __init__(self, stop_at):
        self.month = 0
        self.stop_at = stop_at
        self.values = []

    def step(self):
        self.month += 1
        if self.month == self.stop_at:
            type(self).entered.set()
            if not type(self).released.wait(10):
                raise RuntimeError("le test n'a pas libéré le calcul")
            type(self).observed = list(self.values)


class AdvanceCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = AdvanceCache()
        self.sim = EconomySimulation(SimulationConfig(seed=31, population_size=40, warmup_months=3))

    def tearDown(self):
        self.cache.invalidate()
        self.finish()

    def finish(self):
        thread = self.cache._thread
        if thread is not None:
            thread.join(15)
            self.assertFalse(thread.is_alive(), "le calcul préparatoire n'a pas terminé")

    def test_preparing_keeps_visible_state_and_exact_future_including_random_streams(self):
        before = digest(self.sim)
        direct = copy.deepcopy(self.sim)
        self.assertTrue(self.cache.prepare(self.sim, "vue", 12))
        self.finish()
        self.assertEqual(digest(self.sim), before)
        prepared = self.cache.take(self.sim, "vue", 12)
        self.assertIsNotNone(prepared)
        for _ in range(12):
            direct.step()
        self.assertEqual(digest(prepared), digest(direct))
        for _ in range(6):
            self.assertEqual(repr(prepared.step()), repr(direct.step()))

    def test_loan_book_copy_preserves_values_versions_and_attribute_order(self):
        book = LoanAccounts({"first": 50.0})
        book.positive_book()
        book["second"] = 75.0
        for clone in (copy.deepcopy(book), pickle.loads(pickle.dumps(book))):
            self.assertEqual(digest(clone), digest(book))
            clone["first"] = 10.0
            self.assertEqual(clone.positive_book(), 85.0)
        self.assertEqual(book["first"], 50.0)

    def test_one_month_is_available_even_for_a_longer_batch(self):
        direct = copy.deepcopy(self.sim)
        direct.step()
        self.cache.prepare(self.sim, "vue", 12)
        self.finish()
        self.assertEqual(digest(self.cache.take(self.sim, "vue", 1)), digest(direct))

    def test_intervention_and_revision_discard_an_obsolete_future(self):
        self.cache.prepare(self.sim, "avant", 2)
        self.finish()
        adjust_household_cash(self.sim, self.sim.people[0].id, 1000.0)
        self.assertIsNone(self.cache.take(self.sim, "après", 2))
        direct = copy.deepcopy(self.sim)
        for _ in range(2):
            direct.step()
        self.cache.prepare(self.sim, "après", 2)
        self.finish()
        self.assertEqual(digest(self.cache.take(self.sim, "après", 2)), digest(direct))

    def test_other_engine_or_month_cannot_receive_a_prepared_state(self):
        self.cache.prepare(self.sim, "vue", 1)
        self.finish()
        self.assertIsNone(self.cache.take(copy.deepcopy(self.sim), "vue", 1))
        self.cache.prepare(self.sim, "vue", 1)
        self.finish()
        self.sim.step()
        self.assertIsNone(self.cache.take(self.sim, "vue", 1))

    def test_world_keeps_cross_region_state_and_shared_references(self):
        world = World(WorldConfig(base=self.sim.config, regions=2, profiles=()))
        direct = copy.deepcopy(world)
        self.cache.prepare(world, "monde", 3)
        self.finish()
        prepared = self.cache.take(world, "monde", 3)
        self.assertIsNotNone(prepared)
        for _ in range(3):
            direct.step()
        self.assertEqual(digest(prepared), digest(direct))
        self.assertIs(prepared.pools[0], prepared.regions[0].world_accounts[0])
        self.assertIs(prepared.nonresidents[1], prepared.regions[1].nonresident)
        self.assertEqual(repr(prepared.step()), repr(direct.step()))

    def test_cancellation_or_unready_result_falls_back_without_waiting(self):
        self.assertIsNone(self.cache.take(self.sim, "vue", 1))
        self.cache.prepare(self.sim, "vue", 12)
        self.cache.invalidate()
        self.finish()
        self.assertIsNone(self.cache.take(self.sim, "vue", 12))

    def test_a_new_preparation_replaces_a_running_obsolete_job(self):
        GatedEngine.entered.clear()
        GatedEngine.released.clear()
        old, new = GatedEngine(1), GatedEngine(-1)
        self.cache.prepare(old, "ancienne", 2)
        try:
            self.assertTrue(GatedEngine.entered.wait(5))
            self.cache.prepare(new, "nouvelle", 1)
        finally:
            GatedEngine.released.set()
        self.finish()
        prepared = self.cache.take(new, "nouvelle", 1)
        self.assertIsNotNone(prepared)
        self.assertEqual((old.month, new.month, prepared.month), (0, 0, 1))

    def test_adopting_one_month_cannot_mutate_the_remaining_background_work(self):
        GatedEngine.entered.clear()
        GatedEngine.released.clear()
        source = GatedEngine(2)
        self.cache.prepare(source, "vue", 3)
        try:
            self.assertTrue(GatedEngine.entered.wait(5))
            prepared = self.cache.take(source, "vue", 1)
            self.assertIsNotNone(prepared)
            prepared.values.append("intervention après adoption")
        finally:
            GatedEngine.released.set()
        self.finish()
        self.assertEqual(GatedEngine.observed, [])
        self.assertEqual(source.values, [])

    def test_limits_and_worker_errors_keep_the_visible_engine_intact(self):
        with patch.object(advance_cache, "MAX_SNAPSHOT_BYTES", 1):
            self.assertFalse(self.cache.prepare(self.sim, "vue", 1))
        self.assertIsNone(self.cache.take(self.sim, "vue", 1))
        broken = BrokenEngine()
        self.assertTrue(self.cache.prepare(broken, "vue", 1))
        self.finish()
        self.assertIsNone(self.cache.take(broken, "vue", 1))
        self.assertEqual(broken.month, 0)

    def test_large_batches_only_prepare_one_month_and_diagnostics_skip_preparation(self):
        with patch.object(advance_cache, "MAX_MONTHS", 12):
            self.cache.prepare(self.sim, "vue", 120)
            self.finish()
            self.assertIsNone(self.cache.take(self.sim, "vue", 120))
        diagnostics.start()
        try:
            self.assertFalse(self.cache.prepare(self.sim, "vue", 1))
            self.assertEqual(diagnostics.snapshot()["bites"], {})
        finally:
            diagnostics.stop()


class AdvanceProgressTests(unittest.TestCase):
    """Troisième passe (11 novembre 2026) : préparation longue, conservée d'un rendu à l'autre
    tant que l'état n'a pas changé, et reprise partielle au clic."""

    def setUp(self):
        self.cache = AdvanceCache()
        self.sim = EconomySimulation(SimulationConfig(seed=47, population_size=40, warmup_months=3))

    def tearDown(self):
        self.cache.invalidate()
        self.finish()

    def finish(self):
        thread = self.cache._thread
        if thread is not None:
            thread.join(15)
            self.assertFalse(thread.is_alive(), "le calcul préparatoire n'a pas terminé")

    def direct(self, months):
        clone = copy.deepcopy(self.sim)
        for _ in range(months):
            clone.step()
        return clone

    def test_a_long_batch_is_prepared_entirely_and_is_exact(self):
        expected = self.direct(30)
        self.assertTrue(self.cache.prepare(self.sim, "vue", 30))
        self.finish()
        prepared, done = self.cache.take_progress(self.sim, "vue", 30)
        self.assertEqual(done, 30)
        self.assertEqual(digest(prepared), digest(expected))

    def test_unchanged_state_keeps_the_running_preparation_across_renders(self):
        GatedEngine.entered.clear()
        GatedEngine.released.clear()
        source = GatedEngine(3)
        self.cache.prepare(source, "premier rendu", 4)
        try:
            self.assertTrue(GatedEngine.entered.wait(5))
            job = self.cache._job
            self.cache.hold()  # nouveau rendu : rien n'est adoptable avant confirmation
            self.assertIsNone(self.cache._current(source, "premier rendu"))
            self.assertTrue(self.cache.prepare(source, "second rendu", 4))
            self.assertIs(self.cache._job, job)  # même travail, non recommencé
        finally:
            GatedEngine.released.set()
        self.finish()
        self.assertIsNone(self.cache._current(source, "premier rendu"))
        prepared, done = self.cache.take_progress(source, "second rendu", 4)
        self.assertEqual((done, prepared.month, source.month), (4, 4, 0))

    def test_a_render_without_confirmation_makes_the_preparation_unusable(self):
        self.cache.prepare(self.sim, "vue", 2)
        self.finish()
        self.cache.hold()
        self.assertIsNone(self.cache.take_progress(self.sim, "vue", 2))

    def test_changed_state_restarts_from_the_new_state(self):
        self.cache.prepare(self.sim, "avant", 3)
        self.finish()
        adjust_household_cash(self.sim, self.sim.people[0].id, 1000.0)
        expected = self.direct(3)
        self.cache.hold()
        self.assertTrue(self.cache.prepare(self.sim, "après", 3))
        self.finish()
        prepared, done = self.cache.take_progress(self.sim, "après", 3)
        self.assertEqual(done, 3)
        self.assertEqual(digest(prepared), digest(expected))

    def test_partial_progress_is_resumed_and_matches_direct_run(self):
        expected = self.direct(40)
        self.cache.prepare(self.sim, "vue", 40)
        deadline = time.monotonic() + 15
        while self.cache._job.done < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        prepared, done = self.cache.take_progress(self.sim, "vue", 40)
        self.finish()
        self.assertTrue(1 <= done <= 40)
        self.assertEqual(prepared.month, self.sim.month + done)
        for _ in range(40 - done):
            prepared.step()
        self.assertEqual(digest(prepared), digest(expected))

    def test_extending_the_period_continues_and_shortening_restarts(self):
        self.cache.prepare(self.sim, "vue", 4)
        self.finish()
        job = self.cache._job
        self.cache.hold()
        self.cache.prepare(self.sim, "plus long", 6)
        self.assertIs(self.cache._job, job)
        self.finish()
        self.cache.hold()
        self.cache.prepare(self.sim, "plus court", 2)
        self.assertIsNot(self.cache._job, job)
        self.finish()
        prepared, done = self.cache.take_progress(self.sim, "plus court", 2)
        self.assertEqual(done, 2)
        self.assertEqual(digest(prepared), digest(self.direct(2)))

    def test_progress_is_never_handed_beyond_the_requested_months(self):
        self.cache.prepare(self.sim, "vue", 6)
        self.finish()
        prepared, done = self.cache.take_progress(self.sim, "vue", 3)
        self.assertEqual(done, 1)  # seul le premier mois, détaché, précède la demande
        self.assertEqual(digest(prepared), digest(self.direct(1)))

    def test_world_partial_progress_keeps_shared_references(self):
        world = World(WorldConfig(base=self.sim.config, regions=2, profiles=()))
        direct = copy.deepcopy(world)
        self.cache.prepare(world, "monde", 5)
        self.finish()
        prepared, done = self.cache.take_progress(world, "monde", 8)
        self.assertEqual(done, 5)
        for _ in range(3):
            prepared.step()
        for _ in range(8):
            direct.step()
        self.assertEqual(digest(prepared), digest(direct))
        self.assertIs(prepared.pools[0], prepared.regions[0].world_accounts[0])


class HousingAllocationOptimisationTests(unittest.TestCase):
    def test_existing_leases_lost_landlord_and_full_capacity_keep_priority_and_payments(self):
        first = Company("first", "Premier", "real_estate", 0.0, 0.0, property_inventory=1)
        empty = Company("empty", "Vide", "real_estate", 0.0, 0.0, property_inventory=0)
        second = Company("second", "Second", "real_estate", 0.0, 0.0, property_inventory=2)
        people = [Person(f"p{i}", str(i), 1000.0, income_this_turn=200.0) for i in range(5)]
        people[0].rental_owner_id = "second"
        people[1].rental_owner_id = "disparu"
        people[2].rental_owner_id = "first"
        people[3].rental_owner_id = "second"
        people[4].rental_owner_id = "empty"
        paid = allocate_housing_and_collect_rent([first, empty, second], people, 100.0)
        self.assertEqual([p.rental_owner_id for p in people], ["second", "first", "second", None, None])
        self.assertEqual(paid, 300.0)
        self.assertEqual((first.cash, empty.cash, second.cash), (100.0, 0.0, 200.0))
        self.assertEqual((first.occupied_units, empty.occupied_units, second.occupied_units), (1, 0, 2))
        self.assertEqual((first.unserved_rentals, empty.unserved_rentals, second.unserved_rentals), (1, 1, 0))

    def test_household_landlord_receives_income_before_paying_its_own_rent(self):
        firm = Company("firm", "Promoteur", "real_estate", 0.0, 0.0, property_inventory=0)
        tenant = Person("tenant", "Locataire", 100.0, income_this_turn=100.0)
        owner = Person("owner", "Bailleur", 0.0, investment_homes=2, income_this_turn=0.0)
        paid = allocate_housing_and_collect_rent([firm], [tenant, owner], 100.0)
        self.assertEqual(paid, 200.0)
        self.assertEqual((tenant.cash, owner.cash), (0.0, 100.0))
        self.assertEqual(owner.rented_homes, 2)


class PreferenceSearchOptimisationTests(unittest.TestCase):
    def test_circular_search_matches_exhaustive_scores_and_ties(self):
        rng = random.Random(79)
        cases = [[0.0], [0.0, 1.0], [0.25, 0.75], [0.5] * 20,
                 [0.5, math.nextafter(0.5, 1.0), math.nextafter(0.5, 0.0)],
                 [-1.0, 0.1, 1.2]]
        cases.extend([rng.random() for _ in range(n)] for n in (2, 3, 10, 50, 200))
        for positions in cases:
            firms = [Company(str(i), str(i), "food", 0.0, 0.0, product_style=s)
                     for i, s in enumerate(positions)]
            index = _StyleIndex(firms)
            tastes = [0.0, 0.25, 0.5, 0.75, 1.0, math.nextafter(0.0, 1.0),
                      math.nextafter(1.0, 0.0), *positions]
            tastes.extend(rng.random() for _ in range(1000))
            for taste in tastes:
                expected = max(firms, key=lambda c: (taste_fit(taste, c.product_style), c.id))
                self.assertIs(index.closest(taste), expected, (taste, positions))


if __name__ == "__main__":
    unittest.main()
