#!/usr/bin/env python3
"""Orchestrateur : cache → téléchargement → extraction → validation.

Protocole (pré-enregistré) :
1. si le cache contient une installation validée → la réutiliser ;
2. sinon → télécharger l'archive officielle (SHA256 vérifié en flux) ;
3. extraire le sous-ensemble minimal ;
4. valider (`bin/lean --version` contient la version attendue) ;
5. publier (marqueur `.valide` avec le SHA256).

Option `avec_natif=True` : extrait en plus le kit natif (MOTIFS_NATIFS :
leanc + clang embarqué + ld.lld + archives statiques + en-têtes C) et le
valide (`leanc --version`). L'extraction est additive : un appel ultérieur
avec `avec_natif=True` sur une installation minimale existante n'ajoute
que le kit natif (pas de re-téléchargement).

Le marqueur `.valide` rend l'installation idempotente : un second appel
ne retélécharge rien. Supprimer le répertoire de cache force la
réinstallation complète (reprise propre).

Variables d'environnement :
- PHI_TOOLCHAIN_CACHE : répertoire de cache (défaut ~/.cache/...).
- PHI_LEAN_RELEASE_BASE : miroir (remplace le préfixe GitHub du manifeste).
"""

import json
import os
import subprocess

from .delta import calculer_delta, fichiers_a_extraire, resumer_delta
from .download import ErreurTelechargement, ErreurVerification, telecharger
from .extract import (
    ErreurExtraction,
    MOTIFS_EXTENSION_LEAN,
    MOTIFS_EXTENSION_STD,
    MOTIFS_MINIMAUX,
    MOTIFS_NATIFS,
    extraire_fichiers,
    extraire_minimal,
)
from .manifeste import generer_manifeste, lire_manifeste
from .validate import ErreurValidation, valider, valider_extension, valider_natif
from .version import (
    FormatLeanToolchainInvalide,
    VersionNonSupportee,
    lire_version_projet,
)

CHEMIN_DEFAUT_CACHE = os.path.join(
    os.path.expanduser("~"), ".cache", "phi-complexity", "toolchains"
)
MANIFESTE_DEFAUT = os.path.join(os.path.dirname(__file__), "TOOLCHAIN_MANIFEST.json")
NOM_MARQUEUR = ".valide"
NOM_ARCHIVE = "lean.tar.zst"

# Extensions optionnelles : nom -> motifs d'extraction (extract.py).
# Le manifeste miroite ces motifs à titre documentaire (+ tailles mesurées) ;
# un test garde l'égalité stricte entre les deux sources.
EXTENSIONS_MOTIFS = {
    "std": MOTIFS_EXTENSION_STD,
    "lean": MOTIFS_EXTENSION_LEAN,
}


class ToolchainAbsente(Exception):
    """Aucune toolchain installée et aucun téléchargement demandé."""


def _lire_manifeste(chemin=None):
    chemin = chemin or MANIFESTE_DEFAUT
    with open(chemin, "r", encoding="utf-8") as f:
        return json.load(f)


def _url_effective(manifeste):
    base = os.environ.get("PHI_LEAN_RELEASE_BASE")
    url = manifeste["url"]
    if not base:
        return url
    prefixe_defaut = "https://github.com/leanprover/lean4/releases/download/v4.34.0"
    if url.startswith(prefixe_defaut):
        return base.rstrip("/") + url[len(prefixe_defaut):]
    return url


class ToolchainManager:
    """Gère le cycle de vie de la toolchain Lean mini."""

    def __init__(self, cache_dir=None, manifeste=None):
        self.cache_dir = (
            cache_dir
            or os.environ.get("PHI_TOOLCHAIN_CACHE")
            or CHEMIN_DEFAUT_CACHE
        )
        self.manifeste = _lire_manifeste(manifeste)
        self.version = self.manifeste["version"]
        self.rep_install = os.path.join(
            self.cache_dir, "%s-mini" % self.version
        )
        self.chemin_archive = os.path.join(self.rep_install, NOM_ARCHIVE)
        self.chemin_marqueur = os.path.join(self.rep_install, NOM_MARQUEUR)

    # ── état ──────────────────────────────────────────────

    def est_installee(self):
        """True ssi le marqueur .valide existe avec le bon SHA256."""
        if not os.path.isfile(self.chemin_marqueur):
            return False
        try:
            with open(self.chemin_marqueur, "r", encoding="utf-8") as f:
                contenu = json.load(f)
            return (
                contenu.get("sha256") == self.manifeste["sha256"]
                and contenu.get("version") == self.version
                and os.path.isfile(os.path.join(self.rep_install, "bin", "lean"))
            )
        except (OSError, ValueError):
            return False

    def chemin_lean(self):
        """Chemin du binaire lean. Lève ToolchainAbsente si non installée."""
        if not self.est_installee():
            raise ToolchainAbsente(
                "toolchain Lean %s absente — appelez installer() d'abord "
                "(ou `phi lean --init`)" % self.version
            )
        return os.path.join(self.rep_install, "bin", "lean")

    def env_lean_path(self):
        """Valeur à mettre dans LEAN_PATH pour charger Init/."""
        return os.path.join(self.rep_install, "lib", "lean")

    def _natif_marque(self):
        """Lit le drapeau `natif` du marqueur .valide (False si absent)."""
        try:
            with open(self.chemin_marqueur, "r", encoding="utf-8") as f:
                contenu = json.load(f)
            return bool(contenu.get("natif"))
        except (OSError, ValueError):
            return False

    def natif_installe(self):
        """True ssi le kit natif (bin/leanc) est installé et marqué."""
        if not os.path.isfile(os.path.join(self.rep_install, "bin", "leanc")):
            return False
        return self._natif_marque()

    def chemin_leanc(self):
        """Chemin du linker natif leanc. Lève ToolchainAbsente si non installé."""
        if not self.natif_installe():
            raise ToolchainAbsente(
                "kit natif Lean %s absent — appelez installer(avec_natif=True) "
                "d'abord" % self.version
            )
        return os.path.join(self.rep_install, "bin", "leanc")

    # ── installation ─────────────────────────────────────

    def installer(self, progression=None, avec_natif=False):
        """Installe la toolchain (idempotent). Retourne un dict de statut.

        Args:
            progression: callable optionnel(phase, info) où phase ∈
                {"deja_installee", "telechargement", "extraction",
                 "validation", "terminee"}.
            avec_natif: si True, extrait aussi le kit natif (leanc +
                clang embarqué + ld.lld + archives statiques + en-têtes C ;
                +575 Mo) et le valide. L'extraction est additive : sur une
                installation minimale existante, seul le kit natif est
                ajouté (pas de re-téléchargement).
        """
        def _sig(phase, info=None):
            if progression is not None:
                progression(phase, info or {})

        deja = self.est_installee()
        if deja and (not avec_natif or self.natif_installe()):
            _sig("deja_installee", {"rep": self.rep_install})
            return {
                "statut": "DEJA_INSTALLEE",
                "rep": self.rep_install,
                "version": self.version,
            }

        os.makedirs(self.rep_install, exist_ok=True)

        if not deja:
            url = _url_effective(self.manifeste)

            _sig("telechargement", {"url": url})
            telecharger(
                url,
                self.chemin_archive,
                self.manifeste["sha256"],
                progression=lambda n: _sig("telechargement", {"octets": n}),
            )

        _sig("extraction", {"archive": self.chemin_archive,
                            "natif": avec_natif})
        extraire_minimal(
            self.chemin_archive,
            self.rep_install,
            motifs=(MOTIFS_MINIMAUX + MOTIFS_NATIFS) if avec_natif else None,
            progression=lambda nf, no: _sig(
                "extraction", {"fichiers": nf, "octets": no,
                               "natif": avec_natif}
            ),
        )

        _sig("validation", {})
        resultat = valider(self.rep_install, self.version)
        natif_final = avec_natif or self._natif_marque()
        resultat_natif = None
        if avec_natif:
            resultat_natif = valider_natif(self.rep_install)

        with open(self.chemin_marqueur, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": self.version,
                    "sha256": self.manifeste["sha256"],
                    "natif": natif_final,
                    "valide_par": resultat["sortie"],
                },
                f,
            )
        _sig("terminee", {"rep": self.rep_install, "natif": natif_final})
        statut = {
            "statut": "INSTALLEE",
            "rep": self.rep_install,
            "version": self.version,
            "chemin_lean": resultat["chemin_lean"],
            "natif": natif_final,
        }
        if resultat_natif is not None:
            statut["chemin_leanc"] = resultat_natif["chemin_leanc"]
        return statut

    # ── version de projet (lean-toolchain) ───────────────────

    def version_demandee_pour(self, fichier_lean):
        """Version Lean demandée par le projet contenant `fichier_lean`.

        Returns:
            (version, chemin_fichier) — version normalisée (ex. "4.34.0")
            et chemin du lean-toolchain trouvé en remontant les parents ;
            (None, None) si aucun fichier lean-toolchain.

        Raises:
            FormatLeanToolchainInvalide: fichier trouvé mais inparsable.
        """
        dossier = os.path.dirname(os.path.abspath(fichier_lean))
        return lire_version_projet(dossier)

    def verifier_version_projet(self, dossier):
        """Vérifie que la version demandée par le projet est installée.

        Args:
            dossier: répertoire du projet (ou d'un fichier du projet).

        Returns:
            La version demandée (ex. "4.34.0"), ou None si aucun
            fichier lean-toolchain n'est trouvé.

        Raises:
            FormatLeanToolchainInvalide: fichier trouvé mais inparsable.
            VersionNonSupportee: la version demandée diffère de la seule
                version installée (multi-versions = évolution future).
        """
        demandee, chemin = lire_version_projet(dossier)
        if demandee is None:
            return None
        if demandee != self.version:
            raise VersionNonSupportee(demandee, self.version, chemin)
        return demandee

    # ── extensions optionnelles ──────────────────────────

    def extensions_disponibles(self):
        """Noms des extensions déclarées par le manifeste (ex. ["std", "lean"])."""
        return list(self.manifeste.get("extensions", {}).keys())

    def _chemin_marqueur_extension(self, nom):
        return os.path.join(self.rep_install, ".valide-ext-%s" % nom)

    def _verifier_nom_extension(self, nom):
        disponibles = self.extensions_disponibles()
        if nom not in disponibles:
            raise ValueError(
                "extension inconnue : %r (disponibles : %s)"
                % (nom, ", ".join(disponibles) or "aucune")
            )
        return self.manifeste["extensions"][nom]

    def extension_installee(self, nom):
        """True ssi le marqueur `.valide-ext-<nom>` existe avec le bon SHA256."""
        self._verifier_nom_extension(nom)
        chemin = self._chemin_marqueur_extension(nom)
        if not os.path.isfile(chemin):
            return False
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                contenu = json.load(f)
            return (
                contenu.get("sha256") == self.manifeste["sha256"]
                and contenu.get("version") == self.version
                and contenu.get("extension") == nom
            )
        except (OSError, ValueError):
            return False

    def installer_extension(self, nom, progression=None):
        """Installe une extension optionnelle (idempotent).

        L'extension s'extrait de l'archive officielle DÉJÀ en cache
        (aucun téléchargement supplémentaire si elle y est ; sinon elle
        est re-téléchargée avec vérification SHA256), puis est validée
        par élaboration réelle (`import <Racine>`).

        Args:
            nom: "std" (`import Std`, +290 Mo) ou "lean"
                (`import Lean`, +1,2 Go — inclut Std par fermeture).
            progression: callable optionnel(phase, info) où phase ∈
                {"deja_installee", "telechargement", "extraction",
                 "validation", "extension_extraction",
                 "extension_validation", "terminee"}.

        Returns:
            dict de statut (statut ∈ {"DEJA_INSTALLEE",
            "EXTENSION_INSTALLEE"}).

        Raises:
            ValueError: nom d'extension inconnu.
        """
        def _sig(phase, info=None):
            if progression is not None:
                progression(phase, info or {})

        meta = self._verifier_nom_extension(nom)

        if self.extension_installee(nom):
            _sig("deja_installee", {"rep": self.rep_install,
                                    "extension": nom})
            return {
                "statut": "DEJA_INSTALLEE",
                "rep": self.rep_install,
                "extension": nom,
            }

        # La base d'abord (idempotent : ne re-télécharge pas si présente).
        if not self.est_installee():
            self.installer(progression=progression)

        # L'archive a pu disparaître du cache : la re-télécharger si besoin.
        if not os.path.isfile(self.chemin_archive):
            url = _url_effective(self.manifeste)
            _sig("telechargement", {"url": url, "extension": nom})
            telecharger(
                url,
                self.chemin_archive,
                self.manifeste["sha256"],
                progression=lambda n: _sig(
                    "telechargement", {"octets": n, "extension": nom}),
            )

        _sig("extension_extraction", {"extension": nom,
                                      "archive": self.chemin_archive})
        extraire_minimal(
            self.chemin_archive,
            self.rep_install,
            motifs=EXTENSIONS_MOTIFS[nom],
            progression=lambda nf, no: _sig(
                "extension_extraction",
                {"extension": nom, "fichiers": nf, "octets": no},
            ),
        )

        _sig("extension_validation", {"extension": nom})
        resultat = valider_extension(
            self.rep_install, meta["module_racine"])

        with open(self._chemin_marqueur_extension(nom), "w",
                  encoding="utf-8") as f:
            json.dump(
                {
                    "version": self.version,
                    "sha256": self.manifeste["sha256"],
                    "extension": nom,
                    "valide_par": resultat,
                },
                f,
            )
        _sig("terminee", {"rep": self.rep_install, "extension": nom})
        return {
            "statut": "EXTENSION_INSTALLEE",
            "rep": self.rep_install,
            "version": self.version,
            "extension": nom,
            "module_racine": meta["module_racine"],
        }

    # ── mise à jour par delta ────────────────────────────────

    def plan_mise_a_jour(self, nouveau_manifeste):
        """Calcule le delta vers une nouvelle version SANS télécharger.

        Args:
            nouveau_manifeste: dict avec "version", "url", "sha256"
                (manifeste de release, pas par fichier).

        Returns:
            dict: {"version_actuelle": ..., "version_cible": ...,
                   "url": ..., "sha256": ..., "taille_tarball": ...}
            Le delta par fichier ne peut être calculé qu'après
            téléchargement (les hashes par fichier ne sont pas publiés).

        Note honnête : le tarball complet doit être téléchargé — les
        releases Lean ne fournissent pas de delta officiel. Le gain du
        système de patch est sur l'extraction (I/O disque) et la
        traçabilité, pas sur la bande passante.
        """
        return {
            "version_actuelle": self.version if self.est_installee() else None,
            "version_cible": nouveau_manifeste["version"],
            "url": nouveau_manifeste["url"],
            "sha256": nouveau_manifeste["sha256"],
            "taille_tarball": nouveau_manifeste.get("taille_octets_approx"),
            "note": (
                "Le tarball complet sera téléchargé (pas de delta officiel). "
                "Seule l'extraction sera sélective."
            ),
        }

    def mettre_a_jour(self, nouveau_manifeste, progression=None):
        """Met à jour vers une nouvelle version avec extraction sélective.

        Protocole :
        1. Télécharge le nouveau tarball (complet — pas de delta officiel).
        2. Extrait le minimal vers un répertoire temporaire.
        3. Génère le manifeste par fichier de la nouvelle version.
        4. Calcule le delta avec le manifeste de la version installée.
        5. Crée le nouveau répertoire de version :
           - fichiers inchangés : lien physique depuis l'ancienne version
             (zéro copie, instantané) ;
           - fichiers ajoutés/modifiés : copie depuis l'extraction temp.
        6. Supprime les fichiers obsolètes (non repris).
        7. Valide et marque la nouvelle version.

        L'ancienne version reste intacte (rollback possible par simple
        changement de répertoire).

        Args:
            nouveau_manifeste: dict avec "version", "url", "sha256".
            progression: callable optionnel(phase, info).

        Returns:
            dict: {"statut": "MIS_A_JOUR", "delta": {...}, ...}.
        """
        import shutil
        import tempfile

        def _sig(phase, info=None):
            if progression is not None:
                progression(phase, info or {})

        ancienne_version = self.version if self.est_installee() else None
        nouvel_version = nouveau_manifeste["version"]
        url = nouveau_manifeste["url"]
        sha256 = nouveau_manifeste["sha256"]

        # Si déjà à jour, ne rien faire.
        if ancienne_version == nouvel_version and self.est_installee():
            _sig("deja_a_jour", {"version": nouvel_version})
            return {"statut": "DEJA_A_JOUR", "version": nouvel_version}

        _sig("telechargement", {"url": url, "version": nouvel_version})
        rep_temp = tempfile.mkdtemp(prefix="phi-lean-update-")
        archive_temp = os.path.join(rep_temp, "lean.tar.zst")
        try:
            telecharger(
                url, archive_temp, sha256,
                progression=lambda n: _sig("telechargement", {"octets": n}),
            )

            # Extraire le nouveau minimal vers un temp.
            _sig("extraction_nouvelle", {"version": nouvel_version})
            rep_nouveau = os.path.join(rep_temp, "nouveau")
            extraire_minimal(
                archive_temp, rep_nouveau,
                progression=lambda nf, no: _sig(
                    "extraction_nouvelle", {"fichiers": nf, "octets": no}),
            )

            # Manifeste de la nouvelle version.
            _sig("manifeste", {})
            manif_nouveau = generer_manifeste(rep_nouveau, nouvel_version)

            # Delta avec l'ancien.
            _sig("delta", {})
            if ancienne_version and self.est_installee():
                manif_ancien = lire_manifeste(self.rep_install)
                ancien_rep = self.rep_install
            else:
                manif_ancien = None
                ancien_rep = None
            delta = calculer_delta(manif_ancien, manif_nouveau)
            _sig("delta", {"resume": resumer_delta(delta)})

            # Nouveau répertoire de version.
            nouveau_rep = os.path.join(
                self.cache_dir, "%s-mini" % nouvel_version)
            os.makedirs(nouveau_rep, exist_ok=True)

            # Fichiers inchangés : lien physique depuis l'ancien (rapide).
            # Ajoutés/modifiés : copie depuis l'extraction temp.
            _sig("application", {})
            n_lies = 0
            n_copies = 0
            tous_nouveaux = set(manif_nouveau["fichiers"].keys())
            a_extraire = set(fichiers_a_extraire(delta))
            for relatif in sorted(tous_nouveaux):
                dst = os.path.join(nouveau_rep, relatif)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if relatif in a_extraire:
                    # Ajouté ou modifié : depuis l'extraction temp.
                    src = os.path.join(rep_nouveau, relatif)
                    shutil.copy2(src, dst)
                    n_copies += 1
                elif ancien_rep:
                    # Inchangé : lien physique (pas de copie).
                    src = os.path.join(ancien_rep, relatif)
                    if os.path.exists(src) and not os.path.exists(dst):
                        try:
                            os.link(src, dst)
                            n_lies += 1
                        except OSError:
                            # Filesystem sans hardlinks : copie.
                            shutil.copy2(src, dst)
                            n_copies += 1
            _sig("application", {"lies": n_lies, "copies": n_copies})

            # Manifeste par fichier dans la nouvelle installation.
            generer_manifeste(nouveau_rep, nouvel_version)

            # Valider et marquer.
            _sig("validation", {})
            resultat = valider(nouveau_rep, nouvel_version)
            marqueur = os.path.join(nouveau_rep, ".valide")
            with open(marqueur, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": nouvel_version,
                        "sha256": sha256,
                        "valide_par": resultat["sortie"],
                        "mis_a_jour_depuis": ancienne_version,
                    },
                    f,
                )

            # Basculer le manager vers la nouvelle version.
            self.manifeste = nouveau_manifeste
            self.version = nouvel_version
            self.rep_install = nouveau_rep
            self.chemin_archive = os.path.join(nouveau_rep, "lean.tar.zst")
            self.chemin_marqueur = marqueur
            # Conserver l'archive pour les extensions futures.
            shutil.copy2(archive_temp,
                         os.path.join(nouveau_rep, "lean.tar.zst"))

            _sig("terminee", {"version": nouvel_version, "delta": delta})
            return {
                "statut": "MIS_A_JOUR",
                "de_version": ancienne_version,
                "vers_version": nouvel_version,
                "delta": delta,
                "fichiers_lies": n_lies,
                "fichiers_copies": n_copies,
            }
        finally:
            shutil.rmtree(rep_temp, ignore_errors=True)

    # ── usage ────────────────────────────────────────────

    def _env(self):
        env = dict(os.environ)
        env["LEAN_PATH"] = self.env_lean_path()
        return env
    def compiler(self, fichier_lean, args_extra=None, timeout_s=300):
        """Élabore `fichier_lean` avec le lean mini.

        Returns:
            {"rc": int, "stdout": str, "stderr": str, "ok": bool}.

        Raises:
            VersionNonSupportee: si le projet du fichier demande une autre
                version de Lean que celle installée.
        """
        self.verifier_version_projet(os.path.dirname(os.path.abspath(fichier_lean)))
        binaire = self.chemin_lean()
        cmd = [binaire] + (args_extra or []) + [fichier_lean]
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout_s, env=self._env(),
        )
        return {
            "rc": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "ok": proc.returncode == 0,
        }

    def executer(self, fichier_lean, args_extra=None, timeout_s=300):
        """Exécute `fichier_lean` via `lean --run`.

        Returns:
            {"rc": int, "stdout": str, "stderr": str, "ok": bool}.

        Raises:
            VersionNonSupportee: si le projet du fichier demande une autre
                version de Lean que celle installée.
        """
        return self.compiler(
            fichier_lean,
            args_extra=["--run"] + (args_extra or []),
            timeout_s=timeout_s,
        )
