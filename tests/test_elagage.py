"""Tests de l'élagage réel : nomenclature à quatre zones, registre des
IMPOSSIBLE_VALIDÉ, et retrait effectif des directions validées par Lean.

Discipline : docs/DISCIPLINE_ELAGAGE_REEL.md — seul Lean promeut
CANDIDAT_IMPOSSIBLE en IMPOSSIBLE_VALIDÉ.
"""
import json
import os

import pytest

from phi_complexity.chemins_verifiables import (
    _atteignabilite,
    _chemin_registre_impossibles,
    candidats_cablage,
    directions_impossibles,
    est_impossible_valide,
    inscrire_impossible_valide,
    lire_impossibles_valides,
    mesurer_directions,
)


@pytest.fixture
def registre_isole(tmp_path, monkeypatch):
    """Registre redirigé vers un fichier temporaire (jamais ~/.phi réel)."""
    chemin = str(tmp_path / "impossibles_valides.json")
    monkeypatch.setattr(
        "phi_complexity.chemins_verifiables._chemin_registre_impossibles",
        lambda: chemin)
    return chemin


@pytest.fixture
def dossier_elagage(tmp_path):
    (tmp_path / "S.lean").write_text(
        "structure Sol where\n"
        "  u : Nat → Nat\n"
        "  h : 0 < 1\n"
        "  mom : u 0 = 0\n"
        "theorem but (s : Sol) : True := by\n"
        "  sorry\n"
        "theorem cand (u : Nat → Nat) (hm : u 0 = 0)\n"
        "    (hr : ∀ y, ∀ x, P x u) : True :=\n"
        "  trivial\n",
        encoding="utf-8")
    return str(tmp_path)


class TestNomenclature:
    def test_atteignabilite_ne_rend_jamais_impossible_brut(self):
        # Le classifieur statique ne prétend jamais IMPOSSIBLE_VALIDÉ.
        for t1, t2 in [("Nat", "Nat"), ("Nat", "String"), ("Nat", "∀ x, Nat")]:
            zone, _ = _atteignabilite(t1, t2, {})
            assert zone in {"ATTEIGNABLE", "CANDIDAT_IMPOSSIBLE", "INCONNU"}, zone
            assert zone != "IMPOSSIBLE_VALIDÉ"

    def test_mismatch_decisif_est_candidat(self):
        zone, raison = _atteignabilite("Nat", "String", {})
        assert zone == "CANDIDAT_IMPOSSIBLE"
        assert "incompatibles" in raison


class TestRegistre:
    def test_registre_vide_par_defaut(self, registre_isole):
        assert lire_impossibles_valides() == {}
        assert not est_impossible_valide("s", "trou", "terme")

    def test_inscrire_puis_lire(self, registre_isole):
        inscrire_impossible_valide("but", "h", "Nat", "s",
                                   "têtes incompatibles", "Solidite_but_0_0.lean")
        reg = lire_impossibles_valides()
        assert len(reg) == 1
        assert est_impossible_valide("but", "h", "s")
        assert not est_impossible_valide("but", "h", "autre")
        assert not est_impossible_valide("autre_sorry", "h", "s")

    def test_registre_persiste_sur_disque(self, registre_isole):
        inscrire_impossible_valide("but", "h", "Nat", "s", "r", "f.lean")
        data = json.loads(open(registre_isole, encoding="utf-8").read())
        assert len(data) == 1
        fiche = next(iter(data.values()))
        assert fiche["type_trou"] == "Nat"
        assert "date" in fiche

    def test_registre_illisible_ne_leve_pas(self, registre_isole):
        with open(registre_isole, "w", encoding="utf-8") as fh:
            fh.write("pas du json {{{")
        assert lire_impossibles_valides() == {}


class TestElagageReel:
    def test_direction_validee_est_elaguee(self, dossier_elagage, registre_isole):
        # Mesure avant : des CANDIDAT_IMPOSSIBLE ouverts.
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        assert res["statut"] == "TROUVÉ"
        cand = res["candidats"][0]
        avant = cand["directions"]["directions_ouvertes"]
        imposs = cand["impossibles"]
        assert imposs, "le fixture doit produire des CANDIDAT_IMPOSSIBLE"
        cible = imposs[0]
        assert avant > 0

        # Lean tranche (simulé) : on inscrit au registre.
        inscrire_impossible_valide("but", cible["trou"], cible["type_trou"],
                                   cible["terme"], cible["raison"], "f.lean")

        # Mesure après : la direction est IMPOSSIBLE_VALIDÉ, élaguée.
        res2 = candidats_cablage("but", dossier_elagage, max_candidats=1)
        cand2 = res2["candidats"][0]
        apres = cand2["directions"]["directions_ouvertes"]
        assert apres == avant - 1
        # Elle n'est plus proposée à la falsification.
        termes = {(p["trou"], p["terme"]) for p in cand2["impossibles"]}
        assert (cible["trou"], cible["terme"]) not in termes
        # Mais elle reste visible comme validée.
        valides = sum(t.get("IMPOSSIBLE_VALIDÉ", 0)
                      for t in cand2["directions"]["trous"].values())
        assert valides == 1

    def test_sans_sorry_pas_de_registre(self, dossier_elagage, registre_isole):
        # mesurer_directions sans sorry : pas de consultation du registre.
        inscrire_impossible_valide("but", "h", "Nat", "s", "r", "f.lean")
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        assert res["candidats"][0]["directions"]["directions_ouvertes"] > 0
