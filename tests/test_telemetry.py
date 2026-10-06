"""Tests de telemetry.py — lecture de l'état interne de l'exporteur v2.

Chaque test vérifie le comportement contre des données connues,
pas contre une autre implémentation.
"""

import json
import os

import pytest

from phi_complexity.telemetry import (
    afficher,
    age_secondes,
    formater_octets,
    lire_telemetry,
    taux_dedup_estime,
    trouver_telemetry,
)


def _donnees_valides():
    return {
        "decls_done": 123000,
        "current_decl": "Mathlib.Analysis.Basic",
        "term_table_size": 800000,
        "str_table_size": 45000,
        "bytes_written": 847923114,
        "dedup_hits": 2400000,
        "timestamp_ms": 1790000000000,
    }


def test_lire_telemetry_valide(tmp_path):
    p = tmp_path / "export_v2.telemetry.json"
    p.write_text(json.dumps(_donnees_valides()), encoding="utf-8")
    donnees, erreur = lire_telemetry(str(p))
    assert erreur is None
    assert donnees["decls_done"] == 123000
    assert donnees["current_decl"] == "Mathlib.Analysis.Basic"


def test_lire_telemetry_json_invalide(tmp_path):
    p = tmp_path / "export_v2.telemetry.json"
    p.write_text("pas du json {{{", encoding="utf-8")
    donnees, erreur = lire_telemetry(str(p))
    assert donnees is None
    assert "JSON invalide" in erreur


def test_lire_telemetry_fichier_absent(tmp_path):
    donnees, erreur = lire_telemetry(str(tmp_path / "nope.json"))
    assert donnees is None
    assert erreur is not None


def test_trouver_telemetry_remonte(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    (tmp_path / "export_v2.telemetry.json").write_text("{}", encoding="utf-8")
    trouve = trouver_telemetry(str(sub))
    assert trouve == str(tmp_path / "export_v2.telemetry.json")


def test_trouver_telemetry_absent(tmp_path):
    assert trouver_telemetry(str(tmp_path)) is None


def test_taux_dedup_estime():
    # 2400000 hits / (2400000 + 800000) = 0.75
    assert taux_dedup_estime(_donnees_valides()) == pytest.approx(0.75)


def test_taux_dedup_estime_donnees_manquantes():
    assert taux_dedup_estime({}) is None
    assert taux_dedup_estime({"dedup_hits": 0, "term_table_size": 0}) is None


def test_formater_octets():
    assert formater_octets(500) == "500 o"
    assert formater_octets(1500000) == "1.5 Mo"
    assert formater_octets(2500000000) == "2.50 Go"
    assert formater_octets("?") == "?"


def test_age_secondes_sans_timestamp():
    assert age_secondes({}) is None


def test_afficher_retourne_zero(tmp_path, capsys):
    p = tmp_path / "export_v2.telemetry.json"
    p.write_text(json.dumps(_donnees_valides()), encoding="utf-8")
    donnees, _ = lire_telemetry(str(p))
    assert afficher(donnees, str(p)) == 0
    out = capsys.readouterr().out
    assert "Mathlib.Analysis.Basic" in out
    assert "123000" in out
