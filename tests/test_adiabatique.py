"""
tests/test_adiabatique.py — Tests d'équilibre adiabatique, universel et support de Lean 4.
"""
import os
import tempfile
import textwrap
import pytest

from phi_complexity import auditer
from phi_complexity.metriques import CalculateurRadiance
from phi_complexity.modeles import MetriqueFonction, ResultatAnalyse
from phi_complexity.langs import langage_pour_extension, est_fichier_supporte, obtenir_analyseur

tree_sitter_language_pack = pytest.importorskip("tree_sitter_language_pack")


def test_invariance_echelle_lilith():
    """
    Vérifie que la variance relative de Lilith Var_rel = Var / mu²
    est strictement invariante par changement d'échelle (multiplication de tous les nœuds par k).
    """
    f_p1 = [MetriqueFonction("f1", 1, 10, 1, 5, 1, 0.0, 1.0),
            MetriqueFonction("f2", 10, 20, 1, 10, 1, 0.0, 1.0)]
    f_p2 = [MetriqueFonction("f1", 1, 100, 1, 5, 1, 0.0, 1.0),
            MetriqueFonction("f2", 10, 200, 1, 10, 1, 0.0, 1.0)]

    res1 = ResultatAnalyse("test1.py", fonctions=f_p1)
    res2 = ResultatAnalyse("test2.py", fonctions=f_p2)

    c1 = CalculateurRadiance(res1, mode="adiabatique").calculer()
    c2 = CalculateurRadiance(res2, mode="adiabatique").calculer()

    assert abs(c1["lilith_rel_variance"] - c2["lilith_rel_variance"]) < 1e-5
    assert abs(c1["shannon_entropy_norm"] - c2["shannon_entropy_norm"]) < 1e-5


def test_equilibre_adiabatique_refactoring():
    """
    Vérifie qu'un découpage modulaire en sous-fonctions équilibrées
    ne détruit pas la Radiance adiabatique.
    """
    # 10 fonctions harmonieuses de complexité similaire
    fonctions = [
        MetriqueFonction(f"fn_{i}", i * 10, 15, 2, 8, 1, 0.0, 1.0)
        for i in range(10)
    ]
    res = ResultatAnalyse("modulaire.py", fonctions=fonctions)
    calc = CalculateurRadiance(res, mode="adiabatique").calculer()

    # Le score adiabatique doit être excellent (Hermétique)
    assert calc["radiance_adiabatique"] >= 85.0
    assert calc["shannon_entropy_norm"] > 0.90


def test_support_extension_lean():
    """Vérifie que l'extension .lean est reconnue automatiquement."""
    assert langage_pour_extension("moteur.lean") == "lean"
    assert est_fichier_supporte("moteur.lean") is True


def test_analyse_lean_ast():
    """Vérifie l'analyseur Tree-sitter sur un extrait de code Lean 4 certifié."""
    code_lean = """
    inductive Expr where
      | num (n : Int)
      | var (x : String)

    def eval (x : Int) : Int :=
      x + 1

    theorem eval_pos (x : Int) (h : 0 < x) : 0 < eval x := by
      simp [eval]
      omega
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lean", delete=False, encoding="utf-8") as f:
        f.write(textwrap.dedent(code_lean))
        chemin = f.name

    try:
        analyseur = obtenir_analyseur(chemin)
        resultat = analyseur.analyser()
        noms = {fn.nom for fn in resultat.fonctions}
        assert "eval" in noms
        assert "eval_pos" in noms
        assert resultat.nb_classes == 1  # inductive Expr

        calc = CalculateurRadiance(resultat, mode="adiabatique").calculer()
        assert calc["radiance"] >= 75.0
        assert calc["langage"] == "lean"
    finally:
        os.unlink(chemin)


def test_antifragilite_points_remarquables():
    """
    Vérifie les propriétés algébriques exactes de l'indice antifragile :
    - Homogène (Var_rel = 0) -> phi^-1 ≈ 0.618
    - Résonance dorée (Var_rel = phi^-1) -> 1.0
    - Rupture (Var_rel >= phi^2) -> 0.0
    """
    from phi_complexity.core import PHI, PHI_INV, calculer_antifragilite

    assert abs(calculer_antifragilite(0.0) - PHI_INV) < 1e-6
    assert abs(calculer_antifragilite(PHI_INV) - 1.0) < 1e-6
    assert abs(calculer_antifragilite(PHI ** 2) - 0.0) < 1e-6


def test_spectre_lilith_skewness_kurtosis():
    """Vérifie le calcul d'asymétrie et de kurtosis sur un module à queue lourde."""
    from phi_complexity.rapport import GenerateurRapport
    # Module avec une singularité (God-object)
    fonctions = [
        MetriqueFonction(f"micro_{i}", i * 5, 2, 1, 3, 0, 0.0, 0.1)
        for i in range(10)
    ] + [MetriqueFonction("monolithe", 100, 250, 6, 80, 4, 15.0, 10.0)]

    res = ResultatAnalyse("singularite.py", fonctions=fonctions)
    calc = CalculateurRadiance(res).calculer()

    assert calc["lilith_skewness"] > 1.5
    assert calc["lilith_kurtosis"] > 5.0
    assert calc["statut_antifragile"] in ("FRAGILE ░", "RÉSISTANT ◈")

    gen = GenerateurRapport(calc)
    conseils = gen.prescriptions()
    assert any("Singularité détectée" in c or "Décomposition de l'Oudjat" in c for c in conseils)
