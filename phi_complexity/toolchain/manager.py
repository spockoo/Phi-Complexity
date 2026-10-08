#!/usr/bin/env python3
"""Orchestrateur : cache → téléchargement → extraction → validation.

Protocole (pré-enregistré) :
1. si le cache contient une installation validée → la réutiliser ;
2. sinon → télécharger l'archive officielle (SHA256 vérifié en flux) ;
3. extraire le sous-ensemble minimal ;
4. valider (`bin/lean --version` contient la version attendue) ;
5. publier (marqueur `.valide` avec le SHA256).

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

from .download import ErreurTelechargement, ErreurVerification, telecharger
from .extract import ErreurExtraction, extraire_minimal
from .validate import ErreurValidation, valider

CHEMIN_DEFAUT_CACHE = os.path.join(
    os.path.expanduser("~"), ".cache", "phi-complexity", "toolchains"
)
MANIFESTE_DEFAUT = os.path.join(os.path.dirname(__file__), "TOOLCHAIN_MANIFEST.json")
NOM_MARQUEUR = ".valide"
NOM_ARCHIVE = "lean.tar.zst"


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

    # ── installation ─────────────────────────────────────

    def installer(self, progression=None):
        """Installe la toolchain (idempotent). Retourne un dict de statut.

        Args:
            progression: callable optionnel(phase, info) où phase ∈
                {"deja_installee", "telechargement", "extraction",
                 "validation", "terminee"}.
        """
        def _sig(phase, info=None):
            if progression is not None:
                progression(phase, info or {})

        if self.est_installee():
            _sig("deja_installee", {"rep": self.rep_install})
            return {
                "statut": "DEJA_INSTALLEE",
                "rep": self.rep_install,
                "version": self.version,
            }

        os.makedirs(self.rep_install, exist_ok=True)
        url = _url_effective(self.manifeste)

        _sig("telechargement", {"url": url})
        telecharger(
            url,
            self.chemin_archive,
            self.manifeste["sha256"],
            progression=lambda n: _sig("telechargement", {"octets": n}),
        )

        _sig("extraction", {"archive": self.chemin_archive})
        extraire_minimal(
            self.chemin_archive,
            self.rep_install,
            progression=lambda nf, no: _sig(
                "extraction", {"fichiers": nf, "octets": no}
            ),
        )

        _sig("validation", {})
        resultat = valider(self.rep_install, self.version)

        with open(self.chemin_marqueur, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": self.version,
                    "sha256": self.manifeste["sha256"],
                    "valide_par": resultat["sortie"],
                },
                f,
            )
        _sig("terminee", {"rep": self.rep_install})
        return {
            "statut": "INSTALLEE",
            "rep": self.rep_install,
            "version": self.version,
            "chemin_lean": resultat["chemin_lean"],
        }

    # ── usage ────────────────────────────────────────────

    def _env(self):
        env = dict(os.environ)
        env["LEAN_PATH"] = self.env_lean_path()
        return env

    def compiler(self, fichier_lean, args_extra=None, timeout_s=300):
        """Élabore `fichier_lean` avec le lean mini.

        Returns:
            {"rc": int, "stdout": str, "stderr": str, "ok": bool}.
        """
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
        """
        return self.compiler(
            fichier_lean,
            args_extra=["--run"] + (args_extra or []),
            timeout_s=timeout_s,
        )
