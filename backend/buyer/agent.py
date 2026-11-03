"""The agent loop.

The model investigates through read only tools and proposes one decision.
Everything after that is code: the policy gate, the approval rule, the
executor and the verifier. When the verified outcome differs from the plan,
the difference goes back to the model as an observation and it re-plans,
within a fixed budget of turns, rejected proposals and recoveries.
"""
import json
import os

import litellm

from buyer import db
from buyer.actions import execute, verify
from buyer.policy import validate
from buyer.tools import TOOL_SPECS, call_tool, render

MODEL = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-5-5")
MAX_TURNS = 30
MAX_REJECTED = 3
MAX_RECOVERIES = 2

SYSTEM = """You are the purchasing agent for a quick commerce network of dark stores. You handle one situation for one SKU at one node.

How you work:
1. Investigate before deciding. Gather the facts the decision depends on: stock and storage (get_item_snapshot), demand and its drivers (get_demand), open purchase orders and supplier messages (get_open_purchase_orders), supplier terms (get_supplier_options), budget (get_budget), stock at other nodes (get_network_stock) and live supplier availability (check_supplier_availability).
2. Never do inventory arithmetic in your head. Use project_inventory on the current plan and on every candidate plan.
3. Treat the recommendation or the current plan as a hypothesis. It may ignore open orders, constraints or data problems.
4. Choose the demand basis on purpose. Compare forecast with recent sales and find the cause of any gap (promotion, weather, event, stock problem). Do not chase a spike whose cause has ended; do follow a sustained change whose cause is still live.
5. Distrust the stock record when the evidence says so (no cycle count for a long time, zero sales while the system shows stock). Investigate instead of buying or rejecting on bad data.
6. Prefer the cheapest plan that keeps the store covered through the coverage horizon: surplus stock at another node first, then the cheaper supplier if its lead time still lands in time. Respect MOQ, case packs, budget, storage and shelf life. Expiring stock is a cost too.
7. When no plan satisfies the constraints, do the safe part and escalate the rest with a concrete question for the buyer.

Deciding: call propose_decision once you have the evidence. You cannot write to any system yourself. A deterministic policy gate checks the proposal and returns errors to fix. Valid proposals are executed (some after buyer approval) and verified against the database and the supplier's confirmation, and the result comes back to you. If the outcome differs from the plan, look at the new state and propose a recovery decision with only the additional actions needed; executed actions stay executed.

Verdicts: accept (the recommendation or current plan stands as is), modify (change it), reject (do not buy), investigate (data must be checked first), escalate (a human must decide). Keep key_factors short and concrete, with numbers."""


def brief(sit: dict) -> str:
    return (f"Today is {db.today().isoformat()}.\nSituation {sit['id']} ({sit['kind']}): {sit['title']}\n"
            f"{sit['description']}\nNode: {sit['node_id']}  SKU: {sit['sku']}  Reference: {sit['ref_id']}")


def save(run_id: int, state: dict, status: str, **fields) -> None:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    db.run(f"UPDATE agent_runs SET state = %(state)s, status = %(status)s{', ' + sets if sets else ''} "
           "WHERE id = %(id)s", {"state": db.dumps(state), "status": status, "id": run_id,
                                 **{k: db.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}})


def finish(run_id: int, state: dict, status: str, outcome: dict | None = None, error: str | None = None) -> None:
    save(run_id, state, status, decision=state.get("decision"), outcome=outcome, error=error)
    db.run("UPDATE agent_runs SET finished_at = now() WHERE id = %s", (run_id,))
    db.log_step(run_id, "final", status, {"outcome": outcome, "error": error})


def situation_of(run_id: int) -> dict:
    return db.one("SELECT s.* FROM situations s JOIN agent_runs r ON r.situation_id = s.id WHERE r.id = %s",
                  (run_id,))


def start(situation_id: str, mode: str = "agent") -> int:
    sit = db.one("SELECT * FROM situations WHERE id = %s", (situation_id,))
    run_id = db.one("INSERT INTO agent_runs (situation_id, mode, model, status) VALUES (%s, %s, %s, 'running') "
                    "RETURNING id", (situation_id, mode, MODEL if mode == "agent" else None))["id"]
    db.log_step(run_id, "situation", sit["title"], sit)
    state = {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief(sit)}],
             "tools_called": [], "rejected": 0, "recoveries": 0, "turns": 0}
    save(run_id, state, "running")
    return run_id


def run(run_id: int) -> None:
    state = db.one("SELECT state FROM agent_runs WHERE id = %s", (run_id,))["state"]
    try:
        loop(run_id, state)
    except Exception as e:  # a crashed run must still end in a terminal state
        finish(run_id, state, "failed", error=f"{type(e).__name__}: {e}")


def llm(run_id: int, messages: list[dict]) -> dict:
    resp = litellm.completion(model=MODEL, messages=messages, tools=TOOL_SPECS, max_tokens=4096,
                              num_retries=2, timeout=180)
    db.run("UPDATE agent_runs SET tokens = tokens + %s WHERE id = %s",
           (getattr(resp, "usage", None) and resp.usage.total_tokens or 0, run_id))
    m = resp.choices[0].message
    out = {"role": "assistant", "content": m.content or None}
    if m.tool_calls:
        out["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                             for c in m.tool_calls]
    return out


def reply(state: dict, call_id: str, payload) -> None:
    state["messages"].append({"role": "tool", "tool_call_id": call_id, "content": render(payload)})


def loop(run_id: int, state: dict) -> None:
    sit = situation_of(run_id)
    while True:
        state["turns"] += 1
        if state["turns"] > MAX_TURNS:
            return give_up(run_id, state, sit, "turn budget exhausted")
        msg = llm(run_id, state["messages"])
        state["messages"].append(msg)
        if msg["content"]:
            db.log_step(run_id, "thought", "model", {"text": msg["content"]})
        calls = msg.get("tool_calls") or []
        if not calls:
            state["messages"].append({"role": "user", "content": "Continue with the tools and finish by calling "
                                                                 "propose_decision."})
            continue
        proposals = [c for c in calls if c["function"]["name"] == "propose_decision"]
        for c in calls:
            if c in proposals:
                continue
            name, args = c["function"]["name"], parse(c)
            result = call_tool(name, args)
            state["tools_called"].append(name)
            db.log_step(run_id, "tool", name, {"args": args, "result": result})
            reply(state, c["id"], result)
        for extra in proposals[:-1]:
            reply(state, extra["id"], {"status": "ignored", "error": "One decision per turn."})
        if proposals and propose(run_id, state, sit, proposals[-1]) == "stop":
            return
        save(run_id, state, "running")


def parse(call: dict) -> dict:
    try:
        return json.loads(call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {}


def propose(run_id: int, state: dict, sit: dict, call: dict) -> str:
    decision = parse(call)
    report = validate(decision, sit, set(state["tools_called"]))
    db.log_step(run_id, "proposal", f"{decision.get('verdict')}: {decision.get('summary', '')}", decision)
    db.log_step(run_id, "validation", "passed" if report["ok"] else "rejected", report)
    if not report["ok"]:
        state["rejected"] += 1
        if state["rejected"] > MAX_REJECTED:
            reply(state, call["id"], {"status": "rejected_by_policy", "errors": report["errors"]})
            give_up(run_id, state, sit, "policy gate rejected too many proposals: " + "; ".join(report["errors"]))
            return "stop"
        reply(state, call["id"], {"status": "rejected_by_policy", "errors": report["errors"],
                                  "warnings": report["warnings"],
                                  "instruction": "Fix every error and call propose_decision again, or escalate."})
        return "continue"
    state.update(decision=decision, report=report, pending_call=call["id"])
    if report["needs_approval"]:
        db.run("INSERT INTO approvals (run_id, proposal, reasons) VALUES (%s, %s, %s)",
               (run_id, db.dumps(decision), db.dumps(report["approval_reasons"])))
        db.log_step(run_id, "approval", "waiting for buyer", {"reasons": report["approval_reasons"]})
        save(run_id, state, "awaiting_approval", decision=decision)
        return "stop"
    return act(run_id, state, sit)


def act(run_id: int, state: dict, sit: dict) -> str:
    decision, report = state["decision"], state["report"]
    results = execute(run_id, decision, sit)
    if results:
        db.log_step(run_id, "action", f"{len(results)} action(s) executed", {"results": results})
    check = verify(decision, results, sit, report["plan"])
    db.log_step(run_id, "verification", "as expected" if check["ok"] else "deviation", check)
    if check["ok"]:
        reply(state, state.pop("pending_call"), {"status": "executed", "results": results, "verification": check})
        escalated = any(a["type"] == "escalate" for a in decision.get("actions") or [])
        finish(run_id, state, "escalated" if escalated else "completed", {"results": results, "verification": check})
        return "stop"
    state["recoveries"] += 1
    if state["recoveries"] > MAX_RECOVERIES:
        reply(state, state.pop("pending_call"), {"status": "executed_with_deviations", "verification": check})
        give_up(run_id, state, sit, "outcome still off plan after recoveries: " + "; ".join(check["deviations"]))
        return "stop"
    db.log_step(run_id, "replan", "outcome differs from plan, asking the agent to recover", check["deviations"])
    reply(state, state.pop("pending_call"), {
        "status": "executed_with_deviations", "results": results, "deviations": check["deviations"],
        "current_projection": check["actual"],
        "instruction": "The outcome differs from the plan. Check the current state and propose a recovery "
                       "decision with only the additional actions needed, or escalate."})
    return "continue"


def give_up(run_id: int, state: dict, sit: dict, why: str) -> None:
    db.run("INSERT INTO tasks (run_id, kind, node_id, sku, detail) VALUES (%s, 'escalation', %s, %s, %s)",
           (run_id, sit["node_id"], sit["sku"], f"Agent stopped: {why}"))
    finish(run_id, state, "escalated", {"reason": why})


def decide_approval(approval_id: int, approved: bool, note: str = "") -> int:
    """Buyer answers an approval. The status guard makes a double click a no op."""
    row = db.one("UPDATE approvals SET status = %s, note = %s, decided_at = now() "
                 "WHERE id = %s AND status = 'pending' RETURNING run_id",
                 ("approved" if approved else "rejected", note, approval_id))
    if row is None:
        raise ValueError(f"approval {approval_id} is not pending")
    run_id = row["run_id"]
    state = db.one("SELECT state FROM agent_runs WHERE id = %s", (run_id,))["state"]
    sit = situation_of(run_id)
    db.log_step(run_id, "approval", "approved" if approved else "rejected by buyer", {"note": note})
    try:
        if approved:
            if act(run_id, state, sit) == "stop":
                return run_id
        else:
            state["rejected"] += 1
            reply(state, state.pop("pending_call"), {"status": "rejected_by_buyer", "note": note,
                                                     "instruction": "Revise the decision with the buyer's note "
                                                                    "in mind, or escalate."})
        save(run_id, state, "running")
        loop(run_id, state)
    except Exception as e:
        finish(run_id, state, "failed", error=f"{type(e).__name__}: {e}")
    return run_id


def run_naive(situation_id: str) -> int:
    """Baseline for the eval: do what the upstream system says, no checks.
    Recommendation: buy it. Short supply: reorder the gap from the same
    supplier. Demand change: keep the plan."""
    run_id = start(situation_id, mode="naive")
    sit = situation_of(run_id)
    actions = []
    rec = db.one("SELECT * FROM recommendations WHERE id = %s", (sit["ref_id"],))
    po = db.one("SELECT * FROM purchase_orders WHERE id = %s", (sit["ref_id"],))
    if rec:
        actions = [{"type": "create_po", "supplier_id": rec["supplier_id"], "qty": rec["qty"], "reason": "as recommended"}]
    elif sit["kind"] == "supplier_shortfall" and po:
        actions = [{"type": "create_po", "supplier_id": po["supplier_id"], "qty": po["qty"] - po["qty_confirmed"],
                    "reason": "reorder the shortfall"}]
    forecast = db.one("SELECT units_per_day FROM forecast WHERE node_id = %s AND sku = %s",
                      (sit["node_id"], sit["sku"]))["units_per_day"]
    decision = {"verdict": "accept", "summary": "Executed upstream instruction.", "key_factors": [],
                "demand_per_day": forecast, "actions": actions, "expected": {}}
    state = {"decision": decision, "tools_called": []}
    db.log_step(run_id, "proposal", "accept: executed upstream instruction", decision)
    results = execute(run_id, decision, sit)
    db.log_step(run_id, "action", f"{len(results)} action(s) executed", {"results": results})
    finish(run_id, state, "completed", {"results": results})
    return run_id

