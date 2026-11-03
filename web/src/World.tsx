import { money, Scenario } from "./api";

function SalesChart({ sales, forecast }: { sales: { day: string; units: number }[]; forecast: number }) {
  const W = 300, H = 80, pad = 4;
  const max = Math.max(forecast, ...sales.map((s) => s.units)) * 1.15 || 1;
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, sales.length - 1);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  const line = sales.map((s, i) => `${x(i).toFixed(1)},${y(s.units).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
         aria-label={`Daily sales for the last ${sales.length} days against a forecast of ${forecast} per day`}>
      <rect x={x(sales.length - 7)} y={0} width={x(sales.length - 1) - x(sales.length - 7)} height={H} className="chart-recent" />
      <line x1={pad} x2={W - pad} y1={y(forecast)} y2={y(forecast)} className="chart-forecast" />
      <polyline points={line} className="chart-sales" />
    </svg>
  );
}

export default function World({ world, scenario }: { world: any; scenario?: Scenario }) {
  const { item, projection: p, situation } = world;
  const last7 = world.sales.slice(-7).reduce((a: number, s: any) => a + s.units, 0) / 7;
  const budget = world.budget.find((b: any) => b.category === item.category);
  const rec = world.recommendation;

  return (
    <>
      <div className="panel">
        <h2>Situation · {situation.kind.replace(/_/g, " ")}</h2>
        <div className="title">{situation.title}</div>
        <p>{situation.description}</p>
        {rec && (
          <p className="callout">
            <b>{rec.id}</b> from {rec.source}: buy <span className="num">{rec.qty}</span> from {rec.supplier_id}. “{rec.reason}”
          </p>
        )}
        {world.supplier_events.map((e: any) => (
          <p key={e.id} className="callout">
            <b>{e.po_id}</b> supplier message: “{e.message}”
          </p>
        ))}
        {world.signals.map((s: any) => (
          <p key={s.id} className="callout">
            <b>{s.kind}</b> {s.start_day} to {s.end_day}: {s.description}
          </p>
        ))}
        {scenario && (
          <details>
            <summary>Answer key (hand computed, used by the eval)</summary>
            <p className="small">{scenario.notes}</p>
          </details>
        )}
      </div>

      <div className="panel">
        <h2>{item.name} · {item.sku} · {situation.node_id} · today {world.today}</h2>
        <div className="kpis">
          <Kpi label="On hand" value={item.on_hand} />
          <Kpi label="Capacity" value={item.capacity} />
          <Kpi label="Forecast / day" value={item.forecast} />
          <Kpi label="Sold / day, last 7" value={last7.toFixed(1)} />
          <Kpi label="Runs out (forecast)" value={`day ${p.first_stockout_day}`} />
          <Kpi label="Horizon" value={`${item.horizon} d`} />
          <Kpi label="Last count" value={item.last_counted} />
          {budget && <Kpi label={`Budget left · ${budget.category}`} value={money(budget.remaining)} />}
        </div>
        <SalesChart sales={world.sales} forecast={item.forecast} />
        <div className="legend small muted">28 days of sales · dashed line forecast · shaded last 7 days</div>
      </div>

      <div className="panel">
        <h2>Purchase orders and transfers</h2>
        <table>
          <thead><tr><th>PO</th><th>Supplier</th><th className="r">Qty</th><th className="r">Confirmed</th><th>Status</th><th>ETA</th><th>By</th></tr></thead>
          <tbody>
            {world.purchase_orders.map((po: any) => (
              <tr key={po.id} className={po.created_by === "agent" ? "agent-row" : ""}>
                <td>{po.id}</td><td>{po.supplier_id}</td><td className="r">{po.qty}</td>
                <td className="r">{po.qty_confirmed ?? "n/a"}</td><td><span className={`chip ${statusTone(po.status)}`}>{po.status}</span></td>
                <td>{po.eta ?? "n/a"}</td><td>{po.created_by}</td>
              </tr>
            ))}
            {world.transfers.map((t: any) => (
              <tr key={t.id} className="agent-row">
                <td>{t.id}</td><td>from {t.from_node}</td><td className="r">{t.qty}</td><td className="r">{t.qty}</td>
                <td><span className="chip">transfer</span></td><td>{t.eta}</td><td>agent</td>
              </tr>
            ))}
            {!world.purchase_orders.length && !world.transfers.length && <tr><td colSpan={7} className="muted">None</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Suppliers</h2>
        <table>
          <thead><tr><th>Supplier</th><th className="r">Price</th><th className="r">MOQ</th><th className="r">Case</th><th className="r">Lead</th><th className="r">Fill rate</th></tr></thead>
          <tbody>
            {world.suppliers.map((s: any) => (
              <tr key={s.supplier_id}>
                <td>{s.supplier_id}{!s.active && " (inactive)"}</td><td className="r">{money(s.unit_price)}</td>
                <td className="r">{s.moq}</td><td className="r">{s.case_pack}</td><td className="r">{s.lead_time_days}d</td>
                <td className="r">{Math.round(s.fill_rate * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        {world.network.length > 1 && (
          <p className="small muted">
            Network stock: {world.network.map((n: any) => `${n.node_id} ${n.on_hand}`).join(" · ")}
          </p>
        )}
      </div>

      {world.tasks.length > 0 && (
        <div className="panel">
          <h2>Tasks for people</h2>
          {world.tasks.map((t: any) => (
            <p key={t.id}><span className="chip warn">{t.kind}</span> {t.detail}</p>
          ))}
        </div>
      )}
    </>
  );
}

function Kpi({ label, value }: { label: string; value: any }) {
  return (
    <div className="kpi">
      <div className="v">{value ?? "n/a"}</div>
      <div className="l">{label}</div>
    </div>
  );
}

export function statusTone(s: string) {
  if (["confirmed", "completed", "received", "approved", "passed"].includes(s)) return "ok";
  if (["partially_confirmed", "awaiting_approval", "escalated", "submitted", "pending"].includes(s)) return "warn";
  if (["rejected", "cancelled", "failed"].includes(s)) return "bad";
  return "";
}
