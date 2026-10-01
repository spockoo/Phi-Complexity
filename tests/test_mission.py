"""
tests/test_mission.py — Garde anti-divergence (2026-10-01).

Troisième directive (Tomy) : développer phi-complexity librement SOUS
CONDITION que ça serve la directive unique (démonstration Navier-Stokes
suivant les critères de l'institut Clay).

Garde MÉCANIQUE, pas disciplinaire : `phi_complexity/mission.py` tient
le registre « but de mission » (module → justification + tag `sert`
en vocabulaire contrôlé). Ces tests échouent si :
- un module du paquet n'a pas de justification ;
- une justification est vide/trop courte ou hors vocabulaire ;
- le registre contient une entrée orpheline (module supprimé).
"""
import pytest

from phi_complexity import mission
from phi_complexity.mission import (
    JUSTIFICATIONS,
    SERT_AUTORISES,
    SORRYS_MASTER,
    modules_couverts,
    valider,
)


class TestGardeMission:
    def test_vocabulaire_controle(self):
        """Le vocabulaire couvre les 5 sorrys + les deux tags génériques."""
        for sorry in ("local_existence", "local_uniqueness",
                      "energy_identity", "leray_existence", "BKM_criterion"):
            assert sorry in SERT_AUTORISES
        assert "tous" in SERT_AUTORISES
        assert "instrument" in SERT_AUTORISES
        assert tuple(SORRYS_MASTER) == (
            "local_existence", "local_uniqueness", "energy_identity",
            "leray_existence", "BKM_criterion")

    def test_arbre_reel_conforme(self):
        """L'arbre réel du paquet passe la garde aujourd'hui."""
        problemes = valider()
        assert problemes == [], "\n".join(problemes)

    def test_decouverte_voit_modules_et_sous_paquets(self):
        modules = modules_couverts()
        assert "veille" in modules
        assert "sondes" in modules
        assert "croyances" in modules
        assert "langs" in modules      # sous-paquet : l'analyseur Lean
        assert "editeur" in modules
        assert not any(m.startswith("__") for m in modules)
        assert "__pycache__" not in modules

    def test_garde_attrape_module_sans_justification(self):
        """Un module ajouté sans entrée registre fait échouer la garde."""
        modules = modules_couverts() + ["module_fantome_xyz"]
        problemes = valider(modules)
        sans_justif = [p for p in problemes if "SANS justification" in p]
        assert len(sans_justif) == 1
        assert "module_fantome_xyz" in sans_justif[0]

    def test_garde_attrape_entree_orpheline(self):
        """Une entrée registre pour un module inexistant est signalée."""
        problemes = valider([])
        assert problemes, "la garde devrait signaler les entrées orphelines"
        assert all("orpheline" in p for p in problemes)
        assert len(problemes) == len(JUSTIFICATIONS)

    def test_garde_attrape_sert_hors_vocabulaire(self, monkeypatch):
        """Un tag `sert` inventé est rejeté."""
        faux = dict(JUSTIFICATIONS)
        faux["veille"] = {"but": "x" * 30, "sert": "magie"}
        monkeypatch.setattr(mission, "JUSTIFICATIONS", faux)
        problemes = valider(["veille"])
        assert any("hors vocabulaire" in p for p in problemes)

    def test_garde_attrape_justification_trop_courte(self, monkeypatch):
        """Une justification de remplissage est rejetée."""
        faux = dict(JUSTIFICATIONS)
        faux["veille"] = {"but": "utile", "sert": "tous"}
        monkeypatch.setattr(mission, "JUSTIFICATIONS", faux)
        problemes = valider(["veille"])
        assert any("trop courte" in p for p in problemes)

    def test_toutes_justifications_non_vides(self):
        """Chaque entrée réelle porte une justification substantielle."""
        for mod, entree in JUSTIFICATIONS.items():
            assert len(entree["but"].strip()) >= 20, mod
            sert = entree["sert"]
            valeurs = list(sert) if isinstance(sert, (list, tuple)) else [sert]
            for v in valeurs:
                assert v in SERT_AUTORISES, (mod, v)
