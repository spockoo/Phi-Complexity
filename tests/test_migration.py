#!/usr/bin/env python3
"""Tests unitaires de la couche migration (``phi_complexity.toolchain.migration``).

Périmètre : pointeur de version active (``.active``), journal JSONL des
migrations, bascule, rollback, nettoyage, détection de projets
``lean-toolchain``, registre des versions connues.

RÈGLE DURE : aucun accès réseau réel. Le réseau est systématiquement
mocké : ``ToolchainManager`` est patché dans le namespace de
``phi_complexity.toolchain.migration`` (le vrai ``installer`` n'est
jamais appelé) ; les installations sont simulées dans un cache
temporaire (``<version>-mini/bin/lean`` + marqueur ``.valide`` cohérent).

Exécution (stdlib uniquement) :
    cd ~/workspace/phi-pub && python3 tests/test_migration.py
Compatible pytest quand il est disponible :
    python3 -m pytest tests/test_migration.py -q
"""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Permet l'exécution directe du fichier depuis n'importe quel cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from phi_complexity.toolchain import (
    GestionnaireVersions,
    MigrationImpossible,
    RollbackImpossible,
    TelechargementRefuse,
    VersionInconnue,
    manifeste_pour_version,
)
from phi_complexity.toolchain import manager as module_manager
from phi_complexity.toolchain import migration as module_migration


def _sha(version):
    """SHA256 épinglé au registre pour `version`."""
    return manifeste_pour_version(version)["sha256"]


class _UsineFauxGestionnaire:
    """Fabrique patchant ToolchainManager dans migration.migrer.

    Le faux `installer()` simule l'installation dans le cache du test
    (dossier <version>-mini + .valide cohérent) — aucun réseau.
    """

    def __init__(self, test):
        self._test = test
        self.installations = []

    def __call__(self, cache_dir, manifeste=None):
        test = self._test
        usine = self
        faux = SimpleNamespace()
        faux.manifeste = manifeste

        def _installer(progression=None):
            usine.installations.append(manifeste["version"])
            test._simuler(manifeste["version"], manifeste["sha256"])
            return {"statut": "INSTALLEE"}

        faux.installer = _installer
        return faux


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "toolchains")
        os.makedirs(self.cache)
        self.gv = GestionnaireVersions(cache_dir=self.cache)

    # ── aide ──────────────────────────────────────────

    def _simuler(self, version, sha256, natif=False,
                 mis_a_jour_depuis=None):
        """Simule une installation validée dans le cache du test."""
        rep = os.path.join(self.cache, "%s-mini" % version)
        os.makedirs(os.path.join(rep, "bin"), exist_ok=True)
        open(os.path.join(rep, "bin", "lean"), "w").close()
        marqueur = {"version": version, "sha256": sha256,
                    "natif": natif}
        if mis_a_jour_depuis is not None:
            marqueur["mis_a_jour_depuis"] = mis_a_jour_depuis
        with open(os.path.join(rep, ".valide"), "w",
                  encoding="utf-8") as f:
            json.dump(marqueur, f)
        return rep

    # ── exports ───────────────────────────────────────

    def test_exports_init(self):
        """Le __init__ exporte la couche migration."""
        self.assertIsNotNone(GestionnaireVersions)
        self.assertIsNotNone(manifeste_pour_version)
        for exc in (VersionInconnue, MigrationImpossible,
                    RollbackImpossible):
            self.assertTrue(issubclass(exc, Exception))

    def test_registre_contenu(self):
        """VERSIONS_CONNUES.json : 2 versions, url/sha256/taille."""
        with open(module_migration.REGISTRE_VERSIONS, "r",
                  encoding="utf-8") as f:
            registre = json.load(f)
        self.assertEqual(set(registre.keys()), {"4.34.0", "4.34.1"})
        for version, entree in registre.items():
            self.assertTrue(
                entree["url"].startswith(
                    "https://github.com/leanprover/lean4/releases/download/"),
                version)
            self.assertEqual(len(entree["sha256"]), 64)
            self.assertGreater(entree["taille_octets_approx"], 100_000_000)
        self.assertEqual(
            registre["4.34.0"]["sha256"],
            "caaa98356098c85dc0fcbbd28e1ec66f39eb6551829972b752ff20e1286b646b")
        self.assertEqual(
            registre["4.34.1"]["sha256"],
            "47bf4bbd78f70c2e9670598ab7124d92b6efb7330ff33e5fbb4030f6fd72e4e4")

    # ── versions_installees ───────────────────────────

    def test_versions_installees_valides(self):
        self._simuler("4.34.0", _sha("4.34.0"), natif=True)
        self._simuler("4.34.1", _sha("4.34.1"),
                      mis_a_jour_depuis="4.34.0")
        inst = self.gv.versions_installees()
        self.assertEqual([v["version"] for v in inst],
                         ["4.34.0", "4.34.1"])
        v0, v1 = inst
        self.assertEqual(v0["sha256"], _sha("4.34.0"))
        self.assertTrue(v0["natif"])
        self.assertIsNone(v0["mis_a_jour_depuis"])
        self.assertFalse(v1["natif"])
        self.assertEqual(v1["mis_a_jour_depuis"], "4.34.0")
        self.assertTrue(v0["rep"].endswith("4.34.0-mini"))

    def test_versions_installees_ignore_invalides(self):
        """Sans marqueur, marqueur corrompu, version incohérente,
        ou bin/lean absent → ignoré."""
        # Marqueur absent.
        os.makedirs(os.path.join(self.cache, "9.9.9-mini", "bin"))
        # Marqueur corrompu (JSON invalide).
        rep2 = os.path.join(self.cache, "9.9.8-mini", "bin")
        os.makedirs(rep2)
        open(os.path.join(rep2, "lean"), "w").close()
        with open(os.path.join(self.cache, "9.9.8-mini", ".valide"), "w") as f:
            f.write("{corrompu")
        # Version du marqueur != dossier.
        self._simuler("9.9.7", "00" * 32)
        with open(os.path.join(self.cache, "9.9.7-mini", ".valide"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": "autre", "sha256": "00" * 32}, f)
        # bin/lean absent.
        rep4 = os.path.join(self.cache, "9.9.6-mini")
        os.makedirs(rep4)
        with open(os.path.join(rep4, ".valide"), "w",
                  encoding="utf-8") as f:
            json.dump({"version": "9.9.6", "sha256": "00" * 32}, f)
        self.assertEqual(self.gv.versions_installees(), [])

    # ── version_active ────────────────────────────────

    def test_version_active_avec_pointeur(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.0")
        self.assertEqual(self.gv.version_active(), "4.34.0")

    def test_version_active_pointeur_vers_absente(self):
        """.active pointant vers une version non installée → repli."""
        self._simuler("4.34.1", _sha("4.34.1"))
        with open(os.path.join(self.cache, ".active"), "w",
                  encoding="utf-8") as f:
            f.write("9.9.9\n")
        # Repli : version du manifeste embarqué si installée.
        with open(module_manager.MANIFESTE_DEFAUT, "r",
                  encoding="utf-8") as f:
            version_manifeste = json.load(f)["version"]
        if version_manifeste == "4.34.1":
            self.assertEqual(self.gv.version_active(), "4.34.1")
        else:
            self.assertIsNone(self.gv.version_active())

    def test_version_active_repli_manifeste(self):
        """Sans .active : version du manifeste embarqué si installée."""
        with open(module_manager.MANIFESTE_DEFAUT, "r",
                  encoding="utf-8") as f:
            manifeste = json.load(f)
        version_manifeste = manifeste["version"]
        self._simuler(version_manifeste, manifeste["sha256"])
        self.assertEqual(self.gv.version_active(), version_manifeste)

    def test_version_active_aucune(self):
        self.assertIsNone(self.gv.version_active())

    # ── definir_active ────────────────────────────────

    def test_definir_active_ok(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        # « de » attendu : le repli manifeste si la version du manifeste
        # embarqué est installée, sinon None.
        de_attendu = self.gv.version_active()
        r1 = self.gv.definir_active("4.34.0", raison="premier choix")
        self.assertEqual(r1, {"de": de_attendu, "vers": "4.34.0"})
        r2 = self.gv.definir_active("4.34.1", raison="mise à niveau")
        self.assertEqual(r2, {"de": "4.34.0", "vers": "4.34.1"})
        with open(os.path.join(self.cache, ".active"), "r",
                  encoding="utf-8") as f:
            self.assertEqual(f.read(), "4.34.1\n")
        # Journal : dernière bascule en premier.
        entrees = self.gv.journal(limite=5)
        self.assertEqual(entrees[0]["evenement"], "bascule")
        self.assertEqual(entrees[0]["de"], "4.34.0")
        self.assertEqual(entrees[0]["vers"], "4.34.1")
        self.assertEqual(entrees[0]["raison"], "mise à niveau")

    def test_definir_active_normalise(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        resultat = self.gv.definir_active("v4.34.0")
        self.assertEqual(resultat, {"de": None, "vers": "4.34.0"})
        self.assertEqual(self.gv.version_active(), "4.34.0")

    def test_definir_active_non_installee(self):
        with self.assertRaises(MigrationImpossible):
            self.gv.definir_active("9.9.9")
        self.assertIsNone(self.gv.version_active())

    # ── manifeste_pour_version ────────────────────────

    def test_manifeste_pour_version_ok(self):
        manifeste = manifeste_pour_version("v4.34.1")
        self.assertEqual(manifeste["version"], "4.34.1")
        self.assertEqual(manifeste["sha256"], _sha("4.34.1"))
        self.assertIn("lean-4.34.1-linux.tar.zst", manifeste["url"])
        self.assertGreater(manifeste["taille_octets_approx"], 0)
        # Le gabarit fournit les champs structurels.
        self.assertIn("fichiers_minimaux", manifeste)
        self.assertIn("extensions", manifeste)

    def test_manifeste_pour_version_inconnue(self):
        with self.assertRaises(VersionInconnue):
            manifeste_pour_version("9.9.9")

    # ── migrer ────────────────────────────────────────

    def test_migrer_deja_active(self):
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        resultat = self.gv.migrer("v4.34.1")
        self.assertEqual(resultat, {"statut": "DEJA_ACTIVE"})

    def test_migrer_sans_telechargement(self):
        """Cible déjà installée : pas d'installer(), bascule directe."""
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.0")
        appels_confirmer = []
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=AssertionError("ne doit pas être appelé")):
            resultat = self.gv.migrer(
                "4.34.1", raison="test",
                confirmer=lambda v, t: appels_confirmer.append((v, t)) or True)
        self.assertEqual(resultat["statut"], "MIGREE")
        self.assertEqual(resultat["de"], "4.34.0")
        self.assertEqual(resultat["vers"], "4.34.1")
        self.assertFalse(resultat["telechargee"])
        self.assertEqual(appels_confirmer, [])
        self.assertEqual(self.gv.version_active(), "4.34.1")
        entrees = self.gv.journal(limite=5)
        self.assertEqual(entrees[0]["evenement"], "migration")
        self.assertFalse(entrees[0]["telechargee"])
        self.assertEqual(entrees[1]["evenement"], "bascule")

    def test_migrer_avec_installation_mockee(self):
        """Cible absente : installer() mocké, puis bascule."""
        self._simuler("4.34.0", _sha("4.34.0"))
        self.gv.definir_active("4.34.0")
        usine = _UsineFauxGestionnaire(self)
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=usine):
            resultat = self.gv.migrer("4.34.1", raison="nouvelle version")
        self.assertEqual(usine.installations, ["4.34.1"])
        self.assertEqual(resultat["statut"], "MIGREE")
        self.assertTrue(resultat["telechargee"])
        self.assertEqual(resultat["de"], "4.34.0")
        self.assertEqual(self.gv.version_active(), "4.34.1")
        # L'ancienne version est intacte (installer(), pas mettre_a_jour).
        self.assertTrue(os.path.isfile(
            os.path.join(self.cache, "4.34.0-mini", "bin", "lean")))

    def test_migrer_confirmer_refuse(self):
        """confirmer=False → TelechargementRefuse, journal « refus ». """
        usine = _UsineFauxGestionnaire(self)
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=usine):
            with self.assertRaises(TelechargementRefuse):
                self.gv.migrer("4.34.1", confirmer=lambda v, t: False)
        self.assertEqual(usine.installations, [])
        self.assertIsNone(self.gv.version_active())
        entrees = self.gv.journal(limite=5)
        self.assertEqual(entrees[0]["evenement"], "refus")
        self.assertEqual(entrees[0]["vers"], "4.34.1")

    def test_migrer_confirmer_accepte(self):
        """confirmer=True → l'installation procède."""
        usine = _UsineFauxGestionnaire(self)
        vus = []
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=usine):
            resultat = self.gv.migrer(
                "4.34.1", confirmer=lambda v, t: vus.append((v, t)) or True)
        self.assertEqual(resultat["statut"], "MIGREE")
        self.assertTrue(resultat["telechargee"])
        self.assertEqual(len(vus), 1)
        self.assertEqual(vus[0][0], "4.34.1")
        self.assertGreater(vus[0][1], 100_000_000)

    def test_migrer_sans_confirmer(self):
        """Sans callable confirmer : pas de demande, installation directe."""
        usine = _UsineFauxGestionnaire(self)
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=usine):
            resultat = self.gv.migrer("4.34.1")
        self.assertEqual(resultat["statut"], "MIGREE")
        self.assertTrue(resultat["telechargee"])

    def test_migrer_version_inconnue(self):
        with self.assertRaises(VersionInconnue):
            self.gv.migrer("9.9.9")

    # ── manager_actif ─────────────────────────────────

    def test_manager_actif_defaut(self):
        """Sans version active : manager avec le manifeste embarqué."""
        gestionnaire = self.gv.manager_actif()
        self.assertIsInstance(gestionnaire, module_manager.ToolchainManager)
        self.assertEqual(gestionnaire.cache_dir, self.cache)
        with open(module_manager.MANIFESTE_DEFAUT, "r",
                  encoding="utf-8") as f:
            self.assertEqual(gestionnaire.version,
                             json.load(f)["version"])

    def test_manager_actif_version_registre(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self.gv.definir_active("4.34.0")
        gestionnaire = self.gv.manager_actif()
        self.assertEqual(gestionnaire.version, "4.34.0")
        self.assertEqual(gestionnaire.manifeste["sha256"], _sha("4.34.0"))

    def test_manager_actif_version_hors_registre(self):
        """Version installée mais inconnue au registre : manifeste
        reconstruit depuis .valide + gabarit."""
        self._simuler("4.33.0", "ab" * 32)
        self.gv.definir_active("4.33.0")
        gestionnaire = self.gv.manager_actif()
        self.assertEqual(gestionnaire.version, "4.33.0")
        self.assertEqual(gestionnaire.manifeste["sha256"], "ab" * 32)
        self.assertIn("fichiers_minimaux", gestionnaire.manifeste)

    # ── revenir ───────────────────────────────────────

    def test_revenir_ok(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.0", raison="initiale")
        usine = _UsineFauxGestionnaire(self)
        with patch("phi_complexity.toolchain.migration.ToolchainManager",
                   side_effect=usine):
            self.gv.migrer("4.34.1", raison="essai")
        self.assertEqual(self.gv.version_active(), "4.34.1")
        resultat = self.gv.revenir(raison="retour en arrière")
        self.assertEqual(resultat["vers"], "4.34.0")
        self.assertEqual(resultat["de"], "4.34.1")
        self.assertEqual(self.gv.version_active(), "4.34.0")
        entrees = self.gv.journal(limite=10)
        self.assertEqual(entrees[0]["evenement"], "bascule")
        self.assertEqual(entrees[0]["raison"], "retour en arrière")

    def test_revenir_sans_journal(self):
        with self.assertRaises(RollbackImpossible):
            self.gv.revenir()

    def test_revenir_cible_desinstallee(self):
        """La version « de » du journal n'est plus installée →
        RollbackImpossible (pas de bascule vers du vide)."""
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv._journaliser({"evenement": "migration", "de": "4.34.0",
                              "vers": "4.34.1", "telechargee": False})
        self.gv.definir_active("4.34.1")
        with self.assertRaises(RollbackImpossible):
            self.gv.revenir()

    # ── nettoyer ──────────────────────────────────────

    def test_nettoyer_rien_a_faire(self):
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        resultat = self.gv.nettoyer(confirmer=lambda v: True)
        self.assertEqual(resultat,
                         {"statut": "RIEN_A_NETTOYER", "supprimees": []})

    def test_nettoyer_sans_confirmer_leve(self):
        """confirmer=None avec suppressions nécessaires →
        MigrationImpossible (le CLI doit demander explicitement)."""
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        with self.assertRaises(MigrationImpossible):
            self.gv.nettoyer()
        self.assertTrue(os.path.isdir(
            os.path.join(self.cache, "4.34.0-mini")))

    def test_nettoyer_annule(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        resultat = self.gv.nettoyer(confirmer=lambda v: False)
        self.assertEqual(resultat,
                         {"statut": "NETTOYAGE_ANNULE", "supprimees": []})
        self.assertTrue(os.path.isdir(
            os.path.join(self.cache, "4.34.0-mini")))

    def test_nettoyer_confirme(self):
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        resultat = self.gv.nettoyer(confirmer=lambda v: True)
        self.assertEqual(resultat,
                         {"statut": "NETTOYE", "supprimees": ["4.34.0"]})
        self.assertFalse(os.path.exists(
            os.path.join(self.cache, "4.34.0-mini")))
        self.assertTrue(os.path.isdir(
            os.path.join(self.cache, "4.34.1-mini")))
        self.assertEqual(self.gv.version_active(), "4.34.1")
        entrees = self.gv.journal(limite=5)
        self.assertEqual(entrees[0]["evenement"], "nettoyage")
        self.assertEqual(entrees[0]["supprimees"], ["4.34.0"])

    def test_nettoyer_garder(self):
        """`garder` protège une version non active."""
        self._simuler("4.34.0", _sha("4.34.0"))
        self._simuler("4.34.1", _sha("4.34.1"))
        self.gv.definir_active("4.34.1")
        resultat = self.gv.nettoyer(garder=["4.34.0"],
                                    confirmer=lambda v: True)
        self.assertEqual(resultat["statut"], "RIEN_A_NETTOYER")
        self.assertTrue(os.path.isdir(
            os.path.join(self.cache, "4.34.0-mini")))

    # ── projets_utilisant ─────────────────────────────

    def _arborescence_projets(self):
        racine = os.path.join(self.tmp.name, "projets")
        def _fichier(dossier, contenu):
            os.makedirs(dossier, exist_ok=True)
            with open(os.path.join(dossier, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write(contenu)
        _fichier(os.path.join(racine, "projA"),
                 "leanprover/lean4:v4.34.0\n")
        _fichier(os.path.join(racine, "projB"), "v4.34.1\n")
        _fichier(os.path.join(racine, ".git", "cache"), "v4.34.0\n")
        _fichier(os.path.join(racine, "projC", ".lake"), "v4.34.0\n")
        _fichier(os.path.join(racine, "projC", "build"), "v4.34.0\n")
        _fichier(os.path.join(racine, "projD"), "ceci n'est pas une version\n")
        return racine

    def test_projets_utilisant(self):
        racine = self._arborescence_projets()
        trouves = self.gv.projets_utilisant("v4.34.0", racine)
        self.assertEqual(len(trouves), 1)
        seul = trouves[0]
        self.assertTrue(seul["projet"].endswith("projA"))
        self.assertTrue(seul["fichier"].endswith(
            os.path.join("projA", "lean-toolchain")))
        self.assertEqual(seul["version_demandee"], "4.34.0")

    def test_projets_utilisant_aucun(self):
        racine = self._arborescence_projets()
        self.assertEqual(self.gv.projets_utilisant("4.33.0", racine), [])

    # ── journal ───────────────────────────────────────

    def test_journal_ordre_inverse(self):
        self.gv._journaliser({"evenement": "a"})
        self.gv._journaliser({"evenement": "b"})
        self.gv._journaliser({"evenement": "c"})
        entrees = self.gv.journal(limite=10)
        self.assertEqual([e["evenement"] for e in entrees], ["c", "b", "a"])
        for entree in entrees:
            self.assertIn("date", entree)

    def test_journal_limite(self):
        for i in range(5):
            self.gv._journaliser({"evenement": "e%d" % i})
        entrees = self.gv.journal(limite=2)
        self.assertEqual([e["evenement"] for e in entrees], ["e4", "e3"])

    def test_journal_ignore_lignes_invalides(self):
        with open(os.path.join(self.cache, ".journal-migrations.jsonl"),
                  "w", encoding="utf-8") as f:
            f.write('{"evenement": "ok"}\n')
            f.write("\n")
            f.write("{json invalide\n")
            f.write('{"evenement": "ok2"}\n')
        entrees = self.gv.journal(limite=10)
        self.assertEqual([e["evenement"] for e in entrees], ["ok2", "ok"])

    def test_journal_absent(self):
        self.assertEqual(self.gv.journal(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
