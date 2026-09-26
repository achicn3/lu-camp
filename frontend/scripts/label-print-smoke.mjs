// B3 標籤列印瀏覽器煙霧測試：登入 → /acquisition → 買斷一件 → 收購完成卡片出現
// 「列印標籤（N 張）」按鈕 → 點擊 → 經硬體代理（:8001 /print/label）→ 顯示「已送出 N 張標籤」。
// 需 backend(:8000) + frontend(:3000) + hardware-agent(:8001) 已起、已 seed（dev-manager + 開帳）。
// 代理可用 Fake 標籤機（驗 UI 流程）；要真打 Brother 需 AGENT_DEVICES=real + AGENT_BROTHER_HOST。
// 執行：mcr playwright 容器內 node scripts/label-print-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { fillEstimatedResale, fillItemName, pickGrade } from "./_acquisition.mjs";
import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
// 每次跑都用新的賣方：身分證字號會被去重比對，沿用同一組第二次就建不出來。
const RUN = Date.now() % 100000;
// 收購的成色（預設 A）。只有「全新未拆」N 標籤印「全新」，其餘印「二手」（2026-09-16）。
// 兩條路都要驗：`SMOKE_GRADE=A` 一次、`SMOKE_GRADE=N` 一次。
const GRADE = process.env.SMOKE_GRADE ?? "A";
const EXPECT_CONDITION = GRADE === "N" ? "全新" : "二手";
const SELLER_NAME = `標籤測試賣家 ${RUN}`;
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
async function api(token, method, path, body) {
  const res = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return res.json();
}
// 本煙霧要自己按「列印標籤」、在中間模擬品牌查不到：「收購送出後自動印標籤」（2026-09-23 加的設定）
// 開著的話，完成畫面一出來就已經印掉了。先關掉、結束時還原。
const adminToken = (await api(null, "POST", "/auth/login", { username: "dev-manager", password: "dev-test-123456" })).access_token;
const autoPrintBefore = (await api(adminToken, "GET", "/settings")).auto_print_acquisition_labels;
await api(adminToken, "PATCH", "/settings", { auto_print_acquisition_labels: false });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

// 攔下真正送到代理的標籤內容：畫面說「已送出」不代表印的東西是對的。
const labels = [];
page.on("request", (req) => {
  if (!req.url().includes("/print/label")) return;
  try {
    labels.push(JSON.parse(req.postData() ?? "{}"));
  } catch {
    labels.push({ parseError: req.postData() });
  }
});

try {
  // 1) 登入
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // 2) 進收購頁 → 建賣方 → 買斷一件（現金，已開帳）
  await page.click('a:has-text("收購")');
  await page.waitForURL(`${BASE}/acquisition`);
  await page.waitForSelector('[role="tab"]:has-text("買斷")');
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', SELLER_NAME);
  await page.fill('input[aria-label="手機"]', uniquePhone(RUN));  // 手機後來改為必填
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=${SELLER_NAME}`);

  await fillItemName(page, "標籤測試外套");
  await pickGrade(page, GRADE);

  const brand = page.getByLabel("品牌");
  await brand.click();
  await brand.fill("TestBrand");
  await page.click('button:has-text("建立「TestBrand」")');

  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill("登山服飾");
  await page.click('button:has-text("建立「登山服飾」")');

  await fillEstimatedResale(page, "3000");
  // 估計轉售價會非同步把含稅價自動填進上架售價；等它落地再覆寫，否則會被蓋掉（偶發紅）。
  await page.waitForFunction(
    () => (document.querySelector('input[aria-label="上架售價（含稅與手續費）"]')?.value ?? "") !== "",
    null,
    { timeout: 5000 },
  );
  await page.waitForSelector("text=建議最高收購成本");
  await page.fill('input[aria-label="收購價"]', "1000");
  await page.fill('input[aria-label="上架售價（含稅與手續費）"]', "3000");
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成");
  ok("買斷送出完成（有序號條碼）", await page.locator("text=序號條碼").isVisible());

  // 3) 列印標籤按鈕出現（張數 = 序號品數）
  const labelBtn = page.locator('.acq-print-labels button:has-text("列印標籤")');
  await labelBtn.waitFor();
  ok("出現「列印標籤（N 張）」按鈕", true, (await labelBtn.textContent()) ?? "");
  await page.screenshot({ path: `${SHOTS}/b3-01-label-button-${GRADE}.png` });

  // 有品牌但查詢成功回傳缺項，不得默默印成無品牌；恢復後可用同一筆收購重試。
  const brandOptions = "**/api/v1/serialized-items/filter-options*";
  await page.route(brandOptions, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: { ...data, brands: [] } });
  });
  await labelBtn.click();
  await page.locator(".acq-print-labels .form-error, .acq-print-labels .form-success").waitFor({ timeout: 5000 });
  ok("品牌缺項時不送印", labels.length === 0);
  ok("品牌缺項提供錯誤原因", (await page.locator(".acq-print-labels").textContent()).includes("品牌名稱"));
  await page.screenshot({ path: `${SHOTS}/b3-03-brand-missing.png` });
  await page.unroute(brandOptions);

  // 4) 點擊 → 經代理列印 → 顯示「已送出 N 張標籤」（代理需在 :8001 回應）
  await labelBtn.click();
  await page.waitForSelector(".acq-print-labels .form-success", {
    timeout: 15000,
  });
  const sent = await page.locator(".acq-print-labels .form-success").count();
  if (sent > 0) {
    ok(
      "標籤列印送出成功",
      true,
      (await page.locator(".acq-print-labels .form-success").textContent()) ?? "",
    );
  } else {
    ok(
      "標籤列印（代理回應）",
      false,
      (await page.locator(".acq-print-labels .form-error").textContent()) ?? "",
    );
  }
  await page.screenshot({ path: `${SHOTS}/b3-02-label-printed.png` });

  // 5) 標籤內容：全新／二手依成色（只有全新未拆印全新）、品牌獨立一行原樣帶上，成色本身不印。
  ok("有攔到標籤送出", labels.length > 0, `${labels.length} 張`);
  for (const [i, l] of labels.entries()) {
    ok(
      `第 ${i + 1} 張標示「${EXPECT_CONDITION}」（成色 ${GRADE}）`,
      l.condition === EXPECT_CONDITION,
      `condition=${JSON.stringify(l.condition)}`,
    );
    ok(`第 ${i + 1} 張品牌為 TestBrand`, l.brand === "TestBrand", `brand=${JSON.stringify(l.brand)}`);
  }
} catch (err) {
  ok("煙霧流程例外", false, String(err));
  // 失敗時留一張現場截圖，否則只有一行 timeout 訊息，查不出卡在哪一步。
  await page.screenshot({ path: `${SHOTS}/b3-99-failure.png` }).catch(() => {});
} finally {
  await api(adminToken, "PATCH", "/settings", { auto_print_acquisition_labels: autoPrintBefore });
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
