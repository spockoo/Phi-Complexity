"""
test_veille.py — Tests de La Veille (v0.11.0) : snapshot + diff.

Scénarios : enveloppe valide, STABLE, nouveau trou (Lean), symbole
perdu, renommage probable, arête cassée, référence invalide/périmée,
trou réparé, codes de sortie CLI.
"""

import json
import os

import pytest

from phi_complexity.veille import (
    prendre_snapshot,
    ecrire_snapshot,
    charger_reference,
    comparer,
    veille_console,
)
from phi_complexity.core import VERSION


def _grammaire_lean_ok() -> bool:
    """La grammaire tree-sitter 'lean' est-elle téléchargeable ici ?"""
    try:
        from tree_sitter_language_pack import get_parser
        get_parser("lean")
        return True
    except Exception:
        return False


@pytest.fixture()
def projet_py(tmp_path):
    """Petit projet Python : alpha.py importe beta.py."""
    (tmp_path / "alpha.py").write_text(
        "import beta\n\n"
        "def grande(x):\n"
        "    total = 0\n"
        "    for i in range(5):\n"
        "        total += i * x\n"
        "    return total\n"
        "\n"
        "def petite(y):\n"
        "    return y + 1\n",
        encoding="utf-8",
    )
    (tmp_path / "beta.py").write_text(
        "def aide(z):\n    return z * 2\n", encoding="utf-8"
    )
    return str(tmp_path)


def _snapshotiser(dossier, tmp_path, nom="ref.json"):
    ref = str(tmp_path / nom)
    ecrire_snapshot(dossier, ref)
    return ref


# ────────────────────────────────────────────────────────
# ENVELOPPE
# ────────────────────────────────────────────────────────

def test_enveloppe_valide(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    env = charger_reference(ref)
    assert env["commande"] == "snapshot"
    assert env["version_phi"] == VERSION
    assert "date" in env and "md5_carte" in env
    assert env["carte"]["nb_symboles"] == 3
    assert isinstance(env["aretes"], dict)
    # alpha.py importe beta.py : arête résolue enregistrée
    assert "beta.py" in env["aretes"].get("alpha.py", [])


def test_reference_invalide(tmp_path):
    with pytest.raises(ValueError):
        charger_reference(str(tmp_path / "inexistant.json"))
    mauvais = tmp_path / "mauvais.json"
    mauvais.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(ValueError):
        charger_reference(str(mauvais))
    pas_snapshot = tmp_path / "autre.json"
    pas_snapshot.write_text('{"commande": "index"}', encoding="utf-8")
    with pytest.raises(ValueError):
        charger_reference(str(pas_snapshot))


# ────────────────────────────────────────────────────────
# STABLE
# ────────────────────────────────────────────────────────

def test_stable(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    diff = comparer(charger_reference(ref), projet_py)
    assert diff["verdict"] == "STABLE"
    assert diff["signaux"] == []
    assert diff["nouveaux_trous"] == []
    assert diff["symboles_perdus"] == []
    assert "Rien n'a bougé" in veille_console(diff)


# ────────────────────────────────────────────────────────
# DÉGRADATIONS
# ────────────────────────────────────────────────────────

@pytest.mark.skipif(not _grammaire_lean_ok(),
                    reason="grammaire tree-sitter 'lean' indisponible")
def test_nouveau_trou_lean(tmp_path):
    dossier = str(tmp_path / "lean")
    os.makedirs(dossier)
    (tmp_path / "lean" / "m.lean").write_text(
        "theorem t1 : True := trivial\n", encoding="utf-8"
    )
    ref = _snapshotiser(dossier, tmp_path, "ref_lean.json")
    (tmp_path / "lean" / "m.lean").write_text(
        "theorem t1 : True := by\n  sorry\n", encoding="utf-8"
    )
    diff = comparer(charger_reference(ref), dossier)
    assert diff["verdict"] == "DÉGRADATION DÉTECTÉE"
    assert len(diff["nouveaux_trous"]) == 1
    assert diff["nouveaux_trous"][0]["nom"] == "t1"


def test_symbole_perdu(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    (tmp_path / "alpha.py").write_text(
        "import beta\n\ndef grande(x):\n    return x\n", encoding="utf-8"
    )
    diff = comparer(charger_reference(ref), projet_py)
    assert diff["verdict"] == "DÉGRADATION DÉTECTÉE"
    noms = [s["nom"] for s in diff["symboles_perdus"]]
    assert "petite" in noms


def test_renommage_probable_pas_perte(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    (tmp_path / "alpha.py").write_text(
        "import beta\n\n"
        "def grande(x):\n    return x\n"
        "\n"
        "def minuscule(y):\n"
        "    return y + 1\n",
        encoding="utf-8",
    )
    diff = comparer(charger_reference(ref), projet_py)
    # Le renommage n'est ni une perte ni un gain sec…
    assert diff["symboles_perdus"] == []
    assert all(s["nom"] != "minuscule" for s in diff["symboles_gagnes"])
    # …mais un renommage probable explicite.
    assert len(diff["renommage_probable"]) == 1
    r = diff["renommage_probable"][0]
    assert (r["ancien_nom"], r["nouveau_nom"]) == ("petite", "minuscule")
    # …et ne fait pas basculer le verdict.
    assert diff["verdict"] == "STABLE"


def test_arete_cassee(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    os.remove(tmp_path / "beta.py")
    diff = comparer(charger_reference(ref), projet_py)
    assert diff["verdict"] == "DÉGRADATION DÉTECTÉE"
    assert len(diff["aretes_cassees"]) == 1
    a = diff["aretes_cassees"][0]
    assert (a["fichier"], a["cible"]) == ("alpha.py", "beta.py")


def test_trou_repare_ne_degrade_pas(tmp_path):
    pytest.importorskip("tree_sitter_language_pack")
    if not _grammaire_lean_ok():
        pytest.skip("grammaire tree-sitter 'lean' indisponible")
    dossier = str(tmp_path / "lean2")
    os.makedirs(dossier)
    (tmp_path / "lean2" / "m.lean").write_text(
        "theorem t1 : True := by\n  sorry\n", encoding="utf-8"
    )
    ref = _snapshotiser(dossier, tmp_path, "ref_lean2.json")
    (tmp_path / "lean2" / "m.lean").write_text(
        "theorem t1 : True := trivial\n", encoding="utf-8"
    )
    diff = comparer(charger_reference(ref), dossier)
    assert len(diff["trous_repares"]) == 1
    assert diff["verdict"] == "STABLE"  # réparer n'est pas dégrader


def test_reference_perimee_avertit(projet_py, tmp_path):
    ref = _snapshotiser(projet_py, tmp_path)
    with open(ref, "r", encoding="utf-8") as f:
        env = json.load(f)
    env["version_phi"] = "0.0.0-perimee"
    with open(ref, "w", encoding="utf-8") as f:
        json.dump(env, f)
    diff = comparer(charger_reference(ref), projet_py)
    assert any("0.0.0-perimee" in a for a in diff["avertissements"])
    # La comparaison a quand même lieu, explicitement.
    assert diff["verdict"] == "STABLE"


# ────────────────────────────────────────────────────────
# CLI : codes de sortie (0 = STABLE, 2 = DÉGRADATION)
# ────────────────────────────────────────────────────────

def test_cli_codes_sortie(projet_py, tmp_path, monkeypatch):
    import sys
    from phi_complexity.cli import main
    ref = _snapshotiser(projet_py, tmp_path)

    monkeypatch.setattr(
        sys, "argv",
        ["phi", "veille", projet_py, "--ref", ref, "--format", "console"])
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 0

    os.remove(tmp_path / "beta.py")
    with pytest.raises(SystemExit) as e:
        main()
    assert e.value.code == 2
