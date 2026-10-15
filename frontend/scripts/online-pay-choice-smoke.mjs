// 購物車付款方式（店主 2026-10-16）：兩個單選——「櫃檯結帳」預設選好；LINE Pay 還沒開通時反灰不能選、寫「即將開放」。
// 唯讀：只連本機 Worker（沒設 LINE Pay），不送單。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const ORDER = process.env.SMOKE_ORDER ?? "http://localhost:8794";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-pay-choice-shots";
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  assert.equal((await (await fetch(`${ORDER}/api/status`)).json()).linepay, false, "this smoke needs a Worker without LINE Pay");
  await page.goto(ORDER);
  await page.locator(".item-add:not([disabled])").first().click();
  if (await page.locator("#sheet").isVisible()) {
    for (const group of await page.locator("#sheet fieldset").all()) {
      if ((await group.locator("input:checked").count()) === 0) await group.locator("input").first().check();
    }
    await page.locator("#detail-add").click();
  }
  await page.getByRole("button", { name: /購物車 \d+ 份/ }).click();
  const counter = page.getByRole("radio", { name: /櫃檯結帳/ });
  const linePay = page.getByRole("radio", { name: /LINE Pay/ });
  assert.ok(await counter.isChecked(), "counter checkout is selected");
  assert.ok(await linePay.isDisabled(), "LINE Pay cannot be selected yet");
  assert.ok(!(await linePay.isChecked()));
  await page.locator(".pay-methods").getByText("即將開放").waitFor();
  await page.getByText("櫃檯結帳訂單請在送出後 10 分鐘內結帳，逾時將自動取消。", { exact: true }).waitFor({ timeout: 3000 });
  await linePay.click({ force: true });
  assert.ok(await counter.isChecked(), "tapping the greyed LINE Pay keeps counter checkout");
  assert.ok(await page.getByRole("button", { name: "送出訂單", exact: true }).isVisible());
  await page.locator(".pay-choice").screenshot({ path: `${SHOTS}/pay-choice.png` });
  console.log("PASS counter checkout selected; LINE Pay greyed out with 即將開放");
} finally {
  await browser.close();
}
