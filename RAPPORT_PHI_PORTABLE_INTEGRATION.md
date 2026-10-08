# RAPPORT — Intégration du portable phiast dans le pipeline de compilation

**Mission :** RUCHE-PHI-PORTABLE-INTEGRATION
**Date :** 2026-10-08
**Statut :** TERMINÉ — Verdict : **NON VIABLE** (avec preuves et mesures)
**Règle :** LOCAL UNIQUEMENT — aucun push (règle dure Tomy 2026-10-08)

---

## 1. État des lieux (Phase 1)

### 1.1 Ce qu'est réellement `.phiast`

Le format `.phiast` est un **export AST pour analyse uniquement**.
`phi_complexity/portable/lecteur_phiast.py` est un **lecteur pur** :
- Parse : en-têtes, noms, niveaux, expressions, déclarations
- Valide : `validate()`, `constats_types()`
- Rapporte : `report()`

**Aucune** des fonctions suivantes n'existe dans le module :
compilation, élaboration, vérification de types, production d'oleans.
Vérifié par grep : zéro occurrence de `compile|elabor|typecheck|olean`.

Le `.phiast` sert aux instruments phi-complexity (veille, sondes).
Il ne compile pas. Confusion de catégories à éviter.

### 1.2 Ce qu'est la "mini toolchain"

Un vrai Lean 4.34.0 **reconditionné** :
- Binaire strippé (`strip --strip-all` : 223 Mo → 158 Mo, -29%)
- Oleas `Init` uniquement (+ `Lake` en 4.34.1-mini après le fix wiring)
- Localisation : `~/.cache/phi-complexity/toolchains/4.34.0-mini/`

**MANQUANT** (vérifié par `ls`) :
- `Lean/` (bibliothèque core du compilateur)
- `Mathlib/` et toutes les dépendances (~1 Go d'oleans)
- La mini toolchain **n'est pas autonome** pour notre travail.

### 1.3 Ce qui a été "réparé" le 2026-10-08

1. Intégration du module `portable/` depuis Metaprogramme-lean
   (lecteur_phiast, lecteur_zz, compresser_body) — imports OK 3.11/3.12.
2. Lake wiring : 4 bugs corrigés (.server/.private, .ir, .ilean/.ir.sig,
   binaire leantar manquant) — `lake exe cache get` fonctionne.

### 1.4 Ce que "plus rapide réfutée" signifie (chiffres)

- INTEGRATION_LOG.md (mission PHI-NATIF-D) : backend scipy_mini
  1,55 s vs Python pur 1,44 s — **aucun gain**, écart = import numpy.
- Mesures propres (cette mission) : voir §2.3.

---

## 2. Tests d'intégration (Phase 2)

### 2.1 Fichiers triviaux (sans Mathlib)

| Fichier | Mini toolchain | Standard (elan) |
|---|---|---|
| Hello.lean (froid) | 6,48 s | 5,86 s |
| Hello.lean (chaud) | 0,36 s | 0,44 s |

Verdict : équivalents (même binaire, écart = cache disque).

### 2.2 Fichier avec Mathlib

`TestMathlib.lean` (`import Mathlib.Data.Nat.Basic`, `#check Nat.add_comm`) :
- Mini seul : ❌ `unknown module prefix 'Lean'` (oleans core absents)
- Mini + LEAN_PATH manuel (elan lib/lean + 8 packages) : ✅
  `Nat.add_comm (n m : ℕ) : n + m = m + n`, EXIT 0

La mini toolchain **exige** un LEAN_PATH pointant vers elan et tous
les packages — elle dépend donc d'elan de toute façon.

### 2.3 Fichier projet réel : `Clay_H2_Bilinear.lean` (595 lignes)

| | Mini toolchain | Standard (`lake env lean`) |
|---|---|---|
| Résultat | ✅ EXIT 0 | ✅ EXIT 0 |
| Temps réel | **59,8 s** | **21,0 s** |
| Temps CPU (user+sys) | 11,3 s | 9,3 s |
| Axiomes (`#print axioms`) | `[propext, Classical.choice, Quot.sound]` | identiques |

**La mini toolchain est PLUS LENTE en wall-clock** (59,8 s vs 21,0 s).
Le temps CPU est comparable (même compilateur) ; l'écart vient de
l'overhead I/O du layout reconditionné (sys : 5,2 s vs 2,8 s).

### 2.4 Bonus : W1 vérifié

Au passage, la compilation de `Clay_H2_Bilinear.lean` (phase W1,
non vérifiée avant le reboot) est maintenant **confirmée** :
EXIT 0, 0 erreur, axiomes standard uniquement.

---

## 3. Décision (Phase 3) : NON VIABLE

### Raisons exactes (techniques, pas d'opinion)

1. **Même compilateur → ne peut pas être plus rapide.**
   C'est le binaire Lean 4.34.0 identique (même commit
   293d5d0c0c3f3dded4688b3ccd6a33939ac5102b).
   Mesuré : plus lent en wall-clock (59,8 s vs 21,0 s).

2. **Non autonome → dépend d'elan de toute façon.**
   Sans `Lean/` core ni Mathlib, la mini toolchain ne compile
   aucun de nos fichiers sans un LEAN_PATH manuel vers elan.
   Le but "portable" (se passer d'elan) est défait.

3. **`.phiast` sans rapport avec la compilation.**
   C'est un format d'analyse pour les instruments phi.
   L'intégrer au "pipeline de compilation" est une erreur de catégorie.

4. **Archives PyPI jamais construites.**
   Le plan 3×<60 Mo (MINI_60MO_RAPPORT.md) est resté à l'état de plan.
   Les `.tar.xz` n'existent pas.

### Seul cas d'usage légitime (hors de notre besoin)

Exécuter `lean` sur une machine sans elan, pour des fichiers
standalone (Init uniquement). Pas notre cas (nous utilisons Mathlib).

### Recommandation

**Ne pas intégrer.** Garder le toolchain standard (elan + lake).
Le travail portable reste valable pour :
- L'analyse AST via `.phiast` (instruments phi-complexity)
- La distribution future (si les archives sont un jour construites)
- L'archivage (découpage + compression documentés)

Mais pas pour accélérer ou remplacer nos compilations.

---

## 4. Fichiers

- Ce rapport : `~/workspace/phi-pub/RAPPORT_PHI_PORTABLE_INTEGRATION.md`
- Module portable : `~/workspace/phi-pub/phi_complexity/portable/`
- Toolchains mini : `~/.cache/phi-complexity/toolchains/4.34.0-mini/`
  et `4.34.1-mini/`
- Aucun push effectué (règle respectée)
