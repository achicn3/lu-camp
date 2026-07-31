// 手冊 15b：溢價率變更（含二次確認對話與變更紀錄）＋ 備份頁。
import { BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("15-settings");
const shot = makeShot(dir);
const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);

await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
const card = page.locator('.card:has(h2:text("溢價率設定"))');
const premiumInput = card.locator("input").last();
await premiumInput.fill("12");
await card.locator('button:has-text("儲存溢價率")').click();
await page.waitForSelector(".settings-confirm-dialog", { timeout: 10000 });
await page.waitForTimeout(500);
await page.fill('.settings-confirm-dialog input', "操作手冊示範：調高回饋");
await shot(page, "premium-confirm", { locator: ".settings-confirm-dialog" });
await page.click('.settings-confirm-dialog button:has-text("確認")');
await page.waitForTimeout(2500);
await shot(page, "premium-saved", { locator: '.card:has(h2:text("溢價率設定"))' });
await shot(page, "premium-history", { locator: '.card:has(h2:text("溢價率變更紀錄"))' });
await shot(page, "signature-retention", { locator: '.card:has(h2:text("簽名 PNG 待清理報表"))' });
note(`變更紀錄：${(await page.textContent('.card:has(h2:text("溢價率變更紀錄"))'))?.replace(/\s+/g, " ").slice(0, 200)}`);

// 還原為 10%
await premiumInput.fill("10");
await card.locator('button:has-text("儲存溢價率")').click();
await page.waitForSelector(".settings-confirm-dialog", { timeout: 10000 });
await page.fill(".settings-confirm-dialog input", "操作手冊示範結束，還原預設");
await page.click('.settings-confirm-dialog button:has-text("確認")');
await page.waitForTimeout(2500);

// ══ 備份 ══
const dir2 = shotsDir("16-backup");
const shot2 = makeShot(dir2);
await page.goto(`${BASE}/backup`, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await shot2(page, "page", { content: true });
await shot2(page, "health", { locator: '.card:has(h2:text("備份健康度"))' });
await shot2(page, "settings-card", { locator: '.card:has(h2:text("備份設定"))' });
await shot2(page, "restore-card", { locator: '.card:has(h2:text("還原（災難復原）"))' });

await page.click('button:has-text("立即備份")');
await page.waitForTimeout(8000);
await shot2(page, "backup-triggered", { content: true });
const runsText = await page.textContent('.card:has(h2:text("備份紀錄"))').catch(() => null);
note(`備份紀錄：${runsText?.replace(/\s+/g, " ").slice(0, 260)}`);
await shot2(page, "runs", { locator: '.card:has(h2:text("備份紀錄"))' });

await browser.close();
console.log("✅ 15b/16 完成");
