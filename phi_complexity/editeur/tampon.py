"""
editeur/tampon.py — Tampon d'édition : le cœur mutable de l'éditeur phi.

Un `Tampon` est une liste de lignes avec un curseur (ligne, colonne),
un drapeau `modifie` et une pile d'annulation bornée. Aucune dépendance
hors stdlib, aucun état global : chaque tampon est souverain.
"""
import os
from typing import List, Optional, Tuple


TAILLE_MAX_UNDO = 200


class Tampon:
    """Conteneur éditable de texte, adressable par lignes."""

    def __init__(self, lignes: Optional[List[str]] = None) -> None:
        self.lignes: List[str] = list(lignes) if lignes else [""]
        if not self.lignes:
            self.lignes = [""]
        self.ligne: int = 0
        self.colonne: int = 0
        self.modifie: bool = False
        self._undo: List[Tuple[List[str], int, int]] = []

    # ── Chargement / sauvegarde ──────────────────────────────

    @classmethod
    def charger(cls, chemin: str) -> "Tampon":
        """Charge un fichier texte dans un nouveau tampon (curseur en tête)."""
        with open(chemin, "r", encoding="utf-8") as f:
            contenu = f.read()
        tampon = cls(contenu.split("\n"))
        tampon.modifie = False
        return tampon

    def sauvegarder(self, chemin: str) -> None:
        """Écrit le tampon sur disque et réinitialise le drapeau `modifie`."""
        dossier = os.path.dirname(os.path.abspath(chemin))
        if dossier:
            os.makedirs(dossier, exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(self.texte())
        self.modifie = False

    def texte(self) -> str:
        """Retourne le contenu complet du tampon."""
        return "\n".join(self.lignes)

    # ── Curseur ──────────────────────────────────────────────

    def _borner_curseur(self) -> None:
        """Ramène le curseur dans les limites du tampon."""
        self.ligne = max(0, min(self.ligne, len(self.lignes) - 1))
        self.colonne = max(0, min(self.colonne, len(self.lignes[self.ligne])))

    def deplacer(self, delta_ligne: int, delta_colonne: int) -> None:
        """Déplace le curseur relativement, en restant dans les bornes."""
        self.ligne += delta_ligne
        self.colonne += delta_colonne
        self._borner_curseur()

    def aller_ligne(self, numero: int) -> None:
        """Place le curseur sur la ligne `numero` (1-based, bornée)."""
        self.ligne = max(1, numero) - 1
        self._borner_curseur()

    def aller_debut_ligne(self) -> None:
        """Place le curseur en début de ligne."""
        self.colonne = 0

    def aller_fin_ligne(self) -> None:
        """Place le curseur en fin de ligne."""
        self.colonne = len(self.lignes[self.ligne])

    # ── Annulation ───────────────────────────────────────────

    def _sauvegarder_etat(self) -> None:
        """Empile l'état courant pour une annulation future (pile bornée)."""
        self._undo.append((list(self.lignes), self.ligne, self.colonne))
        if len(self._undo) > TAILLE_MAX_UNDO:
            self._undo.pop(0)

    def annuler(self) -> bool:
        """Restaure le dernier état sauvegardé. Retourne False si pile vide."""
        if not self._undo:
            return False
        lignes, ligne, colonne = self._undo.pop()
        self.lignes = lignes
        self.ligne = ligne
        self.colonne = colonne
        self.modifie = True
        return True

    # ── Édition ──────────────────────────────────────────────

    def inserer(self, texte: str) -> None:
        """Insère du texte à la position du curseur (gère les retours ligne)."""
        self._sauvegarder_etat()
        morceaux = texte.split("\n")
        ligne_courante = self.lignes[self.ligne]
        avant = ligne_courante[: self.colonne]
        apres = ligne_courante[self.colonne :]
        if len(morceaux) == 1:
            self.lignes[self.ligne] = avant + morceaux[0] + apres
            self.colonne += len(morceaux[0])
        else:
            self.lignes[self.ligne] = avant + morceaux[0]
            for i, morceau in enumerate(morceaux[1:-1], start=1):
                self.lignes.insert(self.ligne + i, morceau)
            derniere = morceaux[-1] + apres
            self.lignes.insert(self.ligne + len(morceaux) - 1, derniere)
            self.ligne += len(morceaux) - 1
            self.colonne = len(morceaux[-1])
        self.modifie = True

    def supprimer_avant(self) -> None:
        """Supprime le caractère avant le curseur (fusionne les lignes si besoin)."""
        if self.ligne == 0 and self.colonne == 0:
            return
        self._sauvegarder_etat()
        if self.colonne > 0:
            ligne = self.lignes[self.ligne]
            self.lignes[self.ligne] = ligne[: self.colonne - 1] + ligne[self.colonne :]
            self.colonne -= 1
        else:
            precedente = self.lignes.pop(self.ligne)
            self.ligne -= 1
            self.colonne = len(self.lignes[self.ligne])
            self.lignes[self.ligne] += precedente
        self.modifie = True

    def supprimer_apres(self) -> None:
        """Supprime le caractère sous le curseur (fusionne les lignes si besoin)."""
        ligne = self.lignes[self.ligne]
        if self.colonne >= len(ligne) and self.ligne >= len(self.lignes) - 1:
            return
        self._sauvegarder_etat()
        if self.colonne < len(ligne):
            self.lignes[self.ligne] = ligne[: self.colonne] + ligne[self.colonne + 1 :]
        else:
            self.lignes[self.ligne] += self.lignes.pop(self.ligne + 1)
        self.modifie = True

    def couper_ligne(self) -> None:
        """Coupe la ligne courante en deux à la position du curseur."""
        self._sauvegarder_etat()
        ligne = self.lignes[self.ligne]
        self.lignes[self.ligne] = ligne[: self.colonne]
        self.lignes.insert(self.ligne + 1, ligne[self.colonne :])
        self.ligne += 1
        self.colonne = 0
        self.modifie = True
