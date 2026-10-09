# Contributing to Phi-Complexity

Merci de votre intérêt pour Phi-Complexity !

## Double licence

En contribuant à ce projet (pull request, issue avec du code, ou toute
autre contribution de code), vous acceptez que votre contribution soit
placée sous **double licence**, au choix de l'utilisateur final :

1. **GNU General Public License v3** (ou ultérieure) — voir `LICENSE`
2. **Licence Commerciale Phi-Complexity** — voir `LICENSE-COMMERCIAL.md`

Cela signifie que Tomy Verreault (mainteneur) peut distribuer votre
contribution sous l'une ou l'autre de ces licences.

Si vous ne pouvez pas accepter ces conditions, ne contribuez pas de code.

## Règles de contribution

- Le code doit passer les tests existants (`python -m pytest`).
- Tout nouveau module `phi_complexity/` doit avoir une justification
  « but de mission » dans `phi_complexity/mission.py` (voir ce fichier
  pour le format — la CI échoue sinon).
- Tout module prescriptif (qui dit à l'utilisateur quoi faire au lieu
  de montrer des faits) fait échouer les tests — voir
  `tests/test_garde_non_prescriptif.py`.
- Vérifiez avec Python 3.11 (`~/workspace/venvs/ci311/bin/python -m
  py_compile`) avant de proposer — la CI tourne en 3.11.

## Doctrine

L'instrument montre, il ne décide jamais. Les statuts sont typés,
jamais booléens. Voir `README.md` pour la philosophie complète.
