"""phi_scipy.csr — CSR BINAIRE ciblé graphes de dépendances PHIAST.

Périmètre : matrices d'adjacence binaires (pas de `data` flottant), très creuses,
accès ligne par ligne (BFS, degrés, propagation). stdlib + numpy uniquement.

Ce que scipy fait mieux (documenté, pas réimplémenté) : factorisation LU creuse
(SuperLU), solve creux, valeurs propres (ARPACK). Ce module ne les touche pas.
"""
import numpy as np

__all__ = ["BinaryCSR"]


class BinaryCSR:
    """Graphe orienté en CSR binaire : (indptr, indices), int32/int64.

    indptr : (n+1,) — indptr[v]..indptr[v+1] = plage des successeurs de v
    indices : (nnz,) — cibles, triées par source (pas forcément triées en cible)
    edge_src : (nnz,) — source de chaque arête (construit paresseusement)
    """

    __slots__ = ("indptr", "indices", "n", "nnz", "_edge_src")

    def __init__(self, indptr, indices, n):
        indptr = np.asarray(indptr)
        indices = np.asarray(indices)
        assert indptr.shape == (n + 1,), (indptr.shape, n)
        assert indptr[0] == 0 and indptr[-1] == len(indices)
        assert np.all(np.diff(indptr) >= 0)
        self.indptr = indptr
        self.indices = indices
        self.n = int(n)
        self.nnz = int(len(indices))
        self._edge_src = None

    # ------------------------------------------------------------------
    @classmethod
    def from_edges(cls, src, tgt, n, dtype=np.int64):
        """Construit le CSR binaire dédupliqué depuis des listes d'arêtes.

        Déduplication par clé int64 (src*n + tgt) + np.unique : triée par
        (src, tgt), donc déjà groupée par source — pas de tri secondaire.
        """
        src = np.asarray(src, dtype=dtype).ravel()
        tgt = np.asarray(tgt, dtype=dtype).ravel()
        assert src.shape == tgt.shape
        assert len(src) == 0 or (src.min() >= 0 and tgt.min() >= 0)
        assert len(src) == 0 or (src.max() < n and tgt.max() < n)
        if len(src) == 0:
            return cls(np.zeros(n + 1, dtype=dtype), np.zeros(0, dtype=dtype), n)
        key = src * np.int64(n) + tgt
        ukey = np.unique(key)          # trié -> groupé par src
        src_u = (ukey // n).astype(dtype)
        tgt_u = (ukey % n).astype(dtype)
        indptr = np.zeros(n + 1, dtype=dtype)
        np.cumsum(np.bincount(src_u, minlength=n), out=indptr[1:])
        return cls(indptr, tgt_u, n)

    # ------------------------------------------------------------------
    @property
    def edge_src(self):
        """Source de chaque arête (une fois, paresseux)."""
        if self._edge_src is None:
            self._edge_src = np.repeat(
                np.arange(self.n, dtype=self.indptr.dtype),
                np.diff(self.indptr))
        return self._edge_src

    @property
    def density(self):
        return self.nnz / (self.n * self.n)

    def successors(self, v):
        return self.indices[self.indptr[v]:self.indptr[v + 1]]

    # ------------------------------------------------------------------
    def degrees_out(self):
        """Degrés sortants : O(n), vectorisé."""
        return np.diff(self.indptr)

    def degrees_in(self):
        """Degrés entrants : O(nnz), un bincount."""
        return np.bincount(self.indices, minlength=self.n)

    # ------------------------------------------------------------------
    def transpose(self):
        """Graphe transposé (équivalent CSC) : tri stable par cible."""
        order = np.argsort(self.indices, kind="stable")
        indptr_t = np.zeros(self.n + 1, dtype=self.indptr.dtype)
        np.cumsum(np.bincount(self.indices, minlength=self.n), out=indptr_t[1:])
        return BinaryCSR(indptr_t, self.edge_src[order], self.n)

    # ------------------------------------------------------------------
    def bfs_layers(self, sources):
        """Distances (en couches) depuis un ensemble de sources — BFS multi-source.

        Vectorisé par couches : chaque couche coûte O(n + nnz) en C
        (indexation booléenne), aucune boucle Python sur les arêtes.
        -> dist : (n,) int64, -1 = inatteignable.
        """
        sources = np.asarray(sources, dtype=np.int64).ravel()
        dist = np.full(self.n, -1, dtype=np.int64)
        if sources.size == 0:
            return dist
        dist[sources] = 0
        frontier = np.unique(sources)
        edge_src = self.edge_src
        indices = self.indices
        d = 0
        in_frontier = np.zeros(self.n, dtype=bool)
        while frontier.size:
            d += 1
            in_frontier[:] = False
            in_frontier[frontier] = True
            nbrs = indices[in_frontier[edge_src]]
            if nbrs.size == 0:
                break
            new = nbrs[dist[nbrs] == -1]
            if new.size == 0:
                break
            new = np.unique(new)
            dist[new] = d
            frontier = new
        return dist

    # ------------------------------------------------------------------
    def matvec(self, x):
        """y = A @ x (flottant) : un bincount pondéré, O(nnz) en C."""
        x = np.asarray(x, dtype=np.float64)
        assert x.shape == (self.n,)
        return np.bincount(self.edge_src, weights=x[self.indices],
                           minlength=self.n)

    def bool_matvec(self, x):
        """y = (A @ x) > 0 (booléen) : propagation d'un front sur le graphe."""
        x = np.asarray(x, dtype=bool)
        assert x.shape == (self.n,)
        hit = np.bincount(self.edge_src, weights=x[self.indices].astype(np.int64),
                          minlength=self.n)
        return hit > 0

    # ------------------------------------------------------------------
    def to_scipy(self):
        """Construit l'équivalent scipy.sparse.csr_matrix (comparaisons)."""
        import scipy.sparse
        return scipy.sparse.csr_matrix(
            (np.ones(self.nnz, dtype=np.float64), self.indices, self.indptr),
            shape=(self.n, self.n))
