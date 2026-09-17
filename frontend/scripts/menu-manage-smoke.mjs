// M4 餐飲菜單管理頁瀏覽器煙霧：登入(dev-manager) → /menu → 清單 → 新增品項 → 出現 → 下架切換，
// 並驗成本欄：填成本自動帶建議售價、送出的 body 真的帶成本、改成本會 PATCH unit_cost。
// **斷言攔到的 request body**，畫面出現數字不代表送出去的是對的。
// 需 backend(:8000)+frontend(:3000) 已起。執行：mcr playwright 容器內 node scripts/menu-manage-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
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
page.on("request", (req) => {
  if (!/\/api\/v1\/menu-items/.test(req.url())) return;
  if (req.method() !== "POST" && req.method() !== "PATCH") return;
  try {
    sentBodies.push({ method: req.method(), url: req.url(), body: JSON.parse(req.postData() ?? "{}") });
  } catch {
    sentBodies.push({ method: req.method(), url: req.url(), body: null });
  }
});
const uniqueName = `煙霧品項-${Date.now().toString().slice(-6)}`;
const costName = `成本品項-${Date.now().toString().slice(-6)}`;
// 攔截送往後端的菜單寫入，比對 body（不是比對畫面文字）。
const sentBodies = [];

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.click('a:has-text("餐飲菜單")');
  await page.waitForURL(`${BASE}/menu`);
  await page.waitForSelector(".inv-table");
  ok("菜單管理清單載入", true);
  await page.screenshot({ path: `${SHOTS}/m4-01-list.png` });

  // 新增品項
  await page.getByLabel("品名").fill(uniqueName);
  await page.getByLabel("售價（整數元）").fill("250");
  await page.getByLabel("分類（選填）").fill("點心");
  await page.click('button:has-text("新增品項")');
  await page.waitForSelector(`tr:has-text("${uniqueName}")`);
  ok("新增品項出現於清單", true, uniqueName);
  await page.screenshot({ path: `${SHOTS}/m4-02-created.png` });

  // 下架該品項（其列的「下架」鈕）→ 狀態變停售
  const row = page.locator(`tr:has-text("${uniqueName}")`);
  await row.locator('button:has-text("下架")').click();
  await page.waitForFunction(
    (name) => {
      const tr = [...document.querySelectorAll("tr")].find((r) =>
        r.textContent?.includes(name),
      );
      return tr?.textContent?.includes("停售") ?? false;
    },
    uniqueName,
    { timeout: 10000 },
  );
  ok("下架後狀態為停售", true);
  await page.screenshot({ path: `${SHOTS}/m4-03-unavailable.png` });

  // --- 成本 → 建議售價 → 毛利率 ---
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const settings = await (
    await page.request.get(`${API}/api/v1/settings`, { headers: { Authorization: `Bearer ${token}` } })
  ).json();
  const tax = Number(settings.tax_rate);
  const fee = Math.max(Number(settings.linepay_fee_pct), Number(settings.taiwanpay_fee_pct));
  const margin = Number(settings.purchase_default_margin_pct);
  const cost = 60;
  // 與 CLAUDE.md §7.9 同式，在腳本內獨立算一次（不引用產品程式碼，否則錯一起錯）
  const expectedPrice = String(Math.round((cost / (1 - margin / 100)) * (1 + tax) / (1 - fee * (1 + tax))));

  await page.getByLabel("品名").fill(costName);
  await page.getByLabel("成本（整數元，選填）").fill(String(cost));
  await page.waitForFunction(
    (expected) => document.querySelector("input[placeholder='180']")?.value === expected,
    expectedPrice,
    { timeout: 10000 },
  );
  ok("填成本後自動帶出建議售價", true, `NT$${expectedPrice}（成本 ${cost}／毛利 ${margin}%）`);
  await page.screenshot({ path: `${SHOTS}/m4-04-suggested-price.png` });

  await page.click('button:has-text("新增品項")');
  await page.waitForSelector(`tr:has-text("${costName}")`);
  const created = sentBodies.find((r) => r.method === "POST" && r.body?.name === costName);
  if (!created) throw new Error("沒攔到新增品項的 POST");
  if (created.body.unit_cost !== String(cost)) throw new Error(`送出的成本是 ${created.body.unit_cost}`);
  if (created.body.unit_price !== expectedPrice) throw new Error(`送出的售價是 ${created.body.unit_price}`);
  ok("新增送出的 body 帶成本與建議售價", true, JSON.stringify({ unit_cost: created.body.unit_cost, unit_price: created.body.unit_price }));

  const costRow = page.locator(`tr:has-text("${costName}")`);
  const marginCell = (await costRow.locator("td").nth(4).textContent()) ?? "";
  const price = Number(expectedPrice);
  const expectedMarginPct = (() => {
    const net = Math.round(price / (1 + tax)) - Math.round(price * fee);
    return `${Math.round(((net - cost) * 100) / net)}%`;
  })();
  if (marginCell.trim() !== expectedMarginPct) {
    throw new Error(`清單毛利率顯示 ${marginCell.trim()}，預期 ${expectedMarginPct}`);
  }
  ok("清單顯示預估毛利率", true, expectedMarginPct);
  await page.screenshot({ path: `${SHOTS}/m4-05-cost-column.png` });

  await costRow.locator('button:has-text("改成本")').click();
  await page.getByLabel(`${costName} 成本`).fill("75");
  await costRow.locator('button:has-text("儲存")').click();
  await page.waitForFunction(
    (name) => {
      const tr = [...document.querySelectorAll("tr")].find((r) => r.textContent?.includes(name));
      return tr?.textContent?.includes("75") ?? false;
    },
    costName,
    { timeout: 10000 },
  );
  const patched = sentBodies.find((r) => r.method === "PATCH" && r.body?.unit_cost === "75");
  if (!patched) throw new Error("沒攔到改成本的 PATCH（unit_cost=75）");
  ok("改成本送出 PATCH unit_cost", true, "75");
  await page.screenshot({ path: `${SHOTS}/m4-06-cost-updated.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
