// POS 拉單／回報／暫停接單（docs/44 §5.2、§4.6、§7；O4b）。全部要 HMAC 簽章。
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

beforeEach(async () => {
  const realFetch = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.startsWith("https://challenges.cloudflare.com/")) return Response.json({ success: true });
    return realFetch(input, init);
  });
  await integration("PUT", "/integration/menu", JSON.stringify(MENU));
  await setStore(true);
  await pull(); // 心跳：POS 在線
});

afterEach(() => vi.restoreAllMocks());

let n = 0;
async function place(lines = [{ item_id: 5, option_ids: [], qty: 1 }]): Promise<string> {
  const resp = await exports.default.fetch(
    new Request("https://order.test/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json", "CF-Connecting-IP": `10.1.0.${++n}` },
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        table_code: null,
        payment_method: "CASH",
        turnstile_token: "tok",
        note: "少冰",
        lines,
      }),
    }),
  );
  expect(resp.status).toBe(201);
  return ((await resp.json()) as { token: string }).token;
}

type Pulled = {
  accepting: boolean;
  paused_reason: string | null;
  orders: { id: string; total: number; hold_status: string; note: string; lines: { item_id: number; qty: number; option_ids: number[] }[] }[];
};

async function pull(): Promise<Pulled> {
  const resp = await integration("GET", "/integration/orders");
  expect(resp.status).toBe(200);
  return (await resp.json()) as Pulled;
}

function report(id: string, body: object): Promise<Response> {
  return integration("POST", `/integration/orders/${id}/status`, JSON.stringify(body));
}

function setStore(accepting: boolean): Promise<Response> {
  return integration("PUT", "/integration/store-status", JSON.stringify({ accepting }));
}

async function customerView(token: string): Promise<{ status: string }> {
  return (await (await exports.default.fetch(new Request(`https://order.test/api/orders/${token}`))).json()) as {
    status: string;
  };
}

describe("POS 拉單", () => {
  it("拉到新單（含明細與備註）；回報 IMPORTED 後不再出現", async () => {
    await place();
    const first = await pull();
    expect(first.orders).toHaveLength(1);
    const o = first.orders[0]!;
    expect(o).toMatchObject({ total: 150, note: "少冰", hold_status: "NONE" });
    expect(o.lines).toEqual([expect.objectContaining({ item_id: 5, qty: 1, option_ids: [] })]);
    expect((await report(o.id, { sync_status: "IMPORTED" })).status).toBe(200);
    expect((await pull()).orders).toHaveLength(0);
  });

  it("拉單＝心跳：沒拉超過 2 分鐘客人就不能下單", async () => {
    await env.DB.prepare("UPDATE stores_meta SET last_pos_seen_ms = ?").bind(Date.now() - 3 * 60_000).run();
    const st = (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as {
      accepting: boolean;
    };
    expect(st.accepting).toBe(false);
    await pull();
    const back = (await (await exports.default.fetch(new Request("https://order.test/api/status"))).json()) as {
      accepting: boolean;
    };
    expect(back.accepting).toBe(true);
  });

  it("沒簽章不能拉", async () => {
    const resp = await exports.default.fetch(new Request("https://order.test/integration/orders"));
    expect(resp.status).toBe(401);
  });
});

describe("回報狀態", () => {
  it("保留到期可從 HELD 回 NONE；重送冪等，不可重新保留", async () => {
    await place([{ item_id: 6, option_ids: [], qty: 1 }]);
    const [order] = (await pull()).orders;
    expect((await report(order!.id, { sync_status: "IMPORTED", hold_status: "HELD" })).status).toBe(200);
    expect((await report(order!.id, { hold_status: "NONE" })).status).toBe(200);
    expect((await report(order!.id, { hold_status: "NONE" })).status).toBe(200);
    expect((await report(order!.id, { hold_status: "HELD" })).status).toBe(409);
    expect((await report(order!.id, { sync_status: "SETTLED", payment_status: "PAID" })).status).toBe(200);
  });

  it("限量品項：POS 保留成功 HELD／不夠 REJECTED，客人看得到", async () => {
    const t1 = await place([{ item_id: 6, option_ids: [], qty: 1 }]);
    const t2 = await place([{ item_id: 6, option_ids: [], qty: 1 }]);
    const [a, b] = (await pull()).orders;
    expect(a!.hold_status).toBe("HOLD_REQUESTED");
    await report(a!.id, { sync_status: "IMPORTED", hold_status: "HELD" });
    await report(b!.id, { sync_status: "IMPORTED", hold_status: "REJECTED" });
    const views = [await customerView(t1), await customerView(t2)].map((v) => v.status).sort();
    expect(views).toEqual(["REJECTED", "UNPAID"]);
  });

  it("結帳成立：PAID＋SETTLED；重送同一結果冪等", async () => {
    const t = await place();
    const [o] = (await pull()).orders;
    await report(o!.id, { sync_status: "IMPORTED" });
    expect((await report(o!.id, { sync_status: "SETTLED", payment_status: "PAID" })).status).toBe(200);
    expect((await report(o!.id, { sync_status: "SETTLED", payment_status: "PAID" })).status).toBe(200);
    expect((await customerView(t)).status).toBe("PAID");
  });

  it("取消：CANCELLED＋VOIDED；已成立的單不能再取消（409）", async () => {
    await place();
    await place();
    const [x, y] = (await pull()).orders;
    expect((await report(x!.id, { sync_status: "VOIDED", payment_status: "CANCELLED" })).status).toBe(200);
    await report(y!.id, { sync_status: "SETTLED", payment_status: "PAID" });
    expect((await report(y!.id, { sync_status: "VOIDED", payment_status: "CANCELLED" })).status).toBe(409);
  });

  it.each([
    [{ sync_status: "NEW" }],
    [{ payment_status: "REFUNDED" }],
    [{ hold_status: "HOLD_REQUESTED" }],
    [{}],
  ])("不合法的狀態：422 %#", async (body) => {
    await place();
    const [o] = (await pull()).orders;
    expect((await report(o!.id, body)).status).toBe(422);
  });

  it("沒有這張單：404", async () => {
    expect((await report("0".repeat(32), { sync_status: "IMPORTED" })).status).toBe(404);
  });

  it("每次轉換都記事件", async () => {
    await place();
    const [o] = (await pull()).orders;
    await report(o!.id, { sync_status: "IMPORTED" });
    await report(o!.id, { sync_status: "SETTLED", payment_status: "PAID" });
    const ev = await env.DB.prepare("SELECT kind, source FROM order_events WHERE order_id = ? ORDER BY id")
      .bind(o!.id)
      .all<{ kind: string; source: string }>();
    expect(ev.results.map((e) => e.kind)).toEqual(["CREATED", "IMPORTED", "SETTLED", "PAID"]);
    expect(ev.results.slice(1).every((e) => e.source === "pos")).toBe(true);
  });
});

describe("暫停接單", () => {
  it("POS 按暫停：客人不能下單；按恢復：清掉自動暫停的原因", async () => {
    await setStore(false);
    expect((await pull()).accepting).toBe(false);
    await env.DB.prepare("UPDATE stores_meta SET paused_reason = 'flood'").run();
    expect((await pull()).paused_reason).toBe("flood");
    await setStore(true);
    const after = await pull();
    expect(after).toMatchObject({ accepting: true, paused_reason: null });
  });
});
