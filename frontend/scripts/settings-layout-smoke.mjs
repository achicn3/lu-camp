// 設定頁分區改版煙霧（2026-10-08）：真 backend＋真 Postgres。
// 分區目錄 → 混合付款發票方式切換並持久化（改完還原）→ 改過未存的提示 → 有未存時離開頁面要確認
// → 手機寬度不橫向捲動、目錄可橫滑。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API=http://localhost:8000 node scripts/settings-layout-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "settings-layout");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiToken() {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  });
  return (await res.json()).access_token;
}
async function settings(token, patch) {
  const res = await fetch(`${API}/api/v1/settings`, {
    method: patch ? "PATCH" : "GET",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(patch ? { body: JSON.stringify(patch) } : {}),
  });
  return res.json();
}

const token = await apiToken();
const original = await settings(token);
const browser = await chromium.launch();
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (e) => errors.push(String(e)));
  await skipOpeningCheckRedirect(page, BASE);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });

  const nav = page.getByRole("navigation", { name: "設定分區" });
  const links = await nav.locator("a").allInnerTexts();
  ok("左側目錄八個分區", links.length === 8, links.join("、"));
  await page.screenshot({ path: join(SHOTS, "01-desktop.png") });

  await nav.getByRole("link", { name: "購物金" }).click();
  await page.waitForTimeout(600);
  const top = await page.locator("#settings-store-credit").evaluate((el) => el.getBoundingClientRect().top);
  ok("點目錄跳到該區（標題沒被頂欄蓋住）", top >= 56 && top < 200, `top=${Math.round(top)}`);

  // 混合付款一律扣掉購物金後開發票（店主 2026-10-08 統一）：只有說明、沒有切換。
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  const invoiceForm = page.getByRole("form", { name: "電子發票" });
  ok(
    "發票區寫明購物金扣掉後開、沒有切換選項",
    (await invoiceForm.getByText(/購物金＋其他付款時，發票扣掉購物金後開/).count()) === 1 &&
      (await invoiceForm.locator('input[name="store_credit_invoice_mode"]').count()) === 0,
  );
  // 未存提示／離開確認：改「開電子發票」開關來測，存好後重新整理確認，最後還原。
  const einvoice = invoiceForm.getByLabel(/開電子發票/);
  const wasOn = await einvoice.isChecked();
  // 開關的 checkbox 被樣式軌道蓋住，像人一樣點整個標籤
  await invoiceForm.locator("label.settings-switch", { hasText: "開電子發票" }).click();
  ok("改了就顯示「有未儲存的變更」", await invoiceForm.getByText("有未儲存的變更").isVisible());
  // 卡片回報「未儲存」給整頁是下一個 render，等一下再數
  await nav.locator(".settings-nav-dot").first().waitFor({ timeout: 5000 }).catch(() => {});
  ok("目錄上那一區也有提示點", (await nav.locator(".settings-nav-dot").count()) === 1);
  await page.screenshot({ path: join(SHOTS, "02-dirty.png") });

  // 有未存變更時點選單離開：要先確認；按取消就留在這頁、變更還在。
  let asked = "";
  page.once("dialog", async (dialog) => {
    asked = dialog.message();
    await dialog.dismiss();
  });
  await page.locator("a[href='/pos']").first().click();
  await page.waitForTimeout(800);
  ok("有未存變更時離開會先問", asked.includes("還沒儲存"), asked);
  ok("按取消就留在設定頁", page.url().endsWith("/settings"), page.url());

  await invoiceForm.getByRole("button", { name: "儲存發票設定" }).click();
  await invoiceForm.getByText("已儲存").waitFor({ timeout: 10000 });
  ok("存好後提示消失", (await invoiceForm.getByText("有未儲存的變更").count()) === 0);
  await page.reload({ waitUntil: "networkidle" });
  const reloaded = page.getByRole("form", { name: "電子發票" });
  ok("重新整理後是剛存的值", (await reloaded.getByLabel(/開電子發票/).isChecked()) === !wasOn);
  ok("後端也存好了", (await settings(token)).einvoice_enabled === !wasOn);

  // 手機：整頁不橫向捲動、目錄在頂端可橫滑
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  phone.on("pageerror", (e) => errors.push(String(e)));
  await skipOpeningCheckRedirect(phone, BASE);
  await phone.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await phone.fill('input[name="username"]', USERNAME);
  await phone.fill('input[name="password"]', PASSWORD);
  await phone.click('button:has-text("登入")');
  await phone.waitForURL(`${BASE}/`);
  await phone.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  const overflow = await phone.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  ok("手機整頁不橫向捲動", overflow <= 0, `溢出 ${overflow}px`);
  const navScroll = await phone
    .getByRole("navigation", { name: "設定分區" })
    .evaluate((el) => el.scrollWidth > el.clientWidth && getComputedStyle(el).overflowX === "auto");
  ok("手機目錄可以橫滑", navScroll);
  await phone.screenshot({ path: join(SHOTS, "03-phone.png") });
  await phone.getByRole("navigation", { name: "設定分區" }).getByRole("link", { name: "購物金" }).click();
  await phone.waitForTimeout(600);
  await phone.screenshot({ path: join(SHOTS, "04-phone-store-credit.png") });

  ok("頁面無 JS 例外", errors.length === 0, errors.join(" | "));
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  // 還原店家原本的設定（煙霧不可留下改過的設定）
  await settings(token, { einvoice_enabled: original.einvoice_enabled });
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} PASS；截圖 ${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
