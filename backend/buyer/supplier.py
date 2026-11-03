"""Mock supplier system. Its portal quote can be stale; the confirmation it
sends back after a PO is submitted is what it can really ship."""
from datetime import timedelta

from buyer.db import clean, connect, one, today

ITEM = """SELECT si.*, s.active, s.name AS supplier_name FROM supplier_items si
          JOIN suppliers s ON s.id = si.supplier_id WHERE si.supplier_id = %s AND si.sku = %s"""


def quote(supplier_id: str, sku: str, qty: int) -> dict:
    si = one(ITEM, (supplier_id, sku))
    if si is None:
        return {"error": f"{supplier_id} does not supply {sku}"}
    if not si["active"]:
        return {"error": f"{supplier_id} is not taking orders"}
    available = si["quoted_qty"] if si["quoted_qty"] is not None else si["available_qty"]
    return {"supplier_id": supplier_id, "sku": sku, "requested": qty, "available": available,
            "unit_price": si["unit_price"], "moq": si["moq"], "case_pack": si["case_pack"],
            "earliest_eta": (today() + timedelta(days=si["lead_time_days"])).isoformat()}


def submit(po_id: str) -> dict:
    """The supplier receives a submitted PO and answers with a confirmation."""
    with connect() as c, c.transaction():
        po = c.execute("SELECT * FROM purchase_orders WHERE id = %s FOR UPDATE", (po_id,)).fetchone()
        si = c.execute(ITEM + " FOR UPDATE OF si", (po["supplier_id"], po["sku"])).fetchone()
        if si is None or not si["active"]:
            confirmed, message = 0, "Supplier is not taking orders for this item."
        elif po["qty"] < si["moq"]:
            confirmed, message = 0, f"Below our minimum order of {si['moq']}."
        else:
            confirmed = min(po["qty"], si["available_qty"])
            message = ("Confirmed in full." if confirmed == po["qty"]
                       else f"We can only supply {confirmed} of {po['qty']} units.")
        status = ("confirmed" if confirmed == po["qty"]
                  else "partially_confirmed" if confirmed else "rejected")
        eta = today() + timedelta(days=si["lead_time_days"]) if si else None
        c.execute("UPDATE supplier_items SET available_qty = available_qty - %s WHERE supplier_id = %s AND sku = %s",
                  (confirmed, po["supplier_id"], po["sku"]))
        c.execute("UPDATE purchase_orders SET qty_confirmed = %s, status = %s, eta = %s WHERE id = %s",
                  (confirmed, status, eta, po_id))
        if confirmed < po["qty"]:
            c.execute("INSERT INTO supplier_events (po_id, kind, message, available) VALUES (%s, %s, %s, %s)",
                      (po_id, "partial_fill" if confirmed else "rejected", message, confirmed))
    return clean({"po_id": po_id, "status": status, "qty_confirmed": confirmed, "eta": eta, "message": message})
