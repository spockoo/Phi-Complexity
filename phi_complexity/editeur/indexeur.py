"""
editeur/indexeur.py — Index des symboles d'un projet pour la navigation.

Construit `{chemin: [Symbole, ...]}` en réutilisant les analyseurs
phi (`obtenir_analyseur(...).analyser(complet=...).fonctions`). Seuls les
fichiers d'extension supportée sont indexés ; un fichier qui échoue à
l'analyse est simplement ignoré (jamais de plantage de l'indexation globale).
Un cache par mtime évite de ré-analyser les fichiers inchangés.

v0.8.0 « La Vitesse » : deux phases + parallélisme.
- `complet=True` (défaut) : analyse complète par fichier (métriques).
- `complet=False` : phase 1 — noms, lignes, proxy de complexité
  (`complexite = nb_lignes`, voir `langs/base.py`). Destiné à
  `phi index --rapide`.
- `parallele=True` (défaut) : les fichiers sont indépendants, ils sont
  analysés dans un pool de processus (`ProcessPoolExecutor`). Repli
  séquentiel honnête si le pool échoue.
"""
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Dict, List, Optional

from ..langs import obtenir_analyseur, est_fichier_supporte, analyseur_disponible
from ..langs import registry as _registre


# Dossiers exclus par défaut de l'indexation : dépendances, caches,
# artefacts de build — jamais du code du projet. L'« oudjat suprême »
# ne doit plus jamais être un script de dépendance.
# `None` → ces exclusions ; `[]` → aucune ; sinon liste explicite.
EXCLUSIONS_DEFAUT = (
    ".lake",        # artefacts de build Lean
    "_build",       # artefacts de build génériques
    ".git",         # historique versionné, pas du code courant
    "__pycache__",  # bytecode Python
    "node_modules", # dépendances JS/TS
    "target",       # artefacts de build Rust
    ".venv",        # environnements virtuels Python
    "venv",         # variante courante
)


def _est_exclu(nom_dossier: str, exclusions) -> bool:
    """
    Un dossier est exclu par nom exact, ou s'il se termine par .egg-info.

    Liste vide = aucune exclusion du tout (--no-exclude : l'utilisateur
    a explicitement demandé à tout indexer, y compris les œufs).
    """
    if not exclusions:
        return False
    if nom_dossier in exclusions:
        return True
    return nom_dossier.endswith(".egg-info")


def _resoudre_exclusions(exclusions) -> list:
    """`None` → défaut ; sinon la liste telle quelle (vide = tout indexer)."""
    if exclusions is None:
        return list(EXCLUSIONS_DEFAUT)
    return list(exclusions)


@dataclass
class Symbole:
    """Un symbole navigable : fonction (ou assimilé) repérée par phi."""
    nom: str
    ligne: int
    complexite: int
    langage: str
    fichier: str
    extraction: str = "analyseur"  # "analyseur" ou "robuste_repli"
    # (durcissement 2026-10-02 : repli robuste Lean quand tree-sitter
    # a avalé la déclaration — métriques = proxy lignes)


# Cache d'invalidation : (chemin, complet) -> mtime de la dernière
# indexation réussie. La clé inclut `complet` : une phase 1 ne doit jamais
# faire croire qu'une analyse complète est en cache (et inversement).
_MTIMES: Dict[tuple, float] = {}


def _symboles_du_fichier(chemin: str, langage: Optional[str] = None,
                         complet: bool = True) -> Optional[List[Symbole]]:
    """
    Analyse un fichier et retourne ses symboles, ou None en cas d'échec.

    `complet=False` : phase 1 — symboles seuls, sans les règles
    souveraines (voir `langs/base.py`). Fonction de niveau module :
    utilisable telle quelle (via `functools.partial`) comme travail
    d'un pool de processus.
    """
    try:
        resultat = obtenir_analyseur(chemin, langage=langage).analyser(complet=complet)
    except Exception:
        return None
    symboles = []
    for f in resultat.fonctions:
        try:
            symboles.append(
                Symbole(
                    nom=f.nom,
                    ligne=int(f.ligne),
                    complexite=int(f.complexite),
                    langage=resultat.langage,
                    fichier=chemin,
                    extraction=getattr(f, "extraction", "analyseur"),
                )
            )
        except Exception:
            continue
    return symboles


def _parcourir(dossier: str, exclusions) -> List[str]:
    """
    Liste triée de TOUS les fichiers sous `dossier`, dossiers exclus élagués.

    L'élagage se fait pendant `os.walk` (les dossiers exclus ne sont jamais
    descendus), par nom de dossier — jamais par motif sur le chemin complet.
    """
    trouves = []
    for racine, dossiers, noms in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if not _est_exclu(d, exclusions))
        for nom in noms:
            trouves.append(os.path.join(racine, nom))
    return sorted(trouves)


def _fichiers_supportes(dossier: str, exclusions=None) -> List[str]:
    """Liste triée des fichiers d'extension supportée sous `dossier`."""
    exclusions = _resoudre_exclusions(exclusions)
    return [c for c in _parcourir(dossier, exclusions) if est_fichier_supporte(c)]


def fichiers_non_supportes(dossier: str, exclusions=None,
                           langage: Optional[str] = None) -> List[dict]:
    """
    Fichiers présents mais SANS analyseur fonctionnel — le rapport honnête
    que l'indexeur devait depuis le début : aucun faux négatif invisible.

    Chaque entrée : {fichier, extension, raison} où raison vaut
    "extension inconnue" (aucun langage mappé) ou "tree-sitter manquant"
    (langage reconnu mais `tree-sitter-language-pack` absent).
    Si `langage` est forcé, il s'applique comme analyseur de repli et la
    liste est vide (l'utilisateur a explicitement pris la responsabilité).
    """
    if not os.path.isdir(dossier):
        return []
    if langage:
        # Langage forcé : l'utilisateur a explicitement pris la responsabilité
        # de l'analyse — rien n'est déclaré non supporté.
        return []
    from ..langs import langage_pour_extension
    exclusions = _resoudre_exclusions(exclusions)
    resultat = []
    for chemin in _parcourir(dossier, exclusions):
        if analyseur_disponible(chemin):
            continue
        ext = os.path.splitext(chemin)[1].lower() or "(sans extension)"
        if langage_pour_extension(chemin) is None:
            raison = "extension inconnue"
        elif ext == ".lean" and _registre.treesitter_disponible():
            # Pack présent mais grammaire Lean inutilisable : le signal
            # honnête, pas « tree-sitter manquant ».
            raison = "grammaire lean indisponible"
        else:
            raison = "tree-sitter manquant"
        resultat.append({"fichier": chemin, "extension": ext, "raison": raison})
    return resultat


def rafraichir_fichier(index: Dict[str, List[Symbole]], chemin: str,
                       langage: Optional[str] = None,
                       complet: bool = True) -> None:
    """
    (Re-)indexe un seul fichier dans `index` (modification en place).

    - Fichier absent ou non supporté → entrée retirée de l'index.
    - mtime inchangé → rien n'est refait.
    - Échec d'analyse → entrée retirée, sans lever d'exception ici ;
      l'échec reste traçable via `fichiers_non_supportes()` et fait
      échouer bruyamment `phi veille` / `phi snapshot`
      (durcissement 2026-10-01).
    - `langage` force le langage d'analyse (défaut : détection par extension).
    - `complet=False` : phase 1 (symboles seuls, sans règles souveraines).

    Chemin séquentiel (un seul fichier : le pool coûterait plus qu'il
    ne rapporterait) — l'éditeur TUI l'utilise à chaque frappe.
    """
    cle = (chemin, complet)
    if not os.path.isfile(chemin) or not est_fichier_supporte(chemin):
        index.pop(chemin, None)
        _MTIMES.pop(cle, None)
        return
    try:
        mtime = os.path.getmtime(chemin)
    except OSError:
        index.pop(chemin, None)
        _MTIMES.pop(cle, None)
        return
    if _MTIMES.get(cle) == mtime and chemin in index:
        return
    symboles = _symboles_du_fichier(chemin, langage=langage, complet=complet)
    if symboles is None:
        index.pop(chemin, None)
        _MTIMES.pop(cle, None)
        return
    index[chemin] = symboles
    _MTIMES[cle] = mtime


def _indexer_en_parallele(fichiers: List[str], langage: Optional[str],
                          complet: bool) -> Dict[str, List[Symbole]]:
    """
    Indexe `fichiers` dans un pool de processus (un fichier = une tâche).

    Les fichiers sont indépendants : c'est du parallélisme pur, sans
    état partagé. Le cache `_MTIMES` est mis à jour dans le processus
    parent à partir des résultats. En cas d'échec du pool (quelle qu'en
    soit la cause), repli séquentiel honnête — jamais de plantage.
    Le pool est borné : min(8, cpu_count, nb fichiers) — un
    ProcessPoolExecutor sans limite créerait trop de workers sur une
    grosse VM.
    """
    index: Dict[str, List[Symbole]] = {}
    travail = partial(_symboles_du_fichier, langage=langage, complet=complet)
    try:
        workers = min(8, os.cpu_count() or 1, len(fichiers))
        with ProcessPoolExecutor(max_workers=workers) as piscine:
            resultats = list(piscine.map(travail, fichiers))
    except Exception:
        # Repli : le parallélisme a échoué, on fait simple et robuste.
        for chemin in fichiers:
            rafraichir_fichier(index, chemin, langage=langage, complet=complet)
        return index
    for chemin, symboles in zip(fichiers, resultats):
        if symboles is None:
            continue
        index[chemin] = symboles
        try:
            _MTIMES[(chemin, complet)] = os.path.getmtime(chemin)
        except OSError:
            pass
    return index


def indexer_projet(dossier: str, langage: Optional[str] = None,
                   exclusions=None, complet: bool = True,
                   parallele: bool = True) -> Dict[str, List[Symbole]]:
    """
    Indexe récursivement tous les fichiers supportés de `dossier`.

    Retourne `{chemin_absolu_ou_relatif: [Symbole, ...]}`. Un fichier
    dont l'analyse échoue est exclu de l'index (jamais d'exception levée
    ici) — mais il ne disparaît PAS silencieusement : il reste visible via
    `fichiers_non_supportes()`, et `phi veille` / `phi snapshot` échouent
    bruyamment (INSTRUMENT DÉGRADÉ, exit 3) quand l'arrière-plan est perdu
    (durcissement 2026-10-01 : la fausse « dégradation » 3531 → 33
    symboles du 2026-09-30 ne doit plus pouvoir se produire).
    `langage` force le langage d'analyse (défaut : détection par extension).
    `exclusions` : `None` → exclusions par défaut (`.lake/`, `.git/`, …) ;
    `[]` → aucune exclusion ; sinon liste explicite de noms de dossiers.
    `complet=False` : phase 1 — index seul, sans les règles souveraines.
    `parallele=False` : force le chemin séquentiel (débogage, déterminisme
    strict des tests).
    Voir `fichiers_non_supportes()` pour le rapport honnête des fichiers
    ignorés — un index qui tait ses angles morts n'est pas un instrument.
    """
    index: Dict[str, List[Symbole]] = {}
    if not os.path.isdir(dossier):
        return index
    fichiers = _fichiers_supportes(dossier, exclusions=exclusions)
    if parallele and len(fichiers) > 1:
        return _indexer_en_parallele(fichiers, langage, complet)
    for chemin in fichiers:
        rafraichir_fichier(index, chemin, langage=langage, complet=complet)
    return index


def symboles_de_l_index(index: Dict[str, List[Symbole]]) -> List[Symbole]:
    """Aplatit l'index en une simple liste de symboles."""
    return [s for symboles in index.values() for s in symboles]
