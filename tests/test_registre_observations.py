"""
tests/test_registre_observations.py — Registre d'observations : le veto
humain consigné.

Falsification d'abord : la consignation manuelle (observation → décision
veto + motif + date) doit être exacte, horodatée, et refuser les motifs
vides. L'instrument apprend le VOCABULAIRE des situations, jamais la
DÉCISION : aucune fonction ici ne prédit ou ne recommande un veto.

Règle constitutionnelle : voir tests/test_garde_non_prescriptif.py.
"""
import json
import os

import pytest

from phi_complexity.registre_observations import (
    charger,
    consigner,
    lister,
)


OBS = {"kind": "enonce_change", "fichier": "a.lean", "ligne": 1,
       "nom": "foo", "saillance": 10}


def _reg(tmp_path):
    return str(tmp_path / "registre.json")


class TestRegistre:
    def test_consigner_puis_lister(self, tmp_path):
        chemin = _reg(tmp_path)
        entree = consigner(OBS, veto=True,
                           motif="énoncé affaibli pour faire passer la preuve",
                           chemin=chemin)
        assert entree["veto"] is True
        assert entree["observation"]["nom"] == "foo"
        assert "date" in entree
        assert len(entree["motif"]) >= 10
        entrees = lister(chemin)
        assert len(entrees) == 1
        assert entrees[0]["motif"] == entree["motif"]

    def test_veto_non(self, tmp_path):
        chemin = _reg(tmp_path)
        consigner(OBS, veto=False,
                  motif="refactorisation de preuve légitime, énoncé intact",
                  chemin=chemin)
        assert lister(chemin)[0]["veto"] is False

    def test_motif_trop_court_refuse(self, tmp_path):
        with pytest.raises(ValueError):
            consigner(OBS, veto=True, motif="ok",
                      chemin=_reg(tmp_path))

    def test_motif_vide_refuse(self, tmp_path):
        with pytest.raises(ValueError):
            consigner(OBS, veto=False, motif="   ",
                      chemin=_reg(tmp_path))

    def test_persistance_json(self, tmp_path):
        chemin = _reg(tmp_path)
        consigner(OBS, veto=True, motif="motif de test un",
                  chemin=chemin)
        consigner(dict(OBS, nom="bar"), veto=False,
                  motif="motif de test deux", chemin=chemin)
        brut = json.load(open(chemin, encoding="utf-8"))
        assert len(brut) == 2
        assert brut[0]["observation"]["nom"] == "foo"
        assert brut[1]["observation"]["nom"] == "bar"

    def test_charger_fichier_absent(self, tmp_path):
        assert charger(_reg(tmp_path)) == []

    def test_aucune_prediction(self):
        """Le module n'expose aucune fonction de prédiction/recommandation
        de veto : l'instrument apprend le vocabulaire, jamais la décision."""
        import phi_complexity.registre_observations as reg
        noms = [n for n in dir(reg) if not n.startswith("_")]
        for n in noms:
            assert "predi" not in n.lower(), n
            assert "recommand" not in n.lower(), n
            assert "sugg" not in n.lower(), n
