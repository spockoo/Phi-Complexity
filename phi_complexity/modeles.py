"""
modeles.py — Structures de données communes, indépendantes du langage source.
Partagées par tous les analyseurs (Python natif, générique tree-sitter).
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MetriqueFonction:
    """Représente une fonction analysée avec toutes ses métriques brutes."""
    nom: str
    ligne: int
    complexite: int       # Nombre de nœuds (pression morphique)
    nb_args: int          # Nombre d'arguments
    nb_lignes: int        # Longueur en lignes
    profondeur_max: int   # Imbrication maximale
    distance_fib: float   # Éloignement de la séquence naturelle
    phi_ratio: float      # Rapport complexité/moyenne (idéal: φ)
    extraction: str = "analyseur"  # "analyseur" ou "robuste_repli"
    # (durcissement 2026-10-02 : symboles récupérés par l'extracteur
    # robuste quand tree-sitter les a avalés — métriques = proxy lignes)


@dataclass
class Annotation:
    """Une observation chirurgicale sur une ligne spécifique du code."""
    ligne: int
    message: str
    niveau: str           # 'INFO', 'WARNING', 'CRITICAL'
    extrait: str          # La ligne de code concernée
    categorie: str        # 'LILITH', 'SUTURE', 'SOUVERAINETE', 'FIBONACCI'


@dataclass
class ResultatAnalyse:
    """Contient tous les résultats bruts d'une analyse de fichier, tous langages confondus."""
    fichier: str
    langage: str = "python"
    fonctions: List[MetriqueFonction] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    nb_classes: int = 0
    nb_imports: int = 0
    nb_lignes_total: int = 0
    nb_commentaires: int = 0
    oudjat: Optional[MetriqueFonction] = None
