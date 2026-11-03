import { useEffect, useState } from "react";
import { get } from "./api";

const CHECKS = ["decision", "evidence", "constraints", "action", "validated", "recovery"];

function Board({ title, data }: { title: string; data: any }) {
  if (!data) return <div className="panel muted">{title}: no results yet. Run <code>uv run python -m buyer.evaluate</code>.</div>;
  const rs = data.results;
  return (
    <div className="panel">
      <h2>{title} · {rs.filter((r: any) => r.pass).length}/{rs.length} pass every check</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr><th>Scenario</th><th>Verdict</th>{CHECKS.map((c) => <th key={c}>{c}</th>)}<th>Tokens</th></tr>
          </thead>
          <tbody>
            {rs.map((r: any, i: number) => (
              <tr key={i}>
                <td>{r.scenario}</td>
                <td>{r.verdict ?? "n/a"}</td>
                {CHECKS.map((c) => (
                  <td key={c} title={r.checks[c].detail}>
                    {r.checks[c].na ? <span className="chip">n/a</span>
                      : <span className={`chip ${r.checks[c].pass ? "ok" : "bad"}`}>{r.checks[c].pass ? "pass" : "fail"}</span>}
                  </td>
                ))}
                <td className="r">{r.tokens}</td>
              </tr>
            ))}
            <tr>
              <td colSpan={2} className="muted">pass rate</td>
              {CHECKS.map((c) => {
                const applies = rs.filter((r: any) => !r.checks[c].na);
                return <td key={c} className="num">{applies.length ? `${applies.filter((r: any) => r.checks[c].pass).length}/${applies.length}` : "n/a"}</td>;
              })}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <details>
        <summary>Why each check passed or failed</summary>
        {rs.map((r: any, i: number) => (
          <div key={i} className="small eval-detail">
            <b>{r.scenario}</b> {r.summary}
            <ul>{CHECKS.map((c) => <li key={c}>{c}: {r.checks[c].detail}</li>)}</ul>
          </div>
        ))}
      </details>
    </div>
  );
}

export default function Eval() {
  const [agent, setAgent] = useState<any>(null);
  const [naive, setNaive] = useState<any>(null);
  useEffect(() => {
    get("/api/eval/latest").then(setAgent).catch(() => {});
    get("/api/eval/naive").then(setNaive).catch(() => {});
  }, []);
  return (
    <>
      <div className="panel">
        <h2>How runs are graded</h2>
        <p className="small">
          Every scenario ships with an answer key worked out by hand. After a run the grader reads the final
          database state (not the agent's own account) and checks six things: the verdict, whether the required
          facts were looked up, whether budget, storage, MOQ, case pack and shelf life still hold, whether the
          actions taken (and the resulting cover at the true demand) are inside the expected band, whether the
          result was verified, and whether a deviation was recovered from. The naive baseline runs the same
          grader on blind execution of the upstream instruction to show the grader can tell the difference.
        </p>
      </div>
      <Board title="Agent" data={agent} />
      <Board title="Naive baseline" data={naive} />
    </>
  );
}
