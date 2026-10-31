// 寄售帳篷預售瀏覽器煙霧（docs/102）：POS 新增預售（含照片）→ 官網看得到「預售」標籤與「LINE 預約看這頂」、
// 沒有「來店看實品」、賣家資料不出店 → 收購頁寄售「從預售帶入」送出 → 預售清單消失、在庫清單接手照片
// → 官網變現貨、舊預售網址轉到新編號。
//
// 需要（同 tents-listing-smoke.mjs，見 docs/20、docs/97）：
//   - backend :8000（ONLINE_ORDER_BASE_URL 指向本機線上點餐 Worker）、已跑 seed_dev_store／seed_dev_user／seed_dev_tents
//   - frontend :3000
//   - 線上點餐 Worker：cd online-order && npx wrangler dev --env production --local --port 8790
//   - 官網 Worker：cd ~/lukeng-homepage && npx wrangler dev --local --port 8787（service binding 接上面那個）
// 執行：node scripts/tents-presale-smoke.mjs
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { PNG } from "pngjs";

import { pickGrade } from "./_acquisition.mjs";
import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SITE = process.env.SMOKE_SITE ?? "http://127.0.0.1:8787";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "tents-presale");
const RUN = String(Date.now()).slice(-6);
const NAME = `NEW X5 預售-${RUN}`;
const SELLER = `預售賣家-${RUN}`;
const SELLER_PHONE = uniquePhone(RUN);
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

function photo(dir, name, [r, g, b]) {
  const png = new PNG({ width: 320, height: 240 });
  for (let i = 0; i < png.data.length; i += 4) {
    png.data[i] = r;
    png.data[i + 1] = g;
    png.data[i + 2] = b;
    png.data[i + 3] = 255;
  }
  const path = join(dir, name);
  writeFileSync(path, PNG.sync.write(png));
  return path;
}

async function feed() {
  const resp = await fetch(`${SITE}/api/tents`);
  return resp.status === 200 ? await resp.json() : { tents: [] };
}

// 存檔後畫面的「已同步」可能是上一輪的；以官網清單本身為準，等到符合為止。
async function waitFeed(check, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let data = await feed();
  while (!check(data) && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    data = await feed();
  }
  return data;
}

async function waitSynced(page) {
  const pending = page.getByRole("button", { name: "立即更新官網" });
  if (await pending.isVisible()) await pending.click();
  await page.locator(".tents-sync-ok").waitFor({ timeout: 20000 });
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("POS 頁面 JS 錯誤", false, String(err)));

try {
  const tmp = mkdtempSync(join(tmpdir(), "tents-presale-"));
  const photos = [photo(tmp, "a.png", [120, 140, 150]), photo(tmp, "b.png", [60, 70, 50])];

  // 1) 登入 → 官網寄售帳篷 → 新增預售
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await page.goto(`${BASE}/consignment/tents`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "＋新增預售" }).click();
  let dialog = page.getByRole("dialog", { name: "新增預售" });
  await dialog.waitFor();
  const brand = dialog.getByLabel("品牌");
  await brand.click();
  await brand.fill("Snow Peak");
  await page.locator('button:has-text("Snow Peak")').first().click();
  await dialog.getByLabel("帳篷名稱").fill(NAME);
  await dialog.getByLabel(/售價/).fill("14999");
  await dialog.getByLabel(/賣家姓名/).fill(SELLER);
  await dialog.getByLabel(/賣家電話/).fill(SELLER_PHONE);
  await dialog.getByLabel(/備註/).fill("週六可以帶來");
  await dialog.getByLabel("上架官網").check();
  await dialog.getByLabel("使用過幾露").fill("4");
  await dialog.getByLabel(/額外配件/).fill("贈客廳專屬地墊");
  await dialog.getByRole("button", { name: "建立預售" }).click();
  dialog = page.getByRole("dialog", { name: /^預售 P1-/ });
  await dialog.waitFor();
  const presaleCode = (await dialog.locator(".tents-dialog-code").textContent()).trim();
  ok("建立預售，拿到 P 開頭編號", /^P1-[0-9A-F]{10}$/.test(presaleCode), presaleCode);
  await dialog.locator('input[type="file"]').setInputFiles(photos);
  await dialog.locator(".tents-photo img").nth(1).waitFor();
  ok("預售加了兩張照片", (await dialog.locator(".tents-photo img").count()) === 2);
  await dialog.screenshot({ path: join(SHOTS, "01-pos-presale-dialog.png") });
  await dialog.getByRole("button", { name: "關閉" }).click();
  await waitSynced(page);
  const row = page.locator("tr", { hasText: presaleCode });
  ok("清單列出預售、標「預售」與賣家", (await row.innerText()).includes("預售") && (await row.innerText()).includes(SELLER));
  await page.screenshot({ path: join(SHOTS, "02-pos-list-with-presale.png"), fullPage: true });

  // 2) 官網：預售標籤、預約按鈕、沒有來店看實品、賣家資料不出店
  let data = await waitFeed((d) =>
    d.tents.some((t) => t.id === presaleCode && t.photos.length === 2),
  );
  let tent = data.tents.find((t) => t.id === presaleCode);
  ok("官網清單有這頂預售", tent?.presale === true && tent?.photos.length === 2, JSON.stringify(tent?.id));
  const text = JSON.stringify(data);
  ok("官網清單沒有賣家姓名、電話、備註", !text.includes(SELLER) && !text.includes(SELLER_PHONE) && !text.includes("週六可以帶來"));
  const site = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  site.on("pageerror", (err) => ok("官網 JS 錯誤", false, String(err)));
  await site.goto(`${SITE}/tents.html`, { waitUntil: "networkidle" });
  await site.locator(".tl-badge-presale").first().waitFor();
  ok("官網卡片有「預售」標籤", (await site.locator(".tl-card", { hasText: NAME }).locator(".tl-badge-presale").count()) === 1);
  await site.getByRole("button", { name: "預售" }).click();
  const onlyPresale = await site.locator(".tl-card").count();
  ok("篩「預售」只剩預售", onlyPresale >= 1 && (await site.locator(".tl-card .tl-badge-presale").count()) === onlyPresale);
  await site.screenshot({ path: join(SHOTS, "03-site-list-presale.png"), fullPage: true });
  await site.goto(`${SITE}/tents.html#/tent/${presaleCode}`, { waitUntil: "networkidle" });
  await site.locator(".td-presale-note").waitFor();
  ok("詳情頁主按鈕是「LINE 預約看這頂」", (await site.locator(".td-ctas .td-cta-main").textContent()) === "LINE 預約看這頂");
  ok("詳情頁沒有「來店看實品」", (await site.locator(".td-ctas .td-cta-sub").count()) === 0);
  await site.screenshot({ path: join(SHOTS, "04-site-presale-detail.png"), fullPage: true });

  // 3) 帳篷到店：收購頁寄售「從預售帶入」
  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });
  await page.getByRole("tab", { name: "寄售" }).click();
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', SELLER);
  await page.fill('input[aria-label="手機"]', SELLER_PHONE);
  await page.fill('input[aria-label="身分證字號"]', validNationalId(RUN));
  await page.click('button:has-text("建立並選取")');
  await page.getByText(SELLER_PHONE).first().waitFor({ timeout: 15000 });
  const acqRow = page.locator(".acq-row").first();
  const picker = acqRow.getByLabel(/從預售帶入/);
  const value = await picker.locator("option", { hasText: presaleCode }).getAttribute("value");
  await picker.selectOption(value);
  await page.waitForTimeout(300);
  ok("帶入品名與售價",
    (await acqRow.getByLabel("上架售價（含稅與手續費）").inputValue()) === "14999");
  ok("賣家電話跟預售登記的一樣：不提醒", (await acqRow.locator(".acq-presale-warn").count()) === 0);
  await pickGrade(acqRow, "A");
  const cat = acqRow.getByLabel("分類");
  await cat.click();
  await cat.fill(`帳篷-${RUN}`);
  await page.locator(`button:has-text("建立「帳篷-${RUN}」")`).first().click();
  await acqRow.getByLabel("上架售價（含稅與手續費）").fill("13999");
  await page.screenshot({ path: join(SHOTS, "05-acq-presale-picked.png"), fullPage: true });
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 20000 });
  await page.screenshot({ path: join(SHOTS, "06-acq-done.png") });

  // 4) POS：預售消失、在庫寄售品接手照片；官網變現貨、舊網址轉新編號
  await page.goto(`${BASE}/consignment/tents`, { waitUntil: "networkidle" });
  await waitSynced(page);
  ok("預售清單不再列這筆", (await page.locator("tr", { hasText: presaleCode }).count()) === 0);
  const taken = page.locator("tr", { hasText: NAME });
  ok("在庫寄售品接手、已上架", (await taken.count()) === 1 && (await taken.innerText()).includes("上架中"));
  await page.screenshot({ path: join(SHOTS, "07-pos-list-after-convert.png"), fullPage: true });
  data = await waitFeed((d) => d.tents.some((t) => (t.formerIds ?? []).includes(presaleCode)));
  tent = data.tents.find((t) => (t.formerIds ?? []).includes(presaleCode));
  ok("官網那頂變現貨（S 開頭、不是預售）", tent !== undefined && tent.id.startsWith("S1-") && tent.presale === false, tent?.id);
  ok("照片沿用、售價以寄售單為準", tent?.photos.length === 2 && tent?.price === 13999);
  ok("官網不再有預售編號那筆", !data.tents.some((t) => t.id === presaleCode));
  await site.goto(`${SITE}/tents.html#/tent/${presaleCode}`, { waitUntil: "networkidle" });
  await site.locator(".td-h1").waitFor();
  await site.waitForTimeout(300);
  ok("舊預售網址轉到新編號", site.url().endsWith(`#/tent/${encodeURIComponent(tent?.id ?? "")}`), site.url());
  ok("轉換後詳情頁有「來店看實品」、沒有預售說明",
    (await site.locator(".td-ctas .td-cta-sub").count()) === 1 && (await site.locator(".td-presale-note").count()) === 0);
  await site.screenshot({ path: join(SHOTS, "08-site-detail-after-convert.png"), fullPage: true });

  // 5) 賣家對不上：另建一筆預售（別的電話），收購頁選原賣家 → 提醒但不擋（不送出，事後取消）
  const api = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
  const { access_token: token } = await (
    await fetch(`${api}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
    })
  ).json();
  const auth = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const [snowPeak] = await (await fetch(`${api}/api/v1/brands?q=Snow%20Peak`, { headers: auth })).json();
  const other = await (
    await fetch(`${api}/api/v1/online-order/tents/presales`, {
      method: "POST",
      headers: auth,
      body: JSON.stringify({
        brand_id: snowPeak.id, name: `對不上-${RUN}`, price: "5000",
        seller_name: "陳大華", seller_phone: "0987-654-321",
      }),
    })
  ).json();
  await page.goto(`${BASE}/acquisition`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "收購" }).first().waitFor({ timeout: 15000 });
  await page.getByRole("tab", { name: "寄售" }).click();
  await page.getByLabel("賣方搜尋").fill(SELLER);
  await page.getByRole("button", { name: new RegExp(SELLER) }).first().click();
  const row2 = page.locator(".acq-row").first();
  await row2.getByLabel(/從預售帶入/).selectOption(String(other.listing_id));
  const warn = row2.locator(".acq-presale-warn");
  await warn.waitFor({ timeout: 5000 });
  ok("賣家電話對不上：提醒預售登記的賣家", (await warn.textContent()).includes("陳大華（0987-654-321）"));
  ok("提醒不擋送出", await page.getByRole("button", { name: "送出收購" }).isEnabled());
  await row2.screenshot({ path: join(SHOTS, "10-acq-seller-mismatch.png") });
  await fetch(`${api}/api/v1/online-order/tents/presales/${other.listing_id}/cancel`, {
    method: "POST",
    headers: auth,
  });

  // 6) 手機寬度的官網清單
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(`${SITE}/tents.html`, { waitUntil: "networkidle" });
  await phone.screenshot({ path: join(SHOTS, "09-site-list-mobile.png"), fullPage: false });
} catch (err) {
  ok("煙霧流程", false, String(err));
  await page.screenshot({ path: join(SHOTS, "zz-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
