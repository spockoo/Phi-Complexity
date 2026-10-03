"""
eft.py — Error-Free Transformations pour phi-complexity (v0.14.1, opt-in).

Références mathématiques :
- T. Dekker, « A floating-point technique for extending the available
  precision », Numer. Math. 18 (1971) : TwoSum, TwoProd, Split,
  arithmétique double-double.
- S. Ozaki, T. Ogita, S. Oishi, S. M. Rump, « Error-free transformations
  of matrix multiplication by using fast routines », 2012.

Idée centrale (prise au sérieux, pas comme métaphore) : une EFT ne perd
JAMAIS l'erreur d'arrondi silencieusement — chaque opération rend le
couple (résultat_arrondi, erreur_exacte) avec :
    résultat + erreur == valeur exacte    (en arithmétique exacte)

Le transfert honnête vers phi n'est PAS la vitesse : il n'y a pas de
tensor cores dans une boucle Python, et le double-double est MESURÉMENT
plus lent que le flottant brut (voir EFT_BAYES_20260930.md §5). Le
transfert, c'est l'EXACTITUDE : la couche bayésienne de phi arrondit
aujourd'hui silencieusement ses croyances ; avec les EFT, chaque croyance
est rendue comme (valeur double-double, borne d'erreur CERTIFIÉE) au lieu
d'un flottant nu. L'instrument observe sans contaminer — et quand la
précision ne suffit plus, le terme d'erreur le dit bruyamment au lieu de
mentir en silence.

PORTÉE DE LA BORNE (lire avant de citer un chiffre) :
- La borne certifiée couvre l'arithmétique CÂBLÉE en EFT (produits,
  sommes de normalisation, divisions double-double), À ENTRÉES FIXÉES.
- Elle ne couvre PAS l'erreur des capteurs en amont (sigmoïdes,
  log1p, métriques d'audit) : ce sont des flottants déjà arrondis quand
  l'EFT les reçoit. Une borne certifiée sur du sable reste du sable —
  elle dit seulement : « l'inférence n'a rien perdu EN PLUS ».
- Hypothèses : arrondi au plus près (round-to-nearest, le mode IEEE 754
  par défaut de CPython), pas d'overflow/underflow dans la chaîne
  (les croyances vivent dans [0, 1] ou en log-échelle ~ [-5, 15] :
  l'hypothèse est vérifiée par construction, et `magnitude_max` est
  exposée pour audit).

CE QUE L'EFT NE GARANTIT PAS (en toutes lettres) :
- Pas de calibration magique : un postérieur certifié à 2⁻¹⁰⁰ près reste
  une HEURISTIQUE d'inspiration bayésienne (voir LIMITE_HONNETETE de
  croyances.py). L'exactitude arithmétique ne rend pas le modèle vrai.
- Pas de vitesse : facteur de ralentissement mesuré et publié.
- La division double-double n'est pas une EFT exacte (aucune division
  ne l'est) : son erreur est d'ordre u², couverte par la borne
  conservative — pas nulle.
"""
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ────────────────────────────────────────────────────────
# CONSTANTES
# ────────────────────────────────────────────────────────

U = 2.0 ** -53
"""Unité d'arrondi binaire64 (u = 2⁻⁵³) en arrondi au plus près."""

FMA_DISPONIBLE = hasattr(math, "fma")
"""math.fma n'existe que depuis Python 3.13 ; sans lui, TwoProd passe
par le découpage de Dekker (17 flops, sans FMA). Le chemin est choisi
à l'import et exposé pour audit."""

K_BORNE = 16.0
"""Constante conservative de la borne certifiée (voir `borne_erreur`).
Chaque opération double-double a une erreur relative ≤ ~4u² (résultat
classique) ; on prend ×4 de marge : une borne certifiée doit être VRAIE,
pas fine."""

SPLIT_S = 27
"""Paramètre du découpage de Dekker pour p = 53 bits : s = ⌈p/2⌉ = 27,
constante C = 2²⁷ + 1 = 134217729."""


def eft_active() -> bool:
    """L'arithmétique exacte est-elle demandée ? (opt-in : PHI_EFT=1)."""
    return os.environ.get("PHI_EFT", "0") == "1"


# ────────────────────────────────────────────────────────
# EFT DE BASE — invariants exacts, documentés un par un.
# ────────────────────────────────────────────────────────

def two_sum(a: float, b: float) -> Tuple[float, float]:
    """TwoSum de Knuth (6 flops, sans hypothèse d'ordre).

    INVARIANT EXACT : s + e == a + b en arithmétique exacte
    (hors overflow ; l'underflow graduel est préservé en
    arrondi au plus près).
    """
    s = a + b
    bp = s - a
    e = (a - (s - bp)) + (b - bp)
    return s, e


def fast_two_sum(a: float, b: float) -> Tuple[float, float]:
    """FastTwoSum (3 flops). PRÉCONDITION : |a| >= |b|.

    Même invariant exact que two_sum. Ne l'appeler que quand la
    précondition est garantie (ex. renormalisation : |s| >= |e|).
    """
    s = a + b
    e = b - (s - a)
    return s, e


def split(a: float) -> Tuple[float, float]:
    """Découpage de Dekker : a = a_hi + a_lo EXACTEMENT.

    a_hi porte ⌈p/2⌉ = 27 bits de poids fort, a_lo le reste.
    INVARIANT EXACT : a_hi + a_lo == a (hors over/underflow).
    """
    c = 134217729.0 * a  # (2^27 + 1) * a
    a_hi = c - (c - a)
    a_lo = a - a_hi
    return a_hi, a_lo


def _two_prod_dekker(a: float, b: float) -> Tuple[float, float]:
    """TwoProd via découpage de Dekker (17 flops, sans FMA)."""
    p = a * b
    a_hi, a_lo = split(a)
    b_hi, b_lo = split(b)
    e = ((a_hi * b_hi - p) + a_hi * b_lo + a_lo * b_hi) + a_lo * b_lo
    return p, e


def _two_prod_fma(a: float, b: float) -> Tuple[float, float]:
    """TwoProd via FMA matériel (2 flops)."""
    p = a * b
    e = math.fma(a, b, -p)
    return p, e


def two_prod(a: float, b: float) -> Tuple[float, float]:
    """Produit sans erreur : (p, e) avec p = fl(a*b).

    INVARIANT EXACT : p + e == a*b en arithmétique exacte
    (hors over/underflow). Chemin FMA si disponible (Python ≥ 3.13),
    sinon découpage de Dekker — même invariant, pas même vitesse.
    """
    if FMA_DISPONIBLE:
        return _two_prod_fma(a, b)
    return _two_prod_dekker(a, b)


# ────────────────────────────────────────────────────────
# ARITHMÉTIQUE DOUBLE-DOUBLE — un nombre = (hi, lo), |lo| ≤ u|hi|.
# ────────────────────────────────────────────────────────

def dd_add(a_hi: float, a_lo: float, b_hi: float, b_lo: float) -> Tuple[float, float]:
    """Addition double-double (Dekker add2, variante robuste).

    Erreur relative ≤ ~4u². La renormalisation finale utilise two_sum
    (pas fast_two_sum) : toujours correcte, sans précondition.
    """
    s, e = two_sum(a_hi, b_hi)
    e = e + (a_lo + b_lo)
    return two_sum(s, e)


def dd_mul(a_hi: float, a_lo: float, b_hi: float, b_lo: float) -> Tuple[float, float]:
    """Multiplication double-double (Dekker mul2).

    Erreur relative ≤ ~4u².
    """
    p1, p2 = two_prod(a_hi, b_hi)
    p2 = p2 + (a_hi * b_lo + a_lo * b_hi)
    return two_sum(p1, p2)


def dd_div(a_hi: float, a_lo: float, b_hi: float, b_lo: float) -> Tuple[float, float]:
    """Division double-double (Dekker, division longue itérée).

    ATTENTION HONNÊTE : ce n'est PAS une EFT exacte — aucune division
    ne peut l'être en nombre fini d'opérations. C'est une approximation
    d'erreur relative O(u²) : q1 = fl(ah/bh), reste r = a − q1·b en
    double-double, q2 = fl(rh/bh), etc. L'erreur résiduelle est couverte
    par la borne conservative (comptée comme 3 opérations dd).
    Diviseur nul → ZeroDivisionError (comportement Python, pas masqué).
    """
    q1 = a_hi / b_hi
    # r = a - q1 * b (double-double)
    r_hi, r_lo = dd_add(a_hi, a_lo, *(_dd_neg(*dd_mul(q1, 0.0, b_hi, b_lo))))
    q2 = r_hi / b_hi
    r_hi, r_lo = dd_add(r_hi, r_lo, *(_dd_neg(*dd_mul(q2, 0.0, b_hi, b_lo))))
    q3 = r_hi / b_hi
    q_hi, q_lo = two_sum(q1, q2)
    return dd_add(q_hi, q_lo, q3, 0.0)


def _dd_neg(a_hi: float, a_lo: float) -> Tuple[float, float]:
    """Opposé double-double (exact)."""
    return -a_hi, -a_lo


def neumaier_sum(xs) -> Tuple[float, float]:
    """Sommation compensée de Neumaier.

    Rend (s, c) avec s + c ≈ Σ xs à l'ordre 2 : l'erreur résiduelle
    est en O(u²·Σ|x|) au lieu de O(u·Σ|x|) pour la somme naïve.
    C'est le « filet » qui capture ce que Σ flottant perd en silence.
    """
    s = 0.0
    c = 0.0
    for x in xs:
        t = s + x
        if abs(s) >= abs(x):
            c += (s - t) + x
        else:
            c += (x - t) + s
        s = t
    return s, c


# ────────────────────────────────────────────────────────
# ACCUMULATEUR CERTIFIÉ — la pièce que la couche bayésienne utilise.
# ────────────────────────────────────────────────────────

class AccumulateurCertifie:
    """Accumulateur double-double avec compteur d'opérations honnête.

    Chaque opération dd est comptée ; `borne_erreur()` rend la borne
    conservative B = K_BORNE · n · u² · M où n = nombre d'opérations dd,
    M = max |hi| rencontré. INVARIANT DOCUMENTÉ :

        |valeur_exacte − (hi + lo)| ≤ B

    sous les hypothèses du module (arrondi au plus près, pas
    d'over/underflow). K_BORNE = 16 inclut ×4 de marge sur les bornes
    publiées : la borne est conservative par construction.
    """

    def __init__(self) -> None:
        self.hi = 0.0
        self.lo = 0.0
        self.n_ops = 0
        self.magnitude_max = 0.0

    def _bump(self, n: int = 1) -> None:
        self.n_ops += n
        m = abs(self.hi)
        if m > self.magnitude_max:
            self.magnitude_max = m

    def charger(self, x_hi: float, x_lo: float = 0.0) -> "AccumulateurCertifie":
        """Charge une valeur initiale (0 opération comptée : pas d'arrondi)."""
        self.hi, self.lo = x_hi, x_lo
        m = abs(x_hi)
        if m > self.magnitude_max:
            self.magnitude_max = m
        return self

    def ajouter(self, x: float) -> "AccumulateurCertifie":
        """Ajoute un flottant (exact en entrée : (x, 0.0))."""
        self.hi, self.lo = dd_add(self.hi, self.lo, x, 0.0)
        self._bump(1)
        return self

    def ajouter_dd(self, x_hi: float, x_lo: float) -> "AccumulateurCertifie":
        """Ajoute un double-double."""
        self.hi, self.lo = dd_add(self.hi, self.lo, x_hi, x_lo)
        self._bump(1)
        return self

    def multiplier(self, x: float) -> "AccumulateurCertifie":
        """Multiplie par un flottant."""
        self.hi, self.lo = dd_mul(self.hi, self.lo, x, 0.0)
        self._bump(1)
        return self

    def diviser_par(self, x_hi: float, x_lo: float) -> "AccumulateurCertifie":
        """Divise par un double-double (compte 3 : la division longue
        itère 3 quotients partiels — voir dd_div)."""
        self.hi, self.lo = dd_div(self.hi, self.lo, x_hi, x_lo)
        self._bump(3)
        return self

    def valeur(self) -> Tuple[float, float]:
        """Le couple (hi, lo) : la valeur la plus précise disponible."""
        return self.hi, self.lo

    def borne_erreur(self) -> float:
        """Borne certifiée conservative sur |exact − (hi+lo)|."""
        if self.n_ops == 0 or self.magnitude_max == 0.0:
            return 0.0
        return K_BORNE * self.n_ops * U * U * self.magnitude_max


# ────────────────────────────────────────────────────────
# CROYANCE CERTIFIÉE — ce que l'oracle exhibe.
# ────────────────────────────────────────────────────────

@dataclass
class CroyanceCertifiee:
    """Une croyance rendue comme (valeur, borne) au lieu d'un flottant nu.

    - `hi`, `lo` : valeur double-double (hi+lo = meilleure approximation).
    - `borne_erreur` : B tel que |exact − (hi+lo)| ≤ B (conservative).
    - `termes` : les termes d'évidence exhibés (même contenu qu'en
      mode flottant — l'EFT ne change pas la formule, seulement
      l'arithmétique).
    - `nb_operations`, `magnitude_max` : les intrants de la borne,
      exposés pour audit (pas de constante cachée).
    """
    nom: str
    hi: float
    lo: float
    borne_erreur: float
    termes: Dict[str, float] = field(default_factory=dict)
    nb_operations: int = 0
    magnitude_max: float = 0.0
    mode: str = "eft"

    @property
    def valeur(self) -> float:
        """Meilleure approximation flottante : hi + lo."""
        return self.hi + self.lo

    def vers_dict(self) -> dict:
        """Forme JSON-sérialisable pour les traces d'oracle."""
        return {
            "nom": self.nom,
            "valeur_hi": self.hi,
            "valeur_lo": self.lo,
            "valeur": self.valeur,
            "borne_erreur_certifiee": self.borne_erreur,
            "termes": dict(self.termes),
            "nb_operations": self.nb_operations,
            "magnitude_max": self.magnitude_max,
            "mode": self.mode,
        }
