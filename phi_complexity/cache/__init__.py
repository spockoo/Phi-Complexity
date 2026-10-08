"""cache — cache des résultats de parsing (principe des .olean Lean).

Intégré depuis Metaprogramme-lean (RUCHE-PARSE-CACHE), local uniquement.
Ne re-parse une déclaration que si ses octets ont changé (sha256).

Modules :
- cache_ast : ParseCache, cache mémoire LRU borné
- invalidation : est_valide / invalider / valider_ou_reparser
- incremental : graphe de dépendances, invalidation en cascade
- persistance : sauvegarde/chargement disque (.parse_cache/)
"""

from .cache_ast import (
    CAPACITE_DEFAUT,
    ErreurCacheAst,
    ParseCache,
    calculer_hash_src,
    lire_octets_declaration,
)
from .invalidation import (
    est_valide,
    invalider,
    invalider_tout,
    reinitialiser_statistiques,
    statistiques,
    valider_ou_reparser,
)
from .incremental import (
    ErreurIncremental,
    FormatAstInattendu,
    OctetsSourceManquants,
    TagExprInconnu,
    construire_graphe_dependances,
    dependants_transitifs,
    extraire_references_expr,
    invalider_cascade,
    reparser_incremental,
    segments_vers_cle,
)
from .persistance import (
    CacheVerrouilleError,
    charger,
    sauvegarder,
    valider_integrite,
)

__all__ = [
    "CAPACITE_DEFAUT", "ErreurCacheAst", "ParseCache",
    "calculer_hash_src", "lire_octets_declaration",
    "est_valide", "invalider", "invalider_tout",
    "reinitialiser_statistiques", "statistiques", "valider_ou_reparser",
    "ErreurIncremental", "construire_graphe_dependances",
    "dependants_transitifs", "invalider_cascade", "reparser_incremental",
    "CacheVerrouilleError", "charger", "sauvegarder", "valider_integrite",
]
