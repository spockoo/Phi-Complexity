# REGISTRE TREE-SITTER — « zéro tree-sitter silencieux »

Tenu à jour par le chantier de verrouillage du 2026-10-03 (mission : faire en
sorte que tree-sitter ne corrompe plus JAMAIS un résultat en silence).
Règle : **chaque site d'appel au parse tree-sitter figure ici avec son
verrouillage**. Tout nouveau site non enregistré fait échouer
`tests/test_zero_treesitter_silencieux.py`.

## Cause racine (rappel)

La grammaire `tree-sitter-lean` (paquet externe `tree-sitter-language-pack`,
hors de notre contrôle) confond les barres `|expr|` (valeur absolue / norme)
**en position de type de retour** avec une alternative de filtrage `|`. Le
nœud ERROR produit avale toutes les déclarations suivantes (récupération
d'erreur) ; chaque nouveau `|…|` en position de type réarme l'échec.
Conséquence : cécité permanente et **silencieuse** sur la fin du fichier
(mesuré : 412 déclarations manquées dans 60/176 fichiers d'un dépôt Lean réel).

On ne répare pas la grammaire externe : on la **neutralise**.

## Doctrine de verrouillage (deux cas, aucun troisième)

Pour chaque site, l'une des deux options :

- **R — robuste source de vérité** : l'existence des symboles vient d'un
  extracteur proprietaire (robuste/autonome) ; tree-sitter ne fournit que les
  métriques fines ou la vérification croisée.
- **B — échec bruyant** : si tree-sitter est dégradé ou divergent, le site
  lève une exception explicite / rend un code de sortie non-zéro / affiche
  une section dédiée. Jamais de résultat tronqué rendu silencieusement.

## Inventaire site par site

| # | Fichier : fonction | Données extraites via tree-sitter | Décision qui en dépend | Verrouillage | État |
|---|---|---|---|---|---|
| 1 | `parseur_lean.py` : `rechercher_declaration` | déclaration par nom | tout l'outillage d'analyse | **R** — source unique : le parseur autonome (stdlib) ; `comparer_extracteurs()` pour le diagnostic croisé | ✅ |
| 2 | `parseur_lean.py` : `parse` / `Noeud` | AST brut | analyses ad hoc | **B** — `has_error` exposé ; les appelants doivent le consulter | ✅ |
| 3 | `cli.py` : commandes `snapshot` / `veille` / `index` / `piste-sorry` / `chemins-verifiables` | (délégation) | verdicts, cartes | **R** — délèguent à `veille.py` / `carte.py` (voir #12, #13) ; aucun appel tree-sitter direct | ✅ |
| 4 | `langs/lean.py` : `AnalyseurLean` | symboles + métriques Lean | carte, veille, croyances | **R** — moteur `"autonome"` par défaut (parseur proprietaire, zéro dépendance) ; moteur `"tree_sitter"` conservé pour vérification croisée optionnelle, avec `_repli_robuste` marqué `extraction="robuste_repli"` | ✅ |
| 5 | `langs/treesitter_generic.py` : `AnalyseurTreeSitter.analyser` | symboles autres langages | carte multilang | **B** — annotation CRITICAL « ⚠️ TREE-SITTER DÉGRADÉ » si `root.has_error` (on ne peut pas appliquer le repli Lean aux autres langages : on signale, on ne tait pas) | ✅ |
| 6 | `langs/registry.py` : `treesitter_disponible`, `obtenir_analyseur` | disponibilité pack/grammaire | choix d'analyseur | **B** — `ImportError` / `ValueError` explicites, jamais de repli silencieux ; Lean est toujours analysable (parseur autonome) | ✅ |
| 7 | `carte.py` : `carte_projet` | symboles (via analyseurs) | carte projet | **R+B** — avertissement « ⚠️ EXTRACTION DÉGRADÉE » explicite dans `avertissements` (rendu console), sans changement de schéma | ✅ |
| 8 | `editeur/indexeur.py` : `_symboles_du_fichier` | symboles | index projet | **R** — propage le champ `extraction` sur `Symbole` | ✅ |
| 9 | `croyances.py` : `_sorry_par_symbole` | `sorry_present` par symbole | veille (trous) | **R** — détection par grep hors commentaires, indépendante de tree-sitter ; expose `extraction_repli: {fichier: n}` | ✅ |
| 10 | `veille.py` : `comparer`, `veille_console` | verdict | porte STABLE/DÉGRADATION | **R+B** — l'existence des symboles vient du parseur autonome ; divergence ≥ 1 symbole → section « ⚠️ EXTRACTION DÉGRADÉE » + clé JSON `extraction_repli` ; le verdict ne bascule pas (comparaison valide) | ✅ |
| 11 | `durcissement.py` : `diagnostiquer_capteurs` | état des capteurs | pré-vol | **B** — capteurs bruyants par design (OK/DÉGRADÉ explicites) | ✅ (préexistant) |
| 12 | `dependances.py` : `extraire_identifiants`, `analyser_fichier` | identifiants, déclarations | graphe de dépendances | **R** — source unique : le lexer et le parseur autonome (spans de corps exacts, binders filtrés) ; zéro dépendance externe | ✅ |
| 13 | `mission.py` | — (métadonnées texte, aucun parse) | registre des modules | N/A — pas un site d'appel | ✅ |

## Portage public — écarts assumés (2026-10-03)

Le verrouillage a été développé sur la branche de travail v110, qui contient
des modules absents du dépôt public. Les sites correspondants n'ont **pas**
d'équivalent public et ne sont donc pas couverts par ce registre :

- `protocole_lean.py`, `dualite.py`, `synthese_locale.py`,
  `visualisation_ast.py`, `godel_fourier.py`, `indexeur_lemmes.py` —
  modules v110 uniquement (commandes `protocole`/`visualiser` de cli.py
  également absentes du public).
- `prevol.py`, `generateur_preuve.py` — modules v110 uniquement (N/A).

Si ces modules sont portés un jour vers le public, leurs verrouillages
devront l'être aussi (voir le registre v110 d'origine pour la référence).

## Garantie exacte

Après ce verrouillage, phi-complexity garantit :

1. **Aucun symbole existant n'est invisible** : l'existence vient toujours du
   parseur proprietaire, sur tous les chemins qui comptent des symboles
   (carte, veille, index, dépendances, recherche de déclaration).
2. **Aucun `sorry` n'est manqué en silence** : la veille le détecte par grep
   indépendant.
3. **Toute dégradation tree-sitter est bruyante** : section console dédiée
   (« ⚠️ EXTRACTION DÉGRADÉE », « ⚠️ TREE-SITTER DÉGRADÉ »), clé JSON, ou
   exception explicite — selon le site.
4. **Aucun nouveau site silencieux ne peut s'ajouter sans faire échouer les
   tests** (`test_zero_treesitter_silencieux.py`, garde a).

## Limites résiduelles assumées

- Les métriques des symboles récupérés par repli sont des **proxys**
  (complexité = lignes du span) — honnêtes et marquées, pas inventées.
- Le repli regex a ~1 % de faux positifs (mot-clé dans un commentaire de
  bloc) — coût explicite, jamais un silence.
- `opaque` n'est extrait ni par tree-sitter ni par le robuste (périmètre
  identique des deux côtés : pas de divergence artificielle).
- Les déclarations indentées (dans `section`/`namespace`) ne sont vues ni
  par l'un ni par l'autre (cohérent des deux côtés).
- Pour les langages non-Lean (`treesitter_generic`), pas de repli robuste
  possible : la dégradation est **signalée** (annotation CRITICAL), pas
  réparée.
- On ne corrige pas la grammaire externe `tree-sitter-language-pack` :
  si une future version la répare, les gardes deviennent silencieusement
  inactives (bien) ; si elle introduit un nouveau motif d'aveuglement, les
  gardes de divergence le signaleront (bien aussi).

---

## Basculement 2026-10-03 — AUTONOMIE STRICTE : le parseur Lean est proprietaire

**Décision** : phi-complexity est construit pour être autonome — zéro outil
externe intégré à travers les architectures (Python/Lean/OS = le sol,
jamais les murs).

### Ce qui change

Nouveau module `phi_complexity/parseur_autonome.py` (stdlib uniquement,
zéro dépendance externe) : lexer (commentaires `--` et `/- -/` imbriqués,
docstrings, strings, char-literals, opérateurs multi-caractères) + descente
récursive sur les en-têtes + délimitation exacte des corps par équilibrage.
Tout construit non reconnu → `Avertissement` avec ligne, JAMAIS de silence.

Le parseur autonome est désormais la **source unique** sur tout le chemin
Lean : `AnalyseurLean` (moteur `"autonome"` par défaut),
`rechercher_declaration`, `dependances.py`, `comparer_extracteurs`, veille,
carte, indexeur. tree-sitter est **relegué en vérification croisée
optionnelle** (`moteur="tree_sitter"`, désactivée par défaut).

### Mesures (2026-10-03, 248 fichiers Lean)

- Autonome : **5377** déclarations ; robuste (référence) : 5240.
- Zéro déclaration perdue vs le robuste ; les extras sont réels (`axiom`,
  `opaque`, `@[simp] theorem` même ligne).
- 0 chevauchement de spans ; les ~570 déclarations manquées par tree-sitter
  (cause racine `|·|`) : toutes trouvées par l'autonome.

### Garantie (chemin Lean)

1. Aucun symbole Lean existant n'est invisible à l'instrument.
2. Aucun `sorry` n'est manqué en silence.
3. Toute dégradation est bruyante (`Avertissement` avec ligne).
4. Aucun nouveau site tree-sitter silencieux ne peut apparaître sans
   faire échouer `tests/test_zero_treesitter_silencieux.py`.

Limites assumées (documentées, pas masquées) :
- Déclarations de premier niveau en colonne 0 (un mot-clé indenté
  déclenche un avertissement explicite, pas un avalage silencieux).
- `mutual` : non supporté, bloc ignoré avec avertissement explicite.
- Contenu des termes/tactiques opaque (comme avant) ; métriques =
  lignes du span + comptage de tactiques documenté.
