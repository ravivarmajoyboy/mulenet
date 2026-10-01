"""Permissioned, hash-chained signal ledger (reference implementation).

Design goals
- No raw customer data on-ledger: identifiers are keyed-hashed (HMAC) before publishing.
- Every entry is signed by the publishing bank (Ed25519) and hash-chained (tamper-evident).
- Anti-poisoning: one bank alone can only raise a SOFT signal (step-up auth).
  A HARD signal (hold) needs corroboration from >= `hard_banks` distinct banks.
- Signals expire (TTL), banks can revoke their own, and governance (regulator role)
  can clear a key after an appeal.
- Per-bank publish rate limit.

Swap-in path: keep the Ledger/BankClient interface, back it with Hyperledger Fabric
chaincode (or an EVM contract) - the scoring service only calls `status()`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

GENESIS = "0" * 64
SOFT, HARD = 1, 2
GOVERNANCE = "REG"
DAY = 86400.0


class LedgerError(Exception):
    pass


@dataclass
class Entry:
    idx: int
    ts: float
    bank: str
    op: str  # publish | revoke | revoke_all
    kind: str  # acct | device
    key: str  # blinded identifier
    ttl: float
    prev: str
    sig: str = ""
    hash: str = ""

    def body(self) -> bytes:
        d = {k: v for k, v in asdict(self).items() if k not in ("sig", "hash")}
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    def digest(self) -> str:
        return hashlib.sha256(
            self.prev.encode() + self.body() + bytes.fromhex(self.sig)
        ).hexdigest()


class Ledger:
    def __init__(self, epoch_key: bytes, hard_banks: int = 2, rate_limit: int = 500):
        self._epoch_key = epoch_key
        self.hard_banks = hard_banks
        self.rate_limit = rate_limit  # publishes per bank per hour
        self.entries: list[Entry] = []
        self.lock = threading.RLock()
        self._pub: dict[str, Ed25519PublicKey] = {}
        self._by_key: dict[tuple[str, str], list[Entry]] = defaultdict(list)
        self._recent: dict[str, deque] = defaultdict(deque)

    # -- identifiers -------------------------------------------------
    def blind(self, raw_id: str, kind: str) -> str:
        """Keyed hash of an identifier. NOTE: a shared epoch key lets any key holder
        test guesses for low-entropy IDs; production should use an OPRF/PSI service."""
        return hmac.new(
            self._epoch_key, f"{kind}:{raw_id}".encode(), hashlib.sha256
        ).hexdigest()[:32]

    # -- membership --------------------------------------------------
    def enroll(self, bank: str) -> "BankClient":
        sk = Ed25519PrivateKey.generate()
        self._pub[bank] = sk.public_key()
        return BankClient(bank, sk, self)

    # -- chain -------------------------------------------------------
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS

    def submit(self, e: Entry) -> Entry:
        with self.lock:
            pub = self._pub.get(e.bank)
            if pub is None:
                raise LedgerError("unknown bank")
            if e.op == "revoke_all" and e.bank != GOVERNANCE:
                raise LedgerError("revoke_all is governance-only")
            if e.op == "publish" and e.bank == GOVERNANCE:
                raise LedgerError("governance cannot publish signals")
            if e.idx != len(self.entries) or e.prev != self.head():
                raise LedgerError("stale head")
            if self.entries and e.ts < self.entries[-1].ts:
                raise LedgerError("non-monotonic timestamp")
            try:
                pub.verify(bytes.fromhex(e.sig), e.body())
            except (InvalidSignature, ValueError):
                raise LedgerError("bad signature")
            if e.op == "publish":
                q = self._recent[e.bank]
                while q and q[0] < e.ts - 3600:
                    q.popleft()
                if len(q) >= self.rate_limit:
                    raise LedgerError("rate limited")
                q.append(e.ts)
            e.hash = e.digest()
            self.entries.append(e)
            self._by_key[(e.kind, e.key)].append(e)
            return e

    def verify_chain(self) -> tuple[bool, int | None]:
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.idx != i or e.prev != prev:
                return False, i
            try:
                if e.hash != e.digest():
                    return False, i
                self._pub[e.bank].verify(bytes.fromhex(e.sig), e.body())
            except (InvalidSignature, KeyError, ValueError):
                return False, i
            prev = e.hash
        return True, None

    # -- queries -----------------------------------------------------
    def status(self, raw_id: str, kind: str, now: float) -> tuple[int, list[str]]:
        """Return (tier, active_banks). tier: 0 none, 1 soft, 2 hard (corroborated)."""
        exp: dict[str, float] = {}
        for e in self._by_key.get((kind, self.blind(raw_id, kind)), ()):
            if e.ts > now:
                break
            if e.op == "publish":
                exp[e.bank] = e.ts + e.ttl
            elif e.op == "revoke":
                exp.pop(e.bank, None)
            elif e.op == "revoke_all":
                exp.clear()
        banks = sorted(b for b, x in exp.items() if x > now)
        if not banks:
            return 0, []
        return (HARD if len(banks) >= self.hard_banks else SOFT), banks

    def tier(self, raw_id: str, kind: str, now: float) -> int:
        return self.status(raw_id, kind, now)[0]


class BankClient:
    """A participant's signing handle."""

    def __init__(self, bank: str, sk: Ed25519PrivateKey, ledger: Ledger):
        self.bank, self._sk, self._ledger = bank, sk, ledger

    def _emit(self, op: str, raw_id: str, kind: str, ttl: float, now: float) -> Entry:
        led = self._ledger
        with led.lock:
            e = Entry(
                idx=len(led.entries),
                ts=now,
                bank=self.bank,
                op=op,
                kind=kind,
                key=led.blind(raw_id, kind),
                ttl=ttl,
                prev=led.head(),
            )
            e.sig = self._sk.sign(e.body()).hex()
            return led.submit(e)

    def publish(self, raw_id: str, kind: str = "acct", ttl: float = 7 * DAY, now: float = 0.0) -> Entry:
        return self._emit("publish", raw_id, kind, ttl, now)

    def revoke(self, raw_id: str, kind: str = "acct", now: float = 0.0) -> Entry:
        return self._emit("revoke", raw_id, kind, 0.0, now)

    def revoke_all(self, raw_id: str, kind: str = "acct", now: float = 0.0) -> Entry:
        return self._emit("revoke_all", raw_id, kind, 0.0, now)
