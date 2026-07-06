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

  // 勾同意 + 選現金 + 簽名
  await page.check('.kiosk-agree-check input[type="checkbox"]');
  await page.click('button.kiosk-payout-btn:has-text("現金")');
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
} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
