#!/usr/bin/env python3
"""Curseur alpha — implémentation de RÉFÉRENCE stdlib (chantier 5/5).

MISSION RUCHE-LILITH-INSTRUMENTS, chantier 5 : référence d'implémentation
pour le contrat PHI_CONTRAT.md §3 (commande `phi lilith alpha`).

Sémantique IDENTIQUE à lilith_instruments/alpha.py (chantier 2, numpy) —
la concordance des deux implémentations est vérifiée par
tests/test_concordance_alpha.py (« vérifié deux fois » : deux jugements
indépendants, pas deux lectures du même).

Pourquoi une seconde implémentation (« battre ou ne pas exister ») :
  - stdlib uniquement : le chemin d'intégration phi n'impose pas numpy ;
  - déterminisme : aucun comportement conditionné à la présence de numpy ;
  - le contrat pointe une référence exécutable partout où Python tourne.

Identité d'unification (démontrée) : D_alpha(P||Q0) = log(n) - H_alpha(P).
Conventions de cas dégénérés : identiques à alpha.py (0*log0 = 0, n=1 -> 0,
somme nulle -> ValueError, jamais de NaN silencieux).

Stdlib uniquement. Python 3.11 et 3.12.
"""

import math

__all__ = [
    "d_alpha",
    "renyi_entropy",
    "tsallis_divergence",
    "curseur",
    "ALPHAS_CONTRAT",
]

#: Les trois coupes prescrites par le contrat (sensibilités complémentaires).
ALPHAS_CONTRAT = (1.0, 2.0, float("inf"))


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
    return n, [x / total for x in k]


def _valider_alpha(alpha):
    a = float(alpha)
    if math.isnan(a) or a <= 0.0:
        raise ValueError("alpha hors domaine ]0, +inf] : %r" % (alpha,))
    return a


def renyi_entropy(kappa, alpha):
    """H_alpha(P) en nats. alpha=1 -> Shannon ; alpha=inf -> -log(max p)."""
    a = _valider_alpha(alpha)
    n, p = _profil(kappa)
    if n == 1:
        return 0.0
    if a == float("inf"):
        m = max(p)
        return -math.log(m) if m > 0 else float("inf")
    if abs(a - 1.0) < 1e-12:
        return -sum(pi * math.log(pi) for pi in p if pi > 0.0)
    return math.log(sum(pi ** a for pi in p)) / (1.0 - a)


def d_alpha(kappa, alpha):
    """D_alpha(P||Q0) en nats. alpha=1 -> KL ; alpha=2 -> D2 ; alpha=inf -> log(R)."""
    a = _valider_alpha(alpha)
    n, p = _profil(kappa)
    if n == 1:
        return 0.0
    if a == float("inf"):
        return math.log(n * max(p))
    if abs(a - 1.0) < 1e-12:
        q = 1.0 / n
        return sum(pi * math.log(pi / q) for pi in p if pi > 0.0)
    s = sum(pi ** a for pi in p)
    return (math.log(s) + (a - 1.0) * math.log(n)) / (a - 1.0)


def tsallis_divergence(kappa, alpha):
    """T_alpha(P||Q0) = (n^(alpha-1) * Somme p_i^alpha - 1) / (alpha-1).

    T_2 = var_relative exactement ; limite alpha->1 : D_KL ; T_inf = R - 1.
    """
    a = _valider_alpha(alpha)
    n, p = _profil(kappa)
    if n == 1:
        return 0.0
    if a == float("inf"):
        return n * max(p) - 1.0
    if abs(a - 1.0) < 1e-12:
        return d_alpha(kappa, 1.0)
    s = sum(pi ** a for pi in p)
    return (math.exp((a - 1.0) * math.log(n)) * s - 1.0) / (a - 1.0)


def curseur(kappa, alphas=ALPHAS_CONTRAT):
    """Balayage du curseur : D_alpha et T_alpha pour chaque alpha demandé.

    Retourne {"D": {str(alpha): valeur}, "T": {...}, "unification_ok": bool}.
    `unification_ok` revérifie D_alpha = log(n) - H_alpha(P) sur chaque coupe
    (tolérance 1e-9) : l'instrument s'auto-vérifie à chaque appel.
    """
    n, p = _profil(kappa)
    sorties_d, sorties_t = {}, {}
    unification_ok = True
    for alpha in alphas:
        a = _valider_alpha(alpha)
        cle = "inf" if a == float("inf") else repr(a)
        d = d_alpha(kappa, a)
        t = tsallis_divergence(kappa, a)
        sorties_d[cle] = d
        sorties_t[cle] = t
        # Auto-vérification de l'identité d'unification.
        if n > 1:
            attendu = math.log(n) - renyi_entropy(kappa, a)
            if abs(d - attendu) > 1e-9 * max(1.0, abs(attendu)):
                unification_ok = False
    return {"D": sorties_d, "T": sorties_t, "unification_ok": unification_ok}
