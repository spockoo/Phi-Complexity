"""
tests/test_carte.py — Tests de la Carte du projet (v0.6.0).

`phi index` / `carte_projet` : structure stable, détection des collisions,
oudjat suprême, robustesse aux fichiers cassés, sortie console.
"""
import json
import textwrap

import pytest

from phi_complexity import carte_projet
from phi_complexity.carte import carte_console


CODE_A = """
def alpha(x):
    return x + 1

def beta(x):
    if x > 0:
        return x
    return -x
"""

CODE_B = """
def beta(y):
    total = 0
    for i in range(y):
        for j in range(i):
            total += j
    return total

def gamma():
    return 42
"""

CODE_CASSE = "def oups(:\n  ceci n'est pas du python valide\n"

CLES_STABLES = {
    "dossier", "version_phi", "nb_fichiers", "nb_symboles",
    "radiance_globale", "statut_gnostique_global",
    "fichiers", "collisions", "oudjat_supreme",
    "non_supportes", "avertissements",  # v0.6.1 : l'instrument dit ses angles morts
    # v0.8.0 : en mode complet, le schéma est EXACTEMENT celui-ci (v0.7.0).
    # La clé "mode"="rapide" n'existe qu'en phase 1 (testée dans test_vitesse).
}


@pytest.fixture
def mini_projet(tmp_path):
    (tmp_path / "a.py").write_text(textwrap.dedent(CODE_A), encoding="utf-8")
    (tmp_path / "b.py").write_text(textwrap.dedent(CODE_B), encoding="utf-8")
    (tmp_path / "casse.py").write_text(CODE_CASSE, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("pas un fichier source", encoding="utf-8")
    return str(tmp_path)


class TestStructure:
    def test_cles_stables(self, mini_projet):
        """La carte expose exactement les clés stables documentées."""
        carte = carte_projet(mini_projet)
        assert set(carte.keys()) == CLES_STABLES

    def test_compteurs(self, mini_projet):
        """2 fichiers valides indexés ; le .txt et le fichier cassé sont ignorés."""
        carte = carte_projet(mini_projet)
        assert carte["nb_fichiers"] == 2
        assert carte["nb_symboles"] == 4  # alpha, beta(a), beta(b), gamma
        noms_fichiers = [f["fichier"] for f in carte["fichiers"]]
        assert not any("casse" in n for n in noms_fichiers)
        assert not any(n.endswith(".txt") for n in noms_fichiers)

    def test_fichier_casse_ne_plante_pas(self, mini_projet):
        """Un fichier à syntaxe cassée est ignoré sans lever d'exception."""
        carte = carte_projet(mini_projet)  # ne doit pas lever
        assert carte["nb_fichiers"] == 2

    def test_json_serialisable(self, mini_projet):
        """La carte passe json.dumps / json.loads sans perte (jq-compatible)."""
        carte = carte_projet(mini_projet)
        texte = json.dumps(carte, ensure_ascii=False, indent=2)
        rechargee = json.loads(texte)
        assert set(rechargee.keys()) == CLES_STABLES
        assert rechargee["nb_symboles"] == 4

    def test_dossier_vide(self, tmp_path):
        """Dossier vide → compteurs à zéro, pas de plantage."""
        carte = carte_projet(str(tmp_path))
        assert carte["nb_fichiers"] == 0
        assert carte["nb_symboles"] == 0
        assert carte["collisions"] == []
        assert carte["oudjat_supreme"] is None

    def test_dossier_inexistant(self, tmp_path):
        """Dossier inexistant → carte vide, pas d'exception."""
        carte = carte_projet(str(tmp_path / "n_existe_pas"))
        assert carte["nb_fichiers"] == 0


class TestCollisions:
    def test_collision_beta_detectee(self, mini_projet):
        """`beta` défini dans a.py et b.py → une collision à 2 occurrences."""
        carte = carte_projet(mini_projet)
        assert len(carte["collisions"]) == 1
        collision = carte["collisions"][0]
        assert collision["nom"] == "beta"
        assert collision["nb_occurrences"] == 2
        assert collision["nb_fichiers"] == 2
        fichiers = {o["fichier"] for o in collision["occurrences"]}
        assert len(fichiers) == 2
        for occ in collision["occurrences"]:
            assert {"fichier", "ligne", "complexite"} <= set(occ.keys())

    def test_pas_de_collision_sans_doublon(self, tmp_path):
        """Sans nom partagé entre fichiers → aucune collision."""
        (tmp_path / "x.py").write_text("def unique_un():\n    return 1\n", encoding="utf-8")
        (tmp_path / "y.py").write_text("def unique_deux():\n    return 2\n", encoding="utf-8")
        carte = carte_projet(str(tmp_path))
        assert carte["collisions"] == []


class TestOudjatSupreme:
    def test_oudjat_supreme_est_le_max(self, mini_projet):
        """L'oudjat suprême porte la complexité maximale des symboles."""
        carte = carte_projet(mini_projet)
        sup = carte["oudjat_supreme"]
        assert sup is not None
        complexites = [
            s["complexite"]
            for f in carte["fichiers"]
            for s in f["symboles"]
        ]
        assert sup["complexite"] == max(complexites)
        assert {"nom", "fichier", "ligne", "complexite"} <= set(sup.keys())

    def test_oudjat_supreme_est_beta_de_b(self, mini_projet):
        """Le `beta` aux boucles imbriquées (b.py) est le plus complexe."""
        carte = carte_projet(mini_projet)
        sup = carte["oudjat_supreme"]
        assert sup["nom"] == "beta"
        assert sup["fichier"].endswith("b.py")


class TestConsole:
    def test_console_non_vide_et_sections(self, mini_projet):
        """La sortie console contient les sections attendues."""
        sortie = carte_console(carte_projet(mini_projet))
        assert "CARTE DU PROJET" in sortie
        assert "TABLEAU DES MODULES" in sortie
        assert "COLLISIONS" in sortie
        assert "beta" in sortie
        assert "OUDJAT SUPRÊME" in sortie

    def test_console_sans_collision(self, tmp_path):
        """Sans collision, la console l'annonce explicitement."""
        (tmp_path / "x.py").write_text("def solo():\n    return 1\n", encoding="utf-8")
        sortie = carte_console(carte_projet(str(tmp_path)))
        assert "Aucune collision" in sortie


class TestSantePhi:
    def test_radiance_et_statut_par_fichier(self, mini_projet):
        """Chaque fichier porte radiance, statut gnostique et oudjat."""
        carte = carte_projet(mini_projet)
        for f in carte["fichiers"]:
            assert 0.0 <= f["radiance"] <= 100.0
            assert f["statut_gnostique"] in ("HERMÉTIQUE ✦", "EN ÉVEIL ◈", "DORMANT ░")
            assert f["langage"] == "python"
            assert isinstance(f["symboles"], list)

    def test_radiance_globale_coherente(self, mini_projet):
        """La radiance globale est la moyenne des radiances de fichiers."""
        carte = carte_projet(mini_projet)
        moyenne = sum(f["radiance"] for f in carte["fichiers"]) / carte["nb_fichiers"]
        assert abs(carte["radiance_globale"] - round(moyenne, 2)) < 0.01
