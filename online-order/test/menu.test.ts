// 菜單發佈與讀取（docs/44 §3.5、§5.1）：版本只能往前、同版本重送冪等、客人讀目前生效的版本（ETag）。
import { describe, expect, it } from "vitest";

import { SNAPSHOT, get, integration } from "./helpers";

const PRESENTATION = {
  flavor_description: "莓果、可可", audience_description: "適合喜歡明亮酸香的人",
  is_recommended: true, is_new: false, limited_on: "2026-10-06",
  show_remaining: false, low_stock_threshold: 5, hide_sold_out: false,
};

function publish(snapshot: object): Promise<Response> {
  return integration("PUT", "/integration/menu", JSON.stringify(snapshot));
}

describe("菜單", () => {
  it("還沒發佈過：客人看到 404 與說明", async () => {
    const resp = await get("/api/menu");
    expect(resp.status).toBe(404);
    expect(await resp.json()).toEqual({ error: "menu_not_published" });
  });

  it("發佈後客人讀得到，ETag 包含菜單與可售版本，帶 If-None-Match 回 304", async () => {
    expect((await publish(SNAPSHOT)).status).toBe(200);
    const resp = await get("/api/menu");
    expect(resp.status).toBe(200);
    expect(resp.headers.get("ETag")).toBe('"menu-v1-a0"');
    expect(resp.headers.get("Cache-Control")).toBe("no-cache");
    expect(await resp.json()).toEqual(SNAPSHOT);
    const again = await get("/api/menu", { "If-None-Match": '"menu-v1-a0"' });
    expect(again.status).toBe(304);
  });

  it("新版本覆蓋舊版本；舊版本不能再發佈（409）", async () => {
    await publish(SNAPSHOT);
    await publish({ ...SNAPSHOT, version: 2, store_name: "露坑 v2" });
    expect(((await (await get("/api/menu")).json()) as { version: number }).version).toBe(2);
    expect((await publish(SNAPSHOT)).status).toBe(409);
  });

  it("同版本同內容重送：冪等 200；同版本不同內容：409", async () => {
    await publish(SNAPSHOT);
    expect((await publish(SNAPSHOT)).status).toBe(200);
    expect((await publish({ ...SNAPSHOT, store_name: "改過" })).status).toBe(409);
  });

  it.each([
    ["不是 JSON", "{not json"],
    ["沒有版本號", JSON.stringify({ ...SNAPSHOT, version: undefined })],
    ["版本號不是正整數", JSON.stringify({ ...SNAPSHOT, version: 1.5 })],
    ["items 不是陣列", JSON.stringify({ ...SNAPSHOT, items: "x" })],
    ["價格不是整數元", JSON.stringify({ ...SNAPSHOT, items: [{ ...SNAPSHOT.items[0], unit_price: 1.5 }] })],
  ])("格式不對拒收（422）：%s", async (_, body) => {
    const resp = await integration("PUT", "/integration/menu", body);
    expect(resp.status).toBe(422);
  });

  it("公開快照拒絕呈現設定混入成本", async () => {
    const presentation = {
      flavor_description: "莓果、可可", audience_description: "適合喜歡明亮酸香的人",
      is_recommended: true, is_new: false, limited_on: "2026-10-06",
      show_remaining: false, low_stock_threshold: 5, hide_sold_out: false,
      cost: 30,
    };
    expect((await publish({ ...SNAPSHOT, items: [{ ...SNAPSHOT.items[0], presentation }] })).status).toBe(422);
    expect((await get("/api/menu")).status).toBe(404);
  });

  it.each([
    ["日期不存在", { limited_on: "2026-02-30" }],
    ["日期不是 ISO 日", { limited_on: "2026-2-3" }],
    ["日期年為零", { limited_on: "0000-01-01" }],
    ["文字過長", { flavor_description: "茶".repeat(121) }],
    ["族群不是文字", { audience_description: [] }],
    ["布林不是布林", { is_recommended: 1 }],
    ["剩餘開關不是布林", { show_remaining: "false" }],
    ["隱藏不是布林", { hide_sold_out: null }],
    ["新品不是布林", { is_new: "true" }],
    ["門檻負數", { low_stock_threshold: -1 }],
    ["門檻太大", { low_stock_threshold: 10000 }],
    ["門檻非整數", { low_stock_threshold: 1.5 }],
    ["缺少欄位", { limited_on: undefined }],
  ])("呈現設定格式錯誤拒收：%s", async (_, invalid) => {
    expect((await publish({ ...SNAPSHOT,
      items: [{ ...SNAPSHOT.items[0], presentation: { ...PRESENTATION, ...invalid } }],
    })).status).toBe(422);
  });

  it("完整呈現設定原样保留，文字以 Unicode 字元計算", async () => {
    const snapshot = { ...SNAPSHOT, items: [{ ...SNAPSHOT.items[0],
      presentation: { ...PRESENTATION, flavor_description: "☕".repeat(120), low_stock_threshold: 9999 },
    }] };
    expect((await publish(snapshot)).status).toBe(200);
    expect(await (await get("/api/menu")).json()).toEqual(snapshot);
  });

  it.each([
    ["商品成本", { ...SNAPSHOT.items[0], cost: 30 }],
    ["群組內部備註", { ...SNAPSHOT.items[0], option_groups: [{
      id: 2, name: "溫度", min_select: 1, max_select: 1, internal_note: "private", options: [],
    }] }],
    ["選項成本", { ...SNAPSHOT.items[0], option_groups: [{
      id: 2, name: "溫度", min_select: 1, max_select: 1, options: [{
        id: 7, name: "熱", price_delta: 10, available: true, remaining: null, cost: 10,
      }],
    }] }],
  ])("公開巢狀資料拒絕內部欄位：%s", async (_, item) => {
    expect((await publish({ ...SNAPSHOT, items: [item] })).status).toBe(422);
  });

  it("只保留最近 5 版", async () => {
    for (let v = 1; v <= 7; v++) await publish({ ...SNAPSHOT, version: v });
    const resp = await get("/api/menu");
    expect(((await resp.json()) as { version: number }).version).toBe(7);
    const { env } = await import("cloudflare:workers");
    const row = await env.DB.prepare("SELECT count(*) AS n FROM menu_snapshots").first<{ n: number }>();
    expect(row?.n).toBe(5);
  });

  it("超過 512 KB 的菜單拒收（413）", async () => {
    const huge = { ...SNAPSHOT, store_name: "x".repeat(600 * 1024) };
    expect((await publish(huge)).status).toBe(413);
  });
});
