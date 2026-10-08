#!/usr/bin/env python3
"""Tests unitaires du module ``phi_complexity.toolchain``.

Périmètre : téléchargement vérifié (download), extraction sélective
(extract), validation (validate), orchestration (manager) et manifeste.

RÈGLE DURE : aucun accès réseau réel. Le réseau est systématiquement
mocké : seam ``_ouvreur`` pour ``telecharger``, faux téléchargeur /
extracteur / validateur patchés dans le namespace du manager, et
``subprocess.run`` patché pour compiler/executer.

Exécution (stdlib uniquement) :
    cd ~/workspace/phi-pub && python3 tests/test_toolchain.py
Compatible pytest quand il est disponible :
    python3 -m pytest tests/test_toolchain.py -q
"""

import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# Permet l'exécution directe du fichier depuis n'importe quel cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import zstandard

from phi_complexity.toolchain import (
    MANIFESTE_DEFAUT,
    ErreurExtraction,
    ErreurTelechargement,
    ErreurValidation,
    ErreurVerification,
    ToolchainAbsente,
    ToolchainManager,
)
from phi_complexity.toolchain.download import telecharger
from phi_complexity.toolchain.extract import MOTIFS_MINIMAUX, extraire_minimal
from phi_complexity.toolchain.validate import valider


# ── utilitaires ──────────────────────────────────────────

def _octets(n):
    """n octets déterministes (motif répété, génération rapide)."""
    motif = b"phi-toolchain-test-0123456789:"
    return (motif * (n // len(motif) + 1))[:n]


def _sha256(donnees):
    return hashlib.sha256(donnees).hexdigest()


class TestTelecharger(unittest.TestCase):
    """download.telecharger — tout le réseau passe par le seam _ouvreur."""

    def test_succes_fichier_cree_et_dict_retourne(self):
        """Contenu connu + bon sha256 → fichier créé, dict correct, pas de .part."""
        contenu = _octets(64 * 1024)
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "lean.tar.zst")
            resultat = telecharger(
                "https://exemple.invalid/lean.tar.zst",
                dest,
                _sha256(contenu),
                _ouvreur=lambda url: io.BytesIO(contenu),
            )
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), contenu)
            self.assertEqual(resultat["chemin"], dest)
            self.assertEqual(resultat["octets"], len(contenu))
            self.assertEqual(resultat["sha256"], _sha256(contenu))
            self.assertFalse(
                os.path.exists(dest + ".part"),
                "le fichier .part ne doit pas survivre au succès",
            )

    def test_hash_incoherent_fichier_rejete_et_supprime(self):
        """Mauvais sha256 → ErreurVerification, dest absent, .part supprimé."""
        contenu = _octets(4096)
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "lean.tar.zst")
            with self.assertRaises(ErreurVerification):
                telecharger(
                    "https://exemple.invalid/lean.tar.zst",
                    dest,
                    "00" * 32,  # hash volontairement faux
                    _ouvreur=lambda url: io.BytesIO(contenu),
                )
            self.assertFalse(os.path.exists(dest),
                             "la destination ne doit pas exister après rejet")
            self.assertFalse(os.path.exists(dest + ".part"),
                             "le .part doit être supprimé après rejet")

    def test_ouvreur_en_panne_erreur_telechargement(self):
        """_ouvreur qui lève → ErreurTelechargement (pas d'autre fuite)."""

        def ouvreur_ko(url):
            raise ConnectionError("réseau coupé (simulation)")

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "lean.tar.zst")
            with self.assertRaises(ErreurTelechargement):
                telecharger(
                    "https://exemple.invalid/lean.tar.zst",
                    dest,
                    "ab" * 32,
                    _ouvreur=ouvreur_ko,
                )
            self.assertFalse(os.path.exists(dest + ".part"))

    def test_progression_octets_croissants(self):
        """progression appelée avec des cumuls strictement croissants."""
        # 2,5 Mio > TAILLE_BLOC (1 Mio) → plusieurs appels garantis.
        contenu = _octets(2_500_000)
        appels = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "gros.bin")
            telecharger(
                "https://exemple.invalid/gros.bin",
                dest,
                _sha256(contenu),
                progression=appels.append,
                _ouvreur=lambda url: io.BytesIO(contenu),
            )
        self.assertGreater(len(appels), 1,
                           "plusieurs blocs attendus pour 2,5 Mio")
        for precedent, suivant in zip(appels, appels[1:]):
            self.assertLess(precedent, suivant,
                            "les octets rapportés doivent croître")
        self.assertEqual(appels[-1], len(contenu),
                         "le dernier appel doit valoir le total")


# ── extract ──────────────────────────────────────────────

def _fabriquer_archive_tar_zst(chemin):
    """Construit une vraie archive .tar.zst de test.

    Racine ``lean-4.34.0-linux/`` avec le minimal attendu plus des
    fichiers pièges (hors motifs, hors racine, traversal).
    """
    membres = [
        # --- le minimal attendu ---
        ("lean-4.34.0-linux/bin/lean", b"#!/bin/sh\necho lean\n", 0o755),
        ("lean-4.34.0-linux/lib/lean/libleanshared.so", b"\x7fELF-faux-so", 0o644),
        ("lean-4.34.0-linux/lib/lean/Init/Data/Nat.olean", b"OLEAN-nat", 0o644),
        ("lean-4.34.0-linux/lib/lean/Init/Data/Nat.olean.private",
         b"OLEAN-nat-prive", 0o644),
        ("lean-4.34.0-linux/lib/lean/Init/Data/Nat.olean.server",
         b"OLEAN-nat-serveur", 0o644),
        # --- pièges : hors motifs ---
        ("lean-4.34.0-linux/bin/lake", b"#!/bin/sh\necho lake\n", 0o755),
        ("lean-4.34.0-linux/src/a.lean", b"-- piege\n", 0o644),
        ("lean-4.34.0-linux/Lean/xxx.olean", b"PIEGE", 0o644),
        # --- pièges : traversal ---
        # ne matche aucun motif (ignoré avant même la garde)
        ("lean-4.34.0-linux/../../../evil.sh", b"#!/bin/sh\necho EVIL\n", 0o755),
        # matche le motif "lib/lean/Init/**/*.olean" MAIS résout hors
        # de dest_dir → la garde commonpath doit le rejeter
        ("lean-4.34.0-linux/lib/lean/Init/../../../../evil.olean",
         b"EVIL", 0o644),
    ]
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as tar:
        for nom, contenu, mode in membres:
            info = tarfile.TarInfo(nom)
            info.size = len(contenu)
            info.mode = mode
            tar.addfile(info, io.BytesIO(contenu))
    compresseur = zstandard.ZstdCompressor()
    with open(chemin, "wb") as f:
        f.write(compresseur.compress(tampon.getvalue()))


class TestExtraireMinimal(unittest.TestCase):
    """extract.extraire_minimal — archive réelle construite en mémoire."""

    def test_seul_le_minimal_est_extrait(self):
        """Les 5 fichiers du minimal sortent, les pièges restent dehors."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_tar_zst(archive)
            dest = os.path.join(tmp, "mini")
            resultat = extraire_minimal(archive, dest)

            attendus = [
                "bin/lean",
                "lib/lean/libleanshared.so",
                "lib/lean/Init/Data/Nat.olean",
                "lib/lean/Init/Data/Nat.olean.private",
                "lib/lean/Init/Data/Nat.olean.server",
            ]
            for relatif in attendus:
                self.assertTrue(
                    os.path.isfile(os.path.join(dest, relatif)),
                    "manquant : %s" % relatif,
                )
            # pièges hors motifs
            for piege in ("bin/lake", "src/a.lean", "Lean/xxx.olean"):
                self.assertFalse(
                    os.path.exists(os.path.join(dest, piege)),
                    "piège extrait à tort : %s" % piege,
                )
            # pièges traversal : rien ne doit sortir de dest_dir
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.sh")))
            self.assertFalse(os.path.exists(os.path.join(dest, "evil.sh")))
            for racine, _, fichiers in os.walk(dest):
                self.assertNotIn("evil.olean", fichiers)
            self.assertEqual(resultat["dest"], dest)
            self.assertEqual(resultat["fichiers"], 5)
            self.assertEqual(resultat["octets"],
                             sum(os.path.getsize(os.path.join(dest, r))
                                 for r in attendus))

    def test_bit_executable_preserve(self):
        """bin/lean (0o755 dans l'archive) reste exécutable après extraction."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_tar_zst(archive)
            dest = os.path.join(tmp, "mini")
            extraire_minimal(archive, dest)
            binaire = os.path.join(dest, "bin", "lean")
            self.assertTrue(os.access(binaire, os.X_OK),
                            "le bit exécutable de bin/lean est perdu")
            with open(binaire, "rb") as f:
                self.assertEqual(f.read(), b"#!/bin/sh\necho lean\n")

    def test_motifs_personnalises_respectes(self):
        """Des motifs explicites remplacent MOTIFS_MINIMAUX."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_tar_zst(archive)
            dest = os.path.join(tmp, "mini")
            resultat = extraire_minimal(archive, dest, motifs=["bin/lake"])
            self.assertTrue(os.path.isfile(os.path.join(dest, "bin", "lake")))
            self.assertFalse(os.path.exists(os.path.join(dest, "bin", "lean")),
                             "bin/lean ne doit pas sortir avec motifs=['bin/lake']")
            self.assertEqual(resultat["fichiers"], 1)

    def test_archive_sans_fichier_retenu_erreur_extraction(self):
        """Aucun fichier retenu → ErreurExtraction."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_tar_zst(archive)
            with self.assertRaises(ErreurExtraction):
                extraire_minimal(archive, os.path.join(tmp, "mini"),
                                 motifs=["rien/du/tout/*"])

    def test_progression_appelee(self):
        """progression(n_fichiers, n_octets) appelée à chaque fichier."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_tar_zst(archive)
            appels = []
            extraire_minimal(
                archive, os.path.join(tmp, "mini"),
                progression=lambda nf, no: appels.append((nf, no)),
            )
            self.assertEqual(len(appels), 5)
            self.assertEqual([nf for nf, _ in appels], [1, 2, 3, 4, 5])
            for precedent, suivant in zip(appels, appels[1:]):
                self.assertLessEqual(precedent[1], suivant[1])

    def test_motifs_minimaux_documentes(self):
        """Garde : MOTIFS_MINIMAUX couvre bin/lean, les .so et Init/*.olean."""
        self.assertIn("bin/lean", MOTIFS_MINIMAUX)
        self.assertTrue(any("libleanshared" in m for m in MOTIFS_MINIMAUX))
        self.assertTrue(any("Init" in m and m.endswith(".olean")
                            for m in MOTIFS_MINIMAUX))


# ── validate ─────────────────────────────────────────────

SCRIPT_FAUX_LEAN = '#!/bin/sh\necho "Lean (version 4.34.0, x86_64)"\n'


def _faux_lean(rep, executable=True):
    """Installe un faux bin/lean (script shell) dans `rep`."""
    dossier_bin = os.path.join(rep, "bin")
    os.makedirs(dossier_bin, exist_ok=True)
    chemin = os.path.join(dossier_bin, "lean")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(SCRIPT_FAUX_LEAN)
    os.chmod(chemin, 0o755 if executable else 0o644)
    return chemin


class TestValider(unittest.TestCase):
    """validate.valider — faux binaire shell, aucun vrai Lean requis."""

    def test_valide_ok(self):
        """Script qui affiche la bonne version → dict retourné."""
        with tempfile.TemporaryDirectory() as tmp:
            chemin = _faux_lean(tmp)
            resultat = valider(tmp, "4.34.0")
            self.assertEqual(resultat["chemin_lean"], chemin)
            self.assertEqual(resultat["version"], "4.34.0")
            self.assertIn("4.34.0", resultat["sortie"])

    def test_version_inattendue(self):
        """Sortie sans la version attendue → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_lean(tmp)
            with self.assertRaises(ErreurValidation):
                valider(tmp, "9.99.9")

    def test_binaire_absent(self):
        """Pas de bin/lean → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ErreurValidation):
                valider(tmp, "4.34.0")

    def test_binaire_non_executable(self):
        """bin/lean sans bit +x → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_lean(tmp, executable=False)
            with self.assertRaises(ErreurValidation):
                valider(tmp, "4.34.0")


# ── manager ──────────────────────────────────────────────

MANIFESTE_TEST = {
    "version": "4.34.0",
    "url": ("https://github.com/leanprover/lean4/releases/download/"
            "v4.34.0/lean-4.34.0-linux.tar.zst"),
    "sha256": "ab" * 32,
    "fichiers_minimaux": ["bin/lean"],
}


def _ecrire_manifeste_test(dossier):
    chemin = os.path.join(dossier, "manifeste.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(MANIFESTE_TEST, f)
    return chemin


class _FauxPhases:
    """Faux téléchargeur / extracteur / validateur (aucun réseau, aucun Lean)."""

    def __init__(self):
        self.urls_telechargees = []

    def telecharger(self, url, dest, sha256_attendu, progression=None,
                    _ouvreur=None):
        self.urls_telechargees.append(url)
        with open(dest, "wb") as f:
            f.write(b"archive-factice")
        return {"chemin": dest, "octets": 15, "sha256": sha256_attendu}

    def extraire_minimal(self, archive, dest_dir, motifs=None, progression=None):
        binaire = os.path.join(dest_dir, "bin", "lean")
        os.makedirs(os.path.dirname(binaire), exist_ok=True)
        with open(binaire, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho Lean\n")
        os.chmod(binaire, 0o755)
        return {"dest": dest_dir, "fichiers": 1, "octets": 20}

    def valider(self, repertoire, version_attendue, timeout_s=60):
        return {
            "chemin_lean": os.path.join(repertoire, "bin", "lean"),
            "version": version_attendue,
            "sortie": "Lean (version %s)" % version_attendue,
        }


def _patch_phases(faux):
    """Patch les 3 phases réseau/disque du namespace manager."""
    return (
        patch("phi_complexity.toolchain.manager.telecharger",
              side_effect=faux.telecharger),
        patch("phi_complexity.toolchain.manager.extraire_minimal",
              side_effect=faux.extraire_minimal),
        patch("phi_complexity.toolchain.manager.valider",
              side_effect=faux.valider),
    )


class TestToolchainManager(unittest.TestCase):
    """manager.ToolchainManager — phases mockées, aucun réseau."""

    def _gestionnaire(self, tmp):
        manifeste = _ecrire_manifeste_test(tmp)
        return ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                manifeste=manifeste)

    def test_cache_vide_non_installee_et_chemin_lean_leve(self):
        """Sur cache vide : est_installee() False, chemin_lean() lève."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            self.assertFalse(m.est_installee())
            with self.assertRaises(ToolchainAbsente):
                m.chemin_lean()

    def test_installer_idempotent(self):
        """2e installer() → DEJA_INSTALLEE sans re-télécharger."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhases()
            p1, p2, p3 = _patch_phases(faux)
            with p1, p2, p3:
                premier = m.installer()
                self.assertEqual(premier["statut"], "INSTALLEE")
                self.assertTrue(m.est_installee())
                second = m.installer()
                self.assertEqual(second["statut"], "DEJA_INSTALLEE")
            self.assertEqual(len(faux.urls_telechargees), 1,
                             "le 2e appel ne doit pas re-télécharger")

    def test_phases_signalees(self):
        """installer() signale telechargement/extraction/validation/terminee."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhases()
            phases = []
            p1, p2, p3 = _patch_phases(faux)
            with p1, p2, p3:
                m.installer(progression=lambda phase, info: phases.append(phase))
                phases_second = []
                m.installer(progression=lambda phase, info:
                            phases_second.append(phase))
            ordre = []
            for phase in phases:
                if not ordre or ordre[-1] != phase:
                    ordre.append(phase)
            self.assertEqual(ordre, ["telechargement", "extraction",
                                     "validation", "terminee"])
            self.assertEqual(phases_second, ["deja_installee"])

    def test_marqueur_corrompu_non_installee(self):
        """Marqueur .valide avec mauvais sha256 (ou JSON invalide) → False."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhases()
            p1, p2, p3 = _patch_phases(faux)
            with p1, p2, p3:
                m.installer()
            self.assertTrue(m.est_installee())
            # corruption : mauvais sha256
            with open(m.chemin_marqueur, "w", encoding="utf-8") as f:
                json.dump({"version": "4.34.0", "sha256": "00" * 32}, f)
            self.assertFalse(m.est_installee())
            with self.assertRaises(ToolchainAbsente):
                m.chemin_lean()
            # corruption : JSON invalide
            with open(m.chemin_marqueur, "w", encoding="utf-8") as f:
                f.write("{ invalide")
            self.assertFalse(m.est_installee())

    def test_variable_cache_respectee(self):
        """PHI_TOOLCHAIN_CACHE prime sur le défaut quand cache_dir est None."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_env = os.path.join(tmp, "cache-env")
            manifeste = _ecrire_manifeste_test(tmp)
            with patch.dict(os.environ, {"PHI_TOOLCHAIN_CACHE": cache_env}):
                m = ToolchainManager(manifeste=manifeste)
            self.assertEqual(m.cache_dir, cache_env)

    def test_variable_miroir_remplace_prefixe_github(self):
        """PHI_LEAN_RELEASE_BASE remplace le préfixe GitHub de l'URL."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhases()
            p1, p2, p3 = _patch_phases(faux)
            miroir = "https://miroir.exemple.invalid/lean/"
            with p1, p2, p3, patch.dict(
                    os.environ, {"PHI_LEAN_RELEASE_BASE": miroir}):
                m.installer()
            self.assertEqual(len(faux.urls_telechargees), 1)
            url = faux.urls_telechargees[0]
            self.assertEqual(
                url,
                "https://miroir.exemple.invalid/lean/lean-4.34.0-linux.tar.zst",
                "le préfixe GitHub doit être remplacé par le miroir",
            )

    def _gestionnaire_installe(self, tmp):
        """Gestionnaire avec installation simulée (phases mockées)."""
        m = self._gestionnaire(tmp)
        faux = _FauxPhases()
        p1, p2, p3 = _patch_phases(faux)
        with p1, p2, p3:
            m.installer()
        return m

    def test_compiler_passe_lean_path_et_retourne_dict(self):
        """compiler() : LEAN_PATH dans env, dict {rc, stdout, stderr, ok}."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire_installe(tmp)
            faux_proc = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       return_value=faux_proc) as faux_run:
                resultat = m.compiler("Test.lean", args_extra=["--quiet"])
            cmd = faux_run.call_args[0][0]
            self.assertEqual(cmd[0], m.chemin_lean())
            self.assertIn("--quiet", cmd)
            self.assertEqual(cmd[-1], "Test.lean")
            env = faux_run.call_args[1]["env"]
            self.assertEqual(
                env["LEAN_PATH"],
                os.path.join(m.rep_install, "lib", "lean"),
            )
            self.assertEqual(resultat,
                             {"rc": 0, "stdout": "ok\n", "stderr": "",
                              "ok": True})

    def test_executer_place_run_en_tete(self):
        """executer() : '--run' en tête des arguments, devant args_extra."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire_installe(tmp)
            faux_proc = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       return_value=faux_proc) as faux_run:
                resultat = m.executer("Main.lean", args_extra=["--quiet"])
            cmd = faux_run.call_args[0][0]
            self.assertEqual(cmd[0], m.chemin_lean())
            self.assertEqual(cmd[1], "--run",
                             "'--run' doit être en tête des arguments")
            self.assertEqual(cmd[2:], ["--quiet", "Main.lean"])
            self.assertTrue(resultat["ok"])

    def test_compiler_echec_remonte_ok_false(self):
        """rc != 0 → ok False, stdout/stderr conservés."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire_installe(tmp)
            faux_proc = SimpleNamespace(returncode=1, stdout="",
                                         stderr="erreur\n")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       return_value=faux_proc):
                resultat = m.compiler("Mauvais.lean")
            self.assertEqual(resultat["rc"], 1)
            self.assertFalse(resultat["ok"])
            self.assertEqual(resultat["stderr"], "erreur\n")


# ── manifeste ────────────────────────────────────────────

class TestManifeste(unittest.TestCase):
    """TOOLCHAIN_MANIFEST.json — structure et format du sha256."""

    def test_manifeste_valide(self):
        """JSON lisible, clés requises présentes, sha256 = 64 hex."""
        with open(MANIFESTE_DEFAUT, "r", encoding="utf-8") as f:
            manifeste = json.load(f)
        for cle in ("version", "url", "sha256", "fichiers_minimaux"):
            self.assertIn(cle, manifeste, "clé manquante : %s" % cle)
        self.assertRegex(manifeste["sha256"], r"^[0-9a-fA-F]{64}$",
                         "le sha256 doit être 64 caractères hexadécimaux")
        self.assertIsInstance(manifeste["fichiers_minimaux"], list)
        self.assertGreater(len(manifeste["fichiers_minimaux"]), 0)
        self.assertTrue(manifeste["url"].startswith("https://"),
                        "l'URL du manifeste doit être HTTPS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
