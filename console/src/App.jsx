import { useEffect, useState } from "react";
import { ACTORS, DEFAULT_KEY, makeClient } from "./api.js";
import ScoreView from "./views/ScoreView.jsx";
import CasesView from "./views/CasesView.jsx";
import LedgerView from "./views/LedgerView.jsx";

const PAGES = [
  { id: "score", label: "Score a payment" },
  { id: "cases", label: "Case queue" },
  { id: "ledger", label: "Shared ledger" },
];

function actorFromKey(key) {
  return key.replace("demo-key-", "");
}

export default function App() {
  const [page, setPage] = useState("score");
  const [key, setKey] = useState(DEFAULT_KEY);
  const [online, setOnline] = useState(null);
  const client = makeClient(undefined, key);

  useEffect(() => {
    let alive = true;
    async function ping() {
      try {
        await client.healthz();
        if (alive) setOnline(true);
      } catch {
        if (alive) setOnline(false);
      }
    }
    ping();
    const id = setInterval(ping, 8000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [client.base, key]);

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          MuleNet
          <small>Cross-bank mule intelligence</small>
        </div>

        <div className="field">
          <label htmlFor="actor">Acting as</label>
          <select id="actor" value={key} onChange={(e) => setKey(e.target.value)}>
            {ACTORS.map((a) => (
              <option key={a} value={`demo-key-${a}`}>{a === "REG" ? "Governance (REG)" : a}</option>
            ))}
          </select>
        </div>

        <nav className="nav">
          {PAGES.map((p) => (
            <button key={p.id} aria-current={page === p.id ? "page" : undefined} onClick={() => setPage(p.id)}>
              {p.label}
            </button>
          ))}
        </nav>

        <div className="rail-status">
          <span><span className={`dot ${online ? "ok" : "bad"}`} />{online === null ? "connecting…" : online ? "API online" : "API unreachable"}</span>
          <span>{client.base}</span>
        </div>
      </aside>

      <main className="main">
        {page === "score" && <ScoreView client={client} />}
        {page === "cases" && <CasesView client={client} />}
        {page === "ledger" && <LedgerView client={client} actor={actorFromKey(key)} />}
      </main>
    </div>
  );
}
