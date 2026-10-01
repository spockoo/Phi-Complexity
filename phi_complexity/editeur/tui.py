"""
editeur/tui.py — L'éditeur phi : application plein-écran (curses).

`lancer_editeur(chemin, projet=None, lang=None)` ouvre le fichier dans
un tampon éditable, avec :
- navigation (flèches, PgUp/PgDn, Home/End) et édition directe ;
- barre de statut (fichier, ligne/colonne, ● si modifié, radiance) ;
- panneau phi (F4 : afficher/masquer, F5 : ré-auditer, auto après ^S) ;
- recherches : ^F dans le tampon, ^K mot-clé projet, ^T fonction (index) ;
- ^G aller à la ligne, ^S sauvegarder, ^Q quitter (confirmation si modifié).

Les résultats de recherche s'ouvrent en liste navigable : Entrée saute
au fichier:ligne (et ouvre le fichier), Échap referme.
"""
import curses
import os

from .tampon import Tampon
from .indexeur import indexer_projet, rafraichir_fichier
from .recherche import rechercher_mot_cle, rechercher_fonction
from .panneau_phi import auditer_tampon
from ..langs import est_fichier_supporte


# ── Touches de contrôle (hermétiques, nommées) ────────────────

CTRL_S = 19
CTRL_Q = 17
CTRL_G = 7
CTRL_F = 6
CTRL_K = 11
CTRL_T = 20
ECHAP = 27
ENTREE = 10

LARGEUR_PANNEAU_PHI = 44
AIDE_RACCOURCIS = (
    "^S sauver  ^Q quitter  ^F chercher  ^K mot-clé  ^T fonction  "
    "^G ligne  F4 phi  F5 audit"
)


# ── Petits formateurs purs (testables sans curses) ────────────

def formater_statut(chemin: str, ligne: int, colonne: int,
                    modifie: bool, radiance) -> str:
    """Construit le texte de la barre de statut."""
    nom = os.path.basename(chemin) or chemin
    marque = "●" if modifie else "○"
    rad = f"  ☼ {radiance:.1f}" if isinstance(radiance, (int, float)) else ""
    return f" {nom}  L{ligne + 1}:C{colonne + 1}  {marque}{rad}"


def formater_entree_mot_cle(resultat: dict) -> str:
    """Une ligne affichable pour un résultat de recherche mot-clé."""
    return f"{resultat['fichier']}:{resultat['ligne']}: {resultat['texte'].strip()}"


def formater_entree_fonction(symbole) -> str:
    """Une ligne affichable pour un symbole de l'index."""
    return (f"{symbole.fichier} :: {symbole.nom} "
            f"(l.{symbole.ligne}, complexité {symbole.complexite})")


def tronquer(texte: str, largeur: int) -> str:
    """Tronque proprement à `largeur` caractères."""
    if largeur <= 0:
        return ""
    return texte if len(texte) <= largeur else texte[: max(0, largeur - 1)] + "…"


# ── État de l'éditeur ─────────────────────────────────────────

class EditeurTUI:
    """État mutable d'une session d'édition (le dessin vit à part)."""

    def __init__(self, chemin: str, projet=None, lang=None) -> None:
        self.chemin = os.path.abspath(chemin)
        base = os.path.dirname(self.chemin)
        self.projet = os.path.abspath(projet) if projet else (base or os.getcwd())
        self.lang = lang
        self.tampon = (Tampon.charger(self.chemin)
                       if os.path.isfile(self.chemin) else Tampon())
        self.defil_ligne = 0
        self.defil_col = 0
        self.panneau_phi_visible = False
        self.resultat_phi = None
        self.message = ""
        self._index = None

    # ── Index (paresseux : construit à la première recherche) ──

    def index(self):
        """Retourne l'index des symboles du projet (construit au besoin)."""
        if self._index is None:
            self._index = indexer_projet(self.projet)
        return self._index

    def invalider_index_fichier(self) -> None:
        """Ré-indexe le fichier courant après sauvegarde."""
        if self._index is not None:
            rafraichir_fichier(self._index, self.chemin)

    # ── Audit phi ─────────────────────────────────────────────

    def audit_phi(self) -> None:
        """Lance l'audit phi du tampon courant (ne lève jamais)."""
        self.resultat_phi = auditer_tampon(self.tampon, self.chemin, lang=self.lang)
        if "erreur" not in self.resultat_phi:
            self.message = (
                f"✦ Audit phi : radiance {self.resultat_phi['radiance']} — "
                f"{self.resultat_phi.get('statut_gnostique', '')}"
            )

    def sauvegarder(self) -> None:
        """Sauvegarde, ré-indexe le fichier et relance l'audit phi."""
        try:
            self.tampon.sauvegarder(self.chemin)
        except OSError as e:
            self.message = f"❌ Sauvegarde impossible : {e}"
            return
        self.invalider_index_fichier()
        self.message = f"✦ Sauvegardé : {self.chemin}"
        if est_fichier_supporte(self.chemin) or self.lang:
            self.audit_phi()

    # ── Fichiers ──────────────────────────────────────────────

    def ouvrir(self, chemin: str, ligne: int = 1) -> None:
        """Remplace le tampon par le contenu de `chemin` (curseur à `ligne`)."""
        self.chemin = os.path.abspath(chemin)
        self.tampon = (Tampon.charger(self.chemin)
                       if os.path.isfile(self.chemin) else Tampon())
        self.tampon.aller_ligne(ligne)
        self.defil_ligne = 0
        self.defil_col = 0
        self.resultat_phi = None
        self.message = f"📄 {self.chemin}"

    # ── Défilement ────────────────────────────────────────────

    def ajuster_defilement(self, hauteur: int, largeur: int) -> None:
        """Garde le curseur visible dans la zone d'édition."""
        t = self.tampon
        if t.ligne < self.defil_ligne:
            self.defil_ligne = t.ligne
        elif t.ligne >= self.defil_ligne + hauteur:
            self.defil_ligne = t.ligne - hauteur + 1
        if t.colonne < self.defil_col:
            self.defil_col = t.colonne
        elif t.colonne >= self.defil_col + largeur:
            self.defil_col = t.colonne - largeur + 1


# ── Dessin ────────────────────────────────────────────────────

def _dessiner_aide(stdscr, largeur: int) -> None:
    try:
        stdscr.addnstr(0, 0, tronquer(AIDE_RACCOURCIS, largeur), largeur,
                       curses.A_DIM)
    except curses.error:
        pass


def _dessiner_tampon(stdscr, ed: EditeurTUI, haut: int, larg: int) -> None:
    t = ed.tampon
    for i in range(haut):
        idx = ed.defil_ligne + i
        if idx >= len(t.lignes):
            break
        morceau = t.lignes[idx][ed.defil_col: ed.defil_col + larg]
        attr = curses.A_REVERSE if idx == t.ligne else curses.A_NORMAL
        try:
            stdscr.addnstr(1 + i, 0, morceau, larg, attr)
        except curses.error:
            pass


def _lignes_panneau_phi(ed: EditeurTUI, larg: int):
    """Contenu textuel du panneau phi (pur, sans curses)."""
    lignes = [" PANNEAU PHI ", ""]
    r = ed.resultat_phi
    if r is None:
        lignes.append("F5 pour auditer.")
        return lignes
    if "erreur" in r:
        lignes.append("⚠ " + r["erreur"])
        return lignes
    lignes.append(f"☼ Radiance : {r['radiance']}")
    lignes.append(f"◈ {r.get('statut_gnostique', '')}")
    lignes.append("")
    oudjat = r.get("oudjat")
    if oudjat:
        lignes.append(f"👑 Oudjat : {oudjat['nom']}")
        lignes.append(f"   l.{oudjat['ligne']} — complexité {oudjat['complexite']}")
    else:
        lignes.append("👑 Oudjat : —")
    lignes.append(f"⚠ Anomalies : {r.get('nb_anomalies', 0)}")
    lignes.append("")
    lignes.append("─ Sutures ─")
    for a in r.get("annotations", []):
        lignes.append(f"l.{a.get('ligne')} [{a.get('niveau')}]")
        lignes.append("  " + str(a.get("message", "")))
    return [tronquer(l, larg) for l in lignes]


def _dessiner_panneau_phi(stdscr, ed: EditeurTUI, haut: int, largeur_tot: int) -> None:
    x0 = largeur_tot - LARGEUR_PANNEAU_PHI
    for i, texte in enumerate(_lignes_panneau_phi(ed, LARGEUR_PANNEAU_PHI - 2)):
        if i >= haut:
            break
        try:
            stdscr.addnstr(1 + i, x0, "│ " + texte, LARGEUR_PANNEAU_PHI - 1)
        except curses.error:
            pass


def _dessiner(stdscr, ed: EditeurTUI) -> None:
    hauteur, largeur = stdscr.getmaxyx()
    stdscr.erase()
    _dessiner_aide(stdscr, largeur)
    larg_edit = (largeur - LARGEUR_PANNEAU_PHI
                 if ed.panneau_phi_visible else largeur)
    haut_edit = max(1, hauteur - 3)
    ed.ajuster_defilement(haut_edit, max(1, larg_edit))
    _dessiner_tampon(stdscr, ed, haut_edit, max(1, larg_edit))
    if ed.panneau_phi_visible and largeur > LARGEUR_PANNEAU_PHI + 20:
        _dessiner_panneau_phi(stdscr, ed, haut_edit, largeur)
    # Barre de statut
    rad = (ed.resultat_phi.get("radiance")
           if ed.resultat_phi and "erreur" not in ed.resultat_phi else None)
    statut = formater_statut(ed.chemin, ed.tampon.ligne, ed.tampon.colonne,
                             ed.tampon.modifie, rad)
    try:
        stdscr.addnstr(hauteur - 2, 0, tronquer(statut, largeur), largeur,
                       curses.A_REVERSE)
        stdscr.addnstr(hauteur - 1, 0, tronquer(ed.message, largeur), largeur)
        stdscr.move(1 + ed.tampon.ligne - ed.defil_ligne,
                    min(ed.tampon.colonne - ed.defil_col, larg_edit - 1))
    except curses.error:
        pass
    stdscr.refresh()


# ── Interactions : invite, confirmation, liste ────────────────

def _demander(stdscr, ed: EditeurTUI, invite: str):
    """Lit une chaîne sur la ligne de commande. None si Échap."""
    hauteur, largeur = stdscr.getmaxyx()
    saisie = ""
    while True:
        try:
            stdscr.addnstr(hauteur - 1, 0, tronquer(invite + saisie, largeur),
                           largeur, curses.A_BOLD)
            stdscr.move(hauteur - 1, min(len(invite + saisie), largeur - 1))
        except curses.error:
            pass
        touche = stdscr.getch()
        if touche in (ENTREE, curses.KEY_ENTER):
            return saisie
        if touche == ECHAP:
            return None
        if touche in (curses.KEY_BACKSPACE, 127, 8):
            saisie = saisie[:-1]
        elif 32 <= touche <= 126:
            saisie += chr(touche)
        ed.message = ""
        _dessiner(stdscr, ed)


def _confirmer(stdscr, ed: EditeurTUI, question: str) -> bool:
    """Oui/Non sur la ligne de commande (o = oui)."""
    reponse = _demander(stdscr, ed, question + " (o/n) ")
    return bool(reponse) and reponse.strip().lower().startswith("o")


def _liste_resultats(stdscr, ed: EditeurTUI, titre: str, entrees):
    """Liste navigable : Entrée = choisir, Échap = annuler."""
    selection = 0
    while True:
        hauteur, largeur = stdscr.getmaxyx()
        stdscr.erase()
        try:
            stdscr.addnstr(0, 0, tronquer(titre, largeur), largeur, curses.A_BOLD)
        except curses.error:
            pass
        visible = hauteur - 2
        debut = max(0, min(selection - visible // 2, max(0, len(entrees) - visible)))
        for i, entree in enumerate(entrees[debut: debut + visible]):
            attr = curses.A_REVERSE if debut + i == selection else curses.A_NORMAL
            try:
                stdscr.addnstr(1 + i, 0, tronquer(entree, largeur - 1),
                               largeur - 1, attr)
            except curses.error:
                pass
        try:
            stdscr.addnstr(hauteur - 1, 0,
                           tronquer("↑↓ naviguer — Entrée : aller — Échap : fermer",
                                    largeur), largeur, curses.A_DIM)
        except curses.error:
            pass
        stdscr.refresh()
        touche = stdscr.getch()
        if touche == ECHAP:
            return None
        if touche in (ENTREE, curses.KEY_ENTER):
            return selection if entrees else None
        if touche == curses.KEY_UP:
            selection = max(0, selection - 1)
        elif touche == curses.KEY_DOWN:
            selection = min(len(entrees) - 1, selection + 1)
        elif touche == curses.KEY_PPAGE:
            selection = max(0, selection - visible)
        elif touche == curses.KEY_NPAGE:
            selection = min(len(entrees) - 1, selection + visible)


# ── Actions de recherche ──────────────────────────────────────

def _action_chercher_tampon(stdscr, ed: EditeurTUI) -> None:
    motif = _demander(stdscr, ed, "Chercher dans le tampon : ")
    if not motif:
        return
    cibles = motif.lower()
    trouves = [(i + 1, l) for i, l in enumerate(ed.tampon.lignes)
               if cibles in l.lower()]
    if not trouves:
        ed.message = f"∅ Aucune occurrence de « {motif} »."
        return
    entrees = [f"L{num} : {texte.strip()}" for num, texte in trouves]
    choix = _liste_resultats(stdscr, ed,
                             f"« {motif} » — {len(trouves)} occurrence(s)", entrees)
    if choix is not None:
        ed.tampon.aller_ligne(trouves[choix][0])


def _action_chercher_mot_cle(stdscr, ed: EditeurTUI) -> None:
    motif = _demander(stdscr, ed, "Mot-clé dans le projet : ")
    if not motif:
        return
    resultats = rechercher_mot_cle(ed.projet, motif)
    if not resultats:
        ed.message = f"∅ Aucun résultat pour « {motif} » dans {ed.projet}."
        return
    entrees = [formater_entree_mot_cle(r) for r in resultats]
    choix = _liste_resultats(stdscr, ed,
                             f"« {motif} » — {len(resultats)} résultat(s)", entrees)
    if choix is not None:
        r = resultats[choix]
        if _peut_changer_de_fichier(stdscr, ed):
            ed.ouvrir(r["fichier"], r["ligne"])


def _action_chercher_fonction(stdscr, ed: EditeurTUI) -> None:
    requete = _demander(stdscr, ed, "Fonction : ")
    if not requete:
        return
    symboles = rechercher_fonction(ed.index(), requete)
    if not symboles:
        ed.message = f"∅ Aucune fonction « {requete} » dans l'index."
        return
    entrees = [formater_entree_fonction(s) for s in symboles]
    choix = _liste_resultats(stdscr, ed,
                             f"« {requete} » — {len(symboles)} symbole(s)", entrees)
    if choix is not None:
        s = symboles[choix]
        if _peut_changer_de_fichier(stdscr, ed):
            ed.ouvrir(s.fichier, s.ligne)


def _peut_changer_de_fichier(stdscr, ed: EditeurTUI) -> bool:
    """Confirme l'abandon du tampon courant s'il est modifié."""
    if not ed.tampon.modifie:
        return True
    return _confirmer(stdscr, ed, "Tampon modifié — abandonner les changements ?")


def _action_aller_ligne(stdscr, ed: EditeurTUI) -> None:
    reponse = _demander(stdscr, ed, "Aller à la ligne : ")
    if reponse and reponse.strip().isdigit():
        ed.tampon.aller_ligne(int(reponse.strip()))
    else:
        ed.message = ""


# ── Boucle principale ─────────────────────────────────────────

def _traiter_touche(stdscr, ed: EditeurTUI, touche: int) -> bool:
    """Traite une touche. Retourne False pour quitter."""
    t = ed.tampon
    if touche == CTRL_Q:
        if t.modifie and not _confirmer(stdscr, ed, "Quitter sans sauvegarder ?"):
            ed.message = ""
            return True
        return False
    if touche == CTRL_S:
        ed.sauvegarder()
        return True
    if touche == CTRL_G:
        _action_aller_ligne(stdscr, ed)
        return True
    if touche == CTRL_F:
        _action_chercher_tampon(stdscr, ed)
        return True
    if touche == CTRL_K:
        _action_chercher_mot_cle(stdscr, ed)
        return True
    if touche == CTRL_T:
        _action_chercher_fonction(stdscr, ed)
        return True
    if touche == curses.KEY_F4:
        ed.panneau_phi_visible = not ed.panneau_phi_visible
        if ed.panneau_phi_visible and ed.resultat_phi is None:
            ed.audit_phi()
        return True
    if touche == curses.KEY_F5:
        ed.audit_phi()
        ed.panneau_phi_visible = True
        return True
    if touche == curses.KEY_UP:
        t.deplacer(-1, 0)
    elif touche == curses.KEY_DOWN:
        t.deplacer(1, 0)
    elif touche == curses.KEY_LEFT:
        t.deplacer(0, -1)
    elif touche == curses.KEY_RIGHT:
        t.deplacer(0, 1)
    elif touche == curses.KEY_PPAGE:
        t.deplacer(-(stdscr.getmaxyx()[0] - 3), 0)
    elif touche == curses.KEY_NPAGE:
        t.deplacer(stdscr.getmaxyx()[0] - 3, 0)
    elif touche == curses.KEY_HOME:
        t.aller_debut_ligne()
    elif touche == curses.KEY_END:
        t.aller_fin_ligne()
    elif touche in (curses.KEY_BACKSPACE, 127, 8):
        t.supprimer_avant()
    elif touche == curses.KEY_DC:
        t.supprimer_apres()
    elif touche in (ENTREE, curses.KEY_ENTER):
        t.couper_ligne()
    elif touche == 9:  # Tabulation
        t.inserer("    ")
    elif 32 <= touche <= 126 or touche > 160:
        t.inserer(chr(touche))
    # Toute autre touche (dont Échap seul) : ignorée.
    return True


def _boucle(stdscr, ed: EditeurTUI) -> None:
    """Boucle d'événements curses."""
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    stdscr.keypad(True)
    ed.audit_phi()  # audit initial si le langage est supporté
    while True:
        _dessiner(stdscr, ed)
        touche = stdscr.getch()
        if not _traiter_touche(stdscr, ed, touche):
            break


def lancer_editeur(chemin: str, projet=None, lang=None) -> int:
    """
    Ouvre l'éditeur phi plein-écran sur `chemin`.

    `projet` : dossier racine pour l'index des symboles (défaut : dossier
    parent du fichier). `lang` : force le langage pour l'audit phi.
    Retourne 0 à la sortie normale.
    """
    editeur = EditeurTUI(chemin, projet=projet, lang=lang)
    curses.wrapper(_boucle, editeur)
    return 0
