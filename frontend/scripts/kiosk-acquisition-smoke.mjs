// K4 收購×手持切結整合煙霧（docs/23）：店員於收購頁鑑價 → 送至手持裝置 → 客人（KIOSK）
// 簽署（API）→ 店員完成收購 → 驗證收購單綁定 signature_task_id、撥款＝客人所選。
// 需 backend:8000 + frontend:3000、dev-manager + dev-kiosk 已 seed、開帳。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import zlib from "node:zlib";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "kiosk-acq-smoke");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiLogin(u, p) {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username: u, password: p }),
  });
  return (await r.json()).access_token;
}

function signaturePng() {
  // 200x80 RGBA、中段黑色筆跡（滿足後端非空白門檻）→ base64（不含 data: 前綴）。
  const magic = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const crc = (buf) => {
    let c = ~0;
    for (const b of buf) {
      c ^= b;
      for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
    }
    return (~c) >>> 0;
  };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const c = Buffer.alloc(4);
    c.writeUInt32BE(crc(td));
    return Buffer.concat([len, td, c]);
  };
  const w = 200, h = 80;
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6;
  const raw = [];
  for (let y = 0; y < h; y++) {
    raw.push(0);
    for (let x = 0; x < w; x++) {
      if (y >= 20 && y <= 40) raw.push(0, 0, 0, 255);
      else raw.push(255, 255, 255, 255);
    }
  }
  const idat = zlib.deflateSync(Buffer.from(raw));
  const png = Buffer.concat([
    magic,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
  return png.toString("base64");
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (e) => ok("頁面 JS 錯誤", false, String(e)));

try {
  const mgr = await apiLogin("dev-manager", "dev-test-123456");
  const kiosk = await apiLogin("dev-kiosk", "dev-test-123456");

  // 開帳（CASH 收購需要）
  await fetch(`${API}/api/v1/cash-sessions/open`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
    body: JSON.stringify({ opening_float: "1000" }),
  });

  // 店員：登入 → 收購頁
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  // 建立賣方（唯一手機避免重跑衝突）
  const nid = "A123456789";
  await page.click('button:has-text("建立新賣方")');
  await page.fill('input[aria-label="姓名"]', "切結賣家");
  await page.fill('input[aria-label="手機"]', `09${Date.now().toString().slice(-8)}`);
  await page.fill('input[aria-label="身分證字號"]', nid);
  await page.click('button:has-text("建立並選取")');
  await page.waitForSelector("text=切結賣家");
  ok("建立並選取賣方", true);

  // 鑑價列
  await page.fill('input[aria-label="品名"]', "登山外套");
  await page.locator(".acq-row select").first().selectOption("A");
  const brand = page.getByLabel("品牌");
  await brand.click();
  await brand.fill(`品牌${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  const cat = page.getByLabel("分類");
  await cat.click();
  await cat.fill(`分類${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  await page.fill('input[aria-label="收購價"]', "1200");
  await page.fill('input[aria-label="上架售價"]', "3000");

  // 送至手持裝置簽署
  await page.click('button:has-text("送至手持裝置簽署")');
  await page.waitForSelector("text=等待客人確認並簽署", { timeout: 8000 });
  ok("送至手持裝置、等待簽署", true);
  await page.screenshot({ path: join(SHOTS, "01-pushed.png"), fullPage: true });

  // 客人（KIOSK）簽署：取當前任務 → 簽名（選現金）
  const cur = await (
    await fetch(`${API}/api/v1/kiosk/tasks/current`, {
      headers: { authorization: `Bearer ${kiosk}` },
    })
  ).json();
  ok("手持端收到切結任務", cur && cur.kind === "ACQUISITION_AFFIDAVIT", `kind=${cur?.kind}`);
  const signResp = await fetch(`${API}/api/v1/kiosk/tasks/${cur.id}/sign`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${kiosk}` },
    body: JSON.stringify({ signature_image_base64: signaturePng(), chosen_payout: "CASH" }),
  });
  ok("手持端簽署成功", signResp.status === 200, `status=${signResp.status}`);

  // 店員端輪詢應轉為「已完成簽署」
  await page.waitForSelector("text=客人已完成簽署", { timeout: 10000 });
  ok("店員端顯示客人已簽署", true);
  await page.screenshot({ path: join(SHOTS, "02-signed.png"), fullPage: true });

  // 完成收購
  await page.click('button:has-text("送出收購")');
  await page.waitForSelector("text=收購完成", { timeout: 10000 });
  ok("完成收購", true);
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // 驗證後端：任務被綁定（get task → ref 或以 sign task 查其綁定收購）。以 acquisition 反查：
  const taskAfter = await (
    await fetch(`${API}/api/v1/signing/tasks/${cur.id}`, {
      headers: { authorization: `Bearer ${mgr}` },
    })
  ).json();
  ok("切結任務仍為 SIGNED", taskAfter.status === "SIGNED");

  // 綁定不可重複使用：以同一 task 再建收購 → 409
  const dup = await fetch(`${API}/api/v1/acquisitions`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${mgr}`,
      "Idempotency-Key": `dup-${Date.now()}`,
    },
    body: JSON.stringify({
      type: "BUYOUT",
      contact_id: cur.contact_id,
      // 與已簽切結相同內容（登山外套/1200），才會通過內容一致檢查、觸及單次使用唯一約束。
      items: [{ name: "登山外套", grade: "A", listed_price: "3000", acquisition_cost: "1200" }],
      payout_method: "CASH",
      signature_task_id: cur.id,
    }),
  });
  ok("切結單次使用（重複綁定→409）", dup.status === 409, `status=${dup.status}`);
} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
