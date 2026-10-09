"""impact — analyse d'impact intelligente pour code Python.

Répond à : "si je change cette fonction, qu'est-ce qui casse ?"

Modules :
- graphe : GrapheDependances — parser AST → graphe orienté pondéré
- propagation : impact_avant / dependances_arriere (BFS déterministe)
- risque : score_risque (0-100), ordre_modification_sure
- visu : generer_html — visualisation SVG autonome
"""

from .graphe import GrapheDependances
from .propagation import impact_avant, dependances_arriere
from .risque import score_risque, ordre_modification_sure
from .visu import generer_html

__all__ = [
    "GrapheDependances",
    "impact_avant",
    "dependances_arriere",
    "score_risque",
    "ordre_modification_sure",
    "generer_html",
]
