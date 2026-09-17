// 設定頁「收購切結書」瀏覽器煙霧：讀現有全文 → 開編輯視窗 → 取消不送出 → 改內容儲存
// → 版本 +1 → 手持裝置（/kiosk 的樣式）不跑版。
//
// **斷言攔到的 request body**（畫面顯示不代表送出的是對的），並實際量測預覽區的寬度：
// 店主可能貼上沒有空白的超長字串，不強制斷行就會把簽署框撐破、客人按不到同意鈕。
//
// 需 backend+frontend 已起。SMOKE_ALLOW_WRITE=1 才會寫入（請用隔離測試庫）。
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots");
assert.equal(
  process.env.SMOKE_ALLOW_WRITE,
  "1",
  "會寫入切結書版本，請指向隔離測試庫並以 SMOKE_ALLOW_WRITE=1 明示",
);
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (err) => ok("頁面 JS 錯誤", false, String(err)));

const sent = [];
page.on("request", (req) => {
  if (/\/api\/v1\/agreements/.test(req.url()) && req.method() === "POST") {
    try {
      sent.push(JSON.parse(req.postData() ?? "{}"));
    } catch {
      sent.push(null);
    }
  }
});

// 跑版考題：沒有空白的超長字串 + 一整片空行 + 全形標點。
// 內容每次都要不一樣：後端對「一字未改」刻意回 200 不發新版（否則開關視窗就多一版），
// 用固定內容重跑會在第二次得到 200，版本號當然不動。
const RUN = Date.now().toString().slice(-6);
const LONG_TOKEN = "台".repeat(120);
const NEW_BODY = [
  `一、物品來源保證（煙霧 ${RUN}）`,
  "本人切結保證所讓售之物品均為合法取得。",
  "",
  "",
  "",
  "二、跑版測試",
  `連續無空白字串：${LONG_TOKEN}`,
  "網址：https://example.com/very/long/path/that/never/breaks/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
].join("\n");

try {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  ok("登入成功", true);

  await page.goto(`${BASE}/settings`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=收購切結書");
  const versionText = await page.locator(".agreement-meta dd").first().textContent();
  ok("設定頁看得到目前版本與全文", true, versionText?.trim() ?? "");
  await page.screenshot({ path: `${SHOTS}/ag-01-card.png`, fullPage: true });

  // 開啟編輯視窗：初始內容必須是現在這份，不是空白
  await page.click('button:has-text("編輯切結書內容")');
  const dialog = page.getByRole("dialog", { name: "編輯切結書內容" });
  await dialog.waitFor();
  const prefilled = await page.getByLabel("切結書內文").inputValue();
  assert.ok(prefilled.length > 50, `編輯視窗沒帶入現有內容（只有 ${prefilled.length} 字）`);
  ok("編輯視窗帶入現有全文", true, `${prefilled.length} 字`);
  await page.screenshot({ path: `${SHOTS}/ag-02-dialog.png` });

  // 取消不得送出任何請求
  await dialog.locator('button:has-text("取消")').click();
  await dialog.waitFor({ state: "detached" });
  assert.equal(sent.length, 0, "按取消卻送出了請求");
  ok("取消不送出", true);

  // 改內容並儲存
  await page.click('button:has-text("編輯切結書內容")');
  await page.getByLabel("切結書內文").fill(NEW_BODY);
  const editDialog = page.getByRole("dialog", { name: "編輯切結書內容" });
  await editDialog.locator('button:has-text("儲存")').click();
  // 先等視窗關閉再驗卡片：視窗自己也有預覽，只看「頁面上有沒有新內文」會抓到視窗裡那份，
  // 卡片其實還沒重新載入。
  await editDialog.waitFor({ state: "detached", timeout: 10000 });
  await page.waitForFunction(
    (token) => document.querySelector(".agreement-preview")?.textContent?.includes(token) ?? false,
    LONG_TOKEN.slice(0, 20),
    { timeout: 10000 },
  );
  assert.equal(sent.length, 1, `送出次數不對：${sent.length}`);
  assert.equal(sent[0].body, NEW_BODY, "送出的內文與輸入不符");
  ok("儲存送出整份內文", true, `${sent[0].body.length} 字`);

  const newVersion = await page.locator(".agreement-meta dd").first().textContent();
  assert.notEqual(newVersion?.trim(), versionText?.trim(), "版本號沒有遞增");
  ok("版本號遞增（舊版保留）", true, `${versionText?.trim()} → ${newVersion?.trim()}`);
  await page.screenshot({ path: `${SHOTS}/ag-03-saved.png`, fullPage: true });

  // 跑版驗證：預覽區不得超出卡片寬度、不得產生水平捲動
  const overflow = await page.evaluate(() => {
    const preview = document.querySelector(".agreement-preview");
    if (!preview) return { missing: true };
    const card = preview.closest(".card");
    return {
      missing: false,
      previewWidth: preview.getBoundingClientRect().width,
      cardWidth: card.getBoundingClientRect().width,
      horizontalScroll: preview.scrollWidth - preview.clientWidth,
      docScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
  assert.equal(overflow.missing, false, "找不到預覽區");
  assert.ok(overflow.previewWidth <= overflow.cardWidth + 1, "預覽區比卡片還寬（跑版）");
  assert.ok(overflow.horizontalScroll <= 1, `預覽區出現水平捲動 ${overflow.horizontalScroll}px`);
  assert.ok(overflow.docScroll <= 1, `整頁出現水平捲動 ${overflow.docScroll}px`);
  ok("超長無空白字串不撐破版面", true, `預覽 ${Math.round(overflow.previewWidth)}px`);

  // 手持裝置端用的是同一組樣式 class，確認預覽確實掛著它
  const sharesKioskStyle = await page.evaluate(
    () => document.querySelector(".agreement-preview")?.classList.contains("kiosk-agreement-body") ?? false,
  );
  assert.ok(sharesKioskStyle, "預覽沒有沿用手持裝置樣式，看到的排版不等於客人看到的");
  ok("預覽與手持裝置同一組樣式", true);
} catch (err) {
  ok("煙霧流程例外", false, String(err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
