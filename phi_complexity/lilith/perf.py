#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domaine DYNAMIQUE — audit de performance statique x dynamique (chantier 4/5).

Combine le profil statique (var_relative, R — lilith_instruments.distributionnel)
et le profil dynamique (cProfile, temps exclusif) pour désigner les fonctions-goulots.

Convention kappa : nombre de noeuds AST du sous-arbre (SPEC_BATTERIE.md §0,
coordonne avec distributionnel.py — meme convention que mesures_conformite.py).
Deviation documentee : enumeration sans double-compte des methodes (necessaire
pour l'attribution par fonction) ; la formule de var_relative est identique.

Verdicts types (SPEC_BATTERIE.md §0) : DÉMONTRÉ / CONDITIONNEL / ÉVIDENCE /
RÉFUTÉ / INDÉCIDABLE / SILENCIEUX. Jamais de booleen nu.

Stdlib uniquement.
"""

from __future__ import annotations

import ast
import cProfile
import json
import math
import os
import statistics
import sys
import time

try:
    from .distributionnel import toutes_metriques, SEUIL_N_MIN
except ImportError:  # usage en script direct
    from distributionnel import toutes_metriques, SEUIL_N_MIN

__all__ = [
    "tailles_fonctions",
    "profil_statique",
    "profiler_workload",
    "mediane_par_fonction",
    "temps_du_module",
    "concentration_pareto",
    "detecter_goulots",
    "topk_baseline",
    "comparer_baseline",
    "pearson",
    "analyser_module",
    "SEUIL_P80",
]

SEUIL_P80 = 0.80  # "grosse"/"chaude" = au-dessus du 80e percentile


# ---------------------------------------------------------------------------
# Profil statique
# ---------------------------------------------------------------------------

def _noeuds(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def tailles_fonctions(chemin: str) -> dict[str, int] | None:
    """Nom qualifie -> kappa (noeuds AST). None si non parsable.

    Compte les defs de niveau module et les methodes de classes, chacune une
    fois. Les fonctions imbriquees/lambdas sont contenues dans leur parent.
    """
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
            arbre = ast.parse(fh.read())
    except (SyntaxError, ValueError, OSError):
        return None
    tailles: dict[str, int] = {}
    for node in arbre.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tailles[node.name] = _noeuds(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    tailles[f"{node.name}.{sub.name}"] = _noeuds(sub)
    return tailles


def profil_statique(chemin: str) -> dict | None:
    """Profil statique complet d'un module. None si non parsable."""
    tailles = tailles_fonctions(chemin)
    if not tailles:
        return None
    kappa = list(tailles.values())
    n = len(kappa)
    m = toutes_metriques(kappa)
    # R calcule localement (ne depend pas des cles de toutes_metriques,
    # qui evoluent avec les chantiers 1-2) ; var_relative via la batterie.
    mx, mn = max(kappa), min(kappa)
    r_ratio = (mx / mn) if mn > 0 else float("inf")
    tri = sorted(kappa)
    p80 = tri[min(n - 1, int(math.ceil(SEUIL_P80 * n)) - 1)]
    return {
        "module": os.path.basename(chemin),
        "chemin": os.path.abspath(chemin),
        "n": n,
        "somme_kappa": sum(kappa),
        "somme_kappa2": sum(k * k for k in kappa),
        "var_relative": m["var_relative"],
        "n_eff": n / (1.0 + m["var_relative"]),
        "R": r_ratio,
        "phi_ratio": m.get("phi_ratio", mx / (sum(kappa) / n) if n else 1.0),
        "p80_taille": p80,
        "tailles": tailles,
        "verdict_n": ("plein" if n >= 5 else
                      "CONDITIONNEL (n<5)" if n >= SEUIL_N_MIN else
                      "INDÉCIDABLE (n<3, C7)"),
    }


# ---------------------------------------------------------------------------
# Profil dynamique (cProfile, temps exclusif)
# ---------------------------------------------------------------------------

def profiler_workload(workload, n_runs: int = 5, n_warmup: int = 1) -> dict:
    """Profile `workload()` (callable sans argument) avec cProfile.

    Retourne par run : {(fichier, lineno, nom): {"tottime": s, "ncalls": n}}
    (tottime = temps EXCLUSIF) + temps mur + charge machine.
    Le warm-up n'est pas mesure.
    """
    for _ in range(n_warmup):
        workload()
    runs = []
    for i in range(n_runs):
        pr = cProfile.Profile()
        t0 = time.perf_counter()
        pr.runcall(workload)
        mur = time.perf_counter() - t0
        stats = {}
        # getstats() -> liste de _lsprof.profiler_entry : .code (co_filename,
        # co_firstlineno, co_name), .callcount, .totaltime (temps EXCLUSIF).
        for entry in pr.getstats():
            code = entry.code
            try:
                fichier, nom = code.co_filename, code.co_name
            except AttributeError:
                continue  # code C sans co_filename : hors perimetre
            key = f"{fichier}\x00{nom}"
            if key in stats:
                stats[key]["tottime"] += entry.totaltime
                stats[key]["ncalls"] += entry.callcount
            else:
                stats[key] = {"fichier": fichier, "nom": nom,
                              "tottime": entry.totaltime,
                              "ncalls": entry.callcount}
        try:
            load = list(os.getloadavg())
        except OSError:
            load = None
        runs.append({"run": i, "mur_s": mur, "loadavg": load,
                     "fonctions": stats,
                     "horodatage": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return {"n_runs": n_runs, "n_warmup": n_warmup,
            "python": sys.version.split()[0], "runs": runs}


def mediane_par_fonction(profil: dict) -> dict:
    """Mediane (runs) du tottime et des ncalls par cle de fonction."""
    par_cle: dict[str, dict[str, list]] = {}
    for run in profil["runs"]:
        for key, f in run["fonctions"].items():
            e = par_cle.setdefault(key, {"tottime": [], "ncalls": [],
                                         "fichier": f["fichier"],
                                         "nom": f["nom"]})
            e["tottime"].append(f["tottime"])
            e["ncalls"].append(f["ncalls"])
    return {k: {"fichier": v["fichier"], "nom": v["nom"],
                "tottime_med": statistics.median(v["tottime"]),
                "ncalls_med": statistics.median(v["ncalls"])}
            for k, v in par_cle.items()}


def temps_du_module(chemin_module: str, med: dict,
                    tailles: dict[str, int]) -> dict:
    """Agrege le temps exclusif par fonction QUALIFIEE du module.

    Mapping cProfile (nom simple) -> nom qualifie via les tailles statiques.
    Les entrees sans correspondant (imbriquees, <listcomp>, <module>...)
    vont dans un seau "hors_attribution" documente separement.
    """
    cible = os.path.abspath(chemin_module)
    simple_vers_qualifie: dict[str, str] = {}
    for qualif in tailles:
        simple = qualif.rsplit(".", 1)[-1]
        # methodes : le nom simple suffit ; en cas de collision, premier gagne
        # (documente dans "collisions").
        simple_vers_qualifie.setdefault(simple, qualif)
    par_fonction: dict[str, dict] = {}
    hors: dict[str, dict] = {}
    collisions: list[str] = []
    for key, f in med.items():
        if os.path.abspath(f["fichier"]) != cible:
            continue
        qualif = simple_vers_qualifie.get(f["nom"])
        dest = par_fonction if qualif else hors
        nom_out = qualif if qualif else f"hors_attribution:{f['nom']}"
        if qualif and f["nom"] in simple_vers_qualifie and \
                sum(1 for q in tailles if q.rsplit(".", 1)[-1] == f["nom"]) > 1:
            collisions.append(f["nom"])
        e = dest.setdefault(nom_out, {"tottime_med": 0.0, "ncalls_med": 0.0,
                                      "kappa": tailles.get(qualif)})
        e["tottime_med"] += f["tottime_med"]
        e["ncalls_med"] += f["ncalls_med"]
    return {"par_fonction": par_fonction, "hors_attribution": hors,
            "collisions_nom_simple": sorted(set(collisions))}


# ---------------------------------------------------------------------------
# Analyse : concentration, goulots, baseline
# ---------------------------------------------------------------------------

def _p80(valeurs: list[float]) -> float:
    tri = sorted(valeurs)
    n = len(tri)
    return tri[min(n - 1, int(math.ceil(SEUIL_P80 * n)) - 1)] if n else 0.0


def concentration_pareto(temps: dict[str, float]) -> dict:
    """Part du temps detenue par les 20 % d'entrees les plus chaudes (H-P2)."""
    vals = sorted(temps.values(), reverse=True)
    n = len(vals)
    if n == 0:
        return {"n_entrees": 0, "part_top20": 0.0, "part_top1": 0.0,
                "seuil_paretien_20_60": False}
    k = max(1, int(math.ceil(0.2 * n)))
    total = sum(vals)
    part = sum(vals[:k]) / total if total > 0 else 0.0
    return {"n_entrees": n, "k_top20": k,
            "part_top20": part,
            "part_top1": (vals[0] / total) if total > 0 else 0.0,
            "seuil_paretien_20_60": part >= 0.60}


def detecter_goulots(stat: dict, dyn: dict) -> list[dict]:
    """Designe les goulots : grosses (F1 statique) ET chaudes (temps reel).

    Cas distingues (H-P3) :
      GOULOT            : grosse ET chaude -> decouper (F1) + optimiser l'algo
      GROSSE_FROIDE     : grosse mais froide -> pas un goulot, juste du code
      PETITE_CHAUDE     : petite mais chaude -> optimiser l'algo / cache si pure
    Verdicts types ; chaque recommandation porte sa justification mesuree.
    """
    tailles = stat["tailles"]
    temps = {nom: f["tottime_med"] for nom, f in dyn["par_fonction"].items()}
    total = sum(temps.values())
    seuil_grosse = stat["p80_taille"]
    seuil_chaude = _p80(list(temps.values())) if temps else 0.0
    # SPEC_BATTERIE : n < 3 -> seuils P80 degeneres ; verdicts conditionnels.
    degenere = stat.get("n", 0) < 3
    portee = ("CONDITIONNELLE (n<3 : seuils P80 degeneres, distinction "
              "mecanique)" if degenere else "pleine")
    verdicts = []
    for nom, kappa in tailles.items():
        t = temps.get(nom, 0.0)
        grosse = kappa >= seuil_grosse
        chaude = t >= seuil_chaude and t > 0
        part = (t / total) if total > 0 else 0.0
        ncalls = dyn["par_fonction"].get(nom, {}).get("ncalls_med", 0)
        base = {"fonction": nom, "kappa": kappa, "tottime_med_s": t,
                "part_temps": part, "ncalls_med": ncalls,
                "seuil_grosse_kappa": seuil_grosse,
                "seuil_chaude_s": seuil_chaude,
                "portee": portee}
        if grosse and chaude:
            base.update({
                "cas": "GOULOT",
                "verdict": "ÉVIDENCE",
                "recommandation": "découper (F1 : fonction géante) ET optimiser "
                                  "l'algorithme (chaude en réel)",
                "justification": (f"kappa={kappa} >= P80={seuil_grosse} (grosse) ; "
                                  f"temps exclusif médian={t:.4f}s "
                                  f"({part*100:.1f}% du module) >= P80={seuil_chaude:.4f}s "
                                  f"(chaude) ; {ncalls:g} appels médians."),
            })
        elif grosse and not chaude:
            base.update({
                "cas": "GROSSE_FROIDE",
                "verdict": "DÉMONTRÉ",
                "recommandation": "pas un goulot : ne pas optimiser ; découpage "
                                  "optionnel pour lisibilité (F1 pur, pas perf)",
                "justification": (f"kappa={kappa} >= P80={seuil_grosse} (grosse) "
                                  f"MAIS temps={t:.4f}s ({part*100:.1f}%) < "
                                  f"P80={seuil_chaude:.4f}s (froide) — le cas "
                                  f"trivial exclu : grosse mais froide."),
            })
        elif chaude and not grosse:
            base.update({
                "cas": "PETITE_CHAUDE",
                "verdict": "ÉVIDENCE",
                "recommandation": ("mettre en cache (appelée souvent, "
                                   "vérifier la pureté) OU optimiser l'algorithme "
                                   "(bien découpée mais coûteuse)"),
                "justification": (f"kappa={kappa} < P80={seuil_grosse} (petite) "
                                  f"MAIS temps={t:.4f}s ({part*100:.1f}%) >= "
                                  f"P80={seuil_chaude:.4f}s (chaude) ; "
                                  f"{ncalls:g} appels médians."),
            })
        else:
            base.update({
                "cas": "NEUTRE",
                "verdict": "SILENCIEUX",
                "recommandation": "rien à signaler",
                "justification": "ni grosse ni chaude.",
            })
        verdicts.append(base)
    verdicts.sort(key=lambda v: v["tottime_med_s"], reverse=True)
    return verdicts


def topk_baseline(dyn: dict, k: int = 5) -> list[dict]:
    """Baseline 'battre ou ne pas exister' : tri cProfile seul, sans metriques Lilith."""
    items = sorted(dyn["par_fonction"].items(),
                   key=lambda kv: kv[1]["tottime_med"], reverse=True)
    total = sum(f["tottime_med"] for _, f in items)
    return [{"fonction": nom, "tottime_med_s": f["tottime_med"],
             "part_temps": (f["tottime_med"] / total) if total > 0 else 0.0,
             "ncalls_med": f["ncalls_med"]}
            for nom, f in items[:k]]


def comparer_baseline(verdicts: list[dict], baseline: list[dict]) -> dict:
    """Valeur ajoutee de la combinaison statique+dynamique vs cProfile seul (H-P3)."""
    noms_base = {b["fonction"] for b in baseline}
    diff = [v for v in verdicts
            if v["cas"] in ("GROSSE_FROIDE", "PETITE_CHAUDE")]
    # cas (b)/(c) que le tri cProfile seul ne distingue pas :
    # - GROSSE_FROIDE : jamais dans le top-k chaud -> le profiler seul la rate
    #   comme "non-probleme" sans dire qu'elle est grosse (F1)
    # - PETITE_CHAUDE : dans le top-k mais le profiler seul ne dit pas
    #   qu'elle est petite (recommandation differente : cache/algo, pas decoupage)
    valeur = []
    for v in diff:
        if v["cas"] == "GROSSE_FROIDE":
            valeur.append({"fonction": v["fonction"], "cas": v["cas"],
                           "apport": "cProfile seul : invisible (froide) ; "
                                     "Lilith ajoute : grosse (F1) -> decoupage "
                                     "lisibilite, pas optimisation."})
        else:
            valeur.append({"fonction": v["fonction"], "cas": v["cas"],
                           "apport": "cProfile seul : 'chaude' -> optimiser ; "
                                     "Lilith ajoute : petite (bien decoupée) -> "
                                     "recommandation cache/algo, PAS decoupage."})
    goultous_rates = [v["fonction"] for v in verdicts if v["cas"] == "GOULOT"
                      and v["fonction"] not in noms_base]
    portee = (verdicts[0].get("portee", "pleine") if verdicts else "pleine")
    return {
        "baseline_topk": [b["fonction"] for b in baseline],
        "cas_distinctifs": valeur,
        "goulots_hors_topk": goultous_rates,
        "verdict_h_p3": ("CORROBORÉE" if valeur else "RÉFUTÉE"),
        "portee": portee,
        "motif": ("au moins un cas (b)/(c) distingue" if valeur else
                  "aucun cas (b)/(c) : les recommandations sont identiques au "
                  "tri cProfile seul — le profiler seul suffit sur ce module "
                  "(documenté honnêtement, règle 'battre ou ne pas exister')"),
    }


def pearson(x: list[float], y: list[float]) -> float | None:
    """r de Pearson (stdlib). None si incalculable."""
    if len(x) != len(y) or len(x) < 3:
        return None
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return None


# ---------------------------------------------------------------------------
# Pipeline complet par module
# ---------------------------------------------------------------------------

def analyser_module(chemin_module: str, workload, n_runs: int = 5,
                    n_warmup: int = 1, k_baseline: int = 5) -> dict:
    """Statique + dynamique + goulots + baseline. `workload` : callable()."""
    stat = profil_statique(chemin_module)
    if stat is None:
        return {"module": chemin_module, "erreur": "non parsable (ast)"}
    tailles = stat.pop("tailles")
    prof = profiler_workload(workload, n_runs=n_runs, n_warmup=n_warmup)
    med = mediane_par_fonction(prof)
    dyn = temps_du_module(chemin_module, med, tailles)
    temps = {n: f["tottime_med"] for n, f in dyn["par_fonction"].items()}
    conc = concentration_pareto(temps)
    verdicts = detecter_goulots({**stat, "tailles": tailles}, dyn)
    base = topk_baseline(dyn, k=k_baseline)
    comp = comparer_baseline(verdicts, base)
    murs = [r["mur_s"] for r in prof["runs"]]
    return {
        "module": stat["module"],
        "chemin": stat["chemin"],
        "n_runs": n_runs, "n_warmup": n_warmup,
        "python": prof["python"],
        "murs_med_s": statistics.median(murs),
        "murs_runs_s": murs,
        "statique": {k: v for k, v in stat.items()},
        "tailles": tailles,
        "dynamique_par_fonction": dyn["par_fonction"],
        "hors_attribution": dyn["hors_attribution"],
        "collisions_nom_simple": dyn["collisions_nom_simple"],
        "concentration": conc,
        "goulots": verdicts,
        "baseline_topk": base,
        "comparaison_baseline": comp,
        "brut_cprofile": prof,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_statique(args) -> int:
    stat = profil_statique(args.module)
    if stat is None:
        print(json.dumps({"erreur": "module non parsable"}))
        return 1
    tailles = stat.pop("tailles")
    print(json.dumps(stat, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="perf",
                                 description="Audit perf : statique (F1) x dynamique (cProfile).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("statique", help="profil statique d'un module")
    s.add_argument("--module", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "statique":
        return _cmd_statique(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
