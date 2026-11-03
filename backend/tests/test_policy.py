"""Policy gate checks against the scenario fixtures. Needs Postgres (DATABASE_URL)."""
from buyer import seed
from buyer.policy import validate

ALL_TOOLS = {"get_item_snapshot", "get_demand", "get_open_purchase_orders", "get_supplier_options",
             "get_budget", "get_network_stock", "project_inventory"}


def check(sid, actions, demand, verdict="modify", tools=ALL_TOOLS):
    sc = seed.load(sid)
    return validate({"verdict": verdict, "demand_per_day": demand, "actions": actions}, sc["situation"], tools)


def po(supplier, qty):
    return {"type": "create_po", "supplier_id": supplier, "qty": qty}


def test_blind_recommendation_breaks_storage_and_budget():
    r = check("s1_modify_open_po", [po("SUP-ANDINA", 800)], 60, verdict="accept")
    assert not r["ok"]
    assert any("Storage" in e for e in r["errors"])
    assert any("remaining budget" in e for e in r["errors"])


def test_right_sized_order_passes_without_approval():
    r = check("s1_modify_open_po", [po("SUP-ANDINA", 240)], 60)
    assert r["ok"], r["errors"]
    assert not r["needs_approval"]
    assert r["plan"]["stockout_after_first_possible_arrival"] == 15


def test_case_pack_and_moq():
    r = check("s1_modify_open_po", [po("SUP-ANDINA", 250), po("SUP-NORTE", 240)], 60)
    assert any("cases of 24" in e for e in r["errors"])
    assert any("MOQ is 480" in e for e in r["errors"])


def test_missing_evidence_blocks():
    r = check("s1_modify_open_po", [po("SUP-ANDINA", 240)], 60, tools={"get_item_snapshot"})
    assert any("Missing evidence" in e for e in r["errors"])


def test_accepting_partial_fill_alone_leaves_gap():
    r = check("s2_shortfall_transfer", [], 50, verdict="accept")
    assert any("Coverage gap" in e for e in r["errors"])


def test_transfer_respects_source_target():
    ok = check("s2_shortfall_transfer", [{"type": "transfer", "from_node": "DS-USAQUEN", "qty": 240}], 50)
    assert ok["ok"], ok["errors"]
    greedy = check("s2_shortfall_transfer", [{"type": "transfer", "from_node": "DS-USAQUEN", "qty": 500}], 50)
    assert any("spare at most 440" in e for e in greedy["errors"])


def test_perishable_moq_flags_waste_and_premium_needs_approval():
    waste = check("s4_moq_perishable", [po("SUP-ANDINA", 240)], 12)
    assert any("expire" in w for w in waste["warnings"]) and waste["needs_approval"]
    local = check("s4_moq_perishable", [po("SUP-LACTEOS", 72)], 12)
    assert local["ok"] and not any("expire" in w for w in local["warnings"])
    assert any("above reference cost" in w for w in local["warnings"]) and local["needs_approval"]


def test_budget_cap_forces_partial_buy_and_escalation():
    full = check("s4_budget_cap", [po("SUP-ANDINA", 300)], 30)
    assert any("remaining budget 900.00" in e for e in full["errors"])
    alone = check("s4_budget_cap", [po("SUP-ANDINA", 144)], 30)
    assert any("Coverage gap" in e for e in alone["errors"])
    escalated = check("s4_budget_cap", [po("SUP-ANDINA", 144),
                                        {"type": "escalate", "reason": "budget exception for 156 units"}], 30)
    assert escalated["ok"], escalated["errors"]


def test_hallucinated_outcome_is_rejected():
    sc = seed.load("s1_modify_open_po")
    r = validate({"verdict": "modify", "demand_per_day": 60, "actions": [po("SUP-ANDINA", 240)],
                  "expected": {"cover_days": 30, "spend": 216}}, sc["situation"], ALL_TOOLS)
    assert any("expected.cover_days" in e for e in r["errors"])
