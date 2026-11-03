# Eval: naive

0/12 runs pass every check.

| scenario | verdict | decision | evidence | constraints | action | validated | recovery | run |
|---|---|---|---|---|---|---|---|---|
| s1_accept_recommendation | accept | pass | FAIL | pass | pass | FAIL | pass | FAIL |
| s1_investigate_phantom | accept | FAIL | FAIL | FAIL | FAIL | FAIL | pass | FAIL |
| s1_modify_open_po | accept | FAIL | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s1_reject_overstock | accept | FAIL | FAIL | FAIL | FAIL | FAIL | pass | FAIL |
| s2_recovery_stale_quote | accept | FAIL | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s2_shortfall_sufficient | accept | pass | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s2_shortfall_transfer | accept | FAIL | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s3_demand_spike | accept | FAIL | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s3_spike_promo_ended | accept | pass | FAIL | pass | pass | FAIL | pass | FAIL |
| s4_budget_cap | accept | FAIL | FAIL | pass | FAIL | FAIL | pass | FAIL |
| s4_budget_race | accept | pass | FAIL | pass | pass | FAIL | pass | FAIL |
| s4_moq_perishable | accept | FAIL | FAIL | FAIL | FAIL | FAIL | pass | FAIL |

## Per check pass rate

- decision: 4/12
- evidence: 0/12
- constraints: 9/12
- action: 3/12
- validated: 0/12
- recovery: 12/12

## Details

**s1_accept_recommendation** (completed, 0 tokens): Executed upstream instruction.
- decision: pass, accept (want accept/modify)
- evidence: FAIL, missing get_budget, get_item_snapshot, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: pass, took ['create_po']
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s1_investigate_phantom** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want investigate/escalate)
- evidence: FAIL, missing get_demand, get_item_snapshot
- constraints: FAIL, PO-5001 breaks MOQ/case pack
- action: FAIL, needed one of ['request_cycle_count']; ordered 800, want 0 to 400
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s1_modify_open_po** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify)
- evidence: FAIL, missing get_budget, get_item_snapshot, get_open_purchase_orders, get_supplier_options
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, needed one of ['create_po']; did not mention PO-1001
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s1_reject_overstock** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want reject)
- evidence: FAIL, missing get_demand, get_item_snapshot, get_open_purchase_orders
- constraints: FAIL, storage peak 1345 > 700; PO-5001 breaks MOQ/case pack
- action: FAIL, took forbidden create_po
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s2_recovery_stale_quote** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify/escalate)
- evidence: FAIL, missing get_item_snapshot, get_network_stock, get_open_purchase_orders, get_supplier_options
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, final cover 7d < 10d at true demand 40
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s2_shortfall_sufficient** (completed, 0 tokens): Executed upstream instruction.
- decision: pass, accept (want accept/modify)
- evidence: FAIL, missing get_demand, get_item_snapshot, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, took forbidden create_po
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s2_shortfall_transfer** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify)
- evidence: FAIL, missing get_demand, get_item_snapshot, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, final cover 8d < 10d at true demand 50
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s3_demand_spike** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify)
- evidence: FAIL, missing get_demand, get_item_snapshot, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, needed one of ['create_po']; demand basis 40.0 < 80; final cover 7d < 10d at true demand 100
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s3_spike_promo_ended** (completed, 0 tokens): Executed upstream instruction.
- decision: pass, accept (want accept)
- evidence: FAIL, missing get_demand, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: pass, took no action
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s4_budget_cap** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify/escalate)
- evidence: FAIL, missing get_budget, get_item_snapshot, get_supplier_options
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: FAIL, needed one of ['create_po']; needed one of ['escalate']
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation

**s4_budget_race** (completed, 0 tokens): Executed upstream instruction.
- decision: pass, not graded
- evidence: FAIL, missing get_budget, get_open_purchase_orders
- constraints: pass, budget, storage, MOQ, shelf life ok
- action: pass, took no action
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation (one was expected)

**s4_moq_perishable** (completed, 0 tokens): Executed upstream instruction.
- decision: FAIL, accept (want modify)
- evidence: FAIL, missing get_item_snapshot, get_supplier_options
- constraints: FAIL, about 144 units expire
- action: FAIL, supplier ['SUP-ANDINA'] not in ['SUP-LACTEOS']; ordered 240, want 48 to 96; final cover 22d > 10d; approval not asked, want asked
- validated: FAIL, 0 verification(s), run completed
- recovery: pass, n/a, no deviation
