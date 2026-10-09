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
import resource
import struct
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

# ---------------------------------------------------------------------------
# Limites mémoire (streaming) — directive Tomy 2026-10-08 :
# le compresseur ne doit jamais charger tout en RAM (cause suspectée des reboots).
# ---------------------------------------------------------------------------
# Seuil RSS au-delà duquel on réduit la taille des lots (Mo)
RSS_SOFT_LIMIT_MB = 800
# Taille cible d'un lot de lecture (octets de données brutes)
BATCH_TARGET_BYTES = 200 * 1024 * 1024
# Bornes sur le nombre de fichiers par lot
BATCH_MAX_FILES = 2000
BATCH_MIN_FILES = 100


def _rss_mb() -> float:
    """Mémoire résidente actuelle du processus, en Mo."""
    # ru_maxrss est en kilo-octets sur Linux
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

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

    def _adapt_batch(self, batch_files: int, batch_bytes: int) -> tuple[int, int]:
        """Réduit la taille des lots si la RAM approche la limite.

        Retourne (max_files, max_bytes) ajustés.
        """
        rss = _rss_mb()
        if rss > RSS_SOFT_LIMIT_MB:
            # Diviser par deux, avec planchers
            batch_files = max(BATCH_MIN_FILES, batch_files // 2)
            batch_bytes = max(50 * 1024 * 1024, batch_bytes // 2)
        return batch_files, batch_bytes

    def _batches(self, files: list[Path]) -> Iterator[list[Path]]:
        """Découpe la liste triée de fichiers en lots bornés en mémoire.

        La taille des lots s'adapte si la RAM approche la limite.
        """
        max_files = BATCH_MAX_FILES
        max_bytes = BATCH_TARGET_BYTES
        i = 0
        n = len(files)
        while i < n:
            max_files, max_bytes = self._adapt_batch(max_files, max_bytes)
            # Estimer la taille du lot sans lire les fichiers :
            # on accumule les tailles via stat() (pas cher, pas de données en RAM)
            batch = []
            batch_bytes = 0
            while i < n and len(batch) < max_files and batch_bytes < max_bytes:
                f = files[i]
                try:
                    sz = f.stat().st_size
                except OSError:
                    sz = 0
                # Un fichier seul plus gros que le lot → lot d'un seul fichier
                if batch and batch_bytes + sz > max_bytes:
                    break
                batch.append(f)
                batch_bytes += sz
                i += 1
            # Garde-fou : toujours progresser
            if not batch:
                batch.append(files[i])
                i += 1
            yield batch

    def _read_batch(self, batch: list[Path], source: Path) -> list[tuple[str, bytes, bytes]]:
        """Lit un lot de fichiers en parallèle. Retourne [(rel, data, sha)]."""
        results: dict[int, tuple[str, bytes, bytes]] = {}
        with ThreadPoolExecutor(max_workers=self.jobs) as ex:
            futs = {ex.submit(self._read_one, f, source): idx
                    for idx, f in enumerate(batch)}
            for fut in as_completed(futs):
                results[futs[fut]] = fut.result()
        # Remettre dans l'ordre du lot (qui est trié globalement)
        return [results[idx] for idx in range(len(batch))]

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
        """Streaming : lit/compresse/écrit par lots, ne garde que l'index en RAM."""
        total = len(files)
        # Index : (rel, orig_size, comp_size, data_offset, sha) — ~100 o/fichier
        index_rows: list[tuple[str, int, int, int, bytes]] = []

        original_total = 0
        compressed_total = 0
        done = 0

        with open(self.output, "wb") as out:
            out.write(b"\x00" * HEADER_SIZE)
            data_offset = 0

            for batch in self._batches(files):
                # Lecture parallèle du lot
                batch_data = self._read_batch(batch, source)

                # Compression parallèle du lot
                def _comp(item):
                    rel, data, sha = item
                    return rel, data, sha, _compress(data, self.preset)

                comp_results: dict[int, tuple] = {}
                with ThreadPoolExecutor(max_workers=self.jobs) as ex:
                    futs = {ex.submit(_comp, item): idx
                            for idx, item in enumerate(batch_data)}
                    for fut in as_completed(futs):
                        comp_results[futs[fut]] = fut.result()
                ordered = [comp_results[idx] for idx in range(len(batch_data))]
                # Libérer les données brutes du lot dès que possible
                del batch_data, comp_results

                # Écriture immédiate (dans l'ordre trié du lot)
                for rel, data, sha, comp in ordered:
                    entry_offset = data_offset
                    out.write(struct.pack("<Q", len(comp)))
                    out.write(comp)
                    data_offset += 8 + len(comp)
                    original_total += len(data)
                    compressed_total += len(comp)
                    index_rows.append((rel, len(data), len(comp), entry_offset, sha))
                    done += 1
                    self._report(done, total, rel)
                del ordered

            # Index final (déjà trié car les lots suivent l'ordre global trié)
            index_offset = out.tell()
            index_buf = bytearray()
            for rel, osize, csize, doff, sha in index_rows:
                pb = rel.encode("utf-8")
                index_buf.extend(struct.pack(
                    INDEX_ENTRY_FMT, len(pb), osize, csize, doff, sha))
                index_buf.extend(pb)
            out.write(index_buf)
            index_size = len(index_buf)
            del index_rows, index_buf

            out.seek(0)
            out.write(struct.pack(HEADER_FMT, PHIZ_MAGIC, PHIZ_VERSION, 0,
                                  total, index_offset, index_size,
                                  b"\x00" * 32))

        return {
            "files": total,
            "original_bytes": original_total,
            "compressed_bytes": compressed_total + HEADER_SIZE + index_size,
            "ratio_pct": compressed_total / original_total * 100 if original_total else 0,
            "mode": "per-file",
        }

    # -- mode solide ---------------------------------------------------------
    def _write_solid(self, source: Path, files: list[Path]) -> dict:
        """Streaming : lots de lecture -> blocs écrits au fur et à mesure.

        Mémoire bornée par : un lot (~200 Mo max) + un bloc (~10 Mo) + l'index.
        Ne charge JAMAIS l'intégralité des données en RAM.
        """
        total = len(files)

        # Index solide : (rel, size, sha, block_idx, offset_in_block) — petit
        all_entries: list[tuple[str, int, bytes, int, int]] = []

        original_total = 0
        compressed_total = 0
        nblocks = 0
        done = 0

        # Bloc courant (reporté entre les lots)
        cur: list[tuple[str, bytes, bytes]] = []
        cur_size = 0

        def _build_and_write_block(bfiles: list[tuple[str, bytes, bytes]],
                                   out, bidx: int) -> int:
            """Construit le buffer framé, compresse, écrit. Retourne la taille
            compressée écrite (incluant le préfixe <Q). Remplit all_entries."""
            buf = bytearray()
            offsets = []
            for rel, data, sha in bfiles:
                pb = rel.encode("utf-8")
                offsets.append(len(buf))
                buf.extend(struct.pack("<H", len(pb)))
                buf.extend(pb)
                buf.extend(struct.pack("<Q32sQ", len(data), sha, len(data)))
                buf.extend(data)
            comp = _compress(bytes(buf), self.preset)
            del buf  # libérer le buffer non compressé au plus tôt
            out.write(struct.pack("<Q", len(comp)))
            out.write(comp)
            for (rel, data, sha), off in zip(bfiles, offsets):
                all_entries.append((rel, len(data), sha, bidx, off))
            return 8 + len(comp)

        with open(self.output, "wb") as out:
            out.write(b"\x00" * HEADER_SIZE)

            for batch in self._batches(files):
                batch_data = self._read_batch(batch, source)

                for rel, data, sha in batch_data:
                    # Même logique de découpage que l'original
                    if cur and cur_size + len(data) > self.solid_block_size:
                        written = _build_and_write_block(cur, out, nblocks)
                        compressed_total += written - 8  # sans le préfixe <Q>
                        nblocks += 1
                        done_blk = nblocks
                        self._report(done + len(cur), total,
                                     f"bloc {done_blk}")
                        cur, cur_size = [], 0
                    # Un fichier seul plus gros que le bloc → son propre bloc
                    if not cur and len(data) > self.solid_block_size:
                        written = _build_and_write_block([(rel, data, sha)],
                                                         out, nblocks)
                        compressed_total += written - 8
                        nblocks += 1
                        done += 1
                        original_total += len(data)
                        self._report(done, total, f"bloc {nblocks} (gros fichier)")
                        continue
                    cur.append((rel, data, sha))
                    cur_size += len(data)
                    original_total += len(data)
                    done += 1

                # Libérer le lot lu (les données restantes vivent dans `cur`)
                del batch_data
                # Progression sur les fichiers traités
                self._report(done, total, f"traitement {done}/{total}")

            # Vider le dernier bloc
            if cur:
                written = _build_and_write_block(cur, out, nblocks)
                compressed_total += written - 8
                nblocks += 1
                cur = []

            # Index final — déjà trié car traitement dans l'ordre global trié
            index_offset = out.tell()
            index_buf = bytearray()
            for rel, fsize, sha, bidx, off in all_entries:
                pb = rel.encode("utf-8")
                index_buf.extend(struct.pack("<H", len(pb)))
                index_buf.extend(pb)
                index_buf.extend(struct.pack("<Q32sIQ", fsize, sha, bidx, off))
            out.write(index_buf)
            index_size = len(index_buf)
            del all_entries, index_buf

            out.seek(0)
            out.write(struct.pack(HEADER_FMT, PHIZ_MAGIC, PHIZ_VERSION,
                                  FLAG_SOLID, total,
                                  index_offset, index_size, b"\x00" * 32))

        return {
            "files": total,
            "original_bytes": original_total,
            "compressed_bytes": compressed_total + HEADER_SIZE + index_size,
            "ratio_pct": compressed_total / original_total * 100 if original_total else 0,
            "mode": "solid",
            "blocks": nblocks,
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
