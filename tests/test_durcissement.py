"""
tests/test_durcissement.py — v0.6.1 « Durcissement de l'index ».

Trois défauts révélés par un dogfood honnêtement négatif (0 symbole sur un
projet Lean, .lake/ pollué, silences trompeurs), trois correctifs testés :

1. Exclusions par défaut (.lake/, .git/, __pycache__/, …) + --no-exclude/--exclude.
2. Rapport « langage non supporté » par fichier (champ `non_supportes`).
3. Avertissement explicite si tree-sitter-language-pack est absent.
"""
import argparse
import json
import textwrap

import pytest

from phi_complexity.carte import carte_projet, carte_console
from phi_complexity.editeur.indexeur import (
    indexer_projet,
    fichiers_non_supportes,
    EXCLUSIONS_DEFAUT,
)
import phi_complexity.carte as module_carte
import phi_complexity.langs.registry as registre


CODE_SIMPLE = textwrap.dedent('''
    def alpha(x):
        if x > 0:
            return x + 1
        return 0
''')


@pytest.fixture
def projet_piege(tmp_path, monkeypatch):
    """Projet avec pièges : dépendances, caches, fichiers non supportés.

    Simule l'environnement CI v0.6.1 (pack tree-sitter absent) : le
    `modele.lean` reste un leurre « non supporté ». Sans cela, un pack
    installé rendrait le .lean indexable et fausserait les comptes —
    ce qui testerait l'environnement, pas les exclusions.
    """
    monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(CODE_SIMPLE, encoding="utf-8")
    (tmp_path / ".lake" / "packages").mkdir(parents=True)
    (tmp_path / ".lake" / "packages" / "dep.py").write_text(CODE_SIMPLE, encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.py").write_text(CODE_SIMPLE, encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("function f(){}\n", encoding="utf-8")
    (tmp_path / "mon.egg-info").mkdir()
    (tmp_path / "mon.egg-info" / "meta.py").write_text(CODE_SIMPLE, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("pas du code", encoding="utf-8")
    (tmp_path / "modele.lean").write_text("def foo : Nat := 42\n", encoding="utf-8")
    return str(tmp_path)


class TestExclusions:
    def test_exclusions_par_defaut(self, projet_piege):
        """Par défaut : .lake/, __pycache__/, node_modules/, *.egg-info/ ignorés."""
        index = indexer_projet(projet_piege)
        assert len(index) == 1
        assert list(index)[0].endswith("src/a.py")

    def test_no_exclude_indexe_tout(self, projet_piege):
        """exclusions=[] : les dépendances sont indexées (choix explicite)."""
        index = indexer_projet(projet_piege, exclusions=[])
        noms = sorted(index)
        assert any(".lake" in n for n in noms)
        assert any("__pycache__" in n for n in noms)
        assert any("mon.egg-info" in n for n in noms)

    def test_exclude_supplementaire(self, projet_piege):
        """Une exclusion explicite s'ajoute aux défauts."""
        index = indexer_projet(projet_piege,
                               exclusions=list(EXCLUSIONS_DEFAUT) + ["src"])
        assert index == {}

    def test_exclusions_dans_non_supportes(self, projet_piege):
        """Les fichiers exclus n'apparaissent pas non plus en non-supportés."""
        ns = fichiers_non_supportes(projet_piege)
        assert not any(".lake" in e["fichier"] for e in ns)
        assert not any("__pycache__" in e["fichier"] for e in ns)


class TestNonSupportes:
    def test_champ_present_et_correct(self, projet_piege, monkeypatch):
        """non_supportes : .txt (extension inconnue), .lean (tree-sitter manquant)."""
        monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
        carte = carte_projet(projet_piege)
        par_fichier = {e["fichier"]: e for e in carte["non_supportes"]}
        txt = next(f for f in par_fichier if f.endswith("notes.txt"))
        lean = next(f for f in par_fichier if f.endswith("modele.lean"))
        assert par_fichier[txt]["extension"] == ".txt"
        assert par_fichier[txt]["raison"] == "extension inconnue"
        assert par_fichier[lean]["extension"] == ".lean"
        assert par_fichier[lean]["raison"] == "tree-sitter manquant"

    def test_non_supportes_vide_quand_lang_force(self, projet_piege, monkeypatch):
        """Langage forcé : l'utilisateur assume, rien n'est déclaré non supporté."""
        monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
        carte = carte_projet(projet_piege, lang="python")
        assert carte["non_supportes"] == []

    def test_console_affiche_la_section(self, projet_piege, monkeypatch):
        monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
        sortie = carte_console(carte_projet(projet_piege))
        assert "NON SUPPORT" in sortie
        assert "modele.lean" in sortie


class TestAvertissementTreesitter:
    def test_avertissement_simule_absent(self, projet_piege, monkeypatch):
        """Pack absent (simulé) : avertissements explicites, pas de silence."""
        monkeypatch.setattr(module_carte, "treesitter_disponible", lambda: False)
        monkeypatch.setattr(registre, "treesitter_disponible", lambda: False)
        carte = carte_projet(projet_piege)
        assert len(carte["avertissements"]) >= 2
        texte = " ".join(carte["avertissements"])
        assert "tree-sitter-language-pack" in texte
        assert "multilang" in texte
        sortie = carte_console(carte)
        assert "AVERTISSEMENTS" in sortie

    def test_silence_quand_present(self, projet_piege, monkeypatch):
        """Pack présent (simulé) : aucun avertissement."""
        monkeypatch.setattr(module_carte, "treesitter_disponible", lambda: True)
        carte = carte_projet(projet_piege)
        assert carte["avertissements"] == []


class TestCLI:
    def _args(self, dossier, **kw):
        base = {"dossier": dossier, "format": "console", "lang": None,
                "exclude": None, "no_exclude": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_index_console_ok(self, projet_piege, capsys):
        from phi_complexity.cli import _executer_index
        code = _executer_index(self._args(projet_piege))
        assert code == 0
        sortie = capsys.readouterr().out
        assert "CARTE DU PROJET" in sortie

    def test_index_json_cles(self, projet_piege, capsys):
        from phi_complexity.cli import _executer_index
        code = _executer_index(self._args(projet_piege, format="json"))
        assert code == 0
        carte = json.loads(capsys.readouterr().out)
        assert "non_supportes" in carte and "avertissements" in carte

    def test_cli_no_exclude(self, projet_piege, capsys):
        from phi_complexity.cli import _executer_index
        code = _executer_index(self._args(projet_piege, format="json",
                                         no_exclude=True))
        assert code == 0
        carte = json.loads(capsys.readouterr().out)
        assert any(".lake" in f["fichier"] for f in carte["fichiers"])
