// 採購定價與電話自動帶入的瀏覽器煙霧（裁示 2026-09-16）：
// 1) 收購頁搜尋不到電話 → 按「建立新賣方」時該電話自動帶入，且格式不合會當場擋下。
// 2) 建立採購單頁「新增商品」：填進貨成本＋毛利率 → 自動算出建議售價（含稅與行動支付手續費），
//    建立後成本自動帶進採購明細那一列（不必打第二次）。
// 需 backend + frontend 已起、已 seed（dev-manager）。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "Use an isolated test DB and set SMOKE_ALLOW_WRITE=1");
assert.ok(process.env.SMOKE_PASSWORD, "SMOKE_PASSWORD is required");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "purchase-pricing");
mkdirSync(SHOTS, { recursive: true });
const RUN = Date.now() % 100000;
const results = [];
const ok = (name, pass, detail = "") => {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', process.env.SMOKE_USERNAME_MANAGER ?? "dev-manager");
  await page.fill('input[name="password"]', process.env.SMOKE_PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  // ── 1) 收購頁：查無電話 → 自動帶入 ──
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  const phone = uniquePhone(RUN);
  const typed = `${phone.slice(0, 4)}-${phone.slice(4, 7)}-${phone.slice(7)}`; // 故意打成有連字號
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/contacts?") && new URL(r.url()).searchParams.get("q") === typed),
    page.fill('input[aria-label="賣方搜尋"]', typed),
  ]);
  await page.click('button:has-text("建立新賣方")');
  const phoneField = page.locator('input[aria-label="手機"]');
  await phoneField.waitFor();
  const filled = await phoneField.inputValue();
  ok("搜尋的電話自動帶入建立表單（且去掉連字號）", filled === phone, `帶入 ${filled}`);
  ok("姓名欄不會被電話汙染", (await page.inputValue('input[aria-label="姓名"]')) === "");

  // 格式不合當場擋下
  await page.fill('input[aria-label="姓名"]', `煙霧賣家 ${RUN}`);
  await page.fill('input[aria-label="手機"]', "0911");
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector("text=/09 開頭的 10 碼/", { timeout: 8000 });
  ok("電話格式不合當場擋下並說明格式", true);
  await page.screenshot({ path: `${SHOTS}/01-phone-autofill.png` });

  // 改回合法號碼即可建立
  await page.fill('input[aria-label="手機"]', typed);
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector(`text=煙霧賣家 ${RUN}`, { timeout: 15000 });
  ok("改回合法號碼即可建立賣方", true);
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.fill('input[aria-label="賣方搜尋"]', typed);
  await page.getByRole("button", { name: new RegExp(`煙霧賣家 ${RUN}`) }).waitFor();
  ok("格式化電話可以查回剛建立的會員", true);
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const settingsResponse = await page.request.get(`${API}/api/v1/settings`, { headers: { Authorization: `Bearer ${token}` } });
  assert.ok(settingsResponse.ok());
  const settings = await settingsResponse.json();
  const defaultMargin = settings.purchase_default_margin_pct;
  const tax = Number(settings.tax_rate);
  const fee = Math.max(Number(settings.linepay_fee_pct), Number(settings.taiwanpay_fee_pct));
  // 合成小金額案例的獨立預期值，不使用前端定價函式。
  // 系統帶出的上架售價一律無條件進位到 10 元（ADR-023）。
  const expectedPrice = (margin) =>
    Math.ceil(Math.round(50 / (1 - margin / 100) * (1 + tax) / (1 - fee * (1 + tax))) / 10) * 10;

  // ── 2) 採購頁：成本 → 建議售價 → 成本帶進明細 ──
  await page.goto(`${BASE}/purchasing/new`, { waitUntil: "networkidle" });
  const name = `煙霧濾掛 ${RUN}`;
  await page.fill('input[aria-label="搜尋一般商品"]', name);
  await page.getByRole("button", { name: "＋ 新增商品", exact: true }).click();
  await page.waitForSelector('input[aria-label="一般商品進貨成本"]');

  const margin = page.locator('input[aria-label="一般商品預估毛利率"]');
  ok("毛利率預設帶 API 設定值", (await margin.inputValue()) === String(defaultMargin), await margin.inputValue());

  await page.fill('input[aria-label="一般商品進貨成本"]', "50");
  const price = page.locator('input[aria-label="一般商品售價"]');
  await page.waitForFunction(
    () => (document.querySelector('input[aria-label="一般商品售價"]')?.value ?? "") !== "",
    null,
    { timeout: 8000 },
  );
  const auto = await price.inputValue();
  ok("填成本即自動算出建議售價", Number(auto) === expectedPrice(defaultMargin), `售價 ${auto}`);
  const hint = await page.locator(".pur-price-hint").first().textContent();
  ok("有說明建議售價怎麼來的", (hint ?? "").includes("毛利"), (hint ?? "").trim().slice(0, 60));
  await page.screenshot({ path: `${SHOTS}/02-purchase-pricing.png` });

  // 毛利率調高 → 售價跟著變高
  const alternateMargin = defaultMargin === 50 ? 40 : 50;
  await margin.fill(String(alternateMargin));
  await page.waitForFunction(
    (prev) => (document.querySelector('input[aria-label="一般商品售價"]')?.value ?? "") !== prev,
    auto,
    { timeout: 8000 },
  );
  const higher = await price.inputValue();
  ok("調整毛利率的售價符合獨立預期值", Number(higher) === expectedPrice(alternateMargin), `${auto} → ${higher}`);

  await page.click('button:has-text("建立並加入採購單")');
  const costCell = page.locator(`input[aria-label="進貨單價 ${name}"]`);
  await costCell.waitFor({ timeout: 15000 });
  ok("成本自動帶進採購明細那一列", (await costCell.inputValue()) === "50", await costCell.inputValue());
  await page.screenshot({ path: `${SHOTS}/03-line-cost.png` });
  await page.route("**/api/v1/settings", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "synthetic unavailable" }) }));
  await page.reload({ waitUntil: "networkidle" });
  await page.getByLabel("搜尋一般商品").fill(`失敗測試-${RUN}`);
  await page.getByRole("button", { name: "＋ 新增商品", exact: true }).click();
  await page.getByLabel("一般商品進貨成本").fill("1000");
  await page.getByLabel("一般商品預估毛利率").fill("30");
  await page.getByText("讀不到稅率設定，請直接輸入含稅售價。").waitFor();
  ok("設定讀取失敗不會以零稅率推價", await page.getByLabel("一般商品售價").inputValue() === "");
  await page.getByLabel("一般商品售價").fill("1600");
  ok("讀不到設定時仍可手填含稅售價", await page.getByLabel("一般商品售價").inputValue() === "1600");
  await page.screenshot({ path: `${SHOTS}/04-settings-unavailable.png` });
} catch (err) {
  ok("煙霧流程例外", false, String(err).slice(0, 300));
  await page.screenshot({ path: `${SHOTS}/99-fail.png` }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
