// 行事曆瀏覽器煙霧（docs/99 第一期 1A）：
// 新增單次待辦 → 打勾 → 新增重複待辦 → 這一次不用做 → 新增公休 → 員工名單與負責人 →
// 首頁與開店前檢查的「今天」→ 導覽列數字 → 過期好幾次的重複待辦一鍵略過 → 平板的清單看法。
//
// 斷言攔到的 request 與後端實際狀態，不是只看畫面文字。結束時把這一輪建的資料刪掉
// （封存），重跑不會越積越多。需 backend + frontend 已起；SMOKE_ALLOW_WRITE=1。
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API ?? "http://localhost:8000";
const SHOTS = join(process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots"), "calendar");
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會寫入待辦與事件，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const run = randomUUID().slice(0, 6);
// 台北的今天（煙霧可能在 UTC 午夜前後跑，不能用本機日期）。
const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());
const shift = (iso, days) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const pyWeekday = (iso) => (new Date(`${iso}T00:00:00Z`).getUTCDay() + 6) % 7;
const WEEKDAY_LABEL = ["一", "二", "三", "四", "五", "六", "日"];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1366, height: 1000 } });
const page = await context.newPage();
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const posts = [];
page.on("request", (req) => {
  if (["POST", "PATCH"].includes(req.method()) && /\/api\/v1\/(agenda|staff-members)/.test(req.url())) {
    let body = null;
    try {
      body = JSON.parse(req.postData() ?? "null");
    } catch {
      body = null;
    }
    posts.push({ method: req.method(), url: req.url(), body });
  }
});

const created = { tasks: [], series: [], events: [], staff: [] };
let headers;

async function apiGet(path) {
  const res = await context.request.fetch(`${API}${path}`, { headers });
  assert.ok(res.ok(), `GET ${path} 失敗：${res.status()}`);
  return res.json();
}

async function waitForPost(pattern, method = "POST") {
  for (let i = 0; i < 50; i += 1) {
    const hit = posts.find((p) => p.method === method && pattern.test(p.url));
    if (hit) return hit;
    await page.waitForTimeout(100);
  }
  throw new Error(`沒有送出 ${method} ${pattern}`);
}

try {
  const login = await context.request.fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    data: { username: "dev-manager", password: PASSWORD },
  });
  assert.ok(login.ok(), `登入 API 失敗：${login.status()}`);
  headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
    "Content-Type": "application/json",
  };

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 15000 });
  // 每天第一次進系統會被帶去開店前檢查（那是另一支煙霧在驗）；先標成今天已帶過，免得中途被導走。
  await page.evaluate((day) => localStorage.setItem(`lu-camp.opening-check.${day}`, "1"), today);

  // 1) 行事曆頁：月曆＋當天
  await page.goto(`${BASE}/calendar?day=${today}`, { waitUntil: "networkidle" });
  await page.locator(".agenda-grid").waitFor({ timeout: 15000 });
  const dayPanel = page.locator("aside.agenda-day");
  ok("行事曆頁打得開，月曆與當天清單都在", (await dayPanel.count()) === 1);

  // 2) 新增單次待辦（預設帶當天）
  posts.length = 0;
  const taskTitle = `回覆廠商-${run}`;
  await page.click('button:has-text("＋ 新增待辦")');
  const taskDialog = page.getByRole("dialog", { name: "新增待辦" });
  await taskDialog.getByLabel("要做什麼").fill(taskTitle);
  await taskDialog.getByRole("button", { name: "儲存" }).click();
  const taskPost = await waitForPost(/\/api\/v1\/agenda\/tasks$/);
  ok("新增單次待辦帶當天日期", taskPost.body?.due_on === today, JSON.stringify(taskPost.body));
  await dayPanel.getByText(taskTitle).waitFor({ timeout: 10000 });
  const feed1 = await apiGet(`/api/v1/agenda/items?start=${today}&end=${today}`);
  const taskItem = feed1.items.find((i) => i.title === taskTitle);
  ok("後端有這筆待辦", taskItem !== undefined);
  created.tasks.push(taskItem?.task_id);

  // 3) 打勾完成 → 按下立刻打勾、後端狀態 DONE；當天清單留著（劃掉），月曆格子預設不放
  await dayPanel.getByRole("checkbox", { name: taskTitle }).check();
  await waitForPost(/\/agenda\/tasks\/\d+\/status$/);
  const doneRow = dayPanel.locator("li.agenda-tone-done", { hasText: taskTitle });
  await doneRow.waitFor({ timeout: 10000 });
  const doneTask = await apiGet(`/api/v1/agenda/tasks/${taskItem.task_id}`);
  ok(
    "打勾後後端是已完成，並記下是誰",
    doneTask.status === "DONE" && doneTask.resolved_by_name === "dev-manager",
    `${doneTask.status}/${doneTask.resolved_by_name}`,
  );
  ok("當天清單留著做完的（劃掉）", (await doneRow.count()) === 1);
  ok(
    "月曆格子預設不放已完成",
    (await page.locator(".agenda-cell-today").getByText(taskTitle).count()) === 0,
  );

  // 4) 新增重複待辦：每週的今天這個星期
  posts.length = 0;
  const seriesTitle = `叫牛奶-${run}`;
  await page.click('button:has-text("＋ 新增待辦")');
  const seriesDialog = page.getByRole("dialog", { name: "新增待辦" });
  await seriesDialog.getByLabel("要做什麼").fill(seriesTitle);
  await seriesDialog.getByRole("radio", { name: "每週" }).click();
  await seriesDialog.getByRole("button", { name: `週${WEEKDAY_LABEL[pyWeekday(today)]}` }).click();
  await page.screenshot({ path: join(SHOTS, "01-new-recurring-task.png") });
  await seriesDialog.getByRole("button", { name: "儲存" }).click();
  const seriesPost = await waitForPost(/\/api\/v1\/agenda\/task-series$/);
  ok(
    "重複待辦送出規則",
    seriesPost.body?.recurrence?.kind === "WEEKLY" &&
      seriesPost.body.recurrence.weekdays?.[0] === pyWeekday(today),
    JSON.stringify(seriesPost.body?.recurrence),
  );
  await dayPanel.getByRole("button", { name: seriesTitle }).waitFor({ timeout: 10000 });
  const seriesList = await apiGet("/api/v1/agenda/task-series");
  const series = seriesList.find((s) => s.title === seriesTitle);
  created.series.push(series?.id);
  ok("重複待辦今天出現、下一次是今天", series?.next_on === today, series?.next_on);

  // 5) 這一次不用做
  await dayPanel.getByRole("button", { name: seriesTitle }).click();
  const occDialog = page.getByRole("dialog", { name: seriesTitle });
  await occDialog.getByText("在這裡改只影響這一次").waitFor();
  await occDialog.getByRole("button", { name: "這次不用做" }).click();
  await waitForPost(new RegExp(`/task-series/${series.id}/occurrences/${today}/status$`));
  // 送出≠完成：成功才會關視窗，等它關了再去問後端。
  await occDialog.waitFor({ state: "detached", timeout: 10000 });
  const feed2 = await apiGet(`/api/v1/agenda/items?start=${today}&end=${today}`);
  const skipped = feed2.items.find((i) => i.series_id === series.id);
  ok("這一次標成不用做（其他次不受影響）", skipped?.status === "SKIPPED", skipped?.status);

  // 6) 公休
  posts.length = 0;
  const closedTitle = `公休-${run}`;
  await page.click('button:has-text("＋ 新增事件")');
  const eventDialog = page.getByRole("dialog", { name: "新增事件" });
  await eventDialog.getByRole("radio", { name: "公休" }).click();
  await eventDialog.getByLabel(/^名稱/).fill(closedTitle);
  await eventDialog.getByRole("button", { name: "儲存" }).click();
  const eventPost = await waitForPost(/\/api\/v1\/agenda\/events$/);
  ok("公休送出", eventPost.body?.kind === "CLOSED" && eventPost.body?.starts_on === today);
  await dayPanel.getByText(closedTitle).waitFor({ timeout: 10000 });
  const todayCell = page.locator(".agenda-cell-today");
  ok("月曆格子上看得到公休", (await todayCell.getByText(closedTitle).count()) === 1);
  const events = await apiGet("/api/v1/agenda/events");
  created.events.push(events.find((e) => e.title === closedTitle)?.id);

  // 7) 員工名單 → 指定負責人
  const staffName = `小王${run}`;
  await page.click('button:has-text("員工名單")');
  const staffDialog = page.getByRole("dialog", { name: "員工名單" });
  await staffDialog.getByLabel("新員工姓名").fill(staffName);
  await staffDialog.getByRole("button", { name: "新增" }).click();
  await staffDialog.getByLabel(`${staffName} 的姓名`).waitFor({ timeout: 10000 });
  ok("員工名單新增成功", true);
  const staff = (await apiGet("/api/v1/staff-members")).find((m) => m.name === staffName);
  created.staff.push(staff?.id);
  await staffDialog.getByRole("button", { name: "關閉" }).click();

  const assignedTitle = `盤點帳篷-${run}`;
  await page.click('button:has-text("＋ 新增待辦")');
  const assignDialog = page.getByRole("dialog", { name: "新增待辦" });
  await assignDialog.getByLabel("要做什麼").fill(assignedTitle);
  await assignDialog.getByLabel("負責人（選填）").selectOption({ label: staffName });
  await assignDialog.getByRole("button", { name: "儲存" }).click();
  const assignedRow = dayPanel.locator("li", { hasText: assignedTitle });
  await assignedRow.waitFor({ timeout: 10000 });
  ok("負責人顯示在待辦上", (await assignedRow.textContent())?.includes(staffName) ?? false);
  const feed3 = await apiGet(`/api/v1/agenda/items?start=${today}&end=${today}`);
  created.tasks.push(feed3.items.find((i) => i.title === assignedTitle)?.task_id);
  await page.screenshot({ path: join(SHOTS, "02-calendar-month.png") });

  // 7b) 換月：右邊跟著換到新月份的第一天（Codex 1A 第一輪 P2）
  const nextMonth = shift(`${today.slice(0, 7)}-01`, 40).slice(0, 7);
  const [nm, nd] = [Number(nextMonth.slice(5, 7)), 1];
  await page.getByRole("button", { name: "下個月" }).click();
  await page.locator(`aside[aria-label^="${nm}/${nd}（"]`).waitFor({ timeout: 10000 });
  ok("換月後右邊是新月份的第一天", true);
  await page.getByRole("button", { name: "今天", exact: true }).click();
  await dayPanel.getByText(assignedTitle).waitFor({ timeout: 10000 });

  // 7c) 沒有日期的待辦：打勾後收進「做完的 N 筆」，按錯可以改回來（Codex 1A 第一輪 P2）
  const undatedTitle = `整理倉庫-${run}`;
  const undatedRes = await context.request.fetch(`${API}/api/v1/agenda/tasks`, {
    method: "POST",
    headers,
    data: { title: undatedTitle },
  });
  assert.ok(undatedRes.ok(), `建沒有日期的待辦失敗：${undatedRes.status()}`);
  const undatedId = (await undatedRes.json()).id;
  created.tasks.push(undatedId);
  await page.reload({ waitUntil: "networkidle" });
  const undatedCard = page.locator(".card", { has: page.getByRole("heading", { name: "沒有日期的待辦" }) });
  // 打勾後這一列會收進「做完的」，元素隨即離開清單，所以用 click 而不是 check（check 會回頭驗狀態）。
  await undatedCard.getByRole("checkbox", { name: undatedTitle }).click();
  await undatedCard.getByRole("checkbox", { name: undatedTitle }).waitFor({ state: "detached", timeout: 10000 });
  await undatedCard.getByRole("button", { name: /^做完的 \d+ 筆$/ }).click();
  await undatedCard.getByRole("checkbox", { name: undatedTitle }).uncheck();
  await page.waitForTimeout(500);
  const reopened = await apiGet(`/api/v1/agenda/tasks/${undatedId}`);
  ok("沒有日期的待辦做完後可以在「做完的」改回來", reopened.status === "OPEN", reopened.status);

  // 8) 過期好幾次的重複待辦（兩週前開始、每天一次）→ 首頁一行顯示、一鍵略過
  const overdueTitle = `補發票紙-${run}`;
  const overdueRes = await context.request.fetch(`${API}/api/v1/agenda/task-series`, {
    method: "POST",
    headers,
    data: {
      title: overdueTitle,
      starts_on: shift(today, -3),
      lead_days: 0,
      recurrence: { kind: "WEEKLY", weekdays: [0, 1, 2, 3, 4, 5, 6] },
    },
  });
  assert.ok(overdueRes.ok(), `建過期的重複待辦失敗：${overdueRes.status()}`);
  created.series.push((await overdueRes.json()).id);

  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const panel = page.getByRole("region", { name: "今天" });
  await panel.getByText(`${overdueTitle}：3 次沒打勾`).waitFor({ timeout: 15000 });
  ok("首頁「今天」把過期 3 次合成一行", true);
  ok("首頁「今天」列出今天要做的", (await panel.getByText(assignedTitle).count()) === 1);
  const badge = page.locator('nav[aria-label="主要導覽"] a[href="/calendar"] .nav-badge');
  const badgeText = (await badge.textContent({ timeout: 10000 })) ?? "";
  const todayInfo = await apiGet("/api/v1/agenda/today");
  ok(
    "導覽列數字＝今天到期未做＋已過期",
    Number(badgeText) === todayInfo.badge_count && todayInfo.badge_count >= 4,
    `畫面 ${badgeText}／後端 ${todayInfo.badge_count}`,
  );
  await page.screenshot({ path: join(SHOTS, "03-home-today.png") });

  const overdueRow = panel.locator("li", { hasText: `${overdueTitle}：3 次沒打勾` });
  await overdueRow.getByRole("button", { name: "都不用做了" }).click();
  await overdueRow.waitFor({ state: "detached", timeout: 10000 });
  const afterSkip = await apiGet("/api/v1/agenda/today");
  ok(
    "一鍵略過後不再列為過期",
    !afterSkip.overdue.some((i) => i.title === overdueTitle),
    `剩 ${afterSkip.overdue.length} 筆過期`,
  );

  // 9) 開店前檢查也有「今天的行事曆」，只顯示、不影響完成
  await page.goto(`${BASE}/opening-check`, { waitUntil: "networkidle" });
  const openingPanel = page.getByRole("region", { name: "今天的行事曆" });
  await openingPanel.getByText(assignedTitle).waitFor({ timeout: 15000 });
  ok("開店前檢查看得到今天的行事曆", true);

  // 10) 平板寬度：清單看法
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.goto(`${BASE}/calendar`, { waitUntil: "networkidle" });
  await page.getByRole("radio", { name: "未來兩週" }).click();
  await page.locator(".agenda-listview").getByText(assignedTitle).waitFor({ timeout: 10000 });
  ok("平板的清單看法列出今天的待辦", true);
  await page.screenshot({ path: join(SHOTS, "04-tablet-list.png") });

  // 10b) 從「今天」點過期的待辦會帶 ?day=：清單要從那天開始（Codex 1A 第二輪 P2），
  // 看法（未來兩週）也要記住，不會被拉回月曆。
  const past = shift(today, -3);
  await page.goto(`${BASE}/calendar?day=${past}`, { waitUntil: "networkidle" });
  const rangeTitle = page.locator(".agenda-listview .agenda-month-nav h2");
  await rangeTitle.waitFor({ timeout: 10000 });
  const pastLabel = `${Number(past.slice(5, 7))}/${Number(past.slice(8, 10))}（`;
  ok(
    "連結帶日期時，清單從那天開始、看法沿用未來兩週",
    ((await rangeTitle.textContent()) ?? "").startsWith(pastLabel),
    (await rangeTitle.textContent()) ?? "",
  );
  const noHorizontalScroll = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  );
  ok("平板寬度沒有左右捲動", noHorizontalScroll);
} catch (err) {
  ok("流程中斷", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png") }).catch(() => {});
} finally {
  // 收尾：刪掉（封存）這一輪建的資料，員工停用。
  if (headers) {
    const del = (path) => context.request.fetch(`${API}${path}`, { method: "DELETE", headers });
    for (const id of created.tasks.filter(Boolean)) await del(`/api/v1/agenda/tasks/${id}`);
    for (const id of created.series.filter(Boolean)) await del(`/api/v1/agenda/task-series/${id}`);
    for (const id of created.events.filter(Boolean)) await del(`/api/v1/agenda/events/${id}`);
    for (const id of created.staff.filter(Boolean)) {
      await context.request.fetch(`${API}/api/v1/staff-members/${id}`, {
        method: "PATCH",
        headers,
        data: { is_active: false },
      });
    }
  }
  await browser.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過；截圖在 ${SHOTS}`);
process.exit(passed === results.length ? 0 : 1);
