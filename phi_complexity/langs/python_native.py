"""
langs/python_native.py — Dissection fractale du code Python via AST natif (ast).
Zéro dépendance tierce — Souveraineté totale pour le langage Python.
Suturée selon les recommandations de phi-complexity v0.1.0 (Protocole BMAD).

PASSE UNIQUE FUSIONNÉE (v0.8.0 « La Vitesse ») — mécanisme mesuré :
cProfile sur 100k lignes / 20k fonctions montrait 55 MILLIONS d'appels :
l'arbre AST était parcouru ~7 fois (`_injecter_parents`, `_compter_elements_globaux`,
`_analyser_fonctions` + 2 walks PAR FONCTION, `_appliquer_regles_souveraines`,
17M d'`isinstance`). Tout est linéaire mais la constante tuait.

Ici : UN SEUL parcours itératif (événements entrée/sortie, pas de récursion)
qui fait tout pendant la visite :
- chaque nœud incrémente le compteur du cadre de fonction courant
  → `complexite` (nb de nœuds du sous-arbre) devient GRATUITE, dans les
  deux phases ;
- les 4 règles souveraines (LILITH/RAII/FIBONACCI/HERMÉTICITÉ) sont
  évaluées inline, en mode complet uniquement ;
- la profondeur de boucles est suivie par compteurs de pile —
  plus d'injection de parents, plus de remontée, plus de walks par fonction.

Non-régression : valeurs IDENTIQUES à l'ancien code multi-passes
(tests `tests/test_*.py`). Quirks historiques préservés et documentés :
- `_NOEUD_COMPTABLE` (profondeur LILITH) exclut `AsyncFunctionDef`
  (comme l'ancien `_profondeur_noeud`) ;
- `complexite` compte le nœud `FunctionDef` lui-même et tout son
  sous-arbre, fonctions imbriquées incluses (comme `len(list(ast.walk(node)))`) ;
- `profondeur_max` compte les nœuds de contrôle (For/While/If/With)
  des fonctions imbriquées vers la fonction englobante (comme l'ancien
  `_profondeur_imbrication`).

Deux phases :
- phase 1 (`analyser(complet=False)`) : noms, lignes, nb d'arguments,
  `complexite` réelle (gratuite), compteurs classes/imports. Champs non
  calculés = sentinelles : `profondeur_max=0`, `distance_fib=0.0`,
  `phi_ratio=1.0`. Aucune règle souveraine.
- phase 2 (`analyser(complet=True)`, défaut) : tout, valeurs historiques.
"""
import ast
import os
from typing import List, Optional

from ..core import fibonacci_plus_proche, distance_fibonacci, PHI
from ..modeles import MetriqueFonction, Annotation, ResultatAnalyse
from ..cache import calculer_hash_src, obtenir_cache_courant
from .base import AnalyseurBase


def _parser_avec_cache(fichier: str, contenu: str) -> ast.AST:
    """Parse `contenu` en consultant le parse cache de la session courante.

    Intégration native PHI-NATIF-C : quand `phi check` a ouvert une session
    de cache (voir phi_complexity.cache.integration_check), l'AST est
    recherché sous la clé (chemin absolu du fichier, sha256 des octets
    source via `calculer_hash_src`). Hit => aucun re-parse ; miss => parse
    normal puis mise en cache.

    TRANSPARENCE : le cache ne change que la vitesse d'obtention de l'AST,
    jamais son contenu — `ast.parse` reçoit exactement les mêmes octets
    dans les deux cas. Une SyntaxError du source remonte exactement comme
    sans cache (elle n'est jamais mise en cache).

    DÉGRADATION GRACIEUSE : toute défaillance du cache (get/put, pickle,
    clé) retombe sur `ast.parse` normal — le cache ne fait jamais échouer
    un audit.
    """
    cache = obtenir_cache_courant()
    if cache is None:
        return ast.parse(contenu, filename=fichier)
    try:
        nom = os.path.abspath(fichier)
        hash_src = calculer_hash_src(contenu.encode("utf-8"))
    except Exception:
        return ast.parse(contenu, filename=fichier)
    try:
        arbre = cache.get(nom, hash_src)
    except Exception:
        arbre = None  # un get() défaillant ne doit jamais bloquer l'audit
    if arbre is not None:
        return arbre
    arbre = ast.parse(contenu, filename=fichier)
    try:
        cache.put(nom, hash_src, arbre)
    except Exception:
        pass  # le cache est une optimisation, pas un prérequis
    return arbre

# Bits de classification d'un nœud. Le dispatch se fait par `type()` +
# dictionnaire (UN lookup par nœud au lieu de ~8 isinstance) ; les bits
# servent aussi à la sortie (pas de re-classification).
_K_FONCTION = 1   # FunctionDef / AsyncFunctionDef → cadre de mesure
_K_COMPTABLE = 2  # ancêtre comptable pour la profondeur LILITH :
                  # For/While/If/FunctionDef — PAS AsyncFunctionDef
                  # (quirk historique de _profondeur_noeud, préservé)
_K_CONTROLE = 4   # For/While/If/With → profondeur d'imbrication
_K_LILITH = 8     # For/While : évalués par la règle LILITH
_K_APPEL = 16     # Call : règle RAII (open sans with)
_K_CLASSE = 32    # ClassDef : compteur global
_K_IMPORT = 64    # Import/ImportFrom : compteur global

_KIND_PAR_TYPE = {
    ast.FunctionDef: _K_FONCTION | _K_COMPTABLE,
    ast.AsyncFunctionDef: _K_FONCTION,
    ast.For: _K_COMPTABLE | _K_CONTROLE | _K_LILITH,
    ast.While: _K_COMPTABLE | _K_CONTROLE | _K_LILITH,
    ast.If: _K_COMPTABLE | _K_CONTROLE,
    ast.With: _K_CONTROLE,
    ast.Call: _K_APPEL,
    ast.ClassDef: _K_CLASSE,
    ast.Import: _K_IMPORT,
    ast.ImportFrom: _K_IMPORT,
}

# Indices du cadre de fonction (liste, pas d'objet : boucle chaude).
_C_NOM = 0
_C_LIGNE = 1
_C_NB_NOEUDS = 2
_C_NB_ARGS = 3
_C_NB_LIGNES = 4
_C_PROF_COURANTE = 5
_C_PROF_MAX = 6


class AnalyseurPython(AnalyseurBase):
    """
    Analyseur fractal basé sur le module `ast` de la bibliothèque standard.
    Dissèque le code Python pour extraire ses métriques souveraines.
    """

    def __init__(self, fichier: str):
        super().__init__(fichier)
        self.tree: Optional[ast.AST] = None
        self.lignes: List[str] = []
        self.resultat = ResultatAnalyse(fichier=fichier, langage="python")

    def charger(self) -> "AnalyseurPython":
        """
        Charge et parse le fichier Python (gestionnaire de contexte, Règle II).
        Ne fait QUE lire + parser : aucun pré-traitement de l'arbre.

        Le parsing transite par `_parser_avec_cache` : quand une session
        `phi check` a activé le parse cache natif, l'AST est réutilisé sans
        re-parse si les octets source sont inchangés — sortie identique,
        juste plus rapide.
        """
        with open(self.fichier, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.lignes = contenu.splitlines()
        self.tree = _parser_avec_cache(self.fichier, contenu)
        return self

    def analyser(self, complet: bool = True) -> ResultatAnalyse:
        """
        Lance l'analyse : UNE SEULE passe fusionnée sur l'AST.

        `complet=True` (défaut) : métriques, règles souveraines, oudjat —
        valeurs identiques à l'analyse historique multi-passes.
        `complet=False` : phase 1 — index seul (noms, lignes, complexité
        réelle gratuite), règles et φ-ratios non calculés (sentinelles).
        """
        if self.tree is None:
            self.charger()
        self.resultat.nb_lignes_total = len(self.lignes)
        self._passe_unique(complet=complet)
        self.resultat.nb_commentaires = sum(
            1 for ligne in self.lignes if ligne.strip().startswith("#")
        )
        self._identifier_oudjat(complet=complet)
        return self.resultat

    # ────────────────────────────────────────────────────────
    # PASSE UNIQUE FUSIONNÉE
    # ────────────────────────────────────────────────────────

    def _passe_unique(self, complet: bool) -> None:
        """
        Un seul parcours itératif de l'AST — pile d'itérateurs (DFS).

        Chaque niveau de pile = (noeud, kind, iterateur_enfants) : quand
        l'itérateur est épuisé, on traite la SORTIE du nœud (pas
        d'événements de sortie alloués, pas de list()+reversed() — les
        enfants sont visités dans l'ordre du source via next()).

        État maintenu pendant la visite :
        - `cadres` : pile des fonctions en cours — chaque nœud visité
          incrémente le compteur du cadre courant (complexité gratuite) ;
          à la sortie d'une fonction, son total est versé au cadre parent
          (les fonctions imbriquées comptent dans l'englobante, comme
          l'historique `len(list(ast.walk(node)))`) ;
        - `chemin` : pile des ancêtres — parent/grand-parent directs
          pour la règle RAII (remplace l'injection de parents) ;
        - `n_comptable` : nb d'ancêtres For/While/If/FunctionDef
          pour la profondeur LILITH (remplace la remontée) ;
        - compteurs de contrôle par cadre : profondeur d'imbrication
          For/While/If/With (remplace `_profondeur_imbrication`).

        Dispatch par `type()` + `_KIND_PAR_TYPE` : UN lookup par nœud
        au lieu de ~8 isinstance (le profil montrait 578k isinstance
        pour 10k lignes).
        """
        resultat = self.resultat
        fonctions = resultat.fonctions

        cadres = []   # pile de cadres [nom, ligne, nb_noeuds, nb_args,
                      #                nb_lignes, prof_courante, prof_max]
        n_comptable = 0  # ancêtres For/While/If/FunctionDef (LILITH)

        # Raccourcis locaux : la boucle chaude n'accède qu'à des locaux.
        _fib_proche = fibonacci_plus_proche
        _iter_enfants = ast.iter_child_nodes
        _kind_par_type = _KIND_PAR_TYPE
        _annoter = self._annoter
        _est_open = self._est_appel_open

        chemin = [self.tree]
        pile = [(self.tree, 0, _iter_enfants(self.tree))]
        while pile:
            noeud, kind, it = pile[-1]
            enfant = next(it, None)
            if enfant is None:
                # ── SORTIE du nœud ──
                if kind & _K_COMPTABLE:
                    if complet:
                        n_comptable -= 1
                if kind & _K_CONTROLE:
                    if complet:
                        for cadre in cadres:
                            cadre[_C_PROF_COURANTE] -= 1
                if kind & _K_FONCTION:
                    cadre = cadres.pop()
                    total = cadre[_C_NB_NOEUDS]
                    if cadres:
                        # La fonction imbriquée compte dans l'englobante.
                        cadres[-1][_C_NB_NOEUDS] += total
                    nb_lignes = cadre[_C_NB_LIGNES]
                    if complet:
                        fib = _fib_proche(nb_lignes)
                        dist = abs(nb_lignes - fib) / PHI
                    else:
                        dist = 0.0
                    fonctions.append(MetriqueFonction(
                        nom=cadre[_C_NOM],
                        ligne=cadre[_C_LIGNE],
                        complexite=total,
                        nb_args=cadre[_C_NB_ARGS],
                        nb_lignes=nb_lignes,
                        profondeur_max=cadre[_C_PROF_MAX] if complet else 0,
                        distance_fib=dist,
                        phi_ratio=1.0,  # calculé après (moyenne connue)
                    ))
                pile.pop()
                chemin.pop()
                continue

            # ── ENTRÉE de l'enfant ──
            t = type(enfant)
            kind = _kind_par_type.get(t, 0)
            if kind == 0 and t.__module__ != "ast":
                # Arbre synthétique exotique (sous-classes) : repli exact.
                kind = self._kind_repli(enfant)
            if kind & _K_FONCTION:
                fin = getattr(enfant, "end_lineno", enfant.lineno)
                nb_lignes = fin - enfant.lineno + 1
                cadres.append([enfant.name, enfant.lineno, 0,
                               len(enfant.args.args), nb_lignes, 0, 0])
                if complet:
                    if kind & _K_COMPTABLE:  # def (pas async def — quirk)
                        n_comptable += 1
                    self._regle_fibonacci(enfant, nb_lignes,
                                          _fib_proche(nb_lignes))
                    self._regle_hermeticite(enfant)
            elif kind & _K_LILITH:  # For / While
                if complet:
                    # Profondeur AVANT de compter le nœud lui-même
                    # (l'ancien _profondeur_noeud partait du parent).
                    if n_comptable >= 2:
                        _annoter(
                            enfant.lineno,
                            f"LILITH : Boucle imbriquée (profondeur "
                            f"{n_comptable}). La variance s'accumule — "
                            "envisagez une fonction auxiliaire.",
                            "CRITICAL" if n_comptable >= 3 else "WARNING",
                            "LILITH",
                        )
                    n_comptable += 1
                    for cadre in cadres:
                        p = cadre[_C_PROF_COURANTE] + 1
                        cadre[_C_PROF_COURANTE] = p
                        if p > cadre[_C_PROF_MAX]:
                            cadre[_C_PROF_MAX] = p
            elif kind & (_K_COMPTABLE | _K_CONTROLE):  # If / With
                if complet:
                    if kind & _K_COMPTABLE:  # If (pas With)
                        n_comptable += 1
                    if kind & _K_CONTROLE:
                        for cadre in cadres:
                            p = cadre[_C_PROF_COURANTE] + 1
                            cadre[_C_PROF_COURANTE] = p
                            if p > cadre[_C_PROF_MAX]:
                                cadre[_C_PROF_MAX] = p
            elif kind & _K_APPEL:  # Call
                if complet and _est_open(enfant):
                    parent = chemin[-1]
                    grand_parent = chemin[-2] if len(chemin) >= 2 else None
                    if not (isinstance(parent, ast.withitem)
                            or isinstance(grand_parent, ast.With)):
                        _annoter(
                            enfant.lineno,
                            "SUTURE : 'open()' sans gestionnaire de contexte "
                            "(with). Risque de traînée d'entropie "
                            "(fuite de ressource).",
                            "WARNING",
                            "SUTURE",
                        )
            elif kind & _K_CLASSE:
                resultat.nb_classes += 1
            elif kind & _K_IMPORT:
                resultat.nb_imports += 1

            # Complexité gratuite : chaque nœud compte pour la fonction
            # courante (les deux phases — aucun surcoût).
            if cadres:
                cadres[-1][_C_NB_NOEUDS] += 1

            chemin.append(enfant)
            pile.append((enfant, kind, _iter_enfants(enfant)))

    @staticmethod
    def _kind_repli(noeud: ast.AST) -> int:
        """Classification exacte (isinstance) pour arbres synthétiques.

        `ast.parse` ne produit que des types exacts (couverts par
        `_KIND_PAR_TYPE`) ; ce repli ne sert qu'aux sous-classes
        construites à la main — quasi jamais emprunté.
        """
        if isinstance(noeud, ast.FunctionDef):
            return _K_FONCTION | _K_COMPTABLE
        if isinstance(noeud, ast.AsyncFunctionDef):
            return _K_FONCTION
        if isinstance(noeud, (ast.For, ast.While)):
            return _K_COMPTABLE | _K_CONTROLE | _K_LILITH
        if isinstance(noeud, ast.If):
            return _K_COMPTABLE | _K_CONTROLE
        if isinstance(noeud, ast.With):
            return _K_CONTROLE
        if isinstance(noeud, ast.Call):
            return _K_APPEL
        if isinstance(noeud, ast.ClassDef):
            return _K_CLASSE
        if isinstance(noeud, (ast.Import, ast.ImportFrom)):
            return _K_IMPORT
        return 0

    def _identifier_oudjat(self, complet: bool = True):
        """Identifie la fonction dominante ; φ-ratios en mode complet seul."""
        if not self.resultat.fonctions:
            return
        self.resultat.oudjat = max(
            self.resultat.fonctions, key=lambda f: f.complexite
        )
        if complet:
            self._calculer_phi_ratios()

    def _calculer_phi_ratios(self):
        """Normalise la complexité de chaque fonction par la moyenne."""
        moyenne = sum(f.complexite for f in self.resultat.fonctions) / len(
            self.resultat.fonctions
        )
        if moyenne == 0:
            return
        for f in self.resultat.fonctions:
            f.phi_ratio = f.complexite / moyenne

    # ────────────────────────────────────────────────────────
    # RÈGLES DE CODAGE SOUVERAIN (évaluées inline, mode complet)
    # ────────────────────────────────────────────────────────

    def _regle_fibonacci(self, noeud: ast.AST, nb_lignes: int,
                         fib_proche: int):
        """Règle III — Taille Naturelle : les fonctions suivent Fibonacci.

        `fib_proche` est pré-calculé par l'appelant (évite de le chercher
        deux fois : ici et dans `distance_fibonacci`).
        """
        if nb_lignes > 55 and abs(nb_lignes - fib_proche) > 10:
            self._annoter(
                noeud.lineno,
                f"FIBONACCI : '{noeud.name}' ({nb_lignes} lignes) s'éloigne "
                f"de la séquence naturelle (idéal: {fib_proche}). "
                "Scinder pour réduire la pression morphique.",
                "WARNING",
                "FIBONACCI"
            )

    def _regle_hermeticite(self, noeud: ast.AST):
        """Règle IV — Herméticité : une fonction ne reçoit pas plus de 5 args."""
        nb_args = len(noeud.args.args)
        if nb_args > 5:
            self._annoter(
                noeud.lineno,
                f"SOUVERAINETÉ : '{noeud.name}' reçoit {nb_args} arguments. "
                "Encapsuler dans un objet (max: 5 / idéal φ: 3).",
                "INFO",
                "SOUVERAINETE"
            )

    # ────────────────────────────────────────────────────────
    # UTILITAIRES
    # ────────────────────────────────────────────────────────

    def _est_appel_open(self, node: ast.Call) -> bool:
        """Retourne True si le nœud est un appel à open()."""
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "open"
        if isinstance(func, ast.Attribute):
            return func.attr == "open"
        return False

    def _annoter(self, ligne: int, msg: str, niveau: str, categorie: str):
        """Enregistre une annotation chirurgicale sur une ligne de code."""
        extrait = self.lignes[ligne - 1].strip() if ligne <= len(self.lignes) else ""
        self.resultat.annotations.append(
            Annotation(ligne=ligne, message=msg, niveau=niveau,
                       extrait=extrait, categorie=categorie)
        )
