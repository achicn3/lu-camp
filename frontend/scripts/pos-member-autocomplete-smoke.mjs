// POS 會員即時查找煙霧：打字就出結果，不必按按鈕（與收購頁的賣方查找一致）。
//
// 為什麼要瀏覽器測：即時查找的價值全在「店員不必多按一下」。單元測證明得了查詢有觸發，
// 證明不了那個清單真的出現在結帳畫面上、也證明不了舊的「查詢會員」按鈕沒有殘留。
//
// 執行（backend:8000 + frontend:3000 已起、庫裡有會員）：
//   node frontend/scripts/pos-member-autocomplete-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "pos-member");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, p, d = "") => { results.push({ n, p }); console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`); };

// 從 API 取一位真實會員來查——寫死名字的話，換一個資料庫這支就沒意義了。
const token = await (await fetch(`${API}/api/v1/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
})).json().then((j) => j.access_token);
const members = await (await fetch(`${API}/api/v1/contacts?role=MEMBER&limit=5`, {
  headers: { Authorization: `Bearer ${token}` },
})).json();
const member = members.find((m) => m.phone);
if (!member) {
  console.error("庫裡沒有帶電話的會員，無法驗證；請先 seed。");
  process.exit(2);
}

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });

  await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: /POS/ }).first().waitFor({ timeout: 15000 });

  ok("舊的「查詢會員」按鈕已移除", (await page.getByRole("button", { name: "查詢會員" }).count()) === 0);

  const box = page.getByPlaceholder("姓名或電話");
  await box.fill(member.phone);

  let appeared = true;
  try {
    await page.getByRole("button", { name: new RegExp(member.name) }).first()
      .waitFor({ state: "visible", timeout: 8000 });
  } catch {
    appeared = false;
  }
  ok(`只打字就出現「${member.name}」`, appeared, member.phone);
  await page.screenshot({ path: join(SHOTS, "01-autocomplete.png"), fullPage: false });

  await box.fill("這個名字不存在的會員");
  ok("查無會員時給明確提示", await page.getByText("查無符合的會員").first()
    .waitFor({ state: "visible", timeout: 8000 }).then(() => true).catch(() => false));

  await box.fill(member.phone);
  await page.getByRole("button", { name: new RegExp(member.name) }).first()
    .waitFor({ state: "visible", timeout: 8000 }).catch(() => {});

  if (appeared) {
    await page.getByRole("button", { name: new RegExp(member.name) }).first().click();
    await page.waitForTimeout(1200);
    const text = await page.locator(".pos-member-selected").innerText().catch(() => "");
    ok("點選後完成歸戶並顯示購物金餘額", /購物金餘額/.test(text), text.replace(/\n/g, " "));
    await page.screenshot({ path: join(SHOTS, "02-selected.png"), fullPage: false });
  }

  await context.close();
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過；截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
