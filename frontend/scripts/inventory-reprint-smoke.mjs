// 庫存補印標籤瀏覽器煙霧測試：登入 → /inventory 三個分頁各按一次「補印標籤」→ 經硬體代理
// (:8001 /print/label) → 顯示「✓ 已送出」，並攔下實際送出的 payload 驗品牌與全新/二手標示
// （裁示 2026-09-14：品牌獨立一行、沒品牌不印、散裝與一般商品都要能印；成色不印）。
// 需 backend(:8000) + frontend(:3000) + hardware-agent(:8001) 已起、已 seed（dev-manager），
// 且庫存中有 IN_STOCK 序號品與 ON_SALE 散裝批（可先跑 acquisition / seed 灌料）。
// 執行：mcr playwright 容器內 node scripts/inventory-reprint-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// 攔下真正送到硬體代理的標籤內容——畫面顯示「已送出」不代表內容對。
const labels = [];
page.on("request", (req) => {
  if (!req.url().includes("/print/label")) return;
  try {
    labels.push(JSON.parse(req.postData() ?? "{}"));
  } catch {
    labels.push({ parseError: req.postData() });
  }
});

/** 把清單收斂到還在架上的品項——已售出／售罄的列本來就沒有補印鈕。 */
async function filterInStock(label) {
  const status = page.locator('select[aria-label="狀態"]').first();
  if ((await status.count()) === 0) return;
  await status.selectOption({ label });
  await page.waitForSelector(".inv-table tbody tr", { timeout: 15000 });
}

/** 第一顆可用補印鈕所在那一列的文字（用來看它的成色）。 */
async function firstReprintRowText() {
  const row = page.locator(".inv-table tbody tr").filter({ has: page.locator(".inv-reprint-btn") });
  return (await row.first().textContent()) ?? "";
}

/** 按下某一列的補印鈕，等代理回應，回傳這次送出的 payload。 */
async function reprintFirstRow(tab) {
  const before = labels.length;
  const btn = page.locator(".inv-table tbody tr .inv-reprint-btn").first();
  await btn.waitFor({ timeout: 15000 });
  ok(`${tab}列有「補印標籤」鈕`, true);
  await btn.click();
  await page.waitForSelector(".inv-reprint-ok, .inv-reprint-err", { timeout: 15000 });
  const okCount = await page.locator(".inv-reprint-ok").count();
  ok(
    `${tab}補印送出成功`,
    okCount > 0,
    okCount > 0 ? "✓ 已送出" : ((await page.locator(".inv-reprint-err").getAttribute("title")) ?? ""),
  );
  return labels.length > before ? labels[labels.length - 1] : null;
}

/** 驗標籤內容：全新/二手標示正確、品牌欄位存在（沒品牌是 null，代表那一行不印）。 */
function checkLabel(tab, payload, expectedCondition) {
  if (payload === null) {
    ok(`${tab}標籤內容`, false, "沒攔到 /print/label 請求");
    return;
  }
  ok(
    `${tab}標籤標示「${expectedCondition}」`,
    payload.condition === expectedCondition,
    `condition=${JSON.stringify(payload.condition)}`,
  );
  const brandOk = payload.brand === null || typeof payload.brand === "string";
  ok(`${tab}標籤帶品牌欄位`, brandOk, `brand=${JSON.stringify(payload.brand)}`);
  // 成色不印：標示只能是「全新」或「二手」，不得夾帶 A/B/C。
  ok(
    `${tab}標籤不印成色`,
    payload.condition === "全新" || payload.condition === "二手",
    `condition=${JSON.stringify(payload.condition)}`,
  );
}

try {
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  const optionsUrl = "**/api/v1/serialized-items/filter-options*";
  let releaseOptions;
  const optionsReady = new Promise((resolve) => { releaseOptions = resolve; });
  await page.route(optionsUrl, async (route) => { await optionsReady; await route.continue(); });

  // 2) 進庫存頁（預設序號品分頁）
  await page.click('a:has-text("庫存")');
  await page.waitForURL(`${BASE}/inventory`);
  await page.waitForSelector('[role="tab"]:has-text("序號品")');
  await page.waitForSelector(".inv-table tbody tr");
  const pendingPrint = page.locator(".inv-reprint-btn").first();
  await pendingPrint.waitFor();
  ok("品牌仍在載入時停用補印", await pendingPrint.isDisabled());
  ok("品牌載入中不送印", labels.length === 0);
  await page.screenshot({ path: `${SHOTS}/inv-reprint-00-brand-pending.png` });
  releaseOptions();
  await page.waitForFunction(() => !document.querySelector(".inv-reprint-btn")?.disabled);
  await page.unroute(optionsUrl);
  await filterInStock("在庫");
  await page.screenshot({ path: `${SHOTS}/inv-reprint-01-serialized.png` });

  // 3) 序號品：只有成色「全新未拆」印全新，其餘印二手（2026-09-16）——依那一列實際的成色判斷。
  const serializedRow = await firstReprintRowText();
  const serializedExpect = serializedRow.includes("全新未拆") ? "全新" : "二手";
  checkLabel("序號品", await reprintFirstRow("序號品"), serializedExpect);
  await page.screenshot({ path: `${SHOTS}/inv-reprint-02-sent.png` });

  // 4) 一般商品：採購進來的＝全新（這個分頁本來沒有補印鈕，裁示後才加）
  await page.click('[role="tab"]:has-text("一般商品")');
  await page.waitForSelector(".inv-table tbody tr");
  checkLabel("一般商品", await reprintFirstRow("一般商品"), "全新");
  await page.screenshot({ path: `${SHOTS}/inv-reprint-03-catalog.png` });

  // 5) 散裝批：一樣是收購進來的＝二手
  await page.click('[role="tab"]:has-text("散裝")');
  await page.waitForSelector(".inv-table tbody tr");
  await filterInStock("販售中");
  checkLabel("散裝", await reprintFirstRow("散裝"), "二手");
  await page.screenshot({ path: `${SHOTS}/inv-reprint-04-bulk.png` });

  // 5b) 成色「全新未拆」一定印「全新」：直接用成色篩選，不靠清單第一列剛好是哪一件。
  //     （先跑 `SMOKE_GRADE=N node scripts/label-print-smoke.mjs` 會收進一件全新未拆的品項。）
  await page.click('[role="tab"]:has-text("序號品")');
  await page.waitForSelector(".inv-table tbody tr");
  await filterInStock("在庫");
  const gradeSelect = page.locator('select[aria-label="成色"]').first();
  const gradeOptions = await gradeSelect.locator("option").allTextContents();
  if (!gradeOptions.includes("全新未拆")) {
    ok("有全新未拆的在庫品可供驗證", false, `成色選項：${gradeOptions.join("、")}`);
  } else {
    await gradeSelect.selectOption({ label: "全新未拆" });
    await page.waitForSelector(".inv-table tbody tr");
    const newPayload = await reprintFirstRow("全新未拆的序號品");
    ok(
      "全新未拆的標籤印「全新」",
      newPayload !== null && newPayload.condition === "全新",
      `condition=${JSON.stringify(newPayload?.condition)}`,
    );
    await page.screenshot({ path: `${SHOTS}/inv-reprint-04b-new-unopened.png` });
    await gradeSelect.selectOption({ label: "全部成色" });
  }

  // 6) 品牌真的會送到標籤：挑一個實際有品牌的篩選值，印出來的 payload 必須帶那個名字。
  //    （前面三張是清單第一列，可能剛好都沒品牌＝驗到的是「不印那一行」那條規則。）
  await page.click('[role="tab"]:has-text("序號品")');
  await page.waitForSelector(".inv-table tbody tr");
  await filterInStock("在庫");
  const brandSelect = page.locator('select[aria-label="品牌"]').first();
  const brandNames = (await brandSelect.locator("option").allTextContents()).filter(
    (t) => t !== "全部品牌",
  );
  if (brandNames.length === 0) {
    ok("有品牌的序號品可供驗證", false, "庫存裡沒有任何掛品牌的序號品");
  } else {
    await brandSelect.selectOption({ label: brandNames[0] });
    await page.waitForSelector(".inv-table tbody tr");
    const payload = await reprintFirstRow(`品牌「${brandNames[0]}」的序號品`);
    ok(
      "標籤帶到品牌名",
      payload !== null && payload.brand === brandNames[0],
      `brand=${JSON.stringify(payload?.brand)}，期望 ${JSON.stringify(brandNames[0])}`,
    );
    await page.screenshot({ path: `${SHOTS}/inv-reprint-05-branded.png` });
  }
  await page.route(optionsUrl, (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "test lookup unavailable" }) }));
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(/品牌名稱尚未取得/).first().waitFor();
  ok("品牌查詢失敗停用補印", await page.locator(".inv-reprint-btn").first().isDisabled());
  await page.screenshot({ path: `${SHOTS}/inv-reprint-06-brand-failed.png` });
  await page.unroute(optionsUrl);
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => !document.querySelector(".inv-reprint-btn")?.disabled);
  ok("重新整理取得品牌後可補印", await page.locator(".inv-reprint-btn").first().isEnabled());
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
