import dataclasses

import pytest

from mulenet.ledger import GOVERNANCE, HARD, SOFT, Ledger, LedgerError, Entry

DAY = 86400.0


@pytest.fixture
def led():
    L = Ledger(b"k", hard_banks=2, rate_limit=3)
    L.clients = {b: L.enroll(b) for b in ("A", "B", "C")}
    L.gov = L.enroll(GOVERNANCE)
    return L


def test_single_bank_is_soft_two_banks_is_hard(led):
    led.clients["A"].publish("acct-1", now=10)
    assert led.tier("acct-1", "acct", 11) == SOFT
    led.clients["B"].publish("acct-1", now=20)
    assert led.tier("acct-1", "acct", 21) == HARD


def test_same_bank_repeat_does_not_corroborate(led):
    led.clients["A"].publish("x", now=1)
    led.clients["A"].publish("x", now=2)
    assert led.tier("x", "acct", 3) == SOFT


def test_ttl_expiry_and_revoke(led):
    led.clients["A"].publish("x", ttl=100, now=0)
    assert led.tier("x", "acct", 50) == SOFT
    assert led.tier("x", "acct", 101) == 0
    led.clients["B"].publish("y", now=200)
    led.clients["B"].revoke("y", now=210)
    assert led.tier("y", "acct", 220) == 0


def test_governance_appeal_clears_all_and_allows_new_signals(led):
    led.clients["A"].publish("x", now=1)
    led.clients["B"].publish("x", now=2)
    led.gov.revoke_all("x", now=3)
    assert led.tier("x", "acct", 4) == 0
    led.clients["C"].publish("x", now=5)
    assert led.tier("x", "acct", 6) == SOFT


def test_banks_cannot_use_governance_ops_and_gov_cannot_publish(led):
    with pytest.raises(LedgerError):
        led.clients["A"].revoke_all("x", now=1)
    with pytest.raises(LedgerError):
        led.gov.publish("x", now=1)


def test_rate_limit(led):
    for i in range(3):
        led.clients["A"].publish(f"id{i}", now=i)
    with pytest.raises(LedgerError, match="rate"):
        led.clients["A"].publish("id9", now=4)
    led.clients["A"].publish("id9", now=4000)  # window slid


def test_chain_detects_tampering(led):
    led.clients["A"].publish("x", now=1)
    led.clients["B"].publish("y", now=2)
    assert led.verify_chain() == (True, None)
    led.entries[0].ttl = 10**9  # extend a signal after the fact
    assert led.verify_chain() == (False, 0)


def test_forged_signature_and_stale_head_rejected(led):
    e = Entry(idx=0, ts=1, bank="A", op="publish", kind="acct", key=led.blind("x", "acct"),
              ttl=DAY, prev=led.head(), sig="00" * 64)
    with pytest.raises(LedgerError, match="signature"):
        led.submit(e)
    led.clients["A"].publish("x", now=1)
    e2 = dataclasses.replace(e, sig="00" * 64)  # idx/prev now stale
    with pytest.raises(LedgerError):
        led.submit(e2)


def test_no_raw_identifier_on_chain(led):
    led.clients["A"].publish("VERY-SECRET-VPA@bank", now=1)
    assert "VERY-SECRET" not in repr(led.entries)
