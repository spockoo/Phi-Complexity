#!/usr/bin/env python3
"""phi_lilith.py — adaptateur CLI des instruments Lilith (chantier 5/5).

MISSION RUCHE-LILITH-INSTRUMENTS, chantier 5 : implémente le contrat
lilith_instruments/PHI_CONTRAT.md en appelant les modules des chantiers 1-4.

Usage :
    python3 phi_lilith.py [--format console|json] mesurer  <fichier.py>
    python3 phi_lilith.py [--format console|json] batterie <fichier.py>
    python3 phi_lilith.py [--format console|json] alpha    <fichier.py> [--alphas 1,2,inf]
    python3 phi_lilith.py [--format console|json] f1       <fichier.py>
    python3 phi_lilith.py perf [--tailles 1000,10000] [--repetitions 3]

    Chaque commande accepte aussi --kappas "10,10,1000" à la place du fichier
    (profil direct, sans parsing).

Autonome : fonctionne SEUL, sans phi-complexity installé. L'intégration
future dans phi (`phi lilith ...`) est un simple branchement : ce module
devient le point d'entrée du sous-parseur, ses fonctions `cmd_*` retournant
déjà les dicts JSON du contrat.

Codes de sortie (alignés sur phi) :
    0 — mesure rendue (y compris verdict INDÉCIDABLE/SILENCIEUX : ce sont
        des réponses, pas des échecs) ;
    2 — erreur d'usage (arguments invalides) ;
    3 — INSTRUMENT DÉGRADÉ : fichier non parsable, profil indéfini
        (vide, somme nulle), n < 3 pour les verdicts.

Convention kappa canonique (contrat §1) : VERBATIM phi-complexity v110
(metriques.py), via distributionnel.kappas_depuis_source — y compris le
double parcours (méthodes comptées deux fois quand elles sont aussi
atteintes par le walk). C'est la convention des mesures publiées ; tout
écart (ex. perf.py::tailles_fonctions, sans double-compte) est documenté
dans le contrat §1.2 et n'est PAS utilisé ici.

Stdlib uniquement. Python 3.11 et 3.12.
"""

import argparse
import json
import math
import sys
import time
from pathlib import Path

# Imports paquet, avec repli « script direct » (même pattern que perf.py).
try:
    from . import distributionnel
    from . import f1 as f1_mod
    from . import curseur as curseur_mod
except ImportError:  # python3 phi_lilith.py lancé depuis son dossier
    import distributionnel  # noqa: F401
    import f1 as f1_mod  # noqa: F401
    import curseur as curseur_mod  # noqa: F401

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DEGRADE = 3

SEUIL_N_MIN = 3  # contrat §5 : en dessous, instrument dégradé (C7)


# ---------------------------------------------------------------------------
# Entrée : profil kappa
# ---------------------------------------------------------------------------

def charger_kappas(fichier=None, kappas_csv=None):
    """Retourne (kappas, provenance) ou (None, motif_dégradation)."""
    if kappas_csv is not None:
        try:
            kappas = [int(x) for x in kappas_csv.split(",") if x.strip() != ""]
        except ValueError:
            return None, "--kappas mal formé (entiers séparés par des virgules)"
        if not kappas:
            return None, "profil vide"
        if any(k < 0 for k in kappas):
            return None, "kappa négatif : hors domaine"
        if sum(kappas) <= 0:
            return None, "somme(kappa) = 0 : profil indéfini"
        return kappas, "cli:--kappas"
    if fichier is None:
        return None, "ni fichier ni --kappas fourni"
    kappas = distributionnel.kappas_depuis_fichier(fichier)
    if kappas is None:
        return None, "fichier non parsable : %s" % fichier
    if not kappas:
        return None, "aucune fonction détectée : %s" % fichier
    if sum(kappas) <= 0:
        return None, "somme(kappa) = 0 : profil indéfini"
    return kappas, "fichier:%s" % fichier


# ---------------------------------------------------------------------------
# Bloc cécités (mécanique, §6 du contrat)
# ---------------------------------------------------------------------------

def bloc_cecites(n):
    """Les 9 cécités, statut mécanique par appel. Jamais de verdict ici."""
    return [
        {"id": "C1", "nom": "permutation",
         "statut": "NON_COUVERT",
         "motif": "toute la structure d'appels est invisible au profil"},
        {"id": "C2", "nom": "echelle",
         "statut": "NON_COUVERT",
         "motif": "var_relative invariante d'échelle (obésité uniforme invisible)"},
        {"id": "C3", "nom": "nain",
         "statut": "PARTIEL" if n >= 5 else "NON_COUVERT",
         "motif": ("sensibilité au nain d'ordre n^2 ; n=%d %s" %
                   (n, ">= 5 : couverture partielle" if n >= 5 else
                    "< 5 : puissance faible"))},
        {"id": "C4", "nom": "comportementale",
         "statut": "NON_COUVERT",
         "motif": "substitution d'opérateur à kappa constant invisible — domaine dynamique requis"},
        {"id": "C5", "nom": "code_mort",
         "statut": "NON_COUVERT",
         "motif": "graphe modifié à profil constant invisible — domaine graphe requis"},
        {"id": "C6", "nom": "intention",
         "statut": "NON_COUVERT",
         "motif": "pas de référence design fournie — le nombre ne contient que d(P,Q0)"},
        {"id": "C7", "nom": "n_egal_1",
         "statut": "COUVERT" if n >= SEUIL_N_MIN else "DÉGRADÉ",
         "motif": ("n=%d : monolithe = score 0 « parfait » si n < %d"
                   % (n, SEUIL_N_MIN))},
        {"id": "C8", "nom": "temporelle",
         "statut": "NON_COUVERT",
         "motif": "snapshot unique, pas d'historique — voir spec veille (contrat §8)"},
        {"id": "C9", "nom": "composition",
         "statut": "COUVERT",
         "motif": "triplet (n, Σκ, Σκ²) archivé dans chaque sortie"},
    ]


def enveloppe(commande, kappas, provenance, corps):
    """Enveloppe JSON du contrat §4 : identification + triplet + corps."""
    n = len(kappas)
    total = sum(kappas)
    return {
        "instrument": "lilith",
        "version": VERSION,
        "commande": commande,
        "provenance": provenance,
        "n": n,
        "triplet_archive": [n, total, sum(k * k for k in kappas)],
        "cecites": bloc_cecites(n),
        **corps,
    }


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------

def cmd_mesurer(kappas, provenance):
    """Contrat §2.1 : les six métriques + F1 + curseur (3 coupes)."""
    m = distributionnel.toutes_metriques(kappas)
    # R du THÉORÈME (max/mu) — pas le "R" max/min de toutes_metriques.
    mu = sum(kappas) / len(kappas)
    phi_ratio = max(kappas) / mu if mu > 0 else 1.0
    return enveloppe("mesurer", kappas, provenance, {
        "metriques": {
            "var_relative": m["var_relative"],
            "kl_divergence_bits": m["kl_divergence"],
            "d_infini_nats": m["d_infini"],
            "hellinger": m["hellinger"],
            "variation_totale": m["variation_totale"],
            "shannon_norm": m["shannon_norm"],
            "phi_ratio": phi_ratio,
            "contraste_max_min": m["contraste_max_min"],
        },
        "f1": f1_mod.instrument_f1(kappas),
        "curseur": curseur_mod.curseur(kappas),
    })


def cmd_batterie(kappas, provenance):
    """Contrat §2.2 : batterie tri-domaine (impossibilité, parties a et b)."""
    dist = distributionnel.toutes_metriques(kappas)
    dist["verdict_f1"] = f1_mod.instrument_f1(kappas)

    # Domaine graphe : emplacement déclaré. Le module graphe_domaine.py
    # (chantier 1, SPEC_BATTERIE.md §2) n'est pas encore livré.
    try:
        import graphe_domaine  # noqa: F401
        graphe = {"statut": "DISPONIBLE",
                  "note": "voir graphe_domaine.py (chantier 1)"}
    except ImportError:
        graphe = {"statut": "EN_CONSTRUCTION",
                  "note": ("domaine graphe (F2, code mort) : instrument "
                           "non livré — voir SPEC_BATTERIE.md §2. "
                           "NON_COUVERT par construction en attendant.")}

    # Domaine dynamique : perf.py (chantier 4) couvre l'audit
    # statique×dynamique, pas le bug comportemental générique (F3).
    try:
        try:
            from . import perf as perf_mod
        except ImportError:
            import perf as perf_mod  # noqa: F401
        dynamique = {"statut": "PARTIEL",
                     "note": ("perf.py : goulots statique×dynamique (cProfile). "
                              "F3 générique (bug comportemental à kappa "
                              "constant) reste NON_COUVERT : aucun scalaire "
                              "ne le détecte (impossibilité, partie b).")}
    except ImportError:
        dynamique = {"statut": "EN_CONSTRUCTION",
                     "note": "perf.py non importable"}

    return enveloppe("batterie", kappas, provenance, {
        "domaines": {
            "distributionnel": {"statut": "DISPONIBLE", **dist},
            "graphe": graphe,
            "dynamique": dynamique,
        },
    })


def cmd_alpha(kappas, provenance, alphas):
    """Contrat §2.3 : curseur de Rényi sur les coupes demandées."""
    return enveloppe("alpha", kappas, provenance, {
        "alphas_demandes": alphas,
        "curseur": curseur_mod.curseur(kappas, alphas=alphas),
        "lecture": ("alpha=1 (KL) : sensible aux petites différences ; "
                    "alpha=2 (chi-2/var_relative) : sensible aux gros "
                    "écarts ; alpha=inf (max) : ne voit que le dominant."),
    })


def cmd_f1(kappas, provenance):
    """Contrat §2.4 : instrument du théorème F1 seul."""
    return enveloppe("f1", kappas, provenance, {
        "f1": f1_mod.instrument_f1(kappas),
    })


def _profil_synthetique(n, graine=20261007):
    a, c, m = 1664525, 1013904223, 2 ** 32
    x = graine
    out = []
    for _ in range(n):
        x = (a * x + c) % m
        out.append(1 + x % 20000)
    out[0] = 200000  # un géant : exerce aussi le régime F1
    return out


def cmd_perf(tailles, repetitions):
    """Contrat §2.5 : banc de performance (budgets §7). Budgets en dur."""
    budgets = {1000: 0.5, 10000: 5.0, 100000: 60.0}
    resultats = []
    for n in tailles:
        kappas = _profil_synthetique(n)
        durees = []
        for _ in range(repetitions):
            t0 = time.perf_counter()
            distributionnel.toutes_metriques(kappas)
            f1_mod.instrument_f1(kappas)
            curseur_mod.curseur(kappas)
            bloc_cecites(n)
            durees.append(time.perf_counter() - t0)
        durees.sort()
        med = durees[len(durees) // 2]
        budget = budgets.get(n)
        resultats.append({
            "n": n,
            "secondes_mediane": med,
            "repetitions": repetitions,
            "budget_s": budget,
            "dans_budget": (budget is None) or (med <= budget),
        })
    return {
        "instrument": "lilith",
        "version": VERSION,
        "commande": "perf",
        "resultats": resultats,
        "tous_dans_budget": all(r["dans_budget"] for r in resultats),
    }


# ---------------------------------------------------------------------------
# Sortie console
# ---------------------------------------------------------------------------

def rendre_console(doc):
    cmd = doc.get("commande")
    lignes = ["[lilith %s] v%s — %s" % (cmd, doc.get("version"),
                                        doc.get("provenance", "?"))]
    if cmd == "perf":
        for r in doc["resultats"]:
            lignes.append("  n=%-7d médiane=%.3fs budget=%s %s" % (
                r["n"], r["secondes_mediane"],
                ("%.1fs" % r["budget_s"]) if r["budget_s"] else "—",
                "OK" if r["dans_budget"] else "DÉPASSEMENT"))
        lignes.append("tous dans budget : %s" % doc["tous_dans_budget"])
        return "\n".join(lignes)
    met = doc.get("metriques") or (doc.get("domaines", {})
                                   .get("distributionnel", {}))
    if met:
        lignes.append("  n=%d  var_relative=%.4f  phi_ratio(R)=%.3f  "
                      "Hellinger=%.4f  TV=%.4f  shannon=%.4f" % (
                          doc["n"], met.get("var_relative", float("nan")),
                          met.get("phi_ratio", float("nan")),
                          met.get("hellinger", float("nan")),
                          met.get("variation_totale", float("nan")),
                          met.get("shannon_norm", float("nan"))))
    f1r = doc.get("f1") or (doc.get("domaines", {})
                            .get("distributionnel", {}).get("verdict_f1"))
    if f1r:
        lignes.append("  F1 [%s] %s" % (f1r.get("verdict"),
                                        f1r.get("motif", "")))
    cur = doc.get("curseur")
    if cur:
        lignes.append("  curseur D_alpha=%s unification=%s" % (
            {k: round(v, 4) for k, v in cur["D"].items()},
            cur["unification_ok"]))
    dom = doc.get("domaines")
    if dom:
        for nom, d in dom.items():
            if nom != "distributionnel":
                lignes.append("  domaine %s : %s — %s" % (
                    nom, d.get("statut"), d.get("note", "")))
    lignes.append("  cécités non couvertes : %s" % ", ".join(
        c["id"] for c in doc.get("cecites", []) if c["statut"] == "NON_COUVERT"))
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_alphas(texte):
    alphas = []
    for morceau in texte.split(","):
        morceau = morceau.strip().lower()
        if morceau in ("inf", "infinity", "+inf"):
            alphas.append(float("inf"))
        else:
            try:
                a = float(morceau)
            except ValueError:
                raise ValueError("alpha invalide : %r" % morceau)
            if a <= 0:
                raise ValueError("alpha hors domaine ]0, +inf] : %r" % morceau)
            alphas.append(a)
    if not alphas:
        raise ValueError("--alphas vide")
    return alphas


def construire_parser():
    p = argparse.ArgumentParser(
        prog="phi_lilith.py",
        description="Adaptateur CLI des instruments Lilith (contrat PHI_CONTRAT.md).")
    p.add_argument("--format", choices=["console", "json"], default="console",
                   help="format de sortie")
    sp = p.add_subparsers(dest="commande", required=True)

    for nom, aide in [
            ("mesurer", "six métriques + F1 + curseur (3 coupes)"),
            ("batterie", "batterie tri-domaine (distributionnel+graphe+dynamique)"),
            ("alpha", "curseur de Rényi sur coupes choisies"),
            ("f1", "instrument du théorème F1 seul")]:
        s = sp.add_parser(nom, help=aide)
        s.add_argument("fichier", nargs="?",
                       help="fichier .py à mesurer (ou --kappas)")
        s.add_argument("--kappas", default=None,
                       help='profil direct, ex. --kappas "10,10,1000"')
    sp_alpha = sp._name_parser_map["alpha"]
    sp_alpha.add_argument("--alphas", default="1,2,inf",
                          help="coupes du curseur, ex. 0.5,1,2,inf (défaut: 1,2,inf)")

    s = sp.add_parser("perf", help="banc de performance (budgets contrat §7)")
    s.add_argument("--tailles", default="1000,10000",
                   help="tailles de profil, ex. 1000,10000,100000")
    s.add_argument("--repetitions", type=int, default=3)
    return p


def main(argv=None):
    parser = construire_parser()
    args = parser.parse_args(argv)
    fmt = args.format

    if args.commande == "perf":
        try:
            tailles = [int(x) for x in args.tailles.split(",") if x.strip()]
        except ValueError:
            parser.error("--tailles mal formé")
        doc = cmd_perf(tailles, args.repetitions)
        code = EXIT_OK if doc["tous_dans_budget"] else EXIT_DEGRADE
    else:
        kappas, provenance = charger_kappas(args.fichier, args.kappas)
        if kappas is None:
            # provenance contient ici le motif de dégradation.
            print("instrument dégradé : %s" % provenance, file=sys.stderr)
            return EXIT_DEGRADE
        if len(kappas) < SEUIL_N_MIN:
            print("instrument dégradé : n=%d < %d (cécité C7)"
                  % (len(kappas), SEUIL_N_MIN), file=sys.stderr)
            return EXIT_DEGRADE
        if args.commande == "mesurer":
            doc = cmd_mesurer(kappas, provenance)
        elif args.commande == "batterie":
            doc = cmd_batterie(kappas, provenance)
        elif args.commande == "alpha":
            try:
                alphas = _parse_alphas(args.alphas)
            except ValueError as e:
                parser.error(str(e))
            doc = cmd_alpha(kappas, provenance, alphas)
        elif args.commande == "f1":
            doc = cmd_f1(kappas, provenance)
        code = EXIT_OK

    if fmt == "json":
        print(json.dumps(doc, ensure_ascii=False, indent=1))
    else:
        print(rendre_console(doc))
    return code


if __name__ == "__main__":
    sys.exit(main())
