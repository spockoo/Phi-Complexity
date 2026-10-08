#!/usr/bin/env python3
"""
phi-compress : Compresseur d'artefacts Lean pour Phi-Complexity.

Format .phiz : archive indexée avec compression XZ.

RÉSULTAT HONNÊTE (2026-10-08) : L'algorithme XZ n'est pas battu sur les oleans.
Mesures sur 8530 fichiers réels :
  XZ -6 : 27.9% par fichier (MEILLEUR algo)
  zstd -19 : 30.7% | gzip -9 : 34.6% | bzip2 -9 : 33.2%
  Pré-traitement + XZ : 31.3% (PIRE — le pré-traitement ajoute du surcoût)

En revanche, le MODE SOLIDE (blocs de 10 Mo compressés comme un seul flux)
atteint ~19.7%, soit 17% de mieux que le per-file, au niveau de tar.xz,
tout en gardant l'index et l'intégrité SHA256 par fichier.

Modes :
  per-file : chaque fichier compressé séparément. Accès aléatoire fin.
  solid    : fichiers groupés en blocs de N Mo, chaque bloc = un flux XZ.
             Meilleur ratio. Accès aléatoire au niveau du bloc.
"""

import hashlib
import lzma
import os
import struct
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Format .phiz v1
# ---------------------------------------------------------------------------
PHIZ_MAGIC = b"PHIZ"
PHIZ_VERSION = 1
HEADER_FMT = "<4sHHQQQ32s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 64

# Index per-file : path_len(H) + path + orig_size(Q) + comp_size(Q) + data_offset(Q) + sha256(32s)
INDEX_ENTRY_FMT = "<HQQL32s"

# Index solide : path_len(H) + path + orig_size(Q) + sha256(32s) + block_idx(I) + offset_in_block(Q)
SOLID_INDEX_FMT = "<HQ32sIQ"
SOLID_INDEX_FIXED = struct.calcsize("<HQ32sIQ")  # sans le H+path

# Flags
FLAG_SOLID = 0x0002

# Framing intra-bloc : [path_len:2][path][orig_size:8][sha256:32][data_len:8][data]
BLOCK_REC_HEADER = "<HQ32sQ"  # path_len déjà écrit séparément


@dataclass
class PhizEntry:
    path: str
    original_size: int
    compressed_size: int   # per-file : taille compressée ; solide : taille du bloc
    data_offset: int       # per-file : offset du fichier ; solide : offset du bloc
    sha256: bytes
    # Champs solides (None en per-file)
    block_idx: Optional[int] = None
    offset_in_block: Optional[int] = None


def _compress(data: bytes, preset: int = 6) -> bytes:
    return lzma.compress(data, preset=preset)


def _decompress(data: bytes) -> bytes:
    return lzma.decompress(data)


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def collect_files(source: Path) -> list[Path]:
    files = []
    for root, _, filenames in os.walk(source):
        for fn in filenames:
            files.append(Path(root) / fn)
    return sorted(files)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------
class PhizWriter:
    def __init__(self, output: Path, preset: int = 6, jobs: int = 4,
                 progress_cb: Optional[Callable[[int, int, str], None]] = None,
                 solid_block_size: int = 10 * 1024 * 1024):
        """
        Args:
            solid_block_size: 0 = per-file ; >0 = blocs solides de cette taille.
                Défaut 10 Mo : bon compromis ratio / granularité d'accès.
        """
        self.output = Path(output)
        self.preset = preset
        self.jobs = jobs
        self.progress_cb = progress_cb
        self.solid_block_size = solid_block_size

    # -- utilitaires -------------------------------------------------------
    def _read_one(self, fpath: Path, source: Path):
        rel = str(fpath.relative_to(source))
        with open(fpath, "rb") as f:
            data = f.read()
        return rel, data, _sha256(data)

    def _report(self, done: int, total: int, name: str = ""):
        if self.progress_cb:
            self.progress_cb(done, total, name)

    # -- entrée principale --------------------------------------------------
    def write(self, source: Path) -> dict:
        source = Path(source)
        files = collect_files(source)
        if not files:
            raise ValueError(f"Aucun fichier dans {source}")
        if self.solid_block_size > 0:
            return self._write_solid(source, files)
        return self._write_per_file(source, files)

    # -- mode per-file ------------------------------------------------------
    def _write_per_file(self, source: Path, files: list[Path]) -> dict:
        total = len(files)
        results: list[tuple[str, bytes, bytes, bytes]] = []  # rel, data, sha, compressed

        def _work(fpath: Path):
            rel, data, sha = self._read_one(fpath, source)
            return rel, data, sha, _compress(data, self.preset)

        done = 0
        with ThreadPoolExecutor(max_workers=self.jobs) as ex:
            futs = {ex.submit(_work, f): f for f in files}
            for fut in as_completed(futs):
                results.append(fut.result())
                done += 1
                self._report(done, total, results[-1][0])

        results.sort(key=lambda x: x[0])

        original_total = 0
        compressed_total = 0
        with open(self.output, "wb") as out:
            out.write(b"\x00" * HEADER_SIZE)
            data_offset = 0
            index_buf = bytearray()
            for rel, data, sha, comp in results:
                entry_offset = data_offset
                out.write(struct.pack("<Q", len(comp)))
                out.write(comp)
                data_offset += 8 + len(comp)
                original_total += len(data)
                compressed_total += len(comp)

                pb = rel.encode("utf-8")
                index_buf.extend(struct.pack(
                    INDEX_ENTRY_FMT, len(pb), len(data), len(comp),
                    entry_offset, sha))
                index_buf.extend(pb)

            index_offset = out.tell()
            out.write(index_buf)
            index_size = len(index_buf)
            out.seek(0)
            out.write(struct.pack(HEADER_FMT, PHIZ_MAGIC, PHIZ_VERSION, 0,
                                  len(results), index_offset, index_size,
                                  b"\x00" * 32))

        return {
            "files": len(results),
            "original_bytes": original_total,
            "compressed_bytes": compressed_total + HEADER_SIZE + index_size,
            "ratio_pct": compressed_total / original_total * 100 if original_total else 0,
            "mode": "per-file",
        }

    # -- mode solide ---------------------------------------------------------
    def _write_solid(self, source: Path, files: list[Path]) -> dict:
        total = len(files)

        # 1. Lecture parallèle
        file_data: list[tuple[str, bytes, bytes]] = []
        done = 0
        with ThreadPoolExecutor(max_workers=self.jobs) as ex:
            futs = {ex.submit(self._read_one, f, source): f for f in files}
            for fut in as_completed(futs):
                file_data.append(fut.result())
                done += 1
                self._report(done, total * 2, "lecture")
        file_data.sort(key=lambda x: x[0])

        # 2. Découpage en blocs
        blocks: list[list[tuple[str, bytes, bytes]]] = []
        cur: list[tuple[str, bytes, bytes]] = []
        cur_size = 0
        for rel, data, sha in file_data:
            if cur and cur_size + len(data) > self.solid_block_size:
                blocks.append(cur)
                cur, cur_size = [], 0
            # Un fichier seul plus gros que le bloc → son propre bloc
            if not cur and len(data) > self.solid_block_size:
                blocks.append([(rel, data, sha)])
                continue
            cur.append((rel, data, sha))
            cur_size += len(data)
        if cur:
            blocks.append(cur)

        # 3. Compression des blocs (parallèle)
        # Chaque bloc : concaténation framée [path_len:2][path][orig:8][sha:32][len:8][data]
        def _compress_block(bfiles):
            buf = bytearray()
            # offsets intra-bloc (dans le buffer décompressé)
            offsets = []
            for rel, data, sha in bfiles:
                pb = rel.encode("utf-8")
                offsets.append(len(buf))
                buf.extend(struct.pack("<H", len(pb)))
                buf.extend(pb)
                buf.extend(struct.pack("<Q32sQ", len(data), sha, len(data)))
                buf.extend(data)
            comp = _compress(bytes(buf), self.preset)
            orig = len(buf)
            # entrées : (rel, orig_size, sha, offset_in_block)
            entries = [(rel, len(data), sha, off)
                       for (rel, data, sha), off in zip(bfiles, offsets)]
            return comp, entries, orig

        block_out: list[tuple[bytes, list, int]] = []
        done = 0
        with ThreadPoolExecutor(max_workers=self.jobs) as ex:
            futs = [ex.submit(_compress_block, b) for b in blocks]
            # Garder l'ordre des blocs : on récupère dans l'ordre de soumission
            # via un mapping
            fut_list = list(futs)
            for i, fut in enumerate(fut_list):
                block_out.append(fut.result())
                done += 1
                self._report(total + done, total + len(blocks),
                             f"bloc {done}/{len(blocks)}")

        # 4. Écriture
        original_total = 0
        compressed_total = 0
        all_entries: list[tuple[str, int, bytes, int, int]] = []  # rel, size, sha, bidx, off
        with open(self.output, "wb") as out:
            out.write(b"\x00" * HEADER_SIZE)
            data_offset = 0
            for bidx, (comp, entries, orig) in enumerate(block_out):
                out.write(struct.pack("<Q", len(comp)))
                out.write(comp)
                for rel, fsize, sha, off in entries:
                    all_entries.append((rel, fsize, sha, bidx, off))
                # data_offset du bloc (pour référence)
                data_offset += 8 + len(comp)
                compressed_total += len(comp)
                original_total += orig  # inclut le framing (négligeable)

            # Correction : original_total doit être la somme des fichiers, pas du framing
            original_total = sum(e[1] for e in all_entries)

            all_entries.sort(key=lambda x: x[0])
            index_offset = out.tell()
            index_buf = bytearray()
            for rel, fsize, sha, bidx, off in all_entries:
                pb = rel.encode("utf-8")
                index_buf.extend(struct.pack("<H", len(pb)))
                index_buf.extend(pb)
                index_buf.extend(struct.pack("<Q32sIQ", fsize, sha, bidx, off))
            out.write(index_buf)
            index_size = len(index_buf)

            out.seek(0)
            out.write(struct.pack(HEADER_FMT, PHIZ_MAGIC, PHIZ_VERSION,
                                  FLAG_SOLID, len(all_entries),
                                  index_offset, index_size, b"\x00" * 32))

        return {
            "files": len(all_entries),
            "original_bytes": original_total,
            "compressed_bytes": compressed_total + HEADER_SIZE + index_size,
            "ratio_pct": compressed_total / original_total * 100 if original_total else 0,
            "mode": "solid",
            "blocks": len(block_out),
        }


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------
class PhizReader:
    def __init__(self, archive: Path):
        self.archive = Path(archive)
        self.solid = False
        self.entries: list[PhizEntry] = []
        self._block_cache: dict[int, bytes] = {}  # block_idx -> decompressed
        self._load()

    def _load(self):
        with open(self.archive, "rb") as f:
            hdr = f.read(HEADER_SIZE)
            magic, version, flags, nfiles, idx_off, idx_size, _ = struct.unpack(
                HEADER_FMT, hdr)
            if magic != PHIZ_MAGIC:
                raise ValueError(f"Pas une archive .phiz (magic={magic!r})")
            if version != PHIZ_VERSION:
                raise ValueError(f"Version .phiz non supportée : {version}")
            self.solid = bool(flags & FLAG_SOLID)
            f.seek(idx_off)
            idx = f.read(idx_size)

        off = 0
        if not self.solid:
            fixed = struct.calcsize(INDEX_ENTRY_FMT)
            for _ in range(nfiles):
                plen, osize, csize, doff, sha = struct.unpack(
                    INDEX_ENTRY_FMT, idx[off:off + fixed])
                off += fixed
                path = idx[off:off + plen].decode("utf-8")
                off += plen
                self.entries.append(PhizEntry(path, osize, csize, doff, sha))
        else:
            fixed = struct.calcsize("<Q32sIQ")
            for _ in range(nfiles):
                plen = struct.unpack("<H", idx[off:off + 2])[0]
                off += 2
                path = idx[off:off + plen].decode("utf-8")
                off += plen
                osize, sha, bidx, boff = struct.unpack(
                    "<Q32sIQ", idx[off:off + fixed])
                off += fixed
                self.entries.append(PhizEntry(
                    path, osize, 0, 0, sha,
                    block_idx=bidx, offset_in_block=boff))

    def list_files(self) -> list[str]:
        return [e.path for e in self.entries]

    def _read_block(self, bidx: int) -> bytes:
        """Lit et décompresse un bloc (avec cache)."""
        if bidx in self._block_cache:
            return self._block_cache[bidx]
        # Trouver l'offset du bloc : on doit scanner les blocs précédents.
        # Pour éviter un index séparé, on lit séquentiellement depuis le début.
        with open(self.archive, "rb") as f:
            f.seek(HEADER_SIZE)
            for i in range(bidx + 1):
                (csize,) = struct.unpack("<Q", f.read(8))
                cdata = f.read(csize)
                if i == bidx:
                    raw = _decompress(cdata)
                    # Cache borné : garder au plus 4 blocs (~40 Mo)
                    if len(self._block_cache) >= 4:
                        self._block_cache.pop(next(iter(self._block_cache)))
                    self._block_cache[bidx] = raw
                    return raw
        raise IndexError(f"Bloc {bidx} introuvable")

    def extract_file(self, path: str) -> bytes:
        entry = next((e for e in self.entries if e.path == path), None)
        if entry is None:
            raise KeyError(f"Fichier absent de l'archive : {path}")

        if not self.solid:
            with open(self.archive, "rb") as f:
                f.seek(HEADER_SIZE + entry.data_offset)
                (csize,) = struct.unpack("<Q", f.read(8))
                data = _decompress(f.read(csize))
        else:
            raw = self._read_block(entry.block_idx)
            off = entry.offset_in_block
            plen = struct.unpack("<H", raw[off:off + 2])[0]
            off += 2
            # path = raw[off:off+plen]  # déjà connu
            off += plen
            osize, sha, dlen = struct.unpack("<Q32sQ", raw[off:off + 48])
            off += 48
            data = raw[off:off + dlen]
            if len(data) != entry.original_size:
                raise ValueError(f"Taille incohérente pour {path}")

        if _sha256(data) != entry.sha256:
            raise ValueError(f"SHA256 invalide pour {path}")
        return data

    def extract_all(self, dest: Path,
                    progress_cb: Optional[Callable[[int, int, str], None]] = None,
                    jobs: int = 4):
        dest = Path(dest)
        total = len(self.entries)

        def _one(entry: PhizEntry):
            data = self.extract_file(entry.path)
            out = dest / entry.path
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, out)
            return entry.path

        # En mode solide, extraction séquentielle (le cache de blocs aide)
        # En per-file, parallèle.
        if self.solid:
            for i, e in enumerate(self.entries):
                p = _one(e)
                if progress_cb:
                    progress_cb(i + 1, total, p)
        else:
            done = 0
            with ThreadPoolExecutor(max_workers=jobs) as ex:
                futs = {ex.submit(_one, e): e for e in self.entries}
                for fut in as_completed(futs):
                    p = fut.result()
                    done += 1
                    if progress_cb:
                        progress_cb(done, total, p)


# ---------------------------------------------------------------------------
# Découpage / réassemblage (règle Tomy : morceaux ~10 Mo)
# ---------------------------------------------------------------------------
def split_file(path: Path, chunk_size: int, output_dir: Path,
               progress_cb: Optional[Callable[[int, int], None]] = None) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = Path(path)

    size = path.stat().st_size
    nparts = (size + chunk_size - 1) // chunk_size
    parts = []
    with open(path, "rb") as f:
        for i in range(nparts):
            pname = f"{path.name}.part{i:03d}"
            ppath = output_dir / pname
            with open(ppath, "wb") as out:
                remaining = chunk_size
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            parts.append(ppath)
            if progress_cb:
                progress_cb(i + 1, nparts)

    # Manifeste avec SHA256
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    manifest = output_dir / f"{path.name}.manifest"
    with open(manifest, "w") as mf:
        mf.write(f"original={path.name}\nparts={nparts}\nchunk_size={chunk_size}\n")
        mf.write(f"total_size={size}\nsha256={h.hexdigest()}\n")
        for p in parts:
            ph = hashlib.sha256()
            with open(p, "rb") as pf:
                while chunk := pf.read(65536):
                    ph.update(chunk)
            mf.write(f"part={p.name} sha256={ph.hexdigest()} size={p.stat().st_size}\n")
    return parts


def join_parts(manifest_path: Path, output: Path,
               progress_cb: Optional[Callable[[int, int], None]] = None):
    manifest_path = Path(manifest_path)
    base = manifest_path.parent
    parts, expected = [], None
    with open(manifest_path) as mf:
        for line in mf:
            line = line.strip()
            if line.startswith("part="):
                parts.append(base / line.split()[0].split("=", 1)[1])
            elif line.startswith("sha256=") and "=" in line and not line.startswith("part"):
                # sha256=... (ligne globale, pas part=... sha256=...)
                if expected is None:
                    expected = line.split("=", 1)[1]
    # La ligne globale sha256= est la 5e ; les part= ont aussi sha256=
    # On relit proprement :
    expected = None
    with open(manifest_path) as mf:
        for line in mf:
            if line.startswith("sha256="):
                expected = line.strip().split("=", 1)[1]
                break

    with open(output, "wb") as out:
        for i, p in enumerate(parts):
            with open(p, "rb") as pf:
                while chunk := pf.read(65536):
                    out.write(chunk)
            if progress_cb:
                progress_cb(i + 1, len(parts))

    if expected:
        h = hashlib.sha256()
        with open(output, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        if h.hexdigest() != expected:
            raise ValueError("SHA256 invalide après réassemblage")
