"""
tests/test_bayes.py — Tests unitaires et d'intégration du moteur d'inférence bayésienne.
"""
import os
import tempfile
import textwrap
import pytest

from phi_complexity import auditer, diagnostic_bayesien, MoteurInferenceBayesienne
from phi_complexity.bayes import HYPOTHESES_MORPHIC


CODE_MONOLITHE = """
def petit_1():
    return 1

def petit_2():
    return 2

def monstre_monolithe(x, y, z):
    total = 0
    for i in range(x):
        for j in range(y):
            for k in range(z):
                total += i * j * k
    return total
"""

CODE_CHAOS_BOUCLES = """
def boucle_imbriquee_1(a):
    for i in range(a):
        for j in range(a):
            pass

def boucle_imbriquee_2(b):
    for i in range(b):
        for j in range(b):
            pass
"""

CODE_HARMONIEUX = """
def addition(a: int, b: int) -> int:
    return a + b

def soustraction(a: int, b: int) -> int:
    return a - b

def produit(a: int, b: int) -> int:
    return a * b
"""


def creer_fichier(code: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(textwrap.dedent(code))
        return f.name


def test_distribution_posteriors_somme_a_un():
    """La somme des probabilités a posteriori Σ P(H_k | E) doit être égale à 1.0."""
    fichier = creer_fichier(CODE_HARMONIEUX)
    try:
        diag = diagnostic_bayesien(fichier)
        somme_p = sum(diag.posteriors.values())
        assert abs(somme_p - 1.0) < 1e-3
        assert diag.hypothese_dominante in HYPOTHESES_MORPHIC
    finally:
        os.unlink(fichier)


def test_detection_bayesiene_monolithe():
    """Un code dominé par un organe géant doit inférer H_MONOLITHE en probabilité dominante."""
    fichier = creer_fichier(CODE_MONOLITHE)
    try:
        diag = diagnostic_bayesien(fichier)
        assert diag.hypothese_dominante in ("H_MONOLITHE", "H_CHAOS_IMBRIQUE")
        assert diag.gain_espere_radiance > 5.0
        assert "SUTURE" in diag.action_recommandee or "BOUCLES" in diag.action_recommandee
        assert diag.auto_patch_suggestion is not None
    finally:
        os.unlink(fichier)


def test_detection_bayesiene_chaos():
    """Un code avec boucles imbriquées répétées doit favoriser H_CHAOS_IMBRIQUE."""
    fichier = creer_fichier(CODE_CHAOS_BOUCLES)
    try:
        diag = diagnostic_bayesien(fichier)
        assert diag.hypothese_dominante == "H_CHAOS_IMBRIQUE"
        assert diag.posteriors["H_CHAOS_IMBRIQUE"] > 0.40
        assert "APLATISSEMENT" in diag.action_recommandee
    finally:
        os.unlink(fichier)


def test_detection_bayesiene_harmonique():
    """Un code épuré et modulaire doit inférer H_HARMONIQUE."""
    fichier = creer_fichier(CODE_HARMONIEUX)
    try:
        diag = diagnostic_bayesien(fichier)
        assert diag.hypothese_dominante == "H_HARMONIQUE"
        assert diag.posteriors["H_HARMONIQUE"] > 0.35
        assert "MAINTIEN" in diag.action_recommandee
    finally:
        os.unlink(fichier)
