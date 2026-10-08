#!/usr/bin/env python3
"""Validation d'une toolchain Lean mini installée.

Critères (pré-enregistrés) :
1. `bin/lean` existe et est exécutable ;
2. `bin/lean --version` retourne un code 0 ;
3. la sortie contient la version attendue (ex. "4.34.0").

Extensions (chantier 3) : `valider_extension` vérifie qu'une extension
optionnelle (`import Std`, `import Lean`) s'élabore réellement avec le
`lean` installé — pas seulement que les fichiers existent.

CONSTAT : VALIDITE — tout écart lève ErreurValidation avec le détail.
"""

import os
import subprocess
import tempfile


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


def valider_extension(repertoire, module_racine, timeout_s=600):
    """Valide une extension optionnelle par élaboration réelle.

    Écrit un fichier temporaire `import <module_racine>` (+ un `#check`
    témoin) et l'élabore avec le `bin/lean` du répertoire. C'est la seule
    preuve honnête que l'extension est utilisable : l'existence des
    fichiers ne suffit pas (imports transitifs, variantes .olean).

    Args:
        repertoire: répertoire racine de l'installation (contient bin/lean).
        module_racine: "Std" ou "Lean".
        timeout_s: délai max pour l'élaboration (le premier `import Lean`
            charge ~1 200 modules, compter ~30 s sur cette VM).

    Returns:
        dict: {"module": ..., "chemin_lean": ...}.

    Raises:
        ErreurValidation: critère non satisfait (détail dans le message).
    """
    binaire = os.path.join(repertoire, "bin", "lean")
    if not os.path.isfile(binaire):
        raise ErreurValidation("binaire absent : %s" % binaire)

    temoins = {
        "Std": "#check @Std.HashMap\n",
        "Lean": "#check @Lean.Elab.Tactic.evalTactic\n",
    }
    contenu = "import %s\n%s" % (module_racine, temoins.get(module_racine, ""))
    chemin_test = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lean", delete=False, encoding="utf-8"
        ) as f:
            f.write(contenu)
            chemin_test = f.name
        env = dict(os.environ)
        env["LEAN_PATH"] = os.path.join(repertoire, "lib", "lean")
        proc = subprocess.run(
            [binaire, chemin_test],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErreurValidation(
            "`import %s` a dépassé %ds" % (module_racine, timeout_s)
        ) from exc
    except OSError as exc:
        raise ErreurValidation(
            "exécution impossible de %s : %s" % (binaire, exc)
        ) from exc
    finally:
        if chemin_test and os.path.isfile(chemin_test):
            os.unlink(chemin_test)

    sortie = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ErreurValidation(
            "`import %s` a échoué (rc=%d) : %s"
            % (module_racine, proc.returncode, sortie.strip()[:800])
        )
    return {"module": module_racine, "chemin_lean": binaire}
