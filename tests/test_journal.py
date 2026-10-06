"""
tests/test_journal.py — Journal d'activité et empreinte résiduelle (2026-10-05).

Le journal (WAL) trace chaque appel d'outil ; à l'exception, le
mécanisme imprime l'empreinte résiduelle puis re-lève. Devoir des
gardiens : vérifier les empreintes, pas les produire.
Garantie : aucune perte sans trace.
"""
import json
import os

import pytest

from phi_complexity import journal
from phi_complexity.journal import (
    TYPES_JOURNAL,
    agents,
    empreinte_residuelle,
    empreintes,
    journaliser,
    lire,
    lire_empreinte,
    proteger,
    verifier_empreintes,
)


class TestJournaliser:
    def test_journaliser_roundtrip(self, tmp_path):
        terrain = str(tmp_path / "m")
        e = journaliser(terrain, "a1", "appel",
                        {"outil": "exec", "cmd": "lake env lean x.lean"},
                        item="A5")
        assert e["type"] == "appel" and e["a"] == "a1"
        assert e["item"] == "A5"
        assert set(e) <= {"t", "a", "item", "type", "d"}
        lus = lire(terrain, agent="a1")
        assert len(lus) == 1 and lus[0]["d"]["outil"] == "exec"

    def test_journaliser_type_hors_vocabulaire_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            journaliser(str(tmp_path / "m"), "a1", "pensee", {})

    def test_journaliser_detail_non_dict_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            journaliser(str(tmp_path / "m"), "a1", "appel", [1, 2])

    def test_lire_ignore_ligne_corrompue(self, tmp_path):
        terrain = str(tmp_path / "m")
        journaliser(terrain, "a1", "appel", {"x": 1})
        chemin = os.path.join(terrain, ".phi-mission", "JOURNAL", "a1.jsonl")
        with open(chemin, "a", encoding="utf-8") as fh:
            fh.write('{"t": "tronquee\n')
        assert len(lire(terrain, agent="a1")) == 1

    def test_lire_filtre_types(self, tmp_path):
        terrain = str(tmp_path / "m")
        journaliser(terrain, "a1", "appel", {})
        journaliser(terrain, "a1", "resultat", {"exit": 0})
        assert [e["type"] for e in lire(terrain, types=("resultat",))] == [
            "resultat"]

    def test_agents(self, tmp_path):
        terrain = str(tmp_path / "m")
        journaliser(terrain, "a1", "appel", {})
        journaliser(terrain, "b2", "appel", {})
        assert agents(terrain) == ["a1", "b2"]


class TestEmpreinte:
    def test_empreinte_imprimee_mecaniquement(self, tmp_path):
        terrain = str(tmp_path / "m")
        emp = empreinte_residuelle(
            terrain, "a1", item="A5",
            dernier_appel={"outil": "exec", "cmd": "lake env lean A.lean"},
            dernier_resultat={"exit": 1},
            erreur="RuntimeError: boom",
            note="contexte")
        assert emp["imprimee"] is True
        chemin = os.path.join(terrain, ".phi-mission", "JOURNAL",
                              "EMPREINTE_a1.json")
        with open(chemin, encoding="utf-8") as fh:
            disque = json.load(fh)
        assert disque["erreur"] == "RuntimeError: boom"
        assert disque["dernier_appel"]["cmd"] == "lake env lean A.lean"
        # + entrée au journal.
        assert lire(terrain, types=("empreinte",))[0]["a"] == "a1"

    def test_lire_empreinte_absente(self, tmp_path):
        assert lire_empreinte(str(tmp_path / "m"), "fantome") is None
        assert empreintes(str(tmp_path / "m")) == []

    def test_empreinte_ne_masque_jamais_l_erreur(self, tmp_path):
        # Même si l'impression échoue, pas d'exception : l'empreinte ne
        # doit jamais masquer l'erreur d'origine.
        terrain = str(tmp_path / "m")
        emp = empreinte_residuelle(terrain, "a1", erreur="x")
        assert isinstance(emp, dict) and "imprimee" in emp


class TestProteger:
    def test_proteger_sortie_normale(self, tmp_path):
        terrain = str(tmp_path / "m")
        with proteger(terrain, "a1", item="A5") as p:
            p.noter_appel({"outil": "exec"})
            p.noter_resultat({"exit": 0})
        types = [e["type"] for e in lire(terrain, agent="a1")]
        assert types == ["note", "appel", "resultat", "note"]
        assert empreintes(terrain) == []  # pas d'échec → pas d'empreinte

    def test_proteger_imprime_et_releve(self, tmp_path):
        terrain = str(tmp_path / "m")
        with pytest.raises(RuntimeError, match="boom"):
            with proteger(terrain, "a1", item="A5") as p:
                p.noter_appel({"outil": "exec", "cmd": "compile"})
                raise RuntimeError("boom")
        # L'exception a été re-levée (le mécanisme ne répare pas)...
        # ...mais l'empreinte a été imprimée mécaniquement avant.
        emp = lire_empreinte(terrain, "a1")
        assert emp is not None and emp["imprimee"] is True
        assert emp["dernier_appel"] == {"outil": "exec", "cmd": "compile"}
        assert "RuntimeError: boom" in emp["erreur"]
        assert emp["item"] == "A5"


class TestDevoirGardiens:
    def test_verifier_empreintes(self, tmp_path):
        terrain = str(tmp_path / "m")
        journaliser(terrain, "a1", "appel", {})
        journaliser(terrain, "b2", "appel", {})
        empreinte_residuelle(terrain, "a1", item="A5", erreur="boom")
        verif = verifier_empreintes(terrain)
        assert verif["agents_journalises"] == ["a1", "b2"]
        assert len(verif["empreintes"]) == 1
        assert verif["empreintes"][0]["a"] == "a1"
        assert verif["orphelines_sans_journal"] == []
        # Le gardien montre : rendu texte.
        texte = journal.rendre_verification_console(verif)
        assert "a1" in texte and "boom" in texte

    def test_verifier_detecte_orpheline(self, tmp_path):
        # Empreinte sans journal (cas limite) : signalée, pas cachée.
        terrain = str(tmp_path / "m")
        dossier = os.path.join(terrain, ".phi-mission", "JOURNAL")
        os.makedirs(dossier, exist_ok=True)
        with open(os.path.join(dossier, "EMPREINTE_z9.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"t": "x", "a": "z9", "item": "", "erreur": "y"},
                      fh)
        verif = verifier_empreintes(terrain)
        assert verif["orphelines_sans_journal"] == ["z9"]


class TestIntegrationSentinelle:
    def test_brief_reprise_mentionne_empreintes(self, tmp_path):
        from phi_complexity import sentinelle
        terrain = str(tmp_path / "m")
        sentinelle.init(terrain, "M")
        empreinte_residuelle(terrain, "w3", item="A9", erreur="timeout")
        texte = sentinelle.brief_reprise(terrain)
        assert "Empreintes résiduelles" in texte
        assert "w3" in texte
