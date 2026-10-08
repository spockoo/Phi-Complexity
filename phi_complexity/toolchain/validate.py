#!/usr/bin/env python3
"""Validation d'une toolchain Lean mini installée.

Critères (pré-enregistrés) :
1. `bin/lean` existe et est exécutable ;
2. `bin/lean --version` retourne un code 0 ;
3. la sortie contient la version attendue (ex. "4.34.0").

CONSTAT : VALIDITE — tout écart lève ErreurValidation avec le détail.
"""

import os
import subprocess


class ErreurValidation(Exception):
    """La toolchain installée ne passe pas les critères."""


def valider(repertoire, version_attendue, timeout_s=60):
    """Valide une installation mini.

    Args:
        repertoire: répertoire racine du minimal (contient bin/lean).
        version_attendue: ex. "4.34.0".
        timeout_s: délai max pour `lean --version`.

    Returns:
        dict: {"chemin_lean": ..., "version": ..., "sortie": ...}.

    Raises:
        ErreurValidation: critère non satisfait (détail dans le message).
    """
    binaire = os.path.join(repertoire, "bin", "lean")
    if not os.path.isfile(binaire):
        raise ErreurValidation("binaire absent : %s" % binaire)
    if not os.access(binaire, os.X_OK):
        raise ErreurValidation("binaire non exécutable : %s" % binaire)

    try:
        proc = subprocess.run(
            [binaire, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErreurValidation(
            "`lean --version` a dépassé %ds" % timeout_s
        ) from exc
    except OSError as exc:
        raise ErreurValidation(
            "exécution impossible de %s : %s" % (binaire, exc)
        ) from exc

    sortie = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ErreurValidation(
            "`lean --version` a retourné %d : %s"
            % (proc.returncode, sortie.strip()[:500])
        )
    if version_attendue not in sortie:
        raise ErreurValidation(
            "version inattendue : %r ne contient pas %r"
            % (sortie.strip()[:200], version_attendue)
        )
    return {
        "chemin_lean": binaire,
        "version": version_attendue,
        "sortie": sortie.strip()[:200],
    }
