# Changelog

## [Non publié] — kit natif leanc (chantier 1, 2026-10-08)

- `ToolchainManager.installer(avec_natif=True)` : extrait le kit natif
  depuis l'archive officielle déjà en cache — `bin/leanc`, clang 22
  embarqué, `ld.lld`, archives statiques (`libLean.a`, `libStd.a`,
  `libLake.a`, ...), en-têtes C (`include/clang`, `include/lean`),
  objets glibc. `lean --c` + `leanc` produisent un binaire natif lié
  statiquement (hors libc système) — Hello World validé de bout en bout.
- Kit natif : 89 membres, 575 228 874 octets mesurés (+575 Mo sur
  l'installation). Extraction additive : `installer(avec_natif=True)`
  sur une installation minimale existante n'ajoute que le kit
  (pas de re-téléchargement) ; marqueur `.valide` enrichi (`natif`).
- `extract.py` : `MOTIFS_NATIFS` + recréation des liens symboliques
  (garde anti-traversal) ; `validate.valider_natif()`.
- Correctif : les 3 motifs `lib/lean/Init.olean[.private|.server]`
  manquaient dans `MOTIFS_MINIMAUX` (le manifeste les listait depuis
  f35dd52) — sans eux, une installation fraîche ne peut pas élaborer.
- 15 nouveaux tests unitaires (réseau mocké).

## [Non publié] — extensions Lean/Std à la demande (chantier 3, 2026-10-08)

- `phi lean --init --extension std` : installe la bibliothèque Std
  (`import Std`, +290 Mo, 1 467 fichiers) depuis l'archive officielle
  déjà en cache — aucun téléchargement supplémentaire.
- `phi lean --init --extension lean` : installe la bibliothèque Lean
  (`import Lean`, métaprogrammation + tactiques custom, +1,2 Go,
  5 121 fichiers — inclut Std par fermeture d'imports).
- Mesures exactes et preuves d'élagage dans `TOOLCHAIN_MINI_SPEC.md` §9 :
  3 variantes olean requises, aucun sous-arbre élagable, pas de mini
  Lean utile (`Lean.Elab.Tactic` seul = 963 Mo).
- `ToolchainManager.installer_extension()` : idempotent (marqueur
  `.valide-ext-<nom>`), validation par élaboration réelle.
- 16 nouveaux tests unitaires (réseau mocké) — 56 tests OK.

## [0.17.0] — 2026-10-06 — Double licence

- **Double licence** : GPL v3 (usage libre et open source) OU Licence
  Commerciale (usage dans logiciel propriétaire, à partir de 200 $/an).
  Voir `LICENSE`, `LICENSE-COMMERCIAL.md`.
- Nouveau `CONTRIBUTING.md` : les contributeurs acceptent la double licence.
- *Les versions 0.16.1 et antérieures restent sous licence MIT.*

## [0.16.1] — 2026-10-06 — Correctif changelog

- Ajout de l'entrée changelog **v0.16.0** manquante dans le README.
- Aucun changement de code depuis 0.16.0.

## [0.16.0] — 2026-10-06 — Modules v110 + phi telemetry

- Nouveaux modules v110 : `sentinelle`, `markov`, `phiwrite`, `carnet`,
  `journal`. Nouvelles commandes : `phi markov`, `phi write`, `phi sentinelle`.
- Nouveau `phi telemetry` : lit l'état interne de l'exporteur Lean.
- `mission.py` : justifications pour les 6 nouveaux modules.

## [0.15.0] — 2026-10-03 — Le Témoin : radar + sismique

- `phi radar --avant S1 --apres S2` : vue du mouvement structurel entre deux
  états — 5 détecteurs mécaniques (énoncé modifié, preuve effondrée, nouvel
  axiome, définition dupliquée, nouveau sorry), poids de saillance fixes,
  faits uniquement, jamais de verdict.
- `phi sismique --depuis <date>` : mémoire des rythmes via l'historique git
  (fréquence, mécanisme, répliques, profondeur, rayon, magnitude, épicentre).
- `phi consigner` / `phi registre` : consigne les décisions humaines de veto.
- Garde constitutionnelle `test_garde_non_prescriptif.py` : l'instrument
  montre, il ne décide jamais.
- `mission.py` : justifications ajoutées pour les 3 modules.

## [0.14.2] — 2026-10-03 — README : historique des versions complété

Correctif d'emballage uniquement — aucun changement de code depuis 0.14.1.
- README : ajout de l'entrée **v0.14.1 (Parseur Lean autonome)** manquante
  dans la section « Historique des Versions » (la 0.14.1 avait été publiée
  avec un README dont l'historique s'arrêtait à v0.13.0 ; la description
  PyPI étant immuable, cette 0.14.2 la remplace à l'affichage).
- Note d'honnêteté : PyPI sert désormais la 0.14.2.

## [0.14.1] — 2026-10-03 — Durcissement extraction + parseur Lean autonome

Portage ordonné par Tomy des trois chantiers vérifiés sur la branche de
travail : (1) la veille ne devient plus aveugle, (2) verrouillage « zéro
tree-sitter silencieux », (3) parseur Lean 4 proprietaire (autonomie stricte).

**Cause racine** : la grammaire `tree-sitter-lean` (paquet externe, hors de
notre contrôle) confond les barres `|expr|` (valeur absolue / norme) **en
position de type de retour** avec une alternative de filtrage `|` → nœud
ERROR qui avale toutes les déclarations suivantes, **silencieusement**
(mesuré : 412 déclarations manquées dans 60/176 fichiers d'un dépôt Lean
réel). On ne répare pas la grammaire externe : on la neutralise, puis on
la remplace.

**1. La veille ne devient plus aveugle** : l'existence des symboles vient
désormais d'un extracteur proprietaire ; toute divergence ≥ 1 symbole entre
extracteurs déclenche une section console bruyante « ⚠️ EXTRACTION
DÉGRADÉE » + clé JSON `extraction_repli` (le verdict ne bascule pas : la
comparaison reste valide). Le durcissement « nouveau symbole troué »
(#236) couvre désormais aussi les zones ex-aveugles.

**2. Verrouillage « zéro tree-sitter silencieux »** : audit exhaustif des
sites d'appel tree-sitter — chacun est verrouillé (extracteur proprietaire
source de vérité, ou échec bruyant explicite). `REGISTRE_TREE_SITTER.md`
(registre des sites + garantie exacte + limites assumées). Garde
automatique : `tests/test_zero_treesitter_silencieux.py` fait échouer tout
nouveau site non enregistré.

**3. Parseur Lean 4 autonome** (`phi_complexity/parseur_autonome.py`,
~830 lignes, stdlib uniquement, zéro dépendance externe) : lexer +
descente récursive sur les en-têtes Lean 4 + délimitation exacte des corps ;
tout construit non reconnu → avertissement avec ligne, jamais de silence.
Mesuré sur 248 fichiers : 5377 déclarations, 0 perdue, les ~570 manquées
par tree-sitter toutes trouvées. Source unique sur tout le chemin Lean
(`AnalyseurLean` moteur `"autonome"` par défaut) ; tree-sitter relégué en
vérification croisée optionnelle.

**Changement de contrat assumé** : Lean ne dépend plus de tree-sitter —
l'incident du 2026-09-30 (fausse dégradation après effacement du pack)
ne peut plus se reproduire pour Lean. Les tests au contrat obsolète ont
été mis à jour (`test_lean.py`, `test_durcissement.py`,
`test_durcissement_20261001.py`) ; le chemin « arrière-plan perdu »
reste couvert via un langage encore dépendant de tree-sitter.

**Écarts de portage assumés** : les modules v110 absents du public
(`protocole_lean`, `dualite`, `synthese_locale`, `visualisation_ast`,
`godel_fourier`, `indexeur_lemmes`) n'ont pas d'équivalent public — leurs
verrouillages ne sont pas portés (documenté dans `REGISTRE_TREE_SITTER.md`).

## [Unreleased] — Audit 2026-10-01 : fail-loud complété + garde anti-divergence

Chantier ordonné par Tomy (2026-10-01) : auditer phi-complexity, tests
unitaires du durcissement, garde mécanique contre la divergence de la
3e directive (développer phi-complexity) envers la directive unique
(démonstration Navier-Stokes Clay).

**Audit — trouvailles :**
- `test_sondes.py::TestParsingTable::test_h7_cout_et_tag` ÉCHOUÉ :
  attente périmée (H7 reclassifiée NOMMÉ → CONDITIONNELLE au ch.45,
  2026-09-30). Le parseur avait raison, le test avait tort — attente
  mise à jour. Angle mort noté : ces tests lisent le registre vivant,
  toute évolution légitime du registre les casse (échec bruyant,
  pas silencieux — acceptable).
- `phi chemins` restait SILENCIEUX quand l'arrière-plan manquait
  (là où `phi index` prévenait) — même classe de silence que
  l'incident 3531 → 33 symboles du 2026-09-30. **Corrigé** : `cli.py`
  (`_executer_chemins`) surface désormais l'avertissement
  « fichiers SANS analyseur fonctionnel » (stdout en console, stderr
  en mode JSON pour ne pas corrompre le document). Contrat de sortie
  inchangé (commande informative, exit 0).
- Vérifié sans anomalie : `veille.py` (verdict INSTRUMENT DÉGRADÉ),
  `_executer_snapshot` (refus baseline sans --force, exit 3),
  `_executer_veille` (exit 3/2/0), shim `~/workspace/bin/phi`
  (venv prioritaire, repli python3).

**Tests ajoutés** (`tests/test_durcissement_20261001.py`, 11 tests) :
exit 3 + bandeau INSTRUMENT DÉGRADÉ quand tree-sitter manque (jamais
de ✅), refus baseline sans --force / acceptée avec --force,
avertissement `chemins` (console + JSON), shim venv vs repli —
comportements vérifiés empiriquement avant écriture.

**Garde anti-divergence** (`phi_complexity/mission.py` +
`tests/test_mission.py`, 8 tests) : registre « but de mission » —
chaque module porte `but` (phrase) + `sert` (vocabulaire contrôlé :
les 5 sorrys, `tous`, `instrument`). `mission.valider()` liste les
problèmes ; le test échoue si un module est sans justification, si
une justification est vide/hors vocabulaire, ou si une entrée est
orpheline.

Suite complète : **386 passed** (367 avant : 366 verts + 1 périmé
corrigé ; +19 nouveaux).

## [Unreleased] — Ancrage entropique : la lentille rejoint la chaîne des sondes + `phi ou-aller`

La lentille entropique est ancrée dans la chaîne des sondes par **import
direct** (jamais de subprocess) :

- **`phi sonde <mécanisme> --format json`** expose désormais une section
  `entropie` par **nœud** du DAG (h_initiale, trace ΔH par réfutation,
  h_finale, booleanization_cost en bits + ratio en étalons-or,
  routes_admissibles) et la section complète au niveau mécanisme. Mode
  console inchangé (rapide, sans lentille).
- **Doubles verdicts rattachés au niveau nœud** : une obstruction à double
  verdict (ex. ch.139 — version inconditionnelle RÉFUTÉE, version
  conditionnelle DÉMONTRÉE sous `DissipationDomination`) n'est plus laissée
  en Sonde B seule avec un nœud NON-ATTAQUÉ : le nœud porte explicitement
  les deux versions (`double_statut`), exhibées aussi en `doubles_verdicts`.
- **Nouveau : `phi ou-aller`** (console + JSON) — mesures typées par
  mécanisme (6 sorrys du Master + hypothèses §1 : ΔH totale, coût de
  booléanisation en bits et en étalons-or, routes admissibles, statut,
  double verdict) **triées par coût de booléanisation décroissant**. C'est
  une liste d'attention ordonnée — PAS un score, PAS une P(A) : l'instrument
  montre où regarder (là où le typage porte le plus de bits), il ne décide
  pas ; le veto de Tomy tranche. En tête : `BKM_criterion` (≈ 11.2 étalons).

Interdiction formelle (aucun score A/B, aucune P(A)) testée par balayage
des **clés** du JSON — pas de la prose (le texte de l'interdiction contient
« P(A) » en toutes lettres). Le garde-fort « aucun flottant » des sondes
évolue : flottants interdits partout **sauf** sous les sections `entropie`
(bits d'attention légitimes). Non-garanties : `ANCRAGE_ENTROPIE_20260930.md`.
22 tests neufs, suite complète verte, `phi veille` STABLE 3564→3564
bit-identique. VERSION non bumpée (0.11.0).

## [Unreleased] — Lentille entropique : étalons « Bit Générateur » (opt-in)

La sous-commande **`phi entropie <mécanisme>`** (lecture seule) gagne trois
quantités de référence issues de l'étude « Bit Générateur » de Tomy
(arithmétique vérifiée indépendamment le 2026-09-30) :

- **`booleanization_cost`** : l'entropie perdue si l'on effondrait la partition
  typée DÉMONTRÉ/CONDITIONNEL/RÉFUTÉ/NON-ATTAQUÉ en booléen
  ({DÉMONTRÉ, CONDITIONNEL} → « tient » contre le reste — l'écrasement
  cargo-cult refusé). Étalon-or documenté : **ΔH = 1 − H_φ ≈ 0.0405812718 bits** ;
  la sortie donne le coût, sa borne EFT, et le ratio en étalons-or
  (ex. `BKM_criterion` : 0.454944 bits ≈ 11.2 étalons).
- **`h_top = log₂φ ≈ 0.6942419136 bit/symbole`** (Perron-Frobenius sur
  [[1,1],[1,0]]) : taux de croissance de référence des routes admissibles.
- **Verrou combinatoire** : `routes_golden_shift(k) = F_{k+2}` exact
  (N(10) = F_12 = 144) + `compter_routes_dag` (récurrence linéaire exacte,
  nœuds RÉFUTÉ retirés, cycle → erreur documentée) + modèle de référence en
  couches (rangs d'attaque) par mécanisme — la réponse instrumentale à R4
  (explosion combinatoire guidée, jamais naïve).

Non-garantie écrite : ces étalons structurent la mesure de l'instrument, ils
ne prouvent rien sur les mathématiques. Doc : `ENTROPIE_SONDEB_20260930.md`.
15 tests neufs (39 au total sur le module), suite complète 294 verts,
`phi veille` STABLE 3564→3564 bit-identique. VERSION non bumpée (0.11.0).

## [Unreleased] — EFT bayésien + traces d'oracle (opt-in)

L'arithmétique de la couche bayésienne cesse de perdre l'erreur en
silence : derrière le flag opt-in `--exact` / `PHI_EFT=1`, les postérieurs
sont calculés en **double-double** (TwoSum/TwoProd de Dekker 1971) et
rendus comme **(valeur, borne d'erreur certifiée)** au lieu d'un flottant
nu. Le transfert honnête d'Ozaki et al. (2012) vers phi n'est PAS la
vitesse (mesuré : ×3.9 sur `inferer`, ×1.1 sur `chemins`) mais
l'EXACTITUDE. La commande **`phi oracle <cible> [--symbole NOM]
[--exact]`** exhibe la chaîne de raisonnement (prior → évidences →
postérieur → action) dans les deux modes ; seul `--exact` ajoute la
borne certifiée. **Le mode défaut (flottant) est bit-identique à la
v0.11.0** : `phi veille` reste STABLE, les JSON `chemins`/`infer` par
défaut ne portent aucune clé EFT (preuve : voir EFT_BAYES_20260930.md).

### Added
- **`phi_complexity/eft.py`** : TwoSum/FastTwoSum (Knuth), Split/TwoProd
  (Dekker, chemin FMA si Python ≥ 3.13 sinon 17-flop), dd_add/dd_mul/dd_div,
  neumaier_sum, `AccumulateurCertifie` (compteur d'opérations honnête,
  borne B = 16·n·u²·M), `CroyanceCertifiee`, `eft_active()` (PHI_EFT).
  Invariants exacts testés contre `fractions.Fraction`.
- **`phi_complexity/oracle.py`** : `TraceurOracle`, `TraceEntree`
  (horodatage, moteur, cible, symbole, prior, termes d'évidence,
  postérieur hi/lo, borne certifiée, décision, mode, limites), rendus
  console (prior → évidences → postérieur → action) et JSON.
- **`bayes.py`** : `MoteurInferenceBayesienne(..., exact=None,
  traceur=None, cible="")`, `_calculer_posteriors_eft()` (même formule,
  produits two_prod, norme double-double, division dd),
  `DiagnosticBayesien.mode` / `.certifie` (défauts inertes).
- **`croyances.py`** : `chemins_croyants(..., exact=None, traceur=None)`,
  `_posterior_eft()`, `CroyanceSymbole.mode` / `.certifie` ; clés JSON
  EFT (`posterior_hi/lo`, `borne_erreur_certifiee`,
  `nb_operations_eft`, `mode_arithmetique`) **uniquement en mode exact**.
- **CLI** : `phi oracle <cible> [--symbole NOM] [--format console|json]
  [--exact] [--top N]` (fichier → moteur bayes, dossier → chemins
  croyants) ; `phi infer --exact`, `phi chemins --exact`.
- **`__init__.py`** : `diagnostic_bayesien(..., exact=None, traceur=None)`.
- **Tests** : 27 nouveaux (`tests/test_eft.py`) — invariants exacts en
  rationnel, cancellation catastrophique, bornes couvrant l'erreur
  exacte, non-régression des classements flottant vs EFT, traces deux
  modes. Total : 219 tests verts.

### Limites (lire avant de citer une borne)
- La borne certifiée couvre l'arithmétique EFT **à entrées fixées** ;
  elle ne couvre PAS l'erreur des capteurs amont (sigmoïdes, log1p).
- La division double-double n'est pas exacte (erreur O(u²), couverte).
- Hypothèses : arrondi au plus près, pas d'over/underflow (croyances
  dans [0,1] ou ~[-5,15] par construction).
- Un postérieur certifié reste une **heuristique** (LIMITE_HONNETETE) :
  l'exactitude arithmétique ne rend pas le modèle vrai.
- Ralentissement mesuré : ×3.9 (`inferer`), ×1.1 (`chemins`, dominé par
  l'indexation). Pas de FMA avant Python 3.13 (chemin Dekker actif).

## [Unreleased] — Sondes A/B : tracer l'inconditionnel (opt-in)

La commande **`phi sonde <mécanisme> [--format json] [--exact]`** interroge
le registre vivant des hypothèses (lecture seule) et exhibe, pour un sorry
du Master, une hypothèse H*, un chantier ou un symbole nommé : la **sonde A**
(route vers l'inconditionnel positif — DAG fini dans l'ordre d'attaque §4),
la **sonde B** (inconditionnel négatif — obstructions §5 et verdicts
RÉFUTÉ/ÉLIMINÉ du journal §12), et la **TERRA INCOGNITA** (nœuds du DAG
jamais attaqués : aucun chantier, aucun verdict — la carte montre le
territoire inexploré, pas qu'il contient un passage). Chaque nœud porte sa
zone DÉMONTRÉ | CONDITIONNEL | RÉFUTÉ | NON-ATTAQUÉ, son statut typé, son
tag, son coût et son ancrage.

### Added
- **`phi_complexity/sondes.py`** : parseur défensif du registre
  (table §1, transitions §1bis, coûts/tags §4, obstructions §5, journal §12
  avec récolte d'identifiants ; compteur d'entrées non parsées affiché),
  `sonder()` (pôles A/B), `zone_sonde()`, rendus console/JSON,
  **INTERDICTION FORMELLE** : aucun score A/B, aucune P(A) — ni dans la
  sortie, ni dans le code (garde mécanique par balayage des clés JSON et
  exclusion de tout flottant).
- **`tests/test_sondes.py`** : 36 tests (parsing contre faits connus du
  registre, défense anti-malformé, pôles A/B, trois zones, interdiction,
  double passe `--exact`, mécanisme inconnu).
- **`SONDES_AB_20260930.md`** : garanties ET non-garanties en toutes lettres.

### Limites
- La sonde montre la ROUTE, pas sa praticabilité ; la terra incognita
  montre où l'on n'est pas allé, pas qu'il y a un passage.
- Le rattachement sorry → trous est une table codée en dur
  (`SORRY_VERS_TROU`) : à maintenir si le Master renomme un sorry.
- La liste NON-ATTAQUÉ mêle hypothèses et objets nommés du journal.

## [0.11.0] — La Veille

L'instrument ne photographie plus seulement l'édifice : il **compare deux
photos** et dit si on l'a déconstruit par manque de prudence. `phi snapshot`
fige l'état structurel (carte croyante + arêtes d'imports résolues) dans une
enveloppe datée, versionnée et signée (md5) ; `phi veille` recalcule la carte
et rend un verdict typé — `STABLE` ou `DÉGRADATION DÉTECTÉE` — jamais un
booléen seul sans le détail.

### Added
- **`phi_complexity/veille.py`** : `prendre_snapshot()` / `ecrire_snapshot()`
  / `charger_reference()` / `comparer()` / `veille_console()` + CLI
  `phi snapshot <dossier> --out ref.json` et
  `phi veille <dossier> --ref ref.json [--format json]`.
- **Diff** : `nouveaux_trous` (sorry apparu là où il n'y en avait pas),
  `trous_repares` (informatif positif), `symboles_perdus` (alerte forte :
  théorème/fonction disparu), `symboles_gagnes` (informatif),
  `renommage_probable` (perdu + gagné, même fichier, ≤ 10 lignes d'écart —
  heuristique explicite, pas compté comme perte), `aretes_cassees`
  (imports résolus dans la ref, non résolus aujourd'hui),
  `derive_couplage` (blast radius max par fichier),
  `derive_posterieure` (top-12 des variations de postérieur).
- **Règles d'honnêteté** : référence d'une autre version de phi →
  avertissement explicite (comparaison quand même, jamais silencieuse) ;
  comparaison par chemins relatifs (le projet peut avoir déménagé) ;
  les dérives informatives ne font jamais basculer le verdict.
- **Codes de sortie** : `phi veille` → 0 = STABLE, 2 = DÉGRADATION
  DÉTECTÉE (utilisable comme porte en fin de chantier / hook).

### Tests
- 10 nouveaux tests (`tests/test_veille.py`) : enveloppe, STABLE,
  nouveau trou Lean, symbole perdu, renommage probable, arête cassée,
  référence invalide/périmée, trou réparé (ne dégrade pas), codes CLI.

### Limites
- Le diff est structurel, pas sémantique : une preuve réécrite mais
  équivalente apparaît comme dérive postérieure, pas comme dégradation.
- Le seuil de renommage (10 lignes) est une heuristique ; un déplacement
  massif + renommage simultanés sera vu comme perte + gain.
- `aretes_cassees` ne voit que les imports résolus sur disque (Python,
  Lean) — les imports externes (Mathlib…) sont hors périmètre, comme en
  v0.10.0.

## [0.10.0] — Les Chemins Croyants

La carte statique devient une carte *croyante* : chaque symbole reçoit un
score de priorisation d'inspiration bayésienne —
**prior structurel** (métriques phi existantes) × **vraisemblance**
(évidence mesurée) → **postérieur**. Application tueuse :
« quel `sorry` attaquer en premier ».

### Added
- **`phi_complexity/croyances.py`** : `chemins_croyants(dossier)` +
  `chemins_console()` + CLI `phi chemins <dossier> [--format json] [--top N]`.
- **Formule exhibée** (pas de mécanisme caché) :
  `log_posterieur = log(1 + complexite) + W_sorry·1[sorry]`
  `+ W_aval·log(1 + dependants_aval) + W_churn·log(1 + commits_30j)` —
  chaque terme est écrit dans le JSON (`termes`), chaque poids vit dans
  UN seul endroit (`POIDS_CROYANCES` : sorry=3.0, aval=1.0, churn=0.5,
  tests=0.0 — constantes de jugement, pas paramètres estimés).
- **Capteurs** : graphe d'imports fichier→fichier (Python via `ast`,
  Lean via lignes `import`, résolus sur disque ; dépendants transitifs
  bornés à profondeur 3 = « blast radius ») ; churn git (30 j, optionnel) ;
  `sorry` Lean — **repli grep honnête** (heuristique lexicale à états :
  commentaires `--`, `/- -/` imbriqués ET littéraux de chaîne retirés —
  durci après dogfood : 2 faux positifs `sorry`-dans-chaîne tués, dont le
  string gap `\` multi-ligne), marqué `capteur_degrade: true` (le flag
  natif `contient_sorry` arrive en v0.9.0 ; bascule automatique si une
  version l'expose).
- **Dégradation honnête** : capteur indisponible → facteur neutre (0 en
  log) + `capteurs_actifs` listant ce qui a *vraiment* mesuré.
- **Tests** : 16 nouveaux (`tests/test_croyances.py`) — termes exacts,
  neutralité hors git, chaîne d'imports a→b→c, grep sorry (commentaires
  exclus), CLI JSON. **180 passed** au total.

### Limites (en toutes lettres, aussi dans le JSON `limites`)
- Classement HEURISTIQUE, PAS un modèle probabiliste calibré : le
  « postérieur » est un **score de priorisation**, PAS une probabilité
  d'échec ni une prédiction de bug.
- Le capteur `tests` n'est pas branché (terme neutre, réservé).
- Extraction d'imports : Python + Lean uniquement ; imports non résolus
  listés, jamais inventés en arêtes.

## [0.8.0] — La Vitesse

Réorientation sur données mesurées : cProfile (100k lignes, 20k fonctions,
6.7 s) montrait **55 millions d'appels** — l'arbre AST était parcouru ~7 fois
(`_injecter_parents` 4.9 s, `_compter_elements_globaux` 3.3 s, `_analyser_fonctions`
+ 2 walks *par fonction*, `_appliquer_regles_souveraines` 5.6 s, 17M d'`isinstance`).
Tout est linéaire mais la constante tuait. Le goulot n'était pas là où le brief
initial le supposait (les « 21 métriques par fonction ») : c'était le nombre de
traversées. Le dogfood (`phi index` sur `langs/`) désignait déjà
`_profondeur_imbrication` comme oudjat du fichier — le profiler confirme.

### Changed (correctif prioritaire : fusion mono-passe)
- **`langs/python_native.py` réécrit autour de `_passe_unique()`** : UN SEUL
  parcours itératif de l'AST (pile d'itérateurs, pas de récursion, dispatch
  par `type()` + dict au lieu de ~8 `isinstance` par nœud) qui fait tout
  pendant la visite :
  - chaque nœud incrémente le compteur du cadre de fonction courant →
    `complexite` (nb de nœuds du sous-arbre) devient **gratuite**, dans les
    deux phases ;
  - les 4 règles souveraines (LILITH/RAII/FIBONACCI/HERMÉTICITÉ) évaluées
    inline (mode complet) ;
  - profondeur de boucles par compteurs de pile — **plus d'injection de
    parents, plus de remontée, plus de walks par fonction**.
- **Non-régression prouvée** : différentiel v0.7.0 (multi-passes) vs v0.8.0
  (mono-passe) sur 27 fichiers (tout le package + cas tordus : fonctions
  imbriquées, `async def`, boucles profondes, `open()` variés, 20k fonctions
  synthétiques) — **0 écart** sur toutes les métriques et annotations.
  Quirks historiques préservés et documentés (`AsyncFunctionDef` exclu des
  ancêtres comptables LILITH ; fonctions imbriquées comptées dans l'englobante).

### Added
- **Deux phases** : `analyser(complet=True)` (défaut, comportement historique),
  `analyser(complet=False)` = phase 1 — index seul (noms, lignes, nb d'args,
  `complexite` réelle gratuite). Sentinelles documentées pour les champs non
  calculés : `profondeur_max=0`, `distance_fib=0.0`, `phi_ratio=1.0`.
- **CLI `phi index --rapide`** : passe `complet=False` à la carte (symboles
  sans audit par fichier). En phase 1, la carte JSON porte `mode="rapide"`
  (clé présente *uniquement* en phase 1) et `metriques_calculees=false`.
- **Parallélisme dossier** : `indexer_projet(..., parallele=True)` (défaut)
  — `ProcessPoolExecutor` borné à `min(8, cpu_count, nb fichiers)`, repli
  séquentiel honnête. Cache `_MTIMES` cléé `(chemin, complet)` : une phase 1
  ne fait jamais croire qu'une analyse complète est en cache.

### Garanties de schéma
- En mode complet, la sortie JSON de `carte_projet()` est **exactement**
  celle de v0.7.0 (aucune clé ajoutée) ; `tests/test_carte.py::CLES_STABLES`
  l'impose.

### Mesuré
- Bench synthétique 100k lignes / 20k fonctions (VM) : `ast.parse`
  incompressible 2.03 s ; analyse **10.51 s → 3.17 s** (×3.3 ; ×7.4 sur la
  partie parcours seule, hors parse).
- Projet Lean réel (166 fichiers, 2863 symboles) : `phi index`
  8.0 s → complet **5.9 s** (parallélisme) → `--rapide` **2.5 s** ;
  inventaire identique (301 collisions, 108 non-supportés, JSON valide).
- Tests : **164 passed** (dont 19 nouveaux : phases, rapidité, parallélisme,
  CLI `--rapide`, non-régression du schéma complet).

### Limites honnêtes
- La cible « ÷5 » n'est pas atteinte en total (×3.3) : `ast.parse` (~20 %) et
  `ast.iter_child_nodes` (~30 %) sont structurels. La phase 1 coûte la même
  passe que le complet (les règles sont une fraction du coût) : le gain
  `--rapide` vient surtout de l'audit par fichier sauté dans `phi index`.
- Fusion appliquée à l'analyseur Python (le point chaud mesuré) ; les
  analyseurs Lean/tree-sitter gardent leur passe existante.

## [0.7.0] — Le Lecteur Lean

Analyseur Lean 4 dédié (`langs/lean.py`, enregistré prioritaire sur le
générique dans `langs/registry.py`). Motif : le générique extrayait
2781 symboles du projet Lean mais 292 collisions quasi-toutes de bruit
(`<anonyme>` ×16, lieurs locaux `omega`/`xi`/`term_id`…).

### Added
- **Analyseur Lean dédié** : n'extraire que les déclarations de tête
  nommées (`theorem`/`lemma`/`def`/`abbrev`/`instance`/`structure`/`class`/
  `inductive`) ; lieurs, identifiants internes, définitions anonymes
  (`_…`) et instances anonymes ignorés.
- **Proxy de complexité explicite** : `complexite = nb_lignes + nb_tactiques`
  (pas de tactique = enfants nommés directs des blocs `by`) — documenté
  dans le docstring avec sa justification.
- **Signal honnête** : sans le pack ou sans la grammaire,
  `ImportError` « grammaire lean indisponible » (jamais de repli
  silencieux vers le générique) ; raison `non_supportes` dédiée.
- **Tests** (`tests/test_lean.py`, 8 tests) : oracle grep sur
  `Part16f_HilbertTruncatedL2.lean` (22/22 déclarations, 0 bruit).

### Mesuré (dogfood `phi index` sur le projet Lean)
- Avant : 2781 symboles, 292 collisions (~tout du bruit).
- Après : 2854 symboles, 301 collisions, **0 bruit** — les collisions
  restantes sont de vraies duplications inter-fichiers
  (`BKM_criterion` dans Master et Master_Euclidean, `Fterm` ×3…).
  Oudjat suprême : `antisym_part_eq`, c=691 (contre 9559 en
  comptage de nœuds brut — le proxy est lisible).

### Limites connues
- `import A.B` mal parsé par la grammaire (nœud ERROR) : `nb_imports`
  reste à 0, documenté.
- `lemma`/`class` normalisés par la grammaire en `theorem`/`structure` ;
  le vrai mot-clé est relu dans le texte.

## [0.6.1] — Durcissement de l'index

Correctifs issus d'un dogfood honnêtement négatif (0 symbole extrait sur un
projet Lean, `.lake/` pollué, silences trompeurs) : un instrument de
confiance ne tait jamais ses angles morts.

### Fixed
- **Exclusions par défaut** (`editeur/indexeur.py`, `EXCLUSIONS_DEFAUT`) :
  `.lake/`, `_build/`, `.git/`, `__pycache__/`, `node_modules/`, `target/`,
  `.venv/`, `venv/`, `*.egg-info/` ne sont plus indexés. L'Oudjat suprême
  ne sera plus un script de dépendance. CLI : `--exclude DIRS` (s'ajoute
  aux défauts), `--no-exclude` (indexe tout, choix explicite).
- **Rapport « non supporté » par fichier** : `fichiers_non_supportes()`
  liste chaque fichier sans analyseur *fonctionnel* — champ JSON
  `non_supportes: [{fichier, extension, raison}]` (`extension inconnue` ou
  `tree-sitter manquant`), section console dédiée. Fini le faux négatif
  invisible.
- **Backend Tree-sitter vérifié** : `langs/registry.py` expose
  `treesitter_disponible()` / `analyseur_disponible()`. `phi index`
  annonce explicitement (stderr + section AVERTISSEMENTS + champ JSON
  `avertissements`) l'absence de `tree-sitter-language-pack` en listant
  les extensions reconnues mais non analysées — au lieu d'une carte vide
  qui ressemble à un projet vide. L'avertissement CLI part sur stderr
  pour ne jamais polluer le JSON (agents, `jq`).

### Changed
- `carte_projet()` accepte `exclusions` (`None` → défauts, `[]` → aucune)
  et retourne deux nouvelles clés stables : `non_supportes`,
  `avertissements` (rétrocompatible par ajout).
- `indexer_projet()` accepte `exclusions` (rétrocompatible).
- `tests/test_carte.py` : `CLES_STABLES` étendu aux deux nouvelles clés.

### Added
- 12 nouveaux tests (`tests/test_durcissement.py`). Suite complète :
  128 passed, 2 skipped.
- README : sous-section « Exclusions, non-supportés et backend (v0.6.1) ».

## [0.6.0] — La Carte

### Added
- **La Carte du projet** (`phi_complexity/carte.py`, `phi index <dossier>`) :
  l'index des symboles devient une commande de première classe, pour les
  humains (`--format console`) comme pour les agents (`--format json`,
  clés stables, compatible `jq`).
- Par fichier : symboles (`nom`, `ligne`, `complexite`), radiance, statut
  gnostique, Oudjat — construits en une seule passe via
  `editeur.indexeur.indexer_projet` + `auditer()` (garde-fou try/except
  par fichier, aucun plantage).
- **Détection des collisions** : noms de symboles définis dans ≥ 2
  fichiers distincts, avec occurrences (`fichier`, `ligne`, `complexite`).
  A déjà surface en usage réel deux `evaluer_gate` de signatures
  différentes.
- **Oudjat suprême du projet** : symbole de complexité maximale, avec
  son fichier.
- API Python : `carte_projet(dossier, lang=None) -> dict` et
  `carte_console(carte) -> str` (exportés dans `__all__`).
- 14 nouveaux tests (`tests/test_carte.py`). Suite complète : 116 passed,
  2 skipped.

### Notes
- L'éditeur (`phi edit`) est désormais un simple client de l'index ;
  la carte est le produit.
- `indexer_projet()` accepte désormais un paramètre optionnel
  `langage` (rétrocompatible).

## [0.5.0] — L'Éditeur

### Added
- **Éditeur de texte pour la programmation** (`phi_complexity/editeur/`,
  zéro dépendance hors stdlib) : `phi edit <fichier> [--projet DIR] [--lang L]`
  et API Python `editer(fichier, projet=None, lang=None)`. Interface plein-écran
  curses : édition, navigation, sauvegarde, aller à la ligne, undo (200 états).
- **Tampon** (`editeur/tampon.py`) : liste de lignes, curseur, insertion /
  suppression / coupe de ligne, `charger` / `sauvegarder`, drapeau `modifie`,
  pile undo bornée.
- **Indexeur de projet** (`editeur/indexeur.py`) : `{chemin: [Symbole, ...]}`
  depuis `obtenir_analyseur(f).analyser().fonctions` (nom, ligne, complexité,
  langage) ; fichiers supportés uniquement ; cache invalidé par mtime ;
  `rafraichir_fichier(index, chemin)` ; un fichier en échec n'interrompt jamais
  l'indexation.
- **Recherche** (`editeur/recherche.py`) : `rechercher_mot_cle` (texte brut,
  regex optionnelle, insensible à la casse, contexte N lignes, binaires ignorés)
  et `rechercher_fonction` (classement exact > préfixe > sous-chaîne, puis
  complexité décroissante).
- **Panneau phi** (`editeur/panneau_phi.py`) : `auditer_tampon(tampon, chemin)`
  audite le tampon via un fichier temporaire de même extension ; retourne
  `{radiance, statut_gnostique, oudjat, nb_anomalies, annotations}` (top 5) ;
  toute erreur retourne `{"erreur": ...}` sans lever.
- **TUI curses** (`editeur/tui.py`) : barre de statut (fichier, ligne/colonne,
  `●` si modifié, radiance), panneau phi (F4 toggle, F5 audit manuel, auto
  après `Ctrl+S`), recherches `Ctrl+F` (tampon) / `Ctrl+K` (mot-clé projet) /
  `Ctrl+T` (fonction via l'index) avec listes de résultats navigables — `Entrée`
  saute au fichier:ligne, `Échap` ferme.
- 25 nouveaux tests (`tests/test_editeur.py`, sans curses). Suite complète :
  102 passed, 2 skipped.

### Notes
- L'éditeur ouvre n'importe quel fichier texte ; l'audit phi ne s'applique
  qu'aux extensions supportées (sinon le panneau affiche « langage non supporté »).
- `VERSION = "0.5.0"` (core.py + pyproject.toml).

## [0.4.0] — Langage de formules

### Added
- **Langage de formules** (`phi_complexity/formules/`) : évaluez vos propres
  métriques composites et portes logiques de qualité directement en CLI.
  Parseur dérivé du parseur d'expressions GNS-754 (descente récursive, zéro
  dépendance) étendu aux identifiants de métriques, comparaisons
  (`>`, `<`, `>=`, `<=`, `==`, `!=`), logique booléenne (`and`, `or`, `not`)
  et fonctions (`sqrt`, `abs`, `log`, `log2`, `exp`). Constantes `phi`, `pi`,
  `e` natives.
- `phi check cible --formule "100 - lilith_variance/phi - shannon_entropy^2"` :
  affiche la valeur de la formule personnalisée sur chaque fichier audité.
- `phi check cible --gate "radiance >= 75 and nb_anomalies == 0"` : porte
  logique composite — exit 1 si la porte est fermée (remplace et généralise
  `--min-radiance` pour le CI/CD).
- API Python : `evaluer_formule(fichier, expression, lang=None)` et
  `evaluer_gate(fichier, expression, lang=None)`.
- 21 nouveaux tests (`tests/test_formules.py`). Suite complète : 77 passed.

### Notes
- L'environnement d'une formule expose les métriques numériques d'`auditer()`
  (`radiance`, `lilith_variance`, `shannon_entropy`, `phi_ratio`, `zeta_score`,
  `nb_fonctions`, `nb_anomalies`, ...) — un nom inconnu lève `NameError`
  avec la liste des noms disponibles.

## [0.2.0] — Multi-language support

### Added
- **Universal Tree-sitter engine** (`phi_complexity.langs.treesitter_generic`) enabling
  audits of JavaScript, TypeScript, Java, C, C++, C#, Go, Rust, PHP, Ruby, Swift, Kotlin,
  Scala, Lua, Bash, and effectively any language exposed by `tree-sitter-language-pack`.
- New optional dependency group: `pip install phi-complexity[multilang]`.
- `phi_complexity.langs` sub-package: `AnalyseurBase` contract, extension→language
  registry (`obtenir_analyseur`, `langage_pour_extension`, `est_fichier_supporte`).
- `--lang` CLI flag and `lang=` keyword on `auditer()` / `rapport_*()` to force a
  language explicitly (useful for extensionless files or ambiguous extensions).
- `langage` field added to the metrics dictionary and to console/Markdown reports.
- `phi check ./src/` now recursively collects files across ALL supported languages,
  not just `.py`.
- New test suite `tests/test_multilang.py` (skipped automatically if
  `tree-sitter-language-pack` is not installed).

### Changed
- Data model (`MetriqueFonction`, `Annotation`, `ResultatAnalyse`) extracted into a new
  language-agnostic `phi_complexity/modeles.py` module.
- `metriques.py` (Radiance calculation) is now 100% language-agnostic.
- Python analysis logic moved to `phi_complexity/langs/python_native.py`
  (`AnalyseurPython`); `phi_complexity.analyseur.AnalyseurPhi` is kept as a
  backward-compatible alias.

### Notes
- Python auditing remains a **zero-dependency** feature (stdlib `ast` only).
- The RAII/"resource without context manager" rule remains Python-specific for now.

## [0.1.0] — Initial release
- Python-only code quality audit based on Golden Ratio invariants.
