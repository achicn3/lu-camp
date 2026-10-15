// 客人頁頁首與分類列（店主 2026-10-15）真瀏覽器煙霧：
// ① 點分類：分類列貼齊頁首下緣（不被頁首蓋住）；往下滑仍貼在頁首下面，可直接換分類；品項不被分類列擋住。
// ② 按左上 logo／店名：回首頁（菜單最上方、預設分類）；從購物車、訂單頁按也回到同一桌的菜單。
// 唯讀：只連本機 Worker，不送單。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const ORDER = process.env.SMOKE_ORDER ?? "http://localhost:8794";
const TABLE = process.env.SMOKE_TABLE; // 本機 D1 上任一個內用桌位碼
assert.ok(TABLE, "需要 SMOKE_TABLE");
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-header-tabs-shots";
// 有給本機 D1 就多測訂單頁的「回到首頁」：暫時打開接單、送一張外帶現金單，測完刪掉這張單。
const D1 = process.env.SMOKE_D1_ARGS;
const WRANGLER = process.env.SMOKE_WRANGLER ?? "../online-order/node_modules/.bin/wrangler";
const sql = (q) => execFileSync(WRANGLER, ["d1", "execute", ...D1.split(" "), "--command", q], { stdio: "pipe" });
mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 700 }, isMobile: true, hasTouch: true });
  await page.goto(`${ORDER}/t/${TABLE}`);
  await page.locator("#tabs .tab").first().waitFor();
  // 示範菜單品項少、頁面不夠長：撐高清單，才量得到「捲動後」的位置（真菜單本來就夠長）。
  await page.evaluate(() => { document.getElementById("list").style.minHeight = "2400px"; }); // CSP 擋 <style>，用 CSSOM
  const box = async (sel) => page.locator(sel).first().evaluate((n) => { const r = n.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; });
  const tabs = page.locator("#tabs .tab");
  const labels = await tabs.allTextContents();
  assert.ok(labels.length >= 3, `need several tabs: ${labels}`);

  // ① 點第二個分類：分類列上緣＝頁首下緣
  await tabs.nth(1).click();
  await page.waitForTimeout(400);
  let bar = await box(".bar"), nav = await box("#tabs");
  assert.ok(Math.abs(nav.top - bar.bottom) <= 1, `tabs align under header: tabs.top=${nav.top} bar.bottom=${bar.bottom}`);
  const first = await box("#list > *");
  assert.ok(first.top >= nav.bottom - 1, `first item is not hidden under the tabs: item.top=${first.top} tabs.bottom=${nav.bottom}`);
  await page.screenshot({ path: `${SHOTS}/01-tab-aligned.png` });

  // 往下滑：分類列仍貼在頁首下面，直接換第三個分類，不必往上滑
  await page.mouse.wheel(0, 900);
  await page.waitForTimeout(300);
  bar = await box(".bar"); nav = await box("#tabs");
  assert.ok(Math.abs(nav.top - bar.bottom) <= 1, `tabs stay under header while scrolling: ${nav.top} vs ${bar.bottom}`);
  await page.screenshot({ path: `${SHOTS}/02-scrolled-tabs-visible.png` });
  await tabs.nth(2).click();
  await page.waitForTimeout(400);
  assert.equal(await tabs.nth(2).getAttribute("aria-pressed"), "true");
  nav = await box("#tabs");
  assert.ok(Math.abs(nav.top - (await box(".bar")).bottom) <= 1, "after switching, tabs still aligned");
  assert.ok((await page.locator("#list > *").count()) > 1, "this category has several items");
  assert.ok((await box("#list > *")).top >= nav.bottom - 1, "first item of a multi-item category is below the tabs");

  // ② 按 logo／店名回首頁：最上方、預設分類
  const home = page.getByRole("button", { name: "回到首頁" });
  await home.click();
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => window.scrollY), 0, "home scrolls to top");
  assert.ok(await page.locator(".welcome-title").isVisible());
  assert.equal(await tabs.nth(0).getAttribute("aria-pressed"), "true", "home resets to the default tab");
  assert.equal(new URL(page.url()).pathname, `/t/${TABLE}`);

  // 從購物車按：回菜單
  await page.locator(".item-add:not([disabled])").first().click();
  if (await page.locator("#sheet").isVisible()) {
    for (const group of await page.locator("#sheet fieldset").all()) {
      if ((await group.locator("input:checked").count()) === 0) await group.locator("input").first().check();
    }
    await page.locator("#detail-add").click();
  }
  await page.getByRole("button", { name: /購物車 \d+ 份/ }).click();
  await page.locator("#cart-view").waitFor({ state: "visible" });
  await home.click();
  await page.locator("#menu-view").waitFor({ state: "visible" });
  assert.ok(await page.locator("#cart-view").isHidden());
  assert.equal(await page.evaluate(() => window.scrollY), 0);

  // 從訂單頁（網址只有訂單、沒有桌號）按：回到剛才那一桌，不是外帶
  await page.goto(`${ORDER}/order/${"x".repeat(43)}`);
  await page.locator("#order-view").waitFor({ state: "visible" });
  await home.click();
  await page.waitForURL(new RegExp(`/t/${TABLE}$`));
  await page.locator("#menu-view").waitFor({ state: "visible" });
  await page.screenshot({ path: `${SHOTS}/03-home-from-order.png` });

  if (D1) {
    sql(`UPDATE stores_meta SET accepting_orders = 1, paused_reason = NULL, last_pos_seen_ms = ${Date.now()} WHERE store_id = 1`);
    const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await guest.goto(`${ORDER}/t/${TABLE}`);
    await guest.goto(ORDER); // 外帶（沒有低消）
    await guest.locator(".item-add:not([disabled])").first().click();
    if (await guest.locator("#sheet").isVisible()) {
      for (const group of await guest.locator("#sheet fieldset").all()) {
        if ((await group.locator("input:checked").count()) === 0) await group.locator("input").first().check();
      }
      await guest.locator("#detail-add").click();
    }
    await guest.getByRole("button", { name: /購物車 \d+ 份/ }).click();
    await guest.getByRole("button", { name: "送出現金訂單", exact: true }).click();
    await guest.waitForURL(/\/order\//, { timeout: 90000 });
    const backHome = guest.locator("#order-body").getByRole("button", { name: "回到首頁" });
    await backHome.waitFor();
    await guest.screenshot({ path: `${SHOTS}/04-order-home-button.png` });
    const token = new URL(guest.url()).pathname.split("/").pop();
    await backHome.click();
    await guest.locator("#menu-view").waitFor({ state: "visible" });
    assert.equal(new URL(guest.url()).pathname, "/", "takeout order goes back to the takeout menu");
    const hash = (await import("node:crypto")).createHash("sha256").update(token).digest("hex");
    sql(`DELETE FROM order_lines WHERE order_id IN (SELECT id FROM orders WHERE token_hash = '${hash}'); ` +
      `DELETE FROM order_events WHERE order_id IN (SELECT id FROM orders WHERE token_hash = '${hash}'); ` +
      `DELETE FROM orders WHERE token_hash = '${hash}'`);
  }
  console.log(`PASS tabs align under header & stay sticky (${labels.length} tabs); logo → home from menu/cart/order keeps table`);
} finally {
  await browser.close();
}
