"""Causal graph-propagation signal (the "GNN upgrade").

Method: Personalized PageRank propagation of ledger-flagged risk through the recent
payment graph - the same decoupled propagate-then-predict idea as APPNP (Klicpera,
Bojchevski & Gunnemann, 2019), simplified to an unrolled fixed-point diffusion instead
of a trained network. This directly targets what the GBDT's local, per-account features
miss: a mule 2-3 hops downstream of a flagged account has *no* suspicious features of
its own (it's freshly created, low fan-in) but is one payment away from money that is
already known-bad. Guilt-by-association through the live fund-flow graph catches that.

Causality: the graph at time t is built only from edges already seen (a short rolling
window, since ring activity concentrates within hours) and seeds are the ledger tiers
already known at t. No future transaction or future signal is used - same no-lookahead
contract as FeatureStore, and covered by the same style of test.
"""
from __future__ import annotations

from collections import defaultdict, deque

GRAPH_WINDOW_S = 4 * 3600.0  # ring activity is fast; a short window keeps subgraphs small
ALPHA = 0.15  # PPR restart probability (standard default)
ITERS = 6


class GraphSignal:
    def __init__(self, ledger, alpha: float = ALPHA, iters: int = ITERS, window_s: float = GRAPH_WINDOW_S):
        self.ledger, self.alpha, self.iters, self.window_s = ledger, alpha, iters, window_s
        self.adj: dict[str, dict[str, float]] = defaultdict(dict)  # undirected, weight = #payments
        self.edges: deque = deque()  # (ts, u, v) for trimming

    def update(self, t: dict) -> None:
        u, v, now = t["src"], t["dst"], t["ts"]
        self.adj[u][v] = self.adj[u].get(v, 0.0) + 1.0
        self.adj[v][u] = self.adj[v].get(u, 0.0) + 1.0
        self.edges.append((now, u, v))
        cutoff = now - self.window_s
        while self.edges and self.edges[0][0] < cutoff:
            _, ou, ov = self.edges.popleft()
            for a, b in ((ou, ov), (ov, ou)):
                if self.adj[a].get(b, 0.0) > 1.0:
                    self.adj[a][b] -= 1.0
                else:
                    self.adj[a].pop(b, None)
                    if not self.adj[a]:
                        del self.adj[a]

    def _seed(self, node: str, now: float) -> float:
        tier = self.ledger.tier(node, "acct", now) if self.ledger else 0
        return {0: 0.0, 1: 0.5, 2: 1.0}[tier]

    def score(self, a: str, b: str, now: float) -> tuple[float, float]:
        """Personalized-PageRank risk for a and b over the current local subgraph."""
        nodes = set()
        for n in (a, b):
            if n in self.adj:
                nodes.add(n)
                nodes.update(self.adj[n])
        if not nodes:
            return self._seed(a, now), self._seed(b, now)

        seed = {n: self._seed(n, now) for n in nodes}
        if not any(seed.values()):
            return 0.0, 0.0  # nothing flagged nearby: skip the diffusion, it's a no-op

        r = dict(seed)
        for _ in range(self.iters):
            nxt = {}
            for n in nodes:
                nbrs = self.adj.get(n, {})
                deg = sum(nbrs.values()) or 1.0
                inflow = sum(r[m] * w / (sum(self.adj[m].values()) or 1.0)
                             for m, w in nbrs.items() if m in r)
                nxt[n] = self.alpha * seed[n] + (1 - self.alpha) * inflow
            r = nxt
        return r.get(a, 0.0), r.get(b, 0.0)
