// 手冊 16b：備份頁「立即備份」在本機（未設定 R2/AES 口令）的實際回應，以及備份設定儲存。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("16-backup");
const shot = makeShot(dir);
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);
await page.goto(`${BASE}/backup`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);

await page.click('button:has-text("立即備份")');
await page.waitForTimeout(5000);
const err = await page.textContent(".form-error").catch(() => null);
note(`立即備份回應：${err}`);
await shot(page, "backup-503", { content: true });

// 備份設定儲存（可實測）
const interval = page.locator('.card:has(h2:text("備份設定")) input').nth(1);
await interval.fill("12");
await page.click('button:has-text("儲存設定")');
await page.waitForTimeout(2500);
note(`備份設定儲存結果：${(await page.textContent('.card:has(h2:text("備份設定"))'))?.replace(/\s+/g, " ").slice(0, 200)}`);
await shot(page, "settings-saved", { locator: '.card:has(h2:text("備份設定"))' });
await interval.fill("24");
await page.click('button:has-text("儲存設定")');
await page.waitForTimeout(2000);

await browser.close();
console.log("✅ 16b 完成");
