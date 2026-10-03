"""Garde-fous « zéro tree-sitter silencieux » (2026-10-03).

Contexte : la grammaire tree-sitter-lean confond `|expr|` en position de
type de retour avec une alternative de filtrage `|` → nœud ERROR qui avale
toutes les déclarations suivantes, silencieusement.

(a) INVENTAIRE : tout site d'appel tree-sitter doit figurer au registre
    (REGISTRE_TREE_SITTER.md) avec son verrouillage. Un nouveau site non
    enregistré fait échouer le test.
(b) INTÉGRATION : le scénario du bug rejoué à travers chaque commande
    concernée — aucune ne doit rendre un résultat silencieusement tronqué.

Portage public (2026-10-03) : adapté depuis v110. Les modules v110
absents du dépôt public (dualite, synthese_locale, visualisation_ast,
godel_fourier, indexeur_lemmes, protocole_lean, et les commandes
protocole/visualiser de cli.py) ne sont pas couverts ici — écart
documenté dans la description de la PR et au registre.
"""

import os
import re

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(RACINE, "phi_complexity")

# ---------------------------------------------------------------------------
# (a) Inventaire des sites d'appel tree-sitter
# ---------------------------------------------------------------------------

# Motif -> fichiers autorisés à le contenir (tout le reste = nouveau site
# non enregistré -> échec). Granularité fichier : les numéros de ligne
# bougent, les fichiers non.
SITES_AUTORISES = {
    # get_parser("lean") direct
    "get_parser(": {
        "phi_complexity/langs/lean.py",
        "phi_complexity/langs/treesitter_generic.py",
        "phi_complexity/parseur_lean.py",
    },
    # import du paquet externe
    "tree_sitter_language_pack": {
        "phi_complexity/langs/registry.py",
        "phi_complexity/langs/lean.py",
        "phi_complexity/langs/treesitter_generic.py",
        "phi_complexity/parseur_lean.py",
        # mention en chaîne uniquement (nom du capteur de durcissement) :
        "phi_complexity/durcissement.py",
    },
    # extracteur tree-sitter NON robuste (hors sa propre définition)
    "extraire_declarations(": {
        "phi_complexity/parseur_lean.py",
    },
}

# Chaque fichier verrouillé doit contenir son marqueur de verrouillage
# (preuve que le verrouillage est bien branché, pas seulement documenté).
MARQUEURS_VERROUILLAGE = {
    "phi_complexity/parseur_lean.py": "robuste_repli",
    "phi_complexity/langs/lean.py": "_repli_robuste",
    "phi_complexity/langs/treesitter_generic.py": "TREE-SITTER DÉGRADÉ",
    "phi_complexity/carte.py": "EXTRACTION DÉGRADÉE",
    "phi_complexity/dependances.py": "parseur_autonome",
    "phi_complexity/veille.py": "EXTRACTION DÉGRADÉE",
}


def _fichiers_contenant(motif: str):
    trouves = set()
    for racine, _, fichiers in os.walk(PKG):
        for f in fichiers:
            if not f.endswith(".py"):
                continue
            chemin = os.path.join(racine, f)
            with open(chemin, encoding="utf-8", errors="replace") as fh:
                contenu = fh.read()
            if motif == "extraire_declarations(":
                # exclure la définition et la variante robuste
                lignes = [l for l in contenu.splitlines()
                          if "extraire_declarations(" in l
                          and "extraire_declarations_robuste(" not in l
                          and not l.strip().startswith("def ")
                          and not l.strip().startswith("#")
                          and not l.strip().startswith('"""')
                          and "Contrairement" not in l]
                if lignes:
                    trouves.add(os.path.relpath(chemin, RACINE))
            elif motif in contenu:
                trouves.add(os.path.relpath(chemin, RACINE))
    return trouves


@pytest.mark.parametrize("motif,autorises", list(SITES_AUTORISES.items()))
def test_inventaire_sites_treesitter(motif, autorises):
    trouves = _fichiers_contenant(motif)
    nouveaux = trouves - autorises
    assert not nouveaux, (
        f"NOUVEAU(X) SITE(S) TREE-SITTER NON ENREGISTRÉ(S) pour {motif!r} : "
        f"{sorted(nouveaux)} — l'ajouter à REGISTRE_TREE_SITTER.md avec son "
        f"verrouillage, puis à SITES_AUTORISES ici."
    )


@pytest.mark.parametrize("fichier,marqueur",
                         list(MARQUEURS_VERROUILLAGE.items()))
def test_verrouillage_branche(fichier, marqueur):
    chemin = os.path.join(RACINE, fichier)
    with open(chemin, encoding="utf-8", errors="replace") as fh:
        contenu = fh.read()
    assert marqueur in contenu, (
        f"{fichier} : marqueur de verrouillage {marqueur!r} introuvable — "
        f"le verrouillage documenté au registre n'est pas branché."
    )


def test_registre_existe_et_couvre():
    registre = os.path.join(RACINE, "REGISTRE_TREE_SITTER.md")
    assert os.path.isfile(registre), "REGISTRE_TREE_SITTER.md manquant"
    with open(registre, encoding="utf-8") as fh:
        contenu = fh.read()
    for fichier in MARQUEURS_VERROUILLAGE:
        nom = os.path.basename(fichier)
        assert nom in contenu, (
            f"{fichier} : absent du registre — tout site verrouillé doit "
            f"y figurer.")


# ---------------------------------------------------------------------------
# (b) Intégration : scénario du bug à travers chaque commande
# ---------------------------------------------------------------------------

CODE_BUG = """theorem sain_avant : 1 = 1 := rfl
lemma declencheur {t l r : ℝ} : |t - l| ≤ r - l := by
  sorry
theorem apres_un : 2 = 2 := by
  sorry
theorem apres_deux : 3 = 3 := rfl
"""


@pytest.fixture()
def fichier_bug(tmp_path):
    p = tmp_path / "bug.lean"
    p.write_text(CODE_BUG, encoding="utf-8")
    return str(p)


def test_rechercher_declaration_sans_angle_mort():
    from phi_complexity.parseur_lean import rechercher_declaration
    avant = rechercher_declaration(CODE_BUG, "sain_avant")
    assert avant is not None and avant.extraction == "autonome"
    apres = rechercher_declaration(CODE_BUG, "apres_un")
    assert apres is not None, "déclaration après la coupure introuvable"
    # Autonomie stricte : plus aucun angle mort, donc plus de repli —
    # la source unique voit tout.
    assert apres.extraction == "autonome"
    assert "apres_un" in apres.texte
    assert rechercher_declaration(CODE_BUG, "nexiste_pas") is None


def test_carte_signale_le_repli(fichier_bug, tmp_path):
    from phi_complexity.carte import carte_projet
    carte = carte_projet(str(tmp_path), complet=False)
    # Autonomie stricte : plus de repli, donc plus d'alerte d'extraction
    # dégradée — mais tous les symboles sont présents quand même.
    assert not any("EXTRACTION DÉGRADÉE" in a
                   for a in carte["avertissements"])
    assert carte["nb_symboles"] >= 4


def test_analyseur_generique_annotation_critical(fichier_bug):
    from phi_complexity.langs.treesitter_generic import AnalyseurTreeSitter
    res = AnalyseurTreeSitter(fichier_bug, ts_langage="lean").analyser(
        complet=False)
    crit = [a for a in res.annotations
            if a.niveau == "CRITICAL" and "TREE-SITTER DÉGRADÉ" in a.message]
    assert crit, "annotation CRITICAL de dégradation manquante"


def test_comparer_extracteurs_signale_la_divergence():
    from phi_complexity.parseur_lean import comparer_extracteurs
    comp = comparer_extracteurs(CODE_BUG)
    seuls_auto = {d.nom for d in comp["autonome_seuls"]}
    # `declencheur` est vue par tree-sitter (c'est elle qui porte le `|·|`
    # déclencheur) ; seules les déclarations APRÈS la coupure divergent.
    assert {"apres_un", "apres_deux"} <= seuls_auto
