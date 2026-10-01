"""tests/test_chemins_durcissement.py — Durcissement du générateur (2026-10-01).

Régressions verrouillées après la validation multi-lemmes des 4 sorrys
restants : la démo a révélé 5 défauts que Lean a nommés formellement.

B1. Lieurs à indices Unicode (`h₁`, `sol₂`) : le motif d'identifiants
     coupait les souscrits → noms `h`/`sol` inexistants dans le fichier
     généré (diagnostiqué sur `local_uniqueness` : `Unknown identifier h`).
B2. Lemmes nichés dans un `namespace` : le nom court était émis sans
     qualification → `Unknown identifier local_existence_discharge`
     (vrai nom : `PicardTrilogy138.local_existence_discharge` ; les
     `section` ne qualifient pas).
B3. `open` non rejoués : `Integrable` (sous `open MeasureTheory`) était
     inconnu dans le fichier généré.
B4. Univers `.{u}` recopiés sans `universe u` : `unknown universe level`.
B5. Recherche exhaustive sans borne de travail : `energy_identity`
     (pool 172, n_max 4) ne terminait pas — R4 exige une borne.
"""
import os

import pytest

from phi_complexity.chemins_verifiables import (
    DeclarationProuvee,
    _namespaces_de_ligne,
    _nom_lean,
    _noms_groupe,
    _opens_fichier,
    _piles_namespaces,
    _sans_univers_explicites,
    _squelette_chaine,
    _type_etape,
    chemins,
    declarations_dans_fichier,
    fichier_verification_chemin,
)


def _decl(nom="etape1", lieurs=("n : Nat",), conclusion="Q n",
          qualifie="", opens=()):
    return DeclarationProuvee(
        module="M", fichier="M.lean", nom=nom, genre="theorem", ligne=1,
        entete="", lieurs=[f"({l})" for l in lieurs],
        noms_lieurs=[l.split(" :")[0].split()[0] for l in lieurs],
        conclusion=conclusion, nom_qualifie=qualifie, opens=list(opens))


class TestB1IdentifiantsUnicode:
    def test_souscrits_conserves(self):
        assert _noms_groupe("h₁ h₂ : ∀ x, T x") == ["h₁", "h₂"]

    def test_substitution_ne_coupe_pas(self):
        # `sol` ne doit pas matcher à l'intérieur de `sol₁`.
        d = _decl(nom="e", lieurs=("sol₁ : S", "sol₂ : S"),
                  conclusion="R sol₁ sol₂")
        t = _type_etape(d, ["a₁", "a₂"])
        assert t == "R a₁ a₂"
        assert "sol" not in t.replace("a₁", "").replace("a₂", "")

    def test_jetons_grecs_et_primes(self):
        assert _noms_groupe("ν x' _h : T") == ["ν", "x'", "_h"]


class TestB2NamespacesQualifies:
    def test_namespace_simple(self, tmp_path):
        (tmp_path / "N.lean").write_text(
            "namespace Foo\n"
            "theorem bar (n : Nat) : P n := by trivial\n"
            "end Foo\n",
            encoding="utf-8")
        ds = declarations_dans_fichier(str(tmp_path), str(tmp_path / "N.lean"))
        assert ds[0].nom == "bar"
        assert ds[0].nom_qualifie == "Foo.bar"

    def test_section_ne_qualifie_pas(self, tmp_path):
        (tmp_path / "N.lean").write_text(
            "namespace Pic\n"
            "section Travail\n"
            "theorem lem (n : Nat) : P n := by trivial\n"
            "end Travail\n"
            "end Pic\n",
            encoding="utf-8")
        ds = declarations_dans_fichier(str(tmp_path), str(tmp_path / "N.lean"))
        assert ds[0].nom_qualifie == "Pic.lem"

    def test_squelette_emet_qualifie(self):
        d = _decl(nom="bar", qualifie="Foo.bar", lieurs=(),
                  conclusion="P")
        sq = _squelette_chaine([d, _decl(nom="but", lieurs=(),
                                        conclusion="P")],
                               [("n", "Nat")])
        assert "Foo.bar" in sq
        assert "refine bar " not in sq and ":= bar " not in sq

    def test_nom_lean_repli_court(self):
        assert _nom_lean(_decl(nom="x")) == "x"


class TestB3OpensRejoues:
    def test_opens_fichier(self, tmp_path):
        (tmp_path / "O.lean").write_text(
            "import X\n"
            "open Real Finset\n"
            "open scoped InnerProductSpace\n"
            "open MeasureTheory\n"
            "theorem t : P := by trivial\n",
            encoding="utf-8")
        ds = declarations_dans_fichier(str(tmp_path), str(tmp_path / "O.lean"))
        assert ds[0].opens == ["open Real Finset",
                               "open scoped InnerProductSpace",
                               "open MeasureTheory"]

    def test_open_in_non_rejoue(self):
        assert _opens_fichier("open Foo in\ntheorem t : P := by trivial\n") == []

    def test_fichier_verification_rejoue_opens(self):
        chemin = {"noms": ["a"], "modules": ["M"], "opens": ["open MeasureTheory"],
                  "inegalite": "1 ≤ 2", "score": 1.0, "squelette": "exact a"}
        contenu = fichier_verification_chemin(
            "but", chemin, "S", ["(n : Nat)"], "P n", opens_sorry=["open Real"])
        assert "open Real" in contenu
        assert "open MeasureTheory" in contenu
        # les opens viennent après les imports
        assert contenu.index("import S") < contenu.index("open Real")


class TestB4UniversRetires:
    def test_strip_simple(self):
        assert _sans_univers_explicites("T.{u} → C") == "T → C"

    def test_strip_multiple(self):
        assert _sans_univers_explicites(
            "F.{u, v} (x : G.{w}) : H.{0}") == "F (x : G) : H"

    def test_type_etape_strip(self):
        d = _decl(nom="e", lieurs=("h : T",),
                  conclusion="K.{u} h")
        assert _type_etape(d, ["a"]) == "K a"

    def test_fichier_verification_strip_header(self):
        chemin = {"noms": ["a"], "modules": ["M"], "opens": [],
                  "inegalite": "1 ≤ 2", "score": 1.0, "squelette": "exact a"}
        contenu = fichier_verification_chemin(
            "but", chemin, "S", ["(h : T.{u})"], "P.{v} h")
        assert ".{" not in contenu


class TestB5BorneDeRecherche:
    @pytest.fixture
    def petit_dossier(self, tmp_path):
        (tmp_path / "C.lean").write_text(
            "theorem l1 (n : Nat) : P n := by trivial\n"
            "theorem l2 (n : Nat) (h : P n) : Q n := by trivial\n"
            "theorem l3 (n : Nat) (h : Q n) : R n := by trivial\n"
            "theorem but (n : Nat) (h : P n) : R n := by\n"
            "  sorry\n",
            encoding="utf-8")
        return str(tmp_path)

    def test_borne_signalee_honnetement(self, petit_dossier):
        res = chemins("but", petit_dossier, max_candidats=12, max_prefixes=1)
        assert res["recherche_bornee"] is True
        assert res["borne_prefixes"] == 1
        assert res["prefixes_explores"] <= 1

    def test_sans_borne_pas_de_drapeau(self, petit_dossier):
        res = chemins("but", petit_dossier, max_candidats=12)
        assert res["recherche_bornee"] is False

    def test_borne_ne_casse_pas_les_meilleurs(self, petit_dossier):
        # Avec une borne généreuse, la chaîne l2 → l3 reste admissible
        # (le sorry fournit déjà h : P n, donc l1 est redondant).
        res = chemins("but", petit_dossier, max_candidats=12,
                      max_prefixes=20000)
        assert res["statut"] == "TROUVÉ"
        assert any(ch["noms"] == ["l2", "l3"] for ch in res["chemins"])


@pytest.mark.skipif(
    not os.path.isdir(os.path.expanduser("~/workspace/lean-navier-stokes")),
    reason="dossier Lean réel absent (opt-in, comme les sondes)")
class TestDurcissementReel:
    DOSSIER = os.path.expanduser("~/workspace/lean-navier-stokes")

    def test_local_existence_discharge_qualifie(self):
        ds = declarations_dans_fichier(
            self.DOSSIER, os.path.join(self.DOSSIER,
                                       "Clay_NS_Part63_PicardTrilogy.lean"))
        d = next(x for x in ds if x.nom == "local_existence_discharge")
        assert d.nom_qualifie == "PicardTrilogy138.local_existence_discharge"

    def test_energy_identity_termine_bornee(self):
        res = chemins("energy_identity", self.DOSSIER, max_candidats=3)
        assert res["statut"] == "TROUVÉ"
        assert res["prefixes_explores"] <= res["borne_prefixes"]
