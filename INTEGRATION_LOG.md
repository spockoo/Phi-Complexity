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
