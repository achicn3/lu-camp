// 帶回家零售商品（docs/63 §13、M1d）：快照驗證、可售同步、送單計價、拉單帶商品 id、交貨回報、客人看到「已領取」。
import { env, exports } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MenuRetailView, MenuSnapshot } from "../src/client/types";
import { currentEffectiveMenu } from "../src/menu";
import { priceOrder } from "../src/pricing";
import { SNAPSHOT, get, integration } from "./helpers";

const BEAN: MenuRetailView = {
  id: 41, name: "耶加雪菲 200g", description: "柑橘、茉莉", category: "咖啡豆",
  unit_price: 450, photo: null, role: "bean", available: true, remaining: 3,
};
const MENU = { ...SNAPSHOT, retail: [BEAN] } as MenuSnapshot;

function publish(body: object): Promise<Response> {
  return integration("PUT", "/integration/menu", JSON.stringify(body));
}

describe("快照", () => {
  it("收帶回家商品；欄位不對、角色不是豆／濾掛就拒收", async () => {
    expect((await publish(MENU)).status).toBe(200);
    expect(((await (await get("/api/menu")).json()) as MenuSnapshot).retail).toEqual([BEAN]);
    for (const bad of [
      { ...BEAN, unit_cost: 200 },
      { ...BEAN, role: "coffee" },
      { ...BEAN, unit_price: 1.5 },
      { ...BEAN, remaining: -1 },
    ]) {
      expect((await publish({ ...MENU, version: 9, retail: [bad] })).status).toBe(422);
    }
  });

  it("可售同步覆蓋現量；沒列到的商品視為不可賣", async () => {
    await publish(MENU);
    const update = { menu_version: 1, revision: 1, items: [], options: [], retail: [{ id: 41, available: true, remaining: 1 }] };
    expect((await integration("PUT", "/integration/menu/availability", JSON.stringify(update))).status).toBe(200);
    expect((await currentEffectiveMenu(env, 1))?.retail?.[0]?.remaining).toBe(1);
    const gone = { ...update, revision: 2, retail: [] };
    await integration("PUT", "/integration/menu/availability", JSON.stringify(gone));
    expect((await currentEffectiveMenu(env, 1))?.retail?.[0]?.available).toBe(false);
  });
});

describe("送單計價", () => {
  it("照快照售價算、一律要 POS 保留；超過現量、停售、不存在都擋", () => {
    const ok = priceOrder(MENU, [{ catalog_product_id: 41, qty: 2 }]);
    expect(ok).toMatchObject({ ok: true, total: 900, needsHold: true });
    if (ok.ok) {
      expect(ok.lines[0]).toEqual({
        catalog_product_id: 41, name: "耶加雪菲 200g", option_ids: [], unit_price: 450, qty: 2, line_total: 900, limited: true,
      });
    }
    expect(priceOrder(MENU, [{ catalog_product_id: 41, qty: 4 }])).toMatchObject({ ok: false, reason: "sold_out" });
    expect(priceOrder(MENU, [{ catalog_product_id: 41, qty: 2 }, { catalog_product_id: 41, qty: 2 }]))
      .toMatchObject({ ok: false, reason: "sold_out" });
    const off = { ...MENU, retail: [{ ...BEAN, available: false }] } as MenuSnapshot;
    expect(priceOrder(off, [{ catalog_product_id: 41, qty: 1 }])).toMatchObject({ ok: false, reason: "item_not_found" });
    expect(priceOrder(MENU, [{ catalog_product_id: 99, qty: 1 }])).toMatchObject({ ok: false, reason: "item_not_found" });
  });
});

describe("拉單與交貨", () => {
  beforeEach(async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ success: true }));
    await publish(MENU);
    await integration("PUT", "/integration/store-status", JSON.stringify({ accepting: true }));
    await integration("GET", "/integration/orders");
  });
  afterEach(() => vi.restoreAllMocks());

  it("帶回家商品的單：拉單帶商品 id；付款後待交貨、交貨後客人看到已領取", async () => {
    const placed = await exports.default.fetch(new Request("https://order.test/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json", "CF-Connecting-IP": "10.9.0.1" },
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(), table_code: null, payment_method: "CASH",
        turnstile_token: "tok", note: "", lines: [{ catalog_product_id: 41, qty: 1 }],
      }),
    }));
    expect(placed.status).toBe(201);
    const { token } = (await placed.json()) as { token: string };
    const pulled = (await (await integration("GET", "/integration/orders")).json()) as {
      orders: { id: string; hold_status: string; lines: Record<string, unknown>[] }[];
    };
    const order = pulled.orders[0]!;
    expect(order.hold_status).toBe("HOLD_REQUESTED");
    expect(order.lines[0]).toMatchObject({ catalog_product_id: 41, item_id: null, qty: 1, limited: true });

    const report = (body: object) => integration("POST", `/integration/orders/${order.id}/status`, JSON.stringify(body));
    expect((await report({ sync_status: "IMPORTED", hold_status: "HELD" })).status).toBe(200);
    expect((await report({ fulfillment: "HANDED_OVER" })).status).toBe(409); // 沒付款不能交貨
    expect((await report({ sync_status: "SETTLED", payment_status: "PAID", fulfillment: "AWAITING" })).status).toBe(200);
    const view = async () => (await (await exports.default.fetch(new Request(`https://order.test/api/orders/${token}`))).json()) as {
      status: string; fulfillment: string;
    };
    expect(await view()).toMatchObject({ status: "PAID", fulfillment: "AWAITING" });
    expect((await report({ fulfillment: "HANDED_OVER" })).status).toBe(200);
    expect((await report({ fulfillment: "HANDED_OVER" })).status).toBe(200); // 重送冪等
    expect(await view()).toMatchObject({ fulfillment: "HANDED_OVER" });
    expect((await report({ fulfillment: "AWAITING" })).status).toBe(409); // 不能倒退
  });
});
