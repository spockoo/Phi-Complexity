#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Domaine DISTRIBUTIONNEL — batterie Lilith (chantier 1/5).

Les six métriques du profil, trois coupes de la nappe d'unification
D_alpha(P||Q0) = log(n) - H_alpha(P) (cf. SPEC_BATTERIE.md §1 et
VARIANCE_LILITH_IDENTITES.md).

Convention de mesure : kappa_i = nombre de noeuds AST du sous-arbre de la
fonction i (VERBATIM lilith_conformite/mesures_conformite.py).

Stdlib uniquement. Verdicts typés, jamais de booléen nu.
"""

from __future__ import annotations

import ast
import math

__all__ = [
    "complexite_noeud",
    "kappas_depuis_fichier",
    "kappas_depuis_source",
    "profil",
    "var_relative",
    "kl_divergence",
    "d_infini",
    "hellinger",
    "variation_totale",
    "shannon_norm",
    "toutes_metriques",
    "verdict_f1",
    "SEUIL_N_PLEIN",
    "SEUIL_N_MIN",
    "SEUIL_GEANT",
]

SEUIL_N_PLEIN = 5   # n >= 5 : verdicts pleins
SEUIL_N_MIN = 3     # 3 <= n < 5 : CONDITIONNEL ; n < 3 : refus
SEUIL_GEANT = 5.0   # phi_ratio = max/moyenne >= 5 : "géant avéré"


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------

def complexite_noeud(node: ast.AST) -> int:
    """kappa(f) = nombre de noeuds AST du sous-arbre (pression morphique)."""
    return sum(1 for _ in ast.walk(node))


def kappas_depuis_source(source: str) -> list[int]:
    """Extrait les kappa de toutes les defs (top-level + méthodes).

    Convention VERBATIM de mesures_conformite.py : parcours ast.walk pour les
    FunctionDef/AsyncFunctionDef, puis second parcours pour les méthodes des
    classes (les defs imbriquées dans des fonctions sont comptées via le
    premier walk, comme dans la référence).
    """
    arbre = ast.parse(source)
    fonctions: list[ast.AST] = []
    for node in ast.walk(arbre):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fonctions.append(node)
    for node in ast.walk(arbre):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fonctions.append(sub)
    return [complexite_noeud(nd) for nd in fonctions]


def kappas_depuis_fichier(chemin: str) -> list[int] | None:
    """None si le fichier n'est pas parsable."""
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        return kappas_depuis_source(source)
    except (SyntaxError, ValueError, OSError):
        return None


def profil(kappa: list[int]) -> tuple[int, list[float]]:
    """(n, P) avec P : p_i = kappa_i / sum(kappa)."""
    n = len(kappa)
    total = sum(kappa)
    if n == 0 or total <= 0:
        raise ValueError("profil vide ou somme nulle")
    return n, [k / total for k in kappa]


# ---------------------------------------------------------------------------
# Les six métriques (I1..I9 vérifiées dans verifier_identites.py)
# ---------------------------------------------------------------------------

def var_relative(kappa: list[int]) -> float:
    """alpha=2 : chi-2(P||Q0) = n*||P-Q0||_2^2 = exp(D2)-1 (I1, I2, I3)."""
    n, p = profil(kappa)
    q = 1.0 / n
    return sum((pi - q) ** 2 / q for pi in p)


def kl_divergence(kappa: list[int]) -> float:
    """alpha->1 : D_KL(P||Q0) = (1 - shannon_norm)*log2(n) (I9).

    REDONDANT exact avec shannon_norm : conservé comme dérivée à coût nul
    pour l'interopérabilité du curseur alpha (SPEC §1.2).
    """
    n, p = profil(kappa)
    q = 1.0 / n
    return sum(pi * math.log2(pi / q) for pi in p if pi > 0.0)


def d_infini(kappa: list[int]) -> float:
    """alpha->infini : D_inf = log(n * max p_i) = log(phi_ratio) (I7)."""
    n, p = profil(kappa)
    q = 1.0 / n
    return math.log(max(p) / q)


def hellinger(kappa: list[int]) -> float:
    """H(P,Q0) = sqrt(1/2 * sum(sqrt(p_i)-sqrt(q_i))^2), dans [0,1].

    Vraie métrique (inégalité triangulaire), bornée : comparable entre
    fichiers de n différents, contrairement à var_relative (bornée par n-1).
    """
    n, p = profil(kappa)
    q = 1.0 / n
    rq = math.sqrt(q)
    return math.sqrt(0.5 * sum((math.sqrt(pi) - rq) ** 2 for pi in p))


def variation_totale(kappa: list[int]) -> float:
    """TV(P,Q0) = 1/2 * sum|p_i - q_i|, dans [0, 1-1/n].

    Sens opérationnel : part de masse à déplacer pour uniformiser.
    """
    n, p = profil(kappa)
    q = 1.0 / n
    return 0.5 * sum(abs(pi - q) for pi in p)


def shannon_norm(kappa: list[int]) -> float:
    """H(P)/log2(n) dans [0,1] ; 1 = uniforme parfaite."""
    n, p = profil(kappa)
    if n == 1:
        return 1.0
    h = -sum(pi * math.log2(pi) for pi in p if pi > 0.0)
    return h / math.log2(n)


def toutes_metriques(kappa: list[int]) -> dict:
    """Les six métriques + grandeurs auxiliaires (n, phi_ratio,
    contraste_max_min)."""
    n = len(kappa)
    mx, mn = max(kappa), min(kappa)
    mu = sum(kappa) / n
    return {
        "n": n,
        # contraste_max_min = max/min : DIAGNOSTIC AUXILIAIRE uniquement
        # (PHI_CONTRAT.md §1.3(c)) — jamais injecté dans la borne du théorème F1,
        # qui porte sur phi_ratio = max/mu (cf. correction dans verdict_f1).
        "contraste_max_min": (mx / mn) if mn > 0 else float("inf"),
        "phi_ratio": mx / mu if mu > 0 else 1.0,
        "var_relative": var_relative(kappa),
        "kl_divergence": kl_divergence(kappa),
        "d_infini": d_infini(kappa),
        "hellinger": hellinger(kappa),
        "variation_totale": variation_totale(kappa),
        "shannon_norm": shannon_norm(kappa),
    }


# ---------------------------------------------------------------------------
# Verdicts typés sur F1
# ---------------------------------------------------------------------------

def verdict_f1(kappa: list[int]) -> dict:
    """Verdict typé du domaine distributionnel sur la facette F1.

    Théorème CORRECT (Samuelson, forme population) :
        var_relative >= (phi_ratio - 1)^2 / n   avec phi_ratio = max(kappa)/mu.
    Contraposée : phi_ratio <= 1 + sqrt(n * var_relative).

    CORRECTION du 2026-10-07 : VARIANCE_LILITH_IDENTITES.md énonce le théorème
    avec R = max/min ; sa preuve suppose implicitement mu = min (l'égalité
    (kmax-mu)/mu = R-1 exige mu = kmin). Contre-exemple : kappa=[10,9,11,10,9]
    donne var_relative=0.00583 < (R-1)^2/n=0.00988. L'énoncé vrai porte sur
    phi_ratio = max/mu (l'exemple numérique du doc lui-même calcule max/mu).
    Cet instrument implémente l'énoncé VRAI, pas l'énoncé faux.

    - Géant avéré (phi_ratio >= SEUIL_GEANT) -> DÉMONTRÉ.
    - Sinon borne supérieure DÉMONTRÉE sur phi_ratio (contraposée).
    - F2, F3 -> SILENCIEUX (théorème d'impossibilité).
    """
    n = len(kappa)
    if n < SEUIL_N_MIN:
        return {
            "facette": "F1",
            "verdict": "INDÉCIDABLE",
            "motif": f"n={n} < {SEUIL_N_MIN} : pas de mesure (C7).",
            "mesures": {"n": n},
        }
    m = toutes_metriques(kappa)
    phi_ratio = m["phi_ratio"]
    # Théorème vrai : var_relative >= (phi_ratio-1)^2/n (toujours vérifié).
    borne_theoreme = (phi_ratio - 1.0) ** 2 / n
    assert m["var_relative"] + 1e-9 >= borne_theoreme, \
        "violation du théorème F1 (vrai) : instrument ou théorie à réviser"
    base = {
        "facette": "F1",
        "mesures": m,
        "borne_theoreme": borne_theoreme,
        "regime": ("CONDITIONNEL (n<5, C3 aggravée)" if n < SEUIL_N_PLEIN
                   else "plein"),
    }
    if phi_ratio >= SEUIL_GEANT:
        base.update({
            "verdict": "DÉMONTRÉ",
            "motif": (f"phi_ratio={phi_ratio:.2f} >= {SEUIL_GEANT} : fonction "
                      f"géante avérée (>= {SEUIL_GEANT}x la moyenne) ; "
                      f"var_relative={m['var_relative']:.3f} >= "
                      f"(phi_ratio-1)^2/n={borne_theoreme:.3f} (théorème F1, "
                      f"forme corrigée Samuelson)."),
        })
    else:
        phi_max = 1.0 + math.sqrt(n * m["var_relative"])
        base.update({
            "verdict": "DÉMONTRÉ",
            "motif": (f"borne supérieure DÉMONTRÉE (contraposée du théorème F1 "
                      f"corrigé) : phi_ratio <= 1+sqrt(n*var_relative) = "
                      f"{phi_max:.2f}. Aucune fonction ne dépasse "
                      f"{phi_max:.2f}x la taille moyenne."),
            "phi_ratio_max_demontre": phi_max,
        })
    if n < SEUIL_N_PLEIN:
        base["verdict"] = "CONDITIONNEL"
        base["motif"] += " [n<5 : puissance faible, verdict conditionnel]"
    return base
