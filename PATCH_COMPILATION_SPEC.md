# PATCH-COMPILATION : Compilation incrémentale inter-versions Lean

**Date :** 2026-10-08
**Statut :** TERMINÉ — Verdict rendu avec preuves

## Verdict

| Scénario | Verdict |
|---|---|
| **B** (nouvelle version Lean, réutiliser oleans) | **IMPOSSIBLE** — prouvé par test |
| **A** (même version Lean, Mathlib MAJ) | **VIABLE** — mais Lake le fait déjà |
| **C** (code utilisateur, même version) | **VIABLE** — mtime-based, ~50 lignes |

## Preuve d'impossibilité inter-versions

**Test réel effectué le 2026-10-08 :**

```bash
# Compiler avec 4.34.0
lean-4.34.0 -o Test4340.olean Test4340.lean  # ✓ OK

# Charger avec 4.34.1
LEAN_PATH=/tmp/patchtest lean-4.34.1 Use4340.lean
# → error: failed to read file 'Test4340.olean', incompatible header
```

**Cause physique** (header olean, octets 0-47) :
```
6f 6c 65 61 6e 02 01 34 2e 33 34 2e 30 00 00 00
>olean..4.34.0...<
293d5d0c0c3f3dded4688b3ccd6a33939ac5102b
```

Le header contient :
1. La version Lean (`4.34.0`)
2. Le hash git exact du commit (`293d5d0c...`)

**Double verrouillage** : même deux builds "4.34.0" de commits différents sont incompatibles.

**Conséquence** : aucun système de patch ne peut réutiliser des `.olean` entre versions Lean différentes. Le "patch de compilation inter-versions" est physiquement impossible.

## Ce que fait Lake (existant)

Lake implémente déjà la compilation incrémentale **intra-version** :
- Tracking par mtime (fichier source plus récent que l'artefact → rebuild)
- Graphe de dépendances topologique (via `lean --deps`)
- Stockage des artefacts intermédiaires

**Notre toolchain mini n'a pas Lake** (binaire `lake` = 13 Ko wrapper, mais nécessite tout l'écosystème).

## Données mesurées

| Composant | Taille | Dans notre mini ? |
|---|---|---|
| Sources `.lean` (2520 fichiers) | 27 Mo | **Non** (0 fichiers) |
| Binaire `lake` | 13 Ko (wrapper) | Non |
| Binaire `leanc` | 36 Ko (wrapper) | Non |

**Sans sources `.lean`, aucune compilation depuis les sources n'est possible.** Notre toolchain ne peut qu'exécuter (`--run`) et vérifier, pas compiler de nouveaux modules depuis zéro.

## Scénarios détaillés

### Scénario A : Même version Lean, Mathlib mise à jour

**Viable en théorie.** Si seuls 50 modules Mathlib changent sur 8939 :
- Recompiler uniquement les 50 + leurs dépendants transitifs
- `lean --deps` donne le graphe de dépendances
- Mtime tracking comme `make` (~50 lignes de Python)

**Mais** : c'est exactement ce que `lake exe cache get` + `lake build` font déjà. Réimplémenter = dupliquer Lake.

**Recommandation** : utiliser Lake (ou le cache précompilé) plutôt que réinventer.

### Scénario B : Nouvelle version Lean

**Impossible.** Voir preuve ci-dessus.

Quand Lean passe de 4.34.0 à 4.34.1 :
- TOUS les oleans doivent être recompilés depuis les sources
- Aucun artefact n'est réutilisable
- Le "patch" ne peut pas exister au niveau olean

**La seule optimisation possible** : le patch au niveau **téléchargement** (ne télécharger que les sources modifiées), pas au niveau compilation.

### Scénario C : Code utilisateur (nos fichiers)

**Viable et simple.** Pour les fichiers `.lean` de Tomy :
```python
# Pseudo-code (~50 lignes)
def a_besoin_rebuild(source, artefact):
    return (not os.path.exists(artefact) or
            os.path.getmtime(source) > os.path.getmtime(artefact))

def compiler_incremental(fichiers):
    ordre = tri_topologique(fichiers)  # via lean --deps
    for f in ordre:
        if a_besoin_rebuild(f, f + ".olean"):
            compiler(f)
```

**Test réel** : chaîne Base → Middle → Top, modification de Base :
- Rebuild complet manuel : 1.7s (3 modules)
- Avec tracking mtime : ne recompilerait que ce qui dépend de Base

## Recommandations

1. **Ne pas tenter** le patch compilation inter-versions (impossible physique)
2. **Pour Mathlib** : utiliser le cache précompilé officiel (quand disponible)
3. **Pour le code utilisateur** : implémenter le tracking mtime simple (~50 lignes) dans `phi lean`
4. **Ajouter les sources `.lean`** (27 Mo) à la toolchain si on veut compiler depuis zéro
5. **Le vrai gain** est au niveau téléchargement (patch binaire), pas compilation

## Fichiers de test

- `/tmp/patchtest/` : modules de test (nettoyable)
- Toolchains testées : `~/.cache/phi-complexity/toolchains/4.34.0-mini/` et `4.34.1-mini/`
