"""Load a scenario fixture into a fresh schema.

Fixtures use day offsets relative to the scenario's `today` so the numbers in
them can be checked by hand.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from buyer.db import connect

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
# Weekly wobble so sales history is not a flat line.
NOISE = [0.0, 0.05, -0.04, 0.02, -0.03, 0.06, -0.05]


def scenario_ids() -> list[str]:
    return sorted(p.stem for p in SCENARIOS.glob("*.json"))


def scenario(sid: str) -> dict:
    return json.loads((SCENARIOS / f"{sid}.json").read_text())


def insert(c, table, row, conflict=""):
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks}) {conflict}", list(row.values()))


def po_row(po: dict, today: date) -> dict:
    po = dict(po)
    po["order_date"] = today + timedelta(days=po.pop("order_day"))
    eta = po.pop("eta_day", None)
    po["eta"] = None if eta is None else today + timedelta(days=eta)
    return po


def inject_before_execute() -> list[str]:
    """Failure injection: writes another actor makes between the agent's
    validation and its commit. Each row lands once."""
    with connect() as c:
        world = c.execute("SELECT today, scenario FROM world").fetchone()
        injected = scenario(world["scenario"]).get("inject_before_execute", [])
        for po in injected:
            insert(c, "purchase_orders", po_row(po, world["today"]), "ON CONFLICT (id) DO NOTHING")
        return [po["id"] for po in injected]


def load(sid: str) -> dict:
    sc = scenario(sid)
    today = date.fromisoformat(sc.get("today", "2026-11-03"))

    def day(n):
        return None if n is None else today + timedelta(days=n)

    with connect() as c:
        c.execute(SCHEMA)
        insert(c, "world", {"id": 1, "today": today, "scenario": sid})
        for n in sc["nodes"]:
            insert(c, "nodes", n)
        for p in sc["products"]:
            insert(c, "products", p)
        for s in sc["suppliers"]:
            insert(c, "suppliers", s)
        for si in sc["supplier_items"]:
            insert(c, "supplier_items", si)
        for i in sc["inventory"]:
            i = dict(i)
            i["last_counted"] = day(i.pop("last_counted_day"))
            insert(c, "inventory", i)
        for f in sc["forecast"]:
            insert(c, "forecast", f)
        for s in sc["sales"]:
            history = [round(s["base"] * (1 + NOISE[k % 7])) for k in range(28)]
            last = s.get("last", [])
            if last:
                history[-len(last):] = last
            for k, units in enumerate(history):
                insert(c, "sales_daily", {"node_id": s["node_id"], "sku": s["sku"],
                                          "day": day(k - 28), "units": units})
        for b in sc.get("budgets", []):
            b = dict(b)
            b["period_start"], b["period_end"] = day(b.pop("start_day")), day(b.pop("end_day"))
            insert(c, "budgets", b)
        for po in sc.get("purchase_orders", []):
            insert(c, "purchase_orders", po_row(po, today))
        for sig in sc.get("signals", []):
            sig = dict(sig)
            sig["start_day"], sig["end_day"] = day(sig["start_day"]), day(sig["end_day"])
            insert(c, "demand_signals", sig)
        for r in sc.get("recommendations", []):
            insert(c, "recommendations", r)
        for e in sc.get("supplier_events", []):
            insert(c, "supplier_events", e)
        insert(c, "situations", sc["situation"])
    return sc


if __name__ == "__main__":
    print(load(sys.argv[1] if len(sys.argv) > 1 else "s1_modify_open_po")["id"], "loaded")
