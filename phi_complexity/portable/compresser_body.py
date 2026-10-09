#!/usr/bin/env python3
"""Compression réelle du body d'un .phiast v2 — axe H-F3 (mesure décisive).

Répond à la question : le body complet passe-t-il sous 500 Mo avec un
compresseur dictionnaire (zlib-9) ? Streaming par chunks INDÉPENDANTS de
256 Mo : checkpointable/reprenable (directive RÉSILIENCE), et la table des
chunks permet une décompression indexée future (compatible lazy-load).

Sorties :
  <src>.zz       : chunks zlib-9 concaténés
  <src>.zz.json : manifeste (chunks, offsets, sha256 du body, ratio)

Usage :
    python3 compresser_body.py <fichier.phiast> [--niveau 9] [--chunk-mo 256]
"""

import hashlib
import json
import os
import struct
import sys
import time
import zlib


def _pread(fd, n, offset):
    """Lecture positionnée portable (os.pread n'existe pas sur Windows)."""
    if hasattr(os, "pread"):
        return os.pread(fd, n, offset)
    # Windows : lseek + read en boucle (os.read peut rendre moins que demandé).
    os.lseek(fd, offset, os.SEEK_SET)
    chunks = []
    restant = n
    while restant > 0:
        k = os.read(fd, restant)
        if not k:
            break
        chunks.append(k)
        restant -= len(k)
    return b"".join(chunks)

CHUNK_MO_DEFAUT = 256
NIVEAU_DEFAUT = 9


def lire_manifeste(mpath):
    try:
        with open(mpath) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def compresser(src, niveau=9, chunk_mo=CHUNK_MO_DEFAUT):
    t0 = time.perf_counter()
    size = os.path.getsize(src)
    with open(src, "rb") as f:
        assert f.read(8) == b"PHIAST01"
        version = struct.unpack("<I", f.read(4))[0]
        assert version == 2
        tc_len = struct.unpack("<H", f.read(2))[0]
        body_start = 14 + tc_len + 8  # magic+version+len+toolchain+n_decls+flags
        f.seek(size - 16)
        strtab_offset = struct.unpack("<Q", f.read(8))[0]
        assert f.read(8) == b"PHIAST02"
    body_len = strtab_offset - body_start

    dst = src + ".zz"
    mpath = dst + ".json"
    man = lire_manifeste(mpath)
    if man and man.get("src_size") == size and man.get("niveau") == niveau \
            and man.get("chunk_mo") == chunk_mo:
        chunks = man["chunks"]
        print("reprise : %d/%d chunks déjà faits" % (
            len(chunks), man["n_chunks_attendus"]), flush=True)
    else:
        chunks = []
        man = None

    chunk_octets = chunk_mo * 1000000
    n_chunks = (body_len + chunk_octets - 1) // chunk_octets
    sha = hashlib.sha256()
    out_total = sum(c["out"] for c in chunks)

    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY  # Windows : mode binaire obligatoire
    fd = os.open(src, flags)
    fout = open(dst, "ab" if chunks else "wb")
    try:
        for i in range(len(chunks), n_chunks):
            debut = body_start + i * chunk_octets
            fin = min(debut + chunk_octets, body_start + body_len)
            # NOTE : pour une reprise exacte du sha256 il faudrait re-hacher
            # les chunks déjà faits ; ici on ne reprend qu'un run interrompu
            # avant la fin (le sha est recalculé en relisant tout à la fin
            # si reprise — voir ci-dessous).
            n = fin - debut
            buf = bytearray(n)
            mv = memoryview(buf)
            r = 0
            while r < n:
                k = _pread(fd, n - r, debut + r)
                if not k:
                    raise IOError(
                        "lecture tronquée à l'offset %d (chunk %d, %d/%d octets lus, "
                        "taille fichier %d)" % (debut + r, i, r, n, size)
                    )
                mv[r:r + len(k)] = k
                r += len(k)
            comp = zlib.compress(bytes(buf), niveau)
            if not chunks:
                sha.update(buf)
            fout.write(comp)
            chunks.append({"chunk": i, "in": n, "out": len(comp),
                           "offset": out_total})
            out_total += len(comp)
            dt = time.perf_counter() - t0
            print("  chunk %d/%d : %d -> %d o (ratio %.3f) — %.1f Mo/s"
                  % (i + 1, n_chunks, n, len(comp), len(comp) / n,
                     (body_start + fin - body_start) / dt / 1e6
                     if dt > 0 else 0), flush=True)
            with open(mpath + ".tmp", "w") as mf:
                json.dump({"src": src, "src_size": size, "niveau": niveau,
                           "chunk_mo": chunk_mo, "body_start": body_start,
                           "body_len": body_len,
                           "n_chunks_attendus": n_chunks,
                           "chunks": chunks}, mf)
            os.replace(mpath + ".tmp", mpath)
    finally:
        fout.close()
        os.close(fd)

    # sha256 complet du body (relecture séquentielle, une fois).
    t1 = time.perf_counter()
    sha2 = hashlib.sha256()
    with open(src, "rb") as f:
        f.seek(body_start)
        rest = body_len
        while rest:
            b = f.read(min(8 << 20, rest))
            if not b:
                break
            sha2.update(b)
            rest -= len(b)
    dt_total = time.perf_counter() - t0
    resultat = {
        "src": src,
        "niveau_zlib": niveau,
        "body_Mo": body_len / 1e6,
        "comprime_Mo": out_total / 1e6,
        "ratio": out_total / body_len,
        "cible_500Mo_atteinte": out_total / 1e6 < 500.0,
        "sha256_body": sha2.hexdigest(),
        "duree_s": dt_total,
        "n_chunks": n_chunks,
    }
    with open(mpath + ".tmp", "w") as mf:
        json.dump({"src": src, "src_size": size, "niveau": niveau,
                   "chunk_mo": chunk_mo, "body_start": body_start,
                   "body_len": body_len, "n_chunks_attendus": n_chunks,
                   "chunks": chunks, "resultat": resultat}, mf)
    os.replace(mpath + ".tmp", mpath)
    print(json.dumps(resultat, indent=2), flush=True)
    return resultat


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip().split("Usage :")[-1])
        return 2
    src = argv[1]
    niveau = NIVEAU_DEFAUT
    chunk_mo = CHUNK_MO_DEFAUT
    if "--niveau" in argv:
        niveau = int(argv[argv.index("--niveau") + 1])
    if "--chunk-mo" in argv:
        chunk_mo = int(argv[argv.index("--chunk-mo") + 1])
    compresser(src, niveau=niveau, chunk_mo=chunk_mo)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
