#!/usr/bin/env python3
"""Tests unitaires du module ``phi_complexity.toolchain``.

Périmètre : téléchargement vérifié (download), extraction sélective
(extract), validation (validate), orchestration (manager), manifeste,
et extensions optionnelles Lean/Std (chantier 3).

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
import contextlib
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

try:
    import zstandard
except ImportError:  # pragma: no cover - CI sans la dépendance optionnelle
    zstandard = None


def _requiert_zstandard():
    """Saute le test si zstandard (dépendance optionnelle) est absent."""
    if zstandard is None:
        raise unittest.SkipTest(
            "zstandard requis pour fabriquer les archives .tar.zst de test"
        )

from phi_complexity.toolchain import (
    EXTENSIONS_MOTIFS,
    MANIFESTE_DEFAUT,
    MOTIFS_EXTENSION_LEAN,
    MOTIFS_EXTENSION_STD,
    MOTIFS_LAKE,
    MOTIFS_NATIFS,
    ErreurExtraction,
    ErreurTelechargement,
    ErreurValidation,
    ErreurVerification,
    FormatLeanToolchainInvalide,
    LakeAbsent,
    LakeConfigNonSupportee,
    ToolchainAbsente,
    ToolchainManager,
    VersionNonSupportee,
    lire_version_projet,
    valider_extension,
    valider_lake,
    valider_natif,
)
from phi_complexity.toolchain.download import telecharger
from phi_complexity.toolchain.extract import (
    MOTIFS_EXTENSION_LEAN as MOTIFS_LEAN_EXTRACT,
)
from phi_complexity.toolchain.extract import (
    MOTIFS_EXTENSION_STD as MOTIFS_STD_EXTRACT,
)
from phi_complexity.toolchain.extract import MOTIFS_MINIMAUX, extraire_minimal
from phi_complexity.toolchain.extract import MOTIFS_NATIFS as MOTIFS_NATIFS_EXTRACT
from phi_complexity.toolchain.extract import (
    MOTIFS_LAKE as MOTIFS_LAKE_EXTRACT,
)
from phi_complexity.toolchain.extract import creer_liens_lake
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
    _requiert_zstandard()
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


def _fabriquer_archive_natif_tar_zst(chemin):
    _requiert_zstandard()
    """Construit une vraie archive .tar.zst avec un kit natif factice.

    Racine ``lean-4.34.0-linux/`` avec les pièces du kit natif, un lien
    symbolique sain (libc++.so.1 -> libc++.so.1.0), des pièges (bin/lake,
    bin/llvm-ar volontairement exclu du kit) et des liens symboliques
    malveillants (absolu, évasion relative).
    """
    racine = "lean-4.34.0-linux/"
    membres = [
        ("bin/leanc", b"#!/bin/sh\necho leanc\n", 0o755),
        ("bin/clang", b"#!/bin/sh\necho clang\n", 0o755),
        ("bin/ld.lld", b"#!/bin/sh\necho ld.lld\n", 0o755),
        ("lib/libc++.so.1.0", b"FAUX-so-c++", 0o644),
        ("lib/glibc/libc.so", b"FAUX-libc", 0o644),
        ("lib/lean/libInit.a", b"FAUX-libInit", 0o644),
        ("lib/lean/libLean.a", b"FAUX-libLean", 0o644),
        ("include/clang/stddef.h", b"/* stddef */\n", 0o644),
        ("include/lean/lean.h", b"/* lean.h */\n", 0o644),
        # pièges : hors motifs natifs
        ("bin/lake", b"#!/bin/sh\necho lake\n", 0o755),
        ("bin/llvm-ar", b"#!/bin/sh\necho ar\n", 0o755),
    ]
    liens = [
        # sain : relatif, reste sous dest_dir
        ("lib/libc++.so.1", "libc++.so.1.0"),
        # malveillants : refusés par la garde
        ("lib/libunwind.so.1", "/etc/passwd"),
        ("lib/libc++abi.so.1", "../../evil.so"),
    ]
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w") as tar:
        for nom, contenu, mode in membres:
            info = tarfile.TarInfo(racine + nom)
            info.size = len(contenu)
            info.mode = mode
            tar.addfile(info, io.BytesIO(contenu))
        for nom, cible in liens:
            info = tarfile.TarInfo(racine + nom)
            info.type = tarfile.SYMTYPE
            info.linkname = cible
            tar.addfile(info)
    compresseur = zstandard.ZstdCompressor()
    with open(chemin, "wb") as f:
        f.write(compresseur.compress(tampon.getvalue()))


class TestExtraireNatif(unittest.TestCase):
    """extract.extraire_minimal + MOTIFS_NATIFS — kit leanc, liens sains."""

    def test_kit_natif_extrait(self):
        """Les pièces du kit sortent, les pièges (lake, llvm-ar) restent."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_natif_tar_zst(archive)
            dest = os.path.join(tmp, "natif")
            resultat = extraire_minimal(
                archive, dest, motifs=MOTIFS_NATIFS_EXTRACT)

            attendus = [
                "bin/leanc",
                "bin/clang",
                "bin/ld.lld",
                "lib/libc++.so.1.0",
                "lib/glibc/libc.so",
                "lib/lean/libInit.a",
                "lib/lean/libLean.a",
                "include/clang/stddef.h",
                "include/lean/lean.h",
            ]
            for relatif in attendus:
                self.assertTrue(
                    os.path.isfile(os.path.join(dest, relatif)),
                    "manquant : %s" % relatif,
                )
            for piege in ("bin/lake", "bin/llvm-ar"):
                self.assertFalse(
                    os.path.exists(os.path.join(dest, piege)),
                    "piège extrait à tort : %s" % piege,
                )
            self.assertEqual(resultat["dest"], dest)

    def test_lien_symbolique_sain_recree(self):
        """libc++.so.1 -> libc++.so.1.0 recréé comme lien, résolvable."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_natif_tar_zst(archive)
            dest = os.path.join(tmp, "natif")
            extraire_minimal(archive, dest, motifs=MOTIFS_NATIFS_EXTRACT)
            lien = os.path.join(dest, "lib", "libc++.so.1")
            self.assertTrue(os.path.islink(lien),
                            "libc++.so.1 doit être un lien symbolique")
            self.assertEqual(os.readlink(lien), "libc++.so.1.0")
            with open(lien, "rb") as f:
                self.assertEqual(f.read(), b"FAUX-so-c++")

    def test_liens_malveillants_rejetes(self):
        """Lien absolu et évasion relative : jamais créés."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_natif_tar_zst(archive)
            dest = os.path.join(tmp, "natif")
            extraire_minimal(archive, dest, motifs=MOTIFS_NATIFS_EXTRACT)
            for nom in ("lib/libunwind.so.1", "lib/libc++abi.so.1"):
                self.assertFalse(
                    os.path.lexists(os.path.join(dest, nom)),
                    "lien malveillant créé : %s" % nom,
                )
            self.assertFalse(os.path.exists(os.path.join(tmp, "evil.so")))

    def test_bit_executable_leanc_preserve(self):
        """bin/leanc (0o755 dans l'archive) reste exécutable."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_natif_tar_zst(archive)
            dest = os.path.join(tmp, "natif")
            extraire_minimal(archive, dest, motifs=MOTIFS_NATIFS_EXTRACT)
            binaire = os.path.join(dest, "bin", "leanc")
            self.assertTrue(os.access(binaire, os.X_OK),
                            "le bit exécutable de bin/leanc est perdu")

    def test_motifs_natifs_documentes(self):
        """Garde : pièces critiques présentes, llvm-ar exclu, motifs uniques."""
        self.assertIn("bin/leanc", MOTIFS_NATIFS_EXTRACT)
        self.assertIn("bin/clang", MOTIFS_NATIFS_EXTRACT)
        self.assertIn("bin/ld.lld", MOTIFS_NATIFS_EXTRACT)
        self.assertIn("lib/lean/libLean.a", MOTIFS_NATIFS_EXTRACT)
        self.assertIn("include/clang/*", MOTIFS_NATIFS_EXTRACT)
        self.assertNotIn("bin/llvm-ar", MOTIFS_NATIFS_EXTRACT,
                         "llvm-ar n'est jamais invoqué par leanc")
        self.assertEqual(len(MOTIFS_NATIFS_EXTRACT),
                         len(set(MOTIFS_NATIFS_EXTRACT)),
                         "motif dupliqué dans MOTIFS_NATIFS")


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


def _faux_kit_natif(dossier):
    """Crée un kit natif factice (binaires + pièces critiques)."""
    for nom in ("bin/leanc", "bin/clang", "bin/ld.lld"):
        chemin = os.path.join(dossier, nom)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho "clang version 22.1.4"\n')
        os.chmod(chemin, 0o755)
    from phi_complexity.toolchain.validate import _FICHIERS_NATIFS_CRITIQUES
    for relatif in _FICHIERS_NATIFS_CRITIQUES:
        chemin = os.path.join(dossier, relatif)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(b"FAUX")
    return os.path.join(dossier, "bin", "leanc")


class TestValiderNatif(unittest.TestCase):
    """validate.valider_natif — kit leanc factice, binaires mockés."""

    def test_succes(self):
        """Kit complet + leanc --version rc 0 → dict retourné."""
        with tempfile.TemporaryDirectory() as tmp:
            chemin = _faux_kit_natif(tmp)
            resultat = valider_natif(tmp)
            self.assertEqual(resultat["chemin_leanc"], chemin)
            self.assertIn("22.1.4", resultat["sortie"])

    def test_binaire_absent(self):
        """Pas de bin/leanc → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ErreurValidation):
                valider_natif(tmp)

    def test_piece_critique_manquante(self):
        """Sans lib/lean/libLean.a → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_kit_natif(tmp)
            os.unlink(os.path.join(tmp, "lib", "lean", "libLean.a"))
            with self.assertRaises(ErreurValidation):
                valider_natif(tmp)

    def test_leanc_version_echec(self):
        """`leanc --version` rc != 0 → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_kit_natif(tmp)
            with patch("phi_complexity.toolchain.validate.subprocess.run",
                       ) as faux_run:
                faux_run.return_value = SimpleNamespace(
                    returncode=1, stdout="", stderr="boom\n")
                with self.assertRaises(ErreurValidation):
                    valider_natif(tmp)


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


class _FauxPhasesNatif(_FauxPhases):
    """_FauxPhases + motifs capturés + bin/leanc simulé + valider_natif."""

    def __init__(self):
        super().__init__()
        self.motifs_recus = []
        self.validations_natif = []

    def extraire_minimal(self, archive, dest_dir, motifs=None,
                         progression=None):
        self.motifs_recus.append(list(motifs) if motifs else None)
        resultat = super().extraire_minimal(
            archive, dest_dir, motifs=motifs, progression=progression)
        if motifs and "bin/leanc" in motifs:
            binaire = os.path.join(dest_dir, "bin", "leanc")
            with open(binaire, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\necho leanc\n")
            os.chmod(binaire, 0o755)
        return resultat

    def valider_natif(self, repertoire, timeout_s=120):
        self.validations_natif.append(repertoire)
        return {
            "chemin_leanc": os.path.join(repertoire, "bin", "leanc"),
            "sortie": "clang version 22.1.4",
        }


def _patch_phases_natif(faux):
    """Les 3 phases + valider_natif patchées dans le namespace manager."""
    p1, p2, p3 = _patch_phases(faux)
    p4 = patch("phi_complexity.toolchain.manager.valider_natif",
               side_effect=faux.valider_natif)
    return p1, p2, p3, p4


class TestInstallerNatif(unittest.TestCase):
    """manager.installer(avec_natif=True) — phases mockées, aucun réseau."""

    def _gestionnaire(self, tmp):
        manifeste = _ecrire_manifeste_test(tmp)
        return ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                manifeste=manifeste)

    def test_avec_natif_transmet_motifs_combines(self):
        """avec_natif=True → motifs MINIMAUX+NATIFS, valider_natif appelé."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesNatif()
            p1, p2, p3, p4 = _patch_phases_natif(faux)
            with p1, p2, p3, p4:
                resultat = m.installer(avec_natif=True)
            self.assertEqual(resultat["statut"], "INSTALLEE")
            self.assertTrue(resultat["natif"])
            self.assertEqual(
                faux.motifs_recus[-1], MOTIFS_MINIMAUX + MOTIFS_NATIFS,
                "l'extraction doit recevoir MINIMAUX + NATIFS")
            self.assertEqual(faux.validations_natif, [m.rep_install])
            self.assertEqual(resultat["chemin_leanc"],
                             os.path.join(m.rep_install, "bin", "leanc"))
            self.assertTrue(m.natif_installe())
            self.assertEqual(m.chemin_leanc(),
                             os.path.join(m.rep_install, "bin", "leanc"))

    def test_sans_natif_comportement_inchange(self):
        """Par défaut : motifs=None, pas de validation natif, marqueur False."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesNatif()
            p1, p2, p3, p4 = _patch_phases_natif(faux)
            with p1, p2, p3, p4:
                resultat = m.installer()
            self.assertEqual(resultat["statut"], "INSTALLEE")
            self.assertFalse(resultat["natif"])
            self.assertEqual(faux.motifs_recus[-1], None,
                             "sans avec_natif, motifs=None (défaut)")
            self.assertEqual(faux.validations_natif, [])
            self.assertFalse(m.natif_installe())
            with self.assertRaises(ToolchainAbsente):
                m.chemin_leanc()

    def test_ajout_natif_sur_installation_existante(self):
        """installer() puis installer(avec_natif=True) : ajout sans
        re-téléchargement ; 3e appel → DEJA_INSTALLEE."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesNatif()
            p1, p2, p3, p4 = _patch_phases_natif(faux)
            with p1, p2, p3, p4:
                m.installer()
                self.assertFalse(m.natif_installe())
                ajout = m.installer(avec_natif=True)
            self.assertEqual(ajout["statut"], "INSTALLEE")
            self.assertTrue(ajout["natif"])
            self.assertEqual(len(faux.urls_telechargees), 1,
                             "l'ajout du natif ne re-télécharge pas")
            self.assertEqual(faux.motifs_recus[-1],
                             MOTIFS_MINIMAUX + MOTIFS_NATIFS)
            self.assertTrue(m.natif_installe())
            with p1, p2, p3, p4:
                troisieme = m.installer(avec_natif=True)
            self.assertEqual(troisieme["statut"], "DEJA_INSTALLEE")
            self.assertEqual(len(faux.urls_telechargees), 1)

    def test_chemin_leanc_sans_installation_leve(self):
        """Cache vide : chemin_leanc() lève ToolchainAbsente."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            with self.assertRaises(ToolchainAbsente):
                m.chemin_leanc()


# ── lean-toolchain (version.py) ──────────────────────────

class TestLireVersionProjet(unittest.TestCase):
    """version.lire_version_projet — parsing, remontée, absence (sans réseau)."""

    def test_format_prefixe_organisation(self):
        """'leanprover/lean4:v4.34.0' → '4.34.0'."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.0\n")
            version, chemin = lire_version_projet(tmp)
            self.assertEqual(version, "4.34.0")
            self.assertEqual(chemin, os.path.join(tmp, "lean-toolchain"))

    def test_format_version_bare(self):
        """'v4.34.0' (format récent) → '4.34.0'."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("v4.34.0\n")
            version, chemin = lire_version_projet(tmp)
            self.assertEqual(version, "4.34.0")
            self.assertTrue(chemin.endswith("lean-toolchain"))

    def test_format_sans_prefixe_v(self):
        """'4.34.0' sans 'v' → '4.34.0' (toléré)."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("  4.34.0  \n")
            version, _ = lire_version_projet(tmp)
            self.assertEqual(version, "4.34.0")

    def test_lignes_vides_ignorees(self):
        """Les lignes vides avant/après sont ignorées."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("\n\nleanprover/lean4:v4.34.0\n\n")
            version, _ = lire_version_projet(tmp)
            self.assertEqual(version, "4.34.0")

    def test_remontee_dossiers_parents(self):
        """Le fichier est trouvé en remontant depuis un sous-dossier."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.0\n")
            profond = os.path.join(tmp, "src", "sous", "dossier")
            os.makedirs(profond)
            version, chemin = lire_version_projet(profond)
            self.assertEqual(version, "4.34.0")
            self.assertEqual(chemin, os.path.join(tmp, "lean-toolchain"))

    def test_plus_proche_gagne(self):
        """Deux lean-toolchain : celui du dossier le plus proche gagne."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.0\n")
            sous = os.path.join(tmp, "projet")
            os.makedirs(sous)
            with open(os.path.join(sous, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("v4.33.0\n")
            version, chemin = lire_version_projet(sous)
            self.assertEqual(version, "4.33.0")
            self.assertEqual(chemin, os.path.join(sous, "lean-toolchain"))

    def test_absence_fichier(self):
        """Aucun lean-toolchain → (None, None), pas d'exception."""
        with tempfile.TemporaryDirectory() as tmp:
            version, chemin = lire_version_projet(tmp)
            self.assertIsNone(version)
            self.assertIsNone(chemin)

    def test_contenu_invalide_leve(self):
        """Contenu inparsable → FormatLeanToolchainInvalide."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("ceci n'est pas une version\n")
            with self.assertRaises(FormatLeanToolchainInvalide):
                lire_version_projet(tmp)

    def test_fichier_vide_leve(self):
        """Fichier vide → FormatLeanToolchainInvalide."""
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "lean-toolchain"), "w").close()
            with self.assertRaises(FormatLeanToolchainInvalide):
                lire_version_projet(tmp)


class TestVersionProjetManager(unittest.TestCase):
    """ToolchainManager — détection et refus de version non supportée."""

    def _gestionnaire(self, tmp):
        cache = os.path.join(tmp, "cache")
        return ToolchainManager(cache_dir=cache)

    def test_version_correspondante_passe(self):
        """lean-toolchain sur 4.34.1 → verifier retourne la version."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.1\n")
            m = self._gestionnaire(tmp)
            self.assertEqual(m.verifier_version_projet(tmp), "4.34.1")

    def test_absence_fichier_retourne_none(self):
        """Sans lean-toolchain → None, pas d'exception."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            self.assertIsNone(m.verifier_version_projet(tmp))

    def test_version_differente_leve_version_non_supportee(self):
        """Version demandée ≠ 4.34.1 → VersionNonSupportee avec attributs."""
        with tempfile.TemporaryDirectory() as tmp:
            chemin_tc = os.path.join(tmp, "lean-toolchain")
            with open(chemin_tc, "w", encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.33.0\n")
            m = self._gestionnaire(tmp)
            with self.assertRaises(VersionNonSupportee) as ctx:
                m.verifier_version_projet(tmp)
            e = ctx.exception
            self.assertEqual(e.demandee, "4.33.0")
            self.assertEqual(e.disponible, "4.34.1")
            self.assertEqual(e.chemin, chemin_tc)
            message = str(e)
            self.assertIn("4.33.0", message, "le message cite la demandée")
            self.assertIn("4.34.1", message, "le message cite la disponible")
            self.assertIn("multi-versions", message,
                          "le message documente l'évolution future")

    def test_compiler_refuse_sans_appeler_lean(self):
        """compiler() lève AVANT subprocess.run si version non supportée."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("v4.33.0\n")
            m = self._gestionnaire(tmp)
            fichier = os.path.join(tmp, "Test.lean")
            with patch("phi_complexity.toolchain.manager.subprocess.run") \
                    as faux_run:
                with self.assertRaises(VersionNonSupportee):
                    m.compiler(fichier)
            faux_run.assert_not_called()

    def test_compiler_passe_la_verification_si_version_correspondante(self):
        """Version OK → compiler() atteint chemin_lean() (pas d'install)."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("v4.34.1\n")
            m = self._gestionnaire(tmp)
            fichier = os.path.join(tmp, "Test.lean")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       ) as faux_run:
                # La vérification de version passe ; chemin_lean() lève
                # ToolchainAbsente car rien n'est installé (pas d'install ici).
                with self.assertRaises(ToolchainAbsente):
                    m.compiler(fichier)
            faux_run.assert_not_called()

    def test_version_demandee_pour(self):
        """version_demandee_pour() : (version, chemin) depuis un fichier."""
        with tempfile.TemporaryDirectory() as tmp:
            chemin_tc = os.path.join(tmp, "lean-toolchain")
            with open(chemin_tc, "w", encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.0\n")
            m = self._gestionnaire(tmp)
            version, chemin = m.version_demandee_pour(
                os.path.join(tmp, "src", "Test.lean"))
            self.assertEqual(version, "4.34.0")
            self.assertEqual(chemin, chemin_tc)


# ── CLI : phi lean --version avec lean-toolchain ──────────

class TestCliLeanVersion(unittest.TestCase):
    """_executer_lean --version : affichage projet, jamais de traceback."""

    def _args_version(self):
        return SimpleNamespace(init=False, version=True, ou=False,
                               fichier=None, exec=False, timeout=300)

    def _lancer(self, args, dossier):
        from phi_complexity.cli import _executer_lean
        precedent = os.getcwd()
        try:
            os.chdir(dossier)
            with contextlib.redirect_stdout(io.StringIO()) as tampon:
                code = _executer_lean(args)
            return code, tampon.getvalue()
        finally:
            os.chdir(precedent)

    def test_version_affiche_projet_detecte(self):
        """--version avec lean-toolchain valide → ligne Projet affichée."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("leanprover/lean4:v4.34.0\n")
            code, sortie = self._lancer(self._args_version(), tmp)
            self.assertEqual(code, 0)
            self.assertIn("4.34.0", sortie)
            self.assertIn("lean-toolchain", sortie)

    def test_version_lean_toolchain_invalide_sans_traceback(self):
        """--version avec lean-toolchain invalide → message, pas d'exception."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "lean-toolchain"), "w",
                      encoding="utf-8") as f:
                f.write("pas une version du tout\n")
            code, sortie = self._lancer(self._args_version(), tmp)
            self.assertEqual(code, 0, "aucune exception ne doit fuir")
            self.assertIn("invalide", sortie)

    def test_version_sans_lean_toolchain(self):
        """--version sans lean-toolchain → pas de ligne Projet."""
        with tempfile.TemporaryDirectory() as tmp:
            code, sortie = self._lancer(self._args_version(), tmp)
            self.assertEqual(code, 0)
            self.assertNotIn("Projet", sortie)


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

    def test_fichiers_natifs_presents(self):
        """Clé fichiers_natifs : liste non vide de motifs (kit leanc)."""
        with open(MANIFESTE_DEFAUT, "r", encoding="utf-8") as f:
            manifeste = json.load(f)
        self.assertIn("fichiers_natifs", manifeste,
                      "clé manquante : fichiers_natifs")
        natifs = manifeste["fichiers_natifs"]
        self.assertIsInstance(natifs, list)
        self.assertGreater(len(natifs), 0)
        for motif in natifs:
            self.assertIsInstance(motif, str)
            self.assertTrue(motif, "motif vide interdit")
        self.assertIn("bin/leanc", natifs)
        self.assertGreater(manifeste.get("taille_natif_octets_approx", 0), 0,
                           "taille_natif_octets_approx doit être mesurée")

    def test_motifs_synchronises_avec_extract(self):
        """Égalité stricte manifeste <-> constantes extract.py.

        Garde anti-dérive : le manifeste est documentaire, extract.py fait
        foi à l'exécution — les deux doivent dire la même chose.
        """
        with open(MANIFESTE_DEFAUT, "r", encoding="utf-8") as f:
            manifeste = json.load(f)
        self.assertEqual(sorted(manifeste["fichiers_minimaux"]),
                         sorted(MOTIFS_MINIMAUX),
                         "dérive : fichiers_minimaux != MOTIFS_MINIMAUX")
        self.assertEqual(sorted(manifeste["fichiers_natifs"]),
                         sorted(MOTIFS_NATIFS),
                         "dérive : fichiers_natifs != MOTIFS_NATIFS")


# ── extensions optionnelles Lean/Std (chantier 3) ─────────

def _fabriquer_archive_extensions(chemin):
    _requiert_zstandard()
    """Archive .tar.zst de test avec des modules Lean/Std factices.

    Contient de quoi tester la sélectivité des motifs d'extension :
    racines Std.olean(*), un sous-module Std, des pièges Lean et des
    variantes non-olean (.ir, .ilean) qui doivent rester dehors.
    """
    membres = [
        ("lean-4.34.0-linux/lib/lean/Std.olean", b"OLEAN-std", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std.olean.private",
         b"OLEAN-std-prive", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std.olean.server",
         b"OLEAN-std-srv", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std/Data/HashMap.olean",
         b"OLEAN-hm", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std/Data/HashMap.olean.private",
         b"OLEAN-hm-prive", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std/Data/HashMap.olean.server",
         b"OLEAN-hm-srv", 0o644),
        # --- pièges : autre bibliothèque ---
        ("lean-4.34.0-linux/lib/lean/Lean.olean", b"PIEGE", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lean/Meta.olean", b"PIEGE", 0o644),
        # --- pièges : variantes non lues par lean au runtime ---
        ("lean-4.34.0-linux/lib/lean/Std/Data/HashMap.ir",
         b"PIEGE-IR", 0o644),
        ("lean-4.34.0-linux/lib/lean/Std/Data/HashMap.ilean",
         b"PIEGE-ILEAN", 0o644),
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


class TestMotifsExtensions(unittest.TestCase):
    """extract — sélectivité des motifs d'extension sur archive réelle."""

    def test_motifs_std_selectionnent_std_uniquement(self):
        """MOTIFS_EXTENSION_STD : racines + arbre Std, rien d'autre."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_extensions(archive)
            dest = os.path.join(tmp, "ext")
            resultat = extraire_minimal(
                archive, dest, motifs=MOTIFS_EXTENSION_STD)
            attendus = [
                "lib/lean/Std.olean",
                "lib/lean/Std.olean.private",
                "lib/lean/Std.olean.server",
                "lib/lean/Std/Data/HashMap.olean",
                "lib/lean/Std/Data/HashMap.olean.private",
                "lib/lean/Std/Data/HashMap.olean.server",
            ]
            for relatif in attendus:
                self.assertTrue(
                    os.path.isfile(os.path.join(dest, relatif)),
                    "manquant : %s" % relatif,
                )
            for piege in ("lib/lean/Lean.olean",
                          "lib/lean/Lean/Meta.olean",
                          "lib/lean/Std/Data/HashMap.ir",
                          "lib/lean/Std/Data/HashMap.ilean"):
                self.assertFalse(
                    os.path.exists(os.path.join(dest, piege)),
                    "piège extrait à tort : %s" % piege,
                )
            self.assertEqual(resultat["fichiers"], 6)

    def test_motifs_lean_contiennent_std(self):
        """MOTIFS_EXTENSION_LEAN ⊇ MOTIFS_EXTENSION_STD (fermeture)."""
        self.assertLessEqual(set(MOTIFS_EXTENSION_STD),
                             set(MOTIFS_EXTENSION_LEAN),
                             "l'extension 'lean' doit inclure 'std' "
                             "(fermeture de `import Lean`)")

    def test_motifs_extensions_documentes(self):
        """Garde : motifs non vides, tous sous lib/lean/, 3 variantes."""
        for nom, motifs in (("std", MOTIFS_EXTENSION_STD),
                            ("lean", MOTIFS_EXTENSION_LEAN)):
            self.assertGreater(len(motifs), 0)
            for motif in motifs:
                self.assertTrue(
                    motif.startswith("lib/lean/"),
                    "motif '%s' hors lib/lean/ (%s)" % (motif, nom),
                )
            self.assertTrue(any(m.endswith(".olean") and ".private" not in m
                                and ".server" not in m for m in motifs),
                            "variante .olean manquante (%s)" % nom)
            self.assertTrue(any(m.endswith(".olean.private") for m in motifs),
                            "variante .olean.private manquante (%s)" % nom)
            self.assertTrue(any(m.endswith(".olean.server") for m in motifs),
                            "variante .olean.server manquante (%s)" % nom)


class TestValiderExtension(unittest.TestCase):
    """validate.valider_extension — élaboration réelle (binaire mocké)."""

    def test_succes_binaire_reel_mocke(self):
        """Faux lean qui sort 0 → dict retourné, fichier temp nettoyé."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_lean(tmp)
            with patch("phi_complexity.toolchain.validate.subprocess.run",
                       ) as faux_run:
                faux_run.return_value = SimpleNamespace(
                    returncode=0, stdout="ok\n", stderr="")
                resultat = valider_extension(tmp, "Std")
            cmd = faux_run.call_args[0][0]
            self.assertTrue(cmd[0].endswith(os.path.join("bin", "lean")))
            self.assertTrue(cmd[1].endswith(".lean"))
            env = faux_run.call_args[1]["env"]
            self.assertEqual(env["LEAN_PATH"],
                             os.path.join(tmp, "lib", "lean"))
            self.assertEqual(resultat["module"], "Std")
            self.assertFalse(os.path.exists(cmd[1]),
                             "le fichier temporaire doit être supprimé")

    def test_contenu_fichier_importe_le_module(self):
        """Le fichier élaboré contient bien `import <module>`."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_lean(tmp)
            contenus = []

            def faux_run(cmd, **kwargs):
                with open(cmd[1], "r", encoding="utf-8") as f:
                    contenus.append(f.read())
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("phi_complexity.toolchain.validate.subprocess.run",
                       side_effect=faux_run):
                valider_extension(tmp, "Lean")
            self.assertEqual(len(contenus), 1)
            self.assertIn("import Lean", contenus[0])

    def test_echec_elaboration(self):
        """rc != 0 → ErreurValidation (détail conservé)."""
        with tempfile.TemporaryDirectory() as tmp:
            _faux_lean(tmp)
            with patch("phi_complexity.toolchain.validate.subprocess.run",
                       ) as faux_run:
                faux_run.return_value = SimpleNamespace(
                    returncode=1, stdout="", stderr="unknown module\n")
                with self.assertRaises(ErreurValidation):
                    valider_extension(tmp, "Std")

    def test_binaire_absent(self):
        """Pas de bin/lean → ErreurValidation."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ErreurValidation):
                valider_extension(tmp, "Std")


# ── manager : installer_extension ────────────────────────

MANIFESTE_TEST_EXT = {
    "version": "4.34.0",
    "url": ("https://github.com/leanprover/lean4/releases/download/"
            "v4.34.0/lean-4.34.0-linux.tar.zst"),
    "sha256": "ab" * 32,
    "fichiers_minimaux": ["bin/lean"],
    "extensions": {
        "std": {
            "description": "extension de test (Std)",
            "module_racine": "Std",
            "motifs": MOTIFS_STD_EXTRACT,
            "fichiers_mesures": 1467,
            "taille_octets_mesuree": 304056336,
            "note": "test",
        },
        "lean": {
            "description": "extension de test (Lean)",
            "module_racine": "Lean",
            "motifs": MOTIFS_LEAN_EXTRACT,
            "fichiers_mesures": 5121,
            "taille_octets_mesuree": 1292721440,
            "note": "test",
        },
    },
}


def _ecrire_manifeste_test_ext(dossier):
    chemin = os.path.join(dossier, "manifeste-ext.json")
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(MANIFESTE_TEST_EXT, f)
    return chemin


class _FauxPhasesExt(_FauxPhases):
    """_FauxPhases + extracteur avec motifs capturés + valider_extension."""

    def __init__(self):
        super().__init__()
        self.motifs_recus = []
        self.validations_extension = []
        self.extractions = 0

    def extraire_minimal(self, archive, dest_dir, motifs=None,
                         progression=None):
        self.motifs_recus.append(list(motifs) if motifs else None)
        self.extractions += 1
        return super().extraire_minimal(
            archive, dest_dir, motifs=motifs, progression=progression)

    def valider_extension(self, repertoire, module_racine, timeout_s=600):
        self.validations_extension.append((repertoire, module_racine))
        return {"module": module_racine,
                "chemin_lean": os.path.join(repertoire, "bin", "lean")}


def _patch_phases_ext(faux):
    """Les 3 phases + valider_extension patchées dans le namespace manager."""
    p1, p2, p3 = _patch_phases(faux)
    p4 = patch("phi_complexity.toolchain.manager.valider_extension",
               side_effect=faux.valider_extension)
    return p1, p2, p3, p4


class TestInstallerExtension(unittest.TestCase):
    """manager.installer_extension — phases mockées, aucun réseau."""

    def _gestionnaire(self, tmp):
        manifeste = _ecrire_manifeste_test_ext(tmp)
        return ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                manifeste=manifeste)

    def _installer_base(self, m, faux):
        p1, p2, p3, p4 = _patch_phases_ext(faux)
        with p1, p2, p3, p4:
            m.installer()
        # simule l'archive restée en cache après installer()
        with open(m.chemin_archive, "wb") as f:
            f.write(b"archive-factice")

    def test_extension_inconnue_leve(self):
        """Nom inconnu → ValueError (extension_installee et installer)."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            with self.assertRaises(ValueError):
                m.extension_installee("nope")
            with self.assertRaises(ValueError):
                m.installer_extension("nope")

    def test_flux_complet_std(self):
        """Base installée → extraction avec les bons motifs → validation."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesExt()
            self._installer_base(m, faux)
            self.assertFalse(m.extension_installee("std"))
            p1, p2, p3, p4 = _patch_phases_ext(faux)
            with p1, p2, p3, p4:
                resultat = m.installer_extension("std")
            self.assertEqual(resultat["statut"], "EXTENSION_INSTALLEE")
            self.assertEqual(resultat["extension"], "std")
            self.assertEqual(resultat["module_racine"], "Std")
            self.assertEqual(faux.motifs_recus[-1], MOTIFS_EXTENSION_STD,
                             "l'extraction doit recevoir MOTIFS_EXTENSION_STD")
            self.assertIn((m.rep_install, "Std"),
                          faux.validations_extension,
                          "valider_extension doit recevoir (rep, 'Std')")
            self.assertTrue(m.extension_installee("std"))
            self.assertTrue(
                os.path.isfile(
                    os.path.join(m.rep_install, ".valide-ext-std")),
                "le marqueur .valide-ext-std doit exister")

    def test_idempotent(self):
        """2e installer_extension → DEJA_INSTALLEE sans ré-extraction."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesExt()
            self._installer_base(m, faux)
            p1, p2, p3, p4 = _patch_phases_ext(faux)
            with p1, p2, p3, p4:
                premier = m.installer_extension("lean")
                self.assertEqual(premier["statut"], "EXTENSION_INSTALLEE")
                extractions_apres_premier = faux.extractions
                second = m.installer_extension("lean")
                self.assertEqual(second["statut"], "DEJA_INSTALLEE")
            self.assertEqual(faux.extractions, extractions_apres_premier,
                             "le 2e appel ne doit pas ré-extraire")

    def test_installe_la_base_si_absente(self):
        """Base absente → installer() d'abord (téléchargement), puis ext."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesExt()
            p1, p2, p3, p4 = _patch_phases_ext(faux)
            with p1, p2, p3, p4:
                # pas d'archive en cache : le faux téléchargeur l'écrit
                resultat = m.installer_extension("std")
            self.assertEqual(resultat["statut"], "EXTENSION_INSTALLEE")
            self.assertTrue(m.est_installee())
            self.assertTrue(m.extension_installee("std"))
            self.assertGreaterEqual(len(faux.urls_telechargees), 1,
                                    "la base aurait dû être téléchargée")

    def test_retelecharge_archive_manquante(self):
        """Archive effacée du cache → re-téléchargée avant extraction."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesExt()
            self._installer_base(m, faux)
            os.unlink(m.chemin_archive)
            telechargements_avant = len(faux.urls_telechargees)
            p1, p2, p3, p4 = _patch_phases_ext(faux)
            with p1, p2, p3, p4:
                # le faux téléchargeur réécrit l'archive factice
                def telecharger_reecrit(url, dest, sha256_attendu,
                                        progression=None, _ouvreur=None):
                    faux.urls_telechargees.append(url)
                    with open(dest, "wb") as f:
                        f.write(b"archive-factice")
                    return {"chemin": dest, "octets": 15,
                            "sha256": sha256_attendu}
                with patch("phi_complexity.toolchain.manager.telecharger",
                           side_effect=telecharger_reecrit):
                    resultat = m.installer_extension("std")
            self.assertEqual(resultat["statut"], "EXTENSION_INSTALLEE")
            self.assertGreater(len(faux.urls_telechargees),
                               telechargements_avant,
                               "l'archive manquante doit être re-téléchargée")

    def test_marqueur_extension_corrompu(self):
        """Marqueur avec mauvais sha256 → extension_installee() False."""
        with tempfile.TemporaryDirectory() as tmp:
            m = self._gestionnaire(tmp)
            faux = _FauxPhasesExt()
            p1, p2, p3, p4 = _patch_phases_ext(faux)
            with p1, p2, p3, p4:
                m.installer_extension("std")
            self.assertTrue(m.extension_installee("std"))
            with open(m._chemin_marqueur_extension("std"),
                      "w", encoding="utf-8") as f:
                json.dump({"version": "4.34.0", "sha256": "00" * 32,
                           "extension": "std"}, f)
            self.assertFalse(m.extension_installee("std"))

    def test_manifeste_extensions_synchronises(self):
        """Garde : le manifeste réel == constantes extract.py (motifs)."""
        with open(MANIFESTE_DEFAUT, "r", encoding="utf-8") as f:
            manifeste = json.load(f)
        extensions = manifeste.get("extensions", {})
        self.assertEqual(set(extensions.keys()), set(EXTENSIONS_MOTIFS),
                         "le manifeste et EXTENSIONS_MOTIFS doivent "
                         "déclarer les mêmes extensions")
        self.assertEqual(extensions["std"]["motifs"], MOTIFS_EXTENSION_STD)
        self.assertEqual(extensions["lean"]["motifs"], MOTIFS_EXTENSION_LEAN)
        self.assertEqual(extensions["std"]["module_racine"], "Std")
        self.assertEqual(extensions["lean"]["module_racine"], "Lean")
        # tailles mesurées présentes et plausibles (> 0)
        self.assertGreater(
            extensions["std"]["taille_octets_mesuree"], 0)
        self.assertGreater(
            extensions["lean"]["taille_octets_mesuree"],
            extensions["std"]["taille_octets_mesuree"])


# ── Lake (chantier 2, 2026-10-08) ──────────────────────────

SCRIPT_FAUX_LAKE = '#!/bin/sh\necho "Lake version 5.0.0-test (Lean version 4.34.0)"\n'


def _fabriquer_archive_lake_tar_zst(chemin):
    _requiert_zstandard()
    """Archive .tar.zst de test avec des fichiers Lake factices + pièges.

    Les pièges vérifient la sélectivité : .olean.private / .olean.server /
    .ilean / .ir doivent rester dehors (exclus par conception, voir
    MOTIFS_LAKE), ainsi que libLake.a et l'arbre Lean.*.
    """
    membres = [
        ("lean-4.34.0-linux/bin/lake", SCRIPT_FAUX_LAKE.encode(), 0o755),
        ("lean-4.34.0-linux/lib/lean/libLake_shared.so",
         b"\x7fELF-faux-lake", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lake.olean", b"OLEAN-lake", 0o644),
        ("lean-4.34.0-linux/lib/lean/LakeMain.olean",
         b"OLEAN-lakemain", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lake/DSL/Config.olean",
         b"OLEAN-cfg", 0o644),
        ("lean-4.34.0-linux/include/lean/lean.h", b"/* lean.h */", 0o644),
        ("lean-4.34.0-linux/lib/libc++.a", b"AR-faux-c++", 0o644),
        ("lean-4.34.0-linux/lib/libgmp.a", b"AR-faux-gmp", 0o644),
        # --- pièges : variantes olean exclues ---
        ("lean-4.34.0-linux/lib/lean/Lake.olean.private",
         b"PIEGE-prive", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lake/DSL/Config.olean.server",
         b"PIEGE-serveur", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lake/DSL/Config.ilean",
         b"PIEGE-ilean", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lake/DSL/Config.ir",
         b"PIEGE-ir", 0o644),
        # --- pièges : hors motifs ---
        ("lean-4.34.0-linux/lib/lean/libLake.a", b"PIEGE-statique", 0o644),
        ("lean-4.34.0-linux/lib/lean/Lean.olean", b"PIEGE-lean", 0o644),
        ("lean-4.34.0-linux/lib/lean/Init.olean", b"PIEGE-init", 0o644),
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


def _installer_base_lake(tmp):
    """Gestionnaire + base minimale simulée (marqueur .valide + bin/lean).

    Retourne (manager, archive_lake) avec l'archive lake factice déjà
    placée à l'emplacement attendu par installer_lake().
    """
    manifeste = _ecrire_manifeste_test(tmp)
    m = ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                         manifeste=manifeste)
    rep = m.rep_install
    os.makedirs(os.path.join(rep, "bin"), exist_ok=True)
    with open(os.path.join(rep, "bin", "lean"), "w") as f:
        f.write("#!/bin/sh\necho Lean\n")
    os.chmod(os.path.join(rep, "bin", "lean"), 0o755)
    with open(m.chemin_marqueur, "w", encoding="utf-8") as f:
        json.dump({"version": "4.34.0", "sha256": "ab" * 32}, f)
    _fabriquer_archive_lake_tar_zst(m.chemin_archive)
    return m


class TestMotifsLake(unittest.TestCase):
    """Gardes documentaires sur MOTIFS_LAKE et sa synchro manifeste."""

    def test_pieces_requises_presentes(self):
        self.assertIn("bin/lake", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/lean/libLake_shared.so", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/lean/Lake.olean", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/lean/LakeMain.olean", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/lean/Lake/*.olean", MOTIFS_LAKE_EXTRACT)
        self.assertIn("include/lean/*.h", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/libc++.a", MOTIFS_LAKE_EXTRACT)
        self.assertIn("lib/libgmp.a", MOTIFS_LAKE_EXTRACT)

    def test_variantes_exclues(self):
        """Ni .private (64 Mo), ni .server, ni .ilean, ni .ir, ni .a Lean."""
        for motif in MOTIFS_LAKE_EXTRACT:
            self.assertNotIn(".private", motif,
                             "variante .private exclue par conception")
            self.assertNotIn(".server", motif)
            self.assertNotIn(".ilean", motif)
            self.assertNotIn(".ir", motif)
        self.assertNotIn("lib/lean/libLake.a", MOTIFS_LAKE_EXTRACT,
                         "le statique libLake.a (21 Mo) est inutile "
                         "(le .so suffit)")
        self.assertEqual(len(MOTIFS_LAKE_EXTRACT),
                         len(set(MOTIFS_LAKE_EXTRACT)),
                         "motif dupliqué dans MOTIFS_LAKE")

    def test_motifs_synchronises_avec_manifeste(self):
        """Égalité stricte manifeste <-> extract.py (garde anti-dérive)."""
        with open(MANIFESTE_DEFAUT, "r", encoding="utf-8") as f:
            manifeste = json.load(f)
        self.assertEqual(sorted(manifeste["fichiers_lake"]),
                         sorted(MOTIFS_LAKE_EXTRACT),
                         "dérive : fichiers_lake != MOTIFS_LAKE")
        self.assertGreater(manifeste.get("taille_lake_octets_approx", 0), 0,
                           "taille_lake_octets_approx doit être mesurée")
        self.assertIn("lakefile.toml", manifeste.get("note_lake", ""),
                      "la note doit documenter la limite lakefile.toml-only")


class TestExtraireLake(unittest.TestCase):
    """extract.extraire_minimal + MOTIFS_LAKE — sélectivité prouvée."""

    def test_seul_lake_est_extrait(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_lake_tar_zst(archive)
            dest = os.path.join(tmp, "lake")
            resultat = extraire_minimal(
                archive, dest, motifs=MOTIFS_LAKE_EXTRACT)

            attendus = [
                "bin/lake",
                "lib/lean/libLake_shared.so",
                "lib/lean/Lake.olean",
                "lib/lean/LakeMain.olean",
                "lib/lean/Lake/DSL/Config.olean",
                "include/lean/lean.h",
                "lib/libc++.a",
                "lib/libgmp.a",
            ]
            for relatif in attendus:
                self.assertTrue(
                    os.path.isfile(os.path.join(dest, relatif)),
                    "manquant : %s" % relatif,
                )
            pieges = [
                "lib/lean/Lake.olean.private",
                "lib/lean/Lake/DSL/Config.olean.server",
                "lib/lean/Lake/DSL/Config.ilean",
                "lib/lean/Lake/DSL/Config.ir",
                "lib/lean/libLake.a",
                "lib/lean/Lean.olean",
                "lib/lean/Init.olean",
            ]
            for piege in pieges:
                self.assertFalse(
                    os.path.exists(os.path.join(dest, piege)),
                    "piège extrait à tort : %s" % piege,
                )
            self.assertEqual(resultat["fichiers"], len(attendus))

    def test_bit_executable_lake_preserve(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "lean.tar.zst")
            _fabriquer_archive_lake_tar_zst(archive)
            dest = os.path.join(tmp, "lake")
            extraire_minimal(archive, dest, motifs=MOTIFS_LAKE_EXTRACT)
            binaire = os.path.join(dest, "bin", "lake")
            self.assertTrue(os.access(binaire, os.X_OK),
                            "le bit exécutable de bin/lake est perdu")


class TestCreerLiensLake(unittest.TestCase):
    """extract.creer_liens_lake — liens + libStd.a vide, idempotent."""

    def test_liens_et_archive_vide_crees(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "inst")
            os.makedirs(os.path.join(dest, "lib", "lean"))
            # cibles des liens (factices, seul le lien compte ici)
            for cible in ("libleanshared.so", "libInit_shared.so",
                          "libLake_shared.so"):
                open(os.path.join(dest, "lib", "lean", cible), "w").close()
            open(os.path.join(dest, "lib", "libc++.a"), "w").close()
            open(os.path.join(dest, "lib", "libgmp.a"), "w").close()

            resultat = creer_liens_lake(dest)

            self.assertEqual(
                os.readlink(os.path.join(dest, "lib", "lean", "libLean.so")),
                "libleanshared.so")
            self.assertEqual(
                os.readlink(os.path.join(dest, "lib", "lean",
                                         "libgmp.a")),
                "../libgmp.a")
            chemin_std = os.path.join(dest, "lib", "lean", "libStd.a")
            self.assertTrue(os.path.isfile(chemin_std))
            with open(chemin_std, "rb") as f:
                self.assertEqual(f.read(), b"!<arch>\n")
            self.assertEqual(resultat["liens"], 10)
            self.assertEqual(resultat["archives"], 1)

    def test_idempotent_et_non_destructif(self):
        """2e appel : rien à faire ; vraie libStd.a (kit natif) conservée."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "inst")
            os.makedirs(os.path.join(dest, "lib", "lean"))
            for cible in ("libleanshared.so", "libInit_shared.so",
                          "libLake_shared.so"):
                open(os.path.join(dest, "lib", "lean", cible), "w").close()
            # vraie libStd.a du kit natif : ne doit JAMAIS être écrasée
            vraie = os.path.join(dest, "lib", "lean", "libStd.a")
            with open(vraie, "wb") as f:
                f.write(b"!<arch>\n" + b"VRAIE-ARCHIVE-NATIF" * 100)
            taille_avant = os.path.getsize(vraie)

            premier = creer_liens_lake(dest)
            self.assertEqual(premier["archives"], 0,
                             "la vraie libStd.a ne doit pas être écrasée")
            self.assertEqual(os.path.getsize(vraie), taille_avant)
            second = creer_liens_lake(dest)
            self.assertEqual(second["liens"], 0)
            self.assertEqual(second["archives"], 0)

    def test_destination_absente_leve(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ErreurExtraction):
                creer_liens_lake(os.path.join(tmp, "inexistant"))


class TestValiderLake(unittest.TestCase):
    """validate.valider_lake — faux binaire shell, aucun vrai Lake requis."""

    def _faux_lake(self, rep, executable=True):
        dossier_bin = os.path.join(rep, "bin")
        os.makedirs(dossier_bin, exist_ok=True)
        chemin = os.path.join(dossier_bin, "lake")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(SCRIPT_FAUX_LAKE)
        os.chmod(chemin, 0o755 if executable else 0o644)
        for relatif in ("lib/lean/libLake_shared.so",
                        "lib/lean/Lake.olean",
                        "lib/lean/Lake/DSL/Config.olean",
                        "include/lean/lean.h"):
            chemin_f = os.path.join(rep, relatif)
            os.makedirs(os.path.dirname(chemin_f), exist_ok=True)
            open(chemin_f, "w").close()
        return chemin

    def test_succes(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = self._faux_lake(tmp)
            resultat = valider_lake(tmp)
            self.assertEqual(resultat["chemin_lake"], chemin)
            self.assertIn("Lake version", resultat["version"])

    def test_binaire_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ErreurValidation):
                valider_lake(tmp)

    def test_piece_critique_manquante(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._faux_lake(tmp)
            os.remove(os.path.join(tmp, "lib", "lean", "Lake.olean"))
            with self.assertRaises(ErreurValidation):
                valider_lake(tmp)

    def test_sortie_inattendue(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier_bin = os.path.join(tmp, "bin")
            os.makedirs(dossier_bin, exist_ok=True)
            chemin = os.path.join(dossier_bin, "lake")
            with open(chemin, "w") as f:
                f.write('#!/bin/sh\necho "pas lake du tout"\n')
            os.chmod(chemin, 0o755)
            for relatif in ("lib/lean/libLake_shared.so",
                            "lib/lean/Lake.olean",
                            "lib/lean/Lake/DSL/Config.olean",
                            "include/lean/lean.h"):
                chemin_f = os.path.join(tmp, relatif)
                os.makedirs(os.path.dirname(chemin_f), exist_ok=True)
                open(chemin_f, "w").close()
            with self.assertRaises(ErreurValidation):
                valider_lake(tmp)


class TestManagerLake(unittest.TestCase):
    """manager : installer_lake(), avec_lake, chemin_lake, executer_lake."""

    def test_installer_lake_ajout_reel_sans_reseau(self):
        """Extraction réelle depuis l'archive factice en cache (0 réseau)."""
        with tempfile.TemporaryDirectory() as tmp:
            m = _installer_base_lake(tmp)
            self.assertFalse(m.lake_installe())

            resultat = m.installer_lake()

            self.assertEqual(resultat["statut"], "LAKE_INSTALLE")
            self.assertTrue(os.access(
                os.path.join(m.rep_install, "bin", "lake"), os.X_OK))
            self.assertTrue(os.path.islink(
                os.path.join(m.rep_install, "lib", "lean", "libLean.so")))
            self.assertTrue(m.lake_installe())
            self.assertEqual(m.chemin_lake(),
                             os.path.join(m.rep_install, "bin", "lake"))
            # idempotence
            second = m.installer_lake()
            self.assertEqual(second["statut"], "DEJA_INSTALLEE")

    def test_installer_lake_sans_base_leve(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifeste = _ecrire_manifeste_test(tmp)
            m = ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                 manifeste=manifeste)
            with self.assertRaises(ToolchainAbsente):
                m.installer_lake()

    def test_chemin_lake_absent_leve_lake_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = _installer_base_lake(tmp)
            with self.assertRaises(LakeAbsent) as ctx:
                m.chemin_lake()
            self.assertIn("--avec-lake", str(ctx.exception))

    def test_installer_avec_lake_delegue(self):
        """installer(avec_lake=True) appelle installer_lake() (mocké ici)."""
        with tempfile.TemporaryDirectory() as tmp:
            manifeste = _ecrire_manifeste_test(tmp)
            m = ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                 manifeste=manifeste)
            faux = _FauxPhases()
            p1, p2, p3 = _patch_phases(faux)
            with p1, p2, p3, \
                    patch("phi_complexity.toolchain.manager.valider_lake",
                          return_value={"chemin_lake": "X",
                                        "version": "Lake test",
                                        "sortie": "Lake version test"}), \
                    patch.object(ToolchainManager, "installer_lake",
                                 return_value={"statut": "LAKE_INSTALLE",
                                               "chemin_lake": "X",
                                               "fichiers": 1, "octets": 1,
                                               "liens": 1}) as faux_lake:
                resultat = m.installer(avec_lake=True)
            self.assertEqual(resultat["statut"], "INSTALLEE")
            self.assertTrue(resultat["lake"])
            self.assertEqual(resultat["chemin_lake"], "X")
            faux_lake.assert_called_once()

    def test_installer_sans_lake_inchange(self):
        """Par défaut : pas d'appel à installer_lake, clé lake=False."""
        with tempfile.TemporaryDirectory() as tmp:
            manifeste = _ecrire_manifeste_test(tmp)
            m = ToolchainManager(cache_dir=os.path.join(tmp, "cache"),
                                 manifeste=manifeste)
            faux = _FauxPhases()
            p1, p2, p3 = _patch_phases(faux)
            with p1, p2, p3, \
                    patch.object(ToolchainManager, "installer_lake") \
                    as faux_lake:
                resultat = m.installer()
            faux_lake.assert_not_called()
            self.assertFalse(resultat["lake"])

    def test_executer_lake_environnement_et_cwd(self):
        """executer_lake : LEAN_PATH + LD_LIBRARY_PATH, cwd propagé."""
        with tempfile.TemporaryDirectory() as tmp:
            m = _installer_base_lake(tmp)
            # simuler lake installé (binaire factice suffit pour le chemin)
            os.makedirs(os.path.join(m.rep_install, "bin"), exist_ok=True)
            with open(os.path.join(m.rep_install, "bin", "lake"), "w") as f:
                f.write("#!/bin/sh\necho lake\n")
            os.chmod(os.path.join(m.rep_install, "bin", "lake"), 0o755)
            with open(m.chemin_marqueur, "r", encoding="utf-8") as f:
                marqueur = json.load(f)
            marqueur["lake"] = True
            with open(m.chemin_marqueur, "w", encoding="utf-8") as f:
                json.dump(marqueur, f)

            projet = os.path.join(tmp, "projet")
            os.makedirs(projet)
            faux_proc = SimpleNamespace(returncode=0, stdout="ok\n",
                                        stderr="")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       return_value=faux_proc) as faux_run:
                resultat = m.executer_lake(["new", "demo"], cwd=projet)

            cmd = faux_run.call_args[0][0]
            self.assertEqual(cmd[0], m.chemin_lake())
            self.assertEqual(cmd[1:], ["new", "demo"])
            self.assertEqual(faux_run.call_args[1]["cwd"], projet)
            env = faux_run.call_args[1]["env"]
            lib_lean = os.path.join(m.rep_install, "lib", "lean")
            self.assertEqual(env["LEAN_PATH"], lib_lean)
            self.assertIn(lib_lean, env["LD_LIBRARY_PATH"])
            self.assertEqual(resultat, {"rc": 0, "stdout": "ok\n",
                                        "stderr": "", "ok": True})

    def test_executer_lake_refuse_lakefile_lean(self):
        """lakefile.lean sans lakefile.toml → LakeConfigNonSupportee claire."""
        with tempfile.TemporaryDirectory() as tmp:
            m = _installer_base_lake(tmp)
            os.makedirs(os.path.join(m.rep_install, "bin"), exist_ok=True)
            with open(os.path.join(m.rep_install, "bin", "lake"), "w") as f:
                f.write("#!/bin/sh\necho lake\n")
            os.chmod(os.path.join(m.rep_install, "bin", "lake"), 0o755)
            with open(m.chemin_marqueur, "r", encoding="utf-8") as f:
                marqueur = json.load(f)
            marqueur["lake"] = True
            with open(m.chemin_marqueur, "w", encoding="utf-8") as f:
                json.dump(marqueur, f)

            projet = os.path.join(tmp, "projet")
            os.makedirs(projet)
            with open(os.path.join(projet, "lakefile.lean"), "w") as f:
                f.write("import Lake\n")
            with self.assertRaises(LakeConfigNonSupportee) as ctx:
                m.executer_lake(["build"], cwd=projet)
            self.assertIn("lakefile.toml", str(ctx.exception))
            # avec les deux fichiers : le TOML l'emporte, pas d'erreur
            with open(os.path.join(projet, "lakefile.toml"), "w") as f:
                f.write("[package]\n")
            faux_proc = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("phi_complexity.toolchain.manager.subprocess.run",
                       return_value=faux_proc):
                resultat = m.executer_lake(["build"], cwd=projet)
            self.assertTrue(resultat["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
