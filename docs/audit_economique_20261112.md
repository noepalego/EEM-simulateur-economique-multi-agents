# Audit économique et corrections du 12 novembre 2026

La simulation dispose d'un circuit monétaire, de contraintes de production et de nombreux
tests. L'audit a cependant trouvé des incohérences qui faussaient la production mesurée,
la disponibilité des ressources et les décisions de financement. Les corrections ci-dessous
sont appliquées au moteur et aux outils d'évaluation. Aucun coefficient de comportement
n'a été recalibré pour obtenir moins de chômage ou une croissance positive.

Les sources présentes au début de l'audit, y compris les modifications déjà en cours, sont
conservées localement dans `results/audit_economique_20261112/avant/`. Les anciens graphiques
du README décrivent leurs versions historiques ; ils ne constituent pas une mesure de cette
nouvelle version.

## Défauts reproduits et corrections appliquées

| Défaut | Conséquence | Correction |
|---|---|---|
| La variation nominale des stocks était la différence entre deux valeurs comptables à des prix différents. | La hausse du prix des stocks existants augmentait le PIB sans nouvelle production. Avec 100 unités alimentaires et 50 biens inchangés, multiplier le coût des intrants par trois créait 17 200 unités de PIB fictif. | Valoriser la variation des **quantités** au coût courant du mois ; conserver les coûts de base pour le PIB réel. |
| Construire un relevé mettait à jour les stocks précédents. Le premier mois ne disposait pas d'un stock d'ouverture. | Consulter deux fois les mêmes flux donnait deux PIB différents ; les stocks produits au premier mois pouvaient être oubliés. | Initialiser le stock d'ouverture avant la chauffe et mémoriser la clôture dans le moteur. `build_record` reste une lecture. |
| Sans extracteur, le stock naturel entier restait utilisable. | Un secteur disparu continuait à approvisionner les producteurs sans travail d'extraction. | N'autoriser dans ce cas que les matières déjà recyclées. |
| L'exploration ajoutait les réserves annoncées même sans prestataire ou paiement suffisant. | Des commandes refusées ou non financées découvraient gratuitement des gisements. | Proportionner les découvertes aux prestations effectivement payées, pour les biens et les chantiers. |
| Le poids des titres publics modifiait la capacité de prêt, mais pas toutes les exigences de capital. | Une banque pouvait avoir du capital suffisant pour prêter et être simultanément considérée sous-capitalisée pour ses dividendes ou sa recapitalisation. | Employer la même exposition pondérée dans les décisions de capital, le backstop et le coût du capital. L'apport de titres nouveaux respecte `x = (rX - E) / (1 - rw)`. |
| Une saisie transférait un logement contre le minimum du prix et de la trésorerie de l'agence. | Une agence sans argent pouvait recevoir gratuitement un logement ; les pertes bancaires étaient artificiellement amplifiées. | Exiger le paiement du prix décoté. Sinon, reporter la vente en conservant le logement et la dette. |
| Une commande de ménage sans répartition positive prélevait quand même son montant. | De la monnaie quittait le ménage sans bénéficiaire. | Une commande vide ou de poids nul ne prélève rien, y compris dans le chemin optimisé. |
| Les outils annualisaient des périodes mal datées et comptaient les sociétés de chauffe comme de nouvelles entrées. | Inflation surestimée, croissance fausse ou plantage sur horizons courts, entrées et concentration mal interprétées. | Ancrer l'inflation à la fin de chauffe, dater les moyennes par leur centre, exclure les sociétés préexistantes, utiliser la durée réelle pour le ROE et signaler les statistiques indisponibles. Sans ventes, aucune part de marché ni HHI n'est inventé. |

Le traitement des stocks suit le principe de la comptabilité nationale : la variation des
volumes est valorisée aux prix de la période ; une simple réévaluation n'est pas une nouvelle
production. Le modèle conserve sa valorisation simplifiée au coût des intrants, sans prétendre
reproduire un système complet de comptes nationaux.
Source : [BEA, Change in private inventories](https://www.bea.gov/help/glossary/change-private-inventories).

## Protocole de vérification

La suite avant correction comptait 742 tests. Les nouveaux tests ciblent les invariants
économiques : une prestation non payée ne découvre pas de réserves ; un acheteur insolvable
ne reçoit pas de logement ; une réévaluation sans variation physique ne crée pas de PIB.
Les fixtures de quatre anciens tests ont reçu les fournisseurs ou matières recyclées
nécessaires pour continuer à isoler leur mécanisme. Les assertions n'ont pas été affaiblies.

**Validation : 769 tests exécutés, sans échec inattendu**, dont **27 nouveaux tests de
régression** ; l'échec attendu historique sur les bornes de patrimoine et de dette reste
déclaré comme tel. Une relecture
croisée a aussi vérifié l'égalité des mesures d'inflation entre `evaluate.py` et
`realism_probe.py` sur 12 couples de durées de chauffe et d'observation.

La comparaison des trajectoires utilise les mêmes graines **3001, 3002 et 3003**, **120 mois
de chauffe** puis **400 mois observés** : trois économies aux réglages de base et trois
mondes de trois régions avec les réglages du bouton « Tout activer ». Cela donne 12
trajectoires régionales par version. Les paramètres exacts et tous les relevés mensuels sont
enregistrés avec les résultats ; les réglages du bouton sont lus sans lancer Streamlit.

```powershell
.\.venv\Scripts\python.exe run_tests.py -j 4
.\.venv\Scripts\python.exe results/audit_economique_20261112/campagne.py --source results/audit_economique_20261112/avant --output results/audit_economique_20261112/mesures_avant --months 400 --jobs 4
.\.venv\Scripts\python.exe results/audit_economique_20261112/campagne.py --source results/audit_economique_20261112/apres --output results/audit_economique_20261112/mesures_apres --months 400 --jobs 4
```

Les corrections modifient intentionnellement les trajectoires. L'ancienne référence
`identity_reference.json` est conservée pour rendre ces différences visibles ; elle ne doit
pas être réenregistrée pour masquer le changement du modèle. Une comparaison avant/après
porte sur des chemins devenus différents dans un système sensible aux conditions initiales,
et ne suffit pas à attribuer chaque écart macroéconomique à un mécanisme particulier.

Le contrôle d'identité historique réussit sur **5 cas sur 5 avec les sources d'avant**.
Il signale **5 différences sur 5 après correction**, résultat attendu d'un changement
économique : le PIB corrigé intervient notamment dans les décisions publiques et bancaires.
La référence n'a pas été réécrite.

## Résultats sur 400 mois

Chaque valeur est la médiane des statistiques de chaque région. Le chômage est d'abord
moyenné sur les 400 mois de chaque trajectoire ; les taux de demande non servie sont pondérés
par les volumes demandés. Dans le monde, ils décrivent le marché intérieur avant les achats
entre régions et ne mesurent donc pas la pénurie finale des ménages après importations.
La croissance compare les moyennes du PIB réel par ménage des 24 premiers et derniers mois,
annualisées selon la distance entre les centres des fenêtres.

| Indicateur | Base avant (3 régions) | Base après | Monde avant (9 régions) | Monde après |
|---|---:|---:|---:|---:|
| Chômage moyen | 4,68 % | 4,50 % | 4,84 % | 4,18 % |
| Pic de chômage | 15,99 % | 12,03 % | 22,29 % | 14,81 % |
| Inflation annualisée | 4,46 % | 4,97 % | 4,83 % | 4,74 % |
| Croissance réelle par ménage | −0,23 %/an | +0,09 %/an | +0,57 %/an | +1,55 %/an |
| Demande alimentaire intérieure non servie | 38,59 % | 41,52 % | 8,81 % | 10,52 % |
| Demande de biens intérieure non servie | 62,81 % | 73,05 % | 27,59 % | 28,95 % |
| Mois sans capacité de crédit | 30 | 6 | 16 | 28 |

**L'amélioration de certaines médianes ne signifie pas une stabilisation générale.** Le
chômage moyen s'améliore dans 5 régions du monde et se dégrade dans 4. La pire crise atteint
69,23 % de chômage après correction, contre 57,86 % avant ; la région la plus affectée
présente 22,40 % de chômage en moyenne sur les 400 mois. La demande intérieure non servie
augmente dans les trois économies de base et dans six des neuf régions du monde. Le nombre
de mois sans capacité de crédit augmente dans huit régions du monde sur neuf.

Aucune des 24 trajectoires régionales avant/après ne s'interrompt. Aucun relevé non fini,
ménage en trésorerie inférieure à −0,01 ou demande non servie négative n'est observé pendant
ces périodes. Les écarts comptables maximaux, sur l'ensemble des observations avant/après,
sont de **0,0000659 u.m.** pour la monnaie, **0,00000109** pour le rapprochement des prêts et
**0,000000440** pour celui de la dette publique. Ces écarts en flottants sont mesurés, pas
remplacés par des zéros.

Les données complètes et le calcul des agrégats sont dans
`results/audit_economique_20261112/mesures_avant/`, `mesures_apres/`, `comparaison.py`,
`comparaison.json` et `comparaison.md`. Trois graines par scénario restent un échantillon
limité. Les corrections retirent des transactions ou ressources fictives ; leur validation
porte d'abord sur la cohérence du mécanisme, pas sur une garantie de croissance.

### Séparation des corrections : monde de graine 3001

Pour comprendre les dégradations les plus fortes, trois copies de la version d'avant ont
reçu chacune un seul groupe de corrections, avec les mêmes réglages et 400 mois observés.
Les chiffres ci-dessous sont les chômages moyens, calculés séparément pour chaque région.

| Sources | Nord | Sud | Littoral |
|---|---:|---:|---:|
| Avant | 7,33 % | 4,20 % | 4,21 % |
| Matières, exploration et commandes vides seulement | 7,33 % | 4,20 % | 4,21 % |
| Capital bancaire et saisies seulement | 5,03 % | 8,46 % | 7,51 % |
| Comptabilité des stocks seulement | 3,79 % | 5,72 % | 8,20 % |
| Toutes les corrections combinées | 16,00 % | 7,01 % | 22,40 % |

Les relevés de la variante physique sont identiques à ceux d'avant dans cette expérience :
les nouveaux cas limites corrigés n'y sont pas rencontrés. Les fortes dégradations du Nord
et du Littoral apparaissent dans la correction combinée, sans être reproduites
dans cette ampleur par l'une des familles seule. Cela montre une interaction des boucles
de décision ; cette unique graine ne permet pas d'en attribuer la cause à un paramètre
précis ni à une paire particulière de corrections. Les contraintes physiques peuvent
s'activer dans une trajectoire déjà modifiée par les autres corrections, même si elles
n'ont aucun effet isolé ici. Le PIB corrigé modifie les contraintes publiques, le financement et la recherche,
et le capital bancaire corrigé change les recapitalisations et le partage du résultat.
Ces mécanismes doivent être étudiés ensemble avant une tentative de stabilisation.

Le script `results/audit_economique_20261112/ablation.py` et les copies de sources
`variantes/` conservent cette expérience. Aucun de ces essais ne modifie le moteur actif.

## Limites économiques et pistes suivantes

Ces corrections rendent le circuit plus cohérent. Elles ne démontrent pas que le modèle
reproduit une économie réelle ou que son équilibre est souhaitable.

- **Pénuries et allocation du travail.** Un faible chômage agrégé peut coexister avec une
  forte demande de biens non servie. Il faut mesurer par secteur les postes proposés,
  candidatures refusées, métiers manquants et capacités inutilisées avant de modifier les
  salaires ou les vitesses d'embauche. Une hausse générale du plafond d'embauche a déjà
  dégradé le chômage dans les expériences du lot U.
- **Croissance et ressources.** La connaissance augmente surtout la productivité de
  l'alimentation et des biens ; les services restent simplifiés. L'amélioration suivante
  devrait distinguer capacité physique des services, progrès technique et financement de
  la recherche, avec des expériences séparées. Augmenter seulement l'efficacité de la
  recherche peut accélérer l'épuisement des ressources.
- **Concentration et faillites.** Mesurer la part du premier producteur uniquement quand il
  vend, puis distinguer monopole, secteur vide et producteurs sans salariés. Tester chaque
  mécanisme d'entrée ou de rachat séparément avant de les cumuler.
- **Calendrier des comptes régionaux.** Les marchandises mises à quai quittent les stocks
  avant le relevé régional et leurs recettes arrivent dans les comptes du mois suivant.
  Une extension des stocks aux quais doit être accompagnée d'un traitement cohérent des
  ventes, retours et exportations au règlement. Ce décalage préexistant reste à traiter,
  comme celui des ventes tardives de capacité des centrales.
- **Contraintes budgétaires.** L'impôt plafonné par la trésorerie est activé dans « Tout
  activer », mais reste optionnel dans les réglages de base. La pondération nulle des
  titres publics est un scénario réglementaire, pas la preuve que leur risque économique
  est nul. Les comptes de dépôt individuels en partie double restent un chantier distinct.
- **Validation empirique.** Comparer distribution des tailles d'entreprise, durée des crises,
  concentration, inflation et mobilité du travail à des séries de référence. Les résultats
  actuels ne sont pas des prévisions ni des recommandations de politique réelle.
