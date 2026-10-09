#!/usr/bin/env python3
"""Couche migration multi-versions pour la toolchain Lean mini.

Le gestionnaire mono-version (``ToolchainManager``, manager.py) sait
installer UNE version. Ce module ajoute la couche qui en gère PLUSIEURS
dans le même cache :

- pointeur de version active (fichier ``.active`` dans le cache) ;
- registre des versions connues (SHA256 épinglés, VERSIONS_CONNUES.json) ;
- construction de manifestes par version (gabarit TOOLCHAIN_MANIFEST.json) ;
- journal append-only des migrations/basculles/nettoyages (JSONL) ;
- rollback vers la version précédente ;
- nettoyage des versions obsolètes (confirmation explicite requise) ;
- détection des projets lean-toolchain utilisant une version donnée.

Conventions :
- statuts typés : exceptions typées et dicts de statut, jamais de
  booléen nu ;
- aucun téléchargement sans SHA256 épinglé dans le registre
  (``VersionInconnue``) et aucun téléchargement >100 Mo sans confirmation
  explicite (``TelechargementRefuse``, gouvernance Mathlib).

Variables d'environnement :
- PHI_TOOLCHAIN_CACHE : répertoire de cache (défaut
  ~/.cache/phi-complexity/toolchains).
"""

import json
import os
import shutil
from datetime import datetime, timezone

from .manager import CHEMIN_DEFAUT_CACHE, ToolchainManager
from .mathlib import TelechargementRefuse
from .version import (
    NOM_FICHIER as NOM_FICHIER_LEAN_TOOLCHAIN,
    FormatLeanToolchainInvalide,
    analyser_contenu,
    normaliser_version,
)

NOM_ACTIF = ".active"
NOM_JOURNAL = ".journal-migrations.jsonl"
REGISTRE_VERSIONS = os.path.join(os.path.dirname(__file__),
                                 "VERSIONS_CONNUES.json")
GABARIT_MANIFESTE = os.path.join(os.path.dirname(__file__),
                                 "TOOLCHAIN_MANIFEST.json")

# Seuil de gouvernance : au-delà, un feu vert explicite est requis.
SEUIL_CONFIRMATION_OCTETS = 100_000_000

# Répertoires ignorés par projets_utilisant (caches, builds, VCS).
REPERTOIRES_IGNORES = {
    ".git", ".lake", "build", "__pycache__", ".cache", "node_modules",
}


class VersionInconnue(Exception):
    """Version cible sans SHA256 épinglé dans le registre.

    Attributs : ``version`` (version demandée, normalisée).
    """


class MigrationImpossible(Exception):
    """La migration ne peut pas procéder (message explicite)."""


class RollbackImpossible(Exception):
    """Aucune migration précédente à annuler."""


def _lire_registre():
    with open(REGISTRE_VERSIONS, "r", encoding="utf-8") as f:
        return json.load(f)


def _gabarit_manifeste():
    """Charge le gabarit TOOLCHAIN_MANIFEST.json (copie indépendante)."""
    with open(GABARIT_MANIFESTE, "r", encoding="utf-8") as f:
        gabarit = json.load(f)
    # Copie profonde via JSON : le gabarit ne contient que des scalaires.
    return json.loads(json.dumps(gabarit))


def manifeste_pour_version(version):
    """Construit un manifeste complet pour une version connue.

    Charge le gabarit TOOLCHAIN_MANIFEST.json puis remplace
    version/url/sha256/taille par les valeurs du registre
    VERSIONS_CONNUES.json.

    Args:
        version: ex. "4.34.1" (le préfixe "v" est accepté et normalisé).

    Returns:
        dict : manifeste prêt pour ToolchainManager(manifeste=...).

    Raises:
        VersionInconnue: version absente du registre.
    """
    version = normaliser_version(str(version))
    registre = _lire_registre()
    if version not in registre:
        raise VersionInconnue(
            "version Lean %r inconnue : aucun SHA256 épinglé dans "
            "%s. Ajoutez-la au registre avec son SHA256 officiel avant "
            "toute migration." % (version, REGISTRE_VERSIONS)
        )
    entree = registre[version]
    manifeste = _gabarit_manifeste()
    manifeste["version"] = version
    manifeste["url"] = entree["url"]
    manifeste["sha256"] = entree["sha256"]
    manifeste["taille_octets_approx"] = entree["taille_octets_approx"]
    return manifeste


class GestionnaireVersions:
    """Gère plusieurs versions Lean installées dans un même cache."""

    def __init__(self, cache_dir=None):
        self.cache_dir = (
            cache_dir
            or os.environ.get("PHI_TOOLCHAIN_CACHE")
            or CHEMIN_DEFAUT_CACHE
        )

    # ── inventaire ─────────────────────────────────────

    def versions_installees(self):
        """Scanne cache_dir pour les installations ``<version>-mini``.

        Vérifie pour chaque dossier ``<version>-mini`` :
        1. le marqueur ``.valide`` existe ;
        2. le marqueur est un JSON valide ;
        3. ``contenu["version"]`` == version du dossier ;
        4. ``bin/lean`` existe.

        Returns:
            list[dict] : chaque dict vaut {"version", "rep", "sha256",
            "natif" (bool), "mis_a_jour_depuis" (None si absent)}.
            Triée par version croissante.
        """
        trouvees = []
        if not os.path.isdir(self.cache_dir):
            return trouvees
        for nom in sorted(os.listdir(self.cache_dir)):
            if not nom.endswith("-mini"):
                continue
            version = nom[: -len("-mini")]
            rep = os.path.join(self.cache_dir, nom)
            if not os.path.isdir(rep):
                continue
            chemin_marqueur = os.path.join(rep, ".valide")
            chemin_lean = os.path.join(rep, "bin", "lean")
            try:
                with open(chemin_marqueur, "r", encoding="utf-8") as f:
                    contenu = json.load(f)
            except (OSError, ValueError):
                continue
            if contenu.get("version") != version:
                continue
            if not os.path.isfile(chemin_lean):
                continue
            trouvees.append({
                "version": version,
                "rep": rep,
                "sha256": contenu.get("sha256"),
                "natif": bool(contenu.get("natif")),
                "mis_a_jour_depuis": contenu.get("mis_a_jour_depuis"),
            })
        return trouvees

    # ── version active ─────────────────────────────────

    def version_active(self):
        """Version Lean active, ou None si aucune.

        Ordre de résolution :
        1. le fichier ``.active`` s'il existe ET pointe vers une version
           installée ;
        2. sinon la version du manifeste embarqué si elle est installée
           (comportement historique mono-version) ;
        3. sinon None.
        """
        installees = {v["version"] for v in self.versions_installees()}
        chemin_actif = os.path.join(self.cache_dir, NOM_ACTIF)
        if os.path.isfile(chemin_actif):
            try:
                with open(chemin_actif, "r", encoding="utf-8") as f:
                    pointe = normaliser_version(f.read().strip())
                if pointe in installees:
                    return pointe
            except OSError:
                pass
        manifeste = _gabarit_manifeste()
        version_manifeste = manifeste.get("version")
        if version_manifeste in installees:
            return version_manifeste
        return None

    def definir_active(self, version, raison=""):
        """Bascule le pointeur actif vers `version`.

        Args:
            version: version déjà installée (ex. "4.34.1").
            raison: justification enregistrée au journal.

        Returns:
            {"de": <ancienne version ou None>, "vers": <version>}.

        Raises:
            MigrationImpossible: version non installée.
        """
        version = normaliser_version(str(version))
        installees = {v["version"] for v in self.versions_installees()}
        if version not in installees:
            raise MigrationImpossible(
                "impossible de définir la version active à %r : elle n'est "
                "pas installée dans %s (versions installées : %s)"
                % (version, self.cache_dir,
                   ", ".join(sorted(installees)) or "aucune")
            )
        de = self.version_active()
        with open(os.path.join(self.cache_dir, NOM_ACTIF), "w",
                  encoding="utf-8") as f:
            f.write(version + "\n")
        self._journaliser({
            "evenement": "bascule",
            "de": de,
            "vers": version,
            "raison": raison,
        })
        return {"de": de, "vers": version}

    def manager_actif(self):
        """ToolchainManager configuré pour la version active.

        - Si aucune version active : ToolchainManager(cache_dir) par défaut
          (manifeste embarqué — comportement historique).
        - Si la version active n'est PAS au registre : le manifeste est
          reconstruit depuis le marqueur ``.valide`` (SHA256 mesuré) +
          le gabarit (fichiers_minimaux etc.). L'URL d'origine n'étant
          pas enregistrée dans le marqueur, celle du gabarit est
          conservée — elle ne sert qu'en cas de re-téléchargement, qui
          re-vérifiera le SHA256.
        """
        active = self.version_active()
        if active is None:
            return ToolchainManager(self.cache_dir)
        try:
            manifeste = manifeste_pour_version(active)
        except VersionInconnue:
            installees = {v["version"]: v
                          for v in self.versions_installees()}
            info = installees[active]  # garantie installée par version_active
            manifeste = _gabarit_manifeste()
            manifeste["version"] = active
            manifeste["sha256"] = info["sha256"]
        return ToolchainManager(self.cache_dir, manifeste=manifeste)

    # ── migration ──────────────────────────────────────

    def migrer(self, version_cible, progression=None, raison="",
               confirmer=None):
        """Migre vers une version cible (téléchargement si nécessaire).

        Protocole :
        1. normaliser version_cible ; si == active → {"statut": "DEJA_ACTIVE"} ;
        2. manifeste_pour_version (lève VersionInconnue) ;
        3. si pas installée :
           - si taille > 100_000_000 et confirmer fourni :
             appeler confirmer(version, taille) ; si False → journaliser
             le refus et lever TelechargementRefuse ;
           - installer via
             ToolchainManager(cache_dir, manifeste=manifeste_dict).installer()
             (``installer()``, pas ``mettre_a_jour()`` : l'ancienne version
             reste intacte, sans mutation d'état) ;
        4. definir_active(version_cible, raison) ;
        5. journaliser l'événement "migration".

        Args:
            version_cible: ex. "4.34.1" (préfixe "v" accepté).
            progression: callable optionnel(phase, info) transmis à installer().
            raison: justification enregistrée au journal.
            confirmer: None (pas de demande de confirmation) ou
                callable(version, taille_octets) -> True pour autoriser.

        Returns:
            {"statut": "MIGREE", "de": ..., "vers": ..., "telechargee": bool}.

        Raises:
            VersionInconnue: cible absente du registre.
            TelechargementRefuse: confirmer a refusé.
        """
        cible = normaliser_version(str(version_cible))
        de = self.version_active()
        if cible == de:
            return {"statut": "DEJA_ACTIVE"}

        manifeste = manifeste_pour_version(cible)  # lève VersionInconnue

        installee = any(v["version"] == cible
                        for v in self.versions_installees())
        telechargee = False
        if not installee:
            taille = manifeste.get("taille_octets_approx") or 0
            if taille > SEUIL_CONFIRMATION_OCTETS and confirmer is not None:
                if not confirmer(cible, taille):
                    self._journaliser({
                        "evenement": "refus",
                        "vers": cible,
                        "raison": raison,
                    })
                    taille_go = taille / (1024 ** 3)
                    raise TelechargementRefuse(
                        "migration vers Lean %s refusée (téléchargement "
                        "estimé à %.1f Go). Feu vert explicite requis avant "
                        "tout téléchargement >100 Mo." % (cible, taille_go)
                    )
            gestionnaire = ToolchainManager(self.cache_dir,
                                            manifeste=manifeste)
            gestionnaire.installer(progression)
            telechargee = True

        resultat = self.definir_active(cible, raison)
        self._journaliser({
            "evenement": "migration",
            "de": resultat["de"],
            "vers": cible,
            "raison": raison,
            "telechargee": telechargee,
        })
        return {
            "statut": "MIGREE",
            "de": resultat["de"],
            "vers": cible,
            "telechargee": telechargee,
        }

    # ── rollback ───────────────────────────────────────

    def revenir(self, raison="rollback manuel"):
        """Annule la dernière migration/bascule (retour à la version
        précédente).

        Parcourt le journal du plus récent au plus ancien et trouve le
        dernier événement "migration"/"bascule" dont "de" est une version
        installée différente de l'active. Bascule vers elle.

        Returns:
            {"de": <ancienne active>, "vers": <cible>} (comme definir_active).

        Raises:
            RollbackImpossible: aucune migration annulable trouvée.
        """
        active = self.version_active()
        installees = {v["version"] for v in self.versions_installees()}
        for entree in self.journal(limite=10_000):
            if entree.get("evenement") not in ("migration", "bascule"):
                continue
            cible = entree.get("de")
            if cible in installees and cible != active:
                return self.definir_active(cible, raison)
        raise RollbackImpossible(
            "aucune migration précédente à annuler : le journal ne contient "
            "aucun événement migration/bascule dont la version d'origine "
            "est installée et différente de l'active (%s)" % (active,)
        )

    # ── nettoyage ──────────────────────────────────────

    def nettoyer(self, garder=None, confirmer=None):
        """Supprime les versions installées non actives et non protégées.

        Args:
            garder: iterable de versions à conserver en plus de l'active.
            confirmer: None (refus : le CLI doit demander confirmation
                explicite → MigrationImpossible) ou callable(versions) ->
                True pour confirmer la suppression.

        Returns:
            {"statut": "NETTOYE", "supprimees": [...]} en cas de succès ;
            {"statut": "RIEN_A_NETTOYER", "supprimees": []} si rien à faire ;
            {"statut": "NETTOYAGE_ANNULE", "supprimees": []} si refusé.

        Raises:
            MigrationImpossible: suppressions nécessaires mais
                confirmer is None.
        """
        garder = {normaliser_version(str(v)) for v in (garder or [])}
        active = self.version_active()
        a_supprimer = sorted(
            v["version"] for v in self.versions_installees()
            if v["version"] != active and v["version"] not in garder
        )
        if not a_supprimer:
            return {"statut": "RIEN_A_NETTOYER", "supprimees": []}
        if confirmer is None:
            raise MigrationImpossible(
                "nettoyage de %s : confirmation explicite requise "
                "(passer confirmer=callable) — la suppression de versions "
                "installées ne se fait jamais sans ordre explicite."
                % (", ".join(a_supprimer),)
            )
        if not confirmer(a_supprimer):
            return {"statut": "NETTOYAGE_ANNULE", "supprimees": []}
        for version in a_supprimer:
            shutil.rmtree(os.path.join(self.cache_dir,
                                       "%s-mini" % version),
                          ignore_errors=True)
        self._journaliser({
            "evenement": "nettoyage",
            "supprimees": a_supprimer,
        })
        return {"statut": "NETTOYE", "supprimees": a_supprimer}

    # ── détection de projets ───────────────────────────

    def projets_utilisant(self, version, racine):
        """Trouve les projets lean-toolchain demandant `version`.

        Parcourt `racine` (os.walk) en excluant .git/.lake/build/
        __pycache__/.cache/node_modules. Chaque fichier lean-toolchain
        trouvé est analysé avec analyser_contenu (fichiers inparsables
        ignorés).

        Args:
            version: ex. "4.34.0" (préfixe "v" accepté).
            racine: répertoire racine de la recherche.

        Returns:
            list[dict] : [{"projet": dirpath, "fichier": chemin,
            "version_demandee": ...}] pour les projets dont la version
            demandée == version normalisée.
        """
        version = normaliser_version(str(version))
        trouves = []
        for dirpath, dirnames, filenames in os.walk(racine):
            dirnames[:] = [d for d in dirnames
                           if d not in REPERTOIRES_IGNORES]
            if NOM_FICHIER_LEAN_TOOLCHAIN not in filenames:
                continue
            chemin = os.path.join(dirpath, NOM_FICHIER_LEAN_TOOLCHAIN)
            try:
                with open(chemin, "r", encoding="utf-8") as f:
                    demandee = analyser_contenu(f.read(), chemin)
            except (OSError, FormatLeanToolchainInvalide):
                continue
            if demandee == version:
                trouves.append({
                    "projet": dirpath,
                    "fichier": chemin,
                    "version_demandee": demandee,
                })
        return trouves

    # ── journal ────────────────────────────────────────

    def journal(self, limite=20):
        """Lit le journal JSONL : les `limite` dernières entrées,
        plus récentes d'abord. Les lignes vides/invalides sont ignorées."""
        chemin = os.path.join(self.cache_dir, NOM_JOURNAL)
        if not os.path.isfile(chemin):
            return []
        entrees = []
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                for ligne in f:
                    ligne = ligne.strip()
                    if not ligne:
                        continue
                    try:
                        entrees.append(json.loads(ligne))
                    except ValueError:
                        continue
        except OSError:
            return []
        return list(reversed(entrees[-limite:]))

    def _journaliser(self, entree):
        """Ajoute {"date": ISO8601, **entree} au journal (append JSONL)."""
        horodate = datetime.now(timezone.utc).astimezone().isoformat()
        ligne = json.dumps({"date": horodate, **entree},
                           ensure_ascii=False)
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, NOM_JOURNAL), "a",
                  encoding="utf-8") as f:
            f.write(ligne + "\n")
