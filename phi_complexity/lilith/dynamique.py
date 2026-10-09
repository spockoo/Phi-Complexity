#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domaine DYNAMIQUE — batterie Lilith (chantier 1/5).

Mesures d'exécution RÉELLES (cProfile + tracemalloc, stdlib) : ce que ni le
profil statique (C4, C5) ni le graphe (comportement) ne voient — F3 partiel.

Règle d'honnêteté : un scénario est une preuve d'existence (« exécuté / non
exécuté ICI »), jamais une preuve universelle. Aucun DÉMONTRÉ sur F2/F3
depuis le seul dynamique.

Deux modes :
  - profiler_point_entree(chemin, entree, args) : exécute "module:qualname".
  - profiler_tests(racine, cible) : exécute la suite unittest du répertoire
    tests/ le plus proche couvrant la cible, sous cProfile.
"""

from __future__ import annotations

import ast
import cProfile
import contextlib
import importlib.util
import io
import os
import pstats
import sys
import time
import tracemalloc
import unittest

__all__ = [
    "profiler_point_entree",
    "profiler_tests",
    "profiler_tests_depuis_brut",
    "trouver_repertoire_tests",
    "executer_suite_brute",
    "BUDGET_IMPORT_MODULE_S",
    "detecter_non_execute",
    "detecter_hotspot",
    "TimeoutScenario",
    "TIMEOUT_DEFAUT",
    "SEUIL_HOTSPOT",
]

TIMEOUT_DEFAUT = 120          # secondes max par scénario
SEUIL_HOTSPOT = 0.50          # part du cumtime >= 50 % : anomalie de performance


class TimeoutScenario(TimeoutError):
    """Le scénario a dépassé le timeout : mesures invalides, pas de verdict."""


def _fonctions_fichier(chemin: str) -> dict[str, int]:
    """qualname -> ligne de première définition (fonctions + méthodes)."""
    with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
        arbre = ast.parse(fh.read())

    result: dict[str, int] = {}

    class V(ast.NodeVisitor):
        def __init__(self):
            self.pile: list[str] = []

        def _qn(self, nom):
            return ".".join(self.pile + [nom]) if self.pile else nom

        def visit_FunctionDef(self, node):  # noqa: N802
            result[self._qn(node.name)] = node.lineno
            self.pile.append(node.name)
            self.generic_visit(node)
            self.pile.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # noqa: E305

        def visit_ClassDef(self, node):  # noqa: N802
            self.pile.append(node.name)
            self.generic_visit(node)
            self.pile.pop()

    V().visit(arbre)
    return result


def _charger_module(chemin: str):
    """Charge un fichier .py comme module (sans l'exécuter deux fois)."""
    abspath = os.path.abspath(chemin)
    nom = "lilith_dyn_" + os.path.splitext(os.path.basename(abspath))[0]
    for mod in list(sys.modules.values()):
        if getattr(mod, "__file__", None) == abspath:
            return mod
    spec = importlib.util.spec_from_file_location(nom, abspath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


def _resoudre_qualname(module, qualname: str):
    obj = module
    for morceau in qualname.split("."):
        obj = getattr(obj, morceau)
    return obj


def _stats_par_fonction(pr: cProfile.Profile, chemin: str,
                        duree: float, pic_memoire: int) -> dict:
    """pstats -> {qualname: {ncalls, tottime, cumtime, part}} pour le fichier."""
    abspath = os.path.abspath(chemin)
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s)
    fonctions = _fonctions_fichier(chemin)
    par_fonction: dict[str, dict] = {}
    for (fichier, _ligne, nom), (cc, nc, tott, cumt, _appelants) in \
            ps.stats.items():
        if os.path.abspath(fichier) != abspath:
            continue
        # nom pstats = nom court ; on rattache au qualname par le nom court
        # (les homonymes méthode/fonction sont fusionnés — documenté).
        for qualname in fonctions:
            if qualname.split(".")[-1] == nom:
                e = par_fonction.setdefault(
                    qualname, {"ncalls": 0, "tottime": 0.0, "cumtime": 0.0})
                e["ncalls"] += nc
                e["tottime"] += tott
                e["cumtime"] += cumt
    total = sum(e["cumtime"] for e in par_fonction.values()) or 1e-12
    for e in par_fonction.values():
        e["part"] = e["cumtime"] / total
    return {
        "duree_reelle_s": duree,
        "pic_memoire_octets": pic_memoire,
        "fonctions": par_fonction,
        "definies_non_vues": sorted(set(fonctions) - set(par_fonction)),
    }


def _executer_surveille(chemin: str, fonction_lancement, timeout: int) -> dict:
    """Exécute fonction_lancement() sous cProfile + tracemalloc."""
    tracemalloc.start()
    pr = cProfile.Profile()
    debut = time.time()
    try:
        pr.enable()
        fonction_lancement()
        pr.disable()
    finally:
        duree = time.time() - debut
        _courant, pic = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    if duree > timeout:
        raise TimeoutScenario(
            f"scénario > {timeout}s ({duree:.1f}s) : mesures invalides")
    return _stats_par_fonction(pr, chemin, duree, pic)


def profiler_point_entree(chemin: str, entree: str, args=(),
                          timeout: int = TIMEOUT_DEFAUT) -> dict:
    """Profile l'exécution de `entree` ("qualname", ex. "main" ou "Cls.run").

    Sortie : dict de _stats_par_fonction + "entree", "chemin", "scenario".
    """
    module = _charger_module(chemin)
    cible = _resoudre_qualname(module, entree)

    def lancement():
        cible(*args)

    stats = _executer_surveille(chemin, lancement, timeout)
    stats.update({"chemin": os.path.abspath(chemin), "entree": entree,
                  "scenario": f"point_entree:{entree}"})
    return stats


def trouver_repertoire_tests(racine: str, chemin_cible: str) -> str:
    """Le répertoire tests/ le plus proche (SPEC §3), ou lève FileNotFoundError."""
    dossier = os.path.dirname(os.path.abspath(chemin_cible))
    curseur = dossier
    while True:
        candidat = os.path.join(curseur, "tests")
        if os.path.isdir(candidat):
            return candidat
        parent = os.path.dirname(curseur)
        if parent == curseur or not curseur.startswith(
                os.path.abspath(racine)):
            break
        curseur = parent
    candidat = os.path.join(os.path.abspath(racine), "tests")
    if os.path.isdir(candidat):
        return candidat
    raise FileNotFoundError(
        f"aucun répertoire tests/ pour {chemin_cible}")


BUDGET_IMPORT_MODULE_S = 60  # sonde d'import par module de test


def _import_sonde_ok(chemin_test: str, nom_mod: str, racine: str,
                     budget_s: int = BUDGET_IMPORT_MODULE_S) -> bool:
    """Sonde d'import en sous-processus : True si le module s'importe vite.

    Certains modules de test font un travail lourd À L'IMPORT (ex.
    tests/test_gap792_evidence.py parse un .phiast de plusieurs Go à
    l'import — > 4 min). Sans sonde, la batterie se fige. Le module
    ignoré est RAPPORTÉ (pas silencieux), avec son motif.
    """
    import subprocess
    code = (
        "import sys, importlib.util; "
        f"sys.path.insert(0, {os.path.abspath(racine)!r}); "
        "spec = importlib.util.spec_from_file_location("
        f"{nom_mod!r}, {os.path.abspath(chemin_test)!r}); "
        "m = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(m)"
    )
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, timeout=budget_s)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _suite_tests(racine: str, rep_tests: str,
                 budget_import: int = BUDGET_IMPORT_MODULE_S):
    """Construit la suite : TestCase + main() des modules non-TestCase.

    Retourne (suite, ignores) — ignores = [{"module", "raison"}].
    """
    chargeur = unittest.TestLoader()
    suite = unittest.TestSuite()
    ignores: list[dict] = []
    # Chargement manuel par importlib (pas de contrainte de package comme
    # discover : les répertoires tests/ du dépôt n'ont pas tous __init__.py).
    if os.path.abspath(racine) not in sys.path:
        sys.path.insert(0, os.path.abspath(racine))
    fichiers_tests = sorted(
        fn for fn in os.listdir(rep_tests)
        if fn.startswith("test") and fn.endswith(".py"))
    if not fichiers_tests:
        raise FileNotFoundError(
            f"aucun test_*.py dans {rep_tests}")
    for i, fn in enumerate(fichiers_tests):
        chemin_test = os.path.join(rep_tests, fn)
        nom_mod = f"lilith_dyn_test_{i}_{os.path.splitext(fn)[0]}"
        if nom_mod not in sys.modules:
            if not _import_sonde_ok(chemin_test, nom_mod, racine,
                                    budget_s=budget_import):
                ignores.append({"module": fn,
                                "raison": f"import > {budget_import}s ou en "
                                          f"échec (sonde) — module ignoré, "
                                          f"pas de scénario inventé"})
                continue
            spec = importlib.util.spec_from_file_location(nom_mod, chemin_test)
            module = importlib.util.module_from_spec(spec)
            sys.modules[nom_mod] = module
            spec.loader.exec_module(module)
        else:
            module = sys.modules[nom_mod]
        suite.addTests(chargeur.loadTestsFromModule(module))
        # Les suites du dépôt ne sont pas toutes en TestCase : si le module
        # définit main(), on l'exécute comme cas de test (ex. test_graphe.py).
        # NOTE : liaison immédiate (m=main) — un lambda sur la variable de
        # boucle serait évalué tard et appellerait le main du DERNIER module.
        main = getattr(module, "main", None)
        if callable(main):
            suite.addTest(unittest.FunctionTestCase(
                lambda m=main: m(), description=f"{fn}::main"))
    return suite, ignores


def executer_suite_brute(rep_tests: str, racine: str,
                         timeout: int = TIMEOUT_DEFAUT) -> tuple:
    """Exécute la suite sous cProfile+tracemalloc. Retourne (pr, duree, pic).

    Factorisé pour mise en cache par la batterie (un run par répertoire).
    Retourne (pr, duree, pic, ignores).
    """
    suite, ignores = _suite_tests(racine, rep_tests)
    tracemalloc.start()
    pr = cProfile.Profile()
    debut = time.time()
    try:
        pr.enable()

        def lancement():
            # La sortie console des suites (print des main() non-TestCase)
            # est capturée : elle ne doit pas polluer le rapport JSON.
            with contextlib.redirect_stdout(io.StringIO()):
                coureur = unittest.TextTestRunner(stream=io.StringIO(),
                                                  verbosity=0)
                coureur.run(suite)

        lancement()
        pr.disable()
    finally:
        duree = time.time() - debut
        _courant, pic = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    if duree > timeout:
        raise TimeoutScenario(
            f"scénario > {timeout}s ({duree:.1f}s) : mesures invalides")
    return pr, duree, pic, ignores


def profiler_tests(racine: str, chemin_cible: str,
                   timeout: int = TIMEOUT_DEFAUT) -> dict:
    """Exécute sous cProfile la suite unittest du tests/ le plus proche.

    Si aucun test trouvé : lève FileNotFoundError (la batterie traduira en
    INDÉCIDABLE — un scénario ne s'invente jamais).
    """
    rep_tests = trouver_repertoire_tests(racine, chemin_cible)
    pr, duree, pic, ignores = executer_suite_brute(rep_tests, racine, timeout)
    stats = _stats_par_fonction(pr, chemin_cible, duree, pic)
    stats.update({"chemin": os.path.abspath(chemin_cible),
                  "entree": None, "scenario": f"tests:{rep_tests}",
                  "modules_ignores": ignores})
    return stats


def profiler_tests_depuis_brut(chemin_cible: str, rep_tests: str,
                               brut: tuple) -> dict:
    """Attribue à `chemin_cible` un run brut déjà exécuté (cache batterie)."""
    pr, duree, pic, ignores = brut
    stats = _stats_par_fonction(pr, chemin_cible, duree, pic)
    stats.update({"chemin": os.path.abspath(chemin_cible),
                  "entree": None, "scenario": f"tests:{rep_tests}",
                  "modules_ignores": ignores})
    return stats


def detecter_non_execute(stats: dict) -> dict:
    """F2/F3 : fonctions définies mais 0 appel DURANT LE SCÉNARIO.

    Verdict ÉVIDENCE explicitement borné au scénario ; croisement avec le
    statique (graphe.detecter_code_mort) possible en aval.
    """
    non_vues = stats.get("definies_non_vues", [])
    scenario = stats.get("scenario", "?")
    if non_vues:
        return {
            "facette": "F2",
            "instrument": "detecter_non_execute",
            "verdict": "ÉVIDENCE",
            "motif": (f"{len(non_vues)} fonction(s) définie(s) jamais appelée(s) "
                      f"DURANT LE SCÉNARIO « {scenario} ». Portée limitée au "
                      f"scénario : ce n'est pas une preuve de code mort global."),
            "candidats": non_vues,
            "scenario": scenario,
        }
    return {
        "facette": "F2",
        "instrument": "detecter_non_execute",
        "verdict": "INDÉCIDABLE",
        "motif": (f"Toutes les fonctions définies ont été appelées au moins "
                  f"une fois durant « {scenario} » — aucune anomalie, mais un "
                  f"scénario ne prouve pas l'absence de code mort."),
        "candidats": [],
        "scenario": scenario,
    }


def detecter_hotspot(stats: dict) -> dict:
    """F3 partiel : une fonction capte >= SEUIL_HOTSPOT du temps cumulé."""
    fonctions = stats.get("fonctions", {})
    scenario = stats.get("scenario", "?")
    chaudes = sorted(
        ((qn, e) for qn, e in fonctions.items() if e["part"] >= SEUIL_HOTSPOT),
        key=lambda t: -t[1]["part"],
    )
    if chaudes:
        return {
            "facette": "F3",
            "instrument": "detecter_hotspot",
            "verdict": "ÉVIDENCE",
            "motif": (f"{len(chaudes)} hotspot(s) >= {SEUIL_HOTSPOT:.0%} du "
                      f"temps cumulé durant « {scenario} » : anomalie de "
                      f"performance candidate (à confronter à l'intention "
                      f"documentée)."),
            "candidats": [{"qualname": qn, "part": round(e["part"], 3),
                           "ncalls": e["ncalls"],
                           "cumtime_s": round(e["cumtime"], 4)}
                          for qn, e in chaudes],
            "scenario": scenario,
        }
    return {
        "facette": "F3",
        "instrument": "detecter_hotspot",
        "verdict": "INDÉCIDABLE",
        "motif": (f"Aucun hotspot >= {SEUIL_HOTSPOT:.0%} durant « {scenario} ». "
                  f"Un bug silencieux à coût identique reste invisible "
                  f"(F3 résiduel)."),
        "candidats": [],
        "scenario": scenario,
    }
