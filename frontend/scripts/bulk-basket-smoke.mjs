// 散裝販售籃瀏覽器煙霧（ADR-025）：甲賣 10 支營釘開新籃 → 乙賣 20 支加入同一籃（共用標籤）
// → 庫存頁看到 30 支、兩筆來源各自的成本 → POS 掃籃子標籤賣 12 支（先扣甲 10、再扣乙 2）
// → 庫存剩 18 → 掃甲那批已賣完的舊標籤，仍能賣整籃剩下的。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/bulk-basket-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "bulk-basket");
const RUN = Date.now();
const ITEM = `無品牌營釘-${String(RUN).slice(-5)}`;
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
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

async function ensureDrawer(token) {
  const current = await fetch(`${API}/api/v1/cash-sessions/current`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (current.ok && (await current.json())) return;
  await apiJson("/api/v1/cash-sessions/open", {
    method: "POST",
    token,
    body: { opening_float: "5000" },
  });
}

async function createSeller(page, name, seed) {
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', name);
  await page.fill('input[aria-label="手機"]', uniquePhone(seed));
  await page.fill('input[aria-label="身分證字號"]', validNationalId(seed));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${name}`);
}

async function fillLotAmounts(page, { cost, qty }) {
  await page.getByLabel("整堆收購成本").fill(String(cost));
  await page.getByLabel("收購基準").selectOption("BAG");
  await page.getByLabel("件數", { exact: true }).fill(String(qty));
}

async function resultText(page) {
  await page.waitForSelector("text=收購完成");
  return (await page.locator(".acq-result").textContent()) ?? "";
}

async function scan(page, code) {
  await page.fill('input[name="code"]', code);
  await page.press('input[name="code"]', "Enter");
}

async function basketRowText(page) {
  const row = page.locator("tr", { hasText: ITEM }).first();
  await row.waitFor();
  return (await row.innerText()).replace(/\s+/g, " ");
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  await ensureDrawer(token);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入", true);

  // 1) 甲：開新販售籃
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("散裝")');
  await createSeller(page, `甲-${String(RUN).slice(-5)}`, RUN);
  await page.getByLabel("開新販售籃").check();
  await page.locator('input[aria-label="名稱"]').fill(ITEM);
  await fillLotAmounts(page, { cost: 50, qty: 10 });
  await page.locator('input[aria-label="每件均一價"]').fill("20");
  await page.screenshot({ path: join(SHOTS, "01-new-basket-form.png"), fullPage: true });
  await page.click('button:has-text("送出收購")');
  const first = await resultText(page);
  const basketCode = /販售籃：(K\d+-[0-9A-F]{10})/.exec(first)?.[1];
  const lotA = /散裝編號：(L\d+-[0-9A-F]{10})/.exec(first)?.[1];
  ok("甲收購完成並開出販售籃", Boolean(basketCode && lotA), `${basketCode} / ${lotA}`);
  ok("印的是籃子的標籤", await page.locator('button:has-text("列印")').first().isVisible());
  await page.screenshot({ path: join(SHOTS, "02-new-basket-done.png"), fullPage: true });

  // 2) 乙：加入現有販售籃（名稱／售價鎖定，只填本次件數與成本）
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("散裝")');
  await createSeller(page, `乙-${String(RUN).slice(-5)}`, RUN + 1);
  await page.getByLabel("加入現有販售籃").check();
  const picker = page.getByLabel("販售籃", { exact: true });
  await picker.waitFor();
  const optionValue = await picker
    .locator("option", { hasText: ITEM })
    .first()
    .getAttribute("value");
  await picker.selectOption(optionValue ?? "");
  const nameInput = page.locator('input[aria-label="名稱"]');
  await page.waitForFunction(
    (name) => document.querySelector('input[aria-label="名稱"]')?.value === name,
    ITEM,
  );
  ok(
    "選籃後名稱與每件售價帶入並鎖定",
    (await nameInput.getAttribute("readonly")) !== null &&
      (await page.locator('input[aria-label="每件均一價"]').inputValue()) === "20",
  );
  const hint = (await page.locator(".acq-basket .hint").last().textContent()) ?? "";
  ok("顯示目前件數與歷史單件成本", hint.includes("目前 10 件") && hint.includes("單件收購成本 5 元"), hint);
  await fillLotAmounts(page, { cost: 160, qty: 20 });
  await page.locator('input[aria-label="散裝備註"]').fill("有 3 支彎掉");
  await page.screenshot({ path: join(SHOTS, "03-join-basket-form.png"), fullPage: true });
  await page.click('button:has-text("送出收購")');
  const second = await resultText(page);
  ok("乙加入同一籃", second.includes(`販售籃：${basketCode}`), second.replace(/\s+/g, " "));
  ok("提示沿用原本標籤、不必重印", second.includes("不必重印"));
  await page.screenshot({ path: join(SHOTS, "04-join-basket-done.png"), fullPage: true });

  // 3) 庫存頁：一籃 30 支、兩筆來源成本各自保留
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("販售籃")');
  const before = await basketRowText(page);
  ok("庫存看到整籃 30 支、單件成本 5–8", before.includes("30") && before.includes("5–8"), before);
  await page.locator("tr", { hasText: ITEM }).first().getByRole("button", { name: /來源/ }).click();
  await page.waitForSelector(`text=${lotA}`);
  await page.screenshot({ path: join(SHOTS, "05-inventory-basket-sources.png"), fullPage: true });

  // 4) POS：掃籃子標籤賣 12 支
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await scan(page, basketCode);
  await page.waitForSelector(`text=${ITEM}`);
  await page.getByLabel(`${ITEM} 數量`).fill("12");
  await page.waitForFunction(() =>
    document.querySelector(".pos-total strong")?.textContent?.includes("240"),
  );
  ok("POS 一籃一行、12 支合計 240", true);
  ok("乙那批的收購備註在 POS 跟著籃子提醒", await page.locator("text=有 3 支彎掉").first().isVisible());
  await page.screenshot({ path: join(SHOTS, "06-pos-basket-line.png"), fullPage: true });
  await page.click('button:has-text("結帳")');
  // 乙那批有收購備註 → 交貨前確認彈窗（與一般散裝同一套提醒）。
  await page.waitForSelector("text=交貨前請先確認");
  ok("結帳前跳出備註確認", await page.locator("text=交貨前請先確認").isVisible());
  await page.screenshot({ path: join(SHOTS, "06b-pos-note-confirm.png"), fullPage: true });
  await page.click('button:has-text("已確認，繼續結帳")');
  await page.waitForSelector("text=已完成");
  ok("現金結帳完成", true);
  await page.screenshot({ path: join(SHOTS, "07-pos-done.png"), fullPage: true });

  // 5) 庫存剩 18：甲 10 支先賣完、乙剩 18
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("販售籃")');
  const after = await basketRowText(page);
  ok("結帳後整籃剩 18 支", / 18 /.test(` ${after} `), after);
  await page.locator("tr", { hasText: ITEM }).first().getByRole("button", { name: /來源/ }).click();
  const sources = (await page.locator(".inv-basket-sources").innerText()).replace(/\s+/g, " ");
  ok("先進先出：甲那批售完、乙剩 18", sources.includes("售完") && sources.includes("18"), sources);
  await page.screenshot({ path: join(SHOTS, "08-inventory-after-sale.png"), fullPage: true });

  // 6) 掃甲那批已售完的舊標籤：改賣整籃，不報售罄
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await scan(page, lotA);
  await page.waitForSelector(`text=${ITEM}`);
  ok("舊來源標籤導到整籃", (await page.locator("text=已售罄").count()) === 0);
  await page.screenshot({ path: join(SHOTS, "09-pos-old-label.png"), fullPage: true });

  // 7) 手機寬度：收購選籃
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.click('[role="tab"]:has-text("散裝")');
  await page.getByLabel("加入現有販售籃").check();
  await page.getByLabel("販售籃", { exact: true }).waitFor();
  await page.screenshot({ path: join(SHOTS, "10-mobile-join.png"), fullPage: true });
  ok("手機寬度可選籃", true);

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
