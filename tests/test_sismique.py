"""
tests/test_sismique.py — Sismique : mémoire des rythmes via l'historique git.

Falsification d'abord : un dépôt git synthétique à l'historique connu ;
chaque mesure (touches, répliques, mécanisme, rayon, magnitude, épicentre)
doit correspondre EXACTEMENT à l'attendu.

Règle constitutionnelle : le sismogramme DÉCRIT (magnitude, profondeur,
épicentre), il ne JUGE jamais — voir tests/test_garde_non_prescriptif.py.
"""
import os
import subprocess

import pytest

from phi_complexity.sismique import (
    analyser,
    rayon_blast,
)


def _git(depot, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        ["git", "-C", depot] + list(args), capture_output=True, text=True,
        env=e, check=True)


def _commit(depot, date_iso, message):
    env = {"GIT_AUTHOR_DATE": date_iso, "GIT_COMMITTER_DATE": date_iso,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    _git(depot, "add", "-A", env=env)
    _git(depot, "commit", "-m", message, "--date", date_iso,
         "--no-gpg-sign", env=env)


def _lignes(n, prefixe="def x"):
    return "".join(f"{prefixe}{i} : Nat := {i}\n" for i in range(n))


@pytest.fixture
def depot(tmp_path):
    d = str(tmp_path / "depot")
    os.mkdir(d)
    _git(d, "init", "-q")
    # C1 : création f1 (100 lignes), f3 (150 lignes), f4 (100 lignes)
    open(os.path.join(d, "f1.lean"), "w").write(_lignes(100, "def a"))
    open(os.path.join(d, "f3.lean"), "w").write(_lignes(150, "def c"))
    open(os.path.join(d, "f4.lean"), "w").write(_lignes(100, "def d"))
    _commit(d, "2026-01-01T10:00:00+00:00", "C1")
    # C2 : f1 +50 lignes
    with open(os.path.join(d, "f1.lean"), "a") as fh:
        fh.write(_lignes(50, "def b"))
    _commit(d, "2026-01-01T12:00:00+00:00", "C2")
    # C3 : f1 -60 lignes (réécriture sans les 60 dernières)
    lignes = open(os.path.join(d, "f1.lean")).readlines()
    open(os.path.join(d, "f1.lean"), "w").write("".join(lignes[:90]))
    _commit(d, "2026-01-01T14:00:00+00:00", "C3")
    # C4 : f2 créé (+200 lignes)
    open(os.path.join(d, "f2.lean"), "w").write(_lignes(200, "def e"))
    _commit(d, "2026-01-02T10:00:00+00:00", "C4")
    # C5 : f3 -100 lignes, f4 +5 lignes
    lignes = open(os.path.join(d, "f3.lean")).readlines()
    open(os.path.join(d, "f3.lean"), "w").write("".join(lignes[:50]))
    with open(os.path.join(d, "f4.lean"), "a") as fh:
        fh.write(_lignes(5, "def f"))
    _commit(d, "2026-01-03T10:00:00+00:00", "C5")
    return d


class TestSismiqueMesures:
    def test_touches(self, depot):
        s = analyser(depot, depuis="2026-01-01")
        par_fichier = {f["fichier"]: f for f in s["fichiers"]}
        assert par_fichier["f1.lean"]["touches"] == 3
        assert par_fichier["f2.lean"]["touches"] == 1
        assert par_fichier["f3.lean"]["touches"] == 2
        assert par_fichier["f4.lean"]["touches"] == 2

    def test_lignes(self, depot):
        s = analyser(depot, depuis="2026-01-01")
        par_fichier = {f["fichier"]: f for f in s["fichiers"]}
        assert par_fichier["f1.lean"]["ajoute"] == 150
        assert par_fichier["f1.lean"]["retire"] == 60
        assert par_fichier["f2.lean"]["ajoute"] == 200
        assert par_fichier["f2.lean"]["retire"] == 0
        assert par_fichier["f3.lean"]["retire"] == 100

    def test_replique(self, depot):
        """f1.lean touché 3 fois en 24h → réplique."""
        s = analyser(depot, depuis="2026-01-01")
        par_fichier = {f["fichier"]: f for f in s["fichiers"]}
        assert par_fichier["f1.lean"]["replique"] is True
        assert par_fichier["f2.lean"]["replique"] is False

    def test_mecanisme(self, depot):
        s = analyser(depot, depuis="2026-01-01")
        par_fichier = {f["fichier"]: f for f in s["fichiers"]}
        assert par_fichier["f2.lean"]["mecanisme"] == "construction"
        assert par_fichier["f1.lean"]["mecanisme"] == "barattage"

    def test_epicentre(self, depot):
        s = analyser(depot, depuis="2026-01-01")
        assert s["epicentre"] == "f1.lean"

    def test_magnitude(self, depot):
        """magnitude = lignes mues × (1 + rayon) ; rayon 0 ici."""
        s = analyser(depot, depuis="2026-01-01")
        par_fichier = {f["fichier"]: f for f in s["fichiers"]}
        assert par_fichier["f1.lean"]["magnitude"] == 210
        assert par_fichier["f2.lean"]["magnitude"] == 200

    def test_pas_depot_git(self, tmp_path):
        with pytest.raises(ValueError):
            analyser(str(tmp_path), depuis="2026-01-01")


class TestSismiqueProfondeur:
    def test_rayon_blast(self, tmp_path):
        d = str(tmp_path)
        open(os.path.join(d, "base.lean"), "w").write(
            "def base : Nat := 1\n")
        open(os.path.join(d, "mid.lean"), "w").write(
            "def mid : Nat := base + 1\n")
        open(os.path.join(d, "top.lean"), "w").write(
            "def top : Nat := mid + 1\n")
        assert rayon_blast(d, "base.lean") == 2
        assert rayon_blast(d, "mid.lean") == 1
        assert rayon_blast(d, "top.lean") == 0
