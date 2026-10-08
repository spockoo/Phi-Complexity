#!/usr/bin/env python3
"""Manifeste par fichier : inventaire SHA256 d'une toolchain installée.

Chaque installation validée génère un `.manifeste-fichiers.json` qui liste
tous les fichiers extraits avec leur SHA256. Ce manifeste permet :

1. Vérification d'intégrité fine (par fichier, pas juste le tarball) ;
2. Calcul de delta entre deux versions (quels fichiers ont changé) ;
3. Mise à jour sélective (n'extraire que les fichiers modifiés).

Format :
    {
      "version": "4.34.0",
      "genere_le": "2026-10-08T07:40:00Z",
      "fichiers": {
        "bin/lean": "abc123...",
        "lib/lean/Init.olean": "def456..."
      }
    }

Les chemins sont relatifs à la racine d'installation.
"""

import hashlib
import json
import os
from datetime import datetime, timezone


NOM_MANIFESTE_FICHIERS = ".manifeste-fichiers.json"
TAILLE_BLOC = 1024 * 1024  # 1 Mo


def _sha256_fichier(chemin):
    """SHA256 hexadécimal d'un fichier (lecture en flux)."""
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        while True:
            bloc = f.read(TAILLE_BLOC)
            if not bloc:
                break
            h.update(bloc)
    return h.hexdigest()


def generer_manifeste(rep_install, version):
    """Génère le manifeste par fichier d'une installation.

    Args:
        rep_install: répertoire racine de la toolchain installée.
        version: version Lean (ex. "4.34.0").

    Returns:
        dict: le manifeste (aussi écrit dans `.manifeste-fichiers.json`).
    """
    fichiers = {}
    for racine, _, noms in os.walk(rep_install):
        for nom in sorted(noms):
            chemin_abs = os.path.join(racine, nom)
            # Ne pas inclure les marqueurs/manifestes eux-mêmes.
            if nom in (NOM_MANIFESTE_FICHIERS, ".valide", "lean.tar.zst"):
                continue
            if nom.startswith(".valide-ext-"):
                continue
            # Ignorer les liens symboliques (leur cible est déjà hashée).
            if os.path.islink(chemin_abs):
                continue
            relatif = os.path.relpath(chemin_abs, rep_install)
            fichiers[relatif] = _sha256_fichier(chemin_abs)

    manifeste = {
        "version": version,
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "fichiers": fichiers,
    }
    chemin = os.path.join(rep_install, NOM_MANIFESTE_FICHIERS)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(manifeste, f, indent=2, sort_keys=True)
    return manifeste


def lire_manifeste(rep_install):
    """Lit le manifeste par fichier d'une installation, ou None si absent."""
    chemin = os.path.join(rep_install, NOM_MANIFESTE_FICHIERS)
    if not os.path.isfile(chemin):
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        return json.load(f)
