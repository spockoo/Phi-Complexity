#!/usr/bin/env python3
"""Tests unitaires du module mathlib — réseau entièrement mocké.

Aucun appel réseau réel : `_ouvreur` injecte des réponses simulées.
"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phi_complexity.toolchain import mathlib as M


# --- Helpers de mock -------------------------------------------------------

class ReponseFausse:
    """Simule une réponse urllib (GET)."""

    def __init__(self, corps_octets):
        self._corps = corps_octets

    def read(self):
        return self._corps

    def close(self):
        pass


def ouvreur_json(donnees):
    """Fabrique un _ouvreur qui retourne `donnees` en JSON."""
    corps = json.dumps(donnees).encode("utf-8")

    def ouvrir(url):
        return ReponseFausse(corps)

    return ouvrir


def ouvreur_404(url):
    """Simule un 404 (cache absent)."""
    raise IOError("HTTP Error 404: Not Found")


def ouvreur_ok_head(url):
    """Simule un HEAD réussi (marqueur présent)."""

    class Tete:
        def close(self):
            pass

    # L'implémentation appelle ouvrir(req) avec un Request ;
    # on accepte url ou Request.
    return Tete()


# --- Tests : tag_pour_lean -------------------------------------------------

class TestTagPourLean(unittest.TestCase):
    def test_tag_simple(self):
        ouv = ouvreur_json({"object": {"type": "commit", "sha": "abc123"}})
        r = M.tag_pour_lean("4.34.0", _ouvreur=ouv)
        self.assertEqual(r["tag"], "v4.34.0")
        self.assertEqual(r["commit"], "abc123")

    def test_tag_avec_prefixe_v(self):
        ouv = ouvreur_json({"object": {"type": "commit", "sha": "def456"}})
        r = M.tag_pour_lean("v4.34.0", _ouvreur=ouv)
        self.assertEqual(r["tag"], "v4.34.0")

    def test_tag_annote_resolu(self):
        # Premier appel : tag annoté ; second : l'objet tag -> commit.
        appels = {"n": 0}

        def ouvrir(url):
            appels["n"] += 1
            if appels["n"] == 1:
                return ReponseFausse(json.dumps({
                    "object": {"type": "tag", "sha": "t1",
                               "url": "https://api.github.com/x"},
                }).encode())
            return ReponseFausse(json.dumps({
                "object": {"type": "commit", "sha": "c-commit"},
            }).encode())

        r = M.tag_pour_lean("4.34.0", _ouvreur=ouvrir)
        self.assertEqual(r["commit"], "c-commit")

    def test_version_illisible(self):
        with self.assertRaises(M.VersionIntrouvable):
            M.tag_pour_lean("nimporte", _ouvreur=ouvreur_json({}))

    def test_tag_absent(self):
        def ouvrir(url):
            raise IOError("HTTP Error 404")
        with self.assertRaises(M.VersionIntrouvable):
            M.tag_pour_lean("4.99.0", _ouvreur=ouvrir)


# --- Tests : dernier_tag_stable --------------------------------------------

class TestDernierTagStable(unittest.TestCase):
    def test_ignore_rc(self):
        ouv = ouvreur_json([
            {"name": "v4.35.0-rc4", "commit": {"sha": "rc"}},
            {"name": "v4.34.1", "commit": {"sha": "stable1"}},
            {"name": "v4.34.0", "commit": {"sha": "stable0"}},
        ])
        r = M.dernier_tag_stable(_ouvreur=ouv)
        self.assertEqual(r["tag"], "v4.34.1")
        self.assertEqual(r["commit"], "stable1")

    def test_aucun_stable(self):
        ouv = ouvreur_json([{"name": "v4.35.0-rc1", "commit": {"sha": "x"}}])
        with self.assertRaises(M.VersionIntrouvable):
            M.dernier_tag_stable(_ouvreur=ouv)


# --- Tests : cache_disponible ----------------------------------------------

class TestCacheDisponible(unittest.TestCase):
    def test_marqueur_present(self):
        # Simule ouvrir(Request) -> succès.
        def ouvrir(req):
            class T:
                def close(self):
                    pass
            return T()
        self.assertTrue(M.cache_disponible("abc", _ouvreur=ouvrir))

    def test_marqueur_absent_404(self):
        self.assertFalse(M.cache_disponible("abc", _ouvreur=ouvreur_404))

    def test_erreur_reseau(self):
        def ouvrir(req):
            raise IOError("Connection reset")
        with self.assertRaises(M.ErreurReseauMathlib):
            M.cache_disponible("abc", _ouvreur=ouvrir)


# --- Tests : url_marqueur / url_base_cache ---------------------------------

class TestUrls(unittest.TestCase):
    def test_url_marqueur_format(self):
        url = M.url_marqueur("deadbeef")
        self.assertIn("mathlib4-master", url)
        self.assertIn("leanprover-community/mathlib4", url)
        self.assertTrue(url.endswith("deadbeef"))

    def test_miroir_env(self):
        os.environ[M.VAR_MIROIR] = "https://miroir.example.com/"
        try:
            self.assertEqual(M.url_base_cache(), "https://miroir.example.com")
        finally:
            del os.environ[M.VAR_MIROIR]
        self.assertEqual(M.url_base_cache(), M.URL_CACHE_DEFAUT)


# --- Tests : version_installee / marquer_installee --------------------------

class TestVersionInstallee(unittest.TestCase):
    def test_absente(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(M.version_installee(base=d))

    def test_marquer_et_lire(self):
        with tempfile.TemporaryDirectory() as d:
            M.marquer_installee("v4.34.0", "abc123", base=d)
            v = M.version_installee(base=d)
            self.assertEqual(v["tag"], "v4.34.0")
            self.assertEqual(v["commit"], "abc123")

    def test_marqueur_corrompu(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".valide"), "w") as f:
                f.write("pas du json {{{")
            self.assertIsNone(M.version_installee(base=d))

    def test_chemin_cache_sanitize(self):
        p = M.chemin_cache_mathlib("v4.34.0;rm -rf /")
        self.assertNotIn(";", p)
        self.assertIn("v4.34.0", p.replace("_", "v4.34.0") or p)


# --- Tests : plan_mise_a_jour ----------------------------------------------

class TestPlanMiseAJour(unittest.TestCase):
    def _ouvreur_plan(self, commit="c1", cache_ok=True):
        def ouvrir(url):
            if "api.github.com" in str(url):
                return ReponseFausse(json.dumps({
                    "object": {"type": "commit", "sha": commit},
                }).encode())
            # HEAD pour le marqueur
            if not cache_ok:
                raise IOError("HTTP Error 404")
            class T:
                def close(self):
                    pass
            return T()
        return ouvrir

    def test_plan_complet(self):
        ouv = self._ouvreur_plan(commit="c1", cache_ok=True)
        plan = M.plan_mise_a_jour("4.34.0", _ouvreur=ouv)
        self.assertEqual(plan["tag"], "v4.34.0")
        self.assertEqual(plan["commit"], "c1")
        self.assertTrue(plan["cache_disponible"])
        self.assertTrue(plan["feu_vert_requis"])  # >100 Mo
        self.assertIn("taille_estimee_octets", plan)

    def test_cache_expire(self):
        ouv = self._ouvreur_plan(commit="c1", cache_ok=False)
        with self.assertRaises(M.CacheExpire):
            M.plan_mise_a_jour("4.34.0", _ouvreur=ouv)


# --- Tests : verifier_feu_vert --------------------------------------------

class TestFeuVert(unittest.TestCase):
    def test_refuse_sans_feu_vert(self):
        plan = {"tag": "v4.34.0", "feu_vert_requis": True,
                "taille_estimee_octets": 2 * 1024 ** 3}
        with self.assertRaises(M.TelechargementRefuse):
            M.verifier_feu_vert(plan, feu_vert=False)

    def test_accepte_avec_feu_vert(self):
        plan = {"tag": "v4.34.0", "feu_vert_requis": True,
                "taille_estimee_octets": 2 * 1024 ** 3}
        M.verifier_feu_vert(plan, feu_vert=True)  # ne lève pas

    def test_pas_de_feu_vert_requis(self):
        plan = {"tag": "v4.34.0", "feu_vert_requis": False,
                "taille_estimee_octets": 10}
        M.verifier_feu_vert(plan, feu_vert=False)  # ne lève pas


if __name__ == "__main__":
    unittest.main()
