#!/usr/bin/env python3
"""Extraction sélective du sous-ensemble minimal depuis le tar.zst officiel.

On télécharge la release officielle complète (SHA256 vérifié en amont),
mais on n'en extrait QUE les fichiers du minimal (spec TOOLCHAIN_MINI_SPEC.md
§6) : bin/lean, les 4 .so, et Init/*.olean(+.private, +.server).

Le kit natif (MOTIFS_NATIFS : leanc + clang embarqué + ld.lld + archives
statiques + en-têtes C) s'extrait en option via
``extraire_minimal(archive, dest, motifs=MOTIFS_MINIMAUX + MOTIFS_NATIFS)`` ;
les liens symboliques requis (libc++.so.1, ...) sont recréés avec garde
anti-traversal.

Le premier composant du chemin dans l'archive (ex. `lean-4.34.0-linux/`)
est ignoré : on reconstruit l'arborescence `bin/`, `lib/` à la racine de
la destination.
"""

import fnmatch
import io
import os
import tarfile

import zstandard


class ErreurExtraction(Exception):
    """Échec de décompression ou d'extraction."""


# Motifs (relatifs à la racine de l'archive, premier composant ignoré).
# Ordre : du plus spécifique au plus général.
# DOIT rester synchronisé avec "fichiers_minimaux" de TOOLCHAIN_MANIFEST.json
# (test de cohérence dans tests/test_toolchain.py).
MOTIFS_MINIMAUX = [
    "bin/lean",
    "lib/lean/libInit_shared.so",
    "lib/lean/libleanshared.so",
    "lib/lean/libleanshared_1.so",
    "lib/lean/libleanshared_2.so",
    "lib/lean/Init/*.olean",
    "lib/lean/Init/*.olean.private",
    "lib/lean/Init/*.olean.server",
    "lib/lean/Init/**/*.olean",
    "lib/lean/Init/**/*.olean.private",
    "lib/lean/Init/**/*.olean.server",
    "lib/lean/Init.olean",
    "lib/lean/Init.olean.private",
    "lib/lean/Init.olean.server",
]

# Motifs du kit natif (leanc) — validés empiriquement le 2026-10-08 :
# `lean --c` + `leanc` produisent un binaire natif fonctionnel
# (Hello World testé : compilation C OK, édition de liens OK,
# exécution OK, binaire lié statiquement hors libc système).
# Contenu : leanc + clang embarqué (+ ses .so) + ld.lld + en-têtes C
# (include/clang : stddef.h & co, requis car leanc passe -nostdinc) +
# objets glibc + archives statiques Lean (libLean.a : 319 Mo à elle seule)
# + archives tierces (gmp, uv, ssl, crypto).
# Note : bin/llvm-ar volontairement exclu (jamais invoqué par leanc).
MOTIFS_NATIFS = [
    "bin/leanc",
    "bin/clang",
    "bin/ld.lld",
    "lib/libclang-cpp.so.22.1",
    "lib/libLLVM.so.22.1",
    "lib/libc++.so",
    "lib/libc++.so.1.0",
    "lib/libc++abi.so.1.0",
    "lib/libunwind.so.1.0",
    "lib/libc++.a",
    "lib/libc++abi.a",
    "lib/libunwind.a",
    "lib/libgmp.a",
    "lib/libuv.a",
    "lib/libssl.a",
    "lib/libcrypto.a",
    "lib/glibc/*",
    "lib/crt1.o",
    "lib/Scrt1.o",
    "lib/crti.o",
    "lib/crtn.o",
    "lib/gcrt1.o",
    "lib/Mcrt1.o",
    "lib/clang/22/lib/x86_64-unknown-linux-gnu/*",
    "lib/lean/libleancpp.a",
    "lib/lean/libleanrt.a",
    "lib/lean/libLeanIR.a",
    "lib/lean/libLeanc.a",
    "lib/lean/libInit.a",
    "lib/lean/libleanmanifest.a",
    "lib/lean/libLean.a",
    "lib/lean/libStd.a",
    "lib/lean/libLake.a",
    "include/lean/*.h",
    "include/clang/*",
    # Liens symboliques requis par l'éditeur de liens dynamique
    # (clang/ld.lld chargent libc++.so.1, libclang-cpp.so.22.1, ...).
    "lib/libclang-cpp.so",
    "lib/libLLVM.so",
    "lib/libLLVM-22.so",
    "lib/libc++.so.1",
    "lib/libc++abi.so",
    "lib/libc++abi.so.1",
    "lib/libunwind.so",
    "lib/libunwind.so.1",
]


# ── extensions optionnelles (chantier 3, 2026-10-08) ──────────
# Ces motifs ne font PAS partie du minimal : ils s'extraient à la demande
# via ToolchainManager.installer_extension(), depuis l'archive officielle
# déjà présente en cache (aucun téléchargement supplémentaire).
#
# Mesures réelles (archive lean-4.34.0-linux.tar.zst, 2026-10-08) :
# - "std"  : `import Std` seul — 489 modules Std, 0 module Lean
#            (fermeture vérifiée par analyse des sources src/lean +
#             test `lean` empirique) : 1 467 fichiers, 304 056 336 octets.
# - "lean" : `import Lean` — 1 217 modules Lean + 489 modules Std
#            (la fermeture de `import Lean` inclut tout Std) :
#            5 121 fichiers, 1 292 721 440 octets.
#
# Résultats d'élagage (tests empiriques) :
# - les 3 variantes (.olean, .olean.private, .olean.server) sont REQUISES
#   (lean échoue avec "failed to open file" si l'une manque) ;
# - aucun sous-arbre élagable pour `import Lean` (ex. sans Lean/Server/**
#   l'import échoue) ; 2 modules orphelins non importés par quiconque
#   (Lean.Elab.ErrorUtils, Lean.PrettyPrinter.Delaborator.DeclWithSig,
#   ~0,2 Mo) — non retirés : gain négligeable, risque inutile ;
# - pas de "mini Lean" utile : `import Lean.Elab.Tactic` seul exige déjà
#   904 modules Lean + 489 Std = 963 Mo (78 % du complet).
_VARIANTES_OLEAN = [".olean", ".olean.private", ".olean.server"]


def _motifs_bibliotheque(racine_module):
    """Motifs des 3 variantes olean pour un module racine et son arbre.

    Ex. _motifs_bibliotheque("Std") couvre lib/lean/Std.olean* et
    lib/lean/Std/**.olean*. Les fichiers .ir/.ir.sig/.ilean ne sont
    jamais lus par `lean` au runtime (spec TOOLCHAIN_MINI_SPEC.md §3).
    """
    motifs = []
    for variante in _VARIANTES_OLEAN:
        motifs.append("lib/lean/%s%s" % (racine_module, variante))
        motifs.append("lib/lean/%s/*%s" % (racine_module, variante))
        motifs.append("lib/lean/%s/**/*%s" % (racine_module, variante))
    return motifs


MOTIFS_EXTENSION_STD = _motifs_bibliotheque("Std")

# "lean" inclut "std" : la fermeture de `import Lean` contient les 489
# modules Std (vérifié par analyse des imports de src/lean).
MOTIFS_EXTENSION_LEAN = _motifs_bibliotheque("Lean") + MOTIFS_EXTENSION_STD


def _sans_racine(chemin):
    """Retire le premier composant ('lean-4.34.0-linux/bin/lean' -> 'bin/lean')."""
    parties = chemin.split("/", 1)
    return parties[1] if len(parties) == 2 else ""


def _est_retenu(chemin_relatif, motifs=None):
    motifs = motifs or MOTIFS_MINIMAUX
    return any(fnmatch.fnmatch(chemin_relatif, m) for m in motifs)


def _dans_dest(dest_dir, chemin):
    """True ssi `chemin` (déjà normalisé) reste sous `dest_dir`."""
    return os.path.commonpath(
        [os.path.abspath(dest_dir), os.path.abspath(chemin)]
    ) == os.path.abspath(dest_dir)


def _lien_sain(cible_lien, dest_dir, chemin_lien):
    """Vérifie qu'un lien symbolique ne sort pas de `dest_dir`.

    Les liens absolus sont refusés ; les liens relatifs sont résolus
    depuis le répertoire parent du lien puis contrôlés.
    """
    if os.path.isabs(cible_lien):
        return False
    resolu = os.path.normpath(
        os.path.join(os.path.dirname(chemin_lien), cible_lien)
    )
    return _dans_dest(dest_dir, resolu)


def extraire_minimal(archive_tar_zst, dest_dir, motifs=None, progression=None):
    """Extrait le sous-ensemble minimal d'un .tar.zst vers `dest_dir`.

    Args:
        archive_tar_zst: chemin du fichier .tar.zst (déjà vérifié).
        dest_dir: répertoire de destination (créé si absent).
        motifs: liste de motifs fnmatch (défaut : MOTIFS_MINIMAUX).
        progression: callable optionnel(fichiers_extraits, octets_extraits).

    Returns:
        dict: {"dest": dest_dir, "fichiers": n, "octets": n}.

    Raises:
        ErreurExtraction: décompression ou lecture impossible.
    """
    motifs = motifs or MOTIFS_MINIMAUX
    os.makedirs(dest_dir, exist_ok=True)
    n_fichiers = 0
    n_octets = 0
    try:
        with open(archive_tar_zst, "rb") as fzst:
            dctx = zstandard.ZstdDecompressor()
            flux = dctx.stream_reader(fzst)
            with tarfile.open(fileobj=flux, mode="r|") as tar:
                for membre in tar:
                    relatif = _sans_racine(membre.name)
                    if not relatif or not _est_retenu(relatif, motifs):
                        continue
                    cible = os.path.join(dest_dir, relatif)
                    # Garde : aucun chemin ne doit sortir de dest_dir.
                    if not _dans_dest(dest_dir, cible):
                        continue
                    os.makedirs(os.path.dirname(cible), exist_ok=True)
                    if membre.issym():
                        # Lien symbolique (ex. lib/libc++.so.1 ->
                        # libc++.so.1.0, requis par clang/ld.lld).
                        if not _lien_sain(membre.linkname, dest_dir, cible):
                            continue
                        if os.path.lexists(cible):
                            os.unlink(cible)
                        os.symlink(membre.linkname, cible)
                        n_fichiers += 1
                        if progression is not None:
                            progression(n_fichiers, n_octets)
                        continue
                    if not membre.isfile():
                        continue
                    extrait = tar.extractfile(membre)
                    if extrait is None:
                        continue
                    with open(cible, "wb") as fout:
                        while True:
                            bloc = extrait.read(1024 * 1024)
                            if not bloc:
                                break
                            fout.write(bloc)
                            n_octets += len(bloc)
                    # Préserve le bit exécutable (bin/lean).
                    if membre.mode & 0o111:
                        st = os.stat(cible)
                        os.chmod(cible, st.st_mode | 0o111)
                    n_fichiers += 1
                    if progression is not None:
                        progression(n_fichiers, n_octets)
    except Exception as exc:
        raise ErreurExtraction(
            "extraction impossible de %s : %s" % (archive_tar_zst, exc)
        ) from exc

    if n_fichiers == 0:
        raise ErreurExtraction(
            "aucun fichier du minimal trouvé dans %s "
            "(motifs : %d)" % (archive_tar_zst, len(motifs))
        )
    return {"dest": dest_dir, "fichiers": n_fichiers, "octets": n_octets}
