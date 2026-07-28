// 手冊補充截圖（Phase E）：A–D 新畫面 + 各系統子分頁（#6 報表各 tab、#7 寄售各 tab、#9）。
// 真 backend(:8010)+frontend(:3010)；輸出到 store-manual/screenshots/（新檔名，不覆蓋既有）。
import { mkdirSync } from "node:fs";

import { chromium } from "playwright";

const BASE = process.env.QA_BASE ?? "http://localhost:3010";
const SHOTS = process.env.MANUAL_SHOTS ?? "/home/test/tmp/store-manual/screenshots";
const TAIWAN_TIME_ZONE = "Asia/Taipei";
mkdirSync(SHOTS, { recursive: true });
const errs = [];
const sectionErrors = [];

async function shotV(page, name, sel) {
  if (sel) {
    await page.locator(sel).first().scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(250);
  }
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
  console.log(`  📸 ${name}`);
}
async function shotFull(page, name) {
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
  console.log(`  📸 ${name} (full)`);
}
const T = (p, t) => p.waitForTimeout(t);

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.getByText("門市作業").first().waitFor({ timeout: 15000 });
}
async function section(label, fn) {
  console.log(`\n— ${label} —`);
  try {
    await fn();
  } catch (e) {
    const message = String(e).slice(0, 140);
    sectionErrors.push(`${label}: ${message}`);
    console.log(`  ⚠️ ${label}: ${message}`);
  }
}

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1366, height: 1100 },
    timezoneId: TAIWAN_TIME_ZONE,
  });
  page.on("pageerror", (e) => errs.push("PE:" + e.message));
  page.on("console", (m) => { if (m.type() === "error") errs.push("CE:" + m.text()); });
  await login(page);

  // ── 庫存：篩選 / 詳細(序號/數量/散裝) / 久滯庫存 ──
  await section("庫存新功能", async () => {
    await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
    await T(page, 800);
    await shotV(page, "inv-01-serialized-filter", ".inv-filters");
    // 序號品詳細
    await page.locator('button:has-text("詳細")').first().click();
    await page.locator("text=商品明細").waitFor({ timeout: 5000 });
    await T(page, 400);
    await shotV(page, "inv-02-detail-serialized", ".inv-detail");
    await page.locator('.inv-detail button:has-text("關閉")').click().catch(() => {});
    // 久滯庫存
    await page.locator('.inv-tab:has-text("久滯庫存")').click();
    await T(page, 800);
    await shotV(page, "inv-03-aging", ".inv-panel");
    // 一般商品詳細
    await page.locator('.inv-tab:has-text("一般商品")').click();
    await T(page, 700);
    await page.locator('button:has-text("詳細")').first().click();
    await page.locator("text=一般商品明細").waitFor({ timeout: 5000 });
    await T(page, 400);
    await shotV(page, "inv-04-detail-catalog", ".inv-detail");
    await page.locator('.inv-detail button:has-text("關閉")').click().catch(() => {});
    // 散裝批詳細
    await page.locator('.inv-tab:has-text("散裝批")').click();
    await T(page, 700);
    await page.locator('button:has-text("詳細")').first().click();
    await page.locator("text=散裝批明細").waitFor({ timeout: 5000 });
    await T(page, 400);
    await shotV(page, "inv-05-detail-bulk", ".inv-detail");
  });

  // ── 採購：新版面 / 採購單詳情 / 供應商分頁 ──
  await section("採購新功能", async () => {
    await page.goto(`${BASE}/purchasing`, { waitUntil: "networkidle" });
    await T(page, 800);
    await shotV(page, "pur-01-layout", ".pur-orders");
    await page.locator(".pur-po-link").first().click();
    await page.locator("text=採購單 #").first().waitFor({ timeout: 5000 });
    await T(page, 400);
    await shotV(page, "pur-02-detail", ".pur-detail");
    await page.locator('.pur-detail button:has-text("關閉")').click().catch(() => {});
    await page.locator('.settle-tabs .chip:has-text("供應商")').click();
    await T(page, 700);
    await shotV(page, "pur-03-suppliers", ".pur-supplier-list");
  });

  // ── 寄售：各狀態分頁 + 手機查找（#7）──
  await section("寄售各分頁", async () => {
    await page.goto(`${BASE}/consignment`, { waitUntil: "networkidle" });
    await T(page, 800);
    await shotV(page, "consign-01-pending", ".settle-head");
    await page.locator('.settle-tabs .chip:has-text("已付款")').click();
    await T(page, 700);
    await shotV(page, "consign-02-paid", ".settle-head");
    await page.locator('.settle-tabs .chip:has-text("已取消")').click();
    await T(page, 700);
    await shotV(page, "consign-03-cancelled", ".settle-head");
  });

  // ── 報表：每個 tab（#6）+ 經營洞察（#8）──
  await section("報表各分頁", async () => {
    await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
    await T(page, 1000);
    const tabs = await page.locator(".inv-tabs .inv-tab").allInnerTexts();
    for (let i = 0; i < tabs.length; i++) {
      const label = tabs[i].trim();
      await page.locator(".inv-tabs .inv-tab").nth(i).click();
      await T(page, 1100);
      const safe = label.replace(/[^一-龥A-Za-z0-9]/g, "");
      await shotFull(page, `rpt-${String(i).padStart(2, "0")}-${safe}`);
    }
  });

  console.log("\nJS errors:", errs.slice(0, 8));
  await browser.close();
  if (sectionErrors.length > 0) {
    throw new Error(`補充截圖有 ${sectionErrors.length} 個區段失敗：${sectionErrors.join("；")}`);
  }
  console.log("=== 補充截圖完成 ===");
}
main().catch((e) => {
  console.error("FATAL", e);
  process.exit(1);
});
