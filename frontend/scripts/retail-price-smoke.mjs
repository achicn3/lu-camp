// 全新售價（原價）＋ 上架售價進位到 10 的倍數：瀏覽器煙霧（docs/08 §6.1）。
//
// 守的是 2026-09-19 的兩項裁示：
//   1. 收購頁與庫存編輯都要有「全新售價（原價）」，純記錄、選填、可事後改可清空。
//   2. **系統自動帶出**的上架售價一律 0 結尾（無條件進位）；店員手打的不擋。
//
// 執行：node scripts/retail-price-smoke.mjs
//   需 backend + frontend 對真 Postgres 跑，且 dev-manager 可登入、已開帳。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { pickGrade } from "./_acquisition.mjs";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-retail-price-smoke");
const RUN = Date.now();
const SELLER = `原價賣家-${String(RUN).slice(-6)}`;
const ITEM = `原價測試帳篷-${String(RUN).slice(-6)}`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  // 開帳：收購付現要在開帳中的班別下進行（§7 不變量 8）。已開帳就略過。
  await fetch(`${API}/api/v1/cash-sessions/open`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ opening_float: "2000" }),
  });

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  // ── 賣方 ────────────────────────────────────────────────────────────
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', SELLER);
  await page.fill('input[aria-label="手機"]', uniquePhone(RUN));
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${SELLER}`);

  // ── 鑑價列 ──────────────────────────────────────────────────────────
  await page.fill('input[aria-label="品名"]', ITEM);
  await pickGrade(page, "A");
  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill("原價煙霧分類");
  await page.click('button:has-text("建立「原價煙霧分類")');

  // ── 1. 自動帶入的上架售價要 0 結尾 ──────────────────────────────────
  // 未稅 2010 × 1.05 = 2110.5 → 2111 → 進位 → 2120。挑這個數字是因為它在進位前後不同，
  // 隨便挑一個剛好整十的數（例如 3000 → 3150）根本驗不出這條規則。
  await page.fill('input[aria-label="估計轉售價"]', "2010");
  const listed = page.locator('input[aria-label="上架售價（含稅與手續費）"]');
  const rounded = await page
    .waitForFunction(
      () => {
        const el = document.querySelector(
          'input[aria-label="上架售價（含稅與手續費）"]',
        );
        return el && el.value !== "" && Number(el.value) % 10 === 0 ? el.value : false;
      },
      null,
      { timeout: 8000 },
    )
    .then((handle) => handle.jsonValue())
    .catch(() => null);
  ok(
    "估計轉售價自動帶入的上架售價是 10 的倍數",
    rounded === "2120",
    rounded === null ? "沒等到自動帶入" : `帶入 ${rounded}（未稅 2010 → 含稅 2111 → 進位）`,
  );

  // ── 2. 手動輸入不擋 ──────────────────────────────────────────────────
  await listed.fill("2099");
  await page.waitForTimeout(300);
  ok(
    "店員手打的非整十價格不被改掉",
    (await listed.inputValue()) === "2099",
    "裁示：只約束系統自動帶出的價格",
  );
  await listed.fill("2120");

  // ── 3. 全新售價（原價）欄位 ─────────────────────────────────────────
  const retail = page.locator('input[aria-label="全新售價（原價）"]').first();
  ok("收購頁有全新售價（原價）欄位", await retail.isVisible());
  await retail.fill("8000");
  await page.fill('input[aria-label="收購價"]', "900");
  await page.screenshot({ path: join(SHOTS, "01-acquisition-retail-price.png"), fullPage: true });

  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 15000 });
  ok("帶著原價送出收購成功", true);
  await page.screenshot({ path: join(SHOTS, "02-acquisition-done.png"), fullPage: true });

  // ── 4. 庫存清單看得到原價 ───────────────────────────────────────────
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  const row = page.locator("tr", { hasText: ITEM }).first();
  await row.waitFor({ timeout: 15000 });
  const rowText = (await row.textContent()) ?? "";
  ok("庫存清單顯示原價", rowText.includes("原價") && rowText.includes("8,000"), rowText.trim());
  await page.screenshot({ path: join(SHOTS, "03-inventory-retail-price.png"), fullPage: true });

  // ── 5. 編輯可以改原價 ───────────────────────────────────────────────
  await row.locator('button:has-text("編輯")').click();
  const dialog = page.locator('[role="dialog"][aria-label="編輯商品"]');
  await dialog.waitFor();
  const editRetail = dialog.locator('input[aria-label="全新售價（原價）"]');
  ok("編輯視窗帶入現有原價", (await editRetail.inputValue()) === "8000");
  await editRetail.fill("9500");
  await page.screenshot({ path: join(SHOTS, "04-edit-retail-price.png"), fullPage: true });
  await dialog.locator('button:has-text("儲存")').click();
  await dialog.waitFor({ state: "detached", timeout: 10000 }).catch(() => null);

  const updated = page.locator("tr", { hasText: ITEM }).first();
  await updated.waitFor();
  const changed = await page
    .waitForFunction(
      (name) =>
        [...document.querySelectorAll("tr")]
          .find((tr) => tr.textContent?.includes(name))
          ?.textContent?.includes("9,500") ?? false,
      ITEM,
      { timeout: 10000 },
    )
    .then(() => true)
    .catch(() => false);
  ok("改過的原價出現在清單上", changed);
  await page.screenshot({ path: join(SHOTS, "05-inventory-updated.png"), fullPage: true });
} finally {
  await browser.close();
}

console.log(`\n截圖：${SHOTS}`);
const passed = results.filter((r) => r.pass).length;
console.log(`${passed}/${results.length} 通過`);
process.exit(passed === results.length ? 0 : 1);
