import pandas as pd

from mulenet.features import FEATURES, FeatureStore, build
from mulenet.sim import PROFILES, generate


def test_features_have_no_lookahead():
    """Features for txn i must be identical whether or not later txns exist."""
    sim = generate(7, n_accounts=150, n_rings=3, days=2)
    full = build(sim)
    cut = 800
    sim.txns = sim.txns.iloc[:cut].copy()
    part = build(sim)
    pd.testing.assert_frame_equal(full.iloc[:cut][FEATURES], part[FEATURES])


def test_sim_is_deterministic_and_has_all_profiles():
    a = generate(3, n_accounts=100, n_rings=2, days=2)
    b = generate(3, n_accounts=100, n_rings=2, days=2)
    pd.testing.assert_frame_equal(a.txns, b.txns)
    for p in PROFILES.values():
        s = generate(1, n_accounts=100, n_rings=2, days=2, evasion=p)
        assert s.txns.label.sum() > 0


def test_online_store_matches_batch():
    sim = generate(5, n_accounts=120, n_rings=2, days=2)
    df = build(sim)
    fs = FeatureStore(None)  # ledger features are 0 without a ledger; compare the rest
    rows = []
    for t in sim.txns.to_dict("records"):
        rows.append(fs.features(t))
        fs.update(t)
    online = pd.DataFrame(rows)
    cols = [c for c in FEATURES if not c.endswith("_led")]
    pd.testing.assert_frame_equal(df[cols], online[cols])
