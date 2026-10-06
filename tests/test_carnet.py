"""
tests/test_carnet.py — La boîte noire des raisonnements (2026-10-05).

Le carnet sauve la MÉMOIRE de travail des agents (où chacun en était,
ce qu'il avait conclu, ce qu'il allait faire) quand un reboot tue les
sessions. Append-only JSONL par agent, rendu .md généré à la demande,
mémoire de reprise versée au brief sentinelle.

Limite honnête testée ici : le carnet ne sauve que l'extériorisé —
il ne rejoue jamais rien (garde non-prescriptive).
"""
import json
import os

import pytest

from phi_complexity import carnet
from phi_complexity.carnet import (
    TYPES_EVENEMENT,
    MSG_MAX,
    agents,
    archiver,
    compter,
    lire,
    noter,
    rendre_md,
    resume_reprise,
)


def _flux(terrain, agent):
    return os.path.join(terrain, ".phi-mission", "CARNET", agent + ".jsonl")


class TestNoter:
    def test_noter_cree_flux_jsonl(self, tmp_path):
        terrain = str(tmp_path / "m")
        e = noter(terrain, "coord-1", "note", "où j'en suis : passe 3")
        assert e["type"] == "note"
        assert e["a"] == "coord-1"
        assert e["t"] and e["msg"] == "où j'en suis : passe 3"
        # Clés courtes (taille minimale des fichiers).
        assert set(e) <= {"t", "a", "item", "type", "msg", "tags", "x"}
        with open(_flux(terrain, "coord-1"), encoding="utf-8") as fh:
            lignes = fh.readlines()
        assert len(lignes) == 1
        assert json.loads(lignes[0])["msg"] == "où j'en suis : passe 3"

    def test_noter_types_vocabulaire(self, tmp_path):
        terrain = str(tmp_path / "m")
        for t in TYPES_EVENEMENT:
            noter(terrain, "a1", t, "msg " + t)
        assert compter(terrain, agent="a1") == len(TYPES_EVENEMENT)

    def test_noter_type_hors_vocabulaire_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            noter(str(tmp_path / "m"), "a1", "pensee", "x")

    def test_noter_message_vide_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            noter(str(tmp_path / "m"), "a1", "note", "   ")

    def test_noter_tronque_les_longs_messages(self, tmp_path):
        terrain = str(tmp_path / "m")
        e = noter(terrain, "a1", "note", "x" * (MSG_MAX + 100))
        assert len(e["msg"]) == MSG_MAX + len("…[tronqué]")
        assert e["msg"].endswith("…[tronqué]")

    def test_noter_item_tags_contexte(self, tmp_path):
        terrain = str(tmp_path / "m")
        e = noter(terrain, "a1", "decision", "on garde la voie A",
                  item="voie-A", tags=["strategie", "t1"],
                  contexte={"pourquoi": "mesures"})
        assert e["item"] == "voie-A"
        assert e["tags"] == ["strategie", "t1"]
        assert e["x"] == {"pourquoi": "mesures"}

    def test_noter_contexte_non_dict_rejete(self, tmp_path):
        with pytest.raises(ValueError):
            noter(str(tmp_path / "m"), "a1", "note", "x", contexte=[1, 2])

    def test_noter_append_only(self, tmp_path):
        # Deuxième note = ajout, jamais réécriture : la première survit.
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "première")
        noter(terrain, "a1", "note", "deuxième")
        msgs = [e["msg"] for e in lire(terrain, agent="a1")]
        assert msgs == ["première", "deuxième"]


class TestLire:
    def test_lire_tous_agents_tries(self, tmp_path):
        # Ordre chronologique même si les lignes sont écrites dans le
        # désordre (horodatages distincts injectés à la main).
        terrain = str(tmp_path / "m")
        chemin = _flux(terrain, "b")
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        for t, a, m in (("2026-10-05T00:00:02+00:00", "b", "second"),
                        ("2026-10-05T00:00:01+00:00", "a", "premier")):
            with open(chemin, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": t, "a": a, "item": "",
                                     "type": "note", "msg": m,
                                     "tags": []}) + "\n")
        entrees = lire(terrain)
        assert [e["msg"] for e in entrees] == ["premier", "second"]
        assert agents(terrain) == ["b"]

    def test_lire_n_derniers(self, tmp_path):
        terrain = str(tmp_path / "m")
        for i in range(5):
            noter(terrain, "a1", "note", "n%d" % i)
        assert [e["msg"] for e in lire(terrain, n=2)] == ["n3", "n4"]

    def test_lire_filtre_types(self, tmp_path):
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "n")
        noter(terrain, "a1", "erreur", "e")
        assert [e["type"] for e in lire(terrain, types=("erreur",))] == ["erreur"]

    def test_lire_ignore_ligne_corrompue(self, tmp_path):
        # Reboot en pleine écriture : la dernière ligne tronquée est
        # ignorée, le carnet survit à sa dernière ligne.
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "saine")
        with open(_flux(terrain, "a1"), "a", encoding="utf-8") as fh:
            fh.write('{"t": "2026-10-05T00:00:00+00:00", "type": "note", "msg": "tronq')
        entrees = lire(terrain, agent="a1")
        assert [e["msg"] for e in entrees] == ["saine"]

    def test_lire_ignore_entree_hors_vocabulaire(self, tmp_path):
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "saine")
        with open(_flux(terrain, "a1"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": "x", "a": "a1", "type": "reve",
                                 "msg": "intruse"}) + "\n")
        assert [e["msg"] for e in lire(terrain, agent="a1")] == ["saine"]

    def test_lire_terrain_vide(self, tmp_path):
        assert lire(str(tmp_path / "nulle-part")) == []
        assert agents(str(tmp_path / "nulle-part")) == []


class TestRendreMd:
    def test_rendre_md_genere_vue_lisible(self, tmp_path):
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "où j'en suis", item="p3",
              tags=["t1"])
        noter(terrain, "a1", "trouvee", "lemme prouvé")
        md = rendre_md(terrain)
        assert md.startswith("# Carnet")
        assert "## Agent `a1`" in md
        assert "où j'en suis" in md and "lemme prouvé" in md
        assert "#t1" in md and "item `p3`" in md

    def test_rendre_md_ne_stocke_rien(self, tmp_path):
        # Le .md est un rendu, pas une source : aucun fichier .md créé.
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "x")
        rendre_md(terrain)
        md_files = []
        for racine, _, noms in os.walk(terrain):
            md_files += [n for n in noms if n.endswith(".md")]
        assert md_files == []


class TestResumeReprise:
    def test_resume_mememoire_par_agent(self, tmp_path):
        terrain = str(tmp_path / "m")
        noter(terrain, "coord", "note", "j'allais relancer A5")
        noter(terrain, "w1", "erreur", "timeout lake", item="A5")
        texte = resume_reprise(terrain)
        assert "coord" in texte and "w1" in texte
        assert "j'allais relancer A5" in texte
        assert "timeout lake" in texte

    def test_resume_sans_carnet(self, tmp_path):
        texte = resume_reprise(str(tmp_path / "m"))
        assert "aucune mémoire de travail" in texte

    def test_resume_ne_rejoue_rien(self, tmp_path):
        # Garde non-prescriptive : le résumé montre, il ne relance pas.
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "en plein milieu de X")
        texte = resume_reprise(terrain)
        assert "ne rejoue rien" in texte
        for verbe in ("relance", "redémarre", "exécute"):
            assert verbe not in texte.lower()


class TestArchiver:
    def test_archiver_compacte_le_flux(self, tmp_path):
        terrain = str(tmp_path / "m")
        for i in range(10):
            noter(terrain, "a1", "note", "n%d" % i)
        res = archiver(terrain, "a1", garder=3)
        assert res["archivees"] == 7 and res["gardees"] == 3
        assert res["archive"].endswith(".jsonl")
        assert os.path.isfile(res["archive"])
        assert [e["msg"] for e in lire(terrain, agent="a1")] == [
            "n7", "n8", "n9"]

    def test_archiver_petit_flux_rien_a_faire(self, tmp_path):
        terrain = str(tmp_path / "m")
        noter(terrain, "a1", "note", "x")
        res = archiver(terrain, "a1", garder=200)
        assert res == {"archive": None, "gardees": 1, "archivees": 0}

    def test_archiver_agent_inconnu(self, tmp_path):
        res = archiver(str(tmp_path / "m"), "fantome")
        assert res["archive"] is None


class TestIntegrationSentinelle:
    def test_brief_reprise_inclut_le_carnet(self, tmp_path):
        # La mémoire de travail est versée au brief de reprise.
        from phi_complexity import sentinelle
        terrain = str(tmp_path / "m")
        sentinelle.init(terrain, "M")
        sentinelle.checkpoint(terrain, "A5", "INTERROMPU")
        noter(terrain, "coord", "note", "A5 à reprendre : passe 4 en vol")
        texte = sentinelle.brief_reprise(terrain)
        assert "A5 à reprendre" in texte
        assert "Carnet" in texte
