"""Tests unitaires de l'atteignabilité typée à trois zones (chantier 2026-10-01).

Discipline : docs/DISCIPLINE_ATTEIGNABILITE_TYPEE.md
- ATTEIGNABLE : équivalence après dépliage (± permutation des lieurs).
- IMPOSSIBLE : dépliage complet des deux côtés, têtes structurellement
  incompatibles. Prétention falsifiable : Lean doit RÉFUTER le câblage.
- INCONNU : borne atteinte avec du dépliage restant, ou mismatch non décisif
  (paire ∀/→, paire de coercition). Aveu d'ignorance, jamais un IMPOSSIBLE
  déguisé.
"""

import pytest

from phi_complexity.chemins_verifiables import (
    _atteignabilite,
    _defs_corps,
    _deplier_temoin,
    _est_depliable,
    _mismatch_irreconciliable,
    _reste_depliable,
    _structures_corpus,
    _tete,
    candidats_cablage,
    classifier_directions_trou,
    directions_impossibles,
    fichier_validation_solidite,
    interpreter_solidite,
)


class TestTete:
    def test_forall(self):
        assert _tete("∀ x, P x") == "∀"

    def test_fleche(self):
        assert _tete("A → B") == "→"

    def test_application(self):
        assert _tete("Integrable f volume") == "Integrable"

    def test_conjonction_parenthesee(self):
        assert _tete("(A ∧ B)") == "∧"

    def test_constante(self):
        assert _tete("ℝ") == "ℝ"

    def test_fleche_profonde_ignoree(self):
        # la flèche sous un lieur ne compte pas : tête = ∀
        assert _tete("∀ x, P x → Q") == "∀"


class TestMismatch:
    def test_identiques(self):
        assert not _mismatch_irreconciliable("ℝ", "ℝ")

    def test_coercition_numerique(self):
        assert not _mismatch_irreconciliable("ℕ", "ℝ")

    def test_coercition_fin(self):
        assert not _mismatch_irreconciliable("Fin", "ℕ")

    def test_forall_fleche_conservateur(self):
        # ∀ et → sont deux types pi — pas décisif au niveau des chaînes
        assert not _mismatch_irreconciliable("∀", "→")

    def test_forall_constante(self):
        assert _mismatch_irreconciliable("∀", "ℝ")

    def test_fleche_constante(self):
        assert _mismatch_irreconciliable("→", "ℝ")

    def test_constantes_distinctes(self):
        assert _mismatch_irreconciliable("Integrable", "HasSchwartzDecay")


class TestTemoin:
    def test_depliable(self):
        assert _est_depliable("A", {"A": "B"})

    def test_recursion_directe_non_depliable(self):
        assert not _est_depliable("A", {"A": "A + 1"})

    def test_corps_trop_long_non_depliable(self):
        assert not _est_depliable("A", {"A": "x" * 2001})

    def test_absent_non_depliable(self):
        assert not _est_depliable("Z", {})

    def test_temoin_complet(self):
        nt, reste = _deplier_temoin("A", {"A": "B", "B": "ℝ"})
        assert nt == "ℝ"
        assert reste is False

    def test_temoin_borne_atteinte(self):
        # chaîne de 6 defs en ordre inverse du dict : chaque passe ne déplie
        # qu'un cran, la borne à 4 stoppe avec du dépliage restant → honnête
        defs = {"F": "ℝ", "E": "F", "D": "E", "C": "D", "B": "C", "A": "B"}
        nt, reste = _deplier_temoin("A", defs)
        assert reste is True
        assert _reste_depliable(nt, defs)

    def test_deplieur_ordre_insertion(self):
        # dans l'ordre du dict, A→B puis B→ℝ ont lieu dans la même passe
        nt, reste = _deplier_temoin("A", {"A": "B", "B": "ℝ"})
        assert (nt, reste) == ("ℝ", False)

    def test_deplieur_plusieurs_noms(self):
        nt, _ = _deplier_temoin("P A B", {"A": "ℕ", "B": "ℤ"})
        assert nt == "P ℕ ℤ"

    def test_deplieur_nom_chevauchant(self):
        # "foobar" ne doit pas devenir "Xbar" : le plus long gagne
        nt, _ = _deplier_temoin("foobar", {"foo": "X", "foobar": "Y"})
        assert nt == "Y"

    def test_deplieur_recursion_directe_ignoree(self):
        # def récursive ignorée : l'entrée est rendue inchangée, et il ne
        # "reste" rien de dépliable (la récursion n'est pas dépliable)
        nt, reste = _deplier_temoin("A", {"A": "A → A"})
        assert nt == "A"
        assert reste is False

    def test_deplieur_nom_introduit_par_corps(self):
        # le corps de A introduit B, plus loin dans l'ordre : même passe
        nt, _ = _deplier_temoin("A", {"A": "F B", "B": "ℕ"})
        assert nt == "F ℕ"


class TestAtteignabilite:
    def test_atteignable_direct(self):
        zone, _ = _atteignabilite("MyR", "ℝ", {"MyR": "ℝ"})
        assert zone == "ATTEIGNABLE"

    def test_atteignable_permutation_lieurs(self):
        zone, _ = _atteignabilite("∀ x y, P x y", "∀ y x, P x y", {})
        assert zone == "ATTEIGNABLE"

    def test_impossible_fleche_vs_reel(self):
        zone, raison = _atteignabilite("ℝ", "Fin 3 → ℝ", {})
        assert zone == "IMPOSSIBLE"
        assert "→" in raison and "ℝ" in raison

    def test_impossible_forall_vs_reel(self):
        zone, _ = _atteignabilite("∀ t, P t", "ℝ", {})
        assert zone == "IMPOSSIBLE"

    def test_impossible_apres_depliage(self):
        zone, _ = _atteignabilite("MyR", "∀ t, P t", {"MyR": "ℝ"})
        assert zone == "IMPOSSIBLE"

    def test_inconnu_coercition(self):
        # ℕ → ℝ : Lean peut insérer une coercition — jamais IMPOSSIBLE
        zone, _ = _atteignabilite("ℕ", "ℝ", {})
        assert zone == "INCONNU"

    def test_inconnu_borne_atteinte(self):
        # déplié à fond, A vaudrait ℝ : mais la borne stoppe avant — honnête.
        # (chaîne en ordre inverse : chaque passe ne déplie qu'un cran)
        defs = {"F": "ℝ", "E": "F", "D": "E", "C": "D", "B": "C", "A": "B"}
        zone, raison = _atteignabilite("A", "Fin 3 → ℝ", defs)
        assert zone == "INCONNU"
        assert "borne" in raison

    def test_inconnu_mismatch_non_decisif(self):
        # même tête, arguments distincts : pas prouvablement impossible
        zone, _ = _atteignabilite("Integrable f volume",
                                  "Integrable g volume", {})
        assert zone == "INCONNU"


@pytest.fixture
def dossier_atteignabilite(tmp_path):
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


class TestDirectionsTrou:
    def test_comptes_trou_simple(self, dossier_atteignabilite):
        structs = _structures_corpus(dossier_atteignabilite)
        defs = _defs_corps(dossier_atteignabilite)
        _, comptes = classifier_directions_trou(
            "Nat → Nat", [("s", "Sol")], structs, defs, {})
        # s.u ATTEIGNABLE ; s, s.h, s.mom prouvablement impossibles
        assert comptes == {"ATTEIGNABLE": 1, "IMPOSSIBLE": 3, "INCONNU": 0}

    def test_directions_nommees(self, dossier_atteignabilite):
        structs = _structures_corpus(dossier_atteignabilite)
        defs = _defs_corps(dossier_atteignabilite)
        directions, _ = classifier_directions_trou(
            "Nat → Nat", [("s", "Sol")], structs, defs, {})
        par_terme = {t: z for t, z, _ in directions}
        assert par_terme["s.u"] == "ATTEIGNABLE"
        assert par_terme["s"] == "IMPOSSIBLE"
        assert par_terme["s.mom"] == "IMPOSSIBLE"

    def test_trou_dependant_apres_substitution(self, dossier_atteignabilite):
        # hm : u 0 = 0 avec u → s.u : s.mom devient ATTEIGNABLE
        # (sans le subst séquentiel ce serait un faux IMPOSSIBLE)
        structs = _structures_corpus(dossier_atteignabilite)
        defs = _defs_corps(dossier_atteignabilite)
        directions, comptes = classifier_directions_trou(
            "u 0 = 0", [("s", "Sol")], structs, defs, {"u": "s.u"})
        par_terme = {t: z for t, z, _ in directions}
        assert par_terme["s.mom"] == "ATTEIGNABLE"
        assert comptes["ATTEIGNABLE"] == 1


class TestMesureIntegration:
    def test_directions_annotees(self, dossier_atteignabilite):
        res = candidats_cablage("but", dossier_atteignabilite)
        assert res["statut"] == "TROUVÉ"
        par_nom = {c["declaration"]: c for c in res["candidats"]}
        mesure = par_nom["cand"]["directions"]
        assert set(mesure["trous"]) == {"u", "hm", "hr"}
        for trou, comptes in mesure["trous"].items():
            assert set(comptes) == {"ATTEIGNABLE", "IMPOSSIBLE", "INCONNU"}, trou
        # u : s.u atteignable ; hm : s.mom après subst ; hr : ?_ (1 INCONNU)
        assert mesure["trous"]["u"]["ATTEIGNABLE"] == 1
        assert mesure["trous"]["hm"]["ATTEIGNABLE"] == 1
        assert mesure["trous"]["hr"]["ATTEIGNABLE"] == 0
        assert mesure["directions_ouvertes"] == 3

    def test_squelette_inchange(self, dossier_atteignabilite):
        # non-régression : la mesure n'altère pas les squelettes
        res = candidats_cablage("but", dossier_atteignabilite)
        par_nom = {c["declaration"]: c for c in res["candidats"]}
        sq = par_nom["cand"]["squelette"]
        assert "s.u" in sq and "s.mom" in sq


class TestValidationSolidite:
    def test_fichier_minimal(self):
        contenu = fichier_validation_solidite(
            "but", "S", ["(s : Sol)"], "Nat → Nat", "s",
            opens_sorry=[], raison="têtes incompatibles")
        assert "import S" in contenu
        assert "example (s : Sol) : Nat → Nat := s" in contenu
        assert "sorry" not in contenu

    def test_fichier_rejoue_opens(self):
        contenu = fichier_validation_solidite(
            "but", "S", ["(s : Sol)"], "Nat", "s", opens_sorry=["open Foo"],
            raison="x")
        assert "open Foo" in contenu

    def test_interpreter_violation(self):
        v = {"verdict": "PROUVÉ", "diagnostic": "exit 0"}
        assert interpreter_solidite(v) == "VIOLATION"

    def test_interpreter_confirme(self):
        v = {"verdict": "RÉFUTÉ",
             "diagnostic": "error: type mismatch\n  s\nhas type\n  Sol"}
        assert interpreter_solidite(v) == "CONFIRMÉ"

    def test_interpreter_inconclusif_sans_mismatch(self):
        v = {"verdict": "RÉFUTÉ", "diagnostic": "error: unknown identifier 'zz'"}
        assert interpreter_solidite(v) == "INCONCLUSIF"

    def test_interpreter_inconclusif_timeout(self):
        v = {"verdict": "INDÉCIDÉ", "diagnostic": "timeout après 600s"}
        assert interpreter_solidite(v) == "INCONCLUSIF"

    def test_directions_impossibles_fixture(self, dossier_atteignabilite):
        from phi_complexity.chemins_verifiables import (
            _contexte_cablage, _lieurs_types, _structures_corpus, _defs_corps,
        )
        ctx = _contexte_cablage("but", dossier_atteignabilite, None)
        scored = sorted(ctx["scored"], key=lambda t: (-t[1], t[0].nom))
        d = next(d for d, _, _ in scored if d.nom == "cand")
        paires = directions_impossibles(d, ctx, dossier_atteignabilite)
        # trou u : s, s.h, s.mom impossibles ; trou hm : 3 ; trou hr : 3
        assert len(paires) == 9
        assert all(set(p) == {"trou", "type_trou", "terme", "raison"}
                   for p in paires)
        par_trou = {}
        for p in paires:
            par_trou.setdefault(p["trou"], []).append(p["terme"])
        assert set(par_trou["u"]) == {"s", "s.h", "s.mom"}
