"""
tests/test_core.py — Tests unitaires des constantes et fonctions core.
"""
import math
from phi_complexity.core import (
    PHI, PHI_INV, TAXE_SUTURE, ETA_GOLDEN, ZETA_PLANCHER,
    SEQUENCE_FIBONACCI, statut_gnostique,
    fibonacci_plus_proche, distance_fibonacci
)


class TestConstantesSouveraines:
    """Vérifie que les constantes correspondent aux valeurs du Morphic Phi Framework."""

    def test_phi_valeur(self):
        """φ = (1 + √5) / 2 ≈ 1.61803"""
        assert abs(PHI - 1.6180339887) < 1e-9

    def test_phi_propriete_auto_similaire(self):
        """La propriété fondamentale : φ² = φ + 1"""
        assert abs(PHI ** 2 - (PHI + 1)) < 1e-10

    def test_phi_inv_propriete(self):
        """1/φ = φ - 1"""
        assert abs(PHI_INV - (PHI - 1)) < 1e-10

    def test_taxe_suture_cm018(self):
        """CM-018 : τ_L = 3/√7 ≈ 1.13389"""
        attendu = 3 / math.sqrt(7)
        assert abs(TAXE_SUTURE - attendu) < 1e-10

    def test_eta_golden(self):
        """η_golden = 1 - 1/φ = 1 - φ_inv"""
        attendu = 1 - PHI_INV
        assert abs(ETA_GOLDEN - attendu) < 1e-10

    def test_zeta_plancher(self):
        """Plancher Zeta = 1/φ² ≈ 0.38196"""
        attendu = PHI_INV ** 2
        assert abs(ZETA_PLANCHER - attendu) < 1e-10

    def test_fibonacci_sequence_debut(self):
        """La séquence de Fibonacci commence par 1, 1, 2, 3, 5, 8..."""
        assert SEQUENCE_FIBONACCI[:6] == [1, 1, 2, 3, 5, 8]

    def test_fibonacci_convergence_phi(self):
        """Les ratios successifs de Fibonacci convergent vers φ (asymptotiquement)."""
        fib = SEQUENCE_FIBONACCI
        # On vérifie uniquement à partir de l'indice 8 (après 21) où la convergence est établie
        for i in range(8, len(fib) - 1):
            ratio = fib[i + 1] / fib[i]
            assert abs(ratio - PHI) < 0.001, f"Ratio {fib[i+1]}/{fib[i]} = {ratio} trop éloigné de φ"


class TestStatutGnostique:

    def test_hermetique(self):
        assert "HERMÉTIQUE" in statut_gnostique(90)
        assert "HERMÉTIQUE" in statut_gnostique(85)

    def test_en_eveil(self):
        assert "EN ÉVEIL" in statut_gnostique(75)
        assert "EN ÉVEIL" in statut_gnostique(60)

    def test_dormant(self):
        assert "DORMANT" in statut_gnostique(59)
        assert "DORMANT" in statut_gnostique(40)


class TestFibonacci:

    def test_fibonacci_plus_proche_petit(self):
        """5 est le Fibonacci le plus proche de 5."""
        assert fibonacci_plus_proche(5) == 5

    def test_fibonacci_plus_proche_intermediaire(self):
        """Le plus proche de 10 est 8 ou 13 — doit retourner 8."""
        assert fibonacci_plus_proche(10) == 8

    def test_fibonacci_plus_proche_grand(self):
        """Le plus proche de 100 est 89."""
        assert fibonacci_plus_proche(100) == 89

    def test_distance_fibonacci_zero_sur_fib(self):
        """Un nombre exactement de Fibonacci a une distance proche de 0."""
        # distance = |n - fib| / PHI = 0 / PHI = 0
        assert distance_fibonacci(8) == 0.0

    def test_distance_fibonacci_positive(self):
        """Un nombre hors séquence a une distance positive."""
        assert distance_fibonacci(10) > 0
