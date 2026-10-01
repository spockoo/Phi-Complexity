"""
__init__.py — API publique de phi-complexity.
Expose les fonctions de haut niveau pour une utilisation simple, multi-langage.
"""
from typing import Optional

from .core import PHI, TAXE_SUTURE, ETA_GOLDEN, VERSION, AUTEUR, FRAMEWORK
from .analyseur import AnalyseurPhi
from .metriques import CalculateurRadiance
from .rapport import GenerateurRapport
from .bayes import MoteurInferenceBayesienne, DiagnosticBayesien
from .carte import carte_projet, carte_console
from .croyances import chemins_croyants, chemins_console
from .veille import prendre_snapshot, ecrire_snapshot, comparer, veille_console
from .langs import (
    obtenir_analyseur,
    langage_pour_extension,
    est_fichier_supporte,
    EXTENSIONS_SUPPORTEES,
)


def auditer(fichier: str, lang: Optional[str] = None) -> dict:
    """
    Lance un audit complet sur un fichier source, quel que soit son langage.

    Le langage est déduit automatiquement de l'extension du fichier. Pour un
    langage non détectable automatiquement (ou pour forcer un choix), passez
    `lang="javascript"`, `lang="java"`, etc. (voir `phi_complexity.langs`).
    Le Python est toujours traité par l'analyseur natif (zéro dépendance) ;
    les autres langages nécessitent `pip install phi-complexity[multilang]`.

    Usage:
        from phi_complexity import auditer
        result = auditer("mon_script.py")
        print(result["radiance"])  # → 82.4

        result = auditer("app.js")            # détection auto par extension
        result = auditer("script", lang="ruby")  # langage forcé
    """
    analyseur = obtenir_analyseur(fichier, langage=lang)
    resultat = analyseur.analyser()
    calculateur = CalculateurRadiance(resultat)
    return calculateur.calculer()


def rapport_console(fichier: str, lang: Optional[str] = None) -> str:
    """Retourne le rapport ASCII formaté pour le terminal."""
    metriques = auditer(fichier, lang=lang)
    return GenerateurRapport(metriques).console()


def rapport_markdown(fichier: str, sortie: str = None, lang: Optional[str] = None) -> str:
    """
    Génère un rapport Markdown.
    Si `sortie` est spécifié, sauvegarde dans ce fichier.
    Retourne le contenu Markdown.
    """
    metriques = auditer(fichier, lang=lang)
    gen = GenerateurRapport(metriques)
    if sortie:
        gen.sauvegarder_markdown(sortie)
    return gen.markdown()


def rapport_json(fichier: str, lang: Optional[str] = None) -> str:
    """Retourne le rapport JSON pour CI/CD."""
    metriques = auditer(fichier, lang=lang)
    return GenerateurRapport(metriques).json()


def rapport_sarif(fichier: str, lang: Optional[str] = None) -> str:
    """Retourne le rapport au format standard OASIS SARIF v2.1.0."""
    metriques = auditer(fichier, lang=lang)
    return GenerateurRapport(metriques).sarif()

def evaluer_formule(fichier: str, expression: str, lang: Optional[str] = None) -> float:
    """
    Évalue une formule arithmétique personnalisée sur les métriques du fichier.
    Le langage supporte les métriques d'audit comme identifiants, les constantes
    phi/pi/e, les opérateurs + - * / ^ (puissance, associatif à droite),
    les comparaisons et les fonctions sqrt/abs/log/log2/exp.

    Ex : evaluer_formule("mon.py", "100 - lilith_variance/phi - shannon_entropy^2")
    """
    from .formules import construire_env, evaluer_formule as _ev
    metriques = auditer(fichier, lang=lang)
    return _ev(expression, construire_env(metriques))


def evaluer_gate(fichier: str, expression: str, lang: Optional[str] = None) -> bool:
    """
    Évalue une porte logique de qualité sur les métriques du fichier.
    Rend True si la porte est ouverte (le fichier passe la porte).

    Ex : evaluer_gate("mon.py", "radiance >= 75 and lilith_variance < 1200")
    """
    from .formules import construire_env, evaluer_gate as _eg
    metriques = auditer(fichier, lang=lang)
    return _eg(expression, construire_env(metriques))


def editer(fichier: str, projet: Optional[str] = None, lang: Optional[str] = None) -> int:
    """
    Ouvre l'éditeur de texte phi (interface plein-écran curses) sur `fichier`.

    `projet` : dossier racine pour l'index des symboles (défaut : dossier
    parent du fichier). `lang` : force le langage pour l'audit phi.
    Retourne 0 à la sortie normale de l'éditeur.

    Usage:
        from phi_complexity import editer
        editer("mon_script.py")
    """
    from .editeur import lancer_editeur
    return lancer_editeur(fichier, projet=projet, lang=lang)


def diagnostic_bayesien(fichier: str, lang: Optional[str] = None,
                        exact: Optional[bool] = None,
                        traceur: Optional["TraceurOracle"] = None) -> DiagnosticBayesien:
    """
    Exécute l'inférence bayésienne sur le fichier pour identifier la pathologie dominante
    et calculer le gain espéré de Radiance E[ΔR | Action].

    `exact` : arithmétique double-double certifiée (EFT). None → PHI_EFT.
    `traceur` : TraceurOracle optionnel (traces dans les deux modes).
    """
    metriques = auditer(fichier, lang=lang)
    moteur = MoteurInferenceBayesienne(metriques, exact=exact,
                                       traceur=traceur, cible=fichier)
    return moteur.inferer()


def auditer_projet(dossier: str) -> dict:
    """
    Audite récursivement l'ensemble d'un projet/dossier.
    Calcule la matrice d'orchestration globale :
    - Radiance macroscopique moyenne
    - Identification de l'Oudjat Suprême du projet
    - Variance inter-fichiers et indice d'antifragilité systémique
    """
    from .cli import _collecter_fichiers
    fichiers = _collecter_fichiers(dossier)
    if not fichiers:
        return {
            "dossier": dossier,
            "nb_fichiers": 0,
            "radiance_globale": 60.0,
            "fichiers": [],
            "oudjat_supreme": None
        }

    rapports_fichiers = []
    tous_oudjats = []
    radiances = []

    for f in fichiers:
        try:
            m = auditer(f)
            rapports_fichiers.append(m)
            radiances.append(m["radiance"])
            if m.get("oudjat"):
                o = dict(m["oudjat"])
                o["fichier"] = f
                tous_oudjats.append(o)
        except Exception:
            continue

    if not radiances:
        return {"dossier": dossier, "nb_fichiers": 0, "radiance_globale": 60.0, "fichiers": []}

    rad_globale = sum(radiances) / len(radiances)
    oudjat_supreme = max(tous_oudjats, key=lambda x: x["complexite"]) if tous_oudjats else None

    return {
        "dossier": dossier,
        "nb_fichiers": len(rapports_fichiers),
        "radiance_globale": round(rad_globale, 2),
        "statut_gnostique_global": GenerateurRapport({"radiance": rad_globale}).m["statut_gnostique"] if False else (
            "HERMÉTIQUE ✦" if rad_globale >= 85 else "EN ÉVEIL ◈" if rad_globale >= 60 else "DORMANT ░"
        ),
        "oudjat_supreme": oudjat_supreme,
        "fichiers": rapports_fichiers
    }


def rapport_matrice_console(dossier: str) -> str:
    """Affiche la Matrice d'Orchestration globale d'un projet dans le terminal."""
    proj = auditer_projet(dossier)
    lignes = [
        "╔════════════════════════════════════════════════════════════════════╗",
        "║      PHI-COMPLEXITY — MATRICE D'ORCHESTRATION DU PROJET            ║",
        "╚════════════════════════════════════════════════════════════════════╝",
        "",
        f"  📁 Dossier   : {proj['dossier']}",
        f"  📚 Fichiers  : {proj['nb_fichiers']}",
        f"  ☼  RADIANCE  : {proj['radiance_globale']} / 100 ({proj['statut_gnostique_global']})",
        "",
    ]
    if proj.get("oudjat_supreme"):
        osup = proj["oudjat_supreme"]
        lignes.append(f"  👑 OUDJAT SUPRÊME DU PROJET : '{osup['nom']}' dans {osup['fichier']}")
        lignes.append(f"     (Complexité: {osup['complexite']}, φ-ratio local: {osup.get('phi_ratio', 1.0):.2f})")
        lignes.append("")

    lignes.append("  TABLEAU COMPARATIF DES MODULES :")
    lignes.append("  " + "-" * 66)
    lignes.append(f"  {'Fichier':<35} | {'Radiance':<8} | {'Antifragilité':<12} | {'Lilith Rel':<10}")
    lignes.append("  " + "-" * 66)
    for f in proj["fichiers"]:
        import os
        nom_court = os.path.basename(f["fichier"])
        lignes.append(
            f"  {nom_court:<35} | {f['radiance']:>6.1f}   | {f.get('antifragilite', 0.0):>6.3f}       | {f.get('lilith_rel_variance', 0.0):>6.3f}"
        )
    lignes.append("  " + "-" * 66)
    return "\n".join(lignes)


__version__ = VERSION
__author__ = AUTEUR
__all__ = [
    "auditer",
    "auditer_projet",
    "diagnostic_bayesien",
    "editer",
    "carte_projet",
    "carte_console",
    "chemins_croyants",
    "chemins_console",
    "MoteurInferenceBayesienne",
    "DiagnosticBayesien",
    "rapport_console",
    "rapport_markdown",
    "rapport_json",
    "rapport_sarif",
    "rapport_matrice_console",
    "evaluer_formule",
    "evaluer_gate",
    "PHI",
    "TAXE_SUTURE",
    "ETA_GOLDEN",
    "AnalyseurPhi",
    "CalculateurRadiance",
    "GenerateurRapport",
    "obtenir_analyseur",
    "langage_pour_extension",
    "est_fichier_supporte",
    "EXTENSIONS_SUPPORTEES",
]
