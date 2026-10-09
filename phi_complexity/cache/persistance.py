#!/usr/bin/env python3
"""Persistance disque du cache de parsing PHIAST — chantier D4.

Mission RUCHE-PARSE-CACHE : D1 = ``cache_ast`` (cache mémoire), D2 =
``invalidation`` (politique), D4 = ce module (persistance disque).

PRINCIPE
--------
Le cache disque est une OPTIMISATION, pas une source de vérité : il accélère
les démarrages et les reprises après reboot (directive RÉSILIENCE de Tomy :
progression sur disque, reprise, écritures atomiques), mais sa perte ou sa
corruption ne doit JAMAIS empêcher un re-parse complet. Tout échec de
chargement dégrade gracieusement vers un cache vide — jamais d'exception
fatale au chargement.

LAYOUT DISQUE (``racine`` = par ex. ``.parse_cache/`` à la racine du dépôt)
---------------------------------------------------------------------------
    xx/<sha256>.pkl   un fichier pickle par entrée, shardé sur les 2 premiers
                      caractères hexadécimaux du sha256 des octets picklés
                      (évite 200 000 fichiers dans un seul répertoire).
    manifest.json     {"version": 1, "entrees": [{"nom", "hash_src",
                      "sha256"}, ...]} — écriture ATOMIQUE (tmp + os.replace).
    .lock             PID de l'écrivain en cours (best-effort, voir ci-dessous).

Note : le contrat de D1/D2 adresse les entrées par (nom, hash_src)
(granularité par déclaration — voir ``invalidation.py``). Le manifest
enregistre donc des triplets (nom, hash_src, sha256), pas un simple
{nom: sha256}.

VERROUILLAGE (best-effort)
--------------------------
``sauvegarder`` écrit son PID dans ``.lock`` avant d'écrire. Si ``.lock``
existe déjà et que le PID est vivant, ``CacheVerrouilleError`` est levée :
on refuse d'écraser le travail d'un écrivain concurrent. Un verrou périmé
(PID mort ou contenu illisible) est écrasé sans bruit.
Ce n'est PAS une exclusion mutuelle atomique : une course reste possible
entre deux processus qui acquièrent le verrou au même instant. C'est un
garde-fou, pas une garantie — documenté comme tel.

CHECKPOINT / REPRISE
--------------------
Chaque ``.pkl`` est écrit de façon atomique (tmp + os.replace) et le manifest
n'est écrit qu'APRÈS tous les ``.pkl``. Si ``sauvegarder`` est interrompu,
les fichiers déjà écrits restent valides ; relancer ``sauvegarder`` reprend
là où on en était (réécriture idempotente). Un manifest absent ou illisible
au chargement = cache vide, jamais d'exception.

Seule la bibliothèque standard est utilisée.
"""

import hashlib
import json
import os
import pickle
import re
import warnings
from pathlib import Path

__all__ = [
    "CacheVerrouilleError",
    "sauvegarder",
    "charger",
    "valider_integrite",
]

_VERSION_MANIFEST = 1

# sha256 hexadécimal : exactement 64 caractères [0-9a-f]. Vérifié à la lecture
# du manifest pour interdire toute traversée de chemin via un manifest
# trafiqué (ex. sha = "../../evil").
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# Noms de méthodes d'énumération reconnues sur ParseCache (D1), par ordre de
# préférence. Chaque élément produit doit être un triplet (nom, hash_src, ast),
# ou un couple (((nom, hash_src)), ast), ou (cle_chaine, ast) avec
# cle_chaine = "nom:hash_src" ou "nom|hash_src".
_METHODES_ENUMERATION = (
    "entrees",
    "items",
    "iterer",
    "iterer_entrees",
)

# Attributs dict internes reconnus en repli (même convention que D2).
# "_d" en premier : c'est le vrai magasin de D1 (cache_ast.ParseCache), qui
# y stocke des BLOBS PICKLE (bytes) sous des clés (nom, hash_src).
_ATTRIBUTS_MAGASIN = (
    "_d",
    "_entrees",
    "_cache",
    "entrees",
    "_store",
    "store",
    "_donnees",
    "donnees",
)


class CacheVerrouilleError(RuntimeError):
    """Levée par sauvegarder() quand un autre écrivain vivant détient .lock."""


class _ParseCacheRepli:
    """Repli minimal si ``cache_ast`` (chantier D1) n'a pas encore livré.

    Implémente exactement le contrat documenté par D2 dans
    ``invalidation.py`` : ``put(nom, hash_src, ast)`` / ``get(nom, hash_src)``.
    Dès que D1 livre ``parse_cache/cache_ast.py`` avec une classe
    ``ParseCache``, c'est elle qui est utilisée (voir _fabriquer_cache).
    """

    def __init__(self):
        self._entrees = {}

    def put(self, nom, hash_src, ast):
        self._entrees[(nom, hash_src)] = ast

    def get(self, nom, hash_src):
        return self._entrees.get((nom, hash_src))


def _fabriquer_cache():
    """Instancie un ParseCache : le vrai (D1) s'il existe, sinon le repli."""
    try:
        from .cache_ast import ParseCache  # import paresseux (D1 peut manquer)
        return ParseCache()
    except ImportError:
        warnings.warn(
            "parse_cache.cache_ast (chantier D1) absent : repli minimal "
            "utilisé pour reconstruire le cache. Les entrées restent "
            "accessibles via put(nom, hash_src, ast) / get(nom, hash_src).",
            RuntimeWarning,
            stacklevel=3,
        )
        return _ParseCacheRepli()


# ---------------------------------------------------------------------------
# Énumération des entrées du cache mémoire
# ---------------------------------------------------------------------------

def _normaliser_cle(cle):
    """cle -> (nom, hash_src). Accepte tuple (nom, hash_src) ou "nom:hash"."""
    if isinstance(cle, tuple) and len(cle) == 2:
        nom, hash_src = cle
        return str(nom), str(hash_src)
    if isinstance(cle, str):
        for sep in (":", "|"):
            if sep in cle:
                nom, hash_src = cle.rsplit(sep, 1)
                return nom, hash_src
    raise TypeError("clé d'entrée de cache non reconnue : %r" % (cle,))


def _normaliser_element(element):
    """Un élément énuméré -> (nom, hash_src, ast)."""
    if isinstance(element, tuple) and len(element) == 3:
        nom, hash_src, ast = element
        return str(nom), str(hash_src), ast
    if isinstance(element, tuple) and len(element) == 2:
        cle, ast = element
        nom, hash_src = _normaliser_cle(cle)
        return nom, hash_src, ast
    raise TypeError("élément d'entrée de cache non reconnu : %r" % (element,))


def _iterer_entrees(cache):
    """Produit des triplets (nom, hash_src, octets_pickles) du cache mémoire.

    Voies reconnues, par ordre de préférence : attribut dict interne
    (_d, _entrees, ...), puis méthode d'énumération explicite
    (entrees/items/iterer/...). Lève TypeError si aucune voie n'existe :
    échouer bruyamment plutôt que de sauvegarder un cache vide en silence
    (même philosophie que D2).

    Cas particulier D1 : ``cache_ast.ParseCache`` stocke dans ``_d`` des
    blobs DÉJÀ picklés (``put`` sérialise) — on les écrit tels quels sur
    disque, sans roundtrip inutile. Pour toute autre voie, la valeur est
    un AST que l'on pickle ici (entrée non picklable = ignorée).
    """
    for attribut in _ATTRIBUTS_MAGASIN:
        magasin = getattr(cache, attribut, None)
        if isinstance(magasin, dict):
            for cle, valeur in magasin.items():
                nom, hash_src = _normaliser_cle(cle)
                if attribut == "_d" and isinstance(valeur, (bytes, bytearray)):
                    octets = bytes(valeur)  # blob D1 : déjà picklé
                else:
                    try:
                        octets = pickle.dumps(
                            valeur, protocol=pickle.HIGHEST_PROTOCOL
                        )
                    except Exception:
                        continue  # entrée non picklable : ignorée (best-effort)
                yield nom, hash_src, octets
            return
    for nom_methode in _METHODES_ENUMERATION:
        methode = getattr(cache, nom_methode, None)
        if callable(methode):
            for element in methode():
                nom, hash_src, ast = _normaliser_element(element)
                try:
                    octets = pickle.dumps(ast, protocol=pickle.HIGHEST_PROTOCOL)
                except Exception:
                    continue
                yield nom, hash_src, octets
            return
    raise TypeError(
        "ParseCache n'offre aucune voie d'énumération : aucune méthode %s ni "
        "attribut dict %s. Sauvegarde impossible sans risquer un cache "
        "disque vide." % (_METHODES_ENUMERATION, _ATTRIBUTS_MAGASIN)
    )


# ---------------------------------------------------------------------------
# Chemins disque
# ---------------------------------------------------------------------------

def _chemin_pkl(racine, sha256):
    """Chemin shardé xx/<sha256>.pkl. Refuse tout sha mal formé."""
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError("sha256 mal formé dans le manifest : %r" % (sha256,))
    return racine / sha256[:2] / (sha256 + ".pkl")


def _chemin_manifest(racine):
    return racine / "manifest.json"


def _chemin_verrou(racine):
    return racine / ".lock"


# ---------------------------------------------------------------------------
# Verrou best-effort
# ---------------------------------------------------------------------------

def _pid_vivant(pid):
    """True si le PID existe encore (signal 0, sans effet)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except ValueError:
        return False
    return True


def _acquerir_verrou(racine):
    """Écrit notre PID dans .lock. Lève CacheVerrouilleError si un écrivain
    vivant détient déjà le verrou. Un verrou périmé est écrasé."""
    verrou = _chemin_verrou(racine)
    if verrou.exists():
        try:
            pid_detenteur = int(verrou.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid_detenteur = None
        if pid_detenteur is not None and _pid_vivant(pid_detenteur):
            raise CacheVerrouilleError(
                "cache disque verrouillé par le PID %d (fichier %s) : "
                "un autre écrivain est en cours. Réessayez plus tard."
                % (pid_detenteur, verrou)
            )
        # Verrou périmé (PID mort ou illisible) : on l'écrase ci-dessous.
    racine.mkdir(parents=True, exist_ok=True)
    verrou.write_text(str(os.getpid()), encoding="utf-8")


def _liberer_verrou(racine):
    """Supprime .lock, mais seulement s'il contient toujours notre PID
    (ne jamais supprimer le verrou d'un autre écrivain)."""
    verrou = _chemin_verrou(racine)
    try:
        if verrou.read_text(encoding="utf-8").strip() == str(os.getpid()):
            verrou.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Écriture atomique
# ---------------------------------------------------------------------------

def _ecrire_atomique(chemin_final, octets):
    """Écrit octets vers chemin_final via fichier tmp + os.replace."""
    chemin_final.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin_final.with_name(
        "%s.tmp.%d" % (chemin_final.name, os.getpid())
    )
    tmp.write_bytes(octets)
    os.replace(tmp, chemin_final)


def _ecrire_manifest_atomique(racine, enregistrements):
    """Écrit manifest.json de façon atomique (tmp + os.replace).

    Un manifest n'est donc jamais à moitié écrit : en cas d'interruption,
    l'ancien manifest (ou aucun) reste en place, et le fichier .tmp résiduel
    est ignoré au chargement.
    """
    contenu = {
        "version": _VERSION_MANIFEST,
        "entrees": enregistrements,
    }
    _ecrire_atomique(
        _chemin_manifest(racine),
        (json.dumps(contenu, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _lire_manifest(racine):
    """Lit le manifest. Retourne [] si absent, illisible ou mal formé.

    Dégradation gracieuse : un manifest corrompu = cache vide, pas d'exception.
    """
    try:
        contenu = json.loads(
            _chemin_manifest(racine).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []
    if not isinstance(contenu, dict):
        return []
    entrees = contenu.get("entrees", [])
    if not isinstance(entrees, list):
        return []
    valides = []
    for e in entrees:
        if (
            isinstance(e, dict)
            and isinstance(e.get("nom"), str)
            and isinstance(e.get("hash_src"), str)
            and isinstance(e.get("sha256"), str)
            and _SHA256_RE.fullmatch(e["hash_src"])
            and _SHA256_RE.fullmatch(e["sha256"])
        ):
            valides.append(e)
        # Enregistrement mal formé : ignoré (compté comme corrompu au
        # chargement via _lire_entree / valider_integrite).
    return valides


# ---------------------------------------------------------------------------
# Lecture d'une entrée
# ---------------------------------------------------------------------------

_CORROMPU = object()  # sentinelle : distincte de toute valeur stockable


def _lire_entree(racine, enregistrement):
    """Lit et vérifie une entrée. Retourne l'AST, ou _CORROMPU.

    Vérifications : sha256 des octets lus == sha256 du manifest, puis
    désérialisation pickle. Tout échec (fichier absent, hash différent,
    pickle invalide) -> _CORROMPU, jamais d'exception.
    """
    try:
        chemin = _chemin_pkl(racine, enregistrement["sha256"])
    except ValueError:
        return _CORROMPU
    try:
        octets = chemin.read_bytes()
    except OSError:
        return _CORROMPU
    if hashlib.sha256(octets).hexdigest() != enregistrement["sha256"]:
        return _CORROMPU
    try:
        return pickle.loads(octets)
    except Exception:
        return _CORROMPU


def _pkl_references(racine, enregistrements):
    """Ensemble des chemins .pkl référencés par le manifest."""
    references = set()
    for e in enregistrements:
        try:
            references.add(_chemin_pkl(racine, e["sha256"]))
        except ValueError:
            pass
    return references


def _lister_pkl(racine):
    """Tous les .pkl présents sur disque (shards xx/), hors fichiers .tmp."""
    trouves = []
    if not racine.is_dir():
        return trouves
    for shard in racine.iterdir():
        if not (shard.is_dir() and re.fullmatch(r"[0-9a-f]{2}", shard.name)):
            continue
        for f in shard.iterdir():
            if (
                f.is_file()
                and f.suffix == ".pkl"
                and ".tmp." not in f.name
            ):
                trouves.append(f)
    return trouves


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def sauvegarder(cache, racine=".parse_cache/"):
    """Vide le cache mémoire vers le disque. Retourne le nb d'entrées écrites.

    - Chaque entrée est sérialisée (le blob pickle de D1 est réutilisé tel
      quel, sans roundtrip), son sha256 (des octets écrits) est calculé,
      puis elle est écrite en ``xx/<sha256>.pkl`` de façon atomique.
    - Le manifest est écrit EN DERNIER, atomiquement : une interruption
      laisse les .pkl déjà écrits valides (reprise = relancer sauvegarder).
    - Les entrées non picklables sont ignorées (le cache disque est une
      optimisation best-effort, pas une archive).
    - Le cache mémoire n'est PAS vidé : c'est un flush, pas un déplacement.
    - Lève CacheVerrouilleError si un autre écrivain vivant détient .lock.
    """
    racine = Path(racine)
    _acquerir_verrou(racine)
    try:
        enregistrements = []
        ecrites = 0
        for nom, hash_src, octets in _iterer_entrees(cache):
            sha256 = hashlib.sha256(octets).hexdigest()
            _ecrire_atomique(_chemin_pkl(racine, sha256), octets)
            enregistrements.append(
                {"nom": nom, "hash_src": hash_src, "sha256": sha256}
            )
            ecrites += 1
        _ecrire_manifest_atomique(racine, enregistrements)
        return ecrites
    finally:
        _liberer_verrou(racine)


def charger(racine=".parse_cache/"):
    """Reconstruit un ParseCache depuis le disque via le manifest.

    - Les ``.pkl`` orphelins (non référencés par le manifest) sont ignorés.
    - Un ``.pkl`` corrompu (absent, hash différent, pickle invalide) est
      compté et ignoré : jamais d'exception fatale au chargement.
    - Le bilan est exposé sur l'attribut ``rapport_chargement`` du cache
      retourné : {"total", "ok", "corrompus", "orphelins"} (si le cache
      supporte l'affectation d'attribut).
    - Racine absente ou manifest illisible -> cache vide (dégradation
      gracieuse : un re-parse complet reste toujours possible).
    """
    racine = Path(racine)
    cache = _fabriquer_cache()
    rapport = {"total": 0, "ok": 0, "corrompus": 0, "orphelins": 0}
    enregistrements = _lire_manifest(racine)
    references = _pkl_references(racine, enregistrements)
    for e in enregistrements:
        rapport["total"] += 1
        ast = _lire_entree(racine, e)
        if ast is _CORROMPU:
            rapport["corrompus"] += 1
            continue
        try:
            cache.put(e["nom"], e["hash_src"], ast)
        except Exception:
            # Clé refusée par le cache (ex. ErreurCacheAst de D1) ou
            # sérialisation impossible : entrée inutilisable -> corrompue.
            # Jamais d'exception fatale au chargement.
            rapport["corrompus"] += 1
            continue
        rapport["ok"] += 1
    rapport["orphelins"] = sum(
        1 for p in _lister_pkl(racine) if p not in references
    )
    try:
        cache.rapport_chargement = rapport
    except AttributeError:
        pass  # cache avec __slots__ : le rapport reste accessible via
              # valider_integrite()
    return cache


def valider_integrite(racine=".parse_cache/"):
    """Audite le cache disque. Retourne {"total", "ok", "corrompus",
    "orphelins"}.

    - total     : entrées au manifest
    - ok        : .pkl présent, hash conforme, pickle lisible
    - corrompus : .pkl absent, hash différent ou pickle invalide
    - orphelins : .pkl sur disque non référencés par le manifest
    """
    racine = Path(racine)
    enregistrements = _lire_manifest(racine)
    references = _pkl_references(racine, enregistrements)
    ok = 0
    corrompus = 0
    for e in enregistrements:
        if _lire_entree(racine, e) is _CORROMPU:
            corrompus += 1
        else:
            ok += 1
    orphelins = sum(1 for p in _lister_pkl(racine) if p not in references)
    return {
        "total": len(enregistrements),
        "ok": ok,
        "corrompus": corrompus,
        "orphelins": orphelins,
    }
