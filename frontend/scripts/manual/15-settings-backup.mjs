// 手冊 15：設定（一般／行動支付／溢價率／變更紀錄／簽名保存報表）＋ 備份（健康度／設定／立即備份／紀錄／還原）。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("15-settings");
const shot = makeShot(dir);
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);

// ══ 設定 ══
await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await shot(page, "page", { content: true });
await shot(page, "general-card", { locator: '.card:has(h2:text("一般設定"))' });

// 修改「購物金低消門檻」並儲存
await page.fill('input[name="store_credit_min_spend"]', "100");
await page.click('button:has-text("儲存一般設定")');
await page.waitForTimeout(2000);
const okMsg = await page.textContent('.card:has(h2:text("一般設定")) .form-success').catch(() => null);
note(`一般設定儲存訊息：${okMsg}`);
await shot(page, "general-saved", { locator: '.card:has(h2:text("一般設定"))' });

// 行動支付設定
await shot(page, "mobile-pay-card", { locator: '.card:has(h2:text("行動支付設定"))' });

// 溢價率
await shot(page, "premium-card", { locator: '.card:has(h2:text("溢價率設定"))' });
const premiumInput = page.locator('input[name="premium_rate"], .card:has(h2:text("溢價率設定")) input').first();
await premiumInput.fill("12");
await page.click('button:has-text("儲存溢價率")');
await page.waitForTimeout(2500);
await shot(page, "premium-saved", { locator: '.card:has(h2:text("溢價率設定"))' });
await shot(page, "premium-history", { locator: '.card:has(h2:text("溢價率變更紀錄"))' });
await shot(page, "signature-retention", { locator: '.card:has(h2:text("簽名 PNG 待清理報表"))' });

// 還原設定，避免影響後續示範
await premiumInput.fill("10");
await page.click('button:has-text("儲存溢價率")');
await page.waitForTimeout(2000);

// ══ 備份 ══
const dir2 = shotsDir("16-backup");
const shot2 = makeShot(dir2);
await page.goto(`${BASE}/backup`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await shot2(page, "page", { content: true });
await shot2(page, "health", { locator: '.card:has(h2:text("備份健康度"))' });
await shot2(page, "settings-card", { locator: '.card:has(h2:text("備份設定"))' });
await shot2(page, "restore-card", { locator: '.card:has(h2:text("還原（災難復原）"))' });

// 立即備份
await page.click('button:has-text("立即備份")');
await page.waitForTimeout(6000);
await shot2(page, "backup-triggered", { content: true });
const runsText = await page.textContent('.card:has(h2:text("備份紀錄"))').catch(() => null);
note(`備份紀錄：${runsText?.replace(/\s+/g, " ").slice(0, 220)}`);
await shot2(page, "runs", { locator: '.card:has(h2:text("備份紀錄"))' });

await browser.close();
console.log("✅ 15/16 完成");
