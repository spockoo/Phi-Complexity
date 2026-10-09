#!/usr/bin/env python3
"""cache_ast.py — Cache AST par déclaration (mission RUCHE-PARSE-CACHE, chantier 1/5).

Principe des .olean Lean appliqué au parsing PHIAST : le résultat de
``parse_decl()`` est sérialisé (pickle) et conservé en mémoire sous la clé
``(nom, hash_src)``, où ``hash_src`` = sha256 des octets bruts de la
déclaration dans le fichier source. Un second passage sur le même fichier
retrouve l'AST sans reparser.

CONTRAT D'INTERFACE (partagé par les 5 disciples de la mission) :
  - octets source d'une déclaration = ``buf[offset_debut:offset_fin]``
  - ``offset_fin`` donné par ``iterer_declarations()``
    (``DeclarationLue.offset_fin``)
  - ``offset_debut`` = ``offset_fin`` de la déclaration précédente
    (ou ``entete.taille_entete`` pour l'index 0)
  - ``hash_src`` = ``hashlib.sha256(octets).hexdigest()``
  - ce module ne touche JAMAIS au fichier source (lecture seule :
    ``lire_octets_declaration`` ouvre en mode ``"rb"`` et ne lit que la
    tranche demandée)

VÉRIFICATION PICKLABILITÉ (2026-10-07) : ``PhiAstReaderV2.parse_decl()``
retourne ``(nom: str, offset_debut: int)`` — un tuple de scalaires, donc
picklable SANS conversion. Aucune conversion n'est appliquée ; si un jour
le lecteur retourne une structure non picklable (évolution du format),
``put()`` lève ``ErreurCacheAst`` explicitement au lieu de deviner une
conversion (vérifié sur ``spike_portable/jouet_portable.phiast`` : 3
déclarations, roundtrip pickle bit-à-bit).

Politique LRU : OrderedDict + move_to_end + popitem(last=False),
même pattern que ``ruche-cache-ultra/cache_memoire.py`` (LRUOrderedDict).
Un ``put()`` sur une clé existante rafraîchit sa récence.

API :
    cache = ParseCache(capacite=20000)
    cache.put(nom, hash_src, ast_obj)   # sérialise (pickle) en mémoire
    cache.get(nom, hash_src)            # AST désérialisé, ou None (miss)
    cache.stats()                      # dict : hits, misses, evictions, ...
    cache.vider()                      # réinitialise contenu + compteurs

    calculer_hash_src(octets) -> str    # sha256 hexadécimal (contrat)
    lire_octets_declaration(chemin, offset_debut, offset_fin) -> bytes

stdlib uniquement, zéro dépendance externe.
"""

import hashlib
import pickle
from collections import OrderedDict

CAPACITE_DEFAUT = 20000


class ErreurCacheAst(Exception):
    """Échec bruyant du cache AST : pickle impossible, clé invalide, etc."""


def _est_hex(s):
    return all(c in "0123456789abcdefABCDEF" for c in s)


def _valider_cle(nom, hash_src):
    """Valide la clé (nom, hash_src) du contrat. Lève ErreurCacheAst sinon."""
    if not isinstance(nom, str):
        raise ErreurCacheAst(
            "nom invalide : attendu str, reçu %s" % type(nom).__name__)
    if (not isinstance(hash_src, str) or len(hash_src) != 64
            or not _est_hex(hash_src)):
        raise ErreurCacheAst(
            "hash_src invalide : attendu le hexdigest sha256 "
            "(64 caractères hexadécimaux), reçu %r" % (hash_src,))


def calculer_hash_src(octets):
    """sha256 hexadécimal des octets bruts d'une déclaration (contrat).

    ``hash_src = hashlib.sha256(buf[offset_debut:offset_fin]).hexdigest()``
    """
    if not isinstance(octets, (bytes, bytearray, memoryview)):
        raise ErreurCacheAst(
            "octets invalides : attendu bytes, reçu %s"
            % type(octets).__name__)
    return hashlib.sha256(octets).hexdigest()


def lire_octets_declaration(chemin, offset_debut, offset_fin):
    """Retourne ``buf[offset_debut:offset_fin]`` en LECTURE SEULE.

    Ouvre le fichier en mode ``"rb"`` et ne lit que la tranche demandée ;
    n'écrit JAMAIS sur le fichier source.
    """
    if (not isinstance(offset_debut, int) or not isinstance(offset_fin, int)
            or isinstance(offset_debut, bool) or isinstance(offset_fin, bool)):
        raise ErreurCacheAst(
            "offsets invalides : entiers attendus, reçu %r / %r"
            % (offset_debut, offset_fin))
    if offset_debut < 0 or offset_fin < offset_debut:
        raise ErreurCacheAst(
            "offsets incohérents : offset_debut=%r, offset_fin=%r"
            % (offset_debut, offset_fin))
    taille = offset_fin - offset_debut
    try:
        with open(chemin, "rb") as f:  # lecture seule, jamais d'écriture
            f.seek(offset_debut)
            donnees = f.read(taille)
    except OSError as e:
        raise ErreurCacheAst(
            "lecture impossible de %r [%d:%d] : %s"
            % (chemin, offset_debut, offset_fin, e)) from e
    if len(donnees) != taille:
        raise ErreurCacheAst(
            "lecture courte sur %r [%d:%d] : %d octet(s) lu(s)"
            % (chemin, offset_debut, offset_fin, len(donnees)))
    return donnees


class ParseCache:
    """Cache LRU borné des AST de déclarations, clé = (nom, hash_src).

    Le contenu stocké est la forme PICKLÉE de l'AST (principe .olean) :
    ``put`` sérialise, ``get`` désérialise à chaque hit (copie fraîche,
    aucun aliasing entre appels).
    """

    def __init__(self, capacite=CAPACITE_DEFAUT):
        if (not isinstance(capacite, int) or isinstance(capacite, bool)
                or capacite < 1):
            raise ValueError(
                "capacite invalide : entier >= 1 attendu, reçu %r"
                % (capacite,))
        self._capacite = capacite
        self._d = OrderedDict()  # clé (nom, hash_src) -> blob pickle
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def put(self, nom, hash_src, ast_obj):
        """Sérialise (pickle) l'AST et l'insère sous la clé (nom, hash_src).

        Lève ErreurCacheAst explicite si la sérialisation échoue —
        aucune conversion devinée.
        """
        _valider_cle(nom, hash_src)
        try:
            blob = pickle.dumps(ast_obj, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:  # pickle.PicklingError, TypeError, ...
            raise ErreurCacheAst(
                "AST non sérialisable pour (%r, %s...) : %s: %s"
                % (nom, hash_src[:12], type(e).__name__, e)) from e
        cle = (nom, hash_src)
        if cle in self._d:
            del self._d[cle]  # ré-insertion = récence rafraîchie
        self._d[cle] = blob
        if len(self._d) > self._capacite:
            self._d.popitem(last=False)  # évince le moins récemment utilisé
            self._evictions += 1

    def get(self, nom, hash_src):
        """Retourne l'AST désérialisé, ou None si absent (miss)."""
        _valider_cle(nom, hash_src)
        cle = (nom, hash_src)
        blob = self._d.get(cle)
        if blob is None:
            self._misses += 1
            return None
        self._hits += 1
        self._d.move_to_end(cle)
        try:
            return pickle.loads(blob)
        except Exception as e:
            raise ErreurCacheAst(
                "désérialisation impossible pour (%r, %s...) : %s: %s"
                % (nom, hash_src[:12], type(e).__name__, e)) from e

    def stats(self):
        """Dict des statistiques : hits, misses, evictions, entrees, ..."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "entrees": len(self._d),
            "capacite": self._capacite,
            "hit_rate": (self._hits / total) if total else 0.0,
        }

    def supprimer_nom(self, nom):
        """Supprime TOUTES les entrées de la déclaration `nom` (tous hashs).

        Ajouté à l'intégration RUCHE-PARSE-CACHE (recommandation D2) : le module
        `invalidation` détecte cette méthode en priorité et n'a plus besoin
        d'accéder au dict interne `_d`.
        Retourne le nombre d'entrées supprimées.
        """
        if not isinstance(nom, str) or not nom:
            raise ErreurCacheAst("nom invalide pour supprimer_nom : %r" % (nom,))
        cles = [cle for cle in self._d if cle[0] == nom]
        for cle in cles:
            del self._d[cle]
        return len(cles)

    def vider(self):
        """Réinitialise le contenu et les compteurs."""
        self._d.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def __len__(self):
        return len(self._d)

    def __contains__(self, cle):
        return cle in self._d
