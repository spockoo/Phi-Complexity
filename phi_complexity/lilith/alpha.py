#!/usr/bin/env python3
"""Curseur α — divergence de Rényi D_α(P‖Q₀) à α variable continu.

MISSION RUCHE-LILITH-INSTRUMENTS, chantier 2/5 (2026-10-07).

SPEC FORMELLE
-------------
Soit un fichier à n fonctions, κᵢ ≥ 0 la taille de la fonction i
(complexité AST), Σκ = Σᵢκᵢ > 0.

- Profil  : pᵢ = κᵢ / Σκ        (distribution P_F)
- Référence : qᵢ = 1/n         (équirépartition Q₀)

Divergence de Rényi d'ordre α ∈ ]0, ∞[ (nats) ::

    D_α(P‖Q₀) = 1/(α−1) · log( n^(α−1) · Σᵢ pᵢ^α )

Entropie de Rényi : H_α(P) = 1/(1−α) · log( Σᵢ pᵢ^α ).

IDENTITÉ D'UNIFICATION (démontrée, vérifiée numériquement à 1e-9) ::

    D_α(P‖Q₀) = log(n) − H_α(P)      pour tout α.

Cas limites (par continuité) :
  - α → 1 : D₁ = D_KL(P‖Q₀) = Σᵢ pᵢ·log(pᵢ·n)   (convention 0·log0 = 0)
  - α → ∞ : D_∞ = log(n·maxᵢ pᵢ) = log(R), R = max(κᵢ)/μ = phi_ratio
  - α → 0 : D₀ = log(n/k), k = #{i : κᵢ > 0}  (taille du support)

Généralisation continue de var_relative : la divergence de Tsallis ::

    T_α(P‖Q₀) = ( n^(α−1)·Σᵢpᵢ^α − 1 ) / (α−1)

  - T₂ = var_relative exactement (χ², identité I3)
  - T_α → D_KL quand α → 1 ;
  - T_α → +∞ quand α → ∞ pour tout profil NON uniforme
    (la Tsallis ne sature pas : seul D_α sature à log R).
    Convention : T_∞ = 0.0 sur profil uniforme, +inf sinon.
  - D_α et T_α sont en bijection strictement croissante à α fixé
    (α fini) : même ordre, mêmes AUC.

CAS DÉGÉNÉRÉS (choix documentés — jamais de NaN silencieux) :
  - n = 1            → D_α = T_α = 0 ∀α   (P = Q₀ ; cécité C7 assumée)
  - κᵢ = 0, Σκ > 0   → 0·log0 = 0 (α=1), 0^α = 0 (α>0) : convention standard
  - Σκ = 0           → ValueError (profil indéfini : on refuse, on n'invente pas)
  - κᵢ < 0           → ValueError (hors domaine)
  - α ≤ 0 ou NaN     → ValueError (hors domaine ]0, ∞])

Domaine de validité : la mesure ne contient QUE la distance du profil à
l'équirépartition (principe du témoin neutre). Toute sémantique
("conformité", "risque") est importée par l'interprète.

Stdlib + numpy uniquement. Python 3.11 et 3.12.
"""

import math

import numpy as np

__all__ = [
    "profile",
    "renyi_entropy",
    "d_alpha",
    "tsallis_divergence",
    "phi_ratio",
    "n_effectif",
]

_INF = float("inf")


def _valider_kappa(kappa):
    """Normalise kappa en vecteur numpy float64 ; lève ValueError hors domaine."""
    k = np.asarray(kappa, dtype=np.float64).ravel()
    if k.size == 0:
        raise ValueError("kappa vide : profil indéfini")
    if np.any(~np.isfinite(k)):
        raise ValueError("kappa contient NaN ou inf : hors domaine")
    if np.any(k < 0):
        raise ValueError("kappa négatif : hors domaine")
    total = float(k.sum())
    if total <= 0.0:
        raise ValueError("somme(kappa) = 0 : profil indéfini (refusé, pas inventé)")
    return k, total


def _valider_alpha(alpha):
    a = float(alpha)
    if not np.isfinite(a):
        if a == _INF and a > 0:
            return _INF
        raise ValueError("alpha hors domaine ]0, +inf] : %r" % (alpha,))
    if a <= 0.0:
        raise ValueError("alpha hors domaine ]0, +inf] : %r" % (alpha,))
    return a


def profile(kappa):
    """pᵢ = κᵢ / Σκ. Lève ValueError si Σκ = 0."""
    k, total = _valider_kappa(kappa)
    return k / total


def phi_ratio(kappa):
    """R = max(κᵢ)/μ (ratio de dominance). n=1 → 1.0."""
    k, total = _valider_kappa(kappa)
    mu = total / k.size
    return float(k.max() / mu)


def n_effectif(kappa):
    """n_eff = 1/Σ pᵢ² (Simpson)."""
    p = profile(kappa)
    s2 = float(np.sum(p ** 2))
    return 1.0 / s2 if s2 > 0 else float(kappa and len(np.ravel(kappa)))


def renyi_entropy(kappa, alpha):
    """H_α(P) = 1/(1−α)·log(Σ pᵢ^α) ; α=1 → Shannon ; α=∞ → −log(max pᵢ).

    Nats. n=1 → 0.
    """
    a = _valider_alpha(alpha)
    p = profile(kappa)
    if p.size == 1:
        return 0.0
    if a == _INF:
        m = float(p.max())
        return -math.log(m) if m > 0 else _INF
    if abs(a - 1.0) < 1e-12:
        # Shannon : Σ_{pᵢ>0} −pᵢ log pᵢ  (convention 0·log0 = 0)
        pos = p[p > 0]
        return float(-np.sum(pos * np.log(pos)))
    s = float(np.sum(p ** a))  # 0^α = 0 pour α > 0 : convention standard
    return math.log(s) / (1.0 - a)


def d_alpha(kappa, alpha):
    """D_α(P‖Q₀), divergence de Rényi d'ordre α ∈ ]0, ∞] (nats).

    α=1 → KL (limite), α=2 → D₂ avec exp(D₂)−1 = var_relative,
    α=∞ → log(R) = log(phi_ratio).
    """
    a = _valider_alpha(alpha)
    k, total = _valider_kappa(kappa)
    n = k.size
    if n == 1:
        return 0.0
    p = k / total
    if a == _INF:
        # D_∞ = log(n · max pᵢ)
        return math.log(n * float(p.max()))
    if abs(a - 1.0) < 1e-12:
        # KL(P‖Q₀) = Σ pᵢ log(pᵢ·n), termes nuls exclus (0·log0 = 0)
        pos = p[p > 0]
        return float(np.sum(pos * np.log(pos * n)))
    s = float(np.sum(p ** a))
    # D_α = 1/(α−1) · log( n^(α−1) · Σ pᵢ^α )
    return (math.log(s) + (a - 1.0) * math.log(n)) / (a - 1.0)


def tsallis_divergence(kappa, alpha):
    """T_α(P‖Q₀) = (n^(α−1)·Σ pᵢ^α − 1)/(α−1) — la « var_relative généralisée ».

    T₂ = var_relative ; T_α → D_KL (α→1) ; T_α → +∞ (α→∞, profil non
    uniforme — la Tsallis ne sature pas, seul D_α sature à log R).
    Monotone en D_α à α fixé (fini) : mêmes ordres, mêmes AUC.
    """
    a = _valider_alpha(alpha)
    k, total = _valider_kappa(kappa)
    n = k.size
    if n == 1:
        return 0.0
    p = k / total
    if a == _INF:
        # lim α→∞ : +∞ sauf profil uniforme (R = 1)
        r = n * float(p.max())
        return 0.0 if r <= 1.0 + 1e-12 else _INF
    if abs(a - 1.0) < 1e-12:
        return d_alpha(kappa, 1.0)  # limite continue : KL
    s = float(np.sum(p ** a))
    return (math.exp((a - 1.0) * math.log(n)) * s - 1.0) / (a - 1.0)
