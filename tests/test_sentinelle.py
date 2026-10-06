"""
tests/test_sentinelle.py — Survie des missions au reboot (2026-10-05).

La sentinelle est le témoin de la continuité : état structuré sur
disque durable, pulsation, détection d'orphelines, brief de reprise.
Elle MONTRE, elle ne décide jamais (garde constitutionnelle).
"""
import json
import os

import pytest

from phi_complexity import sentinelle
from phi_complexity.sentinelle import (
    STATUTS_ITEM,
    _age_s,
    brief_reprise,
    checkpoint,
    est_orpheline,
    init,
    items_a_relancer,
    lire_etat,
    orphelines,
    pulse,
)


def _vieux_iso():
    return "2020-01-01T00:00:00+00:00"


class TestInit:
    def test_init_cree_etat_structure(self, tmp_path):
        terrain = str(tmp_path / "mission-x")
        res = init(terrain, "MISSION-X")
        assert res == {"cree": True, "nom": "MISSION-X"}
        chemin = os.path.join(terrain, ".phi-mission", "MISSION_STATE.json")
        assert os.path.isfile(chemin)
        with open(chemin, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["nom"] == "MISSION-X"
        assert data["items"] == {}
        assert data["cree_iso"] and data["maj_iso"]

    def test_init_idempotent_necrase_jamais(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "i1", "EN-COURS", note="travail")
        res = init(terrain, "M-AUTRE-NOM")
        assert res["cree"] is False
        # L'existant est préservé tel quel, pas écrasé.
        etat = lire_etat(terrain)
        assert etat["nom"] == "M"
        assert etat["items"]["i1"]["statut"] == "EN-COURS"

    def test_lire_etat_absent(self, tmp_path):
        assert lire_etat(str(tmp_path / "inexistant")) is None


class TestCheckpoint:
    def test_checkpoint_statuts_types(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        for statut in STATUTS_ITEM:
            entree = checkpoint(terrain, "item-" + statut, statut)
            assert entree["statut"] == statut
            assert entree["maj_iso"]

    def test_checkpoint_statut_hors_vocabulaire_rejete(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        with pytest.raises(ValueError):
            checkpoint(terrain, "i", "PRESQUE-FINI")

    def test_checkpoint_sans_init_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            checkpoint(str(tmp_path / "m"), "i", "EN-COURS")

    def test_checkpoint_ecrase_proprement(self, tmp_path):
        # Pas de fichier partiel : le JSON reste toujours lisible.
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        for k in range(5):
            checkpoint(terrain, "i", "EN-COURS", note="passe %d" % k)
        chemin = os.path.join(terrain, ".phi-mission", "MISSION_STATE.json")
        with open(chemin, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["items"]["i"]["note"] == "passe 4"
        restes = [n for n in os.listdir(os.path.join(terrain, ".phi-mission"))
                  if n.startswith(".tmp-")]
        assert restes == []


class TestPulse:
    def test_pulse_fraiche_pas_orpheline(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        pulse(terrain, agent="coord-1")
        assert est_orpheline(terrain, seuil_s=1800) is False

    def test_sans_pulse_orpheline(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        assert est_orpheline(terrain) is True

    def test_pulse_expiree_orpheline(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        chemin = os.path.join(terrain, ".phi-mission", "PULSE.json")
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump({"pulse_iso": _vieux_iso(), "agent": "x",
                       "terrain": terrain}, fh)
        assert est_orpheline(terrain, seuil_s=60) is True

    def test_age_illisible(self):
        assert _age_s("pas-une-date") is None
        assert _age_s("") is None


class TestCheck:
    def test_check_trouve_les_orphelines(self, tmp_path):
        racine = str(tmp_path)
        for nom in ("vivante", "morte"):
            t = os.path.join(racine, nom)
            init(t, nom.upper())
        pulse(os.path.join(racine, "vivante"))
        vieux = os.path.join(racine, "morte", ".phi-mission", "PULSE.json")
        with open(vieux, "w", encoding="utf-8") as fh:
            json.dump({"pulse_iso": _vieux_iso(), "agent": "",
                       "terrain": ""}, fh)
        trouvees = orphelines(racine, seuil_s=60)
        assert [t["nom"] for t in trouvees] == ["MORTE"]

    def test_check_racine_absente(self, tmp_path):
        assert orphelines(str(tmp_path / "nulle-part")) == []

    def test_check_ignore_sans_etat(self, tmp_path):
        os.makedirs(str(tmp_path / "pas-une-mission"))
        assert orphelines(str(tmp_path)) == []


class TestReprise:
    def test_brief_liste_a_relancer(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "compile-A", "INTERROMPU", note="reboot 08h01")
        checkpoint(terrain, "rapport-B", "EN-COURS")
        checkpoint(terrain, "nettoyage", "TERMINE")
        pulse(terrain)
        texte = brief_reprise(terrain)
        assert "compile-A" in texte and "rapport-B" in texte
        # Le brief ne prétend jamais que la reprise a eu lieu.
        assert "ne prouve pas que la reprise a eu lieu" in texte

    def test_items_a_relancer(self, tmp_path):
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "a", "EN-COURS")
        checkpoint(terrain, "b", "BLOQUE")
        checkpoint(terrain, "c", "A-REPRENDRE")
        checkpoint(terrain, "d", "TERMINE")
        assert items_a_relancer(terrain) == ["a", "c"]

    def test_brief_sans_etat(self, tmp_path):
        texte = brief_reprise(str(tmp_path / "m"))
        assert "reprise impossible" in texte


def _vieillir_item(terrain, item, iso="2020-01-01T00:00:00+00:00"):
    chemin = os.path.join(terrain, ".phi-mission", "MISSION_STATE.json")
    with open(chemin, encoding="utf-8") as fh:
        data = json.load(fh)
    data["items"][item]["maj_iso"] = iso
    with open(chemin, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class TestDormants:
    def test_dormants_detecte_item_coince_ancien(self, tmp_path):
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "compile-X", "EN-COURS")
        _vieillir_item(terrain, "compile-X")
        trouves = dormants(terrain, seuil_s=60)
        assert [d["item"] for d in trouves] == ["compile-X"]
        assert trouves[0]["statut"] == "EN-COURS"
        assert trouves[0]["age_s"] > 3600
        assert "réveiller" in trouves[0]["action_suggeree"]

    def test_dormants_ignore_termine(self, tmp_path):
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "fini", "TERMINE")
        _vieillir_item(terrain, "fini")
        assert dormants(terrain, seuil_s=60) == []

    def test_dormants_ignore_recent(self, tmp_path):
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "frais", "EN-COURS")
        assert dormants(terrain, seuil_s=3600) == []

    def test_dormants_preuves_carnet(self, tmp_path):
        from phi_complexity import carnet
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "A5", "INTERROMPU")
        _vieillir_item(terrain, "A5")
        carnet.noter(terrain, "w1", "note", "en attente du créneau",
                     item="A5")
        trouves = dormants(terrain, seuil_s=60)
        assert trouves[0]["agents"] == ["w1"]
        assert trouves[0]["derniere_note"] == "en attente du créneau"

    def test_dormants_bloque_suggere_debloquer(self, tmp_path):
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "B2", "BLOQUE", note="verrou fichier")
        _vieillir_item(terrain, "B2")
        trouves = dormants(terrain, seuil_s=60)
        assert "débloquer" in trouves[0]["action_suggeree"]
        assert "verrou fichier" in trouves[0]["action_suggeree"]

    def test_dormants_appels_sans_reponse(self, tmp_path):
        from phi_complexity import journal
        from phi_complexity.sentinelle import dormants
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "C3", "EN-COURS")
        _vieillir_item(terrain, "C3")
        journal.journaliser(terrain, "w2", "appel", {"outil": "exec"},
                            item="C3")
        journal.journaliser(terrain, "w2", "appel", {"outil": "exec"},
                            item="C3")
        journal.journaliser(terrain, "w2", "resultat", {"exit": 0},
                            item="C3")
        trouves = dormants(terrain, seuil_s=60)
        assert trouves[0]["appels_sans_reponse"] == 1

    def test_dormants_sans_etat(self, tmp_path):
        from phi_complexity.sentinelle import dormants, brief_reveil
        assert dormants(str(tmp_path / "m")) == []
        assert "impossible" in brief_reveil(str(tmp_path / "m"))

    def test_brief_reveil_contenu_et_garde(self, tmp_path):
        from phi_complexity import carnet
        from phi_complexity.sentinelle import brief_reveil
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        checkpoint(terrain, "A5", "INTERROMPU", note="reboot")
        _vieillir_item(terrain, "A5")
        carnet.noter(terrain, "w1", "note", "j'attendais le slot", item="A5")
        texte = brief_reveil(terrain, seuil_s=60)
        assert "BRIEF DE RÉVEIL" in texte
        assert "A5" in texte and "w1" in texte
        assert "j'attendais le slot" in texte  # héritage versé
        assert "Mode d'emploi" in texte
        # Garde non-prescriptive : détecte et briefe, ne réveille jamais.
        assert "ne réveille rien seul" in texte
        assert texte.rindex("ne réveille rien seul") > texte.index(
            "Mode d'emploi")

    def test_brief_reveil_sans_dormant(self, tmp_path):
        from phi_complexity.sentinelle import brief_reveil
        terrain = str(tmp_path / "m")
        init(terrain, "M")
        texte = brief_reveil(terrain)
        assert "Aucun item dormant détecté" in texte


class TestCloture:
    """Une mission clôturée (tous items TERMINE) n'est pas orpheline.

    Sans cette distinction, chaque ronde crie à l'orpheline sur les
    missions finies — faux positifs qui noient les vraies alertes.
    """

    def _terrain_pulse_vieille(self, tmp_path, nom="M"):
        from phi_complexity.sentinelle import _dir_etat, FICHIER_ETAT
        terrain = str(tmp_path / nom)
        init(terrain, nom)
        # Pulsation artificiellement vieille (bien au-delà du seuil).
        etat = lire_etat(terrain)
        etat["maj_iso"] = "2020-01-01T00:00:00+00:00"
        chemin = os.path.join(_dir_etat(terrain), FICHIER_ETAT)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(etat, fh)
        return terrain

    def test_mission_cloturee_n_est_pas_orpheline(self, tmp_path):
        from phi_complexity.sentinelle import (
            _dir_etat, FICHIER_ETAT, mission_cloturee)
        terrain = str(tmp_path / "M")
        init(terrain, "M")
        checkpoint(terrain, "i1", "TERMINE", note="fini")
        checkpoint(terrain, "i2", "TERMINE", note="fini")
        # Le checkpoint rafraîchit maj_iso : on revieillit la pulsation.
        etat = lire_etat(terrain)
        etat["maj_iso"] = "2020-01-01T00:00:00+00:00"
        chemin = os.path.join(_dir_etat(terrain), FICHIER_ETAT)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(etat, fh)
        assert mission_cloturee(terrain) is True
        assert est_orpheline(terrain) is False

    def test_mission_non_cloturee_reste_orpheline(self, tmp_path):
        from phi_complexity.sentinelle import _dir_etat, FICHIER_PULSE
        terrain = str(tmp_path / "M")
        init(terrain, "M")
        checkpoint(terrain, "i1", "TERMINE", note="fini")
        checkpoint(terrain, "i2", "EN-COURS", note="en vol")
        # Le checkpoint rafraîchit le pulse (régulateur) : on le revieillit.
        chemin = os.path.join(_dir_etat(terrain), FICHIER_PULSE)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump({"pulse_iso": "2020-01-01T00:00:00+00:00",
                       "agent": "x", "terrain": terrain}, fh)
        assert est_orpheline(terrain) is True

    def test_mission_sans_item_reste_orpheline(self, tmp_path):
        # Aucun item suivi + pulsation vieille : toujours orpheline
        # (pas de clôture à constater).
        terrain = self._terrain_pulse_vieille(tmp_path)
        assert est_orpheline(terrain) is True

    def test_mission_cloturee_absente_du_check(self, tmp_path):
        from phi_complexity.sentinelle import _dir_etat, FICHIER_PULSE
        racine = str(tmp_path)
        for nom, statut in (("finie", "TERMINE"), ("morte", "EN-COURS")):
            terrain = str(tmp_path / nom)
            init(terrain, nom)
            checkpoint(terrain, "i1", statut, note="x")
            # Revieillit la pulsation (le checkpoint l'a rafraîchie).
            chemin = os.path.join(_dir_etat(terrain), FICHIER_PULSE)
            with open(chemin, "w", encoding="utf-8") as fh:
                json.dump({"pulse_iso": "2020-01-01T00:00:00+00:00",
                           "agent": "x", "terrain": terrain}, fh)
        trouvees = orphelines(racine)
        noms = {t["nom"] for t in trouvees}
        assert "finie" not in noms
        assert "morte" in noms


class TestRegulateur:
    """Le régulateur de pulsation natif : extérioriser, c'est vivre.

    Toute écriture au carnet ou au journal rafraîchit la pulsation —
    le cœur n'oublie plus de battre. Un agent vivant mais silencieux
    (MILLENAIRE, 2026-10-05) ne sera plus jamais signalé orphelin.
    """

    def _terrain_vieux_pulse(self, tmp_path, nom="R"):
        from phi_complexity.sentinelle import _dir_etat, FICHIER_PULSE
        terrain = str(tmp_path / nom)
        init(terrain, nom)
        chemin = os.path.join(_dir_etat(terrain), FICHIER_PULSE)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump({"pulse_iso": "2020-01-01T00:00:00+00:00",
                       "agent": "vieux", "terrain": terrain}, fh)
        return terrain

    def test_toucher_rafraichit_le_pulse(self, tmp_path):
        from phi_complexity.sentinelle import toucher, age_pulse_s
        terrain = self._terrain_vieux_pulse(tmp_path)
        assert age_pulse_s(terrain) > 5 * 365 * 24 * 3600
        assert toucher(terrain) is True
        assert age_pulse_s(terrain) < 60

    def test_toucher_conserve_l_agent(self, tmp_path):
        from phi_complexity.sentinelle import toucher, lire_pulse
        terrain = self._terrain_vieux_pulse(tmp_path)
        toucher(terrain)
        assert lire_pulse(terrain)["agent"] == "vieux"

    def test_toucher_sans_etat_ne_cree_rien(self, tmp_path):
        from phi_complexity.sentinelle import toucher
        terrain = str(tmp_path / "fantome")
        assert toucher(terrain) is False
        assert not os.path.exists(
            os.path.join(terrain, ".phi-mission", "PULSE.json"))

    def test_carnet_noter_rafraichit_le_pulse(self, tmp_path):
        from phi_complexity import carnet
        from phi_complexity.sentinelle import age_pulse_s, est_orpheline
        terrain = self._terrain_vieux_pulse(tmp_path)
        assert est_orpheline(terrain) is True
        carnet.noter(terrain, "A-1", "note", "je travaille")
        assert age_pulse_s(terrain) < 60
        assert est_orpheline(terrain) is False

    def test_journal_journaliser_rafraichit_le_pulse(self, tmp_path):
        from phi_complexity import journal
        from phi_complexity.sentinelle import age_pulse_s
        terrain = self._terrain_vieux_pulse(tmp_path)
        journal.journaliser(terrain, "A-1", "note", {"fait": "x"})
        assert age_pulse_s(terrain) < 60

    def test_checkpoint_rafraichit_le_pulse(self, tmp_path):
        from phi_complexity.sentinelle import age_pulse_s
        terrain = self._terrain_vieux_pulse(tmp_path)
        checkpoint(terrain, "i1", "EN-COURS", note="x")
        assert age_pulse_s(terrain) < 60
