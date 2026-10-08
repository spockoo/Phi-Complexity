#!/usr/bin/env python3
"""Lecture du fichier ``lean-toolchain`` (mécanisme standard d'Elan).

Un projet Lean épingle sa version dans un fichier ``lean-toolchain`` à sa
racine, contenant une ligne comme ``leanprover/lean4:v4.34.0`` (ou juste
``v4.34.0`` dans les versions récentes d'Elan). Ce module sait :

- remonter les dossiers parents jusqu'à trouver ce fichier ;
- en analyser la version (normalisée sans préfixe, ex. ``4.34.0``) ;
- refuser proprement toute version autre que celle installée par
  phi-complexity (mono-version assumée dans ce chantier).

Évolution future documentée : le support multi-versions (télécharger la
version demandée au lieu d'échouer). Tant qu'il n'existe pas,
``VersionNonSupportee`` est levée avec un message explicite en français.

Périmètre RUCHE-LEAN-COMPLET / chantier 4 (P2) — support ``lean-toolchain``.
"""

import os
import re

NOM_FICHIER = "lean-toolchain"

# Ligne acceptée : [<organisation>/...:]<version>
# ex. "leanprover/lean4:v4.34.0", "v4.34.0", "4.34.0".
_MOTIF_LIGNE = re.compile(
    r"^(?:[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*:)?"
    r"(v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]*)?)$"
)


class FormatLeanToolchainInvalide(Exception):
    """Le fichier lean-toolchain existe mais son contenu est inparsable."""


class VersionNonSupportee(Exception):
    """La version demandée n'est pas la seule version installée.

    Attributs : ``demandee`` (ex. "4.33.0"), ``disponible`` (ex. "4.34.0"),
    ``chemin`` (fichier lean-toolchain d'où vient la demande).
    """

    def __init__(self, demandee, disponible, chemin):
        self.demandee = demandee
        self.disponible = disponible
        self.chemin = chemin
        super(VersionNonSupportee, self).__init__(
            "Version Lean demandée : %s (fichier lean-toolchain : %s) — "
            "seule la version %s est installée et supportée par "
            "phi-complexity pour l'instant. Le téléchargement "
            "multi-versions n'est pas encore implémenté (évolution future) : "
            "mettez le fichier lean-toolchain sur %s ou attendez le support "
            "multi-versions." % (demandee, chemin, disponible, disponible)
        )


def normaliser_version(texte):
    """'v4.34.0' -> '4.34.0' (le manifeste n'a pas de préfixe 'v')."""
    texte = texte.strip()
    if texte.startswith("v") or texte.startswith("V"):
        return texte[1:]
    return texte


def analyser_contenu(contenu, chemin=NOM_FICHIER):
    """Analyse le contenu d'un lean-toolchain → version normalisée.

    Lève FormatLeanToolchainInvalide si le contenu est vide ou inparsable.
    """
    for ligne in contenu.splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        correspondance = _MOTIF_LIGNE.match(ligne)
        if not correspondance:
            raise FormatLeanToolchainInvalide(
                "contenu inparsable dans %s : %r" % (chemin, ligne)
            )
        return normaliser_version(correspondance.group(1))
    raise FormatLeanToolchainInvalide(
        "fichier %s vide ou sans ligne de version" % chemin
    )


def trouver_lean_toolchain(dossier):
    """Remonte les parents depuis ``dossier`` ; retourne le chemin trouvé.

    Retourne None si aucun fichier lean-toolchain n'existe jusqu'à la
    racine du système de fichiers.
    """
    courant = os.path.abspath(dossier)
    while True:
        candidat = os.path.join(courant, NOM_FICHIER)
        if os.path.isfile(candidat):
            return candidat
        parent = os.path.dirname(courant)
        if parent == courant:
            return None
        courant = parent


def lire_version_projet(dossier):
    """Lit la version Lean demandée par le projet contenant ``dossier``.

    Returns:
        (version, chemin) — version normalisée (ex. "4.34.0") et chemin du
        fichier lean-toolchain trouvé en remontant les parents ;
        (None, None) si aucun fichier lean-toolchain n'existe.

    Raises:
        FormatLeanToolchainInvalide: si le fichier trouvé est inparsable.
    """
    chemin = trouver_lean_toolchain(dossier)
    if chemin is None:
        return None, None
    with open(chemin, "r", encoding="utf-8") as f:
        return analyser_contenu(f.read(), chemin), chemin
