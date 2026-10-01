"""
editeur/recherche.py — Recherche de mots-clés et de fonctions dans un projet.

Deux modes complémentaires :
- `rechercher_mot_cle` : parcours brut du texte (grep intégré), fichiers
  supportés uniquement, binaires ignorés via UnicodeDecodeError.
- `rechercher_fonction` : interrogation de l'index des symboles, avec
  classement exact > préfixe > sous-chaîne, puis complexité décroissante.
"""
import os
import re
from typing import Dict, List

from ..langs import est_fichier_supporte
from .indexeur import Symbole


def _lire_lignes_texte(chemin: str) -> List[str]:
    """Lit un fichier texte ; lève UnicodeDecodeError si binaire."""
    with open(chemin, "r", encoding="utf-8") as f:
        return f.read().split("\n")


def _construire_motif(motif: str, regex: bool, insensible_casse: bool):
    """Compile le motif de recherche (regex ou sous-chaîne littérale)."""
    if regex:
        drapeaux = re.IGNORECASE if insensible_casse else 0
        return re.compile(motif, drapeaux).search
    if insensible_casse:
        cible = motif.lower()
        return lambda ligne: cible in ligne.lower()
    return lambda ligne: motif in ligne


def rechercher_mot_cle(
    dossier: str,
    motif: str,
    regex: bool = False,
    insensible_casse: bool = True,
    contexte: int = 2,
) -> List[dict]:
    """
    Cherche `motif` dans tous les fichiers supportés de `dossier`.

    Retourne `[{fichier, ligne, texte, contexte}]` où `ligne` est 1-based
    et `contexte` la liste des lignes environnantes (sans la ligne elle-même).
    Les fichiers illisibles en UTF-8 (binaires) sont ignorés silencieusement.
    """
    tester = _construire_motif(motif, regex, insensible_casse)
    resultats = []
    if not os.path.isdir(dossier):
        return resultats
    for racine, _, noms in os.walk(dossier):
        for nom in sorted(noms):
            chemin = os.path.join(racine, nom)
            if not est_fichier_supporte(chemin):
                continue
            try:
                lignes = _lire_lignes_texte(chemin)
            except (UnicodeDecodeError, OSError):
                continue
            for i, ligne_texte in enumerate(lignes):
                try:
                    trouve = tester(ligne_texte)
                except re.error:
                    return resultats
                if trouve:
                    debut = max(0, i - contexte)
                    fin = min(len(lignes), i + contexte + 1)
                    resultats.append(
                        {
                            "fichier": chemin,
                            "ligne": i + 1,
                            "texte": ligne_texte,
                            "contexte": lignes[debut:i] + lignes[i + 1 : fin],
                        }
                    )
    return resultats


def _rang_correspondance(nom: str, requete: str) -> int:
    """0 = exact, 1 = préfixe, 2 = sous-chaîne (requête déjà en minuscules)."""
    nom_min = nom.lower()
    if nom_min == requete:
        return 0
    if nom_min.startswith(requete):
        return 1
    return 2


def rechercher_fonction(
    index: Dict[str, List[Symbole]], requete: str
) -> List[Symbole]:
    """
    Filtre les symboles de l'index par sous-chaîne insensible à la casse.

    Tri : correspondance exacte d'abord, puis préfixe, puis sous-chaîne ;
    à rang égal, complexité décroissante (l'Oudjat d'abord).
    """
    q = requete.lower().strip()
    if not q:
        return []
    candidats = [
        s
        for symboles in index.values()
        for s in symboles
        if q in s.nom.lower()
    ]
    candidats.sort(key=lambda s: (_rang_correspondance(s.nom, q), -s.complexite))
    return candidats
