// 手冊 03：會員/賣方——建檔、查找（姓名電話 / 身分證）、所有會員、會員 360 詳情五分頁。
import { writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  apiJson, apiLogin, BASE, login, makeShot, newBrowser, note, shotsDir, uniquePhone, validNationalId,
} from "./_lib.mjs";

const dir = shotsDir("03-contacts");
const shot = makeShot(dir);
const { browser, page } = await newBrowser();
await login(page);

const MAIN = { name: "手冊測試客-林大山", phone: uniquePhone(), nid: validNationalId(1234567), addr: "臺北市大安區測試路 1 號" };
const SECOND = { name: "手冊測試客-陳小美", phone: String(Number(uniquePhone()) + 3), nid: validNationalId(7654321) };

await page.goto(`${BASE}/contacts`, { waitUntil: "networkidle" });
await page.waitForTimeout(600);
await shot(page, "page-search-tab", { content: true });

// ── 建檔（含三角色）──
await page.fill('input[name="name"]', MAIN.name);
await page.fill('input[name="phone"]', MAIN.phone);
await page.fill('input[name="address"]', MAIN.addr);
await page.fill('input[name="national_id"]', MAIN.nid);
for (const role of ["賣方", "寄售人"]) {
  await page.locator(`.member-role-check:has-text("${role}") input`).check();
}
await shot(page, "create-form-filled", { locator: '.card:has(h2:text("新增會員/賣方"))' });
await page.click('button:has-text("建檔")');
await page.waitForTimeout(1500);

// 身分證錯誤示範
await page.fill('input[name="name"]', "手冊測試客-錯誤證號");
await page.fill('input[name="phone"]', "0900000000");
await page.fill('input[name="national_id"]', "A123456780");
await page.click('button:has-text("建檔")');
await page.waitForTimeout(600);
const nidErr = await page.textContent('.card:has(h2:text("新增會員/賣方")) .form-error').catch(() => null);
note(`身分證檢核失敗訊息：${nidErr}`);
await shot(page, "create-nid-error", { locator: '.card:has(h2:text("新增會員/賣方")) ' });

// 第二位會員（只有 MEMBER 角色）
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(600);
await page.fill('input[name="name"]', SECOND.name);
await page.fill('input[name="phone"]', SECOND.phone);
await page.fill('input[name="national_id"]', SECOND.nid);
await page.click('button:has-text("建檔")');
await page.waitForTimeout(1200);

// ── 查找：姓名/電話 ──
await page.fill('input[aria-label="姓名或電話搜尋"]', "手冊測試客");
await page.click('button:has-text("搜尋")');
await page.waitForSelector(".member-list", { timeout: 8000 });
await page.waitForTimeout(500);
await shot(page, "search-by-name", { locator: '.card:has(h2:text("搜尋結果"))' });

// ── 查找：身分證（精確）──
await page.click('button:has-text("身分證字號（精確）")');
await page.waitForTimeout(300);
await page.fill('input[aria-label="身分證字號搜尋"]', "A123456780");
await page.click('button:has-text("搜尋")');
await page.waitForTimeout(500);
const searchErr = await page.textContent(".member-search .form-error").catch(() => null);
note(`身分證搜尋格式錯誤訊息：${searchErr}`);
await shot(page, "search-nid-error", { locator: ".member-search" });

await page.fill('input[aria-label="身分證字號搜尋"]', MAIN.nid);
await page.click('button:has-text("搜尋")');
await page.waitForTimeout(1200);
await shot(page, "search-nid-hit", { content: true });

// ── 所有會員分頁 ──
await page.click('button:has-text("所有會員")');
await page.waitForSelector(".member-table", { timeout: 8000 });
await page.waitForTimeout(600);
await shot(page, "all-members", { locator: ".member-table-wrap" });
await page.fill('input[aria-label="會員清單篩選"]', "陳小美");
await page.click('button:has-text("篩選")');
await page.waitForTimeout(800);
await shot(page, "all-members-filtered", { locator: ".card:has(.member-table)" });

// ── 進入會員詳情 ──
// **先篩選再點，不要清除**：清除後列出的是全部聯絡人的第一頁，而 demo 資料有三千多筆，
// 剛建立的這位排在最後一頁根本點不到。腳本原本寫「清除→點名字」，在只有十幾筆測資的
// 年代可行，被 12 個月的真實資料打壞——**不要假設剛建立的紀錄會出現在列表第一頁**。
await page.fill('input[aria-label="會員清單篩選"]', MAIN.name);
await page.click('button:has-text("篩選")');
await page.waitForSelector(`a.member-table-link:has-text("${MAIN.name}")`, { timeout: 15000 });
await page.waitForTimeout(400);
await page.click(`a.member-table-link:has-text("${MAIN.name}")`);
await page.waitForURL(/\/contacts\/\d+$/);
await page.waitForTimeout(1200);
const contactId = Number(page.url().split("/").pop());
note(`主角會員 id=${contactId}`);
await shot(page, "detail-overview", { content: true });

for (const [label, slug] of [
  ["消費紀錄", "detail-purchases"],
  ["寄售", "detail-consignments"],
  ["帶來的商品", "detail-sourced"],
]) {
  await page.click(`.member-tab:has-text("${label}")`);
  await page.waitForTimeout(900);
  await shot(page, slug, { content: true });
}

// 編輯分頁
await page.click('.member-tab:has-text("編輯")');
await page.waitForTimeout(900);
await shot(page, "detail-edit", { content: true });
await page.fill('input[name="source_note"]', "操作手冊測試備註");
await page.click('button:has-text("儲存")');
await page.waitForSelector(".form-success:has-text('已更新')", { timeout: 8000 });
await shot(page, "detail-edit-saved", { locator: '.card:has(h2:text("基本資料"))' });

// 查看身分證（寫稽核）
await page.click('button:has-text("查看身分證（寫稽核）")');
await page.waitForTimeout(1200);
const revealed = await page.textContent(".member-id-actions .money").catch(() => null);
note(`查看身分證回傳：${revealed}`);
await shot(page, "detail-reveal-nid", { locator: '.card:has(h2:text("角色與身分證"))' });

// **寫入前先向後端確認實際存的是什麼**（而不是把本地變數當成事實）。
//
// 這支腳本按下「建檔」後從不檢查結果，而 `validNationalId(1234567)` 是**固定種子**——
// 重跑會產生同一組身分證、被當成重複建檔而拒絕，但腳本照樣往下走，
// 把一個**從未存進資料庫的電話**寫進 data.json。
// 後面每一支 POS 腳本都靠那個電話找人，於是整串 08/09 系列連鎖失敗，
// 而錯誤訊息只說「找不到會員」，看不出源頭在這裡。
const token = await apiLogin();
const confirmed = {};
for (const [key, person] of [["MAIN", MAIN], ["SECOND", SECOND]]) {
  const found = await apiJson(token, "GET", `/api/v1/contacts?q=${encodeURIComponent(person.name)}`);
  const rows = Array.isArray(found.json) ? found.json : (found.json?.items ?? []);
  const hit = rows.find((c) => c.name === person.name);
  if (hit === undefined) {
    throw new Error(`建檔未成功：資料庫查不到「${person.name}」，後續腳本會連鎖失敗`);
  }
  if (hit.phone !== person.phone) {
    note(`「${person.name}」已存在（電話 ${hit.phone}），沿用既有那筆而非本次產生的號碼`);
  }
  confirmed[key] = { ...person, phone: hit.phone, id: hit.id };
}

writeFileSync(
  join(dir, "data.json"),
  JSON.stringify({ MAIN: confirmed.MAIN, SECOND: confirmed.SECOND, contactId }, null, 2),
);
await browser.close();
console.log("✅ 03-contacts 完成");
