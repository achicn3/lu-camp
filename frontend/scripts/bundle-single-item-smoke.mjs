// 組合價只有一樣（店主 2026-10-10）瀏覽器 E2E：豬尾巴（散裝、每支 $30）買 3 支 $80。
// 門市活動頁：選組合價、預設只有一樣、不能拿掉 → 第 1 樣選品牌、3 件、$80 → 建立、啟用
// → POS 掃散裝、數量 3：應付 $80；改成 4：$110（一組＋一支原價）→ 結帳完成。最後結束活動。
// 需 backend + frontend 已起、已 seed（dev-manager）。會建立活動與銷售，請指向隔離測試庫：
//   SMOKE_ALLOW_WRITE=1 node scripts/bundle-single-item-smoke.mjs
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "bundle-single-item");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立活動與銷售，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });
const RUN = String(Date.now()).slice(-6);
const BRAND = `豬尾巴牌-${RUN}`;
const CAMPAIGN = `豬尾巴三支-${RUN}`;
const ITEM = `豬尾巴-${RUN}`;

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
let idem = 0;
async function api(method, path, body) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "Idempotency-Key": `bundle-single-${RUN}-${(idem += 1)}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

function taipeiLocal(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86_400_000 + 8 * 3_600_000);
  return d.toISOString().slice(0, 16);
}

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1200 } })).newPage();
page.on("pageerror", (err) => ok("頁面沒有 JS 錯誤", false, String(err)));
let campaignId = null;

try {
  token = (
    await (
      await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
      })
    ).json()
  ).access_token;
  if ((await api("GET", "/api/v1/cash-sessions/current")).body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "5000" });
  }
  const brand = (await api("POST", "/api/v1/brands", { name: BRAND })).body;
  const seller = (
    await api("POST", "/api/v1/contacts", {
      name: `散裝賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    })
  ).body;
  const acq = await api("POST", "/api/v1/acquisitions", {
    type: "BULK_LOT",
    contact_id: seller.id,
    lot: {
      name: ITEM,
      brand_id: brand.id,
      acquisition_cost: "100",
      acquisition_basis: "UNSPECIFIED",
      total_qty: 10,
      unit_price: "30",
    },
  });
  ok("API：豬尾巴散裝 10 支、每支 $30", acq.status === 201, JSON.stringify(acq.body?.detail ?? ""));
  const lotCode = acq.body.lot_code;

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });

  await page.getByLabel("活動名稱").fill(CAMPAIGN);
  await page.getByLabel("組合價", { exact: true }).check();
  ok(
    "預設只有一樣、不能拿掉",
    (await page.getByRole("group", { name: "第 2 樣商品" }).count()) === 0 &&
      (await page.getByRole("button", { name: "拿掉這一樣" }).count()) === 0,
  );
  await page.getByLabel("組合價（含稅，元）").fill("80");
  const slot = page.getByRole("group", { name: "第 1 樣商品" });
  await slot.getByLabel("搜尋品牌").fill(BRAND);
  await slot.getByRole("button", { name: `加入 ${BRAND}`, exact: true }).click();
  await page.getByLabel("第 1 樣要幾件").fill("3");
  await page.getByLabel("開始時間").fill(taipeiLocal(-1));
  await page.getByLabel("結束時間").fill(taipeiLocal(1));
  await page.screenshot({ path: join(SHOTS, "01-form.png"), fullPage: true });
  await page.getByRole("button", { name: "建立活動" }).click();
  const row = page.locator("tr", { has: page.getByText(CAMPAIGN, { exact: true }) });
  await row.waitFor();
  const rowText = await row.innerText();
  ok("清單顯示組合價 $80、一樣 ×3", rowText.includes("組合價 $80") && rowText.includes(`組合：${BRAND} ×3`), rowText.replace(/\s+/g, " "));
  await row.getByRole("button", { name: "啟用" }).click();
  await row.getByText("生效中").waitFor();
  campaignId = (await api("GET", "/api/v1/campaigns?status=ACTIVE")).body.find((c) => c.name === CAMPAIGN)?.id ?? null;

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=這筆不開發票");
  await page.fill('input[name="code"]', lotCode);
  await page.press('input[name="code"]', "Enter");
  const qty = page.getByLabel(`${ITEM} 數量`);
  await qty.waitFor();
  await qty.fill("3");
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("80"));
  ok("買 3 支：應付 $80", true, (await page.locator(".pos-total").innerText()).replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "02-pos-three.png"), fullPage: true });
  await qty.fill("4");
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("110"));
  ok("買 4 支：一組 $80＋一支原價 $30＝$110", true);
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.trim() === "結帳");
    return b && !b.disabled;
  });
  await page.getByRole("button", { name: "結帳" }).click();
  await page.waitForSelector("text=已完成");
  const done = await page.locator(".pos-complete").innerText();
  ok("結帳完成（$110）", /110/.test(done), done.replace(/\s+/g, " ").slice(0, 80));
  await page.screenshot({ path: join(SHOTS, "03-complete.png"), fullPage: true });
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  // 一定結束自己開的活動：留著會改變同一個資料庫裡其他煙霧的價格。
  if (campaignId !== null) await api("POST", `/api/v1/campaigns/${campaignId}/end`);
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
