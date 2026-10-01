"""
tests/test_formules.py — Tests du langage de formules (dérivé du parseur GNS-754).
"""
import math
import pytest

from phi_complexity.formules import (
    ParseurFormules, NOMS_METRIQUES, construire_env, evaluer_formule, evaluer_gate,
)

ENV = {"radiance": 82.4, "lilith_variance": 1137.0, "shannon_entropy": 0.837,
       "phi_ratio": 3.192, "nb_anomalies": 1, "nb_fonctions": 5}


class TestArithmetique:
    def test_priorites(self):
        p = ParseurFormules()
        assert p.parse("2+3*4").evaluer({}) == 14.0
        assert p.parse("(2+3)*4").evaluer({}) == 20.0
        assert p.parse("10-2*3").evaluer({}) == 4.0

    def test_puissance_associative_droite(self):
        p = ParseurFormules()
        assert p.parse("2^3^2").evaluer({}) == 512.0

    def test_unaires(self):
        p = ParseurFormules()
        assert p.parse("-3+5").evaluer({}) == 2.0
        assert p.parse("--3").evaluer({}) == 3.0
        assert p.parse("-(2+3)").evaluer({}) == -5.0

    def test_division_par_zero(self):
        p = ParseurFormules()
        with pytest.raises(ZeroDivisionError):
            p.parse("1/0").evaluer({})


class TestComparaisonsEtLogique:
    def test_comparaisons(self):
        p = ParseurFormules()
        assert p.parse("3 > 2").evaluer({}) is True
        assert p.parse("2 >= 3").evaluer({}) is False
        assert p.parse("2 <= 2").evaluer({}) is True
        assert p.parse("1 == 1").evaluer({}) is True
        assert p.parse("1 != 1").evaluer({}) is False
        assert p.parse("1 < 2").evaluer({}) is True

    def test_logique(self):
        p = ParseurFormules()
        assert p.parse("1 < 2 and 3 < 4").evaluer({}) is True
        assert p.parse("1 > 2 and 3 < 4").evaluer({}) is False
        assert p.parse("1 > 2 or 3 < 4").evaluer({}) is True
        assert p.parse("not 1 == 1").evaluer({}) is False
        assert p.parse("not 1 > 2").evaluer({}) is True

    def test_precedence_not_sur_and(self):
        # not lie plus fort que and : (not 1>2) and (1<2) = True
        p = ParseurFormules()
        assert p.parse("not 1 > 2 and 1 < 2").evaluer({}) is True

    def test_court_circuit(self):
        # le court-circuit évite la division par zéro à droite du and
        p = ParseurFormules()
        assert p.parse("1 > 2 and 1/0 == 0").evaluer({}) is False


class TestIdentifiantsEtConstantes:
    def test_identifiants(self):
        assert evaluer_formule("radiance + 10", ENV) == 92.4
        assert evaluer_formule("lilith_variance / 1000", ENV) == pytest.approx(1.137)

    def test_identifiant_inconnu(self):
        with pytest.raises(NameError):
            evaluer_formule("metrique_inexistante + 1", ENV)

    def test_constante_phi(self):
        assert evaluer_formule("phi", {}) == pytest.approx(1.6180339887)
        # l'identité dorée, au bruit flottant près
        assert abs(evaluer_formule("phi^2 - phi - 1", {})) < 1e-12

    def test_fonctions(self):
        assert evaluer_formule("sqrt(16)", {}) == 4.0
        assert evaluer_formule("abs(-3)", {}) == 3.0
        assert evaluer_formule("log2(8)", {}) == 3.0

    def test_fonction_inconnue(self):
        with pytest.raises(NameError):
            evaluer_formule("truc(2)", {})


class TestErreursSyntaxe:
    def test_expression_vide(self):
        with pytest.raises(ValueError):
            ParseurFormules().parse("")

    def test_syntaxe_invalide(self):
        with pytest.raises(SyntaxError):
            ParseurFormules().parse("2+")

    def test_parenthese_non_fermee(self):
        with pytest.raises(SyntaxError):
            ParseurFormules().parse("(2+3")

    def test_trailing_tokens(self):
        with pytest.raises(SyntaxError):
            ParseurFormules().parse("2 3")


class TestEnvironnement:
    def test_construire_env(self):
        metriques = {
            "radiance": 82.4, "phi_ratio": 3.192, "nb_fonctions": 5,
            "annotations": [
                {"niveau": "WARNING"}, {"niveau": "INFO"}, {"niveau": "CRITICAL"},
            ],
        }
        env = construire_env(metriques)
        assert env["radiance"] == 82.4
        assert env["nb_anomalies"] == 2.0
        assert env["phi"] == pytest.approx(1.6180339887)
        # seules les métriques mesurées sont exposées ; le reste lève NameError à l'usage
        assert "radiance_adiabatique" not in env
        with pytest.raises(NameError):
            evaluer_formule("radiance_adiabatique + 1", env)

    def test_gate_realiste_ouverte(self):
        assert evaluer_gate("radiance >= 75 and nb_anomalies <= 1", ENV) is True

    def test_gate_realiste_fermee(self):
        assert evaluer_gate("radiance >= 90 or lilith_variance < 100", ENV) is False

    def test_formule_personnalisee(self):
        val = evaluer_formule("100 - lilith_variance/phi - shannon_entropy^2", ENV)
        attendu = 100 - 1137.0 / 1.618033988749895 - 0.837 ** 2
        assert val == pytest.approx(attendu)
