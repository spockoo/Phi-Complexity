#!/usr/bin/env python3
"""Téléchargeur Mathlib ultra-rapide pour phi-complexity.

Optimisations :
1. Téléchargements parallèles (ThreadPoolExecutor)
2. Sélection automatique du miroir le plus rapide
3. Reprise sur interruption (fichiers partiels via Range)
4. Saut des fichiers déjà en cache (vérif SHA256)
5. Limite de connexions raisonnable (pas de DDoS)

Architecture :
- `FichierATelecharger` : (url, destination, sha256_attendu, taille)
- `resultat_telechargement` : statut typé par fichier
- `telecharger_lot()` : orchestre le tout en parallèle
"""

import concurrent.futures
import hashlib
import os
import time
import urllib.request
import urllib.error

# --- Constantes -----------------------------------------------------------

# Nombre de connexions parallèles par défaut.
# 8 = bon compromis vitesse/politesse (pas de DDoS).
DEFAUT_JOBS = 8
MAX_JOBS = 16

# Timeout par connexion (secondes).
TIMEOUT_CONNEXION = 30

# Taille des chunks de lecture (1 Mo).
TAILLE_CHUNK = 1024 * 1024

# Miroirs du cache Mathlib, par ordre de préférence par défaut.
# `selection_miroir()` les re-classe par latence mesurée.
MIROIRS = [
    ("cache.mathlib.org", "https://cache.mathlib.org"),
    ("cloudflare", "https://mathlib4.lean-cache.cloud"),
    ("azure", "https://lakecache.blob.core.windows.net"),
]


# --- Statuts typés --------------------------------------------------------

class StatutTelechargement:
    """Statuts possibles pour un fichier téléchargé."""
    TELECHARGE = "TELECHARGE"           # téléchargé avec succès
    DEJA_EN_CACHE = "DEJA_EN_CACHE"     # hash OK, rien à faire
    REPRIS = "REPRIS"                   # reprise partielle réussie
    ECHEC_RESEAU = "ECHEC_RESEAU"       # erreur réseau
    ECHEC_HASH = "ECHEC_HASH"           # SHA256 ne correspond pas
    ECHEC_ESPACE = "ECHEC_ESPACE"       # disque plein


class ErreurTelechargement(Exception):
    """Erreur lors du téléchargement."""
    def __init__(self, statut, message, fichier=None):
        super().__init__(message)
        self.statut = statut
        self.fichier = fichier


# --- Utilitaires ----------------------------------------------------------

def sha256_fichier(chemin):
    """Calcule le SHA256 d'un fichier."""
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        while True:
            chunk = f.read(TAILLE_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def fichier_valide(chemin, sha256_attendu):
    """Vérifie si un fichier existe et son hash correspond."""
    if not os.path.isfile(chemin):
        return False
    if sha256_attendu is None:
        return True  # pas de hash à vérifier
    try:
        return sha256_fichier(chemin) == sha256_attendu
    except OSError:
        return False


# --- Sélection du miroir --------------------------------------------------

def mesurer_latence_miroir(base_url, timeout=10):
    """Mesure la latence (TTFB) d'un miroir via HEAD.
    
    Returns:
        float: latence en secondes, ou None si injoignable.
    """
    # On teste sur le conteneur (403 = existe, 404 = n'existe pas).
    # Les deux prouvent que le serveur répond ; on mesure le temps.
    url = base_url.rstrip("/") + "/mathlib4-master/"
    debut = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                pass
        except urllib.error.HTTPError as e:
            # 403/404 = le serveur répond, c'est ce qu'on mesure.
            if e.code in (403, 404):
                pass
            else:
                return None
        return time.monotonic() - debut
    except Exception:
        return None


def selection_miroir(miroirs=None, _mesure=None):
    """Classe les miroirs par latence croissante.
    
    Args:
        miroirs: liste de (nom, base_url). Défaut : MIROIRS.
        _mesure: seam de test — fonction(base_url) -> latence ou None.
    
    Returns:
        list: [(nom, base_url, latence)] triés, injoignables en dernier.
    """
    mesure = _mesure or mesurer_latence_miroir
    resultats = []
    for nom, base in (miroirs or MIROIRS):
        latence = mesure(base)
        resultats.append((nom, base, latence))
    # None (injoignable) en dernier, sinon par latence croissante.
    resultats.sort(key=lambda r: (r[2] is None, r[2] if r[2] is not None else float("inf")))
    return resultats


def meilleur_miroir(miroirs=None, _mesure=None):
    """Retourne la base URL du miroir le plus rapide, ou None."""
    classes = selection_miroir(miroirs, _mesure)
    for _, base, latence in classes:
        if latence is not None:
            return base
    return None


# --- Téléchargement unitaire (avec reprise) -------------------------------

def telecharger_fichier(url, destination, sha256_attendu=None,
                        progression=None, timeout=TIMEOUT_CONNEXION):
    """Télécharge un fichier avec reprise et vérification SHA256.
    
    - Si le fichier existe et le hash correspond : DEJA_EN_CACHE.
    - Si un fichier partiel existe : reprise via header Range.
    - Vérifie le SHA256 après téléchargement.
    
    Args:
        url: URL source.
        destination: chemin local.
        sha256_attendu: SHA256 hexadécimal attendu (ou None).
        progression: callback(octets_recus, octets_totaux) ou None.
        timeout: timeout par connexion.
    
    Returns:
        dict: {"statut": ..., "chemin": ..., "octets": ...}
    
    Raises:
        ErreurTelechargement: en cas d'échec.
    """
    # 1. Déjà en cache ?
    if fichier_valide(destination, sha256_attendu):
        taille = os.path.getsize(destination)
        return {"statut": StatutTelechargement.DEJA_EN_CACHE,
                "chemin": destination, "octets": taille}

    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)

    # 2. Reprise : taille du fichier partiel existant.
    octets_existants = 0
    if os.path.isfile(destination):
        octets_existants = os.path.getsize(destination)

    # 3. Construire la requête (avec Range si reprise).
    headers = {}
    mode = "wb"
    if octets_existants > 0:
        headers["Range"] = f"bytes={octets_existants}-"
        mode = "ab"  # append

    req = urllib.request.Request(url, headers=headers)
    try:
        reponse = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        # 416 = Range non satisfiable (fichier déjà complet côté serveur ?)
        # 404 = fichier inexistant
        raise ErreurTelechargement(
            StatutTelechargement.ECHEC_RESEAU,
            f"HTTP {e.code} pour {url}", fichier=destination)
    except Exception as e:
        raise ErreurTelechargement(
            StatutTelechargement.ECHEC_RESEAU,
            f"connexion impossible à {url} : {e}", fichier=destination)

    # 4. Déterminer la taille totale.
    octets_totaux = None
    content_range = reponse.headers.get("Content-Range")
    content_length = reponse.headers.get("Content-Length")
    if content_range:
        # Format: "bytes 100-999/1234"
        try:
            octets_totaux = int(content_range.split("/")[-1])
        except (ValueError, IndexError):
            pass
    elif content_length:
        try:
            octets_totaux = int(content_length) + octets_existants
        except ValueError:
            pass

    # Si le serveur ignore Range (200 au lieu de 206), recommencer à zéro.
    if octets_existants > 0 and reponse.status == 200:
        mode = "wb"
        octets_existants = 0

    # 5. Télécharger par chunks.
    statut = (StatutTelechargement.REPRIS if octets_existants > 0
              else StatutTelechargement.TELECHARGE)
    recus = octets_existants
    try:
        with open(destination, mode) as f:
            while True:
                chunk = reponse.read(TAILLE_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                recus += len(chunk)
                if progression:
                    progression(recus, octets_totaux)
    except OSError as e:
        if "No space left" in str(e):
            raise ErreurTelechargement(
                StatutTelechargement.ECHEC_ESPACE,
                f"disque plein lors de l'écriture de {destination}",
                fichier=destination)
        raise ErreurTelechargement(
            StatutTelechargement.ECHEC_RESEAU,
            f"écriture impossible vers {destination} : {e}",
            fichier=destination)
    finally:
        try:
            reponse.close()
        except Exception:
            pass

    # 6. Vérifier le hash.
    if sha256_attendu:
        try:
            hash_reel = sha256_fichier(destination)
        except OSError as e:
            raise ErreurTelechargement(
                StatutTelechargement.ECHEC_RESEAU,
                f"lecture impossible de {destination} : {e}",
                fichier=destination)
        if hash_reel != sha256_attendu:
            # Supprimer le fichier corrompu pour forcer un re-téléchargement.
            try:
                os.unlink(destination)
            except OSError:
                pass
            raise ErreurTelechargement(
                StatutTelechargement.ECHEC_HASH,
                f"SHA256 incohérent pour {destination} "
                f"(attendu {sha256_attendu[:12]}..., "
                f"reçu {hash_reel[:12]}...)",
                fichier=destination)

    return {"statut": statut, "chemin": destination, "octets": recus}


# --- Téléchargement parallèle ---------------------------------------------

def telecharger_lot(fichiers, jobs=DEFAUT_JOBS, progression_globale=None,
                    timeout=TIMEOUT_CONNEXION):
    """Télécharge plusieurs fichiers en parallèle.
    
    Args:
        fichiers: liste de dicts {"url": ..., "destination": ...,
            "sha256": ... (optionnel)}.
        jobs: nombre de connexions parallèles (1..MAX_JOBS).
        progression_globale: callback(termines, total, en_cours) ou None.
        timeout: timeout par connexion.
    
    Returns:
        dict: {"reussis": [...], "echecs": [...], "en_cache": [...],
               "octets_totaux": int, "duree_s": float}
    """
    jobs = max(1, min(jobs, MAX_JOBS))
    total = len(fichiers)
    debut = time.monotonic()

    reussis, echecs, en_cache = [], [], []
    octets_totaux = 0
    termines = 0

    def _un(f):
        return telecharger_fichier(
            f["url"], f["destination"], f.get("sha256"),
            timeout=timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        futurs = {ex.submit(_un, f): f for f in fichiers}
        for futur in concurrent.futures.as_completed(futurs):
            f = futurs[futur]
            termines += 1
            try:
                res = futur.result()
                octets_totaux += res.get("octets", 0)
                if res["statut"] == StatutTelechargement.DEJA_EN_CACHE:
                    en_cache.append(res)
                else:
                    reussis.append(res)
            except ErreurTelechargement as e:
                echecs.append({"fichier": f.get("destination"),
                               "url": f.get("url"),
                               "statut": e.statut,
                               "erreur": str(e)})
            if progression_globale:
                progression_globale(termines, total, f.get("destination"))

    return {"reussis": reussis, "echecs": echecs, "en_cache": en_cache,
            "octets_totaux": octets_totaux,
            "duree_s": time.monotonic() - debut}
