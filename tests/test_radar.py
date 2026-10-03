"""
tests/test_radar.py — Radar : vue du mouvement structurel entre deux états.

Falsification d'abord : les fixtures ci-dessous définissent un AVANT et un
APRÈS synthétiques ; chaque détecteur doit produire EXACTEMENT les
observations listées (kind, nom, saillance, ordre). Si un détecteur rate
ou invente une observation, le test échoue honnêtement.

Règle constitutionnelle : aucune observation ne porte de jugement —
le radar MONTRE (faits structurels), il ne DÉCIDE jamais.
"""
import os

import pytest

from phi_complexity.radar import (
    Observation,
    comparer,
    scanner,
)


AVANT_A = """\
theorem foo : 1 = 1 := by rfl
def bar : Nat := 42
theorem stable : True := trivial
theorem longproof : 1 = 1 := by
  simp
  rw [Nat.add_comm]
  rfl
  omega
  decide
  simp
  rfl
"""

APRES_A = """\
theorem foo : 2 = 2 := by rfl
def bar : Nat := 43
theorem stable : True := trivial
theorem longproof : 2 = 2 := by trivial
"""

AVANT_B = "def dup : Nat := 1\n"
APRES_B = "def dup : Nat := 1\n"
AVANT_C = "def dup : Nat := 2\n"
APRES_C = "def dup : Nat := 2\n"

AVANT_D = "theorem avec_sorry : 1 = 1 := by sorry\n"
APRES_D = ("theorem avec_sorry : 1 = 1 := by sorry\n"
           "theorem nouveau_sorry : 2 = 2 := by sorry\n")

AVANT_F = "def olddef : Nat := 7\n"
APRES_G = "def newdef : Nat := 8\n"
APRES_E = "axiom mon_axiome : True\n"


@pytest.fixture
def arborescence(tmp_path):
    avant = tmp_path / "avant"
    apres = tmp_path / "apres"
    avant.mkdir()
    apres.mkdir()
    (avant / "a.lean").write_text(AVANT_A, encoding="utf-8")
    (apres / "a.lean").write_text(APRES_A, encoding="utf-8")
    (avant / "b.lean").write_text(AVANT_B, encoding="utf-8")
    (apres / "b.lean").write_text(APRES_B, encoding="utf-8")
    (avant / "c.lean").write_text(AVANT_C, encoding="utf-8")
    (apres / "c.lean").write_text(APRES_C, encoding="utf-8")
    (avant / "d.lean").write_text(AVANT_D, encoding="utf-8")
    (apres / "d.lean").write_text(APRES_D, encoding="utf-8")
    (avant / "f.lean").write_text(AVANT_F, encoding="utf-8")
    (apres / "e.lean").write_text(APRES_E, encoding="utf-8")
    (apres / "g.lean").write_text(APRES_G, encoding="utf-8")
    return str(avant), str(apres)


def _cles(observations):
    return [(o.kind, o.nom, o.saillance) for o in observations]


class TestRadarDetecteurs:
    def test_observations_exactes(self, arborescence):
        avant_dir, apres_dir = arborescence
        avant = scanner(avant_dir)
        apres = scanner(apres_dir)
        obs = comparer(avant, apres)
        assert _cles(obs) == [
            ("enonce_change", "foo", 10),
            ("enonce_change", "longproof", 10),
            ("effondrement_preuve", "longproof", 9),
            ("nouvel_axiome", "mon_axiome", 9),
            ("doublon", "dup", 8),
            ("nouveau_sorry", "nouveau_sorry", 8),
            ("symbole_perdu", "olddef", 5),
            ("corps_change", "bar", 1),
            ("symbole_gagne", "newdef", 1),
        ]

    def test_enonce_change_facts(self, arborescence):
        avant_dir, apres_dir = arborescence
        obs = comparer(scanner(avant_dir), scanner(apres_dir))
        foo = [o for o in obs if o.kind == "enonce_change" and o.nom == "foo"][0]
        assert foo.fichier == "a.lean"
        assert foo.ligne == 1
        assert "1 = 1" in foo.avant
        assert "2 = 2" in foo.apres

    def test_doublon_fichiers(self, arborescence):
        avant_dir, apres_dir = arborescence
        obs = comparer(scanner(avant_dir), scanner(apres_dir))
        dup = [o for o in obs if o.kind == "doublon"][0]
        assert dup.nom == "dup"
        assert "b.lean" in dup.fichier and "c.lean" in dup.fichier

    def test_sorry_preexistant_non_signale(self, arborescence):
        avant_dir, apres_dir = arborescence
        obs = comparer(scanner(avant_dir), scanner(apres_dir))
        assert not [o for o in obs
                    if o.kind == "nouveau_sorry" and o.nom == "avec_sorry"]

    def test_stable_non_signale(self, arborescence):
        avant_dir, apres_dir = arborescence
        obs = comparer(scanner(avant_dir), scanner(apres_dir))
        assert not [o for o in obs if o.nom == "stable"]

    def test_aucun_verdict(self, arborescence):
        """Aucune observation ne porte de langage prescriptif."""
        from phi_complexity.radar import LISTE_NOIRE_PRESCRIPTIF
        avant_dir, apres_dir = arborescence
        obs = comparer(scanner(avant_dir), scanner(apres_dir))
        for o in obs:
            texte = f"{o.kind} {o.avant} {o.apres}".lower()
            for interdit in LISTE_NOIRE_PRESCRIPTIF:
                assert interdit not in texte, f"'{interdit}' dans {o.kind}"


class TestRadarCasReel:
    def test_doublon_part0_ibp_stokes(self):
        """Le cas réel : partialDeriv/HasSchwartzDecay dupliqués entre
        Clay_NS_Part0.lean et IBP_Stokes.lean — le détecteur doit le
        trouver sur un arbre minimal reproduisant le doublon."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "Part0.lean")
            f2 = os.path.join(tmp, "Stokes.lean")
            open(f1, "w", encoding="utf-8").write(
                "def partialDeriv (f : Nat → Nat) : Nat := f 0\n")
            open(f2, "w", encoding="utf-8").write(
                "def partialDeriv (f : Nat → Nat) : Nat := f 0\n")
            fiches = scanner(tmp)
            obs = comparer({}, fiches)
            doublons = [o for o in obs if o.kind == "doublon"
                        and o.nom == "partialDeriv"]
            assert len(doublons) == 1


class TestRadarExclusionHorsChaine:
    """hors_chaine_clay/ est déclaré hors de la chaîne Clay : ses doublons
    internes (variantes d'exploration) sont exclus de la détection par
    défaut — mais leurs CHANGEMENTS restent détectés."""

    def _arbre(self, tmp):
        hc = os.path.join(tmp, "hors_chaine_clay")
        os.makedirs(hc)
        open(os.path.join(hc, "x.lean"), "w", encoding="utf-8").write(
            "def var_exp : Nat := 1\n")
        open(os.path.join(hc, "y.lean"), "w", encoding="utf-8").write(
            "def var_exp : Nat := 1\n")
        open(os.path.join(tmp, "live.lean"), "w", encoding="utf-8").write(
            "def var_exp : Nat := 1\n")
        return tmp

    def test_doublon_hors_chaine_exclu_par_defaut(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fiches = scanner(self._arbre(tmp))
            obs = comparer({}, fiches)
            # var_exp reste dupliqué entre live.lean et hors_chaine via x.lean
            # exclus : seul live.lean compte → PAS de doublon signalé
            assert not [o for o in obs if o.kind == "doublon"
                        and o.nom == "var_exp"]

    def test_doublon_hors_chaine_inclus_sur_demande(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fiches = scanner(self._arbre(tmp))
            obs = comparer({}, fiches, exclure_doublons=())
            doublons = [o for o in obs if o.kind == "doublon"
                        and o.nom == "var_exp"]
            assert len(doublons) == 1
            assert "hors_chaine_clay" in doublons[0].fichier

    def test_console_replie_preexistants(self):
        from phi_complexity.radar import formater_console
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f1 = os.path.join(tmp, "a.lean")
            f2 = os.path.join(tmp, "b.lean")
            open(f1, "w", encoding="utf-8").write("def vieux : Nat := 1\n")
            open(f2, "w", encoding="utf-8").write("def vieux : Nat := 1\n")
            avant = scanner(tmp)
            apres = scanner(tmp)
            obs = comparer(avant, apres)
            texte = formater_console(obs)
            assert "doublons préexistants repliés" in texte
            assert "DÉFINITION DUPLIQUÉE" not in texte
