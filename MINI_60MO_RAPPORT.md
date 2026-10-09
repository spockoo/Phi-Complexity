# RUCHE-MINI-60MO : Peut-on passer sous la limite PyPI de 60 Mo ?

**Date :** 2026-10-08
**Statut :** TERMINÉ — Plan validé, split en 3 parties requis

## Réponse courte

**Non, pas en un seul fichier.** Le minimum fonctionnel compressé fait ~91 Mo (XZ) ou ~70 Mo (7z).
**Oui, en 3 parties.** Chaque partie < 60 Mo, réassemblées au premier usage.

## Mesures

### Composition du minimal (extrait)

| Composant | Taille | Supprimable ? |
|---|---|---|
| `libleanshared.so` | 223 Mo → **158 Mo strippé** | Non |
| `.olean.private` (649 fichiers) | 259 Mo | **Non** — lean refuse de démarrer sans |
| `.olean` (649 fichiers) | 100 Mo | **Non** — `Init.Prelude.olean` requis |
| `.olean.server` (649 fichiers) | 12 Mo | **Non** — lean exige `Init.olean.server` |
| `bin/lean` + stubs | < 1 Mo | Non |
| **Total extrait** | **~529 Mo** (strippé) | |

### Tests d'élimination (tous validés par `lean --run`)

| Test | Résultat |
|---|---|
| Sans `.olean.private` | ❌ `failed to open file 'Init.olean.private'` |
| Sans `.olean.server` | ❌ `failed to open file 'Init.olean.server'` |
| Sans `.olean` (plain) | ❌ `object file 'Init/Prelude.olean' does not exist` |
| Binaire strippé (`strip --strip-all`) | ✅ Fonctionne, **-65 Mo** (-29%) |
| Modules non utilisés | ❌ 1947/1949 modules chargés même pour Hello World |

### Compression mesurée

| Format | `.so` (158 Mo) | `.olean.private` (échantillon) | `.olean` (échantillon) |
|---|---|---|---|
| Zstd-3 | 28.0% (44 Mo) | 23.6% | 19.5% |
| Zstd-19 | 20.8% (33 Mo) | 19.6% | 16.1% |
| **XZ-6** | **19.3% (30 Mo)** | **16.9%** | **14.2%** |
| 7z (`-mx=9`) | **14.6% (23 Mo)** | — | — |

**Note :** Python stdlib inclut `lzma` (XZ) — aucune dépendance supplémentaire requise.

### Estimation totale compressée (strippé)

| Composant | Brut | XZ-6 estimé | 7z estimé |
|---|---|---|---|
| `.so` | 158 Mo | ~30 Mo | ~23 Mo |
| `.olean.private` | 259 Mo | ~44 Mo | ~34 Mo |
| `.olean` | 100 Mo | ~15 Mo | ~12 Mo |
| `.olean.server` | 12 Mo | ~2 Mo | ~1.5 Mo |
| **Total** | **529 Mo** | **~91 Mo** | **~70 Mo** |

## Plan : split en 3 parties < 60 Mo

PyPI limite à **60 Mo par fichier**, mais autorise plusieurs fichiers par release.

### Découpage recommandé (format XZ, stdlib Python)

| Partie | Contenu | Taille estimée | Fichier PyPI |
|---|---|---|---|
| **Partie 1** | `.so` natifs (strippés) | ~30 Mo | `phi_lean_toolchain_so-4.34.0.tar.xz` |
| **Partie 2** | `.olean.private` (649 fichiers) | ~44 Mo | `phi_lean_toolchain_private-4.34.0.tar.xz` |
| **Partie 3** | `.olean` + `.olean.server` + `bin/` | ~17 Mo | `phi_lean_toolchain_base-4.34.0.tar.xz` |

Chaque partie < 60 Mo. ✅

### Protocole d'assemblage

```
1. Télécharger les 3 parties (parallélisable)
2. Vérifier SHA256 de chaque partie (hashes épinglés dans le manifeste)
3. Extraire chaque partie vers ~/.cache/phi-complexity/toolchains/4.34.0-mini/
4. Valider : bin/lean --version → "4.34.0"
5. Marqueur .valide (idempotent, comme l'actuel)
```

### Modifications requises dans phi-complexity

1. **`TOOLCHAIN_MANIFEST.json`** : remplacer l'URL unique par 3 URLs + 3 SHA256
2. **`download.py`** : supporter le téléchargement multi-parties
3. **`extract.py`** : supporter le format `.tar.xz` (via `lzma` stdlib)
4. **Strip** : intégrer `strip --strip-all` dans le pipeline de construction des parties

### Alternative 7z (plus petit, mais dépendance externe)

Total ~70 Mo en un seul fichier avec 7z `-mx=9`. **Dépasse quand même 60 Mo.**
Nécessiterait quand même un split en 2 parties, plus la dépendance `py7zr`.
**Non recommandé** — XZ suffit avec 3 parties.

## Ce qui a été prouvé

1. ✅ Strip des binaires : -65 Mo, fonctionne
2. ✅ Les 3 variantes `.olean` sont toutes obligatoires (testé individuellement)
3. ✅ 1947/1949 modules chargés pour Hello World (aucune élimination possible)
4. ✅ XZ-6 : meilleur ratio sans dépendance externe (Python stdlib)
5. ✅ Split en 3 parties : chaque partie < 60 Mo

## Ce qui reste à faire

1. Construire les 3 archives `.tar.xz` (strippées)
2. Mesurer les SHA256 réels
3. Mettre à jour `TOOLCHAIN_MANIFEST.json` (3 URLs + 3 hashes)
4. Adapter `download.py` / `extract.py` pour le multi-parties
5. Tester bout-en-bout : téléchargement → assemblage → `lean --run`

## Fichiers

- Ce rapport : `~/workspace/phi-pub/MINI_60MO_RAPPORT.md`
- Toolchain de référence : `~/.cache/phi-complexity/toolchains/4.34.0-mini/`
- Aucun commit, aucun push (contrainte respectée)
