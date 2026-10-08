#!/usr/bin/env python3
"""Extraction sélective du sous-ensemble minimal depuis le tar.zst officiel.

On télécharge la release officielle complète (SHA256 vérifié en amont),
mais on n'en extrait QUE les fichiers du minimal (spec TOOLCHAIN_MINI_SPEC.md
§6) : bin/lean, les 4 .so, et Init/*.olean(+.private, +.server).

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
]


def _sans_racine(chemin):
    """Retire le premier composant ('lean-4.34.0-linux/bin/lean' -> 'bin/lean')."""
    parties = chemin.split("/", 1)
    return parties[1] if len(parties) == 2 else ""


def _est_retenu(chemin_relatif, motifs=None):
    motifs = motifs or MOTIFS_MINIMAUX
    return any(fnmatch.fnmatch(chemin_relatif, m) for m in motifs)


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
                    if not membre.isfile():
                        continue
                    relatif = _sans_racine(membre.name)
                    if not relatif or not _est_retenu(relatif, motifs):
                        continue
                    cible = os.path.join(dest_dir, relatif)
                    # Garde : aucun chemin ne doit sortir de dest_dir.
                    if os.path.commonpath(
                        [os.path.abspath(dest_dir), os.path.abspath(cible)]
                    ) != os.path.abspath(dest_dir):
                        continue
                    os.makedirs(os.path.dirname(cible), exist_ok=True)
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
