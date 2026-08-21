// 手冊 07：餐飲菜單（新增/改價/下架/上架/刪除）＋ 門市活動（建立/啟用/結束/作廢/篩選）。
import { apiJson, apiLogin, BASE, login, makeShot, newBrowser, note, shotsDir } from "./_lib.mjs";

const dir = shotsDir("07-menu-campaigns");
const shot = makeShot(dir);

// **先清掉前幾輪留下的同名活動與菜單品項**。本章每跑一次就建一檔「手冊測試-全店九折」，
// 累積之後畫面上同名的有好幾列，`:has-text(...)` 一次指到多列而失敗
// （Playwright 嚴格模式：strict mode violation）。
// 同一個道理也適用於菜單品項——重跑會建出重複的「手沖咖啡」。
// 不要假設起始狀態是乾淨的。
const token = await apiLogin();
const campaigns = (await apiJson(token, "GET", "/api/v1/campaigns?limit=200")).json ?? [];
const stale = (Array.isArray(campaigns) ? campaigns : campaigns.items ?? []).filter((c) =>
  String(c.name ?? "").startsWith("手冊測試-"),
);
for (const c of stale) {
  // 生效中的要先結束才能作廢；已作廢/已結束的直接跳過
  if (c.status === "ACTIVE") {
    await apiJson(token, "POST", `/api/v1/campaigns/${c.id}/end`, {});
  } else if (c.status === "DRAFT") {
    await apiJson(token, "POST", `/api/v1/campaigns/${c.id}/cancel`, {});
  }
}
if (stale.length > 0) note(`已收掉 ${stale.length} 檔前幾輪留下的「手冊測試-」活動`);

const menuItems = (await apiJson(token, "GET", "/api/v1/menu-items?include_unavailable=true")).json ?? [];
for (const m of (Array.isArray(menuItems) ? menuItems : menuItems.items ?? [])) {
  if (["手沖咖啡", "現烤鬆餅"].includes(m.name)) {
    await apiJson(token, "DELETE", `/api/v1/menu-items/${m.id}`, undefined);
  }
}

const { browser, page } = await newBrowser();
await login(page);

// ══ 餐飲菜單 ══
await page.goto(`${BASE}/menu`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await shot(page, "menu-empty", { content: true });

await page.fill('.menu-form input >> nth=0', "手沖咖啡");
await page.fill('.menu-form input >> nth=1', "150");
await page.fill('.menu-form input >> nth=2', "飲品");
await shot(page, "menu-create-form", { locator: ".menu-form" });
await page.click('button:has-text("新增品項")');
await page.waitForTimeout(1500);

await page.fill('.menu-form input >> nth=0', "現烤鬆餅");
await page.fill('.menu-form input >> nth=1', "120");
await page.fill('.menu-form input >> nth=2', "點心");
await page.click('button:has-text("新增品項")');
await page.waitForTimeout(1500);
await shot(page, "menu-list", { locator: ".menu-list-section" });

// 改價
await page.locator('.inv-table tbody tr:has-text("現烤鬆餅") button:has-text("改價")').click();
await page.waitForTimeout(300);
await shot(page, "menu-edit-price", { locator: '.inv-table tbody tr:has-text("現烤鬆餅")' });
await page.fill('input[aria-label="現烤鬆餅 售價"]', "130");
await page.locator('.inv-table tbody tr:has-text("現烤鬆餅") button:has-text("儲存")').click();
await page.waitForTimeout(1200);

// 下架
await page.locator('.inv-table tbody tr:has-text("現烤鬆餅") button:has-text("下架")').click();
await page.waitForTimeout(1200);
await shot(page, "menu-unavailable", { locator: ".menu-list-section" });
// 再上架
await page.locator('.inv-table tbody tr:has-text("現烤鬆餅") button:has-text("上架")').click();
await page.waitForTimeout(1200);
await shot(page, "menu-available-again", { locator: ".menu-list-section" });

// ══ 門市活動 ══
await page.goto(`${BASE}/campaigns`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await shot(page, "campaign-empty", { content: true });

const now = new Date();
const pad = (n) => String(n).padStart(2, "0");
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const start = new Date(now.getTime() - 3600_000);
const end = new Date(now.getTime() + 86_400_000);

await page.fill('.campaign-form input[placeholder="例如：開幕九折"]', "手冊測試-全店九折");
await page.fill('.campaign-form input[type="number"]', "10");
await page.fill('.campaign-form input[type="datetime-local"] >> nth=0', fmt(start));
await page.fill('.campaign-form input[type="datetime-local"] >> nth=1', fmt(end));
await shot(page, "campaign-create-form", { locator: ".campaign-form" });
await page.click('button:has-text("建立活動")');
await page.waitForTimeout(1800);
await shot(page, "campaign-draft", { locator: ".campaign-list-section" });

// 啟用
await page.locator('.inv-table tbody tr:has-text("手冊測試-全店九折") button:has-text("啟用")').click();
await page.waitForTimeout(1500);
await shot(page, "campaign-active", { locator: ".campaign-list-section" });

// 狀態篩選
await page.locator(".rpt-filters select").selectOption("ACTIVE");
await page.waitForTimeout(1200);
await shot(page, "campaign-filter", { locator: ".campaign-list-section" });
await page.locator(".rpt-filters select").selectOption("ALL");
await page.waitForTimeout(800);

// 另建一筆用來示範作廢
await page.fill('.campaign-form input[placeholder="例如：開幕九折"]', "手冊測試-待作廢活動");
await page.fill('.campaign-form input[type="number"]', "20");
await page.fill('.campaign-form input[type="datetime-local"] >> nth=0', fmt(new Date(now.getTime() + 86_400_000)));
await page.fill('.campaign-form input[type="datetime-local"] >> nth=1', fmt(new Date(now.getTime() + 172_800_000)));
await page.click('button:has-text("建立活動")');
await page.waitForTimeout(1800);
await page.locator('.inv-table tbody tr:has-text("待作廢活動") button:has-text("作廢")').click();
await page.waitForTimeout(1500);
await shot(page, "campaign-cancelled", { locator: ".campaign-list-section" });

await browser.close();
console.log("✅ 07-menu-campaigns 完成");
