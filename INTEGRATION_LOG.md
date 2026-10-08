# INTEGRATION_LOG.md — Intégration locale Phi-Complexity

**Mission RUCHE-INTEGRATION-PHI-LOCAL — 2026-10-08**
**Règle dure : LOCAL UNIQUEMENT. Aucun push sur le dépôt public.**

## Modules intégrés

| # | Source (Metaprogramme-lean) | Destination (phi-pub local) | Fichiers | Tests |
|---|---|---|---|---|
| 1 | `lilith_instruments/` (core) | `phi_complexity/lilith/` | 9 .py + `__init__.py` | import + fonctionnel OK (3.11/3.12) |
| 2 | `phi_scipy/` | `phi_complexity/scipy_mini/` | 4 .py + `__init__.py` | 10/10 OK |
| 3 | `parse_cache/` | `phi_complexity/cache/` | 4 .py + `__init__.py` | 81/81 OK |
| 4 | `impact_analyse/` | `phi_complexity/impact/` | 4 .py + `__init__.py` | 11/11 OK |
| 5 | `sandbox_mp/quasicristal_sched.py` + `scheduler.py` | `phi_complexity/sched/` | 2 .py + `__init__.py` | import OK (3.11/3.12) |
| 6 | `lecteur_phiast.py`, `lecteur_zz.py`, `compresser_body.py` | `phi_complexity/portable/` | 3 .py + `__init__.py` | import OK (3.11/3.12) |

## Adaptations d'imports

- `lilith/` : core déjà en imports relatifs — aucune adaptation. Scripts de
  mesures (`balayage_*`, `mesures_*`) non copiés (recherche, pas bibliothèque).
- `scipy_mini/` : imports relatifs — aucune adaptation.
- `cache/` : imports relatifs — aucune adaptation.
- `impact/` : fichiers autonomes — aucune adaptation.
- `sched/` : `quasicristal_sched.py` attendait `scheduler` comme paquet
  frère → `scheduler.py` copié dans `sched/`, l'import relatif
  `from . import scheduler` fonctionne.
- `portable/` : `from lecteur_phiast import` → `from .lecteur_phiast import`
  dans `lecteur_zz.py`.

## Intégration CLI

Nouvelle sous-commande `phi lilith` (cli.py) :
- `phi lilith mesurer <fichier.py>` — 6 métriques + F1 + curseur
- `phi lilith batterie <fichier.py>` — batterie tri-domaine
- `phi lilith alpha <fichier.py>` — curseur de Rényi
- `phi lilith f1 <fichier.py>` — instrument F1 seul
- `phi lilith perf` — banc de performance
- Options lilith après `--` : `phi lilith -- --format json mesurer ...`

Délégation à `phi_complexity.lilith.phi_lilith.main(argv)` (contrat existant).

## Non-régression

- `phi --version` : OK (0.15.0, inchangé)
- `phi check` : OK sur les nouveaux fichiers
- `phi lilith mesurer/batterie/f1` : OK (console + JSON)
- `py_compile` 3.11 (ci311) + 3.12 : OK sur tous les nouveaux fichiers

## Fichiers non copiés (volontairement)

- Scripts de mesures/benchmarks (`bench_*`, `mesures_*`, `balayage_*`) :
  restent sur Metaprogramme-lean (recherche, pas bibliothèque).
- `quasi_runner.py` : dépend de `bench_infra`/`orchestrateur` (sandbox_mp
  complet) — le scheduler (`quasicristal_sched.py`) est la pièce
  réutilisable, il est intégré.
- `.md` de documentation : restent sur Metaprogramme-lean.

## Commits

- Commit local uniquement. **PUSH INTERDIT** sur `origin`
  (github.com/spockoo/Phi-Complexity).
- Vérifié : aucun `git push` exécuté pendant la mission.

## Mission PHI-NATIF-D (2026-10-08) — phi_scipy natif dans `phi index`

**Constat d'enquête (honnête)** : `phi index` (`carte_projet`) ne fait AUCUN
calcul de graphe BFS/degrés/composantes/PageRank — vérifié par grep + profilage
(le temps va à l'analyse syntaxique et à l'audit par fichier). Le seul noyau à
structure de graphe est l'incidence bipartie nom×fichier de la détection des
collisions (`_detecter_collisions`, ~0,2 % du temps total).

**Intégration** (`phi_complexity/carte.py`, `phi_complexity/cli.py`) :
- `_detecter_collisions_python` : implémentation d'origine, inchangée (référence).
- `_detecter_collisions_scipy` : backend natif `BinaryCSR` (construction +
  comptage vectorisé des fichiers distincts par nom) — sortie prouvée identique.
- `_detecter_collisions(index, utiliser_scipy_mini=True)` : répartiteur —
  tente scipy_mini, REPLI GRACIEUX vers le Python pur sur toute exception
  (numpy absent y compris : `dependencies = []` reste vrai).
- `carte_projet(..., utiliser_scipy_mini=True)` ; CLI : `phi index --sans-scipy-mini`.
- `@auto_optimize` NON utilisé (justifié en commentaire) : son routage par
  densité choisirait toujours le CSR (incidence toujours ultra-creuse) alors
  que les mesures montrent le Python pur à égalité ou devant aux échelles
  réalistes — le routage par densité aurait systématiquement ralenti.

**Mesures** (dogfooding, dépôt phi-pub : 134 fichiers, 2145 symboles, 105 collisions) :
- JSON byte-identique avec/sans (md5 `f903546ce8f2fb70a1f66435452b0ac1`), console
  byte-identique (md5 `41820de014f5342c84f81df9ac84350a`).
- Noyau collisions seul : 3,55 ms (Python) vs 3,43 ms (CSR) — équivalents.
- `phi index` total : 1,55 s (scipy, défaut) vs 1,44 s (`--sans-scipy-mini`) —
  l'écart vient de l'import numpy (220 ms à froid, une fois par processus),
  pas du noyau. Rapporté honnêtement : pas de gain sur cette charge.

**Tests** : `tests/test_carte.py::TestScipyMini` — équivalence des backends,
équivalence carte complète, repli gracieux (panne simulée), index vide.
`pytest tests/test_carte.py tests/test_vitesse.py tests/test_durcissement.py
tests/test_zero_treesitter_silencieux.py` : 55 passed.

**Commits** : local uniquement sur `feat/dual-licensing`. **PUSH INTERDIT**.
