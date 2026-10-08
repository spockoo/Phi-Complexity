#!/usr/bin/env python3
"""Instrument du théorème F1 — tripwire de la facette F1 (chantier 5/5).

MISSION RUCHE-LILITH-INSTRUMENTS, chantier 5 : référence d'implémentation
pour le contrat PHI_CONTRAT.md §2 (commande `phi lilith f1`).

THÉORÈME (source prouvée : lilith_conformite/FORMALISATION_CONFORMITE.md §2.1)
  Soient kappa_1..kappa_n > 0, mu leur moyenne,
  sigma^2_L = (1/n) * Somme (kappa_i - mu)^2 (variance population),
  var_relative = sigma^2_L / mu^2,
  R = max_i kappa_i / mu  (phi_ratio — ratio de dominance à la moyenne).

  (i)   var_relative >= (R-1)^2 / n
  (ii)  contraposée : var_relative <= v  ==>  R <= 1 + sqrt(n*v)
  (iii) forme de Samuelson : R <= 1 + CV * sqrt(n), CV = sqrt(var_relative)

RÉSOLUTION D'AMBIGUÏTÉ (imposée par le contrat) :
  VARIANCE_LILITH_IDENTITES.md:114 énonce R = max(kappa_i)/min(kappa_i) —
  c'est une ERREUR DE TRANSCRIPTION de la synthèse. La source prouvée
  (§2.1, preuve exhibée) utilise R = max/mu. Preuve par contre-exemple :
  kappa = [10]*9 + [1000] donne var_relative = 7.424, (max/min - 1)^2/n =
  980.1 — l'inégalité « 7.424 >= 980.1 » est FAUSSE. Avec R = max/mu = 9.17,
  la borne vaut 6.68 et 7.424 >= 6.68 est VRAIE. Le ratio max/min est
  rapporté comme diagnostic auxiliaire `contraste_max_min`, jamais injecté
  dans la borne.

Verdicts typés (jamais de booléen nu) :
  DÉMONTRÉ     — la borne (i), la contraposée (ii), R_max : mathématiques.
  CONDITIONNEL — « géant probable » : R >= seuil conventionnel (seuil
                 arbitraire documenté, à calibrer sur corpus annoté).
  INDÉCIDABLE  — n < 3 (cécité C7).
  SILENCIEUX   — F2, F3 : théorème d'impossibilité, hors domaine.

Stdlib uniquement. Python 3.11 et 3.12.
"""

import math
import os

try:
    # Contexte package (chantier 1 : extraction κ VERBATIM, seuils coordonnés).
    from .distributionnel import (
        kappas_depuis_fichier,
        toutes_metriques,
        SEUIL_N_PLEIN as _N_PLEIN,
        SEUIL_GEANT as _SEUIL_GEANT,
    )
except ImportError:  # repli : exécution directe hors package
    from distributionnel import (
        kappas_depuis_fichier,
        toutes_metriques,
        SEUIL_N_PLEIN as _N_PLEIN,
        SEUIL_GEANT as _SEUIL_GEANT,
    )

__all__ = [
    "instrument_f1",
    "detecter_f1",
    "SEUIL_N_MIN",
    "SEUIL_PHI_RATIO_GEANT",
    "SEUIL_DEFAUT",
    "SEUIL_PHI_INV",
    "SEUIL_BIPARTITE_CONTRASTE",
    "TOL_BORNE",
]

#: n minimal pour une mesure (cécité C7 : n=1 -> score 0 « parfait »).
SEUIL_N_MIN = 3

#: Seuil CONVENTIONNEL de « géant probable » sur R = max/mu.
#: Arbitraire et documenté comme tel : aucun théorème ne fixe ce seuil ;
#: il sert d'écran de suspicion, pas de verdict. À calibrer sur corpus
#: annoté (recommandation n°5 de VARIANCE_LILITH_IDENTITES.md).
SEUIL_PHI_RATIO_GEANT = 5.0

#: Tolérance numérique sur la vérification de la borne (i).
TOL_BORNE = 1e-9


def _profil(kappa):
    k = [float(x) for x in kappa]
    n = len(k)
    if n == 0:
        raise ValueError("kappa vide : profil indéfini")
    if any((not math.isfinite(x)) or x < 0 for x in k):
        raise ValueError("kappa hors domaine (négatif ou non fini)")
    total = sum(k)
    if total <= 0.0:
        raise ValueError("somme(kappa) = 0 : profil indéfini (refusé, pas inventé)")
    return n, k, total


def instrument_f1(kappa):
    """Applique le théorème F1 au profil kappa.

    Retourne un dict JSON-sérialisable. Ne lève que sur profil indéfini
    (vide, somme nulle, valeurs hors domaine).
    """
    n, k, total = _profil(kappa)
    mu = total / n
    mx, mn = max(k), min(k)

    # R du THÉORÈME : max/mu (phi_ratio). Voir docstring (résolution).
    R = mx / mu if mu > 0 else 1.0
    contraste = (mx / mn) if mn > 0 else float("inf")  # auxiliaire, hors borne

    var = sum((x - mu) ** 2 for x in k) / n
    var_rel = var / (mu ** 2) if mu > 0 else 0.0

    borne_inf = (R - 1.0) ** 2 / n          # (i) : var_relative >= borne_inf
    r_max = 1.0 + math.sqrt(n * var_rel)   # (ii) : R <= r_max (contraposée)
    cv = math.sqrt(var_rel)

    # (i) doit TOUJOURS être satisfaite : sinon c'est la chaîne de mesure
    # qui est fausse, pas le théorème — signal d'instrument dégradé.
    satisfait = var_rel + TOL_BORNE >= borne_inf

    if n < SEUIL_N_MIN:
        return {
            "facette": "F1",
            "verdict": "INDÉCIDABLE",
            "motif": "n=%d < %d : pas de mesure (cécité C7)." % (n, SEUIL_N_MIN),
            "mesures": {"n": n},
        }

    mesures = {
        "n": n,
        "mu": mu,
        "R": R,                                  # max/mu — le R du théorème
        "contraste_max_min": contraste,          # auxiliaire, hors borne
        "var_relative": var_rel,
        "cv": cv,
        "borne_inf_theoreme": borne_inf,         # (i)
        "theoreme_satisfait": satisfait,
        "R_max_contraposee": r_max,              # (ii)
        "triplet_archive": [n, total, sum(x * x for x in k)],  # (n, Σκ, Σκ²)
    }

    base = {"facette": "F1", "mesures": mesures}
    if not satisfait:
        # Mathématiquement impossible : la mesure est corrompue.
        base.update({
            "verdict": "INDÉCIDABLE",
            "motif": ("INCOHÉRENCE : var_relative=%.6g < borne (R-1)^2/n=%.6g ; "
                      "la chaîne de mesure est corrompue, pas le théorème. "
                      "Instrument dégradé." % (var_rel, borne_inf)),
        })
        return base
    if R >= SEUIL_PHI_RATIO_GEANT:
        base.update({
            "verdict": "CONDITIONNEL",
            "motif": ("R=%.2f >= %.1f (seuil conventionnel, à calibrer) : "
                      "géant probable — la décomposition conçue est "
                      "suspecte d'être violée par une responsabilité non "
                      "décomposée. Borne (i) vérifiée : %.3f >= %.3f."
                      % (R, SEUIL_PHI_RATIO_GEANT, var_rel, borne_inf)),
        })
    else:
        base.update({
            "verdict": "DÉMONTRÉ",
            "motif": ("contraposée (ii) DÉMONTRÉE : R <= 1+sqrt(n*var_relative) "
                      "= %.2f — aucun géant au-delà de %.2f× la moyenne. "
                      "Borne (i) vérifiée : %.4g >= %.4g."
                      % (r_max, r_max, var_rel, borne_inf)),
            "R_max_demontre": r_max,
        })
    # F2/F3 : silence explicite, pas omission (théorème d'impossibilité).
    base["f2_f3"] = "SILENCIEUX (théorème d'impossibilité, parties a et b)"
    return base


# ===========================================================================
# CHANTIER 3/5 — détection F1 au niveau fichier : detecter_f1(chemin_fichier)
# ===========================================================================
# Couche calibrée au-dessus du tripwire théorique (instrument_f1 ci-dessus).
# Le théorème F1 donne une condition NÉCESSAIRE (non suffisante) : une
# fonction géante force var_relative ≥ (R−1)²/n avec R = max(κ)/μ (forme
# CORRIGÉE — cf. docstring du module : R = max/min est FAUX).
#
# Verdicts typés (jamais de booléen) :
#   DÉMONTRÉ    — géant avéré : var_relative ≥ seuil ET phi_ratio ≥ 5
#                 (fait mesuré + borne (i) vérifiée ; la lecture « violation »
#                 reste conditionnelle à la référence design — garde C6).
#   ÉVIDENCE    — var_relative ≥ seuil sans géant unique dominant :
#                 hétérogénéité compatible avec une violation de
#                 décomposition, à trancher par lecture du design.
#   CONDITIONNEL— zone grise [seuil/2, seuil), OU var ≥ seuil mais structure
#                 bipartite plausible (faux positif documenté : design à deux
#                 régimes, ex. jouet [50]×5+[500]×5 → var ≈ 0,669).
#   RÉFUTÉ      — var_relative < seuil/2 : aucune évidence instrumentale de
#                 fonction-dieu ; borne R ≤ 1+√(n·var) DÉMONTRÉE par la
#                 contraposée. Ne signifie PAS « décomposition saine »
#                 (cécités C2 : obésité uniforme, C3 : nains — rappelées).
#   INSUFFISANT — l'instrument refuse de scorer : n < 5 (dont n = 1, cécité
#                 C7 : le monolithe scorerait « parfait »), fichier illisible
#                 ou non parsable, α non calibré.
#
# Gardes : G1 (n<5 → INSUFFISANT), G2 (n=1 → INSUFFISANT + drapeau C7),
# G3 (bipartite → avertissement + rétrogradation), G4 (n_eff toujours
# affiché : « effectivement X fonctions sur N »), G5 (Σκ, μ toujours
# exhibés ; rappel C2 si μ élevé avec var basse).
#
# Paramètre α (lien chantier 2) : D_α(P‖Q₀) = log(n) − H_α(P). Seul α = 2
# (var_relative = exp(D₂) − 1) est calibré ; α ∈ {1, ∞} rend les mesures
# avec verdict INSUFFISANT (« seuil non calibré ») — honnêteté plutôt que
# seuil inventé. Formules D_α en stdlib pure (cf. alpha.py, chantier 2).

_PHI = (1.0 + math.sqrt(5.0)) / 2.0

#: φ⁻¹ ≈ 0,618 — le point de départ théorique. CONSERVÉ comme constante de
#: référence mais INVALIDÉ comme seuil par défaut par la calibration
#: (MESURES_CALIBRATION_F1.md : FPR = 1,000 à φ⁻¹ sur le corpus annoté —
#: tout fichier réel déclenche).
SEUIL_PHI_INV = 1.0 / _PHI

#: Seuil par défaut sur var_relative : 2,40 — valeur CALIBRÉE sur le corpus
#: annoté (CORPUS_F1.md, 5 POSITIF / 12 NÉGATIF) : plateau d'optimum
#: 2,40–3,00 (F1-score = 0,833, précision = 0,714, rappel = 1,000,
#: FPR = 0,167). FRAGILE (n = 17) : piste à confirmer sur corpus élargi,
#: pas un théorème. L'instrument à 2,40 bat le baseline naïf
#: « max > 200 lignes » (F1 = 0,750) ; à φ⁻¹ il le perdait (0,455).
SEUIL_DEFAUT = 2.40

#: Contraste minimal (moyenne_grande / moyenne_petite) pour signaler une
#: structure bipartite plausible. Heuristique documentée (le faux positif
#: démontré est à contraste 10).
SEUIL_BIPARTITE_CONTRASTE = 5.0

_VERDICTS_F1 = ("DÉMONTRÉ", "CONDITIONNEL", "ÉVIDENCE", "RÉFUTÉ", "INSUFFISANT")


def _d_alpha_stdlib(kappa, alpha):
    """D_α(P‖Q₀) en stdlib pure (formules VERBATIM de alpha.py, chantier 2).

    alpha = 1 → KL ; alpha = 2 → D₂ (var_relative = exp(D₂)−1) ;
    alpha = inf → log(R) = log(phi_ratio), R = max(κ)/μ.
    """
    n = len(kappa)
    total = float(sum(kappa))
    p = [k / total for k in kappa]
    if alpha == 1:
        return sum(pi * math.log(pi * n) for pi in p if pi > 0.0)
    if alpha == 2:
        s2 = sum(pi ** 2 for pi in p)
        return math.log(n) + math.log(s2)  # log(n) − H₂, H₂ = −log(Σ p²)
    if alpha == float("inf"):
        return math.log(n * max(p))
    raise ValueError("alpha supporté : 1, 2, inf (reçu %r)" % (alpha,))


def _detecter_bipartite(kappa):
    """Heuristique 2-clusters : coupe au plus grand saut sur κ triés.

    Retourne None si aucun découpage en 2 clusters de ≥ 2 fonctions n'existe,
    sinon dict(tailles, moyennes, contraste, coupe). Le contraste est
    moyenne_grande / moyenne_petite.
    """
    s = sorted(kappa)
    n = len(s)
    moyenne = sum(s) / n
    meilleur = None  # (variance_inter, indice_coupe, moy_a, moy_b)
    for i in range(1, n):
        a, b = s[:i], s[i:]
        if len(a) < 2 or len(b) < 2:
            continue
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        inter = (len(a) * (ma - moyenne) ** 2
                 + len(b) * (mb - moyenne) ** 2) / n
        if meilleur is None or inter > meilleur[0]:
            meilleur = (inter, i, ma, mb)
    if meilleur is None:
        return None
    _, i, ma, mb = meilleur
    petite, grande = (ma, mb) if ma <= mb else (mb, ma)
    return {
        "tailles": (i, n - i),
        "moyennes": (ma, mb),
        "contraste": grande / petite,
        "coupe": (s[i - 1], s[i]),
    }


def detecter_f1(chemin_fichier, seuil=SEUIL_DEFAUT, alpha=2):
    """Scorer F1 d'un fichier Python — verdict typé + mesures exhibées.

    Paramètres
    ----------
    chemin_fichier : chemin du fichier .py à scorer.
    seuil : seuil sur var_relative (défaut 2,40 — valeur calibrée sur le
        corpus annoté, cf. MESURES_CALIBRATION_F1.md ; φ⁻¹ ≈ 0,618, le
        point de départ théorique, a été INVALIDÉ comme défaut : FPR =
        1,000 sur le corpus).
    alpha : curseur de sensibilité (chantier 2). Seul alpha=2 est calibré ;
        alpha ∈ {1, inf} rend les mesures avec verdict INSUFFISANT.

    Retour — dict JSON-sérialisable :
        verdict, var_relative, phi_ratio (= R du théorème), R_descriptif
        (= max/min, auxiliaire), n, n_eff, seuil, marge, alpha, D_alpha,
        R_max_contraposee, borne_theoreme_verifiee, somme_kappa,
        moyenne_kappa, kappa_tries, bipartite, gardes (liste),
        explication (str).
    """
    nom = os.path.basename(str(chemin_fichier))
    resultat = {
        "fichier": nom,
        "verdict": "INSUFFISANT",
        "alpha": alpha,
        "seuil": seuil,
        "gardes": [],
    }

    if alpha not in (1, 2, float("inf")):
        resultat["gardes"].append(
            "alpha=%r hors domaine {1, 2, inf}" % (alpha,))
        resultat["explication"] = (
            "INSUFFISANT : alpha=%r non supporté." % (alpha,))
        return resultat

    kappa = kappas_depuis_fichier(chemin_fichier)
    if kappa is None:
        resultat["gardes"].append("fichier illisible ou non parsable")
        resultat["explication"] = (
            "INSUFFISANT : %s illisible ou non parsable — l'instrument "
            "refuse d'inventer un score." % nom)
        return resultat

    n = len(kappa)
    resultat["n"] = n

    # --- gardes G1/G2 : validité ---
    if n == 0:
        resultat["gardes"].append("aucune fonction détectée")
        resultat["explication"] = (
            "INSUFFISANT : aucune fonction détectée dans %s." % nom)
        return resultat
    if n == 1:
        resultat["gardes"].append(
            "C7 : n=1 — un monolithe scorerait var_relative=0 (« parfait »). "
            "L'instrument refuse ce score trompeur.")
        resultat["explication"] = (
            "INSUFFISANT (cécité C7) : fichier mono-fonction — "
            "var_relative serait 0, un « parfait » sans signification. "
            "Aucun verdict de décomposition n'est possible à n=1.")
        return resultat
    if n < _N_PLEIN:
        resultat["gardes"].append(
            "n=%d < %d : condition de validité théorique non remplie "
            "(puissance faible, cécité C3 aggravée)." % (n, _N_PLEIN))
        resultat["explication"] = (
            "INSUFFISANT : n=%d < %d — l'instrument ne score pas en "
            "deçà du seuil de validité." % (n, _N_PLEIN))
        return resultat

    # --- mesures (toutes_metriques : R = max/min DESCRIPTIF, phi_ratio = R
    # --- du théorème ; cf. correction documentée en tête de module) ---
    m = toutes_metriques(kappa)
    var = m["var_relative"]
    phi_ratio = m["phi_ratio"]          # = max(κ)/μ : le R du théorème
    # = max/min : auxiliaire descriptif, hors borne (clé renommée
    # "contraste_max_min" par le chantier 1 — repli sur "R" historique).
    r_descriptif = m.get("contraste_max_min", m.get("R"))
    n_eff = n / (1.0 + var)             # I4 : Simpson
    d_alpha = _d_alpha_stdlib(kappa, alpha)
    somme = sum(kappa)
    mu = somme / n
    # Contraposée (ii) : var ≤ v ⟹ R ≤ 1+√(n·v) — borne DÉMONTRÉE.
    r_max = 1.0 + math.sqrt(n * var)
    # Borne (i) : auto-contrôle de l'instrument (doit toujours tenir).
    borne = (phi_ratio - 1.0) ** 2 / n
    borne_ok = var + TOL_BORNE >= borne

    resultat.update({
        "var_relative": var,
        "phi_ratio": phi_ratio,
        "R_descriptif_max_min": r_descriptif,
        "n_eff": n_eff,
        "marge": var - seuil,
        "D_alpha": d_alpha,
        "R_max_contraposee": r_max,
        "borne_theoreme_verifiee": borne_ok,
        "somme_kappa": somme,
        "moyenne_kappa": mu,
        "kappa_tries": sorted(kappa),
    })

    if not borne_ok:
        # Mathématiquement impossible : chaîne de mesure corrompue.
        resultat["gardes"].append(
            "INCOHÉRENCE : var_relative < (R−1)²/n — instrument dégradé.")
        resultat["explication"] = (
            "INSUFFISANT : incohérence interne (borne (i) violée) — "
            "la chaîne de mesure est corrompue, pas le théorème.")
        return resultat

    # --- garde G3 : bipartite ---
    bip = _detecter_bipartite(kappa)
    resultat["bipartite"] = bip
    bipartite_signale = (
        bip is not None and bip["contraste"] >= SEUIL_BIPARTITE_CONTRASTE)
    if bipartite_signale:
        resultat["gardes"].append(
            "structure BIPARTITE plausible : 2 clusters de tailles %s, "
            "moyennes ≈ (%.0f, %.0f), contraste ≈ %.1f ≥ %.0f — faux "
            "positif documenté (design à deux régimes légitime possible ; "
            "le cas démontré [50]×5+[500]×5 donne var ≈ 0,669 ≥ seuil)."
            % (bip["tailles"], bip["moyennes"][0], bip["moyennes"][1],
               bip["contraste"], SEUIL_BIPARTITE_CONTRASTE))

    # --- garde G5 : échelle toujours exhibée ---
    resultat["gardes"].append(
        "échelle : Σκ=%d nœuds, μ=%.0f nœuds/fonction — le verdict ne juge "
        "jamais la taille totale (cécité C2)." % (somme, mu))

    if alpha != 2:
        resultat["gardes"].append(
            "alpha=%s : mesures rendues (D_α=%.3f), seuil non calibré "
            "(chantier 2) — aucun verdict." % (alpha, d_alpha))
        resultat["explication"] = (
            "INSUFFISANT : alpha=%s non calibré (seul α=2 l'est). "
            "Mesures : D_α=%.3f, var_relative=%.3f, phi_ratio=%.2f, n=%d."
            % (alpha, d_alpha, var, phi_ratio, n))
        return resultat

    # --- verdicts ---
    if var >= seuil:
        if phi_ratio >= _SEUIL_GEANT:
            resultat["verdict"] = "DÉMONTRÉ"
            resultat["explication"] = (
                "DÉMONTRÉ : phi_ratio=%.2f ≥ %.1f (fonction géante avérée : "
                "≥ %.1fx la moyenne) et var_relative=%.3f ≥ seuil %.3f "
                "(marge %+.3f) ; borne (i) vérifiée : %.3f ≥ %.3f. "
                "Effectivement %.1f fonctions sur %d. "
                "Ce qui est démontré : l'existence mesurée du géant et la "
                "borne — la lecture « décomposition violée » reste "
                "conditionnelle à la référence design (garde C6)."
                % (phi_ratio, _SEUIL_GEANT, _SEUIL_GEANT, var, seuil,
                   var - seuil, var, borne, n_eff, n))
        elif bipartite_signale:
            resultat["verdict"] = "CONDITIONNEL"
            resultat["explication"] = (
                "CONDITIONNEL : var_relative=%.3f ≥ seuil %.3f (marge %+.3f) "
                "MAIS structure bipartite plausible (contraste %.1f) — "
                "faux positif documenté (cf. jouet [50]×5+[500]×5 : "
                "var ≈ 0,669). Effectivement %.1f fonctions sur %d. "
                "Trancher par lecture du design déclaré."
                % (var, seuil, var - seuil, bip["contraste"], n_eff, n))
        else:
            resultat["verdict"] = "ÉVIDENCE"
            resultat["explication"] = (
                "ÉVIDENCE : var_relative=%.3f ≥ seuil %.3f (marge %+.3f), "
                "phi_ratio=%.2f (< %.1f : pas de géant unique dominant), "
                "n=%d — hétérogénéité compatible avec une violation de "
                "décomposition. Effectivement %.1f fonctions sur %d. "
                "À confirmer par lecture du design déclaré (garde C6 : "
                "le nombre seul ne démontre rien)."
                % (var, seuil, var - seuil, phi_ratio, _SEUIL_GEANT,
                   n, n_eff, n))
    elif var >= seuil / 2.0:
        resultat["verdict"] = "CONDITIONNEL"
        resultat["explication"] = (
            "CONDITIONNEL : var_relative=%.3f en zone grise [%.3f, %.3f), "
            "phi_ratio=%.2f, n=%d. Effectivement %.1f fonctions sur %d. "
            "Signal faible — lecture du code recommandée."
            % (var, seuil / 2.0, seuil, phi_ratio, n, n_eff, n))
    else:
        resultat["verdict"] = "RÉFUTÉ"
        resultat["explication"] = (
            "RÉFUTÉ (aucune évidence instrumentale) : var_relative=%.3f "
            "< %.3f, phi_ratio=%.2f, n=%d ; contraposée DÉMONTRÉE : "
            "R = max/μ ≤ 1+√(n·var) = %.2f. Effectivement %.1f fonctions "
            "sur %d. Ne signifie PAS « décomposition saine » : obésité "
            "uniforme (C2) et nains (C3) restent invisibles à l'instrument."
            % (var, seuil / 2.0, phi_ratio, n, r_max, n_eff, n))
        if mu > 500:
            resultat["gardes"].append(
                "C2 : μ=%.0f nœuds/fonction élevé avec var basse — obésité "
                "uniforme possible, vérifier Σκ manuellement." % mu)
        if r_max >= _SEUIL_GEANT:
            resultat["gardes"].append(
                "borne démontrée R ≤ %.2f ≥ %.1f : la réfutation est faible "
                "à grand n (ne pas lire comme une preuve d'absence)."
                % (r_max, _SEUIL_GEANT))

    return resultat
