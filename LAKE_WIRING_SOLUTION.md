# RUCHE-LAKE-WIRING : Solution

**Date :** 2026-10-08
**Statut :** TERMINÉ — `lake exe cache get` fonctionne avec notre toolchain mini.

## Problème initial

`lake exe cache get` échouait avec `unknown namespace 'Lake'` quand lake tentait de parser le lakefile.lean de Mathlib.

## Cause racine (3 bugs trouvés et corrigés)

### Bug 1 : Variantes `.olean.server` et `.olean.private` manquantes pour Lake

Le manifeste `fichiers_lake` ne listait que les `.olean`, oubliant les variantes `.server` et `.private` que `lean` exige à l'import.

**Symptôme :** `failed to open file '.../Lake.olean.server': No such file or directory`

**Fix :** Ajout de 8 motifs au manifeste :
- `lib/lean/Lake.olean.private`, `lib/lean/Lake.olean.server`
- `lib/lean/LakeMain.olean.private`, `lib/lean/LakeMain.olean.server`
- `lib/lean/Lake/*.olean.private`, `lib/lean/Lake/*.olean.server`
- `lib/lean/Lake/**/*.olean.private`, `lib/lean/Lake/**/*.olean.server`

### Bug 2 : Fichiers `.ir` manquants

L'archive officielle contient 2518 fichiers `.ir` (représentation intermédiaire pour l'interpréteur). Notre extraction n'en avait aucun.

**Symptôme :** `(interpreter) IR of declaration 'Lake.binder' not available`

**Fix :** Ajout des motifs `.ir` pour Lake, LakeMain, Init au manifeste. 809 fichiers extraits.

### Bug 3 : Fichiers `.ilean` et `.ir.sig` manquants

Comparaison avec la toolchain complète a révélé 325 fichiers manquants :
- `.ilean` (fichiers incrémentaux pour le language server)
- `.ir.sig` (signatures IR)

**Symptôme :** `failed to compile definition, compiler IR check failed` sur `lean_exe`

**Fix :** Ajout des motifs `.ilean` et `.ir.sig` pour Lake, Init, Lean, Std. 1620 + 5118 fichiers extraits.

### Bug 4 : Binaire `leantar` manquant

Le module `Cache.Hashing` de Mathlib requiert le binaire `leantar` pour créer les archives `.ltar`.

**Symptôme :** `leantar not found in Lean sysroot. This toolchain may predate nightly-2026-03-09.`

**Fix :** Extraction de `bin/leantar` (2,7 Mo) depuis l'archive officielle. Ajouté au manifeste.

## Validation

- ✅ `lake build` sur lakefile minimal : fonctionne
- ✅ `lake build` sur lakefile avec `lean_exe` : fonctionne
- ✅ `lake exe cache get` sur Mathlib v4.34.1 : **fonctionne** — 6,9 Go téléchargés, 8519/8529 oleans
- ✅ `import Mathlib` : partiellement (10 oleans manquants dans le cache, non bloquant pour le wiring)

## Fichiers modifiés

- `phi_complexity/toolchain/TOOLCHAIN_MANIFEST.json` : ajout des motifs manquants
- `LAKE_WIRING_SOLUTION.md` : ce document

## Leçons

1. **Les 5 variantes sont obligatoires** : `.olean`, `.olean.private`, `.olean.server`, `.ir`, `.ilean`, `.ir.sig`. En oublier une seule casse des fonctionnalités spécifiques.
2. **Comparer avec la référence** : extraire la toolchain complète et differ a été la méthode qui a trouvé le bug 3.
3. **Tester progressivement** : lakefile minimal → lakefile avec lean_exe → mathlib réel. Chaque étape a révélé un bug différent.
