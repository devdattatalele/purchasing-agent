"""Inventory projection. The agent's what if tool, the policy validator, the
post execution check and the eval grader all use this, so a number the agent
sees is the number it gets judged on."""
from datetime import date

from buyer.db import one, rows, today

HORIZON = 90


def project(on_hand: float, demand: float, arrivals: dict[int, float], horizon: int = HORIZON) -> dict:
    """Day by day stock. Arrivals land at the start of a day, demand is sold
    from what is on the shelf, and anything not sold is a lost sale."""
    stock, peak, lost = float(on_hand), float(on_hand), []
    for d in range(horizon):
        stock += arrivals.get(d, 0)
        peak = max(peak, stock)
        sold = min(stock, demand)
        lost.append(demand - sold)
        stock -= sold
    return {"lost_by_day": lost, "peak": peak, "end_stock": stock}


def first_stockout(proj: dict, from_day: int = 0) -> int:
    """First day at or after from_day with lost sales; HORIZON if none."""
    for d, lost in enumerate(proj["lost_by_day"]):
        if d >= from_day and lost > 0.01:
            return d
    return len(proj["lost_by_day"])


def lost_units(proj: dict, until_day: int) -> float:
    return round(sum(proj["lost_by_day"][:until_day]), 1)


def expected_waste(on_hand: float, demand: float, arrivals: dict[int, float], shelf_life: int | None) -> int:
    """Units that arrive but cannot sell within shelf life, assuming FIFO."""
    if not shelf_life:
        return 0
    stock, waste = float(on_hand), 0.0
    for d in range(HORIZON):
        if arrivals.get(d):
            stock += arrivals[d]
            waste += max(0.0, stock - demand * shelf_life)
            stock = min(stock, demand * shelf_life)
        stock = max(0.0, stock - demand)
    return round(waste)


def offset(day_iso: str | None, base: date) -> int:
    return 0 if day_iso is None else max(0, (date.fromisoformat(day_iso) - base).days)


def inbound(node_id: str, sku: str, base: date) -> list[dict]:
    """Open POs and transfers headed to the node. A partially confirmed PO
    only counts what the supplier confirmed."""
    pos = rows("""
        SELECT id, supplier_id, qty, qty_confirmed, status, eta, coalesce(qty_confirmed, qty) AS expected
        FROM purchase_orders
        WHERE node_id = %s AND sku = %s AND status IN ('submitted', 'confirmed', 'partially_confirmed')
        ORDER BY eta""", (node_id, sku))
    moves = rows("SELECT id, from_node, qty, eta FROM transfers WHERE to_node = %s AND sku = %s",
                 (node_id, sku))
    out = [{"ref": p["id"], "kind": "po", "qty": p["expected"], "day": offset(p["eta"], base)} for p in pos]
    out += [{"ref": t["id"], "kind": "transfer", "qty": t["qty"], "day": offset(t["eta"], base)} for t in moves]
    return out


def arrivals_of(items: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for i in items:
        out[i["day"]] = out.get(i["day"], 0) + i["qty"]
    return out


def sales_stats(node_id: str, sku: str) -> dict:
    hist = [r["units"] for r in rows(
        "SELECT units FROM sales_daily WHERE node_id = %s AND sku = %s ORDER BY day", (node_id, sku))]
    zero_streak = 0
    for units in reversed(hist):
        if units:
            break
        zero_streak += 1
    last7, prior = hist[-7:], hist[:-7]
    return {
        "daily_last_28": hist,
        "avg_last_7": round(sum(last7) / max(1, len(last7)), 1),
        "avg_prior_21": round(sum(prior) / max(1, len(prior)), 1),
        "zero_sale_streak_days": zero_streak,
    }


def item_context(node_id: str, sku: str) -> dict:
    """Everything the policy needs about one SKU at one node."""
    base = today()
    item = one("""
        SELECT p.*, i.on_hand, i.capacity, i.last_counted, f.units_per_day AS forecast
        FROM products p
        JOIN inventory i ON i.sku = p.sku AND i.node_id = %s
        LEFT JOIN forecast f ON f.sku = p.sku AND f.node_id = i.node_id
        WHERE p.sku = %s""", (node_id, sku))
    if item is None:
        raise ValueError(f"{sku} is not stocked at {node_id}")
    leads = rows("""
        SELECT si.lead_time_days FROM supplier_items si JOIN suppliers s ON s.id = si.supplier_id
        WHERE si.sku = %s AND s.active""", (sku,))
    item["min_lead"] = min((r["lead_time_days"] for r in leads), default=0)
    item["horizon"] = item["min_lead"] + item["review_days"]
    item["inbound"] = inbound(node_id, sku, base)
    item["days_since_count"] = (base - date.fromisoformat(item["last_counted"])).days
    return item


def cover_summary(item: dict, demand: float, extra: list[dict] | None = None) -> dict:
    """Project an item with optional extra arrivals and summarise it."""
    flows = item["inbound"] + (extra or [])
    arrivals = arrivals_of(flows)
    proj = project(item["on_hand"], demand, arrivals)
    return {
        "demand_per_day": demand,
        "supply_total": item["on_hand"] + sum(f["qty"] for f in flows),
        "first_stockout_day": first_stockout(proj),
        "stockout_after_first_possible_arrival": first_stockout(proj, item["min_lead"]),
        "lost_units_in_horizon": lost_units(proj, item["horizon"]),
        "peak_stock": round(proj["peak"]),
        "capacity": item["capacity"],
        "expected_waste": expected_waste(item["on_hand"], demand, arrivals, item["shelf_life_days"]),
        "horizon_days": item["horizon"],
        "target_days": item["horizon"] + item["safety_days"],
    }
