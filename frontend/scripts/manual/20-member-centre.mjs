// 手冊 20：會員中心（docs/17 T21）——單一會員的完整往來：帳務摘要、消費紀錄、寄售、
// 帶來的商品、編輯基本資料與角色。
//
// **P1 盤點才發現原本漏了這一頁**：`/contacts` 有腳本，但點進去某位會員的
// `/contacts/[id]` 完全沒拍過——而那才是店員每天真正在看的畫面
// （客人問「我的購物金還剩多少」「上次那件寄售賣掉沒」都在這裡回答）。
//
// 挑一位**五個分頁都有資料**的會員，否則截圖全是空白，說明不了任何事。
import { BASE, apiJson, apiLogin, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("20-member-centre");
const shot = makeShot(dir);
const token = await apiLogin();

/** 找一位**五個分頁都有料**的會員：消費、寄售、帶來的商品、購物金都要有。
 *
 * 挑錯人的話截圖全是空白，說明不了任何事。先用購物金餘額由大到小排（有餘額代表
 * 收購過），再逐一看 overview 的統計數字。
 *
 * 第一版打的是 `/summary`——**那個端點不存在**，於是每個人都不符合、靜靜退回環境變數，
 * 腳本表面上正常跑完。正確的是 `/overview`。
 */
async function pickRichMember() {
  // **要翻頁**：`/contacts/members` 上限 200 筆、沒有排序參數，而有往來紀錄的
  // 通常是後面才建檔的賣方會員（id 2400+）。只讀第一頁的話一個都挑不到——
  // 與電子發票佇列頁同一個毛病（limit 撈不到你要的那筆）。
  const rows = [];
  for (let offset = 0; offset < 3000; offset += 200) {
    const page = await apiJson(token, "GET", `/api/v1/contacts/members?limit=200&offset=${offset}`);
    const batch = Array.isArray(page.json) ? page.json : [];
    if (batch.length === 0) break;
    rows.push(...batch.filter((c) => Number(c.store_credit_balance ?? 0) > 0));
  }
  rows.sort((a, b) => Number(b.store_credit_balance) - Number(a.store_credit_balance));
  for (const c of rows.slice(0, 40)) {
    const detail = await apiJson(token, "GET", `/api/v1/contacts/${c.id}/overview`);
    const counts = detail.json?.counts ?? {};
    // 欄位名以實際回應為準：`{ purchases, consigned_items }`。
    // 先前連猜兩個名字（`purchase_count`／`consignments`）都不對，於是條件永遠不成立。
    // **猜欄位名的成本比查一次高。**
    if (Number(counts.purchases ?? 0) >= 3 && Number(counts.consigned_items ?? 0) >= 1) {
      return c;
    }
  }
  return null;
}

const member = await pickRichMember();
if (member === null) {
  throw new Error(
    "找不到消費≥3 筆且有寄售品的會員——demo 資料不足以說明會員中心，請先補資料而非硬拍空畫面",
  );
}
note(`示範會員：${member.name}（id=${member.id}、購物金 ${member.store_credit_balance}）`);

const { browser, page } = await newBrowser({ width: 1440, height: 1000 });
await login(page);

await page.goto(`${BASE}/contacts/${member.id}`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);

const TABS = ["總覽", "消費紀錄", "寄售", "帶來的商品", "編輯"];
const SLUGS = {
  "總覽": "overview",
  "消費紀錄": "purchases",
  "寄售": "consignments",
  "帶來的商品": "sourced",
  "編輯": "edit",
};

await shot(page, "member-header", { locator: ".member-tabs, .card-stack" });

for (const label of TABS) {
  await page.locator(`.member-tab:has-text("${label}")`).first().click();
  await page.waitForTimeout(1800);
  await shot(page, SLUGS[label], { content: true });
  const text = (await page.textContent(".app-main"))?.replace(/\s+/g, " ").slice(0, 140);
  note(`[${label}] ${text}`);
}

note("總覽的帳務摘要就是店員回答「我購物金還剩多少」「上次寄售的賣掉沒」的地方");
note("「帶來的商品」是這位客人賣給店裡的東西；「寄售」是還沒賣掉、仍屬於他的");

await browser.close();
