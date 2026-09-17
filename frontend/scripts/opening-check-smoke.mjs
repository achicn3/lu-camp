// 開店前檢查瀏覽器煙霧（裁示 2026-09-17）：
// 每天第一次進系統自動帶到檢查頁 → 未完成時選單留紅點 → 開帳/裝置狀態由系統判定 →
// 沒過的可以「今天略過」（不必填原因）→ 自訂項目打勾 → 全部完成後不再自動跳。
//
// 斷言攔到的 request/response 與後端實際狀態，不是只看畫面文字。
// 需 backend + frontend + hardware-agent（可 fake）已起；SMOKE_ALLOW_WRITE=1。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會寫入檢查狀態，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const page = await context.newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const posts = [];
page.on("request", (req) => {
  if (req.method() === "POST" && /\/api\/v1\/opening-check\//.test(req.url())) {
    try {
      posts.push({ url: req.url(), body: JSON.parse(req.postData() ?? "{}") });
    } catch {
      posts.push({ url: req.url(), body: null });
    }
  }
});

try {
  // 先把今天弄成「未完成」再驗自動導向：同一天重跑時，上一輪已經把它標成完成了
  // （那正是正確行為）。新增一條自訂項目就足以讓今天未完成。
  const password = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
  const login = await context.request.fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    data: { username: "dev-manager", password },
  });
  assert.ok(login.ok(), `登入 API 失敗：${login.status()}`);
  const token = (await login.json()).access_token;
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  // 清掉前幾輪留下的自訂項目：它們會讓「今天是否完成」取決於歷史殘留而非這一輪。
  const existing = await (
    await context.request.fetch(`${API}/api/v1/opening-check/today`, { headers })
  ).json();
  for (const item of existing.items) {
    const removed = await context.request.fetch(
      `${API}/api/v1/opening-check/items/${item.id}`,
      { method: "DELETE", headers },
    );
    assert.ok(removed.ok(), `清除舊項目失敗：${removed.status()}`);
  }

  const seeded = await context.request.fetch(`${API}/api/v1/opening-check/items`, {
    method: "POST",
    headers,
    data: { label: `補零錢-${run}`, href: "/cash" },
  });
  assert.ok(seeded.ok(), `新增自訂項目失敗：${seeded.status()}`);

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', password);
  await page.click('button:has-text("登入")');

  // 1) 當天第一次登入就被帶到檢查頁（不必自己點）
  await page.waitForURL(`${BASE}/opening-check`, { timeout: 15000 });
  ok("第一次進系統自動帶到開店前檢查", true);

  // 2) 開帳狀態由系統判定；裝置由代理回報
  // 同一天重跑時，上一輪的「略過」還留在後端（那正是每店每日共用的行為），
  // 所以這裡接受「待處理」或「今天略過」，不接受「正常」——沒開帳就不該是綠燈。
  const cashRow = page.locator('li:has-text("今日已開帳")');
  await cashRow.waitFor({ timeout: 10000 });
  const cashText = (await cashRow.textContent()) ?? "";
  const alreadySkipped = cashText.includes("今天略過");
  assert.ok(
    cashText.includes("待處理") || alreadySkipped,
    `沒開帳卻顯示 ${cashText.slice(0, 20)}`,
  );
  const deviceRows = await page.locator('li:has-text("標籤機")').count();
  assert.ok(deviceRows > 0, "沒有列出任何裝置（代理連不到？）");
  ok("開帳與裝置狀態由系統判定", true, `裝置列 ${deviceRows} 筆`);
  await page.screenshot({ path: `${SHOTS}/oc-01-initial.png`, fullPage: true });

  // 3) 略過開帳：送出只有 key、沒有原因（裁示）
  if (alreadySkipped) {
    ok("開帳已於今天略過（沿用後端狀態）", true, "同日重跑");
  } else {
    await cashRow.getByRole("button", { name: "今天略過" }).click();
    await page.waitForFunction(
      () =>
        [...document.querySelectorAll("li")]
          .find((li) => li.textContent?.includes("今日已開帳"))
          ?.textContent?.includes("今天略過") ?? false,
      undefined,
      { timeout: 10000 },
    );
    const skipPost = posts.find((p) => p.url.includes("/skip"));
    assert.ok(skipPost, "沒攔到略過請求");
    assert.deepEqual(Object.keys(skipPost.body).sort(), ["key"], "略過不該要求填原因");
    ok("略過只送 key、不必填原因", true, JSON.stringify(skipPost.body));
  }

  // 4) 自訂項目（前面已透過 API 建好）：檢查頁看得到 → 打勾
  const item = page.locator(`li:has-text("補零錢-${run}")`);
  await item.waitFor({ timeout: 10000 });
  // 用 click 不用 check：勾選是受控元件，要等後端回來才會變成已勾，
  // check() 會在點完當下就驗狀態而失敗。
  await item.locator('input[type="checkbox"]').click();
  await page.waitForFunction(
    (label) => {
      const li = [...document.querySelectorAll("li")].find((n) =>
        n.textContent?.includes(label),
      );
      return li?.querySelector("input[type=checkbox]")?.checked ?? false;
    },
    `補零錢-${run}`,
    { timeout: 10000 },
  );
  ok("自訂項目可打勾", true);

  // 5) 後端狀態與畫面一致，且「完成」是每店每日共用（用 API 直接確認）
  const today = await (
    await context.request.fetch(`${API}/api/v1/opening-check/today`, { headers })
  ).json();
  assert.equal(today.completed, true, `後端仍未完成：${JSON.stringify(today)}`);
  assert.ok(today.skipped_keys.includes("cash_session"));
  ok("後端記錄每店每日的完成狀態", true, `略過 ${today.skipped_keys.join(",")}`);
  await page.screenshot({ path: `${SHOTS}/oc-02-complete.png`, fullPage: true });

  // 6) 全部完成後，重新登入不再被自動帶到檢查頁
  const fresh = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page2 = await fresh.newPage();
  await page2.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page2.fill('input[name="username"]', "dev-manager");
  await page2.fill('input[name="password"]', password);
  await page2.click('button:has-text("登入")');
  await page2.waitForURL(`${BASE}/`, { timeout: 15000 });
  await page2.waitForTimeout(1500);
  assert.equal(new URL(page2.url()).pathname, "/", "完成後仍被帶去檢查頁");
  ok("完成後不再自動跳出", true);
  await fresh.close();
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
