"""Non-régression des optimisations : dividendes et cache de l'état après chauffe.

Une simulation rechargée depuis le cache doit être identique à une simulation neuve et continuer
exactement la même trajectoire ; la clé change avec la configuration ; le cache disque se relit.
"""
import pickle
import tempfile
import unittest
from pathlib import Path

from economy import warmup_cache
from economy.models import Company, LoanAccounts, Person, SimulationConfig
from economy.simulation import EconomySimulation
from economy.stock_market import distribute_dividends


class DividendOptimisationTests(unittest.TestCase):
    def test_bank_without_shareholders_does_not_refresh_loan_cache(self) -> None:
        bank = Company("bank", "Banque", "bank", 1000.0, 10.0, profit=100.0)
        bank.bank_loan_accounts = LoanAccounts({"borrower": 100.0})
        before = dict(vars(bank.bank_loan_accounts))
        self.assertEqual(distribute_dividends([bank], [], bank_excess_payout_rate=0.1), 0.0)
        self.assertEqual(vars(bank.bank_loan_accounts), before)

    def test_cross_holdings_keep_payment_order_and_extra_holders(self) -> None:
        first = Company("c1", "Première", "food", 1000.0, 10.0, profit=100.0,
                        shares={"c1": 999.0, "c2": 2.0})
        second = Company("c2", "Seconde", "food", 0.0, 10.0, profit=100.0,
                         shares={"c1": 3.0})
        person = Person("p1", "Ménage", 0.0, shares={"c1": 1.0, "c2": 1.0})
        extra = Company("fund", "Fonds", "services", 0.0, 10.0,
                        shares={"c1": 2.0, "c2": 3.0})
        # La seconde entreprise paie avec les dividendes qu'elle vient de recevoir.
        first_payout = 100.0 * (0.12 * (1.0 - first.reinvestment_preference * 0.75))
        second_payout = first_payout * 3.0 / 6.0
        paid = distribute_dividends([first, second], [person], extra_holders=(extra,))
        self.assertEqual(paid, first_payout + second_payout)
        self.assertEqual(first.cash, 1000.0 - first_payout + second_payout * 2.0 / 6.0)
        self.assertEqual(second.cash, 0.0)
        self.assertEqual(person.cash, first_payout * 1.0 / 6.0 + second_payout * 1.0 / 6.0)
        self.assertEqual(person.dividend_income_this_turn, person.cash)
        self.assertEqual(extra.financial_income,
                         first_payout * 2.0 / 6.0 + second_payout * 3.0 / 6.0)

    def test_no_payment_resets_previous_dividend_without_changing_holdings(self) -> None:
        companies = [Company(str(i), str(i), "food", 1000.0, 10.0,
                             profit=100.0, dividend_paid=99.0) for i in range(5)]
        companies[0].active = False
        companies[2].profit = 0.0
        person = Person("p", "Ménage", 50.0, shares={"0": 1.0, "1": 1.0,
                                                    "2": 1.0, "3": 0.0})
        holdings = dict(person.shares)
        paid = distribute_dividends(companies, [person], retained=frozenset({"1"}))
        self.assertEqual(paid, 0.0)
        self.assertEqual([c.dividend_paid for c in companies], [0.0] * 5)
        self.assertEqual(person.cash, 50.0)
        self.assertEqual(person.shares, holdings)


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
