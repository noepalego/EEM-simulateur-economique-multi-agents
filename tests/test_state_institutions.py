"""Nouvel État (``state_model="institutions"``) : tests de contrat de la mission « refonte de l'État ».

Comme ``test_simulation.py``, ce module désactive la période de chauffe (sauf test explicite).
"""

import unittest

from economy.institutions import (
    OFFICES,
    PORTFOLIOS,
    Objective,
    display_name,
    elect_orientation,
    make_doctrine,
    minimum_wage_ambition,
    reflect,
)
from economy.models import Company, Person, SimulationConfig
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def institutional(seed: int = 11, months: int = 3, **overrides) -> EconomySimulation:
    simulation = EconomySimulation(
        SimulationConfig(seed=seed, population_size=80, **overrides)
    )
    for _ in range(months):
        simulation.step()
    return simulation


def money(simulation: EconomySimulation) -> float:
    return (
        sum(p.cash for p in simulation.people)
        + sum(c.cash for c in simulation.companies)
        + simulation.public.cash
        + simulation.resources.market_cash
    )


class StateInstitutionsTests(unittest.TestCase):
    """Gouvernement nommé : présidence, quatre ministres, objectifs, salaires, journal."""



    def test_president_and_four_distinct_ministers_each_with_an_objective(self) -> None:
        simulation = institutional()
        offices = simulation.institutions.offices
        self.assertEqual(set(offices), set(OFFICES))
        holders = [office.person_id for office in offices.values()]
        self.assertEqual(len(set(holders)), 5)
        people = {p.id: p for p in simulation.people}
        for role, office in offices.items():
            person = people[office.person_id]
            self.assertEqual(person.employer_id, "state")
            self.assertGreater(person.current_wage, 0.0)
            if role == "presidence":
                self.assertIsNone(office.objective)
            else:
                self.assertIsInstance(office.objective, Objective)
                self.assertGreater(office.objective.deadline, simulation.month)
        objectives = {offices[role].objective.indicator for role in PORTFOLIOS}
        self.assertEqual(len(objectives), 4)  # quatre objectifs distincts

    def test_nominations_are_reproducible_at_equal_seed(self) -> None:
        def run():
            simulation = institutional(seed=12, months=30, election_period_months=12)
            inst = simulation.institutions
            return (
                [(t.since, round(t.orientation, 9), t.reelected) for t in inst.terms],
                sorted((role, o.person_id) for role, o in inst.offices.items()),
                dict(inst.salaries),
            )

        self.assertEqual(run(), run())

    def test_nominee_leaves_job_and_a_ceo_is_replaced_by_the_company(self) -> None:
        simulation = institutional()
        company = next(
            c for c in simulation.companies
            if c.active and c.executive_id and c.executive_id in c.employees and len(c.employees) > 1
        )
        ceo = next(p for p in simulation.people if p.id == company.executive_id)
        simulation._appoint(ceo, "industrie")
        self.assertNotIn(ceo.id, company.employees)
        self.assertEqual(ceo.employer_id, "state")
        # le mécanisme de l'entreprise remplace son dirigeant parmi ses salariés
        simulation.institutions.offices["industrie"].person_id = ceo.id
        simulation.step()
        self.assertNotEqual(company.executive_id, ceo.id)
        self.assertEqual(ceo.employer_id, "state")  # pas d'embauche privée pendant le mandat

    def test_cabinet_members_are_not_hired_by_private_firms_during_their_term(self) -> None:
        simulation = institutional(months=24)
        for office in simulation.institutions.offices.values():
            person = next(p for p in simulation.people if p.id == office.person_id)
            self.assertEqual(person.employer_id, "state")
            self.assertTrue(person.employment_role.startswith(("Présidence", "Ministre")))

    def test_former_members_return_to_the_labor_market(self) -> None:
        simulation = institutional(seed=13, months=40, election_period_months=12)
        inst = simulation.institutions
        current = {o.person_id for o in inst.offices.values()}
        former = {
            member["person_id"] for term in inst.terms for member in term.members
            if member["until"] is not None
        } - current
        self.assertTrue(former)
        for person in simulation.people:
            if person.id in former:
                self.assertFalse(person.employment_role.startswith(("Présidence", "Ministre")))

    def test_political_salaries_are_paid_from_the_treasury_and_conserve_money(self) -> None:
        simulation = institutional()
        simulation.public.cash = 1e7
        before = money(simulation)
        public_before = simulation.public.cash
        paid, members = simulation._pay_cabinet()
        self.assertEqual(members, 5)
        self.assertGreater(paid, 0.0)
        self.assertAlmostEqual(simulation.public.cash, public_before - paid)
        self.assertAlmostEqual(money(simulation), before, delta=1e-6)
        salaries = simulation.institutions.salaries
        self.assertGreater(salaries["presidence"], salaries["ministre"])

    def test_repeated_refusals_raise_salaries_at_the_next_revision(self) -> None:
        simulation = institutional()
        inst = simulation.institutions
        offered = simulation._office_salary("finances")
        excluded = {o.person_id for o in inst.offices.values()}
        # tous gagnent nettement plus que le salaire proposé, sauf un : les autres refusent
        others = [p for p in simulation.people if p.id not in excluded]
        for person in others:
            person.current_wage = 3.0 * offered
        others[-1].current_wage = 0.0
        office = simulation._nominate("finances", excluded)
        self.assertTrue(office.refusals)
        self.assertEqual(office.person_id, others[-1].id)
        self.assertGreater(inst.refusals_since_revision, 0)
        current = inst.salaries["presidence"]
        simulation._revise_political_salaries()
        self.assertEqual(inst.salaries["presidence"], current)  # pas de hausse immédiate
        self.assertGreater(inst.pending_raise["presidence"], current)
        pending = inst.pending_raise["presidence"]
        simulation._revise_political_salaries()
        self.assertEqual(inst.salaries["presidence"], pending)  # appliquée à la révision suivante

    def test_social_government_caps_pay_relative_to_the_minimum_wage(self) -> None:
        simulation = institutional(initial_political_orientation=95.0, political_cycle_enabled=False)
        inst = simulation.institutions
        self.assertEqual(inst.doctrine.salary_policy, "plafond_smic")
        simulation.live.minimum_wage = 100.0
        simulation._revise_political_salaries()
        cap = simulation.live.social_pay_ratio_cap * 100.0
        self.assertLessEqual(inst.salaries["presidence"], cap + 1e-9)

    def test_every_decision_has_an_author_a_date_and_a_motive(self) -> None:
        simulation = institutional(months=30, election_period_months=12)
        journal = simulation.institutions.journal
        self.assertTrue(journal)
        for entry in journal:
            self.assertTrue(entry.author and entry.person and entry.decision and entry.motive)
            self.assertLessEqual(entry.month, simulation.month)

    def test_objectives_are_tracked_and_closed_at_the_end_of_the_term(self) -> None:
        simulation = institutional(seed=14, months=26, election_period_months=12)
        closed = [t for t in simulation.institutions.terms if t.until is not None]
        self.assertTrue(closed)
        for term in closed:
            self.assertEqual(set(term.record), set(PORTFOLIOS))
            for status in term.record.values():
                self.assertIn(status, ("atteint", "partiel", "manqué"))
            self.assertTrue(0.0 <= term.objectives_met <= 1.0)

    def test_money_is_conserved_over_a_run(self) -> None:
        simulation = institutional(months=60, election_period_months=24)
        self.assertLess(abs(simulation.ledger.residual(simulation)), 1e-3)
        self.assertLess(max(r.public_debt_reconciliation_error for r in simulation.history), 1e-3)

    def test_warmup_keeps_the_current_term_and_clears_the_journal(self) -> None:
        simulation = EconomySimulation(SimulationConfig(
            seed=15, population_size=60, warmup_months=30, election_period_months=12,
        ))
        _ORIGINAL_WARM_UP(simulation)
        inst = simulation.institutions
        self.assertEqual(inst.journal, [])
        self.assertEqual(len(inst.terms), 1)
        self.assertLessEqual(len(inst.history), 24)
        self.assertEqual(simulation.government_history, [simulation.government])


class StateBudgetTests(unittest.TestCase):
    """Budget et dette selon la politique menée : aucun attracteur, financement traçable."""

    def test_no_borrowing_without_a_financing_need(self) -> None:
        simulation = institutional(months=14)
        simulation.public.cash = 1e9
        debt = simulation.public.debt
        simulation.step()
        self.assertEqual(simulation.institutions.history[-1].borrowed, 0.0)
        self.assertLessEqual(simulation.public.debt, debt + 1e-6)

    def test_deficit_is_financed_traceably_by_bank_bonds(self) -> None:
        simulation = institutional(months=14)
        simulation.public.cash = 0.0
        banks = [b for b in simulation.companies if b.active and b.sector == "bank"]
        held = sum(b.bond_holdings for b in banks)
        debt = simulation.public.debt
        simulation._open_state_book()
        funded = simulation._state_funds(50000.0)
        self.assertAlmostEqual(funded, 50000.0)
        book = simulation.institutions.book
        self.assertAlmostEqual(book.borrowed, 50000.0)
        self.assertAlmostEqual(simulation.public.debt, debt + 50000.0)
        self.assertAlmostEqual(sum(b.bond_holdings for b in banks), held + 50000.0)

    def test_debt_trajectories_diverge_with_forced_orientation(self) -> None:
        def debt_ratio(orientation: float) -> float:
            simulation = institutional(
                seed=21, months=240, political_cycle_enabled=False,
                initial_political_orientation=orientation,
            )
            return simulation.public.debt / simulation._annual_gdp()

        # petite économie sans chauffe : écart faible mais de signe constant ; à pleine échelle
        # (270 ménages, chauffe, 360 mois) : 11 à 32 % du PIB à 80/100 contre 0 % à 20/100 (docs/ETAT.md)
        liberal, social = debt_ratio(20.0), debt_ratio(80.0)
        self.assertGreaterEqual(liberal, 0.0)  # aucune règle ne vise désormais une dette nulle
        self.assertGreater(social, liberal + 0.002)

    def test_two_inherited_debts_do_not_converge_under_the_same_government(self) -> None:
        def run(inherited: float) -> tuple[list[float], float, float]:
            # 120 mois d'abord, comme la chauffe : croissance nominale proche de son régime
            simulation = institutional(
                seed=22, months=120, political_cycle_enabled=False, initial_political_orientation=50.0,
            )
            amount = inherited * simulation._annual_gdp()
            if inherited:
                bank = next(b for b in simulation.companies if b.active and b.sector == "bank")
                bank.bond_holdings += amount
                bank.bank_equity += amount
                simulation._sync_public_debt()
                simulation.ledger.calibrate(simulation)
            ratios = []
            for _ in range(96):
                simulation.step()
                ratios.append(simulation.public.debt / simulation._annual_gdp())
            return ratios, amount, simulation.public.debt

        (low, _, _), (high, inherited, final) = run(0.0), run(0.4)
        start_gap = high[0] - low[0]
        self.assertGreater(start_gap, 0.2)
        # le centre ne rembourse pas la dette héritée (aucun niveau commun visé) ; le ratio ne
        # baisse qu'avec la croissance nominale, l'écart ne se referme pas
        self.assertGreater(final, 0.9 * inherited)
        self.assertGreater(high[-1] - low[-1], 0.4 * start_gap)

    def test_legal_limit_is_respected_and_shortfalls_are_visible(self) -> None:
        from economy.records import MonthFlows

        simulation = institutional(
            seed=23, months=14, political_cycle_enabled=False, initial_political_orientation=90.0,
        )
        inst = simulation.institutions
        # la limite légale est atteinte : plus aucun emprunt possible
        simulation.live.debt_ceiling_gdp = simulation.public.debt / simulation._annual_gdp()
        simulation.public.cash = 0.0
        simulation.public.pending_transfers = 0.0
        simulation._open_state_book()
        self.assertAlmostEqual(simulation._state_funds(10000.0), 0.0)
        self.assertAlmostEqual(inst.book.borrowed, 0.0)
        debt = simulation.public.debt
        simulation._institutional_budget(MonthFlows(unemployment=0.05))
        self.assertLessEqual(simulation.public.debt, debt + 1e-6)
        # programmes reportés, visibles (non financés) ; la loi suivante refuse tout déficit
        self.assertGreater(sum(inst.book.unfunded.values()), 0.0)
        gdp = simulation._annual_gdp()
        self.assertGreaterEqual(simulation._balance_goal(gdp, 1e6, 0.0), 0.0)

    def test_interest_is_never_erased(self) -> None:
        simulation = institutional(months=14, debt_ceiling_gdp=0.0)
        bank = next(b for b in simulation.companies if b.active and b.sector == "bank")
        bank.bond_holdings += 1e6
        bank.bank_equity += 1e6
        simulation._sync_public_debt()
        simulation.public.cash = 0.0
        simulation.public.pending_transfers = 0.0
        inst = simulation.institutions
        simulation._open_state_book()
        from economy.records import MonthFlows

        simulation._institutional_budget(MonthFlows(unemployment=0.05))
        owed = inst.arrears_interest
        self.assertGreater(owed, 0.0)  # impayé, mais dû
        simulation.public.cash = 1e7
        revenue = bank.revenue
        simulation._open_state_book()
        simulation._institutional_budget(MonthFlows(unemployment=0.05))
        self.assertAlmostEqual(inst.arrears_interest, 0.0)
        self.assertGreater(bank.revenue - revenue, owed)  # arriérés réglés avec l'intérêt du mois

    def test_monthly_reconciliations_of_cash_and_debt(self) -> None:
        simulation = institutional(seed=24, months=120, initial_political_orientation=85.0)
        for record in simulation.institutions.history:
            scale = max(1.0, abs(record.cash), abs(record.debt))
            self.assertLess(abs(record.cash_residual), 1e-9 * scale + 1e-6)
            self.assertLess(abs(record.debt_residual), 1e-9 * scale + 1e-6)
        self.assertLess(abs(simulation.ledger.residual(simulation)), 1e-3)

    def test_surplus_is_not_redistributed_automatically(self) -> None:
        simulation = institutional(months=14)
        simulation.public.cash = 1e9
        simulation.step()
        law = simulation.institutions.law
        self.assertLessEqual(simulation.public.pending_transfers, law.lines["transferts"] / 12.0 + 1e-6)

    def test_excess_treasury_is_returned_by_doctrine_at_the_next_law(self) -> None:
        for orientation in (50.0, 85.0):  # centre : allègement des impôts ; social : transferts
            simulation = institutional(months=14, political_cycle_enabled=False,
                                       initial_political_orientation=orientation)
            inst = simulation.institutions
            simulation.public.cash += 0.1 * simulation._annual_gdp()
            simulation._vote_budget_law("rectificative", "essai : trésorerie au-delà de la réserve")
            law = inst.law
            self.assertGreater(law.excess_treasury, 0.0)
            self.assertTrue(any(d.decision.startswith("Trésorerie excédentaire") for d in inst.journal))
            if orientation < 65.0:
                self.assertGreater(law.tax_relief, 0.0)
                simulation._apply_budget_decisions()
                durable = min(1.0, simulation.config.income_tax_rate * inst.tax_factors["income_tax"])
                self.assertAlmostEqual(simulation.live.income_tax_rate, durable * (1.0 - law.tax_relief))
            else:
                self.assertEqual(law.tax_relief, 0.0)
                durable = inst.programs["transferts"] * law.gdp
                self.assertEqual(law.lines["transferts"], durable + law.excess_treasury)

    def test_a_programme_cut_to_almost_nothing_can_be_rebuilt(self) -> None:
        from economy.state_budget import _step

        level = 0.001
        for _ in range(4):  # hausse : au plus 25 % du niveau voulu par loi
            level = _step(level, 0.08, 0.25)
        self.assertAlmostEqual(level, 0.08)
        self.assertAlmostEqual(_step(0.08, 0.0, 0.25), 0.06)  # baisse : 25 % du niveau courant

    def test_taxes_are_the_voted_rates_without_fiscal_rule_or_automatic_vat_cut(self) -> None:
        simulation = institutional(months=30)
        inst, live, config = simulation.institutions, simulation.live, simulation.config
        self.assertEqual(simulation.history[-1].tax_adjustment, 1.0)
        keep = 1.0 - inst.tax_relief  # allègement exceptionnel voté par la loi en cours, le cas échéant
        self.assertAlmostEqual(live.income_tax_rate, min(1.0, config.income_tax_rate * inst.tax_factors["income_tax"] * keep))
        self.assertAlmostEqual(live.vat_rate, min(1.0, config.vat_rate * inst.tax_factors["vat"] * keep))
        self.assertEqual(simulation.history[-1].keynesian_investment, 0.0)

    def test_every_law_is_prepared_and_logged(self) -> None:
        simulation = institutional(months=40)
        inst = simulation.institutions
        kinds = [law.kind for law in inst.laws]
        self.assertEqual(kinds[0], "provisoire")
        self.assertIn("initiale", kinds)
        logged = [d for d in inst.journal if d.decision.startswith("Loi de finances")]
        self.assertEqual(len(logged), len(inst.laws))
        self.assertTrue(all(d.author == "Économie et Finances" for d in logged))


class CivilServiceTests(unittest.TestCase):
    """Fonction publique : services, rôles, grille, recrutement concurrentiel, effets visibles."""

    def test_each_agent_has_one_service_a_trade_and_a_grade(self) -> None:
        from economy.institutions import SERVICE_LABELS, SERVICE_TRADES

        simulation = institutional(months=24)
        inst = simulation.institutions
        self.assertTrue(inst.agents)
        cabinet = {office.person_id for office in inst.offices.values()}
        people = {p.id: p for p in simulation.people}
        for person_id, agent in inst.agents.items():
            self.assertNotIn(person_id, cabinet)
            self.assertIn(agent.trade, SERVICE_TRADES[agent.service])
            self.assertIn(agent.grade, (1, 2, 3))
            person = people[person_id]
            self.assertEqual(person.employer_id, "state")
            self.assertIn(SERVICE_LABELS[agent.service], person.employment_role)
            self.assertIn(f"grade {agent.grade}", person.employment_role)
        # aucun service sans effet mesurable : chacun a un besoin et une couverture
        for service in inst.services.values():
            self.assertGreater(service.authorized, 0)
            self.assertGreaterEqual(service.coverage, 0.0)

    def test_salaries_differ_by_trade_and_grade_and_respect_the_minimum_wage(self) -> None:
        simulation = institutional(months=3)
        self.assertGreater(simulation._grid_salary("ingenieur", 1, 0), simulation._grid_salary("agent", 1, 0))
        self.assertGreater(simulation._grid_salary("ingenieur", 3, 0), simulation._grid_salary("ingenieur", 1, 0))
        self.assertGreater(simulation._grid_salary("agent", 1, 48), simulation._grid_salary("agent", 1, 0))
        simulation.live.minimum_wage = 10.0 * simulation.institutions.point
        self.assertEqual(simulation._grid_salary("ingenieur", 3, 240), simulation.live.minimum_wage)

    def test_public_employment_results_from_needs(self) -> None:
        few = institutional(months=12, finance_files_per_agent=240.0)
        many = institutional(months=12, finance_files_per_agent=20.0)
        self.assertLess(few.institutions.services["finances"].authorized,
                        many.institutions.services["finances"].authorized)

    @staticmethod
    def _private_financiers(simulation, wage: float) -> None:
        """Trois salariés du privé deviennent financiers ; tous les financiers du privé gagnent ``wage``."""
        private = [p for p in simulation.people if p.employer_id not in (None, "state")]
        for person in private[:3]:
            person.trade = "financier"
        for person in private:
            if person.trade == "financier":
                person.current_wage = wage

    def test_persistent_vacancies_raise_a_premium_and_applications_freeze_it(self) -> None:
        simulation = institutional(months=3)
        inst = simulation.institutions
        inst.law = None
        self._private_financiers(simulation, 2.0 * simulation._grid_salary("financier", 1, 0))
        inst.vacancy_months = {"financier": 3, "ingenieur": 3}
        inst.applications_quarter = {"financier": 0, "ingenieur": 12}
        inst.vacancies_quarter = {"financier": 3, "ingenieur": 3}
        simulation._revise_premiums()
        self.assertAlmostEqual(inst.premiums["financier"], 1.0 + simulation.live.public_premium_step)
        self.assertNotIn("ingenieur", inst.premiums)  # candidatures nombreuses : gel

    def test_a_premium_stops_rising_once_the_entry_salary_beats_the_private_median(self) -> None:
        simulation = institutional(months=3)
        inst = simulation.institutions
        inst.law = None
        self._private_financiers(simulation, 0.5 * simulation._grid_salary("financier", 1, 0))
        inst.vacancy_months = {"financier": 3}
        inst.applications_quarter = {"financier": 0}
        inst.vacancies_quarter = {"financier": 3}
        simulation._revise_premiums()
        self.assertNotIn("financier", inst.premiums)  # la vacance ne tient pas au salaire

    def test_agents_keep_their_job_with_growing_tenure_and_career(self) -> None:
        simulation = institutional(months=24)
        inst = simulation.institutions
        before = {pid: next(p for p in simulation.people if p.id == pid) for pid in inst.agents}
        tenure = {pid: (p.employer_tenure, p.career_months) for pid, p in before.items()}
        for _ in range(3):
            simulation.step()
        stayed = [pid for pid in tenure if pid in inst.agents]
        self.assertTrue(stayed)
        for pid in stayed:
            person = before[pid]
            self.assertEqual(person.employer_tenure, tenure[pid][0] + 3)
            self.assertEqual(person.career_months, tenure[pid][1] + 3)

    def test_agents_can_leave_for_a_better_private_offer(self) -> None:
        simulation = institutional(seed=31, months=120)
        departures = sum(
            service["departures"] for record in simulation.institutions.history
            for service in record.services.values()
        )
        self.assertGreater(departures, 0)

    def test_private_recruits_join_after_one_month(self) -> None:
        simulation = institutional(seed=32, months=1)
        inst = simulation.institutions
        joined = None
        for _ in range(60):
            pending = dict(inst.pending_recruits)
            simulation.step()
            for person_id, agent in pending.items():
                if person_id in inst.agents:
                    joined = (person_id, agent)
                    break
            if joined:
                break
        self.assertIsNotNone(joined)
        person = next(p for p in simulation.people if p.id == joined[0])
        self.assertEqual(person.employer_id, "state")
        self.assertFalse(any(person.id in c.employees for c in simulation.companies))

    def test_understaffed_finances_delay_collection_without_destroying_money(self) -> None:
        simulation = institutional(months=6)
        inst = simulation.institutions
        inst.tax_backlog = {}
        for person in simulation.people:
            person.income_this_turn = 5000.0
            person.capital_income_last_month = 0.0  # précondition : revenu du travail seul
        live = simulation.live
        # barème de novembre 2026 : au-delà du seuil, taux de base + surtaxe (auparavant le plus
        # grand des deux, qui ne mordait jamais)
        assessed = sum(
            min(5000.0, live.income_tax_threshold) * live.income_tax_rate
            + max(0.0, 5000.0 - live.income_tax_threshold) * (live.income_tax_rate + live.top_marginal_income_tax_rate)
            for _ in simulation.people
        )
        cash = sum(p.cash for p in simulation.people)
        collected = simulation._collect_assessed_taxes(0.25)
        self.assertAlmostEqual(collected, 0.25 * assessed, delta=1e-6)
        self.assertAlmostEqual(sum(inst.tax_backlog.values()), 0.75 * assessed, delta=1e-6)
        self.assertAlmostEqual(sum(p.cash for p in simulation.people), cash - collected, delta=1e-6)
        for person in simulation.people:
            person.income_this_turn = 0.0
        later = simulation._collect_assessed_taxes(1.0)
        self.assertAlmostEqual(later, 0.75 * assessed, delta=1e-6)  # recouvrée plus tard, jamais effacée
        self.assertEqual(inst.tax_backlog, {})

    def test_without_equipment_staff_public_works_are_not_executed(self) -> None:
        simulation = institutional(months=20, works_per_agent=0.0)
        capital = simulation.public.public_capital
        for _ in range(3):
            simulation.step()
        self.assertLessEqual(simulation.public.public_capital, capital)
        lapsed = sum(r.lapsed for r in simulation.institutions.history[-3:])
        self.assertGreater(lapsed, 0.0)

    def test_public_posts_count_in_the_trade_market(self) -> None:
        simulation = institutional(months=6)
        posts = simulation._public_trade_demand()
        self.assertTrue(posts)
        simulation.step()
        for trade, count in simulation._public_trade_demand().items():
            self.assertGreaterEqual(simulation.trades.demand[trade], count)

    def test_funded_retraining_is_paid_by_the_state_to_service_firms(self) -> None:
        simulation = institutional(months=6)
        inst = simulation.institutions
        simulation.public.cash = 1e6
        inst.training_places = 1
        person = simulation.people[0]
        services = [c for c in simulation.companies if c.active and c.sector == "services"]
        revenue = sum(c.revenue for c in services)
        own_cash = person.cash
        cost = 1000.0
        self.assertTrue(simulation._training_sponsor(person, cost))
        self.assertAlmostEqual(simulation.public.cash, 1e6 - cost)
        self.assertAlmostEqual(sum(c.revenue for c in services), revenue + cost)
        self.assertEqual(person.cash, own_cash)
        self.assertFalse(simulation._training_sponsor(person, cost))  # plus de place financée

    def test_exceptional_layoffs_are_logged(self) -> None:
        from economy.institutions import PublicAgent, ServiceState

        simulation = institutional(months=6)
        inst = simulation.institutions
        people = {p.id: p for p in simulation.people}
        idle = [p for p in simulation.people if p.employer_id is None][:4]
        for person in idle:
            simulation._enter_public_service(person, PublicAgent(person.id, "emploi", "agent", 1, simulation.month))
        services = {key: ServiceState(key, authorized=1) for key in inst.services}
        before = len(inst.journal)
        simulation._exceptional_layoffs(services, people)
        self.assertGreater(services["emploi"].layoffs, 0)
        self.assertTrue(any("Licenciement exceptionnel" in d.decision for d in inst.journal[before:]))

    def test_index_point_is_frozen_or_indexed_by_doctrine(self) -> None:
        simulation = institutional(months=30)
        inst = simulation.institutions
        inst.law = None
        inst.doctrine.point_indexation = 0.0
        inst.doctrine.point_catch_up = False
        inst.doctrine.budget_rule = "excedent"
        point = inst.point
        simulation._revise_index_point()
        self.assertEqual(inst.point, point)
        self.assertTrue(inst.journal[-1].decision.startswith("Point d'indice gelé"))
        inst.doctrine.point_indexation = 1.0
        growth = simulation._wage_growth()
        simulation._revise_index_point()
        self.assertAlmostEqual(inst.point, point * (1.0 + max(0.0, growth)))


class MinimumWageInstitutionTests(unittest.TestCase):
    """Salaire minimum légal : institution durable, revalorisée, contraignante à gauche."""

    def test_minimum_wage_exists_under_a_liberal_government(self) -> None:
        simulation = institutional(months=30, political_cycle_enabled=False, initial_political_orientation=10.0)
        inst = simulation.institutions
        self.assertGreater(inst.minimum_wage, 0.0)
        self.assertEqual(simulation.live.minimum_wage, inst.minimum_wage)
        self.assertEqual(inst.minimum_wage_revisions[0]["kind"], "institution")

    def test_ambition_grows_with_orientation_and_binds_on_the_left(self) -> None:
        import random as _random

        rng = _random.Random(7)
        config = SimulationConfig()
        for orientation in (60.0, 70.0, 85.0):
            for _ in range(20):
                self.assertGreaterEqual(make_doctrine(orientation, rng, config).minimum_wage_ambition, 0.60)
        # Isoler la morsure du minimum : les salaires de marché peuvent tous le dépasser.
        from economy.labor import _pay
        simulation = institutional(seed=41, months=1, political_cycle_enabled=False,
                                   initial_political_orientation=85.0)
        floor = simulation.live.minimum_wage
        firm = Company("low", "Bas salaire", "food", 10000.0, floor / 4)
        worker = Person("p", "Salarié", 0.0, skill=1.0)
        self.assertGreater(floor, 0.0)
        self.assertEqual(_pay(firm, worker, "worker", simulation.live), floor)

    def test_persists_after_an_alternation_and_is_never_cut_by_the_rule(self) -> None:
        import random as _random

        simulation = institutional(seed=42, months=40, political_cycle_enabled=False,
                                   initial_political_orientation=85.0)
        inst = simulation.institutions
        level = inst.minimum_wage
        inst.doctrine = make_doctrine(5.0, _random.Random(1), simulation.live)  # alternance libérale
        inst.doctrine.minimum_wage_indexation = 0.0
        levels = []
        for _ in range(26):
            simulation.step()
            levels.append(inst.minimum_wage)
        # précondition : une dérogation n'existe que si l'inflation observée à la révision est
        # positive ; après la refonte du logement (novembre 2026), les deux premières révisions de
        # cette petite économie observent une déflation : on poursuit jusqu'à une révision inflationniste
        for _ in range(72):
            if any(r.get("inflation", 0.0) > 0.0 for r in inst.minimum_wage_revisions
                   if r["kind"] == "revalorisation" and r["month"] > 40):
                break
            simulation.step()
            levels.append(inst.minimum_wage)
        self.assertGreaterEqual(min(levels), level)
        self.assertTrue(any("dérogation" in d.decision for d in inst.journal))

    def test_only_a_non_social_government_cuts_an_excessive_minimum_by_decision(self) -> None:
        for orientation, cut in ((20.0, True), (55.0, True), (80.0, False)):
            simulation = institutional(seed=44, months=30, political_cycle_enabled=False,
                                       initial_political_orientation=orientation)
            inst = simulation.institutions
            inst.doctrine.minimum_wage_indexation = 0.0
            inst.minimum_wage = level = simulation._lagged_median_wage()  # au médian, après une déflation
            simulation._revise_minimum_wage()
            decided = [d for d in inst.journal if d.decision == "Salaire minimum : baisse décidée"]
            if cut:
                self.assertAlmostEqual(inst.minimum_wage, level * (1.0 - simulation.live.minimum_wage_boost_cap))
                self.assertEqual(len(decided), 1)
            else:
                self.assertGreaterEqual(inst.minimum_wage, level)
                self.assertFalse(decided)

    def test_annual_revision_indexes_on_lagged_inflation(self) -> None:
        simulation = institutional(months=30, political_cycle_enabled=False, initial_political_orientation=40.0)
        inst = simulation.institutions
        inst.doctrine.minimum_wage_indexation = 1.0
        # ambition au niveau actuel : ni coup de pouce (droite), ni baisse décidée
        inst.doctrine.orientation = 70.0
        inst.doctrine.minimum_wage_ambition = 0.0
        level = inst.minimum_wage
        history = simulation.history
        inflation = history[-1].consumer_price_index / history[-13].consumer_price_index - 1.0
        simulation._revise_minimum_wage()
        self.assertAlmostEqual(inst.minimum_wage, level * (1.0 + max(0.0, inflation)))

    def test_the_floor_is_paid_by_employers_without_money_creation(self) -> None:
        simulation = institutional(seed=43, months=60, political_cycle_enabled=False,
                                   initial_political_orientation=90.0)
        self.assertLess(abs(simulation.ledger.residual(simulation)), 1e-3)
        floor = simulation.live.minimum_wage
        paid = [p.current_wage for p in simulation.people if p.employer_id not in (None, "state")]
        self.assertTrue(paid)
        below = sum(w < floor * 0.999 for w in paid)
        self.assertEqual(below, simulation.institutions.history[-1].minimum_wage["below_floor"] * len(paid))

    def test_payments_below_the_floor_are_measured(self) -> None:
        from economy.institutions import StateRecord

        simulation = institutional(months=6)
        inst = simulation.institutions
        inst.minimum_wage = 1000.0
        worker = next(p for p in simulation.people if p.employer_id not in (None, "state"))
        worker.current_wage = 500.0  # trésorerie de l'employeur insuffisante
        record = StateRecord(simulation.month, 50.0)
        simulation._record_minimum_wage(record)
        self.assertGreater(record.minimum_wage["below_floor"], 0.0)

    def test_hiring_budgets_count_the_full_expected_pay(self) -> None:
        from economy.labor import allocate_and_pay_labor

        def hires(full_pay: bool) -> int:
            config = SimulationConfig(minimum_wage=0.0)
            company = Company("c", "C", "goods", 0.0, 1000.0, target_employees=10)
            company.age_months = 12
            people = [Person(f"p{i}", f"P{i}", 0.0, skill=1.0) for i in range(12)]
            for person in people[:2]:
                person.employer_id = "c"
                person.current_wage = 2000.0
                company.employees.append(person.id)
            company.wage_bill = 4000.0          # salaire complet moyen : 2 000 (multiplicateurs compris)
            company.revenue_ema = 1e6
            company.cash = 7000.0 + 0.25 * 1400.0  # 7 postes à l'offre, 3 au salaire complet
            allocate_and_pay_labor([company], people, 1400.0, config, full_pay_hiring=full_pay)
            return len(company.employees)

        self.assertLess(hires(True), hires(False))


class IndustrialPolicyTests(unittest.TestCase):
    """Politique industrielle : diagnostic, entreprise publique, nationalisation, cessions."""

    @staticmethod
    def _sector_firms(simulation, sector: str):
        return [c for c in simulation.companies if c.active and c.sector == sector]

    def _make_monopoly(self, simulation, sector: str, margin: float) -> None:
        firms = self._sector_firms(simulation, sector)
        for index, company in enumerate(firms):
            company.previous_revenue = 100000.0 if index == 0 else 0.0
            company.previous_profit = 100000.0 * margin if index == 0 else 0.0
            company.age_months = 60

    def test_a_durable_abnormal_monopoly_is_detected(self) -> None:
        simulation = institutional(months=12)
        inst, live = simulation.institutions, simulation.live
        inst.monopoly_months = {}
        for month in range(live.monopoly_duration_months):
            self._make_monopoly(simulation, "goods", margin=0.30)
            diagnostic = simulation._diagnose_markets()
            self.assertEqual(diagnostic["goods"]["problem"], month + 1 >= live.monopoly_duration_months)
        self.assertTrue(diagnostic["goods"]["abnormal"])
        self.assertIn("goods", simulation._dominated_sectors())

    def test_a_lone_firm_at_reasonable_margins_is_not_a_problem(self) -> None:
        # matières premières : pas de demande non servie mesurée, seule la marge compte
        simulation = institutional(months=12)
        inst, live = simulation.institutions, simulation.live
        inst.monopoly_months = {}
        for _ in range(live.monopoly_duration_months + 2):
            self._make_monopoly(simulation, "materials", margin=live.sector_margin_target["materials"])
            diagnostic = simulation._diagnose_markets()
        self.assertEqual(diagnostic["materials"]["hhi"], 1.0)
        self.assertFalse(diagnostic["materials"]["problem"])

    def test_public_company_creation_conserves_money_and_shares(self) -> None:
        simulation = institutional(months=12)
        simulation.public.cash = 1e8
        before_money = money(simulation)
        before_cash = simulation.public.cash
        simulation._open_state_book()
        self.assertTrue(simulation._create_public_company("goods", "test"))
        company = simulation.companies[-1]
        self.assertTrue(company.id.startswith("public_"))
        self.assertEqual(simulation.public.account.shares[company.id], company.shares_outstanding)
        self.assertTrue(simulation._state_controls(company))
        capital = before_cash - simulation.public.cash
        self.assertGreater(capital, 0.0)
        self.assertAlmostEqual(company.cash, capital)
        self.assertAlmostEqual(money(simulation), before_money, delta=1e-6)
        self.assertAlmostEqual(-simulation.institutions.book.flows["acquisitions_financieres"], capital)
        self.assertIn(company.executive_id, company.employees)  # dirigeant nommé, salarié de l'entreprise
        self.assertEqual(company.pricing_aggression, 1.0)       # mandat : prix modérés

    def test_nationalization_pays_the_sellers_not_the_company(self) -> None:
        simulation = institutional(months=12)
        simulation.public.cash = 1e9
        target = max(self._sector_firms(simulation, "goods"), key=lambda c: c.previous_revenue)
        holders = [h for h in [*simulation.people, *simulation.companies] if h is not target and h.shares.get(target.id, 0.0) > 0.0]
        holder_cash = sum(h.cash for h in holders)
        company_cash = target.cash
        public_cash = simulation.public.cash
        simulation._open_state_book()
        self.assertTrue(simulation._nationalize(target, "test"))
        self.assertTrue(simulation._state_controls(target))
        self.assertEqual(target.cash, company_cash)  # l'argent ne va pas à l'entreprise
        paid = public_cash - simulation.public.cash
        self.assertGreater(paid, 0.0)
        self.assertAlmostEqual(sum(h.cash for h in holders) - holder_cash, paid, delta=1e-6)

    def test_recapitalization_dilutes_existing_holders_explicitly(self) -> None:
        from economy.institutions import PublicHolding

        simulation = institutional(months=12)
        simulation.public.cash = 1e9
        target = self._sector_firms(simulation, "food")[0]
        holder = next(p for p in simulation.people if p.shares.get(target.id, 0.0) > 0.0)
        circulating = target.shares_outstanding - target.treasury_shares
        share_before = holder.shares[target.id] / circulating
        cash_before = target.cash
        simulation._open_state_book()
        holding = PublicHolding(target.id, "participation", simulation.month)
        self.assertTrue(simulation._recapitalize(target, holding, 50000.0, "test"))
        circulating_after = target.shares_outstanding - target.treasury_shares
        self.assertLess(holder.shares[target.id] / circulating_after, share_before)
        self.assertAlmostEqual(target.cash, cash_before + 50000.0)
        self.assertEqual(holding.recapitalizations, 1)

    def test_privatization_is_paid_by_real_buyers(self) -> None:
        from economy.institutions import PublicHolding

        simulation = institutional(seed=51, months=24)
        state = simulation.public.account
        target = self._sector_firms(simulation, "goods")[0]
        # l'État détient une participation minoritaire, prise aux ménages (sans mouvement d'argent)
        quantity = 0.0
        for person in simulation.people:
            taken = min(person.shares.get(target.id, 0.0), 200.0 - quantity)
            if taken > 0.0:
                person.shares[target.id] -= taken
                quantity += taken
        state.shares[target.id] = quantity
        inst = simulation.institutions
        inst.holdings[target.id] = PublicHolding(
            target.id, "participation", simulation.month, decision="vendre", to_sell=quantity, sell_lot=quantity,
        )
        simulation.config.privatization_price_discount = 1.0  # le test ne dépend pas du cours
        treasury = target.treasury_shares
        sold = proceeds = 0.0
        for _ in range(6):
            before = state.shares.get(target.id, 0.0)
            simulation.step()
            sold += before - state.shares.get(target.id, 0.0)
            proceeds += inst.history[-1].flows.get("cessions_titres", 0.0)
            if sold > 0.0:
                break
        self.assertGreater(sold, 0.0)
        self.assertGreater(proceeds, 0.0)  # payé par les acheteurs de la séance
        # aucun rachat par la société privatisée : ses titres en trésorerie n'augmentent pas
        self.assertLessEqual(target.treasury_shares, treasury + 1e-9)

    def test_a_liberal_keeps_a_strategic_stake(self) -> None:
        from economy.institutions import PublicHolding

        simulation = institutional(seed=52, months=24, political_cycle_enabled=False, initial_political_orientation=5.0)
        inst = simulation.institutions
        simulation.public.cash = 1e9
        target = max(self._sector_firms(simulation, "energy"), key=lambda c: c.previous_revenue)
        simulation._open_state_book()
        self.assertTrue(simulation._nationalize(target, "test"))  # secteur critique, contrôlé
        inst.holdings[target.id].origin = "nationalisation"
        simulation._diagnose_markets()
        simulation._review_holdings()
        self.assertEqual(inst.holdings[target.id].decision, "garder")
        self.assertIn("stratégique oui", inst.holdings[target.id].motive)
        self.assertIsInstance(inst.holdings[target.id], PublicHolding)

    def test_a_public_company_can_go_bankrupt(self) -> None:
        simulation = institutional(seed=53, months=12, political_cycle_enabled=False, initial_political_orientation=10.0)
        simulation.public.cash = 1e8
        simulation._open_state_book()
        self.assertTrue(simulation._create_public_company("goods", "test"))
        company = simulation.companies[-1]
        holding = simulation.institutions.holdings[company.id]
        holding.recapitalizations = simulation.live.max_recapitalizations
        company.cash = 0.0
        company.debt = 1e7
        bank = next(b for b in simulation.companies if b.active and b.sector == "bank")
        bank.bank_loan_accounts[company.id] = 1e7
        bank.bank_equity += 0.0
        simulation.step()
        self.assertFalse(company.active)
        self.assertEqual(simulation.institutions.closed_holdings[-1].result, "faillite")

    def test_state_does_not_tender_and_controlled_companies_are_protected(self) -> None:
        from economy.consolidation import takeover_by_value

        simulation = institutional(months=12)
        firms = self._sector_firms(simulation, "goods")
        target, buyer = firms[0], firms[1]
        buyer.cash = 1e12
        buyer.last_acquisition_month = -1000
        state = simulation.public.account
        state.shares[target.id] = target.shares_outstanding - target.treasury_shares
        for holder in [*simulation.people, *simulation.companies]:
            holder.shares.pop(target.id, None)
        completed, _ = takeover_by_value(
            simulation.companies, simulation.people, simulation.live, simulation.month, (state,),
            simulation.policy_rate, locked=(state,), excluded=frozenset({target.id}),
        )
        self.assertTrue(target.active)
        self.assertGreater(state.shares[target.id], 0.0)

    def test_regulation_removes_the_monopoly_margin_from_prices(self) -> None:
        from economy.firm_pricing import signal_index_change
        simulation = institutional(months=12)
        self._make_monopoly(simulation, "goods", margin=0.01)
        free = signal_index_change(0.0, 0.0, 0.1, simulation.live,
                                   simulation._regulated_concentration("goods", 1.0))
        simulation.institutions.regulated["goods"] = simulation.month
        regulated = signal_index_change(0.0, 0.0, 0.1, simulation.live,
                                        simulation._regulated_concentration("goods", 1.0))
        self.assertLess(regulated, free)

    def test_operations_are_financial_and_reconciled(self) -> None:
        simulation = institutional(seed=54, months=36, political_cycle_enabled=False, initial_political_orientation=90.0)
        simulation.public.cash = 1e8
        simulation.ledger.calibrate(simulation)  # monnaie injectée par le test
        simulation._open_state_book()
        simulation._create_public_company("food", "test")
        for _ in range(3):
            simulation.step()
        for record in simulation.institutions.history[-3:]:
            self.assertLess(abs(record.cash_residual), 1e-6)
            self.assertLess(abs(record.debt_residual), 1e-6)
        self.assertLess(abs(simulation.ledger.residual(simulation)), 1e-3)


class InstitutionRulesTests(unittest.TestCase):
    """Règles pures du nouvel État (economy/institutions.py)."""

    def test_reflection_keeps_orientation_inside_bounds_without_sticking(self) -> None:
        self.assertAlmostEqual(reflect(-3.0), 3.0)
        self.assertAlmostEqual(reflect(104.0), 96.0)
        self.assertAlmostEqual(reflect(50.0), 50.0)
        import random

        rng = random.Random(3)
        config = SimulationConfig()
        values = []
        orientation = 0.0
        for _ in range(200):
            orientation, _ = elect_orientation(rng, orientation, config, -0.05, 0.0, 0.5)
            values.append(orientation)
        self.assertTrue(all(0.0 <= v <= 100.0 for v in values))
        self.assertLess(sum(v == 0.0 for v in values), 2)

    def test_display_names_are_deterministic_and_draw_nothing(self) -> None:
        self.assertEqual(display_name("p0001"), display_name("p0001"))
        self.assertNotEqual(display_name("p0001"), display_name("p0002"))

    def test_doctrines_differ_between_governments_of_the_same_colour(self) -> None:
        import random

        rng = random.Random(4)
        config = SimulationConfig()
        first, second = make_doctrine(85.0, rng, config), make_doctrine(85.0, rng, config)
        self.assertEqual(first.budget_rule, "programme")
        self.assertNotEqual(first.deficit_tolerance, second.deficit_tolerance)
        liberal = make_doctrine(10.0, rng, config)
        self.assertEqual(liberal.budget_rule, "excedent")
        self.assertEqual(liberal.surplus_use, "impots")
        self.assertLess(liberal.minimum_wage_indexation, 1.0 + 1e-12)

    def test_minimum_wage_ambition_grows_with_orientation(self) -> None:
        self.assertAlmostEqual(minimum_wage_ambition(30.0), 0.50)
        self.assertAlmostEqual(minimum_wage_ambition(60.0), 0.60)
        # changement de modèle (novembre 2026, docs/AMELIORATIONS_2026-11.md) : 68 % à 80/100 et
        # 80 % à l'extrême gauche (auparavant 65 % et 75 %)
        self.assertAlmostEqual(minimum_wage_ambition(80.0), 0.68)
        self.assertAlmostEqual(minimum_wage_ambition(100.0), 0.80)
        self.assertLess(minimum_wage_ambition(70.0), minimum_wage_ambition(90.0))

    def test_objective_progress_and_close(self) -> None:
        objective = Objective("dette_pib", "réduire", "<=", 0.20, 0.30, deadline=10)
        objective.update(0.25)
        self.assertAlmostEqual(objective.progress, 0.5)
        self.assertEqual(objective.close(), "partiel")
        objective.update(0.19)
        self.assertEqual(objective.close(), "atteint")
        objective.update(0.35)
        self.assertEqual(objective.close(), "manqué")
        stable = Objective("dette_pib", "stabiliser", "~", 0.30, 0.30, deadline=10, tolerance=0.03)
        stable.update(0.32)
        self.assertEqual(stable.progress, 1.0)
        stable.update(0.36)
        self.assertAlmostEqual(stable.progress, 0.0)


if __name__ == "__main__":
    unittest.main()
