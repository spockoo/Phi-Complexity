# MATHLIB_MINI_RAPPORT.md — Miniaturisation Mathlib v4.34.1

**Date :** 2026-10-08
**Statut :** MESURES COMPLÈTES — Cache récupéré avec succès
**Auteur :** Ruche MATHLIB-MINI

## 1. Récupération du cache : SUCCÈS (après correction)

### 1.1 Échec initial et diagnostic

`lake exe cache get` a d'abord échoué : **0/8934 fichiers**.
Cause identifiée : le hash de chaque module mélange un *root hash* calculé
depuis (`lakefile.lean`, `lean-toolchain`, **`lake-manifest.json`**, `Lean.githash`).
Notre `lake-manifest.json` avait été généré le jour même avec les révisions
de dépendances du jour → root hash différent de la CI → miss total garanti.

### 1.2 Correction

Le `lake-manifest.json` de la release **est commité** dans l'historique git :
```
git show v4.34.1:lake-manifest.json
```
Restauration du manifeste de release → `lake exe cache get` → **8696 fichiers**.

**Leçon durable :** ne jamais `lake update` avant `lake exe cache get`.
Le manifeste de la release est la référence ; `lake update` le régénère
avec les tips du jour et invalide tout le cache.

### 1.3 Révisions de dépendances (release v4.34.1)

| Paquet | Révision (release) |
|---|---|
| plausible | `118aa17ee846` |
| LeanSearchClient | `ddf04cf3949f` |
| importGraph | `e928b7254487` |
| proofwidgets | `106ff4fafc74e` |
| aesop | `355695d523e41` |
| Qq | `6a489d9af5d0c` |
| batteries | `f2effa3d803f` |
| Cli | `e92c9f15fdfac` |

## 2. Inventaire complet

### 2.1 Mathlib (`.lake/build/lib/lean/Mathlib/`)

| Variante | Fichiers | Taille |
|---|---|---|
| `.olean` | 8529 | 160 Mo |
| `.olean.private` | 8529 | **656 Mo** |
| `.olean.server` | 8529 | 20 Mo |
| **Total oleans** | **25587** | **~836 Mo** |

### 2.2 Dépendances (3 variantes)

| Paquet | Taille (3 variantes) |
|---|---|
| batteries | 94 Mo |
| aesop | 71 Mo |
| Qq | 11 Mo |
| proofwidgets | 9 Mo |
| plausible | 7 Mo |
| LeanSearchClient | 2 Mo |
| importGraph | 2 Mo |
| Cli | <1 Mo |
| **Total deps** | **~196 Mo** |

### 2.3 Total portable

| Composant | Taille |
|---|---|
| Mathlib oleans | 836 Mo |
| Dépendances oleans | 196 Mo |
| **Total** | **~1032 Mo (~1 Go)** |

## 3. Tests de nécessité des variantes

**Méthode :** copie des seuls `.olean` (sans `.private`/`.server`) dans un
dossier isolé, test `import Mathlib.Data.Nat.Basic` avec `LEAN_PATH`.

| Test | Résultat |
|---|---|
| Sans `.olean.server` | ❌ `failed to open file '...Basic.olean.server'` |
| Sans `.olean.private` | ❌ `failed to open file '...Basic.olean.private'` |
| Les 3 variantes | ✅ `Nat.add_comm` vérifié |

**Verdict : les 3 variantes sont obligatoires**, comme pour Lean core.
Aucune élimination possible ici.

## 4. Stratégie de miniaturisation

### 4.1 Ce qui est RÉFUTÉ

| Approche | Verdict |
|---|---|
| Élagage par namespace | **DANGEREUX** — dépendances transitives denses |
| Suppression d'une variante olean | **RÉFUTÉ** — les 3 sont requises (testé) |
| Subset par fermeture de dépendances | Possible en théorie, mais tout oubli = import cassé. Non tenté (risque > gain pour l'archivage). |

### 4.2 Ce qui est VIABLE : portabilité par découpage

Comme pour la toolchain Lean : découper en archives <60 Mo (contrainte PyPI).
Le découpage est **arbitraire** (les parties sont réassemblées ensemble,
pas utilisées indépendamment).

**Plan de découpage** (tailles compressées XZ estimées à ~17%) :

| Partie | Contenu | Brut | Compressé (est.) |
|---|---|---|---|
| P1 | `.olean.private` (A–M) | ~330 Mo | ~56 Mo |
| P2 | `.olean.private` (N–Z) | ~326 Mo | ~55 Mo |
| P3 | `.olean` (tous) | 160 Mo | ~27 Mo |
| P4 | `.olean.server` + dépendances | 216 Mo | ~37 Mo |

Chaque partie <60 Mo. Total : ~175 Mo compressés pour ~1 Go.

### 4.3 Pourquoi pas de vraie réduction

La "miniaturisation" honnête de Mathlib est limitée :
- Les 3 variantes olean sont incompressibles fonctionnellement.
- L'élagage sémantique (par namespace) casserait les imports transitifs.
- Le vrai gain est la **portabilité** (découpage + compression), pas l'amputation.

C'est cohérent avec la philosophie : le cache Mathlib lui-même ne fait
pas de subset — il distribue tout, adressé par hash.

## 5. Migration : delta par hash de contenu

Le format `.ltar` du cache est **natif content-addressed**. Pour les MAJ :
1. `inventaire_mathlib(tag)` → `{chemin: sha256}` (via `manifeste.py`).
2. `delta_mathlib(v1, v2)` → ajoutés/modifiés/supprimés (via `delta.py`).
3. Ne re-télécharger que les fichiers modifiés.

Entre deux tags proches, la majorité des 25587 fichiers sont inchangés.
C'est le système "malade" demandé par Tomy — implémenté dans `mathlib.py`.

## 6. Fichiers et artefacts

- Clone : `~/workspace/mathlib_scratch/mathlib4/` (v4.34.1)
- Toolchain complète : `~/workspace/mathlib_scratch/toolchain_full/`
- Oleana : `~/workspace/mathlib_scratch/mathlib4/.lake/build/lib/lean/`
- Spec migration : `~/workspace/phi-pub/MATHLIB_MIGRATION_SPEC.md`
- Implémentation : `~/workspace/phi-pub/phi_complexity/toolchain/mathlib.py`
- Tests : `~/workspace/phi-pub/tests/test_mathlib_migration.py` (12/12)

## 7. Recommandations

1. **Ne jamais `lake update` avant `lake exe cache get`.**
2. Pour les archives portables : construire les 4 parties `.tar.xz`.
3. Pour les MAJ : utiliser le delta par hash (implémenté).
4. La compilation depuis les sources reste le fallback ultime (non testée,
   plusieurs heures estimées).
