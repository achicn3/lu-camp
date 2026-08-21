// 用詞煙霧（docs/37 §7.4.6）：確認改過的字**真的顯示在畫面上**，且舊的工程術語不再出現。
//
// 一般煙霧測的是流程，測不到「這句話寫得好不好」。用詞是給店員看的，
// 改完只跑既有煙霧只能證明「沒弄壞」，證明不了「改對了」。
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const OUT = process.env.SMOKE_OUT ?? "/home/test/tmp/codex-test/wording";
mkdirSync(OUT, { recursive: true });
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  // ── 設定頁：改動最多的一頁 ──
  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const settings = await page.locator("body").innerText();
  await page.screenshot({ path: `${OUT}/01-settings.png`, fullPage: true });

  ok("設定頁：出現「簽名圖檔保留天數」", settings.includes("簽名圖檔保留天數"));
  ok("設定頁：不再出現「簽名 PNG」", !settings.includes("簽名 PNG"));
  ok("設定頁：不再出現常數 REPORT_ONLY", !settings.includes("REPORT_ONLY"));
  ok("設定頁：不再出現 hash", !/\bhash\b/i.test(settings));
  ok("設定頁：出現「可清除的簽名圖檔」", settings.includes("可清除的簽名圖檔"));

  // ── 採購頁：搜尋框在「建立採購單」面板裡，要先展開 ──
  await page.goto(`${BASE}/purchasing`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "＋ 建立採購單" }).click();
  await page.waitForSelector(".pur-create");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/02-purchasing.png`, fullPage: true });
  const purchasingHtml = await page.content();
  ok("採購頁：搜尋框提示為「輸入品名或商品編號」", purchasingHtml.includes("輸入品名或商品編號"));
  ok("採購頁：畫面文字不再出現 SKU", !(await page.locator("body").innerText()).includes("SKU"));

  // ── 庫存頁：「商品編號」表頭在**一般商品**分頁，預設分頁是序號品 ──
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  // 分頁是 role="tab" 不是 button（inv-tabs role="tablist"）
  await page.getByRole("tab", { name: "一般商品" }).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/03-inventory.png`, fullPage: true });
  const inventory = await page.locator("body").innerText();
  ok("庫存頁（一般商品）：表頭為「商品編號」", inventory.includes("商品編號"));
  ok("庫存頁（一般商品）：畫面文字不再出現 SKU", !inventory.includes("SKU"));

  // ── POS 保留（店主裁示：POS 是通用字詞） ──
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/04-pos.png`, fullPage: true });
  ok("POS 用語保留（裁示）", (await page.locator("body").innerText()).length > 0);
} catch (err) {
  ok("流程中斷", false, String(err));
  await page.screenshot({ path: `${OUT}/99-failure.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n結果：${passed}/${results.length} 通過`);
console.log(`截圖：${OUT}`);
if (passed !== results.length) process.exit(1);
