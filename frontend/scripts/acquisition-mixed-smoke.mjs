// 收購① 煙霧：收購頁「買斷」再加一堆散裝 → 一次送出、只開一次錢櫃 → 成立買斷一張＋散裝一張；
// 一起收時不能選混合撥款；兩張單的標籤都印得出來。
// 第二輪：送顧客螢幕簽一次（內容含買斷逐件＋「營釘 ×30」）→ 客人選購物金簽名 → 送出兩張都撥購物金。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/acquisition-mixed-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "acquisition-mixed");
const RUN = String(Date.now()).slice(-6);
const SELLER = `混收賣家-${RUN}`;
const CATEGORY = `帳篷${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function drawSignature(target) {
  const canvas = target.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  const pts = [[0.15, 0.5], [0.3, 0.25], [0.45, 0.7], [0.6, 0.3], [0.75, 0.6], [0.85, 0.4]];
  await target.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await target.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await target.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await target.mouse.up();
}

async function fillCombined(sellerName) {
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', sellerName);
  await page.fill('input[aria-label="手機"]', uniquePhone());
  await page.fill('input[aria-label="身分證字號"]', validNationalId());
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${sellerName}`);
  await page.locator('.acq-row summary:has-text("品名")').first().click();
  await page.fill('input[aria-label="品名"]', "雙人帳篷");
  await page.locator(".acq-row select").first().selectOption("A");
  const cat = page.locator(".acq-row").first().getByLabel("分類");
  await cat.click();
  await cat.fill(CATEGORY);
  await page.locator(".acq-row").first().getByRole("option", { name: CATEGORY, exact: true }).click();
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "3000");
  await page.getByRole("button", { name: "＋ 同一位客人還有散裝" }).click();
  const extra = page.locator(".acq-extra-lot").first();
  await extra.getByRole("textbox", { name: "名稱" }).fill("營釘");
  await extra.getByLabel("整堆收購成本").fill("150");
  await extra.getByLabel("收購基準").selectOption("BAG");
  await extra.getByLabel("件數").fill("30");
  await extra.getByRole("textbox", { name: "每件均一價" }).fill("20");
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

const browser = await chromium.launch();
const INSTALLATION = crypto.randomUUID();
const kiosk = await browser.newPage({ viewport: { width: 834, height: 1112 } });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
await page.addInitScript((id) => {
  window.localStorage.setItem("lu-camp.pos-terminal.installation", id);
}, INSTALLATION);
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const mgr = (await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })).json.access_token;
  await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  await api(mgr, "POST", "/api/v1/categories", { name: CATEGORY });
  const before = await api(mgr, "GET", "/api/v1/settings");
  const requireSign = before.json.require_acquisition_affidavit;
  if (requireSign) await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: false });

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  let drawer = 0;
  const labels = [];
  await page.route("**/drawer/open", (route) => {
    drawer += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.route("**/print/label", (route) => {
    labels.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  // 賣方
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', SELLER);
  await page.fill('input[aria-label="手機"]', uniquePhone());
  await page.fill('input[aria-label="身分證字號"]', validNationalId());
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${SELLER}`);

  // 買斷一件：帳篷，收購價 1000、上架 3000
  await page.locator('.acq-row summary:has-text("品名")').first().click();
  await page.fill('input[aria-label="品名"]', "雙人帳篷");
  await page.locator(".acq-row select").first().selectOption("A");
  const cat = page.locator(".acq-row").first().getByLabel("分類");
  await cat.click();
  await cat.fill(CATEGORY);
  await page.locator(".acq-row").first().getByRole("option", { name: CATEGORY, exact: true }).click();
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "3000");

  ok(
    "沒有散裝時只有一顆小按鈕、不顯示散裝區",
    (await page.locator(".acq-extra-lots").count()) === 0 &&
      (await page.getByRole("button", { name: "＋ 同一位客人還有散裝" }).count()) === 1,
  );
  await page.screenshot({ path: join(SHOTS, "00-no-bulk.png"), fullPage: true });
  // 再加一堆散裝：營釘 30 件、整堆 150、每件 20
  await page.getByRole("button", { name: "＋ 同一位客人還有散裝" }).click();
  const extra = page.locator(".acq-extra-lot").first();
  await extra.getByRole("textbox", { name: "名稱" }).fill("營釘");
  await extra.getByLabel("整堆收購成本").fill("150");
  await extra.getByLabel("收購基準").selectOption("BAG");
  await extra.getByLabel("件數").fill("30");
  await extra.getByRole("textbox", { name: "每件均一價" }).fill("20");
  const summary = await page.getByRole("region", { name: "收購摘要" }).innerText();
  ok("摘要：件數與應付含散裝（31 件、1,150）", summary.includes("31 件") && summary.includes("1,150"), summary.replace(/\s+/g, " "));
  const splitRadio = page.locator('.acq-payout-mode', { hasText: "混合" }).locator("input");
  ok("一起收時不能選混合撥款", await splitRadio.isDisabled());
  await page.screenshot({ path: join(SHOTS, "01-filled.png"), fullPage: true });

  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  const done = await page.locator(".acq-result").innerText();
  ok("完成畫面寫出買斷與散裝兩張單號", /買斷 #\d+、散裝 #\d+/.test(done), done.split("\n")[0]);
  ok("付現只開一次錢櫃", drawer === 1, `drawer=${drawer}`);
  const labelButtons = page.locator(".acq-result").getByRole("button", { name: /列印標籤|重新列印標籤/ });
  ok("兩張單各有列印標籤", (await labelButtons.count()) === 2, String(await labelButtons.count()));
  for (const button of await labelButtons.all()) {
    if ((await button.innerText()).startsWith("列印標籤")) await button.click();
  }
  await page.waitForTimeout(800);
  ok("帳篷與營釘的標籤都送出", labels.some((l) => l.name === "雙人帳篷") && labels.some((l) => l.name === "營釘"), JSON.stringify(labels.map((l) => l.name)));
  await page.screenshot({ path: join(SHOTS, "02-done.png"), fullPage: true });

  const records = await api(mgr, "GET", `/api/v1/acquisitions?q=${encodeURIComponent(SELLER)}&limit=10`);
  const mine = records.json.items;
  ok(
    "收購紀錄：同一位賣方一張買斷（1000）一張散裝（150）",
    mine.length === 2 &&
      mine.some((a) => a.type === "BUYOUT" && a.total_cash_paid === "1000") &&
      mine.some((a) => a.type === "BULK_LOT" && a.total_cash_paid === "150"),
    JSON.stringify(mine.map((a) => [a.type, a.total_cash_paid])),
  );
  // ── 第二輪：一起收也能簽一次名（客人選購物金）──
  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kiosk.fill('input[name="username"]', "dev-kiosk");
  await kiosk.fill('input[name="password"]', "dev-test-123456");
  await kiosk.click('button:has-text("啟用裝置")');
  await kiosk.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const code = (await kiosk.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", {
    installation_id: INSTALLATION,
    name: `一起收煙霧 ${RUN}`,
  });
  await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, { pairing_code: code });
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  const member = `${SELLER}-會員`;
  await fillCombined(member);
  const created = await api(mgr, "GET", `/api/v1/contacts?q=${encodeURIComponent(member)}`);
  const contact = (Array.isArray(created.json) ? created.json : created.json.items ?? [])[0];
  await api(mgr, "PATCH", `/api/v1/contacts/${contact.id}`, { roles: ["SELLER", "MEMBER"] });
  await page.getByRole("button", { name: "送至手持裝置簽署" }).click();
  await kiosk.waitForSelector("button.kiosk-payout-btn", { timeout: 10000 });
  const body = await kiosk.textContent(".kiosk-task-body");
  ok("顧客螢幕：買斷帳篷＋「營釘 ×30」、總額 1,150", body.includes("雙人帳篷") && body.includes("營釘 ×30") && body.includes("1,150"));
  await kiosk.screenshot({ path: join(SHOTS, "03-kiosk.png"), fullPage: true });
  await kiosk.check('.kiosk-agree-check input[type="checkbox"]');
  await kiosk.click('button.kiosk-payout-btn:has-text("購物金")');
  await drawSignature(kiosk);
  await kiosk.click("button.kiosk-submit");
  await page.waitForSelector("text=客人已完成簽署", { timeout: 10000 });
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  const signedDone = await page.locator(".acq-result").innerText();
  ok("簽過名的一起收也成立兩張單", /買斷 #\d+、散裝 #\d+/.test(signedDone), signedDone.split("\n")[0]);
  const signedRecords = await api(mgr, "GET", `/api/v1/acquisitions?q=${encodeURIComponent(member)}&limit=10`);
  ok(
    "兩張都撥購物金（客人選的）",
    signedRecords.json.items.length === 2 && signedRecords.json.items.every((a) => a.payout_method === "STORE_CREDIT"),
    JSON.stringify(signedRecords.json.items.map((a) => [a.type, a.payout_method])),
  );
  await page.screenshot({ path: join(SHOTS, "04-signed-done.png"), fullPage: true });

  if (requireSign) await api(mgr, "PATCH", "/api/v1/settings", { require_acquisition_affidavit: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  await browser.close();
}
