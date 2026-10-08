# RAPPORT RUCHE-PHI-NATIF — Intégration native des nouveaux modules

**Mission terminée le 2026-10-08** — 4/4 chantiers livrés.
**Branche** : `feat/dual-licensing` (local uniquement). **Aucun push effectué.**

## Synthèse

Les 6 modules intégrés précédemment en local (`lilith`, `scipy_mini`, `cache`, `impact`, `sched`, `portable`) étaient accessibles comme sous-commandes séparées (opt-in). Cette mission les a rendus **natifs** : intégrés au workflow principal des commandes existantes, actifs par défaut, désactivables par flag.

| Chantier | Commande | Intégration | Commit |
|---|---|---|---|
| A | `phi veille` | Section Lilith (var_relative, n_eff, alerte > 2,95) | `0b4f86d` |
| A | `phi entropie` | Garde l'implémentation actuelle (documenté : plus précise) | `0b4f86d` |
| B | `phi sonde` | Section IMPACT (dépendants, score 0-100, niveau) | `3762a6f` |
| C | `phi check` | Parse cache transparent (byte-identique) | `05a3787` |
| D | `phi index` | Backend phi_scipy natif + repli gracieux | `5143ab4` |

## Détail par chantier

### A — Lilith native dans `veille` (+ `entropie`)

- Nouveau flag `--sans-lilith` sur le parser `veille`.
- Après `comparer()`, `_executer_veille` ajoute `diff["lilith"]` : `{active, seuil_var_relative: 2.95, nb_fichiers, nb_alertes, fichiers: {rel: {n, var_relative, n_eff}}, alertes}`.
- Fichiers non parsables exhibés comme tels, jamais ignorés silencieusement.
- La section est **informative** : ne fait jamais basculer le verdict ni échouer la veille.
- `phi entropie` : comparaison avec `shannon_norm` → **on garde l'implémentation actuelle**. Trois raisons documentées en commentaire (`NOTE PHI-NATIF-A`) : (1) `entropie_certifiee` fait une sommation double-double certifiée avec borne exhibée — plus précis ; (2) le télescopage `ΣΔH` exige des bits bruts, la normalisation `÷log₂(n)` le briserait ; (3) objets mesurés différents (poids arbitraires vs κ AST) — complémentaires, pas substituables.
- Tests : 53/53 (`test_veille`, `test_veille_alternatif`, `test_entropie`) + 14/14 (`test_cli`).
- Fumée : section affichée par défaut (console + JSON), `--sans-lilith` désactive proprement, alerte au format exact sur var_relative=7,85 (verdict resté STABLE).

### B — Analyse d'impact native dans `sonde`

- Nouveau module `phi_complexity/impact_sonde.py` : `analyser_impact(nom_mecanisme, racine, graphe)` — construit le `GrapheDependances` **une seule fois** par invocation, résout le nom sondé (nid exact → qualname → `module.symbole`), calcule `impact_avant` + `score_risque`. Ne lève jamais (try/except global).
- `ResultatSonde.impact` rempli uniquement par le CLI (`sonder()` reste pur registre) ; clé `"impact"` dans `vers_dict()` ; section console `┌─ IMPACT` affichée si active.
- Flag `--sans-impact` ; dégradation gracieuse sur symboles non-Python (avertissement, jamais de crash).
- Tests : 14/14 nouveaux (`test_impact_sonde.py`, paquet synthétique hermétique) + 122/122 existants (`test_sondes`, `test_cli`, `test_durcissement_sondes`, `test_ancrage` — 1 échec initial corrigé en mandatant la clé `impact`).
- Non-régression : `phi sonde energy_identity --sans-impact` **byte-identique** à HEAD pré-changement.
- Fumée : `phi sonde sonder` → 13 dépendants, 46,6/100 MOYEN.

### C — Parse cache natif dans `check`

- Nouveau `phi_complexity/cache/integration_check.py` : `session_parse_cache(cible)` (context manager, charge/sauvegarde disque, ne lève jamais), `resoudre_racine_cache` (`.phi_cache/` local, repli `~/.phi_cache`).
- `AnalyseurPython.charger()` passe par `_parser_avec_cache()` : hash `calculer_hash_src`, hit → AST réutilisé, miss → `ast.parse` + `put`. Lecture fichier inchangée ; `SyntaxError` remonte et n'est jamais cachée.
- Flags `--sans-cache` / `--stats-cache` ; `.phi_cache/` ajouté au `.gitignore`.
- **Preuve de transparence** : 84 fichiers réels, `--sans-cache` vs cache froid vs cache chaud → **diffs vides** (console + JSON + matrice projet).
- Tests : 15/15 nouveaux + 62/62 non-régression.
- **Mesure honnête** : micro-benchmark ~2× par parse (24,5 ms → 12,4 ms), mais bout-en-bout sur 84 petits fichiers : 0,75 s vs 1,59 s — les I/O disque (lectures/écritures atomiques + manifest) dominent. Le gain se matérialise sur parseurs lourds et gros volumes ; `ast.parse` (en C) n'est pas le cas gagnant. Signalé tel quel.
- Couverture : analyseur Python natif uniquement (AST picklable) ; tree-sitter/Lean ignorent le cache (dégradation silencieuse, comportement historique).

### D — phi_scipy natif dans `index`

- **Enquête honnête** : `phi index` ne fait AUCUN BFS/degrés/PageRank (temps = analyse syntaxique + audit). Seul noyau à structure de graphe : incidence bipartie nom×fichier de `_detecter_collisions` (~3,5 ms, 0,2 % du total).
- `carte.py` : `_detecter_collisions` scindée en `_python` (référence inchangée) + `_scipy` (backend `BinaryCSR`, O(nnz) en C). Répartiteur avec **repli gracieux** sur toute exception (numpy absent inclus). Flag `--sans-scipy-mini`.
- **Benchmark** (dogfooding, 134 fichiers, 2145 symboles) : noyau 3,55 ms vs 3,43 ms (≈ équivalents) ; total 1,44 s vs 1,55 s (+8 % = import numpy à froid). À 100k symboles : Python pur légèrement devant (l'assemblage domine).
- **Preuve d'équivalence** : md5 JSON identiques (`f903546c…`), md5 console identiques (`41820de0…`).
- Tests : 55 passed ; repli vérifié avec numpy bloqué.
- **Décision documentée** : `@auto_optimize` non utilisé — son routage par densité choisirait toujours le CSR (incidence toujours ≪ 1 % dense), y compris où le Python pur gagne. Choix explicite + repli automatique.
- **Recommandation** : infrastructure en place et prouvée équivalente — servira quand la carte fera de vrais calculs de graphe. **Ne pas présenter comme un gain de vitesse** : branchement natif + filet de sécurité.

## Coordination des 4 agents parallèles

Les 4 agents modifiaient `cli.py` simultanément. Aucun conflit :
- L'agent A a stagé uniquement ses 3 hunks via `git apply --cached`.
- L'agent B a noté les flags `--sans-cache`/`--stats-cache` déjà présents (travail de C).
- L'agent D a constaté ses 2 hunks CLI déjà commités par B (texte byte-identique, aucun doublon).
- Commits séquentiels propres : `0b4f86d` → `05a3787` → `3762a6f` → `5143ab4`.

## Conformité

- `py_compile` 3.11 (`~/workspace/venvs/ci311`) + 3.12 sur tous les fichiers modifiés : OK.
- **Aucun `git push`** — 4 commits locaux sur `feat/dual-licensing` uniquement.
- `origin/feat/dual-licensing` et le dépôt public **intacts**.
- Tous les rapports en français.

## Ce qui reste hors périmètre (pistes futures)

1. Brancher `valider_ou_reparser` d'`invalidation.py` au lieu du get/put manuel (C).
2. Étendre le hook cache aux analyseurs tree-sitter/Lean quand leurs AST seront sérialisables (C).
3. Vrais calculs de graphe dans `phi index` pour rentabiliser le backend CSR (D).
4. Kit d'intégration phi prêt — n'exécuter que sur ordre explicite de Tomy (rappel mission LILITH-INSTRUMENTS).
