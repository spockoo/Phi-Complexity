"""Tests unitaires de la synthèse contrôlée de structures (chantier 2026-10-01).

La synthèse remplit les trous `?_` des squelettes par :
  P  : projection directe d'un champ de structure (sol.momentum) ;
  S1 : projection simple (sol.u, sol.horizon) ;
  S2 : projection de conjonction via dépliage d'une def, avec
       réordonnancement explicite des lieurs si besoin ;
  H  : échec honnête → le trou reste `?_` (hypothèse nommée absente,
       régularité supérieure non fournie).
"""

import pytest

from unittest.mock import patch

from phi_complexity.chemins_verifiables import (
    _analyse_forall,
    _chemin_projection_conjonction,
    _couper_conjonction,
    _defs_corps,
    _denuder,
    _reordonner_lambda,
    _structures_corpus,
    _squelette_synthese,
    _synthetiser_trou,
    _termes_etendus,
    candidats_cablage,
)


@pytest.fixture
def dossier_structures(tmp_path):
    (tmp_path / "S.lean").write_text(
        "structure Sol where\n"
        "  u : Nat → Nat\n"
        "  h : 0 < 1\n"
        "  mom : u 0 = 0\n"
        "def Reg (u : Nat → Nat) : Prop :=\n"
        "  (∀ x, P x u)\n"
        "  ∧ (∀ y, Q y u)\n"
        "theorem but (s : Sol) : True := by\n"
        "  sorry\n"
        "theorem cand (u : Nat → Nat) (hm : u 0 = 0)\n"
        "    (hr : ∀ y, ∀ x, P x u) : True :=\n"
        "  trivial\n",
        encoding="utf-8")
    return str(tmp_path)


class TestStructuresCorpus:
    def test_champs_lus(self, dossier_structures):
        structs = _structures_corpus(dossier_structures)
        assert "Sol" in structs
        _params, champs = structs["Sol"]
        noms = [c[0] for c in champs]
        assert noms == ["u", "h", "mom"]

    def test_defs_corps(self, dossier_structures):
        defs = _defs_corps(dossier_structures)
        assert "Reg" in defs
        assert len(_couper_conjonction(defs["Reg"])) == 2


class TestSynthese:
    def test_projection_champ_P(self, dossier_structures):
        structs = _structures_corpus(dossier_structures)
        defs = _defs_corps(dossier_structures)
        ctx = [("s", "Sol")]
        terme, raison = _synthetiser_trou("s.u 0 = 0", ctx, structs,
                                          defs, {"u": "s.u"})
        assert terme == "s.mom", raison

    def test_projection_simple_S1(self, dossier_structures):
        structs = _structures_corpus(dossier_structures)
        defs = _defs_corps(dossier_structures)
        ctx = [("s", "Sol")]
        terme, _ = _synthetiser_trou("Nat → Nat", ctx, structs, defs, {})
        assert terme == "s.u"

    def test_echec_honnete(self, dossier_structures):
        structs = _structures_corpus(dossier_structures)
        defs = _defs_corps(dossier_structures)
        ctx = [("s", "Sol")]
        terme, raison = _synthetiser_trou("HypotheseNommee", ctx, structs,
                                          defs, {})
        assert terme is None
        assert "honnête" in raison

    def test_pas_de_reutilisation(self, dossier_structures):
        # deux trous distincts ne reçoivent pas le même terme
        structs = _structures_corpus(dossier_structures)
        defs = _defs_corps(dossier_structures)
        ctx = [("n", "Nat"), ("s", "Sol")]
        terme, _ = _synthetiser_trou("Nat", ctx, structs, defs, {"m": "n"})
        assert terme != "n"


class TestForall:
    def test_denuder(self):
        assert _denuder("(∀ x, P x)") == "∀ x, P x"
        assert _denuder("((A))") == "A"

    def test_analyse_multi_noms(self):
        lieurs, corps = _analyse_forall("∀ x i, P x i")
        assert [n for n, _ in lieurs] == ["x", "i"]
        assert corps == "P x i"

    def test_analyse_appartenance(self):
        lieurs, corps = _analyse_forall("∀ t ∈ S, P t")
        assert lieurs[0][0] == "t"
        assert lieurs[1][0] == "tₘ"
        assert corps == "P t"

    def test_reordonner(self):
        src = [("x", None), ("t", None), ("tₘ", None), ("i", None)]
        tgt = [("t", None), ("tₘ", None), ("x", None), ("i", None)]
        lam = _reordonner_lambda(src, tgt, "h.1")
        assert lam == "(fun t h_t x i => h.1 x t h_t i)"

    def test_reordonner_refuse(self):
        assert _reordonner_lambda([("x", None)], [("y", None)], "h") is None


class TestIntegration:
    def test_squelette_avec_synthese(self, dossier_structures):
        res = candidats_cablage("but", dossier_structures)
        assert res["statut"] == "TROUVÉ"
        par_nom = {c["declaration"]: c for c in res["candidats"]}
        sq = par_nom["cand"]["squelette"]
        # u → s.u (S1), hm → s.mom (P)
        assert "s.u" in sq and "s.mom" in sq
        # hr : ∀ y, ∀ x, P x u — la conjonction Reg ne s'applique pas ici
        # (le type du trou n'est pas une projection de Reg), reste ?_ ou synthétisé
        assert sq.startswith("refine cand")

    def test_synthese_seulement_sur_retenus(self, dossier_structures):
        """Non-régression perf (2026-10-01) : la synthèse coûteuse ne tourne
        que sur les max_candidats retenus APRÈS tri, pas sur tout le pool
        scoré (37015 admissibles pour energy_identity → la version naïve
        synthétisait tout et ne terminait plus)."""
        appels = []
        originale = _squelette_synthese

        def comptable(d, ctx, dossier):
            appels.append(d.nom)
            return originale(d, ctx, dossier)

        with patch("phi_complexity.chemins_verifiables._squelette_synthese",
                   side_effect=comptable):
            res = candidats_cablage("but", dossier_structures,
                                    max_candidats=1)
        assert res["statut"] == "TROUVÉ"
        assert len(res["candidats"]) == 1
        # Un seul appel : le retenu, pas tout le pool.
        assert len(appels) == 1
        assert appels[0] == res["candidats"][0]["declaration"]

    def test_projections_conjonction_imbriquee(self):
        """Non-régression (2026-10-01, défaut nommé par Lean sur
        energy_identity) : `A ∧ B ∧ C ∧ D` = `And A (And B (And C D))`,
        les projections sont `.1`, `.2.1`, `.2.2.1`, `.2.2.2` —
        jamais `.2`, `.3`, `.4` plats."""
        assert _chemin_projection_conjonction(1, 4) == ".1"
        assert _chemin_projection_conjonction(2, 4) == ".2.1"
        assert _chemin_projection_conjonction(3, 4) == ".2.2.1"
        assert _chemin_projection_conjonction(4, 4) == ".2.2.2"
        # Cas dégénérés
        assert _chemin_projection_conjonction(1, 2) == ".1"
        assert _chemin_projection_conjonction(2, 2) == ".2"
        assert _chemin_projection_conjonction(1, 1) == ".1"
