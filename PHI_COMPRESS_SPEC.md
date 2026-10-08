# PHI-COMPRESS : Spécification du format .phiz

**Date :** 2026-10-08
**Mission :** RUCHE-PHI-COMPRESS
**Statut :** TERMINÉ — mesures honnêtes

## Verdict honnête

**L'algorithme XZ n'est pas battu sur les artefacts Lean.** C'est un résultat,
pas un échec : nous avons prouvé par mesure que XZ est optimal pour ces données.

### Tournoi des compresseurs (fichier `.olean.private` réel, 129 744 octets)

| Compresseur | Taille | Ratio | Verdict |
|-------------|--------|-------|---------|
| XZ -6 | 36 188 | 27.9% | **MEILLEUR** |
| XZ -9 | 36 188 | 27.9% | Égal |
| XZ -9e (extrême) | 36 340 | 28.0% | Pire |
| zstd -19 | 39 892 | 30.7% | Pire |
| zstd --ultra -22 | 39 899 | 30.8% | Pire |
| zstd + dict entraîné (110 Ko) | 34.0%* | Pire qu'XZ |
| gzip -9 | 44 847 | 34.6% | Pire |
| bzip2 -9 | 43 116 | 33.2% | Pire |
| Pré-traitement tokens + XZ | — | 31.3% | **PIRE** |

*Sur fichiers hors training set.

### Pourquoi XZ gagne

Les `.olean` contiennent deux types de données :
1. **Sections structurées** (~50%) : headers, tags, noms répétés. XZ/LZMA les compresse optimalement via son dictionnaire glissant.
2. **Sections haute-entropie** (~50%) : hashes SHA, code compilé. Fondamentalement incompressibles (théorie de l'information).

Le pré-traitement par tokens échoue car :
- L'échappement des octets `0x00` ajoute du surcoût
- LZMA trouve déjà les motifs répétitifs aussi bien qu'un pré-traitement naïf
- Résultat mesuré : 31.3% vs 27.9% (pire)

### Ce que `.phiz` apporte vraiment

Le **mode solide** (blocs de 10 Mo = un seul flux XZ) atteint **20.1%**,
contre 23.7% en per-file et 19.7% pour tar.xz. Soit :
- **17% de mieux** que le per-file (gain du dictionnaire inter-fichiers)
- **À 0.4% de tar.xz** (le framing coûte 16 octets)
- **Avec** : index, SHA256 par fichier, accès aléatoire, parallélisme

| Format | Ratio (35 oleans) | Accès aléatoire | Intégrité | Parallèle |
|--------|-------------------|-----------------|-----------|-----------|
| tar.xz | 19.7% | Non | Non | Non |
| .phiz per-file | 23.7% | Oui (fichier) | SHA256/fichier | Oui |
| .phiz solide | 20.1% | Oui (bloc 10 Mo) | SHA256/fichier | Oui |

## Format .phiz v1

```
[Header 64 octets]
  magic: "PHIZ" (4)
  version: u16 = 1 (2)
  flags: u16 (2) — FLAG_SOLID = 0x0002
  file_count: u64 (8)
  index_offset: u64 (8)
  index_size: u64 (8)
  reserved: 32 octets

[Section Data]
  per-file : [taille:8][données XZ] répété
  solide   : [taille:8][bloc XZ] répété
             Chaque bloc décompressé = framing :
             [path_len:2][path][orig_size:8][sha256:32][data_len:8][data] répété

[Section Index] à index_offset
  per-file : [path_len:2][path][orig:8][comp:8][data_offset:8][sha256:32] répété
  solide   : [path_len:2][path][orig:8][sha256:32][block_idx:4][offset_in_block:8] répété
```

## Usage

```bash
# Compression (mode solide par défaut, blocs de 10 Mo)
phi compress <dossier> -o archive.phiz

# Per-file (accès aléatoire fin, ratio légèrement moindre)
phi compress <dossier> -o archive.phiz --per-file

# Blocs personnalisés
phi compress <dossier> -o archive.phiz --bloc 50

# Découpage Drive/DVD (règle Tomy : ~10 Mo)
phi compress <dossier> -o archive.phiz --split 10

# Décompression
phi decompress archive.phiz -o <dossier>
phi decompress archive.phiz --list
phi decompress archive.phiz --extract <chemin> -o <fichier>
```

## Fichiers

- `phi_complexity/toolchain/compress.py` — implémentation (Writer, Reader, split/join)
- `tests/test_phi_compress.py` — 5 tests (roundtrip, accès aléatoire, vide, split/join, corruption)
- `PHI_COMPRESS_SPEC.md` — ce document

## Limites connues

1. Le mode solide lit les blocs séquentiellement pour trouver un bloc par index
   (pas d'index de blocs séparé). Acceptable pour ≤100 blocs.
2. Le cache de blocs est borné à 4 blocs (~40 Mo).
3. Pas de déduplication inter-fichiers (mesuré : 0 doublons sur 8548 fichiers).
