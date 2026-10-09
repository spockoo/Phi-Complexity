#!/usr/bin/env python3
"""Calcul de delta entre deux manifestes par fichier.

Un delta décrit la transformation minimale pour passer d'une version
installée à une nouvelle version :

    {
      "de_version": "4.34.0",
      "vers_version": "4.35.0",
      "ajoutes": ["lib/lean/Nouveau.olean", ...],
      "modifies": ["bin/lean", "lib/lean/Init.olean", ...],
      "supprimes": ["lib/lean/Obsolete.olean", ...],
      "inchanges": 1940,   # compte uniquement (pas la liste, trop longue)
    }

Sémantique :
- ajoutes   : dans le nouveau manifeste, absent de l'ancien → à extraire.
- modifies  : présent dans les deux, hash différent → à extraire.
- supprimes : dans l'ancien, absent du nouveau → à effacer.
- inchanges : même chemin, même hash → ne rien faire.

Note honnête : ce delta économise l'extraction et les I/O disque, mais
PAS le téléchargement — les releases Lean sont des tarballs monolithiques
sans delta officiel. Le téléchargement complet reste nécessaire (sauf si
nous hébergeons un jour nos propres deltas par fichier).
"""

STATUTS_DELTA = ("ajoutes", "modifies", "supprimes")


def calculer_delta(manifeste_ancien, manifeste_nouveau):
    """Calcule le delta entre deux manifestes par fichier.

    Args:
        manifeste_ancien: dict avec "version" et "fichiers" (ou None).
        manifeste_nouveau: dict avec "version" et "fichiers".

    Returns:
        dict: le delta (voir docstring du module).

    Si `manifeste_ancien` est None (première installation), tout est
    considéré comme ajouté.
    """
    fichiers_nouveaux = manifeste_nouveau["fichiers"]
    version_nouvelle = manifeste_nouveau["version"]

    if manifeste_ancien is None:
        return {
            "de_version": None,
            "vers_version": version_nouvelle,
            "ajoutes": sorted(fichiers_nouveaux.keys()),
            "modifies": [],
            "supprimes": [],
            "inchanges": 0,
        }

    fichiers_anciens = manifeste_ancien["fichiers"]
    version_ancienne = manifeste_ancien.get("version")

    ajoutes = []
    modifies = []
    n_inchanges = 0

    for chemin, hash_nouveau in fichiers_nouveaux.items():
        hash_ancien = fichiers_anciens.get(chemin)
        if hash_ancien is None:
            ajoutes.append(chemin)
        elif hash_ancien != hash_nouveau:
            modifies.append(chemin)
        else:
            n_inchanges += 1

    supprimes = sorted(
        c for c in fichiers_anciens if c not in fichiers_nouveaux
    )

    return {
        "de_version": version_ancienne,
        "vers_version": version_nouvelle,
        "ajoutes": sorted(ajoutes),
        "modifies": sorted(modifies),
        "supprimes": supprimes,
        "inchanges": n_inchanges,
    }


def resumer_delta(delta):
    """Résumé lisible d'un delta (une ligne par catégorie)."""
    lignes = [
        "Delta %s -> %s :"
        % (delta["de_version"] or "(aucune)", delta["vers_version"]),
        "  ajoutés   : %d" % len(delta["ajoutes"]),
        "  modifiés  : %d" % len(delta["modifies"]),
        "  supprimés : %d" % len(delta["supprimes"]),
        "  inchangés : %d" % delta["inchanges"],
    ]
    return "\n".join(lignes)


def fichiers_a_extraire(delta):
    """Liste des chemins à extraire pour appliquer le delta."""
    return sorted(set(delta["ajoutes"]) | set(delta["modifies"]))
