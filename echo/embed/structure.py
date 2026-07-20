"""Structural comparison of the two event networks (RQ1).

Combines interpretable graph-level descriptors (modularity, core-periphery
structure, degree assortativity, bridge prevalence) with a compact spectral /
graph-convolutional embedding so that "how differently are these two networks
organised?" can be answered both descriptively and with a single distance plus a
permutation test.

The embedding uses an *untrained* graph-convolutional encoder: node structural
features are propagated through the symmetric-normalised adjacency (the GCN
aggregation operator) for ``k`` hops, then pooled. Untrained message passing is a
well-established, reproducible structural feature extractor that needs no
labels — appropriate here because the comparison is unsupervised. Spectral
descriptors (Laplacian spectrum) provide a complementary, permutation-free graph
distance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import networkx as nx
from scipy import sparse
from scipy.sparse.linalg import eigsh


# ---------------------------------------------------------------------------
# Interpretable descriptors
# ---------------------------------------------------------------------------
def core_periphery_coefficient(G: nx.Graph) -> float:
    """Correlation between node degree and a Borgatti-Everett core score.

    Approximated by the rank correlation of degree with k-core number: strongly
    core-peripheral networks show a steep, monotone degree/core gradient.
    """
    if G.number_of_nodes() < 4:
        return 0.0
    core = nx.core_number(G)
    deg = dict(G.degree())
    nodes = list(G.nodes())
    c = np.array([core[n] for n in nodes], float)
    d = np.array([deg[n] for n in nodes], float)
    if c.std() == 0 or d.std() == 0:
        return 0.0
    return float(np.corrcoef(c, d)[0, 1])


def bridge_prevalence(G: nx.Graph, sample: int = 2000, seed: int = 0) -> float:
    """Fraction of nodes with high betweenness (structural bridges).

    Betweenness is approximated on a node sample for scalability; a node counts
    as a bridge when its betweenness exceeds the 90th percentile.
    """
    if G.number_of_nodes() < 4:
        return 0.0
    k = min(sample, G.number_of_nodes())
    bc = nx.betweenness_centrality(G, k=k, seed=seed)
    vals = np.array(list(bc.values()))
    thr = np.percentile(vals, 90)
    return float(np.mean(vals > thr))


@dataclass
class StructuralProfile:
    n_nodes: int
    n_edges: int
    density: float
    modularity: float
    n_communities: int
    assortativity: float
    avg_clustering: float
    core_periphery: float
    bridge_prevalence: float
    largest_cc_frac: float
    spectrum: np.ndarray = field(default_factory=lambda: np.array([]))

    def as_row(self) -> dict:
        d = self.__dict__.copy()
        d.pop("spectrum")
        return d


def profile_graph(G: nx.Graph, n_spectrum: int = 20, seed: int = 0) -> StructuralProfile:
    n, m = G.number_of_nodes(), G.number_of_edges()
    if n == 0:
        return StructuralProfile(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    density = nx.density(G)
    try:
        comms = nx.community.greedy_modularity_communities(G, weight="weight")
        modularity = nx.community.modularity(G, comms, weight="weight")
        n_comm = len(comms)
    except Exception:
        modularity, n_comm = 0.0, 0
    try:
        assort = nx.degree_assortativity_coefficient(G)
    except Exception:
        assort = 0.0
    clustering = nx.average_clustering(G)
    ccs = sorted(nx.connected_components(G), key=len, reverse=True)
    largest = len(ccs[0]) / n if ccs else 0.0
    return StructuralProfile(
        n, m, density, float(modularity), n_comm,
        float(assort) if np.isfinite(assort) else 0.0,
        float(clustering), core_periphery_coefficient(G),
        bridge_prevalence(G, seed=seed), largest,
        laplacian_spectrum(G, n_spectrum),
    )


# ---------------------------------------------------------------------------
# Spectral graph distance
# ---------------------------------------------------------------------------
def laplacian_spectrum(G: nx.Graph, k: int = 20) -> np.ndarray:
    """Smallest ``k`` normalised-Laplacian eigenvalues of the largest component."""
    if G.number_of_nodes() < 3:
        return np.zeros(k)
    giant = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    n = giant.number_of_nodes()
    L = nx.normalized_laplacian_matrix(giant).astype(float)
    kk = min(k, n - 2)
    try:
        vals = eigsh(L, k=kk, which="SM", return_eigenvalues=True)[0]
    except Exception:
        vals = np.sort(np.linalg.eigvalsh(L.toarray()))[:kk]
    vals = np.sort(np.real(vals))
    out = np.zeros(k)
    out[:len(vals)] = vals[:k]
    return out


def spectral_distance(p: StructuralProfile, q: StructuralProfile) -> float:
    """L2 distance between (padded) Laplacian spectra."""
    a, b = p.spectrum, q.spectrum
    n = max(len(a), len(b))
    a = np.pad(a, (0, n - len(a))); b = np.pad(b, (0, n - len(b)))
    return float(np.linalg.norm(a - b))


# ---------------------------------------------------------------------------
# Untrained graph-convolutional embedding
# ---------------------------------------------------------------------------
def _node_features(G: nx.Graph, nodes: list) -> np.ndarray:
    deg = dict(G.degree())
    clus = nx.clustering(G)
    core = nx.core_number(G)
    feats = np.array([[deg[n], clus[n], core[n]] for n in nodes], float)
    # standardise columns
    mu, sd = feats.mean(0), feats.std(0) + 1e-9
    return (feats - mu) / sd


def gcn_embedding(G: nx.Graph, hops: int = 2, dim: int = 16,
                  seed: int = 0) -> np.ndarray:
    """Graph-level embedding via untrained GCN message passing + pooling.

    X' = (D^-1/2 (A+I) D^-1/2)^hops X W, then mean/std pooled over nodes.
    W is a fixed random projection (reproducible via ``seed``).
    """
    if G.number_of_nodes() < 3:
        return np.zeros(2 * dim)
    nodes = list(G.nodes())
    idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    A = nx.to_scipy_sparse_array(G, nodelist=nodes, weight="weight", format="csr")
    A = A + sparse.identity(n, format="csr")
    d = np.asarray(A.sum(1)).ravel()
    dinv = sparse.diags(1.0 / np.sqrt(np.maximum(d, 1e-9)))
    A_hat = dinv @ A @ dinv
    X = _node_features(G, nodes)
    rng = np.random.default_rng(seed)
    W = rng.normal(size=(X.shape[1], dim)) / np.sqrt(X.shape[1])
    H = X @ W
    for _ in range(hops):
        H = A_hat @ H
        H = np.tanh(H)
    # graph-level readout: concat(mean, std)
    return np.concatenate([H.mean(0), H.std(0)])
