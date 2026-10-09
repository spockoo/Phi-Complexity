"""Invalidation intelligente du cache de parsing PHIAST.

Ce module décide quand un résultat de parsing mis en cache peut être réutilisé
et quand il doit être recalculé. C'est la couche "politique" au-dessus du
stockage brut (``ParseCache``, chantier D1) : lui stocke, nous décidons.

POLITIQUE D'INVALIDATION (posée par la mission RUCHE-PARSE-CACHE)
----------------------------------------------------------------
1. Granularité PAR DÉCLARATION, jamais par fichier.
   Chaque entrée du cache est adressée par (nom de déclaration, hash_src).
   Un fichier source modifié n'invalide que les déclarations dont les octets
   bruts ont effectivement changé ; les autres restent valides.
2. Un hash différent = re-parse OBLIGATOIRE.
   Aucune tolérance, aucun "ça ressemble assez" : deux octets différents
   produisent deux sha256 différents, et le cache ne devine jamais.
3. hash_src = hashlib.sha256(octets_bruts_déclaration).hexdigest().
   Les octets bruts sont buf[offset_debut:offset_fin] (voir D1 pour le calcul
   des offsets). Comparer des hashs, jamais des contenus : le hash est
   l'identité de la déclaration.
4. Lecture seule sur les fichiers source.
   Ce module ne lit ni n'écrit jamais les fichiers .phiast : il reçoit des
   octets déjà extraits par le lecteur (lecteur_phiast.py).
5. Thread-safety : un threading.RLock module-level entoure chaque opération,
   y compris les séquences composites (vérifier-puis-insérer de
   ``valider_ou_reparser``). Réentrant, donc composable. Le multiprocessing
   viendra plus tard : ce verrou ne protège PAS entre processus ; le jour venu,
   c'est le stockage du cache lui-même qui devra devenir process-safe.

CONTRAT D'INTERFACE ATTENDU DE ParseCache (chantier D1)
-------------------------------------------------------
Méthodes requises :
  - put(nom: str, hash_src: str, ast) -> None
  - get(nom: str, hash_src: str) -> ast | None
    (absence = retour None, ou levée de KeyError/LookupError ; les deux
    formes sont acceptées. Une entrée ne stocke jamais None.)
Primitives de suppression (pour invalider / invalider_tout), par ordre de
préférence :
  - méthodes nommées : invalider_nom / supprimer_nom / effacer_nom /
    retirer_nom(nom) ; invalider_tout / vider / reinitialiser / clear /
    purger() -> int (nombre d'entrées supprimées)
  - à défaut, un attribut dict interne (_entrees, _cache, entrees, ...)
    indexé par clés (nom, hash_src) ; ce module le vide directement.
Si aucune voie de suppression n'existe, une TypeError explicite est levée :
échouer bruyamment plutôt que de laisser croire à une invalidation qui
n'a pas eu lieu.

Seule la bibliothèque standard est utilisée.
"""

import hashlib
import threading

__all__ = [
    "est_valide",
    "invalider",
    "invalider_tout",
    "valider_ou_reparser",
    "statistiques",
    "reinitialiser_statistiques",
]

# Verrou réentrant unique pour toutes les opérations de ce module.
_VERROU = threading.RLock()

# Compteurs de hits / misses de valider_ou_reparser (protégés par _VERROU).
_STATS = {"hits": 0, "misses": 0}

# Noms de méthodes de suppression ciblée reconnus sur ParseCache.
_METHODES_SUPPRESSION_NOM = (
    "invalider_nom",
    "supprimer_nom",
    "effacer_nom",
    "retirer_nom",
)

# Noms de méthodes de vidage complet reconnus sur ParseCache.
_METHODES_VIDAGE = (
    "invalider_tout",
    "vider",
    "reinitialiser",
    "clear",
    "purger",
)

# Noms d'attributs dict internes reconnus (repli si aucune méthode nommée).
_ATTRIBUTS_MAGASIN = (
    "_d",  # ParseCache du chantier D1 (OrderedDict clé (nom, hash_src))
    "_entrees",
    "_cache",
    "entrees",
    "_store",
    "store",
    "_donnees",
    "donnees",
)


def _calculer_hash(octets_src):
    """sha256 hexadécimal des octets bruts d'une déclaration."""
    if not isinstance(octets_src, (bytes, bytearray, memoryview)):
        raise TypeError(
            "octets_src doit être bytes/bytearray/memoryview, reçu %s"
            % type(octets_src).__name__
        )
    return hashlib.sha256(bytes(octets_src)).hexdigest()


def _lire_sans_exception(cache, nom, hash_src):
    """get() robuste : None ou KeyError/LookupError = entrée absente."""
    try:
        return cache.get(nom, hash_src)
    except (KeyError, LookupError):
        return None


def _methode(cache, noms):
    """Première méthode appelable trouvée sur le cache, ou None."""
    for nom_methode in noms:
        methode = getattr(cache, nom_methode, None)
        if callable(methode):
            return methode
    return None


def _magasin_interne(cache):
    """Dict interne du cache s'il est exposé sous un nom connu, sinon None."""
    for attribut in _ATTRIBUTS_MAGASIN:
        magasin = getattr(cache, attribut, None)
        if isinstance(magasin, dict):
            return magasin
    return None


def _cles_du_nom(magasin, nom):
    """Clés du magasin appartenant à la déclaration `nom`.

    Forme de clé documentée : tuple (nom, hash_src). Les clés chaîne de la
    forme "nom<sep>hash" (sep = ':' ou '|') sont aussi reconnues.
    """
    cles = []
    for cle in magasin:
        if isinstance(cle, tuple) and len(cle) == 2 and cle[0] == nom:
            cles.append(cle)
        elif isinstance(cle, str):
            for sep in (":", "|"):
                if cle.startswith(nom + sep):
                    cles.append(cle)
                    break
    return cles


def est_valide(nom: str, hash_src_actuel: str, cache) -> bool:
    """True si le cache contient une entrée pour (nom, hash_src_actuel).

    C'est le test d'identité strict : même nom + même hash des octets bruts.
    Un nom présent sous un AUTRE hash n'est pas valide (politique n°2).
    """
    with _VERROU:
        return _lire_sans_exception(cache, nom, hash_src_actuel) is not None


def invalider(nom: str, cache) -> None:
    """Supprime TOUTES les entrées du cache pour la déclaration `nom`,
    quel que soit leur hash_src. Les autres noms sont intacts.

    Lève TypeError si le cache n'offre aucune voie de suppression.
    """
    with _VERROU:
        suppression = _methode(cache, _METHODES_SUPPRESSION_NOM)
        if suppression is not None:
            suppression(nom)
            return
        magasin = _magasin_interne(cache)
        if magasin is not None:
            for cle in _cles_du_nom(magasin, nom):
                del magasin[cle]
            return
        raise TypeError(
            "ParseCache ne supporte pas la suppression ciblée : aucune méthode "
            "%s ni attribut dict %s. Impossible d'invalider %r sans risquer "
            "des lectures périmées." % (_METHODES_SUPPRESSION_NOM,
                                       _ATTRIBUTS_MAGASIN, nom)
        )


def invalider_tout(cache) -> int:
    """Vide entièrement le cache.

    Retourne le nombre d'entrées supprimées.
    Lève TypeError si le cache n'offre aucune voie de vidage.
    """
    with _VERROU:
        vidage = _methode(cache, _METHODES_VIDAGE)
        if vidage is not None:
            # Certaines implémentations (ex. ParseCache.vider() de D1) ne
            # retournent rien : on compte alors avant via __len__ si dispo.
            avant = len(cache) if hasattr(cache, "__len__") else None
            resultat = vidage()
            if resultat is not None:
                return int(resultat)
            return avant if avant is not None else 0
        magasin = _magasin_interne(cache)
        if magasin is not None:
            nombre = len(magasin)
            magasin.clear()
            return nombre
        raise TypeError(
            "ParseCache ne supporte pas le vidage : aucune méthode %s ni "
            "attribut dict %s." % (_METHODES_VIDAGE, _ATTRIBUTS_MAGASIN)
        )


def valider_ou_reparser(nom, octets_src, parse_fn, cache):
    """Fonction pivot : retourne l'AST en cache si valide, sinon re-parse.

    1. Calcule hash_src = sha256(octets_src).
    2. Si est_valide(nom, hash_src, cache) : retourne l'AST caché (hit).
    3. Sinon : ast = parse_fn(octets_src), cache.put(nom, hash_src, ast),
       retourne l'AST frais (miss).

    La séquence vérifier-puis-insérer est atomique sous le RLock du module :
    deux threads ne re-parseront jamais deux fois les mêmes octets.
    Les hits/misses sont comptés (voir statistiques()).
    """
    hash_src = _calculer_hash(octets_src)
    with _VERROU:
        ast = _lire_sans_exception(cache, nom, hash_src)
        if ast is not None:
            _STATS["hits"] += 1
            return ast
        frais = parse_fn(octets_src)
        cache.put(nom, hash_src, frais)
        _STATS["misses"] += 1
        return frais


def statistiques():
    """Copie des compteurs {'hits': int, 'misses': int}."""
    with _VERROU:
        return dict(_STATS)


def reinitialiser_statistiques():
    """Remet les compteurs hits/misses à zéro."""
    with _VERROU:
        _STATS["hits"] = 0
        _STATS["misses"] = 0
