# RUCHE-MATHLIB-UPDATE : Système de mise à jour Mathlib

**Date :** 2026-10-08
**Statut :** RECHERCHE TERMINÉE — implémentation partielle (plan + garde-feu vert)
**Règle Tomy :** aucun téléchargement >100 Mo sans feu vert explicite

## 1. Réponses aux questions de recherche

### 1.1 Où télécharger Mathlib précompilé ?

**Le cache officiel** : `https://cache.mathlib.org` (nouveau, depuis sept. 2026)
- Fallback : `https://lakecache.blob.core.windows.net` (Azure historique)
- Miroir Cloudflare : `https://mathlib4.lean-cache.cloud`

**Pas de releases GitHub** : mathlib4 ne publie aucun artefact précompilé
sur GitHub Releases (0 assets sur tous les tags vérifiés).

**Format** : fichiers `.ltar` (Lean tar), format personnalisé implémenté
en Rust (`digama0/leangz`). Chaque `.ltar` contient :
- `.olean` (compilé Lean)
- `.ilean` (infos d'interface)
- `.trace` (traces de build)
- `.c` (code C généré)
- `.hash` (hashes associés)

**Structure d'URL** (cf. `Cache/Infra.lean` dans mathlib4) :
```
{base}/{conteneur}/{chemin}
```
- Base : `https://cache.mathlib.org`
- Conteneur principal : `mathlib4-master`
- Marqueur de commit : `m/leanprover-community/mathlib4/{sha}`
- Fichiers : `f/{hash}.ltar` (adressé par hash de contenu)

**Paquets couverts** : Mathlib, Batteries, Aesop, Cli, ImportGraph,
LeanSearchClient, Plausible, Qq, ProofWidgets, Archive, Counterexamples.

### 1.2 Taille du téléchargement

- **Plusieurs Go**. Sources : "a few gigabytes", "~7 GB" pour un checkout complet.
- Notre estimation prudente : **≥2 Go** (borne basse).
- **Dépasse largement le seuil de 100 Mo** → feu vert Tomy requis.

### 1.3 Compatibilité des versions

**Mécanisme** : mathlib4 tague ses releases `vX.Y.Z` en suivant les versions Lean.
- Lean 4.34.0 → tag mathlib4 `v4.34.0` → commit `5ed2965256430c3649e86755f9576b54eca72435`
- Vérifié via l'API GitHub (`/repos/leanprover-community/mathlib4/git/refs/tags/v4.34.0`)

**Détermination programmatique** (`tag_pour_lean()`) :
1. Normaliser la version Lean ("v4.34.0" → "4.34.0")
2. Interroger l'API GitHub pour le tag `v4.34.0`
3. Résoudre le SHA (gère les tags annotés)

**Avertissement critique** : les oleans sont spécifiques à la toolchain.
Un bump de Lean sans bump correspondant de Mathlib = cache inutilisable.

### 1.4 Rétention du cache — découverte importante

**Le cache v4.34.0 semble expiré** (HTTP 404 sur le marqueur).
Le cache ne conserve pas indéfiniment les anciennes versions.

**Implication** : si Tomy veut Mathlib pour Lean 4.34.0 spécifiquement,
le cache précompilé pourrait ne plus exister. Options :
1. Viser une version Lean plus récente (dont le cache existe)
2. Compiler Mathlib depuis les sources (plusieurs heures)
3. Vérifier au moment du téléchargement (le code le fait via `cache_disponible()`)

### 1.5 Stratégie de mise à jour

**Pas de delta** : le cache est adressé par hash de contenu. Chaque fichier
est indépendant ; seuls les fichiers modifiés ont un nouveau hash.
Le "delta" est naturel : on ne télécharge que les `.ltar` manquants.

**Détection de nouvelle version** (`dernier_tag_stable()`) :
1. Lister les tags GitHub (30 derniers)
2. Prendre le premier `vX.Y.Z` stable (sans `-rc`, `-beta`)
3. Comparer avec la version installée

**Versions multiples en cache** : oui, le design le permet.
`~/.cache/phi-complexity/mathlib/<tag>/` — chaque version isolée.
Le marqueur `.valide` indique la version active.

## 2. Implémentation livrée

### 2.1 Module `phi_complexity/toolchain/mathlib.py`

| Fonction | Rôle |
|----------|------|
| `tag_pour_lean(version)` | Trouve le tag mathlib4 pour une version Lean |
| `dernier_tag_stable()` | Dernier tag stable (détection de MAJ) |
| `cache_disponible(commit)` | Vérifie l'existence du cache (HEAD léger) |
| `url_marqueur(commit)` | Construit l'URL du marqueur |
| `url_base_cache()` | Base configurable via `MATHLIB_CACHE_BASE_URL` |
| `plan_mise_a_jour(version_lean)` | Plan SANS téléchargement (étude) |
| `verifier_feu_vert(plan, feu_vert)` | Garde : refuse si >100 Mo sans autorisation |
| `version_installee()` / `marquer_installee()` | État local idempotent |
| `chemin_cache_mathlib(tag)` | Chemin isolé par version |

**Exceptions typées** : `VersionIntrouvable`, `ErreurReseauMathlib`,
`CacheExpire`, `TelechargementRefuse`, `ErreurMathlib` (base).

### 2.2 CLI

```bash
phi lean --mathlib-version   # affiche la version installée (ou "non installée")
phi lean --update-mathlib    # affiche le plan, refuse sans feu vert si >100 Mo
```

Le `--update-mathlib` actuel :
1. Détermine le tag compatible
2. Vérifie le cache (lève `CacheExpire` si absent)
3. Affiche le plan (tag, commit, taille estimée)
4. **Refuse** avec `TelechargementRefuse` si >100 Mo sans feu vert
5. Le téléchargement réel (`.ltar` + extraction) reste à implémenter
   après le feu vert de Tomy

### 2.3 Tests

`tests/test_mathlib.py` : **21 tests, tous verts**, réseau entièrement mocké.
- `tag_pour_lean` : tag simple, préfixe `v`, tag annoté, version illisible, tag absent
- `dernier_tag_stable` : ignore les `-rc`, erreur si aucun stable
- `cache_disponible` : présent, absent (404), erreur réseau
- URLs : format du marqueur, miroir via variable d'environnement
- `version_installee` : absente, marquer/lire, marqueur corrompu
- `plan_mise_a_jour` : plan complet, cache expiré
- `verifier_feu_vert` : refuse sans autorisation, accepte avec, pas de garde si petit

`py_compile` : Python 3.11 + 3.12 OK.

## 3. Ce qui reste à faire (après feu vert Tomy)

1. **Téléchargement `.ltar`** : implémenter le fetch des fichiers par hash
   - Nécessite le manifeste du commit (liste des hashes)
   - Ou : cloner mathlib source pour calculer les hashes locaux
2. **Extraction `.ltar`** : implémenter l'extraction du format Lean tar
   - Option A : porter `leantar` (Rust) en Python
   - Option B : appeler un binaire `leantar` externe
   - Option C : vérifier si `tarfile` stdlib peut lire le format
3. **Intégration LEAN_PATH** : ajouter le cache mathlib au `LEAN_PATH`
   dans `ToolchainManager.compiler()` / `executer()`
4. **Test bout-en-bout** : téléchargement réel (plusieurs Go)

## 4. Variables d'environnement

| Variable | Rôle | Défaut |
|----------|------|--------|
| `MATHLIB_CACHE_BASE_URL` | Miroir du cache | `https://cache.mathlib.org` |
| `PHI_TOOLCHAIN_CACHE` | Racine du cache | `~/.cache/phi-complexity` |

## 5. Fichiers

- Module : `phi_complexity/toolchain/mathlib.py`
- Tests : `tests/test_mathlib.py` (21 tests)
- Spec : `MATHLIB_UPDATE_SPEC.md` (ce fichier)
- Exporté dans `phi_complexity/toolchain/__init__.py`

**Contraintes respectées** : local uniquement, aucun commit, aucun push.
Aucun téléchargement >100 Mo effectué (étude uniquement).
