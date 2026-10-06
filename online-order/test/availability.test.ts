import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { currentEffectiveMenu } from "../src/menu";
import { priceOrder } from "../src/pricing";
import { SNAPSHOT, get, integration } from "./helpers";

const snapshot = {
  ...SNAPSHOT,
  items: [
    {
      ...SNAPSHOT.items[0],
      option_groups: [{
        id: 2, name: "溫度", min_select: 1, max_select: 1,
        options: [
          { id: 7, name: "熱", price_delta: 10, available: true, remaining: null },
          { id: 8, name: "冰", price_delta: 20, available: true, remaining: null },
        ],
      }],
    },
  ],
};

const update = {
  menu_version: 1,
  revision: 1,
  items: [{ id: 5, available: true, remaining: 3 }],
  options: [{ id: 7, available: true, remaining: 2 }],
};

function push(body: unknown): Promise<Response> {
  return integration("PUT", "/integration/menu/availability", JSON.stringify(body));
}

async function publish(body: object = snapshot): Promise<Response> {
  return integration("PUT", "/integration/menu", JSON.stringify(body));
}

describe("可售狀態", () => {
  it("需要目前已發佈的同版本菜單；舊版本更新不會影響新菜單", async () => {
    expect(await (await push(update)).json()).toEqual({ error: "version_conflict" });
    await publish();
    expect((await push(update)).status).toBe(200);
    await publish({ ...snapshot, version: 2 });
    expect((await push({ ...update, revision: 2 })).status).toBe(409);
    const menu = await currentEffectiveMenu(env, 1);
    expect(menu?.version).toBe(2);
    expect(menu?.items[0]?.remaining).toBeNull();
    expect((await get("/api/menu")).headers.get("ETag")).toBe('"menu-v2-a0"');
  });

  it("覆蓋目前快照中的 ID，缺少的選項停售；保留快照價格與版本", async () => {
    await publish();
    const before = await get("/api/menu");
    expect(before.headers.get("ETag")).toBe('"menu-v1-a0"');
    expect((await push({
      ...update,
      items: [...update.items, { id: 999, available: true, remaining: 1 }],
      options: [...update.options, { id: 998, available: true, remaining: 1 }],
    })).status).toBe(200);

    const resp = await get("/api/menu", { "If-None-Match": '"menu-v1-a0"' });
    expect(resp.status).toBe(200);
    expect(resp.headers.get("ETag")).toBe('"menu-v1-a1"');
    const menu = await resp.json() as typeof snapshot;
    expect(menu.version).toBe(1);
    expect(menu.items).toHaveLength(1);
    expect(menu.items[0]?.unit_price).toBe(150);
    expect(menu.items[0]?.remaining).toBe(3);
    expect(menu.items[0]?.option_groups[0]?.options).toEqual([
      { id: 7, name: "熱", price_delta: 10, available: true, remaining: 2 },
      { id: 8, name: "冰", price_delta: 20, available: false, remaining: null },
    ]);
    expect((await get("/api/menu", { "If-None-Match": '"menu-v1-a1"' })).status).toBe(304);

    const effective = await currentEffectiveMenu(env, 1);
    expect(effective).toEqual(menu);
    expect(priceOrder(effective!, [{ item_id: 5, option_ids: [7], qty: 1 }])).toMatchObject({
      ok: true, total: 160, needsHold: true,
    });
    expect(priceOrder(effective!, [{ item_id: 5, option_ids: [8], qty: 1 }])).toMatchObject({
      ok: false, reason: "sold_out",
    });
    const stored = await env.DB.prepare("SELECT json FROM menu_snapshots WHERE store_id = 1 AND version = 1")
      .first<{ json: string }>();
    expect(JSON.parse(stored!.json)).toEqual(snapshot);
  });

  it("空陣列令所有已發佈品項停售，後續修訂可恢復", async () => {
    await publish();
    await push({ ...update, items: [], options: [] });
    const unavailable = await currentEffectiveMenu(env, 1);
    expect(unavailable?.items[0]?.available).toBe(false);
    expect(unavailable?.items[0]?.option_groups[0]?.options[0]?.available).toBe(false);
    expect(priceOrder(unavailable!, [{ item_id: 5, option_ids: [7], qty: 1 }])).toMatchObject({
      ok: false, reason: "item_not_found",
    });
    await push({ ...update, revision: 2 });
    expect((await currentEffectiveMenu(env, 1))?.items[0]?.available).toBe(true);
  });

  it("修訂只能增加；同 revision 同內容可重送，內容不同則拒絕", async () => {
    await publish();
    expect((await push(update)).status).toBe(200);
    expect((await push({ ...update, options: [...update.options].reverse() })).status).toBe(200);
    const divergent = await push({ ...update, items: [{ id: 5, available: false, remaining: 0 }] });
    expect(divergent.status).toBe(409);
    expect(await divergent.json()).toEqual({ error: "revision_conflict", current_revision: 1 });
    expect((await push({ ...update, revision: 0 })).status).toBe(422);
    expect((await push({ ...update, revision: 2, items: [{ id: 5, available: false, remaining: 0 }] })).status).toBe(200);
    const stale = await push({ ...update, revision: 1 });
    expect(stale.status).toBe(409);
    expect(await stale.json()).toEqual({ error: "stale_revision", current_revision: 2 });
    expect((await currentEffectiveMenu(env, 1))?.items[0]?.available).toBe(false);
  });

  it.each([
    ["不是 JSON", "{bad"],
    ["多帶價格", JSON.stringify({ ...update, items: [{ ...update.items[0], unit_price: 1 }] })],
    ["多帶名稱", JSON.stringify({ ...update, options: [{ ...update.options[0], name: "hot" }] })],
    ["版本不安全", JSON.stringify({ ...update, menu_version: Number.MAX_SAFE_INTEGER + 1 })],
    ["重複 ID", JSON.stringify({ ...update, items: [...update.items, ...update.items] })],
    ["負數剩餘", JSON.stringify({ ...update, options: [{ id: 7, available: true, remaining: -1 }] })],
    ["多帶欄位", JSON.stringify({ ...update, price: 1 })],
  ])("格式錯誤拒收：%s", async (_, body) => {
    await publish();
    const resp = await integration("PUT", "/integration/menu/availability", body);
    expect(resp.status).toBe(422);
  });

  it("必須通過現有整合簽章", async () => {
    await publish();
    const resp = await get("/integration/menu/availability");
    expect(resp.status).toBe(405);
    const forged = await integration("PUT", "/integration/menu/availability", JSON.stringify(update), {}, {
      secret: "wrong-secret",
    });
    expect(forged.status).toBe(401);
  });
});
