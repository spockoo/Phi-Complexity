"""
langs/registry.py — Association extension de fichier → langage / analyseur.

`EXTENSIONS_PYTHON` reste sur l'analyseur natif (zéro dépendance).
`EXTENSIONS_TREESITTER` couvre un large socle de langages populaires via
`tree-sitter-language-pack` ; ce paquet expose en réalité ~165 grammaires,
donc `langage_pour_extension()` accepte aussi tout nom de langage Tree-sitter
passé explicitement en argument `--lang`.
"""
import os
from typing import Optional

from .base import AnalyseurBase
from .python_native import AnalyseurPython

EXTENSIONS_PYTHON = {".py", ".pyi"}

# Extension de fichier → nom de langage tree-sitter-language-pack
EXTENSIONS_TREESITTER = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash", ".bash": "bash",
    ".pl": "perl",
    ".dart": "dart",
    ".ml": "ocaml",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".jl": "julia",
    ".r": "r",
    ".groovy": "groovy",
    ".zig": "zig",
    ".v": "v",
    ".nim": "nim",
    ".sol": "solidity",
    ".vb": "vb",
    ".d": "d",
    ".clj": "clojure", ".cljs": "clojure",
    ".lean": "lean",
}

EXTENSIONS_SUPPORTEES = EXTENSIONS_PYTHON | set(EXTENSIONS_TREESITTER)

# Extensions disposant d'un analyseur DÉDIÉ, prioritaire sur le générique.
# ".lean" → langs/lean.py (AnalyseurLean) : le générique extrayait les
# lieurs locaux et des `<anonyme>` comme symboles (292 collisions de bruit
# mesurées) ; le dédié ne retient que les déclarations de tête nommées.
EXTENSIONS_ANALYSEUR_DEDIE = {".lean"}


def _analyseur_dedie(extension: str):
    """Classe d'analyseur dédié pour une extension, ou None."""
    if extension == ".lean":
        from .lean import AnalyseurLean
        return AnalyseurLean
    return None


def _grammaire_lean_ok() -> bool:
    """La grammaire Lean est-elle réellement utilisable (pack + téléchargement) ?"""
    try:
        from .lean import grammaire_lean_disponible
        return grammaire_lean_disponible()
    except ImportError:
        return False


def langage_pour_extension(chemin: str) -> Optional[str]:
    """Retourne l'identifiant de langage pour un chemin de fichier, sinon None."""
    ext = os.path.splitext(chemin)[1].lower()
    if ext in EXTENSIONS_PYTHON:
        return "python"
    return EXTENSIONS_TREESITTER.get(ext)


def est_fichier_supporte(chemin: str) -> bool:
    """Indique si l'extension du fichier est reconnue (Python natif ou Tree-sitter)."""
    return os.path.splitext(chemin)[1].lower() in EXTENSIONS_SUPPORTEES


def treesitter_disponible() -> bool:
    """
    Indique si le backend Tree-sitter est réellement utilisable.

    Vérifie l'import de `tree-sitter-language-pack` (dépendance optionnelle,
    installée via `pip install phi-complexity[multilang]`). Sans lui, tous
    les langages non natifs sont reconnus par extension mais sans analyseur
    fonctionnel — un instrument honnête doit le signaler, pas le taire.
    """
    try:
        import tree_sitter_language_pack  # noqa: F401
        return True
    except ImportError:
        return False


def analyseur_disponible(chemin: str, langage: Optional[str] = None) -> bool:
    """
    Indique si un analyseur *fonctionnel* existe pour ce fichier.

    Faux dans deux cas honnêtes à distinguer :
    - extension inconnue (aucun langage mappé) ;
    - langage mappé sur Tree-sitter mais `tree-sitter-language-pack` absent.
    `langage="python"` forcé rend tout fichier analysable (AST natif).
    """
    lang = langage or langage_pour_extension(chemin)
    if lang is None:
        return False
    if lang == "python":
        return True
    ext = os.path.splitext(chemin)[1].lower()
    if ext in EXTENSIONS_ANALYSEUR_DEDIE or lang == "lean":
        # Analyseur dédié : le pack seul ne suffit pas, il faut la grammaire.
        return treesitter_disponible() and _grammaire_lean_ok()
    return treesitter_disponible()


def obtenir_analyseur(fichier: str, langage: Optional[str] = None) -> AnalyseurBase:
    """
    Instancie l'analyseur adapté au fichier (ou au langage forcé explicitement).

    - `langage="python"` (ou extension .py) → analyseur AST natif, zéro dépendance.
    - extension à analyseur dédié (ex. `.lean` → AnalyseurLean) → prioritaire
      sur le générique ; lève ImportError « grammaire lean indisponible »
      si la grammaire ne peut être obtenue (jamais de repli silencieux).
    - tout autre langage → analyseur Tree-sitter générique
      (nécessite `pip install phi-complexity[multilang]`).
    """
    lang = langage or langage_pour_extension(fichier)
    if lang is None:
        raise ValueError(
            f"Langage non reconnu pour '{fichier}'. "
            "Précisez explicitement avec l'option --lang (ou le paramètre lang=...)."
        )
    if lang == "python":
        return AnalyseurPython(fichier)

    ext = os.path.splitext(fichier)[1].lower()
    if ext in EXTENSIONS_ANALYSEUR_DEDIE or lang == "lean":
        if not treesitter_disponible() or not _grammaire_lean_ok():
            raise ImportError(
                "Analyse Lean non supportée : grammaire lean indisponible "
                "(installez `pip install phi-complexity[multilang]` et vérifiez "
                "l'accès réseau pour le téléchargement de la grammaire)."
            )
        return _analyseur_dedie(ext)(fichier)

    from .treesitter_generic import AnalyseurTreeSitter
    return AnalyseurTreeSitter(fichier, ts_langage=lang)
