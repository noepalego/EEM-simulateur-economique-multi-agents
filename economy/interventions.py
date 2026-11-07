"""Interventions de l'opérateur sur une simulation en cours (mode intervention).

Trois sortes d'actions, décidées depuis l'interface entre deux mois :

- **ponctuelles et définitives** : trésorerie d'un ménage ou d'une entreprise, compétence d'un
  ménage. Elles modifient l'agent immédiatement ; le moteur n'y revient jamais (100 000 € donnés
  restent dans la caisse jusqu'à ce que l'agent les dépense) ;
- **orientation imposée** : un nouveau gouvernement de l'orientation choisie est formé au début
  du mois suivant, comme après une élection (doctrine, cabinet, loi de finances rectificative).
  Il gouverne jusqu'à la prochaine élection, qui a lieu à la date normale et se déroule
  normalement (le gouvernement imposé est le sortant jugé par les électeurs) ;
- **leviers forcés** (TVA, impôts, allocation chômage, salaire minimum) : la valeur choisie
  remplace chaque mois la valeur votée, jusqu'à la prochaine élection ou jusqu'à ce qu'un
  nouveau gouvernement soit imposé ; la valeur votée reprend alors la main.

Neutralité : ``simulation.interventions`` n'existe pas tant qu'aucune intervention n'a été
faite, et les crochets du moteur ne font alors rien (aucun tirage, aucun calcul flottant).

Cohérence monétaire : l'argent ajouté à un agent est émis comme de la monnaie fiduciaire
(billets de la banque centrale) ; ``ledger.currency`` est relevé du même montant, de sorte que
l'identité « trésorerie − monnaie fiduciaire = dépôts des bilans bancaires » reste exacte
(``ledger.residual`` inchangé). Un retrait détruit symétriquement de la monnaie.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# générateur propre aux gouvernements imposés : ne consomme jamais ceux du moteur
INTERVENTION_SEED_OFFSET = 51_472_919

# levier -> (attribut de ``live``, libellé, unité d'affichage)
LEVERS = {
    "vat_rate": ("TVA", "%"),
    "income_tax_rate": ("Impôt sur le revenu (taux de base)", "%"),
    "top_marginal_income_tax_rate": ("Taux marginal supérieur", "%"),
    "corporate_tax_rate": ("Impôt sur les bénéfices", "%"),
    "unemployment_benefit": ("Allocation chômage", "€"),
    "minimum_wage": ("Salaire minimum légal", "€"),
}

SKILL_BOUNDS = (0.5, 2.0)  # bornes déjà appliquées par le marché du travail


@dataclass
class Interventions:
    """Interventions en cours et journal (attaché à ``simulation.interventions``)."""

    levers: dict = field(default_factory=dict)        # attribut de ``live`` -> valeur forcée
    voted: dict = field(default_factory=dict)         # valeur votée remplacée (affichage)
    pending_orientation: float | None = None          # gouvernement à imposer au prochain mois
    forced_months: list = field(default_factory=list)  # mois où un gouvernement a été imposé
    issued_money: float = 0.0                          # monnaie émise nette par l'opérateur
    journal: list = field(default_factory=list)

    def log(self, month: int, kind: str, target: str, before, after, note: str = "") -> None:
        def text(value) -> str:
            if value is None:
                return "—"
            return f"{value:g}" if isinstance(value, float) else str(value)

        self.journal.append({
            "Mois": month, "Intervention": kind, "Cible": target,
            "Avant": text(before), "Après": text(after), "Note": note,
        })

    # -- crochets du moteur ----------------------------------------------------------------
    def apply_levers(self, live) -> None:
        """Remplace dans ``live`` les valeurs votées par les valeurs forcées (chaque mois)."""
        self.voted = {name: getattr(live, name) for name in self.levers}
        for name, value in self.levers.items():
            setattr(live, name, value)

    def end_mandate(self, month: int, reason: str) -> None:
        """Fin du mandat : les leviers forcés sont rendus aux lois du gouvernement."""
        if self.levers:
            self.log(month, "Leviers rendus au gouvernement", ", ".join(LEVERS[n][0] for n in self.levers),
                     "forcés", "votés", reason)
            self.levers = {}


def interventions(sim) -> Interventions:
    """Interventions de la simulation, créées à la première action."""
    current = getattr(sim, "interventions", None)
    if current is None:
        current = sim.interventions = Interventions()
    return current


def _issue(sim, amount: float) -> None:
    """Émission (ou destruction) de monnaie fiduciaire correspondant à une intervention."""
    sim.ledger.currency += amount
    interventions(sim).issued_money += amount


def adjust_household_cash(sim, person_id: str, amount: float) -> float:
    """Ajoute (montant positif) ou retire (négatif) de la trésorerie d'un ménage.

    Un retrait est plafonné à la trésorerie positive disponible. Retourne le montant appliqué.
    """
    person = next((p for p in sim.people if p.id == person_id), None)
    if person is None or amount == 0.0:
        return 0.0
    applied = amount if amount > 0.0 else -min(-amount, max(0.0, person.cash))
    if applied == 0.0:
        return 0.0
    before = person.cash
    person.cash += applied
    _issue(sim, applied)
    interventions(sim).log(sim.month, "Trésorerie du ménage", person.name, round(before, 2),
                           round(person.cash, 2), f"{applied:+,.0f} €".replace(",", " "))
    return applied


def adjust_company_cash(sim, company_id: str, amount: float) -> float:
    """Ajoute ou retire de la trésorerie d'une entreprise active. Les banques sont exclues : leur
    trésorerie ne fait pas partie de la monnaie détenue par le public (``ledger.nonbank_cash``),
    l'émission correspondante fausserait l'identité des dépôts. Retourne le montant appliqué."""
    company = next((c for c in sim.companies if c.id == company_id), None)
    if company is None or not company.active or company.sector == "bank" or amount == 0.0:
        return 0.0
    applied = amount if amount > 0.0 else -min(-amount, max(0.0, company.cash))
    if applied == 0.0:
        return 0.0
    before = company.cash
    company.cash += applied
    _issue(sim, applied)
    interventions(sim).log(sim.month, "Trésorerie de l'entreprise", company.name, round(before, 2),
                           round(company.cash, 2), f"{applied:+,.0f} €".replace(",", " "))
    return applied


def set_household_skill(sim, person_id: str, skill: float) -> float:
    """Fixe la compétence d'un ménage (bornée comme le marché du travail). Définitif : la
    compétence évolue ensuite normalement (apprentissage, érosion au chômage)."""
    person = next((p for p in sim.people if p.id == person_id), None)
    if person is None:
        return 0.0
    low, high = SKILL_BOUNDS
    value = max(low, min(high, float(skill)))
    if value != person.skill:
        before = person.skill
        person.skill = value
        interventions(sim).log(sim.month, "Compétence du ménage", person.name,
                               round(before, 3), round(value, 3))
    return value


def request_orientation(sim, orientation: float) -> None:
    """Demande un gouvernement d'orientation donnée (0 à 100), formé au début du mois suivant."""
    current = interventions(sim)
    # les leviers forcés jusqu'ici sont rendus : le nouveau gouvernement applique ses lois ; ceux
    # forcés après la demande valent pour son mandat, jusqu'à l'élection suivante
    current.end_mandate(sim.month, "nouveau gouvernement demandé")
    current.pending_orientation = max(0.0, min(100.0, float(orientation)))
    current.log(sim.month, "Gouvernement demandé", "État", round(sim.government.orientation, 1),
                current.pending_orientation, "formé au début du mois suivant")


def cancel_orientation(sim) -> None:
    current = getattr(sim, "interventions", None)
    if current is not None and current.pending_orientation is not None:
        current.log(sim.month, "Demande de gouvernement annulée", "État", current.pending_orientation, None)
        current.pending_orientation = None


def set_lever(sim, name: str, value: float) -> None:
    """Force un levier jusqu'à la prochaine élection (valeur dans l'unité de ``live``)."""
    if name not in LEVERS:
        raise KeyError(name)
    current = interventions(sim)
    if name not in current.voted:
        # levier non forcé le mois écoulé : ``live`` porte la valeur votée
        current.voted[name] = getattr(sim.live, name)
    before = current.levers.get(name, getattr(sim.live, name))
    current.levers[name] = max(0.0, float(value))
    current.log(sim.month, "Levier forcé", LEVERS[name][0], before, current.levers[name],
                "jusqu'à la prochaine élection")


def clear_lever(sim, name: str) -> None:
    current = getattr(sim, "interventions", None)
    if current is not None and name in current.levers:
        current.log(sim.month, "Levier rendu au gouvernement", LEVERS[name][0], current.levers.pop(name), "voté")


def doctrine_random(sim) -> random.Random:
    """Générateur des doctrines imposées : graine et mois, indépendant des générateurs du moteur."""
    return random.Random(sim.config.seed * 1_000_003 + INTERVENTION_SEED_OFFSET + sim.month)
