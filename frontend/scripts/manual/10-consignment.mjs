// 手冊 10：寄售付款——待付款/已付款/已取消分頁、以手機查找、付款確認、開帳前置。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("10-consignment");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/consignment`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "pending-list", { content: true });
await shot(page, "drawer-status", { locator: ".settle-head" });
await shot(page, "tabs", { locator: ".settle-tabs" });
const banner = await page.textContent(".member-banner").catch(() => null);
note(`待付款合計：${banner}`);

// 以手機查找
const phone = await page.locator(".settle-table tbody tr .row-sub").first().textContent();
note(`寄售人手機：${phone}`);
await page.fill('input[aria-label="以寄售人手機查找"]', phone.trim());
await page.click('button:has-text("查找")');
await page.waitForTimeout(1500);
await shot(page, "search-by-phone", { locator: ".settle-card" });
await page.click('button:has-text("清除（手機")');
await page.waitForTimeout(1000);

// 付款
await page.locator('.settle-table tbody tr button:has-text("付款")').first().click();
await page.waitForSelector(".pos-dialog", { timeout: 10000 });
await page.waitForTimeout(600);
await shot(page, "pay-dialog", { locator: ".pos-dialog" });
await page.locator('.pos-dialog button:has-text("確認付款"), .pos-dialog .btn-primary').first().click();
await page.waitForTimeout(3000);
await shot(page, "paid", { content: true });

// 已付款分頁
await page.click('.settle-tabs button:has-text("已付款")');
await page.waitForTimeout(1500);
await shot(page, "paid-tab", { content: true });
await page.click('.settle-tabs button:has-text("已取消")');
await page.waitForTimeout(1500);
await shot(page, "cancelled-tab", { locator: ".settle-card" });

await browser.close();
console.log("✅ 10-consignment 完成");
