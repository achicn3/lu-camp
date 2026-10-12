import { env, exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sha256Hex } from "../src/http";
import { reconcileLinePay } from "../src/linepay-orders";

const TOKEN = "a".repeat(43);
const ORDER_ID = "b".repeat(32);
const NOW = 1_800_000_000_000;
let authorized = false;
let confirmCalls = 0;
let checkCalls = 0;

const confirm = () => exports.default.fetch(new Request(
  `https://order.test/api/orders/${TOKEN}/linepay/confirm`, { method: "POST" },
));

beforeEach(async () => {
  authorized = false;
  confirmCalls = 0;
  checkCalls = 0;
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.endsWith("/confirm")) {
      confirmCalls += 1;
      return Response.json({ returnCode: authorized ? "0000" : "1150" });
    }
    if (url.endsWith("/check")) {
      checkCalls += 1;
      return Response.json({ returnCode: authorized ? "0110" : "0000" });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  });
  await env.DB.prepare(
    "INSERT INTO orders (id, store_id, token_hash, idem_key, fingerprint, device_id, ip_hash, " +
      "service_mode, menu_version, total, payment_method, payment_status, sync_status, hold_status, " +
      "created_at, updated_at, linepay_attempt, linepay_transaction_id) " +
      "VALUES (?, 1, ?, 'confirm-security', 'fingerprint', 'device', 'ip', 'TAKEOUT', 1, 150, " +
      "'LINE_PAY', 'PENDING', 'IMPORTED', 'NONE', ?, ?, 1, '2026100102385323710')",
  ).bind(ORDER_ID, await sha256Hex(new TextEncoder().encode(TOKEN)), NOW, NOW).run();
});
afterEach(() => vi.restoreAllMocks());

describe("Public LINE Pay confirm abuse", () => {
  it("bounds repeated unapproved confirms independently of IP or start-payment limits", async () => {
    for (let i = 0; i < 8; i++) {
      const response = await confirm();
      expect(response.status).toBe(202);
      expect(await response.json()).toEqual({ status: "PENDING" });
    }
    expect(confirmCalls).toBe(1);
    expect(checkCalls).toBe(1);
  });

  it("atomically bounds concurrent confirmations", async () => {
    const responses = await Promise.all(Array.from({ length: 8 }, () => confirm()));
    expect(responses.every((response) => response.status === 202)).toBe(true);
    expect(confirmCalls).toBe(1);
    expect(checkCalls).toBe(1);
  });

  it("does not delay the first authorized callback after a recent POS check", async () => {
    await env.DB.prepare("UPDATE orders SET linepay_checked_at = ?").bind(NOW).run();
    authorized = true;
    expect(await (await confirm()).json()).toEqual({ status: "PAID" });
    expect(await (await confirm()).json()).toEqual({ status: "PAID" });
    expect(confirmCalls).toBe(1);
  });

  it("allows authorization to complete on the next window", async () => {
    await confirm();
    authorized = true;
    expect(await (await confirm()).json()).toEqual({ status: "PENDING" });
    vi.mocked(Date.now).mockReturnValue(NOW + 30_000);
    expect(await (await confirm()).json()).toEqual({ status: "PAID" });
    expect(confirmCalls).toBe(2);
  });

  it("leaves POS reconciliation available after a throttled callback", async () => {
    await confirm();
    authorized = true;
    await confirm();
    vi.mocked(Date.now).mockReturnValue(NOW + 30_001);
    await reconcileLinePay(env, 1);
    expect(await (await confirm()).json()).toEqual({ status: "PAID" });
    expect(confirmCalls).toBe(2);
  });

  it("a different payment attempt has its own first callback", async () => {
    await confirm();
    await env.DB.prepare("UPDATE orders SET linepay_attempt = 2").run();
    authorized = true;
    expect(await (await confirm()).json()).toEqual({ status: "PAID" });
    expect(confirmCalls).toBe(2);
  });
});
