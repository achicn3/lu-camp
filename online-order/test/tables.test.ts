// 桌位碼（docs/44 §4.1）：POS 發佈整份桌位清單（取代舊的）；客人用碼查桌名，無效或已停用的碼 404。
import { describe, expect, it } from "vitest";

import { get, integration } from "./helpers";

const CODE_A = "tA3kq9ZxWm2pLr7v";
const CODE_TAKEOUT = "tGo8nYc4Hs1dQe6u";

function publish(tables: object[]): Promise<Response> {
  return integration("PUT", "/integration/tables", JSON.stringify({ tables }));
}

describe("桌位碼", () => {
  it("發佈後可用碼查桌名與內用／外帶", async () => {
    expect(
      (
        await publish([
          { code: CODE_A, label: "A3", service_mode: "DINE_IN" },
          { code: CODE_TAKEOUT, label: "外帶", service_mode: "TAKEOUT" },
        ])
      ).status,
    ).toBe(200);
    const resp = await get(`/api/tables/${CODE_A}`);
    expect(resp.status).toBe(200);
    expect(await resp.json()).toEqual({ label: "A3", service_mode: "DINE_IN" });
  });

  it("重發後舊碼失效", async () => {
    await publish([{ code: CODE_A, label: "A3", service_mode: "DINE_IN" }]);
    await publish([{ code: CODE_TAKEOUT, label: "A3", service_mode: "DINE_IN" }]);
    expect((await get(`/api/tables/${CODE_A}`)).status).toBe(404);
    expect((await get(`/api/tables/${CODE_TAKEOUT}`)).status).toBe(200);
  });

  it.each(["short", "has space here!!", "x".repeat(65)])("格式不對的碼直接 404：%s", async (code) => {
    expect((await get(`/api/tables/${encodeURIComponent(code)}`)).status).toBe(404);
  });

  it.each([
    [[{ code: "short", label: "A1", service_mode: "DINE_IN" }]],
    [[{ code: CODE_A, label: "", service_mode: "DINE_IN" }]],
    [[{ code: CODE_A, label: "A1", service_mode: "DELIVERY" }]],
    [[
      { code: CODE_A, label: "A1", service_mode: "DINE_IN" },
      { code: CODE_A, label: "A2", service_mode: "DINE_IN" },
    ]],
  ])("桌位清單不合法拒收（422）", async (tables) => {
    expect((await publish(tables)).status).toBe(422);
  });
});
