# LEAN_PATCH_SPEC — Système de patch/delta entre versions de toolchain

**Date :** 2026-10-08
**Statut :** IMPLÉMENTÉ — `phi_complexity/toolchain/manifeste.py`, `delta.py`, `ToolchainManager.mettre_a_jour()`
**Tests :** 9/9 verts (`tests/test_patch.py`), réseau mocké

## 1. Problème

Tomy veut éviter de re-télécharger 580 Mo à chaque mise à jour de Lean.

## 2. Recherche : que peut-on réellement économiser ?

### 2.1 L'écosystème Lean n'a pas de delta

- **elan** : télécharge les toolchains complètes, aucun mécanisme de delta.
- **GitHub Releases** : uniquement des tarballs monolithiques (vérifié : 10 assets pour v4.34.0, tous complets, aucun delta).
- **Conclusion** : aucun système de patch n'existe dans l'écosystème Lean.

### 2.2 Le téléchargement ne peut pas être évité (sans infrastructure)

Les releases Lean sont des `.tar.zst` monolithiques. Pour obtenir UN fichier
de la nouvelle version, il faut télécharger TOUT le tarball (580 Mo).

Options écartées :
- **Requêtes HTTP range** : le format zstd des releases n'est pas seekable ;
  même avec un index, la complexité/fragilité ne vaut pas le gain.
- **Deltas binaires (bsdiff)** : nécessiterait d'héberger nos propres fichiers
  de delta — infrastructure à maintenir, hors scope.
- **Téléchargement par fichier** : nécessiterait d'extraire et d'héberger
  1952 fichiers — même problème d'infrastructure.

### 2.3 Ce qu'on PEUT économiser : l'extraction et les I/O

Même si le téléchargement est incompressible, on peut :
1. **Ne pas ré-extraire les fichiers inchangés** (gain I/O disque) ;
2. **Utiliser des hardlinks** pour les fichiers inchangés (zéro copie) ;
3. **Tracer précisément** ce qui a changé (audit, pas de boîte noire) ;
4. **Garder l'ancienne version intacte** (rollback trivial).

### 2.4 Stabilité des fichiers entre versions (estimation honnête)

Les `.olean` sont des artefacts de compilation : même un changement mineur
du compilateur invalide TOUS les `.olean` (le format inclut un tampon de
version). Entre 4.34.0 et 4.34.1 (patch release) :
- `bin/lean` : change (binaire recompilé) ;
- `*.so` : changent (recompilés) ;
- `*.olean` : changent TOUS (tampon de version) ;
- **Estimation : 90-100% des fichiers ont un hash différent.**

Le gain réel du delta par fichier est donc FAIBLE pour les `.olean`
(ils changent tous), mais le système reste utile pour :
- la traçabilité (savoir exactement ce qui a changé) ;
- les futurs cas où seule une partie change (ex: ajout d'un module) ;
- la fondation d'un vrai système de delta si nous hébergeons un jour
  nos propres fichiers.

## 3. Conception

### 3.1 Manifeste par fichier (`manifeste.py`)

Chaque installation génère `.manifeste-fichiers.json` :
```json
{
  "version": "4.34.0",
  "genere_le": "2026-10-08T07:40:00+00:00",
  "fichiers": {
    "bin/lean": "abc123...",
    "lib/lean/Init.olean": "def456..."
  }
}
```
- 1952 fichiers → 236 Ko (négligeable).
- Généré après chaque installation/mise à jour.
- Les marqueurs (`.valide`, `.manifeste-fichiers.json`, `lean.tar.zst`)
  et les liens symboliques sont exclus.

### 3.2 Calcul de delta (`delta.py`)

```python
delta = calculer_delta(manifeste_ancien, manifeste_nouveau)
# → {"ajoutes": [...], "modifies": [...], "supprimes": [...], "inchanges": N}
```

Sémantique :
- **ajoutes** : dans le nouveau, absent de l'ancien → à extraire.
- **modifies** : même chemin, hash différent → à extraire.
- **supprimes** : dans l'ancien, absent du nouveau → à effacer.
- **inchanges** : même chemin, même hash → hardlink (zéro copie).

### 3.3 Extraction sélective (`extract.py`)

Nouvelle fonction `extraire_fichiers(archive, dest, fichiers)` :
- Prend une liste EXPLICITE de chemins (pas de motifs fnmatch).
- Sortie anticipée quand tous les fichiers sont trouvés.
- Mêmes gardes anti-traversal que `extraire_minimal`.

### 3.4 Mise à jour (`ToolchainManager.mettre_a_jour()`)

Protocole :
1. Télécharge le nouveau tarball (complet — pas de delta officiel).
2. Extrait le minimal vers un répertoire temporaire.
3. Génère le manifeste par fichier de la nouvelle version.
4. Calcule le delta avec l'ancien manifeste.
5. Crée le nouveau répertoire de version :
   - fichiers inchangés → **hardlink** depuis l'ancien (instantané) ;
   - fichiers ajoutés/modifiés → copie depuis l'extraction temp.
6. Valide (`lean --version`) et marque (`.valide`).
7. L'ancienne version reste intacte (rollback possible).

```
Avant : 4.34.0-mini/ (1952 fichiers, 522 Mo)
Après : 4.34.0-mini/ (intact) + 4.35.0-mini/ (1952 fichiers,
        dont ~1900 hardlinks = ~0 octet supplémentaire pour les inchangés)
```

### 3.5 Plan sans téléchargement (`plan_mise_a_jour()`)

Retourne les métadonnées de la mise à jour (versions, URL, taille)
SANS télécharger — permet à l'utilisateur de voir ce qui va se passer
avant de confirmer.

## 4. API

```python
from phi_complexity.toolchain import (
    ToolchainManager,
    generer_manifeste, lire_manifeste,
    calculer_delta, fichiers_a_extraire, resumer_delta,
)

# Générer le manifeste d'une installation
m = generer_manifeste("/path/to/4.34.0-mini", "4.34.0")

# Calculer un delta
delta = calculer_delta(m_ancien, m_nouveau)
print(resumer_delta(delta))

# Mise à jour complète
mgr = ToolchainManager()
plan = mgr.plan_mise_a_jour({
    "version": "4.35.0",
    "url": "https://...",
    "sha256": "abc...",
})
resultat = mgr.mettre_a_jour({
    "version": "4.35.0",
    "url": "https://...",
    "sha256": "abc...",
})
# → {"statut": "MIS_A_JOUR", "delta": {...},
#    "fichiers_lies": 1900, "fichiers_copies": 52}
```

## 5. CLI (à câbler)

```
phi lean --update          # vérifie, affiche le plan, demande confirmation
phi lean --update --oui    # sans confirmation (si < 100 Mo)
```

Le CLI n'est pas encore câblé — le module est prêt, il reste à ajouter
les arguments dans `cli.py` (fonction `_executer_lean`).

## 6. Limites honnêtes

1. **Le téléchargement de 580 Mo reste nécessaire.** Aucun delta officiel
   n'existe. Le système économise l'extraction et les I/O, pas la bande
   passante.
2. **Entre versions patch, ~100% des `.olean` changent** (tampon de version
   du compilateur). Le gain du delta est maximal quand peu de fichiers
   changent (ex: ajout d'un module sans recompilation).
3. **Les hardlinks nécessitent le même filesystem.** Fallback automatique
   vers `shutil.copy2` si `os.link` échoue.
4. **Le CLI `--update` n'est pas encore câblé.** Le module est testé et
   fonctionnel, l'intégration CLI reste à faire.

## 7. Fichiers

- `phi_complexity/toolchain/manifeste.py` — génération/lecture du manifeste
- `phi_complexity/toolchain/delta.py` — calcul de delta
- `phi_complexity/toolchain/extract.py` — `extraire_fichiers()` ajouté
- `phi_complexity/toolchain/manager.py` — `mettre_a_jour()`, `plan_mise_a_jour()`
- `phi_complexity/toolchain/__init__.py` — exports
- `tests/test_patch.py` — 9 tests, réseau mocké
- Ce spec : `LEAN_PATCH_SPEC.md`

## 8. Travaux futurs

- Câbler `phi lean --update` dans le CLI.
- Si nous hébergeons un jour nos propres fichiers (par hash, comme le
  cache Mathlib), le delta par fichier permettra de ne télécharger QUE
  les fichiers modifiés — le système est déjà prêt pour ça.
- Nettoyage des anciennes versions (`phi lean --nettoyer`).
