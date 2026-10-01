"""
analyseur.py — Alias de compatibilité ascendante avec phi-complexity 0.1.x.

Dans les versions < 0.2.0, `AnalyseurPhi` était l'unique analyseur (AST Python).
Il reste disponible ici tel quel ; l'architecture multi-langage vit désormais
dans le sous-paquet `phi_complexity.langs`.
"""
from .langs.python_native import AnalyseurPython as AnalyseurPhi
from .modeles import MetriqueFonction, Annotation, ResultatAnalyse

__all__ = ["AnalyseurPhi", "MetriqueFonction", "Annotation", "ResultatAnalyse"]
