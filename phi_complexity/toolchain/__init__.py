#!/usr/bin/env python3
"""Gestion décentralisée de la toolchain Lean miniaturisée.

Téléchargement à la demande : au premier besoin, phi récupère la release
officielle Lean depuis GitHub, vérifie son SHA256 épinglé, n'en extrait
que le sous-ensemble minimal (spec TOOLCHAIN_MINI_SPEC.md), puis valide.

Contraintes :
- stdlib + zstandard uniquement (pas de dépendance réseau exotique) ;
- aucun téléchargement pendant les tests unitaires (mocker `telecharger`) ;
- travail local uniquement, jamais de push (règle Tomy).

Statuts typés : ce module lève des exceptions typées, jamais de booléen nu.
"""

from .manager import (
    CHEMIN_DEFAUT_CACHE,
    MANIFESTE_DEFAUT,
    ErreurExtraction,
    ErreurTelechargement,
    ErreurValidation,
    ErreurVerification,
    ToolchainAbsente,
    ToolchainManager,
)

__all__ = [
    "CHEMIN_DEFAUT_CACHE",
    "MANIFESTE_DEFAUT",
    "ErreurExtraction",
    "ErreurTelechargement",
    "ErreurValidation",
    "ErreurVerification",
    "ToolchainAbsente",
    "ToolchainManager",
]
