-- Purchasing world for one scenario. Rebuilt from a fixture on every reset.
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

-- Simulation clock: day 0 of every projection.
CREATE TABLE world (
    id       int PRIMARY KEY CHECK (id = 1),
    today    date NOT NULL,
    scenario text NOT NULL
);

CREATE TABLE nodes (
    id   text PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE products (
    sku             text PRIMARY KEY,
    name            text NOT NULL,
    category        text NOT NULL,
    unit_cost       numeric(10, 2) NOT NULL CHECK (unit_cost > 0),
    shelf_life_days int CHECK (shelf_life_days > 0),
    safety_days     int NOT NULL DEFAULT 3,
    review_days     int NOT NULL DEFAULT 7,
    max_cover_days  int NOT NULL DEFAULT 30
);

CREATE TABLE inventory (
    node_id      text REFERENCES nodes,
    sku          text REFERENCES products,
    on_hand      int NOT NULL CHECK (on_hand >= 0),
    capacity     int NOT NULL CHECK (capacity > 0),
    last_counted date NOT NULL,
    PRIMARY KEY (node_id, sku)
);

CREATE TABLE forecast (
    node_id       text REFERENCES nodes,
    sku           text REFERENCES products,
    units_per_day numeric(10, 2) NOT NULL CHECK (units_per_day >= 0),
    PRIMARY KEY (node_id, sku)
);

CREATE TABLE sales_daily (
    node_id text REFERENCES nodes,
    sku     text REFERENCES products,
    day     date NOT NULL,
    units   int NOT NULL CHECK (units >= 0),
    PRIMARY KEY (node_id, sku, day)
);

-- Promotions, weather, bulk orders: context that explains a demand change.
CREATE TABLE demand_signals (
    id          serial PRIMARY KEY,
    node_id     text REFERENCES nodes,
    sku         text REFERENCES products,
    kind        text NOT NULL,
    description text NOT NULL,
    start_day   date NOT NULL,
    end_day     date NOT NULL
);

CREATE TABLE suppliers (
    id        text PRIMARY KEY,
    name      text NOT NULL,
    active    bool NOT NULL DEFAULT true,
    fill_rate numeric(4, 3) NOT NULL
);

-- available_qty is what the supplier will really confirm. quoted_qty is what
-- its portal reports, which can be stale.
CREATE TABLE supplier_items (
    supplier_id    text REFERENCES suppliers,
    sku            text REFERENCES products,
    unit_price     numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    moq            int NOT NULL CHECK (moq > 0),
    case_pack      int NOT NULL CHECK (case_pack > 0),
    lead_time_days int NOT NULL CHECK (lead_time_days >= 0),
    available_qty  int NOT NULL CHECK (available_qty >= 0),
    quoted_qty     int CHECK (quoted_qty >= 0),
    PRIMARY KEY (supplier_id, sku)
);

CREATE TABLE budgets (
    node_id      text REFERENCES nodes,
    category     text NOT NULL,
    period_start date NOT NULL,
    period_end   date NOT NULL,
    amount       numeric(12, 2) NOT NULL CHECK (amount >= 0),
    next_amount  numeric(12, 2),
    PRIMARY KEY (node_id, category)
);

-- Single line POs keep the model small; a real PO has many lines.
CREATE TABLE purchase_orders (
    id            text PRIMARY KEY,
    node_id       text NOT NULL REFERENCES nodes,
    supplier_id   text NOT NULL REFERENCES suppliers,
    sku           text NOT NULL REFERENCES products,
    qty           int NOT NULL CHECK (qty > 0),
    qty_confirmed int CHECK (qty_confirmed >= 0 AND qty_confirmed <= qty),
    unit_price    numeric(10, 2) NOT NULL CHECK (unit_price > 0),
    status        text NOT NULL CHECK (status IN
                  ('submitted', 'confirmed', 'partially_confirmed', 'rejected', 'cancelled', 'received')),
    order_date    date NOT NULL,
    eta           date,
    created_by    text NOT NULL DEFAULT 'buyer',
    run_id        int,
    note          text
);

CREATE SEQUENCE agent_doc_seq START 5001;

CREATE TABLE transfers (
    id        text PRIMARY KEY,
    sku       text NOT NULL REFERENCES products,
    from_node text NOT NULL REFERENCES nodes,
    to_node   text NOT NULL REFERENCES nodes CHECK (to_node <> from_node),
    qty       int NOT NULL CHECK (qty > 0),
    eta       date NOT NULL,
    run_id    int
);

CREATE TABLE recommendations (
    id      text PRIMARY KEY,
    node_id text NOT NULL REFERENCES nodes,
    sku     text NOT NULL REFERENCES products,
    supplier_id text REFERENCES suppliers,
    qty     int NOT NULL CHECK (qty > 0),
    source  text NOT NULL,
    reason  text NOT NULL,
    status  text NOT NULL DEFAULT 'open'
);

CREATE TABLE supplier_events (
    id        serial PRIMARY KEY,
    po_id     text NOT NULL REFERENCES purchase_orders,
    kind      text NOT NULL,
    message   text NOT NULL,
    available int
);

CREATE TABLE situations (
    id          text PRIMARY KEY,
    kind        text NOT NULL,
    title       text NOT NULL,
    description text NOT NULL,
    node_id     text NOT NULL REFERENCES nodes,
    sku         text NOT NULL REFERENCES products,
    ref_id      text
);

-- Work the agent hands to people: cycle counts, escalations.
CREATE TABLE tasks (
    id      serial PRIMARY KEY,
    run_id  int,
    kind    text NOT NULL CHECK (kind IN ('cycle_count', 'escalation')),
    node_id text,
    sku     text,
    detail  text NOT NULL,
    status  text NOT NULL DEFAULT 'open'
);

CREATE TABLE agent_runs (
    id           serial PRIMARY KEY,
    situation_id text NOT NULL REFERENCES situations,
    mode         text NOT NULL DEFAULT 'agent',
    model        text,
    status       text NOT NULL,
    state        jsonb,
    decision     jsonb,
    outcome      jsonb,
    tokens       int NOT NULL DEFAULT 0,
    error        text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

CREATE TABLE agent_steps (
    id      bigserial PRIMARY KEY,
    run_id  int NOT NULL REFERENCES agent_runs,
    kind    text NOT NULL,
    title   text NOT NULL,
    payload jsonb,
    at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id         serial PRIMARY KEY,
    run_id     int NOT NULL REFERENCES agent_runs,
    proposal   jsonb NOT NULL,
    reasons    jsonb NOT NULL,
    status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    note       text,
    decided_at timestamptz
);

-- Committed spend per budget line, the same number the trigger enforces.
CREATE VIEW budget_status AS
SELECT b.node_id, b.category, b.period_start, b.period_end, b.amount, b.next_amount,
       coalesce(sum(coalesce(po.qty_confirmed, po.qty) * po.unit_price), 0) AS committed,
       b.amount - coalesce(sum(coalesce(po.qty_confirmed, po.qty) * po.unit_price), 0) AS remaining
FROM budgets b
LEFT JOIN products p ON p.category = b.category
LEFT JOIN purchase_orders po ON po.sku = p.sku AND po.node_id = b.node_id
     AND po.status NOT IN ('rejected', 'cancelled')
     AND po.order_date BETWEEN b.period_start AND b.period_end
GROUP BY b.node_id, b.category;

-- Last line of defence: no PO write can push a budget line over its amount,
-- whatever the agent or the validator believed. The budget row lock
-- serialises concurrent writers on the same line.
CREATE FUNCTION enforce_budget() RETURNS trigger AS $$
DECLARE
    b         budgets%ROWTYPE;
    committed numeric;
    new_value numeric := coalesce(NEW.qty_confirmed, NEW.qty) * NEW.unit_price;
BEGIN
    IF NEW.status IN ('rejected', 'cancelled') THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status NOT IN ('rejected', 'cancelled')
       AND new_value <= coalesce(OLD.qty_confirmed, OLD.qty) * OLD.unit_price THEN
        RETURN NEW;
    END IF;
    SELECT bu.* INTO b FROM budgets bu JOIN products p ON p.category = bu.category
    WHERE bu.node_id = NEW.node_id AND p.sku = NEW.sku
      AND NEW.order_date BETWEEN bu.period_start AND bu.period_end
    FOR UPDATE OF bu;
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    SELECT coalesce(sum(coalesce(po.qty_confirmed, po.qty) * po.unit_price), 0) INTO committed
    FROM purchase_orders po JOIN products p ON p.sku = po.sku
    WHERE po.node_id = NEW.node_id AND p.category = b.category AND po.id <> NEW.id
      AND po.status NOT IN ('rejected', 'cancelled')
      AND po.order_date BETWEEN b.period_start AND b.period_end;
    IF committed + new_value > b.amount THEN
        RAISE EXCEPTION 'budget exceeded on %/%: committed %, this PO %, budget %',
            b.node_id, b.category, committed, new_value, b.amount
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER po_budget BEFORE INSERT OR UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION enforce_budget();
