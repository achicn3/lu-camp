// Bounded rejection probes only: no valid orders, credentials, or payment transactions.
// Usage: node test/security-smoke.mjs [http://127.0.0.1:8793]
// Defaults to the demo staging Worker. Production origins are deliberately excluded.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { setTimeout as sleep } from "node:timers/promises";

const staging = "https://lu-camp-online-order-staging.noiping2.workers.dev";
const target = new URL(process.argv[2] ?? staging);
assert(
  target.origin === staging || (target.protocol === "http:" && ["localhost", "127.0.0.1"].includes(target.hostname)),
  "Only demo staging or localhost is allowed",
);
assert(target.pathname === "/" && !target.search && !target.hash && !target.username && !target.password);
const token = randomBytes(32).toString("base64url");
const results = [];
async function probe(name, path, expectedStatus, expectedError, init = {}) {
  let actualStatus = null;
  try {
    const res = await fetch(new URL(path, target), { ...init, redirect: "error", signal: AbortSignal.timeout(10_000) });
    actualStatus = res.status;
    const body = await res.json();
    assert.equal(res.status, expectedStatus);
    if (expectedError !== null) assert.equal(body.error, expectedError);
    assert.match(res.headers.get("content-security-policy") ?? "", /frame-ancestors 'none'/);
    assert.equal(res.headers.get("x-content-type-options"), "nosniff");
    results.push({ name, pass: true, status: res.status });
    return body;
  } catch (error) {
    // Never dump response bodies, order tokens, request headers or credentials.
    results.push({ name, pass: false, status: actualStatus, reason: error instanceof assert.AssertionError ? "unexpected status/body/security headers" : "request failed" });
    return null;
  }
}

const status = await probe("public status", "/api/status", 200, null);
await probe("guessed order token", `/api/orders/${token}`, 404, "not_found");
await probe("unsigned POS pull", "/integration/orders", 401, "unauthorized");
await probe("unsigned POS status update", `/integration/orders/${token}/status`, 401, "unauthorized", { method: "POST", body: "{}" });
await probe("forged POS signature", "/integration/orders", 401, "unauthorized", { headers: {
  "X-LuCamp-Timestamp": String(Math.floor(Date.now() / 1000)),
  "X-LuCamp-Nonce": randomBytes(16).toString("hex"),
  "X-LuCamp-Signature": "0".repeat(64),
} });
await probe("forged payment confirmation", `/api/orders/${token}/linepay/confirm?returnCode=0000`,
  status?.linepay ? 404 : 422, status?.linepay ? "not_found" : "linepay_unavailable",
  { method: "POST", body: '{"status":"PAID","amount":1}' });
for (const suffix of ["", "/confirm", "/cancel"]) {
  await probe(`oversized payment ${suffix || "start"}`, `/api/orders/${token}/linepay${suffix}`, 413, "payload_too_large",
    { method: "POST", body: "x".repeat(16 * 1024 + 1) });
}
await probe("oversized order", "/api/orders", 413, "payload_too_large", { method: "POST", body: "x".repeat(16 * 1024 + 1) });

// Six invalid requests per endpoint prove the 5/min guards; avoid a minute boundary.
for (const kind of ["order", "payment"]) {
  const remaining = 60_000 - Date.now() % 60_000;
  if (remaining < 10_000) await sleep(remaining + 50);
  for (let i = 0; i < 6; i++) {
    const path = kind === "order" ? "/api/orders" : `/api/orders/${token}/linepay`;
    const normalStatus = kind === "order" || !status?.linepay ? 422 : 404;
    const normalError = kind === "order" ? "invalid_request" : status?.linepay ? "not_found" : "linepay_unavailable";
    try {
      const response = await fetch(new URL(path, target), {
        method: "POST", body: "{}", redirect: "error", signal: AbortSignal.timeout(10_000),
      });
      const body = await response.json();
      const limited = response.status === 429 && body.error === "rate_limited";
      const pass = i === 5 ? limited : limited || (response.status === normalStatus && body.error === normalError);
      results.push({ name: `invalid-${kind} rate probe ${i + 1}`, pass, status: response.status });
    } catch {
      results.push({ name: `invalid-${kind} rate probe ${i + 1}`, pass: false, reason: "request failed" });
    }
  }
}
console.log(JSON.stringify({
  target: target.origin, at: new Date().toISOString(), results,
  passed: results.filter((r) => r.pass).length, total: results.length,
  limitations: [
    "Does not create orders, reserve stock, charge payments, or modify POS state.",
    "Valid-signature replay, price tampering, stock exhaustion and browser XSS require isolated fixtures.",
    "Turnstile real-token rejection/replay must be tested separately; dummy keys cannot prove it.",
    "Invalid-request probes increment this IP's minute counters; allow a minute before manual demo orders.",
  ],
}, null, 2));
process.exitCode = results.every((r) => r.pass) ? 0 : 1;
