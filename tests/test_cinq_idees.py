"""Mécanismes ajoutés par la mission « cinq idées » (6 novembre 2026, docs/CINQ_IDEES_2026-11.md).

1. Modération salariale : chômage de longue durée, recettes qui ne couvrent pas la paie.
2. Entreprises sans salarié : reprise selon la valeur d'un poste, liquidation amiable.
3. Démographie : âge, mortalité, succession dans le ménage, droits de succession.
4. Vote individuel des ménages.
5. Éducation publique : compétence de départ de la génération suivante.
"""
import math
import random
import unittest

from economy.business import adjust_profit_sharing_wages
from economy.demography import (
    annual_mortality,
    draw_initial_age,
    life_expectancy,
    monthly_mortality,
    stationary_age_table,
)
from economy.finance import can_dissolve, dissolve_company
from economy.institutions import (
    SERVICES,
    Voter,
    challenger_orientation,
    effective_lean,
    incumbent_support,
    individual_election,
)
from economy.labor import allocate_and_pay_labor
from economy.models import Company, Person, SimulationConfig
from economy.records import MonthFlows
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def small(seed: int = 5, months: int = 0, **overrides) -> EconomySimulation:
    simulation = EconomySimulation(SimulationConfig(seed=seed, population_size=60, **overrides))
    for _ in range(months):
        simulation.step()
    return simulation


def money(simulation: EconomySimulation) -> float:
    return sum(p.cash for p in simulation.people) + sum(c.cash for c in simulation.companies) + simulation.public.cash


class WageModerationTests(unittest.TestCase):
    """Idée 1 : l'offre baisse en chômage de longue durée et quand les recettes ne paient pas."""

    def _firm(self, **fields) -> Company:
        base = dict(employees=["a", "b"], wage_bill=2000.0, staffing_need=2, target_employees=2,
                    revenue_ema=10_000.0, expenses=2000.0, profit=8000.0, revenue=10_000.0)
        base.update(fields)
        return Company("food_1", "Ferme", "food", 50_000.0, 1000.0, **base)

    def _offer(self, firm: Company, long_term: float, config: SimulationConfig | None = None) -> float:
        adjust_profit_sharing_wages([firm], [], 1000.0, 0.0, 0.70, 0.03, {}, config or SimulationConfig(),
                                    long_term_unemployment=long_term)
        return firm.wage_offer

    def test_a_fully_staffed_firm_moderates_its_offer_only_under_long_term_unemployment(self) -> None:
        self.assertAlmostEqual(self._offer(self._firm(), 0.0), 1000.0)
        self.assertAlmostEqual(self._offer(self._firm(), 0.20), 1000.0 * (1.0 - 0.03 * 0.20))
        neutral = SimulationConfig(wage_slack_speed=0.0)
        self.assertAlmostEqual(self._offer(self._firm(), 0.20, neutral), 1000.0)

    def test_a_firm_with_vacancies_does_not_moderate(self) -> None:
        firm = self._firm(staffing_need=4, target_employees=4)
        self.assertGreater(self._offer(firm, 0.20), 1000.0)

    def test_revenue_that_does_not_cover_payroll_cuts_the_offer_and_counts_no_vacancy(self) -> None:
        # recette lissée 800 € pour 2 000 € de paie : couverture 0,4 sous 0,8 ; besoin 5 salariés,
        # 2 en poste : sans la règle, les 3 postes « vacants » relevaient l'offre
        firm = self._firm(revenue_ema=800.0, revenue=800.0, profit=-1200.0, staffing_need=5, target_employees=5)
        offer = self._offer(firm, 0.0)
        self.assertLess(offer, 1000.0)
        self.assertAlmostEqual(offer, 1000.0 * (1.0 - 0.05 * 0.5))

    def test_long_term_unemployment_counts_people_without_work_for_twelve_months(self) -> None:
        simulation = small(months=1)
        for index, person in enumerate(simulation.people):
            person.unemployment_months = 12 if index < 6 else 3
        self.assertAlmostEqual(simulation.long_term_unemployment_share(), 6 / 60)

    def test_unemployment_months_follow_the_labor_market(self) -> None:
        worker = Person("w", "W", 0.0, unemployment_months=5)
        allocate_and_pay_labor([], [worker], 1000.0, SimulationConfig())
        self.assertEqual(worker.unemployment_months, 6)
        firm = Company("goods_1", "Atelier", "goods", 100_000.0, 1000.0, target_employees=1)
        allocate_and_pay_labor([firm], [worker], 1000.0, SimulationConfig())
        self.assertEqual(worker.employer_id, "goods_1")
        self.assertEqual(worker.unemployment_months, 0)


class IdleCompanyTests(unittest.TestCase):
    """Idée 2 : reprise d'une entreprise sans salarié, liquidation amiable."""

    def _shell(self, cash: float = 30_000.0) -> Company:
        return Company("services_9", "Coquille", "services", cash, 10_000.0, target_employees=3, age_months=40)

    def _people(self, count: int = 5) -> list[Person]:
        return [Person(f"p{i}", f"P{i}", 0.0) for i in range(count)]

    def test_a_worthwhile_restart_hires_what_cash_pays(self) -> None:
        shell, people = self._shell(), self._people()
        allocate_and_pay_labor([shell], people, 1000.0, SimulationConfig(), full_pay_hiring=True,
                               restart_values={shell.id: 12_000.0})
        # (30 000 − 250) // 10 000 = 2 postes finançables, sous la limite de 3 embauches
        self.assertEqual(len(shell.employees), 2)

    def test_no_restart_when_a_position_is_not_worth_its_cost(self) -> None:
        shell, people = self._shell(), self._people()
        allocate_and_pay_labor([shell], people, 1000.0, SimulationConfig(), full_pay_hiring=True,
                               restart_values={shell.id: 9_000.0})
        self.assertEqual(shell.employees, [])

    def test_an_empty_sector_always_tries_to_restart(self) -> None:
        simulation = small(months=2)
        energy = [c for c in simulation.companies if c.active and c.sector == "energy"]
        for company in energy:
            company.employees.clear()
            company.age_months = 10
        values = simulation._restart_values()
        for company in energy:
            self.assertTrue(math.isinf(values[company.id]))

    def test_dissolution_pays_cash_to_shareholders_and_creates_no_money(self) -> None:
        shell = self._shell(cash=9_000.0)
        held = Company("goods_1", "Atelier", "goods", 0.0, 1000.0)
        shell.shares = {"goods_1": 30.0}
        a, b = Person("a", "A", 0.0), Person("b", "B", 0.0)
        a.shares, b.shares = {shell.id: 200.0}, {shell.id: 100.0}
        self.assertTrue(can_dissolve(shell))
        before = a.cash + b.cash + shell.cash
        boni = dissolve_company(shell, [a, b], [shell, held], (), month=50, events=[])
        self.assertAlmostEqual(boni, 9_000.0)
        self.assertAlmostEqual(a.cash, 6_000.0)
        self.assertAlmostEqual(b.cash, 3_000.0)
        self.assertAlmostEqual(a.cash + b.cash + shell.cash, before)
        self.assertAlmostEqual(a.shares["goods_1"], 20.0)
        self.assertAlmostEqual(b.shares["goods_1"], 10.0)
        self.assertNotIn(shell.id, a.shares)
        self.assertFalse(shell.active)
        self.assertEqual(shell.exit_reason, "dissolved")

    def test_a_firm_with_debt_or_assets_is_never_dissolved(self) -> None:
        indebted = self._shell()
        indebted.debt = 10.0
        self.assertFalse(can_dissolve(indebted))
        landlord = self._shell()
        landlord.property_inventory = 2
        self.assertFalse(can_dissolve(landlord))
        staffed = self._shell()
        staffed.employees = ["x"]
        self.assertFalse(can_dissolve(staffed))

    def test_idle_shells_without_prospect_are_dissolved_but_never_the_last_of_their_sector(self) -> None:
        simulation = small(months=2, shell_dissolution_months=3)
        tech = [c for c in simulation.companies if c.active and c.sector == "technology"]
        self.assertGreaterEqual(len(tech), 2)
        for company in tech:
            company.employees.clear()
            company.debt = 0.0
            company.bank_loan_accounts.clear()
            company.production_capacity = company.energy_capacity = 0.0
            company.inventory = company.goods_inventory = 0.0
            company.bond_holdings = 0.0
            company.idle_months = 5
        simulation._restart_value_by_id = {c.id: 0.0 for c in tech}
        before = money(simulation)
        dissolved = simulation._dissolve_shells()
        self.assertEqual(dissolved, len(tech) - 1)
        self.assertEqual(sum(c.active for c in tech), 1)
        self.assertAlmostEqual(money(simulation), before, places=4)


class DemographyTests(unittest.TestCase):
    """Idée 3 : âge, mortalité, successions."""

    def test_mortality_rises_with_age_and_life_expectancy_is_plausible(self) -> None:
        config = SimulationConfig()
        rates = [annual_mortality(age, config) for age in (30, 50, 70, 90)]
        self.assertEqual(rates, sorted(rates))
        self.assertLess(rates[0], 0.002)
        self.assertGreater(rates[2], 0.03)
        self.assertEqual(annual_mortality(120, config), 1.0)
        self.assertAlmostEqual(1.0 - (1.0 - monthly_mortality(600, config)) ** 12, annual_mortality(50, config))
        self.assertTrue(45.0 < life_expectancy(config) < 65.0)
        self.assertEqual(annual_mortality(80, SimulationConfig(mortality_level=0.0)), 0.0)

    def test_initial_ages_follow_the_stationary_table(self) -> None:
        config = SimulationConfig()
        table = stationary_age_table(config)
        rng = random.Random(3)
        ages = [draw_initial_age(rng, table) / 12.0 for _ in range(2000)]
        self.assertGreaterEqual(min(ages), config.adult_age_years)
        self.assertTrue(40.0 < sum(ages) / len(ages) < 55.0)

    def test_demography_draws_from_its_own_generator(self) -> None:
        with_ages, without = small(seed=9), small(seed=9, mortality_level=0.0)
        for name in ("random", "labor_random", "trait_random", "politics_random", "event_random",
                     "forecast_random"):
            self.assertEqual(getattr(with_ages, name).getstate(), getattr(without, name).getstate())
        self.assertEqual([p.skill for p in with_ages.people], [p.skill for p in without.people])
        self.assertTrue(all(p.age_months >= 240 for p in with_ages.people))

    def _forced_death(self, simulation: EconomySimulation, person: Person) -> MonthFlows:
        person.age_months = 12 * 115
        others = [p for p in simulation.people if p is not person]
        for other in others:
            other.age_months = 240
        f = MonthFlows()
        simulation._demography(f)
        return f

    def test_the_heir_keeps_assets_and_job_but_not_the_experience(self) -> None:
        simulation = small(months=3, estate_tax_rate=0.0)
        person = max(simulation.people, key=lambda p: p.cash)
        person.career_months, person.leadership_experience, person.skill = 400, 50, 1.9
        snapshot = (person.id, person.cash, dict(person.shares), person.owns_home, person.employer_id,
                    person.mortgage_debt)
        f = self._forced_death(simulation, person)
        self.assertGreaterEqual(f.deaths, 1)
        self.assertEqual((person.id, person.cash, dict(person.shares), person.owns_home, person.employer_id,
                          person.mortgage_debt), snapshot)
        self.assertEqual(person.generation, 2)
        self.assertEqual(person.career_months, 0)
        self.assertEqual(person.leadership_experience, 0)
        self.assertLess(person.skill, 1.3)
        self.assertTrue(240 <= person.age_months <= 360)

    def test_estate_tax_is_a_transfer_to_the_state_set_by_the_doctrine(self) -> None:
        simulation = small(months=3)
        person = simulation.people[0]
        person.cash = 5_000_000.0
        person.shares, person.bond_holdings = {}, 0.0
        person.mortgage_debt = person.consumer_debt = 0.0
        simulation.institutions.doctrine.positions["top_tax"] = 0.0
        rate = simulation._estate_tax_rate()
        self.assertAlmostEqual(rate, 0.20)
        before = money(simulation)
        public_before = simulation.public.cash
        booked_before = simulation.institutions.book.flows.get("droits_succession", 0.0)
        f = self._forced_death(simulation, person)
        self.assertAlmostEqual(money(simulation), before, places=4)
        self.assertGreater(f.estate_taxes, 0.0)
        self.assertAlmostEqual(simulation.public.cash - public_before, f.estate_taxes)
        self.assertAlmostEqual(simulation.institutions.book.flows["droits_succession"] - booked_before,
                               f.estate_taxes)
        simulation.institutions.doctrine.positions["top_tax"] = -1.0
        self.assertEqual(simulation._estate_tax_rate(), 0.0)
        simulation.institutions.doctrine.positions["top_tax"] = 1.0
        self.assertAlmostEqual(simulation._estate_tax_rate(), 0.40)

    def test_illiquid_estates_pay_later_from_cash_above_the_reserve(self) -> None:
        simulation = small(months=3)
        person = simulation.people[1]
        person.cash = 0.0
        person.bond_holdings = 10_000_000.0
        simulation.institutions.doctrine.positions["top_tax"] = 0.0
        self._forced_death(simulation, person)
        owed = simulation.institutions.estate_backlog[person.id]
        self.assertGreater(owed, 0.0)
        person.expected_income = 1_000.0
        person.cash = owed / 2 + 3_000.0  # réserve : 3 mois de revenu attendu
        f = MonthFlows()
        simulation._collect_estate_backlog(f)
        self.assertAlmostEqual(f.estate_taxes, owed / 2)
        self.assertAlmostEqual(person.cash, 3_000.0)
        self.assertAlmostEqual(simulation.institutions.estate_backlog[person.id], owed / 2)

    def test_long_run_accounts_stay_reconciled_with_deaths(self) -> None:
        simulation = small(seed=4, months=60, mortality_level=0.002)
        deaths = sum(r.deaths for r in simulation.history)
        self.assertGreater(deaths, 5)
        for record in simulation.history[-6:]:
            self.assertLess(abs(record.ledger_residual), 1e-3)
        for record in simulation.institutions.history[-6:]:
            self.assertLess(abs(record.cash_residual), 1e-6)


class IndividualVotingTests(unittest.TestCase):
    """Idée 4 : chaque ménage vote selon sa proximité, ses intérêts et son propre bilan."""

    def test_support_follows_personal_income_and_unemployment(self) -> None:
        config = SimulationConfig()
        base = Voter(60.0, 0.5, 0.0, 0.0)
        richer = Voter(60.0, 0.5, 0.2, 0.0)
        jobless = Voter(60.0, 0.5, 0.0, 1.0)
        support = [incumbent_support(v, 60.0, 35.0, config, None) for v in (base, richer, jobless)]
        self.assertGreater(support[1], support[0])
        self.assertLess(support[2], support[0])

    def test_wealth_shifts_the_position_without_determining_it(self) -> None:
        config = SimulationConfig()
        poor, rich = Voter(50.0, 0.0, 0.0, 0.0), Voter(50.0, 1.0, 0.0, 0.0)
        self.assertAlmostEqual(effective_lean(poor, config), 65.0)
        self.assertAlmostEqual(effective_lean(rich, config), 35.0)
        leftist_rich = Voter(90.0, 1.0, 0.0, 0.0)
        self.assertGreater(effective_lean(leftist_rich, config), 50.0)

    def test_challenger_is_the_median_of_the_opposite_camp(self) -> None:
        config = SimulationConfig(vote_interest_weight=0.0)
        voters = [Voter(x, 0.5, 0.0, 0.0) for x in (10.0, 20.0, 40.0, 60.0, 80.0)]
        self.assertAlmostEqual(challenger_orientation(voters, 70.0, config), 20.0)
        self.assertAlmostEqual(challenger_orientation(voters, 30.0, config), 70.0)

    def test_a_bad_personal_record_brings_alternation(self) -> None:
        config = SimulationConfig(vote_swing_sd=0.0)
        content = [Voter(55.0, 0.5, 0.15, 0.0) for _ in range(50)]
        angry = [Voter(55.0, 0.5, -0.30, 0.6) for _ in range(50)]
        _, kept, share_content, _ = individual_election(random.Random(1), 60.0, content, config, 0.5)
        _, lost, share_angry, challenger = individual_election(random.Random(1), 60.0, angry, config, 0.5)
        self.assertTrue(kept)
        self.assertFalse(lost)
        self.assertGreater(share_content, 0.5)
        self.assertLess(share_angry, 0.5)
        self.assertLess(challenger, 50.0)

    def test_an_election_is_decided_by_the_households_and_logged(self) -> None:
        simulation = small(seed=6, months=26, election_period_months=12)
        elections = simulation.institutions.elections
        self.assertGreaterEqual(len(elections), 2)
        for election in elections:
            self.assertTrue(0.0 <= election["part_sortant"] <= 1.0)
            self.assertEqual(election["reelu"], election["part_sortant"] > 0.5)
        self.assertTrue(any("vote des ménages" in d.decision for d in simulation.institutions.journal))


class EducationTests(unittest.TestCase):
    """Idée 5 : un service public d'éducation relève la compétence de la génération suivante."""

    def test_education_is_a_public_service_with_staff_and_quality(self) -> None:
        self.assertIn("education", SERVICES)
        simulation = small(months=24)
        service = simulation.institutions.services["education"]
        self.assertGreater(service.authorized, 0)
        self.assertGreater(simulation.education_quality, 0.0)
        self.assertLessEqual(simulation.education_quality, 1.0)

    def test_heirs_and_newcomers_start_with_the_education_they_received(self) -> None:
        simulation = small(months=2)
        simulation.education_quality = 0.8
        self.assertAlmostEqual(simulation._education_skill_bonus(), 0.15 * 0.8)
        simulation.education_quality = 0.0
        self.assertEqual(simulation._education_skill_bonus(), 0.0)
        simulation.live.education_skill_gain = 0.0
        simulation.education_quality = 1.0
        self.assertEqual(simulation._education_skill_bonus(), 0.0)


class FiveIdeasRegistryTests(unittest.TestCase):
    """Les hypothèses ajoutées par la mission sont listées et justifiées dans AUTONOMIE.md."""

    NEW_FIELDS = (
        "wage_slack_speed", "wage_concession_speed", "long_term_unemployment_months",
        "shell_dissolution_months", "mortality_level", "mortality_growth", "adult_age_years",
        "estate_tax_rate", "estate_tax_allowance_years", "education_skill_gain", "pupils_per_agent",
        "education_memory_months", "vote_interest_weight", "vote_ideology_sensitivity",
        "vote_income_weight", "vote_unemployment_weight", "vote_swing_sd",
    )

    def test_each_new_hypothesis_is_justified(self) -> None:
        import dataclasses
        import os
        import re

        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Hypothèses de la mission « cinq idées »")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        rows = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name for f in dataclasses.fields(SimulationConfig)}
        for name in self.NEW_FIELDS:
            with self.subTest(hypothese=name):
                self.assertIn(name, rows)
                self.assertIn(name, fields)
                self.assertGreaterEqual(len(rows[name]), 60)


if __name__ == "__main__":
    unittest.main()
