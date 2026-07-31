// 手冊 01：登入 / 登入失敗 / 首頁 / 導覽選單 / 登出。
import { BASE, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("01-shell");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();

// 1. 登入頁
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.waitForTimeout(500);
await shot(page, "login-empty", { locator: ".login-card" });

// 2. 登入失敗（錯誤密碼）——實測錯誤訊息
await page.fill('input[name="username"]', "dev-manager");
await page.fill('input[name="password"]', "wrong-password");
await page.click('button:has-text("登入")');
await page.waitForSelector(".form-error", { timeout: 10000 });
const errText = (await page.textContent(".form-error"))?.trim();
note(`登入失敗訊息：${errText}`);
await shot(page, "login-error", { locator: ".login-card" });

// 3. 正確登入
await page.fill('input[name="password"]', "dev-test-123456");
await page.click('button:has-text("登入")');
await page.waitForURL(`${BASE}/`);
await page.waitForTimeout(800);
await shot(page, "home", { full: true });
await shot(page, "header", { locator: ".app-header" });

// 4. 選單抽屜
await page.click('button:has-text("選單")');
await page.waitForSelector(".nav-drawer");
await page.waitForTimeout(300);
await shot(page, "nav-drawer", { locator: ".nav-drawer" });
const drawerItems = await page.locator(".nav-drawer .nav-link").allTextContents();
note(`選單項目：${drawerItems.join(" / ")}`);
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// 5. 手機版首頁與選單（360px）
await page.setViewportSize({ width: 390, height: 780 });
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await shot(page, "home-mobile", { full: true });
await page.setViewportSize({ width: 1440, height: 900 });

// 6. 登出
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(400);
await page.click('button:has-text("登出")');
await page.waitForURL(`${BASE}/login`);
note("登出後導回 /login");
await shot(page, "after-logout", { locator: ".login-card" });

// 7. 未登入直接開受保護頁 → 導回登入
await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
await page.waitForTimeout(1200);
note(`未登入開 /reports 後網址：${page.url()}`);

await browser.close();
console.log("✅ 01-shell 完成");
