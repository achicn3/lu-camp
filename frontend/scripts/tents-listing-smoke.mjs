// 寄售帳篷上官網瀏覽器煙霧測試（docs/97）：POS 編輯官網資料與照片 → 推上雲端 → 官網（自己的網域）
// 讀得到、點餐網址讀不到 → POS 結帳掃到保留中的帳篷跳提醒、照常成交 → 官網移到「最近售出」、照片從雲端刪掉。
//
// 需要（見 docs/20、docs/97）：
//   - backend :8000（ONLINE_ORDER_BASE_URL 指向本機線上點餐 Worker）、已跑 seed_dev_store／seed_dev_user／seed_dev_tents
//   - frontend :3000
//   - 線上點餐 Worker：cd online-order && npx wrangler dev --env production --local --port 8790
//   - 官網 Worker：cd ~/lukeng-homepage && npx wrangler dev --local --port 8787（service binding 接上面那個）
// 執行：node scripts/tents-listing-smoke.mjs
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";
import { PNG } from "pngjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SITE = process.env.SMOKE_SITE ?? "http://127.0.0.1:8787";
const ORDER = process.env.SMOKE_ORDER ?? "http://127.0.0.1:8790";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "tents-listing");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

// 測試照片：兩張不同顏色的 PNG（後端會轉成 WebP）。
function photo(dir, name, [r, g, b]) {
  const png = new PNG({ width: 320, height: 240 });
  for (let i = 0; i < png.data.length; i += 4) {
    png.data[i] = r;
    png.data[i + 1] = g;
    png.data[i + 2] = b;
    png.data[i + 3] = 255;
  }
  const path = join(dir, name);
  writeFileSync(path, PNG.sync.write(png));
  return path;
}

async function feed() {
  const resp = await fetch(`${SITE}/api/tents`);
  return { status: resp.status, body: resp.status === 200 ? await resp.json() : null };
}

async function openEditor(page, code) {
  await page.click(`button[aria-label="編輯 ${code}"]`);
  const dialog = page.getByRole("dialog", { name: new RegExp(code) });
  await dialog.waitFor();
  return dialog;
}

async function saveAndWaitSynced(page, dialog) {
  await dialog.getByRole("button", { name: "儲存" }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.locator(".tents-sync-ok").waitFor({ timeout: 20000 });
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (err) => ok("POS 頁面 JS 錯誤", false, String(err)));

try {
  const tmp = mkdtempSync(join(tmpdir(), "tents-smoke-"));
  const photos = [photo(tmp, "a.png", [190, 170, 130]), photo(tmp, "b.png", [90, 100, 80])];

  // 1) 登入 → 官網寄售帳篷
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/consignment/tents`, { waitUntil: "networkidle" });
  await page.locator("tr", { hasText: "TENT-WEB-01" }).waitFor();
  ok("列出在庫寄售帳篷", (await page.locator("tbody tr").count()) === 3);
  await page.screenshot({ path: join(SHOTS, "01-pos-list.png"), fullPage: true });

  // 2) 第一頂：上架＋保留＋資料＋兩張照片
  let dialog = await openEditor(page, "TENT-WEB-01");
  await dialog.getByLabel("上架官網").check();
  await dialog.getByLabel(/保留中/).check();
  await dialog.getByLabel("顏色").fill("象牙白");
  await dialog.getByLabel("使用過幾露").fill("6");
  await dialog.getByLabel(/瑕疵/).fill("外帳頂部一處淡色污漬，不影響防水");
  await dialog.getByLabel(/額外配件/).fill("原廠地墊\n原廠地布");
  await dialog.getByLabel(/搭帳影片/).fill("https://youtu.be/cSArnUe7cEY");
  await dialog.locator('input[type="file"]').setInputFiles(photos);
  await dialog.locator(".tents-photo img").nth(1).waitFor();
  ok("兩張照片上傳完成", (await dialog.locator(".tents-photo img").count()) === 2);
  await dialog.getByRole("button", { name: "第 2 張往前" }).click();
  await page.waitForTimeout(500);
  await dialog.screenshot({ path: join(SHOTS, "02-pos-edit-dialog.png") });
  await saveAndWaitSynced(page, dialog);
  ok("存檔後官網同步", true);

  // 3) 第二頂：只上架
  dialog = await openEditor(page, "TENT-WEB-02");
  await dialog.getByLabel("上架官網").check();
  await dialog.getByLabel("使用過幾露").fill("9");
  await dialog.getByLabel(/缺件/).fill("營釘少 4 支");
  await saveAndWaitSynced(page, dialog);
  await page.screenshot({ path: join(SHOTS, "03-pos-list-synced.png"), fullPage: true });

  // 4) 官網自己的網域讀得到；點餐網址讀不到；不帶內部資料
  const live = await feed();
  const ids = (live.body?.tents ?? []).map((t) => `${t.id}:${t.status}`).sort();
  ok("官網 /api/tents 有兩頂", JSON.stringify(ids) === JSON.stringify(["TENT-WEB-01:reserved", "TENT-WEB-02:available"]), ids.join(","));
  const first = live.body.tents.find((t) => t.id === "TENT-WEB-01");
  ok("照片是官網網域的相對網址", first.photos.length === 2 && first.photos.every((p) => p.startsWith("/tent-photos/")));
  const photoResp = await fetch(`${SITE}${first.photos[0]}`);
  ok("官網讀得到照片", photoResp.status === 200 && photoResp.headers.get("content-type") === "image/webp");
  ok("點餐網址讀不到帳篷清單", (await fetch(`${ORDER}/api/tents`)).status === 404);
  ok("點餐網址讀不到帳篷照片", (await fetch(`${ORDER}${first.photos[0]}`)).status === 404);
  ok("清單沒有寄售人等內部資料", !JSON.stringify(live.body).includes("寄售人"));

  // 5) 官網畫面
  const site = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  site.on("pageerror", (err) => ok("官網 JS 錯誤", false, String(err)));
  await site.goto(`${SITE}/tents.html`, { waitUntil: "networkidle" });
  await site.locator(".tl-card").first().waitFor();
  ok("官網清單沒有示範字樣", (await site.locator("[data-demo-note]").isHidden()) && (await site.locator(".tl-demo").count()) === 0);
  await site.screenshot({ path: join(SHOTS, "04-site-list.png"), fullPage: true });
  await site.goto(`${SITE}/tents.html#/tent/TENT-WEB-01`, { waitUntil: "networkidle" });
  await site.locator(".td-main img").waitFor();
  ok("官網詳情頁顯示照片", (await site.locator(".td-thumbs img").count()) === 2);
  await site.screenshot({ path: join(SHOTS, "05-site-detail.png"), fullPage: true });

  // 6) POS 結帳：保留中的帳篷跳提醒，確認後照常成交
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });
  await page.locator('input[name="code"]').waitFor();
  await page.fill('input[name="code"]', "TENT-WEB-01");
  await page.press('input[name="code"]', "Enter");
  await page.locator("text=Amenity Dome M").first().waitFor();
  await page.locator(".pos-tender-mode", { hasText: "現金" }).click();
  await page.click('button:has-text("結帳")');
  const reminder = page.getByRole("dialog", { name: "商品備註提醒" });
  await reminder.waitFor();
  ok("保留中提醒出現", (await reminder.textContent()).includes("官網標示「已有人保留」"));
  await page.screenshot({ path: join(SHOTS, "06-pos-reserved-reminder.png") });
  await reminder.getByRole("button", { name: "已確認，繼續結帳" }).click();
  await page.locator(".pos-complete").waitFor({ timeout: 20000 });
  ok("確認後照常成交", true);

  // 7) 推上官網：移到最近售出、沒有照片、保留取消、雲端照片刪掉
  await page.goto(`${BASE}/consignment/tents`, { waitUntil: "networkidle" });
  const pending = page.getByRole("button", { name: "立即更新官網" });
  if (await pending.isVisible()) await pending.click();
  await page.locator(".tents-sync-ok").waitFor({ timeout: 20000 });
  ok("賣掉的不再列在 POS 清單", (await page.locator("tr", { hasText: "TENT-WEB-01" }).count()) === 0);
  const after = await feed();
  const sold = after.body.tents.find((t) => t.id === "TENT-WEB-01");
  ok("官網標示已售出", sold?.status === "sold");
  ok("售出不帶照片", sold?.photos.length === 0);
  ok("售出不帶影片", sold?.video === null);
  ok("雲端照片已刪", (await fetch(`${SITE}${first.photos[0]}`)).status === 404);
  await site.goto(`${SITE}/tents.html`, { waitUntil: "networkidle" });
  await site.locator(".tl-sold").waitFor();
  ok("官網最近售出區塊", (await site.locator(".tl-sold li").count()) === 1);
  await site.screenshot({ path: join(SHOTS, "07-site-list-after-sale.png"), fullPage: true });
  await site.goto(`${SITE}/tents.html#/tent/TENT-WEB-01`, { waitUntil: "networkidle" });
  await site.locator(".td-sold-note").waitFor();
  ok("售出詳情頁不顯示照片佔位格", (await site.locator(".td-slot, .td-thumbs").count()) === 0);
  await site.screenshot({ path: join(SHOTS, "08-site-sold-detail.png"), fullPage: true });

  // 8) 手機寬度的 POS 管理頁
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await phone.fill('input[name="username"]', "dev-manager");
  await phone.fill('input[name="password"]', "dev-test-123456");
  await phone.click('button:has-text("登入")');
  await phone.waitForURL(`${BASE}/`);
  await phone.goto(`${BASE}/consignment/tents`, { waitUntil: "networkidle" });
  await phone.locator("tbody tr").first().waitFor();
  await openEditor(phone, "TENT-WEB-02");
  await phone.screenshot({ path: join(SHOTS, "09-pos-edit-mobile.png") });
} catch (err) {
  ok("煙霧流程", false, String(err));
  await page.screenshot({ path: join(SHOTS, "zz-failure.png"), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
