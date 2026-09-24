// 門市活動 v2 P3 煙霧（docs/40）：在畫面上建立「買二送一、只限某品牌」並啟用 →
// POS 掃三件（1000／600／400）→ 應付 1,600、400 那件標「這件是送的」→
// 店員「改送這件」600 那件（P3b）→ 應付 1,400、600 那件標送的 → 結帳完成。
// 需 backend + frontend 已起、已 seed dev-manager。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/campaigns-bngm-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "campaigns-bngm");
const RUN = String(Date.now()).slice(-6);
const BRAND = `Snow Peak-${RUN}`;
const CAMPAIGN = `${BRAND} 買二送一`;
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method === "POST" ? { "Idempotency-Key": `bn-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  return response.json();
}

function taipeiLocal(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86_400_000 + 8 * 3_600_000);
  return d.toISOString().slice(0, 16);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));
let token = "";

try {
  ({ access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  }));
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", { method: "POST", token, body: { opening_float: "5000" } });
  }
  const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: BRAND } });
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: { name: `買送測試賣家-${RUN}`, phone: uniquePhone(), national_id: validNationalId(), roles: ["SELLER"] },
  });
  const names = ["焚火台", "營燈", "杯子"].map((n) => `${n} ${RUN}`);
  const acq = await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      items: [
        { name: names[0], grade: "A", brand_id: brand.id, acquisition_cost: "300", listed_price: "1000" },
        { name: names[1], grade: "A", brand_id: brand.id, acquisition_cost: "200", listed_price: "600" },
        { name: names[2], grade: "A", brand_id: brand.id, acquisition_cost: "100", listed_price: "400" },
      ],
    },
  });
  ok("造出同品牌三件商品（1000／600／400）", acq.item_codes.length === 3);

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });

  await page.getByLabel("活動名稱").fill(CAMPAIGN);
  await page.getByLabel("買幾送幾").check();
  ok("選買幾送幾後不再出現寄售選項", (await page.getByLabel("對寄售品套用折扣").count()) === 0);
  await page.getByLabel("買幾件").fill("2");
  await page.getByLabel("送幾件").fill("1");
  await page.getByLabel("開始時間").fill(taipeiLocal(-1));
  await page.getByLabel("結束時間").fill(taipeiLocal(1));
  const scope = page.getByRole("group", { name: "指定商品（選填）" });
  await scope.getByLabel("範圍類型").selectOption("BRAND");
  await scope.getByLabel("搜尋品牌").fill(BRAND);
  await scope.getByRole("button", { name: `加入 ${BRAND}`, exact: true }).click();
  await page.screenshot({ path: join(SHOTS, "01-form.png"), fullPage: true });

  await page.getByRole("button", { name: "建立活動" }).click();
  const row = page.locator("tr", { has: page.getByText(CAMPAIGN, { exact: true }) });
  await row.waitFor();
  const rowText = await row.innerText();
  ok("清單顯示「買 2 送 1」", rowText.includes("買 2 送 1"), rowText.replace(/\s+/g, " "));
  await row.getByRole("button", { name: "啟用" }).click();
  await row.getByText("生效中").waitFor();
  await page.screenshot({ path: join(SHOTS, "02-list.png"), fullPage: true });

  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=這筆不開發票");
  for (const [i, code] of acq.item_codes.entries()) {
    await page.fill('input[name="code"]', code);
    await page.press('input[name="code"]', "Enter");
    await page.locator(".pos-cart tbody tr", { hasText: names[i] }).waitFor();
  }
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("1,600"));
  ok("應付 1,600（送最便宜的 400）", true);
  const freeRow = await page.locator(".pos-cart tbody tr", { hasText: names[2] }).innerText();
  ok("400 那件標「這件是送的」", freeRow.includes("這件是送的"), freeRow.replace(/\s+/g, " "));
  const firstRow = await page.locator(".pos-cart tbody tr", { hasText: names[0] }).innerText();
  ok("1000 那件分攤後 800、列出活動名", firstRow.includes("800") && firstRow.includes(CAMPAIGN), firstRow.replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "03-pos-cart.png"), fullPage: true });

  await page.getByRole("button", { name: `改送這件 ${names[1]}` }).click();
  await page.waitForFunction(() => document.querySelector(".pos-total strong")?.textContent?.includes("1,400"));
  ok("改送 600 那件：應付 1,400", true);
  const chosenRow = await page.locator(".pos-cart tbody tr", { hasText: names[1] }).innerText();
  ok("600 那件改標「這件是送的」、可取消", chosenRow.includes("這件是送的") && chosenRow.includes("取消送這件"), chosenRow.replace(/\s+/g, " "));
  const cupRow = await page.locator(".pos-cart tbody tr", { hasText: names[2] }).innerText();
  ok("400 那件不再是送的", !cupRow.includes("這件是送的"), cupRow.replace(/\s+/g, " "));
  await page.screenshot({ path: join(SHOTS, "03b-pos-chosen.png"), fullPage: true });

  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.trim() === "結帳");
    return b && !b.disabled;
  });
  await page.getByRole("button", { name: "結帳" }).click();
  await page.waitForSelector("text=已完成");
  const completeText = await page.locator(".pos-complete").innerText();
  ok("結帳完成（1,400）", /1,?400/.test(completeText), completeText.replace(/\s+/g, " ").slice(0, 120));
  await page.screenshot({ path: join(SHOTS, "04-complete.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  ok("流程例外", false, String(error));
} finally {
  await browser.close();
  // 一定結束自己開的活動：留著會改變同一個資料庫裡其他煙霧的價格。
  if (token) {
    const listed = await apiJson("/api/v1/campaigns?status=ACTIVE", { token }).catch(() => []);
    for (const c of listed) {
      if (c.name === CAMPAIGN) {
        await apiJson(`/api/v1/campaigns/${c.id}/end`, { method: "POST", token }).catch(() => {});
      }
    }
  }
}

const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 通過；截圖 ${SHOTS}`);
process.exitCode = failed > 0 ? 1 : 0;
