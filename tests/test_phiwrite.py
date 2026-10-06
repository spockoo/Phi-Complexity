"""Tests de phiwrite.py — générateur de code Lean 4.

Chaque test vérifie une propriété du générateur ou du validateur,
pas une autre implémentation.
"""

import pytest

from phi_complexity.phiwrite import (
    charger_spec,
    ecrire,
    exemple_spec,
    generer,
    parentheses_equilibrees,
    valider_spec,
    valider_theoreme,
)


def _thm_base(**kw):
    """Théorème minimal valide, surchargeable."""
    base = {
        "name": "t_test",
        "imports": ["Mathlib.Logic.Basic"],
        "statement": "(a : Nat) : a = a",
        "proof": "rfl",
        "doc": "Test.",
    }
    base.update(kw)
    return base


# 1. Génération simple : la spec d'exemple produit les 3 théorèmes.
def test_generation_exemple():
    spec = exemple_spec()
    code = generer(spec)
    assert "theorem phiwrite_exemple_identite" in code
    assert "theorem phiwrite_exemple_add_comm_applique" in code
    assert "theorem phiwrite_exemple_and_intro" in code
    assert "import Mathlib.Logic.Basic" in code
    assert "import Mathlib.Algebra.Ring.Basic" in code


# 2. Mode strict : sorry refusé par défaut, accepté avec le flag.
def test_sorry_refuse_par_defaut():
    thm = _thm_base(proof="by sorry")
    erreurs = valider_theoreme(thm, 0, allow_sorry=False)
    assert any("sorry" in e for e in erreurs)


def test_sorry_autorise_avec_flag():
    thm = _thm_base(proof="by sorry")
    erreurs = valider_theoreme(thm, 0, allow_sorry=True)
    assert not any("sorry" in e for e in erreurs)


def test_admit_refuse_par_defaut():
    thm = _thm_base(proof="by admit")
    erreurs = valider_theoreme(thm, 0, allow_sorry=False)
    assert any("admit" in e for e in erreurs)


# 3. Parenthèses : équilibrées OK, déséquilibrées détectées.
def test_parentheses_equilibrees_ok():
    ok, _ = parentheses_equilibrees("fun (a : Nat) => (a + [1, 2])")
    assert ok


def test_parentheses_desequilibrees():
    ok, diag = parentheses_equilibrees("fun (a : Nat => a")
    assert not ok
    assert diag


def test_parentheses_mismatch():
    ok, _ = parentheses_equilibrees("(a]")
    assert not ok


def test_parentheses_ignore_commentaires_et_chaines():
    # La parenthèse dans le commentaire et dans la chaîne ne compte pas.
    ok, _ = parentheses_equilibrees('def x := "(" -- commentaire ( oublié')
    assert ok


# 4. Imports : dédupliqués et triés dans la sortie.
def test_imports_dedup_tries():
    spec = {"theorems": [
        _thm_base(name="t1", imports=["B", "A"]),
        _thm_base(name="t2", imports=["A", "C"]),
    ]}
    code = generer(spec)
    lignes_import = [l for l in code.split("\n") if l.startswith("import ")]
    assert lignes_import == ["import A", "import B", "import C"]


def test_imports_vides_refuses():
    thm = _thm_base(imports=[])
    erreurs = valider_theoreme(thm, 0)
    assert any("import" in e for e in erreurs)


# 5. Docstring présente dans la sortie.
def test_docstring_rendue():
    spec = {"theorems": [_thm_base(doc="Ma doc spéciale.")]}
    code = generer(spec)
    assert "Ma doc spéciale." in code


def test_sans_doc_pas_de_bloc_vide():
    thm = _thm_base()
    del thm["doc"]
    code = generer({"theorems": [thm]})
    assert "theorem t_test" in code


# Validation : champs requis, noms dupliqués, spec vide.
def test_champ_manquant():
    thm = {"name": "t", "statement": "(a : Nat) : a = a"}  # proof manque
    erreurs = valider_theoreme(thm, 0)
    assert any("proof" in e for e in erreurs)


def test_nom_duplique():
    spec = {"theorems": [_thm_base(name="dup"), _thm_base(name="dup")]}
    erreurs = valider_spec(spec)
    assert any("dupliqué" in e for e in erreurs)


def test_nom_invalide():
    thm = _thm_base(name="pas un nom")
    erreurs = valider_theoreme(thm, 0)
    assert any("invalide" in e for e in erreurs)


def test_spec_sans_theorems():
    assert valider_spec({})
    assert valider_spec({"theorems": []})


def test_ecrire_fichier(tmp_path):
    sortie = str(tmp_path / "out.lean")
    res = ecrire(exemple_spec(), sortie)
    assert res["theoremes"] == 3
    with open(sortie, encoding="utf-8") as fh:
        contenu = fh.read()
    assert "theorem phiwrite_exemple_identite" in contenu


def test_ecrire_spec_invalide_leve(tmp_path):
    sortie = str(tmp_path / "out.lean")
    with pytest.raises(ValueError):
        ecrire({"theorems": [_thm_base(proof="by sorry")]}, sortie)
