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
    EXTENSIONS_MOTIFS,
    MANIFESTE_DEFAUT,
    ErreurExtraction,
    ErreurTelechargement,
    ErreurValidation,
    ErreurVerification,
    ToolchainAbsente,
    ToolchainManager,
)
from .extract import MOTIFS_EXTENSION_LEAN, MOTIFS_EXTENSION_STD, MOTIFS_NATIFS
from .manifeste import (
    NOM_MANIFESTE_FICHIERS,
    generer_manifeste,
    lire_manifeste,
)
from .delta import (
    STATUTS_DELTA,
    calculer_delta,
    fichiers_a_extraire,
    resumer_delta,
)
from .validate import valider_extension, valider_natif
from .mathlib import (
    CacheExpire,
    ErreurMathlib,
    ErreurReseauMathlib,
    TelechargementRefuse,
    VersionIntrouvable,
    cache_disponible,
    chemin_cache_mathlib,
    dernier_tag_stable,
    marquer_installee,
    plan_mise_a_jour,
    tag_pour_lean,
    url_base_cache,
    url_marqueur,
    verifier_feu_vert,
    version_installee,
)
from .version import (
    NOM_FICHIER as NOM_FICHIER_LEAN_TOOLCHAIN,
    FormatLeanToolchainInvalide,
    VersionNonSupportee,
    analyser_contenu,
    lire_version_projet,
    normaliser_version,
    trouver_lean_toolchain,
)

__all__ = [
    "CHEMIN_DEFAUT_CACHE",
    "EXTENSIONS_MOTIFS",
    "MANIFESTE_DEFAUT",
    "MOTIFS_EXTENSION_LEAN",
    "MOTIFS_EXTENSION_STD",
    "MOTIFS_NATIFS",
    "CacheExpire",
    "ErreurExtraction",
    "ErreurMathlib",
    "ErreurReseauMathlib",
    "ErreurTelechargement",
    "ErreurValidation",
    "ErreurVerification",
    "FormatLeanToolchainInvalide",
    "NOM_FICHIER_LEAN_TOOLCHAIN",
    "NOM_MANIFESTE_FICHIERS",
    "STATUTS_DELTA",
    "TelechargementRefuse",
    "ToolchainAbsente",
    "ToolchainManager",
    "VersionIntrouvable",
    "VersionNonSupportee",
    "analyser_contenu",
    "cache_disponible",
    "calculer_delta",
    "chemin_cache_mathlib",
    "dernier_tag_stable",
    "fichiers_a_extraire",
    "generer_manifeste",
    "lire_manifeste",
    "lire_version_projet",
    "marquer_installee",
    "normaliser_version",
    "plan_mise_a_jour",
    "resumer_delta",
    "tag_pour_lean",
    "trouver_lean_toolchain",
    "url_base_cache",
    "url_marqueur",
    "valider_extension",
    "valider_natif",
    "verifier_feu_vert",
    "version_installee",
]
