import { describe, expect, it } from "vitest";

import { roadY } from "@/features/customer-display/camping/panels";
import { doodleText } from "@/features/customer-display/camping/svg";
import { SPOTS, buildSceneHtml } from "@/features/customer-display/camping/world";

describe("顧客螢幕露營動畫的畫面內容", () => {
  it("每次畫出來都一樣（固定種子），而且用到的塗鴉字都有字形", () => {
    // 缺字形時 doodleText 會丟錯，整個場景組不起來
    const html = buildSceneHtml();
    expect(buildSceneHtml()).toBe(html);
  });

  it("時間軸要抓的角色與圖層都在", () => {
    const html = buildSceneHtml();
    for (const cls of [
      "cs-stage",
      "cs-zoom",
      "cs-far",
      "cs-mid",
      "cs-track",
      "cs-lights",
      "cs-van-actor",
      "cs-walker",
      "cs-hammerer",
      "cs-sitter",
      "cs-cliffsitter",
      "cs-roaster",
      "cs-hammock-actor",
      "cs-tent-actor",
      "cs-door-leaf",
      "cs-thanks-word",
      "cs-cup-arm",
    ]) {
      expect(html, cls).toContain(cls);
    }
    expect(html).toContain('id="cs-sky-top"');
  });

  it("字形表沒有的字直接報錯，不會默默畫成空白", () => {
    expect(() => doodleText("露坑", { font: "marker", size: 40, fill: "#fff" })).not.toThrow();
    expect(() => doodleText("帳篷", { font: "marker", size: 40, fill: "#fff" })).toThrow(/帳/);
  });

  it("露營車開到營地時，路面已接回停車的高度附近（不會一停車就跳一下）", () => {
    expect(Math.abs(roadY(3000) - 1262)).toBeLessThan(35);
    expect(SPOTS.vanParkX).toBeGreaterThan(3200);
  });
});
