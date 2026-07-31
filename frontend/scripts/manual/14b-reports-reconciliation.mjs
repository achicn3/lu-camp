// 手冊 14b：報表「對帳」分頁（購物金對帳）——以精確名稱點擊，避免與「現金對帳」混淆。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("14-reports");
const shot = makeShot(dir);
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);
await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.locator('.rpt-tabs-wrap button[role="tab"]').filter({ hasText: /^對帳$/ }).click();
await page.waitForTimeout(2500);
await shot(page, "reconciliation-fixed", { content: true });
note((await page.textContent(".app-main"))?.replace(/\s+/g, " ").slice(0, 300));
await browser.close();
