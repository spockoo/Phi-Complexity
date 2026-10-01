"""
tests/test_multilang.py — Tests de l'analyseur générique Tree-sitter (multi-langage).
Ces tests sont ignorés (skip) si le paquet optionnel 'tree-sitter-language-pack'
n'est pas installé (pip install phi-complexity[multilang]).
"""
import os
import tempfile
import textwrap

import pytest

tree_sitter_language_pack = pytest.importorskip("tree_sitter_language_pack")

from phi_complexity import auditer, langage_pour_extension, est_fichier_supporte
from phi_complexity.langs import obtenir_analyseur
from phi_complexity.langs.treesitter_generic import AnalyseurTreeSitter


def creer_fichier(code: str, suffixe: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffixe, delete=False, encoding="utf-8"
    ) as f:
        f.write(textwrap.dedent(code))
        return f.name


CODE_JS = """
function additionner(a, b) {
    return a + b;
}

function boucleImbriquee(matrice) {
    for (let i = 0; i < matrice.length; i++) {
        for (let j = 0; j < matrice[i].length; j++) {
            console.log(matrice[i][j]);
        }
    }
}
"""

CODE_JAVA = """
public class Calculatrice {
    public int additionner(int a, int b) {
        return a + b;
    }

    public void boucleImbriquee(int[][] matrice) {
        for (int i = 0; i < matrice.length; i++) {
            for (int j = 0; j < matrice[i].length; j++) {
                System.out.println(matrice[i][j]);
            }
        }
    }
}
"""

CODE_GO = """
package main

func additionner(a int, b int) int {
    return a + b
}

func boucleImbriquee(matrice [][]int) {
    for i := 0; i < len(matrice); i++ {
        for j := 0; j < len(matrice[i]); j++ {
            println(matrice[i][j])
        }
    }
}
"""

CODE_RUST = """
fn additionner(a: i32, b: i32) -> i32 {
    a + b
}

fn beaucoup_d_arguments(a: i32, b: i32, c: i32, d: i32, e: i32, f: i32) -> i32 {
    a + b + c + d + e + f
}
"""

CODE_C = """
#include <stdio.h>

int additionner(int a, int b) {
    return a + b;
}
"""


class TestDetectionLangage:

    def test_extension_javascript(self):
        assert langage_pour_extension("app.js") == "javascript"

    def test_extension_java(self):
        assert langage_pour_extension("Main.java") == "java"

    def test_extension_inconnue(self):
        assert langage_pour_extension("fichier.xyz123") is None

    def test_fichier_supporte(self):
        assert est_fichier_supporte("app.js") is True
        assert est_fichier_supporte("app.xyz123") is False


class TestAnalyseurJavaScript:

    def test_detecte_les_fonctions(self):
        fichier = creer_fichier(CODE_JS, ".js")
        try:
            analyseur = obtenir_analyseur(fichier)
            assert isinstance(analyseur, AnalyseurTreeSitter)
            resultat = analyseur.analyser()
            noms = {f.nom for f in resultat.fonctions}
            assert "additionner" in noms
            assert "boucleImbriquee" in noms
        finally:
            os.unlink(fichier)

    def test_detecte_boucle_imbriquee(self):
        fichier = creer_fichier(CODE_JS, ".js")
        try:
            metriques = auditer(fichier)
            categories = [a["categorie"] for a in metriques["annotations"]]
            assert "LILITH" in categories
        finally:
            os.unlink(fichier)


class TestAnalyseurJava:

    def test_detecte_methodes_et_classe(self):
        fichier = creer_fichier(CODE_JAVA, ".java")
        try:
            metriques = auditer(fichier)
            assert metriques["nb_fonctions"] == 2
            assert metriques["nb_classes"] == 1
        finally:
            os.unlink(fichier)


class TestAnalyseurGo:

    def test_detecte_fonctions_go(self):
        fichier = creer_fichier(CODE_GO, ".go")
        try:
            metriques = auditer(fichier)
            assert metriques["nb_fonctions"] == 2
        finally:
            os.unlink(fichier)


class TestAnalyseurRust:

    def test_detecte_trop_d_arguments(self):
        fichier = creer_fichier(CODE_RUST, ".rs")
        try:
            metriques = auditer(fichier)
            categories = [a["categorie"] for a in metriques["annotations"]]
            assert "SOUVERAINETE" in categories
        finally:
            os.unlink(fichier)


class TestAnalyseurC:

    def test_detecte_fonction_c(self):
        fichier = creer_fichier(CODE_C, ".c")
        try:
            metriques = auditer(fichier)
            assert metriques["nb_fonctions"] == 1
        finally:
            os.unlink(fichier)


class TestLangForce:

    def test_lang_force_sans_extension(self):
        """On force le langage 'ruby' sur un fichier sans extension reconnue."""
        code = """
        def additionner(a, b)
          a + b
        end
        """
        fichier = creer_fichier(code, ".rbtxt")
        try:
            metriques = auditer(fichier, lang="ruby")
            assert metriques["langage"] == "ruby"
        finally:
            os.unlink(fichier)
