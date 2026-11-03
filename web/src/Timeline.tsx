import { useState } from "react";
import { money } from "./api";
import { statusTone } from "./World";

const LABEL: Record<string, string> = {
  situation: "Situation", thought: "Reasoning", tool: "Tool", proposal: "Proposal", validation: "Policy gate",
  approval: "Buyer approval", action: "Execution", verification: "Verification", replan: "Re-plan", final: "Done",
};

function Json({ value }: { value: unknown }) {
  return (
    <details>
      <summary>raw</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function Actions({ actions }: { actions: any[] }) {
  if (!actions?.length) return <div className="muted small">No actions.</div>;
  return (
    <ul className="actions">
      {actions.map((a, i) => (
        <li key={i}>
          <span className="chip">{a.type}</span>{" "}
          {a.supplier_id && <>{a.supplier_id} </>}
          {a.po_id && <>{a.po_id} </>}
          {a.from_node && <>from {a.from_node} </>}
          {a.qty != null && <span className="num">× {a.qty} </span>}
          <span className="muted">{a.reason}</span>
          {a.question && <div className="small">Question: {a.question}</div>}
        </li>
      ))}
    </ul>
  );
}

function Step({ step }: { step: any }) {
  const p = step.payload ?? {};
  const bad = (step.kind === "validation" && step.title === "rejected") ||
              (step.kind === "verification" && step.title !== "as expected");
  let body: React.ReactNode = null;
  switch (step.kind) {
    case "thought":
      body = <p className="thought">{p.text}</p>;
      break;
    case "tool":
      body = (
        <>
          <span className="num small muted">{Object.values(p.args ?? {}).map((v) => JSON.stringify(v)).join(", ")}</span>
          <Json value={p.result} />
        </>
      );
      break;
    case "proposal":
      body = (
        <>
          <p>{p.summary}</p>
          {p.key_factors?.length > 0 && <ul className="factors">{p.key_factors.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul>}
          <Actions actions={p.actions} />
          {p.demand_per_day != null && <div className="small muted">Demand basis {p.demand_per_day}/day. {p.demand_basis_reason}</div>}
        </>
      );
      break;
    case "validation":
      body = (
        <>
          {p.errors?.map((e: string, i: number) => <div key={i} className="bad-text">✕ {e}</div>)}
          {p.warnings?.map((w: string, i: number) => <div key={i} className="warn-text">! {w}</div>)}
          {p.plan && (
            <div className="small muted num">
              plan: covers to day {p.plan.stockout_after_first_possible_arrival} (horizon {p.plan.horizon_days}) · peak {p.plan.peak_stock}/{p.plan.capacity} · spend {money(p.spend)}
              {p.needs_approval ? " · needs buyer approval" : ""}
            </div>
          )}
        </>
      );
      break;
    case "action":
      body = (
        <ul className="actions">
          {p.results?.map((r: any, i: number) => (
            <li key={i}>
              <span className={`chip ${r.ok ? "ok" : "bad"}`}>{r.action.type}</span>{" "}
              {r.ok ? (r.po_id ?? r.transfer_id ?? `task ${r.task_id}`) : r.error}
              {r.supplier && <span className="muted"> · supplier: {r.supplier.message}</span>}
            </li>
          ))}
        </ul>
      );
      break;
    case "verification":
      body = (
        <>
          {p.deviations?.map((d: string, i: number) => <div key={i} className="bad-text">✕ {d}</div>)}
          <div className="small muted num">
            expected cover to day {p.expected?.stockout_after_first_possible_arrival}, actual {p.actual?.stockout_after_first_possible_arrival} (horizon {p.actual?.horizon_days})
            {p.human_review ? " · gap accepted, a person is reviewing" : ""}
          </div>
        </>
      );
      break;
    case "replan":
      body = <div className="small">The deviation goes back to the agent as an observation.</div>;
      break;
    case "approval":
      body = p.reasons ? <ul className="factors">{p.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
                       : p.note ? <div className="small muted">{p.note}</div> : null;
      break;
    case "final":
      body = p.error ? <div className="bad-text">{p.error}</div> : p.outcome?.reason ? <div className="small">{p.outcome.reason}</div> : null;
      break;
  }
  return (
    <div className={`step ${step.kind} ${bad ? "bad" : ""}`}>
      <div className="step-head">
        <span className="kind">{LABEL[step.kind] ?? step.kind}</span>
        <span className="step-title">{step.kind === "final" ? <span className={`chip ${statusTone(step.title)}`}>{step.title}</span> : step.title}</span>
      </div>
      {body}
    </div>
  );
}

function ApprovalCard({ approval, onAnswer, busy }: { approval: any; onAnswer: (id: number, ok: boolean, note: string) => void; busy: boolean }) {
  const [note, setNote] = useState("");
  return (
    <div className="panel approval">
      <h2>Waiting for you</h2>
      <p>The policy gate let this plan through but wants a buyer to sign off:</p>
      <ul className="factors">{approval.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
      <Actions actions={approval.proposal.actions} />
      <label className="small muted" htmlFor="note">Note to the agent (used if you reject)</label>
      <input id="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. use the cheaper supplier" />
      <div className="row">
        <button className="primary" disabled={busy} onClick={() => onAnswer(approval.id, true, note)}>Approve and execute</button>
        <button className="ghost" disabled={busy} onClick={() => onAnswer(approval.id, false, note)}>Reject, send back</button>
      </div>
    </div>
  );
}

export default function Timeline({ run, onAnswer, busy }: { run: any; onAnswer: (id: number, ok: boolean, note: string) => void; busy: boolean }) {
  const pending = run.approvals.find((a: any) => a.status === "pending");
  const tools = run.steps.filter((s: any) => s.kind === "tool").length;
  const d = run.decision;
  return (
    <>
      <div className="panel">
        <h2>Run #{run.id} · {run.mode}{run.model ? ` · ${run.model}` : ""}</h2>
        <div className="row">
          <span className={`chip ${statusTone(run.status)}`}>{run.status}</span>
          <span className="small muted num">{tools} tool calls · {run.tokens} tokens</span>
        </div>
        {d && (
          <div className="decision">
            <span className={`verdict v-${d.verdict}`}>{d.verdict}</span> {d.summary}
          </div>
        )}
        {run.error && <div className="bad-text">{run.error}</div>}
      </div>
      {pending && <ApprovalCard approval={pending} onAnswer={onAnswer} busy={busy} />}
      <div className="panel">
        <h2>Trace</h2>
        {run.steps.filter((s: any) => s.kind !== "situation").map((s: any) => <Step key={s.id} step={s} />)}
      </div>
    </>
  );
}
