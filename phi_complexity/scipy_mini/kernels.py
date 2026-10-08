"""phi_scipy.kernels — kernels vectorisés numpy pour nos workloads réels.

- diffusion sur graphe (analogue discret de la convolution par noyau du labo
  quasicristal : densite_electronique.py convolue un champ par un noyau via FFT ;
  ici le "champ" vit sur les nœuds du graphe de dépendances et diffuse le long
  des arêtes) ;
- PageRank sur CSR binaire (centralité — famille des métriques phi-complexity) ;
- normalisations.

Comparateur honnête : le code pur-Python existant du codebase (scipy n'offre
pas ces kernels). stdlib + numpy uniquement.
"""
import numpy as np

__all__ = ["row_weights", "diffuse", "density_map", "pagerank"]


def row_weights(csr):
    """Poids 1/degré_sortant par nœud source (0 si puits). -> (n,) float64."""
    deg = csr.degrees_out().astype(np.float64)
    w = np.zeros(csr.n, dtype=np.float64)
    nz = deg > 0
    w[nz] = 1.0 / deg[nz]
    return w


def diffuse(csr, field, steps, alpha=0.5):
    """Diffuse un champ scalaire le long des arêtes (flot de chaleur discret).

    field_{t+1}[v] = (1-alpha)*field_t[v] + alpha * moyenne(field_t[successeurs(v)])
    Puits (sans successeur) : inchangés. Tout en numpy, O(steps*nnz).
    """
    f = np.asarray(field, dtype=np.float64).ravel()
    assert f.shape == (csr.n,)
    deg = csr.degrees_out().astype(np.float64)
    has_succ = deg > 0
    edge_src = csr.edge_src
    indices = csr.indices
    for _ in range(steps):
        agg = np.bincount(edge_src, weights=f[indices], minlength=csr.n)
        mean_succ = np.zeros(csr.n)
        mean_succ[has_succ] = agg[has_succ] / deg[has_succ]
        f = np.where(has_succ, (1.0 - alpha) * f + alpha * mean_succ, f)
    return f


def density_map(csr, steps=8, alpha=0.5):
    """Carte de densité locale : diffuse le champ des degrés entrants.

    Analogue graphe de la densité électronique du labo quasicristal :
    les "pics de Bragg" deviennent ici les zones de forte concentration
    de dépendances. -> (n,) float64.
    """
    return diffuse(csr, csr.degrees_in().astype(np.float64), steps, alpha)


def pagerank(csr, alpha=0.85, tol=1e-8, max_iter=200):
    """PageRank sur CSR binaire, itération vectorisée.

    r_{t+1}[v] = (1-alpha)/n + alpha*(sum_{u->v} r_t[u]/outdeg(u) + dangling/n)
    -> (rangs (n,), n_iter). Les puits redistribuent uniformément.
    """
    n = csr.n
    w = row_weights(csr)
    edge_src = csr.edge_src
    indices = csr.indices
    dangling = w == 0.0
    r = np.full(n, 1.0 / n, dtype=np.float64)
    base = (1.0 - alpha) / n
    for it in range(1, max_iter + 1):
        contrib = r * w
        acc = np.bincount(edge_src, weights=contrib[edge_src], minlength=n)
        dang = r[dangling].sum()
        r_new = base + alpha * (acc + dang / n)
        if np.abs(r_new - r).sum() < tol:
            return r_new, it
        r = r_new
    return r, max_iter
