import { useState } from "react";

export default function LedgerView({ client, actor }) {
  const [pub, setPub] = useState({ raw_id: "acc-4471", kind: "acct", ttl_days: 7 });
  const [lookup, setLookup] = useState({ raw_id: "acc-4471", kind: "acct" });
  const [status, setStatus] = useState(null);
  const [verify, setVerify] = useState(null);
  const [appeal, setAppeal] = useState({ raw_id: "acc-4471", kind: "acct", reason: "KYC re-verified" });
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  async function run(fn, okMsg) {
    setErr(null);
    setMsg(null);
    try {
      const r = await fn();
      setMsg(okMsg);
      return r;
    } catch (ex) {
      setErr(ex.message);
    }
  }

  return (
    <>
      <h1 className="page-title">Shared ledger</h1>
      <p className="page-sub">Acting as <b style={{ color: "var(--text)" }}>{actor}</b>. Publish and revoke only affect this bank's own signal.</p>

      {err && <div className="error-banner">{err}</div>}
      {msg && !err && <div className="hint">{msg}</div>}

      <section className="section">
        <h2 className="section-label">Publish or revoke a signal</h2>
        <div className="form-grid">
          <div className="field">
            <label>Account or device ID</label>
            <input value={pub.raw_id} onChange={(e) => setPub({ ...pub, raw_id: e.target.value })} />
          </div>
          <div className="field">
            <label>Kind</label>
            <select value={pub.kind} onChange={(e) => setPub({ ...pub, kind: e.target.value })}>
              <option value="acct">Account</option>
              <option value="device">Device</option>
            </select>
          </div>
          <div className="field">
            <label>TTL (days)</label>
            <input type="number" min="1" max="30" value={pub.ttl_days}
                   onChange={(e) => setPub({ ...pub, ttl_days: e.target.value })} />
          </div>
          <button className="btn" onClick={() =>
            run(() => client.publish(pub.raw_id, pub.kind, Number(pub.ttl_days) * 86400), "Signal published.")
          }>Publish</button>
          <button className="btn ghost" onClick={() =>
            run(() => client.revoke(pub.raw_id, pub.kind), "Signal revoked.")
          }>Revoke</button>
        </div>
      </section>

      <section className="section">
        <h2 className="section-label">Look up a signal</h2>
        <div className="form-grid">
          <div className="field">
            <label>Account or device ID</label>
            <input value={lookup.raw_id} onChange={(e) => setLookup({ ...lookup, raw_id: e.target.value })} />
          </div>
          <div className="field">
            <label>Kind</label>
            <select value={lookup.kind} onChange={(e) => setLookup({ ...lookup, kind: e.target.value })}>
              <option value="acct">Account</option>
              <option value="device">Device</option>
            </select>
          </div>
          <button className="btn ghost" onClick={async () => {
            const r = await run(() => client.status(lookup.raw_id, lookup.kind));
            if (r) setStatus(r);
          }}>Check status</button>
        </div>
        {status && (
          <div className="ledger-status">
            <span className={`tier-${status.tier}`}>
              tier {status.tier} ({status.tier === 2 ? "corroborated / HOLD-eligible" : status.tier === 1 ? "flagged / STEP_UP" : "clear"})
            </span>
            <span>{status.reporting_banks} bank(s) reporting</span>
          </div>
        )}
      </section>

      <section className="section">
        <h2 className="section-label">Chain integrity</h2>
        <button className="btn ghost" onClick={async () => {
          const r = await run(() => client.verify());
          if (r) setVerify(r);
        }}>Verify hash chain</button>
        {verify && (
          <div className="ledger-status">
            <span className={verify.ok ? "tier-0" : "tier-2"}>{verify.ok ? "chain intact" : "tamper detected"}</span>
            <span>{verify.entries} entries</span>
            <span className="mono" title={verify.head}>head {verify.head?.slice(0, 12)}…</span>
          </div>
        )}
      </section>

      {actor === "REG" && (
        <section className="section">
          <h2 className="section-label">Governance: uphold an appeal</h2>
          <p className="hint" style={{ marginTop: 0 }}>Clears every bank's active signal on this key. Use when a flagged account is confirmed clean.</p>
          <div className="form-grid">
            <div className="field">
              <label>Account or device ID</label>
              <input value={appeal.raw_id} onChange={(e) => setAppeal({ ...appeal, raw_id: e.target.value })} />
            </div>
            <div className="field">
              <label>Kind</label>
              <select value={appeal.kind} onChange={(e) => setAppeal({ ...appeal, kind: e.target.value })}>
                <option value="acct">Account</option>
                <option value="device">Device</option>
              </select>
            </div>
            <div className="field">
              <label>Reason</label>
              <input value={appeal.reason} onChange={(e) => setAppeal({ ...appeal, reason: e.target.value })} />
            </div>
            <button className="btn" onClick={() =>
              run(() => client.appeal(appeal.raw_id, appeal.kind, appeal.reason), "Appeal upheld — signal cleared.")
            }>Uphold appeal</button>
          </div>
        </section>
      )}
    </>
  );
}
