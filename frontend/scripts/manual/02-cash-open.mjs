// 手冊 02：現金對帳——開帳、手動調整、調整紀錄（結帳留到最後一支腳本）。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("02-cash");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await shot(page, "open-form", { content: true, highlight: ['input[name="opening_float"]'] });

// 驗證：非數字被擋下
await page.fill('input[name="opening_float"]', "abc");
const typed = await page.inputValue('input[name="opening_float"]');
note(`輸入 abc 後欄位實際內容：「${typed}」（非數字鍵被擋）`);

// 開帳 3000
await page.fill('input[name="opening_float"]', "3000");
await page.click('button:has-text("開帳")');
await page.waitForSelector(".badge-open", { timeout: 10000 });
await page.waitForTimeout(500);
await shot(page, "opened", { content: true });

// 手動調整（管理者）：金額 + 事由
await page.fill('input[name="amount"]', "-200");
await page.fill('input[name="note"]', "操作手冊測試：買清潔用品");
await shot(page, "adjust-form", { locator: ".card:has(h2:text('現金手動調整'))" });
await page.click('button:has-text("送出調整")');
await page.waitForSelector(".form-success:has-text('已調整')", { timeout: 10000 });
await page.waitForTimeout(800);
await shot(page, "adjust-done", { locator: ".cash-adjustments" });

// 事由未填的錯誤
await page.fill('input[name="amount"]', "100");
await page.click('button:has-text("送出調整")');
await page.waitForTimeout(400);
const err = await page.textContent(".card:has(h2:text('現金手動調整')) .form-error").catch(() => null);
note(`未填事由錯誤訊息：${err}`);
await shot(page, "adjust-error", { locator: ".card:has(h2:text('現金手動調整'))" });

// 結帳卡（只截圖，不送出）
await shot(page, "close-card", { locator: ".card:has(h2:text('結帳'))" });

await browser.close();
console.log("✅ 02-cash 完成");
