"""Read only tools the model can call, in OpenAI function calling format
(litellm translates for other providers). No tool here writes anything."""
import json
from datetime import date, timedelta

from buyer import supplier
from buyer.db import rows, today
from buyer.domain import cover_summary, item_context, sales_stats
from buyer.policy import budget_line, transferable

NODE_SKU = {"node_id": {"type": "string"}, "sku": {"type": "string"}}


def get_item_snapshot(node_id, sku):
    i = item_context(node_id, sku)
    return {
        "sku": sku, "name": i["name"], "category": i["category"], "reference_unit_cost": i["unit_cost"],
        "shelf_life_days": i["shelf_life_days"],
        "stock": {"on_hand": i["on_hand"], "capacity": i["capacity"], "last_counted": i["last_counted"],
                  "days_since_count": i["days_since_count"]},
        "policy": {"review_days": i["review_days"], "safety_days": i["safety_days"],
                   "max_cover_days": i["max_cover_days"], "fastest_supplier_lead_days": i["min_lead"],
                   "coverage_horizon_days": i["horizon"],
                   "note": "A plan must not run out before the coverage horizon (fastest lead + review)."},
    }


def get_demand(node_id, sku):
    i = item_context(node_id, sku)
    s = sales_stats(node_id, sku)
    signals = rows("""SELECT kind, description, start_day, end_day FROM demand_signals
                      WHERE node_id = %s AND (sku = %s OR sku IS NULL)
                        AND end_day >= %s AND start_day <= %s ORDER BY start_day""",
                   (node_id, sku, today() - timedelta(days=14), today() + timedelta(days=30)))
    return {"forecast_per_day": i["forecast"], **s,
            "last_7_vs_forecast": round(s["avg_last_7"] / i["forecast"], 2) if i["forecast"] else None,
            "signals": signals, "today": today().isoformat()}


def get_open_purchase_orders(node_id, sku):
    pos = rows("""SELECT id, supplier_id, qty, qty_confirmed, unit_price, status, order_date, eta, created_by
                  FROM purchase_orders WHERE node_id = %s AND sku = %s
                    AND status IN ('submitted', 'confirmed', 'partially_confirmed') ORDER BY eta""",
               (node_id, sku))
    for po in pos:
        po["supplier_messages"] = [e["message"] for e in rows(
            "SELECT message FROM supplier_events WHERE po_id = %s ORDER BY id", (po["id"],))]
    inbound = rows("SELECT id, from_node, qty, eta FROM transfers WHERE to_node = %s AND sku = %s",
                   (node_id, sku))
    return {"open_purchase_orders": pos, "inbound_transfers": inbound,
            "note": "Projection counts confirmed qty for partially confirmed POs."}


def get_supplier_options(sku):
    return {"options": rows("""
        SELECT s.id AS supplier_id, s.name, s.active, s.fill_rate, si.unit_price,
               round(si.unit_price / p.unit_cost - 1, 3) AS premium_vs_reference,
               si.moq, si.case_pack, si.lead_time_days
        FROM supplier_items si JOIN suppliers s ON s.id = si.supplier_id JOIN products p ON p.sku = si.sku
        WHERE si.sku = %s ORDER BY si.unit_price""", (sku,)),
        "note": "Availability is not shown here; ask check_supplier_availability."}


def check_supplier_availability(supplier_id, sku, qty):
    return supplier.quote(supplier_id, sku, qty)


def get_budget(node_id, sku):
    b = budget_line(node_id, sku)
    if b is None:
        return {"budget": None, "note": "No budget line; spend is not capped."}
    return {**b, "days_until_reset": (date.fromisoformat(b["period_end"]) - today()).days + 1}


def get_network_stock(sku, exclude_node_id):
    out = []
    for r in rows("""SELECT i.node_id, i.on_hand, f.units_per_day FROM inventory i
                     LEFT JOIN forecast f ON f.node_id = i.node_id AND f.sku = i.sku
                     WHERE i.sku = %s AND i.node_id <> %s""", (sku, exclude_node_id)):
        out.append({**r, "transferable": transferable(r["node_id"], sku), "transfer_lead_days": 1})
    return {"nodes": out, "note": "transferable keeps each node at its own coverage target."}


def project_inventory(node_id, sku, demand_per_day, extra_supply=None, po_qty_overrides=None):
    item = item_context(node_id, sku)
    overrides = {o["po_id"]: o["qty"] for o in po_qty_overrides or []}
    item["inbound"] = [{**i, "qty": min(i["qty"], overrides.get(i["ref"], i["qty"]))}
                       for i in item["inbound"] if overrides.get(i["ref"], 1) > 0]
    extra = [{"ref": e.get("label", "candidate"), "kind": "candidate", "qty": e["qty"], "day": e["arrival_day"]}
             for e in extra_supply or []]
    summary = cover_summary(item, demand_per_day, extra)
    arriving = item["on_hand"] + sum(f["qty"] for f in item["inbound"] + extra if f["day"] < summary["target_days"])
    summary["units_short_of_target"] = max(0, round(demand_per_day * summary["target_days"] - arriving))
    return summary


SPECS = [
    ("get_item_snapshot", "Product master, stock on hand, storage capacity, last cycle count and the "
     "replenishment policy for one SKU at one node.", NODE_SKU, ["node_id", "sku"]),
    ("get_demand", "Forecast, last 28 days of sales, recent averages, zero sale streak and demand "
     "signals (promotions, weather, events) for one SKU at one node.", NODE_SKU, ["node_id", "sku"]),
    ("get_open_purchase_orders", "Open POs (with supplier messages) and inbound transfers for one SKU at "
     "one node.", NODE_SKU, ["node_id", "sku"]),
    ("get_supplier_options", "Every supplier that carries the SKU: price, premium over reference cost, "
     "MOQ, case pack, lead time, fill rate.", {"sku": {"type": "string"}}, ["sku"]),
    ("check_supplier_availability", "Ask a supplier's portal how many units it can ship and when.",
     {"supplier_id": {"type": "string"}, "sku": {"type": "string"}, "qty": {"type": "integer"}},
     ["supplier_id", "sku", "qty"]),
    ("get_budget", "Open to buy budget for the SKU's category at the node: amount, committed, remaining, "
     "period end and next period amount.", NODE_SKU, ["node_id", "sku"]),
    ("get_network_stock", "Stock of the SKU at other nodes and how much each can transfer without "
     "dropping below its own target.", {"sku": {"type": "string"}, "exclude_node_id": {"type": "string"}},
     ["sku", "exclude_node_id"]),
    ("project_inventory", "Deterministic day by day projection. Returns first stockout day, stockout "
     "after the first possible arrival (the cover metric the policy uses), lost units, peak stock vs "
     "capacity, expected waste and units short of the target. Use it for the current plan and for "
     "every candidate plan.",
     {**NODE_SKU, "demand_per_day": {"type": "number"},
      "extra_supply": {"type": "array", "description": "Candidate arrivals, e.g. a new PO landing after its lead time.",
                       "items": {"type": "object", "properties": {
                           "qty": {"type": "integer"}, "arrival_day": {"type": "integer"},
                           "label": {"type": "string"}}, "required": ["qty", "arrival_day"]}},
      "po_qty_overrides": {"type": "array", "description": "Simulate amending or cancelling (qty 0) an open PO.",
                           "items": {"type": "object", "properties": {
                               "po_id": {"type": "string"}, "qty": {"type": "integer"}},
                               "required": ["po_id", "qty"]}}},
     ["node_id", "sku", "demand_per_day"]),
]

DECISION = ("propose_decision", "Submit the decision. It is validated by the policy gate, may need buyer "
            "approval, is executed, and the verified result comes back to you.", {
    "verdict": {"type": "string", "enum": ["accept", "modify", "reject", "investigate", "escalate"],
                "description": "Judgement on the current recommendation or plan."},
    "summary": {"type": "string", "description": "One or two sentences for the buyer."},
    "key_factors": {"type": "array", "items": {"type": "string"},
                    "description": "The facts that drove the decision, with numbers."},
    "demand_per_day": {"type": "number", "description": "Demand basis used for the plan."},
    "demand_basis_reason": {"type": "string"},
    "actions": {"type": "array", "items": {"type": "object", "properties": {
        "type": {"type": "string", "enum": ["create_po", "amend_po", "cancel_po", "transfer",
                                            "request_cycle_count", "escalate"]},
        "supplier_id": {"type": "string", "description": "create_po"},
        "qty": {"type": "integer", "description": "create_po, amend_po (new total qty), transfer"},
        "po_id": {"type": "string", "description": "amend_po, cancel_po"},
        "from_node": {"type": "string", "description": "transfer source node"},
        "reason": {"type": "string"},
        "question": {"type": "string", "description": "escalate: what the buyer must decide"}},
        "required": ["type", "reason"]}},
    "expected": {"type": "object", "properties": {
        "cover_days": {"type": "number", "description": "stockout_after_first_possible_arrival for the final plan"},
        "spend": {"type": "number"}}},
}, ["verdict", "summary", "key_factors", "demand_per_day", "actions", "expected"])

FUNCTIONS = {name: globals()[name] for name, *_ in SPECS}
TOOL_SPECS = [{"type": "function", "function": {
    "name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}}
    for name, desc, props, req in SPECS + [DECISION]]


def call_tool(name: str, args: dict):
    if name not in FUNCTIONS:
        return {"error": f"unknown tool {name}"}
    try:
        return FUNCTIONS[name](**args)
    except (TypeError, ValueError, KeyError) as e:
        return {"error": str(e)}


def render(result) -> str:
    return json.dumps(result, default=str)
