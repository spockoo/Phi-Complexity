#!/usr/bin/env python3
"""Tests du système de patch/delta entre versions de toolchain.

Réseau entièrement mocké — aucun téléchargement réel.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from phi_complexity.toolchain.manifeste import (
    NOM_MANIFESTE_FICHIERS,
    generer_manifeste,
    lire_manifeste,
)
from phi_complexity.toolchain.delta import (
    calculer_delta,
    fichiers_a_extraire,
    resumer_delta,
)


def _manifeste_fictif(version, fichiers):
    """Construit un manifeste synthétique pour les tests."""
    return {
        "version": version,
        "genere_le": "2026-10-08T00:00:00+00:00",
        "fichiers": dict(fichiers),
    }


class TestManifeste(unittest.TestCase):
    def test_generer_et_lire(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Créer des fichiers fictifs.
            os.makedirs(os.path.join(tmp, "bin"))
            os.makedirs(os.path.join(tmp, "lib", "lean"))
            with open(os.path.join(tmp, "bin", "lean"), "w") as f:
                f.write("binaire")
            with open(os.path.join(tmp, "lib", "lean", "Init.olean"),
                      "w") as f:
                f.write("olean")

            m = generer_manifeste(tmp, "4.34.0")
            self.assertEqual(m["version"], "4.34.0")
            self.assertEqual(len(m["fichiers"]), 2)
            self.assertIn("bin/lean", m["fichiers"])
            # SHA256 vérifiable.
            attendu = hashlib.sha256(b"binaire").hexdigest()
            self.assertEqual(m["fichiers"]["bin/lean"], attendu)

            # Relire depuis le disque.
            m2 = lire_manifeste(tmp)
            self.assertEqual(m2["fichiers"], m["fichiers"])

    def test_lire_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(lire_manifeste(tmp))

    def test_ignore_marqueurs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for nom in (".valide", "lean.tar.zst", NOM_MANIFESTE_FICHIERS):
                with open(os.path.join(tmp, nom), "w") as f:
                    f.write("x")
            m = generer_manifeste(tmp, "4.34.0")
            self.assertEqual(len(m["fichiers"]), 0)


class TestDelta(unittest.TestCase):
    def test_premiere_installation(self):
        nouveau = _manifeste_fictif("4.35.0", {"a": "1", "b": "2"})
        delta = calculer_delta(None, nouveau)
        self.assertEqual(delta["de_version"], None)
        self.assertEqual(delta["vers_version"], "4.35.0")
        self.assertEqual(sorted(delta["ajoutes"]), ["a", "b"])
        self.assertEqual(delta["modifies"], [])
        self.assertEqual(delta["supprimes"], [])
        self.assertEqual(delta["inchanges"], 0)

    def test_aucun_changement(self):
        fichiers = {"a": "1", "b": "2", "c": "3"}
        ancien = _manifeste_fictif("4.34.0", fichiers)
        nouveau = _manifeste_fictif("4.34.0", dict(fichiers))
        delta = calculer_delta(ancien, nouveau)
        self.assertEqual(delta["ajoutes"], [])
        self.assertEqual(delta["modifies"], [])
        self.assertEqual(delta["supprimes"], [])
        self.assertEqual(delta["inchanges"], 3)

    def test_ajout_modif_suppression(self):
        ancien = _manifeste_fictif("4.34.0", {
            "a": "1",      # inchangé
            "b": "2",      # modifié
            "c": "3",      # supprimé
        })
        nouveau = _manifeste_fictif("4.35.0", {
            "a": "1",      # inchangé
            "b": "X",      # modifié
            "d": "4",      # ajouté
        })
        delta = calculer_delta(ancien, nouveau)
        self.assertEqual(delta["de_version"], "4.34.0")
        self.assertEqual(delta["vers_version"], "4.35.0")
        self.assertEqual(delta["ajoutes"], ["d"])
        self.assertEqual(delta["modifies"], ["b"])
        self.assertEqual(delta["supprimes"], ["c"])
        self.assertEqual(delta["inchanges"], 1)

    def test_fichiers_a_extraire(self):
        delta = {
            "ajoutes": ["d", "e"],
            "modifies": ["b"],
            "supprimes": ["c"],
            "inchanges": 10,
            "de_version": "4.34.0",
            "vers_version": "4.35.0",
        }
        self.assertEqual(
            fichiers_a_extraire(delta), ["b", "d", "e"])

    def test_resumer(self):
        delta = {
            "ajoutes": ["d"],
            "modifies": ["b", "c"],
            "supprimes": [],
            "inchanges": 100,
            "de_version": "4.34.0",
            "vers_version": "4.35.0",
        }
        resume = resumer_delta(delta)
        self.assertIn("4.34.0 -> 4.35.0", resume)
        self.assertIn("ajoutés   : 1", resume)
        self.assertIn("modifiés  : 2", resume)
        self.assertIn("inchangés : 100", resume)


class TestDeltaReel(unittest.TestCase):
    """Test avec le vrai manifeste de la 4.34.0 installée."""

    def test_delta_identique_a_soi_meme(self):
        rep = os.path.expanduser(
            "~/.cache/phi-complexity/toolchains/4.34.0-mini")
        if not os.path.isdir(rep):
            self.skipTest("toolchain 4.34.0 non installée")
        m = lire_manifeste(rep)
        if m is None:
            self.skipTest("manifeste non généré")
        # Delta d'un manifeste avec lui-même : tout inchangé.
        delta = calculer_delta(m, copy.deepcopy(m))
        self.assertEqual(delta["ajoutes"], [])
        self.assertEqual(delta["modifies"], [])
        self.assertEqual(delta["supprimes"], [])
        self.assertEqual(delta["inchanges"], len(m["fichiers"]))
        self.assertGreater(delta["inchanges"], 1900)


if __name__ == "__main__":
    unittest.main(verbosity=2)
