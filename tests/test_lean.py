"""
tests/test_lean.py — Tests de l'analyseur Lean 4 dédié (v0.7.0 « Le Lecteur Lean »).

Ignorés (skip) si `tree-sitter-language-pack` n'est pas installé ou si la
grammaire Lean est indisponible — sauf le test du signal honnête, qui
vérifie justement ce cas.
"""
import os
import subprocess
import tempfile
import textwrap

import pytest

tree_sitter_language_pack = pytest.importorskip("tree_sitter_language_pack")

from phi_complexity.langs import obtenir_analyseur, analyseur_disponible
from phi_complexity.langs.lean import (
    AnalyseurLean,
    grammaire_lean_disponible,
)
from phi_complexity.langs import treesitter_disponible  # noqa: F401  (signal honnête)

grammaire_ok = pytest.mark.skipif(
    not grammaire_lean_disponible(),
    reason="grammaire lean indisponible",
)

TEMOIN = os.path.expanduser(
    "~/workspace/lean-navier-stokes/Part16f_HilbertTruncatedL2.lean")

# Lieurs / identifiants internes qui NE DOIVENT JAMAIS apparaître comme symboles.
BRUIT_INTERDIT = {"ξ", "f", "ε", "R", "hm", "C", "x", "m", "<anonyme>"}


def creer_fichier(code: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lean", delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(code))
        return f.name


@grammaire_ok
def test_registre_prioritaire_sur_generique():
    """`.lean` → AnalyseurLean, jamais le générique bruyant."""
    from phi_complexity.langs.treesitter_generic import AnalyseurTreeSitter
    analyseur = obtenir_analyseur("/tmp/faux.lean")
    assert isinstance(analyseur, AnalyseurLean)
    assert not isinstance(analyseur, AnalyseurTreeSitter)


@grammaire_ok
def test_oracle_fichier_temoin():
    """Le compte de symboles égale l'oracle grep des déclarations nommées."""
    if not os.path.isfile(TEMOIN):
        pytest.skip("fichier témoin absent")
    oracle = subprocess.run(
        ["grep", "-cE",
         r"^(noncomputable |private |protected )?(theorem|lemma|def|abbrev|instance|structure|class|inductive) ",
         TEMOIN],
        capture_output=True, text=True,
    )
    attendu = int(oracle.stdout.strip())
    resultat = AnalyseurLean(TEMOIN).analyser()
    assert len(resultat.fonctions) == attendu, (
        f"analyseur={len(resultat.fonctions)} oracle={attendu}"
    )


@grammaire_ok
def test_oracle_noms_identiques():
    """Les noms extraits sont exactement les déclarations de tête."""
    if not os.path.isfile(TEMOIN):
        pytest.skip("fichier témoin absent")
    resultat = AnalyseurLean(TEMOIN).analyser()
    noms = sorted(f.nom for f in resultat.fonctions)
    assert not any(n.startswith("<") or n.startswith("_") for n in noms)
    assert BRUIT_INTERDIT.isdisjoint(set(noms)), (
        f"bruit détecté : {BRUIT_INTERDIT & set(noms)}"
    )
    assert "norm_hilbertTruncLp_le" in noms
    assert "hilbertLp" in noms


@grammaire_ok
def test_ignore_anonymes_et_modificateurs():
    """Instances anonymes et `def _…` ignorés ; modificateurs traversés."""
    chemin = creer_fichier("""
        def _cache : Nat := 0

        instance : Inhabited Nat := inferInstance

        private theorem prive : True := trivial

        @[simp] def avec_attr : Nat := 0

        noncomputable def nc (x : Nat) : Nat := x
        """)
    try:
        noms = [f.nom for f in AnalyseurLean(chemin).analyser().fonctions]
    finally:
        os.unlink(chemin)
    assert "_cache" not in noms
    assert "inferInstance" not in noms  # pas le corps de l'instance anonyme
    assert "prive" in noms
    assert "avec_attr" in noms
    assert "nc" in noms


@grammaire_ok
def test_proxy_complexite_lignes_plus_tactiques():
    """complexite = nb_lignes + nb pas de tactique (proxy documenté)."""
    chemin = creer_fichier("""theorem demo : 1 + 1 = 2 := by
  rw [Nat.add_comm]
  rfl
""")
    try:
        (f,) = AnalyseurLean(chemin).analyser().fonctions
    finally:
        os.unlink(chemin)
    assert f.nb_lignes >= 3
    # Proxy documenté : complexite = nb_lignes + nb pas de tactique
    # (2 pas ici : `rw […]` et `rfl`), quelle que soit la convention
    # de fin de nœud de la grammaire.
    assert f.complexite - f.nb_lignes == 2


@grammaire_ok
def test_compte_arguments_lieurs():
    """nb_args = noms liés avant `:` dans les lieurs explicites/implicites."""
    chemin = creer_fichier("""
        def f (x y : Nat) {z : Int} [Inhabited Nat] : Nat := x
        """)
    try:
        (f,) = AnalyseurLean(chemin).analyser().fonctions
    finally:
        os.unlink(chemin)
    assert f.nb_args == 3  # x, y, z (le lieur d'instance ne nomme rien)


@grammaire_ok
def test_analyseur_disponible_lean():
    assert analyseur_disponible("/tmp/faux.lean") is True


def test_grammaire_indisponible_signal_honnete(monkeypatch):
    """Sans la grammaire : ImportError explicite, jamais de silence."""
    import phi_complexity.langs.lean as lean_mod
    monkeypatch.setitem(lean_mod._GRAMMAIRE_CACHE, "ok", False)
    monkeypatch.setattr(
        "tree_sitter_language_pack.get_language",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("pas de réseau")),
    )
    assert lean_mod.grammaire_lean_disponible() is False
    with pytest.raises(ImportError, match="[Ll]ean"):
        obtenir_analyseur("/tmp/faux.lean")
