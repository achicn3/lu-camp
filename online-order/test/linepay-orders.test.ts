// 線上 LINE Pay 付款（docs/44 §4.4.2；O5a）：要付款連結 → 導回請款 → 補查；C2、C7、C10。
// LINE Pay 用假伺服器（攔 fetch）；真沙盒驗收由店主手機掃碼配合（§4.4.1）。
import { env, exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SNAPSHOT, integration } from "./helpers";

type Tx = { amount: number; state: "reserved" | "authorized" | "captured"; orderId: string };
const fake = {
  txs: new Map<string, Tx>(),
  next: 2026100102385323700n,
  confirmCalls: 0,
  confirmFails: false, // 模擬請款回應遺失
};

function lineReply(body: string): Response {
  return new Response(body, { headers: { "Content-Type": "application/json" } });
}

beforeEach(async () => {
  fake.txs.clear();
  fake.confirmCalls = 0;
  fake.confirmFails = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.startsWith("https://challenges.cloudflare.com/")) return Response.json({ success: true });
    if (!url.startsWith("https://linepay.test/")) throw new Error(`unexpected fetch ${url}`);
    const path = new URL(url).pathname;
    if (path === "/v4/payments/request") {
      const req = JSON.parse(String(init?.body)) as { amount: number; orderId: string };
      const id = String(++fake.next);
      fake.txs.set(id, { amount: req.amount, state: "reserved", orderId: req.orderId });
      // 數字形式的 transactionId（真的 LINE Pay 就是這樣）
      return lineReply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${id},` +
        `"paymentUrl":{"web":"https://pay.test/${id}","app":"line://pay/${id}"}}}`);
    }
    const confirm = /^\/v4\/payments\/(\d+)\/confirm$/.exec(path);
    if (confirm) {
      fake.confirmCalls += 1;
      const tx = fake.txs.get(confirm[1]!);
      if (fake.confirmFails) {
        if (tx) tx.state = "captured"; // 錢收了，但回應沒回來
        throw new Error("timeout");
      }
      const body = JSON.parse(String(init?.body)) as { amount: number };
      if (!tx || tx.state !== "authorized" || body.amount !== tx.amount) {
        return lineReply('{"returnCode":"1150","returnMessage":"transaction not found or not authorized"}');
      }
      tx.state = "captured";
      return lineReply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${confirm[1]}}}`);
    }
    const check = /^\/v4\/payments\/requests\/(\d+)\/check$/.exec(path);
    if (check) {
      const tx = fake.txs.get(check[1]!);
      if (!tx) return lineReply('{"returnCode":"1150","returnMessage":"no transaction"}');
      const code = { reserved: "0000", authorized: "0110", captured: "0123" }[tx.state];
      return lineReply(`{"returnCode":"${code}","returnMessage":"ok"}`);
    }
    throw new Error(`unexpected LINE Pay call ${path}`);
  });
  await integration("PUT", "/integration/menu", JSON.stringify({
    ...SNAPSHOT,
    items: [
      { ...SNAPSHOT.items[0], id: 5, name: "拿鐵", unit_price: 150, option_groups: [] },
      { ...SNAPSHOT.items[0], id: 6, name: "戚風", unit_price: 90, remaining: 2, option_groups: [] },
    ],
  }));
  await integration("PUT", "/integration/store-status", JSON.stringify({ accepting: true }));
  await integration("GET", "/integration/orders");
});
afterEach(() => vi.restoreAllMocks());

let ip = 0;
async function place(extra: object = {}, lines = [{ item_id: 5, option_ids: [], qty: 1 }]) {
  const resp = await exports.default.fetch(new Request("https://order.test/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": `10.5.0.${++ip}` },
    body: JSON.stringify({
      idempotency_key: crypto.randomUUID(), table_code: null, payment_method: "LINE_PAY",
      turnstile_token: "tok", note: "", lines, ...extra,
    }),
  }));
  return { status: resp.status, body: (await resp.json()) as { token: string; status: string; error?: string } };
}
const post = (path: string) => exports.default.fetch(new Request(`https://order.test${path}`, { method: "POST" }));
const view = async (token: string) =>
  (await (await exports.default.fetch(new Request(`https://order.test/api/orders/${token}`))).json()) as Record<string, unknown>;
const authorize = (n = 0) => { [...fake.txs.values()][n]!.state = "authorized"; };
type Pulled = { orders: { id: string; payment_status: string; payment?: Record<string, unknown>; invoice?: Record<string, unknown> }[] };
const pull = async () => (await (await integration("GET", "/integration/orders")).json()) as Pulled;

describe("線上 LINE Pay", () => {
  it("店家狀態告訴客人頁可以用 LINE Pay", async () => {
    const st = (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as { linepay: boolean };
    expect(st.linepay).toBe(true);
  });

  it("要付款連結 → 客人授權 → 導回請款：已付款；導回兩次只請款一次（C2）", async () => {
    const { body } = await place({ invoice: { carrier: "/ABC+123" } });
    const started = await post(`/api/orders/${body.token}/linepay`);
    expect(started.status).toBe(200);
    const { payment_url } = (await started.json()) as { payment_url: string };
    expect(payment_url).toMatch(/^https:\/\/pay\.test\/\d{19}$/);
    expect(await view(body.token)).toMatchObject({ status: "PENDING", payment_method: "LINE_PAY" });
    const sent = JSON.parse(String((vi.mocked(fetch).mock.calls.find(([u]) => String(u).endsWith("/request"))?.[1] as RequestInit).body)) as {
      redirectUrls: { confirmUrl: string; cancelUrl: string }; amount: number;
    };
    expect(sent.amount).toBe(150);
    expect(sent.redirectUrls.confirmUrl).toBe(`https://order.test/order/${body.token}?linepay=return`);
    authorize();
    const confirmed = await post(`/api/orders/${body.token}/linepay/confirm`);
    expect(confirmed.status).toBe(200);
    expect(await view(body.token)).toMatchObject({ status: "PAID" });
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(200);
    expect(fake.confirmCalls).toBe(1);
    // POS 拉單看得到付款資料（交易號字串）與發票載具
    const order = (await pull()).orders.find((o) => o.payment_status === "PAID")!;
    expect(order.payment).toEqual({
      method: "LINE_PAY", transaction_id: [...fake.txs.keys()][0], order_id: `${order.id}-1`, amount: 150,
    });
    expect(order.invoice).toEqual({ carrier: "/ABC+123", tax_id: null });
  });

  it("請款回應遺失：維持確認中、不判失敗；POS 拉單時補查到已完成（C7）", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    authorize();
    fake.confirmFails = true;
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(202);
    expect(await view(body.token)).toMatchObject({ status: "CONFIRMING" });
    fake.confirmFails = false;
    await env.DB.prepare("UPDATE orders SET linepay_checked_at = 0").run();
    await pull();
    expect(await view(body.token)).toMatchObject({ status: "PAID" });
    expect(fake.confirmCalls).toBe(1); // 補查看到已完成，不再請款一次
  });

  it("客人授權後沒回到訂單頁：POS 拉單時補查到已授權，代為請款", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    authorize();
    await env.DB.prepare("UPDATE orders SET linepay_checked_at = 0, updated_at = 0").run();
    await pull();
    expect(await view(body.token)).toMatchObject({ status: "PAID" });
  });

  it("取消付款：回到未付款，可以重付（新的 LINE Pay 訂單號）或改到櫃檯付現", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    expect((await post(`/api/orders/${body.token}/linepay/cancel`)).status).toBe(200);
    expect(await view(body.token)).toMatchObject({ status: "UNPAID", linepay_result: "CANCELLED" });
    await post(`/api/orders/${body.token}/linepay`);
    const ids = [...fake.txs.values()].map((t) => t.orderId);
    expect(new Set(ids).size).toBe(2);
  });

  it("限量品項：POS 保留成功前不能付；保留過期就不請款、不扣錢（C10）", async () => {
    const { body } = await place({}, [{ item_id: 6, option_ids: [], qty: 1 }]);
    expect(body.status).toBe("HOLD_REQUESTED");
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(409);
    const id = (await pull()).orders[0]!.id;
    await integration("POST", `/integration/orders/${id}/status`, JSON.stringify({ sync_status: "IMPORTED", hold_status: "HELD" }));
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
    authorize();
    // POS 那邊保留到期（10 分鐘）回報 NONE
    await integration("POST", `/integration/orders/${id}/status`, JSON.stringify({ hold_status: "NONE" }));
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(409);
    expect(await view(body.token)).toMatchObject({ status: "UNPAID", linepay_result: "EXPIRED" });
    expect(fake.confirmCalls).toBe(0);
  });

  it("付款中再按一次付款：沿用原連結，不另開一筆交易（交易號不會被蓋掉，Codex O5 第一輪）", async () => {
    const { body } = await place();
    const first = (await (await post(`/api/orders/${body.token}/linepay`)).json()) as { payment_url: string };
    const again = (await (await post(`/api/orders/${body.token}/linepay`)).json()) as { payment_url: string };
    expect(again.payment_url).toBe(first.payment_url);
    expect(fake.txs.size).toBe(1);
    authorize();
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(200);
    const order = (await pull()).orders.find((o) => o.payment_status === "PAID")!;
    expect(order.payment?.transaction_id).toBe([...fake.txs.keys()][0]);
  });

  it("保留快到期（POS 回報的到期時間前 1 分鐘）就不請款，不靠 POS 的到期回報準時到（Codex O5 第一輪）", async () => {
    const { body } = await place({}, [{ item_id: 6, option_ids: [], qty: 1 }]);
    const id = (await pull()).orders[0]!.id;
    const soon = new Date(Date.now() + 30_000).toISOString(); // 剩 30 秒
    await integration("POST", `/integration/orders/${id}/status`, JSON.stringify({
      sync_status: "IMPORTED", hold_status: "HELD", hold_expires_at: soon,
    }));
    // 剩不到 1 分鐘：連付款都不開始
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(409);
    const later = new Date(Date.now() + 5 * 60_000).toISOString();
    await env.DB.prepare("UPDATE orders SET hold_expires_at = ?").bind(Date.parse(later)).run();
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
    authorize();
    // 客人付到一半保留到期了（POS 的 NONE 回報還沒到）
    await env.DB.prepare("UPDATE orders SET hold_expires_at = ?").bind(Date.now() + 10_000).run();
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(409);
    expect(fake.confirmCalls).toBe(0);
  });

  it("發票資料：手機條碼 / 開頭 8 碼、統編 8 位數字，兩個不能同時填", async () => {
    for (const invoice of [{ carrier: "ABC12345" }, { tax_id: "1234" }, { carrier: "/ABC+123", tax_id: "12345678" }]) {
      expect((await place({ invoice })).status).toBe(422);
    }
    expect((await place({ invoice: { tax_id: "12345678" } })).status).toBe(201);
  });

  it("現金單不能走 LINE Pay 付款", async () => {
    const { body } = await place({ payment_method: "CASH" });
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(409);
  });
});
