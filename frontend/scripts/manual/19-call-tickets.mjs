// 手冊 19：叫號（docs/38）——取號 → 候位清單 → 完成 → 顯示已完成；**含跨日情境**。
//
// 跨日是這個功能最容易被誤解的地方，而且實際行為與直覺不同：
//   - 號碼**每天從 1 重新開始**
//   - **昨天沒完成的不會留在今天的候位清單**（docs/38 裁示：站在今天的角度已不重要）
//   - 但資料沒有刪：勾「顯示已完成」就找得回來，號碼前面會標日期（`8/20 #1`）
// 手冊要說明這件事，就必須同時拍到「乾淨的今日候位清單」與「勾選後看見昨天的」。
import { execSync } from "node:child_process";

import { BASE, apiJson, apiLogin, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("19-call-tickets");
const shot = makeShot(dir);
const token = await apiLogin();

function runSql(sql) {
  const db = process.env.MANUAL_DB ?? "lucamp_manual";
  const pw = process.env.MANUAL_DB_PASSWORD ?? process.env.PGPASSWORD;
  if (!pw) throw new Error("需要 MANUAL_DB_PASSWORD（造跨日情境要直接改資料庫）");
  execSync(`docker exec -e PGPASSWORD='${pw}' lu-camp-db-1 psql -U lucamp -d ${db} -q -c "${sql}"`, {
    stdio: "pipe",
  });
}

/** 把某張叫號單改成昨天——**沒有 API 可以造舊資料**（號碼一律配今天）。
 *
 * **號碼要一起重配**：`(store_id, ticket_date, ticket_no)` 是唯一鍵，而昨天通常
 * 已經有 #1 了；直接搬日期會撞上唯一約束（實測擋下）。取昨天最大號碼 +1。
 */
function backdateToYesterday(ticketId) {
  runSql(
    `UPDATE call_tickets t SET ticket_date = d.day, created_at = t.created_at - interval '1 day',` +
      ` ticket_no = COALESCE(` +
      `   (SELECT max(x.ticket_no) FROM call_tickets x` +
      `     WHERE x.store_id = t.store_id AND x.ticket_date = d.day), 0) + 1` +
      ` FROM (SELECT (now() AT TIME ZONE 'Asia/Taipei')::date - 1 AS day) d` +
      ` WHERE t.id = ${Number(ticketId)}`,
  );
}

/** 清掉今天既有的叫號，讓走查從乾淨的畫面開始。 */
function clearToday() {
  runSql(
    "DELETE FROM call_tickets WHERE ticket_date = (now() AT TIME ZONE 'Asia/Taipei')::date",
  );
}

// **先清今天的**：seed 會預先放好幾張候位單，不清的話畫面上同一個「陳先生」會出現
// 好幾次，讀者會以為是重複的 bug；而且第一張截圖叫「empty」卻不是空的。
clearToday();

// ── 先製造昨天的兩張：一張未完成、一張已完成 ──────────────────────
async function issue(name, memo) {
  const res = await apiJson(token, "POST", "/api/v1/call-tickets", { name, note: memo });
  if (res.status !== 201) throw new Error(`取號失敗 ${res.status}：${JSON.stringify(res.json)}`);
  return res.json;
}

const yFresh = await issue("昨天的黃小姐", "昨天留下來沒處理完的");
const yDone = await issue("昨天的陳先生", "昨天已經處理完");
await apiJson(token, "POST", `/api/v1/call-tickets/${yDone.id}/complete`, {});
backdateToYesterday(yFresh.id);
backdateToYesterday(yDone.id);
note(`已回填兩張昨天的叫號單（#${yFresh.ticket_no} 未完成、#${yDone.ticket_no} 已完成）`);

const { browser, page } = await newBrowser();
await login(page);

// ── 取號 ────────────────────────────────────────────────────────
await page.goto(`${BASE}/call-tickets`, { waitUntil: "networkidle" });
await page.waitForTimeout(900);
await shot(page, "empty", { content: true });

await page.fill('.call-ticket-form input >> nth=0', "王先生");
await page.fill('.call-ticket-form input >> nth=1', "https://forms.gle/example123");
await page.fill('.call-ticket-form textarea, .call-ticket-form input >> nth=2', "帳篷兩頂、桌椅一組");
await shot(page, "form-filled", { locator: ".call-ticket-form" });

await page.click(".call-ticket-issue");
await page.waitForTimeout(1200);
// 取號後畫面會放大顯示號碼——這是要念給客人聽的那個數字
await shot(page, "issued-number", { locator: ".call-ticket-issued" });

for (const [name, memo] of [
  ["林小姐", "睡袋三個"],
  ["張太太", "兩箱雜物，需要秤重"],
]) {
  await page.fill('.call-ticket-form input >> nth=0', name);
  await page.fill('.call-ticket-form textarea, .call-ticket-form input >> nth=2', memo);
  await page.click(".call-ticket-issue");
  await page.waitForTimeout(1000);
}

// ── 候位清單：今天的是 #N，昨天未完成的標日期 ────────────────────
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await shot(page, "waiting-list", { locator: ".call-ticket-list-card" });
// **候位清單只有今天的**。昨天沒完成的那張不會出現在這裡——這是刻意的
// （docs/38 裁示：昨天未完成的，站在今天的角度已經不重要）。資料沒有刪，
// 勾「顯示已完成」就找得回來。第一版的說明寫成「同時有今天與昨天」，是錯的。
note("候位清單只有今天的 #1/#2/#3——號碼每天從 1 重新開始，昨天未完成的不再佔用清單");

// ── 完成一位 ────────────────────────────────────────────────────
const firstRow = page.locator(".call-ticket-list tbody tr").first();
await shot(page, "before-complete", { locator: ".call-ticket-list-card", highlight: [".call-ticket-list tbody tr:first-child button"] });
await firstRow.locator('button:has-text("完成")').click();
await page.waitForTimeout(1200);
await shot(page, "after-complete", { locator: ".call-ticket-list-card" });
note("完成後從候位清單消失，但資料仍留著（下一步勾選即可看到）");

// ── 顯示已完成：昨天已完成的那張也會出現、並標日期 ────────────────
await page.locator('input[type="checkbox"]').first().check();
await page.waitForTimeout(1200);
await shot(page, "include-done", { locator: ".call-ticket-list-card", content: false });
note("勾選「顯示已完成」後，前幾天的單才會出現，且號碼前面標著日期（如 8/20 #1）");

await browser.close();
