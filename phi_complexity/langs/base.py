"""
langs/base.py — Interface commune à tous les analyseurs (contrat multi-langage).
"""
from abc import ABC, abstractmethod

from ..modeles import ResultatAnalyse


class AnalyseurBase(ABC):
    """
    Contrat que doit respecter tout analyseur de langage.
    Un analyseur transforme un fichier source en ResultatAnalyse générique,
    quel que soit le langage informatique sous-jacent.
    """

    def __init__(self, fichier: str):
        self.fichier = fichier

    @abstractmethod
    def charger(self) -> "AnalyseurBase":
        """Charge et parse le fichier source. Doit retourner self (chaînage)."""
        raise NotImplementedError

    @abstractmethod
    def analyser(self, complet: bool = True) -> ResultatAnalyse:
        """
        Lance l'analyse et retourne le résultat générique.

        `complet=True` (défaut) : analyse complète — toutes les métriques,
        règles souveraines, oudjat. Comportement historique, inchangé.

        `complet=False` (phase 1, mode rapide) : index seul — noms, lignes,
        classification et un PROXY de complexité explicite
        (`complexite = nb_lignes`, documenté par analyseur). Les mesures
        coûteuses (comptage fin de nœuds, profondeur d'imbrication,
        règles souveraines, φ-ratios) sont sautées : les champs non
        calculés portent des valeurs sentinelles documentées
        (`profondeur_max=0`, `distance_fib=0.0`, `phi_ratio=1.0`).
        Destiné à `phi index --rapide` : la carte des symboles sans le
        prix des métriques.
        """
        raise NotImplementedError
