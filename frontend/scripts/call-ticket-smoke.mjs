// 叫號系統瀏覽器煙霧（docs/38）：真 backend＋真 Postgres＋真瀏覽器。
// 取號 → 號碼顯示 → 清單 → 完成 → 從清單消失 → 以 include_done 確認資料還在。
// 需 backend(:8000) + frontend(:3000) 已起。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "call-ticket");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function api(token, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token === null ? {} : { Authorization: `Bearer ${token}` }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, json: await res.json().catch(() => null) };
}

const RUN = Date.now().toString().slice(-6);
const login = await api(null, "POST", "/api/v1/auth/login", {
  username: USERNAME,
  password: PASSWORD,
});
if (login.status !== 200) {
  ok("API 登入", false, `HTTP ${login.status}`);
  process.exit(1);
}
const token = login.json.access_token;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  // 導覽進得去（不是只有直接打網址才到得了）
  await page.click('a:has-text("叫號")');
  await page.waitForURL(`${BASE}/call-tickets`);
  ok("從導覽進得去叫號頁", true);

  // ── 取號 ──
  const name = `煙霧客-${RUN}`;
  await page.fill('input[name="name"]', name);
  await page.fill('input[name="link"]', `https://example.com/form/${RUN}`);
  await page.fill('input[name="note"]', "帳篷兩頂");
  await page.click("button.call-ticket-issue");
  await page.waitForSelector(".call-ticket-big-number", { timeout: 15000 });
  const issued = (await page.locator(".call-ticket-big-number").textContent()) ?? "";
  ok("取號後把號碼大大地顯示出來", /^\d+$/.test(issued.trim()), `#${issued.trim()}`);
  await page.screenshot({ path: `${SHOTS}/01-issued.png` });

  // ── 櫃檯打完稱呼按 Enter 就該取號（不必移到按鈕）──
  const name2 = `煙霧客Enter-${RUN}`;
  await page.fill('input[name="name"]', name2);
  await page.press('input[name="name"]', "Enter");
  await page.waitForSelector(`table.call-ticket-list tbody tr:has-text("${name2}")`, {
    timeout: 15000,
  });
  ok("在稱呼欄按 Enter 即可取號", true);

  // ── 出現在候位清單 ──
  const row = page.locator("table.call-ticket-list tbody tr", { hasText: name });
  await row.waitFor({ timeout: 15000 });
  ok("出現在候位清單", true);
  // 連結要渲染成可點的外部連結
  const href = await row.locator("a").getAttribute("href");
  const rel = await row.locator("a").getAttribute("rel");
  ok(
    "表單連結可點且帶 noopener",
    href === `https://example.com/form/${RUN}` && (rel ?? "").includes("noopener"),
    `${href} rel=${rel}`,
  );
  await page.screenshot({ path: `${SHOTS}/02-waiting-list.png` });

  // ── 完成 → 從清單消失 ──
  await row.getByRole("button", { name: /完成叫號/ }).click();
  await row.waitFor({ state: "detached", timeout: 15000 });
  ok("按完成後從候位清單消失", true);
  await page.screenshot({ path: `${SHOTS}/03-after-complete.png` });

  // ── **從畫面上**找得回已完成的（裁示「資料留著」若只有 API 撈得到，等於找不回來）──
  await page.getByLabel(/顯示已完成/).check();
  const doneRow = page.locator("table.call-ticket-list tbody tr", { hasText: name });
  await doneRow.waitFor({ timeout: 15000 });
  const doneText = (await doneRow.textContent()) ?? "";
  ok("勾「顯示已完成」後，畫面上找得回那筆與它的表單連結", doneText.includes("已完成"));
  ok(
    "已完成的不再顯示「完成」按鈕",
    (await doneRow.getByRole("button", { name: /完成叫號/ }).count()) === 0,
  );
  await page.screenshot({ path: `${SHOTS}/04-show-done.png` });
  await page.getByLabel(/顯示已完成/).uncheck();

  // ── 資料還在（後端層再確認一次）──
  const all = await api(token, "GET", "/api/v1/call-tickets?include_done=true&limit=200");
  const found = (all.json ?? []).find((t) => t.name === name);
  ok(
    "完成後資料仍查得到（含表單連結）",
    found != null && found.status === "DONE" && found.link === `https://example.com/form/${RUN}`,
    found ? `${found.status} ${found.link}` : "查無",
  );

  const waiting = await api(token, "GET", "/api/v1/call-tickets?limit=200");
  ok(
    "預設清單不含已完成",
    !(waiting.json ?? []).some((t) => t.name === name),
    `${(waiting.json ?? []).length} 筆候位中`,
  );

  // ── 危險連結在邊界被擋（不是只有前端不渲染）──
  const bad = await api(token, "POST", "/api/v1/call-tickets", {
    name: `壞連結-${RUN}`,
    link: "javascript:alert(1)",
  });
  ok("javascript: 連結被後端擋成 422", bad.status === 422, `HTTP ${bad.status}`);
} catch (err) {
  ok("煙霧流程例外", false, String(err));
  await page.screenshot({ path: `${SHOTS}/99-error.png` }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
