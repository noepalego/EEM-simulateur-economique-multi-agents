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
