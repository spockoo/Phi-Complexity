"""
tests/test_durcissement_extraction_20261002.py — Durcissement : la veille
ne doit plus devenir aveugle silencieusement.

Cause racine (diagnostic 2026-10-02) : la grammaire tree-sitter-lean
confond les barres `|expr|` (valeur absolue / norme) en position de
type de retour avec une alternative de filtrage `|` ; le nœud ERROR
produit avale ensuite les déclarations suivantes, qui deviennent
invisibles à l'analyseur — silencieusement. Constaté : 169 déclarations
vues sur 261 dans scratch_65c_global.lean (coupure ligne ~3327),
412 déclarations manquées sur 60/176 fichiers du dépôt Lean.

Durcissement : l'extracteur robuste (regex) est la source de vérité
pour l'EXISTENCE des symboles ; tree-sitter garde la main sur les
métriques fines. Toute divergence est signalée explicitement
(section console + clé JSON `extraction_repli`), jamais tue.

Ignorés (skip) si la grammaire lean est indisponible.
"""
import textwrap

import pytest

tree_sitter_language_pack = pytest.importorskip("tree_sitter_language_pack")

from phi_complexity.langs.lean import (
    AnalyseurLean,
    grammaire_lean_disponible,
)
from phi_complexity.parseur_lean import (
    comparer_extracteurs,
    extraire_declarations,
    extraire_declarations_robuste,
)
from phi_complexity.veille import (
    comparer,
    ecrire_snapshot,
    prendre_snapshot,
    veille_console,
)

grammaire_ok = pytest.mark.skipif(
    not grammaire_lean_disponible(),
    reason="grammaire lean indisponible",
)

# Reproducteur minimal de la coupure : `|t - l|` dans le type de retour.
# tree-sitter voit `avant` et `abs_coupe` (la fautive elle-même), puis
# plus rien ; le robuste voit les 4 déclarations.
CODE_COUPURE = textwrap.dedent("""\
    lemma avant (a b : ℝ) (h : a < b) : a < b + 1 := by linarith

    lemma abs_coupe {t l r : ℝ} (h : t ∈ Set.Ioo l r) (hlr : l ≤ r) :
        |t - l| ≤ r - l := by
      rw [abs_of_nonneg (sub_nonneg.mpr h.1.le)]
      linarith [h.2]

    def apres_coupe (x : ℕ) : ℕ := x

    theorem propre_apres : True := trivial
    """)

CODE_SAIN = textwrap.dedent("""\
    def alpha (x : ℕ) : ℕ := x + 1

    theorem beta : True := trivial
    """)


def _ecrire(tmp_path, nom, code):
    p = tmp_path / nom
    p.write_text(code, encoding="utf-8")
    return str(p)


@grammaire_ok
def test_cause_racine_barre_absolue_coupe_tree_sitter():
    """La cause racine : `|expr|` en position de type fait perdre à
    tree-sitter les déclarations suivantes ; le parseur autonome les voit.
    (Documente pourquoi tree-sitter n'est plus sur le chemin critique.)"""
    comp = comparer_extracteurs(CODE_COUPURE)
    noms_ts = {d.nom for d in extraire_declarations(CODE_COUPURE)}
    from phi_complexity.parseur_autonome import parse_declarations
    noms_auto = {d.nom for d in parse_declarations(CODE_COUPURE)}
    # L'autonome voit tout.
    assert noms_auto == {"avant", "abs_coupe", "apres_coupe", "propre_apres"}
    # tree-sitter est aveugle après la coupure…
    assert "apres_coupe" not in noms_ts
    # …et le diagnostic le dit explicitement.
    assert {d.nom for d in comp["autonome_seuls"]} >= {"apres_coupe"}
    assert "apres_coupe" not in {d.nom for d in comp["tree_sitter_seuls"]}


@grammaire_ok
def test_repli_robuste_recupere_les_invisibles(tmp_path):
    """Le parseur autonome voit les symboles que tree-sitter avale :
    aucun n'est perdu, tous portent extraction='autonome'
    (plus de repli : la source unique voit tout)."""
    chemin = _ecrire(tmp_path, "coupure.lean", CODE_COUPURE)
    res = AnalyseurLean(chemin).charger().analyser(complet=False)
    par_nom = {f.nom: f for f in res.fonctions}
    assert set(par_nom) == {"avant", "abs_coupe", "apres_coupe", "propre_apres"}
    assert par_nom["apres_coupe"].extraction == "autonome"
    assert par_nom["avant"].extraction == "autonome"
    assert par_nom["apres_coupe"].complexite >= 1
    assert par_nom["apres_coupe"].nb_lignes >= 1


@grammaire_ok
def test_repli_ne_cree_pas_de_nouveau_perimetre(tmp_path):
    """`example` n'est pas récupéré : le repli répare un angle mort,
    il n'étend pas le périmètre de l'analyseur."""
    code = CODE_COUPURE + "\n    example : True := trivial\n"
    chemin = _ecrire(tmp_path, "coupure_ex.lean", code)
    res = AnalyseurLean(chemin).charger().analyser(complet=False)
    assert "example" not in {f.nom for f in res.fonctions}


@grammaire_ok
def test_regex_nettoie_point_univers():
    """`theorem foo.{u}` : le nom capturé ne garde pas le point traînant
    (parseur autonome comme l'ancien regex)."""
    from phi_complexity.parseur_autonome import parse_declarations
    decls = parse_declarations(
        "theorem bridge_local_existence.{u}\n    (h : True) : True := trivial\n")
    assert [d.nom for d in decls] == ["bridge_local_existence"]


@grammaire_ok
def test_veille_signale_extraction_degradee(tmp_path):
    """Autonomie stricte : il n'y a plus d'extraction dégradée à signaler —
    le parseur autonome voit tout, sans repli. La veille est STABLE sans
    clé ni section parasite."""
    _ecrire(tmp_path, "coupure.lean", CODE_COUPURE)
    ref = str(tmp_path / "ref.json")
    ecrire_snapshot(str(tmp_path), ref)
    from phi_complexity.veille import charger_reference
    diff = comparer(charger_reference(ref), str(tmp_path))
    assert diff["verdict"] == "STABLE"
    assert not diff.get("extraction_repli")
    console = veille_console(diff)
    assert "EXTRACTION DÉGRADÉE" not in console


@grammaire_ok
def test_veille_detecte_trou_apres_coupure(tmp_path):
    """Scénario critique : un `sorry` introduit APRÈS la coupure
    tree-sitter (dans un symbole invisible à tree-sitter) DOIT être
    détecté — le parseur autonome le voit, sans aucun repli."""
    _ecrire(tmp_path, "coupure.lean", CODE_COUPURE)
    ref = str(tmp_path / "ref.json")
    ecrire_snapshot(str(tmp_path), ref)
    # On ajoute un nouveau symbole AVEC TROU après la coupure
    # (en colonne 0 : la grammaire documentée du parseur autonome
    # exige les déclarations de premier niveau en colonne 0).
    _ecrire(tmp_path, "coupure.lean",
            CODE_COUPURE + "\ndef nouveau_trou : ℕ := by sorry\n")
    from phi_complexity.veille import charger_reference
    diff = comparer(charger_reference(ref), str(tmp_path))
    assert diff["verdict"] == "DÉGRADATION DÉTECTÉE"
    assert any(s["nom"] == "nouveau_trou"
               for s in diff["trous_nouveaux_symboles"])


@grammaire_ok
def test_non_regression_sans_divergence(tmp_path):
    """Sans divergence, comportement inchangé : pas d'alerte,
    pas de clé parasite, STABLE."""
    _ecrire(tmp_path, "sain.lean", CODE_SAIN)
    ref = str(tmp_path / "ref.json")
    ecrire_snapshot(str(tmp_path), ref)
    from phi_complexity.veille import charger_reference
    diff = comparer(charger_reference(ref), str(tmp_path))
    assert diff["verdict"] == "STABLE"
    assert diff["extraction_repli"] == {}
    assert "EXTRACTION DÉGRADÉE" not in veille_console(diff)
    # La carte snapshot ne change pas de forme pour un projet sain.
    carte = prendre_snapshot(str(tmp_path))["carte"]
    assert carte["extraction_repli"] == {}
