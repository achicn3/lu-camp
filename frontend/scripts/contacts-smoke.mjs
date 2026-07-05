// 會員建檔瀏覽器煙霧：姓名/電話必填 + 身分證字號檢核（前端防呆）＋後端 422 一致。
// 執行：node scripts/contacts-smoke.mjs（需 backend:8000 + frontend:3000 已起、dev-manager 可登入）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API_BASE = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "contacts-smoke");
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', USERNAME);
  await page.fill('input[name="password"]', PASSWORD);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await login(page);
  await page.goto(`${BASE}/contacts`);
  await page.waitForSelector('button:has-text("建檔")');

  const run = Date.now().toString().slice(-8);
  const name = `煙霧會員 ${run.slice(-6)}`;
  const phone = `09${run}`; // 每次唯一（同店手機唯一約束）
  await page.getByLabel("姓名 *").fill(name);
  await page.getByLabel("電話 *").fill(phone);
  await page.getByLabel("住址（切結書顯示用）").fill("台中市西屯區煙霧路 1 號");

  // 1) 不合法身分證字號 → 前端擋下、不建檔
  await page.getByLabel("身分證字號（收購/寄售必填）").fill("A123456788"); // 末碼錯
  await page.click('button:has-text("建檔")');
  const err = page.locator('[role="alert"].form-error', { hasText: /身分證字號格式或檢核碼不正確/ });
  await err.waitFor({ state: "visible", timeout: 8000 });
  ok("不合法身分證字號 → 前端防呆擋下", true, (await err.textContent()) ?? "");
  await page.screenshot({ path: `${SHOTS}/01-invalid-national-id.png` });

  // 2) 改為合法身分證字號 → 建檔成功（表單清空、清單可見）
  await page.getByLabel("身分證字號（收購/寄售必填）").fill(validNationalId());
  await page.click('button:has-text("建檔")');
  await page.waitForFunction(
    () => !document.querySelector('[role="alert"].form-error'),
    undefined,
    { timeout: 8000 },
  );
  ok("合法身分證字號 → 建檔成功", true);
  await page.screenshot({ path: `${SHOTS}/02-created.png` });

  // 3) K1 住址：以 API 找回 id → 會員中心顯示住址 → 編輯分頁改住址 → 重載驗證
  const loginRes = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  });
  const token = (await loginRes.json()).access_token;
  const found = await fetch(
    `${API_BASE}/api/v1/contacts?q=${encodeURIComponent(phone)}&limit=5`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const rows = await found.json();
  const cid = rows[0]?.id;
  ok("API 取回新會員（住址已存）", rows[0]?.address === "台中市西屯區煙霧路 1 號", `id=${cid}`);

  await page.goto(`${BASE}/contacts/${cid}`);
  await page.waitForSelector("text=住址：台中市西屯區煙霧路 1 號");
  ok("會員中心頁首顯示住址", true);
  await page.screenshot({ path: `${SHOTS}/03-address-shown.png` });

  await page.click('button:has-text("編輯")');
  const addrInput = page.getByLabel("住址（切結書顯示用）");
  await addrInput.waitFor({ state: "visible" });
  await addrInput.fill("台中市北區改址路 2 號");
  await page.click('button:has-text("儲存")');
  await page.waitForSelector("text=已儲存", { timeout: 8000 }).catch(() => {});
  await page.goto(`${BASE}/contacts/${cid}`);
  await page.waitForSelector("text=住址：台中市北區改址路 2 號");
  ok("編輯住址後重載顯示新值", true);
  await page.screenshot({ path: `${SHOTS}/04-address-edited.png` });
} finally {
  await browser.close();
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n結果：${passed}/${results.length} 通過\n截圖：${SHOTS}`);
if (passed !== results.length) process.exit(1);
