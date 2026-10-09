"""Tests de l'analyse d'impact native intégrée à `phi sonde` (PHI-NATIF-B).

Paquet synthétique hermétique (jamais le dépôt réel) : rapide et
déterministe. Vérifie : résolution des symboles, structure du résultat,
dégradation gracieuse, non-régression du rendu/JSON.
"""

import os

import pytest

from phi_complexity.impact import GrapheDependances
from phi_complexity.impact_sonde import (
    analyser_impact,
    racine_impact_par_defaut,
    trouver_symboles,
)
from phi_complexity.sondes import ResultatSonde, rendre_sonde_console


@pytest.fixture()
def paquet(tmp_path):
    (tmp_path / "a.py").write_text(
        "def cible(x):\n"
        "    return x + 1\n"
        "\n"
        "class Conteneur:\n"
        "    def methode(self):\n"
        "        return cible(1)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from a import cible\n"
        "\n"
        "def usage():\n"
        "    return cible(41)\n",
        encoding="utf-8",
    )
    return str(tmp_path)


@pytest.fixture()
def graphe(paquet):
    return GrapheDependances.depuis_repertoire(paquet)


# ── résolution des symboles ──────────────────────────────────────────

def test_trouver_qualname(graphe):
    trouves = trouver_symboles(graphe, "cible")
    assert trouves and trouves[0] == "a.py:cible"


def test_trouver_nom_pointe(graphe):
    assert trouver_symboles(graphe, "a.cible")[0] == "a.py:cible"


def test_trouver_methode_classe(graphe):
    assert trouver_symboles(graphe, "Conteneur.methode") == [
        "a.py:Conteneur.methode"]


def test_trouver_inconnu_vide(graphe):
    assert trouver_symboles(graphe, "H29") == []
    assert trouver_symboles(graphe, "") == []


# ── analyse complète ─────────────────────────────────────────────────

def test_analyser_impact_structure(paquet, graphe):
    r = analyser_impact("cible", racine=paquet, graphe=graphe)
    assert r["desactive"] is False
    assert r["avertissement"] is None
    assert r["noeud"] == "a.py:cible"
    assert r["symbole_recherche"] == "cible"
    assert r["racine"] == os.path.abspath(paquet)
    assert r["graphe"]["noeuds"] > 0
    dep = r["dependants"]
    assert dep["nombre"] >= 1  # b.py:usage (+ a.py:Conteneur.methode)
    assert any("b.py:usage" in i for i in dep["liste"])
    ris = r["risque"]
    assert 0.0 <= ris["score"] <= 100.0
    assert ris["niveau"] in {"FAIBLE", "MOYEN", "ELEVE", "CRITIQUE"}
    assert set(ris["facteurs"]) == {"D", "P", "T", "C"}


def test_analyser_impact_graphe_construit_une_fois(paquet):
    # Sans graphe fourni : construction interne, même résultat.
    r = analyser_impact("cible", racine=paquet)
    assert r["noeud"] == "a.py:cible"
    assert r["dependants"]["nombre"] >= 1


def test_analyser_impact_symbole_inconnu(paquet, graphe):
    # Dégradation gracieuse : pas d'exception, avertissement explicite.
    r = analyser_impact("H29", racine=paquet, graphe=graphe)
    assert r["noeud"] is None
    assert r["avertissement"] is not None
    assert "H29" in r["avertissement"]
    assert r["desactive"] is False  # le graphe, lui, a bien été construit


def test_analyser_impact_racine_invalide():
    r = analyser_impact("cible", racine="/tmp/phi-inexistant-xyz-123")
    assert r["desactive"] is True
    assert r["avertissement"] is not None


def test_racine_par_defaut_est_le_paquet():
    racine = racine_impact_par_defaut()
    assert os.path.isdir(racine)
    assert os.path.basename(racine) == "phi_complexity"


# ── intégration ResultatSonde (JSON + console) ───────────────────────

def test_vers_dict_contient_impact(paquet, graphe):
    res = ResultatSonde(mecanisme="cible", type_mecanisme="inconnu")
    res.impact = analyser_impact("cible", racine=paquet, graphe=graphe)
    d = res.vers_dict()
    assert d["impact"]["noeud"] == "a.py:cible"
    assert "risque" in d["impact"]


def test_vers_dict_impact_vide_par_defaut():
    # --sans-impact : clé présente mais vide, jamais de crash.
    d = ResultatSonde(mecanisme="x", type_mecanisme="inconnu").vers_dict()
    assert d["impact"] == {}


def test_console_section_impact(paquet, graphe):
    res = ResultatSonde(mecanisme="cible", type_mecanisme="inconnu")
    res.impact = analyser_impact("cible", racine=paquet, graphe=graphe)
    texte = rendre_sonde_console(res)
    assert "IMPACT" in texte
    assert "a.py:cible" in texte
    assert "Score de risque" in texte


def test_console_sans_impact_pas_de_section():
    res = ResultatSonde(mecanisme="x", type_mecanisme="inconnu")
    assert "IMPACT" not in rendre_sonde_console(res)


def test_console_avertissement_symbole_inconnu(paquet, graphe):
    res = ResultatSonde(mecanisme="H29", type_mecanisme="hypothese")
    res.impact = analyser_impact("H29", racine=paquet, graphe=graphe)
    assert "aucun symbole Python" in rendre_sonde_console(res)
