// 手冊 18：現金對帳收尾——結帳（實點金額 vs 應有現金、差異）與重新開帳、開帳欄位輸入限制。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("18-cash-close");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await shot(page, "before-close", { content: true });

// 先看報表算出的應有現金，再輸入實點金額（故意少 100 元示範差異）
await page.fill('input[name="counted_amount"]', "4000");
await shot(page, "close-form", { locator: '.card:has(h2:text("結帳"))' });
await page.click('button:has-text("結帳")');
await page.waitForSelector('h2:has-text("已結帳")', { timeout: 15000 });
await page.waitForTimeout(1200);
const summary = await page.textContent(".card");
note(`結帳結果：${summary?.replace(/\s+/g, " ").slice(0, 200)}`);
await shot(page, "closed-summary", { content: true });

// 重新開帳 → 開帳欄位輸入限制
await page.click('button:has-text("重新開帳")');
await page.waitForSelector('input[name="opening_float"]', { timeout: 10000 });
await page.waitForTimeout(800);
const openInput = page.locator('input[name="opening_float"]');
await openInput.click();
await openInput.pressSequentially("12a3");
note(`開帳零用金鍵入「12a3」→ 欄位內容「${await openInput.inputValue()}」`);
await page.waitForTimeout(500);
await shot(page, "opening-input-blocked", { locator: '.card:has(h2:text("開帳"))' });

await openInput.fill("");
await openInput.pressSequentially("3000");
await page.click('button:has-text("開帳")');
await page.waitForSelector(".badge-open", { timeout: 15000 });
await page.waitForTimeout(800);
await shot(page, "reopened", { content: true });

await browser.close();
console.log("✅ 18 完成");
