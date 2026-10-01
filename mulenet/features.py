"""Streaming feature store. The same code path builds training data and serves live scores,
so there is no train/serve skew, and features(t) only ever sees transactions before t."""
from __future__ import annotations

import math
from collections import defaultdict, deque

import pandas as pd

from .graph import GraphSignal
from .ledger import Ledger, LedgerError
from .sim import BANKS, Sim

DAY, HOUR, TEN_MIN = 86400.0, 3600.0, 600.0
_EMPTY: deque = deque()

FEATURES = [
    # beneficiary (dst) behaviour
    "b_age", "b_active_h", "b_in_n_10m", "b_in_n_1h", "b_in_uniq_1h", "b_in_uniq_24h",
    "b_in_amt_1h", "b_out_n_1h", "b_out_amt_1h", "b_fwd_1h", "b_fwd_24h",
    "b_out_uniq_24h", "b_out_xbank_24h", "b_dev_accts",
    # remitter (src) behaviour
    "a_age", "a_in_amt_24h", "a_in_uniq_24h", "a_out_n_1h", "a_fwd_now", "a_since_in", "a_dev_accts",
    # transaction / pair
    "amount", "pair_prior", "cross_bank",
    # shared-ledger signals (0 none, 1 soft, 2 corroborated)
    "b_led", "b_dev_led", "a_led", "a_dev_led",
]

GNN_FEATURES = ["a_gnn", "b_gnn"]  # appended separately: opt-in, see model.py USE_GRAPH


def _win(dq: deque, now: float, w: float):
    """(count, amount, unique counterparties, cross-bank count) over the last w seconds."""
    n, amt, xb, cps, lim = 0, 0.0, 0, set(), now - w
    for ts, cp, a, x in reversed(dq):
        if ts < lim:
            break
        n += 1
        amt += a
        xb += x
        cps.add(cp)
    return n, amt, len(cps), xb


class FeatureStore:
    def __init__(self, ledger: Ledger | None = None, graph: bool = True):
        self.ledger = ledger
        self.gnn = GraphSignal(ledger) if graph else None
        self.inb: dict[str, deque] = defaultdict(deque)
        self.outb: dict[str, deque] = defaultdict(deque)
        self.first: dict[str, float] = {}
        self.last_in: dict[str, float] = {}
        self.pairs: dict[tuple, int] = defaultdict(int)
        self.acct_dev: dict[str, str] = {}
        self.dev_accts: dict[str, set] = defaultdict(set)

    def _tier(self, raw_id: str | None, kind: str, now: float) -> int:
        if self.ledger is None or raw_id is None:
            return 0
        return self.ledger.tier(raw_id, kind, now)

    def features(self, t: dict) -> dict:
        now, a, b, amt = t["ts"], t["src"], t["dst"], t["amount"]
        b_in10 = _win(self.inb.get(b, _EMPTY), now, TEN_MIN)
        b_in1 = _win(self.inb.get(b, _EMPTY), now, HOUR)
        b_in24 = _win(self.inb.get(b, _EMPTY), now, DAY)
        b_out1 = _win(self.outb.get(b, _EMPTY), now, HOUR)
        b_out24 = _win(self.outb.get(b, _EMPTY), now, DAY)
        a_in24 = _win(self.inb.get(a, _EMPTY), now, DAY)
        a_out1 = _win(self.outb.get(a, _EMPTY), now, HOUR)

        b_dev = self.acct_dev.get(b)
        a_dev = t["src_dev"]
        a_dev_set = self.dev_accts.get(a_dev, ())
        a_last_in = self.last_in.get(a)
        a_gnn, b_gnn = self.gnn.score(a, b, now) if self.gnn else (0.0, 0.0)

        return {
            "b_age": min(t["dst_age"], 3650.0),
            "b_active_h": (now - self.first[b]) / HOUR if b in self.first else 0.0,
            "b_in_n_10m": b_in10[0],
            "b_in_n_1h": b_in1[0],
            "b_in_uniq_1h": b_in1[2],
            "b_in_uniq_24h": b_in24[2],
            "b_in_amt_1h": math.log1p(b_in1[1]),
            "b_out_n_1h": b_out1[0],
            "b_out_amt_1h": math.log1p(b_out1[1]),
            "b_fwd_1h": min(b_out1[1] / (b_in1[1] + 1.0), 3.0),
            "b_fwd_24h": min(b_out24[1] / (b_in24[1] + 1.0), 3.0),
            "b_out_uniq_24h": b_out24[2],
            "b_out_xbank_24h": b_out24[3] / b_out24[0] if b_out24[0] else 0.0,
            "b_dev_accts": len(self.dev_accts.get(b_dev, ())) if b_dev else 0,
            "a_age": min(t["src_age"], 3650.0),
            "a_in_amt_24h": math.log1p(a_in24[1]),
            "a_in_uniq_24h": a_in24[2],
            "a_out_n_1h": a_out1[0],
            "a_fwd_now": min(amt / (a_in24[1] + 1.0), 5.0),
            "a_since_in": min(now - a_last_in, 2 * DAY) if a_last_in is not None else 2 * DAY,
            "a_dev_accts": len(a_dev_set) + (a not in a_dev_set),
            "amount": math.log1p(amt),
            "pair_prior": min(self.pairs.get((a, b), 0), 10),
            "cross_bank": int(t["src_bank"] != t["dst_bank"]),
            "b_led": self._tier(b, "acct", now),
            "b_dev_led": self._tier(b_dev, "device", now),
            "a_led": self._tier(a, "acct", now),
            "a_dev_led": self._tier(a_dev, "device", now),
            "a_gnn": a_gnn,
            "b_gnn": b_gnn,
        }

    def update(self, t: dict) -> None:
        now, a, b, amt = t["ts"], t["src"], t["dst"], t["amount"]
        xb = int(t["src_bank"] != t["dst_bank"])
        self.first.setdefault(a, now)
        self.first.setdefault(b, now)
        self.outb[a].append((now, b, amt, xb))
        self.inb[b].append((now, a, amt, xb))
        self.last_in[b] = now
        self.pairs[(a, b)] += 1
        self.acct_dev[a] = t["src_dev"]
        self.dev_accts[t["src_dev"]].add(a)
        for dq in (self.outb[a], self.inb[b]):
            while dq and dq[0][0] < now - DAY:
                dq.popleft()
        if self.gnn:
            self.gnn.update(t)


def make_ledger(seed: int = 0, banks=BANKS) -> tuple[Ledger, dict]:
    led = Ledger(epoch_key=f"epoch-{seed}".encode())
    return led, {b: led.enroll(b) for b in banks}


def build(sim: Sim) -> pd.DataFrame:
    """Replay a simulation in time order: ledger events land as they happen, features are
    computed *before* each transaction updates state."""
    led, clients = make_ledger()
    fs = FeatureStore(led)
    ev, j, rows = sim.events, 0, []
    for t in sim.txns.to_dict("records"):
        while j < len(ev) and ev[j]["ts"] <= t["ts"]:
            e = ev[j]
            j += 1
            try:
                clients[e["bank"]].publish(e["raw_id"], e["kind"], e["ttl"], now=e["ts"])
            except LedgerError:
                pass  # rate-limited / rejected signals are simply dropped
        f = fs.features(t)
        f.update(ts=t["ts"], label=t["label"], amount_raw=t["amount"], ring=t["ring"], hop=t["hop"])
        rows.append(f)
        fs.update(t)
    return pd.DataFrame(rows)
