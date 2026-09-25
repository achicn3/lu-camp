// 排隊收購 I3 煙霧（docs/42 §6）：叫號確認（買斷 2＋散裝 10＋寄售 1）→ 送顧客螢幕 → 處置鎖住 →
// 客人在顧客螢幕看到品項金額（寄售不在內）、選現金、簽名 → 店員按付款 → 開錢櫃、已付款待整理、
// 成立三張收購（買斷／寄售／散裝）且商品都是「待整理」→ 列印整批收購明細（含簽名）。
// 另驗設定頁「收購一定要簽名」開關：打開後叫號確認頁不再出現「不簽名直接付款」（結束時還原）。
// 需 backend + frontend 已起、已 seed（dev-manager、dev-kiosk）。執行：node scripts/intake-payment-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-payment");
const RUN = String(Date.now()).slice(-6);
const SELLER = `付款賣家-${RUN}`;
const CHAIR = `黑色折疊椅${RUN}`;
const INSTALLATION = crypto.randomUUID();
let originalRequire = null;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin(username) {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password: "dev-test-123456" }),
  });
  if (!res.ok) throw new Error(`login ${username} failed: ${res.status}`);
  return (await res.json()).access_token;
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function drawSignature(page) {
  const canvas = page.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("找不到簽名畫布");
  const pts = [
    [0.15, 0.5],
    [0.3, 0.25],
    [0.45, 0.7],
    [0.6, 0.3],
    [0.75, 0.6],
    [0.85, 0.4],
  ];
  await page.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await page.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await page.mouse.up();
}

const browser = await chromium.launch();
const kiosk = await browser.newPage({ viewport: { width: 834, height: 1112 } });
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const mgr = await apiLogin("dev-manager");

  // 顧客螢幕：啟用裝置 → 配對到這台櫃檯（櫃檯的安裝碼預先寫進瀏覽器）
  await kiosk.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await kiosk.fill('input[name="username"]', "dev-kiosk");
  await kiosk.fill('input[name="password"]', "dev-test-123456");
  await kiosk.click('button:has-text("啟用裝置")');
  await kiosk.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const pairingCode = (await kiosk.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await api(mgr, "POST", "/api/v1/customer-display/terminals", {
    installation_id: INSTALLATION,
    name: `排隊收購煙霧櫃檯 ${RUN}`,
  });
  const paired = await api(mgr, "POST", `/api/v1/customer-display/terminals/${terminal.json.id}/pair`, {
    pairing_code: pairingCode,
  });
  ok("顧客螢幕與櫃檯配對", paired.status === 200, `status=${paired.status}`);
  await page.addInitScript((id) => {
    window.localStorage.setItem("lu-camp.pos-terminal.installation", id);
  }, INSTALLATION);

  // 資料準備（估價與叫號畫面由 intake-queue-smoke 驗）：一批三種類型、全部接受
  await api(mgr, "POST", "/api/v1/cash-sessions/open", { opening_float: "1000" });
  const contact = await api(mgr, "POST", "/api/v1/contacts", {
    name: SELLER,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  });
  const batch = await api(mgr, "POST", "/api/v1/intake-batches", {
    contact_id: contact.json.id,
    declared_item_count: 13,
  });
  const batchId = batch.json.id;
  const lines = [
    { short_name: CHAIR, qty: 2, acquisition_type: "BUYOUT", reference_price: "1000", discount_pct: 50, expected_listed_price: "500", deal_cost: "250", grade: "B" },
    { short_name: "營釘", qty: 10, acquisition_type: "BULK_LOT", expected_listed_price: "20", deal_cost: "5" },
    { short_name: "帳篷", qty: 1, acquisition_type: "CONSIGNMENT", expected_listed_price: "6000", commission_pct: 40, grade: "A" },
  ];
  const lineIds = [];
  for (const line of lines) {
    const res = await api(mgr, "POST", `/api/v1/intake-batches/${batchId}/lines`, line);
    lineIds.push(res.json.id);
  }
  const ready = await api(mgr, "POST", `/api/v1/intake-batches/${batchId}/ready`);
  ok("估完送叫號", ready.status === 200, JSON.stringify(ready.json?.detail ?? ""));
  for (const [index, qty] of [2, 10, 1].entries()) {
    await api(mgr, "PATCH", `/api/v1/intake-batches/${batchId}/lines/${lineIds[index]}/disposition`, {
      disposition: "ACCEPTED",
      accepted_qty: qty,
    });
  }

  // 店員：叫號確認頁
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const before = await api(mgr, "GET", "/api/v1/settings");
  originalRequire = before.json.require_acquisition_affidavit;
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  const toggle = page.locator('input[name="require_acquisition_affidavit"]');
  await toggle.check();
  await toggle.evaluate((el) => el.closest("form").requestSubmit());
  await page.getByText("設定已儲存").waitFor();
  const after0 = await api(mgr, "GET", "/api/v1/settings");
  ok("設定頁可打開「收購一定要簽名」", after0.json.require_acquisition_affidavit === true);
  await page.screenshot({ path: join(SHOTS, "00-settings.png"), fullPage: true });

  let drawerOpened = 0;
  const receiptPrints = [];
  await page.route("**/print/acquisition", (route) => {
    receiptPrints.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.route("**/drawer/open", (route) => {
    drawerOpened += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.goto(`${BASE}/acquisition/intake/${batchId}`, { waitUntil: "networkidle" });
  const panel = page.getByLabel("簽名與付款");
  await panel.waitFor();
  const payout = await page.getByLabel("要付給客人").innerText();
  ok("要付給客人 $550（買斷 500＋散裝 50）", payout.includes("$550"), payout.replace(/\n/g, " "));
  ok(
    "規定要簽名時沒有「不簽名直接付款」、也沒有付款鈕",
    (await panel.getByText("不簽名直接付款").count()) === 0 &&
      (await panel.getByRole("button", { name: /^付款/ }).count()) === 0,
  );
  await page.screenshot({ path: join(SHOTS, "01-confirm.png"), fullPage: true });

  await panel.getByRole("button", { name: "送到顧客螢幕給客人簽名" }).click();
  await panel.getByText(/已送到顧客螢幕|客人正在核對/).waitFor({ timeout: 8000 });
  ok("送簽後處置鎖住", await page.getByLabel("第 1 列處置").first().isDisabled());
  ok("送簽後不能取消整批", (await page.getByRole("button", { name: "取消整批" }).count()) === 0);
  await page.screenshot({ path: join(SHOTS, "02-waiting.png"), fullPage: true });

  // 客人：顧客螢幕上核對並簽名（選現金）
  await kiosk.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 10000 });
  await kiosk.waitForSelector("button.kiosk-payout-btn", { timeout: 8000 });
  const body = await kiosk.textContent(".kiosk-task-body");
  ok(
    "顧客螢幕：買斷逐件、散裝帶件數、總額 550、寄售不在內",
    body.includes("黑色折疊椅") && body.includes("營釘 ×10") && body.includes("550") && !body.includes("帳篷"),
  );
  await kiosk.screenshot({ path: join(SHOTS, "03-kiosk.png"), fullPage: true });
  await kiosk.check('.kiosk-agree-check input[type="checkbox"]');
  await kiosk.click('button.kiosk-payout-btn:has-text("現金")');
  await drawSignature(kiosk);
  await kiosk.click("button.kiosk-submit");

  await panel.getByText(/客人已簽名，選擇拿現金/).waitFor({ timeout: 10000 });
  ok("店員畫面：客人已簽名、選現金", true);
  await page.screenshot({ path: join(SHOTS, "04-signed.png"), fullPage: true });

  await panel.getByRole("button", { name: /付款 \$550（現金）/ }).click();
  const result = page.getByLabel("付款結果");
  await result.waitFor({ timeout: 10000 });
  const resultText = await result.innerText();
  ok("付款後提示拿 $550、商品進待整理", resultText.includes("請從錢櫃拿 $550") && resultText.includes("待整理"), resultText.replace(/\n/g, " "));
  ok("付現有開錢櫃", drawerOpened === 1, `drawer=${drawerOpened}`);
  ok("狀態：已付款待整理", (await page.locator(".intake-summary").innerText()).includes("已付款待整理"));
  await page.screenshot({ path: join(SHOTS, "05-paid.png"), fullPage: true });

  await result.getByRole("button", { name: "列印收購明細（含簽名）" }).click();
  await result.getByText("收購明細已送出列印").waitFor({ timeout: 8000 });
  const printed = receiptPrints[0];
  ok(
    "收購明細：整批品項、總額 550、現金、列出全部收購單號、附簽名",
    receiptPrints.length === 1 &&
      printed.items.length === 3 &&
      printed.items[2].name === "營釘 ×10" &&
      printed.total === "550" &&
      printed.payout_method === "CASH" &&
      /排隊收購 A\d{3}，收購單 #\d+、#\d+、#\d+/.test(printed.reference) &&
      printed.signature_png_base64.length > 100,
    JSON.stringify({ ...printed, signature_png_base64: "…" }),
  );
  await page.screenshot({ path: join(SHOTS, "06-receipt-printed.png"), fullPage: true });

  const after = await api(mgr, "GET", `/api/v1/intake-batches/${batchId}`);
  ok("成立三張收購（買斷／寄售／散裝）", after.json.acquisition_ids.length === 3, JSON.stringify(after.json.acquisition_ids));
  const pending = await api(mgr, "GET", `/api/v1/serialized-items?status=PENDING_LISTING&q=${encodeURIComponent(CHAIR)}&limit=200`);
  const items = Array.isArray(pending.json) ? pending.json : (pending.json?.items ?? []);
  const mine = items.filter((item) => item.name === CHAIR);
  ok("買斷的 2 件在庫存是「待整理」", mine.length === 2 && mine.every((item) => item.status === "PENDING_LISTING"), `status=${pending.status} n=${mine.length}`);

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  await kiosk.screenshot({ path: join(SHOTS, "99-kiosk.png"), fullPage: true });
  console.log(String(error));
  process.exitCode = 1;
} finally {
  if (typeof originalRequire === "boolean") {
    await api(await apiLogin("dev-manager"), "PATCH", "/api/v1/settings", {
      require_acquisition_affidavit: originalRequire,
    });
  }
  await browser.close();
}
