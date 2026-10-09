#!/usr/bin/env python3
"""Score de risque d'une modification — analyseur d'impact (chantier 3/5).

Stdlib uniquement (`ast`). Ne dépend de rien d'autre que de
`propagation.py` de façon OPTIONNELLE (paramètre `impact` de `score_risque`).

=====================================================================
DÉFINITION FORMELLE DE LA MÉTRIQUE  (à lire avant l'implémentation)
=====================================================================

Soit G = (V, E) le graphe de dépendances du code : (u → v) ∈ E signifie
que v *dépend de* u (modifier u peut impacter v). Pour un nœud n ∈ V :

1.  ENSEMBLE D'IMPACT — I(n) = { m ∈ V | m ≠ n et il existe un chemin
    orienté n ⇝ m dans G }  (atteignabilité avant, sans n lui-même).

2.  D(n) = |I(n)| : nombre de nœuds impactés par la modification de n.

3.  P(n) = max { longueur du plus court chemin n ⇝ m | m ∈ I(n) },
    avec la convention P(n) = 0 si I(n) = ∅.
    (P mesure la « profondeur » de propagation, en niveaux.)

4.  T(n) ∈ {0, 1} : 1 ssi au moins un fichier *de test* fait référence
    à n. Un fichier est « de test » si la chaîne « test » (insensible
    à la casse) apparaît dans son nom ou son chemin relatif à la
    racine analysée. La référence est détectée par occurrence brute
    du qualname de n OU de son nom court dans le contenu du fichier.

5.  C(n) : complexité cyclomatique (variante McCabe simplifiée).
    Si n est une fonction ou une méthode :
        C(n) = 1 + |{ nœud a de l'AST du corps de n |
                      type(a) ∈ S }|
    où S = {If, For, AsyncFor, While, ExceptHandler, With, AsyncWith,
            BoolOp, IfExp, comprehension, Assert}.
    Si n n'est pas une fonction/méthode : C(n) = 0.

6.  SCORE DE RISQUE :
        R(n) = 100 × ( 0.35 × min(D,50)/50
                     + 0.25 × min(P,6)/6
                     + 0.25 × (1 − T)
                     + 0.15 × min(C,20)/20 )
    avec D = D(n), P = P(n), T = T(n), C = C(n).
    Les plafonds (50, 6, 20) rendent chaque facteur adimensionné dans
    [0, 1] ; les poids (0.35, 0.25, 0.25, 0.15) somment à 1, donc
    R(n) ∈ [0, 100].

7.  NIVEAUX :  R < 25        → FAIBLE
              25 ≤ R ≤ 50    → MOYEN
              50 < R ≤ 75    → ÉLEVÉ
              R > 75         → CRITIQUE

8.  ORDRE DE MODIFICATION SÛRE : étant donné un ensemble de nœuds à
    modifier, les trier par R croissant — modifier d'abord les nœuds
    les moins risqués.

Conventions d'interface
-----------------------
`graphe` désigne l'une des deux formes suivantes (les deux acceptées) :
  a) un dict  nid → info, où info est un dict pouvant contenir :
        "succ"     : itérable des nids directement impactés par nid
                     (voisins avant : v tels que nid → v),
        "ast"      : nœud `ast` (FunctionDef/AsyncFunctionDef/ClassDef…)
                     ou None,
        "qualname" : nom qualifié (str) ou None,
        "nom"      : nom court (str) ou None ;
     si info est un set/list/tuple, il est interprété comme les "succ".
  b) un objet exposant les méthodes :
        .successeurs(nid) -> itérable,
        .noeud(nid)       -> dict de la forme (a) ou None (optionnel).
Le nid lui-même est utilisé comme qualname de repli pour la détection
de tests (T), et son dernier composant après "." comme nom court.
"""

import ast
import os

__all__ = [
    "complexite_cyclomatique",
    "couverture_test",
    "score_risque",
    "ordre_modification_sure",
    "NIVEAUX",
]

NIVEAUX = ("FAIBLE", "MOYEN", "ELEVE", "CRITIQUE")

# Types AST qui incrémentent la complexité cyclomatique (§5 de la définition).
_TYPES_BRANCHEMENT = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.BoolOp,
    ast.IfExp,
    ast.comprehension,
    ast.Assert,
)

# Plafonds de normalisation (§6 de la définition).
_PLAFOND_D = 50
_PLAFOND_P = 6
_PLAFOND_C = 20

# Poids des quatre facteurs (§6 de la définition).
_POIDS = {"D": 0.35, "P": 0.25, "T": 0.25, "C": 0.15}


# ------------------------------------------------------------------ utils

def _successeurs(graphe, nid):
    """Voisins avant de nid : les nœuds directement impactés par nid."""
    if hasattr(graphe, "successeurs"):
        return list(graphe.successeurs(nid))
    info = graphe.get(nid)
    if info is None:
        return []
    if isinstance(info, dict):
        return list(info.get("succ", ()))
    # set / list / tuple : interprétation directe comme successeurs.
    return list(info)


def _info_noeud(graphe, nid):
    """Renvoie le dict d'info d'un nœud, ou {} si indisponible."""
    if hasattr(graphe, "noeud"):
        try:
            info = graphe.noeud(nid)
        except Exception:
            info = None
        return info if isinstance(info, dict) else {}
    info = graphe.get(nid) if hasattr(graphe, "get") else None
    return info if isinstance(info, dict) else {}


def _impact_local(graphe, nid):
    """Calcule (D, P) par BFS avant sur les successeurs (§1–3).

    D = nombre de nœuds atteignables distincts, n exclu.
    P = profondeur maximale atteinte (niveaux BFS), 0 si aucun impact.
    Les cycles sont gérés par un ensemble de visités.
    """
    visites = set()
    profondeur = {}
    file = [nid]
    profondeur[nid] = 0
    visites.add(nid)
    p_max = 0
    while file:
        courant = file.pop(0)
        for s in _successeurs(graphe, courant):
            if s not in visites:
                visites.add(s)
                profondeur[s] = profondeur[courant] + 1
                p_max = max(p_max, profondeur[s])
                file.append(s)
    visites.discard(nid)  # n lui-même n'est pas « impacté » par sa modification
    return len(visites), p_max


# ------------------------------------------------------------------ API

def complexite_cyclomatique(noeud_ast) -> int:
    """Complexité cyclomatique §5 : 1 + nb de nœuds de branchement.

    Renvoie 0 si noeud_ast n'est pas une fonction/méthode
    (FunctionDef / AsyncFunctionDef) ou si None est passé.
    Le corps analysé est le nœud lui-même (ast.walk inclut la racine,
    qui n'est jamais un type de S, donc sans double comptage).
    """
    if noeud_ast is None:
        return 0
    if not isinstance(noeud_ast, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return 0
    return 1 + sum(
        1 for n in ast.walk(noeud_ast) if isinstance(n, _TYPES_BRANCHEMENT)
    )


def couverture_test(racine: str, qualname: str, nom_court: str) -> bool:
    """T(n) : True ssi un fichier de test référence n (§4).

    Fichier « de test » : « test » (insensible à la casse) dans son nom
    ou son chemin relatif à `racine`. Référence : occurrence brute du
    `qualname` ou du `nom_court` dans le contenu du fichier. Les fichiers
    illisibles ou non décodables sont ignorés silencieusement.
    """
    if not qualname and not nom_court:
        return False
    aiguilles = [a for a in (qualname, nom_court) if a]
    for dirpath, _dirnames, filenames in os.walk(racine):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), racine)
            if "test" not in rel.lower():
                continue
            chemin = os.path.join(dirpath, fn)
            try:
                with open(chemin, "r", encoding="utf-8", errors="strict") as f:
                    contenu = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            if any(a in contenu for a in aiguilles):
                return True
    return False


def _niveau(score: float) -> str:
    if score < 25:
        return "FAIBLE"
    if score <= 50:
        return "MOYEN"
    if score <= 75:
        return "ELEVE"
    return "CRITIQUE"


def score_risque(graphe, nid: str, racine: str, impact: dict | None = None) -> dict:
    """Calcule R(n) et son niveau pour le nœud `nid` (§6–7).

    `impact` : résultat de propagation.impact_avant si disponible, sinon
    None → calcul local par BFS avant (§1–3). Format attendu de `impact`
    (s'il est fourni) : dict avec au moins les clés "D" (int) et "P"
    (int), éventuellement "impactes" (ensemble des nœuds atteints).
    """
    if impact is not None:
        D = int(impact.get("D", 0))
        P = int(impact.get("P", 0))
    else:
        D, P = _impact_local(graphe, nid)

    info = _info_noeud(graphe, nid)
    qualname = info.get("qualname") or nid
    nom_court = info.get("nom") or str(nid).split(".")[-1]
    T = 1 if couverture_test(racine, qualname, nom_court) else 0
    C = complexite_cyclomatique(info.get("ast"))

    score = 100.0 * (
        _POIDS["D"] * min(D, _PLAFOND_D) / _PLAFOND_D
        + _POIDS["P"] * min(P, _PLAFOND_P) / _PLAFOND_P
        + _POIDS["T"] * (1 - T)
        + _POIDS["C"] * min(C, _PLAFOND_C) / _PLAFOND_C
    )
    return {
        "noeud": nid,
        "score": score,
        "niveau": _niveau(score),
        "facteurs": {"D": D, "P": P, "T": T, "C": C},
    }


def ordre_modification_sure(graphe, nids: list[str], racine: str) -> list[dict]:
    """Trie les nœuds par score de risque croissant (§8).

    Retourne la liste des résultats de `score_risque`, triés par score
    croissant (les moins risqués d'abord).
    """
    resultats = [score_risque(graphe, nid, racine) for nid in nids]
    resultats.sort(key=lambda r: r["score"])
    return resultats
