"""MuleNet scoring service.

  uvicorn mulenet.api:app --port 8000

Auth: `X-Bank-Key` header. The acting bank is derived from the key, never from the body,
so a participant cannot publish signals in another bank's name. Demo keys are
`demo-key-<BANK>` (override with MULENET_KEYS='{"key": "BANK_A", ...}').
Set MULENET_FAST=1 to train a tiny model at startup (tests / demos).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import joblib
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .features import FeatureStore, make_ledger
from .ledger import GOVERNANCE, LedgerError
from .model import NAMES, STEP_UP, MuleModel, explain
from .sim import BANKS

ARTIFACT = Path(os.environ.get("MULENET_MODEL", "artifacts/model.joblib"))


class State:
    def __init__(self) -> None:
        self.ledger, self.clients = make_ledger(seed=int(time.time()))
        self.gov = self.ledger.enroll(GOVERNANCE)
        self.fs = FeatureStore(self.ledger)
        self.lock = threading.Lock()
        self.model = self._load_model()
        self.cases: deque = deque(maxlen=200)  # analyst console case queue (in-memory demo store)
        default = {f"demo-key-{b}": b for b in BANKS} | {"demo-key-REG": GOVERNANCE}
        self.keys: dict[str, str] = json.loads(os.environ.get("MULENET_KEYS", "null")) or default

    @staticmethod
    def _load_model() -> MuleModel:
        if ARTIFACT.exists() and not os.environ.get("MULENET_FAST"):
            return joblib.load(ARTIFACT)
        from .adversary import train_hardened  # slow path: train on the fly
        return train_hardened(quick=True, use_graph=True)


_state: State | None = None


def state() -> State:
    global _state
    if _state is None:
        _state = State()
    return _state


def bank_of(x_bank_key: str = Header(...)) -> str:
    bank = state().keys.get(x_bank_key)
    if bank is None:
        raise HTTPException(401, "invalid bank key")
    return bank


app = FastAPI(title="MuleNet", version="0.1.0")
app.add_middleware(  # demo-only: the console runs on a different origin during local dev
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


class Txn(BaseModel):
    src: str
    dst: str
    amount: float = Field(gt=0)
    src_bank: str
    dst_bank: str
    src_dev: str
    src_age: float = 365.0
    dst_age: float = 365.0
    ts: float | None = None  # defaults to server time; keep monotonic if you set it


class SignalIn(BaseModel):
    raw_id: str
    kind: str = Field("acct", pattern="^(acct|device)$")
    ttl_s: float = Field(7 * 86400.0, gt=0, le=30 * 86400.0)


class AppealIn(BaseModel):
    raw_id: str
    kind: str = Field("acct", pattern="^(acct|device)$")
    reason: str = Field(min_length=3)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/v1/score")
def score(t: Txn, bank: str = Depends(bank_of)):
    S = state()
    t0 = time.perf_counter()
    rec = t.model_dump()
    rec["ts"] = rec["ts"] if rec["ts"] is not None else time.time()
    with S.lock:
        f = S.fs.features(rec)
        X = pd.DataFrame([f])
        p = float(S.model.score(X)[0])
        code = int(S.model.decide(X)[0])
        if code < 2:  # HOLD => payment not executed, state not updated
            S.fs.update(rec)
    result = {
        "decision": NAMES[code],
        "risk_score": round(p, 4),
        "ledger": {"beneficiary_tier": f["b_led"], "remitter_tier": f["a_led"]},
        "graph": {"remitter": round(f["a_gnn"], 3), "beneficiary": round(f["b_gnn"], 3)},
        "reasons": explain(f, code, p),
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    if code >= STEP_UP:
        S.cases.appendleft({"bank": bank, **rec, **result})
    return result


@app.get("/v1/cases")
def cases(bank: str = Depends(bank_of)):
    """Analyst console case queue: recent STEP_UP/HOLD decisions, newest first."""
    return {"cases": list(state().cases)}


@app.post("/v1/ledger/publish")
def publish(s: SignalIn, bank: str = Depends(bank_of)):
    if bank == GOVERNANCE:
        raise HTTPException(403, "governance cannot publish signals")
    try:
        e = state().clients[bank].publish(s.raw_id, s.kind, s.ttl_s, now=time.time())
    except LedgerError as err:
        raise HTTPException(409, str(err))
    return {"idx": e.idx, "hash": e.hash}


@app.post("/v1/ledger/revoke")
def revoke(s: SignalIn, bank: str = Depends(bank_of)):
    if bank == GOVERNANCE:
        raise HTTPException(403, "use /v1/appeals")
    try:
        e = state().clients[bank].revoke(s.raw_id, s.kind, now=time.time())
    except LedgerError as err:
        raise HTTPException(409, str(err))
    return {"idx": e.idx, "hash": e.hash}


@app.post("/v1/appeals")
def appeal(a: AppealIn, bank: str = Depends(bank_of)):
    """Governance-only: clears all signals on a key (appeal upheld). Reason is audited by the
    chain entry itself; store the case file off-chain."""
    if bank != GOVERNANCE:
        raise HTTPException(403, "governance key required")
    try:
        e = state().gov.revoke_all(a.raw_id, a.kind, now=time.time())
    except LedgerError as err:
        raise HTTPException(409, str(err))
    return {"idx": e.idx, "hash": e.hash}


@app.get("/v1/ledger/status")
def status(raw_id: str, kind: str = "acct", bank: str = Depends(bank_of)):
    tier, banks = state().ledger.status(raw_id, kind, time.time())
    return {"tier": tier, "reporting_banks": len(banks)}  # identities of reporters not exposed


@app.get("/v1/ledger/verify")
def verify(bank: str = Depends(bank_of)):
    ok, bad = state().ledger.verify_chain()
    return {"ok": ok, "first_bad_index": bad, "entries": len(state().ledger.entries),
            "head": state().ledger.head()}
