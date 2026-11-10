"""Lot K (novembre 2026) : influence, popularité, élection à candidats, lois de pouvoir, aides aux
entreprises, favoritisme, lobbying, pantouflage, dynasties (``economy/power.py``). Désactivés par
défaut ; activés, la monnaie est conservée et le livre de l'État reste rapproché."""

import dataclasses
import math
import os
import re
import unittest

from economy.institutions import MonopolyCase, Voter, incumbent_support
from economy.models import SimulationConfig
from economy.power import (
    ADVISOR_FEE_SHARE,
    AID_PAYROLL_SHARE,
    DISCRETIONARY_SHARE,
    DYNASTY_SHARE,
    FAVOR_CONNECTION,
    FINE_RATE,
    LAWS,
    SWITCHES,
    TERM_LIMIT,
)
from economy.simulation import EconomySimulation

_ORIGINAL_WARM_UP = EconomySimulation._warm_up
PARAMETERS = ("vote_popularity_weight", "business_aid_gdp_share", "favoritism_monthly_chance",
              "audit_detection_chance")


def setUpModule() -> None:
    EconomySimulation._warm_up = lambda self: None


def tearDownModule() -> None:
    EconomySimulation._warm_up = _ORIGINAL_WARM_UP


def simulation(months: int = 1, **overrides) -> EconomySimulation:
    sim = EconomySimulation(SimulationConfig(seed=11, population_size=80, **overrides))
    for _ in range(months):
        sim.step()
    return sim


def money(sim: EconomySimulation) -> float:
    return sum(p.cash for p in sim.people) + sum(c.cash for c in sim.companies) + sim.public.cash


def president(sim: EconomySimulation) -> str:
    return sim.institutions.holder("presidence")


def set_traits(sim: EconomySimulation, person_id: str, charisma: float = 0.5, integrity: float = 0.5,
               ambition: float = 0.5) -> None:
    sim._power_state().traits[person_id] = (charisma, integrity, ambition)


def private_firm(sim: EconomySimulation, sector: str = "goods"):
    return next(c for c in sim._private_firms() if c.sector == sector)


class PowerRegistryTests(unittest.TestCase):
    """Chaque réglage du lot K est listé et justifié dans la section « Réglages du lot K »
    d'AUTONOMIE.md, existe dans ``SimulationConfig`` et y est neutre pour l'identité."""

    ADDED = {*SWITCHES, *PARAMETERS}

    def test_settings_are_registered_and_neutral(self) -> None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "AUTONOMIE.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("## Réglages du lot K")
        end = text.find("\n## ", start + 1)
        section = text[start:] if end < 0 else text[start:end]
        registry = {m.group(1): m.group(2) for m in re.finditer(r"^\|\s*`(\w+)`\s*\|(.*)$", section, re.MULTILINE)}
        fields = {f.name: f for f in dataclasses.fields(SimulationConfig)}
        self.assertEqual(set(registry), self.ADDED)
        for name in self.ADDED:
            with self.subTest(parametre=name):
                self.assertEqual(fields[name].metadata.get("identity"), "neutre")
                self.assertGreaterEqual(len(registry[name]), 60)
        for name in SWITCHES:
            self.assertIs(fields[name].default, False, name)

    def test_disabled_is_identical_and_creates_no_state(self) -> None:
        reference = simulation()
        explicit = simulation(**{name: False for name in SWITCHES})
        for _ in range(5):
            self.assertEqual(reference.step(), explicit.step())
        self.assertIsNone(getattr(explicit, "power", None))

    def test_reading_influence_creates_no_state(self) -> None:
        sim = simulation(months=2)
        rows = sim.influence_table(5)
        self.assertEqual(len(rows), 5)
        self.assertIsNone(getattr(sim, "power", None))

    def test_ranking_alone_does_not_change_the_economy(self) -> None:
        reference = simulation()
        measured = simulation(influence_ranking=True)
        for _ in range(8):
            self.assertEqual(reference.step(), measured.step())
        self.assertEqual(len(measured.power.top_history), 9)


class InfluenceTests(unittest.TestCase):

    def test_score_is_the_sum_of_its_four_bounded_parts(self) -> None:
        sim = simulation(months=3, influence_ranking=True, personal_popularity=True)
        for row in sim.influence_scores(refresh=True).values():
            parts = row["fortune"] + row["fonction"] + row["capital"] + row["popularite"]
            self.assertAlmostEqual(row["total"], parts)
            self.assertTrue(0.0 <= row["fortune"] <= 30.0 and 0.0 <= row["fonction"] <= 35.0)
            self.assertTrue(0.0 <= row["capital"] <= 20.0 and 0.0 <= row["popularite"] <= 15.0)

    def test_president_ministers_and_executives_carry_office_points(self) -> None:
        sim = simulation(months=3, influence_ranking=True)
        scores = sim.influence_scores(refresh=True)
        inst = sim.institutions
        self.assertEqual(scores[president(sim)]["fonction"], 35.0)
        self.assertEqual(scores[inst.holder("finances")]["fonction"], 22.0)
        firm = next(c for c in sim.companies if c.active and c.executive_id and c.executive_id in scores
                    and c.executive_id not in {o.person_id for o in inst.offices.values()})
        self.assertGreater(scores[firm.executive_id]["fonction"], 6.0)
        self.assertIn("Direction", scores[firm.executive_id]["poste"])

    def test_wealth_and_controlled_capital_raise_influence(self) -> None:
        sim = simulation(months=3, influence_ranking=True)
        cabinet = {o.person_id for o in sim.institutions.offices.values()}
        person = next(p for p in sim.people if p.id not in cabinet)
        before = sim.influence_scores(refresh=True)[person.id]
        firm = private_firm(sim)
        person.cash += 5_000_000.0
        person.shares[firm.id] = 0.5 * (firm.shares_outstanding - firm.treasury_shares)
        after = sim.influence_scores(refresh=True)[person.id]
        self.assertGreater(after["fortune"], before["fortune"])
        self.assertGreater(after["capital"], before["capital"])
        self.assertIn(person.id, [row["id"] for row in sim.influence_table(3)])


class PopularityTests(unittest.TestCase):

    def test_traits_are_drawn_once_in_the_unit_interval(self) -> None:
        sim = simulation(months=2, personal_popularity=True)
        again = simulation(months=2, personal_popularity=True)
        self.assertEqual(sim.power.traits, again.power.traits)
        self.assertEqual(set(sim.power.traits), {p.id for p in sim.people})
        self.assertTrue(all(0.0 <= value <= 1.0 for traits in sim.power.traits.values() for value in traits))

    def test_president_becomes_known_and_unknowns_stay_unknown(self) -> None:
        sim = simulation(months=36, personal_popularity=True)
        state = sim.power
        self.assertGreater(state.notoriety[president(sim)], 0.9)
        visible = {o.person_id for o in sim.institutions.offices.values()}
        visible |= {c.executive_id for c in sim.companies if c.executive_id}
        visible |= set(state.alumni)
        unknown = [p.id for p in sim.people if p.id not in visible and state.notoriety[p.id] == 0.0]
        self.assertGreater(len(unknown), 20)
        self.assertEqual(sim._appeal(unknown[0]), 0.0)

    def test_good_results_raise_an_executive_opinion(self) -> None:
        opinions = {}
        for label, profit, staff in (("bon", 0.3, 0.5), ("mauvais", -0.3, 2.0)):
            sim = simulation(months=2, personal_popularity=True)
            firm = private_firm(sim)
            set_traits(sim, firm.executive_id)
            sim.power.opinion[firm.executive_id] = 0.5
            firm.previous_revenue = 100_000.0
            firm.previous_profit = profit * firm.previous_revenue
            sim.power.staff[firm.id] = staff * len(firm.employees)
            sim._update_popularity()
            opinions[label] = sim.power.opinion[firm.executive_id]
        self.assertGreater(opinions["bon"], 0.5)
        self.assertLess(opinions["mauvais"], 0.5)

    def test_president_opinion_follows_unemployment(self) -> None:
        sim = simulation(months=14, personal_popularity=True)
        for record in sim.history:
            record.unemployment = 0.25
        self.assertLess(sim._president_signal(), 0.0)
        for record in sim.history:
            record.unemployment = 0.0
        sim.institutions.offices["finances"].objective.progress = 1.0
        calm = sim._president_signal()
        sim.power.laws["medias"] = sim.month
        for record in sim.history:
            record.unemployment = 0.25
        self.assertGreater(calm, sim._president_signal())
        muted = sim._president_signal()
        del sim.power.laws["medias"]
        self.assertAlmostEqual(muted, 0.5 * sim._president_signal())


class CandidateElectionTests(unittest.TestCase):

    def test_popularity_shift_moves_individual_votes(self) -> None:
        config = SimulationConfig()
        voter = Voter(50.0, 0.5, 0.0, 0.0)
        base = incumbent_support(voter, 50.0, 30.0, config, 0.5)
        self.assertEqual(base, incumbent_support(voter, 50.0, 30.0, config, 0.5, 0.0))
        self.assertGreater(incumbent_support(voter, 50.0, 30.0, config, 0.5, 1.0), base)
        self.assertLess(incumbent_support(voter, 50.0, 30.0, config, 0.5, -1.0), base)

    def _before_election(self, **overrides) -> EconomySimulation:
        return simulation(months=12, election_period_months=12, candidate_elections=True,
                          personal_popularity=True, **overrides)

    def test_candidates_are_declared_from_opposite_camps(self) -> None:
        sim = self._before_election()
        candidates = sim.power.candidates
        self.assertIsNotNone(candidates)
        inst = sim.institutions
        self.assertEqual(candidates["sortant"], president(sim))
        self.assertNotIn(candidates["opposant"], {o.person_id for o in inst.offices.values()})
        social = sim.government.orientation >= 50.0
        self.assertEqual(inst.lean[candidates["opposant"]] < 50.0, social)

    def test_a_popular_opponent_wins_and_becomes_president(self) -> None:
        sim = self._before_election(vote_popularity_weight=40.0)
        state = sim.power
        candidates = state.candidates
        incumbent, opponent = candidates["sortant"], candidates["opposant"]
        for person_id, opinion in ((incumbent, 0.0), (opponent, 1.0)):
            set_traits(sim, person_id, charisma=opinion)
            state.opinion[person_id] = opinion
            state.notoriety[person_id] = 1.0
        sim.step()
        election = sim.institutions.elections[-1]
        self.assertFalse(election["reelu"])
        self.assertEqual(president(sim), opponent)
        self.assertEqual(sim.power.consecutive_terms, 1)
        self.assertIn(incumbent, sim.power.alumni)
        self.assertIsNone(sim.power.candidates)
        person = next(p for p in sim.people if p.id == opponent)
        self.assertEqual(person.employer_id, "state")

    def test_a_popular_incumbent_is_reelected_and_stays(self) -> None:
        sim = self._before_election(vote_popularity_weight=40.0)
        state = sim.power
        incumbent, opponent = state.candidates["sortant"], state.candidates["opposant"]
        for person_id, opinion in ((incumbent, 1.0), (opponent, 0.0)):
            set_traits(sim, person_id, charisma=opinion)
            state.opinion[person_id] = opinion
            state.notoriety[person_id] = 1.0
        sim.step()
        self.assertTrue(sim.institutions.elections[-1]["reelu"])
        self.assertEqual(president(sim), incumbent)
        self.assertEqual(sim.power.consecutive_terms, 2)

    def test_term_limit_puts_forward_an_heir_until_the_law_lifts_it(self) -> None:
        sim = simulation(months=6, election_period_months=12, candidate_elections=True,
                         executive_power_laws=True)
        sim.power.consecutive_terms = TERM_LIMIT
        for _ in range(6):
            sim.step()
        candidates = sim.power.candidates
        self.assertTrue(candidates["dauphin"])
        self.assertNotEqual(candidates["sortant"], president(sim))
        sim.power.laws["mandats"] = sim.month
        sim.power.candidates = None
        sim._declare_candidates()
        self.assertFalse(sim.power.candidates["dauphin"])
        self.assertEqual(sim.power.candidates["sortant"], president(sim))

    def test_an_elected_heir_replaces_the_president(self) -> None:
        sim = simulation(months=6, election_period_months=12, candidate_elections=True,
                         executive_power_laws=True, personal_popularity=True, vote_popularity_weight=40.0)
        sim.power.consecutive_terms = TERM_LIMIT
        former = president(sim)
        for _ in range(6):
            sim.step()
        state = sim.power
        heir, opponent = state.candidates["sortant"], state.candidates["opposant"]
        for person_id, opinion in ((heir, 1.0), (opponent, 0.0)):
            set_traits(sim, person_id, charisma=opinion)
            state.opinion[person_id] = opinion
            state.notoriety[person_id] = 1.0
        sim.step()
        self.assertTrue(sim.institutions.elections[-1]["reelu"])
        self.assertEqual(president(sim), heir)
        self.assertNotEqual(heir, former)
        holders = [o.person_id for o in sim.institutions.offices.values()]
        self.assertEqual(len(holders), len(set(holders)))


class PowerLawTests(unittest.TestCase):

    def _at_law_month(self, **overrides) -> EconomySimulation:
        sim = simulation(months=3, executive_power_laws=True, business_subsidies=True, **overrides)
        sim.institutions.terms[-1].since = sim.month - 6
        return sim

    def test_an_ambitious_president_with_capital_passes_laws_in_order(self) -> None:
        sim = self._at_law_month()
        set_traits(sim, president(sim), integrity=0.1, ambition=0.9)
        sim.power.mandate_share = 0.70
        sim._legislate()
        self.assertEqual(list(sim.power.laws), ["enveloppe"])
        sim._legislate()
        self.assertEqual(list(sim.power.laws), ["enveloppe", "nominations"])
        self.assertIn("Loi de pouvoir votée", sim.institutions.journal[-1].decision)

    def test_irrelevant_laws_are_skipped(self) -> None:
        sim = simulation(months=3, executive_power_laws=True)
        sim.institutions.terms[-1].since = sim.month - 6
        set_traits(sim, president(sim), integrity=0.1, ambition=0.9)
        sim.power.mandate_share = 0.95
        for _ in LAWS:
            sim._legislate()
        self.assertEqual(set(sim.power.laws), {"nominations", "controle", "medias"})

    def test_a_weak_mandate_fails_and_each_law_is_harder(self) -> None:
        sim = self._at_law_month()
        set_traits(sim, president(sim), integrity=0.1, ambition=0.9)
        sim.power.mandate_share = 0.50
        sim._legislate()
        self.assertEqual(sim.power.laws, {})
        sim.power.mandate_share = 0.53
        sim._legislate()
        sim._legislate()
        self.assertEqual(list(sim.power.laws), ["enveloppe"])

    def test_a_modest_president_proposes_nothing_and_an_honest_one_repeals(self) -> None:
        sim = self._at_law_month()
        sim.power.mandate_share = 0.9
        set_traits(sim, president(sim), integrity=0.5, ambition=0.3)
        sim._legislate()
        self.assertEqual(sim.power.laws, {})
        sim.power.laws.update({"enveloppe": 1, "controle": 2})
        set_traits(sim, president(sim), integrity=0.9, ambition=0.9)
        sim._legislate()
        self.assertEqual(list(sim.power.laws), ["enveloppe"])

    def test_laws_are_only_debated_once_a_year(self) -> None:
        sim = self._at_law_month()
        set_traits(sim, president(sim), integrity=0.1, ambition=0.9)
        sim.power.mandate_share = 0.9
        sim.institutions.terms[-1].since = sim.month - 5
        sim._legislate()
        self.assertEqual(sim.power.laws, {})


class BusinessAidTests(unittest.TestCase):

    def _ready(self, **overrides) -> EconomySimulation:
        sim = simulation(months=3, business_subsidies=True, **overrides)
        for company in sim.companies:
            company.loss_streak = 0
        sim.public.cash += 10_000_000.0
        sim.power.aided = set()
        return sim

    def test_aid_goes_to_firms_with_lasting_losses_and_conserves_money(self) -> None:
        sim = self._ready()
        firm, other = sim._private_firms()[:2]
        firm.loss_streak = 6
        firm_cash, other_cash, state_cash, before = firm.cash, other.cash, sim.public.cash, money(sim)
        sim._business_aid()
        paid = firm.cash - firm_cash
        self.assertGreater(paid, 0.0)
        self.assertLessEqual(paid, AID_PAYROLL_SHARE * firm.wage_bill + 1e-9)
        self.assertEqual(other.cash, other_cash)
        self.assertAlmostEqual(sim.public.cash, state_cash - paid)
        self.assertAlmostEqual(money(sim), before, places=4)
        self.assertAlmostEqual(sim.institutions.book.flows["aides_entreprises"], -paid)
        self.assertIn(firm.name, sim.institutions.journal[-1].decision)
        self.assertEqual(sim.power.aid_log[-1]["type"], "critères")

    def test_banks_public_firms_and_healthy_firms_get_nothing(self) -> None:
        sim = self._ready()
        bank = next(c for c in sim.companies if c.active and c.sector == "bank")
        bank.loss_streak = 12
        before = sim.public.cash
        sim._business_aid()
        self.assertEqual(sim.public.cash, before)
        self.assertEqual(sim.power.aid_log, [])

    def test_state_book_stays_reconciled_over_time(self) -> None:
        sim = simulation(months=1, business_subsidies=True)
        paid = 0.0
        for _ in range(40):
            sim.step()
            record = sim.institutions.history[-1]
            self.assertLess(abs(record.cash_residual), 1e-3)
            self.assertLess(abs(record.debt_residual), 1e-3)
            paid += -record.flows.get("aides_entreprises", 0.0)
        self.assertGreater(paid, 0.0)


class FavoritismTests(unittest.TestCase):

    def _ready(self, integrity: float = 0.05, **overrides) -> tuple:
        """Président d'intégrité donnée, loi « enveloppe » votée, une entreprise dont le dirigeant
        est riche et lié à la présidence (ancien employeur, don de campagne)."""
        settings = {"favoritism_monthly_chance": 1.0, **overrides}
        sim = simulation(months=3, business_subsidies=True, political_favoritism=True,
                         executive_power_laws=True, **settings)
        state = sim.power
        head = president(sim)
        set_traits(sim, head, integrity=integrity)
        state.laws["enveloppe"] = sim.month
        for company in sim.companies:
            company.loss_streak = 0
        sim.public.cash += 10_000_000.0
        firm = private_firm(sim)
        patron = next(p for p in sim.people if p.id == firm.executive_id)
        patron.cash += 20_000_000.0
        sim.institutions.offices["presidence"].previous_employer = firm.name
        state.donations[firm.id] = {head: 1000.0}
        return sim, firm, patron

    def test_a_connected_patron_receives_the_discretionary_envelope(self) -> None:
        sim, firm, patron = self._ready()
        sim.power.laws["controle"] = sim.month
        self.assertGreaterEqual(sim._connection(firm, president(sim)), FAVOR_CONNECTION)
        cash, before = firm.cash, money(sim)
        budget = sim.live.business_aid_gdp_share * sim._annual_gdp() / 12.0
        sim._business_aid()
        self.assertAlmostEqual(firm.cash - cash, DISCRETIONARY_SHARE * budget, places=4)
        self.assertAlmostEqual(money(sim), before, places=4)
        affair = sim.power.affairs[-1]
        self.assertEqual((affair["entreprise"], affair["patron"], affair["revele"]), (firm.id, patron.id, None))
        self.assertIn("Aide discrétionnaire", sim.institutions.journal[-1].decision)

    def test_every_condition_is_required(self) -> None:
        cases = {
            "président intègre": dict(integrity=0.8, law="controle"),
            "contrôle intact et président prudent": dict(integrity=0.3),
            "pas d'enveloppe": dict(law="controle", drop="enveloppe"),
            "pas de lien": dict(law="controle", unlink=True),
            "patron sans influence": dict(law="controle", poor=True),
            "hasard défavorable": dict(law="controle", favoritism_monthly_chance=0.0),
        }
        for label, case in cases.items():
            with self.subTest(cas=label):
                extra = {k: v for k, v in case.items() if k == "favoritism_monthly_chance"}
                sim, firm, patron = self._ready(integrity=case.get("integrity", 0.05), **extra)
                state = sim.power
                if "law" in case:
                    state.laws[case["law"]] = sim.month
                if "drop" in case:
                    del state.laws[case["drop"]]
                if case.get("unlink"):
                    state.donations.clear()
                    sim.institutions.offices["presidence"].previous_employer = "Sans emploi"
                    next(p for p in sim.people if p.id == president(sim)).shares.pop(firm.id, None)
                if case.get("poor"):
                    patron.cash = 0.0
                    patron.shares.clear()
                sim._business_aid()
                self.assertEqual(state.affairs, [])

    def test_a_very_dishonest_president_dares_despite_the_audit(self) -> None:
        sim, firm, _ = self._ready(integrity=0.05)
        sim._business_aid()
        self.assertEqual(len(sim.power.affairs), 1)

    def test_a_revealed_affair_is_repaid_fined_and_punished(self) -> None:
        sim, firm, patron = self._ready(personal_popularity=True)
        state = sim.power
        state.laws["controle"] = sim.month
        sim._business_aid()
        affair = state.affairs[-1]
        head = president(sim)
        opinion_before = (sim._opinion(head), sim._opinion(patron.id))
        minister = sim.institutions.holder("industrie")
        state_cash, before = sim.public.cash, money(sim)
        sim._reveal_affair(affair)
        due = affair["montant"] * (1.0 + FINE_RATE)
        self.assertEqual(affair["revele"], sim.month)
        self.assertAlmostEqual(sim.public.cash - state_cash, due, places=4)
        self.assertAlmostEqual(affair["recouvre"], due, places=4)
        self.assertAlmostEqual(money(sim), before, places=4)
        self.assertLess(sim._opinion(head), opinion_before[0])
        self.assertLess(sim._opinion(patron.id), opinion_before[1])
        self.assertNotIn("controle", state.laws)
        self.assertIn("Scandale", sim.institutions.journal[-2].decision + sim.institutions.journal[-1].decision)
        sim.step()
        self.assertNotEqual(sim.institutions.holder("industrie"), minister)
        self.assertLess(abs(sim.institutions.history[-1].cash_residual), 1e-3)

    def test_a_fine_never_exceeds_the_cash_of_the_firm(self) -> None:
        sim, firm, _ = self._ready()
        sim._business_aid()
        affair = sim.power.affairs[-1]
        firm.cash = 10.0
        sim._reveal_affair(affair)
        self.assertEqual(affair["recouvre"], 10.0)
        self.assertEqual(firm.cash, 0.0)

    def test_detection_depends_on_audit_and_laws(self) -> None:
        sim, firm, _ = self._ready(audit_detection_chance=1.0)
        sim._business_aid()
        sim._detect_affairs()
        self.assertIsNotNone(sim.power.affairs[-1]["revele"])
        sim, firm, _ = self._ready(audit_detection_chance=0.0)
        state = sim.power
        sim._business_aid()
        state.laws.update({"controle": 1, "medias": 1})
        for _ in range(12):
            sim._detect_affairs()
        revealed = state.affairs[-1]["revele"]
        again, _, _ = self._ready(audit_detection_chance=0.0)
        again._business_aid()
        again.power.laws.update({"controle": 1, "medias": 1})
        for _ in range(12):
            again._detect_affairs()
        self.assertEqual(revealed, again.power.affairs[-1]["revele"])


class LobbyingTests(unittest.TestCase):

    def _case(self, **overrides) -> tuple:
        sim = simulation(months=3, corporate_lobbying=True, **overrides)
        firm = private_firm(sim, "food")
        firm.previous_profit, firm.cash = 200_000.0, firm.cash + 1_000_000.0
        case = MonopolyCase("food", sim.month, target=firm.id)
        sim.institutions.cases.append(case)
        return sim, firm, case

    def test_lobbying_pays_services_and_slows_the_case(self) -> None:
        sim, firm, case = self._case()
        patron = next(p for p in sim.people if p.id == firm.executive_id)
        patron.cash += 20_000_000.0
        cash, before = firm.cash, money(sim)
        services = sum(c.revenue for c in sim.companies if c.sector == "services")
        sim._lobby_cases()
        spent = cash - firm.cash
        self.assertGreater(spent, 0.0)
        self.assertAlmostEqual(sum(c.revenue for c in sim.companies if c.sector == "services") - services, spent)
        self.assertAlmostEqual(money(sim), before, places=4)
        drag = sim._case_drag(case)
        self.assertTrue(0.0 < drag <= 0.5)

    def test_a_losing_or_public_target_does_not_lobby(self) -> None:
        sim, firm, case = self._case()
        firm.previous_profit = -1.0
        sim._lobby_cases()
        self.assertEqual(sim._case_drag(case), 0.0)

    def test_nominations_law_freezes_the_case_of_a_connected_firm(self) -> None:
        sim, firm, case = self._case(executive_power_laws=True)
        head = president(sim)
        set_traits(sim, head, integrity=0.1)
        sim.power.laws["nominations"] = sim.month
        self.assertLess(sim._case_drag(case), 1.0)
        sim.institutions.offices["presidence"].previous_employer = firm.name
        sim.power.donations[firm.id] = {head: 1.0}
        self.assertEqual(sim._case_drag(case), 1.0)
        set_traits(sim, head, integrity=0.9)
        self.assertLess(sim._case_drag(case), 1.0)

    def test_campaign_donations_reach_the_closest_candidate(self) -> None:
        sim = simulation(months=12, election_period_months=12, candidate_elections=True,
                         corporate_lobbying=True)
        state = sim.power
        candidates = state.candidates
        self.assertIsNotNone(candidates)
        state.campaign, state.donations = {}, {}
        for firm in sim._private_firms():
            firm.previous_profit, firm.cash = 100_000.0, firm.cash + 500_000.0
        before = money(sim)
        sim._campaign_donations()
        self.assertAlmostEqual(money(sim), before, places=4)
        self.assertGreater(sum(state.campaign.values()), 0.0)
        self.assertTrue(set(state.campaign) <= {candidates["sortant"], candidates["opposant"]})
        donor = next(iter(state.donations))
        self.assertEqual(sum(state.donations[donor].values()) > 0.0, True)
        favored = max(state.campaign, key=state.campaign.get)
        other = candidates["opposant"] if favored == candidates["sortant"] else candidates["sortant"]
        shift = sim._election_shift()
        self.assertEqual(shift > 0.0, favored == candidates["sortant"] and state.campaign.get(other, 0.0) < state.campaign[favored])


class RevolvingDoorTests(unittest.TestCase):

    def test_a_former_minister_advises_a_large_firm_of_his_field(self) -> None:
        sim = simulation(months=3, revolving_door=True)
        state = sim.power
        head = president(sim)
        cabinet = {o.person_id for o in sim.institutions.offices.values()}
        former = next(p for p in sim.people if p.id not in cabinet and p.employer_id is None)
        state.alumni[former.id] = {"role": "industrie", "mois": sim.month, "presidence": head}
        for firm in sim._private_firms():
            firm.previous_profit = 10_000.0
        cash, before = former.cash, money(sim)
        sim._revolving_door()
        firm = next(c for c in sim.companies if state.advisors.get(c.id, {}).get("personne") == former.id)
        self.assertIn(firm.sector, ("energy", "materials", "goods"))
        self.assertEqual(former.cash, cash)
        sim._revolving_door()
        fee = ADVISOR_FEE_SHARE * sim.institutions.salaries["ministre"]
        self.assertAlmostEqual(former.cash - cash, fee)
        self.assertAlmostEqual(money(sim), before, places=4)
        self.assertGreaterEqual(sim._connection(firm, head), 0.3)

    def test_the_mandate_ends_and_money_is_conserved_in_a_run(self) -> None:
        sim = simulation(months=3, revolving_door=True, election_period_months=12)
        for _ in range(60):
            sim.step()
            self.assertLess(abs(sim.institutions.history[-1].cash_residual), 1e-3)
        state = sim.power
        self.assertGreater(len(state.alumni), 0)
        self.assertTrue(all(sim.month - a["mois"] < 36 for a in state.advisors.values()))
        self.assertGreater(sum(row.get("pantouflages", 0.0) for row in state.history), 0.0)


class DynastyTests(unittest.TestCase):

    def test_an_heir_keeps_a_share_of_a_known_name(self) -> None:
        for enabled, expected in ((True, DYNASTY_SHARE * 0.8), (False, 0.0)):
            sim = simulation(months=2, personal_popularity=True, political_dynasties=enabled)
            state = sim.power
            person = sim.people[5]
            state.notoriety[person.id], state.opinion[person.id] = 0.8, 0.9
            traits = state.traits[person.id]
            state.alumni[person.id] = {"role": "travail", "mois": 1, "presidence": "x"}
            sim._succeed(person, 0.0)
            self.assertAlmostEqual(state.notoriety[person.id], expected)
            self.assertNotEqual(state.traits[person.id], traits)
            self.assertNotIn(person.id, state.opinion)
            self.assertNotIn(person.id, state.alumni)
            self.assertEqual(person.id in state.dynasties, enabled)

    def test_an_unknown_name_is_not_inherited(self) -> None:
        sim = simulation(months=2, personal_popularity=True, political_dynasties=True)
        person = sim.people[5]
        sim.power.notoriety[person.id] = 0.1
        sim._succeed(person, 0.0)
        self.assertEqual(sim.power.notoriety[person.id], 0.0)
        self.assertEqual(sim.power.dynasties, {})


class PowerTogetherTests(unittest.TestCase):

    def test_everything_enabled_runs_with_exact_accounts(self) -> None:
        sim = simulation(months=1, election_period_months=24, **{name: True for name in SWITCHES})
        for _ in range(96):
            record = sim.step()
            state_record = sim.institutions.history[-1]
            self.assertTrue(math.isfinite(record.nominal_gdp))
            self.assertLess(abs(state_record.cash_residual), 1e-3)
            self.assertLess(abs(state_record.debt_residual), 1e-3)
        state = sim.power
        self.assertEqual(len(state.history), 97)
        self.assertEqual(len(sim.power_history()), 97)
        self.assertGreaterEqual(len(sim.institutions.elections), 3)
        self.assertIn("president_elu", sim.institutions.elections[-1])
        self.assertTrue(all(0.0 <= value <= 1.0 for value in state.opinion.values()))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in state.notoriety.values()))
        holders = [o.person_id for o in sim.institutions.offices.values()]
        self.assertEqual(len(holders), len(set(holders)))

    def test_same_seed_gives_the_same_story(self) -> None:
        first = simulation(months=40, election_period_months=12, **{name: True for name in SWITCHES})
        second = simulation(months=40, election_period_months=12, **{name: True for name in SWITCHES})
        self.assertEqual(first.history[-1], second.history[-1])
        self.assertEqual(first.power.log, second.power.log)
        self.assertEqual(first.power.opinion, second.power.opinion)


if __name__ == "__main__":
    unittest.main()
