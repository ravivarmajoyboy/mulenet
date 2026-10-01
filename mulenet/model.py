"""Fast-path scorer (gradient boosting) + decision policy that fuses model score and ledger tier."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from .features import FEATURES, GNN_FEATURES

ALLOW, STEP_UP, HOLD = 0, 1, 2
NAMES = {ALLOW: "ALLOW", STEP_UP: "STEP_UP", HOLD: "HOLD"}


class MuleModel:
    def __init__(self, seed: int = 0, use_graph: bool = False):
        self.clf = HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.08, max_leaf_nodes=31,
            l2_regularization=1.0, random_state=seed,
        )
        self.use_graph = use_graph
        self.cols = FEATURES + GNN_FEATURES if use_graph else FEATURES
        self.hold_thr, self.step_thr = 0.5, 0.2

    def fit(self, df: pd.DataFrame) -> "MuleModel":
        y = df["label"].to_numpy()
        self.clf.fit(df[self.cols], y, sample_weight=np.where(y == 1, 5.0, 1.0))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return self.clf.predict_proba(df[self.cols])[:, 1]

    def calibrate(self, df: pd.DataFrame, hold_fpr: float = 0.001, step_fpr: float = 0.005) -> "MuleModel":
        """Pick thresholds from benign traffic so operating points are set by false-positive
        budget, not by an arbitrary probability cut."""
        s = self.score(df[df["label"] == 0])
        self.hold_thr = float(np.quantile(s, 1 - hold_fpr))
        self.step_thr = min(float(np.quantile(s, 1 - step_fpr)), self.hold_thr)
        return self

    def decide(self, df: pd.DataFrame, use_ledger: bool = True) -> np.ndarray:
        p = self.score(df)
        dec = np.where(p >= self.hold_thr, HOLD, np.where(p >= self.step_thr, STEP_UP, ALLOW))
        if use_ledger:
            acct = np.maximum(df["b_led"].to_numpy(), df["a_led"].to_numpy())  # 2 => corroborated
            dev = np.minimum(np.maximum(df["b_dev_led"].to_numpy(), df["a_dev_led"].to_numpy()), 1)
            dec = np.maximum(dec, np.maximum(acct, dev))  # devices are shared by families: soft only
        return dec


def metrics(df: pd.DataFrame, dec: np.ndarray) -> dict:
    pos = df["label"].to_numpy() == 1
    hop0 = pos & (df["hop"].to_numpy() == 0)  # victim -> first mule: blocking these prevents loss
    flagged, held = dec >= STEP_UP, dec >= HOLD
    amt = df["amount_raw"].to_numpy()
    return {
        "victim_recall": float(flagged[hop0].mean()) if hop0.any() else float("nan"),
        "victim_value_recall": float(amt[hop0 & flagged].sum() / amt[hop0].sum()) if hop0.any() else float("nan"),
        "flow_recall": float(flagged[pos].mean()),
        "fpr": float(flagged[~pos].mean()),
        "hold_fpr": float(held[~pos].mean()),
        "n_pos": int(pos.sum()),
        "n": int(len(df)),
    }


def explain(f: dict, code: int, p: float) -> list[str]:
    """Rule-derived reason codes for analysts (not SHAP; SHAP is a sprint-4 item)."""
    r = []
    if f["b_led"] == 2 or f["a_led"] == 2:
        r.append("account corroborated by >=2 banks on shared ledger")
    elif f["b_led"] == 1 or f["a_led"] == 1:
        r.append("account flagged by 1 bank on shared ledger")
    if max(f["b_dev_led"], f["a_dev_led"]) >= 1:
        r.append("device flagged on shared ledger")
    if f["b_age"] < 30:
        r.append(f"beneficiary account is new ({f['b_age']:.0f}d)")
    if f["b_in_uniq_1h"] >= 5:
        r.append(f"fan-in: {f['b_in_uniq_1h']} unique senders in 1h")
    if f["b_fwd_24h"] > 0.6:
        r.append("beneficiary forwards most inflow within 24h (pass-through)")
    if f["a_fwd_now"] > 0.6 and f["a_since_in"] < 6 * 3600:
        r.append("remitter is forwarding recently received funds")
    if max(f["a_dev_accts"], f["b_dev_accts"]) >= 3:
        r.append("device shared across many accounts")
    if max(f.get("a_gnn", 0), f.get("b_gnn", 0)) > 0.05 and max(f["b_led"], f["a_led"]) == 0:
        r.append("connected via recent fund flow to a separately flagged account (graph signal)")
    if not r and code != ALLOW:
        r.append(f"model risk score {p:.2f} above threshold")
    return r
