"""Adversarial harness.

1. Train a baseline on clean mule behaviour.
2. Attack it with behavioural evasion (structuring, throttling, layering, aged accounts,
   device rotation) - the attacker changes *behaviour*, not feature vectors.
3. Retrain with evasive traces (adversarial training), re-attack, incl. a held-out
   'combined' strategy the hardened model never saw.

Run:  python -m mulenet.adversary            (full, ~1-2 min)
      python -m mulenet.adversary --quick    (smaller, for a smoke test)
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from .features import build
from .model import MuleModel, metrics
from .sim import PROFILES, generate

HARDEN_PROFILES = ["clean", "clean", "split", "slow", "aged_rotate", "layered"]
TEST_PROFILES = ["clean", "split", "slow", "aged_rotate", "layered", "combined"]


def sim_df(profile: str, seed: int, **kw) -> pd.DataFrame:
    return build(generate(seed, evasion=PROFILES[profile], **kw))


def val_df(seed: int, kw: dict) -> pd.DataFrame:
    """Clean validation traffic used only to set FPR-budget thresholds (3 sims for stability)."""
    return pd.concat([sim_df("clean", seed + 900 + i, **kw) for i in range(3)], ignore_index=True)


def train_baseline(seed: int = 1000, quick: bool = False, use_graph: bool = False) -> MuleModel:
    kw = dict(n_accounts=600 if quick else 1200, n_rings=10 if quick else 20)
    train = pd.concat([sim_df("clean", seed + i, **kw) for i in range(len(HARDEN_PROFILES))], ignore_index=True)
    return MuleModel(use_graph=use_graph).fit(train).calibrate(val_df(seed, kw))


def train_hardened(seed: int = 1000, quick: bool = False, use_graph: bool = False) -> MuleModel:
    kw = dict(n_accounts=600 if quick else 1200, n_rings=10 if quick else 20)
    train = pd.concat([sim_df(p, seed + i, **kw) for i, p in enumerate(HARDEN_PROFILES)], ignore_index=True)
    return MuleModel(use_graph=use_graph).fit(train).calibrate(val_df(seed, kw))


def evaluate(model: MuleModel, tests: dict[str, pd.DataFrame]) -> dict[str, dict]:
    return {name: metrics(df, model.decide(df)) for name, df in tests.items()}


def table(cols: dict[str, dict]) -> str:
    names = list(cols)
    h = "| attacker profile | " + " -> ".join(f"victim% ({n})" for n in names) + " | FPR | hold-FPR |\n"
    h += "|---|" + "---|" * (len(names) + 1) + "\n"
    rows = []
    for k in cols[names[0]]:
        tag = f"{k} (held-out)" if k == "combined" else k
        vr = " -> ".join(f"{cols[n][k]['victim_recall']:.1%}" for n in names)
        last = cols[names[-1]][k]
        rows.append(f"| {tag} | {vr} | {last['fpr']:.2%} | {last['hold_fpr']:.3%} |")
    return h + "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="results")
    a = ap.parse_args()
    t0 = time.time()
    kw = dict(n_accounts=600 if a.quick else 1200, n_rings=6 if a.quick else 12)
    tests = {p: sim_df(p, 5000 + i, **kw) for i, p in enumerate(TEST_PROFILES)}
    print(f"[{time.time()-t0:5.1f}s] built {len(tests)} held-out attack traces", flush=True)
    base = train_baseline(quick=a.quick)
    print(f"[{time.time()-t0:5.1f}s] baseline trained", flush=True)
    hard = train_hardened(quick=a.quick)
    print(f"[{time.time()-t0:5.1f}s] hardened trained", flush=True)
    hard_g = train_hardened(quick=a.quick, use_graph=True)
    print(f"[{time.time()-t0:5.1f}s] hardened+graph trained", flush=True)
    results = {
        "baseline": evaluate(base, tests),
        "hardened": evaluate(hard, tests),
        "hardened+graph": evaluate(hard_g, tests),
    }
    md = table(results)
    print("\n" + md)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    (out / "adversarial_eval.md").write_text(md + "\n")
    (out / "adversarial_eval.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
