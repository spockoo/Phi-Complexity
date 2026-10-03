# -*- coding: utf-8 -*-
"""Tests ciblés du parseur Lean 4 autonome (propriété de phi-complexity).

Autonomie stricte (2026-10-03) : ce parseur (stdlib uniquement) remplace
tree-sitter sur tout le chemin Lean. Ces tests verrouillent son contrat :
en-têtes exotiques du corpus, `|·|` en position de type (la cause racine
du bug tree-sitter), corps imbriqués, échec bruyant (jamais silencieux),
spans exacts et non-chevauchants.

Règle : si un test échoue, c'est le parseur qu'on répare, pas le test
qu'on affaiblit — sauf documentation explicite d'une limite assumée.
"""
import pytest

from phi_complexity.parseur_autonome import (
    parse,
    parse_declarations,
    tokenize,
)


# ---------------------------------------------------------------------------
# En-têtes exotiques : l'inventaire du corpus réel (176 fichiers)
# ---------------------------------------------------------------------------

def test_modificateurs_protected_noncomputable_unsafe():
    code = ("protected def foo.protected (x : Nat) : Nat := x\n"
            "noncomputable def bar : Nat := 0\n"
            "unsafe def baz : Nat := 0\n")
    noms = {d.nom: d.kind for d in parse_declarations(code)}
    assert noms == {"foo.protected": "def", "bar": "def", "baz": "def"}


def test_attributs_accolades_et_docstring():
    code = ("/-- Documentation. -/\n"
            "@[simp, norm_cast] theorem thm_attr : 1 = 1 := rfl\n"
            "@[simp]\n"
            "theorem thm_attr_ligne_suivante : 2 = 2 := rfl\n")
    decls = parse_declarations(code)
    assert [d.nom for d in decls] == ["thm_attr", "thm_attr_ligne_suivante"]
    # La docstring fait partie du span (ligne_debut < ligne du mot-clé).
    assert decls[0].ligne_debut < decls[0].ligne
    assert "/--" in decls[0].texte


def test_univers_explicites_et_noms_pointes():
    decls = parse_declarations(
        "theorem bridge_local_existence.{u} {α : Type u} : True := trivial\n")
    assert [d.nom for d in decls] == ["bridge_local_existence"]


def test_axiom_et_opaque_sans_corps():
    decls = parse_declarations("axiom choix_magique : True\n"
                               "opaque constante_cachee : Nat\n")
    assert [(d.kind, d.nom, d.corps) for d in decls] == [
        ("axiom", "choix_magique", ""),
        ("opaque", "constante_cachee", ""),
    ]


def test_instance_anonyme_ignoree_avec_avertissement():
    res = parse("instance : Inhabited Nat := ⟨0⟩\n"
                "def apres : Nat := 0\n")
    assert [d.nom for d in res.declarations] == ["apres"]
    assert any("instance anonyme" in a.message for a in res.avertissements), (
        "l'instance anonyme doit être signalée, pas avalée en silence")


def test_structure_class_inductive():
    code = ("structure Point where\n"
            "  x : Nat\n"
            "  y : Nat\n"
            "class Magma (α : Type) where\n"
            "  op : α → α → α\n"
            "inductive Couleur where\n"
            "  | rouge\n"
            "  | bleu\n"
            "def apres : Nat := 0\n")
    noms = [(d.kind, d.nom) for d in parse_declarations(code)]
    assert ("structure", "Point") in noms
    assert ("class", "Magma") in noms
    assert ("inductive", "Couleur") in noms
    assert ("def", "apres") in noms


def test_structure_extends_avant_where():
    """`structure S ... extends P Q where` : le `extends` ne doit pas
    déclencher « en-tête sans := » (cas réel du corpus)."""
    res = parse("structure Sys (K : Nat) extends BEq K where\n"
                "  h : 0 ≤ 1\n"
                "def apres : Nat := 0\n")
    assert [(d.kind, d.nom) for d in res.declarations] == [
        ("structure", "Sys"), ("def", "apres")]
    assert not res.avertissements


def test_binders_sur_lignes_suivantes():
    """Style corpus : les binders peuvent commencer sur la ligne
    suivant le nom (`theorem foo\\n    (a : T) : ...`)."""
    res = parse("theorem thm_multi\n"
                "    (a : Nat) (b : Nat) :\n"
                "    a + b = b + a := by\n"
                "  rw [Nat.add_comm]\n")
    assert len(res.declarations) == 1
    d = res.declarations[0]
    assert d.nom == "thm_multi"
    assert d.nb_args == 2
    assert "Nat.add_comm" in d.corps
    assert not res.avertissements


# ---------------------------------------------------------------------------
# La cause racine : `|·|` en position de type ne doit rien avaler
# ---------------------------------------------------------------------------

def test_barre_absolue_en_position_de_type():
    """Reproducteur minimal du bug tree-sitter : `|t - l|` en type de
    retour. Le parseur autonome voit les déclarations AVANT et APRÈS."""
    code = ("lemma avant : 1 = 1 := rfl\n"
            "lemma abs_coupe {t l : ℝ} (h : t ≤ l) : |t - l| ≤ 0 := by\n"
            "  sorry\n"
            "def apres_coupe (x : Nat) : Nat := x\n"
            "theorem propre_apres : True := trivial\n")
    noms = [d.nom for d in parse_declarations(code)]
    assert noms == ["avant", "abs_coupe", "apres_coupe", "propre_apres"]
    assert not parse(code).avertissements


def test_filtrage_apres_where_toujours_supporte():
    """Les `|` de filtrage (inductive, match) ne sont pas confondus
    avec des déclarations."""
    code = ("def f : Nat → Nat\n"
            "  | 0 => 0\n"
            "  | n + 1 => n\n"
            "def g : Nat := 0\n")
    noms = [d.nom for d in parse_declarations(code)]
    assert noms == ["f", "g"]


# ---------------------------------------------------------------------------
# Corps imbriqués : délimitation exacte par équilibrage
# ---------------------------------------------------------------------------

def test_corps_imbriques_accolades_parens():
    code = ("def imbrique : Nat := Id.run do\n"
            "  let x := { a := (1 + (2 * 3)) : Point }\n"
            "  pure x.a\n"
            "def suivant : Nat := 0\n")
    decls = parse_declarations(code)
    assert [d.nom for d in decls] == ["imbrique", "suivant"]
    assert "(2 * 3)" in decls[0].corps
    assert "suivant" not in decls[0].corps


def test_spans_non_chevauchants_et_ordonnes():
    code = ("def a : Nat := 1\n"
            "theorem b : 1 = 1 := rfl\n"
            "lemma c : 2 = 2 := by\n"
            "  rfl\n")
    decls = parse_declarations(code)
    spans = [(d.ligne_debut, d.ligne_fin) for d in decls]
    for (d1, f1), (d2, f2) in zip(spans, spans[1:]):
        assert f1 < d2, f"chevauchement : {(d1, f1)} vs {(d2, f2)}"
    # Le dernier span couvre jusqu'à la fin du corps.
    assert decls[-1].ligne_fin == 4


def test_commentaires_et_strings_ignores_par_le_lexer():
    """`def` dans un commentaire ou une string n'est pas une déclaration."""
    code = ("-- def faux_positif : Nat := 0\n"
            "/- def faux_bloc : Nat := 0 -/\n"
            "def vrai : String := \"def pas_une_declaration\"\n")
    assert [d.nom for d in parse_declarations(code)] == ["vrai"]


# ---------------------------------------------------------------------------
# Échec bruyant : jamais de silence
# ---------------------------------------------------------------------------

def test_mot_cle_hors_colonne_zero_bruyant():
    """Un mot-clé de déclaration hors préfixe colonne 0 est signalé
    avec sa ligne — pas avalé en silence."""
    res = parse("def a : Nat := 1\n"
                "  def indente : Nat := 2\n")
    assert any("indente" in a.message or "colonne 0" in a.message
               for a in res.avertissements), (
        [a.message for a in res.avertissements])


def test_avertissements_portent_la_ligne():
    res = parse("instance : Inhabited Nat := ⟨0⟩\n")
    assert res.avertissements
    for a in res.avertissements:
        assert a.ligne >= 1
        assert a.message


def test_parse_ne_leve_jamais():
    """Même sur du code pathologique, parse() retourne un résultat
    (déclarations + avertissements), il ne lève pas."""
    res = parse("def inacheve (x :\n@[ bizarre\n| | |\n")
    assert isinstance(res.declarations, list)
    assert isinstance(res.avertissements, list)


# ---------------------------------------------------------------------------
# Métriques : tactiques, profondeur by, binders
# ---------------------------------------------------------------------------

def test_comptage_tactiques():
    decls = parse_declarations("theorem t : True := by\n"
                               "  constructor\n"
                               "  rfl\n")
    assert decls[0].nb_tactiques == 2
    assert decls[0].profondeur_by >= 1


def test_nb_args_binders():
    decls = parse_declarations(
        "theorem t (a b : Nat) {c : Int} [Inhabited α] : True := trivial\n")
    assert decls[0].nb_args == 3  # [Inhabited α] sans nom ne compte pas
