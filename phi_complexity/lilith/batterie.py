#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrateur — batterie d'instruments tri-domaine (chantier 1/5).

Prescrite par le théorème d'impossibilité : aucun scalaire ne couvre F1+F2+F3 ;
la batterie lance les trois domaines et agrège par facette avec des verdicts
typés. Chaque instrument hors de son domaine est enregistré SILENCIEUX
(explicite, pas omis).

Usage : python -m lilith_instruments.batterie <fichier.py|répertoire>
        [--format console|json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import distributionnel as _dist
from . import graphe_domaine as _graphe
from . import dynamique as _dyn

__all__ = ["trouver_racine", "lancer_fichier", "lancer", "rendre_markdown",
           "main"]

_VERDICTS = ("DÉMONTRÉ", "CONDITIONNEL", "ÉVIDENCE", "RÉFUTÉ",
             "INDÉCIDABLE", "SILENCIEUX")

# Ordre d'agrégation : le verdict le plus fort « parle » pour la facette.
_FORCE = {"DÉMONTRÉ": 5, "ÉVIDENCE": 4, "CONDITIONNEL": 3,
          "RÉFUTÉ": 2, "INDÉCIDABLE": 1, "SILENCIEUX": 0}


def _silencieux(facette: str, instrument: str, partie: str) -> dict:
    return {
        "facette": facette,
        "instrument": instrument,
        "verdict": "SILENCIEUX",
        "motif": (f"Hors domaine par construction : le théorème d'impossibilité "
                  f"(partie {partie}) prouve qu'aucun instrument de ce domaine "
                  f"ne peut parler de {facette}. Enregistré, pas omis."),
    }


def trouver_racine(cible: str) -> str:
    """Racine du dépôt : premier parent contenant .git ; repli = dossier cible."""
    curseur = os.path.abspath(cible)
    if not os.path.isdir(curseur):
        curseur = os.path.dirname(curseur)
    while True:
        if os.path.isdir(os.path.join(curseur, ".git")):
            return curseur
        parent = os.path.dirname(curseur)
        if parent == curseur:
            return os.path.abspath(cible if os.path.isdir(cible)
                                   else os.path.dirname(cible))
        curseur = parent


def lancer_fichier(chemin: str, racine: str, graphe=None,
                   cache_dyn: dict | None = None) -> dict:
    """Batterie complète sur un fichier .py. Retourne le rapport (dict)."""
    chemin = os.path.abspath(chemin)
    if cache_dyn is None:
        cache_dyn = {}

    # ---- Domaine distributionnel --------------------------------------
    kappa = _dist.kappas_depuis_fichier(chemin)
    if kappa is None or len(kappa) < _dist.SEUIL_N_MIN:
        dist_f1 = {"facette": "F1", "instrument": "distributionnel",
                   "verdict": "INDÉCIDABLE",
                   "motif": "Fichier non parsable ou n < 3 (C7).",
                   "mesures": {"n": len(kappa) if kappa else 0}}
        mesures = dist_f1["mesures"]
    else:
        mesures = _dist.toutes_metriques(kappa)
        dist_f1 = _dist.verdict_f1(kappa)
        dist_f1["instrument"] = "distributionnel"
    dist_f2 = _silencieux("F2", "distributionnel", "a")
    dist_f3 = _silencieux("F3", "distributionnel", "b")

    # ---- Domaine graphe -------------------------------------------------
    if graphe is None:
        graphe = _graphe.construire_graphe(racine)
    g_f2 = _graphe.detecter_code_mort(graphe, racine, chemin)
    g_api = _graphe.detecter_api_morte(graphe, racine, chemin)
    g_struct = _graphe.centralite_couplage(graphe, racine, chemin)
    g_f1 = _silencieux("F1", "graphe", "—")
    g_f1["motif"] = ("Hors domaine : le graphe voit la structure d'appels, "
                     "pas la distribution des tailles.")
    g_f3 = _silencieux("F3", "graphe", "b")

    # ---- Domaine dynamique ----------------------------------------------
    # Un run brut par répertoire tests/ (cache) ; attribution par fichier.
    try:
        rep_tests = _dyn.trouver_repertoire_tests(racine, chemin)
        if rep_tests not in cache_dyn:
            cache_dyn[rep_tests] = _dyn.executer_suite_brute(
                rep_tests, racine)
        stats = _dyn.profiler_tests_depuis_brut(
            chemin, rep_tests, cache_dyn[rep_tests])
        d_f2 = _dyn.detecter_non_execute(stats)
        d_f3 = _dyn.detecter_hotspot(stats)
        dyn_note = None
    except FileNotFoundError:
        stats = None
        scenario = "aucun"
        d_f2 = {"facette": "F2", "instrument": "dynamique",
                "verdict": "INDÉCIDABLE",
                "motif": "Aucun répertoire tests/ trouvé : aucun scénario "
                         "disponible. Un scénario ne s'invente jamais.",
                "scenario": scenario}
        d_f3 = {"facette": "F3", "instrument": "dynamique",
                "verdict": "INDÉCIDABLE",
                "motif": "Aucun scénario d'exécution disponible (voir F2).",
                "scenario": scenario}
        dyn_note = "scénario indisponible"
    d_f1 = _silencieux("F1", "dynamique", "—")
    d_f1["motif"] = ("Hors domaine : le dynamique mesure l'exécution, "
                     "pas la distribution des tailles.")

    # ---- Agrégation par facette ------------------------------------------
    par_facette = {
        "F1": [dist_f1, g_f1, d_f1],
        "F2": [dist_f2, g_f2, d_f2],
        "F3": [dist_f3, g_f3, d_f3],
        "F3-structurelle": [g_api],
    }
    synthese = {}
    for facette, verdicts in par_facette.items():
        parlants = [v for v in verdicts if v["verdict"] != "SILENCIEUX"]
        if not parlants:
            synthese[facette] = {"verdict": "SILENCIEUX",
                                 "motif": "Aucun instrument ne parle ici."}
            continue
        # RÉFUTÉ ne gagne jamais contre une ÉVIDENCE/DÉMONTRÉ positive.
        positifs = [v for v in parlants
                    if v["verdict"] in ("DÉMONTRÉ", "ÉVIDENCE", "CONDITIONNEL")]
        gagnant = (max(positifs, key=lambda v: _FORCE[v["verdict"]])
                   if positifs
                   else max(parlants, key=lambda v: _FORCE[v["verdict"]]))
        synthese[facette] = {
            "verdict": gagnant["verdict"],
            "par": gagnant["instrument"],
            "motif": gagnant["motif"],
        }

    return {
        "fichier": chemin,
        "distributionnel": {"mesures": mesures, "F1": dist_f1,
                            "F2": dist_f2, "F3": dist_f3},
        "graphe": {"F1": g_f1, "F2": g_f2, "F3-structurelle": g_api,
                   "F3": g_f3, "structure": g_struct},
        "dynamique": {"F1": d_f1, "F2": d_f2, "F3": d_f3,
                      "scenario": (stats or {}).get("scenario")
                      if stats else None,
                      "note": dyn_note},
        "synthese": synthese,
    }


def _fichiers_py(repertoire: str) -> list[str]:
    trouves = []
    for dirpath, dirnames, filenames in os.walk(repertoire):
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                trouves.append(os.path.join(dirpath, fn))
    return trouves


def lancer(cible: str) -> dict:
    """Batterie sur un fichier ou un répertoire (récursif)."""
    cible = os.path.abspath(cible)
    racine = trouver_racine(cible)
    graphe = _graphe.construire_graphe(racine)
    cache_dyn: dict = {}
    if os.path.isdir(cible):
        fichiers = _fichiers_py(cible)
    else:
        fichiers = [cible]
    rapports = [lancer_fichier(f, racine, graphe=graphe, cache_dyn=cache_dyn)
                for f in fichiers]
    return {"cible": cible, "racine": racine,
            "fichiers": len(rapports), "rapports": rapports}


def rendre_markdown(resultat: dict) -> str:
    """Rapport unifié en markdown : tableaux de verdicts par facette."""
    lignes = ["# Batterie Lilith — rapport tri-domaine",
              "",
              f"Cible : `{resultat['cible']}` — {resultat['fichiers']} fichier(s)",
              ""]
    for rap in resultat["rapports"]:
        lignes.append(f"## `{os.path.relpath(rap['fichier'], resultat['racine'])}`")
        lignes.append("")
        lignes.append("| Facette | Verdict | Par | Motif |")
        lignes.append("|---|---|---|---|")
        for facette in ("F1", "F2", "F3", "F3-structurelle"):
            s = rap["synthese"].get(facette, {})
            motif = s.get("motif", "").replace("\n", " ")
            if len(motif) > 220:
                motif = motif[:217] + "…"
            lignes.append(f"| {facette} | **{s.get('verdict', '?')}** "
                          f"| {s.get('par', '—')} | {motif} |")
        lignes.append("")
        m = rap["distributionnel"]["mesures"]
        if m.get("n", 0) >= 3:
            lignes.append(
                f"Mesures : n={m['n']}, var_relative={m['var_relative']:.3f}, "
                f"phi_ratio={m['phi_ratio']:.2f}, "
                f"contraste_max_min={m['contraste_max_min']:.2f}, "
                f"shannon_norm={m['shannon_norm']:.3f}, "
                f"hellinger={m['hellinger']:.3f}, "
                f"TV={m['variation_totale']:.3f}.")
            lignes.append("")
        g2 = rap["graphe"]["F2"]
        if g2["candidats"]:
            lignes.append("Code mort candidat (F2, ÉVIDENCE) : "
                          + ", ".join(f"`{c['qualname']}` (l.{c['ligne']})"
                                      for c in g2["candidats"]))
            lignes.append("")
        api = rap["graphe"]["F3-structurelle"]
        if api["candidats"]:
            lignes.append("API morte (F3-structurelle, ÉVIDENCE) : "
                          + ", ".join(f"`{c['nom']}` (l.{c['ligne']})"
                                      for c in api["candidats"]))
            lignes.append("")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batterie d'instruments Lilith tri-domaine "
                    "(distributionnel + graphe + dynamique).")
    parser.add_argument("cible", help="fichier .py ou répertoire")
    parser.add_argument("--format", choices=("console", "json"),
                        default="console")
    args = parser.parse_args(argv)
    resultat = lancer(args.cible)
    if args.format == "json":
        print(json.dumps(resultat, ensure_ascii=False, indent=2,
                         default=str))
    else:
        print(rendre_markdown(resultat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
