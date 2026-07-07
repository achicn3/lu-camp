// 手持簽署裝置瀏覽器煙霧（docs/23 K3）：店員端經 API 建收購切結任務 → 手持端（KIOSK 帳號）
// 登入 /kiosk → 看到切結書/品項/撥款 → 勾同意、選現金、手寫簽名 → 送出 → 完成畫面。
// K4（收購頁推任務）尚未建，故任務以 API 種入；K3 只驗手持端的顯示與簽名送出。
// 執行：node scripts/kiosk-smoke.mjs（需 backend:8000 + frontend:3000、dev-manager + dev-kiosk 可登入）。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API_BASE = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "kiosk-smoke");
const MGR_USER = process.env.SMOKE_USERNAME ?? "dev-manager";
const MGR_PASS = process.env.SMOKE_PASSWORD ?? "dev-test-123456";
const KIOSK_USER = process.env.SMOKE_KIOSK_USERNAME ?? "dev-kiosk";
const KIOSK_PASS = process.env.SMOKE_KIOSK_PASSWORD ?? "dev-test-123456";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin(username, password) {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`login ${username} failed: ${res.status}`);
  return (await res.json()).access_token;
}

async function apiJson(token, method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function drawSignature(page) {
  // 於 canvas 上以滑鼠（→ pointer 事件）畫幾筆連續線，產生足量深色像素（後端要求可見墨跡）。
  // 先捲入視野：AFFIDAVIT 的畫布在切結書/撥款之下，於捲動容器內可能位於折線以下。
  const canvas = page.locator("canvas.kiosk-sign-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("找不到簽名畫布");
  const cx = box.x;
  const cy = box.y;
  const pts = [
    [0.15, 0.5],
    [0.3, 0.25],
    [0.45, 0.7],
    [0.6, 0.3],
    [0.75, 0.6],
    [0.85, 0.4],
  ];
  await page.mouse.move(cx + box.width * pts[0][0], cy + box.height * pts[0][1]);
  await page.mouse.down();
  for (const [fx, fy] of pts.slice(1)) {
    await page.mouse.move(cx + box.width * fx, cy + box.height * fy, { steps: 12 });
  }
  await page.mouse.up();
}

const browser = await chromium.launch();
try {
  // ── 前置：店員端經 API 建立收購切結任務 ──────────────────────────────
  const mgrToken = await apiLogin(MGR_USER, MGR_PASS);
  const phone = uniquePhone();
  const nid = validNationalId();
  const created = await apiJson(mgrToken, "POST", "/api/v1/contacts", {
    name: "煙霧簽署客",
    phone,
    national_id: nid,
    roles: ["SELLER"],
  });
  ok("建立 SELLER 聯絡人", created.status === 201, `status=${created.status}`);
  const contactId = created.json?.id;

  const masked = `${nid.slice(0, 3)}****${nid.slice(-3)}`;
  const taskRes = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    content: {
      seller_name: "煙霧簽署客",
      national_id_masked: masked,
      phone,
      items: [
        { name: "登山背包", amount: "1200" },
        { name: "登山杖一組", amount: "600" },
      ],
      total: "1800",
    },
  });
  ok("建立收購切結任務", taskRes.status === 201, `status=${taskRes.status}`);
  const taskId = taskRes.json?.id;

  // ── 手持端：KIOSK 登入 → 顯示任務 → 簽名送出 ─────────────────────────
  const page = await browser.newPage({ viewport: { width: 834, height: 1112 } }); // 直式平板
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);

  // 裝置登入
  await page.fill('input[name="username"]', KIOSK_USER);
  await page.fill('input[name="password"]', KIOSK_PASS);
  await page.click('button:has-text("啟用裝置")');

  // 輪詢後應出現任務標題與切結書
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  ok("手持端顯示切結任務", true);
  const bodyText = await page.textContent(".kiosk-task-body");
  ok("顯示品項與金額", bodyText.includes("登山背包") && bodyText.includes("1,800"));
  ok("顯示切結書全文", bodyText.includes("非贓物") && bodyText.includes("個人資料"));
  await page.screenshot({ path: join(SHOTS, "01-task.png"), fullPage: true });

  // 送出鈕在未同意/未選撥款/未簽名時應 disabled
  const disabledInitially = await page.locator("button.kiosk-submit").isDisabled();
  ok("未完成前送出鈕停用", disabledInitially);

  // 勾同意 + 選現金
  await page.check('.kiosk-agree-check input[type="checkbox"]');
  await page.click('button.kiosk-payout-btn:has-text("現金")');

  // 單擊畫布（無筆劃）不足以構成簽名：送出鈕仍停用（對齊後端非空白門檻）。
  const canvasBox = await page.locator("canvas.kiosk-sign-canvas").boundingBox();
  await page.mouse.click(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
  await page.waitForTimeout(150);
  ok("單擊不算簽名、送出仍停用", await page.locator("button.kiosk-submit").isDisabled());

  // 完整簽名
  await drawSignature(page);
  await page.waitForTimeout(200);
  await page.screenshot({ path: join(SHOTS, "02-signed.png"), fullPage: true });

  const enabledNow = await page.locator("button.kiosk-submit").isEnabled();
  ok("完成三項後送出鈕啟用", enabledNow);

  await page.click("button.kiosk-submit");
  await page.waitForSelector('h1:has-text("已完成簽署")', { timeout: 8000 });
  ok("送出後顯示完成畫面", true);
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // ── 驗證後端狀態：任務 SIGNED、撥款 CASH、有簽名影像 ────────────────
  const check = await apiJson(mgrToken, "GET", `/api/v1/signing/tasks/${taskId}`);
  ok("後端任務為 SIGNED", check.json?.status === "SIGNED", `status=${check.json?.status}`);
  ok("撥款回填為現金", check.json?.chosen_payout === "CASH", `payout=${check.json?.chosen_payout}`);
  ok("已存簽名影像", check.json?.has_signature === true);

  const sig = await fetch(`${API_BASE}/api/v1/signing/tasks/${taskId}/signature`, {
    headers: { authorization: `Bearer ${mgrToken}` },
  });
  ok("簽名 PNG 可取回", sig.ok && sig.headers.get("content-type") === "image/png");

  // ── 交回鎖持久化：完成畫面重整後仍停在交回、不解鎖（Codex K3 第六輪 high）──
  await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "TRANSACTION_ACK",
    contact_id: contactId,
    content: { items: "單一字串品項", note: "非陣列 items 也要顯示" },
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  ok(
    "重整後仍停在交回畫面（持久鎖）",
    (await page.locator('h1:has-text("已完成簽署")').isVisible()) &&
      !(await page.locator('h1:has-text("交易紀錄簽收")').isVisible()),
  );

  // ── 交回鎖：簽署完成後即使店員建了下一張任務，也不得自動帶出（Codex K3 high）──
  await page.waitForTimeout(3000); // 跨過一個輪詢週期（2s）
  const stillHandoff = await page.locator('h1:has-text("已完成簽署")').isVisible();
  const nextLeaked = await page.locator('h1:has-text("交易紀錄簽收")').isVisible();
  ok("交回前不自動帶出下一位任務", stillHandoff && !nextLeaked);

  // 解鎖需現場店務員帳密：錯帳密不得解鎖
  await page.click('button:has-text("店員解鎖，接續下一位")');
  await page.fill('.kiosk-unlock-form input[name="username"]', MGR_USER);
  await page.fill('.kiosk-unlock-form input[name="password"]', "wrong-pass");
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('.kiosk-unlock-form .form-error', { timeout: 6000 });
  ok("錯誤店務帳密不得解鎖", await page.locator('h1:has-text("已完成簽署")').isVisible());

  // 正確店務帳密 → 恢復輪詢，下一張任務才出現
  await page.fill('.kiosk-unlock-form input[name="password"]', MGR_PASS);
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('h1:has-text("交易紀錄簽收")', { timeout: 8000 });
  ok("店務帳密解鎖後帶出下一張任務", true);

  // ── 回歸：content.items 非陣列時仍完整顯示、不靜默丟棄（Codex K3 high）─────
  const ackBody = await page.textContent(".kiosk-task-body");
  ok(
    "非陣列 items 不被丟棄",
    ackBody.includes("單一字串品項") && ackBody.includes("非陣列 items 也要顯示"),
  );

  // ── 回歸：送出遇網路失敗不得卡死（Codex K3 第五輪 medium）──────────────────
  // 攔截並中止簽名 POST，模擬 LAN 失敗；畫面須顯示可重試錯誤、送出鈕恢復可按、不卡死。
  await page.route("**/api/v1/kiosk/tasks/*/sign", (route) => route.abort());
  await drawSignature(page);
  await page.click("button.kiosk-submit");
  await page.waitForSelector(".kiosk-task-footer .form-error", { timeout: 6000 });
  await page.waitForTimeout(300);
  const recoverable =
    (await page.locator('h1:has-text("交易紀錄簽收")').isVisible()) &&
    (await page.locator("button.kiosk-submit").isEnabled());
  ok("送出失敗後可重試、不卡死", recoverable);

  // 網路恢復後以同一冪等鍵重送 → 成功進交回畫面（thrown→retry 收斂；Codex K3 第六輪）
  await page.unroute("**/api/v1/kiosk/tasks/*/sign");
  await page.click("button.kiosk-submit");
  await page.waitForSelector('h1:has-text("已完成簽署")', { timeout: 8000 });
  ok("網路恢復後重送成功進交回", true);
  // 解鎖回到待機（此任務已簽、無其他待簽）
  await page.click('button:has-text("店員解鎖，接續下一位")');
  await page.fill('.kiosk-unlock-form input[name="username"]', MGR_USER);
  await page.fill('.kiosk-unlock-form input[name="password"]', MGR_PASS);
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 });

  // ── 回歸：KIOSK token 導到店務頁 → 不渲染店務殼、導回 /kiosk（Codex K3 medium）──
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  ok("KIOSK token 不得進店務殼", page.url().replace(/\/+$/, "").endsWith("/kiosk"), page.url());

  // ── 回歸：客人裝置上殘留店務 token → /kiosk 不掛載 console、清除並回裝置登入
  //    （Codex K3 high：非 KIOSK token 絕不留在客人裝置）───────────────────────
  await page.evaluate((t) => window.localStorage.setItem("lu-camp.access-token", t), mgrToken);
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  const loginShown = await page.locator('button:has-text("啟用裝置")').isVisible();
  const cleared = await page.evaluate(() => window.localStorage.getItem("lu-camp.access-token"));
  ok("殘留店務 token 被清除且回裝置登入", loginShown && cleared === null);
} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
