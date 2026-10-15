// 上線前資安（docs/44 §8.4）：客人頁的文字一律 textContent。唯一例外是手沖卡的插畫 SVG
// （brew.ts svg()：程式裡寫死的圖形字串，不含任何店家或客人輸入）。新增 HTML 寫入點要先過審。
import { describe, expect, it } from "vitest";

const sources = import.meta.glob("../src/client/**/*.ts", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const SINK = /\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML\s*\(|document\.write\s*\(/g;

describe("客人頁不把字串當 HTML 塞進頁面", () => {
  it("掃得到客人頁原始碼", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(3);
  });
  it("只有手沖卡的固定 SVG 用 innerHTML", () => {
    const found = Object.entries(sources).flatMap(([file, text]) =>
      [...text.matchAll(SINK)].map((m) => `${file.replace("../src/client/", "")}:${m[0]}`));
    expect(found).toEqual(["brew.ts:.innerHTML ="]);
  });
});
