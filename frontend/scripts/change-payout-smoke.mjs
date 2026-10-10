// 收購「改撥款方式」（店主 2026-10-10）瀏覽器 E2E：客人反悔，購物金 ↔ 現金，一顆按鈕兩個方向。
// 1) 現金收購帳篷 $1,000 → 收購紀錄按「改撥款方式」→ 視窗「現金 → 購物金」、照目前溢價率試算購物金
//    → 送出：提示收回現金 $1,000、撥購物金；清單改成購物金；後端：抽屜收回 $1,000、客人有購物金。
// 2) 同一張再按「改撥款方式」→「購物金 → 現金」→ 送出：提示付現 $1,000、扣回購物金；客人購物金回 0。
// 3) 再改回購物金不行（購物金只能撥一次）：那列不再顯示按鈕；硬打 API 也被擋。
// 需 backend + frontend 已起、已 seed（dev-manager）。會建立收購，請指向隔離測試庫：
//   SMOKE_ALLOW_WRITE=1 node scripts/change-payout-smoke.mjs
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "change-payout");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立收購，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
async function api(method, path, body, headers = {}) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面沒有 JS 錯誤", false, String(err)));
let originalRequire = null;

async function changePayout(acqId, confirmLabel) {
  const row = page.locator("tr", { hasText: `#${acqId}` });
  await row.getByRole("button", { name: "改撥款方式" }).click();
  const dialog = page.getByRole("dialog", { name: "改撥款方式" });
  await dialog.waitFor();
  return { row, dialog, confirm: () => dialog.getByRole("button", { name: confirmLabel }).click() };
}

try {
  token = (
    await (
      await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
      })
    ).json()
  ).access_token;
  const settings = (await api("GET", "/api/v1/settings")).body;
  originalRequire = settings.require_acquisition_affidavit;
  await api("PATCH", "/api/v1/settings", { require_acquisition_affidavit: false });
  if ((await api("GET", "/api/v1/cash-sessions/current")).body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "5000" });
  }
  const credit = 1000 + Math.round(1000 * Number(settings.premium_rate));
  const run = randomUUID().slice(0, 6);
  const member = await api("POST", "/api/v1/contacts", {
    name: `改撥款會員-${run}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  });
  const acq = await api(
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: member.body.id,
      items: [{ name: `帳篷-${run}`, grade: "A", acquisition_cost: "1000", listed_price: "1800" }],
      payout_method: "CASH",
    },
    { "Idempotency-Key": `change-${run}` },
  );
  ok("API：現金收購 $1,000", acq.status === 201, `HTTP ${acq.status}`);
  const acqId = acq.body.acquisition_id;

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition/records`, { waitUntil: "networkidle" });
  await page.locator("tr", { hasText: `#${acqId}` }).filter({ hasText: "現金 1,000" }).waitFor();

  // 1) 現金 → 購物金
  const toCredit = await changePayout(acqId, "確定改成購物金");
  await toCredit.dialog.getByText(`$${credit.toLocaleString("en-US")}`).waitFor();
  const t1 = (await toCredit.dialog.textContent()) ?? "";
  ok("視窗：現金 → 購物金、收回 $1,000、試算購物金", t1.includes("現金 → 購物金") && t1.includes("$1,000"), t1);
  await page.screenshot({ path: join(SHOTS, "01-cash-to-credit.png"), fullPage: true });
  await toCredit.confirm();
  const n1 = page.getByRole("status").filter({ hasText: "已改成購物金" });
  await n1.waitFor();
  ok(
    "提示收回現金、撥購物金",
    ((await n1.textContent()) ?? "").includes(`請向客人收回現金 $1,000`) &&
      ((await n1.textContent()) ?? "").includes(`$${credit.toLocaleString("en-US")}`),
    (await n1.textContent()) ?? "",
  );
  await page.locator("tr", { hasText: `#${acqId}` }).filter({ hasText: "購物金 1,000" }).waitFor();
  const bal1 = Number((await api("GET", `/api/v1/contacts/${member.body.id}/store-credit`)).body.balance);
  ok("後端：客人拿到購物金", bal1 === credit, String(bal1));
  await page.screenshot({ path: join(SHOTS, "02-credited.png"), fullPage: true });

  // 2) 購物金 → 現金（客人又反悔）
  const toCash = await changePayout(acqId, "確定改成現金");
  const t2 = (await toCash.dialog.textContent()) ?? "";
  ok("視窗：購物金 → 現金、付現 $1,000", t2.includes("購物金 → 現金") && t2.includes("$1,000"), t2);
  await toCash.confirm();
  const n2 = page.getByRole("status").filter({ hasText: "已改成現金" });
  await n2.waitFor();
  ok("提示付現、扣回購物金", ((await n2.textContent()) ?? "").includes("請從抽屜拿現金 $1,000"), (await n2.textContent()) ?? "");
  await page.locator("tr", { hasText: `#${acqId}` }).filter({ hasText: "現金 1,000" }).waitFor();
  const bal2 = Number((await api("GET", `/api/v1/contacts/${member.body.id}/store-credit`)).body.balance);
  ok("後端：客人購物金扣回到 0", bal2 === 0, String(bal2));

  // 3) 再改回購物金：按鈕不再出現、API 也擋
  const row = page.locator("tr", { hasText: `#${acqId}` });
  ok("撥過購物金又改回現金的單不再顯示「改撥款方式」", (await row.getByRole("button", { name: "改撥款方式" }).count()) === 0);
  const again = await api("POST", `/api/v1/acquisitions/${acqId}/change-payout`, { payout_method: "STORE_CREDIT" });
  ok("硬打 API 改回購物金也被擋", again.status === 422 && again.body.detail.includes("購物金只能入帳一次"), JSON.stringify(again.body));
  await page.screenshot({ path: join(SHOTS, "03-no-more-button.png"), fullPage: true });
  const detail = await api("GET", `/api/v1/acquisitions/${acqId}`);
  ok("後端：最後是現金撥款 1000", detail.body.payout_method === "CASH" && detail.body.total_cash_paid === "1000");
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true }).catch(() => {});
} finally {
  if (originalRequire !== null) {
    await api("PATCH", "/api/v1/settings", { require_acquisition_affidavit: originalRequire });
  }
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
