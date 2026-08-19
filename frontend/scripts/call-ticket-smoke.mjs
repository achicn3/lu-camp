// 叫號系統瀏覽器煙霧（docs/38）：真 backend＋真 Postgres＋真瀏覽器。
// 取號 → 號碼顯示 → 清單 → 完成 → 從清單消失 → 以 include_done 確認資料還在。
// 需 backend(:8000) + frontend(:3000) 已起。
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "call-ticket");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const DB_CONTAINER = process.env.SMOKE_DB_CONTAINER ?? "lu-camp-db-1";
const DB_NAME = process.env.SMOKE_DB_NAME ?? "lucamp_e2e";
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

// 跨日情境只能靠回填日期製造（API 一律用當下時間）。這是**測試資料的佈置**，
// 不是繞過流程：畫面與 API 行為全部走真路徑（同 return-invoice-smoke 的既有做法）。
function sql(statement) {
  return execFileSync(
    "docker",
    ["exec", DB_CONTAINER, "psql", "-U", "lucamp", "-d", DB_NAME, "-tAc", statement],
    { encoding: "utf8" },
  ).trim();
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

  // ── 前一天的資料不得汙染當天（店主特別要求）──
  // 最危險的樣子：昨天的 #3 沒處理完、今天又有一個 #3，店員喊「3 號」時兩個人站起來。
  const ydName = `昨天未完成-${RUN}`;
  const ydDoneName = `昨天已完成-${RUN}`;
  const yd = await api(token, "POST", "/api/v1/call-tickets", { name: ydName });
  const ydDone = await api(token, "POST", "/api/v1/call-tickets", { name: ydDoneName });
  await api(token, "POST", `/api/v1/call-tickets/${ydDone.json.id}/complete`);
  sql(`UPDATE call_tickets SET ticket_date = CURRENT_DATE - 1 WHERE id = ${yd.json.id}`);
  sql(`UPDATE call_tickets SET ticket_date = CURRENT_DATE - 1 WHERE id = ${ydDone.json.id}`);

  // 1) 今天的號碼**不得**接續昨天
  const todayTicket = await api(token, "POST", "/api/v1/call-tickets", {
    name: `今天新客-${RUN}`,
  });
  const todayNo = todayTicket.json.ticket_no;
  const sameDayMax = Number(
    sql(
      `SELECT COALESCE(MAX(ticket_no),0) FROM call_tickets ` +
        `WHERE ticket_date = CURRENT_DATE AND id <> ${todayTicket.json.id}`,
    ),
  );
  ok(
    "今天的號碼只依當日流水，不接續昨天",
    todayNo === sameDayMax + 1,
    `今天配到 #${todayNo}，當日既有最大 #${sameDayMax}`,
  );

  // **強制製造真正的撞號**：把昨天那筆的號碼改成與今天這筆相同。
  // 不這樣做，「同號不同日分得開」那條斷言會因為根本沒撞到而空過（假綠）。
  sql(`UPDATE call_tickets SET ticket_no = ${todayNo} WHERE id = ${yd.json.id}`);

  await page.reload({ waitUntil: "networkidle" });
  await page.waitForSelector("table.call-ticket-list tbody tr");

  // 2) 昨天沒處理完的仍在清單（客人真的還在等），但**必須標日期**
  const ydRow = page.locator("table.call-ticket-list tbody tr", { hasText: ydName });
  await ydRow.waitFor({ timeout: 15000 });
  const ydLabel = ((await ydRow.locator("td").first().textContent()) ?? "").trim();
  ok(
    "昨天未完成的仍在清單，且號碼標了日期（否則與今天的號碼混淆）",
    /^\d+\/\d+ #\d+$/.test(ydLabel),
    ydLabel,
  );

  // 3) 昨天**已完成**的不得混進今天的候位清單
  ok(
    "昨天已完成的不出現在候位清單",
    (await page.locator("table.call-ticket-list tbody tr", { hasText: ydDoneName }).count()) === 0,
  );

  // 4) 同號不同日必須在畫面上分得開——這是「喊 3 號兩個人站起來」的防線
  const labels = await page.locator("table.call-ticket-list tbody td:first-child").allTextContents();
  const trimmed = labels.map((t) => t.trim());
  // 先確認這一輪**真的有撞號**，否則下面那條斷言等於沒驗
  const collided = trimmed.filter((t) => t.endsWith(`#${todayNo}`));
  ok(
    "本輪真的製造出同號不同日（否則下一條斷言是空的）",
    collided.length === 2,
    collided.join(" | "),
  );
  ok(
    "同號不同日在畫面上分得開（標籤不重複）",
    new Set(trimmed).size === trimmed.length,
    trimmed.join(" | "),
  );
  await page.screenshot({ path: `${SHOTS}/05-cross-day.png` });

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
