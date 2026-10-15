// 排隊收購：兩台裝置同時開同一位客人的估價（店主 2026-10-16）。
// 新報到 1 件（快速估價）→ 裝置 A、B 都打開估價頁 → A 打開「詳細」先放著 →
// B 在列上填收購價 → A 不重新整理、幾秒內列上就看到新價 → A 在詳細只填備註、儲存 →
// 後端：B 填的收購價、A 填的備註都在（A 沒把 B 的蓋掉）。號碼是當天字母＋三位數。
// 需 backend + frontend 已起、已 seed（dev-manager）。
// 執行：SMOKE_BASE=http://localhost:3000 SMOKE_API_BASE=http://localhost:8000 node scripts/intake-two-devices-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "intake-two-devices");
const RUN = String(Date.now()).slice(-6);
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function device(browser) {
  const context = await browser.newContext({ viewport: { width: 1100, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  return { page, errors };
}

const browser = await chromium.launch();
const pages = [];
try {
  const mgr = (
    await api(null, "POST", "/api/v1/auth/login", { username: "dev-manager", password: "dev-test-123456" })
  ).json.access_token;
  const seller = (
    await api(mgr, "POST", "/api/v1/contacts", {
      name: `兩台裝置-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    })
  ).json;
  const batch = (
    await api(mgr, "POST", "/api/v1/intake-batches", {
      contact_id: seller.id,
      declared_item_count: 1,
      prefill_lines: true,
    })
  ).json;
  ok("號碼是當天字母＋三位數", /^[A-HJ-NP-Z]\d{3}$/.test(batch.ticket_label), batch.ticket_label);

  const a = await device(browser);
  const b = await device(browser);
  pages.push(a, b);
  for (const d of [a, b]) {
    await d.page.goto(`${BASE}/acquisition/intake/${batch.id}`, { waitUntil: "networkidle" });
  }

  // A 打開詳細先放著
  await a.page.getByRole("button", { name: "1 號 詳細" }).click();
  const formA = a.page.getByRole("form", { name: /修改第 1 列/ });
  await formA.waitFor();

  // B 在列上填收購價
  const priceB = b.page.getByLabel("1 號 收購價", { exact: true });
  await priceB.fill("480");
  await priceB.press("Enter");
  await b.page.waitForFunction(
    async ([apiBase, id, auth]) => {
      const r = await fetch(`${apiBase}/api/v1/intake-batches/${id}`, { headers: { Authorization: `Bearer ${auth}` } });
      return (await r.json()).lines?.[0]?.deal_cost === "480";
    },
    [API, batch.id, mgr],
    { timeout: 8000 },
  );

  // A 沒重新整理：幾秒內列上就看到 B 填的
  const priceA = a.page.getByLabel("1 號 收購價", { exact: true });
  const seen = await priceA
    .evaluate(
      (el) =>
        new Promise((resolve) => {
          const started = Date.now();
          const tick = () => {
            if (el.value === "480") resolve(true);
            else if (Date.now() - started > 9000) resolve(false);
            else setTimeout(tick, 200);
          };
          tick();
        }),
    )
    .catch(() => false);
  ok("A 不必重新整理，幾秒內看到 B 填的收購價", seen === true, await priceA.inputValue());
  await a.page.screenshot({ path: join(SHOTS, "01-a-sees-b.png") });

  // A 在詳細只填備註、儲存
  await formA.getByLabel("配件／特殊狀況").fill("缺收納袋");
  await formA.getByRole("button", { name: "儲存詳細" }).click();
  await a.page.waitForFunction(
    async ([apiBase, id, auth]) => {
      const r = await fetch(`${apiBase}/api/v1/intake-batches/${id}`, { headers: { Authorization: `Bearer ${auth}` } });
      return (await r.json()).lines?.[0]?.note === "缺收納袋";
    },
    [API, batch.id, mgr],
    { timeout: 8000 },
  );
  const line = (await api(mgr, "GET", `/api/v1/intake-batches/${batch.id}`)).json.lines[0];
  ok(
    "A 存詳細沒蓋掉 B 的收購價，備註也存了",
    line.deal_cost === "480" && line.note === "缺收納袋",
    JSON.stringify([line.deal_cost, line.note]),
  );
  await a.page.screenshot({ path: join(SHOTS, "02-both-kept.png") });
  ok("兩台都沒有 JS 例外", a.errors.length === 0 && b.errors.length === 0, [...a.errors, ...b.errors].join(" / "));
} catch (error) {
  ok("流程例外", false, String(error));
  for (const [i, d] of pages.entries()) {
    await d.page.screenshot({ path: join(SHOTS, `99-failure-${i}.png`), fullPage: true }).catch(() => {});
  }
} finally {
  await browser.close();
}
console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
if (failures.length > 0) process.exitCode = 1;
