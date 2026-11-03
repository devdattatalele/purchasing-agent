import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from buyer import agent, seed, supplier
from buyer.db import one, rows
from buyer.domain import cover_summary, item_context

app = FastAPI(title="AI purchasing agent")
ROOT = Path(__file__).resolve().parents[2]


@app.on_event("startup")
def first_world():
    """A fresh database gets the headline scenario so the UI has something to show."""
    try:
        one("SELECT 1 FROM world")
    except Exception:
        seed.load("s1_modify_open_po")


def background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


@app.get("/api/scenarios")
def scenarios():
    return [{k: sc[k] for k in ("id", "title", "notes", "situation")} for sc in map(seed.scenario, seed.scenario_ids())]


@app.post("/api/scenarios/{sid}/load")
def load(sid: str):
    if sid not in seed.scenario_ids():
        raise HTTPException(404, f"unknown scenario {sid}")
    seed.load(sid)
    return world()


@app.get("/api/world")
def world():
    w = one("SELECT * FROM world")
    sit = one("SELECT * FROM situations")
    node, sku = sit["node_id"], sit["sku"]
    item = item_context(node, sku)
    return {
        "today": w["today"], "scenario": w["scenario"], "situation": sit,
        "item": {k: item[k] for k in ("sku", "name", "category", "unit_cost", "shelf_life_days", "on_hand",
                                      "capacity", "last_counted", "forecast", "horizon", "safety_days")},
        "projection": cover_summary(item, item["forecast"] or 0),
        "sales": rows("SELECT day, units FROM sales_daily WHERE node_id = %s AND sku = %s ORDER BY day", (node, sku)),
        "purchase_orders": rows("SELECT * FROM purchase_orders WHERE sku = %s ORDER BY order_date, id", (sku,)),
        "transfers": rows("SELECT * FROM transfers WHERE sku = %s", (sku,)),
        "network": rows("SELECT node_id, on_hand, capacity FROM inventory WHERE sku = %s", (sku,)),
        "suppliers": rows("""SELECT si.*, s.name, s.active, s.fill_rate FROM supplier_items si
                             JOIN suppliers s ON s.id = si.supplier_id WHERE si.sku = %s ORDER BY unit_price""", (sku,)),
        "budget": rows("SELECT * FROM budget_status WHERE node_id = %s", (node,)),
        "recommendation": one("SELECT * FROM recommendations WHERE id = %s", (sit["ref_id"],)),
        "supplier_events": rows("SELECT * FROM supplier_events ORDER BY id"),
        "signals": rows("SELECT * FROM demand_signals"),
        "tasks": rows("SELECT * FROM tasks ORDER BY id"),
    }


class RunRequest(BaseModel):
    mode: str = "agent"


@app.post("/api/runs")
def start_run(req: RunRequest):
    sit = one("SELECT id FROM situations")
    if req.mode == "naive":
        return {"run_id": agent.run_naive(sit["id"])}
    run_id = agent.start(sit["id"])
    background(agent.run, run_id)
    return {"run_id": run_id}


@app.get("/api/runs")
def list_runs():
    return rows("SELECT id, situation_id, mode, model, status, tokens, started_at, finished_at FROM agent_runs "
                "ORDER BY id DESC")


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = one("SELECT id, situation_id, mode, model, status, decision, outcome, tokens, error, started_at, "
              "finished_at FROM agent_runs WHERE id = %s", (run_id,))
    if run is None:
        raise HTTPException(404, "no such run")
    run["steps"] = rows("SELECT id, kind, title, payload, at FROM agent_steps WHERE run_id = %s ORDER BY id", (run_id,))
    run["approvals"] = rows("SELECT * FROM approvals WHERE run_id = %s ORDER BY id", (run_id,))
    return run


class Answer(BaseModel):
    approved: bool
    note: str = ""


@app.post("/api/approvals/{approval_id}")
def answer(approval_id: int, body: Answer):
    if one("SELECT id FROM approvals WHERE id = %s AND status = 'pending'", (approval_id,)) is None:
        raise HTTPException(409, "approval is not pending")
    background(agent.decide_approval, approval_id, body.approved, body.note)
    return {"ok": True}


@app.get("/api/eval/{name}")
def eval_results(name: str):
    if name not in ("latest", "naive"):
        raise HTTPException(404, "unknown eval")
    path = ROOT / "eval" / "results" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


# The mock supplier's own API, the same functions the executor calls.
@app.get("/supplier/{supplier_id}/quote")
def supplier_quote(supplier_id: str, sku: str, qty: int):
    return supplier.quote(supplier_id, sku, qty)


@app.post("/supplier/orders/{po_id}/submit")
def supplier_submit(po_id: str):
    if one("SELECT id FROM purchase_orders WHERE id = %s AND status = 'submitted'", (po_id,)) is None:
        raise HTTPException(409, "PO is not in submitted state")
    return supplier.submit(po_id)


if (ROOT / "web" / "dist").exists():
    app.mount("/", StaticFiles(directory=ROOT / "web" / "dist", html=True), name="web")
