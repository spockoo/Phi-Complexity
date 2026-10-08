"""scipy_mini — bibliothèque scientifique maison (stdlib + numpy, zéro autre dépendance).

Intégrée depuis Metaprogramme-lean (RUCHE-SCIPY-MAISON), local uniquement.
Ne réinvente que ce qui BAT scipy sur nos cas réels (mesuré) ; délègue le
reste à scipy.

Modules :
- csr : BinaryCSR — graphe creux (BFS ×3,35 vs scipy sur graphe PHIAST)
- kernels : row_weights, diffuse, density_map, pagerank
- auto : auto_optimize (décorateur dense/sparse), estimate_density
- bridges : welch_ttest (×12,3 vs scipy, résultats identiques à 1e-9)
"""

from .csr import BinaryCSR
from . import kernels
from .auto import auto_optimize, estimate_density
from . import bridges

__version__ = "0.1.0"
__all__ = ["BinaryCSR", "kernels", "auto_optimize", "estimate_density", "bridges"]
