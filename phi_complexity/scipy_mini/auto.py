"""phi_scipy.auto — `@auto_optimize` : décorateur auto-optimisant.

(a) profile la densité d'entrée (seuil ~10 %, échantillonnée si grande),
(b) choisit dense (numpy) / sparse (notre CSR ou scipy),
(c) met en cache la décision par (fonction, type, forme) : jamais de
    re-profilage à chaque appel.

Transparent pour l'appelant : un seul appelable, mêmes arguments.
stdlib + numpy uniquement.
"""
import functools
import numpy as np

__all__ = ["auto_optimize", "estimate_density"]

_DEFAULT_THRESHOLD = 0.10
_SAMPLE_MAX = 10_000


def estimate_density(A, sample_max=_SAMPLE_MAX):
    """Densité estimée : exacte si petite, échantillonnée sinon."""
    if isinstance(A, np.ndarray):
        if A.size <= sample_max:
            return float(np.count_nonzero(A)) / A.size
        rng = np.random.default_rng(20261007)
        idx = rng.choice(A.size, size=sample_max, replace=False)
        return float(np.count_nonzero(A.ravel()[idx])) / sample_max
    if isinstance(A, tuple) and len(A) == 2:  # notre CSR (indptr, indices)
        indptr, indices = A
        n = len(indptr) - 1
        return len(indices) / (n * n) if n else 0.0
    if hasattr(A, "nnz") and hasattr(A, "shape"):  # scipy sparse
        n = int(np.prod(A.shape))
        return A.nnz / n if n else 0.0
    raise TypeError("type non supporté : %r" % type(A))


def _as_dense(A):
    if isinstance(A, np.ndarray):
        return A
    if isinstance(A, tuple) and len(A) == 2:
        from .csr import BinaryCSR
        indptr, indices = A
        n = len(indptr) - 1
        D = np.zeros((n, n), dtype=np.float64)
        src = np.repeat(np.arange(n), np.diff(np.asarray(indptr)))
        D[src, np.asarray(indices)] = 1.0
        return D
    if hasattr(A, "toarray"):
        return np.asarray(A.toarray(), dtype=np.float64)
    raise TypeError("conversion dense impossible : %r" % type(A))


def _as_phi_csr(A):
    from .csr import BinaryCSR
    if isinstance(A, BinaryCSR):
        return A
    if isinstance(A, tuple) and len(A) == 2:
        indptr, indices = A
        return BinaryCSR(np.asarray(indptr), np.asarray(indices),
                         len(indptr) - 1)
    if isinstance(A, np.ndarray):
        u, v = np.nonzero(A)
        return BinaryCSR.from_edges(u, v, A.shape[0])
    if hasattr(A, "tocoo"):  # scipy sparse
        c = A.tocoo()
        return BinaryCSR.from_edges(c.row, c.col, A.shape[0])
    raise TypeError("conversion CSR impossible : %r" % type(A))


def auto_optimize(dense_impl, sparse_impl, threshold=_DEFAULT_THRESHOLD,
                  sparse_backend="phi"):
    """Construit un appelable qui route dense/sparse avec décision cachée.

    dense_impl(A_dense, *a, **k), sparse_impl(A_csr, *a, **k) où A_csr est
    un BinaryCSR. sparse_backend : "phi" (défaut) ou "scipy".
    """
    if sparse_backend not in ("phi", "scipy"):
        raise ValueError("sparse_backend : 'phi' ou 'scipy'")
    decisions = {}

    @functools.wraps(dense_impl)
    def dispatcher(A, *args, **kwargs):
        if isinstance(A, np.ndarray):
            key = ("ndarray", A.shape, A.dtype.str)
        elif isinstance(A, tuple):
            key = ("csr-tuple", (len(A[0]) - 1,))
        elif hasattr(A, "nnz"):
            key = ("scipy", tuple(A.shape))
        else:
            from .csr import BinaryCSR
            if isinstance(A, BinaryCSR):
                key = ("phi-csr", (A.n,))
            else:
                raise TypeError("type non supporté : %r" % type(A))
        hit = decisions.get(key)
        if hit is None:
            d = estimate_density(A)
            hit = ("dense" if d >= threshold else "sparse", d)
            decisions[key] = hit
        route, _d = hit
        if route == "dense":
            return dense_impl(_as_dense(A), *args, **kwargs)
        if sparse_backend == "phi":
            return sparse_impl(_as_phi_csr(A), *args, **kwargs)
        # backend scipy : convertit en csr_matrix et appelle sparse_impl
        import scipy.sparse
        M = A if hasattr(A, "tocsr") else scipy.sparse.csr_matrix(_as_dense(A))
        return sparse_impl(M, *args, **kwargs)

    dispatcher.decisions = decisions
    dispatcher.threshold = threshold
    return dispatcher
