const DEFAULT_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export class ApiError extends Error {}

async function call(base, key, path, opts = {}) {
  let res;
  try {
    res = await fetch(base + path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-Bank-Key": key, ...(opts.headers || {}) },
    });
  } catch {
    throw new ApiError(`Can't reach ${base}. Is the API running (uvicorn mulenet.api:app)?`);
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* no body */
  }
  if (!res.ok) throw new ApiError(body?.detail || `Request failed (${res.status})`);
  return body;
}

export function makeClient(base = DEFAULT_BASE, key) {
  return {
    base,
    healthz: () => call(base, key, "/healthz"),
    score: (txn) => call(base, key, "/v1/score", { method: "POST", body: JSON.stringify(txn) }),
    cases: () => call(base, key, "/v1/cases"),
    publish: (raw_id, kind, ttl_s) =>
      call(base, key, "/v1/ledger/publish", { method: "POST", body: JSON.stringify({ raw_id, kind, ttl_s }) }),
    revoke: (raw_id, kind) =>
      call(base, key, "/v1/ledger/revoke", { method: "POST", body: JSON.stringify({ raw_id, kind }) }),
    status: (raw_id, kind) => call(base, key, `/v1/ledger/status?raw_id=${encodeURIComponent(raw_id)}&kind=${kind}`),
    verify: () => call(base, key, "/v1/ledger/verify"),
    appeal: (raw_id, kind, reason) =>
      call(base, key, "/v1/appeals", { method: "POST", body: JSON.stringify({ raw_id, kind, reason }) }),
  };
}

export const BANKS = ["BANK_A", "BANK_B", "BANK_C", "BANK_D"];
export const ACTORS = [...BANKS, "REG"];
export const DEFAULT_KEY = import.meta.env.VITE_BANK_KEY || "demo-key-BANK_C";
