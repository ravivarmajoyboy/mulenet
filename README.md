# MuleNet — cross-bank mule-account intelligence (Drunix CHL-7007, PS1 Real-Time Payments)

Banks share **keyed-hash** risk signals on a signed, hash-chained ledger. A streaming scorer fuses
those signals with behavioural + graph-propagation features and returns ALLOW / STEP_UP / HOLD per
payment. An adversarial harness attacks the model with evasive mule behaviour, then retrains against
it. The ledger also exists as a real Solidity contract, and a React console gives an analyst a live
view of decisions and the shared signal ledger.

```
make install && make test        # 21 python tests
make eval                        # adversarial eval  -> results/adversarial_eval.md
make train && make serve         # API on :8000  (docs at /docs)

cd chain && npm install && npm test        # 8 Solidity tests, fully local
cd console && npm install && npm run dev   # analyst console on :5173 (needs the API running)
```

## Layout
| path | what |
|---|---|
| `mulenet/ledger.py` | Ed25519-signed, hash-chained ledger (Python reference impl). 1 bank = SOFT (step-up); >=2 distinct banks = HARD (hold). TTL, self-revoke, governance appeal, per-bank rate limit, tamper detection. |
| `mulenet/features.py` | Online feature store (10m/1h/24h windows, pass-through, fan-in, device sharing, ledger tiers, graph signal). Same code trains and serves; tested for no look-ahead. |
| `mulenet/graph.py` | **Graph-propagation signal.** Personalized-PageRank diffusion of ledger-flagged risk through the recent payment graph (APPNP-style: propagate, don't train an end-to-end net). Catches mules with no suspicious features of their own that are one hop from a known-bad account. Causal — only ever sees edges and signals that existed at the time. |
| `mulenet/sim.py` | Multi-bank traffic + mule rings + 5 evasion profiles + benign hard negatives (merchants, collections, pay-on-behalf forwarders, family devices, new customers). |
| `mulenet/model.py` | Gradient-boosted fast path, thresholds set by FPR budget, ledger-aware decision policy, reason codes (now including the graph signal). |
| `mulenet/adversary.py` | Evasion evaluation + adversarial training. Three-way comparison: baseline / hardened / hardened+graph, including a held-out `combined` attack never seen in training. |
| `mulenet/api.py` | `/v1/score`, `/v1/cases`, `/v1/ledger/{publish,revoke,status,verify}`, `/v1/appeals`. Bank identity comes from the API key, not the request body. |
| `chain/contracts/MuleLedger.sol` | **On-chain version of the ledger** — same corroboration/TTL/appeal/rate-limit logic as `ledger.py`, as a real Solidity contract with its own test suite. |
| `console/` | **React analyst console** (Vite): score a payment live, watch the case queue, publish/revoke/inspect ledger signals, verify chain integrity, uphold appeals as governance. |
| `mulenet/aml_adapter.py`, `mulenet/aml_eval.py` | **External validation on real data** (IBM AMLworld `HI-Small_Trans.csv`, not our own simulator). Adapter normalizes the CSV into MuleNet's causal feature pipeline; eval trains/calibrates/tests a behavioural-only model on it. |

## Current results (synthetic data, see caveats)
Scoring latency in-service: p50 6.0 ms, p99 6.5 ms (single process, 300 requests).

| attacker profile | victim payments blocked: baseline -> hardened -> hardened+graph | FPR | hold-FPR |
|---|---|---|---|
| clean | 100.0% -> 100.0% -> 100.0% | 0.37% | 0.106% |
| split | 100.0% -> 100.0% -> 100.0% | 0.50% | 0.129% |
| slow | 99.2% -> 99.7% -> 99.7% | 0.48% | 0.111% |
| aged_rotate | 15.5% -> 97.3% -> 97.3% | 0.56% | 0.192% |
| layered | 100.0% -> 100.0% -> 100.0% | 0.42% | 0.095% |
| **combined (held-out)** | 46.5% -> 85.9% -> **90.2%** | 0.34% | 0.055% |

The graph signal's whole point is the held-out `combined` row: adversarial training alone gets to
85.9%, guilt-by-association through the payment graph adds another 4.3 points on an attack pattern
the model never trained on. It does nothing on the other profiles because they don't have the
"clean-looking mule near a flagged one" structure it targets — expected, not a bug.

## Solidity ledger
`chain/contracts/MuleLedger.sol` mirrors `ledger.py` on-chain: `publish`/`revoke` per bank, a
`hardBankThreshold` for corroboration, TTL-based expiry checked at query time, an owner-gated
`enrollBank`, and a `governance`-gated `upholdAppeal` that clears a key without erasing the event
history. 8 tests in `chain/test/MuleLedger.test.js` cover corroboration, TTL, revoke, appeals,
rate-limiting, and event emission for the audit trail.

**Note on running it here:** Hardhat's default `compile` task downloads the solc binary from
`binaries.soliditylang.org`, which this sandboxed environment couldn't reach. `chain/scripts/offline-compile.js`
compiles the same contract with the `solc` npm package instead (fully local, no network needed) and
writes Hardhat-format artifacts directly; `npm test` and `npm run deploy:local` both call it first.
If your machine can reach the usual solc download, `npx hardhat compile` works normally too — the
offline path is a workaround for this sandbox, not a requirement of the contract itself.

Deploying to a real testnet (Sepolia, Polygon Amoy, etc.) needs an RPC URL and a funded wallet in
`hardhat.config.js` — that has to happen on your machine, since this sandbox has no outbound access
to any blockchain RPC endpoint. `chain/scripts/deploy.js` already runs against the local Hardhat
network; adding a network block and an env-based key is the only change needed for a live deploy.

## React console
`console/` is a small Vite + React app with three views: score a payment against the live API and
see the decision, ledger tiers, and graph score; watch the case queue (every STEP_UP/HOLD, polling
every 4s); and a ledger page to publish/revoke signals, look up a tier, verify the hash chain, and
(acting as governance) uphold an appeal. It talks to `mulenet/api.py` over HTTP — start the API
first (`make serve`), then `cd console && npm install && npm run dev`. `.env.example` shows the two
env vars it reads (`VITE_API_URL`, `VITE_BANK_KEY`); the acting bank can also be switched live from
the console's sidebar to demo multiple banks' perspectives in one run.

## External validation on real data: IBM AMLworld
Everything above is validated on our own simulator. `HI-Small_Trans.csv` from IBM's AMLworld
benchmark ([Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml))
is a real, independently-labelled, multi-bank transaction dataset — 5.08M transactions, ~30k
banks, ~500k accounts, 0.10% laundering rate. **It is not UPI data** (amounts run up to ~1e12,
institutional wire/ACH/credit-card transfers, not consumer payments) — treat this as "does the
behavioural graph-feature approach generalize to a real transaction graph", not "validated on
real UPI data", since no public UPI dataset exists.

```
make aml-adapt CSV=/path/to/HI-Small_Trans.csv   # ~3 min, streams the file once, writes results/aml_{train,cal,test}.parquet
make aml-eval                                    # trains + evaluates -> results/aml_validation.md
```

No device IDs or account-open dates exist in this dataset, so those signals aren't testable here;
ledger/graph-propagation columns are dropped entirely for this model variant rather than left in
as always-zero dead weight (see `mulenet/aml_adapter.py`'s docstring for the exact proxies used
and their limitations — an account's "age" here is days-since-first-observed, not its real age).

**Result: ROC-AUC 0.916, PR-AUC 0.235**, on a held-out time slice the model never trained on.

| FPR budget | actual FPR | recall | precision |
|---|---|---|---|
| 5.0% | 28.0% | 89.5% | 0.007 |
| 1.0% | 9.7% | 73.4% | 0.016 |
| 0.1% | 1.3% | 48.6% | 0.075 |

**The FPR-budget gap is a real finding, not a bug:** thresholds calibrated on one time window
don't hold on a later one (actual FPR runs 3-9x over budget). This dataset is heavily front-loaded
— 98% of transactions fall in the first 10 of 18 days — so train/calibration/test are split by row
count on the time-sorted stream, not by calendar day (an earlier calendar-day split landed almost
entirely in the near-empty last week and produced a ~143-row test set). Even with a sensible
split, the score distribution still shifts between calibration and test windows — a real
deployment needs to recalibrate close to serving time, not once at training time. The synthetic
simulator doesn't show this failure mode because its evasion profiles model attacker adaptation,
not this kind of drift.


- **Synthetic data.** The simulator is still far more separable than real UPI traffic (note 100% on
  the naive attacker). Treat the table as a *relative robustness* test (baseline vs hardened vs
  hardened+graph), not expected production accuracy. Do not present absolute recall as a real-world claim.
- The held-out `combined` attack still evades ~10% of victim payments even with the graph signal.
- The graph signal is Personalized PageRank over recent edges, not a trained GNN (no `torch`
  dependency, deliberately — see the note in `mulenet/graph.py`). It's the right tool for
  guilt-by-association from *known* ledger signals; it does not learn new structural patterns the
  way a trained GCN/GraphSAGE model would. Framed honestly to judges as "graph propagation", not
  "we trained a graph neural network."
- Blinding uses a shared epoch key (Python) / no blinding scheme at all yet on-chain (Solidity —
  the demo passes `keccak256` of the raw ID for simplicity). Production needs an OPRF/PSI service
  on both sides. Hashed identifiers are still personal data under the DPDP Act: consent and
  governance must be designed, not assumed.
- The Solidity contract's bank enrolment and governance address are owner-controlled single keys —
  fine for a demo, not for production (see the contract's NatSpec for what a real deployment needs).
- Reason codes are rule-derived, not SHAP. No NPCI integration yet (access details unconfirmed).
- API demo keys are static; feature store, ledger, and case queue are in-process (no persistence,
  single worker, and the case queue is capped at 200 entries).
- The console's CORS policy (`allow_origins=["*"]`) is demo-only.
- The AMLworld validation trains fresh on that dataset (not the synthetic-trained model applied
  zero-shot) — that's a deliberate, more defensible choice (see that section), not a transfer-
  learning result. Its calibration-drift gap is unresolved; don't quote the FPR-budget numbers
  without the actual-FPR column next to them.

## Roadmap
1. Ledger-poisoning red team: malicious bank publishes false signals -> measure wrongful-hold rate vs corroboration threshold / rate limit.
2. A trained GNN (GCN/GraphSAGE) over payment paths, benchmarked against the current PPR propagation on the `combined` attack.
3. Longer windows / account-history features for throttled (>24h) laundering.
4. Kafka/Redis Streams ingestion, Redis feature store, Postgres case + audit store (replacing the in-process demo state).
5. OPRF-based blinding on both the Python and Solidity sides; real testnet deployment.
6. Wire the console's case queue to persistent storage so it survives an API restart.
7. Close the AMLworld calibration-drift gap: recalibrate on a slice closer to serving time (e.g. a rolling window) instead of a single fixed calibration split, and see how much of the 3-9x FPR overshoot that alone removes.
