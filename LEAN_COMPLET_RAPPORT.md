# RUCHE-LEAN-COMPLET : combler les manques vs Lean authentique

**Date :** 2026-10-08
**Statut :** TERMINÉ — 4/4 chantiers livrés
**Branche :** `feat/dual-licensing` (commits locaux uniquement, aucun push)
**Note :** la mission sœur MATHLIB-UPDATE tourne en parallèle (non couverte ici).

## Synthèse

| Chantier | Priorité | Verdict | Commit | Taille (opt-in) |
|---|---|---|---|---|
| 1. `leanc` | P0 | ✅ INTÉGRÉ (opt-in) | `1eb72f2` | +575,2 Mo |
| 2. Lake | P1 | ✅ INTÉGRÉ (opt-in, non committé — voir §5) | — (hunks prêts) | +34,2 Mo |
| 3. `Lean/` + `Std/` | P1 | ✅ INTÉGRABLE en OPT-IN | `1c59e4f` | +290 Mo (std) / +1232,8 Mo (lean) |
| 4. `lean-toolchain` | P2 | ✅ IMPLÉMENTÉ | `ab55ae7` | 0 (code seul) |

Philosophie retenue partout : **le mini reste le défaut, chaque extension est opt-in et additive** (pas de re-téléchargement, marqueurs d'idempotence).

---

## Chantier 1 — `leanc` (P0) ✅

**Pipeline natif validé bout-en-bout** sur l'archive officielle 4.34.0 (en cache) :
```
lean --c=Hello.c Hello.lean  →  Hello.c
leanc Hello.c -o hello_nat   →  binaire natif (lié statiquement)
./hello_nat                  →  OK, exit 0
```

**Fichiers requis** (identifiés par `ldd` + `strace` + tests d'élimination) :
- `bin/leanc`, `bin/clang`, `bin/ld.lld` (RUNPATH `$ORIGIN/../lib`, pas de `LD_LIBRARY_PATH` requis)
- `lib/libclang-cpp.so.22.1` (65 Mo), `lib/libLLVM.so.22.1` (77 Mo) + symlinks
- `lib/libc++.so.1.0`, `libc++abi`, `libunwind` + symlinks
- `.a` statiques : `libLean.a` (**319 Mo**, 55 % du kit), `libStd.a` (31 Mo), `libInit.a` (27 Mo), `libLake.a` (21 Mo), `libleancpp`, `libleanrt`, `libLeanIR`, `libLeanc`, `libleanmanifest` — tous exigés par la ligne d'édition de liens codée en dur dans `leanc`
- `lib/libgmp.a`, `libuv.a`, `libssl.a`, `libcrypto.a`, `lib/glibc/*`, `lib/crt*.o`, `lib/clang/22/...`
- `include/lean/*.h` + `include/clang/*` — **critique** (`fatal error: 'stddef.h'` sinon)
- `bin/llvm-ar` volontairement exclu (jamais invoqué, prouvé par strace)

**Code** : `MOTIFS_NATIFS` (43 motifs, `extract.py`), `valider_natif()` (`validate.py`), `installer(avec_natif=False)` additif (`manager.py`), manifeste enrichi, 15 nouveaux tests.

**Mesures** : kit natif seul **575,2 Mo** (89 membres) ; mini + natif ≈ 1156 Mo. Extraction ~21–26 s.

**Décisions documentées** :
- Strip des `.a` **REJETÉ** : `strip -g` les *grossit* de ~10 % (GNU strip reconstruit moins bien les archives llvm-ar).
- **Bug réel trouvé et fixé** : `MOTIFS_MINIMAUX` (11) ≠ manifeste `fichiers_minimaux` (14) — le commit `f35dd52` avait ajouté les 3 `Init.olean` racine au manifeste mais oublié `extract.py`. Une extraction fraîche produisait une toolchain cassée (`object file 'Init.olean' does not exist`). Corrigé.

---

## Chantier 2 — Lake (P1) ✅ (code prêt, commit différé — voir §5)

**Verdict : LAKE INTÉGRÉ**, support TOML complet ; `lakefile.lean` exclu avec chiffres.

**Mesures** (archive 4.34.0) : **174 fichiers, 34,2 Mo** (+6,5 % sur le mini) :
- `bin/lake` (13 840 o, ELF natif), `lib/lean/libLake_shared.so` (12,5 Mo), `Lake*.olean` (161 f., 17,8 Mo), headers (0,2 Mo), `.a` d'édition de liens (5,3 Mo), symlinks + `libStd.a` vide.

**Élagage prouvé empiriquement** (`lake new` + `lake build` 8/8 après chaque retrait) : `.olean.private` (64,6 Mo), `.olean.server`, `.ilean`, `.ir`, `libLake.a` (21 Mo) exclus. **Astuce** : symlinks `libLean.so → libleanshared.so` (233 Mo déjà présent) au lieu des `.a` statiques.

**Limite honnête** : `lakefile.lean` **NON supporté** — son élaboration exige `import Lake` → transitivement `lib/lean/Lean/**` = 1217 modules, **1,33 Go**. Seuls les projets `lakefile.toml` (défaut de `lake new`) fonctionnent. Refus explicite via `LakeConfigNonSupportee` (message actionnable, testé en CLI).

**Code** (7 fichiers, +1129/−12) : `MOTIFS_LAKE` + `creer_liens_lake()` idempotent et non destructif, `valider_lake()`, `installer(avec_lake=)` / `executer_lake()` (positionne `LEAN_PATH` + `LD_LIBRARY_PATH`), `phi lean --init --avec-lake`, **`phi lean lake <args>`**, `phi lean --version` affiche Lake, 19 nouveaux tests (zéro réseau).

**E2E réel** (miroir `file://`, cache isolé) : init → `lake new` → `lake build` 8/8 → exe exécuté → garde `lakefile.lean` → `lean --run` non régressé → idempotence. L'e2e a attrapé un vrai bug (corrigé).

---

## Chantier 3 — `Lean/` + `Std/` (P1) ✅

**Verdict : INTÉGRABLE en OPT-IN.** Aucun élagage possible (vérifié empiriquement).

**Mesures exactes** :
- Extension `std` : `import Std` seul, 489 modules, 1467 fichiers, **290,0 Mo**
- Extension `lean` : `import Lean` (+ Std par fermeture), 1706 modules, 5121 fichiers, **1232,8 Mo**

**Élagage réfuté** : 3 variantes `.olean` requises, aucun sous-arbre élagable, pas de mini Lean utile (Elab.Tactic seul = 963 Mo).

**Code** : `MOTIFS_EXTENSION_*` (`extract.py`), `valider_extension` (`validate.py`), `ToolchainManager.installer_extension()` idempotent (marqueur `.valide-ext-<nom>`), `phi lean --init --extension {std,lean}`, 16 nouveaux tests — **56/56 OK**.

**Validé** : tactiques custom (macro, `elab_rules`, `@[tactic]`).

---

## Chantier 4 — Support `lean-toolchain` (P2) ✅

**Nouveau module `toolchain/version.py`** (isolé) :
- `lire_version_projet(dossier)` : remonte les parents, parse `leanprover/lean4:v4.34.0` / `v4.34.0` / `4.34.0`, le plus proche gagne.
- Exceptions typées : `FormatLeanToolchainInvalide`, `VersionNonSupportee(demandee, disponible, chemin)`.

**Intégration** : `verifier_version_projet()` dans `compiler()`/`executer()` — **avant** tout appel au binaire. Message français clair si mismatch (version demandée, chemin du fichier, seule 4.34.0 supportée, multi-versions = évolution future documentée).

**CLI** : `phi lean --version` affiche la version du projet si `lean-toolchain` existe ; message gracieux (pas de traceback) si invalide.

**Tests** : 19 nouveaux (parsing, remontée, mismatch, CLI) — **42/42 verts**, sans réseau. `py_compile` 3.11 + 3.12.

**Bug réel trouvé en live et corrigé** : `phi lean --version` plantait (traceback) sur `lean-toolchain` invalide → test de régression ajouté.

**Limite assumée** : mono-version (4.34.0). Toute autre version échoue proprement au lieu de télécharger.

---

## §5 — Points de vigilance pour le parent

1. **Chantier 2 NON COMMITTÉ (décision délibérée)** : la mission sœur édite activement les mêmes fichiers (`cli.py`, `validate.py`, `test_toolchain.py`, `__init__.py`) et son `migration.py` est **tronqué** (marqueur de troncature → `SyntaxError` à l'import du package). Commiter embarquerait son travail inachevé. Les hunks du chantier 2 sont intacts et vérifiés. À commiter quand la sœur aura stabilisé (ou commit sélectif par hunks).
2. **Le package `phi_complexity.toolchain` est actuellement INIMPORTABLE** à cause de ce `migration.py` (mission sœur, hors scope des 4 chantiers). Les tests du chantier 2 ont tourné via shim mémoire uniquement.
3. **3 tests `TestVersionProjetManager` en échec** : pré-existants, causés par le bump manifeste 4.34.0→4.34.1 de la mission sœur (vérifié par `git stash`).
4. Mesures chantiers 1–3 sur **4.34.0** (seule archive en cache) alors que le manifeste vise 4.34.1 — motifs indépendants de la version, noté dans les notes de manifeste.
5. Coexistence : si le kit natif s'installe après Lake, sa vraie `libStd.a` écrase l'archive vide de Lake — voulu et testé (jamais l'inverse).

## Conformité

- **Aucun `git push`** (vérifié : remote intact).
- Commits locaux : `1eb72f2` (chantier 1), `1c59e4f` (chantier 3), `ab55ae7` (chantier 4) — tous "(LOCAL UNIQUEMENT)".
- `py_compile` 3.11 (`~/workspace/venvs/ci311`) + 3.12 sur tous les fichiers touchés.
- `lean --run` non régressé (vérifié sur le cache réel par chaque chantier).
- Toolchain installée en cache non modifiée par les chantiers (un incident de strip accidentel au chantier 1 a été réparé à l'octet près, md5 vérifiés).
