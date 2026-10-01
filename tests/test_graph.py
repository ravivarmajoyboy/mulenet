from mulenet.features import FeatureStore
from mulenet.graph import GraphSignal
from mulenet.ledger import Ledger


def test_guilt_by_association_propagates_through_unflagged_hop():
    led = Ledger(b"k")
    bank = led.enroll("A")
    bank.publish("mule1", now=0)  # single-bank -> SOFT tier

    g = GraphSignal(led, alpha=0.15, iters=6)
    for i, (u, v, ts) in enumerate([("victim", "mule1", 1), ("mule1", "layer2", 2),
                                     ("layer2", "sink", 3)]):
        g.update({"src": u, "dst": v, "ts": ts})

    s_layer2, s_sink = g.score("layer2", "sink", 4)
    assert s_layer2 > 0, "layer2 is one hop from a flagged account and should inherit risk"
    assert s_layer2 > s_sink, "risk should decay with graph distance from the flagged account"


def test_no_signal_nearby_gives_zero():
    g = GraphSignal(None)
    g.update({"src": "a", "dst": "b", "ts": 1})
    assert g.score("a", "b", 2) == (0.0, 0.0)


def test_edges_outside_window_are_forgotten():
    g = GraphSignal(None, window_s=100)
    g.update({"src": "a", "dst": "b", "ts": 0})
    assert "b" in g.adj["a"]
    g.update({"src": "c", "dst": "d", "ts": 500})  # triggers trim of the old edge
    assert "b" not in g.adj.get("a", {})


def test_graph_feature_has_no_lookahead():
    """Same no-lookahead contract as the rest of the feature store: truncating the future
    must not change graph features computed for earlier transactions."""
    import pandas as pd
    from mulenet.sim import generate

    sim = generate(11, n_accounts=120, n_rings=3, days=2)
    cut = 600

    def run(records):
        fs, out = FeatureStore(None), []
        for t in records:
            out.append(fs.features(t))
            fs.update(t)
        return pd.DataFrame(out)[["a_gnn", "b_gnn"]]

    full = run(sim.txns.to_dict("records"))
    part = run(sim.txns.iloc[:cut].to_dict("records"))
    pd.testing.assert_frame_equal(full.iloc[:cut].reset_index(drop=True), part)
