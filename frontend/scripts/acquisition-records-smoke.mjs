// 收購紀錄清單煙霧（2026-09-23）：用 API 造三張收購（可作廢的買斷、寄售、已作廢），確認
// 管理者在清單上看得到每一張、不能作廢的事先反灰並講原因、可直接在列上作廢；
// 篩選與賣方搜尋可用；店員（dev-clerk）看得到清單但沒有作廢鈕。
// 需 backend + frontend 已起、已 seed dev-manager 與 dev-clerk（seed_dev_user，SEED_USER_ROLE=CLERK）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/acquisition-records-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acquisition-records");
const RUN = String(Date.now()).slice(-6);
const SELLER = `陳賣家-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method === "POST" ? { "Idempotency-Key": `rec-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

async function login(page, username) {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', username);
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
}

function rowOf(page, id) {
  return page.locator("tr", { has: page.getByText(`#${id}`, { exact: true }) });
}

const browser = await chromium.launch();
const pageErrors = [];

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "20000" } });
  }
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: { name: SELLER, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const buyout = async (names) =>
    (
      await apiJson("/api/v1/acquisitions", {
        method: "POST",
        token,
        body: {
          type: "BUYOUT",
          contact_id: seller.id,
          payout_method: "CASH",
          items: names.map((name) => ({ name, grade: "A", acquisition_cost: "500", listed_price: "1200" })),
        },
      })
    ).acquisition_id;
  const voidable = await buyout(["焚火台", "營燈", "睡袋", "爐頭"]);
  const consigned = (
    await apiJson("/api/v1/acquisitions", {
      method: "POST",
      token,
      body: {
        type: "CONSIGNMENT",
        contact_id: seller.id,
        items: [{ name: "寄賣摺疊椅", grade: "A", listed_price: "900" }],
      },
    })
  ).acquisition_id;
  const alreadyVoided = await buyout(["登錄錯誤的帳篷"]);
  await apiJson(`/api/v1/acquisitions/${alreadyVoided}/void`, {
    method: "POST",
    token,
    body: { reason: "煙霧測試先作廢" },
  });
  ok("造出三張收購（可作廢／寄售／已作廢）", true, `#${voidable} #${consigned} #${alreadyVoided}`);

  // ── 管理者 ──
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await login(page, "dev-manager");
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.locator("details.acq-more summary").click();
  await page.getByRole("link", { name: "收購紀錄" }).click();
  await page.waitForURL(`${BASE}/acquisition/records`);
  ok("從收購頁「更多操作」連到收購紀錄", true);

  await rowOf(page, voidable).waitFor();
  const first = await rowOf(page, voidable).innerText();
  ok(
    "列上看得到賣方、類型、品項與付款",
    first.includes(SELLER) && first.includes("買斷") && first.includes("等 4 件") && first.includes("現金 2,000"),
    first.replace(/\s+/g, " "),
  );
  const voidBtn = (id) => rowOf(page, id).getByRole("button", { name: "作廢", exact: true });
  ok("可作廢的單作廢鈕可按", await voidBtn(voidable).isEnabled());
  ok(
    "寄售反灰並講原因",
    (await voidBtn(consigned).isDisabled()) && (await rowOf(page, consigned).innerText()).includes("寄售不能作廢"),
  );
  ok("已作廢的單反灰", await voidBtn(alreadyVoided).isDisabled());
  await page.screenshot({ path: join(SHOTS, "01-list-manager.png"), fullPage: true });

  await voidBtn(voidable).click();
  const dialog = page.getByRole("dialog", { name: "作廢收購確認" });
  await dialog.getByLabel("作廢原因").fill("賣方反悔，煙霧測試");
  await page.screenshot({ path: join(SHOTS, "02-void-dialog.png") });
  await dialog.getByRole("button", { name: "確認作廢" }).click();
  await page.getByText(`已作廢收購單 #${voidable}`).waitFor();
  await page.waitForFunction(
    (id) => [...document.querySelectorAll("tr")].some((tr) => tr.textContent?.includes(`#${id}`) && tr.textContent.includes("已作廢")),
    voidable,
  );
  ok("列上作廢成功，清單即時更新為已作廢", await voidBtn(voidable).isDisabled());
  await page.screenshot({ path: join(SHOTS, "03-after-void.png"), fullPage: true });

  await page.getByRole("button", { name: "寄售", exact: true }).click();
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".acq-records-table tbody tr")];
    return rows.length > 0 && rows.every((tr) => tr.textContent?.includes("寄售"));
  });
  ok("類型篩選：只剩寄售", true);
  await page.getByRole("button", { name: "全部類型" }).click();
  await page.getByLabel("賣方搜尋").fill(SELLER);
  await page.getByRole("button", { name: "搜尋", exact: true }).click();
  await page.waitForFunction(
    (name) => {
      const rows = [...document.querySelectorAll(".acq-records-table tbody tr")];
      return rows.length === 3 && rows.every((tr) => tr.textContent?.includes(name));
    },
    SELLER,
  );
  ok("賣方搜尋：只剩這位賣方的 3 張", true);

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度整頁不橫向捲動（表格在框內捲）", !overflow);
  await page.screenshot({ path: join(SHOTS, "04-mobile.png"), fullPage: true });
  await page.close();

  // ── 店員 ──
  const clerkPage = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  clerkPage.on("pageerror", (err) => pageErrors.push(String(err)));
  await login(clerkPage, "dev-clerk");
  await clerkPage.goto(`${BASE}/acquisition/records`, { waitUntil: "networkidle" });
  await rowOf(clerkPage, consigned).waitFor();
  ok("店員看得到收購紀錄", true);
  ok(
    "店員沒有作廢鈕",
    (await clerkPage.getByRole("button", { name: "作廢", exact: true }).count()) === 0,
  );
  await clerkPage.screenshot({ path: join(SHOTS, "05-list-clerk.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
