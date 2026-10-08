#!/usr/bin/env python3
"""integration_check.py — Parse cache natif dans `phi check` (mission PHI-NATIF-C).

RÔLE
----
Brancher le ParseCache (chantier D1) sur la sous-commande `phi check` de façon
TRANSPARENTE : avant d'auditer un fichier, l'analyseur Python natif consulte
le cache de la session en cours — même hash des octets source => AST réutilisé
sans re-parse ; sinon parse normal puis mise en cache.

Le cache ne change que la VITESSE d'obtention de l'AST, jamais son contenu :
la sortie de `phi check` est byte-identique avec et sans cache.

CYCLE DE VIE (géré par la CLI, voir `_executer_check` dans cli.py)
------------------------------------------------------------------
    with session_parse_cache(cible, sans_cache=False) as cache:
        ... audits ...   # chaque AnalyseurPython.charger() consulte le cache

- Entrée : chargement disque depuis la racine résolue (dégradation gracieuse
  vers un cache vide si le disque est absent ou corrompu).
- Sortie : sauvegarde disque (best-effort, jamais bloquante).
- `--sans-cache` : aucun cache courant n'est exposé — re-parse systématique.
- `--stats-cache` : la CLI affiche `cache.stats()` en fin d'audit.

RACINE DISQUE
-------------
`.phi_cache/` dans le dossier audité ; repli sur `~/.phi_cache` quand le
dossier audité est absent ou non inscriptible. `.phi_cache/` est ignoré par
git (voir .gitignore) : le cache disque est une optimisation locale, jamais
un artefact versionné.

ROBUSTESSE
----------
Toutes les fonctions de ce module dégradent gracieusement : un cache
défaillant (disque corrompu, permissions, pickle invalide) ne doit JAMAIS
faire échouer un audit. Les analyseurs n'appellent que
`obtenir_cache_courant()` et entourent get/put de try/except.

Seule la bibliothèque standard est utilisée.
"""

import contextlib
import os
import threading
from pathlib import Path

from .cache_ast import ParseCache
from .persistance import charger, sauvegarder

__all__ = [
    "NOM_DOSSIER_CACHE",
    "resoudre_racine_cache",
    "obtenir_cache_courant",
    "definir_cache_courant",
    "session_parse_cache",
]

# Nom du dossier de cache disque, dans le dossier audité.
NOM_DOSSIER_CACHE = ".phi_cache"

# Cache de la session en cours, porté par un threading.local : chaque thread
# voit son propre cache courant (les audits parallèles ne se marchent pas
# dessus), et hors session la valeur est None (comportement historique).
_LOCAL = threading.local()


def _dossier_inscriptible(dossier):
    """True si `dossier` existe et est accessible en écriture."""
    try:
        return os.path.isdir(dossier) and os.access(dossier, os.W_OK | os.X_OK)
    except Exception:
        return False


def resoudre_racine_cache(cible):
    """Retourne le dossier racine du cache disque pour `cible`.

    - cible = dossier audité      -> `<dossier>/.phi_cache`
    - cible = fichier audité      -> `<dossier parent>/.phi_cache`
    - dossier absent/non inscriptible, cible invalide -> `~/.phi_cache`

    Ne lève jamais : en cas de doute, le repli `~/.phi_cache` est retourné.
    """
    dossier = None
    try:
        if isinstance(cible, (str, os.PathLike)):
            cible_s = os.fspath(cible)
            if os.path.isdir(cible_s):
                dossier = os.path.abspath(cible_s)
            elif os.path.isfile(cible_s):
                dossier = os.path.dirname(os.path.abspath(cible_s)) or os.getcwd()
    except Exception:
        dossier = None
    if dossier and _dossier_inscriptible(dossier):
        return Path(dossier) / NOM_DOSSIER_CACHE
    # Repli : cache personnel. On n'écrit jamais dans un dossier qu'on ne
    # peut pas écrire — le cache disque reste une optimisation, pas un droit.
    try:
        return Path.home() / NOM_DOSSIER_CACHE
    except Exception:
        # Dernier repli : dossier courant (ne devrait jamais arriver).
        return Path(os.getcwd()) / NOM_DOSSIER_CACHE


def obtenir_cache_courant():
    """Retourne le ParseCache de la session `phi check` en cours, ou None.

    None = pas de session active (ou `--sans-cache`) : l'analyseur parse
    normalement, comportement historique inchangé.
    """
    return getattr(_LOCAL, "cache", None)


def definir_cache_courant(cache):
    """Expose (ou retire, avec None) le ParseCache courant pour ce thread.

    Point d'extension pour les tests et les usages programmatiques ; la CLI
    passe par `session_parse_cache`.
    """
    _LOCAL.cache = cache


@contextlib.contextmanager
def session_parse_cache(cible, sans_cache=False):
    """Gère le cycle de vie du parse cache pour un `phi check`.

    - `sans_cache=True` : expose explicitement None (aucun cache résiduel
      d'une session englobante ne doit fuiter), ne touche pas au disque.
    - sinon : charge le cache depuis `resoudre_racine_cache(cible)` (cache
      vide si le disque est absent/corrompu), l'expose comme cache courant,
      puis le sauvegarde sur disque à la sortie (best-effort).

    Ne lève JAMAIS : un cache défaillant dégrade vers « pas de cache »
    plutôt que de bloquer l'audit.
    """
    if sans_cache:
        precedent = obtenir_cache_courant()
        definir_cache_courant(None)
        try:
            yield None
        finally:
            definir_cache_courant(precedent)
        return
    racine = resoudre_racine_cache(cible)
    cache = None
    try:
        cache = charger(racine)  # gracieux par construction (cf. persistance)
    except Exception:
        cache = None
    if cache is None:
        try:
            cache = ParseCache()
        except Exception:
            cache = None
    precedent = obtenir_cache_courant()
    definir_cache_courant(cache)
    try:
        yield cache
    finally:
        definir_cache_courant(precedent)
        if cache is not None:
            try:
                sauvegarder(cache, racine)
            except Exception:
                # Le cache disque est une optimisation, jamais un prérequis :
                # un échec de sauvegarde ne doit pas faire échouer l'audit.
                pass
