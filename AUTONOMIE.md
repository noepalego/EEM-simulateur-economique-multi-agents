# Autonomie : configuration unique et registre des cibles

Depuis le 5 novembre 2026, `SimulationConfig()` est la configuration auparavant appelée
« Autonome stable » avec l'État institutionnel. Les sélecteurs de modes et les
préréglages ont été retirés. Les valeurs numériques restent réglables.

Les entreprises fixent leurs prix et leurs offres salariales sur leurs signaux locaux,
prévoient leur demande, évaluent les investissements sur les recettes supplémentaires
et les salaires économisés. Les banques évaluent le risque et constituent un coussin
sur les pertes du cycle ; leur résolution publique est plafonnée. Les ménages budgètent
sur leur revenu attendu. Les fournisseurs sans salariés ne sont pas payés.

L'État vote une loi de finances, emploie des agents par service, fixe un minimum légal
et mène une politique industrielle. Sa dette ne suit aucune cible de ratio.
Les prix de marché n'ont plus de plafond technique et le dernier producteur peut fermer.
Les variantes expérimentales hors configuration retenue ont aussi été supprimées :
notamment comptes sur mois complet, machines limitées par la main-d'œuvre et
candidatures solvables au logement. Leur intérêt économique reste discuté dans les
rapports historiques ; leur présence passée ne signifie pas qu'elles sont encore disponibles.

Le fonctionnement du modèle, de l'État et ses limites sont décrits dans le [README](README.md).

## Registre des cibles

`TargetRegistryTests` lit ce tableau à cet emplacement. Chaque champ contenant
un mot `target`, `ceiling`, `cap`, `boost`, `floor` ou `probability` doit être justifié.
Les descriptions expliquent les règles conservées, sans annoncer comme futurs
des modes déjà codés. Les valeurs exactes sont celles de `SimulationConfig`.

| Paramètre | Nature | Justification actuelle |
|---|---|---|
| `sector_margin_target` | cible sectorielle | Référence de marge encore lue dans les diagnostics et décisions conservés ; les prix sont formés par les signaux propres aux entreprises. |
| `profit_margin_target` | cible de marge | Seuil encore lu par les décisions conservées de rentabilité ; les investissements de capacité sont évalués sur leurs flux supplémentaires. |
| `target_inventory_months` | stock de précaution | Stock désiré en mois de demande ; intervient dans les décisions de production, avec anticipations propres aux entreprises. |
| `payroll_coverage_target` | seuil de couverture | Compare les recettes de l'entreprise à sa masse salariale pour les ajustements d'effectifs ; décision de gestion fondée sur ses comptes. |
| `target_employment_rate` | emploi initial | Sert au dimensionnement d'effectifs et à des décisions d'entrée ; ne garantit pas le taux d'emploi observé après les échanges et les ajustements. |
| `policy_rate_cap` | limite institutionnelle | Plafond du taux directeur dans la règle de banque centrale ; il borne l'instrument de politique, sans garantir l'inflation obtenue. |
| `debt_ceiling_gdp` | limite budgétaire | Contrainte d'endettement public en proportion du PIB ; distincte de la cible de dette et des décisions de dépenses. |
| `bank_bailout_cap_gdp` | limite de résolution | Plafonne le financement public sous la politique `capped_public`. |
| `company_founder_probability` | fondation probabiliste | Représente la fréquence de tentative des ménages éligibles ; le secteur et le financement dépendent ensuite des signaux et contraintes du mode choisi. |
| `home_investment_probability` | achat probabiliste | Fréquence de tentative d'achat patrimonial d'un ménage éligible ; ne dispense pas des contraintes de trésorerie, de financement et de logement disponible. |
| `retraining_probability` | reconversion probabiliste | Hétérogénéité des décisions de reconversion professionnelle ; coûts, délais et besoins de métiers restent des contraintes du marché du travail. |
| `minimum_wage_boost_cap` | coup de pouce borné | Nouvel État : hausse discrétionnaire annuelle du salaire minimum au-delà de l'indexation légale, au plus 10 %, sans dilution sur le mandat restant depuis novembre 2026 (montée progressive vers l'ambition de la doctrine), et baisse décidée par un gouvernement non social bornée de même ; vitesse d'une décision politique (révision sur observations retardées), pas un niveau visé du salaire minimum. |
| `social_pay_ratio_cap` | plafond politique | État institutionnel : un gouvernement social plafonne le salaire de la présidence à 8 fois le salaire minimum ; choix de doctrine consigné au journal, appliqué aux révisions. |
| `countercyclical_floor` | plancher prudentiel | Lot F1, actif seulement avec `countercyclical_capital` : exigence de fonds propres minimale (6 %) pendant un allègement contracyclique ; borne un instrument de politique prudentielle, sans viser de volume de crédit ni de croissance. |

### Cibles institutionnelles explicitement reconnues

| Paramètre | Agent | Justification |
|---|---|---|
| `inflation_target` | Banque centrale | Objectif de sa règle de taux ; sert aussi à l'ancrage de certaines anticipations agrégées. |

## Bornes et hypothèses ajoutées en novembre 2026

Mission « améliorations ». Ces bornes ne contiennent
aucun mot du registre ci-dessus ; `ImprovementRegistryTests` vérifie que chacune est listée ici
et existe dans `SimulationConfig` (ou, pour une constante de doctrine, dans son module). Aucune ne
vise un résultat (Gini, part du patrimoine, nombre de logements) ; aucun tirage au sort n'a été
ajouté.

| Borne ou hypothèse | Où | Justification |
|---|---|---|
| `founder_max_stake_share` | `SimulationConfig` (0,25) | Part de sa fortune nette au-delà de laquelle un fondateur prudent diversifie : décision d'agent, vendue sur le marché au cours, à la vitesse de rééquilibrage des ménages. |
| `founder_stake_risk_weight` | `SimulationConfig` (0,5) | La limite de concentration croît avec le goût du risque du fondateur (0,25 à 0,75 de sa fortune) : hétérogénéité des comportements, pas une part visée. |
| `ipo_window_months` | `SimulationConfig` (12) | Durée de l'introduction en Bourse d'une entreprise fondée par un ménage : ses titres offerts servent la demande avant les vendeurs ; ne crée aucun acheteur. |
| `minimum_wage_ambition` | `economy/institutions.py` | Courbe de doctrine : 50 % du médian à droite, 60 % à 60/100, 68 % à 80/100, 80 % à 100/100 ; un gouvernement social gèle l'indexation (sans baisse) au-dessus de son ambition. Le niveau atteint dépend des salaires observés et des révisions. |
| `capital_flat_factor` | `economy/institutions.py` (`Doctrine`) | Sous 50/100, dividendes et loyers imposés au forfait (taux de base × (1 + position « tranche supérieure »)) ; sinon au barème. Dérivé des positions déjà tirées. |
| `transfer_targeting` | `economy/institutions.py` (`Doctrine`) | Part des transferts versée sous condition de ressources = position « transferts » bornée à [0, 1] ; dérivée des positions déjà tirées. |
| `income_tax_threshold` | `SimulationConfig`, indexé dans `state_budget.py` | Seuil de la tranche supérieure en salaires de départ, indexé sur le niveau nominal des salaires ; au-delà, taux de base + surtaxe `top_marginal_income_tax_rate`. |
| `land_expansion_rate` | `SimulationConfig` (0,4 %/mois) | Valeur inchangée ; assiette changée : tout le parc de la ville (et non le seul bilan des promoteurs). |

## Hypothèses de la mission « cinq idées » (6 novembre 2026)

Mission « cinq idées ». `FiveIdeasRegistryTests` vérifie que
chaque hypothèse est listée ici et existe dans `SimulationConfig`. Aucune ne vise un résultat
(chômage, Gini, nombre d'entreprises, dette) : ce sont des vitesses, des délais ou des préférences
d'agents, non calibrés sur des données. Seuls ajouts de tirages : un générateur démographique propre
(âges, décès, traits des héritiers) et, à chaque élection, deux tirages du générateur politique
existant (écart national, écart du nouveau gouvernement), comme l'ancien vote.

| Hypothèse | Valeur | Justification |
|---|---|---|
| `wage_slack_speed` | 0,03 | Baisse mensuelle de l'offre d'une entreprise sans poste vacant, par unité de part des ménages au chômage depuis au moins un an : candidats abondants. Même ordre que `wage_vacancy_speed` ; nulle en régime normal (chômage frictionnel court, mesuré à 0 à 0,6 %). |
| `wage_concession_speed` | 0,05 | Baisse mensuelle de l'offre par unité d'écart relatif de couverture de la paie par la recette lissée : l'entreprise qui licencie faute de recettes modère aussi ses salaires, et ne compte plus comme vacants les postes qu'elle vient de supprimer. |
| `long_term_unemployment_months` | 12 | Durée à partir de laquelle un chômage compte comme de longue durée (définition statistique usuelle) ; statistique publique lue par toutes les entreprises. |
| `shell_dissolution_months` | 24 | Mois sans salarié après lesquels les actionnaires d'une entreprise sans dette ni actif physique, dont un poste ne rapporterait pas son coût, la liquident à l'amiable (trésorerie versée aux actionnaires). Jamais la dernière de son secteur. |
| `mortality_level` | 0,00005 | Niveau de la loi de Gompertz (taux annuel extrapolé à la naissance) : environ 0,2 % de décès par an à 40 ans, 1,5 % à 60 ans, 10 % à 80 ans ; espérance de vie d'environ 74 ans. Ordre de grandeur des tables françaises, non calibré. 0 neutralise la démographie. |
| `mortality_growth` | 0,095 | Pente de la loi de Gompertz (hausse annuelle du taux de mortalité de 10 % environ), ordre de grandeur des populations humaines. |
| `adult_age_years` | 20 | Âge d'entrée dans la vie active : un nouveau ménage ou un héritier a entre 20 et 30 ans et repart sans carrière. Un héritier à « âge du défunt − 30 ans » a été essayé puis retiré : chefs de ménage de 48 ans sans aucune expérience, âge moyen dérivant vers 60 ans. |
| `estate_tax_rate` | 0,20 | Taux de base des droits de succession sous un gouvernement du centre, multiplié par (1 + position « tranche supérieure ») : nul à l'extrême droite, 40 % à l'extrême gauche. Décision de doctrine, au-delà de l'abattement. |
| `estate_tax_allowance_years` | 10 | Abattement en années de salaire de référence (indexé) : les petites successions ne paient rien. |
| `education_skill_gain` | 0,15 | Compétence de départ ajoutée à un héritier ou un nouveau ménage par une éducation publique pleinement couverte (compétences tirées entre 0,55 et 1,05) : petit effet de niveau, une fois par génération, sans croissance composée. |
| `pupils_per_agent` | 30 | Ménages dont la génération suivante est scolarisée par un agent du service de l'Éducation (ordre de grandeur d'une classe) ; le besoin croît avec la population. |
| `education_memory_months` | 180 | Lissage de la couverture du service : la qualité reçue résulte de toute une scolarité (environ quinze ans), pas du mois courant. |
| `vote_interest_weight` | 0,30 | Glissement de la position d'un électeur selon son rang de fortune (± 15 points) : intérêts anticipés (fiscalité du capital, transferts), sans déterminisme par la richesse. |
| `vote_ideology_sensitivity` | 8,0 | Poids, dans le logit du vote, de l'écart de proximité entre le sortant et l'opposition : la conviction reste le premier déterminant du vote. |
| `vote_income_weight` | 4,0 | Poids de l'évolution du revenu réel attendu du ménage pendant le mandat (bornée à ± 50 %) : vote sur son propre bilan. |
| `vote_unemployment_weight` | 3,0 | Poids de la fréquence lissée de ses propres mois sans emploi : un chômeur sanctionne davantage le sortant. |
| `vote_swing_sd` | 0,03 | Écart-type de l'écart national de la part des voix (un tirage par élection) : aléa de campagne, qui remplace l'ancien tirage de réélection. |

## Réglages du programme de modifications (lots B à F, novembre 2026)

Chaque mécanisme ajouté par le programme `results/prompt_modifications.md` a son réglage dans
`SimulationConfig`, **désactivé par défaut** et marqué neutre pour l'empreinte d'identité
(`metadata={"identity": "neutre"}`) : à sa valeur par défaut, aucun calcul ni tirage n'est
modifié. `RateRegistryTests` (`tests/test_taux.py`) vérifie que la liste ci-dessous correspond aux
réglages ajoutés. Les forçages du mode intervention (taux directeur, marge, écart des dépôts) ne
sont pas des paramètres : ils vivent dans `simulation.interventions` et au journal.

| Réglage | Défaut (lot) | Justification |
|---|---|---|
| `bank_margin_offset` | 0 (lot B) | Marge supplémentaire mensuelle des banques commerciales, ajoutée à tout nouveau crédit aux entreprises (après la marge de risque), à l'écart hypothécaire et à celui du crédit à la consommation, ainsi qu'au coût du crédit que les entreprises anticipent ; marges bornées à zéro. Instrument d'expérience (pouvoir de marché des banques, durcissement du crédit), ne vise aucun résultat. |
| `enriched_objectives` | faux (lot C) | Objectifs ministériels enrichis : un objectif principal et un secondaire par ministre, choisis selon la doctrine parmi des indicateurs du relevé mensuel ; cible relevée d'un pas quand la situation de départ la remplit déjà ; un objectif en retard actionne un levier de son ministère. Aucun indicateur n'est imposé à l'économie : seuls les instruments publics bougent. |
| `objective_effort` | 0,25 (lot C) | Intensité du levier actionné par un objectif principal en retard : solde voulu relevé de 0,25 point de PIB, couverture d'un service relevée de 25 %, coup de pouce du salaire minimum plafonné 25 % plus haut, instruction ou cessions 25 % plus rapides. Ordre de grandeur d'une priorité budgétaire, non calibré. |
| `secondary_objective_weight` | 0,5 (lot C) | Poids d'un objectif secondaire relativement au principal, pour l'effort sur son levier, le partage de l'enveloppe du ministère et le bilan compté au vote : un objectif secondaire compte, mais moins que le principal. |
| `volume_discount_max` | 0 (lot D) | Remise maximale sur le volume d'une commande entre entreprises (0,15 dans les mesures) : courbe dégressive bornée, jamais au-delà de la marge observée des fournisseurs, donc jamais sous leur coût. Pouvoir de négociation des gros acheteurs, non calibré. |
| `volume_discount_half_volume` | 5 000 (lot D) | Commande (au prix de base, déflatée par l'indice des intrants) qui obtient la moitié de la remise maximale ; choisie d'après la distribution observée des commandes (médiane ≈ 300, 90e centile ≈ 3 000, 99e ≈ 50 000), pour que seules les grosses commandes soient nettement remisées. |
| `fixed_cost_share` | 0 (lot D) | Part des intrants non matériels des producteurs d'alimentation et de biens payée en frais de structure mensuels, calés sur les ventes moyennes du secteur (0,3 dans les mesures) : coût total inchangé à la taille moyenne, coût unitaire décroissant au-delà. Une entreprise à l'arrêt ne paie rien. |
| `ownership_regime` | faux (lot E) | Régime de propriété des entreprises selon l'orientation (`economy/ownership.py`) : laisser-faire près de 0 (pas d'intervention contre les monopoles, seuil de contrôle des OPA abaissé, dividendes accrus), coopératives près de 100 (parts salariées minimales, OPA plus difficiles, dividendes et salaires de direction votés à la baisse), interpolation continue, nul au centre. Aucun transfert direct de richesse : les inégalités et la concentration doivent émerger. |
| `ownership_transition_months` | 48 (lot E) | Durée d'un passage complet du régime d'un extrême à l'autre : un changement de gouvernement s'applique progressivement (environ un mandat), sans rupture des règles de propriété. |
| `cooperative_share_max` | 0,30 (lot E) | Part du capital de chaque entreprise non bancaire détenue par ses salariés à 100/100 (proportionnelle à l'intensité coopérative) ; minorité de blocage partielle, sans prise de contrôle par les salariés. Ordre de grandeur des entreprises à actionnariat salarié fort, non calibré. |
| `cooperative_purchase_rate` | 0,01 (lot E) | Part du capital qu'une entreprise peut acquérir par mois pour ses salariés (titres auto-détenus, puis rachat au cours sur sa trésorerie libre) : convergence lente, sans expropriation ni ponction brutale de trésorerie. |
| `countercyclical_capital` | faux (lot F1) | Exigence de fonds propres contracyclique : le diagnostic (`results/lot_f_stabilite/DIAGNOSTIC.md`) montre que le crédit gèle sur la contrainte de fonds propres, jamais sur les réserves ; l'exigence effective baisse progressivement en cas de capacité épuisée et d'activité en baisse, puis remonte. Instrument de politique prudentielle, ne vise aucun résultat. |
| `countercyclical_floor` | 0,06 (lot F1) | Exigence minimale pendant l'allègement (8 % → 6 % au plus) : ordre de grandeur des coussins contracycliques relâchés en crise, l'exigence de base restant contraignante. |
| `countercyclical_step` | 0,0025 (lot F1) | Allègement ou rétablissement mensuel (0,25 point) : huit mois pour atteindre le plancher, sans saut de l'exigence d'un mois à l'autre. |
| `countercyclical_trigger` | 0,02 (lot F1) | Seuil de capacité de crédit du système (2 % de l'encours) sous lequel l'allègement commence, s'il y a baisse d'activité ; rétablissement au-delà de 2,5 fois ce seuil (hystérésis). Choisi d'après le diagnostic : capacité sous 2 % pendant 150 à 340 mois sur 800. |
| `bank_capital_backstop` | faux (lot F1) | Recapitalisation de dernier recours : après un gel durable du crédit (capacité nulle), l'État souscrit des actions nouvelles des banques au cours, comme une recapitalisation de la politique industrielle (titres à l'État, journal, financement par trésorerie ou emprunt) ; aucune monnaie créée. |
| `bank_backstop_months` | 3 (lot F1) | Mois consécutifs de capacité nulle avant l'intervention : un trimestre, pour laisser d'abord jouer l'émission d'actions de marché et le bénéfice des banques. |
| `bank_backstop_capacity` | 0,05 (lot F1) | Capacité de crédit rendue par la recapitalisation (5 % de l'encours de chaque banque) : le minimum qui rouvre le crédit, pas un renflouement complet. |
| `stock_earnings_smoothing_months` | 0 (lot F2) | Mois de bénéfice moyennés dans la valeur fondamentale des actions (12 dans les mesures) au lieu du bénéfice du mois extrapolé, qui varie d'environ 100 % de son niveau d'un mois à l'autre ; 0 : ancienne formule. |

## Refonte de la comptabilité bancaire (novembre 2026)

La trésorerie propre d'une banque (ce qu'elle peut payer : salaires, charges, intérêts des dépôts,
dividendes) n'entre pas dans l'identité du bilan bancaire de `ledger.py` : ce n'est pas de la
monnaie. Par défaut c'est un compteur indépendant, que les pertes sur prêts ne réduisent pas.
Réglage désactivé par défaut et neutre pour l'empreinte d'identité ; `BankOwnFundsTests`
(`tests/test_refonte_banque.py`) vérifie que la liste ci-dessous correspond aux réglages ajoutés.

| Réglage | Défaut | Justification |
|---|---|---|
| `bank_own_funds` | `cash` | « equity » : en début de mois, la trésorerie propre est la part liquide des fonds propres (fonds propres − actifs réels − prêts interbancaires accordés + emprunts interbancaires, jamais négative), et l'excédent distribué est le capital au-delà de l'exigence et de son coussin (`capital_requirement` + `bank_recap_buffer`). Correction comptable : mesuré sur 800 mois, le compteur par défaut atteint 5 à 10 fois les fonds propres nets et 30 à 50 % des dividendes bancaires sont versés au-delà du bénéfice. Ne vise aucun résultat. |

## Réglages du lot G : entreprises (novembre 2026)

Le lot H (salariés obligatoires, conseil d'actionnaires) est inscrit dans le même tableau.

Huit mécanismes d'entreprise (`economy/enterprise.py`), chacun derrière son réglage, **désactivé par
défaut** et neutre pour l'empreinte d'identité : sans réglage activé, aucun calcul, aucun tirage et
aucun attribut de simulation n'est ajouté. `EnterpriseRegistryTests` (`tests/test_entreprises.py`)
vérifie que la liste ci-dessous correspond aux réglages ajoutés. Aucun ne vise un résultat (nombre
d'entreprises, concentration, patrimoine) : ce sont des règles de décision d'agents, des coûts et des
vitesses, non calibrés sur des données. Seul ajout de tirages : un générateur propre aux goûts des
ménages et aux styles des producteurs (G3), créé à l'activation. Constantes du module, non réglables :
ancienneté de 24 mois avant de bâtir un groupe, cible d'au plus la moitié de la capitalisation de
l'acquéreur, lien de filiale gardé jusqu'aux deux tiers du seuil de contrôle, loyer d'un logement
dégradé d'au moins 30 % du loyer du marché, un quart de l'écart de productivité transmis par une
société mère à une cible rachetée (ce que l'acquéreur apporte, et la raison de son offre).

Constantes du lot I (`economy/enterprise.py`, non réglables, actives seulement avec le mécanisme
concerné) : deux vendeurs par magasin ; hausse des quantités voulues par les ménages d'au plus 5 %
par la commodité d'achat (un magasin pour 50 ménages en donne la moitié) ; dérive mensuelle du
goût d'un ménage d'écart-type 0,01 et déplacement du style d'un producteur informé d'au plus 0,02
par mois ; un quart de la trésorerie excédentaire d'un fonds placé en actions cotées par mois,
demande de cotation quand sa participation vaut deux fois son coût ; soutien d'une filiale d'au
plus un quart de la trésorerie libre de sa société mère par mois, mise en vente après douze mois
de pertes, reprise d'une filiale sans salarié une fois tous les 24 mois ; gain de productivité
d'un anniversaire de mandat d'au plus 0,5 % ; exigence du conseil d'actionnaires de ± 20 % autour
du coût des fonds propres. Aucune ne vise un résultat.

| Réglage | Défaut | Justification |
|---|---|---|
| `experience_learning` | faux (G1) | Apprentissage par la pratique : un fondateur qui a déjà créé des entreprises fonde mieux, un salarié qui a souvent changé d'employeur et un employeur qui a beaucoup recruté négocient mieux le salaire d'embauche, un acquéreur expérimenté intègre plus vite et garde les meilleures pratiques. L'expérience disparaît à la succession du ménage. |
| `experience_gain` | 0,10 (G1) | Effet maximal de l'expérience : productivité et innovation de départ d'une entreprise relevées d'au plus 10 %, salaire d'embauche négocié dans une bande de ± 5 % autour de l'offre (jamais sous le salaire minimum). Ordre de grandeur d'une prime d'ancienneté, non calibré. |
| `experience_half_count` | 3 (G1) | Nombre d'expériences qui donne la moitié de l'effet maximal (compétence = n / (n + 3)) : rendements décroissants, aucune croissance sans fin. Un employeur compte ses embauches par dizaines, car il recrute bien plus souvent qu'un salarié ne change de poste. |
| `property_upkeep` | faux (G2) | Gros entretien des logements, en plus de l'entretien courant déjà payé (`housing_maintenance_rate`) : l'état du parc de chaque propriétaire s'use, il paie les travaux qui le rétablissent s'il en a les moyens, et un parc dégradé se loue moins cher (loyer × état, au moins 30 %). Dépense réelle versée aux services et aux matières. |
| `property_wear_rate` | 0,002 (G2) | Usure mensuelle de l'état d'un logement (environ 2,4 % par an) : sans travaux, un logement perd la moitié de son état en une trentaine d'années. Ordre de grandeur d'un amortissement immobilier. |
| `property_repair_cost_share` | 0,5 (G2) | Coût des travaux qui remettent à neuf un logement entièrement dégradé, en part du coût de construction : au rythme d'usure retenu, environ 70 € de départ par logement et par mois, la moitié de l'entretien courant. |
| `consumer_preferences` | faux (G3) | Chaque ménage a un goût et chaque producteur d'alimentation ou de biens un style ; l'affinité déplace les achats entre producteurs sans changer la demande totale. Les entreprises technologiques vendent les données des ménages équipés, qui rapprochent l'offre de l'acheteur du goût de ses clients. |
| `preference_strength` | 0,5 (G3) | Hausse maximale du poids d'un producteur dans les achats d'un ménage dont il rejoint exactement le goût (poids × 1,5), du même ordre que la préférence pour la qualité déjà présente. |
| `data_budget_share` | 0,01 (G3) | Part du chiffre d'affaires du mois précédent qu'un producteur bénéficiaire consacre à l'achat de données, dans la limite de sa trésorerie libre : ordre de grandeur d'un budget d'études de marché, payé aux entreprises technologiques qui ont des salariés. |
| `data_decay_rate` | 0,05 (G3) | Oubli mensuel de la connaissance des clients (les goûts connus se périment en un an et demi environ) : la connaissance doit être rachetée, ce qui fait des données une recette durable de la technologie. |
| `diversification` | faux (G4) | Une entreprise indépendante, rentable et installée crée une filiale dans le secteur le plus attractif autre que le sien, d'après les signaux d'entrée que voient déjà les ménages fondateurs ; au plus une par mois dans l'économie. Le groupe vend alors plusieurs produits par ses filiales, qui peuvent échouer. |
| `conglomerate_takeovers` | faux (G5) | Rachats entre secteurs aux règles des offres publiques existantes (prime, seuil de contrôle, acceptation par les actionnaires) : la cible, plus petite, devient une filiale sans fusion. L'État n'apporte pas ses titres, les entreprises publiques ne sont ni acquéreuses ni cibles. |
| `retail_stores` | faux (G6) | Magasins des producteurs d'alimentation et de biens : un réseau relève le poids de l'entreprise dans les achats des ménages. Les magasins déplacent des ventes entre producteurs, ils ne créent pas de demande. Ouverture décidée sur le rendement attendu, comparé au coût des fonds propres. |
| `store_opening_cost` | 40 000 (G6) | Coût d'ouverture d'un magasin en euros de départ, indexé sur le niveau des salaires, payé à l'immobilier, aux services et aux matières et capitalisé (amorti comme la capacité de production) : de l'ordre de seize unités de capacité. |
| `store_running_cost` | 600 (G6) | Charges mensuelles d'un magasin en euros de départ (loyer et services), indexées sur les salaires ; la part que la trésorerie ne paie pas ferme. Proche du loyer d'un logement au départ. |
| `store_reach` | 0,5 (G6) | Hausse maximale du poids d'un producteur dans les achats des ménages par son réseau (poids × 1,5 pour un réseau très étendu) : la portée d'un réseau est bornée, pour limiter la boucle « plus gros, plus de magasins, plus gros ». |
| `store_half_count` | 3 (G6) | Nombre de magasins qui apporte la moitié de la portée maximale : rendements décroissants, chaque magasin de plus rapporte moins que le précédent et finit par ne plus couvrir ses charges. |
| `group_distribution` | faux (G7) | Les magasins d'un groupe vendent les produits de toutes ses entreprises, et un acquéreur compte les ventes ainsi gagnées dans la valeur d'une cible : une grande entreprise bien distribuée a intérêt à racheter un petit producteur. Sans effet sans magasins ni groupes. |
| `private_startups` | faux (G8) | Une entreprise fondée par un ménage n'est plus cotée dès sa création (sauf une banque). Chaque banque gère un fonds où les ménages placent une part de leur épargne ; le fonds finance les non cotées et les accompagne. L'introduction en Bourse n'arrive que pour un gros besoin de trésorerie ou à la sortie du fonds. |
| `fund_savings_share` | 0,05 (G8) | Part de son épargne investissable qu'un ménage de goût du risque moyen vise dans les fonds (2,5 à 7,5 % selon ce goût), comblée à la vitesse de rééquilibrage des portefeuilles ; une souscription est bornée par ce que le fonds peut employer. |
| `fund_fee_rate` | 0,0015 (G8) | Frais de gestion mensuels prélevés sur l'actif du fonds (environ 1,8 % par an), recette de la banque gérante comme un intérêt encaissé : ordre de grandeur des frais d'un fonds de capital-investissement. |
| `fund_ticket_share` | 0,25 (G8) | Part maximale de l'actif d'un fonds apportée à une entreprise en une opération : le fonds reste diversifié et ne peut pas tout perdre sur une seule entreprise. |
| `fund_support_gain` | 0,05 (G8) | Hausse de la productivité d'une entreprise au premier apport d'un fonds (appui de gestion, une seule fois, productivité bornée comme partout à 1,8) : petit effet de niveau, sans croissance composée. |
| `fund_exit_months` | 60 (G8) | Durée après laquelle le fonds entré au capital demande l'introduction en Bourse pour sortir (cinq ans, horizon usuel du capital-investissement) ; il vend ensuite ses titres à la séance, par dixièmes. |
| `staffless_closure_months` | 0 (lot H) | Salariés obligatoires : une entreprise non bancaire restée ce nombre de mois sans aucun salarié est fermée (liquidation amiable sans dette ni actif, sinon liquidation avec vente des actifs et remboursement des banques). 0 : jamais. Jamais la dernière de son secteur ni une participation de l'État. Règle de droit des sociétés, ne vise aucun nombre d'entreprises. |
| `shareholder_governance` | faux (lot H) | Conseil d'actionnaires : les plus gros actionnaires nomment le dirigeant parmi les salariés (un actionnaire salarié vote pour lui-même), le révoquent après un an si l'entreprise perd de l'argent plus longtemps que leur patience, et orientent la part des bénéfices réinvestie selon leur goût du risque. Vote pondéré par les titres, aucun tirage. |
| `board_seats` | 4 (lot H) | Nombre d'actionnaires qui siègent au conseil : les quatre plus gros porteurs (ménages, entreprises, État, fonds), dont la société mère d'une filiale ; au-delà, les petits porteurs ne pèsent pas sur la direction. |
| `board_patience_months` | 6 (lot H) | Mois de pertes consécutives au-delà desquels un membre du conseil vote la révocation du dirigeant (un ménage : 3 à 9 mois selon son goût du risque), jamais avant douze mois de mandat. Même durée que le délai de cessation de paiements (`bankruptcy_months`). |
| `ipo_need_share` | 0,5 (G8) | Besoin de trésorerie restant après l'apport du fonds, en part des fonds propres, au-delà duquel une entreprise non cotée demande son introduction en Bourse : seul un besoin que ni sa trésorerie ni le fonds ne couvrent l'y pousse. |

## Réglages du lot J : obligations, commerce extérieur, syndicats (novembre 2026)

Trois mécanismes (`economy/macro.py`), chacun derrière son réglage, **désactivé par défaut** et
neutre pour l'empreinte d'identité. `MacroRegistryTests` (`tests/test_macro.py`) vérifie que la liste
ci-dessous correspond aux réglages ajoutés. Aucun ne vise un résultat (solde extérieur, niveau des
salaires, encours de dette) ; aucun tirage. Constantes du module, non réglables : émetteur
d'obligations d'au moins 24 mois, émission minimale de 10 000 € de départ, un dixième de la
trésorerie excédentaire d'un fonds placé en obligations par mois ; avoirs du reste du monde d'au plus
2 % du PIB annuel, dont il redépense la moitié chaque mois ; accord salarial de douze mois, grève
au-delà de 2,5 points d'écart entre la demande et l'offre (la moitié du travail du mois perdue, la
moitié de l'écart concédée, une grève par secteur tous les trois ans au plus), syndicat sans pouvoir à 20 % de chômage, moitié de la marge au-delà de
la référence du secteur réclamée en salaires.

| Réglage | Défaut | Justification |
|---|---|---|
| `corporate_bonds` | faux | Obligations d'entreprise : une entreprise non bancaire rentable emprunte aux ménages et aux fonds des banques. Un achat est un transfert de dépôt, sans création de monnaie ; coupon et amortissement mensuels ; en liquidation, les obligataires passent après les banques et avant les actionnaires. Financement de marché à côté du crédit bancaire. |
| `corporate_bond_share` | 0,05 | Part de son épargne investissable qu'un ménage de goût du risque moyen vise en obligations d'entreprise (2,5 à 7,5 %, les prudents davantage), comblée à la vitesse de rééquilibrage des portefeuilles et seulement si le taux dépasse celui des dépôts. |
| `corporate_bond_term_months` | 60 | Durée d'amortissement d'une obligation : un soixantième de l'encours est remboursé chaque mois (cinq ans, durée usuelle d'une obligation d'entreprise), en plus du coupon. |
| `corporate_bond_leverage` | 0,5 | Encours obligataire maximal d'un émetteur, en part de ses fonds propres : les épargnants ne prêtent pas au-delà de la moitié de ce que les actionnaires ont engagé, ce qui borne la perte en cas de défaut. |
| `foreign_trade` | faux | Commerce extérieur : un reste du monde vend aux ménages l'alimentation et les biens que le marché intérieur ne sert pas, et redépense la monnaie gagnée en produits et en services intérieurs. Aucune monnaie n'entre ni ne sort ; taux de change flottant. Les exportations entrent dans le PIB. |
| `trade_openness` | 0,5 | Part de la demande non servie d'un marché que les importations peuvent couvrir chaque mois : une économie à moitié ouverte, où une pénurie intérieure n'est jamais entièrement comblée par l'étranger. |
| `trade_price_elasticity` | 2,0 | Élasticité des volumes échangés à l'écart entre prix intérieur et prix étranger converti : un produit importé 10 % plus cher se vend environ 20 % de moins. Ordre de grandeur des élasticités du commerce, non calibré. |
| `exchange_rate_speed` | 0,05 | Variation mensuelle maximale du taux de change (5 %), proportionnelle au déséquilibre relatif des échanges du mois : un déficit déprécie la monnaie, renchérit les importations et aide les exportations. |
| `collective_bargaining` | faux | Négociation collective : une fois par an et par secteur, un syndicat demande le rattrapage de l'inflation et une part des marges ; l'accord relève les offres salariales du secteur et les empêche de baisser pendant douze mois ; une grève suit un écart trop grand. Les salaires gardent leur plancher légal et leurs autres règles. |
| `union_power` | 0,5 | Pouvoir de négociation d'un syndicat sous un gouvernement du centre et sans chômage : part de sa demande qu'il obtient. Multiplié par 0,5 à l'extrême droite et 1,5 à l'extrême gauche, et réduit par le chômage (nul à 20 %). |
| `union_max_raise` | 0,05 | Hausse annuelle maximale qu'un syndicat demande (5 %) : borne la demande dans une année d'inflation forte, sans indexation automatique des salaires sur les prix. |

## Réglages du lot K : influence, popularité, pouvoir et favoritisme (novembre 2026)

Neuf mécanismes (`economy/power.py`), chacun derrière son réglage, **désactivé par défaut** et
neutre pour l'empreinte d'identité. `PowerRegistryTests` (`tests/test_power.py`) vérifie que la liste
ci-dessous correspond aux réglages ajoutés. Aucun ne vise un résultat (taux de réélection, nombre
d'affaires, niveau de corruption) : ce sont des règles de décision de personnes et des probabilités
d'événements, non calibrées sur des données. Tirages : un générateur propre au lot (traits des
ménages, passage à l'acte d'un président, révélation d'une affaire), créé à l'activation ; aucun
autre générateur n'est déplacé. L'état vit dans `simulation.power`, créé au premier usage.

Constantes du module, non réglables. Influence : 30 points de fortune (atteints à dix fois la
fortune moyenne), 35 de fonction (présidence 35, ministre 22, dirigeant 6 à 28 selon le poids de
son entreprise, 3 pour un siège au conseil d'actionnaires), 20 de capital contrôlé (participations
d'au moins 10 %, maximum à un quart de l'économie), 15 de popularité. Popularité : opinion de
référence de 0,3 à 0,7 selon le charisme, déplacée d'au plus 0,3 par les résultats, rejointe de 8 %
par mois ; notoriété gagnée de 10 % par mois et perdue de 2 % ; notoriété d'un ministre 0,7.
Élection : campagne de six mois, avantage de campagne d'au plus 0,10 (moitié à 24 salaires mensuels
dépensés), deux mandats consécutifs au plus tant que la loi « mandats » n'est pas votée. Lois : une
proposition par an, six mois après le début du mandat, goût du pouvoir requis de 0,55, capital
politique de 52 % plus 2 points par loi déjà votée, abrogation par un président d'intégrité
supérieure à 0,6, 0,03 d'opinion perdue par loi rejetée. Aides : entreprise en perte depuis trois
mois, au plus un quart de sa masse salariale, budget × 0,5 / 1 / 1,5 selon la doctrine
industrielle, moitié du budget à la main de la présidence sous la loi « enveloppe ». Favoritisme :
intégrité du président sous 0,4 (sous 0,15 si le contrôle est intact), influence du patron d'au
moins 25, lien d'au moins 0,5 (ancien employeur 0,35, titres détenus 0,25, proximité politique au
plus 0,20, don de campagne 0,30, ancien du cabinet 0,30) ; contrôle affaibli : un quart des audits ;
presse : 1 % par mois (moitié sous la loi « médias ») ; affaire révélable pendant dix ans ; opinion
perdue 0,30 ; amende de la moitié de l'aide en plus du remboursement. Lobbying : don de 1 % du
bénéfice mensuel pendant la campagne, 0,5 % contre un dossier de monopole, instruction freinée d'au
plus la moitié (influence du dirigeant de 40). Pantouflage : dans les douze mois après la sortie du
cabinet, mandat de trente-six mois, honoraires de la moitié du salaire d'un ministre. Dynasties :
30 % de la notoriété transmise si elle atteint 0,2.

| Réglage | Défaut | Justification |
|---|---|---|
| `influence_ranking` | faux | Influence de chaque ménage, de 0 à 100 : fortune, fonction (présidence, ministère, direction d'entreprise), capital contrôlé, popularité ; les dix premiers sont relevés chaque mois. Mesure seule : aucune décision ne la lit sans un autre réglage, la trajectoire économique est inchangée. |
| `personal_popularity` | faux | Trois traits tirés une fois par ménage (charisme, intégrité, ambition), une notoriété donnée par la fonction et une opinion qui suit les résultats : croissance, chômage et objectifs pour la présidence, avancement de son objectif pour un ministre, marge et effectifs pour un dirigeant. |
| `candidate_elections` | faux | Le sortant (ou son dauphin après deux mandats, sous les lois de pouvoir) affronte le membre le plus fort du camp opposé ; l'écart de popularité et de campagne décale le vote de chaque ménage ; l'élu devient président. Demande le vote individuel ; les tirages du vote sont inchangés. |
| `vote_popularity_weight` | 3,0 | Poids de l'écart d'avantage électoral (notoriété × (opinion − 0,5), de −0,5 à 0,5) dans le logit de chaque électeur : au plus ± 1 environ en pratique, du même ordre que l'écart idéologique entre deux programmes (poids 8 × un écart de proximité de 0,1 à 0,2). |
| `executive_power_laws` | faux | Lois qui étendent le pouvoir de la présidence (enveloppe discrétionnaire, nominations, contrôle des comptes affaibli, tutelle sur les médias, limite de mandats levée), proposées par un président ambitieux et peu intègre, votées selon son capital politique, abrogées par un successeur intègre. Sans tirage. |
| `business_subsidies` | faux | Programme légal d'aides aux entreprises privées non bancaires en perte depuis trois mois, au prorata de leurs salariés, au plus un quart de leur masse salariale. Vrai paiement de l'État, inscrit au livre mensuel, financé par la trésorerie ou l'emprunt ; le budget non employé n'est pas dépensé. |
| `business_aid_gdp_share` | 0,005 | Budget annuel des aides en part du PIB (0,5 %), au centre ; × 0,5 pour une doctrine de laisser-faire, × 1,5 pour une doctrine d'opérateur public. Ordre de grandeur des aides d'État aux entreprises hors crise, non calibré. |
| `political_favoritism` | faux | Sous la loi « enveloppe », un président peu intègre attribue la part discrétionnaire des aides à une entreprise dont le patron est influent et lié à lui ; chaque affaire peut être révélée (opinion, remboursement, amende, remaniement, contrôle rétabli). Demande les aides et les lois de pouvoir. |
| `favoritism_monthly_chance` | 0,10 | Probabilité mensuelle qu'un président passe à l'acte quand toutes les conditions sont réunies (environ une fois par an) : le favoritisme reste un événement rare et daté, pas un flux permanent. |
| `audit_detection_chance` | 0,03 | Probabilité mensuelle qu'un audit révèle une aide de faveur donnée (environ un tiers par an, les trois quarts en quatre ans) ; divisée par quatre sous la loi « contrôle ». S'y ajoute 1 % par mois d'enquête de presse. |
| `corporate_lobbying` | faux | Dons de campagne des entreprises bénéficiaires de taille au moins moyenne au candidat le plus proche de leur dirigeant, et dépenses de conseil d'une entreprise visée par un dossier de monopole, qui freinent son instruction. Dépenses réelles payées aux entreprises de services. |
| `revolving_door` | faux | Pantouflage : un ancien membre du cabinet devient conseiller rémunéré de la plus grande entreprise bénéficiaire des secteurs de son ancien portefeuille ; ce conseiller relie l'entreprise au président qu'il a servi. Honoraires payés par l'entreprise au ménage. |
| `political_dynasties` | faux | L'héritier d'un ménage connu (notoriété d'au moins 0,2) garde 30 % de sa notoriété ; ses traits et son opinion sont les siens. Sans ce réglage, la notoriété disparaît à la succession. Sans effet sans la popularité. |

## Réglages des lots M, R et S : médias, agents apprenants, relations bancaires (novembre 2026)

Trois mécanismes, chacun derrière son réglage, **désactivé par défaut** et neutre pour l'empreinte
d'identité : médias (`economy/media.py`, état dans `simulation.media`), agents apprenants
(`economy/learning.py`, `simulation.learning`), relations bancaires individuelles
(`economy/bank_relations.py`, `simulation.bankrel`). `NewLotsRegistryTests` (`tests/test_monde.py`)
vérifie que la liste ci-dessous correspond aux réglages ajoutés. Aucun ne vise un résultat (audience,
résultat d'une élection, concentration bancaire, niveau des marges) : ce sont des règles de décision
d'agents, des coûts et des vitesses, non calibrés sur des données. Tirages : un générateur propre à
chaque lot, créé à l'activation ; aucun autre générateur n'est déplacé.

Constantes non réglables. Médias : capital de départ de 60 000 € de départ indexé sur les salaires,
au plus la moitié de la trésorerie du fondateur, trois journalistes visés à la création, nouvelle
fondation au plus une fois par an, ligne rapprochée de 5 % par mois de la position de l'actionnaire
d'au moins 10 %, média reconsidéré une fois par an par chaque ménage, ton nul à 25 points d'écart
avec le gouvernement, déplacement du jugement sur la présidence d'au plus 0,30, tutelle (loi
« médias ») : dérive moitié moins rapide vers le pouvoir et effort d'enquête divisé par deux, stock
de publicité oublié de 20 % par mois (moitié de l'effet à cinq salaires mensuels), annonceur ménagé
au-delà de 10 % des recettes publicitaires, 2 % par mois de chance maximale de révéler une affaire,
0,10 de crédibilité gagnée par révélation. Apprentissage : moitié du style d'un nouveau dirigeant
appliquée, précaution d'au plus 30 % de la propension à consommer (atteinte à un mois sur deux sans
emploi), pas maximal du goût du risque à ± 50 % de variation des titres, imitation d'un collègue à
la moitié de `imitation_rate`. Banques : coût de changement doublé pour un emprunteur, au plus la
moitié des clients hors tour fuient une banque fragile en un mois.

Complément de livraison : les banques essaient un coussin prudentiel **supplémentaire** entre
0 et 1 (multiplicateur de l'exigence, jamais une baisse de la règle), selon leur rendement des
fonds propres, pertes incluses. Les ministres dotés d'objectifs enrichis essaient un multiplicateur
d'effort entre 0,5 et 1,5 selon la progression de leur objectif principal ; la mémoire est propre
à la personne, sa génération, sa fonction et l'objectif en cours. Même période et même taille
d'essai que les entreprises, générateur du lot R. Ces bornes décrivent des décisions, pas des
résultats à atteindre. Une direction d'essai est réfléchie lorsqu'elle atteint une borne.

| Réglage | Défaut | Justification |
|---|---|---|
| `media_sector` | faux (lot M) | Entreprises de médias : des ménages aisés des trois camps politiques fondent chacun un média avec leur trésorerie (secteur « media », salariés journalistes). Recettes : abonnements des ménages et publicité des producteurs. La ligne éditoriale suit l'actionnaire de contrôle, colore le jugement des lecteurs et le vote, et les enquêtes remplacent la probabilité fixe de révélation par la presse du lot K. |
| `media_outlets` | 3 (lot M) | Nombre de médias que des ménages cherchent à fonder (un par camp politique au départ) ; les fusions et les faillites peuvent le réduire, une nouvelle fondation est tentée au plus une fois par an. Ne fixe pas le pluralisme observé, qui dépend des audiences. |
| `media_subscription_share` | 0,04 (lot M) | Part du budget de services d'un ménage consacrée à l'abonnement à son média, TVA comprise, dans la limite de sa trésorerie : ordre de grandeur de la dépense de presse et d'audiovisuel d'un ménage, non calibré. |
| `media_ad_share` | 0,01 (lot M) | Part du chiffre d'affaires du mois écoulé qu'un producteur bénéficiaire d'alimentation ou de biens consacre à la publicité, sans entamer son fonds de roulement ; répartie entre les médias selon leur audience. Même ordre que le budget de données du lot G. |
| `media_ad_reach` | 0,3 (lot M) | Hausse maximale du poids d'un annonceur dans les achats des ménages (poids × 1,3 pour un stock de publicité très élevé) : rendements décroissants, inférieure à la portée des magasins, pour limiter la boucle « plus gros, plus de publicité, plus gros ». |
| `media_vote_weight` | 0,10 (lot M) | Écart maximal entre le revenu réel qu'un électeur perçoit et le sien, selon le ton de son média et sa crédibilité : au plus ± 0,4 dans le logit du vote, moins que l'écart idéologique entre deux programmes. Un biais de perception, pas un transfert de voix. |
| `bank_relationships` | faux (lot S) | Relations bancaires individuelles : une banque principale par agent, dépôts lus par banque, prêt demandé d'abord à sa banque, taux des dépôts propre à chaque banque, changements de banque, fuite des banques fragiles, règlement entre banques des écarts de financement, décote localisée. Les soldes restent ceux du moteur en flottants ; le prototype en centimes n'est pas branché. |
| `bank_review_share` | 0,08 (lot S) | Part de la clientèle qui compare les banques chaque mois, à tour de rôle (un client revoit son choix environ une fois par an) : inertie des relations bancaires, sans tirage. |
| `bank_run_threshold` | 0,5 (lot S) | Solidité (fonds propres rapportés à ceux qu'exige l'encours) sous laquelle une banque est fuie par ses clients et n'en attire plus : la moitié de l'exigence réglementaire. Déclenche une décision des déposants, ne borne aucun résultat. |
| `bank_specialization` | 0,5 (lot S) | Avantage d'information d'une banque sur un secteur qu'elle finance déjà : son appétit pour un prêt hors de sa clientèle vaut sa capacité × (1 + 0,5 × la part de son encours dans ce secteur). Fait émerger des banques spécialisées, donc des risques concentrés. |
| `adaptive_agents` | faux (lot R) | Agents apprenants : chaque entreprise ajuste par essais et erreurs son agressivité de prix et sa part réinvestie d'après sa propre marge et imite la plus rentable de son secteur ; un dirigeant emporte son style ; les ménages adaptent précaution et goût du risque à ce qu'ils ont vécu ; les banques essaient un coussin prudentiel et les ministres leur effort avec les objectifs enrichis. Aucun agent ne connaît le modèle. |
| `learning_period_months` | 12 (lot R) | Durée d'un essai : une entreprise juge un changement sur une année de marge, un ménage révise ses habitudes une fois par an. Assez long pour lisser les à-coups mensuels du bénéfice. |
| `learning_step` | 0,05 (lot R) | Taille moyenne d'un pas d'essai sur un trait compris entre 0 et 1 (tirée entre la moitié et une fois et demie), et pas maximal du goût du risque d'un ménage par période : exploration lente, vingt périodes pour traverser l'intervalle. |
| `imitation_rate` | 0,25 (lot R) | Part de l'écart de traits qu'une entreprise comble avec la plus rentable de son secteur à chaque période (la moitié pour un ménage envers son collègue le plus riche) : diffusion des pratiques sans alignement immédiat. |

## Réglages du monde (lots L, N, O, P, Q, novembre 2026)

Un monde (`economy/world.py`) porte plusieurs régions, chacune une `EconomySimulation` complète.
Ses réglages sont ceux de `WorldConfig` ; `base` (un `SimulationConfig`) reste la source des
paramètres de chaque région, modifiée par son profil (`REGION_PROFILES`). Un monde d'une région sans
profil est identique au moteur (`WorldTests`). `NewLotsRegistryTests` vérifie la liste ci-dessous.
Aucun réglage ne vise un résultat (solde extérieur, taux de change, solde migratoire, nombre de
groupes). Constantes du module, non réglables : départ possible avant 45 ans, région jamais sous
40 ménages, prime de rachat de 30 %, un quart de la trésorerie libre de l'acquéreur engagé par
mois, ramassage abandonné après 36 mois, acquéreur d'au moins 24 mois, cible d'au plus la moitié
de sa capitalisation et moins productive d'au moins 10 %, 24 mois entre deux opérations d'un même
acquéreur, contrôle à la moitié du capital en circulation, un quart de l'avance de productivité
transmis, filtrage au-delà d'une orientation de 65, écart de croissance pris en compte borné à 20 %,
taux de change entre 0,2 et 5 et de moyenne géométrique 1.

Compensation : les trésoreries sont réservées une seule fois par payeur avant résolution des
flux ; les créances migratoires prépayées n'ajoutent pas une seconde fois de liquidité. Une
circulation à capacité finie maximise le volume réglé, en unité commune, dans un graphe de cinq
nœuds au plus (régions et réserve). Les intentions d'un même couple sont servies au prorata ;
les cycles résiduels sont résolus par Bellman-Ford (précision des capacités 10⁻⁹). Une chambre
déjà au-delà du plafond ne peut pas accroître cet avoir. Refus explicite si un crédit n'est pas
financé à 10⁻⁶ près. Normalisation du change par translation des
logarithmes et projection entre les deux bornes (64 itérations), afin que la moyenne géométrique
ne fasse pas sortir un taux de son intervalle.

| Réglage | Défaut | Justification |
|---|---|---|
| `regions` | 3 | Nombre de régions (1 à 4), chacune avec ses ménages, ses entreprises, ses banques, sa banque centrale, son gouvernement et sa monnaie. Les profils leur donnent des populations, des productivités, des ressources et une orientation de départ différentes. |
| `integration_month` | 0 | Nombre de mois pendant lesquels les régions restent isolées avant l'ouverture du commerce, des départs et des rachats : permet d'observer des économies d'abord indépendantes, puis leur intégration. |
| `interregional_trade` | vrai | Commerce entre régions : une région achète aux autres une part de sa demande non servie d'alimentation et de biens, prise sur leur production du mois suivant, seulement si son prix dépasse le prix du vendeur converti et majoré du transport. |
| `transport_cost` | 0,10 | Majoration du prix d'un produit venu d'une autre région : sans écart de prix d'au moins 10 % entre deux régions, aucun échange n'a lieu. Ordre de grandeur d'un coût de transport et de distribution, non calibré. |
| `migration` | vrai | Mobilité des ménages : un ménage mobile part pour la région où le revenu réel attendu, loyer déduit, dépasse le sien, après avoir vendu son logement et remboursé ses crédits ; il emporte sa trésorerie convertie et garde ses actions. |
| `migration_monthly_share` | 0,005 | Part maximale des ménages d'une région qui la quittent en un mois (6 % par an au plus) : les départs sont étalés, une région ne se vide pas d'un coup et les salaires ont le temps de réagir. |
| `migration_gain` | 0,15 | Gain de revenu réel attendu au-delà duquel un ménage mobile part : coût de déménagement et attachement à sa région, auquel s'ajoute la décote de la vente rapide de son logement. |
| `commuting` | vrai | Travail frontalier : un ménage conserve sa résidence, son logement, ses crédits, sa consommation et son vote ; un mandataire occupe un poste vacant dans une autre région. Les salaires et primes sont rapatriés par compensation et imposés à résidence le mois suivant leur encaissement. Les mandataires ne comptent jamais dans la population. |
| `commuting_monthly_share` | 0,005 | Nombre maximal de nouveaux contrats frontaliers par mois en part de la population de résidence. Les fractions s'accumulent ; les postes restés vacants après les embauches locales sont pourvus en premier par métier et compétence. Ne fixe pas l'effectif frontalier observé. |
| `commuting_gain` | 0,15 | Gain minimal du salaire converti après le barème d'impôt de résidence par rapport au salaire net actuel ou au revenu attendu hors emploi. Représente l'inertie et le coût du trajet dans la décision, sans inventer un prélèvement monétaire ni garantir un gain réalisé. |
| `cross_border_takeovers` | vrai | Groupes multirégionaux : une entreprise rentable ramasse les titres d'un concurrent d'une autre région, plus petit et moins productif, quand le bénéfice attendu couvre le coût de ses fonds propres ; au-delà de la moitié du capital elle le contrôle et en reçoit les dividendes. |
| `investment_screening` | vrai | Filtrage des investissements : un gouvernement d'orientation supérieure à 65 refuse le rachat par une entreprise d'une autre région d'une entreprise d'un secteur critique (`critical_sectors`). Décision de doctrine, comptée au relevé. |
| `yardstick_voting` | vrai | Vote comparatif : les électeurs d'une région jugent aussi leur gouvernement sur l'écart entre la croissance de leur PIB réel par ménage et celle des autres régions, en plus de leur propre revenu. |
| `yardstick_weight` | 0,5 | Part de l'écart de croissance avec les autres régions (borné à ± 20 %) ajoutée à la croissance du revenu réel que perçoit l'électeur : au plus ± 0,1, soit ± 0,4 dans le logit du vote, du même ordre que le biais d'un média. |
| `tax_competition` | vrai | À la prochaine loi de finances, un gouvernement non social peut rapprocher son objectif d'impôt sur les sociétés du taux moyen observé chez les voisins moins imposés. La contrainte budgétaire peut annuler cette baisse ; les gouvernements sociaux conservent l'objectif de leur doctrine. |
| `tax_competition_weight` | 0,25 | Part maximale de l'écart relatif de taux comblée dans l'objectif fiscal, multipliée par le libéralisme du gouvernement (1 − orientation / 65, jamais négatif). La vitesse annuelle de vote reste celle de budget_tax_speed et aucun taux réalisé n'est fixé. |
| `clearing_holding_share` | 0,02 | Avoirs maximaux de la chambre de compensation d'une région, en part de son PIB annuel, au-delà de ce qu'elle doit reverser : le reste du monde n'accumule pas sa monnaie, une région ne paie durablement à l'extérieur que ce qu'elle en reçoit. Même valeur que le plafond du reste du monde du lot J. |

## Réglages du lot T : offres publiques, croissance des effectifs et sauvegarde (novembre 2026)

Trois mécanismes (`economy/consolidation.py`, `economy/labor.py`, `economy/safeguard.py`), désactivés par défaut : tant
qu'ils sont décochés, la simulation est identique au bit près. `TakeoverRegistryTests`
(`tests/test_rachats.py`) vérifie la liste ci-dessous. Aucun réglage ne vise un nombre de rachats,
une concentration ou une taille d'entreprise. Constantes reprises du lot G, non réglables : un
quart de l'écart de productivité transmis à la cible (`MANAGEMENT_TRANSFER`), écart pris en compte
borné à 50 %. Constantes de la procédure de sauvegarde (`economy/safeguard.py`), non réglables : au
moins 10 salariés, 3 mois de pertes consécutifs, trésorerie sous 3 mois de masse salariale, un noyau
viable d'au moins un quart de l'effectif, prêt de sauvegarde de 3 mois de la paie réduite, 24 mois
entre deux plans d'une même entreprise.

| Réglage | Défaut | Justification |
|---|---|---|
| `synergy_takeovers` | faux (T1) | Offres publiques réalistes dans un même secteur : l'acquéreur offre au moins la valeur fondamentale de la cible (sinon ses actionnaires informés refuseraient), compte ce qu'il apporte (méthodes, magasins, frais de structure évités) dans la valeur qu'il lui donne, peut payer une part du prix en actions nouvelles, et la productivité fusionnée est pondérée par les effectifs au lieu d'une moyenne simple. Les règles de contrôle, de concurrence et d'acceptation restent celles du moteur. |
| `takeover_share_payment` | 0,5 (T1) | Part maximale du prix payée en actions nouvelles de l'acquéreur coté, au cours du jour (offre mixte) : la trésorerie libre ne finance que le reste. Ordre de grandeur des offres mixtes ; dilue les actionnaires de l'acquéreur, sans création de monnaie. |
| `proportional_hiring` | faux (T2) | Embauches et licenciements mensuels bornés en proportion de l'effectif d'une entreprise, jamais moins que les bornes fixes `max_monthly_hiring` et `max_monthly_layoffs` : une grande entreprise ajuste ses effectifs à son échelle. Le marché du travail, la trésorerie et la couverture de la masse salariale restent les vraies contraintes. |
| `staff_change_share` | 0,10 (T2) | Part de l'effectif qu'une entreprise peut ajouter ou retirer en un mois avec la croissance proportionnelle : un doublement prend au moins sept mois. Ordre de grandeur d'une croissance rapide d'entreprise, non calibré. |
| `safeguard_procedure` | faux (T3) | Procédure de sauvegarde : une entreprise d'au moins dix salariés, en pertes, presque sans trésorerie, à qui les banques refusent par leur propre test le crédit de sa paie, mais dont les recettes paient un noyau viable, ramène en un mois son effectif à ce noyau, voit sa dette bancaire en partie abandonnée (perte des banques) et reçoit un prêt de sauvegarde remboursé en priorité. Remplace l'effondrement en bloc puis la survie en coquille mesurés au lot T ; ne vise aucun nombre d'entreprises ni d'emplois. |
| `safeguard_debt_share` | 0,5 (T3) | Part de la dette que les recettes permettent de servir (intérêts sous `maximum_debt_service_ratio` de la recette lissée, au taux de l'entreprise) conservée après le plan : la moitié laisse de la place au crédit d'exploitation ordinaire. Ordre de grandeur des remises de dettes d'un plan de sauvegarde, non calibré. |

## Réglages du lot U : impôt, crédit, embauche et prix (12 novembre 2026)

Quatre mécanismes (`economy/state_budget.py`, `economy/banking.py`, `economy/labor.py`,
`economy/business.py`, `economy/simulation.py`), neutres à leur valeur par défaut : la simulation
est alors identique au bit près. `StabilisationRegistryTests` (`tests/test_stabilisation.py`)
vérifie la liste ci-dessous. Diagnostic, mesures avant/après et réserves :
`results/chantiers_20261112/RAPPORT.md`. Aucun réglage ne vise un taux de chômage, un prix, un
volume de crédit ou un nombre d'entreprises.

| Réglage | Défaut | Justification |
|---|---|---|
| `tax_capped_by_cash` | faux | L'impôt sur le revenu recouvré, arriérés compris, ne dépasse pas la trésorerie du ménage ; le solde reste dû et sera recouvré plus tard. Sans le réglage (règle historique conservée pour l'identité), un rappel d'impôt rend la trésorerie négative, puis la demande d'alimentation et de biens du ménage négative : mesuré dans 14 régions sur 36 d'un monde « Tout activer ». |
| `tax_backlog_months` | 0 | Recouvrement échelonné : l'arriéré d'impôt d'un ménage est appelé par mensualités d'un N-ième au lieu d'être exigé en une fois ; 0 garde la règle historique. Mesuré : un service des Finances de nouveau pourvu recouvrait des années d'arriéré en un mois (35,6 M pour un PIB mensuel de 22 M), et 8 crises sur 15 suivaient un tel prélèvement. Aucun montant n'est effacé. |
| `sovereign_risk_weight` | 1,0 | Poids des titres publics dans l'exposition qui consomme les fonds propres d'une banque, pour sa capacité de crédit et sa solidité perçue par les déposants. 1 : comme un prêt (règle historique). 0 : aucun fonds propre exigé, ordre de grandeur de la pondération nulle des titres souverains en monnaie nationale. Mesuré : quand le crédit est gelé, les titres publics font 64 % de l'exposition des banques. |
| `demand_led_hiring` | faux | Embauche tirée par la demande non servie, pour l'alimentation et les biens seulement : plafond mensuel relevé, perte du mois non bloquante si chaque unité vendue couvre ses intrants, offre salariale alignée sur les employeurs les mieux-disants dans la limite de ce qu'un salarié fait vendre et de ce que la trésorerie paie. Le marché du travail, la trésorerie et la valeur marginale restent les contraintes. |
| `demand_hiring_share` | 0,25 | Part de l'écart entre le besoin en personnel et l'effectif qu'un producteur peut combler en un mois avec le réglage précédent, jamais moins que `max_monthly_hiring` : un écart se résorbe aux trois quarts en cinq mois. Ordre de grandeur d'une montée en charge, non calibré. |
| `shortage_price_release` | faux | L'encadrement des prix d'un gouvernement social ne s'applique pas à un produit en pénurie : un prix bloqué y entretient la demande non servie (mesuré : 75 % de biens non servis sous encadrement contre 52 % à prix libres). Le levier politique reste entier hors pénurie. |
| `price_control_shortage_limit` | 0,20 | Part de la demande non servie le mois précédent au-delà de laquelle l'encadrement est levé pour ce produit. Un cinquième : une pénurie nette, au-dessus des à-coups mensuels ordinaires ; non calibré. |

## Rareté des matières premières et efficacité de la recherche (12 novembre 2026)

Diagnostic et mesures : `results/analyse_pib_par_menage_20261112/RESUME.md`. L'option est désactivée par
défaut ; « Tout activer » la coche et porte `knowledge_growth_per_intensity` à 0,5 (« Tout désactiver »
remet 0,25). Aucun prix, volume ou stock n'est visé : la rente et l'exploration réagissent à la seule
part du stock de départ consommée.

| Réglage | Défaut | Justification |
|---|---|---|
| `knowledge_growth_per_intensity` | 0,25 | Croissance mensuelle du stock de connaissances par point de PIB de recherche, avant 0,2 % d'obsolescence : il faut 0,8 % du PIB en recherche pour compenser l'obsolescence. Désormais réglable dans l'interface (« Efficacité de la recherche »). À 0,5, la productivité croît assez pour que le PIB réel par ménage progresse ; sans la rareté des matières, cette croissance épuise le stock vierge du scénario complet. |
| `raw_material_scarcity` | faux | Rareté des matières premières : le stock vierge est fini et son prix suivait le seul coût d'extraction, sans signal de rareté, d'où une rupture brutale de la production à l'épuisement. Avec l'option, le prix plancher reçoit une rente de rareté et l'exploration remplace une part croissante des volumes extraits (production et chantiers). |
| `scarcity_rent` | 0,5 | Rente de rareté : prix plancher × (1 + rente × rareté / (1 − rareté)), rareté = part du stock de départ consommée ; +50 % à mi-stock, bornée à × 11 à l'épuisement. Ordre de grandeur d'une rente de Hotelling simplifiée, non calibré. |
| `scarcity_exploration_response` | 0,5 | Part remplacée par l'exploration = `reserve_replacement_rate` + réponse × rareté : à 0,95 de base, l'exploration compense l'extraction dès 10 % du stock consommé. Représente des gisements mieux payés donc plus explorés ; le coût unitaire d'exploration reste `exploration_cost_per_unit`, payé par les extracteurs aux services. |

## Réglages du lot V : douze mécanismes de société (12 novembre 2026)

Douze mécanismes (`economy/society.py` pour les ménages et l'État, `economy/ventures.py` pour les
entreprises et la finance), chacun derrière son réglage, **désactivé par défaut** et neutre pour
l'empreinte d'identité. `SocietyRegistryTests` (`tests/test_societe.py`) vérifie que la liste
ci-dessous correspond aux réglages ajoutés. Aucun ne vise un résultat (nombre de dossiers, de
manifestations, de faillites, niveau des prix) : ce sont des règles de décision et des chances
d'événements, non calibrées sur des données. Tirages : un générateur propre au lot (traits des
ménages, tirage de la loterie, cause d'un donateur, traits des entreprises fondées), créé à
l'activation ; aucun autre générateur n'est déplacé. L'état vit dans `simulation.society`, créé au
premier usage. Les comptes de la loterie, des fondations et des fonds spéculatifs entrent dans
`Ledger.nonbank_cash` ; les titres adossés sont un passif des banques dans `Ledger.deposits`.

Constantes des modules, non réglables. Surendettement : reste à vivre d'un mois de salaire minimum.
Philanthropie : don d'au plus un quart de la trésorerie par mois ; une fondation dépense 1/24 de sa
trésorerie par mois ; effet à moitié pour une dépense mensuelle de 1 % d'un salaire de référence par
ménage ; au plus 0,3 de qualité de l'éducation et 15 % de mortalité en moins ; renommée complète
pour dix années de salaire données, oubliée de 2 % par mois. Loterie : mise d'au plus un quart de la
trésorerie ; le plus modeste joue trois fois plus que le plus riche. Addictions : une unité par mois
à dépendance complète, 150 unités vendues par salarié, seuil de penchant de 0,3 (0,15 après six mois
de chômage), 2 % de l'écart à l'équilibre comblé par mois, 5 % de sevrage par mois sans produit,
1 % par doublement du prix, dégâts de 0,002 par mois à dépendance complète, soins à partir de 0,05,
prix entre 0,5 et 2,5 fois la référence (+1 % si les volumes tiennent, −3 % s'ils reculent de plus
de 5 %). Capital d'une entreprise de tabac ou d'une agence : 60 000, apport d'au plus la moitié de
la trésorerie du fondateur. Manifestations : tension rejointe de 15 % par mois, pression de 1 pour
10 points de chômage, 20 points de patrimoine des 10 % ou 10 % de salaire réel perdus ; manifestation
à 1, émeute à 2 (2 % des stocks détruits), six mois entre deux, trois concessions par gouvernement,
moitié de la tension retombée après une concession. Grands travaux : route (poids 1, 24 mois),
réseau (0,75, 18 mois), port (1,5, 36 mois) ; entretien de 0,2 % du coût par mois ; un lancement par
an ; ouvrage hors service sous 0,2 d'état ; un port relève l'ouverture commerciale d'au plus 50 %.
Marques : 4 salaires de référence de campagnes par salarié d'agence et par mois ; moitié de l'effet
à 20 salaires de référence de notoriété. Franchises : franchiseur d'au moins 24 mois, trois
franchises au plus, une ouverture par semestre, apport d'au moins deux droits d'entrée, 95 % de la
productivité du franchiseur. Coopératives : apport d'au plus la moitié de la trésorerie et trois
mois de salaire, capital d'au moins deux mois de paie, 90 % de l'ancien salaire, moitié de la
clientèle gardée, mémoire de douze mois des salariés licenciés. Fonds spéculatifs : souscripteurs
au goût du risque d'au moins 0,7 et à la trésorerie d'au moins 20 salaires, 10 % confiés, capital
minimal de 30 salaires ; cible en pertes depuis trois mois ; trois positions, un quart de la
trésorerie par position, moitié des titres libres des ménages ; rachat à +25 %, après 24 mois ou au
redressement ; distribution au-delà de 1,2 fois le capital, dissolution sous 0,25. Titrisation :
émission minimale de 5 salaires de référence. Prêts participatifs : encours d'au plus les fonds
propres, prêt minimal de 2 salaires de référence.

| Réglage | Défaut | Justification |
|---|---|---|
| `personal_bankruptcy` | faux | Commission de surendettement : efface le crédit à la consommation d'un ménage en impayé dont la charge de dette est insoutenable, après saisie de sa trésorerie au-delà du reste à vivre ; la banque passe la perte, le ménage est interdit de crédit. Sans le réglage, un ménage en défaut peut réemprunter dès le mois suivant. |
| `debt_relief_service_share` | 0,5 | Part du revenu du mois au-delà de laquelle la charge de dette (crédit à la consommation et prêt immobilier) rend un dossier recevable : la moitié du revenu, seuil d'usage des commissions de surendettement. Critère d'entrée, pas un objectif de nombre de dossiers. |
| `credit_ban_months` | 60 | Durée de l'interdiction de crédit après un dossier ou un défaut : cinq ans, comme l'inscription à un fichier des incidents de paiement. Elle s'éteint à la succession du ménage. |
| `philanthropy` | faux | Dons des ménages très riches à trois fondations (école, hôpital, presse) qui dépensent auprès des entreprises ; effets sur la compétence des nouveaux ménages, la mortalité et les médias ; renommée du donateur. Sans le réglage, la fortune ne sert qu'à l'épargne et à l'héritage. |
| `philanthropy_wealth_multiple` | 10 | Fortune nette, en multiples de la fortune moyenne, à partir de laquelle un ménage donne : seuls les très riches (ordre du centième le plus fortuné) sont concernés. Seuil de comportement, sans cible de montant donné. |
| `philanthropy_giving_rate` | 0,01 | Part mensuelle de la fortune au-delà du seuil qu'un donateur pleinement généreux donne (1 %, soit environ 11 % par an de l'excédent), multipliée par son trait de générosité tiré entre 0 et 1. |
| `state_lottery` | faux | Loterie d'État : mises des ménages indépendantes du revenu, part de l'État, cagnotte emportée par un seul joueur. Recette publique volontaire qui pèse plus sur les revenus modestes (mesurée par l'effort des cinquièmes extrêmes). |
| `lottery_stake_share` | 0,02 | Mise mensuelle du joueur le plus assidu et le plus modeste, en part du salaire de référence × 1,5 : de l'ordre de 2 à 3 % d'un salaire, ordre de grandeur des dépenses de jeu des joueurs réguliers. |
| `lottery_payout_share` | 0,5 | Part des mises reversée aux joueurs par la cagnotte ; le reste est une recette de l'État. Une loterie publique redistribue de l'ordre de la moitié des mises. |
| `lottery_draw_chance` | 0,25 | Chance mensuelle que la cagnotte soit gagnée : en moyenne un gagnant tous les quatre mois, ce qui laisse la cagnotte grossir et crée des fortunes soudaines. Tirage du générateur du lot. |
| `addictive_goods` | faux | Entreprises de tabac et d'alcool (secteur « vice ») fondées par des ménages : demande qui ne dépend pas du prix, accise, dépendance qui évolue, soins et surmortalité plus tard. Sans le réglage, aucun bien n'a de demande rigide. |
| `addiction_firms` | 2 | Nombre d'entreprises du secteur à fonder : deux, pour qu'une concurrence par les prix soit possible sans disperser un petit marché. |
| `addiction_budget_share` | 0,08 | Dépense mensuelle d'un ménage pleinement dépendant au prix de référence, taxes comprises, en part du salaire de référence. Avec une dépendance moyenne proche d'un tiers, le secteur pèse quelques pour cent de la consommation. |
| `addiction_excise_rate` | 0,5 | Accise en part du prix hors taxes, recette de l'État (la TVA s'applique en plus, accise comprise). Taxe de comportement : la moitié du prix hors taxes. |
| `addiction_care_cost` | 0,10 | Coût mensuel des soins d'un ménage aux dégâts de santé complets, en part du salaire de référence ; proportionnel aux dégâts. Partagé entre l'État et le ménage selon l'orientation du gouvernement. |
| `addiction_mortality_gain` | 1,0 | Hausse de la probabilité de décès à dégâts de santé complets : doublée. Les dégâts s'accumulent lentement (0,5 après vingt ans de dépendance complète). |
| `franchising` | faux | Franchises : un producteur rentable et débordé loue sa marque et ses méthodes à un petit entrepreneur, qui ouvre une entreprise à part entière. Entrée de concurrents moins capitalisés, liés à un réseau. |
| `franchise_entry_fee` | 10 000 | Droit d'entrée payé par le franchisé au franchiseur, en euros de départ indexés sur les salaires : un dixième du seuil de trésorerie d'un fondateur ordinaire (100 000). |
| `franchise_royalty_rate` | 0,05 | Redevance mensuelle sur le chiffre d'affaires du franchisé (5 %), ordre de grandeur des contrats de franchise ; payée dans la limite de sa trésorerie. |
| `brand_marketing` | faux | Agences de marketing (secteur « marketing ») fondées par des ménages : elles vendent des campagnes aux producteurs, dont la notoriété durable relève le poids dans les achats. Déplace des ventes entre producteurs sans créer de demande. |
| `marketing_agencies` | 2 | Nombre d'agences à fonder : deux, pour que les annonceurs ne dépendent pas d'un seul prestataire. |
| `marketing_budget_share` | 0,02 | Part du chiffre d'affaires du mois écoulé qu'un producteur bénéficiaire consacre aux campagnes (2 %), sans entamer son fonds de roulement ; servie dans la limite de ce que les agences produisent. |
| `brand_decay_rate` | 0,03 | Part de la notoriété d'une marque oubliée chaque mois (3 %, demi-vie de près de deux ans) : un stock durable, à la différence de la publicité de presse du lot M (20 % par mois). |
| `brand_reach` | 0,5 | Hausse maximale du poids d'un producteur dans les achats des ménages par sa marque (+50 % à notoriété infinie), à rendements décroissants ; même ordre que les magasins du lot G. |
| `worker_buyouts` | faux | Coopératives de chômeurs : les salariés d'une entreprise liquidée la relancent avec leur épargne et leurs allocations versées d'avance ; parts égales et incessibles. Sans le réglage, une liquidation disperse toujours l'équipe. |
| `buyout_min_workers` | 3 | Nombre minimal d'anciens salariés sans emploi pour une reprise : en dessous, ce n'est pas la reprise d'une équipe. |
| `buyout_benefit_months` | 6 | Mois d'allocation chômage que l'État verse d'avance à chaque repreneur (capitalisation des droits), dans la limite de ses fonds ; consigné au journal des décisions. |
| `short_selling` | faux | Fonds spéculatifs financés par des ménages : ils empruntent les titres d'une entreprise fragile, les vendent à la séance et gagnent si le cours baisse ou si elle fait faillite. La vente pèse sur le cours, donc sur la capacité de l'entreprise à lever des fonds. |
| `hedge_funds` | 2 | Nombre de fonds spéculatifs à créer, si des ménages au goût du risque suffisant réunissent le capital minimal. |
| `short_float_share` | 0,15 | Part maximale du flottant d'une cible empruntée et mise en vente en un mois ; les titres invendus retournent aux prêteurs. Borne technique d'un ordre, pas un objectif de baisse. |
| `short_fee_rate` | 0,002 | Frais mensuels de prêt de titres payés aux ménages prêteurs, en part de la valeur empruntée (environ 2,4 % par an). |
| `mortgage_securitization` | faux | Titrisation : une banque émet des titres adossés à ses prêts immobiliers ; intérêts, remboursements et pertes passent aux ménages porteurs, et ses fonds propres sont libérés. Sans le réglage, tout le risque immobilier reste au bilan des banques. |
| `securitization_share` | 0,5 | Part maximale des prêts immobiliers d'une banque qu'elle peut adosser à des titres : la moitié, le reste demeurant à son risque. Borne réglementaire de l'émission. |
| `securitization_savings_share` | 0,05 | Part de son épargne investissable qu'un ménage vise en titres adossés, × (1,5 − goût du risque) : les prudents en achètent davantage, comme pour les obligations d'entreprise. |
| `securitization_fee_rate` | 0,10 | Part des intérêts que la banque garde pour gérer les prêts titrisés ; le reste est transmis aux porteurs. |
| `crowdfunding` | faux | Financement participatif : les ménages prêtent directement aux jeunes entreprises de leur région, sans banque ni création de monnaie ; en faillite ils passent après les banques. Sans le réglage, une jeune entreprise ne dépend que de sa banque et de la Bourse. |
| `crowdfunding_savings_share` | 0,02 | Part de son épargne investissable qu'un ménage vise en prêts participatifs, × 2 × son goût du risque : un placement risqué, que les audacieux portent. |
| `crowdfunding_max_age_months` | 36 | Âge au-delà duquel une entreprise n'est plus une « jeune entreprise » éligible : trois ans ; ensuite elle peut émettre des obligations (lot J, à partir de deux ans). |
| `crowdfunding_premium` | 0,004 | Prime mensuelle au-dessus du taux directeur payée par l'emprunteur (environ 4,8 points par an) : le prix du risque d'une jeune entreprise. |
| `social_unrest` | faux | Tension sociale et manifestations : le chômage, les inégalités de patrimoine et la baisse du salaire réel font monter la tension ; une manifestation fait perdre du travail et le gouvernement concède des transferts. Sans le réglage, le mécontentement ne s'exprime qu'au vote. |
| `unrest_unemployment` | 0,10 | Taux de chômage au-delà duquel la pression sociale monte : 10 %, le double du chômage de référence de la banque centrale. Seuil de déclenchement, pas un objectif. |
| `unrest_inequality` | 0,60 | Part du patrimoine détenue par les 10 % les plus riches au-delà de laquelle la pression monte : 60 %, niveau observé dans les économies inégalitaires. |
| `unrest_production_loss` | 0,30 | Part du travail du mois perdue pendant une manifestation à tension de 1 ou plus : moins qu'une grève de secteur (50 %), mais dans toute l'économie. |
| `unrest_concession` | 0,10 | Hausse des transferts et de l'allocation chômage de la doctrine concédée après une manifestation (10 %, moitié moins pour un gouvernement libéral), trois fois au plus par gouvernement ; appliquée par la loi de finances suivante. |
| `public_works` | faux | Grands travaux : route, réseau et port, chantiers pluriannuels payés aux entreprises ; un ouvrage achevé relève la productivité de tous selon son état, qui se dégrade sans entretien. Distinct du capital public courant de la loi de finances. |
| `public_works_gdp_share` | 0,02 | Coût d'un ouvrage de poids 1 en part du PIB annuel (2 %), étalé sur la durée du chantier ; payé sur la trésorerie de l'État puis par emprunt. |
| `public_works_gain` | 0,02 | Gain de productivité de toutes les entreprises par ouvrage en parfait état (2 %), multiplié par son état ; trois ouvrages donnent au plus 6 %. Hypothèse non calibrée. |
| `public_works_wear_rate` | 0,005 | Perte d'état mensuelle d'un ouvrage laissé sans aucun entretien (0,5 %) ; nulle s'il est entièrement entretenu. Sans entretien, il est hors service en treize ans. |
| `public_works_debt_limit` | 1,0 | Dette publique, en parts du PIB annuel, au-delà de laquelle aucun chantier n'est lancé : règle de prudence de l'État, pas une cible de dette. |
