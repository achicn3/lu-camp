// 排隊收購：估價時直接選類型（docs/42 §13；店主 2026-10-02）。報到 4 件 → 1 二手、2 全新、3 散裝（整堆總價、
// 件數）、4 寄售（寄售售價）→ 輸入框在平板與手機寬度都不被擠窄 → 估完 → 客人勾選看到寄售售價、散裝件數 →
// 簽署頁寄售品列在同一張表、不進合計 → 回上一頁只留寄售 → 不用選收款方式、簽名 → 店員按「確認收下寄售」→
// 後端成立寄售收購、簽署已用掉。需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：node scripts/intake-types-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-types");
const RUN = String(Date.now()).slice(-6);
const SELLER = `類型賣家-${RUN}`;
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
  if (!box) throw new Error("找不到簽名畫布");
  const pts = [[0.15, 0.5], [0.3, 0.25], [0.45, 0.7], [0.6, 0.3], [0.75, 0.6], [0.85, 0.4]];
  await target.mouse.move(box.x + box.width * pts[0][0], box.y + box.height * pts[0][1]);
  await target.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await target.mouse.move(box.x + box.width * fx, box.y + box.height * fy, { steps: 12 });
  }
  await target.mouse.up();
}

async function inputWidths(page) {
  return page.$$eval("input[data-quick-input]", (els) => els.map((el) => Math.round(el.getBoundingClientRect().width)));
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 820, height: 1180 }, hasTouch: true });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  const prints = [];
  await page.route("**/print/**", (route) => {
    prints.push({ url: route.request().url(), body: route.request().postDataJSON() });
    return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ok"}' });
  });
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(SELLER);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByLabel("收購幾件").fill("4");
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  const batchId = Number(/\/acquisition\/intake\/(\d+)/.exec(page.url())?.[1]);
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const batchApi = async () =>
    (await fetch(`${API}/api/v1/intake-batches/${batchId}`, { headers: { Authorization: `Bearer ${token}` } })).json();

  const group = (n) => page.getByRole("group", { name: `${n} 號 類型` });
  await group(1).waitFor();
  ok(
    "每件四個類型按鈕、預設二手",
    (await group(1).getByRole("button").allInnerTexts()).join() === "二手,全新,散裝,寄售" &&
      (await group(1).getByRole("button", { name: "二手" }).getAttribute("aria-pressed")) === "true",
  );
  await page.getByLabel("1 號 收購價").fill("300");
  await page.getByLabel("1 號 收購價").press("Enter");
  await group(2).getByRole("button", { name: "全新" }).click();
  await page.getByLabel("2 號 收購價").fill("800");
  await page.getByLabel("2 號 收購價").press("Enter");
  await group(3).getByRole("button", { name: "散裝" }).click();
  await page.getByLabel("3 號 整堆總價").fill("50");
  await page.getByLabel("3 號 整堆總價").press("Enter");
  ok("散裝按 Enter 跳到件數", await page.getByLabel("3 號 件數（可不填）").evaluate((el) => el === document.activeElement));
  await page.getByLabel("3 號 件數（可不填）").fill("10");
  await page.getByLabel("3 號 件數（可不填）").press("Enter");
  await group(4).getByRole("button", { name: "寄售" }).click();
  await page.getByLabel("4 號 寄售售價").fill("3000");
  await page.getByLabel("4 號 寄售售價").press("Enter");
  await page.getByText(/已填 4／4 件/).waitFor();
  const widths = await inputWidths(page);
  ok("平板寬度：價格輸入框不被擠窄（≥ 140px，件數 ≥ 100px）", widths.length === 5 && widths.every((w) => w >= 100) && widths.filter((w) => w >= 140).length === 4, JSON.stringify(widths));
  await page.screenshot({ path: join(SHOTS, "01-types-tablet.png") });

  let saved = await batchApi();
  ok(
    "後端：二手、全新（成色 N）、散裝（整堆 50、10 件）、寄售（售價 3000、抽成預設）",
    JSON.stringify(saved.lines.map((l) => [l.acquisition_type, l.grade, l.deal_cost, l.bulk_piece_count, l.expected_listed_price])) ===
      JSON.stringify([
        ["BUYOUT", null, "300", null, null],
        ["BUYOUT", "N", "800", null, null],
        ["BULK_LOT", null, "50", 10, null],
        ["CONSIGNMENT", null, null, null, "3000"],
      ]) && saved.lines[3].commission_pct !== null,
    JSON.stringify(saved.lines.map((l) => [l.acquisition_type, l.grade, l.deal_cost, l.bulk_piece_count, l.expected_listed_price, l.commission_pct])),
  );

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  const narrow = await inputWidths(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  ok("手機寬度：輸入框仍 ≥ 100px、整頁不橫向捲動", narrow.every((w) => w >= 100) && !overflow, JSON.stringify(narrow));
  await page.screenshot({ path: join(SHOTS, "02-types-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 820, height: 1180 });

  await page.getByRole("button", { name: "估完，給客人確認" }).click();
  await page.getByRole("button", { name: "交給客人勾選" }).click();
  const sheet = page.getByRole("dialog", { name: /確認要賣的商品/ });
  await sheet.waitFor();
  const sheetText = await sheet.innerText();
  ok("勾選清單：寄售顯示寄售售價、散裝帶件數、合計只算要付錢的", sheetText.includes("寄售 $3,000") && sheetText.includes("×10") && /\$1,150/.test(sheetText), sheetText.replace(/\n/g, " "));
  await sheet.getByRole("button", { name: "確認" }).click();
  const signing = page.getByRole("dialog", { name: "簽署切結書" });
  await signing.waitFor();
  const table = await signing.locator(".intake-sign-items").innerText();
  ok("簽署頁：寄售品在同一張表、寫售價與抽成", /寄售・售價 \$3,000・抽成 \d+%/.test(table) && table.includes("×10"), table.replace(/\n/g, " "));
  ok("簽署頁合計只算要付錢的 $1,150", (await signing.locator(".intake-customer-total").innerText()).includes("$1,150"));
  await page.screenshot({ path: join(SHOTS, "03-signing-mixed.png") });

  // 客人改主意：只寄售
  await signing.getByRole("button", { name: /回上一頁/ }).click();
  for (const n of [1, 2, 3]) await sheet.getByRole("checkbox", { name: new RegExp(`${n} 號`) }).uncheck();
  await sheet.getByRole("button", { name: "確認" }).click();
  await signing.waitFor();
  ok("只賣寄售：簽署頁沒有收款方式", (await signing.getByText("請選擇收款方式").count()) === 0);
  await signing.getByRole("checkbox", { name: /同意/ }).check();
  await drawSignature(page);
  await page.screenshot({ path: join(SHOTS, "04-signing-consign-only.png") });
  await signing.getByRole("button", { name: "確認並送出" }).click();
  await page.getByText(/請把平板交還給店員/).waitFor();
  await page.getByRole("button", { name: "交還店員" }).click();
  const panel = page.getByLabel("簽名與付款");
  await panel.getByText(/客人已簽名（只有寄售/).waitFor({ timeout: 8000 });
  ok("店員畫面：客人已簽名、只有寄售", true);
  await panel.getByRole("button", { name: /確認收下寄售 1 件/ }).click();
  await page.getByLabel("付款結果").waitFor({ timeout: 8000 });
  saved = await batchApi();
  ok("收下寄售：已付款待整理、成立 1 張收購", saved.status === "PAID" && saved.acquisition_ids.length === 1, `${saved.status} ${saved.acquisition_ids}`);
  const task = await (
    await fetch(`${API}/api/v1/signing/tasks/${saved.signature_task_id}`, { headers: { Authorization: `Bearer ${token}` } })
  ).json();
  ok("簽署已用掉、沒有收款方式", task.status === "CONSUMED" && task.chosen_payout === null, `${task.status} ${task.chosen_payout}`);
  await page.screenshot({ path: join(SHOTS, "05-paid.png"), fullPage: true });
  await page.getByLabel("付款結果").getByRole("button", { name: "列印收購明細（含簽名）" }).click();
  await page.getByText("收購明細已送出列印").waitFor({ timeout: 8000 });
  const receipt = prints.find((p) => p.url.endsWith("/print/acquisition"))?.body;
  ok(
    "只賣寄售也印得出收購明細：寄售品帶售價與抽成、沒有撥款方式",
    receipt?.items.length === 0 &&
      receipt.total === "0" &&
      receipt.payout_method === null &&
      receipt.consignments?.[0]?.listed_price === "3000" &&
      receipt.signature_png_base64.length > 100,
    JSON.stringify({ ...receipt, signature_png_base64: "…" }),
  );

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
