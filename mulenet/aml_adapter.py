"""External validation on real transaction data: IBM AMLworld (HI-Small_Trans.csv).

https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml

What this is and isn't
- This is a real, multi-bank, multi-currency transaction graph with real laundering labels
  ("Is Laundering"). It is NOT UPI/retail P2P data: amounts range up to ~1e12 (institutional
  wire/ACH/credit-card/reinvestment transfers, not consumer payments), and there are ~30k banks
  and ~500k accounts. Treat this as "does the behavioural graph-feature approach generalize to a
  real, independently-labelled multi-bank transaction graph", not "validated on real UPI data" -
  no such public dataset exists (see the chat history / README for why).
- The dataset has no device IDs and no account-open dates, so those signals can't be tested here.
  `src_dev` is set to the account id itself (defeats device-sharing features, deliberately - we're
  not fabricating device data), and account "age" is a proxy: days since the account was first
  observed in this window, not its real age. Every account necessarily looks brand-new the moment
  it's first seen, which would bias the "new account" signal at the very start of the stream, so
  the first BURN_IN_FRAC of rows are used only to warm up the streaming feature store and are
  excluded from train/calibration/test.
- No ledger/cross-bank signal-sharing ground truth exists in this data either (or anywhere public),
  so this validates the *behavioural* features and the GBDT only - ledger and graph-propagation
  columns are necessarily constant zero here and are dropped from this model variant rather than
  left in as dead weight.

Usage:  python -m mulenet.aml_adapter /path/to/HI-Small_Trans.csv
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURES, FeatureStore

BEHAVIORAL_FEATURES = [f for f in FEATURES if not f.endswith("_led")]
BURN_IN_FRAC = 0.05   # warms up the streaming feature store; see note below
TRAIN_FRAC = 0.70
CAL_FRAC = 0.15       # clean-only slice right after train, for FPR-budget calibration
# TEST_FRAC is the remainder (~0.15)
#
# Splits are row-count fractions of the time-sorted stream, not calendar-day boundaries. This
# dataset is extremely front-loaded - 98% of the 5.08M rows fall in the first 10 of 18 days, with
# a near-empty tail (the last week has a few hundred rows total). An earlier version of this
# script split by calendar time ("last 25% of the date range") and landed almost entirely in that
# empty tail: a 143-row test set. Row-count fractions on the already time-sorted stream keep the
# causal train-before-cal-before-test property while giving each split a real, roughly
# proportional amount of data regardless of how unevenly it's spread across calendar days.


def load_aml(path: str, nrows: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(path, nrows=nrows)
    # (s - epoch) / 1s is resolution-independent: pandas >=2.2 may parse to us/ns/s depending on
    # version, and a naive `.astype('int64') / 1e9` silently assumes ns and is wrong by 1000x
    # on a build that parses to microseconds (verified against pandas 3.0.2 in this sandbox).
    dt = pd.to_datetime(df["Timestamp"], format="%Y/%m/%d %H:%M")
    ts = (dt - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)

    # Factorize accounts/banks to compact int codes - same node identity, far cheaper to hash
    # and store than "bank:account" strings across a 5M-row, ~500k-account graph.
    src_key = df["From Bank"].astype(str) + ":" + df["Account"].astype(str)
    dst_key = df["To Bank"].astype(str) + ":" + df["Account.1"].astype(str)
    codes, _ = pd.factorize(pd.concat([src_key, dst_key], ignore_index=True), sort=False)
    n = len(df)
    src_code, dst_code = codes[:n], codes[n:]
    bank_codes, _ = pd.factorize(pd.concat([df["From Bank"], df["To Bank"]], ignore_index=True), sort=False)
    src_bank, dst_bank = bank_codes[:n], bank_codes[n:]

    out = pd.DataFrame({
        "ts": ts.to_numpy(),
        "src": src_code,
        "dst": dst_code,
        "amount": df["Amount Paid"].astype(float).clip(lower=0.01).to_numpy(),
        "src_bank": src_bank,
        "dst_bank": dst_bank,
        "label": df["Is Laundering"].astype(int).to_numpy(),
    })
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


def build_and_write(df: pd.DataFrame, out_dir: Path) -> dict[str, int]:
    """Causal feature build, identical contract to mulenet.features.build: features(t) is
    computed strictly before update(t). Account 'age' and device id are proxies - see module
    docstring. No ledger, no graph (this dataset can't support either).

    Writes each row directly into a per-split buffer, allocated lazily and flushed to parquet
    the moment that split's row range ends - never holds all three splits' buffers (or the
    original input frame) in memory at once. On this sandbox's ~3.3GB budget, a version that
    preallocated all three buffers up front and kept the raw input frame alive throughout got
    OOM-killed right as it started writing the larger cal/test buffers near the end of the run;
    freeing each piece as soon as it's no longer needed was the fix, not a bigger box.
    """
    import gc

    n = len(df)
    ts_a = df["ts"].to_numpy()
    label_a = df["label"].to_numpy()
    src_a, dst_a = df["src"].to_numpy(), df["dst"].to_numpy()
    amt_a = df["amount"].to_numpy()
    sb_a, db_a = df["src_bank"].to_numpy(), df["dst_bank"].to_numpy()
    del df
    gc.collect()

    # Row-count fraction boundaries on the time-sorted stream (see BURN_IN_FRAC note above).
    burn_end = int(n * BURN_IN_FRAC)
    train_end = burn_end + int((n - burn_end) * TRAIN_FRAC)
    cal_end = train_end + int((n - burn_end) * CAL_FRAC)
    # cal keeps only label==0 rows; its buffer is sized by scanning the label column up front,
    # not by (cal_end - train_end), which would over-allocate for a slice that's mostly kept.
    cal_size = int((label_a[train_end:cal_end] == 0).sum())
    sizes = {"train": train_end - burn_end, "cal": cal_size, "test": n - cal_end}

    cols = BEHAVIORAL_FEATURES
    all_cols = cols + ["label", "amount_raw", "ts"]

    fs = FeatureStore(ledger=None, graph=False)
    PAIR_CAP = 500_000  # see note in the module docstring / README on this trade-off
    first_seen: dict[int, float] = {}

    buf, j, current = None, 0, None
    out_dir.mkdir(exist_ok=True)
    counts: dict[str, int] = {}

    def start(name: str) -> None:
        nonlocal buf, j, current
        current, j = name, 0
        buf = {c: np.empty(sizes[name], dtype=np.float32) for c in all_cols}
        print(f"  -> {name}: {sizes[name]:,} rows expected", flush=True)

    def flush() -> None:
        nonlocal buf
        pd.DataFrame(buf).iloc[:j].to_parquet(out_dir / f"aml_{current}.parquet")
        counts[current] = j
        print(f"  wrote {out_dir}/aml_{current}.parquet ({j:,} rows, "
              f"{int(buf['label'][:j].sum())} positive)", flush=True)
        buf = None
        gc.collect()

    t0 = time.time()
    start("train")
    for i in range(n):
        if i == train_end:
            flush()
            start("cal")
        elif i == cal_end:
            flush()
            start("test")

        src, dst, ts_i = int(src_a[i]), int(dst_a[i]), float(ts_a[i])
        fs_src = first_seen.setdefault(src, ts_i)
        fs_dst = first_seen.setdefault(dst, ts_i)
        t = {
            "ts": ts_i, "src": src, "dst": dst, "amount": float(amt_a[i]),
            "src_bank": int(sb_a[i]), "dst_bank": int(db_a[i]), "src_dev": src,
            "src_age": min((ts_i - fs_src) / 86400.0, 3650.0),
            "dst_age": min((ts_i - fs_dst) / 86400.0, 3650.0),
        }
        f = fs.features(t)
        if i >= burn_end and not (current == "cal" and label_a[i] != 0):
            for c in cols:
                buf[c][j] = f[c]
            buf["label"][j] = label_a[i]
            buf["amount_raw"][j] = t["amount"]
            buf["ts"][j] = ts_i
            j += 1
        fs.update(t)
        if len(fs.pairs) > PAIR_CAP:
            fs.pairs.clear()
        if (i + 1) % 1_000_000 == 0:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            print(f"  ...{i+1:,} / {n:,} rows ({time.time()-t0:.0f}s, peak {rss:.0f} MB)", flush=True)
    flush()
    return counts


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "HI-Small_Trans.csv"
    print(f"loading {path} ...")
    raw = load_aml(path)
    print(f"{len(raw):,} rows, {raw['label'].sum():,} laundering-labelled "
          f"({raw['label'].mean():.4%}), {raw.ts.max()-raw.ts.min():.0f}s span")

    print("building causal features (this streams the whole file once)...")
    counts = build_and_write(raw, Path("results"))
    print("done:", counts)


if __name__ == "__main__":
    main()
