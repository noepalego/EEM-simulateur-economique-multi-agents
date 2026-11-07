"""Démographie des ménages (idée 3, novembre 2026) : âge, mortalité, succession, droits de succession.

Un ménage est une lignée : au décès de son chef, l'héritier lui succède sous le même identifiant.
Il garde donc tout ce que le ménage possède ou doit (trésorerie, titres, obligations, logements,
prêts), son logement et son contrat de travail, de sorte qu'aucun registre (prêts, actionnaires,
baux, emplois) n'a à être réécrit et qu'aucune monnaie n'est créée. Il repart en revanche de sa
propre compétence (tirée comme celle d'un nouveau ménage, plus l'effet de l'éducation publique,
idée 5), de ses propres traits (goût du risque, propension à consommer, esprit d'entreprise,
proximité politique) et sans carrière : l'expérience accumulée par le défunt disparaît avec lui.

Les droits de succession sont une décision de l'État : taux de base × (1 + position « tranche
supérieure » de la doctrine), donc nuls à l'extrême droite et doublés à l'extrême gauche, sur la
fortune nette au-delà d'un abattement indexé sur le salaire de référence. Ils sont payés sur la
trésorerie ; le reste est recouvré chaque mois sur la trésorerie au-delà de la réserve de
consommation (le rééquilibrage des portefeuilles vend alors des titres au cours, contre de vrais
acheteurs). Les tirages (âges, décès, traits de l'héritier) viennent d'un générateur propre
(graine + ``DEMOGRAPHY_SEED_OFFSET``) : ils ne déplacent aucun autre tirage du modèle.
"""

from __future__ import annotations

import bisect
import math
import random

from economy.labor import initial_skills
from economy.models import Person, SimulationConfig
from economy.sectors import SKILL_SECTORS

DEMOGRAPHY_SEED_OFFSET = 86028157
MAX_AGE_YEARS = 110


def annual_mortality(age_years: float, config: SimulationConfig) -> float:
    """Probabilité annuelle de décès à cet âge (loi de Gompertz), jamais au-delà de 1."""
    if config.mortality_level <= 0.0:
        return 0.0
    if age_years >= MAX_AGE_YEARS:
        return 1.0
    return min(1.0, config.mortality_level * math.exp(config.mortality_growth * age_years))


def monthly_mortality(age_months: int, config: SimulationConfig) -> float:
    """Probabilité mensuelle de décès (taux annuel converti, sans double compte)."""
    annual = annual_mortality(age_months / 12.0, config)
    return 1.0 if annual >= 1.0 else 1.0 - (1.0 - annual) ** (1.0 / 12.0)


def stationary_age_table(config: SimulationConfig) -> tuple[list[int], list[float]]:
    """Âges adultes (années) et répartition cumulée d'une population stationnaire : densité
    proportionnelle à la survie depuis ``adult_age_years`` (table de mortalité ci-dessus)."""
    start = int(config.adult_age_years)
    ages, cumulative, survival, total = [], [], 1.0, 0.0
    for age in range(start, MAX_AGE_YEARS):
        ages.append(age)
        total += survival
        cumulative.append(total)
        survival *= 1.0 - annual_mortality(age + 0.5, config)
    return ages, [value / total for value in cumulative]


def draw_initial_age(rng: random.Random, table: tuple[list[int], list[float]]) -> int:
    """Âge de départ en mois, tiré dans la population stationnaire (deux tirages)."""
    ages, cumulative = table
    index = min(len(ages) - 1, bisect.bisect_left(cumulative, rng.random()))
    return ages[index] * 12 + rng.randrange(12)


def life_expectancy(config: SimulationConfig, from_age_years: float | None = None) -> float:
    """Espérance de vie (années) à partir d'un âge, d'après la même table (mesure seule)."""
    age = config.adult_age_years if from_age_years is None else from_age_years
    survival, years = 1.0, 0.0
    while age < MAX_AGE_YEARS and survival > 1e-9:
        years += survival
        survival *= 1.0 - annual_mortality(age + 0.5, config)
        age += 1.0
    return years


class DemographyMixin:
    """Vieillissement, décès et successions des ménages (phase 7, avant la croissance de la
    population)."""

    def _seed_ages(self) -> None:
        """Âges de la population de départ (générateur démographique, ordre de la population)."""
        table = stationary_age_table(self.live)
        for person in self.people:
            person.age_months = draw_initial_age(self.demography_random, table)

    def _newcomer_age(self) -> int:
        """Âge d'un nouveau ménage : jeune adulte (entre l'âge adulte et dix ans de plus)."""
        return int(12 * (self.live.adult_age_years + 10.0 * self.demography_random.random()))

    def _education_skill_bonus(self) -> float:
        """Compétence de départ ajoutée par l'éducation publique reçue (idée 5) : gain maximal ×
        qualité de l'éducation (couverture lissée du service, entre 0 et 1)."""
        return self.live.education_skill_gain * max(0.0, min(1.0, getattr(self, "education_quality", 0.0)))

    def _estate_tax_rate(self) -> float:
        """Taux des droits de succession décidé par la doctrine en place."""
        inst = getattr(self, "institutions", None)
        position = inst.doctrine.positions.get("top_tax", 0.0) if inst is not None and inst.doctrine else 0.0
        return max(0.0, self.live.estate_tax_rate * (1.0 + position))

    def _demography(self, f) -> None:
        """Un mois de vie : chaque chef de ménage vieillit ; un décès ouvre une succession."""
        live = self.live
        if live.mortality_level <= 0.0 or not self.people:
            return
        from economy.wealth import household_net_worth, share_prices

        inst = self.institutions
        backlog = inst.estate_backlog
        self._collect_estate_backlog(f)
        prices = None
        allowance = live.estate_tax_allowance_years * 12.0 * max(self.wage_reference, 0.0)
        rate = self._estate_tax_rate()
        for person in self.people:
            person.age_months += 1
            if self.demography_random.random() >= monthly_mortality(person.age_months, live):
                continue
            prices = prices if prices is not None else share_prices(self.companies)
            estate = household_net_worth(person, self.companies, live, prices)
            due = rate * max(0.0, estate - allowance)
            paid = max(0.0, min(due, person.cash))
            if paid > 0.0:
                person.cash -= paid
                person.expenses_this_turn += paid
                person.cumulative_expenses += paid
                self.public.cash += paid
                f.estate_taxes += paid
            if due - paid > 1e-9:
                backlog[person.id] = backlog.get(person.id, 0.0) + due - paid
            self._succeed(person, estate - due)
            f.deaths += 1
        if f.estate_taxes:
            inst.book.add("droits_succession", f.estate_taxes)
            self.public.revenue += f.estate_taxes

    def _collect_estate_backlog(self, f) -> None:
        """Droits restant dus : recouvrés sur la trésorerie au-delà de la réserve de consommation
        (``savings_cash_buffer_months`` mois de revenu attendu)."""
        backlog = self.institutions.estate_backlog
        if not backlog:
            return
        people = {p.id: p for p in self.people}
        for person_id in list(backlog):
            person = people.get(person_id)
            if person is None:
                del backlog[person_id]
                continue
            reserve = self.live.savings_cash_buffer_months * max(0.0, person.expected_income)
            paid = max(0.0, min(backlog[person_id], person.cash - reserve))
            if paid <= 0.0:
                continue
            person.cash -= paid
            person.expenses_this_turn += paid
            person.cumulative_expenses += paid
            self.public.cash += paid
            f.estate_taxes += paid
            backlog[person_id] -= paid
            if backlog[person_id] <= 1e-9:
                del backlog[person_id]

    def _succeed(self, person: Person, inherited: float) -> None:
        """L'héritier succède au chef de ménage décédé (même identifiant, mêmes avoirs et dettes,
        même logement et même emploi) avec sa propre compétence et ses propres traits."""
        from economy.institutions import political_lean

        live, rng = self.live, self.demography_random
        # l'héritier entre dans la vie active : jeune adulte, sans carrière (cohérent avec la table
        # stationnaire des âges de départ ; essayé à « âge du défunt − 30 ans » : chefs de ménage
        # de 48 ans sans aucune expérience, âge moyen dérivant vers 60 ans)
        person.age_months = self._newcomer_age()
        person.generation += 1
        person.inherited_wealth = inherited
        person.skill = rng.uniform(0.55, 1.05) + self._education_skill_bonus()
        person.skills = initial_skills(person.skill, rng, SKILL_SECTORS)
        person.risk_tolerance = rng.random()
        person.consumption_propensity = rng.uniform(
            1.0 - live.household_propensity_variance, 1.0 + live.household_propensity_variance
        )
        person.quality_preference = rng.uniform(0.65, 1.35)
        person.entrepreneurial_spirit = rng.random()
        self.institutions.lean[person.id] = political_lean(rng)
        person.career_months = 0
        person.leadership_experience = 0
        person.employer_tenure = 0
        person.rank = -1
        person.lifetime_earnings = 0.0
