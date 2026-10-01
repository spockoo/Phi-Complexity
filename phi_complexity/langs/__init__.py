"""
langs — Sous-paquet d'analyseurs multi-langages de phi-complexity.
"""
from .base import AnalyseurBase
from .python_native import AnalyseurPython
from .registry import (
    obtenir_analyseur,
    langage_pour_extension,
    est_fichier_supporte,
    treesitter_disponible,
    analyseur_disponible,
    EXTENSIONS_SUPPORTEES,
    EXTENSIONS_PYTHON,
    EXTENSIONS_TREESITTER,
    EXTENSIONS_ANALYSEUR_DEDIE,
)

__all__ = [
    "AnalyseurBase",
    "AnalyseurPython",
    "obtenir_analyseur",
    "langage_pour_extension",
    "est_fichier_supporte",
    "treesitter_disponible",
    "analyseur_disponible",
    "EXTENSIONS_SUPPORTEES",
    "EXTENSIONS_PYTHON",
    "EXTENSIONS_TREESITTER",
    "EXTENSIONS_ANALYSEUR_DEDIE",
]
