"""
tests/test_eft.py — Tests des Error-Free Transformations (opt-in).

Stratégie : les invariants EFT sont EXACTS, donc testables contre
l'arithmétique rationnelle (fractions.Fraction) — pas contre des
constantes magiques. Un test qui échoue ici signifie un théorème faux,
pas une tolérance mal réglée.
"""
import math
import os
import random
from fractions import Fraction

import pytest

from phi_complexity import eft
from phi_complexity.eft import (
    AccumulateurCertifie, CroyanceCertifiee,
    dd_add, dd_div, dd_mul, neumaier_sum,
    split, two_prod, two_sum, eft_active,
)
from phi_complexity.oracle import TraceurOracle, rendre_oracle_console


# ────────────────────────────────────────────────────────
# INVARIANTS EXACTS (Dekker 1971) — vérifiés en rationnel.
# ────────────────────────────────────────────────────────

def _exact(a: float) -> Fraction:
    return Fraction(a)  # valeur binaire exacte du flottant


@pytest.mark.parametrize("a,b", [
    (1.0, 2.0), (1e16, 1.0), (0.1, 0.2), (-3.5, 1e-10),
    (1.7976931348623157e308, 1.0), (5e-324, 5e-324),
])
def test_two_sum_invariant_exact(a, b):
    s, e = two_sum(a, b)
    assert _exact(s) + _exact(e) == _exact(a) + _exact(b)


def test_two_sum_aleatoire():
    rng = random.Random(42)
    for _ in range(200):
        a = rng.uniform(-1e10, 1e10)
        b = rng.uniform(-1e10, 1e10)
        s, e = two_sum(a, b)
        assert _exact(s) + _exact(e) == _exact(a) + _exact(b)


@pytest.mark.parametrize("a", [1.0, 3.14159, 1e16, 1e-16, 123456789.123456789,
                         2.0 ** -1074, 1e300])
def test_split_invariant_exact(a):
    # Domaine : hors overflow — split(DBL_MAX) déborde par construction
    # (C·a avec C = 2^27+1), hypothèse documentée dans eft.py. 1e300 est
    # le plus grand ordre de grandeur testé sans déborder.
    hi, lo = split(a)
    # INVARIANT PORTeur : hi + lo == a en arithmétique exacte.
    # (C'est lui — et non un comptage de bits — dont two_prod dépend ;
    # l'exactitude de two_prod est vérifiée directement ci-dessous.)
    assert _exact(hi) + _exact(lo) == _exact(a)


def test_two_prod_invariant_exact():
    rng = random.Random(7)
    for _ in range(200):
        a = rng.uniform(-1e5, 1e5)
        b = rng.uniform(-1e5, 1e5)
        p, e = two_prod(a, b)
        assert _exact(p) + _exact(e) == _exact(a) * _exact(b)


def test_two_prod_chemin_dekker_sans_fma():
    # Python 3.12 : pas de math.fma → le chemin Dekker (17 flops) est
    # actif. On vérifie que le drapeau est cohérent avec l'interpréteur.
    assert eft.FMA_DISPONIBLE == hasattr(math, "fma")


# ────────────────────────────────────────────────────────
# ARITHMÉTIQUE DOUBLE-DOUBLE — erreur bornée, pas exacte.
# ────────────────────────────────────────────────────────

def test_dd_add_mul_precision():
    rng = random.Random(11)
    for _ in range(100):
        a = rng.uniform(-100, 100)
        b = rng.uniform(-100, 100)
        s_hi, s_lo = dd_add(a, 0.0, b, 0.0)
        p_hi, p_lo = dd_mul(a, 0.0, b, 0.0)
        # L'erreur double-double est d'ordre u², pas u :
        assert abs((_exact(s_hi) + _exact(s_lo)) - (_exact(a) + _exact(b))) \
            <= Fraction(4) * Fraction(2) ** -106 * max(1, abs(_exact(a) + _exact(b)))
        assert abs((_exact(p_hi) + _exact(p_lo)) - _exact(a) * _exact(b)) \
            <= Fraction(4) * Fraction(2) ** -106 * max(1, abs(_exact(a) * _exact(b)))


def test_dd_div_ordre_u2():
    q_hi, q_lo = dd_div(1.0, 0.0, 3.0, 0.0)
    approche = float(_exact(q_hi) + _exact(q_lo))
    assert abs(approche - 1 / 3) < 1e-30  # bien mieux que u ~ 1e-16
    with pytest.raises(ZeroDivisionError):
        dd_div(1.0, 0.0, 0.0, 0.0)


# ────────────────────────────────────────────────────────
# NEUMAIER — le filet anti-annulation catastrophique.
# ────────────────────────────────────────────────────────

def test_neumaier_cancellation_catastrophique():
    xs = [1e16, 1.0, -1e16]
    # La somme « naïve » (pliage gauche explicite) perd le 1.0 en silence.
    naif = 0.0
    for x in xs:
        naif = naif + x
    s, c = neumaier_sum(xs)
    assert naif == 0.0
    assert s + c == 1.0           # Neumaier récupère le terme perdu
    assert s == 0.0 and c == 1.0  # il vit dans la compensation
    # Note : depuis Python 3.12, sum() natif compense déjà (Neumaier) et
    # rend 1.0 ici — même le langage a dû cesser de perdre l'erreur en
    # silence. Le pliage manuel ci-dessus reste le « naïf » de référence.


# ────────────────────────────────────────────────────────
# ACCUMULATEUR CERTIFIÉ — la borne couvre l'erreur vraie.
# ────────────────────────────────────────────────────────

def test_accumulateur_borne_couvre_erreur_exacte():
    rng = random.Random(99)
    for _ in range(50):
        acc = AccumulateurCertifie()
        exact = Fraction(0)
        for _ in range(20):
            x = rng.uniform(-10, 10)
            acc.ajouter(x)
            exact += _exact(x)
        for _ in range(5):
            x = rng.uniform(0.5, 2.0)
            acc.multiplier(x)
            exact *= _exact(x)
        approx = _exact(acc.hi) + _exact(acc.lo)
        assert abs(exact - approx) <= Fraction(acc.borne_erreur()), \
            "la borne certifiée DOIT couvrir l'erreur exacte"
        # La borne suit la formule documentée B = K·n·u²·M (pas de magie).
        assert acc.borne_erreur() == pytest.approx(
            eft.K_BORNE * acc.n_ops * eft.U ** 2 * acc.magnitude_max)
        # Note d'échelle : ce stress-test fait grimper M (~300) par
        # multiplications ; la borne grandit honnêtement avec. Le seuil
        # falsifiable < 2⁻⁹⁰ du critère (b) s'applique aux cas d'usage
        # réels (bayes/croyances, M ~ 1) — vérifié dans les tests dédiés.


def test_accumulateur_charger_zero_ops():
    acc = AccumulateurCertifie().charger(0.25)
    assert acc.n_ops == 0 and acc.valeur() == (0.25, 0.0)


def test_eft_active_opt_in():
    assert not eft_active()  # défaut : inactif, même sans variable d'env
    os.environ["PHI_EFT"] = "1"
    try:
        assert eft_active()
    finally:
        del os.environ["PHI_EFT"]


# ────────────────────────────────────────────────────────
# NON-RÉGRESSION DES CLASSEMENTS (critère a).
# ────────────────────────────────────────────────────────

def _fichiers_phi():
    base = os.path.join(os.path.dirname(__file__), "..", "phi_complexity")
    return sorted(f for f in (os.path.join(base, f) for f in os.listdir(base))
                  if f.endswith(".py"))


def test_bayes_dominant_identique_flottant_vs_eft():
    from phi_complexity import MoteurInferenceBayesienne, auditer
    for f in _fichiers_phi():
        m = auditer(f)
        d_float = MoteurInferenceBayesienne(m).inferer()
        d_eft = MoteurInferenceBayesienne(m, exact=True).inferer()
        assert d_eft.hypothese_dominante == d_float.hypothese_dominante, \
            f"changement de dominant sur {f} = SIGNAL LILITH"
        assert d_eft.mode == "eft" and d_float.mode == "flottant"
        assert d_float.certifie == {}


def test_bayes_bornes_valides_rationnel():
    from phi_complexity import MoteurInferenceBayesienne, auditer
    from phi_complexity.bayes import HYPOTHESES_MORPHIC
    for f in _fichiers_phi()[:5]:
        m = auditer(f)
        eng = MoteurInferenceBayesienne(m)
        L = eng._calculer_vraisemblances()
        Lf = {h: _exact(L[h]) for h in HYPOTHESES_MORPHIC}
        pf = {h: _exact(eng.priors.get(h, 0.2)) for h in HYPOTHESES_MORPHIC}
        norme = sum(Lf[h] * pf[h] for h in HYPOTHESES_MORPHIC)
        exact = {h: Lf[h] * pf[h] / norme for h in HYPOTHESES_MORPHIC}
        d_eft = MoteurInferenceBayesienne(m, exact=True).inferer()
        for h in HYPOTHESES_MORPHIC:
            c = d_eft.certifie[h]
            ecart = abs(exact[h] - (_exact(c.hi) + _exact(c.lo)))
            assert ecart <= Fraction(c.borne_erreur), f"borne fausse sur {f}:{h}"
            assert c.borne_erreur < 2.0 ** -90


def test_croyances_classement_identique_flottant_vs_eft(tmp_path):
    from phi_complexity import chemins_croyants
    src = tmp_path / "mod.py"
    src.write_text(
        "def alpha(x):\n    return x + 1\n\ndef beta(y):\n    z = y * 2\n    return z\n")
    carte_f = chemins_croyants(str(tmp_path))
    carte_e = chemins_croyants(str(tmp_path), exact=True)
    noms_f = [s["nom"] for s in carte_f["symboles"]]
    noms_e = [s["nom"] for s in carte_e["symboles"]]
    assert noms_f == noms_e
    assert "mode_arithmetique" not in carte_f          # JSON flottant intact
    assert carte_e["mode_arithmetique"] == "eft"
    assert "borne_erreur_certifiee" not in carte_f["symboles"][0]
    assert carte_e["symboles"][0]["borne_erreur_certifiee"] < 2.0 ** -90


# ────────────────────────────────────────────────────────
# ORACLE — traces dans les deux modes.
# ────────────────────────────────────────────────────────

def test_oracle_traces_deux_modes():
    from phi_complexity import MoteurInferenceBayesienne, auditer
    from phi_complexity.bayes import HYPOTHESES_MORPHIC
    f = _fichiers_phi()[0]
    m = auditer(f)
    for exact in (False, True):
        tr = TraceurOracle()
        MoteurInferenceBayesienne(m, exact=exact, traceur=tr,
                                 cible=f).inferer()
        assert len(tr.entrees) == len(HYPOTHESES_MORPHIC)
        e = tr.entrees[0]
        assert e.mode == ("eft" if exact else "flottant")
        if exact:
            assert e.borne_erreur is not None and e.borne_erreur < 2.0 ** -90
        else:
            assert e.borne_erreur is None  # écrit en toutes lettres
        assert e.prior >= 0 and e.posterior is not None
    texte = rendre_oracle_console(tr.entrees)
    assert "TRACE D'ORACLE" in texte and "borne d'erreur certifiée" in texte
    from phi_complexity.oracle import TraceEntree
    entree_flottant = TraceEntree(
        horodatage="t", moteur="bayes", cible="c", symbole="s", prior=0.2,
        posterior=0.5, decision="d")  # borne_erreur=None : mode flottant
    assert "non certifiée (mode flottant)" in rendre_oracle_console(
        [entree_flottant])


def test_croyance_certifiee_vers_dict():
    c = CroyanceCertifiee(nom="h", hi=0.5, lo=1e-17, borne_erreur=1e-30,
                          termes={"prior": 0.2}, nb_operations=3,
                          magnitude_max=0.5)
    d = c.vers_dict()
    assert d["valeur"] == 0.5 + 1e-17
    assert d["borne_erreur_certifiee"] == 1e-30
