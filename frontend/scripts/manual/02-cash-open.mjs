// 手冊 02：現金對帳——開帳、手動調整、調整紀錄（結帳留到最後一支腳本）。
import { apiJson, apiLogin, BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("02-cash");
const shot = makeShot(dir);

// **先把前置狀態弄乾淨**：本章要拍的是「開帳」，但畫面上若已有開帳中的班別，
// 顯示的是已開帳狀態、根本沒有開帳表單，腳本會卡在找不到欄位而逾時。
// 整套跑第二次、或先前有腳本開了班別沒結，就會遇到——**不要假設起始狀態**。
const token = await apiLogin();
const current = await apiJson(token, "GET", "/api/v1/cash-sessions/current");
if (current.status === 200 && current.json?.id) {
  await apiJson(token, "POST", `/api/v1/cash-sessions/${current.json.id}/close`, {
    counted_amount: String(current.json.opening_float ?? "0"),
  });
  note(`已先結掉遺留的開帳班別 #${current.json.id}，讓本章從「尚未開帳」開始`);
}

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
