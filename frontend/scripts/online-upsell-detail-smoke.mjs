// 購物車加點回歸（店主 2026-10-15 回報）：在購物車按加點的「選擇選項」→ 選好加入，
// 購物車要當場多一行、合計跟著變，不必回菜單再進來。唯讀：不送單、不改設定。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const ORDER = "http://localhost:8794";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-upsell-detail-shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
try {
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto(ORDER);
  await guest.getByRole("button", { name: "甜點", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "巴斯克蛋糕" }).locator(".item-add").click();
  await guest.getByRole("button", { name: /購物車 1 份/ }).click();
  await guest.getByText("合計 $120").waitFor();

  const upsell = guest.locator(".upsell-item").filter({ hasText: "拿鐵" });
  await upsell.getByRole("button", { name: "選擇選項" }).click();
  const sheet = guest.locator("#sheet");
  await sheet.waitFor({ state: "visible" });
  // 每個必選群組若還沒選，選第一個
  for (const group of await sheet.locator("fieldset").all()) {
    if ((await group.locator("input:checked").count()) === 0) await group.locator("input").first().check();
  }
  await sheet.locator("#detail-add").click();
  await sheet.waitFor({ state: "hidden" });

  // 不離開購物車：新的一行要出現、合計要大於 $120
  await guest.locator(".cart-line").filter({ hasText: "拿鐵" }).waitFor({ timeout: 3000 });
  const total = await guest.locator(".cart-total").textContent();
  assert.ok(Number(total.replace(/[^0-9]/g, "")) > 120, `total must include the upsell: ${total}`);
  await guest.screenshot({ path: `${SHOTS}/cart-after-upsell.png` });

  // 從菜單開的選項視窗加入：仍留在菜單，不跳進購物車
  await guest.getByRole("link", { name: /返回菜單/ }).or(guest.getByRole("button", { name: /返回菜單/ })).first().click();
  await guest.locator("#menu-view").waitFor({ state: "visible" });
  await guest.getByRole("button", { name: "飲品", exact: true }).click();
  await guest.locator(".item").filter({ hasText: "拿鐵" }).first().locator(".item-add").click();
  await sheet.waitFor({ state: "visible" });
  for (const group of await sheet.locator("fieldset").all()) {
    if ((await group.locator("input:checked").count()) === 0) await group.locator("input").first().check();
  }
  await sheet.locator("#detail-add").click();
  await sheet.waitFor({ state: "hidden" });
  assert.ok(await guest.locator("#menu-view").isVisible(), "adding from the menu must stay on the menu");
  assert.ok(await guest.locator("#cart-view").isHidden());
  console.log(`PASS upsell with options updates cart in place → ${total.trim()}; menu add stays on menu`);
} finally {
  await browser.close();
}
