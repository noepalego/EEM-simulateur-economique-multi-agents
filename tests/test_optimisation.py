"""Optimisation du 7 novembre 2026 : cache de l'état après chauffe (``economy.warmup_cache``).

Une simulation rechargée depuis le cache doit être identique à une simulation neuve et continuer
exactement la même trajectoire ; la clé change avec la configuration ; le cache disque se relit.
"""
import pickle
import tempfile
import unittest
from pathlib import Path

from economy import warmup_cache
from economy.models import SimulationConfig
from economy.simulation import EconomySimulation


def small(**overrides) -> SimulationConfig:
    return SimulationConfig(seed=31, population_size=60, warmup_months=24, **overrides)


class WarmupCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        warmup_cache.clear_memory()
        self._dir = tempfile.TemporaryDirectory()
        self._saved = warmup_cache.CACHE_DIR
        warmup_cache.CACHE_DIR = Path(self._dir.name)

    def tearDown(self) -> None:
        warmup_cache.CACHE_DIR = self._saved
        warmup_cache.clear_memory()
        self._dir.cleanup()

    def test_a_cached_simulation_continues_exactly_like_a_fresh_one(self) -> None:
        fresh = EconomySimulation(small())
        first = warmup_cache.warmed_simulation(small())   # calculée puis mémorisée
        second = warmup_cache.warmed_simulation(small())  # relue en mémoire
        self.assertIsNot(first, second)
        for _ in range(24):
            expected = repr(fresh.step())
            self.assertEqual(repr(first.step()), expected)
            self.assertEqual(repr(second.step()), expected)
        self.assertEqual(fresh.random.getstate(), second.random.getstate())
        self.assertEqual(fresh.demography_random.getstate(), second.demography_random.getstate())

    def test_copies_are_independent(self) -> None:
        one = warmup_cache.warmed_simulation(small())
        one.step()
        two = warmup_cache.warmed_simulation(small())
        self.assertEqual(two.month, 24)
        self.assertEqual(one.month, 25)

    def test_the_key_follows_every_setting_and_the_code(self) -> None:
        self.assertEqual(warmup_cache.cache_key(small()), warmup_cache.cache_key(small()))
        self.assertNotEqual(warmup_cache.cache_key(small()), warmup_cache.cache_key(small(vat_rate=0.11)))
        self.assertNotEqual(warmup_cache.cache_key(small()),
                            warmup_cache.cache_key(SimulationConfig(seed=32, population_size=60, warmup_months=24)))
        self.assertEqual(len(warmup_cache.source_fingerprint()), 64)

    def test_disk_cache_is_reread_and_a_corrupt_file_is_recomputed(self) -> None:
        warmup_cache.warmed_simulation(small())
        files = list(warmup_cache.CACHE_DIR.glob("*.pkl"))
        self.assertEqual(len(files), 1)
        warmup_cache.clear_memory()
        reread = warmup_cache.warmed_simulation(small())
        self.assertEqual(repr(reread.step()), repr(EconomySimulation(small()).step()))
        files[0].write_bytes(b"pas un pickle")
        warmup_cache.clear_memory()
        recomputed = warmup_cache.warmed_simulation(small())
        self.assertEqual(recomputed.month, 24)
        self.assertIsInstance(pickle.loads(files[0].read_bytes()), EconomySimulation)


if __name__ == "__main__":
    unittest.main()
