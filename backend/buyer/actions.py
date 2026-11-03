"""Executes a validated decision, then reads the world back to check the
result against what the validator predicted."""
from datetime import timedelta

import psycopg

from buyer import seed, supplier
from buyer.db import connect, one, today
from buyer.domain import cover_summary, item_context
from buyer.policy import accepts_gap, budget_line, supplier_item


def _next_id(c, prefix: str) -> str:
    return f"{prefix}-{c.execute('SELECT nextval(%s) AS n', ('agent_doc_seq',)).fetchone()['n']}"


def create_po(run_id, a, sit):
    si = supplier_item(a["supplier_id"], sit["sku"])
    with connect() as c:
        po_id = _next_id(c, "PO")
        c.execute("""
            INSERT INTO purchase_orders (id, node_id, supplier_id, sku, qty, unit_price, status, order_date,
                                         eta, created_by, run_id, note)
            VALUES (%s, %s, %s, %s, %s, %s, 'submitted', %s, %s, 'agent', %s, %s)""",
                  (po_id, sit["node_id"], a["supplier_id"], sit["sku"], a["qty"], si["unit_price"], today(),
                   today() + timedelta(days=si["lead_time_days"]), run_id, a.get("reason")))
    return {"ok": True, "po_id": po_id, "supplier": supplier.submit(po_id)}


def amend_po(run_id, a, sit):
    with connect() as c:
        c.execute("""
            UPDATE purchase_orders
            SET qty = %(q)s,
                qty_confirmed = LEAST(qty_confirmed, %(q)s),
                status = CASE WHEN qty_confirmed >= %(q)s THEN 'confirmed' ELSE status END,
                note = %(note)s
            WHERE id = %(id)s""", {"q": a["qty"], "id": a["po_id"], "note": a.get("reason")})
    return {"ok": True, "po_id": a["po_id"]}


def cancel_po(run_id, a, sit):
    with connect() as c:
        c.execute("UPDATE purchase_orders SET status = 'cancelled', note = %s WHERE id = %s",
                  (a.get("reason"), a["po_id"]))
    return {"ok": True, "po_id": a["po_id"]}


def transfer(run_id, a, sit):
    with connect() as c, c.transaction():
        moved = c.execute("""UPDATE inventory SET on_hand = on_hand - %s
                             WHERE node_id = %s AND sku = %s AND on_hand >= %s RETURNING on_hand""",
                          (a["qty"], a["from_node"], sit["sku"], a["qty"])).fetchone()
        if moved is None:
            return {"ok": False, "error": f"{a['from_node']} no longer holds {a['qty']} units"}
        tr_id = _next_id(c, "TR")
        c.execute("INSERT INTO transfers (id, sku, from_node, to_node, qty, eta, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (tr_id, sit["sku"], a["from_node"], sit["node_id"], a["qty"], today() + timedelta(days=1), run_id))
    return {"ok": True, "transfer_id": tr_id}


def task(kind):
    def open_task(run_id, a, sit):
        with connect() as c:
            row = c.execute("INSERT INTO tasks (run_id, kind, node_id, sku, detail) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                            (run_id, kind, sit["node_id"], sit["sku"],
                             a["reason"] + (f" Question: {a['question']}" if a.get("question") else ""))).fetchone()
        return {"ok": True, "task_id": row["id"]}
    return open_task


APPLY = {"create_po": create_po, "amend_po": amend_po, "cancel_po": cancel_po, "transfer": transfer,
         "request_cycle_count": task("cycle_count"), "escalate": task("escalation")}


def execute(run_id: int, decision: dict, sit: dict) -> list[dict]:
    seed.inject_before_execute()
    results = []
    for a in decision.get("actions") or []:
        try:
            results.append({"action": a, **APPLY[a["type"]](run_id, a, sit)})
        except psycopg.Error as e:
            # A constraint or trigger said no: this is feedback, not a crash.
            results.append({"action": a, "ok": False, "error": e.diag.message_primary or str(e)})
    return results


def verify(decision: dict, results: list[dict], sit: dict, predicted: dict) -> dict:
    """Did the world end up where the validator said it would?"""
    deviations = []
    for r in results:
        a = r["action"]
        if not r["ok"]:
            deviations.append(f"{a['type']} failed: {r['error']}")
            continue
        if a["type"] == "create_po":
            po = one("SELECT * FROM purchase_orders WHERE id = %s", (r["po_id"],))
            if po is None:
                deviations.append(f"{r['po_id']} is missing after submit.")
            elif po["status"] == "rejected":
                deviations.append(f"{po['supplier_id']} rejected {po['id']}: {r['supplier']['message']}")
            elif po["qty_confirmed"] < po["qty"]:
                deviations.append(f"{po['supplier_id']} confirmed only {po['qty_confirmed']} of {po['qty']} "
                                  f"on {po['id']}.")
        elif a["type"] == "amend_po":
            po = one("SELECT qty FROM purchase_orders WHERE id = %s", (a["po_id"],))
            if po["qty"] != a["qty"]:
                deviations.append(f"{a['po_id']} has qty {po['qty']}, expected {a['qty']}.")

    item = item_context(sit["node_id"], sit["sku"])
    actual = cover_summary(item, decision["demand_per_day"])
    want = predicted["stockout_after_first_possible_arrival"]
    got = actual["stockout_after_first_possible_arrival"]
    if got < want:
        deviations.append(f"Projected cover is {got} days, the plan expected {want}.")
    if actual["peak_stock"] > item["capacity"]:
        deviations.append(f"Stock now peaks at {actual['peak_stock']} over capacity {item['capacity']}.")
    budget = budget_line(sit["node_id"], sit["sku"])
    if budget and budget["remaining"] < 0:
        deviations.append(f"Budget overrun: remaining {budget['remaining']}.")
    covered = got >= item["horizon"]
    human_review = accepts_gap(decision)
    if not covered and not human_review:
        deviations.append(f"Store runs out on day {got}, before the coverage horizon (day {item['horizon']}).")
    return {"ok": not deviations, "deviations": deviations, "covered": covered,
            "human_review": human_review, "expected": predicted, "actual": actual}
