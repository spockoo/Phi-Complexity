"""Tests de markov.py — chaînes de Markov pour la chaîne des goulots.

Chaque test vérifie une propriété contre une solution connue
analytiquement, pas contre une autre implémentation.
"""

import math

import pytest

from phi_complexity.markov import (
    ABSORBANTS,
    ECHEC,
    ORDRE_AST,
    TERMINE,
    comparer_scenarios,
    construire,
    distribution_stationnaire,
    exemple_observations_ast,
    fiabilite,
    fraction_temps,
    identifier_goulot,
    simuler_debit,
    temps_absorption_moyen,
)


def _chaine_2_etats():
    """Chaîne à 2 états, solution analytique connue.

    P = [[0.7, 0.3], [0.4, 0.6]]
    Stationnaire : pi0 = 0.4 / (0.3 + 0.4) = 4/7, pi1 = 3/7.
    """
    return {
        "etats": ["A", "B"],
        "transitions": {"A": {"A": 0.7, "B": 0.3},
                        "B": {"A": 0.4, "B": 0.6}},
        "durees_moyennes": {"A": 1.0, "B": 1.0},
    }


def test_stationnaire_2_etats_solution_analytique():
    """La stationnaire d'une chaîne 2×2 doit valoir 4/7 et 3/7."""
    chaine = _chaine_2_etats()
    res = distribution_stationnaire(chaine)
    assert res["converge"] is True
    assert res["distribution"]["A"] == pytest.approx(4.0 / 7.0, abs=1e-6)
    assert res["distribution"]["B"] == pytest.approx(3.0 / 7.0, abs=1e-6)


def test_stationnaire_converge_residu():
    """L'itération de puissance converge avec un résidu sous la tolérance."""
    chaine = _chaine_2_etats()
    res = distribution_stationnaire(chaine, tol=1e-12)
    assert res["converge"] is True
    assert res["residu_l1"] < 1e-12
    # C'est une vraie distribution : somme = 1, valeurs positives.
    total = sum(res["distribution"].values())
    assert total == pytest.approx(1.0, abs=1e-9)
    assert all(v >= 0.0 for v in res["distribution"].values())


def test_goulot_identifie_par_fraction_temps():
    """Le goulot est l'état qui domine le TEMPS, pas les transitions.

    Chaîne récurrente A→B→A. Stationnaire : 0.5/0.5.
    Durées : A=1s, B=100s → fraction temps de B ≈ 99 %.
    Le goulot doit être B malgré la stationnaire équilibrée.
    """
    chaine = {
        "etats": ["A", "B"],
        "transitions": {"A": {"B": 1.0},
                        "B": {"A": 1.0}},
        "durees_moyennes": {"A": 1.0, "B": 100.0},
    }
    dist = distribution_stationnaire(chaine)["distribution"]
    # Stationnaire équilibrée.
    assert dist["A"] == pytest.approx(0.5, abs=1e-6)
    assert dist["B"] == pytest.approx(0.5, abs=1e-6)
    # Mais le goulot en temps doit être B.
    goulot = identifier_goulot(chaine, dist)
    assert goulot["goulot"] == "B"
    assert goulot["fraction_temps"] == pytest.approx(100.0 / 101.0, abs=1e-6)


def test_absorption_chaine_lineaire():
    """A → B → TERMINE : le temps moyen vaut d_A + d_B."""
    chaine = {
        "etats": ["A", "B", TERMINE],
        "transitions": {"A": {"B": 1.0},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 25.0, TERMINE: 0.0},
    }
    res = temps_absorption_moyen(chaine, "A")
    assert res["temps_s"] == pytest.approx(35.0, abs=1e-9)
    # Depuis B : seulement d_B.
    res_b = temps_absorption_moyen(chaine, "B")
    assert res_b["temps_s"] == pytest.approx(25.0, abs=1e-9)
    # Depuis TERMINE : 0.
    res_t = temps_absorption_moyen(chaine, TERMINE)
    assert res_t["temps_s"] == pytest.approx(0.0)


def test_absorption_avec_echec():
    """A → B (p=0.8) ou ECHEC (p=0.2) : l'échec ne coûte que d_A."""
    chaine = {
        "etats": ["A", "B", TERMINE, ECHEC],
        "transitions": {"A": {"B": 0.8, ECHEC: 0.2},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0},
                        ECHEC: {ECHEC: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 20.0,
                            TERMINE: 0.0, ECHEC: 0.0},
    }
    # t_A = 10 + 0.8 * 20 + 0.2 * 0 = 26.
    res = temps_absorption_moyen(chaine, "A")
    assert res["temps_s"] == pytest.approx(26.0, abs=1e-9)


def test_simulation_double_debit():
    """Doubler le débit d'un étage divise sa durée par 2 et le gain
    est cohérent : sur A→B→TERMINE avec d_A=10, d_B=30, doubler B
    donne (40-25)/40 = 37.5 %."""
    chaine = {
        "etats": ["A", "B", TERMINE],
        "transitions": {"A": {"B": 1.0},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 30.0, TERMINE: 0.0},
    }
    sim = simuler_debit(chaine, "B", 2.0)
    assert sim["temps_avant_s"] == pytest.approx(40.0, abs=1e-9)
    assert sim["temps_apres_s"] == pytest.approx(25.0, abs=1e-9)
    assert sim["gain_relatif"] == pytest.approx(0.375, abs=1e-9)
    # Après ×2, B (15 s) reste devant A (10 s) : toujours le goulot.
    assert sim["nouveau_goulot"] == "B"
    # Après ×4 (B=7.5 s), le goulot bascule vers A.
    sim4 = simuler_debit(chaine, "B", 4.0)
    assert sim4["nouveau_goulot"] == "A"


def test_simulation_facteur_invalide():
    """Un facteur <= 0 est refusé."""
    chaine = _chaine_2_etats()
    with pytest.raises(ValueError):
        simuler_debit(chaine, "A", 0.0)
    with pytest.raises(ValueError):
        simuler_debit(chaine, "INCONNUE", 2.0)


def test_construire_depuis_observations():
    """La construction compte correctement les transitions."""
    observations = [
        {"etape": "X", "duree_s": 1.0, "succes": True},
        {"etape": "Y", "duree_s": 2.0, "succes": True},
        # Nouveau run (retour en arrière dans l'ordre).
        {"etape": "X", "duree_s": 1.5, "succes": False},
    ]
    chaine = construire(observations, ordre=["X", "Y"])
    # X→Y une fois, X→ECHEC une fois : 0.5 / 0.5.
    assert chaine["transitions"]["X"]["Y"] == pytest.approx(0.5)
    assert chaine["transitions"]["X"][ECHEC] == pytest.approx(0.5)
    # Y→TERMINE une fois.
    assert chaine["transitions"]["Y"][TERMINE] == pytest.approx(1.0)
    # Durées moyennes.
    assert chaine["durees_moyennes"]["X"] == pytest.approx(1.25)
    assert chaine["durees_moyennes"]["Y"] == pytest.approx(2.0)
    # Absorbants bouclent.
    assert chaine["transitions"][TERMINE][TERMINE] == pytest.approx(1.0)
    assert chaine["transitions"][ECHEC][ECHEC] == pytest.approx(1.0)


def test_construire_exemple_ast():
    """L'exemple AST se construit sans erreur et a 5 runs."""
    observations = exemple_observations_ast()
    chaine = construire(observations, ordre=ORDRE_AST)
    assert chaine["runs_n"] == 5
    assert chaine["observations_n"] == len(observations)
    # L'échec d'export du run 3 doit apparaître.
    assert chaine["transitions"]["EXPORT"].get(ECHEC, 0.0) > 0.0
    # Diagnostic complet sans exception.
    from phi_complexity.markov import rendre_diagnostic_console
    texte = rendre_diagnostic_console(chaine)
    assert "GOULOT" in texte


def test_fiabilite_signale_etats_rares():
    """Les états peu observés sont signalés, pas masqués."""
    observations = [
        {"etape": "X", "duree_s": 1.0, "succes": True},
        {"etape": "Y", "duree_s": 2.0, "succes": True},
    ]
    chaine = construire(observations, ordre=["X", "Y"])
    fragiles = fiabilite(chaine, seuil_min=5)
    assert "X" in fragiles
    assert "Y" in fragiles
    # Les absorbants artificiels ne comptent pas.
    assert TERMINE not in fragiles
    assert ECHEC not in fragiles


def test_comparer_scenarios_ordonne_par_gain():
    """comparer_scenarios trie par gain décroissant et désigne le bon
    étage à optimiser en premier."""
    chaine = {
        "etats": ["A", "B", TERMINE],
        "transitions": {"A": {"B": 1.0},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 30.0, TERMINE: 0.0},
    }
    scenarios = comparer_scenarios(chaine, facteurs=(2.0,))
    assert len(scenarios) == 2
    # B d'abord (gain 37.5 %), puis A (gain 12.5 %).
    assert scenarios[0]["etape"] == "B"
    assert scenarios[0]["gain_relatif"] > scenarios[1]["gain_relatif"]


def test_sejours_moyens_absorbant():
    """Les séjours moyens d'une chaîne linéaire valent les durées."""
    from phi_complexity.markov import sejours_moyens
    chaine = {
        "etats": ["A", "B", TERMINE],
        "transitions": {"A": {"B": 1.0},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 25.0, TERMINE: 0.0},
    }
    res = sejours_moyens(chaine, "A")
    assert res["sejours"]["A"] == pytest.approx(10.0, abs=1e-9)
    assert res["sejours"]["B"] == pytest.approx(25.0, abs=1e-9)
    assert res["total_s"] == pytest.approx(35.0, abs=1e-9)


def test_sejours_moyens_avec_boucle():
    """Avec une boucle A→A (p=0.5), le séjour en A double."""
    from phi_complexity.markov import sejours_moyens
    chaine = {
        "etats": ["A", TERMINE],
        "transitions": {"A": {"A": 0.5, TERMINE: 0.5},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, TERMINE: 0.0},
    }
    # Nombre moyen de visites en A : 1 / 0.5 = 2 → séjour 20 s.
    res = sejours_moyens(chaine, "A")
    assert res["sejours"]["A"] == pytest.approx(20.0, abs=1e-9)


def test_goulot_regime_absorbant():
    """En régime absorbant, le goulot vient des séjours moyens."""
    chaine = {
        "etats": ["A", "B", TERMINE],
        "transitions": {"A": {"B": 1.0},
                        "B": {TERMINE: 1.0},
                        TERMINE: {TERMINE: 1.0}},
        "durees_moyennes": {"A": 10.0, "B": 90.0, TERMINE: 0.0},
    }
    goulot = identifier_goulot(chaine)
    assert goulot["regime"] == "absorbant"
    assert goulot["goulot"] == "B"
    assert goulot["fraction_temps"] == pytest.approx(0.9, abs=1e-9)
