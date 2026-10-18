// 客人送單（docs/44 §4.2、§4.3、§4.5 C1、§7、§8）：現金單。
// Turnstile 以 spy 攔 fetch（不連外）；營業狀態以 D1 直接設定（O4b 由 POS 心跳維護）。
import { env, exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SNAPSHOT, integration, sha256Hex } from "./helpers";

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
    ["付款方式不對", { payment_method: "BITCOIN" }],
    ["沒有冪等鍵", { idempotency_key: "" }],
    ["沒有 Turnstile", { turnstile_token: "" }],
    ["行不是陣列", { lines: "x" }],
  ])("格式不對（%s）：422", async (_, body) => {
    expect((await order(body)).status).toBe(422);
  });

  it("body 超過 16 KB：413", async () => {
    expect((await order({ padding: "x".repeat(17 * 1024) })).status).toBe(413);
  });
});

// 備註已從線上點餐整個移除（店主 2026-10-15）：客人頁不送、雲端不存、不回傳、不帶給 POS。
describe("沒有備註", () => {
  it("舊版客人頁帶空備註重送：指紋不變，取回同一單", async () => {
    const key = crypto.randomUUID();
    const first = await order({ idempotency_key: key, note: "" });
    expect(first.status).toBe(201);
    const retry = await order({ idempotency_key: key });
    expect(retry.status).toBe(200);
    expect(await retry.json()).toMatchObject({ token: ((await first.json()) as { token: string }).token });
    expect(await env.DB.prepare("SELECT count(*) AS n FROM orders").first()).toEqual({ n: 1 });
  });

  it.each(["少冰", "少\u0000冰", "<script>alert(1)</script>"])("帶了備註 %j 也不存、不回傳、POS 拉不到", async (note) => {
    const created = await order({ note });
    expect(created.status).toBe(201);
    const { token } = await created.json() as { token: string };
    const view = await (await exports.default.fetch(new Request(`https://order.test/api/orders/${token}`))).json();
    expect(view).not.toHaveProperty("note");
    const columns = await env.DB.prepare("SELECT name FROM pragma_table_info('orders')").all<{ name: string }>();
    expect(columns.results.map((c) => c.name)).not.toContain("note");
    const pull = await (await integration("GET", "/integration/orders")).json() as { orders: Record<string, unknown>[] };
    expect(pull.orders[0]).not.toHaveProperty("note");
  });
});


describe("接單狀態", () => {
  it("POS 太久沒來拉單（超過 2 分鐘）：視為暫停，請至櫃檯", async () => {
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

it("台北營業時間之外拒收新單；舊單仍可查詢及冪等重試", async () => {
  const original = { idempotency_key: crypto.randomUUID() };
  const old = await (await order(original)).json() as { token: string };
  const published = await integration("PUT", "/integration/menu", JSON.stringify({
    ...MENU, version: 4, hours: { enabled: true, days: Array(7).fill(null) },
  }));
  expect(published.status).toBe(200);
  expect((await (await exports.default.fetch(new Request("https://order.test/api/status"))).json())).toMatchObject({ accepting: false, within_hours: false });
  expect((await order()).status).toBe(503);
  expect((await order(original)).status).toBe(200);
  expect((await exports.default.fetch(new Request(`https://order.test/api/orders/${old.token}`))).status).toBe(200);
});

it.each([
  ["2026-10-12T11:59:59Z", false], // Monday 19:59 Taipei
  ["2026-10-12T12:00:00Z", true],
  ["2026-10-12T17:59:59Z", true], // Tuesday 01:59, Monday spillover
  ["2026-10-12T18:00:00Z", false],
  ["2026-10-14T00:59:59Z", false],
  ["2026-10-14T01:00:00Z", true],
  ["2026-10-14T08:59:59Z", true],
  ["2026-10-14T09:00:00Z", false],
  ["2026-10-18T14:00:00Z", true], // Sunday 22:00
  ["2026-10-18T16:59:59Z", true], // Monday 00:59, week rollover
  ["2026-10-18T17:00:00Z", false],
])("營業邊界 %s => %s", async (at, allowed) => {
  vi.spyOn(Date, "now").mockReturnValue(Date.parse(at));
  await open();
  const published = await integration("PUT", "/integration/menu", JSON.stringify({
    ...MENU, version: 4, hours: { enabled: true, days: [
      {opens:"20:00",closes:"02:00"}, null, {opens:"09:00",closes:"17:00"}, null, null, null,
      {opens:"22:00",closes:"01:00"},
    ] },
  }));
  expect(published.status).toBe(200);
  const status = await exports.default.fetch(new Request("https://order.test/api/status"));
  expect(await status.json()).toMatchObject({ accepting: allowed, within_hours: allowed });
  expect((await order()).status).toBe(allowed ? 201 : 503);
});

it("營業時段不能覆蓋店員暫停；錯誤時段不能發佈", async () => {
  await integration("PUT", "/integration/store-status", JSON.stringify({accepting:false}));
  expect((await order()).status).toBe(503);
  expect((await integration("PUT", "/integration/menu", JSON.stringify({
    ...MENU, version:4, hours:{enabled:true,days:[{opens:"25:00",closes:"09:00"},...Array(6).fill(null)]},
  }))).status).toBe(422);
});

async function minimumTable(minimum: object) {
  expect((await integration("PUT", "/integration/tables", JSON.stringify({revision:2, tables:[
    {code:TABLE,label:"A3",service_mode:"DINE_IN",minimum},
  ]}))).status).toBe(200);
}
it("指定桌最低金額由伺服器計價，未達不建單；外帶不受影響", async () => {
  await minimumTable({mode:"amount",amount:350});
  const denied=await order({total:99999});
  expect(denied.status).toBe(422);
  expect(await denied.json()).toMatchObject({error:"minimum_not_met",missing_amount:50});
  expect(await env.DB.prepare("SELECT COUNT(*) AS n FROM orders").first("n")).toBe(0);
  expect((await order({lines:[{item_id:5,option_ids:[],qty:3}]})).status).toBe(201);
  expect((await order({table_code:null})).status).toBe(201);
});
it("每人至少一份：缺人數拒收、份數不足顯示缺額、人數同步 POS", async () => {
  await minimumTable({mode:"per_person_item"});
  expect((await order()).status).toBe(422);
  const denied=await order({party_size:3});
  expect(await denied.json()).toMatchObject({error:"minimum_not_met",missing_items:1});
  const response=await order({party_size:3,lines:[{item_id:5,option_ids:[],qty:3}]});
  expect(response.status).toBe(201);
  const pull=await (await integration("GET","/integration/orders")).json() as {orders:{party_size:number}[]};
  expect(pull.orders[0]?.party_size).toBe(3);
});
it.each([0,-1,1.5,51,"2",true])("拒絕無效用餐人數 %s", async party_size => {
  expect((await order({party_size})).status).toBe(422);
});
it("人數納入冪等指紋；規則更動不阻擋已成立訂單重試", async () => {
  await minimumTable({mode:"per_person_item"});
  const request={party_size:2,idempotency_key:crypto.randomUUID()};
  expect((await order(request)).status).toBe(201);
  await integration("PUT","/integration/tables",JSON.stringify({revision:3,tables:[
    {code:TABLE,label:"A3",service_mode:"DINE_IN",minimum:{mode:"amount",amount:999}},
  ]}));
  expect((await order(request)).status).toBe(200);
  expect((await order({...request,party_size:1})).status).toBe(409);
});

// 加點（店主 2026-10-15）：客人自己勾「我是加點」就不檢查低消，真假由店員現場判斷；標記帶給 POS。
it("加點不檢查整筆低消，標記同步 POS", async () => {
  await minimumTable({mode:"amount",amount:350});
  const response=await order({add_on:true});
  expect(response.status).toBe(201);
  const pull=await (await integration("GET","/integration/orders")).json() as {orders:{add_on:boolean}[]};
  expect(pull.orders[0]?.add_on).toBe(true);
});
it("加點不用填人數、也不檢查每人一份", async () => {
  await minimumTable({mode:"per_person_item"});
  expect((await order({add_on:true})).status).toBe(201);
  expect((await order({add_on:true,party_size:4})).status).toBe(201);
});
it("沒勾加點照樣擋；一般單同步 POS 時加點為 false", async () => {
  await minimumTable({mode:"amount",amount:350});
  expect((await order({add_on:false})).status).toBe(422);
  expect((await order({lines:[{item_id:5,option_ids:[],qty:3}]})).status).toBe(201);
  const pull=await (await integration("GET","/integration/orders")).json() as {orders:{add_on:boolean}[]};
  expect(pull.orders[0]?.add_on).toBe(false);
});
it.each(["true",1,null])("拒絕無效的加點標記 %s", async add_on => {
  expect((await order({add_on})).status).toBe(422);
});
it("外帶不能標加點", async () => {
  expect((await order({table_code:null,add_on:true})).status).toBe(422);
});
it("加點納入冪等指紋；沒勾加點的舊版重送指紋不變", async () => {
  await minimumTable({mode:"amount",amount:350});
  const key=crypto.randomUUID();
  expect((await order({idempotency_key:key,add_on:true})).status).toBe(201);
  expect((await order({idempotency_key:key,add_on:true})).status).toBe(200);
  expect((await order({idempotency_key:key})).status).toBe(409);
  const plain={idempotency_key:crypto.randomUUID(),lines:[{item_id:5,option_ids:[],qty:3}]};
  expect((await order(plain)).status).toBe(201);
  expect((await order({...plain,add_on:false})).status).toBe(200);
});

// 續點（店主 2026-10-16）：可續點的品項照折數算；續點不算低消；標記帶給 POS。
describe("續點", () => {
  const REFILL_MENU = { ...MENU, version: 9, refill_pct: 80, items: [{ ...MENU.items[0], refillable: true }, MENU.items[1]] };
  /** 本桌先點過、付過（店主 2026-10-19：續點要本桌 90 分鐘內點過，限內用）。 */
  async function paidBefore(opts: { table?: string | null; item?: number; minutesAgo?: number; paid?: boolean } = {}) {
    const resp = await order({ table_code: opts.table === undefined ? TABLE : opts.table,
      lines: [{ item_id: opts.item ?? 5, option_ids: [], qty: 1 }] });
    expect(resp.status).toBe(201);
    // 標成已付、指定下單時間；標已匯入，後面拉單只看得到測試自己送的單。
    await env.DB.prepare(
      "UPDATE orders SET payment_status = ?, sync_status = 'IMPORTED', created_at = ? " +
        "WHERE id = (SELECT id FROM orders ORDER BY rowid DESC LIMIT 1)",
    ).bind(opts.paid === false ? "UNPAID" : "PAID", Date.now() - (opts.minutesAgo ?? 10) * 60_000).run();
  }
  const refillLine = { item_id: 5, option_ids: [], qty: 1, refill: true };
  const tableView = async (code = TABLE) =>
    (await (await exports.default.fetch(new Request(`https://order.test/api/tables/${code}`))).json()) as
      { refill_item_ids?: number[] };
  beforeEach(async () => {
    expect((await integration("PUT", "/integration/menu", JSON.stringify(REFILL_MENU))).status).toBe(200);
  });

  it("照折數算價、品名冠續點，拉單時帶 refill（一般行沒有）", async () => {
    await paidBefore();
    const resp = await order({ lines: [
      { item_id: 5, option_ids: [], qty: 1, refill: true },
      { item_id: 5, option_ids: [], qty: 1 },
    ] });
    expect(resp.status).toBe(201);
    expect(((await resp.json()) as { total: number }).total).toBe(270);
    const pull = await (await integration("GET", "/integration/orders")).json() as
      { orders: { lines: { name: string; unit_price: number; refill?: boolean }[] }[] };
    expect(pull.orders[0]?.lines.map((l) => [l.name, l.unit_price, l.refill ?? null])).toEqual([
      ["續點・拿鐵", 120, true],
      ["拿鐵", 150, null],
    ]);
  });

  it("不可續點的品項拒收", async () => {
    const resp = await order({ lines: [{ item_id: 6, option_ids: [], qty: 1, refill: true }] });
    expect(resp.status).toBe(422);
    expect(await resp.json()).toMatchObject({ error: "not_refillable", item_id: 6 });
  });

  it.each(["true", 1, null, false])("拒絕無效的續點標記 %s", async (refill) => {
    expect((await order({ lines: [{ item_id: 5, option_ids: [], qty: 1, refill }] })).status).toBe(422);
  });

  it("帶回家商品不能續點", async () => {
    expect((await order({ lines: [{ catalog_product_id: 9, qty: 1, refill: true }] })).status).toBe(422);
  });

  it("續點不算低消：金額與每人份數都不數續點", async () => {
    await paidBefore();
    await minimumTable({ mode: "amount", amount: 300 });
    const denied = await order({ lines: [
      { item_id: 5, option_ids: [], qty: 1 }, { item_id: 5, option_ids: [], qty: 2, refill: true },
    ] });
    expect(await denied.json()).toMatchObject({ error: "minimum_not_met", missing_amount: 150 });
    await minimumTable({ mode: "per_person_item" });
    const short = await order({ party_size: 2, lines: [
      { item_id: 5, option_ids: [], qty: 1 }, { item_id: 5, option_ids: [], qty: 1, refill: true },
    ] });
    expect(await short.json()).toMatchObject({ error: "minimum_not_met", missing_items: 1 });
  });

  it("續點納入冪等指紋；沒續點的舊版重送指紋不變", async () => {
    await paidBefore();
    const key = crypto.randomUUID();
    const refill = { idempotency_key: key, lines: [{ item_id: 5, option_ids: [], qty: 1, refill: true }] };
    expect((await order(refill)).status).toBe(201);
    expect((await order(refill)).status).toBe(200);
    expect((await order({ idempotency_key: key, lines: [{ item_id: 5, option_ids: [], qty: 1 }] })).status).toBe(409);
  });

  // 店主 2026-10-19：續點要本桌 90 分鐘內點過（已付款）的同一品項；外帶不續點；份數不限。
  it("本桌沒點過這項：不能續點", async () => {
    await paidBefore({ item: 6 });
    const resp = await order({ lines: [refillLine] });
    expect(resp.status).toBe(422);
    expect(await resp.json()).toMatchObject({ error: "refill_not_eligible", item_id: 5 });
  });

  it.each<[string, { table?: string; minutesAgo?: number; paid?: boolean }]>([
    ["超過 90 分鐘", { minutesAgo: 91 }],
    ["別桌點的", { table: "tB9kq9ZxWm2pLr7v" }],
    ["還沒付款", { paid: false }],
  ])("%s不算", async (_, opts) => {
    if (opts.table) {
      await integration("PUT", "/integration/tables", JSON.stringify({ revision: 5, tables: [
        { code: TABLE, label: "A3", service_mode: "DINE_IN" }, { code: opts.table, label: "B1", service_mode: "DINE_IN" },
      ] }));
    }
    await paidBefore(opts);
    expect(await (await order({ lines: [refillLine] })).json()).toMatchObject({ error: "refill_not_eligible" });
  });

  it("外帶不能續點", async () => {
    await paidBefore({ table: null });
    expect(await (await order({ table_code: null, lines: [refillLine] })).json()).toMatchObject({ error: "refill_not_eligible" });
  });

  it("同桌換另一支手機也能續點，份數不限", async () => {
    await paidBefore();
    const resp = await order({ lines: [{ ...refillLine, qty: 5 }] }, { device: "another-phone-device-id-0001" });
    expect(resp.status).toBe(201);
  });

  // 不限品項續點（店主 2026-10-19）：本桌 90 分鐘內有消費過（已付款）就能續點任何品項。
  describe("不限品項續點", () => {
    const ANY_MENU = { ...REFILL_MENU, version: 10, refill_any_item: true,
      items: [{ ...MENU.items[0], refillable: true }, { ...MENU.items[1], refillable: true }] };
    beforeEach(async () => {
      expect((await integration("PUT", "/integration/menu", JSON.stringify(ANY_MENU))).status).toBe(200);
    });

    it("本桌點過別的品項也能續點", async () => {
      await paidBefore({ item: 6 });
      expect((await order({ lines: [refillLine] })).status).toBe(201);
      expect((await tableView())).toMatchObject({ refill_any: true });
    });

    it("本桌還沒消費過：不能續點", async () => {
      expect(await (await order({ lines: [refillLine] })).json()).toMatchObject({ error: "refill_not_eligible" });
      expect((await tableView())).toMatchObject({ refill_any: false });
    });

    it("外帶照樣不能續點", async () => {
      await paidBefore({ table: null });
      expect(await (await order({ table_code: null, lines: [refillLine] })).json()).toMatchObject({ error: "refill_not_eligible" });
    });

    it("快照的 refill_any_item 只收 true", async () => {
      const bad = await integration("PUT", "/integration/menu", JSON.stringify({ ...ANY_MENU, version: 11, refill_any_item: "yes" }));
      expect(bad.status).toBe(422);
    });
  });

  it("桌位資訊帶本桌 90 分鐘內點過的品項，客人頁才知道要不要顯示續點", async () => {
    expect((await tableView()).refill_item_ids).toEqual([]);
    await paidBefore({ item: 6 });
    await paidBefore({ item: 5, minutesAgo: 30 });
    await paidBefore({ item: 5, minutesAgo: 100 });
    expect((await tableView()).refill_item_ids).toEqual([5, 6]);
  });
});
