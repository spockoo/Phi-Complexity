#!/usr/bin/env python3
"""Tests unitaires pour mathlib_download.py.

Réseau entièrement mocké (règle Tomy). Aucun téléchargement réel.
"""

import hashlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phi_complexity.toolchain.mathlib_download import (
    DEFAUT_JOBS,
    MAX_JOBS,
    StatutTelechargement,
    ErreurTelechargement,
    sha256_fichier,
    fichier_valide,
    selection_miroir,
    meilleur_miroir,
    telecharger_fichier,
    telecharger_lot,
)


def _sha(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


class TestSha256(unittest.TestCase):
    def test_sha256_fichier(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"bonjour")
            chemin = f.name
        try:
            self.assertEqual(sha256_fichier(chemin), _sha(b"bonjour"))
        finally:
            os.unlink(chemin)

    def test_fichier_valide_ok(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"abc")
            chemin = f.name
        try:
            self.assertTrue(fichier_valide(chemin, _sha(b"abc")))
        finally:
            os.unlink(chemin)

    def test_fichier_valide_mauvais_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"abc")
            chemin = f.name
        try:
            self.assertFalse(fichier_valide(chemin, _sha(b"xyz")))
        finally:
            os.unlink(chemin)

    def test_fichier_valide_inexistant(self):
        self.assertFalse(fichier_valide("/tmp/n_existe_pas_xyz", _sha(b"a")))

    def test_fichier_valide_sans_hash(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"abc")
            chemin = f.name
        try:
            self.assertTrue(fichier_valide(chemin, None))
        finally:
            os.unlink(chemin)


class TestSelectionMiroir(unittest.TestCase):
    def test_tri_par_latence(self):
        miroirs = [("a", "http://a"), ("b", "http://b"), ("c", "http://c")]
        mesures = {"http://a": 0.5, "http://b": 0.1, "http://c": 0.3}
        res = selection_miroir(miroirs, _mesure=mesures.get)
        self.assertEqual([r[0] for r in res], ["b", "c", "a"])

    def test_injoignable_en_dernier(self):
        miroirs = [("a", "http://a"), ("b", "http://b")]
        mesures = {"http://a": None, "http://b": 0.2}
        res = selection_miroir(miroirs, _mesure=mesures.get)
        self.assertEqual([r[0] for r in res], ["b", "a"])

    def test_meilleur_miroir(self):
        miroirs = [("a", "http://a"), ("b", "http://b")]
        self.assertEqual(
            meilleur_miroir(miroirs, _mesure={"http://a": 0.9,
                                              "http://b": 0.1}.get),
            "http://b")

    def test_meilleur_miroir_aucun(self):
        miroirs = [("a", "http://a")]
        self.assertIsNone(meilleur_miroir(miroirs, _mesure=lambda u: None))


class _ReponseFausse:
    """Simule une réponse HTTP avec contenu prédéfini."""

    def __init__(self, contenu: bytes, status=200, headers=None):
        self._contenu = contenu
        self.status = status
        self.headers = headers or {}
        self._pos = 0

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._contenu) - self._pos
        chunk = self._contenu[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


class TestTelechargerFichier(unittest.TestCase):
    def _mock_urlopen(self, contenu, status=200, headers=None):
        def _ouvrir(req, timeout=None):
            return _ReponseFausse(contenu, status, headers or {})
        return _ouvrir

    def test_telechargement_simple(self):
        contenu = b"donnees de test" * 100
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with mock.patch("urllib.request.urlopen",
                            self._mock_urlopen(contenu)):
                res = telecharger_fichier(
                    "http://x/f.bin", dest, sha256_attendu=_sha(contenu))
            self.assertEqual(res["statut"], StatutTelechargement.TELECHARGE)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), contenu)

    def test_deja_en_cache(self):
        contenu = b"cache"
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with open(dest, "wb") as f:
                f.write(contenu)
            # Même avec un urlopen qui échouerait, on ne doit pas l'appeler.
            with mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("ne pas appeler")):
                res = telecharger_fichier(
                    "http://x/f.bin", dest, sha256_attendu=_sha(contenu))
            self.assertEqual(res["statut"], StatutTelechargement.DEJA_EN_CACHE)

    def test_hash_incoherent(self):
        contenu = b"corrompu"
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with mock.patch("urllib.request.urlopen",
                            self._mock_urlopen(contenu)):
                with self.assertRaises(ErreurTelechargement) as ctx:
                    telecharger_fichier("http://x/f.bin", dest,
                                        sha256_attendu=_sha(b"autre"))
            self.assertEqual(ctx.exception.statut,
                             StatutTelechargement.ECHEC_HASH)
            # Le fichier corrompu doit être supprimé.
            self.assertFalse(os.path.exists(dest))

    def test_erreur_reseau(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with mock.patch("urllib.request.urlopen",
                            side_effect=urllib.error.URLError("timeout")):
                with self.assertRaises(ErreurTelechargement) as ctx:
                    telecharger_fichier("http://x/f.bin", dest)
            self.assertEqual(ctx.exception.statut,
                             StatutTelechargement.ECHEC_RESEAU)

    def test_reprise_range(self):
        # Simule un serveur qui honore Range (206 + Content-Range).
        partie1 = b"AAAA"
        partie2 = b"BBBB"
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with open(dest, "wb") as f:
                f.write(partie1)

            def _ouvrir(req, timeout=None):
                # Vérifier que le header Range est présent.
                self.assertEqual(req.headers.get("Range"), "bytes=4-")
                return _ReponseFausse(
                    partie2, status=206,
                    headers={"Content-Range": "bytes 4-7/8"})

            with mock.patch("urllib.request.urlopen", _ouvrir):
                res = telecharger_fichier(
                    "http://x/f.bin", dest,
                    sha256_attendu=_sha(partie1 + partie2))
            self.assertEqual(res["statut"], StatutTelechargement.REPRIS)
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), partie1 + partie2)

    def test_progression_appelee(self):
        contenu = b"x" * 100
        appels = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "f.bin")
            with mock.patch("urllib.request.urlopen",
                            self._mock_urlopen(contenu)):
                telecharger_fichier(
                    "http://x/f.bin", dest,
                    progression=lambda r, t: appels.append((r, t)))
            self.assertTrue(len(appels) > 0)
            self.assertEqual(appels[-1][0], 100)


class TestTelechargerLot(unittest.TestCase):
    def test_lot_parallele(self):
        contenus = {f"http://x/f{i}.bin": b"data%d" % i for i in range(5)}

        def _ouvrir(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            # urllib Request : req.full_url
            u = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
            return _ReponseFausse(contenus[u])

        with tempfile.TemporaryDirectory() as tmp:
            fichiers = [
                {"url": url,
                 "destination": os.path.join(tmp, f"f{i}.bin"),
                 "sha256": _sha(contenus[url])}
                for i, url in enumerate(contenus)
            ]
            with mock.patch("urllib.request.urlopen", _ouvrir):
                res = telecharger_lot(fichiers, jobs=3)
            self.assertEqual(len(res["reussis"]), 5)
            self.assertEqual(len(res["echecs"]), 0)
            self.assertGreaterEqual(res["duree_s"], 0)

    def test_lot_avec_echec(self):
        import urllib.error

        def _ouvrir(req, timeout=None):
            u = req.get_full_url()
            if "mauvais" in u:
                raise urllib.error.URLError("boom")
            return _ReponseFausse(b"ok")

        with tempfile.TemporaryDirectory() as tmp:
            fichiers = [
                {"url": "http://x/bon.bin",
                 "destination": os.path.join(tmp, "bon.bin")},
                {"url": "http://x/mauvais.bin",
                 "destination": os.path.join(tmp, "mauvais.bin")},
            ]
            with mock.patch("urllib.request.urlopen", _ouvrir):
                res = telecharger_lot(fichiers, jobs=2)
            self.assertEqual(len(res["reussis"]), 1)
            self.assertEqual(len(res["echecs"]), 1)
            self.assertEqual(res["echecs"][0]["statut"],
                             StatutTelechargement.ECHEC_RESEAU)

    def test_jobs_bornes(self):
        # jobs=0 -> 1, jobs=999 -> MAX_JOBS (pas d'explosion de threads).
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen",
                            side_effect=Exception("ne pas appeler")):
                # Lot vide : rien à télécharger, mais le pool doit se créer.
                res = telecharger_lot([], jobs=0)
                self.assertEqual(res["reussis"], [])
                res = telecharger_lot([], jobs=999)
                self.assertEqual(res["reussis"], [])

    def test_progression_globale(self):
        def _ouvrir(req, timeout=None):
            return _ReponseFausse(b"z")

        appels = []
        with tempfile.TemporaryDirectory() as tmp:
            fichiers = [
                {"url": f"http://x/f{i}.bin",
                 "destination": os.path.join(tmp, f"f{i}.bin")}
                for i in range(3)
            ]
            with mock.patch("urllib.request.urlopen", _ouvrir):
                telecharger_lot(
                    fichiers, jobs=2,
                    progression_globale=lambda t, tot, cur: appels.append(t))
            self.assertEqual(sorted(appels), [1, 2, 3])


class TestConstantes(unittest.TestCase):
    def test_jobs_raisonnables(self):
        self.assertGreaterEqual(DEFAUT_JOBS, 1)
        self.assertLessEqual(DEFAUT_JOBS, MAX_JOBS)
        self.assertLessEqual(MAX_JOBS, 32)  # pas de DDoS


if __name__ == "__main__":
    unittest.main(verbosity=2)
