// M4 餐飲菜單管理頁瀏覽器煙霧：登入(dev-manager) → /menu → 清單 → 新增品項 → 出現 → 下架切換。
// 需 backend(:8000)+frontend(:3000) 已起。執行：mcr playwright 容器內 node scripts/menu-manage-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));
const uniqueName = `煙霧品項-${Date.now().toString().slice(-6)}`;

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.click('a:has-text("餐飲菜單")');
  await page.waitForURL(`${BASE}/menu`);
  await page.waitForSelector(".inv-table");
  ok("菜單管理清單載入", true);
  await page.screenshot({ path: `${SHOTS}/m4-01-list.png` });

  // 新增品項
  await page.getByLabel("品名").fill(uniqueName);
  await page.getByLabel("售價（整數元）").fill("250");
  await page.getByLabel("分類（選填）").fill("點心");
  await page.click('button:has-text("新增品項")');
  await page.waitForSelector(`tr:has-text("${uniqueName}")`);
  ok("新增品項出現於清單", true, uniqueName);
  await page.screenshot({ path: `${SHOTS}/m4-02-created.png` });

  // 下架該品項（其列的「下架」鈕）→ 狀態變停售
  const row = page.locator(`tr:has-text("${uniqueName}")`);
  await row.locator('button:has-text("下架")').click();
  await page.waitForFunction(
    (name) => {
      const tr = [...document.querySelectorAll("tr")].find((r) =>
        r.textContent?.includes(name),
      );
      return tr?.textContent?.includes("停售") ?? false;
    },
    uniqueName,
    { timeout: 10000 },
  );
  ok("下架後狀態為停售", true);
  await page.screenshot({ path: `${SHOTS}/m4-03-unavailable.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
