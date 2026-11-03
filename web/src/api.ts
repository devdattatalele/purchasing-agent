async function call(path: string, init?: RequestInit) {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const get = (path: string) => call(path);

export const post = (path: string, body: unknown = {}) =>
  call(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const TERMINAL = ["completed", "escalated", "failed"];

export type Scenario = { id: string; title: string; notes: string; situation: { id: string; title: string; kind: string } };

export const money = (n: number | null | undefined) =>
  n == null ? "n/a" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
