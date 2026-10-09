#!/usr/bin/env python3
"""Téléchargement vérifié de la toolchain Lean.

Protocole :
1. téléchargement en flux (pas de chargement complet en RAM) ;
2. SHA256 calculé en flux pendant l'écriture ;
3. vérification contre le hash épinglé AVANT toute utilisation ;
4. écriture atomique (fichier .part puis renommage).

En cas d'échec de vérification : le fichier partiel est supprimé et
`ErreurVerification` est levée (CONSTAT : INTEGRITE, jamais de booléen nu).
"""

import hashlib
import os
import urllib.request

TAILLE_BLOC = 1024 * 1024  # 1 Mo


class ErreurTelechargement(Exception):
    """Échec réseau ou HTTP pendant le téléchargement."""


class ErreurVerification(Exception):
    """Le SHA256 mesuré ne correspond pas au hash épinglé.

    CONSTAT : INTEGRITE — le fichier est rejeté, jamais utilisé.
    """


def telecharger(url, dest, sha256_attendu, progression=None, _ouvreur=None):
    """Télécharge `url` vers `dest` avec vérification SHA256 en flux.

    Args:
        url: URL HTTPS du fichier.
        dest: chemin de destination (écriture atomique via .part).
        sha256_attendu: hash hexadécimal attendu (épinglé au manifeste).
        progression: callable optionnel(octets_lus) pour la barre de
            progression.
        _ouvreur: seam de test — callable(url) -> objet fichier binaire.
            Quand None, utilise urllib.request.urlopen.

    Returns:
        dict: {"chemin": dest, "octets": n, "sha256": hexdigest}.

    Raises:
        ErreurTelechargement: échec réseau/HTTP.
        ErreurVerification: hash incohérent (fichier supprimé).
    """
    partiel = dest + ".part"
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)

    ouvrir = _ouvreur or urllib.request.urlopen
    try:
        reponse = ouvrir(url)
    except Exception as exc:
        raise ErreurTelechargement(
            "ouverture impossible de %s : %s" % (url, exc)
        ) from exc

    h = hashlib.sha256()
    lus = 0
    try:
        with open(partiel, "wb") as fout:
            while True:
                bloc = reponse.read(TAILLE_BLOC)
                if not bloc:
                    break
                fout.write(bloc)
                h.update(bloc)
                lus += len(bloc)
                if progression is not None:
                    progression(lus)
    except Exception as exc:
        try:
            os.unlink(partiel)
        except OSError:
            pass
        raise ErreurTelechargement(
            "échec pendant le téléchargement de %s : %s" % (url, exc)
        ) from exc
    finally:
        try:
            reponse.close()
        except Exception:
            pass

    mesure = h.hexdigest()
    if mesure.lower() != sha256_attendu.lower():
        try:
            os.unlink(partiel)
        except OSError:
            pass
        raise ErreurVerification(
            "SHA256 incohérent pour %s\n  attendu : %s\n  mesuré  : %s\n"
            "Le fichier a été rejeté et supprimé." % (url, sha256_attendu, mesure)
        )

    os.replace(partiel, dest)
    return {"chemin": dest, "octets": lus, "sha256": mesure}
