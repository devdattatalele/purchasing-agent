"""Executor and post execution verification against the mock supplier."""
from buyer import seed
from buyer.actions import execute, verify
from buyer.db import one
from buyer.policy import validate
from tests.test_policy import ALL_TOOLS


def run(sid, actions, demand, verdict="modify"):
    sit = seed.load(sid)["situation"]
    decision = {"verdict": verdict, "demand_per_day": demand, "actions": actions}
    report = validate(decision, sit, ALL_TOOLS)
    assert report["ok"], report["errors"]
    results = execute(0, decision, sit)
    return results, verify(decision, results, sit, report["plan"])


def test_full_confirmation_verifies():
    results, check = run("s1_modify_open_po", [{"type": "create_po", "supplier_id": "SUP-ANDINA", "qty": 240}], 60)
    assert results[0]["supplier"]["status"] == "confirmed"
    assert check["ok"] and not check["deviations"]


def test_stale_quote_short_confirmation_is_caught():
    results, check = run("s2_recovery_stale_quote",
                         [{"type": "create_po", "supplier_id": "SUP-NORTE", "qty": 228}], 40)
    assert results[0]["supplier"]["qty_confirmed"] == 72
    assert not check["ok"]
    assert any("confirmed only 72 of 228" in d for d in check["deviations"])
    assert not check["covered"]


def test_concurrent_spend_is_stopped_by_the_database():
    results, check = run("s4_budget_race", [{"type": "create_po", "supplier_id": "SUP-ANDINA", "qty": 240}], 60)
    assert not results[0]["ok"] and "budget exceeded" in results[0]["error"]
    assert not check["ok"]
    assert one("SELECT remaining FROM budget_status")["remaining"] >= 0


def test_transfer_moves_stock_and_closes_gap():
    results, check = run("s2_shortfall_transfer",
                         [{"type": "amend_po", "po_id": "PO-2001", "qty": 250, "reason": "accept partial"},
                          {"type": "transfer", "from_node": "DS-USAQUEN", "qty": 240}], 50)
    assert check["ok"], check["deviations"]
    assert one("SELECT status FROM purchase_orders WHERE id = 'PO-2001'")["status"] == "confirmed"
    assert one("SELECT on_hand FROM inventory WHERE node_id = 'DS-USAQUEN'")["on_hand"] == 460


def test_investigation_with_a_known_gap_is_not_a_deviation():
    # The gate accepts this plan (a person is checking the stock first); the
    # verifier must agree with the gate instead of calling it a failure.
    _, check = run("s1_investigate_phantom",
                   [{"type": "request_cycle_count", "reason": "zero sales for 6 days"}], 50, verdict="investigate")
    assert not check["covered"] and check["human_review"]
    assert check["ok"], check["deviations"]


def test_uncovered_plan_always_names_the_deviation():
    _, check = run("s2_recovery_stale_quote",
                   [{"type": "create_po", "supplier_id": "SUP-NORTE", "qty": 228}], 40)
    assert not check["ok"] and any("coverage horizon" in d for d in check["deviations"])
