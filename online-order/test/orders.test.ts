// 客人送單（docs/44 §4.2、§4.3、§4.5 C1、§7、§8）：現金單。
// Turnstile 以 spy 攔 fetch（不連外）；營業狀態以 D1 直接設定（O4b 由 POS 心跳維護）。
import { env, exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SNAPSHOT, integration } from "./helpers";

const MENU = {
  ...SNAPSHOT,
  version: 3,
  items: [
    { ...SNAPSHOT.items[0], id: 5, name: "拿鐵", unit_price: 150, option_groups: [] },
    { ...SNAPSHOT.items[0], id: 6, name: "戚風", unit_price: 90, remaining: 2, option_groups: [] },
  ],
};
const TABLE = "tA3kq9ZxWm2pLr7v";

let turnstileOk = true;
let turnstileCalls = 0;

beforeEach(async () => {
  turnstileOk = true;
  turnstileCalls = 0;
  const realFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.startsWith("https://challenges.cloudflare.com/turnstile/")) {
      turnstileCalls++;
      return Response.json({ success: turnstileOk });
    }
    return realFetch(input, init);
  });
  await integration("PUT", "/integration/menu", JSON.stringify(MENU));
  await integration(
    "PUT",
    "/integration/tables",
    JSON.stringify({ revision: 1, tables: [{ code: TABLE, label: "A3", service_mode: "DINE_IN" }] }),
  );
  await open();
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** 模擬 POS 在線且開放接單（O4b 由拉單心跳維護）。 */
async function open(seenMsAgo = 0): Promise<void> {
  await env.DB.prepare(
    "UPDATE stores_meta SET accepting_orders = 1, paused_reason = NULL, last_pos_seen_ms = ? WHERE store_id = 1",
  )
    .bind(Date.now() - seenMsAgo)
    .run();
}

let ipCounter = 0;
function order(
  body: Record<string, unknown> = {},
  opts: { ip?: string; device?: string } = {},
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "CF-Connecting-IP": opts.ip ?? `10.0.0.${++ipCounter}`,
  };
  if (opts.device) headers.Cookie = `lk_dev=${opts.device}`;
  return exports.default.fetch(
    new Request("https://order.test/api/orders", {
      method: "POST",
      headers,
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        table_code: TABLE,
        payment_method: "CASH",
        turnstile_token: "tok",
        note: "",
        lines: [{ item_id: 5, option_ids: [], qty: 2 }],
        ...body,
      }),
    }),
  );
}

describe("送單", () => {
  it("現金單成立：伺服器算的金額、回訂單權杖；用權杖查得到、只看得到自己這張", async () => {
    const resp = await order();
    expect(resp.status).toBe(201);
    const body = (await resp.json()) as { token: string; total: number; status: string };
    expect(body.total).toBe(300);
    expect(body.status).toBe("UNPAID");
    expect(body.token).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(resp.headers.get("Set-Cookie")).toMatch(/lk_dev=.*HttpOnly.*Secure.*SameSite=Strict/);

    const read = await exports.default.fetch(new Request(`https://order.test/api/orders/${body.token}`));
    expect(read.status).toBe(200);
    const view = (await read.json()) as Record<string, unknown>;
    expect(view).toMatchObject({ total: 300, table_label: "A3", status: "UNPAID" });
    expect(JSON.stringify(view)).not.toContain("ip");
    const bad = await exports.default.fetch(new Request(`https://order.test/api/orders/${"x".repeat(43)}`));
    expect(bad.status).toBe(404);
    // 權杖只存雜湊
    const row = await env.DB.prepare("SELECT token_hash FROM orders").first<{ token_hash: string }>();
    expect(row?.token_hash).not.toBe(body.token);
  });

  it("客人送來的金額一律不理（伺服器重算）", async () => {
    const resp = await order({ total: 1, lines: [{ item_id: 5, option_ids: [], qty: 1, unit_price: 1 }] });
    expect(((await resp.json()) as { total: number }).total).toBe(150);
  });

  it("同一個冪等鍵重送：回同一張單、同一個權杖，不再驗 Turnstile、不多建單", async () => {
    const key = crypto.randomUUID();
    const a = (await (await order({ idempotency_key: key }, { device: "dev-a" })).json()) as { token: string };
    const callsAfterFirst = turnstileCalls;
    const again = await order({ idempotency_key: key }, { device: "dev-a" });
    expect(again.status).toBe(200);
    expect(((await again.json()) as { token: string }).token).toBe(a.token);
    expect(turnstileCalls).toBe(callsAfterFirst);
    const n = await env.DB.prepare("SELECT count(*) AS n FROM orders").first<{ n: number }>();
    expect(n?.n).toBe(1);
  });

  it("同一個冪等鍵但內容不同：409", async () => {
    const key = crypto.randomUUID();
    await order({ idempotency_key: key }, { device: "dev-b" });
    const resp = await order(
      { idempotency_key: key, lines: [{ item_id: 5, option_ids: [], qty: 1 }] },
      { device: "dev-b" },
    );
    expect(resp.status).toBe(409);
  });

  it("Turnstile 沒過：403，不建單", async () => {
    turnstileOk = false;
    const resp = await order();
    expect(resp.status).toBe(403);
    expect(((await resp.json()) as { error: string }).error).toBe("challenge_failed");
    const n = await env.DB.prepare("SELECT count(*) AS n FROM orders").first<{ n: number }>();
    expect(n?.n).toBe(0);
  });

  it("含限量品項：先等 POS 確認庫存（HOLD_REQUESTED）", async () => {
    const resp = await order({ lines: [{ item_id: 6, option_ids: [], qty: 1 }] });
    expect(((await resp.json()) as { status: string }).status).toBe("HOLD_REQUESTED");
  });

  it("送單依最新可售狀態擋售罄及超過剩餘份數", async () => {
    const availability = (revision: number, remaining: number) => integration(
      "PUT", "/integration/menu/availability", JSON.stringify({
        menu_version: MENU.version,
        revision,
        items: [
          { id: 5, available: true, remaining: null },
          { id: 6, available: true, remaining },
        ],
        options: [],
      }),
    );
    expect((await availability(1, 0)).status).toBe(200);
    const soldOut = await order({ lines: [{ item_id: 6, option_ids: [], qty: 1 }] });
    expect(soldOut.status).toBe(422);
    expect(await soldOut.json()).toMatchObject({ error: "sold_out", item_id: 6 });

    expect((await availability(2, 1)).status).toBe(200);
    const tooMany = await order({ lines: [{ item_id: 6, option_ids: [], qty: 2 }] });
    expect(tooMany.status).toBe(422);
    expect(await tooMany.json()).toMatchObject({ error: "sold_out", item_id: 6 });
    const oneLeft = await order({ lines: [{ item_id: 6, option_ids: [], qty: 1 }] });
    expect(oneLeft.status).toBe(201);
    expect(await oneLeft.json()).toMatchObject({ status: "HOLD_REQUESTED", total: 90 });
    const count = await env.DB.prepare("SELECT count(*) AS n FROM orders").first<{ n: number }>();
    expect(count?.n).toBe(1);
  });

  it("桌位碼無效：404；沒帶桌位碼＝外帶也可以", async () => {
    expect((await order({ table_code: "zzzzzzzzzzzzzzzz" })).status).toBe(404);
    const takeout = await order({ table_code: null });
    expect(takeout.status).toBe(201);
  });

  it.each([
    ["備註超過 60 字", { note: "字".repeat(61) }],
    ["付款方式不對", { payment_method: "BITCOIN" }],
    ["沒有冪等鍵", { idempotency_key: "" }],
    ["沒有 Turnstile", { turnstile_token: "" }],
    ["行不是陣列", { lines: "x" }],
  ])("格式不對（%s）：422", async (_, body) => {
    expect((await order(body)).status).toBe(422);
  });

  it("body 超過 16 KB：413", async () => {
    expect((await order({ note: "x".repeat(17 * 1024) })).status).toBe(413);
  });
});

describe("接單狀態", () => {
  it("POS 太久沒來拉單（超過 2 分鐘）：視為暫停，請至櫃台", async () => {
    await open(3 * 60 * 1000);
    const resp = await order();
    expect(resp.status).toBe(503);
    expect(((await resp.json()) as { error: string }).error).toBe("not_accepting");
  });

  it("已送出的單在暫停後重送：照樣拿回同一張（回應遺失的客人查得到自己的單）", async () => {
    const key = crypto.randomUUID();
    const first = await order({ idempotency_key: key }, { ip: "10.9.0.1" });
    expect(first.status).toBe(201);
    const { token } = (await first.json()) as { token: string };
    await env.DB.prepare("UPDATE stores_meta SET accepting_orders = 0 WHERE store_id = 1").run();
    const again = await order({ idempotency_key: key }, { ip: "10.9.0.1" });
    expect(again.status).toBe(200);
    expect(((await again.json()) as { token: string }).token).toBe(token);
    // 新的單仍然擋
    expect((await order({}, { ip: "10.9.0.2" })).status).toBe(503);
  });

  it("店家按了暫停：503", async () => {
    await env.DB.prepare("UPDATE stores_meta SET accepting_orders = 0 WHERE store_id = 1").run();
    expect((await order()).status).toBe(503);
  });

  it("公開設定只提供 Turnstile site key，不洩漏驗證密鑰", async () => {
    const response = await exports.default.fetch(new Request("https://order.test/api/status"));
    const body = await response.json() as Record<string, unknown>;
    expect(body.turnstile_site_key).toBe("test-public-site-key");
    expect(JSON.stringify(body)).not.toContain("test-turnstile-secret");
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect(response.headers.get("Content-Security-Policy")).toContain("frame-src https://challenges.cloudflare.com");
  });

  it("GET /api/status 告訴客人頁現在能不能點", async () => {
    const ok = (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as {
      accepting: boolean;
    };
    expect(ok.accepting).toBe(true);
    await open(3 * 60 * 1000);
    const off = (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as {
      accepting: boolean;
    };
    expect(off.accepting).toBe(false);
  });
});

describe("防灌單上限（§8.2）", () => {
  it("同一裝置未付款單最多 2 張", async () => {
    expect((await order({}, { device: "same" })).status).toBe(201);
    expect((await order({}, { device: "same" })).status).toBe(201);
    const third = await order({}, { device: "same" });
    expect(third.status).toBe(429);
    expect(((await third.json()) as { error: string }).error).toBe("device_unpaid_limit");
  });

  it("同一 IP 未付款單最多 4 張", async () => {
    for (let i = 0; i < 4; i++) expect((await order({}, { ip: "10.9.9.9" })).status).toBe(201);
    const fifth = await order({}, { ip: "10.9.9.9" });
    expect(fifth.status).toBe(429);
  });

  it("同一桌未付款單最多 4 張", async () => {
    for (let i = 0; i < 4; i++) expect((await order()).status).toBe(201);
    const fifth = await order();
    expect(fifth.status).toBe(429);
    expect(((await fifth.json()) as { error: string }).error).toBe("table_unpaid_limit");
  });

  it("同一 IP 每分鐘送單最多 5 次（含失敗的）", async () => {
    for (let i = 0; i < 5; i++) await order({ table_code: "zzzzzzzzzzzzzzzz" }, { ip: "10.8.8.8" });
    const sixth = await order({}, { ip: "10.8.8.8" });
    expect(sixth.status).toBe(429);
    expect(((await sixth.json()) as { error: string }).error).toBe("rate_limited");
  });

  it("5 分鐘內未付款現金單超過 10 張：自動暫停接單", async () => {
    for (let i = 0; i < 11; i++) {
      await order({ table_code: null });
    }
    const meta = await env.DB.prepare("SELECT accepting_orders, paused_reason FROM stores_meta").first<{
      accepting_orders: number;
      paused_reason: string;
    }>();
    expect(meta).toEqual({ accepting_orders: 0, paused_reason: "flood" });
    expect((await order()).status).toBe(503);
  });
});
