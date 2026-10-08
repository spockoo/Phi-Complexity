# MATHLIB_MIGRATION_SPEC.md — Spécification du système de migration Mathlib

**Date :** 2026-10-08
**Statut :** SPÉCIFICATION (implémentation partielle existante dans `mathlib.py`)
**Auteur :** Ruche MATHLIB-MINI

## 1. Objectif

Permettre à phi-complexity de maintenir Mathlib à jour sans re-téléchargement
complet aveugle, avec rollback possible, en suivant les mêmes principes que
la migration Lean (`GestionnaireVersions`).

Commandes cibles :
```
phi lean --mathlib-version          # version installée (tag + commit)
phi lean --update-mathlib           # plan de MAJ (sans télécharger)
phi lean --migrer-mathlib <tag>     # migration vers un tag (avec feu vert)
phi lean --migrer-mathlib --dernier # migration vers le dernier tag stable
```

## 2. Architecture existante (déjà implémentée)

Le module `phi_complexity/toolchain/mathlib.py` fournit déjà :

| Fonction | Rôle |
|---|---|
| `tag_pour_lean(version_lean)` | tag Mathlib `vX.Y.Z` compatible via API GitHub |
| `dernier_tag_stable()` | dernier tag non-RC (détection de MAJ) |
| `cache_disponible(commit)` | sonde HEAD sur le marqueur de commit |
| `chemin_cache_mathlib(tag)` | `~/.cache/phi-complexity/mathlib/<tag>/` |
| `version_installee()` / `marquer_installee()` | marqueur `.valide` (idempotence) |
| `plan_mise_a_jour(version_lean)` | plan SANS téléchargement |
| `verifier_feu_vert(plan, feu_vert)` | refuse >100 Mo sans autorisation |

Exceptions typées : `VersionIntrouvable`, `CacheExpire`, `TelechargementRefuse`,
`ErreurReseauMathlib`. Jamais de booléen nu.

## 3. Ce qui manque (à implémenter)

### 3.1 Téléchargement réel

`plan_mise_a_jour()` ne télécharge rien. Il faut :

```
telecharger_mathlib(tag, commit, destination, progression=None, feu_vert=False)
```

- `verifier_feu_vert()` d'abord (refuse sans autorisation si >100 Mo) ;
- récupère le manifeste du cache pour ce commit (liste des `.ltar` + hashs) ;
- télécharge chaque `.ltar`, vérifie le hash de contenu ;
- écriture atomique (`.part` → rename), comme `download.py` ;
- retourne le chemin du dossier téléchargé + la taille réelle.

### 3.2 Format `.ltar`

Le cache Mathlib (depuis sept. 2026) utilise `.ltar` (Lean tar, format Rust),
adressé par hash de contenu : `f/{hash}.ltar`.

**Point d'honnêteté :** le format `.ltar` n'est pas du tar standard.
Options :
1. Utiliser `lake exe cache get` comme sous-processus (recommandé — le
   machinery lake connaît le format) ;
2. Réimplémenter le lecteur `.ltar` en Python (risqué, format non stabilisé).

**Recommandation : option 1.** Le chantier Lake (opt-in, +34 Mo) fournit
le binaire. `phi lean --migrer-mathlib` invoque le lake du cache :
```
<cache>/lake exe cache get
```
depuis un clone mathlib4 au bon tag. Le clone source (~quelques centaines
de Mo en shallow) est nécessaire de toute façon pour `lake`.

### 3.3 Migration avec rollback

S'inspirer de `GestionnaireVersions.migrer()` :

```
migrer_mathlib(tag_cible, feu_vert=False, progression=None)
```

Phases :
1. **Vérification** : `plan_mise_a_jour()` → tag/commit valides, cache dispo.
   Si `deja_installee`, ne rien faire (idempotent).
2. **Téléchargement** : vers `~/.cache/phi-complexity/mathlib/<tag>.part/`.
3. **Extraction/assemblage** : vers `~/.cache/phi-complexity/mathlib/<tag>/`.
4. **Validation** : un fichier témoin existe
   (ex: `Mathlib.olean`), `marquer_installee(tag, commit)`.
5. **Journal** : append dans `.journal-mathlib.jsonl`
   (même format que `.journal-migrations.jsonl`).
6. **Bascule** : mettre à jour le pointeur `.active-mathlib`.

L'ancienne version reste intacte jusqu'à validation complète.
Rollback : `phi lean --revenir-mathlib` (lit le journal, rebascule le pointeur).

### 3.4 Delta inter-versions (le vrai gain)

C'est ici que le design devient "malade" (mot de Tomy) :

Le cache `.ltar` est **adressé par hash de contenu**. Deux versions de Mathlib
partagent la majorité de leurs fichiers identiques (même hash = même `.ltar`).

```
delta_mathlib(tag_source, tag_cible):
    manifeste_source = inventaire(tag_source)  # {chemin: hash}
    manifeste_cible = inventaire(tag_cible)
    a_telecharger = [f for f in cible if hash(f) != hash_source(f)]
    a_supprimer = [f for f in source if f not in cible]
    return Delta(ajoutes, modifies, supprimes, inchanges)
```

**Gain estimé :** entre deux tags proches (ex: v4.34.0 → v4.34.1), la plupart
des 8939 fichiers sont inchangés. Seuls les fichiers modifiés sont
re-téléchargés. C'est le même principe que `delta.py` pour la toolchain,
mais ici le hash de contenu est natif au format de distribution.

**Implémentation :**
- `manifeste.py` (existant) : `inventaire_olean(dossier)` → `{chemin: sha256}`.
- Nouveau : `phi_complexity/toolchain/mathlib_delta.py` ou extension de `delta.py`.
- Le téléchargement sélectif par hash réutilise `telecharger()` existant.

### 3.5 Nettoyage

```
phi lean --nettoyer-mathlib [--oui]
```
- Dry-run par défaut (liste ce qui serait supprimé + taille récupérée).
- Ne jamais supprimer la version active ni la précédente (rollback).
- Journalise le nettoyage.

## 4. Miniaturisation Mathlib (portabilité)

### 4.1 Constats

| Approche | Verdict |
|---|---|
| Élagage par namespace (`Mathlib/Analysis/` seul) | **DANGEREUX** — dépendances transitives denses, casserait les imports |
| Suppression `.olean.private` / `.server` | **À TESTER** — pour Lean core les 3 variantes étaient obligatoires ; à vérifier pour Mathlib |
| Découpage en archives <60 Mo | **VIABLE** — même méthode que la toolchain (3 parties) |
| Compression XZ | **VIABLE** — stdlib Python, ~15-20% sur oleans |

### 4.2 Stratégie recommandée

1. **D'abord : portabilité, pas amputation.** Découper le Mathlib complet
   en N archives <60 Mo (comme les 3 parties Lean). Chaque partie =
   un sous-ensemble de namespaces avec SHA256.
2. **Ensuite (optionnel) : subset par fermeture de dépendances.**
   - Parser les imports des `.olean` (le parseur existe dans phi ?) ;
   - partir de points d'entrée (ex: `Mathlib.Analysis.*` pour Navier-Stokes) ;
   - calculer la fermeture transitive ;
   - ne garder que la fermeture.
   - **Risque :** toute omission = import cassé. À valider par `lean --run`.

### 4.3 Format d'archive portable

```
phi_mathlib_<tag>_part<N>.tar.xz
```
- Nommage : `phi_mathlib-v4.34.1-p1.tar.xz`, etc.
- Chaque archive <60 Mo (contrainte PyPI future).
- `SHA256SUMS.txt` accompagnant.
- Manifeste JSON par partie : liste des fichiers + version + tag.

## 5. Intégration CLI

```
phi lean --mathlib-version
phi lean --update-mathlib              # plan seul
phi lean --migrer-mathlib <tag> [--oui]
phi lean --migrer-mathlib --dernier [--oui]
phi lean --revenir-mathlib
phi lean --nettoyer-mathlib [--oui]
```

Le flag `--oui` saute la confirmation interactive (pour les ruches).

## 6. Tests requis

- `tests/test_mathlib_migration.py` : réseau mocké.
  - plan sans téléchargement ;
  - refus sans feu vert ;
  - delta entre deux manifestes fictifs ;
  - rollback via journal ;
  - idempotence (`deja_installee`).
- `py_compile` 3.11 + 3.12.

## 7. Ordre d'implémentation

1. ✅ `plan_mise_a_jour()` + `verifier_feu_vert()` (fait)
2. `telecharger_mathlib()` via `lake exe cache get` (sous-processus)
3. `migrer_mathlib()` + journal + rollback
4. `delta_mathlib()` (inventaire + comparaison)
5. Découpage portable en N parties <60 Mo
6. CLI complète

## 8. Risques

- **Cache expiré** : le cache ne garde pas les vieilles versions. Si le tag
  visé n'a plus de cache, `CacheExpire` est levée avec un message clair.
  Mitigation : viser des tags récents, ou compiler depuis les sources
  (plusieurs heures — à éviter).
- **Format `.ltar`** : si `lake` n'est pas disponible, pas de lecture.
  Mitigation : dépendance explicite sur le chantier Lake (opt-in).
- **Taille** : plusieurs Go. Le delta par hash est le seul moyen réaliste
  de rendre les MAJ supportables.
