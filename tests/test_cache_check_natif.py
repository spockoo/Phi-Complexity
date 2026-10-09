#!/usr/bin/env python3
"""test_cache_check_natif.py — Intégration native du parse cache dans `phi check`.

Mission PHI-NATIF-C : le parse cache (ParseCache + calculer_hash_src) est
branché sur `phi check` de façon transparente.

Couvre :
- résolution de la racine disque (.phi_cache/ dans le dossier audité,
  ~/.phi_cache en repli)
- session : exposition/retrait du cache courant, persistance disque
- transparence : métriques byte-identiques avec et sans cache
- hit effectif au second passage (le re-parse est évité)
- invalidation : un fichier modifié est re-parsé (hash différent)
- --sans-cache : aucun cache exposé
- dégradation gracieuse : cache défaillant => parse normal, jamais de crash
- SyntaxError remonte comme avant, même avec cache actif
- .gitignore ignore .phi_cache/
"""
import os
import shutil
import tempfile
import textwrap
from pathlib import Path

from phi_complexity import auditer, rapport_json
from phi_complexity.cache import (
    NOM_DOSSIER_CACHE,
    ParseCache,
    definir_cache_courant,
    obtenir_cache_courant,
    resoudre_racine_cache,
    session_parse_cache,
)
from phi_complexity.langs.python_native import AnalyseurPython


CODE_SIMPLE = """
def ajouter(a, b):
    return a + b

def multiplier(a, b):
    return a * b
"""

CODE_MODIFIE = """
def ajouter(a, b):
    return a + b

def soustraire(a, b):
    return a - b
"""

CODE_INVALIDE = """
def brisee(:
    return ???
"""


def ecrire_py(dossier, nom="module_test.py", code=CODE_SIMPLE):
    chemin = os.path.join(dossier, nom)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(code))
    return chemin


class TestResoudreRacineCache:
    """La racine disque suit la règle : .phi_cache/ du dossier audité."""

    def test_dossier_audite(self):
        dossier = tempfile.mkdtemp()
        try:
            assert resoudre_racine_cache(dossier) == Path(dossier) / ".phi_cache"
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_fichier_audite(self):
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            assert resoudre_racine_cache(fichier) == Path(dossier) / ".phi_cache"
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_cible_inexistante_repli_home(self):
        racine = resoudre_racine_cache("/chemin/qui/n/existe/pas")
        assert racine == Path.home() / NOM_DOSSIER_CACHE

    def test_nom_dossier_cache(self):
        assert NOM_DOSSIER_CACHE == ".phi_cache"


class TestSessionParseCache:
    """La session expose le cache courant puis le retire proprement."""

    def test_exposition_et_restauration(self):
        assert obtenir_cache_courant() is None
        dossier = tempfile.mkdtemp()
        try:
            with session_parse_cache(dossier) as cache:
                assert cache is not None
                assert obtenir_cache_courant() is cache
            assert obtenir_cache_courant() is None
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_sans_cache_expose_none(self):
        dossier = tempfile.mkdtemp()
        try:
            with session_parse_cache(dossier, sans_cache=True) as cache:
                assert cache is None
                assert obtenir_cache_courant() is None
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_persistance_disque(self):
        """Le cache est sauvegardé dans .phi_cache/ à la sortie de session."""
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            with session_parse_cache(dossier):
                AnalyseurPython(fichier).charger()
            manifest = Path(dossier) / ".phi_cache" / "manifest.json"
            assert manifest.is_file(), ".phi_cache/manifest.json manquant"
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_rechargement_disque_fait_hit(self):
        """Une seconde session recharge le disque : hit immédiat, 0 miss."""
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            with session_parse_cache(dossier):
                AnalyseurPython(fichier).charger()
            with session_parse_cache(dossier) as cache2:
                AnalyseurPython(fichier).charger()
                stats = cache2.stats()
            assert stats["hits"] >= 1
            assert stats["misses"] == 0
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestCacheActif:
    """Le cache évite le re-parse et s'invalide sur modification."""

    def test_second_charger_fait_hit(self):
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            with session_parse_cache(dossier) as cache:
                AnalyseurPython(fichier).charger()  # miss
                AnalyseurPython(fichier).charger()  # hit
                stats = cache.stats()
            assert stats["misses"] == 1
            assert stats["hits"] == 1
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_fichier_modifie_invalide(self):
        """Octets changés => hash changé => re-parse, nouvel AST."""
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            with session_parse_cache(dossier) as cache:
                a1 = AnalyseurPython(fichier).charger().analyser()
                noms1 = sorted(f.nom for f in a1.fonctions)
                with open(fichier, "w", encoding="utf-8") as f:
                    f.write(textwrap.dedent(CODE_MODIFIE))
                a2 = AnalyseurPython(fichier).charger().analyser()
                noms2 = sorted(f.nom for f in a2.fonctions)
                stats = cache.stats()
            assert noms1 == ["ajouter", "multiplier"]
            assert noms2 == ["ajouter", "soustraire"]
            assert stats["misses"] == 2  # deux contenus distincts
            assert stats["hits"] == 0
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestTransparence:
    """Sortie byte-identique avec et sans cache (contrat PHI-NATIF-C)."""

    def test_metriques_identiques(self):
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            sans = auditer(fichier)
            with session_parse_cache(dossier):
                avec = auditer(fichier)
            assert avec == sans
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_rapport_json_identique(self):
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            sans = rapport_json(fichier)
            with session_parse_cache(dossier):
                # Deux passages : le second est servi par le cache (hit).
                AnalyseurPython(fichier).charger()
                avec = rapport_json(fichier)
            assert avec == sans
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestDegradationGracieuse:
    """Un cache défaillant ne fait jamais échouer un audit."""

    def test_cache_qui_explose(self):
        class CacheExplosif:
            def get(self, nom, hash_src):
                raise RuntimeError("get en panne")
            def put(self, nom, hash_src, ast):
                raise RuntimeError("put en panne")
            def stats(self):
                raise RuntimeError("stats en panne")

        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier)
            definir_cache_courant(CacheExplosif())
            try:
                resultat = AnalyseurPython(fichier).charger().analyser()
            finally:
                definir_cache_courant(None)
            assert len(resultat.fonctions) == 2
        finally:
            shutil.rmtree(dossier, ignore_errors=True)

    def test_syntax_error_remonte_avec_cache(self):
        """Une SyntaxError n'est jamais mise en cache et remonte telle quelle."""
        dossier = tempfile.mkdtemp()
        try:
            fichier = ecrire_py(dossier, code=CODE_INVALIDE)
            with session_parse_cache(dossier) as cache:
                for _ in range(2):
                    try:
                        AnalyseurPython(fichier).charger()
                    except SyntaxError:
                        pass
                    else:
                        raise AssertionError("SyntaxError attendue")
                # Aucune entrée : l'échec de parse n'a rien mis en cache.
                assert cache.stats()["entrees"] == 0
        finally:
            shutil.rmtree(dossier, ignore_errors=True)


class TestGitignore:
    """Le cache disque ne doit jamais être versionné."""

    def test_phi_cache_ignore(self):
        racine_projet = Path(__file__).resolve().parent.parent
        gitignore = racine_projet / ".gitignore"
        assert gitignore.is_file()
        contenu = gitignore.read_text(encoding="utf-8")
        assert ".phi_cache/" in contenu
