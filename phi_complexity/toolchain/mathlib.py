#!/usr/bin/env python3
"""Gestion des mises à jour Mathlib pour phi-complexity.

Mathlib est vivant : il change quotidiennement. Ce module fournit le
mécanisme pour maintenir une version compatible à jour, sans reconstruire
depuis les sources (trop long : plusieurs heures).

Architecture :
- le cache officiel Mathlib (https://cache.mathlib.org) héberge des
  `.ltar` précompilés, adressés par hash de contenu ;
- la version compatible se détermine via les tags git `vX.Y.Z` de mathlib4,
  qui suivent les versions Lean ;
- le téléchargement est vérifié par SHA256, comme pour la toolchain.

Contraintes (règle Tomy) :
- aucun téléchargement >100 Mo sans feu vert explicite ;
- réseau entièrement mocké dans les tests unitaires ;
- travail local uniquement, jamais de push.

Statuts typés : exceptions typées, jamais de booléen nu.
"""

import json
import os
import re
import urllib.request

# --- Constantes -----------------------------------------------------------

# Endpoint public du cache Mathlib (transition depuis Azure, sept. 2026).
# Réf : Cache/Infra.lean, `publicCacheEndpoint`.
URL_CACHE_DEFAUT = "https://cache.mathlib.org"
# Fallback : compte Azure historique.
URL_CACHE_AZURE = "https://lakecache.blob.core.windows.net"

# Dépôt canonique Mathlib.
DEPOT_MATHLIB = "leanprover-community/mathlib4"

# Container pour les builds de la branche principale.
CONTENEUR_MASTER = "mathlib4-master"

# Taille estimée d'un cache Mathlib complet (plusieurs Go).
# Tout téléchargement réel dépasse 100 Mo -> feu vert Tomy requis.
SEUIL_FEU_VERT_OCTETS = 100 * 1024 * 1024

# Variable d'environnement pour un miroir personnalisé.
VAR_MIROIR = "MATHLIB_CACHE_BASE_URL"


# --- Exceptions typées -----------------------------------------------------

class ErreurMathlib(Exception):
    """Base des erreurs du module Mathlib."""


class VersionIntrouvable(ErreurMathlib):
    """Aucune version Mathlib compatible trouvée pour cette version Lean.

    CONSTAT : COMPATIBILITE — l'utilisateur doit mettre à jour Lean
    ou choisir une version Mathlib manuellement.
    """


class ErreurReseauMathlib(ErreurMathlib):
    """Échec réseau lors de l'interrogation de l'API GitHub ou du cache."""


class CacheExpire(ErreurMathlib):
    """Le cache précompilé n'existe plus pour cette révision.

    CONSTAT : RETENTION — le cache ne conserve pas indéfiniment les
    anciennes versions. Il faut viser une révision plus récente
    (et donc potentiellement un Lean plus récent).
    """


class TelechargementRefuse(ErreurMathlib):
    """Téléchargement >100 Mo refusé sans feu vert explicite de Tomy.

    CONSTAT : GOUVERNANCE — les gros téléchargements requièrent
    une autorisation explicite, jamais implicite.
    """


# --- Détection de version compatible ---------------------------------------

def _requete_json(url, _ouvreur=None):
    """Récupère et parse une réponse JSON via HTTP GET.

    Args:
        url: URL à interroger.
        _ouvreur: seam de test — callable(url) -> objet avec .read().

    Raises:
        ErreurReseauMathlib: échec HTTP ou JSON invalide.
    """
    ouvrir = _ouvreur or urllib.request.urlopen
    try:
        reponse = ouvrir(url)
        try:
            donnees = reponse.read()
        finally:
            try:
                reponse.close()
            except Exception:
                pass
    except Exception as exc:
        raise ErreurReseauMathlib(
            "requête impossible vers %s : %s" % (url, exc)
        ) from exc
    try:
        return json.loads(donnees)
    except (ValueError, TypeError) as exc:
        raise ErreurReseauMathlib(
            "réponse JSON invalide de %s : %s" % (url, exc)
        ) from exc


def tag_pour_lean(version_lean, _ouvreur=None):
    """Trouve le tag mathlib4 correspondant à une version Lean.

    Mathlib tague ses releases `vX.Y.Z` en suivant les versions Lean.
    Ex : Lean 4.34.0 -> tag mathlib4 `v4.34.0`.

    Args:
        version_lean: ex "4.34.0" (sans préfixe "v", sans suffixe).
        _ouvreur: seam de test.

    Returns:
        dict: {"tag": "v4.34.0", "commit": "<sha1>"}.

    Raises:
        VersionIntrouvable: aucun tag correspondant.
        ErreurReseauMathlib: échec API GitHub.
    """
    # Normaliser : "v4.34.0" -> "4.34.0", "4.34.0-rc2" -> "4.34.0"
    m = re.match(r"^v?(\d+\.\d+\.\d+)", version_lean.strip())
    if not m:
        raise VersionIntrouvable(
            "version Lean illisible : %r (attendu : X.Y.Z)" % version_lean
        )
    base = m.group(1)
    tag_vise = "v%s" % base

    url = "https://api.github.com/repos/%s/git/refs/tags/%s" % (
        DEPOT_MATHLIB, tag_vise)
    try:
        d = _requete_json(url, _ouvreur=_ouvreur)
    except ErreurReseauMathlib as exc:
        raise VersionIntrouvable(
            "tag %s introuvable pour Lean %s : %s" % (tag_vise, version_lean, exc)
        ) from exc

    try:
        obj = d["object"]
        sha = obj["sha"]
    except (KeyError, TypeError) as exc:
        raise VersionIntrouvable(
            "réponse inattendue pour le tag %s : %s" % (tag_vise, exc)
        ) from exc

    # Si c'est un tag annoté, résoudre vers le commit.
    if obj.get("type") == "tag":
        url2 = obj.get("url")
        if url2:
            d2 = _requete_json(url2, _ouvreur=_ouvreur)
            try:
                sha = d2["object"]["sha"]
            except (KeyError, TypeError):
                pass

    return {"tag": tag_vise, "commit": sha}


def dernier_tag_stable(_ouvreur=None):
    """Retourne le dernier tag stable (non-rc) de mathlib4.

    Utile pour proposer une mise à jour : si le tag installé est plus
    ancien que celui-ci, une mise à jour existe.

    Returns:
        dict: {"tag": ..., "commit": ...} du dernier stable.

    Raises:
        ErreurReseauMathlib: échec API GitHub.
    """
    url = "https://api.github.com/repos/%s/tags?per_page=30" % DEPOT_MATHLIB
    tags = _requete_json(url, _ouvreur=_ouvreur)
    for t in tags:
        nom = t.get("name", "")
        # Stable = vX.Y.Z sans suffixe rc/beta/alpha.
        if re.fullmatch(r"v\d+\.\d+\.\d+", nom):
            return {"tag": nom, "commit": t.get("commit", {}).get("sha", "")}
    raise VersionIntrouvable("aucun tag stable trouvé dans les 30 derniers")


# --- Vérification du cache -------------------------------------------------

def url_base_cache():
    """Retourne l'URL de base du cache (miroir configurable)."""
    miroir = os.environ.get(VAR_MIROIR, "").strip().rstrip("/")
    return miroir or URL_CACHE_DEFAUT


def url_marqueur(commit):
    """Construit l'URL du marqueur de cache pour un commit.

    L'existence du marqueur signale que le `.ltar` complet a été
    téléversé pour ce commit (cf. Cache/Marker.lean).
    """
    base = url_base_cache()
    repo = DEPOT_MATHLIB.lower()
    return "%s/%s/m/%s/%s" % (base, CONTENEUR_MASTER, repo, commit)


def cache_disponible(commit, _ouvreur=None):
    """Vérifie si le cache précompilé existe pour un commit.

    Effectue une requête HEAD (légère, pas de téléchargement).

    Returns:
        True si le marqueur existe, False sinon.

    Raises:
        ErreurReseauMathlib: échec réseau autre que 404.
    """
    url = url_marqueur(commit)
    ouvrir = _ouvreur or urllib.request.urlopen
    # urllib n'a pas de HEAD natif simple : on utilise Request avec method.
    try:
        req = urllib.request.Request(url, method="HEAD")
        reponse = ouvrir(req)
        try:
            reponse.close()
        except Exception:
            pass
        return True
    except Exception as exc:
        # 404 -> pas de cache ; autre erreur -> réseau.
        msg = str(exc)
        if "404" in msg or "Not Found" in msg:
            return False
        raise ErreurReseauMathlib(
            "vérification du cache impossible pour %s : %s" % (commit, exc)
        ) from exc


# --- Gestion du cache local -------------------------------------------------

def chemin_cache_mathlib(version_tag, base=None):
    """Chemin du cache local pour une version Mathlib donnée."""
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    # Sanitize : le tag ne doit pas contenir de séparateur.
    tag_sûr = re.sub(r"[^A-Za-z0-9._-]", "_", version_tag)
    return os.path.join(racine, tag_sûr)


def version_installee(base=None):
    """Lit la version Mathlib installée (via le marqueur .valide).

    Returns:
        dict {"tag": ..., "commit": ...} ou None si rien d'installé.
    """
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    marqueur = os.path.join(racine, ".valide")
    if not os.path.isfile(marqueur):
        return None
    try:
        with open(marqueur, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def marquer_installee(tag, commit, base=None):
    """Enregistre la version installée (idempotence)."""
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    os.makedirs(racine, exist_ok=True)
    marqueur = os.path.join(racine, ".valide")
    with open(marqueur, "w", encoding="utf-8") as f:
        json.dump({"tag": tag, "commit": commit}, f)


# --- Plan de mise à jour (sans téléchargement) ------------------------------

def plan_mise_a_jour(version_lean, _ouvreur=None):
    """Construit le plan de mise à jour SANS télécharger.

    C'est l'étape "étude" : on détermine quoi télécharger, combien ça
    pèse (estimation), et si le cache existe — avant tout feu vert.

    Args:
        version_lean: version Lean installée, ex "4.34.0".
        _ouvreur: seam de test.

    Returns:
        dict: {
            "tag": "v4.34.0",
            "commit": "<sha>",
            "cache_disponible": bool,
            "taille_estimee_octets": int | None,
            "feu_vert_requis": bool,  # True si >100 Mo
            "deja_installee": bool,
        }

    Raises:
        VersionIntrouvable, ErreurReseauMathlib, CacheExpire.
    """
    cible = tag_pour_lean(version_lean, _ouvreur=_ouvreur)
    tag, commit = cible["tag"], cible["commit"]

    installee = version_installee()
    deja = bool(installee and installee.get("commit") == commit)

    disponible = cache_disponible(commit, _ouvreur=_ouvreur)
    if not disponible:
        raise CacheExpire(
            "aucun cache précompilé pour %s (%s). "
            "Le cache ne conserve pas indéfiniment les anciennes versions ; "
            "envisagez un Lean plus récent." % (tag, commit[:12])
        )

    # Estimation : plusieurs Go (cf. spec). On ne connaît la taille exacte
    # qu'après récupération du manifeste ; on borne inférieurement.
    taille_estimee = 2 * 1024 * 1024 * 1024  # 2 Go, borne basse prudente

    return {
        "tag": tag,
        "commit": commit,
        "cache_disponible": disponible,
        "taille_estimee_octets": taille_estimee,
        "feu_vert_requis": taille_estimee > SEUIL_FEU_VERT_OCTETS,
        "deja_installee": deja,
    }


def verifier_feu_vert(plan, feu_vert=False):
    """Vérifie l'autorisation avant tout gros téléchargement.

    Args:
        plan: dict retourné par plan_mise_a_jour().
        feu_vert: True si Tomy a explicitement autorisé.

    Raises:
        TelechargementRefuse: si feu vert requis mais non donné.
    """
    if plan.get("feu_vert_requis") and not feu_vert:
        taille_go = plan["taille_estimee_octets"] / (1024 ** 3)
        raise TelechargementRefuse(
            "téléchargement Mathlib estimé à %.1f Go pour %s. "
            "Feu vert explicite de Tomy requis avant tout téléchargement "
            ">100 Mo." % (taille_go, plan["tag"])
        )


# --- Migration (delta + rollback) ------------------------------------------
#
# Réutilise les briques génériques :
# - `manifeste.generer_manifeste()` pour l'inventaire SHA256 ;
# - `delta.calculer_delta()` pour ajoutés/modifiés/supprimés/inchagés.
#
# Le téléchargement réel passe par `lake exe cache get` (sous-processus),
# car le format `.ltar` n'est pas du tar standard. Voir
# MATHLIB_MIGRATION_SPEC.md §3.2.

def _imports_migration():
    """Import paresseux pour éviter les cycles."""
    from . import manifeste as _manifeste
    from . import delta as _delta
    return _manifeste, _delta


def inventaire_mathlib(tag, base=None):
    """Inventaire SHA256 des oleans Mathlib installés pour `tag`.

    Returns:
        dict {"version": tag, "fichiers": {chemin_relatif: sha256}}
        ou None si le tag n'est pas installé.
    """
    _manifeste, _ = _imports_migration()
    dossier = chemin_cache_mathlib(tag, base=base)
    if not os.path.isdir(dossier):
        return None
    return _manifeste.generer_manifeste(dossier, tag)


def delta_mathlib(tag_source, tag_cible, base=None):
    """Calcule le delta entre deux versions Mathlib installées.

    C'est le cœur du système "malade" : seuls les fichiers dont le hash
    a changé doivent être re-téléchargés.

    Returns:
        dict {"de_version", "vers_version", "ajoutes", "modifies",
              "supprimes", "inchanges"} (voir `delta.calculer_delta`).
    """
    _, _delta = _imports_migration()
    ancien = inventaire_mathlib(tag_source, base=base)
    nouveau = inventaire_mathlib(tag_cible, base=base)
    if nouveau is None:
        raise VersionIntrouvable(
            "Mathlib %s n'est pas installé localement ; "
            "impossible de calculer le delta." % tag_cible
        )
    return _delta.calculer_delta(ancien, nouveau)


def _journal_mathlib(base=None, entree=None):
    """Journal append-only des migrations Mathlib (JSONL)."""
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    chemin = os.path.join(racine, ".journal-mathlib.jsonl")
    if entree is None:
        if not os.path.isfile(chemin):
            return []
        lignes = []
        with open(chemin, "r", encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if ligne:
                    try:
                        lignes.append(json.loads(ligne))
                    except ValueError:
                        pass
        return lignes
    os.makedirs(racine, exist_ok=True)
    import datetime
    entree = dict(entree)
    entree["horodatage"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(entree, ensure_ascii=False) + "\n")


def _lire_pointeur_actif(base=None):
    """Lit le tag Mathlib actif (fichier `.active-mathlib`)."""
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    chemin = os.path.join(racine, ".active-mathlib")
    if not os.path.isfile(chemin):
        return None
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _ecrire_pointeur_actif(tag, base=None):
    """Bascule le pointeur de version active (écriture atomique)."""
    racine = base or os.path.expanduser("~/.cache/phi-complexity/mathlib")
    os.makedirs(racine, exist_ok=True)
    chemin = os.path.join(racine, ".active-mathlib")
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(tag + "\n")
    os.replace(tmp, chemin)


def version_active_mathlib(base=None):
    """Tag Mathlib actif, ou None si aucun."""
    return _lire_pointeur_actif(base=base)


def migrer_mathlib(tag_cible, commit_cible=None, feu_vert=False,
                   base=None, installateur=None):
    """Migre Mathlib vers `tag_cible` avec rollback possible.

    Phases : vérification → installation (via `installateur` ou
    `lake exe cache get`) → validation → bascule du pointeur → journal.

    Args:
        tag_cible: ex "v4.34.1".
        commit_cible: SHA git (résolu via API si None).
        feu_vert: True si Tomy a autorisé le téléchargement lourd.
        base: racine du cache (défaut ~/.cache/phi-complexity/mathlib).
        installateur: fonction(tag, commit, destination) -> None.
            Si None, lève TelechargementRefuse avec les instructions
            (le vrai téléchargement requiert `lake`).

    Returns:
        dict {"tag": ..., "deja_installee": bool, "rollback_possible": bool}.

    L'ancienne version reste intacte jusqu'à validation complète.
    """
    # 1. Vérification : le tag est-il déjà installé ?
    # Marqueur par version (installations côte à côte pour rollback).
    dossier_cible = chemin_cache_mathlib(tag_cible, base=base)
    marqueur_version = os.path.join(dossier_cible, ".valide")
    if os.path.isfile(marqueur_version):
        _journal_mathlib(base, {"action": "migration",
                                "tag": tag_cible,
                                "resultat": "deja_installee"})
        return {"tag": tag_cible, "deja_installee": True,
                "rollback_possible": True}

    # 2. Résoudre le commit si non fourni.
    if commit_cible is None:
        commit_cible = tag_pour_lean(
            _version_lean_associee(tag_cible))["commit"]

    # 3. Feu vert pour le téléchargement lourd.
    plan = {"tag": tag_cible,
            "taille_estimee_octets": 2 * 1024 ** 3,
            "feu_vert_requis": True}
    verifier_feu_vert(plan, feu_vert=feu_vert)

    # 4. Installation.
    if installateur is None:
        raise TelechargementRefuse(
            "aucun installateur fourni pour Mathlib %s. "
            "Utilisez `lake exe cache get` depuis un clone mathlib4 au tag "
            "%s, puis placez les oleans dans %s."
            % (tag_cible, tag_cible, dossier_cible)
        )
    os.makedirs(dossier_cible, exist_ok=True)
    installateur(tag_cible, commit_cible, dossier_cible)

    # 5. Validation : au moins un olean racine présent.
    temoins = [os.path.join(dossier_cible, "Mathlib.olean"),
               os.path.join(dossier_cible, "lib", "lean", "Mathlib.olean")]
    if not any(os.path.isfile(t) for t in temoins):
        raise ErreurMathlib(
            "validation échouée pour Mathlib %s : aucun Mathlib.olean "
            "trouvé dans %s." % (tag_cible, dossier_cible)
        )
    marquer_installee(tag_cible, commit_cible, base=base)
    # Marqueur par version (idempotence des installations côte à côte).
    with open(os.path.join(dossier_cible, ".valide"), "w",
              encoding="utf-8") as f:
        json.dump({"tag": tag_cible, "commit": commit_cible}, f)

    # 6. Bascule + journal.
    ancien_actif = _lire_pointeur_actif(base=base)
    _ecrire_pointeur_actif(tag_cible, base=base)
    _journal_mathlib(base, {"action": "migration",
                            "tag": tag_cible,
                            "commit": commit_cible,
                            "ancien_actif": ancien_actif,
                            "resultat": "ok"})

    return {"tag": tag_cible, "deja_installee": False,
            "rollback_possible": ancien_actif is not None}


def revenir_mathlib(base=None):
    """Rollback vers la version Mathlib précédemment active.

    Lit le journal, rebascule le pointeur. Ne supprime rien.
    """
    journal = _journal_mathlib(base)
    for entree in reversed(journal):
        if (entree.get("action") == "migration"
                and entree.get("resultat") == "ok"
                and entree.get("ancien_actif")):
            cible = entree["ancien_actif"]
            _ecrire_pointeur_actif(cible, base=base)
            _journal_mathlib(base, {"action": "rollback",
                                    "tag": cible,
                                    "resultat": "ok"})
            return {"tag": cible, "resultat": "ok"}
    raise ErreurMathlib("aucune migration précédente à annuler.")


def _version_lean_associee(tag_mathlib):
    """Extrait la version Lean d'un tag Mathlib `vX.Y.Z`."""
    m = re.match(r"^v(\d+\.\d+\.\d+)", tag_mathlib or "")
    if not m:
        raise VersionIntrouvable(
            "tag Mathlib invalide : %r (attendu vX.Y.Z)." % tag_mathlib)
    return m.group(1)
