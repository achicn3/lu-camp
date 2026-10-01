// POS 條碼欄：進頁面自動對焦、中文輸入法提醒、全形轉半形（店主 2026-10-01）的瀏覽器煙霧。
// 中文輸入法用 Chromium 的 CDP `Input.imeSetComposition` 模擬組字（無頭瀏覽器沒有真的輸入法）。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 node scripts/pos-scan-focus-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-scan-focus");
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
await skipOpeningCheckRedirect(page);
const focusedName = () =>
  page.evaluate(() => document.activeElement?.getAttribute("name") ?? document.activeElement?.tagName);

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  // 1) 從導覽列點進來（店員實際的路徑）：解鎖後焦點要在條碼欄
  await page.getByRole("link", { name: "POS 結帳" }).first().click();
  const box = page.locator('input[name="code"]');
  await box.waitFor();
  await page.waitForFunction(() => !document.querySelector('input[name="code"]')?.disabled);
  await page.waitForFunction(() => document.activeElement?.getAttribute("name") === "code", null, {
    timeout: 5000,
  }).catch(() => {});
  ok("從選單進 POS，焦點自動在條碼欄", (await focusedName()) === "code", String(await focusedName()));
  await page.screenshot({ path: `${SHOTS}/01-focused.png` });

  // 2) 直接開網址也一樣
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.activeElement?.getAttribute("name") === "code", null, {
    timeout: 5000,
  }).catch(() => {});
  ok("直接開 /pos，焦點也在條碼欄", (await focusedName()) === "code", String(await focusedName()));

  // 3) 中文輸入法組字中 → 提醒切英文
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.imeSetComposition", { text: "ㄋ", selectionStart: 1, selectionEnd: 1 });
  const imeAlert = page.locator(".pos-scan [role=alert]", { hasText: "切成英文" });
  await imeAlert.waitFor({ timeout: 3000 }).catch(() => {});
  ok("中文輸入法組字時提醒切成英文", await imeAlert.isVisible());
  await page.screenshot({ path: `${SHOTS}/02-ime-warning.png` });
  await cdp.send("Input.insertText", { text: "" });
  await box.fill("");

  // 4) 全形英數 → 半形
  await box.fill("ＡＢＣ１２３");
  ok("全形英數自動轉半形", (await box.inputValue()) === "ABC123", await box.inputValue());
  ok("轉成半形後不再提醒", !(await imeAlert.isVisible()));
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (err) {
  await page.screenshot({ path: `${SHOTS}/error.png` }).catch(() => {});
  ok("煙霧流程", false, String(err));
}

await browser.close();
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
process.exit(failures.length === 0 ? 0 : 1);
