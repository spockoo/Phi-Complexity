"""tests/test_chemins_b6_memoire.py — B6 : la recherche multi-lemmes est
bornée en MÉMOIRE, pas seulement en travail.

Contexte : le scoreur typé (tête logique à travers les `∀`) densifie le
graphe de chaînage (local_uniqueness : 172 → 1541 candidats, 6k → 685k
arêtes) et la recherche se faisait tuer par manque de mémoire (OOM,
sortie vide). B6 verrouille :

1. seuls les `max_candidats` meilleurs chemins sont retenus — top-K
   EXACT (la sortie n'utilisait jamais que ceux-là) ;
2. `admissibles_total` reste un compteur exact, `admissibles_tronques`
   signale honnêtement la troncature ;
3. les squelettes ne sont générés que pour les retenus finaux ;
4. la file de recherche est plafonnée (compaction vers la moitié la
   moins chère, signalée par `file_bornee` — même justification que
   `max_prefixes`).
"""
import os

import pytest

from phi_complexity.chemins_verifiables import chemins


@pytest.fixture
def dense(tmp_path):
    """Graphe de chaînage dense : 25 égalités qui se connectent toutes."""
    lignes = "".join(
        f"theorem eq_{i} : a_{i} = b_{i} := rfl\n" for i in range(25))
    lignes += "theorem but : x = y := sorry\n"
    (tmp_path / "D.lean").write_text(lignes)
    return str(tmp_path)


def test_top_k_exact(dense):
    """Le top-K borné est un préfixe exact d'une passe plus large."""
    etroit = chemins("but", dense, chemin_registre="/nonexistent",
                     max_candidats=5, max_prefixes=2000)
    large = chemins("but", dense, chemin_registre="/nonexistent",
                    max_candidats=0, max_prefixes=2000)
    assert etroit["statut"] == "TROUVÉ"
    noms_etroit = [tuple(c["noms"]) for c in etroit["chemins"]]
    noms_large = [tuple(c["noms"]) for c in large["chemins"]]
    assert noms_etroit == noms_large[:5]
    # même tri final dans les deux passes
    cles = [(-c["score"], c["cout"], c["noms"]) for c in large["chemins"]]
    assert cles == sorted(cles)


def test_compteur_total_exact_et_troncature(dense):
    """admissibles_total est exact ; la troncature est signalée."""
    res = chemins("but", dense, chemin_registre="/nonexistent",
                  max_candidats=5, max_prefixes=2000)
    assert res["admissibles_total"] > len(res["chemins"])
    assert res["admissibles_tronques"] is True
    assert res["chemins_retenus"] == len(res["chemins"]) == 5


def test_pas_de_troncature_quand_tout_tient(tmp_path):
    """Sans dépassement, le drapeau reste faux et le total est le tout."""
    (tmp_path / "M.lean").write_text(
        "theorem lem_a : 1 = 1 := rfl\n"
        "theorem but : 1 = 1 := sorry\n")
    res = chemins("but", str(tmp_path), chemin_registre="/nonexistent",
                  max_candidats=12, max_prefixes=500)
    assert res["statut"] == "TROUVÉ"
    assert res["admissibles_tronques"] is False
    assert res["admissibles_total"] == len(res["chemins"])


def test_squelettes_presents_pour_les_retenus(dense):
    """Les squelettes différés existent pour chaque chemin retenu."""
    res = chemins("but", dense, chemin_registre="/nonexistent",
                  max_candidats=5, max_prefixes=2000)
    for ch in res["chemins"]:
        assert ch["squelette"], f"squelette manquant pour {ch['noms']}"
        assert "exact" in ch["squelette"] or "have" in ch["squelette"]


def test_file_bornee_declenchee_et_termine(dense):
    """Graphe dense + petite borne : la file est compactée, ça termine,
    et c'est signalé honnêtement."""
    res = chemins("but", dense, chemin_registre="/nonexistent",
                  max_candidats=5, max_prefixes=30)
    assert res["statut"] == "TROUVÉ"
    assert res["file_bornee"] is True
    assert res["admissibles_total"] > 0


def test_indetermine_inchange(tmp_path):
    """Le régime INDÉCIDÉ garde ses champs, avec les nouveaux drapeaux."""
    (tmp_path / "M.lean").write_text(
        "theorem lem : P x := trivial\n"
        "theorem but : Q y := sorry\n")
    res = chemins("but", str(tmp_path), chemin_registre="/nonexistent",
                  max_candidats=5, max_prefixes=500)
    assert res["statut"] == "INDÉCIDÉ"
    assert res["admissibles_total"] == 0
    assert res["file_bornee"] is False
