import { useEffect, useRef, useState } from "react";

export default function CasesView({ client }) {
  const [cases, setCases] = useState([]);
  const [err, setErr] = useState(null);
  const seen = useRef(new Set());

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const r = await client.cases();
        if (alive) {
          setCases(r.cases);
          setErr(null);
        }
      } catch (ex) {
        if (alive) setErr(ex.message);
      }
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [client]);

  return (
    <>
      <h1 className="page-title">Case queue</h1>
      <p className="page-sub">Every payment scored STEP_UP or HOLD, newest first. Refreshes every 4s.</p>

      {err && <div className="error-banner">{err}</div>}

      {cases.length === 0 && !err ? (
        <div className="empty">No open cases. Score a suspicious-looking payment to see one land here.</div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Decision</th>
              <th className="mono">Score</th>
              <th className="mono">Remitter → beneficiary</th>
              <th className="mono">Amount</th>
              <th>Top reason</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => {
              const key = `${c.ts}-${c.dst}`;
              const isNew = !seen.current.has(key);
              seen.current.add(key);
              return (
                <tr key={key} className={isNew ? "enter" : ""}>
                  <td><span className={`badge ${c.decision}`}>{c.decision.replace("_", " ")}</span></td>
                  <td className="mono">{c.risk_score.toFixed(3)}</td>
                  <td className="mono">{c.src} → {c.dst}</td>
                  <td className="mono">₹{Number(c.amount).toLocaleString("en-IN")}</td>
                  <td>{c.reasons?.[0] || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
