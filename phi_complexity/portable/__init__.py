"""portable — format PHIAST portable (.phiast / .zz).

Modules :
- lecteur_phiast : lecteur du format .phiast v2 (mmap, statuts typés)
- lecteur_zz : LecteurZZ — lecture lazy du .zz compressé (15 chunks zlib-9),
  byte-identique au .phiast d'origine (validé sha256)
- compresser_body : compresser() — 3613 Mo → 80,6 Mo (chunks 256 Mo)
"""

from .lecteur_zz import (
    LecteurZZ,
    ZzError,
    Manifeste,
    lire_entete,
    iterer_declarations,
)
from .compresser_body import compresser, lire_manifeste

__all__ = [
    "LecteurZZ",
    "ZzError",
    "Manifeste",
    "lire_entete",
    "iterer_declarations",
    "compresser",
    "lire_manifeste",
]
