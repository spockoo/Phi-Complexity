"""Tests de l'élagage réel : nomenclature à quatre zones, registre des
IMPOSSIBLE_VALIDÉ, et retrait effectif des directions validées par Lean.

Discipline : docs/DISCIPLINE_ELAGAGE_REEL.md — seul Lean promeut
CANDIDAT_IMPOSSIBLE en IMPOSSIBLE_VALIDÉ.
"""
import json
import os

import pytest

from phi_complexity.chemins_verifiables import (
    _atteignabilite,
    _chemin_registre_impossibles,
    candidats_cablage,
    directions_impossibles,
    entrees_legacy,
    empreinte_enonce,
    ErreurRegistre,
    est_impossible_valide,
    inscrire_impossible_valide,
    lire_impossibles_valides,
    mesurer_directions,
)


@pytest.fixture
def registre_isole(tmp_path, monkeypatch):
    """Registre redirigé vers un fichier temporaire (jamais ~/.phi réel)."""
    chemin = str(tmp_path / "impossibles_valides.json")
    monkeypatch.setattr(
        "phi_complexity.chemins_verifiables._chemin_registre_impossibles",
        lambda: chemin)
    return chemin


@pytest.fixture
def dossier_elagage(tmp_path):
    (tmp_path / "S.lean").write_text(
        "structure Sol where\n"
        "  u : Nat → Nat\n"
        "  h : 0 < 1\n"
        "  mom : u 0 = 0\n"
        "theorem but (s : Sol) : True := by\n"
        "  sorry\n"
        "theorem cand (u : Nat → Nat) (hm : u 0 = 0)\n"
        "    (hr : ∀ y, ∀ x, P x u) : True :=\n"
        "  trivial\n",
        encoding="utf-8")
    return str(tmp_path)


class TestNomenclature:
    def test_atteignabilite_ne_rend_jamais_impossible_brut(self):
        # Le classifieur statique ne prétend jamais IMPOSSIBLE_VALIDÉ.
        for t1, t2 in [("Nat", "Nat"), ("Nat", "String"), ("Nat", "∀ x, Nat")]:
            zone, _ = _atteignabilite(t1, t2, {})
            assert zone in {"ATTEIGNABLE", "CANDIDAT_IMPOSSIBLE", "INCONNU"}, zone
            assert zone != "IMPOSSIBLE_VALIDÉ"

    def test_mismatch_decisif_est_candidat(self):
        zone, raison = _atteignabilite("Nat", "String", {})
        assert zone == "CANDIDAT_IMPOSSIBLE"
        assert "incompatibles" in raison


EMP = empreinte_enonce("theorem but (s : Sol) : True")


class TestRegistre:
    def test_registre_vide_par_defaut(self, registre_isole):
        assert lire_impossibles_valides() == {}
        assert not est_impossible_valide("s", "c", "trou", "Nat", "terme",
                                         EMP)

    def test_inscrire_puis_lire(self, registre_isole):
        inscrire_impossible_valide("but", "cand", "h", "Nat", "s",
                                   "têtes incompatibles", "Solidite_but_0_0.lean",
                                   EMP)
        reg = lire_impossibles_valides()
        assert len(reg) == 1
        assert est_impossible_valide("but", "cand", "h", "Nat", "s", EMP)
        assert not est_impossible_valide("but", "cand", "h", "Nat", "autre",
                                         EMP)
        assert not est_impossible_valide("autre_sorry", "cand", "h", "Nat",
                                         "s", EMP)

    def test_registre_persiste_sur_disque(self, registre_isole):
        fiche = inscrire_impossible_valide("but", "cand", "h", "Nat", "s",
                                           "r", "f.lean", EMP)
        data = json.loads(open(registre_isole, encoding="utf-8").read())
        assert len(data) == 1
        fiche_lue = next(iter(data.values()))
        assert fiche_lue["type_trou"] == "Nat"
        assert fiche_lue["candidat"] == "cand"
        assert fiche_lue["empreinte_enonce"] == EMP
        assert fiche_lue["schema"] == 2
        assert "date" in fiche_lue
        assert fiche == fiche_lue

    def test_registre_corrompu_leve_erreur_visible(self, registre_isole):
        # Durcissement : la corruption n'est plus un {} silencieux.
        with open(registre_isole, "w", encoding="utf-8") as fh:
            fh.write("pas du json {{{")
        with pytest.raises(ErreurRegistre, match="corrompu"):
            lire_impossibles_valides()

    def test_registre_racine_non_objet_leve(self, registre_isole):
        with open(registre_isole, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        with pytest.raises(ErreurRegistre, match="corrompu"):
            lire_impossibles_valides()


class TestRegistreDurci:
    """Schéma v2 (2026-10-01) : clé complète + empreinte d'énoncé,
    écriture atomique, legacy non réutilisé, échecs visibles."""

    def test_normalisation_espaces_meme_cle(self, registre_isole):
        inscrire_impossible_valide("but", "cand", "h", "Nat  →\n Nat", "s",
                                   "r", "f.lean", EMP)
        assert est_impossible_valide("but", "cand", "h", "Nat → Nat", "s",
                                     EMP)
        assert est_impossible_valide("but", "cand", "h", "  Nat → Nat  ",
                                     "s", EMP)

    def test_changement_enonce_invalide_les_validations(self,
                                                       registre_isole):
        # Le corpus évolue (énoncé différent) → l'ancienne validation
        # ne s'applique plus silencieusement.
        inscrire_impossible_valide("but", "cand", "h", "Nat", "s",
                                   "r", "f.lean", EMP)
        autre = empreinte_enonce("theorem but (s : Sol) (t : Nat) : True")
        assert autre != EMP
        assert not est_impossible_valide("but", "cand", "h", "Nat", "s",
                                         autre)

    def test_meme_direction_autre_candidat_cle_distincte(self,
                                                        registre_isole):
        inscrire_impossible_valide("but", "cand1", "h", "Nat", "s",
                                   "r", "f.lean", EMP)
        assert not est_impossible_valide("but", "cand2", "h", "Nat", "s",
                                         EMP)

    def test_legacy_non_reutilise_mais_compte(self, registre_isole):
        # Entrée v1 (ancienne clé sans empreinte) : visible, jamais
        # réutilisée pour l'élagage.
        with open(registre_isole, "w", encoding="utf-8") as fh:
            json.dump({"but\x00h\x00s": {"type_trou": "Nat"}}, fh)
        reg = lire_impossibles_valides()
        assert entrees_legacy(reg) == 1
        assert not est_impossible_valide("but", "cand", "h", "Nat", "s",
                                         EMP, reg)

    def test_ecriture_atomique_pas_de_tronque(self, registre_isole):
        # Après inscription, le fichier est un JSON complet valide et
        # aucun .tmp ne traîne.
        inscrire_impossible_valide("but", "cand", "h", "Nat", "s",
                                   "r", "f.lean", EMP)
        data = json.loads(open(registre_isole, encoding="utf-8").read())
        assert len(data) == 1
        restes = [f for f in os.listdir(os.path.dirname(registre_isole))
                  if f.startswith("impossibles_valides.json.tmp")]
        assert restes == []

    def test_inscription_impossible_leve(self, registre_isole,
                                        monkeypatch):
        # Dossier non inscriptible → ErreurRegistre visible, pas de
        # `except: pass`.
        monkeypatch.setattr(
            "phi_complexity.chemins_verifiables._chemin_registre_impossibles",
            lambda: "/proc/impossible/ir.lean.json")
        with pytest.raises(ErreurRegistre, match="non inscriptible"):
            inscrire_impossible_valide("but", "cand", "h", "Nat", "s",
                                       "r", "f.lean", EMP)

    def test_mesure_signale_registre_corrompu(self, dossier_elagage,
                                             registre_isole):
        # La mesure continue mais le meta signale la corruption.
        with open(registre_isole, "w", encoding="utf-8") as fh:
            fh.write("{{{")
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        assert res["statut"] == "TROUVÉ"
        meta = res["registre"]
        assert meta["avertissement"] is not None
        assert "corrompu" in meta["avertissement"]

    def test_mesure_compte_legacy(self, dossier_elagage, registre_isole):
        with open(registre_isole, "w", encoding="utf-8") as fh:
            json.dump({"but\x00h\x00s": {"type_trou": "Nat"}}, fh)
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        meta = res["registre"]
        assert meta["entrees_legacy"] == 1
        assert meta["entrees_v2"] == 0
        assert meta["avertissement"] is None


class TestElagageReel:
    def test_direction_validee_est_elaguee(self, dossier_elagage, registre_isole):
        # Mesure avant : des CANDIDAT_IMPOSSIBLE ouverts.
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        assert res["statut"] == "TROUVÉ"
        cand = res["candidats"][0]
        avant = cand["directions"]["directions_ouvertes"]
        imposs = cand["impossibles"]
        assert imposs, "le fixture doit produire des CANDIDAT_IMPOSSIBLE"
        cible = imposs[0]
        assert avant > 0

        # Lean tranche (simulé) : on inscrit au registre.
        emp = empreinte_enonce(res["enonce_sorry"])
        inscrire_impossible_valide("but", cand["declaration"], cible["trou"],
                                   cible["type_trou"], cible["terme"],
                                   cible["raison"], "f.lean", emp)

        # Mesure après : la direction est IMPOSSIBLE_VALIDÉ, élaguée.
        res2 = candidats_cablage("but", dossier_elagage, max_candidats=1)
        cand2 = res2["candidats"][0]
        apres = cand2["directions"]["directions_ouvertes"]
        assert apres == avant - 1
        # Elle n'est plus proposée à la falsification.
        termes = {(p["trou"], p["terme"]) for p in cand2["impossibles"]}
        assert (cible["trou"], cible["terme"]) not in termes
        # Mais elle reste visible comme validée.
        valides = sum(t.get("IMPOSSIBLE_VALIDÉ", 0)
                      for t in cand2["directions"]["trous"].values())
        assert valides == 1

    def test_sans_sorry_pas_de_registre(self, dossier_elagage, registre_isole):
        # mesurer_directions sans sorry : pas de consultation du registre.
        inscrire_impossible_valide("but", "cand", "h", "Nat", "s", "r",
                                   "f.lean", EMP)
        res = candidats_cablage("but", dossier_elagage, max_candidats=1)
        assert res["candidats"][0]["directions"]["directions_ouvertes"] > 0


class TestDurcissementSolidite:
    """B8/B9 (2026-10-01) : la généralisation à `local_existence` a révélé
    que le test de solidité liait les lieurs du sorry et n'importait que
    son module — 9 INCONCLUSIF sur 16 (Lean : `Unknown identifier 'sol.u'`,
    `HasInitialData`/`BKMAnalytic` inconnus). Le test doit lier les lieurs
    du candidat et importer son module : le type du trou vit dans le
    contexte du candidat.
    """

    def _candidat_bkm(self):
        # Données réelles du run local_existence du 2026-10-01
        # (Solidite_local_existence_0_4.lean).
        return {
            "declaration": "bkm_extension_preserves_data",
            "module": "Clay_NS_Part14_Interfaces",
            "lieurs": ["(ν : ℝ)", "(data : ClayInitialData)",
                       "(sol : ClassicalSolution ν)",
                       "(hdata : HasInitialData ν data sol)"],
            "opens": ["open MeasureTheory"],
        }

    def test_b8_lie_les_lieurs_du_candidat(self):
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        contenu = fichier_validation_solidite(
            "local_existence", "Clay_NS_Master", self._candidat_bkm(), {},
            "ContinuousOn (vorticityLinf sol.u) (Set.Icc (0:ℝ) sol.horizon)",
            "ν", opens_sorry=[], raison="têtes incompatibles")
        ligne_ex = next(l for l in contenu.splitlines()
                        if l.startswith("example"))
        # `sol` (lieur du candidat) est lié ; le type du trou l'utilise.
        assert "(sol : ClassicalSolution ν)" in ligne_ex
        assert "sol.u" in contenu

    def test_b9_importe_le_module_du_candidat(self):
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        contenu = fichier_validation_solidite(
            "local_existence", "Clay_NS_Master", self._candidat_bkm(), {},
            "BKMAnalytic ν sol", "ν", opens_sorry=[],
            raison="têtes incompatibles")
        assert "import Clay_NS_Master" in contenu
        # BKMAnalytic est déclaré dans Clay_NS_Part5_BKM, importé
        # transitivement par le module du candidat — pas par celui du sorry.
        assert "import Clay_NS_Part14_Interfaces" in contenu

    def test_b9_rejoue_les_opens_du_candidat(self):
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        contenu = fichier_validation_solidite(
            "local_existence", "Clay_NS_Master", self._candidat_bkm(), {},
            "Nat", "0", opens_sorry=["open Topology"],
            raison="têtes incompatibles")
        assert "open MeasureTheory" in contenu
        assert "open Topology" in contenu

    def test_sans_univers_explicites_dans_les_lieurs(self):
        # B4 : un lieur avec univers explicite ne doit pas être recopié brut.
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        cand = {"declaration": "c", "module": "M",
                "lieurs": ["(x : Foo.{u})"], "opens": []}
        contenu = fichier_validation_solidite(
            "s", "MS", cand, {}, "Nat", "0", raison="r")
        assert ".{u}" not in contenu

    def test_b10_terme_du_sorry_lie_aussi(self):
        # Le pool de termes inclut les lieurs du sorry : `hν` testé pour
        # un trou du candidat doit être lié (cas réel Solidite_..._0_0.lean).
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        cand = {"declaration": "bkm_extension_preserves_data",
                "module": "Clay_NS_Part14_Interfaces",
                "lieurs": ["(ν : ℝ)", "(sol : ClassicalSolution ν)"],
                "groupes_complets": ["(ν : ℝ)", "(sol : ClassicalSolution ν)"],
                "univers": [], "variables": [], "opens": []}
        ctx_sorry = {"lieurs": ["(ν : ℝ)", "(hν : 0 < ν)",
                                "(data : ClayInitialData)"],
                     "univers": [], "variables": []}
        contenu = fichier_validation_solidite(
            "local_existence", "Clay_NS_Master", cand, ctx_sorry,
            "ClassicalSolution ν", "hν", raison="têtes incompatibles")
        ligne_ex = next(l for l in contenu.splitlines()
                        if l.startswith("example"))
        assert "(hν : 0 < ν)" in ligne_ex
        assert "(sol : ClassicalSolution ν)" in ligne_ex
        # `ν` lié une seule fois malgré la présence dans les deux contextes.
        assert ligne_ex.count("(ν : ℝ)") == 1

    def test_b11_universe_et_variable_emis(self):
        # Cas réel Solidite_local_existence_1_2.lean : `E` vient d'un
        # `variable (E : Type u) [NormedAddCommGroup E]` — sans lui,
        # `synthInstanceFailed`.
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        cand = {"declaration": "local_existence_conditional",
                "module": "Clay_NS_Part25_KatoBanachY",
                "lieurs": ["(ν : ℝ)"],
                "groupes_complets": ["(ν : ℝ)"],
                "univers": ["u"],
                "variables": ["(E : Type u)", "[NormedAddCommGroup E]",
                              "[NormedSpace ℝ E]"],
                "opens": []}
        contenu = fichier_validation_solidite(
            "local_existence", "Clay_NS_Master", cand, {},
            "∀ T : ℝ, KatoBilinearYData E T", "ν",
            raison="têtes incompatibles")
        assert "universe u" in contenu
        ligne_ex = next(l for l in contenu.splitlines()
                        if l.startswith("example"))
        assert "(E : Type u)" in ligne_ex
        assert "[NormedAddCommGroup E]" in ligne_ex

    def test_b11_implicites_en_tete_rejoues(self):
        # Les groupes `{...}` / `[...]` de l'en-tête sont rejoués.
        from phi_complexity.chemins_verifiables import (
            fichier_validation_solidite)
        cand = {"declaration": "c", "module": "M",
                "lieurs": ["(x : Nat)"],
                "groupes_complets": ["{E : Type*}", "[NormedAddCommGroup E]",
                                     "(x : Nat)"],
                "univers": [], "variables": [], "opens": []}
        contenu = fichier_validation_solidite(
            "s", "MS", cand, {}, "E", "x", raison="r")
        ligne_ex = next(l for l in contenu.splitlines()
                        if l.startswith("example"))
        assert "{E : Type*}" in ligne_ex
        assert "[NormedAddCommGroup E]" in ligne_ex


class TestContexteVariables:
    def test_variable_section_refermee_exclue(self):
        from phi_complexity.chemins_verifiables import _contexte_variables
        texte = (
            "universe u\n"
            "variable (E : Type u) [NormedAddCommGroup E]\n"
            "section Fermee\n"
            "variable (X : Nat)\n"
            "end Fermee\n"
            "theorem c (x : Nat) : True := trivial\n")
        univers, variables = _contexte_variables(texte, 6)
        assert univers == ["u"]
        assert "(E : Type u)" in variables
        assert "[NormedAddCommGroup E]" in variables
        assert not any("X : Nat" in g for g in variables)

    def test_analyser_entete_inclure_implicites(self):
        from phi_complexity.chemins_verifiables import analyser_entete
        entete = ("theorem c {E : Type*} [NormedAddCommGroup E] "
                  "(x : E) : True")
        groupes, _, _ = analyser_entete(entete, "c")
        assert groupes == ["(x : E)"]
        complets, _, _ = analyser_entete(
            entete, "c", inclure_implicites=True)
        assert complets == ["{E : Type*}", "[NormedAddCommGroup E]",
                            "(x : E)"]
