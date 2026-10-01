"""tests/test_scoreur_type.py — Scoreur à pertinence typée (2026-10-01).

Le scoreur lexical promouvait des lemmes hors-sujet : pour
`local_uniqueness`, le top-3 était dominé par `face_volume_finite`
(recouvrement de jetons génériques `Set`/`Fin`, aucune connexion
typée avec l'unicité des solutions). Trois corrections :

S1. `_symbole_tete` voit à travers les `∀` de tête et reconnaît les
    infixes (`=` → `Eq`, `↔` → `Iff`, `∃` → `Exists`) : la tête logique
    de `local_uniqueness` est `Eq`, pas `""`.
S2. Recouvrement de jetons pondéré par rareté (IDF relative au pool) :
    un jeton présent dans tout le pool pèse 0.
S3. Bonus de lieurs typés : le lemme consomme ce que le sorry fournit
    (têtes de types, pas noms de lieurs).

S4. Bonus de tête pondéré par rareté (2026-10-01) : une tête partagée par
    85 % du pool (`Eq`) ne vaut presque plus rien (2 × IDF) ; la proximité
    de module devient un a priori faible (1 / 0.5 / 1), jamais décisive
    seule. La preuve typée directe domine.
"""
import os

import pytest

from phi_complexity.chemins_verifiables import (
    _contexte_cablage,
    _depouiller_lieurs,
    _poids_idf,
    _symbole_tete,
)


class TestTeteLogique:
    def test_voit_a_travers_forall(self):
        assert _symbole_tete(
            "∀ x, ∀ t ∈ Set.Ico (0:ℝ) T, sol₁.u x t = sol₂.u x t") == "Eq"

    def test_virgule_dans_parens_ne_coupe_pas(self):
        assert _depouiller_lieurs(
            "∀ f : (ℕ → ℝ), ∀ x, P (f x, x)") == "P (f x, x)"

    def test_iff(self):
        assert _symbole_tete("ClayStatementB ↔ ¬ ClayStatementA") == "Iff"

    def test_exists(self):
        assert _symbole_tete("∃ T, 0 < T ∧ ∃ sol, CoversTime ν data sol T"
                             ) == "Exists"

    def test_and(self):
        assert _symbole_tete("A ∧ B") == "And"

    def test_pas_de_faux_egal(self):
        # `=>` et `==` ne sont pas des égalités propositionnelles.
        assert _symbole_tete("f = fun x => x") == "Eq"  # vrai `=` présent
        assert _symbole_tete("a == b") != "Eq"

    def test_tete_simple_inchangee(self):
        assert _symbole_tete("Integrable f volume") == "Integrable"


class TestPoidsIdf:
    def test_ubiquitaire_pese_zero(self):
        idf = _poids_idf({"Set": 10, "ClassicalSolution": 1}, 10)
        assert idf["Set"] == pytest.approx(0.0)
        assert idf["ClassicalSolution"] == pytest.approx(1.0)

    def test_moitie_pese_moitie(self):
        idf = _poids_idf({"X": 4}, 16)
        assert idf["X"] == pytest.approx(0.5)

    def test_pool_degenere(self):
        assert _poids_idf({"X": 1}, 1) == {"X": 0.0}


class TestInversionSynthetique:
    @pytest.fixture
    def dossier(self, tmp_path):
        (tmp_path / "S.lean").write_text(
            "theorem bon (s : Solution) (h : HasData s) :\n"
            "    ∀ t, BehavesAt s t := by trivial\n"
            "theorem bruit (s : Solution) : HasData s := by trivial\n"
            "theorem but (s : Solution) (h : HasData s) :\n"
            "    ∀ t, BehavesAt s t := by\n"
            "  sorry\n",
            encoding="utf-8")
        return str(tmp_path)

    def test_bon_devance_bruit(self, dossier):
        ctx = _contexte_cablage("but", dossier, None)
        notes = {d.nom: (score, raisons)
                 for d, score, raisons in ctx["scored"]}
        assert notes["bon"][0] > notes["bruit"][0]
        raisons_bon = " ".join(notes["bon"][1])
        assert "tête logique" in raisons_bon
        assert "lieurs typés" in raisons_bon

    def test_tete_sorry_eq(self, dossier):
        ctx = _contexte_cablage("but", dossier, None)
        assert ctx["tete_sorry"] == "BehavesAt"


@pytest.mark.skipif(
    not os.path.isdir(os.path.expanduser("~/workspace/lean-navier-stokes")),
    reason="dossier Lean réel absent (opt-in, comme les sondes)")
class TestScoreurReel:
    DOSSIER = os.path.expanduser("~/workspace/lean-navier-stokes")

    def test_uniqueness_surface_la_decharge(self):
        # Le vrai lemme de décharge existe (Part63) : le scoreur typé
        # doit le remonter, et le bruit géométrique doit disparaître
        # du top.
        ctx = _contexte_cablage("local_uniqueness", self.DOSSIER, None)
        top = sorted(ctx["scored"], key=lambda t: -t[1])[:12]
        noms = [d.nom for d, _, _ in top]
        assert "local_uniqueness_discharge" in noms
        assert not any(n.startswith("face_volume") for n in noms[:3])

    def test_tete_sorry_non_vide(self):
        ctx = _contexte_cablage("local_uniqueness", self.DOSSIER, None)
        assert ctx["tete_sorry"] == "Eq"

    def test_decharge_dans_top_soumis(self):
        # Test OPÉRATIONNEL (S4) : le vrai lemme de décharge doit figurer
        # dans le top-3 du pool noté — c'est ce top qui alimente les chemins
        # effectivement soumis à Lean. Si le bruit lexical (égalités
        # génériques du même fichier) peut encore le noyer, le scoreur
        # n'est pas corrigé et ce test échoue.
        ctx = _contexte_cablage("local_uniqueness", self.DOSSIER, None)
        top3 = sorted(ctx["scored"], key=lambda t: -t[1])[:3]
        noms3 = [d.nom for d, _, _ in top3]
        assert "local_uniqueness_discharge" in noms3, (
            f"top-3 = {noms3}")

    def test_tete_ubiquitaire_ne_domine_plus(self):
        # Une tête partagée par ~85 % du pool ne doit plus porter le score
        # à elle seule : son bonus (2 × IDF) est négligeable.
        ctx = _contexte_cablage("local_uniqueness", self.DOSSIER, None)
        par_nom = {d.nom: (s, rs) for d, s, rs in ctx["scored"]}
        s_eq, rs_eq = par_nom["euclidean_normSq_eq"]
        bonus_tete = sum(
            1 for r in rs_eq if "même tête logique" in r)
        # L'égalité générique ne survit que par l'a priori de module faible,
        # pas par sa tête : elle doit rester sous le lemme de décharge.
        s_dech, _ = par_nom["local_uniqueness_discharge"]
        assert s_dech > s_eq
