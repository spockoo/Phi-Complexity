"""Tests du mode encerclement : cerner les mécanismes INCONNU sans les
fermer au premier contact (docs/ENCERCLEMENT.md).

Discipline : chaque angle est une proposition, seul Lean tranche. Les
tests mockent `verdict_lean` (jamais de vrai build Lean ici) et
vérifient les transitions de statuts typés.
"""
import json
import os

import pytest

from phi_complexity.chemins_verifiables import (
    _angles_encerclement,
    _deplier,
    _deplier_profond,
    _sous_termes,
    encercler_direction,
    ErreurRegistre,
    fichier_encerclement,
    inscrire_dossier_encerclement,
    lire_dossiers_encerclement,
)


@pytest.fixture()
def defs_chaine():
    # Chaîne en ordre d'insertion INVERSÉ (A5, …, A1) : chaque passe du
    # _Deplieur n'avance que d'un cran → la borne (_PROF_DEPLIAGE = 4)
    # est atteinte avant le bout → INCONNU "borne de dépliage atteinte".
    return ({"A5": "Nat", "A4": "A5", "A3": "A4", "A2": "A3",
             "A1": "A2"})


class TestDepliageProfond:
    def test_va_au_dela_de_la_borne(self, defs_chaine):
        assert _deplier("A1", defs_chaine) != "Nat"  # borne atteinte
        assert _deplier_profond("A1", defs_chaine) == "Nat"

    def test_sans_defs_restantes_identite(self):
        assert _deplier_profond("Nat → Nat", {}) == "Nat → Nat"


class TestSousTermes:
    def test_application_composee(self):
        assert _sous_termes("f x y") == ["f", "f x"]

    def test_terme_simple(self):
        assert _sous_termes("x") == []

    def test_parenthese_protegee(self):
        assert _sous_termes("(f x) y") == []

    def test_profondeur_respectee(self):
        assert _sous_termes("f (g x) y") == ["f", "f (g x)"]


class TestAnglesApplicables:
    def test_borne_de_depliage_propose_depliage_profond(self, defs_chaine):
        angles = _angles_encerclement(
            "borne de dépliage atteinte, définitions restantes",
            "A1", "0", "Nat", defs_chaine)
        noms = [a["nom"] for a in angles]
        assert "DEPLIAGE_PROFOND" in noms
        profond = next(a for a in angles if a["nom"] == "DEPLIAGE_PROFOND")
        assert profond["type_trou"] == "Nat"

    def test_paire_coercition_propose_coercition(self):
        angles = _angles_encerclement(
            "pas d'équivalence, mismatch non décisif",
            "ℝ", "n", "ℕ", {})
        noms = [a["nom"] for a in angles]
        assert "COERCION" in noms
        coer = next(a for a in angles if a["nom"] == "COERCION")
        assert "((n : ℕ) : ℝ)" in coer["terme"]

    def test_mismatch_non_coercition_sans_angle_coercition(self):
        angles = _angles_encerclement(
            "pas d'équivalence, mismatch non décisif",
            "Nat", "s", "String", {})
        assert "COERCION" not in [a["nom"] for a in angles]

    def test_terme_compose_propose_sous_termes(self):
        angles = _angles_encerclement(
            "pas d'équivalence, mismatch non décisif",
            "ℕ", "f x y", "ℕ → ℕ → ℕ", {})
        sous = [a for a in angles if a["nom"] == "SOUS_TERMES"]
        assert {a["terme"] for a in sous} == {"f", "f x"}


class TestDossier:
    @pytest.fixture()
    def chemin_tmp(self, tmp_path, monkeypatch):
        import phi_complexity.chemins_verifiables as m
        chemin = str(tmp_path / "dossiers.json")
        monkeypatch.setattr(m, "_chemin_dossiers_encerclement",
                            lambda: chemin)
        return chemin

    def test_absent_vaut_vide(self, chemin_tmp):
        assert lire_dossiers_encerclement() == {}

    def test_round_trip(self, chemin_tmp):
        dossier = {"statut": "RESISTANT", "angles": []}
        fiche = inscrire_dossier_encerclement(
            "s", "c", "trou", "Nat", "t", "empreinte", dossier)
        assert fiche["statut"] == "RESISTANT"
        assert fiche["date"]
        relus = lire_dossiers_encerclement()
        assert len(relus) == 1
        assert next(iter(relus.values()))["statut"] == "RESISTANT"

    def test_statut_invalide_refuse(self, chemin_tmp):
        with pytest.raises(ErreurRegistre):
            inscrire_dossier_encerclement(
                "s", "c", "trou", "Nat", "t", "e", {"statut": "FLOU"})

    def test_corrompu_visible(self, chemin_tmp):
        with open(chemin_tmp, "w", encoding="utf-8") as fh:
            fh.write("{ invalide")
        with pytest.raises(ErreurRegistre):
            lire_dossiers_encerclement()

    def test_cle_liee_a_l_enonce(self, chemin_tmp):
        d1 = {"statut": "RESISTANT", "angles": []}
        inscrire_dossier_encerclement("s", "c", "t", "Nat", "x", "e1", d1)
        inscrire_dossier_encerclement("s", "c", "t", "Nat", "x", "e2", d1)
        assert len(lire_dossiers_encerclement()) == 2


def _candidat_minimal():
    return {"declaration": "cand", "module": "M", "lieurs": [],
            "opens": [], "groupes_complets": [], "univers": [],
            "variables": []}


def _direction():
    return {"trou": "h", "type_trou": "ℝ", "terme": "n",
            "type_terme": "ℕ",
            "raison": "pas d'équivalence, mismatch non décisif"}


class TestEncerclerDirection:
    @pytest.fixture()
    def isole(self, tmp_path, monkeypatch):
        import phi_complexity.chemins_verifiables as m
        monkeypatch.setattr(
            m, "_chemin_dossiers_encerclement",
            lambda: str(tmp_path / "dossiers.json"))
        inscrits = []
        monkeypatch.setattr(
            m, "inscrire_impossible_valide",
            lambda *a, **k: inscrits.append(a) or {"ok": True})
        return inscrits

    def _lance(self, verdicts, monkeypatch, inscrits, **kw):
        import phi_complexity.chemins_verifiables as m
        it = iter(verdicts)
        monkeypatch.setattr(
            m, "verdict_lean",
            lambda chemin, dossier, timeout_s=600: next(it))
        return encercler_direction(
            "s", "MS", _candidat_minimal(), {}, _direction(), {},
            "empreinte", "/tmp", timeout_s=10, max_angles=3, **kw)

    def test_prouve_devient_atteignable_trouve(self, isole, monkeypatch):
        fiche = self._lance(
            [{"verdict": "PROUVÉ", "diagnostic": "exit 0"}], monkeypatch,
            isole)
        assert fiche["statut"] == "ATTEIGNABLE_TROUVE"
        assert isole == []  # jamais inscrit au registre des impossibilités
        assert fiche["registre"] is None

    def test_refute_mismatch_devient_encercle_et_inscrit(
            self, isole, monkeypatch):
        fiche = self._lance(
            [{"verdict": "RÉFUTÉ",
              "diagnostic": "type mismatch: ℕ vs ℝ"}], monkeypatch, isole)
        assert fiche["statut"] == "ENCERCLE"
        assert fiche["angles_decisifs"] == ["COERCION"]
        assert len(isole) == 1  # Lean a tranché → registre
        assert fiche["registre"] == "IMPOSSIBLE_VALIDÉ"

    def test_sans_verdict_decisif_devient_resistant(
            self, isole, monkeypatch):
        fiche = self._lance(
            [{"verdict": "INDÉCIDÉ", "diagnostic": "timeout"}], monkeypatch,
            isole)
        assert fiche["statut"] == "RESISTANT"
        assert isole == []
        assert len(fiche["angles"]) == 1

    def test_aucun_angle_applicable_rend_none(self, isole, monkeypatch):
        import phi_complexity.chemins_verifiables as m
        direction = {"trou": "h", "type_trou": "Nat", "terme": "x",
                     "type_terme": "String",
                     "raison": "pas d'équivalence, mismatch non décisif"}
        assert encercler_direction(
            "s", "MS", _candidat_minimal(), {}, direction, {},
            "empreinte", "/tmp", max_angles=3) is None

    def test_on_ne_ferme_pas_au_premier_contact(
            self, isole, monkeypatch):
        # Même avec un premier angle décisif, tous les angles applicables
        # sont sondés avant le verdict final (encercler ≠ tuer vite).
        import phi_complexity.chemins_verifiables as m
        vus = []
        def faux_verdict(chemin, dossier, timeout_s=600):
            vus.append(chemin)
            if "DEPLIAGE_PROFOND" in chemin:
                return {"verdict": "RÉFUTÉ",
                        "diagnostic": "type mismatch"}
            return {"verdict": "INDÉCIDÉ", "diagnostic": "timeout"}
        monkeypatch.setattr(m, "verdict_lean", faux_verdict)
        direction = {"trou": "h", "type_trou": "A1", "terme": "f x",
                     "type_terme": "ℕ → ℕ",
                     "raison": "borne de dépliage atteinte"}
        defs = {"A1": "ℕ"}
        fiche = encercler_direction(
            "s", "MS", _candidat_minimal(), {}, direction, defs,
            "empreinte", "/tmp", max_angles=3)
        noms = [a["angle"] for a in fiche["angles"]]
        assert noms == ["DEPLIAGE_PROFOND", "SOUS_TERMES"]
        assert len(vus) == 2
        assert fiche["statut"] == "ENCERCLE"


class TestFichierEncerclement:
    def test_entete_b8_b11(self):
        cand = {"declaration": "c", "module": "MC",
                "lieurs": ["(sol : ClassicalSolution ν)"],
                "groupes_complets": ["(sol : ClassicalSolution ν)"],
                "univers": [], "variables": [],
                "opens": ["open MeasureTheory"]}
        angle = {"nom": "COERCION", "type_trou": "ℝ",
                 "terme": "((n : ℕ) : ℝ)",
                 "justification": "coercition explicite ℕ → ℝ"}
        contenu = fichier_encerclement(
            "s", "MS", cand, {}, angle, opens_sorry=[],
            raison="mismatch non décisif")
        assert "import MS" in contenu
        assert "import MC" in contenu
        assert "open MeasureTheory" in contenu
        assert "(sol : ClassicalSolution ν)" in contenu
        ligne_ex = next(l for l in contenu.splitlines()
                        if l.startswith("example"))
        assert "((n : ℕ) : ℝ)" in ligne_ex
        assert "Angle : COERCION" in contenu
