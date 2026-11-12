"""Lot M (novembre 2026) : entreprises de médias.

Un réglage, ``media_sector``, désactivé par défaut (aucun calcul, aucun tirage, aucun attribut
ajouté sans lui). L'état vit dans ``simulation.media`` (``MediaState``), créé au premier usage ;
les tirages viennent d'un générateur dédié.

- **Entreprises** : des ménages aisés, pris à gauche, au centre et à droite, fondent chacun un
  média (secteur « media ») avec leur propre trésorerie. Un média est une entreprise comme les
  autres : salariés (les journalistes), salaires, impôt, dividendes, Bourse, rachat, faillite.
- **Recettes** : abonnements des ménages (une part de leur budget de services, TVA comprise) et
  publicité des producteurs d'alimentation et de biens, répartie selon l'audience.
- **Publicité** : le stock de publicité d'un annonceur relève son poids dans les achats des
  ménages (rendements décroissants) et s'efface s'il cesse de payer. Elle déplace des ventes
  entre producteurs, sans créer de demande.
- **Ligne éditoriale** (0 à 100) : celle du fondateur, puis elle dérive vers la position de
  l'actionnaire de contrôle. Chaque ménage lit le média le plus proche de lui, à qualité et
  crédibilité comparables.
- **Opinion et vote** : le ton d'un média envers le gouvernement (proche de sa ligne : favorable)
  déplace le jugement de ses lecteurs sur la présidence et le revenu qu'ils perçoivent au moment
  de voter.
- **Enquêtes** : la probabilité qu'une aide de faveur (lot K) soit révélée par la presse dépend
  des journalistes de chaque média et de son indépendance : il n'enquête ni sur son propriétaire
  ni sur un gros annonceur ; la loi « médias » rapproche les lignes du pouvoir et divise l'effort
  d'enquête par deux.

Tous les paiements sont de vrais transferts : la monnaie est conservée.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from economy.goods import household_budget
from economy.institutions import display_name
from economy.models import Company
from economy.payments import transfer_cash
from economy.treasury import working_capital

MEDIA_SEED_OFFSET = 96769021    # générateur dédié (traits des médias, attribution d'une révélation)
MEDIA_SECTOR = "media"
MEDIA_LABEL = "Médias"
ADVERTISER_SECTORS = ("food", "goods")
MEDIA_CAPITAL = 60000.0         # capital de départ d'un média, en euros de départ (indexé sur les salaires)
FOUNDER_STAKE = 0.5             # part de sa trésorerie qu'un fondateur engage au plus
FOUNDER_MINIMUM = 0.25          # part du capital de départ sans laquelle il renonce
START_STAFF = 3                 # journalistes visés à la création
REFOUNDING_MONTHS = 12          # délai avant une nouvelle fondation quand un média a disparu
LINE_SPEED = 0.05               # part de l'écart entre la ligne et la position du propriétaire comblée par mois
CONTROL_STAKE = 0.10            # part du capital à partir de laquelle un actionnaire pèse sur la ligne
REVIEW_MONTHS = 12              # un ménage reconsidère son média une fois par an
STAFF_HALF = 5.0                # journalistes qui donnent la moitié de la qualité maximale
TONE_RANGE = 25.0               # écart de ligne au-delà duquel un média est hostile au gouvernement
SPIN = 0.30                     # déplacement maximal du jugement sur la présidence par le ton des médias
CAPTURE = 0.5                   # tutelle (loi « médias ») : dérive vers le pouvoir, effort d'enquête restant
AD_DECAY = 0.20                 # part du stock de publicité d'un annonceur oubliée chaque mois
AD_HALF_WAGES = 5.0             # stock (en salaires mensuels de référence) qui donne la moitié de l'effet
ADVERTISER_CONFLICT = 0.10      # part des recettes publicitaires au-delà de laquelle un annonceur est ménagé
INVESTIGATION_CHANCE = 0.02     # probabilité mensuelle maximale qu'un média révèle une affaire donnée
SCOOP_TRUST = 0.10              # crédibilité gagnée par une révélation
TRUST_SPEED = 0.02              # retour mensuel de la crédibilité vers sa valeur de départ
TRUST_BASE = 0.5


@dataclass
class Outlet:
    """Ce qu'un média a de plus qu'une entreprise."""

    line: float                     # ligne éditoriale, de 0 (libérale) à 100 (sociale)
    founder: str
    trust: float = TRUST_BASE       # crédibilité, de 0 à 1
    owner: str | None = None        # actionnaire de contrôle du mois
    readers: int = 0
    subscriptions: float = 0.0      # recettes d'abonnement du mois
    advertising: float = 0.0        # recettes publicitaires du mois
    scoops: int = 0
    advertisers: dict = field(default_factory=dict)  # annonceur -> recettes récentes (lissées)


@dataclass
class MediaState:
    """État propre au lot M, créé au premier usage."""

    random: random.Random
    outlets: dict = field(default_factory=dict)     # entreprise -> Outlet
    readers: dict = field(default_factory=dict)     # ménage -> média lu
    ad_stock: dict = field(default_factory=dict)    # annonceur -> stock de publicité
    last_founding: int = -10 ** 6
    log: list = field(default_factory=list)
    month_stats: dict = field(default_factory=dict)
    history: list = field(default_factory=list)


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ad_factor(stock: float, wage: float, reach: float) -> float:
    """Poids dans les achats d'un annonceur : 1 + ``reach`` × s / (s + ``AD_HALF_WAGES``), où s
    est son stock de publicité en salaires mensuels de référence."""
    scaled = max(0.0, stock) / max(wage, 1.0)
    return 1.0 + max(0.0, reach) * scaled / (scaled + AD_HALF_WAGES)


class MediaMixin:
    """Entreprises de médias : audience, publicité, ligne éditoriale, enquêtes (lot M)."""

    # ------------------------------------------------------------------ état
    def _media_state(self) -> MediaState:
        state = getattr(self, "media", None)
        if state is None:
            state = self.media = MediaState(random.Random(self.config.seed + MEDIA_SEED_OFFSET))
        return state

    def _media_count(self, key: str, amount: float = 1.0) -> None:
        stats = self._media_state().month_stats
        stats[key] = stats.get(key, 0.0) + amount

    def _active_outlets(self) -> list:
        """Médias en activité, dans l'ordre du registre des entreprises."""
        outlets = self.media.outlets
        return [c for c in self.companies if c.active and c.id in outlets]

    def _publishing_outlets(self) -> list:
        """Médias qui paraissent : il faut au moins un journaliste."""
        return [c for c in self._active_outlets() if c.employees]

    # ------------------------------------------------------------------ phase 1
    def _media_month_start(self) -> None:
        """Après la formation du gouvernement : fondations, lignes éditoriales, lecteurs."""
        state = self._media_state()
        self._ensure_leans()
        wanted = max(0, int(self.live.media_outlets))
        if len(self._active_outlets()) < wanted and self.month - state.last_founding >= REFOUNDING_MONTHS:
            self._found_media(wanted - len(self._active_outlets()))
        self._media_lines()
        self._media_readers()
        for company in self._active_outlets():
            if company.age_months == 0 and company.revenue_ema <= 0.0:
                # un média qui démarre dimensionne sa rédaction sur les abonnements attendus
                company.revenue_ema = self._media_potential(company)

    def _subscription_budget(self, person) -> float:
        """Abonnement mensuel d'un ménage, TVA comprise : ``media_subscription_share`` de son
        budget de services, dans la limite de sa trésorerie."""
        live = self.live
        return min(
            max(0.0, person.cash),
            max(0.0, live.media_subscription_share)
            * household_budget(person, live.services_budget_share, live.services_cash_draw_rate, live)
            * person.engel_services
            * person.consumption_propensity,
        )

    def _media_potential(self, company) -> float:
        """Abonnements mensuels, hors TVA, que les lecteurs actuels d'un média lui apporteraient."""
        readers = self.media.readers
        total = sum(self._subscription_budget(p) for p in self.people if readers.get(p.id) == company.id)
        return total / (1.0 + self.live.vat_rate)

    def _media_restart_value(self, company) -> float:
        """Valeur mensuelle d'un premier poste pour un média sans journaliste : les abonnements
        de ses lecteurs, qu'il n'encaisse que s'il paraît (lu par ``_restart_values``)."""
        if getattr(self, "media", None) is None or company.id not in self.media.outlets:
            return 0.0
        return self._media_potential(company)

    def _found_media(self, count: int) -> None:
        """Des ménages aisés fondent ``count`` médias, chacun avec sa propre trésorerie.

        Pour que la presse soit pluraliste au départ, les fondateurs sont pris à tour de rôle à
        gauche (proximité politique au-delà de 60), au centre et à droite (en deçà de 40) : le
        plus riche de chaque camp qui n'est ni au gouvernement ni déjà fondateur. Il apporte au
        plus ``FOUNDER_STAKE`` de sa trésorerie, dans la limite du capital de départ, et renonce
        s'il n'en réunit pas ``FOUNDER_MINIMUM``."""
        state, live, inst = self.media, self.live, self.institutions
        state.last_founding = self.month
        capital = MEDIA_CAPITAL * self._wage_scale()
        cabinet = {office.person_id for office in inst.offices.values()}
        camps = (
            lambda lean: lean > 60.0,
            lambda lean: 40.0 <= lean <= 60.0,
            lambda lean: lean < 40.0,
        )
        rich = sorted(
            (p for p in self.people if p.id not in cabinet and not p.founded_company),
            key=lambda p: (-p.cash, p.id),
        )
        taken: set[str] = set()
        offset = len(state.outlets)
        rng = state.random
        for index in range(count):
            camp = camps[(offset + index) % len(camps)]
            founder = next(
                (p for p in rich if p.id not in taken and camp(inst.lean[p.id])), None
            ) or next((p for p in rich if p.id not in taken), None)
            if founder is None:
                return
            taken.add(founder.id)
            contribution = min(capital, FOUNDER_STAKE * max(0.0, founder.cash))
            if contribution < FOUNDER_MINIMUM * capital:
                continue
            shares = 1000.0
            floated = shares * live.founder_public_share_fraction
            company = Company(
                id=f"media_{self.month}_{offset + index + 1}",
                name=f"Média {display_name(founder.id).split(' ', 1)[-1]} ({self.month})",
                sector=MEDIA_SECTOR,
                cash=contribution,
                wage_offer=max(self.wage_reference, live.minimum_wage, 1.0),
                target_employees=START_STAFF,
                price=1.0,
                stock_price=max(1.0, contribution / shares),
                previous_stock_price=max(1.0, contribution / shares),
                shares_outstanding=shares,
                treasury_shares=floated,
                productivity_trait=rng.uniform(0.75, 1.35),
                quality_trait=rng.uniform(
                    1.0 - live.quality_trait_variance, 1.0 + live.quality_trait_variance
                ),
                risk_tolerance=founder.risk_tolerance,
                pricing_aggression=rng.random(),
                reinvestment_preference=rng.random(),
                innovation=rng.random(),
                executive_id=founder.id,
                ceo_experience=max(1, founder.leadership_experience),
                founder_id=founder.id,
                construction_pipeline=[],
            )
            founder.cash -= contribution
            founder.founded_company = True
            founder.shares[company.id] = shares - floated
            self.companies.append(company)
            state.outlets[company.id] = Outlet(line=inst.lean[founder.id], founder=founder.id)
            self._media_count("fondations")
            state.log.append({
                "mois": self.month, "type": "fondation",
                "texte": f"{display_name(founder.id)} fonde {company.name} "
                         f"(ligne {inst.lean[founder.id]:.0f})",
            })

    def _media_owner(self, company) -> str | None:
        """Actionnaire de contrôle d'un média : le plus gros porteur d'au moins
        ``CONTROL_STAKE`` de ses titres en circulation (ménage, entreprise, État ou fonds)."""
        floated = company.shares_outstanding - company.treasury_shares
        if floated <= 0.0:
            return None
        best, best_stake = None, CONTROL_STAKE * floated
        holders = [*self.people, *(c for c in self.companies if c.active),
                   *self._outside_shareholders()]
        for holder in holders:
            stake = holder.shares.get(company.id, 0.0) if holder.shares else 0.0
            if stake > best_stake or (stake == best_stake and best is not None and holder.id < best):
                best, best_stake = holder.id, stake
        return best

    def _owner_position(self, owner: str | None) -> float | None:
        """Position politique que le propriétaire imprime à la ligne : sa proximité s'il est un
        ménage, celle de son dirigeant s'il est une entreprise, l'orientation du gouvernement
        s'il est l'État. ``None`` : pas d'influence (fonds, entreprise sans dirigeant)."""
        if owner is None:
            return None
        lean = self.institutions.lean
        if owner in lean:
            return lean[owner]
        if owner == self.public.account.id:
            return self.government.orientation
        company = next((c for c in self.companies if c.id == owner), None)
        if company is not None and company.executive_id in lean:
            return lean[company.executive_id]
        return None

    def _media_lines(self) -> None:
        """Ligne éditoriale du mois : elle rejoint de ``LINE_SPEED`` la position de l'actionnaire
        de contrôle ; sous la loi « médias » (lot K), elle dérive en plus vers l'orientation du
        gouvernement (``CAPTURE`` × la même vitesse). La crédibilité revient vers sa référence."""
        state = self.media
        power = getattr(self, "power", None)
        captured = power is not None and "medias" in power.laws
        for company in self._active_outlets():
            outlet = state.outlets[company.id]
            outlet.owner = self._media_owner(company)
            target = self._owner_position(outlet.owner)
            if target is not None:
                outlet.line += LINE_SPEED * (target - outlet.line)
            if captured:
                outlet.line += LINE_SPEED * CAPTURE * (self.government.orientation - outlet.line)
            outlet.line = _clip(outlet.line, 0.0, 100.0)
            outlet.trust += TRUST_SPEED * (TRUST_BASE - outlet.trust)

    def _media_appeal(self, lean: float, company, outlet: Outlet) -> float:
        """Attrait d'un média pour un ménage : proximité de sa ligne × crédibilité × qualité
        (nombre de journalistes, rendements décroissants) ; divisé par quatre s'il ne paraît pas
        (aucun journaliste), sauf le mois de sa fondation."""
        staff = len(company.employees)
        closeness = 1.0 - abs(lean - outlet.line) / 100.0
        appeal = closeness * (0.5 + outlet.trust) * (1.0 + staff / (staff + STAFF_HALF))
        return appeal if staff or company.age_months == 0 else 0.25 * appeal

    def _media_readers(self) -> None:
        """Chaque ménage lit un média : il le choisit à son arrivée, quand le sien disparaît, et
        le reconsidère une fois par an (à tour de rôle, sans tirage)."""
        state = self.media
        lean = self.institutions.lean
        outlets = self._active_outlets()
        ids = {c.id for c in outlets}
        turn = self.month % REVIEW_MONTHS
        counts = dict.fromkeys(ids, 0)
        for index, person in enumerate(self.people):
            current = state.readers.get(person.id)
            if current not in ids or index % REVIEW_MONTHS == turn:
                if not outlets:
                    state.readers.pop(person.id, None)
                    continue
                position = lean[person.id]
                best = max(
                    outlets,
                    key=lambda c: (self._media_appeal(position, c, state.outlets[c.id]), c.id),
                )
                current = state.readers[person.id] = best.id
            counts[current] += 1
        for company in self._active_outlets():
            state.outlets[company.id].readers = counts.get(company.id, 0)

    # ------------------------------------------------------------------ ton, opinion, vote
    def _media_tone(self, outlet: Outlet) -> float:
        """Ton d'un média envers le gouvernement, de −1 (hostile) à 1 (favorable) : 1 quand sa
        ligne est celle du gouvernement, 0 à ``TONE_RANGE`` points d'écart, −1 au double."""
        return _clip(1.0 - abs(outlet.line - self.government.orientation) / TONE_RANGE, -1.0, 1.0)

    def _media_spin(self) -> float:
        """Déplacement du jugement de l'opinion sur la présidence : ``SPIN`` × la moyenne, sur
        les lecteurs, du ton de leur média pondéré par sa crédibilité."""
        state = getattr(self, "media", None)
        if state is None or not self.people:
            return 0.0
        total = 0.0
        for company in self._publishing_outlets():
            outlet = state.outlets[company.id]
            total += outlet.readers * self._media_tone(outlet) * outlet.trust
        return SPIN * total / len(self.people)

    def _media_vote_bias(self, person_id: str) -> float:
        """Écart entre le revenu réel que l'électeur perçoit et le sien, selon le ton de son
        média : ``media_vote_weight`` × ton × crédibilité. 0 sans média."""
        state = getattr(self, "media", None)
        if state is None:
            return 0.0
        outlet = state.outlets.get(state.readers.get(person_id))
        if outlet is None:
            return 0.0
        return max(0.0, self.live.media_vote_weight) * self._media_tone(outlet) * outlet.trust

    # ------------------------------------------------------------------ phase 3 : abonnements
    def _media_subscriptions(self, f) -> None:
        """Abonnements du mois, après les achats de services : chaque lecteur consacre
        ``media_subscription_share`` de son budget de services à son média, TVA comprise (la TVA
        va à l'État). Un média sans journaliste ne paraît pas et n'encaisse rien."""
        live = self.live
        state = self.media
        share = max(0.0, live.media_subscription_share)
        publishing = {c.id: c for c in self._publishing_outlets()}
        for outlet in state.outlets.values():
            outlet.subscriptions = 0.0
        if share <= 0.0 or not publishing:
            return
        tax_factor = 1.0 + live.vat_rate
        paid_total = vat = 0.0
        for person in self.people:
            company = publishing.get(state.readers.get(person.id))
            if company is None:
                continue
            budget = self._subscription_budget(person)
            if budget <= 0.01:
                continue
            net = budget / tax_factor
            tax = budget - net
            transfer_cash(person, company, net)
            person.cash -= tax
            person.expenses_this_turn += budget
            person.cumulative_expenses += budget
            company.revenue += net
            company.units_sold += 1.0
            state.outlets[company.id].subscriptions += net
            paid_total += net
            vat += tax
        self.public.cash += vat
        f.vat_revenue += vat
        f.household_services += paid_total
        self._media_count("abonnements", paid_total)

    # ------------------------------------------------------------------ phase 6 : publicité
    def _media_operations(self) -> None:
        """Publicité du mois, avant la clôture des comptes.

        Un producteur d'alimentation ou de biens, bénéficiaire le mois écoulé et doté de
        salariés, y consacre ``media_ad_share`` de son chiffre d'affaires du mois écoulé, sans
        entamer son fonds de roulement ; la dépense est répartie entre les médias qui paraissent
        selon leur audience. Son stock de publicité perd ``AD_DECAY`` par mois."""
        live = self.live
        state = self.media
        for company_id in list(state.ad_stock):
            state.ad_stock[company_id] *= 1.0 - AD_DECAY
            if state.ad_stock[company_id] < 1.0:
                del state.ad_stock[company_id]
        for outlet in state.outlets.values():
            outlet.advertising = 0.0
            for advertiser in list(outlet.advertisers):
                outlet.advertisers[advertiser] *= 1.0 - AD_DECAY
                if outlet.advertisers[advertiser] < 1.0:
                    del outlet.advertisers[advertiser]
        outlets = [c for c in self._publishing_outlets() if state.outlets[c.id].readers > 0]
        audience = sum(state.outlets[c.id].readers for c in outlets)
        share = max(0.0, live.media_ad_share)
        if not outlets or audience <= 0 or share <= 0.0:
            return
        total = 0.0
        for company in self.companies:
            if (
                not company.active or company.sector not in ADVERTISER_SECTORS
                or company.previous_profit <= 0.0 or not company.employees
            ):
                continue
            budget = min(
                share * max(0.0, company.previous_revenue),
                max(0.0, company.cash - working_capital(company, live)),
            )
            if budget <= 1.0:
                continue
            for medium in outlets:
                outlet = state.outlets[medium.id]
                part = budget * outlet.readers / audience
                transfer_cash(company, medium, part)
                medium.revenue += part
                outlet.advertising += part
                outlet.advertisers[company.id] = outlet.advertisers.get(company.id, 0.0) + part
            company.expenses += budget
            state.ad_stock[company.id] = state.ad_stock.get(company.id, 0.0) + budget
            total += budget
        self._media_count("publicite", total)

    def _media_affinity(self, sector: str, base):
        """Poids des producteurs de ``sector`` dans les achats de chaque ménage, publicité
        comprise : ``base`` (lot G, ou ``None``) × le facteur de chaque annonceur."""
        state = getattr(self, "media", None)
        if state is None or not state.ad_stock:
            return base
        wage = max(self.wage_reference, 1.0)
        reach = self.live.media_ad_reach
        factors = {
            c.id: ad_factor(state.ad_stock[c.id], wage, reach)
            for c in self.companies
            if c.active and c.sector == sector and c.id in state.ad_stock
        }
        if not factors:
            return base
        if base is None:
            return {person.id: factors for person in self.people}
        merged = {}
        for person in self.people:
            own = base.get(person.id) or {}
            row = dict(own)
            for company_id, factor in factors.items():
                row[company_id] = own.get(company_id, 1.0) * factor
            merged[person.id] = row
        return merged

    # ------------------------------------------------------------------ enquêtes (lot K)
    def _media_effort(self, company, affair: dict) -> float:
        """Probabilité mensuelle qu'un média révèle l'affaire : ``INVESTIGATION_CHANCE`` × la
        qualité de sa rédaction × son indépendance. Indépendance nulle si son propriétaire est le
        président ou le patron en cause, si l'État le contrôle sous ce président, ou si
        l'entreprise aidée lui apporte au moins ``ADVERTISER_CONFLICT`` de sa publicité ; divisée
        par deux s'il est favorable au président encore en place, et encore sous la loi
        « médias »."""
        outlet = self.media.outlets[company.id]
        staff = len(company.employees)
        if staff == 0:
            return 0.0
        president = affair["presidence"]
        in_office = self.institutions.holder("presidence") == president
        if outlet.owner in (president, affair["patron"]):
            return 0.0
        if in_office and outlet.owner == self.public.account.id:
            return 0.0
        receipts = sum(outlet.advertisers.values())
        if receipts > 0.0 and outlet.advertisers.get(affair["entreprise"], 0.0) >= ADVERTISER_CONFLICT * receipts:
            return 0.0
        independence = 1.0
        if in_office and self._media_tone(outlet) > 0.0:
            independence *= 0.5
        power = getattr(self, "power", None)
        if power is not None and "medias" in power.laws:
            independence *= CAPTURE
        return INVESTIGATION_CHANCE * staff / (staff + STAFF_HALF) * independence

    def _media_press_chance(self, affair: dict) -> float:
        """Probabilité mensuelle qu'au moins un média révèle l'affaire (remplace la probabilité
        fixe de la presse du lot K)."""
        missed = 1.0
        for company in self._publishing_outlets():
            missed *= 1.0 - self._media_effort(company, affair)
        return 1.0 - missed

    def _media_scoop(self, affair: dict, press: float, chance: float) -> None:
        """L'affaire vient d'être révélée : par la presse avec la probabilité ``press / chance``
        (sinon par l'audit). Le média qui la sort, tiré selon son effort d'enquête, y gagne
        ``SCOOP_TRUST`` de crédibilité."""
        state = self.media
        if chance <= 0.0 or state.random.random() >= press / chance:
            return
        efforts = [(c, self._media_effort(c, affair)) for c in self._publishing_outlets()]
        total = sum(effort for _, effort in efforts)
        if total <= 0.0:
            return
        draw = state.random.random() * total
        winner = efforts[-1][0]
        for company, effort in efforts:
            draw -= effort
            if draw < 0.0:
                winner = company
                break
        outlet = state.outlets[winner.id]
        outlet.trust = _clip(outlet.trust + SCOOP_TRUST)
        outlet.scoops += 1
        affair["revele_par"] = winner.name
        self._media_count("revelations")
        state.log.append({
            "mois": self.month, "type": "revelation",
            "texte": f"{winner.name} révèle l'aide de faveur à {affair['nom']}",
        })

    # ------------------------------------------------------------------ phase 8 : relevé
    def _media_record(self) -> None:
        """Relevé mensuel du lot M (mesures seules)."""
        state = self._media_state()
        stats = state.month_stats
        outlets = self._active_outlets()
        readers = [state.outlets[c.id].readers for c in outlets]
        audience = sum(readers)
        stats["mois"] = self.month
        stats["medias"] = len(outlets)
        stats["journalistes"] = sum(len(c.employees) for c in outlets)
        stats["lecteurs"] = audience
        stats["pluralisme"] = (
            1.0 - sum((count / audience) ** 2 for count in readers) if audience else 0.0
        )
        stats["ligne_moyenne"] = (
            sum(state.outlets[c.id].line * state.outlets[c.id].readers for c in outlets) / audience
            if audience else 0.0
        )
        stats["ton_moyen"] = self._media_spin() / SPIN
        stats.setdefault("abonnements", 0.0)
        stats.setdefault("publicite", 0.0)
        state.history.append(stats)
        state.month_stats = {}

    # ------------------------------------------------------------------ lectures pour l'interface
    def media_table(self) -> list[dict]:
        """Une ligne par média en activité : ligne, propriétaire, audience, recettes."""
        state = getattr(self, "media", None)
        if state is None:
            return []
        rows = []
        for company in self._active_outlets():
            outlet = state.outlets[company.id]
            rows.append({
                "id": company.id, "nom": company.name, "ligne": outlet.line,
                "ton": self._media_tone(outlet), "credibilite": outlet.trust,
                "proprietaire": outlet.owner, "fondateur": outlet.founder,
                "lecteurs": outlet.readers, "journalistes": len(company.employees),
                "abonnements": outlet.subscriptions, "publicite": outlet.advertising,
                "revelations": outlet.scoops, "tresorerie": company.cash,
            })
        return rows

    def media_history(self) -> list[dict]:
        """Relevés mensuels du lot M depuis la période 0 (``periode`` : mois observé)."""
        state = getattr(self, "media", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset} for row in state.history if row["mois"] - offset >= 0]

    def media_events(self) -> list[dict]:
        """Événements du lot M depuis la période 0, du plus récent au plus ancien."""
        state = getattr(self, "media", None)
        if state is None:
            return []
        offset = self.warmup_offset
        return [{**row, "periode": row["mois"] - offset}
                for row in reversed(state.log) if row["mois"] - offset >= 0]
