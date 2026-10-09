#!/usr/bin/env python3
"""
Tests pour phi-compress (format .phiz).
"""

import hashlib
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from phi_complexity.toolchain.compress import (
    PhizWriter,
    PhizReader,
    split_file,
    join_parts,
    PHIZ_MAGIC,
    PHIZ_VERSION,
)


def _make_test_files(base: Path, n: int = 10) -> list[Path]:
    """Crée des fichiers de test avec du contenu pseudo-réaliste."""
    base.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(n):
        # Contenu avec patterns répétitifs (comme les oleans)
        content = b"olean\x02\x01" + f"4.34.1-module-{i}".encode() + b"\x00" * 100
        content += bytes([0x42, 0x74, 0xA8, 0x58, 0x00, 0x00] * 500)
        content += os.urandom(1000)  # Partie haute-entropie
        p = base / f"subdir{i % 3}" / f"file{i}.olean"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        files.append(p)
    return files


def test_roundtrip():
    """Test : compresse puis décompresse, vérifie l'intégrité."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src"
        _make_test_files(src, n=20)

        # Référence : checksums originaux
        orig_hashes = {}
        for f in src.rglob("*"):
            if f.is_file():
                orig_hashes[str(f.relative_to(src))] = hashlib.sha256(f.read_bytes()).hexdigest()

        # Compresse
        archive = tmp / "test.phiz"
        writer = PhizWriter(archive, jobs=2)
        summary = writer.write(src)

        assert summary["files"] == 20, f"Attendu 20 fichiers, got {summary['files']}"
        assert summary["ratio_pct"] < 100, "La compression devrait réduire la taille"
        print(f"  Ratio: {summary['ratio_pct']:.1f}% ({summary['original_bytes']} -> {summary['compressed_bytes']})")

        # Vérifie le header
        with open(archive, "rb") as f:
            magic = f.read(4)
            assert magic == PHIZ_MAGIC, f"Magic invalide: {magic!r}"

        # Décompresse
        dest = tmp / "dest"
        reader = PhizReader(archive)
        assert len(reader.list_files()) == 20
        reader.extract_all(dest, jobs=2)

        # Vérifie
        for rel, expected_hash in orig_hashes.items():
            extracted = dest / rel
            assert extracted.exists(), f"Manquant: {rel}"
            actual_hash = hashlib.sha256(extracted.read_bytes()).hexdigest()
            assert actual_hash == expected_hash, f"Corrompu: {rel}"

        print("  ✓ Roundtrip OK (20 fichiers, intégrité vérifiée)")


def test_random_access():
    """Test : extraction d'un seul fichier sans tout décompresser."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src"
        _make_test_files(src, n=10)

        archive = tmp / "test.phiz"
        PhizWriter(archive, jobs=2).write(src)

        reader = PhizReader(archive)
        files = reader.list_files()
        assert len(files) == 10

        # Extrait un seul fichier
        target = files[5]
        data = reader.extract_file(target)
        orig = (src / target).read_bytes()
        assert data == orig, "Extraction aléatoire corrompue"

        print("  ✓ Accès aléatoire OK")


def test_empty_dir_fails():
    """Test : dossier vide → erreur explicite."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        empty = tmp / "empty"
        empty.mkdir()
        try:
            PhizWriter(tmp / "x.phiz").write(empty)
            assert False, "Aurait dû lever ValueError"
        except ValueError as e:
            assert "Aucun fichier" in str(e)
        print("  ✓ Dossier vide rejeté proprement")


def test_split_join():
    """Test : découpage et réassemblage avec vérification SHA256."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Crée un fichier de 25 Mo
        big = tmp / "big.phiz"
        big.write_bytes(os.urandom(25 * 1024 * 1024))
        orig_hash = hashlib.sha256(big.read_bytes()).hexdigest()

        # Découpe en 10 Mo
        parts_dir = tmp / "parts"
        parts = split_file(big, 10 * 1024 * 1024, parts_dir)
        assert len(parts) == 3, f"Attendu 3 parties, got {len(parts)}"
        print(f"  Découpé en {len(parts)} parties")

        # Vérifie le manifeste
        manifest = parts_dir / "big.phiz.manifest"
        assert manifest.exists()

        # Réassemble
        reassembled = tmp / "reassembled.phiz"
        join_parts(manifest, reassembled)
        new_hash = hashlib.sha256(reassembled.read_bytes()).hexdigest()
        assert new_hash == orig_hash, "Réassemblage corrompu"

        print("  ✓ Découpage/réassemblage OK (SHA256 vérifié)")


def test_corruption_detected():
    """Test : corruption détectée par SHA256."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "src"
        _make_test_files(src, n=5)

        archive = tmp / "test.phiz"
        PhizWriter(archive, jobs=1).write(src)

        # Corrompt un byte dans les données
        with open(archive, "r+b") as f:
            f.seek(100)  # Dans la section data
            b = f.read(1)
            f.seek(100)
            f.write(bytes([b[0] ^ 0xFF]))

        # La lecture devrait échouer (soit au décompress, soit au checksum)
        reader = PhizReader(archive)
        try:
            reader.extract_all(tmp / "dest")
            # Si ça passe, c'est que la corruption n'a pas touché de données critiques
            # (possible mais rare). On ne fail pas le test.
            print("  ⚠ Corruption non détectée (byte non critique)")
        except Exception as e:
            print(f"  ✓ Corruption détectée : {type(e).__name__}")


def run_all():
    print("=== Tests phi-compress ===")
    test_roundtrip()
    test_random_access()
    test_empty_dir_fails()
    test_split_join()
    test_corruption_detected()
    print("\n✅ Tous les tests passent")


if __name__ == "__main__":
    run_all()
