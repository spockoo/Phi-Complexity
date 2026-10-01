"""
editeur/__init__.py — L'éditeur phi : tampon, index, recherche, panneau phi, TUI.

`tui.lancer_editeur` n'est importé que paresseusement (via la fonction
`lancer_editeur` ci-dessous) pour ne jamais imposer `curses` aux usages
non interactifs (tests, scripts).
"""
from .tampon import Tampon, TAILLE_MAX_UNDO
from .indexeur import (
    Symbole,
    indexer_projet,
    rafraichir_fichier,
    symboles_de_l_index,
    fichiers_non_supportes,
    EXCLUSIONS_DEFAUT,
)
from .recherche import rechercher_mot_cle, rechercher_fonction
from .panneau_phi import auditer_tampon, NB_ANNOTATIONS_AFFICHEES


def lancer_editeur(chemin: str, projet=None, lang=None) -> int:
    """Ouvre l'éditeur phi plein-écran (curses) sur `chemin`."""
    from .tui import lancer_editeur as _lancer
    return _lancer(chemin, projet=projet, lang=lang)


__all__ = [
    "Tampon",
    "TAILLE_MAX_UNDO",
    "Symbole",
    "indexer_projet",
    "rafraichir_fichier",
    "symboles_de_l_index",
    "fichiers_non_supportes",
    "EXCLUSIONS_DEFAUT",
    "rechercher_mot_cle",
    "rechercher_fonction",
    "auditer_tampon",
    "NB_ANNOTATIONS_AFFICHEES",
    "lancer_editeur",
]
