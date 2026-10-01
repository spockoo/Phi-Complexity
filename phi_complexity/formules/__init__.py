"""
phi_complexity/formules/__init__.py — Langage de formules de phi-complexity.

API publique :
    construire_env(metriques)  -> dict   # métriques d'audit -> environnement numérique
    evaluer_formule(expr, env) -> float   # formule arithmétique personnalisée
    evaluer_gate(expr, env)    -> bool    # porte logique de qualité (CI)

Le parseur est dérivé du parseur d'expressions de la calculatrice GNS-754
(Tomy Verreault, 2026), étendu aux identifiants de métriques, aux comparaisons
et à la logique booléenne. Zéro dépendance hors stdlib.
"""
from .parser import ParseurFormules

__all__ = [
    "ParseurFormules",
    "NOMS_METRIQUES",
    "construire_env",
    "evaluer_formule",
    "evaluer_gate",
]

# Métriques numériques exposées comme identifiants dans le langage.
# (noms exacts du dictionnaire retourné par auditer())
NOMS_METRIQUES = [
    "radiance",
    "radiance_adiabatique",
    "radiance_classique",
    "antifragilite",
    "lilith_variance",
    "lilith_rel_variance",
    "lilith_skewness",
    "lilith_kurtosis",
    "shannon_entropy",
    "shannon_entropy_norm",
    "phi_ratio",
    "phi_ratio_delta",
    "fibonacci_distance",
    "fibonacci_distance_moyenne",
    "zeta_score",
    "nb_fonctions",
    "nb_classes",
    "nb_imports",
    "nb_lignes_total",
    "ratio_commentaires",
    "nb_anomalies",
]

_parseur = ParseurFormules()


def construire_env(metriques: dict) -> dict:
    """
    Construit l'environnement d'évaluation depuis le dictionnaire de métriques
    retourné par auditer(). Les constantes phi, pi, e sont ajoutées.
    """
    env = {}
    for nom in NOMS_METRIQUES:
        if nom == "nb_anomalies":
            env[nom] = float(sum(
                1 for a in metriques.get("annotations", [])
                if a.get("niveau") in ("WARNING", "CRITICAL")
            ))
        elif nom in metriques and isinstance(metriques[nom], (int, float)):
            env[nom] = float(metriques[nom])
    # Constantes mathématiques (résolues aussi par le parseur lui-même)
    import math
    env.setdefault("phi", (1 + math.sqrt(5)) / 2)
    env.setdefault("pi", math.pi)
    env.setdefault("e", math.e)
    return env


def evaluer_formule(expression: str, env: dict) -> float:
    """
    Évalue une formule arithmétique sur les métriques.
    Ex : evaluer_formule("100 - lilith_variance/phi - shannon_entropy^2", env)
    """
    return _parseur.parse(expression).evaluer(env)


def evaluer_gate(expression: str, env: dict) -> bool:
    """
    Évalue une porte logique de qualité. Rend True si la porte est ouverte.
    Ex : evaluer_gate("radiance >= 75 and lilith_variance < 1200", env)
    """
    return bool(_parseur.parse(expression).evaluer(env))
