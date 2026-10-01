"""tests/test_chemins_multilemmes.py — Chemins multi-lemmes (v2).

Verrouille la formulation validée le 2026-10-01
(FORMULATION_CHEMINS_MULTI_LEMMES.md, §6 — falsification avant intégration) :

1. la recherche exhaustive naïve est interdite PAR CONSTRUCTION
   (l'inégalité élague pendant la génération, pas après) ;
2. les 3 chemins de la démo `leray_existence` restent admis
   (opt-in : dossier Lean réel) ;
3. INDÉCIDÉ a priori prédit SANS brûler un build Lean ;
4. le budget est relatif et étalonné (aucune constante absolue) ;
5. R4 comme propriété testée : aucune soumission Lean sans preuve
   d'admissibilité archivée AVANT l'appel.
"""
import json
import math
import os
import subprocess

import pytest

from phi_complexity.chemins_verifiables import (
    _args_etape,
    _choisir_arg,
    _connecte_but,
    _connecte_etape,
    _connecte_premier_pas,
    _connexion_forte,
    _deballe_nonempty,
    _lieurs_types,
    _squelette_chaine,
    _type_groupe,
    budget_sorry,
    chemins,
    complexite_declaration,
    cout_chemin,
    declarations_dans_fichier,
    fichier_verification_chemin,
    realiser_chemins,
    rendre_chemins,
    types_connectes,
    verdict_lean,
)


@pytest.fixture
def chaine(tmp_path):
    (tmp_path / "A.lean").write_text(
        "theorem etape1 (n : Nat) (h : P n) : Q n := sorry\n"
        "theorem etape2 (n : Nat) (h : Q n) : R n := sorry\n"
        "theorem but (n : Nat) (h : P n) : R n := by\n"
        "  sorry\n",
        encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def chaine_prouvee(tmp_path):
    # Même topologie, lemmes prouvés (seuls des prouvés sont candidats).
    (tmp_path / "A.lean").write_text(
        "theorem etape1 (n : Nat) (h : P n) : Q n := by trivial\n"
        "theorem etape2 (n : Nat) (h : Q n) : R n := by trivial\n"
        "theorem lemme_inutile (x : String) : Z x := by trivial\n"
        "theorem but (n : Nat) (h : P n) : R n := by\n"
        "  sorry\n",
        encoding="utf-8")
    return str(tmp_path)


class TestMesureEtBudget:
    def test_phi_deterministe_positive(self, chaine_prouvee):
        decls = declarations_dans_fichier(
            chaine_prouvee, os.path.join(chaine_prouvee, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        p1 = complexite_declaration(par_nom["etape1"])
        assert p1 == complexite_declaration(par_nom["etape1"])
        assert p1 >= 1

    def test_cout_superlineaire(self):
        # La pénalité de composition punit les longues chaînes.
        c1 = cout_chemin([10], 10)
        c2 = cout_chemin([10, 10], 10)
        c3 = cout_chemin([10, 10, 10], 10)
        assert c2 - c1 < c3 - c2

    def test_budget_relatif_sans_constante_absolue(self):
        # Doubler toutes les entrées double le budget (homogénéité) :
        # aucune constante absolue ne survit dans le calcul.
        b1 = budget_sorry(20, 10)
        b2 = budget_sorry(40, 20)
        assert b2["budget"] == pytest.approx(2 * b1["budget"])
        assert b2["n_max"] == b1["n_max"]
        # Le budget dépend du pool : même sorry, pools différents.
        assert budget_sorry(20, 10)["budget"] != budget_sorry(20, 11)["budget"]
        assert b1["n_max"] >= 1

    def test_type_groupe_profondeur_reelle(self):
        assert _type_groupe("(ν : ℝ)") == "ℝ"
        assert _type_groupe("(h : f (g x) = 0)") == "f (g x) = 0"
        assert _type_groupe("(n : Nat := 0)") == "Nat := 0"


class TestConnexions:
    def test_types_connectes(self):
        assert types_connectes("ℝ", "ℝ")          # égalité normalisée
        assert types_connectes("Q n", "Q n")
        assert types_connectes("List Nat", "List Bool")  # même tête
        assert not types_connectes("Nat", "Bool")
        assert not types_connectes("", "Nat")

    def test_premier_pas(self, chaine_prouvee):
        decls = declarations_dans_fichier(
            chaine_prouvee, os.path.join(chaine_prouvee, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        lieurs_but = _lieurs_types(par_nom["but"])
        assert _connecte_premier_pas(par_nom["etape1"], lieurs_but)
        # lemme_inutile : aucun lieur connecté au sorry
        assert not _connecte_premier_pas(par_nom["lemme_inutile"], lieurs_but)

    def test_etape_stricte(self, chaine_prouvee):
        decls = declarations_dans_fichier(
            chaine_prouvee, os.path.join(chaine_prouvee, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        # etape2 consomme la conclusion de etape1, pas un lemme indépendant.
        assert _connecte_etape(par_nom["etape2"], "Q n")
        assert not _connecte_etape(par_nom["etape2"], "Z x")

    def test_but(self, chaine_prouvee):
        decls = declarations_dans_fichier(
            chaine_prouvee, os.path.join(chaine_prouvee, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        assert _connecte_but(par_nom["etape2"], "R", set(), "R n")
        assert not _connecte_but(par_nom["etape1"], "R", set(), "R n")


class TestChaine:
    def test_chemin_deux_etapes_trouve(self, chaine_prouvee):
        res = chemins("but", chaine_prouvee)
        assert res["statut"] == "TROUVÉ"
        noms = [ch["noms"] for ch in res["chemins"]]
        assert ["etape1", "etape2"] in noms

    def test_squelette_fragment_d(self, chaine_prouvee):
        res = chemins("but", chaine_prouvee)
        ch = next(c for c in res["chemins"]
                  if c["noms"] == ["etape1", "etape2"])
        sq = ch["squelette"]
        assert "have h_chem1 :" in sq
        assert sq.rstrip().splitlines()[-1].strip().startswith("exact")
        code = "\n".join(l for l in sq.splitlines())
        for tac in ("simp", "aesop", "auto", "tauto", "omega", "decide",
                    "sorry", "admit"):
            assert tac not in code
        # L'étape 2 consomme h_chem1 (pas le lieur h du sorry, de type P n).
        assert "etape2 n h_chem1" in sq

    def test_args_etape_nom_et_type(self, chaine_prouvee):
        # Le nom seul ne suffit pas : h (Q n) ≠ h (P n) du sorry.
        decls = declarations_dans_fichier(
            chaine_prouvee, os.path.join(chaine_prouvee, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        lieurs_but = _lieurs_types(par_nom["but"])
        args, deballages = _args_etape(
            par_nom["etape2"], lieurs_but + [("h_chem1", "Q n")], set())
        assert args == ["n", "h_chem1"]
        assert deballages == []

    def test_json_serialisable(self, chaine_prouvee):
        json.dumps(chemins("but", chaine_prouvee), ensure_ascii=False)

    def test_inegalite_verifiee_sur_tous_les_chemins(self, chaine_prouvee):
        # Critère §6.1 (2e moitié) : tout chemin émis satisfait C(P) ≤ B(S).
        res = chemins("but", chaine_prouvee)
        assert res["chemins"]
        for ch in res["chemins"]:
            assert ch["cout"] <= ch["budget"]

    def test_fichier_verification_chemin(self, chaine_prouvee):
        res = chemins("but", chaine_prouvee)
        ch = next(c for c in res["chemins"]
                  if c["noms"] == ["etape1", "etape2"])
        src = fichier_verification_chemin(
            "but", ch, res["module_sorry"], ["(n : Nat)", "(h : P n)"],
            "R n")
        assert "import A" in src
        assert "have h_chem1" in src and "exact etape2" in src
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("--"))
        assert "sorry" not in code


class TestDeballageNonempty:
    # Durcissement du 2026-10-01 : la démo Lean a réfuté 3 chemins sur le
    # même point mécanique — `Nonempty T` passé où `T` est attendu.
    # Le recouvrement de jetons n'est pas une connexion utilisable telle
    # quelle : le déballage par `Classical.choice` devient un `have`
    # explicite du fragment D.

    @pytest.fixture
    def chaine_nonempty(self, tmp_path):
        # Dimensionné pour passer le budget : C([4,5]) = 13.5 ≤ B = 18.5.
        # Le budget est strict sur les chaînes PAR DESIGN (la composition
        # paie C(n,2)·Φ̄ de cohérence) — un fixture trop disproportionné
        # serait honnêtement exclu, ce n'est pas ce qu'on teste ici.
        (tmp_path / "A.lean").write_text(
            "theorem fam_nonempty (h : T) : Nonempty Fam := by trivial\n"
            "theorem closure (h : T) (f : Fam) : Goal := by trivial\n"
            "theorem but (h : T) (x : X) : Goal := by\n"
            "  sorry\n",
            encoding="utf-8")
        return str(tmp_path)

    def test_deballe_nonempty_unites(self):
        assert _deballe_nonempty("Nonempty (Fam v)") == "Fam v"
        assert _deballe_nonempty("Nonempty (LerayHopfSolution ν data)") == \
            "LerayHopfSolution ν data"
        assert _deballe_nonempty("Nonempty Fam") == "Fam"
        assert _deballe_nonempty("Fam v") is None
        assert _deballe_nonempty("NonemptyT") is None
        assert _deballe_nonempty("") is None

    def test_connexion_forte(self):
        assert _connexion_forte("Nat", "Nat")
        assert _connexion_forte("List Nat", "List Bool")
        assert not _connexion_forte("Nonempty Fam", "Fam")
        assert not _connexion_forte("Nat", "Bool")

    def test_choisir_arg_deballe(self):
        expr, deb = _choisir_arg(
            "f", "Fam",
            [("h", "T"), ("h_chem1", "Nonempty Fam")], set())
        assert expr == "h_chem1_choix"
        assert deb == ("h_chem1_choix", "Fam",
                       "Classical.choice h_chem1")

    def test_choisir_arg_forte_sans_deballage(self):
        # Quand le type colle fort, pas de déballage superflu.
        expr, deb = _choisir_arg(
            "h", "Nonempty (Fam v)",
            [("h", "Nonempty (Fam v)")], set())
        assert (expr, deb) == ("h", None)

    def test_chemin_nonempty_bout_en_bout(self, chaine_nonempty):
        res = chemins("but", chaine_nonempty)
        assert res["statut"] == "TROUVÉ"
        ch = next(c for c in res["chemins"]
                  if c["noms"] == ["fam_nonempty", "closure"])
        sq = ch["squelette"]
        assert "have h_chem1_choix : Fam := Classical.choice h_chem1" in sq
        assert "exact closure h h_chem1_choix" in sq
        for ch2 in res["chemins"]:
            assert ch2["cout"] <= ch2["budget"]


class TestBudgetElague:
    @pytest.fixture
    def gros_lemme(self, tmp_path):
        # Un lemme qui se connecte au but mais dont la complexité Φ
        # dépasse forcément le budget : l'inégalité doit l'exclure.
        gros_type = "Gros " * 200
        (tmp_path / "A.lean").write_text(
            "theorem petit (n : Nat) (h : P n) : R n := by trivial\n"
            "theorem petit2 (n : Nat) (h : P n) : R n := by trivial\n"
            "theorem petit3 (n : Nat) (h : P n) : R n := by trivial\n"
            f"theorem geant (n : Nat) (h : P n) : R ({gros_type.strip()}) "
            ":= by trivial\n"
            "theorem but (n : Nat) (h : P n) : R n := by\n"
            "  sorry\n",
            encoding="utf-8")
        return str(tmp_path)

    def test_lemme_hors_budget_exclu(self, gros_lemme):
        # Critère §6.1 : l'inégalité mord — le géant connecté est exclu.
        res = chemins("but", gros_lemme)
        assert res["statut"] == "TROUVÉ"
        noms = [n for ch in res["chemins"] for n in ch["noms"]]
        assert "petit" in noms
        assert "geant" not in noms

    def test_explosion_contenue(self, tmp_path):
        # 12 lemmes tous connectés entre eux : l'exhaustif naïf exploserait
        # (12^profondeur) ; le budget + le chaînage strict le contiennent.
        lignes = []
        for i in range(12):
            lignes.append(
                f"theorem l{i} (n : Nat) (h : T{i} n) : T{i + 1} n "
                ":= by trivial")
        lignes.append("theorem but (n : Nat) (h : T0 n) : T12 n := by\n"
                      "  sorry\n")
        (tmp_path / "A.lean").write_text("\n".join(lignes), encoding="utf-8")
        res = chemins("but", str(tmp_path))
        assert res["statut"] == "TROUVÉ"
        # L'exploration reste très inférieure à la borne naïve 12^n_max.
        n_max = res["budget"]["n_max"]
        assert res["prefixes_explores"] < 12 ** n_max
        for ch in res["chemins"]:
            assert ch["cout"] <= ch["budget"]


class TestIndecideAPriori:
    @pytest.fixture
    def sans_connexion(self, tmp_path):
        (tmp_path / "A.lean").write_text(
            "theorem loin (x : Nat) : Lointain x := by trivial\n"
            "theorem but (n : Nat) : Inatteignable n := by\n"
            "  sorry\n",
            encoding="utf-8")
        return str(tmp_path)

    def test_indecide_sans_lean(self, sans_connexion, monkeypatch):
        # Critère §6.3 : aucun chemin admissible → INDÉCIDÉ a priori,
        # verdict_lean ne doit JAMAIS être appelé.
        def interdit(*a, **k):
            raise AssertionError("Lean ne doit pas être appelé")
        monkeypatch.setattr(
            "phi_complexity.chemins_verifiables.verdict_lean", interdit)
        res = realiser_chemins("but", sans_connexion, verifier=True)
        assert res["statut"] == "INDÉCIDÉ"
        assert res["a_priori"] is True
        assert res["archive"] == []
        assert "aucun build Lean brûlé" in res["raison"]

    def test_rendu_console_indecide(self, sans_connexion):
        txt = rendre_chemins(chemins("but", sans_connexion))
        assert "INDÉCIDÉ (a priori)" in txt


class TestR4Propriete:
    def test_archive_precede_lean(self, chaine_prouvee, monkeypatch):
        # Critère §6.5 : chaque appel Lean est précédé de l'archive de sa
        # preuve d'admissibilité ; chaque entrée vérifie C(P) ≤ B(S).
        appels = []

        def faux_verdict(chemin_fichier, dossier_lean, timeout_s=600):
            appels.append(chemin_fichier)
            return {"verdict": "RÉFUTÉ", "diagnostic": "test"}

        monkeypatch.setattr(
            "phi_complexity.chemins_verifiables.verdict_lean", faux_verdict)
        res = realiser_chemins("but", chaine_prouvee, verifier=True,
                               max_candidats=3)
        assert res["statut"] == "TROUVÉ"
        assert len(res["archive"]) == len(appels) == len(res["chemins"])
        for entree in res["archive"]:
            assert entree["cout"] <= entree["budget"]
            assert entree["verdict"] == "RÉFUTÉ"
        assert all(c["verdict"] == "RÉFUTÉ" for c in res["chemins"])

    def test_pas_de_verification_pas_d_archive(self, chaine_prouvee):
        res = realiser_chemins("but", chaine_prouvee, verifier=False)
        assert res["archive"] == []


@pytest.mark.skipif(
    not os.path.isdir(os.path.expanduser("~/workspace/lean-navier-stokes")),
    reason="dossier Lean réel absent (opt-in, comme les sondes)")
class TestDemoLerayExistence:
    # Critère §6.2 : la formulation n'exclut pas ce qui marchait en
    # mono-source — les 3 chemins de la démo du 2026-10-01 restent admis.
    DOSSIER = os.path.expanduser("~/workspace/lean-navier-stokes")

    def test_trois_chemins_demo_admis(self):
        # max_candidats large : on teste l'admission par le BUDGET, pas la
        # tranche opérationnelle (réglée par l'utilisateur).
        res = chemins("leray_existence", self.DOSSIER, max_candidats=500)
        assert res["statut"] == "TROUVÉ"
        noms = {n for ch in res["chemins"] for n in ch["noms"]}
        for attendu in ("bridge_leray_existence", "leray_hopf_conditional",
                        "leray_existence_discharge"):
            assert attendu in noms, f"{attendu} exclu par le budget !"
        for ch in res["chemins"]:
            assert ch["cout"] <= ch["budget"]


class TestCLI:
    def _ns(self, dossier, **kw):
        from types import SimpleNamespace
        base = dict(dossier=dossier, sorry="but", registre=None,
                    max_candidats=12, verifier=False, timeout=600,
                    garder=False, format="console", multi=True)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_multi_code_0(self, chaine_prouvee, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(self._ns(chaine_prouvee)) == 0
        assert "etape1 → etape2" in capsys.readouterr().out

    def test_multi_code_2_introuvable(self, chaine_prouvee, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(
            self._ns(chaine_prouvee, sorry="zzz")) == 2

    def test_multi_json(self, chaine_prouvee, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(
            self._ns(chaine_prouvee, format="json")) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["statut"] == "TROUVÉ"
