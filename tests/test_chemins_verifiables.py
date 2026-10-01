"""tests/test_chemins_verifiables.py — Chemins vérifiables vers un sorry.

Verrouille :
- seules des déclarations theorem/lemma PROUVÉES sont candidates
  (jamais une source contenant un sorry, jamais une def, jamais le sorry) ;
- classement guidé et expliqué, borné (pas d'explosion combinatoire) ;
- squelette `refine` avec lieurs du sorry par nom, trous `?_` sinon ;
- fichier de vérification formellement identifiable comme preuve ;
- verdict Lean mécanique (PROUVÉ / RÉFUTÉ / INDÉCIDÉ), mocké ;
- CLI : codes 0/2 ; garde mission.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from phi_complexity import mission
from phi_complexity.chemins_verifiables import (
    analyser_entete,
    candidats_cablage,
    declarations_dans_fichier,
    extraire_entete,
    fichier_verification,
    rendre_console,
    verdict_lean,
    _modules_des_chantiers,
)


@pytest.fixture
def dossier(tmp_path):
    (tmp_path / "A.lean").write_text(
        "theorem cle (n : Nat) : n = n := rfl\n"
        "theorem avec_sorry : True := by\n"
        "  sorry\n",
        encoding="utf-8")
    (tmp_path / "B.lean").write_text(
        "import A\n"
        "def util : Nat := 3\n"
        "theorem pont : True := by\n"
        "  trivial\n",
        encoding="utf-8")
    (tmp_path / "C.lean").write_text(
        "import B\n"
        "theorem but : True := by\n"
        "  sorry\n",
        encoding="utf-8")
    return str(tmp_path)


class TestDeclarations:
    def test_marquee_sorry(self, dossier):
        import os
        decls = declarations_dans_fichier(
            dossier, os.path.join(dossier, "A.lean"))
        par_nom = {d.nom: d for d in decls}
        assert par_nom["cle"].a_sorry is False
        assert par_nom["avec_sorry"].a_sorry is True

    def test_entete_et_lieurs(self, dossier):
        import os
        texte = open(os.path.join(dossier, "A.lean"),
                     encoding="utf-8").read()
        entete = extraire_entete("cle", texte)
        assert entete is not None and "n = n" in entete
        groupes, noms, conclusion = analyser_entete(entete, "cle")
        assert noms == ["n"]
        assert groupes == ["(n : Nat)"]
        assert conclusion == "n = n"

    def test_entete_multiligne(self):
        texte = ("theorem discharge (ν : ℝ) (hν : 0 < ν)\n"
                 "    (data : D) :\n"
                 "    Nonempty (S ν data) := by\n")
        entete = extraire_entete("discharge", texte)
        groupes, noms, conclusion = analyser_entete(entete, "discharge")
        assert noms == ["ν", "hν", "data"]
        assert conclusion == "Nonempty (S ν data)"

    def test_lieur_implicite_exclu_du_squelette(self):
        # Diagnostiqué sur leray_hopf_conditional : passer {ν} en positionnel
        # décale tous les arguments (ν : ℝ donné là où 0 < ?m est attendu).
        texte = ("theorem cond {ν : ℝ} (hν : 0 < ν) (data : D)\n"
                 "    (fam : F ν) : Nonempty (S ν data) :=\n")
        entete = extraire_entete("cond", texte)
        groupes, noms, conclusion = analyser_entete(entete, "cond")
        assert noms == ["hν", "data", "fam"]
        assert groupes == ["(hν : 0 < ν)", "(data : D)", "(fam : F ν)"]

    def test_parentheses_imbriquees_pas_de_faux_lieurs(self):
        # Diagnostiqué sur leray_existence_discharge : le (fun x _ => ...)
        # dans le type de hE0 produisait les faux lieurs fun/x/data/u0.
        texte = ("theorem dis (ν : ℝ) (hν : 0 < ν) (data : D)\n"
                 "    (hE0 : E = kineticEnergy (fun x _ => data.u0 x) 0) :\n"
                 "    Nonempty (S ν data) := by\n")
        entete = extraire_entete("dis", texte)
        groupes, noms, conclusion = analyser_entete(entete, "dis")
        assert noms == ["ν", "hν", "data", "hE0"]
        assert "fun" not in noms and "u0" not in noms
        assert noms.count("data") == 1

    def test_valeur_par_defaut_ne_coupe_pas(self):
        texte = ("theorem dv (n : Nat := 0) (h : n = n) :\n"
                 "    n = n := by\n")
        entete = extraire_entete("dv", texte)
        assert entete is not None and "(h : n = n)" in entete
        _, noms, conclusion = analyser_entete(entete, "dv")
        assert noms == ["n", "h"]
        assert conclusion == "n = n"


class TestCandidats:
    def test_pont_premier_avec_raisons(self, dossier):
        res = candidats_cablage("but", dossier)
        assert res["statut"] == "TROUVÉ"
        noms = [c["declaration"] for c in res["candidats"]]
        assert noms[0] == "pont"
        c0 = res["candidats"][0]
        assert any("clôture d'imports" in r for r in c0["raisons"])
        assert any("même tête de conclusion" in r for r in c0["raisons"])

    def test_aucune_source_avec_sorry(self, dossier):
        res = candidats_cablage("but", dossier)
        noms = [c["declaration"] for c in res["candidats"]]
        assert "avec_sorry" not in noms  # contient un sorry
        assert "but" not in noms         # le sorry lui-même
        assert "util" not in noms        # une def n'est pas une preuve

    def test_squelette_lieurs_par_nom(self, dossier):
        res = candidats_cablage("but", dossier)
        # pont : aucun lieur ; cle (n) : n ∉ lieurs du sorry → ?_
        par_nom = {c["declaration"]: c for c in res["candidats"]}
        assert par_nom["pont"]["squelette"] == "refine pont"
        assert par_nom["cle"]["squelette"] == "refine cle ?_"

    def test_borne_anti_explosion(self, dossier):
        res = candidats_cablage("but", dossier, max_candidats=1)
        assert len(res["candidats"]) == 1

    def test_introuvable(self, dossier):
        res = candidats_cablage("n_existe_pas", dossier)
        assert res["statut"] == "INTROUVABLE"
        assert res["candidats"] == []

    def test_registre_absent_opt_in(self, dossier):
        res = candidats_cablage(
            "but", dossier, chemin_registre="/chemin/qui/n/existe/pas.md")
        assert res["statut"] == "TROUVÉ"

    def test_modules_des_chantiers(self):
        texte = ("### Ch.99 — câblage test\n"
                 "Le module `D.lean` porte la décharge.\n"
                 "### Ch.100 — autre\n"
                 "Rien ici.\n")
        mods = _modules_des_chantiers(["ch.99"], texte)
        assert mods == {"D"}

    def test_bonus_registre(self, dossier, monkeypatch):
        import phi_complexity.piste_sorry as ps
        vrai_piste = ps.piste

        def fausse_piste(nom, dos, chemin_registre=None):
            p = vrai_piste(nom, dos, chemin_registre)
            p["registre"] = {"disponible": True, "trou": 1,
                             "chantiers": [{"nom": "Ch.99", "statut": "EN-COURS",
                                            "chantiers_cites": [],
                                            "fichier_ligne": ""}]}
            return p

        monkeypatch.setattr(ps, "piste", fausse_piste)
        (open(__import__("os").path.join(dossier, "reg.md"), "w",
              encoding="utf-8").write(
            "### Ch.99 — câblage test\nLe module `A.lean` porte la décharge.\n"))
        res = candidats_cablage("but", dossier,
                                chemin_registre=__import__("os").path.join(
                                    dossier, "reg.md"))
        par_nom = {c["declaration"]: c for c in res["candidats"]}
        assert any("chantier du registre" in r
                   for r in par_nom["cle"]["raisons"])

    def test_json_serialisable(self, dossier):
        json.dumps(candidats_cablage("but", dossier), ensure_ascii=False)


class TestFichierVerification:
    def test_forme_preuve(self, dossier):
        res = candidats_cablage("but", dossier)
        c = res["candidats"][0]
        groupes, noms, conclusion = analyser_entete(
            res["enonce_sorry"], "but")
        src = fichier_verification("but", c, res["module_sorry"],
                                   groupes, conclusion)
        assert "import C" in src
        assert "import B" in src
        assert "example" in src and ":= by" in src
        assert "refine pont" in src
        # aucune tactique sorry hors commentaires
        code = "\n".join(l for l in src.splitlines()
                         if not l.strip().startswith("--"))
        assert "sorry" not in code

    def test_rendu_console(self, dossier):
        txt = rendre_console(candidats_cablage("but", dossier))
        assert "pont" in txt and "squelette" in txt
        assert "INTROUVABLE" in rendre_console(
            candidats_cablage("zzz", dossier))


class TestVerdict:
    def test_prouve(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 0, stdout="", stderr=""))
        v = verdict_lean("/tmp/x.lean", "/tmp")
        assert v["verdict"] == "PROUVÉ"

    def test_refute_avec_diagnostic(self, monkeypatch):
        err = "x.lean:9:2: unsolved goals\n⊢ fam : UniformGalerkinFamily"
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 1, stdout="", stderr=err))
        v = verdict_lean("/tmp/x.lean", "/tmp")
        assert v["verdict"] == "RÉFUTÉ"
        assert "unsolved goals" in v["diagnostic"]

    def test_indecide_timeout(self, monkeypatch):
        def leve(*a, **k):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=1)
        monkeypatch.setattr(subprocess, "run", leve)
        v = verdict_lean("/tmp/x.lean", "/tmp")
        assert v["verdict"] == "INDÉCIDÉ"


class TestCLI:
    def _ns(self, dossier, **kw):
        base = dict(dossier=dossier, sorry="but", registre=None,
                    max_candidats=12, verifier=False, timeout=600,
                    garder=False, format="console")
        base.update(kw)
        return SimpleNamespace(**base)

    def test_code_0(self, dossier, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(self._ns(dossier)) == 0

    def test_code_2_introuvable(self, dossier, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(
            self._ns(dossier, sorry="zzz")) == 2

    def test_code_1_dossier_absent(self, capsys):
        from phi_complexity.cli import _executer_chemins_verifiables
        assert _executer_chemins_verifiables(
            self._ns("/dossier/qui/n/existe/pas")) == 1


class TestGardeMission:
    def test_module_declare(self):
        assert "chemins_verifiables" in mission.modules_couverts()

    def test_justification_valide(self):
        problemes = [p for p in mission.valider()
                     if "chemins_verifiables" in p]
        assert problemes == []

    def test_sert_vocabulaire(self):
        assert (mission.JUSTIFICATIONS["chemins_verifiables"]["sert"]
                in mission.SERT_AUTORISES)
