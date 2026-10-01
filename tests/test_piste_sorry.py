"""tests/test_piste_sorry.py — Piste d'un sorry à travers Lean 4.

Ce que ces tests verrouillent :
- rigueur de l'inventaire : un `sorry` dans un commentaire (--, /- -/,
  docstring /-- -/) ou un littéral de chaîne N'EST PAS un sorry ;
  `sorryAx` n'est pas un sorry (frontière de mot) ;
- la déclaration englobante est correctement attribuée ;
- le graphe d'imports et la fermeture des dépendants ;
- `piste()` : TROUVÉ avec occurrences, INTROUVABLE sans inventer,
  registre absent => piste structurelle seule (opt-in, jamais d'échec) ;
- la numérotation des trous est lue dans le registre, jamais en dur ;
- la garde mission couvre le nouveau module.
"""
import json
import os

import pytest

from phi_complexity import mission
from phi_complexity.piste_sorry import (
    Sorry,
    chantiers_du_sorry,
    decaper_lean,
    dependants_de,
    graphe_imports,
    inventorier_sorrys,
    piste,
    rendre_console,
    sorrys_dans_fichier,
    trou_du_sorry,
)


FICHIER_PIEGE = """-- un sorry en commentaire de ligne ne compte pas : sorry
/- un sorry en bloc ne compte pas : sorry
   /- imbriqué : sorry -/
-/
theorem vrai_theoreme : True := by
  trivial

/-- docstring : sorry ne compte pas -/
def fausse_piste : Nat :=
  42 -- sorry ici non plus

theorem avec_trou : 1 = 1 := by
  sorry

def utilise_string : String :=
  "ceci n'est pas un sorry : sorry"

theorem sorryAx_pas_un_sorry : True := by
  trivial
"""


@pytest.fixture
def dossier_lean(tmp_path):
    (tmp_path / "A.lean").write_text(
        "theorem trou_alpha : True := by\n  sorry\n", encoding="utf-8")
    (tmp_path / "B.lean").write_text(
        "import A\ntheorem sans_trou : True := by trivial\n", encoding="utf-8")
    (tmp_path / "C.lean").write_text(
        "import B\n" + FICHIER_PIEGE, encoding="utf-8")
    return str(tmp_path)


class TestDecapage:
    def test_commentaire_ligne_retire(self):
        lignes = decaper_lean("-- sorry\ntrivial")
        assert all("sorry" not in t for _, t in lignes)

    def test_bloc_imbrique_retire(self):
        lignes = decaper_lean("/- a /- sorry -/ b -/\nok")
        assert all("sorry" not in t for _, t in lignes)
        assert any("ok" in t for _, t in lignes)

    def test_docstring_retiree(self):
        lignes = decaper_lean("/-- sorry -/\ntheorem x : True := by trivial")
        assert all("sorry" not in t for _, t in lignes)

    def test_string_retiree(self):
        lignes = decaper_lean('def s := "sorry"\nok')
        assert all("sorry" not in t for _, t in lignes)
        assert any("ok" in t for _, t in lignes)

    def test_echappement_string(self):
        lignes = decaper_lean('def s := "a\\"b"\nsorry')
        assert any("sorry" in t for _, t in lignes)

    def test_code_conserve(self):
        lignes = decaper_lean("theorem t : True := by\n  sorry\n")
        assert sum(t.count("sorry") for _, t in lignes) == 1


class TestInventaire:
    def test_sorry_reel_trouve_avec_declaration(self, tmp_path):
        f = tmp_path / "T.lean"
        f.write_text("theorem mon_trou : True := by\n  sorry\n",
                     encoding="utf-8")
        res = sorrys_dans_fichier(str(f))
        assert len(res) == 1
        assert res[0].ligne == 2
        assert res[0].declaration == "mon_trou"

    def test_pieges_ignores(self, tmp_path):
        f = tmp_path / "P.lean"
        f.write_text(FICHIER_PIEGE, encoding="utf-8")
        res = sorrys_dans_fichier(str(f))
        # un seul vrai sorry : celui de avec_trou
        assert len(res) == 1
        assert res[0].declaration == "avec_trou"

    def test_sorryAx_non_matche(self, tmp_path):
        f = tmp_path / "S.lean"
        f.write_text("theorem t : True := sorryAx\n", encoding="utf-8")
        assert sorrys_dans_fichier(str(f)) == []

    def test_inventaire_recursif_trie(self, dossier_lean):
        res = inventorier_sorrys(dossier_lean)
        decls = [s.declaration for s in res]
        assert "trou_alpha" in decls
        assert "avec_trou" in decls
        assert len(res) == 2
        fichiers = [s.fichier for s in res]
        assert fichiers == sorted(fichiers)


class TestGrapheImports:
    def test_imports_lus(self, dossier_lean):
        g = graphe_imports(dossier_lean)
        assert g["B"] == ["A"]
        assert g["C"] == ["B"]
        assert g["A"] == []

    def test_dependants_transitifs(self, dossier_lean):
        g = graphe_imports(dossier_lean)
        assert dependants_de("A", g) == ["B", "C"]
        assert dependants_de("C", g) == []


class TestTrouDuSorry:
    def test_numerotation_lue_pas_en_dur(self):
        texte = ("| `Mod.lean` | 7 (`mon_sorry`) | 12 | 34 | `abc` |\n"
                 "| `Aut.lean` | 2 (`autre`) | 13 | 35 | `def` |\n")
        assert trou_du_sorry("mon_sorry", texte) == 7
        assert trou_du_sorry("autre", texte) == 2
        assert trou_du_sorry("inconnu", texte) is None


class TestPiste:
    def test_trouve(self, dossier_lean):
        p = piste("trou_alpha", dossier_lean)
        assert p["statut"] == "TROUVÉ"
        assert len(p["occurrences"]) == 1
        assert p["occurrences"][0]["declaration"] == "trou_alpha"
        assert "B" in p["modules_dependants"]
        assert "C" in p["modules_dependants"]

    def test_introuvable_sans_inventer(self, dossier_lean):
        p = piste("sorry_inexistant", dossier_lean)
        assert p["statut"] == "INTROUVABLE"
        assert p["occurrences"] == []
        assert p["registre"]["chantiers"] == []

    def test_registre_absent_opt_in(self, dossier_lean):
        # chemin inexistant : piste structurelle seule, pas d'échec
        p = piste("trou_alpha", dossier_lean,
                  chemin_registre="/chemin/qui/n/existe/pas.md")
        assert p["statut"] == "TROUVÉ"
        assert p["registre"]["disponible"] is False

    def test_rendu_console(self, dossier_lean):
        p = piste("trou_alpha", dossier_lean)
        txt = rendre_console(p)
        assert "TROUVÉ" in txt
        assert "trou_alpha" in txt
        txt2 = rendre_console(piste("zzz", dossier_lean))
        assert "INTROUVABLE" in txt2

    def test_format_json_serialisable(self, dossier_lean):
        p = piste("trou_alpha", dossier_lean)
        json.dumps(p, ensure_ascii=False)


class TestGardeMissionPisteSorry:
    def test_module_declare(self):
        assert "piste_sorry" in mission.modules_couverts()

    def test_justification_valide(self):
        problemes = [p for p in mission.valider()
                     if "piste_sorry" in p]
        assert problemes == []

    def test_sert_vocabluaire(self):
        sert = mission.JUSTIFICATIONS["piste_sorry"]["sert"]
        assert sert in mission.SERT_AUTORISES
