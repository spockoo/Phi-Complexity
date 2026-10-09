"""sched — ordonnanceur quasicristallin pour granularité fine.

L'ordonnancement apériodique sturmien bat le prototype naïf (1,75-1,86× vs 1,47×)
là où le multiprocessing classique échouait (1,08×).

Classes :
- OrdonnanceurQuasicristal : ordonnanceur principal
- StrategieDensiteQuasicristal : distribution par densité quasicristalline
- StrategiePrioriteVieillissement : stratégie alternative
- fabrique_quasicristal : fabrique avec stratégie par défaut
"""

from .quasicristal_sched import (
    OrdonnanceurQuasicristal,
    StrategieDensite,
    StrategieDensiteQuasicristal,
    StrategiePrioriteVieillissement,
    fabrique_quasicristal,
)

__all__ = [
    "OrdonnanceurQuasicristal",
    "StrategieDensite",
    "StrategieDensiteQuasicristal",
    "StrategiePrioriteVieillissement",
    "fabrique_quasicristal",
]
