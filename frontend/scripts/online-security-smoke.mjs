// Active adversarial smoke: isolated local Worker/D1 + real backend/Postgres.
// Creates dedicated items/orders. Requires SMOKE_ALLOW_WRITE=1, local origins only.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

assert.equal(process.env.SMOKE_ALLOW_WRITE, "1");
const API = process.env.SMOKE_API ?? "http://127.0.0.1:8116";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8794";
for (const origin of [API, ORDER]) {
  const url = new URL(origin);
  assert.equal(url.protocol, "http:");
  assert.ok(["127.0.0.1", "localhost"].includes(url.hostname), "Isolated local environments only");
}
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/lukeng-security-shots";
mkdirSync(SHOTS, { recursive: true });
const findings = [];
let auth = "";
async function api(method, path, body) {
  const res = await fetch(`${API}/api/v1${path}`, {
    method, headers: { "Content-Type": "application/json", Authorization: `Bearer ${auth}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.ok(res.ok, `${method} ${path}: ${res.status}`);
  return res.json();
}
async function until(fn, label) {
  for (let i = 0; i < 80; i++) {
    const value = await fn();
    if (value) return value;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Timeout: ${label}`);
}
let client = 0;
const run = randomUUID().slice(0, 6);
function submit(item, note = "", extra = {}) {
  return fetch(`${ORDER}/api/orders`, {
    method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": `10.${parseInt(run.slice(0, 2), 16)}.0.${++client}` },
    body: JSON.stringify({ idempotency_key: randomUUID(), table_code: null,
      payment_method: "CASH", turnstile_token: "XXXX.DUMMY.TOKEN.XXXX", note,
      lines: [{ item_id: item, option_ids: [], qty: 1 }], ...extra }),
  });
}
const cleanup = [];
auth = (await api("POST", "/auth/login", {
  username: process.env.SMOKE_USERNAME ?? "dev-manager",
  password: process.env.SMOKE_PASSWORD ?? "dev-test-123456",
})).access_token;
const item = await api("POST", "/menu-items", { name: `security-coffee-${run}`, unit_price: "150", category: "security" });
const scarce = await api("POST", "/menu-items", { name: `security-last-${run}`, unit_price: "90", category: "security" });
await api("PATCH", `/menu-items/${scarce.id}`, { daily_limited: true });
await api("POST", `/menu-daily-stock/item/${scarce.id}/set`, { qty: 1, expected_remaining: 0 });
await api("POST", "/online-order/publish");
await api("PUT", "/online-orders/accepting", { accepting: true });
await until(async () => (await (await fetch(`${ORDER}/api/status`)).json()).accepting, "POS heartbeat");
let browser;
try {
  for (const note of ["bad\u0000note", "bad\ud800note"]) {
    const res = await submit(item.id, note);
    assert.equal(res.status, 422);
    assert.equal((await res.json()).error, "invalid_request");
  }
  findings.push("NUL and lone surrogate rejected by real Worker before POS import");

  for (const qty of [-1, 0, 0.5, 11]) {
    const res = await submit(item.id, "", { lines: [{ item_id: item.id, option_ids: [], qty }] });
    assert.equal(res.status, 422);
    assert.equal((await res.json()).error, "invalid_qty");
  }
  findings.push("Negative, zero, fractional and excessive quantities rejected");

  const xss = `<img src=x onerror="window.__security_xss=1"> ${run}`;
  const sqlNote = `'; DROP TABLE online_orders; -- ${run}`;
  for (const note of ["少冰", xss, sqlNote]) {
    const rejected = await submit(item.id, note);
    assert.equal(rejected.status, 422);
    assert.equal((await rejected.json()).error, "notes_not_allowed");
  }
  findings.push("Normal, XSS and SQL notes rejected before order creation");
  const response = await submit(item.id, "", {
    total: 1, payment_status: "PAID", linepay_transaction_id: "1",
    lines: [{ item_id: item.id, option_ids: [], qty: 1, unit_price: 1 }],
  });
  assert.equal(response.status, 201);
  const created = await response.json();
  assert.equal(created.total, 150);
  assert.equal(created.status, "UNPAID");
  const pos = await until(async () => (await api("GET", "/online-orders")).orders.find((o) => o.lines.some((line) => line.item_id === item.id)), "note-free order import");
  cleanup.push(pos.id);
  assert.equal(pos.total, "150");
  assert.equal(pos.payment_status, "UNPAID");
  assert.equal(pos.linepay_paid, false);
  findings.push("Forged price/payment fields ignored through real POS import; total150 and unpaid");

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(`${ORDER}/order/${created.token}`, { waitUntil: "networkidle" });
  await page.getByText("合計 $150").waitFor();
  assert.equal(pos.note, null);
  assert.equal(await page.getByText(xss, { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => window.__security_xss), undefined);
  assert.equal(await page.locator('img[onerror]').count(), 0);
  await page.screenshot({ path: `${SHOTS}/guest-no-note.png`, fullPage: true });
  findings.push("Note-free guest order renders correctly; rejected XSS never reaches POS");

  const attempts = await Promise.all([submit(scarce.id), submit(scarce.id)]);
  for (const res of attempts) assert.equal(res.status, 201);
  const raced = await until(async () => {
    const rows = (await api("GET", "/online-orders")).orders.filter((o) => o.lines.some((line) => line.item_id === scarce.id));
    return rows.length === 2 && rows.every((o) => ["HELD", "REJECTED"].includes(o.hold_status)) ? rows : null;
  }, "last item holds");
  assert.deepEqual(raced.map((o) => o.hold_status).sort(), ["HELD", "REJECTED"]);
  const held = raced.find((o) => o.hold_status === "HELD");
  cleanup.push(held.id);
  const remaining = async () => (await api("GET", "/menu-items")).find((i) => i.id === scarce.id).remaining;
  assert.equal(await remaining(), 0);
  await api("POST", `/online-orders/${held.id}/cancel`);
  cleanup.splice(cleanup.indexOf(held.id), 1);
  assert.equal(await remaining(), 1);
  findings.push("Two clients compete for last unit: one held, one rejected; stock0 then restored1 after staff cancellation");
  console.log(JSON.stringify({ passed: findings.length, findings, screenshots: SHOTS }, null, 2));
} finally {
  if (browser) await browser.close();
  for (const id of cleanup) await api("POST", `/online-orders/${id}/cancel`);
}
