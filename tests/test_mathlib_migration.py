#!/usr/bin/env python3
"""Tests unitaires de la migration Mathlib (``phi_complexity.toolchain.mathlib``).

Périmètre : inventaire, delta inter-versions, migration avec rollback,
journal, pointeur actif, idempotence.

RÈGLE DURE : aucun accès réseau réel. Tout est simulé dans des dossiers
temporaires ; l'« installateur » est une fonction factice qui écrit de
faux oleans.

Exécution (stdlib uniquement) :
    cd ~/workspace/phi-pub && python3 tests/test_mathlib_migration.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phi_complexity.toolchain import mathlib as m


def _faux_installateur(tag, commit, destination):
    """Simule `lake exe cache get` : écrit de faux oleans."""
    os.makedirs(destination, exist_ok=True)
    with open(os.path.join(destination, "Mathlib.olean"), "w") as f:
        f.write("faux olean pour %s" % tag)
    sous = os.path.join(destination, "Mathlib", "Data")
    os.makedirs(sous, exist_ok=True)
    with open(os.path.join(sous, "Nat.olean"), "w") as f:
        f.write("faux olean Nat pour %s" % tag)


class TestInventaireEtDelta(unittest.TestCase):
    def test_inventaire_version_absente(self):
        with tempfile.TemporaryDirectory() as base:
            self.assertIsNone(m.inventaire_mathlib("v9.9.9", base=base))

    def test_inventaire_et_delta(self):
        with tempfile.TemporaryDirectory() as base:
            d1 = m.chemin_cache_mathlib("v4.34.0", base=base)
            d2 = m.chemin_cache_mathlib("v4.34.1", base=base)
            os.makedirs(os.path.join(d1, "Mathlib"), exist_ok=True)
            os.makedirs(os.path.join(d2, "Mathlib"), exist_ok=True)
            # v1 : deux fichiers
            with open(os.path.join(d1, "Mathlib", "A.olean"), "w") as f:
                f.write("A v1")
            with open(os.path.join(d1, "Mathlib", "B.olean"), "w") as f:
                f.write("B v1")
            # v2 : A modifié, B inchangé, C ajouté
            with open(os.path.join(d2, "Mathlib", "A.olean"), "w") as f:
                f.write("A v2 MODIFIE")
            with open(os.path.join(d2, "Mathlib", "B.olean"), "w") as f:
                f.write("B v1")
            with open(os.path.join(d2, "Mathlib", "C.olean"), "w") as f:
                f.write("C nouveau")

            inv1 = m.inventaire_mathlib("v4.34.0", base=base)
            inv2 = m.inventaire_mathlib("v4.34.1", base=base)
            self.assertEqual(inv1["version"], "v4.34.0")
            self.assertEqual(len(inv1["fichiers"]), 2)

            delta = m.delta_mathlib("v4.34.0", "v4.34.1", base=base)
            self.assertEqual(delta["de_version"], "v4.34.0")
            self.assertEqual(delta["vers_version"], "v4.34.1")
            self.assertEqual(len(delta["modifies"]), 1)   # A
            self.assertEqual(len(delta["ajoutes"]), 1)    # C
            self.assertEqual(delta["inchanges"], 1)       # B
            self.assertEqual(len(delta["supprimes"]), 0)

    def test_delta_cible_absente(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(m.VersionIntrouvable):
                m.delta_mathlib("v4.34.0", "v9.9.9", base=base)


class TestMigration(unittest.TestCase):
    def test_migration_sans_feu_vert_refusee(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(m.TelechargementRefuse):
                m.migrer_mathlib("v4.34.1", commit_cible="abc123",
                                 feu_vert=False, base=base,
                                 installateur=_faux_installateur)

    def test_migration_sans_installateur(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(m.TelechargementRefuse):
                m.migrer_mathlib("v4.34.1", commit_cible="abc123",
                                 feu_vert=True, base=base,
                                 installateur=None)

    def test_migration_complete(self):
        with tempfile.TemporaryDirectory() as base:
            res = m.migrer_mathlib("v4.34.1", commit_cible="abc123",
                                   feu_vert=True, base=base,
                                   installateur=_faux_installateur)
            self.assertEqual(res["tag"], "v4.34.1")
            self.assertFalse(res["deja_installee"])
            # Pointeur actif basculé
            self.assertEqual(m.version_active_mathlib(base=base), "v4.34.1")
            # Marqueur .valide écrit
            inst = m.version_installee(base=base)
            self.assertEqual(inst["tag"], "v4.34.1")

    def test_migration_idempotente(self):
        with tempfile.TemporaryDirectory() as base:
            m.migrer_mathlib("v4.34.1", commit_cible="abc123",
                             feu_vert=True, base=base,
                             installateur=_faux_installateur)
            res = m.migrer_mathlib("v4.34.1", commit_cible="abc123",
                                   feu_vert=True, base=base,
                                   installateur=_faux_installateur)
            self.assertTrue(res["deja_installee"])

    def test_rollback(self):
        with tempfile.TemporaryDirectory() as base:
            m.migrer_mathlib("v4.34.0", commit_cible="aaa",
                             feu_vert=True, base=base,
                             installateur=_faux_installateur)
            m.migrer_mathlib("v4.34.1", commit_cible="bbb",
                             feu_vert=True, base=base,
                             installateur=_faux_installateur)
            self.assertEqual(m.version_active_mathlib(base=base), "v4.34.1")
            res = m.revenir_mathlib(base=base)
            self.assertEqual(res["tag"], "v4.34.0")
            self.assertEqual(m.version_active_mathlib(base=base), "v4.34.0")

    def test_rollback_sans_historique(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(m.ErreurMathlib):
                m.revenir_mathlib(base=base)

    def test_journal(self):
        with tempfile.TemporaryDirectory() as base:
            m.migrer_mathlib("v4.34.1", commit_cible="abc",
                             feu_vert=True, base=base,
                             installateur=_faux_installateur)
            journal = m._journal_mathlib(base)
            self.assertTrue(any(e.get("action") == "migration"
                                for e in journal))


class TestVersionLeanAssociee(unittest.TestCase):
    def test_extraction(self):
        self.assertEqual(m._version_lean_associee("v4.34.1"), "4.34.1")

    def test_invalide(self):
        with self.assertRaises(m.VersionIntrouvable):
            m._version_lean_associee("nimporte-quoi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
