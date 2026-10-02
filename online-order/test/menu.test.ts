// 菜單發佈與讀取（docs/44 §3.5、§5.1）：版本只能往前、同版本重送冪等、客人讀目前生效的版本（ETag）。
import { describe, expect, it } from "vitest";

import { SNAPSHOT, get, integration } from "./helpers";

function publish(snapshot: object): Promise<Response> {
  return integration("PUT", "/integration/menu", JSON.stringify(snapshot));
}

describe("菜單", () => {
  it("還沒發佈過：客人看到 404 與說明", async () => {
    const resp = await get("/api/menu");
    expect(resp.status).toBe(404);
    expect(await resp.json()).toEqual({ error: "menu_not_published" });
  });

  it("發佈後客人讀得到，ETag 是版本號，帶 If-None-Match 回 304", async () => {
    expect((await publish(SNAPSHOT)).status).toBe(200);
    const resp = await get("/api/menu");
    expect(resp.status).toBe(200);
    expect(resp.headers.get("ETag")).toBe('"menu-v1"');
    expect(resp.headers.get("Cache-Control")).toBe("no-cache");
    expect(await resp.json()).toEqual(SNAPSHOT);
    const again = await get("/api/menu", { "If-None-Match": '"menu-v1"' });
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
