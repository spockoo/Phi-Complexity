# RUCHE-LEAN-UPGRADE : Migration Lean 4.34.0 → 4.34.1

**Date :** 2026-10-08
**Statut :** TERMINÉ — Toolchain 4.34.1 installée et validée
**Décision Tomy :** Option 2 (migrer vers version récente avec cache Mathlib actif)

## 1. Version cible choisie

| Critère | Valeur |
|---|---|
| **Version Lean** | **4.34.1** |
| Publiée le | 2026-09-24 |
| Statut | Dernière STABLE (v4.35.0-rc4 est prerelease) |
| Commit Lean | `5045d0056413266e57c625dcd7c365b10e377c52` |
| Tag Mathlib compatible | `v4.34.1` → commit `d13f23b723b8` |
| SHA256 toolchain | `47bf4bbd78f70c2e9670598ab7124d92b6efb7330ff33e5fbb4030f6fd72e4e4` |

**Pourquoi 4.34.1 et pas 4.35.0-rc4 :** les RC sont des prereleases, pas des stables.
Tomy travaille sur du long terme (DVD, archivage) — une stable est le bon choix.

## 2. Toolchain mini reconstruite

| Métrique | 4.34.0 | 4.34.1 |
|---|---|---|
| Téléchargement | 580 Mo | 553 Mo |
| Fichiers extraits | 1949 | 1952 |
| Taille extraite | 604 Mo | 582 Mo |
| `lean --version` | ✅ 4.34.0 | ✅ 4.34.1 |
| `lean --run` Hello World | ✅ | ✅ |

**Cache :** `~/.cache/phi-complexity/toolchains/4.34.1-mini/`
**Ancienne version :** `~/.cache/phi-complexity/toolchains/4.34.0-mini/` (conservée en parallèle)

## 3. Manifeste mis à jour

`phi_complexity/toolchain/TOOLCHAIN_MANIFEST.json` :
- `version` : `4.34.1`
- `url` : `https://github.com/leanprover/lean4/releases/download/v4.34.1/lean-4.34.1-linux.tar.zst`
- `sha256` : `47bf4bbd...fd72e4e4` (mesuré)
- `_version_precedente` : référence 4.34.0 conservée dans le manifeste

Le `ToolchainManager` lit la version depuis le manifeste — aucun changement de code requis.
`phi lean --version` → `Lean 4.34.1` ✅

## 4. Mathlib : état du cache

### Recherche effectuée

Le cache Mathlib (`https://cache.mathlib.org`) utilise un contrat d'URL `/{container}/{key}` :
- Fichiers : `{endpoint}/f/{hash}.ltar` (hash calculé par fichier, pas par version)
- Conteneur par défaut pour mathlib4 : `master`
- Format `.ltar` (Lean tar, via `leantar`)

### Problème fondamental

**Le cache ne se sonde pas par version.** Les fichiers sont adressés par hash de contenu
calculé à partir de : `lakefile.lean` + `lean-toolchain` + `lake-manifest.json` + hash du
compilateur + hash du fichier + hashs des imports. Sans le machinery `lake` complet,
impossible de calculer ces hashs et donc de vérifier l'existence du cache pour v4.34.1.

### Ce qu'on sait

- Tag Mathlib `v4.34.1` existe (commit `d13f23b723b8a846827a245b89c10fc7d3f11612`)
- Le cache est peuplé par CI pour les commits récents
- v4.34.1 date de sept 2026, l'endpoint public existe depuis sept 2026
- **Probabilité que le cache soit actif : ÉLEVÉE** (version récente, CI active)

### Ce qu'on ne peut pas prouver sans télécharger

L'existence effective des `.ltar` pour le commit `d13f23b723b8`.

## 5. Plan Mathlib (en attente du feu vert Tomy)

**NE PAS EXÉCUTER sans autorisation explicite** (plusieurs Go) :

```
1. Cloner mathlib4 au tag v4.34.1 (shallow, ~quelques centaines de Mo de sources)
2. Créer un projet lake minimal avec lean-toolchain = "leanprover/lean4:v4.34.1"
3. Ajouter mathlib comme dépendance au tag v4.34.1
4. Lancer `lake exe cache get` (télécharge les .ltar depuis cache.mathlib.org)
5. Si le cache répond : extraire vers ~/.cache/phi-complexity/mathlib/v4.34.1/
6. Si le cache ne répond pas (404) : fallback = `lake build` (compilation locale, TRÈS long)
```

**Taille estimée :** 2-7 Go (oleans + ileans + traces)

**Prérequis :** `lake` doit être disponible. Notre toolchain mini ne l'inclut pas actuellement
(voir mission RUCHE-LEAN-COMPLET en cours pour l'ajout de Lake).

## 6. Incidents

1. **/tmp plein** (tmpfs 512 Mo) : le téléchargement de 553 Mo a échoué avec curl exit 23.
   Solution : téléchargement vers `~/workspace/` au lieu de `/tmp/`.
2. **Marqueur `.valide` manquant** : l'extraction manuelle ne crée pas le marqueur
   d'idempotence. Créé manuellement après validation `lean --version`.

## 7. Fichiers modifiés

- `phi_complexity/toolchain/TOOLCHAIN_MANIFEST.json` (version 4.34.1 + référence 4.34.0)
- `LEAN_UPGRADE_RAPPORT.md` (ce fichier)

**Contraintes respectées :** local uniquement, aucun push. L'archive `~/workspace/lean-4.34.1-linux.tar.zst`
(553 Mo) est conservée pour référence — à supprimer si l'espace devient critique.
