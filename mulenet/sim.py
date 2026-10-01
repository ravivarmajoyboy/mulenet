"""Synthetic multi-bank UPI-style traffic with mule rings and attacker evasion profiles.

Benign traffic deliberately includes hard negatives so a model can't win on fan-in alone:
merchants (high fan-in + daily settlement), collection accounts (young, bursty inflow,
no forwarding), salary pass-through (big in -> big out within hours), family-shared
devices, and normal users paying external sinks (ATM/wallet/etc).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BANKS = ["BANK_A", "BANK_B", "BANK_C", "BANK_D"]
DAY = 86400.0
N_SINKS = 10


@dataclass(frozen=True)
class Evasion:
    name: str = "clean"
    split: int = 1  # pieces per first-hop forward (structuring)
    hold_s: float = 240.0  # mean dwell time before forwarding (throttling)
    hops: int = 2  # layering depth
    aged: bool = False  # buy aged accounts (defeats account-age signal)
    rotate_devices: bool = False  # one device per mule account (defeats device-sharing)
    victim_mu: float = 7.7  # log-mean of victim payment (low-and-slow => smaller)
    campaign_s: float = 5400.0  # window over which victims are hit
    victims: tuple = (8, 16)  # victims per ring [lo, hi)


PROFILES: dict[str, Evasion] = {
    "clean": Evasion(),
    "split": Evasion("split", split=4),
    "slow": Evasion("slow", hold_s=4 * 3600, victim_mu=6.6, campaign_s=6 * 3600, victims=(20, 40)),
    "aged_rotate": Evasion("aged_rotate", aged=True, rotate_devices=True),
    "layered": Evasion("layered", hops=4, hold_s=600),
    "combined": Evasion(
        "combined", split=3, hold_s=2 * 3600, hops=3, aged=True, rotate_devices=True,
        victim_mu=6.9, campaign_s=4 * 3600, victims=(16, 30),
    ),
}


@dataclass
class Sim:
    txns: pd.DataFrame
    events: list  # ledger publish events: ts, bank, raw_id, kind, ttl
    evasion: Evasion


def generate(seed: int, n_accounts: int = 1200, days: int = 3, n_rings: int = 20,
             evasion: Evasion = PROFILES["clean"]) -> Sim:
    rng = np.random.default_rng(seed)
    accts: dict[str, dict] = {}
    rows: list[tuple] = []
    events: list[dict] = []
    dev_n = iter(range(10**9))

    def new_dev() -> str:
        return f"DEV{seed}-{next(dev_n)}"

    def mk(kind: str, age: float, bank: str | None = None, dev: str | None = None) -> str:
        aid = f"ACC{len(accts)}"
        accts[aid] = dict(bank=bank or BANKS[rng.integers(len(BANKS))], age=float(age),
                          dev=dev or new_dev(), kind=kind)
        return aid

    def tx(ts, s, d, amt, label=0, ring=-1, hop=-1):
        sa = accts[s]
        if d.startswith("EXT"):
            db, da = "EXT", 3650.0
        else:
            db, da = accts[d]["bank"], accts[d]["age"] + ts / DAY
        rows.append((float(ts), s, d, float(amt), sa["bank"], db, sa["dev"],
                     sa["age"] + ts / DAY, da, label, ring, hop))

    normal = [mk("normal", rng.uniform(1, 60) if rng.random() < 0.25 else rng.uniform(30, 3000))
              for _ in range(n_accounts)]
    merchants = [mk("merchant", rng.uniform(200, 3000)) for _ in range(max(5, n_accounts // 20))]
    collect = [mk("collection", rng.uniform(5, 40)) for _ in range(max(2, n_accounts // 70))]
    employers = [mk("employer", rng.uniform(500, 4000)) for _ in range(5)]
    normal_arr = np.array(normal)

    # family-shared devices (hard negative for device-sharing signal)
    for a in rng.choice(normal_arr, int(0.08 * n_accounts), replace=False):
        accts[a]["dev"] = accts[normal[rng.integers(n_accounts)]]["dev"]

    contacts = {}
    for a in normal:
        c = [x for x in rng.choice(normal_arr, 5, replace=False) if x != a][:4]
        contacts[a] = c

    # ---- benign flows
    for a in normal:
        for _ in range(rng.poisson(5 * days)):
            ts = rng.integers(days) * DAY + float(np.clip(rng.normal(13 * 3600, 4 * 3600), 0, DAY - 1))
            u = rng.random()
            if u < 0.25:
                tx(ts, a, merchants[rng.integers(len(merchants))], rng.lognormal(5.5, 0.8))
            elif u < 0.92:
                tx(ts, a, contacts[a][rng.integers(4)],
                   rng.lognormal(8.3, 0.8) if rng.random() < 0.10 else rng.lognormal(6.3, 1.1))
            elif u < 0.97:
                tx(ts, a, normal[rng.integers(n_accounts)], rng.lognormal(6.0, 1.2))
            else:
                tx(ts, a, f"EXT{rng.integers(N_SINKS)}", rng.lognormal(6.5, 1.0))

    for a in rng.choice(normal_arr, int(0.02 * n_accounts), replace=False):  # salary pass-through
        ts = rng.integers(days) * DAY + rng.uniform(8, 11) * 3600
        amt = rng.lognormal(10.2, 0.3)
        tx(ts, employers[rng.integers(len(employers))], a, amt)
        tx(ts + rng.uniform(1, 20) * 3600, a, contacts[a][0], amt * rng.uniform(0.6, 0.9))

    for a in rng.choice(normal_arr, int(0.04 * n_accounts), replace=False):  # pay-on-behalf forwarders
        for _ in range(rng.integers(1, 4)):
            ts = rng.integers(days) * DAY + rng.uniform(8, 22) * 3600
            amt = rng.lognormal(7.0, 0.8)
            tx(ts, normal[rng.integers(n_accounts)], a, amt)
            tx(ts + 30 + rng.exponential(600), a, contacts[a][rng.integers(4)], amt * rng.uniform(0.9, 1.0))

    for c in collect:  # bursty benign collections (wedding/fundraiser), no forwarding
        t0 = rng.integers(days) * DAY + rng.uniform(6, 14) * 3600
        for s in rng.choice(normal_arr, rng.integers(30, 80), replace=False):
            tx(t0 + rng.uniform(0, 6 * 3600), s, c, rng.lognormal(6.0, 0.6))

    inflow: dict[tuple, float] = {}
    for r in rows:  # merchant daily settlement to a supplier
        if r[2] in merchants:
            k = (r[2], int(r[0] // DAY))
            inflow[k] = inflow.get(k, 0.0) + r[3]
    for (m, d), amt in inflow.items():
        tx(d * DAY + 23 * 3600, m, normal[rng.integers(n_accounts)], 0.7 * amt)

    # ---- mule rings
    for ring in range(n_rings):
        t0 = rng.uniform(0.3 * DAY, (days - 0.8) * DAY)
        ring_dev = [new_dev(), new_dev()]

        def mule() -> str:
            age = rng.uniform(200, 900) if evasion.aged else rng.uniform(1, 25)
            dev = new_dev() if evasion.rotate_devices else ring_dev[rng.integers(2)]
            return mk("mule", age, None, dev)

        colls = [mule() for _ in range(rng.integers(2, 4))]
        layers = [[mule() for _ in range(rng.integers(2, 4))] for _ in range(evasion.hops)]
        for m in colls + [x for layer in layers for x in layer]:  # "seasoning": benign-looking history
            for _ in range(rng.integers(1, 4)):
                ts = max(0.0, t0 - rng.uniform(1, 30) * 3600)
                tx(ts, m, merchants[rng.integers(len(merchants))], rng.lognormal(5.0, 0.6))
                tx(ts + 60, normal[rng.integers(n_accounts)], m, rng.lognormal(5.0, 0.6))
        nv = int(rng.integers(*evasion.victims))
        vtimes = np.sort(t0 + rng.uniform(0, evasion.campaign_s, nv))
        for v, tv in zip(rng.choice(normal_arr, nv, replace=False), vtimes):
            c = colls[rng.integers(len(colls))]
            amt = float(rng.lognormal(evasion.victim_mu, 0.9))
            tx(tv, v, c, amt, 1, ring, 0)

            if rng.random() < 0.55:  # victim complains -> victim's bank flags the beneficiary
                tc = tv + rng.lognormal(np.log(3600), 0.6)
                events.append(dict(ts=tc, bank=accts[v]["bank"], raw_id=c, kind="acct", ttl=7 * DAY))
                if rng.random() < 0.5:  # beneficiary bank confirms and flags the device
                    events.append(dict(ts=tc + 1800, bank=accts[c]["bank"], raw_id=accts[c]["dev"],
                                       kind="device", ttl=7 * DAY))
                if layers and rng.random() < 0.5:  # traceback flags first-layer accounts
                    for l1 in layers[0]:
                        events.append(dict(ts=tc + 2700, bank=accts[v]["bank"], raw_id=l1,
                                           kind="acct", ttl=7 * DAY))

            holders = [(c, tv, amt)]
            for h in range(evasion.hops + 1):
                nxt = []
                for node, t_in, a_in in holders:
                    parts = evasion.split if h == 0 else 1
                    for sh in rng.dirichlet(np.ones(parts)) * a_in * 0.96:
                        t_out = t_in + 30 + rng.exponential(evasion.hold_s)
                        dst = layers[h][rng.integers(len(layers[h]))] if h < evasion.hops \
                            else f"EXT{rng.integers(N_SINKS)}"
                        tx(t_out, node, dst, sh, 1, ring, h + 1)
                        nxt.append((dst, t_out, float(sh)))
                holders = nxt

    df = pd.DataFrame(rows, columns=["ts", "src", "dst", "amount", "src_bank", "dst_bank",
                                     "src_dev", "src_age", "dst_age", "label", "ring", "hop"])
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    return Sim(df, sorted(events, key=lambda e: e["ts"]), evasion)
