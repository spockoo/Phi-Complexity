"""lilith — instruments de mesure Lilith pour phi-complexity (local uniquement).

Intégré depuis Metaprogramme-lean (RUCHE-LILITH-INSTRUMENTS), jamais poussé
sur le dépôt public sans ordre explicite de Tomy.

Modules :
- distributionnel : les 6 métriques (var_relative, kl_divergence, d_infini,
  hellinger, variation_totale, shannon_norm) + toutes_metriques()
- alpha : curseur α — divergence de Rényi D_α à α variable
- curseur : exploration du curseur de sensibilité
- f1 : detecter_f1 — détection F1 (violation de décomposition), seuil 2,95
- batterie : batterie tri-domaine (distributionnel + graphe + dynamique)
- graphe_domaine / dynamique : les deux autres domaines de la batterie
- perf : audit de performance (corrélation concentration/temps)
- phi_lilith : adaptateur CLI (contrat PHI_CONTRAT.md)
"""

from .distributionnel import (
    var_relative,
    kl_divergence,
    d_infini,
    hellinger,
    variation_totale,
    shannon_norm,
    toutes_metriques,
    kappas_depuis_fichier,
    kappas_depuis_source,
)
from .alpha import d_alpha
from .f1 import detecter_f1, SEUIL_DEFAUT
from .batterie import lancer as batterie_lancer

__all__ = [
    "var_relative",
    "kl_divergence",
    "d_infini",
    "hellinger",
    "variation_totale",
    "shannon_norm",
    "toutes_metriques",
    "kappas_depuis_fichier",
    "kappas_depuis_source",
    "d_alpha",
    "detecter_f1",
    "SEUIL_DEFAUT",
    "batterie_lancer",
]
