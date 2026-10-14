// 排隊收購「詳細」的商品簡稱建議（店主 2026-10-14）。
// 先用 API 在另一批估兩次同名商品（Coleman 收 250、Snow Peak 收 300，品牌帶在估價列上）→
// 新報到 1 件 → 「詳細」打簡稱一部分 → 清單列出兩筆、各帶品牌與上次估的原價／預計售價／收購價 →
// 點 Snow Peak 那筆 → 簡稱帶入、下方留著「上次估價」→ 存檔，後端簡稱正確。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/intake-short-name-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-short-name");
const RUN = String(Date.now()).slice(-6);
const NAME = `折疊椅${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
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
  const token = await page.evaluate(() => localStorage.getItem("lu-camp.access-token"));
  const call = async (path, method = "GET", body) => {
    const resp = await fetch(`${API}/api/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status} ${await resp.text()}`);
    return resp.json();
  };

  // 之前的估價：另一批，同名、不同品牌
  const seller = await call("/contacts", "POST", {
    name: `簡稱建議前一位-${RUN}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER"],
  });
  const coleman = await call("/brands", "POST", { name: `Coleman-${RUN}` });
  const snowPeak = await call("/brands", "POST", { name: `Snow Peak-${RUN}` });
  const earlier = await call("/intake-batches", "POST", { contact_id: seller.id, declared_item_count: 2 });
  const line = (brandId, deal) => ({
    short_name: NAME,
    qty: 1,
    acquisition_type: "BUYOUT",
    reference_price: "1000",
    discount_pct: 50,
    expected_listed_price: "500",
    deal_cost: deal,
    brand_id: brandId,
  });
  await call(`/intake-batches/${earlier.id}/lines`, "POST", line(coleman.id, "250"));
  await call(`/intake-batches/${earlier.id}/lines`, "POST", line(snowPeak.id, "300"));

  // 新的一位客人報到 1 件
  await page.goto(`${BASE}/acquisition/intake`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /建立新賣方/ }).click();
  await page.getByLabel("姓名", { exact: true }).fill(`簡稱建議賣家-${RUN}`);
  await page.getByLabel("手機", { exact: true }).fill(uniquePhone());
  await page.getByLabel("身分證字號", { exact: true }).fill(validNationalId());
  await page.getByRole("button", { name: "建立並選取" }).click();
  await page.getByLabel("收購幾件").fill("1");
  await page.getByRole("button", { name: "報到，發號碼" }).click();
  await page.waitForURL(/\/acquisition\/intake\/\d+/);
  const batchId = Number(/\/acquisition\/intake\/(\d+)/.exec(page.url())?.[1]);

  await page.getByRole("button", { name: "1 號 詳細" }).click();
  const form = page.getByRole("form", { name: /修改第 1 列/ });
  const input = form.getByLabel("商品簡稱");
  await input.fill("");
  await input.pressSequentially(`椅${RUN}`, { delay: 30 });
  const menu = form.getByRole("listbox", { name: "以前估過的商品" });
  await menu.waitFor({ timeout: 8000 });
  const options = menu.getByRole("option");
  ok("打簡稱一部分就列出以前估過的", (await options.count()) === 2, String(await options.count()));
  const texts = await options.allTextContents();
  ok(
    "同名兩筆：各帶品牌",
    texts.some((t) => t.includes(`Coleman-${RUN}`)) && texts.some((t) => t.includes(`Snow Peak-${RUN}`)),
    texts.join(" | "),
  );
  ok(
    "每筆附原價／預計售價／收購價",
    texts.every((t) => t.includes("原價$1,000") && t.includes("預計售價$500")) &&
      texts.some((t) => t.includes("收購價$250")) &&
      texts.some((t) => t.includes("收購價$300")),
  );
  await page.screenshot({ path: join(SHOTS, "01-suggestions.png") });

  await options.filter({ hasText: `Snow Peak-${RUN}` }).click();
  ok("點選後簡稱帶入", (await input.inputValue()) === NAME, await input.inputValue());
  const last = form.getByLabel("上次估價");
  const lastText = (await last.textContent()) ?? "";
  ok("下方留著上次估價（Snow Peak、收購價 300）", lastText.includes(`Snow Peak-${RUN}`) && lastText.includes("收購價$300"), lastText);
  ok("價格不自動填", (await form.getByLabel("原價／件").inputValue()) === "");
  await page.screenshot({ path: join(SHOTS, "02-picked.png") });

  await form.getByRole("button", { name: "儲存詳細" }).click();
  await page.waitForFunction(
    async ([api, id, auth, name]) => {
      const r = await fetch(`${api}/api/v1/intake-batches/${id}`, { headers: { Authorization: `Bearer ${auth}` } });
      return (await r.json()).lines?.[0]?.short_name === name;
    },
    [API, batchId, token, NAME],
    { timeout: 8000 },
  );
  ok("存檔後端簡稱正確", true, NAME);
  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
