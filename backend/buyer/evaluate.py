"""Scenario eval. Each scenario is loaded fresh, run end to end (a simulated
buyer approves anything the agent sends for approval), then graded against
the hand computed expectations in its fixture. The grader reads the final
database state, not the agent's own account of what it did.

    uv run python -m buyer.evaluate                 # agent, every scenario
    uv run python -m buyer.evaluate --mode naive    # blind execution baseline
    uv run python -m buyer.evaluate --only s2_shortfall_transfer --repeat 3
"""
import argparse
import json
import time
from pathlib import Path

from buyer import agent, seed
from buyer.db import one, rows
from buyer.domain import cover_summary, item_context

OUT = Path(__file__).resolve().parents[2] / "eval" / "results"
CHECKS = ["decision", "evidence", "constraints", "action", "validated", "recovery"]


def run_scenario(sid: str, mode: str) -> int:
    sit = seed.load(sid)["situation"]
    if mode == "naive":
        return agent.run_naive(sit["id"])
    run_id = agent.start(sit["id"])
    agent.run(run_id)
    for _ in range(5):
        pending = one("SELECT id FROM approvals WHERE run_id = %s AND status = 'pending'", (run_id,))
        if pending is None:
            break
        agent.decide_approval(pending["id"], True, "simulated buyer: approved")
    return run_id


def verdict(ok: bool, detail: str = "") -> dict:
    return {"pass": ok, "detail": detail}


def grade(sc: dict, run_id: int) -> dict:
    exp, sit = sc["expect"], sc["situation"]
    node, sku = sit["node_id"], sit["sku"]
    run = one("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
    decision = run["decision"] or {}
    steps = rows("SELECT kind, title, payload FROM agent_steps WHERE run_id = %s ORDER BY id", (run_id,))
    tools = {s["title"] for s in steps if s["kind"] == "tool"}
    pos = rows("SELECT * FROM purchase_orders WHERE run_id = %s", (run_id,))
    live = [p for p in pos if p["status"] not in ("rejected", "cancelled")]
    transfers = rows("SELECT * FROM transfers WHERE run_id = %s", (run_id,))
    tasks = rows("SELECT * FROM tasks WHERE run_id = %s", (run_id,))
    approvals = rows("SELECT * FROM approvals WHERE run_id = %s", (run_id,))
    executed = [r["action"]["type"] for s in steps if s["kind"] == "action"
                for r in s["payload"]["results"] if r["ok"]]
    taken = set(executed) | {"escalate" for t in tasks if t["kind"] == "escalation"}
    out = {}

    want = exp.get("verdicts")
    out["decision"] = (verdict(True, "not graded") if want is None else
                       verdict(decision.get("verdict") in want, f"{decision.get('verdict')} (want {'/'.join(want)})"))

    missing = sorted(set(exp.get("required_tools", [])) - tools)
    out["evidence"] = verdict(not missing, f"missing {', '.join(missing)}" if missing else f"{len(tools)} tools used")

    item = item_context(node, sku)
    true_demand = exp.get("true_daily_demand", item["forecast"])
    final = cover_summary(item, true_demand)
    problems = []
    for b in rows("SELECT * FROM budget_status"):
        if b["remaining"] < 0:
            problems.append(f"budget {b['category']} overrun by {-b['remaining']:.2f}")
    if final["peak_stock"] > item["capacity"]:
        problems.append(f"storage peak {final['peak_stock']} > {item['capacity']}")
    for p in live:
        si = one("SELECT moq, case_pack FROM supplier_items WHERE supplier_id = %s AND sku = %s", (p["supplier_id"], sku))
        if p["qty"] < si["moq"] or p["qty"] % si["case_pack"]:
            problems.append(f"{p['id']} breaks MOQ/case pack")
    if final["expected_waste"] and (live or transfers):
        problems.append(f"about {final['expected_waste']} units expire")
    spend = sum((p["qty_confirmed"] if p["qty_confirmed"] is not None else p["qty"]) * p["unit_price"] for p in live)
    po_exp = exp.get("po", {})
    if "max_spend" in po_exp and spend > po_exp["max_spend"]:
        problems.append(f"spend {spend:.2f} > {po_exp['max_spend']}")
    out["constraints"] = verdict(not problems, "; ".join(problems) or "budget, storage, MOQ, shelf life ok")

    problems = []
    for group in exp.get("actions_required_any", []):
        if not taken & set(group):
            problems.append(f"needed one of {group}")
    for bad in exp.get("actions_forbidden", []):
        if bad in taken:
            problems.append(f"took forbidden {bad}")
    if live and "suppliers" in po_exp and any(p["supplier_id"] not in po_exp["suppliers"] for p in live):
        problems.append(f"supplier {[p['supplier_id'] for p in live]} not in {po_exp['suppliers']}")
    qty = sum(p["qty"] for p in live)
    if live and not po_exp.get("min_qty", 0) <= qty <= po_exp.get("max_qty", 10 ** 9):
        problems.append(f"ordered {qty}, want {po_exp.get('min_qty', 0)} to {po_exp.get('max_qty', 'any')}")
    if "min_demand_basis" in exp and (decision.get("demand_per_day") or 0) < exp["min_demand_basis"]:
        problems.append(f"demand basis {decision.get('demand_per_day')} < {exp['min_demand_basis']}")
    text = " ".join([decision.get("summary", "")] + decision.get("key_factors", [])).lower()
    for word in exp.get("must_mention", []):
        if word.lower() not in text:
            problems.append(f"did not mention {word}")
    cover = final["stockout_after_first_possible_arrival"]
    escalated = "escalate" in taken
    if "min_cover_days" in exp and cover < exp["min_cover_days"] and not (exp.get("cover_or_escalate") and escalated):
        problems.append(f"final cover {cover}d < {exp['min_cover_days']}d at true demand {true_demand}")
    if "max_cover_days" in exp and (live or transfers) and exp["max_cover_days"] < cover < 90:
        problems.append(f"final cover {cover}d > {exp['max_cover_days']}d")
    if "approval_required" in exp and bool(approvals) != exp["approval_required"]:
        problems.append(f"approval {'asked' if approvals else 'not asked'}, want "
                        f"{'asked' if exp['approval_required'] else 'not asked'}")
    out["action"] = verdict(not problems, "; ".join(problems) or f"took {sorted(taken) or 'no action'}")

    checks = [s for s in steps if s["kind"] == "verification"]
    out["validated"] = verdict(run["status"] in ("completed", "escalated") and bool(checks),
                               f"{len(checks)} verification(s), run {run['status']}")

    deviated = [s for s in checks if not s["payload"]["ok"]]
    if not deviated:
        out["recovery"] = verdict(True, "n/a, no deviation" + (" (one was expected)" if exp.get("expects_deviation") else ""))
    else:
        handled = run["status"] == "escalated" or checks[-1]["payload"]["ok"]
        out["recovery"] = verdict(handled, f"{len(deviated)} deviation(s): {deviated[0]['payload']['deviations'][0]}")

    return {"scenario": sc["id"], "title": sc["title"], "run_id": run_id, "status": run["status"],
            "verdict": decision.get("verdict"), "summary": decision.get("summary"), "tokens": run["tokens"],
            "tools": sorted(tools), "checks": out, "pass": all(c["pass"] for c in out.values())}


def scorecard(results: list[dict], mode: str) -> str:
    mark = {True: "pass", False: "FAIL"}
    lines = [f"# Eval: {mode}", "",
             f"{sum(r['pass'] for r in results)}/{len(results)} runs pass every check.", "",
             "| scenario | verdict | " + " | ".join(CHECKS) + " | run |",
             "|---|---|" + "---|" * len(CHECKS) + "---|"]
    for r in results:
        lines.append(f"| {r['scenario']} | {r['verdict']} | "
                     + " | ".join(mark[r["checks"][c]["pass"]] for c in CHECKS) + f" | {mark[r['pass']]} |")
    lines += ["", "## Per check pass rate", ""]
    for c in CHECKS:
        lines.append(f"- {c}: {sum(r['checks'][c]['pass'] for r in results)}/{len(results)}")
    lines += ["", "## Details", ""]
    for r in results:
        lines.append(f"**{r['scenario']}** ({r['status']}, {r['tokens']} tokens): {r['summary']}")
        for c in CHECKS:
            lines.append(f"- {c}: {mark[r['checks'][c]['pass']]}, {r['checks'][c]['detail']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="agent", choices=["agent", "naive"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    results = []
    for sid in args.only or seed.scenario_ids():
        for _ in range(args.repeat):
            t0 = time.time()
            run_id = run_scenario(sid, args.mode)
            r = grade(seed.scenario(sid), run_id)
            r["seconds"] = round(time.time() - t0, 1)
            results.append(r)
            print(f"{sid:28} {'PASS' if r['pass'] else 'FAIL'}  verdict={r['verdict']}  "
                  + " ".join(f"{c}={'ok' if r['checks'][c]['pass'] else 'x'}" for c in CHECKS), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    name = "latest" if args.mode == "agent" and not args.only else f"{args.mode}" if not args.only else "partial"
    (OUT / f"{name}.json").write_text(json.dumps({"mode": args.mode, "results": results}, indent=2, default=str))
    (OUT / f"{name}.md").write_text(scorecard(results, args.mode))
    print(f"\n{sum(r['pass'] for r in results)}/{len(results)} pass. Wrote {OUT / name}.md")


if __name__ == "__main__":
    main()
