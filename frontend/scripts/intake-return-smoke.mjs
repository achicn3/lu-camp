// 待整理上架：客人不賣了＋成色選不了（店主 2026-10-09 回報）瀏覽器 E2E：
// 付款後的一批（冰桶 ×3，買斷每件 $450，快速估價沒選成色）→ 整理上架頁：
// 1) 成色下拉顯示「請選成色」，選「全新」真的選得到；
// 2) 第 3 件按「客人不賣了（退回）」→ 提示收回 $450、那件從清單消失，後端記作廢、現金進抽屜；
// 3) 其餘 2 件選好成色上架成功（以前會被「上架前要選成色」擋下）。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/intake-return-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-return");
const RUN = String(Date.now()).slice(-6);
const SELLER = `退回賣家-${RUN}`;
const CATEGORY = `冰桶${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1200 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
let mgr = null;
let originalRequire = null;

try {
  mgr = (
    await api(null, "POST", "/api/v1/auth/login", {
      username: "dev-manager",
      password: "dev-test-123456",
    })
  ).json.access_token;
  originalRequire = (await api(mgr, "GET", "/api/v1/settings")).json.require_acquisition_affidavit;
  await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: false });
  if ((await api(mgr, "GET", "/api/v1/cash-sessions/current")).json === null) {
    await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  }
  const category = (await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY })).json;
  const contact = await api(mgr, "POST", "/api/v1/contacts", {
    name: SELLER,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const batch = (
    await api(mgr, "POST", "/api/v1/intake-batches", {
      contact_id: contact.json.id,
      declared_item_count: 3,
    })
  ).json;
  const line = await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/lines`, {
    short_name: "15.1L 冒險系列 冰桶",
    qty: 3,
    acquisition_type: "BUYOUT",
    expected_listed_price: "949",
    deal_cost: "450",
  });
  await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/ready`);
  await api(mgr, "PATCH", `/api/v1/intake-batches/${batch.id}/lines/${line.json.id}/disposition`, {
    disposition: "ACCEPTED",
    accepted_qty: 3,
  });
  const paid = await api(mgr, "POST", `/api/v1/intake-batches/${batch.id}/pay`, {
    payout_method: "CASH",
  });
  ok("準備：冰桶 ×3 付現 $1,350（沒選成色）", paid.json?.status === "PAID", JSON.stringify(paid.json?.detail ?? ""));
  const items = (await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}/items`)).json;
  ok("三件都沒有成色", items.length === 3 && items.every((i) => i.grade === null));
  const [first, second, third] = items;

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.route("**/print/label", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' }),
  );
  await page.goto(`${BASE}/acquisition/intake/${batch.id}/listing`, { waitUntil: "networkidle" });

  // 1) 成色：顯示「請選成色」，選全新（第一個選項）真的選得到
  const firstGrade = page.getByLabel(`${first.code} 成色`);
  await firstGrade.waitFor();
  const shown = await firstGrade.evaluate((el) => ({
    value: el.value,
    text: el.selectedOptions[0]?.textContent ?? "",
  }));
  ok("沒選成色時顯示「請選成色」", shown.value === "" && shown.text === "請選成色", JSON.stringify(shown));
  await firstGrade.selectOption("N");
  ok("選「全新」選得到", (await firstGrade.inputValue()) === "N");
  await page.screenshot({ path: join(SHOTS, "01-grade-placeholder.png"), fullPage: true });

  // 2) 第 3 件客人不賣了：退回、收回現金
  const unit = page.locator(".intake-list-unit", { hasText: third.code });
  await unit.getByRole("button", { name: "客人不賣了（退回）" }).click();
  await unit.getByRole("group", { name: `${third.code} 退回客人` }).waitFor();
  await page.screenshot({ path: join(SHOTS, "02-return-confirm.png"), fullPage: true });
  await unit.getByRole("button", { name: "確定退回" }).click();
  const notice = page.getByRole("status").filter({ hasText: "已退回客人" });
  await notice.waitFor();
  const noticeText = (await notice.textContent()) ?? "";
  ok("提示向客人收回現金 $450", noticeText.includes("請向客人收回現金 $450"), noticeText);
  await page.locator(".intake-list-unit", { hasText: third.code }).waitFor({ state: "detached" });
  ok("退回的那件從待整理消失", (await page.locator(".intake-list-unit").count()) === 2);
  await page.screenshot({ path: join(SHOTS, "03-returned.png"), fullPage: true });
  const after = (await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}/items`)).json;
  ok("後端：剩 2 件待整理", after.length === 2 && !after.some((i) => i.id === third.id));
  const voidItems = (
    await api(mgr, "GET", `/api/v1/acquisitions/${paid.json.acquisition_ids[0]}/void-items`)
  ).json;
  const voided = voidItems.filter((i) => i.voided).map((i) => i.id);
  ok(
    "收購紀錄：只有第 3 件記為作廢（不是報廢）",
    voided.length === 1 && voided[0] === third.id,
    JSON.stringify(voided),
  );

  // 3) 其餘兩件：選好成色、分類 → 上架成功
  await page.getByLabel(`${second.code} 成色`).selectOption("A");
  const categoryInput = page.getByLabel("分類", { exact: true }).last();
  await categoryInput.fill(CATEGORY);
  await page.getByRole("option", { name: CATEGORY, exact: true }).first().click();
  const publish = page.getByRole("button", { name: /上架勾選的 2 件/ });
  await publish.click();
  const listedNotice = page.getByRole("status").filter({ hasText: "已上架 2 件" });
  await listedNotice.waitFor({ timeout: 10_000 });
  ok("兩件選好成色後上架成功", true, (await listedNotice.textContent()) ?? "");
  const finalBatch = (await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}`)).json;
  ok("批次狀態＝全部上架", finalBatch.status === "LISTED", finalBatch.status);
  const listedItems = (await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}/items`)).json;
  ok(
    "上架的兩件：成色 全新／良好、分類正確",
    listedItems.every((i) => i.listed && i.category_id === category.id) &&
      listedItems.map((i) => i.grade).sort().join(",") === "A,N",
    JSON.stringify(listedItems.map((i) => [i.grade, i.category_id])),
  );
  await page.screenshot({ path: join(SHOTS, "04-listed.png"), fullPage: true });
  ok("頁面沒有未捕捉例外", pageErrors.length === 0, pageErrors.join(" | "));
} catch (error) {
  ok("流程中斷", false, String(error));
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
} finally {
  if (mgr && originalRequire !== null) {
    await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: originalRequire });
  }
  await browser.close();
}

console.log(`\n${checks - failures.length}/${checks} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failures.length === 0 ? 0 : 1);
