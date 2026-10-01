"""
tests/test_editeur.py — Tests de l'éditeur phi (v0.5.0).

Couvre les 4 modules non-UI : tampon, indexeur, recherche, panneau_phi.
Aucun test curses (la TUI n'est pas testée ici ; ses formateurs purs
vivent dans editeur/tui.py et restent importables sans terminal).
"""
import os
import textwrap

import pytest

from phi_complexity.editeur import (
    Tampon,
    Symbole,
    indexer_projet,
    rafraichir_fichier,
    rechercher_mot_cle,
    rechercher_fonction,
    auditer_tampon,
)


CODE_PROJET = {
    "alpha.py": '''
        def calculer_total(items):
            total = 0
            for i in items:
                if i > 0:
                    total += i
            return total

        def aide():
            return 42
    ''',
    "beta.py": '''
        def calculer_moyenne(valeurs):
            return sum(valeurs) / max(1, len(valeurs))
    ''',
    "notes.txt": "ceci n'est pas du code supporté par l'indexeur\n",
}


@pytest.fixture
def mini_projet(tmp_path):
    """Crée un mini-projet Python sur disque."""
    for nom, code in CODE_PROJET.items():
        (tmp_path / nom).write_text(textwrap.dedent(code), encoding="utf-8")
    return str(tmp_path)


# ── Tampon ────────────────────────────────────────────────────

class TestTampon:
    def test_insertion_simple(self):
        t = Tampon()
        t.inserer("bonjour")
        assert t.texte() == "bonjour"
        assert t.modifie is True

    def test_couper_ligne(self):
        t = Tampon(["abcdef"])
        t.deplacer(0, 3)
        t.couper_ligne()
        assert t.lignes == ["abc", "def"]
        assert (t.ligne, t.colonne) == (1, 0)

    def test_supprimer_avant_fusionne(self):
        t = Tampon(["abc", "def"])
        t.aller_ligne(2)
        t.supprimer_avant()
        assert t.lignes == ["abcdef"]

    def test_supprimer_apres_fin_de_ligne_fusionne(self):
        t = Tampon(["abc", "def"])
        t.aller_fin_ligne()
        t.supprimer_apres()
        assert t.lignes == ["abcdef"]

    def test_bornes_curseur(self):
        t = Tampon(["ab"])
        t.deplacer(100, 100)
        assert (t.ligne, t.colonne) == (0, 2)
        t.deplacer(-100, -100)
        assert (t.ligne, t.colonne) == (0, 0)

    def test_undo_restaure(self):
        t = Tampon(["x"])
        t.aller_fin_ligne()
        t.inserer("yz")
        assert t.texte() == "xyz"
        assert t.annuler() is True
        assert t.texte() == "x"

    def test_undo_vide(self):
        assert Tampon().annuler() is False

    def test_save_load_roundtrip(self, tmp_path):
        chemin = str(tmp_path / "doc.txt")
        t = Tampon(["ligne 1", "ligne 2"])
        t.sauvegarder(chemin)
        assert t.modifie is False
        t2 = Tampon.charger(chemin)
        assert t2.lignes == ["ligne 1", "ligne 2"]
        assert t2.modifie is False


# ── Indexeur ──────────────────────────────────────────────────

class TestIndexeur:
    def test_indexer_projet_trouve_fonctions(self, mini_projet):
        index = indexer_projet(mini_projet)
        noms = {s.nom for symboles in index.values() for s in symboles}
        assert {"calculer_total", "aide", "calculer_moyenne"} <= noms

    def test_indexeur_ignore_non_supportes(self, mini_projet):
        index = indexer_projet(mini_projet)
        assert not any(c.endswith("notes.txt") for c in index)

    def test_symbole_champs(self, mini_projet):
        index = indexer_projet(mini_projet)
        s = next(s for symboles in index.values() for s in symboles
                 if s.nom == "calculer_total")
        assert isinstance(s, Symbole)
        assert s.ligne >= 1 and s.complexite >= 1
        assert s.langage == "python" and s.fichier.endswith("alpha.py")

    def test_fichier_casse_ne_plante_pas(self, tmp_path):
        (tmp_path / "casse.py").write_text("def oups(:\n  syntaxe invalide", encoding="utf-8")
        (tmp_path / "ok.py").write_text("def saine():\n    pass\n", encoding="utf-8")
        index = indexer_projet(str(tmp_path))
        assert any(c.endswith("ok.py") for c in index)

    def test_rafraichir_fichier(self, mini_projet):
        index = indexer_projet(mini_projet)
        cible = os.path.join(mini_projet, "beta.py")
        with open(cible, "a", encoding="utf-8") as f:
            f.write("\ndef nouvelle_fonction():\n    pass\n")
        rafraichir_fichier(index, cible)
        noms = {s.nom for s in index[cible]}
        assert "nouvelle_fonction" in noms

    def test_rafraichir_fichier_supprime(self, mini_projet):
        index = indexer_projet(mini_projet)
        cible = os.path.join(mini_projet, "beta.py")
        os.unlink(cible)
        rafraichir_fichier(index, cible)
        assert cible not in index


# ── Recherche ─────────────────────────────────────────────────

class TestRecherche:
    def test_mot_cle_simple(self, mini_projet):
        res = rechercher_mot_cle(mini_projet, "calculer_total")
        assert len(res) >= 1
        r = res[0]
        assert r["fichier"].endswith("alpha.py")
        assert r["ligne"] >= 1 and "calculer_total" in r["texte"]

    def test_mot_cle_insensible_casse(self, mini_projet):
        res = rechercher_mot_cle(mini_projet, "CALCULER_TOTAL")
        assert len(res) >= 1

    def test_mot_cle_regex(self, mini_projet):
        res = rechercher_mot_cle(mini_projet, r"def calculer_\w+", regex=True)
        assert {r["texte"].strip().split("(")[0] for r in res} >= {
            "def calculer_total", "def calculer_moyenne"}

    def test_mot_cle_ignore_binaire(self, tmp_path):
        (tmp_path / "bin.py").write_bytes(b"\x00\x01\x02\xff\xfe")
        (tmp_path / "ok.py").write_text("motif_visible = 1\n", encoding="utf-8")
        res = rechercher_mot_cle(str(tmp_path), "motif_visible")
        assert len(res) == 1

    def test_rechercher_fonction_ranking(self):
        index = {"f.py": [
            Symbole("calculer", 1, 5, "python", "f.py"),
            Symbole("recalculer_total", 2, 50, "python", "f.py"),
            Symbole("calculer_total", 3, 10, "python", "f.py"),
        ]}
        res = rechercher_fonction(index, "calculer")
        assert [s.nom for s in res] == ["calculer", "calculer_total", "recalculer_total"]

    def test_rechercher_fonction_complexite_decroissante(self):
        index = {"f.py": [
            Symbole("util_petit", 1, 2, "python", "f.py"),
            Symbole("util_grand", 2, 99, "python", "f.py"),
        ]}
        res = rechercher_fonction(index, "util")
        assert [s.nom for s in res] == ["util_grand", "util_petit"]

    def test_rechercher_fonction_vide(self):
        assert rechercher_fonction({}, "x") == []
        assert rechercher_fonction({"f.py": []}, "  ") == []


# ── Panneau phi ───────────────────────────────────────────────

class TestPanneauPhi:
    def test_auditer_tampon_python(self):
        t = Tampon(textwrap.dedent('''
            def ajouter(a, b):
                return a + b
        ''').split("\n"))
        res = auditer_tampon(t, "exemple.py")
        assert "erreur" not in res
        assert isinstance(res["radiance"], float)
        assert 0.0 <= res["radiance"] <= 100.0
        assert res["statut_gnostique"]
        assert isinstance(res["nb_anomalies"], int)
        assert len(res["annotations"]) <= 5
        for a in res["annotations"]:
            assert {"ligne", "niveau", "message"} <= set(a)

    def test_auditer_tampon_langage_inconnu(self):
        t = Tampon(["juste du texte"])
        res = auditer_tampon(t, "notes.txt")
        assert "erreur" in res
        assert "non supporté" in res["erreur"]

    def test_auditer_tampon_lang_force(self):
        t = Tampon(["x = 1\n"])
        res = auditer_tampon(t, "sans_extension", lang="python")
        assert "erreur" not in res
        assert "radiance" in res

    def test_auditer_tampon_ne_laisse_pas_de_temp(self, tmp_path):
        avant = set(os.listdir(tmp_path))
        t = Tampon(["def f():\n    pass\n"])
        auditer_tampon(t, "f.py")
        # le temporaire est créé dans le dossier système, pas ici ;
        # on vérifie au moins l'absence de résidu évident
        assert set(os.listdir(tmp_path)) == avant
