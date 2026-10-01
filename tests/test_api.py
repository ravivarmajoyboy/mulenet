import os

os.environ["MULENET_FAST"] = "1"

import pytest
from fastapi.testclient import TestClient

from mulenet.api import app


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        yield client


def H(bank):
    return {"X-Bank-Key": f"demo-key-{bank}"}


TXN = dict(src="u1", dst="mule9", amount=4200.0, src_bank="BANK_A", dst_bank="BANK_B",
           src_dev="dev-u1", src_age=900, dst_age=400)


def test_auth_required(c):
    assert c.post("/v1/score", json=TXN).status_code == 422
    assert c.post("/v1/score", json=TXN, headers={"X-Bank-Key": "nope"}).status_code == 401


def test_score_shape_and_latency(c):
    r = c.post("/v1/score", json=TXN, headers=H("BANK_A")).json()
    assert r["decision"] in {"ALLOW", "STEP_UP", "HOLD"}
    assert r["latency_ms"] < 100  # p99 target; single-request sanity check


def test_corroboration_flow_and_appeal(c):
    t = dict(TXN, dst="mule-77")
    c.post("/v1/ledger/publish", json={"raw_id": "mule-77"}, headers=H("BANK_A"))
    r = c.post("/v1/score", json=dict(t, src="v1", src_dev="dv1"), headers=H("BANK_C")).json()
    assert r["decision"] in {"STEP_UP", "HOLD"} and r["ledger"]["beneficiary_tier"] == 1

    c.post("/v1/ledger/publish", json={"raw_id": "mule-77"}, headers=H("BANK_B"))
    r = c.post("/v1/score", json=dict(t, src="v2", src_dev="dv2"), headers=H("BANK_C")).json()
    assert r["decision"] == "HOLD" and r["ledger"]["beneficiary_tier"] == 2

    assert c.post("/v1/appeals", json={"raw_id": "mule-77", "reason": "kyc verified"},
                  headers=H("BANK_A")).status_code == 403
    assert c.post("/v1/appeals", json={"raw_id": "mule-77", "reason": "kyc verified"},
                  headers=H("REG")).status_code == 200
    st = c.get("/v1/ledger/status", params={"raw_id": "mule-77"}, headers=H("BANK_A")).json()
    assert st["tier"] == 0


def test_step_up_or_hold_lands_in_case_queue(c):
    t = dict(TXN, dst="mule-case-1", src="v-case")
    r = c.post("/v1/score", json=t, headers=H("BANK_A")).json()
    if r["decision"] != "ALLOW":
        cq = c.get("/v1/cases", headers=H("BANK_A")).json()["cases"]
        entry = next(x for x in cq if x["dst"] == "mule-case-1")
        assert entry["ts"] is not None  # regression: ts must be the resolved time, not the raw None default


def test_ledger_integrity_endpoint(c):
    r = c.get("/v1/ledger/verify", headers=H("BANK_D")).json()
    assert r["ok"] is True and r["entries"] >= 3
