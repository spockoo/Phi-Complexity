# LEAN_MIGRATION_SPEC — Système de migration entre versions Lean

**Date :** 2026-10-08
**Statut :** IMPLÉMENTÉ (local uniquement, jamais pushé)
**Mission :** RUCHE-LEAN-MIGRATION

## 1. Problème

Les patchs inter-versions sont physiquement impossibles : les `.olean` sont
verrouillés par version (header contenant version + hash git exact du commit —
prouvé par test : `incompatible header` en chargeant un olean 4.34.0 sous 4.34.1).

Mais la transition entre versions doit être fluide, sûre et réversible.
C'est le rôle du système de migration : pas un delta binaire, un protocole
de bascule.

## 2. Architecture

```
~/.cache/phi-complexity/toolchains/
├── 4.34.0-mini/          # installation complète, intacte
│   ├── bin/lean
│   ├── lib/lean/...
│   └── .valide           # {"version": "4.34.0", "sha256": ..., "natif": ...}
├── 4.34.1-mini/          # installation complète, intacte
│   └── .valide
├── .active                # "4.34.1\n" — pointeur de version active
└── .journal-migrations.jsonl  # journal append-only
```

### 2.1 Pointeur de version active (`.active`)

Un fichier texte contenant la version active (ex. `4.34.1`).
- Écrit atomiquement lors de `definir_active()` / `migrer()`.
- Si absent ou pointant vers une version désinstallée : repli sur la version
  du manifeste embarqué si installée (comportement historique préservé).
- `phi lean` utilise toujours la version active via `manager_actif()`.

### 2.2 Journal (`.journal-migrations.jsonl`)

Append-only, une ligne JSON par événement :
```json
{"date": "2026-10-08T12:00:00+0000", "evenement": "migration",
 "de": "4.34.0", "vers": "4.34.1", "raison": "...", "telechargee": true}
{"date": "...", "evenement": "bascule", "de": "4.34.1", "vers": "4.34.0",
 "raison": "rollback manuel"}
{"date": "...", "evenement": "nettoyage", "supprimees": ["4.34.0"]}
{"date": "...", "evenement": "migration_refusee", "vers": "4.35.0",
 "raison": "téléchargement >100 Mo sans feu vert"}
```

Événements : `migration`, `bascule`, `nettoyage`, `migration_refusee`.

### 2.3 Registre des versions (`VERSIONS_CONNUES.json`)

SHA256 épinglés mesurés (pas d'estimations) :
```json
{
  "4.34.0": {"url": "https://github.com/leanprover/lean4/releases/download/...",
             "sha256": "caaa9835...", "taille_octets_approx": 608583270},
  "4.34.1": {...}
}
```

Seules les versions du registre sont migrables (sécurité : pas de SHA256
inconnu = pas de téléchargement). Pour ajouter une version : mesurer le
SHA256 du tarball officiel et l'ajouter au registre.

## 3. Protocole de migration

`GestionnaireVersions.migrer(version_cible, progression, raison, confirmer)`

| Phase | Action | Réversibilité |
|---|---|---|
| 1. Vérification | Version connue ? Déjà active ? Déjà installée ? | N/A (lecture seule) |
| 2. Téléchargement | Si non installée : `ToolchainManager.installer()` avec le manifeste de la cible. Si >100 Mo et `confirmer` fourni : demander d'abord, sinon lever `TelechargementRefuse` + journaliser | L'ancienne version n'est pas touchée |
| 3. Validation | Faite par `installer()` (`lean --version` contient la version) | L'ancienne reste intacte |
| 4. Bascule | Écriture atomique de `.active` | `revenir()` annule |
| 5. Journal | Entrée `migration` dans le journal | Append-only (historique) |

**Garantie :** l'ancienne version reste 100% intacte jusqu'à la fin de la
phase 4. Un échec en phase 2 ou 3 ne change rien à l'état actif.

## 4. Rollback

`GestionnaireVersions.revenir(raison)` :
1. Parcourt le journal (plus récent d'abord).
2. Trouve le dernier événement `migration`/`bascule` dont `de` est une
   version installée différente de l'active.
3. Bascule vers elle via `definir_active()`.
4. Lève `RollbackImpossible` si aucun candidat.

Le rollback est lui-même une bascule journalisée — on peut donc
"revenir du revenir".

## 5. Nettoyage

`GestionnaireVersions.nettoyer(garder=None, confirmer=None)` :
- Ne touche jamais à la version active.
- `garder` : versions supplémentaires à conserver.
- Sans `confirmer` et avec des suppressions nécessaires : lève
  `MigrationImpossible` (le CLI doit demander confirmation explicite).
- `confirmer` retournant False : `{"statut": "NETTOYAGE_ANNULE"}`.
- Sinon : `shutil.rmtree` + journal.

## 6. Détection de projets

`GestionnaireVersions.projets_utilisant(version, racine)` :
- `os.walk` en excluant `.git`, `.lake`, `build`, `__pycache__`, `.cache`, `node_modules`.
- Pour chaque `lean-toolchain` : `analyser_contenu()` (réutilise `version.py`).
- Retourne les projets dont la version demandée == version cible.

Usage : avant une migration, `phi lean --migrer` peut signaler les projets
impactés. La migration des projets eux-mêmes (recompilation) reste manuelle
et confirmée — jamais automatique.

## 7. CLI

```
phi lean --migrer 4.35.0     # protocole complet (confirmation si >100 Mo)
phi lean --migrer 4.35.0 --oui  # sans confirmation interactive
phi lean --versions          # liste les versions installées + active
phi lean --active             # affiche la version active
phi lean --revenir            # rollback vers la précédente
phi lean --revenir --oui      # (pas de confirmation requise pour revenir)
phi lean --nettoyer           # dry-run : montre ce qui serait supprimé
phi lean --nettoyer --oui     # supprime réellement
```

`phi lean` (sans option) et `phi lean fichier.lean` utilisent désormais la
version active via `GestionnaireVersions().manager_actif()`.

## 8. Sécurité

- **Aucun téléchargement >100 Mo sans feu vert** : le paramètre `confirmer`
  est appelé avant tout gros téléchargement ; le CLI demande confirmation
  interactive sauf `--oui`.
- **Chaque étape réversible** : installation dans un nouveau répertoire,
  bascule atomique, rollback par journal.
- **Pas de migration de projet automatique** : détection seule, l'utilisateur
  décide.
- **Statuts typés** : `VersionInconnue`, `MigrationImpossible`,
  `RollbackImpossible`, `TelechargementRefuse` — jamais de booléen nu.

## 9. Fichiers

| Fichier | Rôle |
|---|---|
| `phi_complexity/toolchain/migration.py` | `GestionnaireVersions` + exceptions + `manifeste_pour_version()` |
| `phi_complexity/toolchain/VERSIONS_CONNUES.json` | Registre URL+SHA256 par version |
| `phi_complexity/toolchain/manager.py` | `_lire_manifeste()` accepte un dict (changement minimal) |
| `phi_complexity/toolchain/__init__.py` | Exports du module migration |
| `tests/test_migration.py` | Tests unitaires (réseau mocké) |
| `LEAN_MIGRATION_SPEC.md` | Ce document |

## 10. Limites connues

1. **Pas de delta binaire** : le tarball complet est téléchargé (les releases
   Lean ne fournissent pas de delta officiel ; ~100% des olean changent entre
   versions de toute façon).
2. **Registre manuel** : ajouter une version = mesurer son SHA256 et l'ajouter
   à `VERSIONS_CONNUES.json`. Pas de découverte automatique (sécurité).
3. **Mathlib non couverte** : la migration ne gère que la toolchain Lean.
   Mathlib a son propre système (`mathlib.py`).
4. **Extensions par version** : les extensions `std`/`lean` sont installées
   par version ; migrer ne migre pas les extensions (à réinstaller).

## 11. Tests

`tests/test_migration.py` : réseau systématiquement mocké, dossiers
temporaires isolés (jamais le vrai cache). Couvre : versions_installees,
version_active (avec/sans .active, repli), definir_active, manifeste_pour_version,
migrer (DEJA_ACTIVE, installation mockée, refus de téléchargement),
revenir (ok + impossible), nettoyer (4 cas), projets_utilisant, journal.
