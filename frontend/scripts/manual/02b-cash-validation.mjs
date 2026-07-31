// 手冊 02b：現金頁的輸入限制與錯誤訊息（以真鍵盤輸入驗證，非程式填值）。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("02-cash");
const shot = makeShot(dir);
shot.n = 6;
const { browser, page } = await newBrowser();
await login(page);
await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);

// 調整金額欄：以真鍵盤輸入非數字
const amount = page.locator('input[name="amount"]');
await amount.click();
await amount.pressSequentially("abc12");
note(`調整金額欄鍵入 abc12 → 實際內容「${await amount.inputValue()}」`);

// 事由填空白 → 前端錯誤訊息
await amount.fill("100");
const noteInput = page.locator('input[name="note"]');
await noteInput.fill("   ");
await page.click('button:has-text("送出調整")');
await page.waitForTimeout(500);
const err = await page
  .textContent(".card:has(h2:text('現金手動調整')) .form-error")
  .catch(() => null);
note(`事由僅空白時的錯誤訊息：${err}`);
await shot(page, "adjust-error", { locator: ".card:has(h2:text('現金手動調整'))" });

await browser.close();
console.log("✅ 02b 完成");
