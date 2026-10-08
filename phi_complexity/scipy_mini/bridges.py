"""phi_scipy.bridges — ce qui BAT scipy, en numpy pur (zéro dépendance scipy).

Verdict de l'étude (vague 1) confirmé par les mesures (vague 3) :
- FFT : scipy.fft gagne ~1,9× sur np.fft (pocketfft mieux réglé côté scipy)
  → aucun pont FFT (un wrapper n'apporterait rien).
- Solve dense : parité scipy/numpy (bruit de mesure) → aucun pont.
- Test de Welch : notre implémentation directe bat scipy ×12,3 (l'overhead
  de validation générique de scipy domine sur petites entrées) → GARDÉ.

Règle appliquée : battre ou ne pas exister.
"""
import math
import numpy as np

__all__ = ["welch_ttest"]


# ---------------------------------------------------------------------------
# Fonction bêta incomplète régularisée I_x(a, b) — fraction continue (NR betacf)
# ---------------------------------------------------------------------------
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delt = c * d
        h *= delt
        if abs(delt - 1.0) < EPS:
            break
    return h


def _ibetainc(x, a, b):
    """I_x(a, b) régularisée, 0 <= x <= 1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_sf(t, df):
    """Fonction de survie de la loi de Student : P(T > t), t >= 0."""
    if t <= 0:
        return 1.0 - _t_sf(-t, df)
    x = df / (df + t * t)
    return 0.5 * _ibetainc(x, df / 2.0, 0.5)


# ---------------------------------------------------------------------------
def welch_ttest(a, b, alternative="two-sided"):
    """Test t de Welch — parité numpy de scipy.stats.ttest_ind(equal_var=False).

    t = (m1-m2)/sqrt(s1²/n1 + s2²/n2), ddl de Welch-Satterthwaite.
    -> (statistic, pvalue). alternative : 'two-sided' | 'greater' | 'less'.
    """
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    n1, n2 = a.size, b.size
    m1, m2 = a.mean(), b.mean()
    v1, v2 = a.var(ddof=1), b.var(ddof=1)
    se2 = v1 / n1 + v2 / n2
    t = (m1 - m2) / math.sqrt(se2)
    df = se2 ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    if alternative == "two-sided":
        p = 2.0 * _t_sf(abs(t), df)
    elif alternative == "greater":
        p = _t_sf(t, df)
    elif alternative == "less":
        p = _t_sf(-t, df)
    else:
        raise ValueError("alternative inconnue")
    return t, min(p, 1.0)
