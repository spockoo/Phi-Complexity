#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domaine GRAPHE — batterie Lilith (chantier 1/5).

Instruments structurels : ce que le distributionnel ne voit pas (C1, C5).
Réutilise `impact_analyse/graphe.py` (GrapheDependances) — « battre ou ne pas
exister » : le parseur existe, on l'instrumente, on ne le réécrit pas.

Verdicts typés sur F2 (code mort) ; SILENCIEUX sur F3-comportemental.
Stdlib uniquement.
"""

from __future__ import annotations

import ast
import os
import sys

__all__ = [
    "construire_graphe",
    "noeuds_fichier",
    "detecter_code_mort",
    "detecter_api_morte",
    "centralite_couplage",
    "SEUIL_HUB",
    "SEUIL_INSTABILITE",
    "SEUIL_CE_MIN",
]

SEUIL_HUB = 10          # fan-in "appel" >= 10 : hub structurel
SEUIL_INSTABILITE = 0.8  # I = Ce/(Ca+Ce) >= 0.8 : module instable
SEUIL_CE_MIN = 5        # ... avec au moins 5 dépendances sortantes


def _charger_impact_analyse(racine: str):
    """Import paresseux d'impact_analyse (le dépôt n'est pas un package)."""
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from impact_analyse import graphe as _g  # noqa: E402
    from impact_analyse import risque as _r  # noqa: E402
    return _g, _r


def construire_graphe(racine: str):
    """Construit le graphe de dépendances du dépôt ENTIER (cf. SPEC §2.1).

    Coût : O(F·S). À construire UNE fois par session de batterie.
    """
    _g, _ = _charger_impact_analyse(racine)
    return _g.GrapheDependances.depuis_repertoire(racine)


def _relpath(racine: str, chemin: str) -> str:
    return os.path.relpath(os.path.abspath(chemin), os.path.abspath(racine))


def noeuds_fichier(graphe, racine: str, chemin: str) -> list[str]:
    """Nids des fonctions/méthodes définies dans le fichier (pas les classes)."""
    rel = _relpath(racine, chemin)
    return [n["id"] for n in graphe.noeuds()
            if n["fichier"] == rel and n["type"] == "fonction"]


def _appels_entrants(graphe, nid: str) -> list[str]:
    """Sources des arêtes entrantes de type 'appel' uniquement."""
    return [src for src, meta in graphe.predecesseurs(nid)
            if meta.get("type") == "appel"]


def _est_dunder(qualname: str) -> bool:
    court = qualname.split(".")[-1]
    return court.startswith("__") and court.endswith("__")


def _classe_parente_nid(nid: str) -> str | None:
    """Nid de la classe englobante d'un nœud méthode, ou None."""
    rel, _, qualname = nid.partition(":")
    if "." not in qualname:
        return None
    return f"{rel}:{qualname.rpartition('.')[0]}"


def _est_dispatch_visiteur(graphe, nid: str) -> bool:
    """Pattern visiteur : méthode `visit_*`/`leave_*` d'une classe visiteur.

    ast.NodeVisitor appelle dynamiquement `visit_ClassDef`, `visit_Assign`,
    ... via generic_visit — aucune arête « appel » statique n'existe, par
    CONSTRUCTION du pattern (stdlib documentée), pas par oubli de l'analyseur.
    Les exclure du code mort n'est pas un passe-droit ad hoc : c'est la
    reconnaissance d'un protocole de dispatch dynamique explicite.
    """
    qualname = nid.split(":", 1)[1]
    court = qualname.split(".")[-1]
    if not (court.startswith("visit_") or court.startswith("leave_")):
        return False
    nid_classe = _classe_parente_nid(nid)
    if nid_classe is None:
        return False
    for dst, meta in graphe.successeurs(nid_classe):
        if meta.get("type") == "heritage" and "Visitor" in dst.split(":")[-1]:
            return True
    # repli : la classe définit elle-même generic_visit/visit (visiteur maison)
    for dst, meta in graphe.successeurs(nid_classe):
        if meta.get("type") == "appel":
            nom = dst.split(":")[-1].split(".")[-1]
            if nom in ("generic_visit", "visit"):
                return True
    return False


_CACHE_USAGE_TESTS: dict[str, set[str]] = {}


def _noms_utilises_tests(racine: str) -> set[str]:
    """Noms APPELÉS ou IMPORTÉS dans les fichiers de test (usage réel).

    Diffère volontairement de risque.couverture_test (occurrence brute) :
    pour la détection de code mort, une simple MENTION dans une chaîne
    (ex. un test qui ASSERT la mortalité d'une fonction) n'est pas une
    couverture — l'occurrence brute créerait un faux négatif auto-infligé.
    Pour le score de risque (chantier 3), le rappel de la mention reste
    préférable : périmètres distincts, définitions distinctes, documentées.

    Cache par racine (lecture seule du dépôt).
    """
    cle = os.path.abspath(racine)
    if cle in _CACHE_USAGE_TESTS:
        return _CACHE_USAGE_TESTS[cle]
    utilises: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(racine):
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), racine)
            if "test" not in rel.lower():
                continue
            try:
                with open(os.path.join(dirpath, fn), "r",
                          encoding="utf-8", errors="strict") as fh:
                    arbre = ast.parse(fh.read())
            except (SyntaxError, ValueError, OSError, UnicodeDecodeError):
                continue
            for node in ast.walk(arbre):
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Name):
                        utilises.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        utilises.add(f.attr)
                elif isinstance(node, ast.Import):
                    for a in node.names:
                        utilises.add((a.asname or a.name).split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        if a.name != "*":
                            utilises.add(a.asname or a.name)
    _CACHE_USAGE_TESTS[cle] = utilises
    return utilises


def _couvert_par_test_usage(utilises: set[str], qualname: str,
                            nom_court: str) -> bool:
    """True si le qualname ou le nom court est appelé/importé dans un test."""
    return bool(utilises) and (qualname in utilises or nom_court in utilises)


def _noms_all_et_references(chemin: str):
    """(__all__ du module, noms référencés hors de leur propre def).

    Référence = tout Name chargé dans le module SAUF dans le sous-arbre de
    sa propre définition (un usage comme callback/décorateur/point d'entrée
    __main__ compte comme vivant — conservateur : moins de faux positifs).
    """
    with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
        arbre = ast.parse(fh.read())
    noms_all: list[str] = []
    for node in arbre.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__"
                        for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    noms_all.append(elt.value)
    # sous-arbres des defs/classes (leurs noms propres)
    propres: dict[str, ast.AST] = {}

    class Collecteur(ast.NodeVisitor):
        def __init__(self):
            self.pile: list[str] = []

        def _qn(self, nom):
            return ".".join(self.pile + [nom]) if self.pile else nom

        def visit_FunctionDef(self, node):  # noqa: N802
            propres[self._qn(node.name)] = node
            self.pile.append(node.name)
            self.generic_visit(node)
            self.pile.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: E305

        def visit_ClassDef(self, node):  # noqa: N802
            self.pile.append(node.name)
            self.generic_visit(node)
            self.pile.pop()

    Collecteur().visit(arbre)
    # noms chargés hors du sous-arbre propre de chaque def
    references: dict[str, set[str]] = {qn: set() for qn in propres}

    class RefVisitor(ast.NodeVisitor):
        def __init__(self, qn_exclu, racine_exclue):
            self.qn_exclu = qn_exclu
            self.racine_exclue = racine_exclue
            self.trouves: set[str] = set()

        def visit_Name(self, node):  # noqa: N802
            if isinstance(node.ctx, ast.Load):
                self.trouves.add(node.id)

        def generic_visit(self, node):
            if node is self.racine_exclue:
                return  # ne pas descendre dans sa propre définition
            super().generic_visit(node)

    for qn, sous_arbre in propres.items():
        v = RefVisitor(qn, sous_arbre)
        v.visit(arbre)
        references[qn] = v.trouves
    return noms_all, references


def detecter_code_mort(graphe, racine: str, chemin: str) -> dict:
    """F2 : fonctions définies, jamais appelées (analyse statique).

    Exclusions (conservatrices, toutes rapportées) : dunders, noms dans
    __all__ (-> api_morte), méthodes visit_*/leave_* d'une classe visiteur
    (dispatch dynamique), noms référencés hors de leur def (callbacks,
    __main__, décorateurs), noms APPELÉS ou IMPORTÉS par un fichier de test
    (usage réel — une mention en chaîne n'est pas une couverture).
    Verdict : ÉVIDENCE si candidats (jamais DÉMONTRÉ : limites R4.1e/R6 de
    graphe.py — getattr/eval/dispatch dynamique invisibles) ;
    INDÉCIDABLE si rien (« aucun détecté » ≠ « aucun »).
    """
    _, _r = _charger_impact_analyse(racine)
    rel = _relpath(racine, chemin)
    noms_all, references = _noms_all_et_references(chemin)
    # Usage réel dans les tests (appel/import), pas simple mention : une mention
    # dans une chaîne (ex. assertion de mortalité) n'est pas une couverture.
    utilises_tests = _noms_utilises_tests(racine)
    nids_connus = {n["id"] for n in graphe.noeuds()}
    candidats = []
    exclusions = []
    for nid in noeuds_fichier(graphe, racine, chemin):
        qualname = nid.split(":", 1)[1]
        court = qualname.split(".")[-1]
        if _est_dunder(qualname):
            exclusions.append({"qualname": qualname,
                               "raison": "dunder (appel implicite)"})
            continue  # appel implicite (instanciation, opérateurs, ...)
        if court in noms_all:
            exclusions.append({"qualname": qualname,
                               "raison": "__all__ (voir detecter_api_morte)"})
            continue  # API déclarée -> instrument api_morte
        if _appels_entrants(graphe, nid):
            continue  # appelée quelque part
        if _est_dispatch_visiteur(graphe, nid):
            exclusions.append({"qualname": qualname,
                               "raison": "dispatch dynamique (pattern visiteur)"})
            continue  # appelée par generic_visit, pas par arête statique
        refs = references.get(qualname, set()) | references.get(court, set())
        if court in refs or qualname in refs:
            exclusions.append({"qualname": qualname,
                               "raison": "référencée hors de sa def"})
            continue  # référencée (callback, __main__, décorateur, ...)
        if _couvert_par_test_usage(utilises_tests, qualname, court):
            exclusions.append({"qualname": qualname,
                               "raison": "appelée/importée par un test"})
            continue  # utilisée par un test
        ligne = next((n["ligne"] for n in graphe.noeuds() if n["id"] == nid), 0)
        candidats.append({"nid": nid, "qualname": qualname, "ligne": ligne})
    candidats.sort(key=lambda c: c["ligne"])
    base = {
        "facette": "F2",
        "instrument": "detecter_code_mort",
        "candidats": candidats,
        "exclusions": exclusions,
    }
    if candidats:
        base.update({
            "verdict": "ÉVIDENCE",
            "motif": (f"{len(candidats)} fonction(s) sans aucun appelant "
                      f"statique dans tout le dépôt (graphe.py R1–R4), après "
                      f"{len(exclusions)} exclusion(s) motivée(s). "
                      f"Limites : getattr/eval/fabriques dynamiques non résolus "
                      f"(R6) — un faux positif reste possible, d'où ÉVIDENCE "
                      f"et non DÉMONTRÉ."),
        })
        return base
    base.update({
        "verdict": "INDÉCIDABLE",
        "motif": ("Aucune fonction morte détectée par l'analyse statique. "
                  "L'absence de détection n'est pas une preuve d'absence "
                  "(dispatch dynamique, callbacks inter-fichiers, monkey-patch)."),
    })
    return base


def detecter_api_morte(graphe, racine: str, chemin: str) -> dict:
    """F3-structurelle (critères §1) : nom dans __all__, zéro appelant.

    Cas vérité terrain : `ordre_modification_sure` dans impact_analyse/risque.py.
    """
    rel = _relpath(racine, chemin)
    noms_all, _ = _noms_all_et_references(chemin)
    nids_noeuds = {n["id"]: n for n in graphe.noeuds()}
    mortes = []
    for nom in noms_all:
        nid = f"{rel}:{nom}"
        noeud = nids_noeuds.get(nid)
        # Seules les fonctions/classes sont « appelables » : une constante
        # exportée (ex. NIVEAUX) n'a pas d'« appelant » par construction —
        # la rapporter serait un faux positif, pas une API morte.
        if noeud is None or noeud.get("type") not in ("fonction", "classe"):
            continue
        if not _appels_entrants(graphe, nid):
            ligne = noeud.get("ligne", 0)
            mortes.append({"nid": nid, "nom": nom, "ligne": ligne})
    mortes.sort(key=lambda c: c["ligne"])
    if mortes:
        return {
            "facette": "F3-structurelle",
            "instrument": "detecter_api_morte",
            "verdict": "ÉVIDENCE",
            "motif": (f"{len(mortes)} nom(s) exporté(s) dans __all__ sans "
                      f"aucun appelant dans tout le dépôt : API publique "
                      f"déclarée, jamais réalisée."),
            "candidats": mortes,
        }
    return {
        "facette": "F3-structurelle",
        "instrument": "detecter_api_morte",
        "verdict": "INDÉCIDABLE",
        "motif": "Aucune API morte détectée (tous les noms de __all__ ont "
                 "au moins un appelant statique, ou pas de __all__).",
        "candidats": [],
    }


def centralite_couplage(graphe, racine: str, chemin: str) -> dict:
    """Structure d'appels : fan-in/fan-out par fonction, Ca/Ce/I par module.

    Lève partiellement C1 (la structure d'appels devient visible).
    SILENCIEUX sur F3-comportemental (C4 : un hub peut être correct).
    Mesures + seuils documentés ; pas de verdict binaire.
    """
    rel = _relpath(racine, chemin)
    fonctions = []
    for nid in noeuds_fichier(graphe, racine, chemin):
        fan_in = len(_appels_entrants(graphe, nid))
        fan_out = len([dst for dst, meta in graphe.successeurs(nid)
                       if meta.get("type") == "appel"
                       and not dst.startswith("externe:")])
        fonctions.append({
            "nid": nid,
            "qualname": nid.split(":", 1)[1],
            "fan_in": fan_in,
            "fan_out": fan_out,
            "hub": fan_in >= SEUIL_HUB,
        })
    modules_in: set[str] = set()   # modules qui appellent ce fichier (Ca)
    modules_out: set[str] = set()  # modules appelés par ce fichier (Ce)
    for nid in noeuds_fichier(graphe, racine, chemin):
        for src, meta in graphe.predecesseurs(nid):
            if meta.get("type") == "appel" and not src.startswith("externe:"):
                modules_in.add(src.split(":")[0])
        for dst, meta in graphe.successeurs(nid):
            if meta.get("type") == "appel" and not dst.startswith("externe:"):
                modules_out.add(dst.split(":")[0])
    modules_in.discard(rel)
    modules_out.discard(rel)
    ca, ce = len(modules_in), len(modules_out)
    instabilite = ce / (ca + ce) if (ca + ce) > 0 else 0.0
    hubs = [f for f in fonctions if f["hub"]]
    notes = []
    if hubs:
        notes.append(f"{len(hubs)} hub(s) structurel(s) (fan-in >= {SEUIL_HUB}).")
    if instabilite >= SEUIL_INSTABILITE and ce >= SEUIL_CE_MIN:
        notes.append(f"Module instable : I={instabilite:.2f} >= {SEUIL_INSTABILITE} "
                     f"avec Ce={ce} >= {SEUIL_CE_MIN}.")
    return {
        "facette": "structure",
        "instrument": "centralite_couplage",
        "verdict": ("ÉVIDENCE" if notes else "INDÉCIDABLE"),
        "motif": ("Points d'attention structurels : " + " ".join(notes)
                  if notes else
                  "Aucun hub ni instabilité au-delà des seuils "
                  f"(hub: fan-in>={SEUIL_HUB} ; instable: I>={SEUIL_INSTABILITE}, "
                  f"Ce>={SEUIL_CE_MIN})."),
        "fonctions": sorted(fonctions, key=lambda f: -f["fan_in"]),
        "module": {"Ca": ca, "Ce": ce, "I": round(instabilite, 3)},
    }
