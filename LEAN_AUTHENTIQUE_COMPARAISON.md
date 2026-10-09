# Comparaison : notre toolchain Lean mini vs Lean authentique

**Date :** 2026-10-08
**Mission :** RUCHE-LEAN-AUTHENTIQUE
**Statut :** Étude terminée — mesures réelles, pas d'estimations

---

## 1. Ce que nous avons (mesuré)

Notre toolchain mini (`~/.cache/phi-complexity/toolchains/4.34.0-mini/`) :

| Composant | Présent | Testé |
|---|---|---|
| `bin/lean` | ✅ | `--version` → 4.34.0 |
| `lib/lean/Init/*.olean` (649 modules) | ✅ | `import Init.Data.List.Basic` OK |
| `libleanshared.so` + `libInit_shared.so` | ✅ | `--run` fonctionnel |
| Élaboration (`lean fichier.lean`) | ✅ | Fonctionne |
| Exécution (`lean --run`) | ✅ | "Bonjour Lean depuis phi" validé |
| Génération `.olean` (`-o`) | ✅ | Fichier .olean produit (4568 o) |
| Génération code C (`-c`) | ✅ | Fichier .c produit (4458 o) |
| **Serveur de langage (`--server`)** | ✅ | **Répond LSP initialize avec capacités complètes** |
| Installation | ✅ | `phi lean --init` (téléchargement + SHA256 + extraction) |

## 2. Ce qui nous manque (vérifié par tests)

| Composant | Absent | Impact mesuré |
|---|---|---|
| `bin/lake` | ❌ | Pas de gestion de projet, pas de dépendances |
| `bin/leanc` | ❌ | Pas de compilation native (binaire exécutable) |
| `lib/lean/Lean/` (~1,3 Go) | ❌ | `import Lean` → "unknown module prefix 'Lean'" |
| `lib/lean/Std/` (~348 Mo) | ❌ | `import Std` → "unknown module prefix 'Std'" |
| `lib/lean/Lake/` (~108 Mo) | ❌ | Pas de DSL lakefile |
| Mathlib (~7 Go, 8939 fichiers) | ❌ | Aucune tactique avancée (`ring`, `omega` via Mathlib, etc.) |
| Elan | ❌ | Pas de gestion multi-versions |

## 3. Lake : ce qu'il fait que nous ne faisons pas

Lake est le système de build ET le gestionnaire de paquets de Lean.

### Fonctionnalités clés

| Commande | Rôle | Équivalent chez nous |
|---|---|---|
| `lake new` / `lake init` | Crée un projet (lakefile + lean-toolchain + structure) | ❌ Rien |
| `lake update` | Télécharge les dépendances git dans `.lake/packages/` | ❌ Rien |
| `lake build` | Compile tous les modules avec cache incrémental | ❌ `lean` un fichier à la fois |
| `lake exe <nom>` | Exécute un binaire défini dans les dépendances | ❌ Rien |
| `lake env <cmd>` | Exécute avec `LEAN_PATH`/`LD_LIBRARY_PATH` corrects | ⚠️ Partiel (`manager.py` définit `LEAN_PATH`) |

### Le fichier `lakefile.lean`

```lean
import Lake
open Lake DSL

package mon_projet where
  version := v!"0.1.0"

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.34.0"

@[default_target]
lean_lib MonProjet where
  srcDir := "MonProjet"
```

Sans Lake, impossible de :
- Déclarer des dépendances
- Builder un projet multi-fichiers avec résolution d'imports
- Gérer les versions des dépendances

### Verdict Lake

**Manque critique pour un usage "programmeur standard".** Notre `phi lean` ne gère que des fichiers isolés.

## 4. Mathlib : la bibliothèque

### Mesures réelles (sources publiques, 2026)

| Métrique | Valeur |
|---|---|
| Modules | ~8559 (~8939 fichiers avec cache) |
| Déclarations | ~792 459 (avec dépendances : 10 881 modules) |
| Taille checkout + build | ~7 Go |
| Temps de téléchargement cache | ~5 minutes (`lake exe cache get`) |
| Temps de compilation source | Plusieurs heures (d'où l'importance du cache) |
| Arêtes d'import | ~26 800 |

### Peut-on en faire une version mini ?

**Partiellement.** Une expérience documentée montre qu'on peut élaguer par fermeture transitive :
- Chaque "shard" (algèbre, analyse, etc.) garde 53–68% de Mathlib complet
- Lean ne charge que les `.olean` transitivement importés, pas tout ce qui est sur disque

**Mais :** même le plus petit shard utile fait ~53% de 7 Go ≈ 3,7 Go. Pas de "Mathlib nano".

### Ce que Mathlib apporte (et que nous n'avons pas)

- Tactiques : `ring`, `omega`, `linarith`, `simp` (version étendue), `norm_num`, `positivity`, `field_simp`
- Théorie des nombres, analyse réelle, algèbre, topologie, théorie des catégories
- `#print axioms` significatif (vérification des axiomes utilisés)

### Verdict Mathlib

**Le plus gros manque fonctionnel.** Sans Mathlib, on ne peut pas faire de vraies mathématiques formalisées au standard de la communauté. C'est le point que Tomy vise avec ses "vrais tests en mathématiques pures".

## 5. Serveur de langage (LSP)

### Test réel effectué

```
$ lean --server < initialize LSP
→ Réponse avec capacités complètes :
  - completionProvider, definitionProvider
  - documentSymbolProvider, codeActionProvider
  - hover, references, etc.
```

**Verdict : ✅ FONCTIONNE.** Notre toolchain supporte le LSP. Un éditeur (VS Code + extension lean4) pourrait théoriquement se connecter à notre `lean --server`.

**Limite :** sans `Lean/` et `Std/`, l'autocomplétion ne couvre que `Init`. Utile mais incomplet.

## 6. Elan : gestionnaire de toolchains

### Comment ça marche (documentation officielle)

| Mécanisme | Description |
|---|---|
| Fichier `lean-toolchain` | Une ligne avec l'identifiant version, à la racine du projet |
| Résolution | Remonte les répertoires parents jusqu'à trouver un `lean-toolchain` ou un override |
| Override | `elan override set <version>` — config locale, prioritaire sur le fichier |
| Défaut | `elan default <version>` — repli si rien n'est trouvé |
| Auto-téléchargement | La version manquante est téléchargée à la première utilisation |

### Notre approche vs Elan

| Aspect | Elan | Notre `phi lean` |
|---|---|---|
| Sélection par projet | ✅ `lean-toolchain` | ❌ Version unique globale (4.34.0) |
| Multi-versions | ✅ Plusieurs toolchains côte à côte | ❌ Une seule |
| Override local | ✅ | ❌ |
| Auto-téléchargement | ✅ | ✅ (`--init` + auto à la compilation) |
| Vérification d'intégrité | ⚠️ Non documenté | ✅ SHA256 épinglé |
| Stockage | `~/.elan/toolchains/` | `~/.cache/phi-complexity/toolchains/` |

### Verdict Elan

**Notre approche est plus simple mais moins flexible.** Pour un usage mono-version (le cas de Tomy), c'est suffisant. Le multi-versions deviendrait nécessaire si on doit supporter plusieurs projets avec des versions différentes.

## 7. Workflow typique d'un programmeur Lean

### Standard (avec Lake + Elan + Mathlib)

```bash
# 1. Installation (une fois)
curl https://elan.lean-lang.org/install.sh -sSf | sh

# 2. Création du projet
lake new mon_projet math    # template avec Mathlib
cd mon_projet

# 3. Dépendances
lake update                  # clone Mathlib (~7 Go)
lake exe cache get          # télécharge les .olean précompilés (~5 min)

# 4. Développement (VS Code + extension lean4)
#    - LSP via `lean --server`
#    - Feedback en temps réel

# 5. Build
lake build                   # compile le projet

# 6. Exécution
lake exe mon_executable
```

### Nôtre (avec phi)

```bash
# 1. Installation (une fois)
phi lean --init              # télécharge toolchain mini (~580 Mo)

# 2. Fichier isolé
echo 'def main : IO Unit := IO.println "hello"' > hello.lean

# 3. Compilation/exécution
phi lean hello.lean --exec
```

### Écarts

| Étape | Standard | Nôtre |
|---|---|---|
| Projet multi-fichiers | ✅ Lake | ❌ |
| Dépendances | ✅ `require` git | ❌ |
| Cache Mathlib | ✅ `lake exe cache get` | ❌ |
| IDE complet | ✅ VS Code + LSP | ⚠️ LSP seul (sans Lean/Std) |
| Binaire natif | ✅ `leanc` | ❌ (C généré mais non linké) |
| Fichier isolé | ✅ `lean fichier.lean` | ✅ |

## 8. Tableau comparatif global

| Fonctionnalité | Lean authentique | Notre mini | Écart |
|---|---|---|---|
| Élaboration de base | ✅ | ✅ | Aucun |
| `Init` (types de base, List, Nat) | ✅ | ✅ | Aucun |
| Exécution (`--run`) | ✅ | ✅ | Aucun |
| Serveur LSP | ✅ | ✅ | Aucun (mais complétion limitée à Init) |
| Génération `.olean` | ✅ | ✅ | Aucun |
| Génération code C | ✅ | ✅ | Aucun |
| **Binaire natif** | ✅ (`leanc`) | ❌ | **Bloquant pour distribution** |
| **Lake (build/deps)** | ✅ | ❌ | **Bloquant pour projets** |
| **Elan (multi-versions)** | ✅ | ❌ | Gênant si plusieurs versions |
| **`Lean/` (métaprogrammation)** | ✅ | ❌ | Bloquant pour tactiques custom |
| **`Std/` (bibliothèque standard)** | ✅ | ❌ | Manque utilitaires |
| **Mathlib** | ✅ | ❌ | **Bloquant pour maths sérieuses** |

## 9. Recommandations priorisées

### P0 — Critique pour Tomy

1. **Mathlib en téléchargement à la demande** (même pattern que la toolchain)
   - Pourquoi : sans Mathlib, pas de "vraies mathématiques pures" au standard communautaire
   - Comment : `phi lean --mathlib` → `lake exe cache get` équivalent (télécharge les .olean précompilés)
   - Taille : ~7 Go (mais c'est le prix d'entrée standard)
   - Alternative légère : shard par fermeture transitive (~3,7 Go pour le plus petit)

2. **`leanc` dans la toolchain mini** (compilation native)
   - Pourquoi : sans lui, impossible de produire des binaires distribuables
   - Coût : quelques Mo supplémentaires (c'est un wrapper autour de clang/gcc)
   - Test : `lean -c` fonctionne déjà, il manque juste l'étape de link

### P1 — Important pour un usage "programmeur standard"

3. **Mini-Lake** : gestion de projet simplifiée
   - Pas besoin du Lake complet — un sous-ensemble suffit :
     - Résolution des imports multi-fichiers
     - `LEAN_PATH` automatique
     - Cache de build incrémental
   - Notre `manager.py` fait déjà une partie (`LEAN_PATH`)

4. **`Lean/` et `Std/` en option**
   - Pourquoi : métaprogrammation (tactiques custom) et utilitaires standard
   - Taille : ~1,3 Go + 348 Mo (mais téléchargeables à la demande)
   - Sans eux, pas de développement de tactiques

### P2 — Confort

5. **Support `lean-toolchain`** (fichier de version par projet)
   - Même sans Elan complet, lire le fichier `lean-toolchain` et sélectionner la bonne version
   - Préparation au multi-versions

6. **Intégration VS Code**
   - Le LSP fonctionne déjà — documenter comment pointer l'extension vers notre toolchain
   - Testé : `lean --server` répond correctement

### Volontairement exclu (et c'est OK)

| Exclu | Pourquoi c'est OK |
|---|---|
| Elan complet | Notre cas d'usage est mono-version ; la complexité d'Elan n'est pas justifiée |
| Build Mathlib depuis source | Personne ne fait ça (`lake exe cache get` est la norme) |
| `leanmake` (ancien) | Obsolète, remplacé par Lake |

## 10. Réponse directe aux questions de Tomy

**Qu'est-ce qui nous manque pour être "un vrai Lean" ?**
→ Lake (projets/dépendances), Mathlib (maths), leanc (binaires natifs), Lean/Std (métaprogrammation).

**Qu'est-ce qui est volontairement exclu (et pourquoi c'est OK) ?**
→ Elan complet (mono-version suffit), build Mathlib source (le cache est la norme).

**Qu'est-ce qu'on devrait ajouter en priorité ?**
→ P0 : Mathlib à la demande + leanc. C'est ce qui débloque les "vraies mathématiques".

**Y a-t-il des fonctionnalités critiques pour Tomy ?**
→ Oui : **Mathlib**. Sans elle, impossible de formaliser des mathématiques au niveau qu'il vise (Navier-Stokes). C'est le gap le plus important entre notre toolchain et un usage recherche.

---

*Toutes les mesures de notre toolchain sont réelles (tests exécutés le 2026-10-08).
Les chiffres Mathlib proviennent de sources publiques documentées (liens dans le rapport de mission).*
