import { useEffect, useState } from "react";
import { get, post, Scenario, TERMINAL } from "./api";
import World from "./World";
import Timeline from "./Timeline";
import Eval from "./Eval";

const GROUPS: [string, string][] = [
  ["s1", "S1 · Recommendation review"],
  ["s2", "S2 · Supplier shortfall"],
  ["s3", "S3 · Demand shift"],
  ["s4", "S4 · Constraints"],
];

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [world, setWorld] = useState<any>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [run, setRun] = useState<any>(null);
  const [tab, setTab] = useState<"console" | "eval">("console");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshWorld = () => get("/api/world").then(setWorld).catch(() => setWorld(null));

  useEffect(() => {
    get("/api/scenarios").then(setScenarios).catch((e) => setError(String(e)));
    refreshWorld();
    get("/api/runs").then((runs) => runs[0] && setRunId(runs[0].id)).catch(() => {});
  }, []);

  useEffect(() => {
    if (runId == null) return setRun(null);
    let alive = true;
    const tick = async () => {
      const r = await get(`/api/runs/${runId}`);
      if (!alive) return;
      setRun(r);
      if (!TERMINAL.includes(r.status)) setTimeout(tick, 1000);
      else refreshWorld();
    };
    tick();
    return () => {
      alive = false;
    };
  }, [runId]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const load = (id: string) =>
    act(async () => {
      setWorld(await post(`/api/scenarios/${id}/load`));
      setRunId(null);
      setTab("console");
    });

  const start = (mode: "agent" | "naive") =>
    act(async () => {
      const { run_id } = await post("/api/runs", { mode });
      setRunId(run_id);
    });

  const answer = (approvalId: number, approved: boolean, note: string) =>
    act(async () => {
      await post(`/api/approvals/${approvalId}`, { approved, note });
    });

  const running = run && !TERMINAL.includes(run.status);

  return (
    <div className="app">
      <aside className="side">
        <h1>Purchasing Agent</h1>
        <p className="muted small">Pick a situation. Each one loads its own world into Postgres.</p>
        {GROUPS.map(([prefix, label]) => (
          <div key={prefix} className="group">
            <div className="group-label">{label}</div>
            {scenarios
              .filter((s) => s.id.startsWith(prefix))
              .map((s) => (
                <button key={s.id} className={`scn ${world?.scenario === s.id ? "active" : ""}`}
                        disabled={busy || running} onClick={() => load(s.id)}>
                  {s.title.replace(/^S\d: /, "")}
                </button>
              ))}
          </div>
        ))}
        <div className="tabs">
          <button className={tab === "console" ? "on" : ""} onClick={() => setTab("console")}>Console</button>
          <button className={tab === "eval" ? "on" : ""} onClick={() => setTab("eval")}>Eval</button>
        </div>
      </aside>

      {tab === "eval" ? (
        <main className="main single"><Eval /></main>
      ) : (
        <main className="main">
          <section>
            {error && <div className="panel bad-panel">{error}</div>}
            {world ? <World world={world} scenario={scenarios.find((s) => s.id === world.scenario)} />
                   : <div className="panel muted">Pick a scenario to load its world.</div>}
          </section>
          <section>
            <div className="panel controls">
              <button className="primary" disabled={!world || busy || running} onClick={() => start("agent")}>
                {running ? "Agent running…" : "Run agent"}
              </button>
              <button className="ghost" disabled={!world || busy || running} onClick={() => start("naive")}
                      title="Executes the upstream recommendation without investigation or checks">
                Run naive baseline
              </button>
              {world && <span className="muted small">Re-load the scenario to reset its world.</span>}
            </div>
            {run ? <Timeline run={run} onAnswer={answer} busy={busy} />
                 : <div className="panel muted">No run yet for this world.</div>}
          </section>
        </main>
      )}
    </div>
  );
}
