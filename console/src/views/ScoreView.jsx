import { useState } from "react";
import { BANKS } from "../api.js";

const initial = {
  src: "v001",
  dst: "acc-4471",
  amount: 4200,
  src_bank: "BANK_A",
  dst_bank: "BANK_B",
  src_dev: "dev-882",
  src_age: 640,
  dst_age: 12,
};

export default function ScoreView({ client }) {
  const [form, setForm] = useState(initial);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  function set(k, v) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await client.score({ ...form, amount: Number(form.amount), src_age: Number(form.src_age), dst_age: Number(form.dst_age) });
      setResult(r);
    } catch (ex) {
      setErr(ex.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="page-title">Score a payment</h1>
      <p className="page-sub">Run a single transaction through the live model and shared ledger.</p>

      <form className="section" onSubmit={submit}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="src">Remitter account</label>
            <input id="src" value={form.src} onChange={(e) => set("src", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="dst">Beneficiary account</label>
            <input id="dst" value={form.dst} onChange={(e) => set("dst", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="amount">Amount (INR)</label>
            <input id="amount" type="number" min="0.01" step="0.01" value={form.amount}
                   onChange={(e) => set("amount", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="src_bank">Remitter bank</label>
            <select id="src_bank" value={form.src_bank} onChange={(e) => set("src_bank", e.target.value)}>
              {BANKS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="dst_bank">Beneficiary bank</label>
            <select id="dst_bank" value={form.dst_bank} onChange={(e) => set("dst_bank", e.target.value)}>
              {BANKS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="src_dev">Remitter device ID</label>
            <input id="src_dev" value={form.src_dev} onChange={(e) => set("src_dev", e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="src_age">Remitter account age (days)</label>
            <input id="src_age" type="number" min="0" value={form.src_age} onChange={(e) => set("src_age", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="dst_age">Beneficiary account age (days)</label>
            <input id="dst_age" type="number" min="0" value={form.dst_age} onChange={(e) => set("dst_age", e.target.value)} />
          </div>
          <button className="btn" disabled={busy}>{busy ? "Scoring…" : "Score payment"}</button>
        </div>
      </form>

      {err && <div className="error-banner">{err}</div>}

      {result && (
        <div className="decision">
          <div className="decision-head">
            <span className={`badge ${result.decision}`}>{result.decision.replace("_", " ")}</span>
            <span className="risk-score">risk score <b>{result.risk_score.toFixed(3)}</b></span>
          </div>
          <div className="meter"><div style={{ width: `${Math.min(result.risk_score * 100, 100)}%` }} /></div>

          <div className="chips">
            <span className={`chip ${result.ledger.beneficiary_tier > 0 ? "lit" : ""}`}>
              ledger · beneficiary {tierLabel(result.ledger.beneficiary_tier)}
            </span>
            <span className={`chip ${result.ledger.remitter_tier > 0 ? "lit" : ""}`}>
              ledger · remitter {tierLabel(result.ledger.remitter_tier)}
            </span>
            <span className={`chip ${result.graph.beneficiary > 0.05 ? "lit" : ""}`}>
              graph · beneficiary {result.graph.beneficiary.toFixed(2)}
            </span>
            <span className={`chip ${result.graph.remitter > 0.05 ? "lit" : ""}`}>
              graph · remitter {result.graph.remitter.toFixed(2)}
            </span>
          </div>

          {result.reasons?.length > 0 && (
            <ul className="reasons">
              {result.reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          )}
          <div className="latency">scored in {result.latency_ms} ms</div>
        </div>
      )}
    </>
  );
}

function tierLabel(t) {
  return t === 2 ? "corroborated" : t === 1 ? "flagged" : "clear";
}
