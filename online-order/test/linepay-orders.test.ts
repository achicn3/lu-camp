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
  /** 客人 LINE Pay 綁定的載具：請款回應的 merchantReference 帶回（null＝沒綁）。 */
  boundCarrier: null as string | null,
  turnstileOk: true,
  turnstileTokens: [] as string[],
};

function lineReply(body: string): Response {
  return new Response(body, { headers: { "Content-Type": "application/json" } });
}

beforeEach(async () => {
  fake.txs.clear();
  fake.confirmCalls = 0;
  fake.confirmFails = false;
  fake.boundCarrier = null;
  fake.turnstileOk = true;
  fake.turnstileTokens = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.startsWith("https://challenges.cloudflare.com/")) {
      fake.turnstileTokens.push(String((init?.body as FormData).get("response")));
      return Response.json({ success: fake.turnstileOk });
    }
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
      const reference = fake.boundCarrier === null ? ""
        : `,"merchantReference":{"affiliateCards":[{"cardType":"MOBILE_CARRIER","cardId":"${fake.boundCarrier}"}]}`;
      return lineReply(`{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":${confirm[1]}${reference}}}`);
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
  // 線上 LINE Pay 開關（店主 2026-10-20）：預設關，POS 設定打開才開放；這裡的測試都在開著時跑。
  await integration("PUT", "/integration/linepay", JSON.stringify({ enabled: true }));
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
      turnstile_token: "tok", lines, ...extra,
    }),
  }));
  return { status: resp.status, body: (await resp.json()) as { token: string; status: string; error?: string } };
}
const post = (path: string, body?: string) =>
  exports.default.fetch(new Request(`https://order.test${path}`, { method: "POST", body }));
// 重新付款（之前發起過、已取消或失敗）要附新的人機驗證（docs/44 §8.4）。
const REPAY = JSON.stringify({ turnstile_token: "repay-tok" });
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
    expect((await post(`/api/orders/${body.token}/linepay`, REPAY)).status).toBe(200);
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

  it("補查途中客人取消又重付：舊交易不能被請款記到新的付款上（Codex O5 第二輪）", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    authorize(0);
    const [oldTx] = [...fake.txs.keys()];
    // 補查查舊交易時（等 LINE Pay 回應的空檔），客人取消、又開了一筆新的付款
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith(`/requests/${oldTx}/check`)) {
        vi.mocked(fetch).mockImplementation(original);
        await post(`/api/orders/${body.token}/linepay/cancel`);
        await post(`/api/orders/${body.token}/linepay`, REPAY);
      }
      return original(input, init);
    });
    await env.DB.prepare("UPDATE orders SET linepay_checked_at = 0, updated_at = 0").run();
    await pull();
    expect(fake.confirmCalls).toBe(0); // 舊交易沒被請款
    const row = await env.DB.prepare("SELECT payment_status, linepay_attempt, linepay_transaction_id FROM orders").first<{
      payment_status: string; linepay_attempt: number; linepay_transaction_id: string;
    }>();
    expect(row).toMatchObject({ payment_status: "PENDING", linepay_attempt: 2 });
    expect(row?.linepay_transaction_id).not.toBe(oldTx);
  });

  it("卡住沒成立的已付款單再多，也擋不住新單進 POS（新單與付款更新分開名額，Codex O5 第四輪）", async () => {
    const stmts = Array.from({ length: 60 }, (_, i) => env.DB.prepare(
      "INSERT INTO orders (id, store_id, token_hash, idem_key, fingerprint, device_id, ip_hash, service_mode, menu_version, " +
        "total, payment_method, payment_status, sync_status, hold_status, created_at, updated_at, linepay_transaction_id) " +
        "VALUES (?, 1, ?, ?, 'f', 'd', 'i', 'TAKEOUT', 1, 150, 'LINE_PAY', 'PAID', 'IMPORTED', 'NONE', ?, ?, '1')",
    ).bind(i.toString(16).padStart(32, "0"), `h${i}`, `k${i}`.padEnd(16, "x"), i, i));
    await env.DB.batch(stmts);
    const { body } = await place({ payment_method: "CASH" });
    expect(body.status).toBeTruthy();
    const pulled = await pull();
    expect(pulled.orders.some((o) => o.payment_status === "UNPAID")).toBe(true);
  });

  it("發票資料：手機條碼 / 開頭 8 碼、統編 8 位數字，兩個不能同時填", async () => {
    for (const invoice of [{ carrier: "ABC12345" }, { tax_id: "1234" }, { carrier: "/ABC+123", tax_id: "12345678" }]) {
      expect((await place({ invoice })).status).toBe(422);
    }
    expect((await place({ invoice: { tax_id: "12345678" } })).status).toBe(201);
  });

  it("重新付款沒附人機驗證：拒絕，不向 LINE Pay 開新交易（docs/44 §8.4）", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    await post(`/api/orders/${body.token}/linepay/cancel`);
    const denied = await post(`/api/orders/${body.token}/linepay`);
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual({ error: "challenge_required" });
    expect(fake.txs.size).toBe(1);
    expect(await view(body.token)).toMatchObject({ status: "UNPAID", linepay_result: "CANCELLED" });
  });

  it("重新付款的人機驗證沒過：拒絕，不開新交易；驗過才開，且用的是這次的新驗證", async () => {
    const { body } = await place();
    await post(`/api/orders/${body.token}/linepay`);
    await post(`/api/orders/${body.token}/linepay/cancel`);
    fake.turnstileOk = false;
    const denied = await post(`/api/orders/${body.token}/linepay`, REPAY);
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual({ error: "challenge_failed" });
    expect(fake.txs.size).toBe(1);
    fake.turnstileOk = true;
    fake.turnstileTokens = [];
    expect((await post(`/api/orders/${body.token}/linepay`, REPAY)).status).toBe(200);
    expect(fake.turnstileTokens).toEqual(["repay-tok"]);
    expect(fake.txs.size).toBe(2);
  });

  it("第一次付款與付款中再按一次：不必另外驗證（建單時已驗過）", async () => {
    const { body } = await place();
    fake.turnstileTokens = [];
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
    expect(fake.turnstileTokens).toEqual([]);
    expect(fake.txs.size).toBe(1);
  });

  it.each(["{", "[]", JSON.stringify({ turnstile_token: "" }), JSON.stringify({ turnstile_token: 1 })])(
    "重新付款的驗證格式不對（%s）：當成沒附驗證", async (raw) => {
      const { body } = await place();
      await post(`/api/orders/${body.token}/linepay`);
      await post(`/api/orders/${body.token}/linepay/cancel`);
      const denied = await post(`/api/orders/${body.token}/linepay`, raw);
      expect(denied.status).toBe(403);
      expect(fake.txs.size).toBe(1);
    });

  it("現金單不能走 LINE Pay 付款", async () => {
    const { body } = await place({ payment_method: "CASH" });
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(409);
  });
});


// 店主 2026-10-17：客人輸入的優先；沒輸入用 LINE Pay 綁定的載具；都沒有就印紙本，付款後告訴客人。
describe("LINE Pay 綁定的載具", () => {
  async function payWith(invoice: object | undefined, bound: string | null) {
    fake.boundCarrier = bound;
    const { body } = await place(invoice === undefined ? {} : { invoice });
    expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
    authorize(fake.txs.size - 1);
    expect((await post(`/api/orders/${body.token}/linepay/confirm`)).status).toBe(200);
    const order = (await pull()).orders.find((o) => o.payment_status === "PAID")!;
    await integration("POST", `/integration/orders/${order.id}/status`, JSON.stringify({ sync_status: "IMPORTED" }));
    return { pulled: order.invoice, shown: (await view(body.token)).invoice };
  }

  it("沒輸入、LINE Pay 有綁：用綁定的載具（補上 /），客人頁說已存入綁定載具", async () => {
    expect(await payWith(undefined, "ABC+123")).toEqual({
      pulled: { carrier: "/ABC+123", tax_id: null },
      shown: { kind: "LINEPAY_CARRIER", carrier: "/ABC+123" },
    });
  });

  it("客人自己輸入的載具優先於綁定的", async () => {
    expect(await payWith({ carrier: "/XYZ.999" }, "/ABC+123")).toEqual({
      pulled: { carrier: "/XYZ.999", tax_id: null },
      shown: { kind: "CARRIER", carrier: "/XYZ.999" },
    });
  });

  it("填了統編：照統編開，不帶綁定的載具", async () => {
    expect(await payWith({ tax_id: "12345678" }, "/ABC+123")).toEqual({
      pulled: { carrier: null, tax_id: "12345678" },
      shown: { kind: "TAX_ID", tax_id: "12345678" },
    });
  });

  it("沒輸入也沒綁：印紙本", async () => {
    expect(await payWith(undefined, null)).toEqual({
      pulled: { carrier: null, tax_id: null },
      shown: { kind: "PAPER" },
    });
  });

  it("還沒付款不顯示發票去向（還不知道有沒有綁）", async () => {
    fake.boundCarrier = "/ABC+123";
    const { body } = await place();
    expect(await view(body.token)).not.toHaveProperty("invoice");
  });
});

// 店主 2026-10-20：POS 設定可以開關線上 LINE Pay（預設關）。關掉只擋新的付款，已經在付的照常完成。
describe("線上 LINE Pay 開關", () => {
  const status = async () =>
    (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as { linepay: boolean };
  const setLinePay = (enabled: unknown) => integration("PUT", "/integration/linepay", JSON.stringify({ enabled }));

  it("關掉：客人頁不顯示、不能建 LINE Pay 單；打開又可以", async () => {
    const off = await setLinePay(false);
    expect(off.status).toBe(200);
    expect(await off.json()).toMatchObject({ linepay_enabled: false, linepay_configured: true });
    expect((await status()).linepay).toBe(false);
    const denied = await place();
    expect(denied.status).toBe(422);
    expect(denied.body.error).toBe("linepay_unavailable");
    await setLinePay(true);
    expect((await status()).linepay).toBe(true);
    expect((await place()).status).toBe(201);
  });

  it("關掉後：還沒開始付的不能再付；已經授權的照常請款", async () => {
    const waiting = await place();
    const paying = await place();
    expect((await post(`/api/orders/${paying.body.token}/linepay`)).status).toBe(200);
    authorize(fake.txs.size - 1);
    await setLinePay(false);
    expect((await post(`/api/orders/${waiting.body.token}/linepay`)).status).toBe(422);
    expect((await post(`/api/orders/${paying.body.token}/linepay/confirm`)).status).toBe(200);
    expect(await view(paying.body.token)).toMatchObject({ status: "PAID" });
  });

  it("POS 拉單看得到開關與雲端有沒有設定金鑰", async () => {
    await setLinePay(false);
    const pulled = (await (await integration("GET", "/integration/orders")).json()) as Record<string, unknown>;
    expect(pulled).toMatchObject({ linepay_enabled: false, linepay_configured: true });
  });

  it("開關只收 true／false", async () => {
    for (const bad of ["yes", 1, null]) expect((await setLinePay(bad)).status).toBe(422);
  });
});

describe("付款端點防濫用（docs/44 §8.4）", () => {
  const paymentPost = (token: string, suffix = "", ip = "10.99.0.1", body?: string) =>
    exports.default.fetch(new Request(`https://order.test/api/orders/${token}/linepay${suffix}`, {
      method: "POST", headers: { "CF-Connecting-IP": ip }, body,
    }));

  it("同 IP 反覆重開付款最多每分鐘 5 次；被限流仍能完成已授權付款", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.now());
    const { body } = await place();
    for (let i = 0; i < 5; i++) {
      expect((await paymentPost(body.token, "", "10.99.0.1", i === 0 ? undefined : REPAY)).status).toBe(200);
      if (i < 4) expect((await paymentPost(body.token, "/cancel")).status).toBe(200);
    }
    const blocked = await paymentPost(body.token, "", "10.99.0.1", REPAY);
    expect(blocked.status).toBe(429);
    expect(await blocked.json()).toEqual({ error: "rate_limited" });
    expect(fake.txs.size).toBe(5);
    authorize(4);
    expect((await paymentPost(body.token, "/confirm")).status).toBe(200);
    expect((await view(body.token)).status).toBe("PAID");
    expect(fake.confirmCalls).toBe(1);
  });

  it("付款限流依 IP 與分鐘分開，送單額度不被付款重試吃掉", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.now());
    const { body } = await place();
    for (let i = 0; i < 5; i++) expect((await paymentPost(body.token)).status).toBe(200);
    expect((await paymentPost(body.token)).status).toBe(429);
    expect((await paymentPost(body.token, "", "10.99.0.2")).status).toBe(200);
    const newOrder = await exports.default.fetch(new Request("https://order.test/api/orders", {
      method: "POST", headers: { "CF-Connecting-IP": "10.99.0.1" },
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(), table_code: null, payment_method: "CASH",
        turnstile_token: "tok", lines: [{ item_id: 5, option_ids: [], qty: 1 }],
      }),
    }));
    expect(newOrder.status).toBe(201);
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now + 60_000);
    expect((await paymentPost(body.token)).status).toBe(200);
  });

  it.each(["", "/confirm", "/cancel"])("付款 %s 拒絕超大 body，沒有交易或狀態副作用", async (suffix) => {
    const { body } = await place();
    expect((await paymentPost(body.token)).status).toBe(200);
    authorize();
    const before = await env.DB.prepare("SELECT * FROM orders").first();
    const oversized = await paymentPost(body.token, suffix, "10.99.0.1", "x".repeat(16 * 1024 + 1));
    expect(oversized.status).toBe(413);
    expect(await oversized.json()).toEqual({ error: "payload_too_large" });
    expect(await env.DB.prepare("SELECT * FROM orders").first()).toEqual(before);
    expect(fake.txs.size).toBe(1);
    expect(fake.confirmCalls).toBe(0);
  });

  it("訂單頁告訴客人頁重新付款要先驗證：同網路已用 4 個名額，照指示先驗證再送，只用 1 個名額就成功（Codex 審查）", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.now());
    const { body } = await place();
    expect(await view(body.token)).toMatchObject({ repay_challenge: false });
    expect((await paymentPost(body.token)).status).toBe(200);
    expect((await paymentPost(body.token, "/cancel")).status).toBe(200);
    expect(await view(body.token)).toMatchObject({ repay_challenge: true });
    const other = await place();
    for (let i = 0; i < 3; i++) expect((await paymentPost(other.body.token)).status).toBe(200);
    // 客人頁的做法：先看訂單頁要不要驗證，要的話先拿驗證再送一次（不先空送一次去試）
    expect((await paymentPost(body.token, "", "10.99.0.1", REPAY)).status).toBe(200);
    expect(fake.txs.size).toBe(3);
    expect((await paymentPost(other.body.token)).status).toBe(429);
  });

  it("偽造成功回呼及金額不能把未授權交易變成已付款", async () => {
    const { body } = await place();
    expect((await paymentPost(body.token)).status).toBe(200);
    const response = await paymentPost(body.token, "/confirm?returnCode=0000&transactionId=1", "10.99.0.1",
      JSON.stringify({ status: "PAID", amount: 1, transactionId: "1" }));
    expect(response.status).toBe(202);
    expect((await view(body.token)).status).toBe("PENDING");
    expect([...fake.txs.values()][0]?.state).toBe("reserved");
  });
});

it.each([false, true])("付款超過十分鐘仍未定，POS 收到提醒；補查付款成功後提醒消失（舊版嘗試 %s）", async (legacy) => {
  const start = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(start - 120000);
  const { body } = await place();
  vi.mocked(Date.now).mockReturnValue(start);
  expect((await post(`/api/orders/${body.token}/linepay`)).status).toBe(200);
  if (legacy) await env.DB.prepare("UPDATE orders SET linepay_started_at = NULL").run();
  vi.mocked(Date.now).mockReturnValue(start + 599999);
  let result = await (await integration("GET", "/integration/orders")).json() as {payment_alerts: unknown[]};
  expect(result.payment_alerts).toEqual([]);
  vi.mocked(Date.now).mockReturnValue(start + 600000);
  result = await (await integration("GET", "/integration/orders")).json() as {payment_alerts: unknown[]};
  expect(result.payment_alerts).toEqual([expect.objectContaining({
    total:150, status:"PENDING", started_at:new Date(start).toISOString(),
  })]);
  authorize();
  vi.mocked(Date.now).mockReturnValue(start + 631000);
  result = await (await integration("GET", "/integration/orders")).json() as {payment_alerts: unknown[]};
  expect(result.payment_alerts).toEqual([]);
});
