"""
test_veille_alternatif.py — Tests unitaires ALTERNATIFS de La Veille.

Angle différent des tests existants (test_veille.py) : au lieu de vérifier
les scénarios nominaux, ces tests PROBENT les zones grises observées en
production (2026-10-02) :

  A. Nouveau symbole AVEC trou → DÉGRADATION DÉTECTÉE (durcissement
     2026-10-02 : un trou dans un nouveau symbole est un signal dur,
     distinct des trous sur symboles existants).
  B. Trou réparé avec décalage de lignes + corps très différent.
     La clé (fichier, nom) est-elle robuste au déplacement ?
  C. Isolation du postérieur : modifier le fichier A ne doit pas faire
     dériver le postérieur des symboles du fichier B (fichiers disjoints,
     sans dépendance).
  D. Churn uniforme : toucher un fichier fait-il dériver uniformément
     TOUS ses symboles (terme churn par fichier) ?

Ces tests utilisent des fichiers de tests unitaires synthétiques
(mini-projets Python et Lean).
"""

import os

import pytest

from phi_complexity.veille import (
    prendre_snapshot,
    ecrire_snapshot,
    charger_reference,
    comparer,
)


def _grammaire_lean_ok() -> bool:
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("lean")
        return True
    except Exception:
        return False


def _snapshotiser(dossier, tmp_path, nom="ref.json"):
    ref = str(tmp_path / nom)
    ecrire_snapshot(dossier, ref)
    return ref


@pytest.fixture()
def projet_deux_fichiers(tmp_path):
    """Deux fichiers Python indépendants (aucune dépendance croisée)."""
    (tmp_path / "a.py").write_text(
        "def f_a(x):\n    return x + 1\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text(
        "def f_b1(x):\n    return x * 2\n\n"
        "def f_b2(x):\n    total = 0\n"
        "    for i in range(3):\n        total += i\n"
        "    return total + x\n",
        encoding="utf-8",
    )
    return str(tmp_path)


# ────────────────────────────────────────────────────────
# A. Nouveau symbole AVEC trou → DÉGRADATION DÉTECTÉE (durcissement 2026-10-02).
# ────────────────────────────────────────────────────────

@pytest.mark.skipif(not _grammaire_lean_ok(),
                    reason="grammaire tree-sitter 'lean' indisponible")
def test_nouveau_symbole_avec_trou_declenche_degradation(tmp_path):
    """Durcissement 2026-10-02 : un NOUVEAU symbole avec un sorry fait
    basculer le verdict (avant : STABLE, simple information de gain).
    Le trou apparaît dans `trous_nouveaux_symboles` avec un signal dédié,
    distinct de `nouveaux_trous` (trous sur symboles existants)."""
    dossier = str(tmp_path / "projA")
    os.makedirs(dossier)
    (tmp_path / "projA" / "m.lean").write_text(
        "theorem t1 : True := trivial\n", encoding="utf-8"
    )
    ref = _snapshotiser(dossier, tmp_path, "refA.json")
    # Nouveau fichier, avec un trou.
    (tmp_path / "projA" / "n.lean").write_text(
        "theorem t2 : True := by\n  sorry\n", encoding="utf-8"
    )
    diff = comparer(charger_reference(ref), dossier)
    assert diff["verdict"] == "DÉGRADATION DÉTECTÉE"
    assert diff["nouveaux_trous"] == []
    assert len(diff["trous_nouveaux_symboles"]) == 1
    assert diff["trous_nouveaux_symboles"][0]["nom"] == "t2"
    assert any("nouveaux symboles" in s for s in diff["signaux"])


# ────────────────────────────────────────────────────────
# B. Trou réparé avec décalage de lignes + corps très différent.
# ────────────────────────────────────────────────────────

@pytest.mark.skipif(not _grammaire_lean_ok(),
                    reason="grammaire tree-sitter 'lean' indisponible")
def test_trou_repare_malgre_decalage_lignes(tmp_path):
    """Un sorry fermé reste détecté comme 'trou réparé' même si :
    - des lignes sont ajoutées AVANT (décalage de la ligne du symbole),
    - le corps de la preuve est très différent (long vs sorry)."""
    dossier = str(tmp_path / "projB")
    os.makedirs(dossier)
    (tmp_path / "projB" / "m.lean").write_text(
        "theorem t1 : True := by\n  sorry\n", encoding="utf-8"
    )
    ref = _snapshotiser(dossier, tmp_path, "refB.json")
    (tmp_path / "projB" / "m.lean").write_text(
        "-- commentaire ajouté avant (décale les lignes)\n"
        "-- deuxième ligne de commentaire\n"
        "theorem aux1 : True := trivial\n"
        "theorem t1 : True := by\n"
        "  have h1 : True := trivial\n"
        "  have h2 : True := trivial\n"
        "  exact h1\n",
        encoding="utf-8",
    )
    diff = comparer(charger_reference(ref), dossier)
    assert len(diff["trous_repares"]) == 1
    assert diff["trous_repares"][0]["nom"] == "t1"
    assert diff["verdict"] == "STABLE"


# ────────────────────────────────────────────────────────
# C. Isolation du postérieur entre fichiers disjoints.
# ────────────────────────────────────────────────────────

def test_posterieur_isole_fichiers_disjoints(projet_deux_fichiers, tmp_path):
    """Modifier uniquement a.py ne doit PAS faire dériver le postérieur
    des symboles de b.py (aucune dépendance croisée, pas de git → churn neutre)."""
    ref = _snapshotiser(projet_deux_fichiers, tmp_path, "refC.json")
    # On touche seulement a.py (ajout d'un symbole).
    (tmp_path / "a.py").write_text(
        "def f_a(x):\n    return x + 1\n\n"
        "def f_a2(x):\n    return x + 2\n",
        encoding="utf-8",
    )
    diff = comparer(charger_reference(ref), projet_deux_fichiers)
    # Les symboles de b.py ne doivent pas dériver.
    derives_b = [d for d in diff["derive_posterieure"]
                 if d["fichier"] == "b.py"]
    assert derives_b == [], f"dérive inattendue sur b.py : {derives_b}"


# ────────────────────────────────────────────────────────
# D. Churn uniforme par fichier (dépôt git réel).
# ────────────────────────────────────────────────────────

def test_churn_uniforme_par_fichier(tmp_path):
    """Dans un dépôt git, toucher un fichier (nouveau commit) fait dériver
    le postérieur de TOUS ses symboles du même delta (terme churn par fichier).
    Test de caractérisation du comportement observé en production."""
    import subprocess
    dossier = str(tmp_path / "projD")
    os.makedirs(dossier)
    (tmp_path / "projD" / "m.py").write_text(
        "def f1(x):\n    return x\n\ndef f2(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", dossier], check=True)
    subprocess.run(["git", "-C", dossier, "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", dossier, "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", dossier, "add", "."], check=True)
    subprocess.run(["git", "-C", dossier, "commit", "-qm", "init"], check=True)
    ref = _snapshotiser(dossier, tmp_path, "refD.json")

    # Deuxième commit qui touche m.py.
    (tmp_path / "projD" / "m.py").write_text(
        "def f1(x):\n    return x\n\ndef f2(x):\n    return x + 1\n\n# commentaire\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", dossier, "add", "."], check=True)
    subprocess.run(["git", "-C", dossier, "commit", "-qm", "touch"], check=True)

    diff = comparer(charger_reference(ref), dossier)
    derives = [d for d in diff["derive_posterieure"]
               if d["fichier"] == "m.py"]
    # Caractérisation : les deux symboles dérivent du MÊME delta
    # (le terme churn est par fichier, pas par symbole).
    assert len(derives) == 2, f"attendu 2 dérives, obtenu : {derives}"
    deltas = {d["delta"] for d in derives}
    assert len(deltas) == 1, f"deltas non uniformes : {derives}"
