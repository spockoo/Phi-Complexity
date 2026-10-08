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


# Fichiers critiques du kit natif : sans eux l'édition de liens échoue
# (mesuré le 2026-10-08 : `leanc` invoque clang avec -nostdinc -isystem
# <sysroot>/include/clang, puis ld.lld avec les .a ci-dessous).
_FICHIERS_NATIFS_CRITIQUES = [
    "lib/lean/libInit.a",
    "lib/lean/libLean.a",
    "lib/lean/libStd.a",
    "lib/lean/libLake.a",
    "lib/lean/libleancpp.a",
    "lib/lean/libleanrt.a",
    "lib/lean/libleanmanifest.a",
    "include/lean/lean.h",
    "include/clang/stddef.h",
    "lib/Scrt1.o",
    "lib/crti.o",
    "lib/crtn.o",
]


def valider_natif(repertoire, timeout_s=120):
    """Valide le kit natif (leanc) d'une installation.

    Critères (pré-enregistrés) :
    1. `bin/leanc`, `bin/clang`, `bin/ld.lld` existent et sont exécutables ;
    2. `bin/leanc --version` retourne un code 0 (délègue à clang : vérifie
       implicitement la résolution des .so embarqués libclang-cpp,
       libLLVM, libc++.so.1, ...) ;
    3. les pièces critiques de l'édition de liens sont présentes
       (archives statiques Lean, en-têtes C, objets de démarrage).

    Args:
        repertoire: répertoire racine de l'installation (contient bin/).
        timeout_s: délai max pour `leanc --version`.

    Returns:
        dict: {"chemin_leanc": ..., "sortie": ...}.

    Raises:
        ErreurValidation: critère non satisfait (détail dans le message).
    """
    for nom in ("bin/leanc", "bin/clang", "bin/ld.lld"):
        chemin = os.path.join(repertoire, nom)
        if not os.path.isfile(chemin):
            raise ErreurValidation("binaire natif absent : %s" % chemin)
        if not os.access(chemin, os.X_OK):
            raise ErreurValidation(
                "binaire natif non exécutable : %s" % chemin
            )

    for relatif in _FICHIERS_NATIFS_CRITIQUES:
        chemin = os.path.join(repertoire, relatif)
        if not os.path.isfile(chemin):
            raise ErreurValidation(
                "pièce natif manquante : %s" % relatif
            )

    binaire = os.path.join(repertoire, "bin", "leanc")
    try:
        proc = subprocess.run(
            [binaire, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErreurValidation(
            "`leanc --version` a dépassé %ds" % timeout_s
        ) from exc
    except OSError as exc:
        raise ErreurValidation(
            "exécution impossible de %s : %s" % (binaire, exc)
        ) from exc

    sortie = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ErreurValidation(
            "`leanc --version` a retourné %d : %s"
            % (proc.returncode, sortie.strip()[:500])
        )
    return {
        "chemin_leanc": binaire,
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


# ── Lake (chantier 2, 2026-10-08) ────────────────────────────

# Pièces critiques sans lesquelles `lake new` / `lake build` échouent
# (déterminées empiriquement, voir MOTIFS_LAKE dans extract.py).
_FICHIERS_LAKE_CRITIQUES = [
    "lib/lean/libLake_shared.so",
    "lib/lean/Lake.olean",
    "lib/lean/Lake/DSL/Config.olean",
    "include/lean/lean.h",
]


def valider_lake(repertoire, timeout_s=60):
    """Valide l'installation optionnelle de Lake.

    Critères (pré-enregistrés) :
    1. `bin/lake` existe et est exécutable ;
    2. `bin/lake --version` retourne un code 0 et mentionne Lake ;
    3. les pièces critiques sont présentes (libLake_shared.so,
       Lake.olean, un olean du DSL, lean.h).

    Args:
        repertoire: répertoire racine de l'installation (contient bin/).
        timeout_s: délai max pour `lake --version`.

    Returns:
        dict: {"chemin_lake": ..., "version": ..., "sortie": ...}.

    Raises:
        ErreurValidation: critère non satisfait (détail dans le message).
    """
    binaire = os.path.join(repertoire, "bin", "lake")
    if not os.path.isfile(binaire):
        raise ErreurValidation("binaire lake absent : %s" % binaire)
    if not os.access(binaire, os.X_OK):
        raise ErreurValidation("binaire lake non exécutable : %s" % binaire)

    for relatif in _FICHIERS_LAKE_CRITIQUES:
        chemin = os.path.join(repertoire, relatif)
        if not os.path.isfile(chemin):
            raise ErreurValidation(
                "pièce lake manquante : %s" % chemin
            )

    try:
        proc = subprocess.run(
            [binaire, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErreurValidation(
            "`lake --version` a dépassé %ds" % timeout_s
        ) from exc
    except OSError as exc:
        raise ErreurValidation(
            "exécution impossible de %s : %s" % (binaire, exc)
        ) from exc

    sortie = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ErreurValidation(
            "`lake --version` a retourné %d : %s"
            % (proc.returncode, sortie.strip()[:500])
        )
    if "Lake version" not in sortie:
        raise ErreurValidation(
            "sortie inattendue : %r ne contient pas 'Lake version'"
            % sortie.strip()[:200]
        )
    return {
        "chemin_lake": binaire,
        "version": sortie.strip().splitlines()[0] if sortie.strip() else "",
        "sortie": sortie.strip()[:200],
    }
