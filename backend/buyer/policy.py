"""Deterministic policy gate between the model's proposal and any write.

Errors block the proposal and go back to the model as feedback. Warnings let
it through but route executable actions to a human for approval.
"""
import os

from buyer.db import one
from buyer.domain import HORIZON, cover_summary, item_context, sales_stats

AUTO_APPROVE_LIMIT = float(os.environ.get("AUTO_APPROVE_LIMIT", "1000"))
PRICE_PREMIUM_LIMIT = 0.15
STALE_COUNT_DAYS = 30

# Facts the agent must have looked at before it may propose a given action.
BASE_EVIDENCE = {"get_item_snapshot", "get_demand", "get_open_purchase_orders"}
EVIDENCE = {
    "create_po": {"get_supplier_options", "get_budget"},
    "transfer": {"get_network_stock"},
}
EXECUTABLE = {"create_po", "amend_po", "cancel_po", "transfer"}
TASKS = {"request_cycle_count", "escalate"}


def budget_line(node_id: str, sku: str) -> dict | None:
    return one("""
        SELECT bs.* FROM budget_status bs JOIN products p ON p.category = bs.category
        WHERE bs.node_id = %s AND p.sku = %s
          AND (SELECT today FROM world) BETWEEN bs.period_start AND bs.period_end""", (node_id, sku))


def supplier_item(supplier_id: str, sku: str) -> dict | None:
    return one("""
        SELECT si.*, s.active, s.fill_rate FROM supplier_items si JOIN suppliers s ON s.id = si.supplier_id
        WHERE si.supplier_id = %s AND si.sku = %s""", (supplier_id, sku))


def transferable(node_id: str, sku: str) -> int:
    """Stock a node can give away and still cover its own target."""
    try:
        ctx = item_context(node_id, sku)
    except ValueError:
        return 0
    need = (ctx["forecast"] or 0) * (ctx["horizon"] + ctx["safety_days"])
    supply = ctx["on_hand"] + sum(i["qty"] for i in ctx["inbound"])
    return int(max(0, min(ctx["on_hand"], supply - need)))


def accepts_gap(decision: dict) -> bool:
    """A coverage gap is acceptable only while a person is being brought in."""
    return decision.get("verdict") in ("investigate", "escalate") or any(
        a.get("type") == "escalate" for a in decision.get("actions") or [])


def validate(decision: dict, sit: dict, tools_called: set[str]) -> dict:
    node, sku = sit["node_id"], sit["sku"]
    item = item_context(node, sku)
    errors: list[str] = []
    warnings: list[str] = []
    actions = decision.get("actions") or []
    kinds = {a.get("type") for a in actions}

    needed = set(BASE_EVIDENCE)
    for k in kinds:
        needed |= EVIDENCE.get(k, set())
    missing = sorted(needed - tools_called)
    if missing:
        errors.append(f"Missing evidence: call {', '.join(missing)} before deciding.")

    demand = decision.get("demand_per_day")
    if not isinstance(demand, (int, float)) or demand < 0:
        errors.append("demand_per_day must be a number >= 0.")
        demand = item["forecast"] or 0

    inbound = [dict(i) for i in item["inbound"]]
    extra: list[dict] = []
    spend = 0.0
    for a in actions:
        t = a.get("type")
        qty = a.get("qty")
        if t in EXECUTABLE and t != "cancel_po" and (not isinstance(qty, int) or qty <= 0):
            errors.append(f"{t}: qty must be a positive integer.")
            continue
        if t == "create_po":
            si = supplier_item(a.get("supplier_id", ""), sku)
            if si is None:
                errors.append(f"create_po: {a.get('supplier_id')} does not supply {sku}.")
                continue
            if not si["active"]:
                errors.append(f"create_po: {si['supplier_id']} is inactive.")
            if qty < si["moq"]:
                errors.append(f"create_po: {si['supplier_id']} MOQ is {si['moq']}, proposed {qty}.")
            if qty % si["case_pack"]:
                lo = qty // si["case_pack"] * si["case_pack"]
                errors.append(f"create_po: {si['supplier_id']} sells in cases of {si['case_pack']}; "
                              f"use {lo} or {lo + si['case_pack']}, not {qty}.")
            premium = si["unit_price"] / item["unit_cost"] - 1
            if premium > PRICE_PREMIUM_LIMIT:
                warnings.append(f"{si['supplier_id']} price {si['unit_price']:.2f} is {premium:.0%} above "
                                f"reference cost {item['unit_cost']:.2f}.")
            spend += qty * si["unit_price"]
            extra.append({"ref": f"new PO {si['supplier_id']}", "kind": "po", "qty": qty,
                          "day": si["lead_time_days"]})
        elif t in ("amend_po", "cancel_po"):
            po = one("SELECT * FROM purchase_orders WHERE id = %s", (a.get("po_id"),))
            if po is None or (po["node_id"], po["sku"]) != (node, sku):
                errors.append(f"{t}: {a.get('po_id')} is not a PO for {sku} at {node}.")
                continue
            if po["status"] not in ("submitted", "confirmed", "partially_confirmed"):
                errors.append(f"{t}: {po['id']} is {po['status']} and cannot change.")
                continue
            entry = next(i for i in inbound if i["ref"] == po["id"])
            if t == "cancel_po":
                inbound.remove(entry)
                if po["status"] == "confirmed":
                    warnings.append(f"Cancelling confirmed {po['id']} may carry a supplier penalty.")
            elif qty > po["qty"]:
                errors.append(f"amend_po: can only reduce {po['id']} (qty {po['qty']}); "
                              "create a new PO for more units.")
            else:
                entry["qty"] = min(entry["qty"], qty)
        elif t == "transfer":
            src = a.get("from_node")
            room = transferable(src, sku) if src != node else 0
            if qty > room:
                errors.append(f"transfer: {src} can spare at most {room} units of {sku} "
                              "without dropping below its own target.")
                continue
            extra.append({"ref": f"transfer from {src}", "kind": "transfer", "qty": qty, "day": 1})
        elif t in TASKS:
            if not (a.get("reason") or "").strip():
                errors.append(f"{t}: give a reason.")
        else:
            errors.append(f"Unknown action type {t!r}.")

    budget = budget_line(node, sku)
    if spend and budget and spend > budget["remaining"] + 0.005:
        errors.append(f"Spend {spend:.2f} exceeds the remaining budget {budget['remaining']:.2f} "
                      f"(period ends {budget['period_end']}, next period {budget['next_amount']}).")

    plan = cover_summary({**item, "inbound": inbound}, demand, extra)
    cover = plan["stockout_after_first_possible_arrival"]
    if plan["peak_stock"] > item["capacity"]:
        errors.append(f"Storage: stock peaks at {plan['peak_stock']} against capacity {item['capacity']}.")
    if cover < item["horizon"]:
        msg = (f"Coverage gap: at {demand}/day the plan runs out on day {cover}, before the next order "
               f"could land (day {item['horizon']}). Add supply or escalate.")
        (warnings if accepts_gap(decision) else errors).append(msg)
    if extra and item["max_cover_days"] < cover < HORIZON:
        warnings.append(f"Overstock: plan covers {cover} days, above the {item['max_cover_days']} day cap.")
    if extra and plan["expected_waste"]:
        warnings.append(f"Shelf life: about {plan['expected_waste']} units would expire "
                        f"({item['shelf_life_days']} day shelf life).")

    stats = sales_stats(node, sku)
    reference = max(item["forecast"] or 0, stats["avg_last_7"])
    if reference and demand > 1.5 * reference:
        warnings.append(f"Demand basis {demand}/day is far above both forecast and recent sales.")
    stale = item["days_since_count"]
    if "create_po" in kinds and "request_cycle_count" not in kinds and (
            stale > STALE_COUNT_DAYS or stats["zero_sale_streak_days"] >= 4):
        warnings.append(f"Stock record suspect (last count {stale} days ago, "
                        f"{stats['zero_sale_streak_days']} zero sale days) and no cycle count requested.")

    claimed = decision.get("expected") or {}
    if isinstance(claimed.get("spend"), (int, float)) and abs(claimed["spend"] - spend) > max(1.0, 0.05 * spend):
        errors.append(f"expected.spend says {claimed['spend']} but the actions cost {spend:.2f}.")
    if (isinstance(claimed.get("cover_days"), (int, float)) and cover < HORIZON
            and abs(claimed["cover_days"] - cover) > max(2, 0.25 * cover)):
        errors.append(f"expected.cover_days says {claimed['cover_days']} but the projection gives {cover}. "
                      "Use project_inventory.")

    reasons: list[str] = []
    if kinds & EXECUTABLE:
        reasons += warnings
        if spend > AUTO_APPROVE_LIMIT:
            reasons.append(f"Spend {spend:.2f} is above the auto approve limit {AUTO_APPROVE_LIMIT:.0f}.")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "spend": round(spend, 2),
            "plan": plan, "needs_approval": bool(reasons), "approval_reasons": reasons}
