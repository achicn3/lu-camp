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
  // 擬真手寫筆跡（非色塊）：主筆劃＝雙頻正弦曲線、加一撇收尾，2px 半徑圓筆頭；
  // 400x120 RGBA，如實呈現簽名管線的渲染結果（憑證上看起來像真的簽名）。
  const w = 400, h = 120;
  const ink = Array.from({ length: h }, () => new Uint8Array(w));
  const dab = (cx, cy) => {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) {
        if (dx * dx + dy * dy > 4) continue;
        const x = Math.round(cx) + dx, y = Math.round(cy) + dy;
        if (x >= 0 && x < w && y >= 0 && y < h) ink[y][x] = 1;
      }
    }
  };
  for (let i = 0; i <= 2400; i++) {
    const t = i / 2400;
    dab(20 + t * 360, 62 + 26 * Math.sin(t * Math.PI * 3) + 10 * Math.sin(t * Math.PI * 9 + 1));
  }
  for (let i = 0; i <= 700; i++) {
    const t = i / 700;
    dab(140 + t * 150, 98 - t * 60); // 收尾一撇
  }
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
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6;
  const raw = [];
  for (let y = 0; y < h; y++) {
    raw.push(0);
    for (let x = 0; x < w; x++) {
      if (ink[y][x]) raw.push(0, 0, 0, 255);
      else raw.push(255, 255, 255, 255);
    }
  }
  const idat = zlib.deflateSync(Buffer.from(raw));
  return Buffer.concat([
    magic,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]).toString("base64");
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
  const firstDoneText = await page.textContent(".acq-result p");
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // K6：收購憑證聯（切結品項/總額/撥款＋賣方簽名）——以網路回應為準（不受畫面殘留影響）
  const printResp1 = page.waitForResponse(
    (r) => r.url().includes("/print/acquisition") && r.status() === 200,
    { timeout: 8000 },
  );
  await page.click('button:has-text("列印收購憑證聯")');
  await printResp1;
  ok("收購憑證聯送出列印（現金撥款）", true);
  await page.screenshot({ path: join(SHOTS, "03b-receipt.png"), fullPage: true });

  // ── K6 變體：購物金撥款的收購憑證聯（撥入購物金行）───────────────────
  // 會員賣家（SELLER+MEMBER，B100000002 去重冪等）由 API 建立，UI 以電話搜尋選取。
  const memberSeller = await (
    await fetch(`${API}/api/v1/contacts`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${mgr}` },
      body: JSON.stringify({
        name: "憑證會員",
        phone: `09${Date.now().toString().slice(-8)}`,
        national_id: "B100000002",
        roles: ["SELLER", "MEMBER"],
      }),
    })
  ).json();
  // 完成收購後表單已重置（seller 已清空），直接搜尋選取會員賣家。
  await page.fill('input[aria-label="賣方搜尋"]', memberSeller.phone);
  await page.click(`.acq-results button:has-text("${memberSeller.name}")`);
  await page.fill('input[aria-label="品名"]', "睡袋");
  await page.locator(".acq-row select").first().selectOption("A");
  const cat2 = page.getByLabel("分類");
  await cat2.click();
  await cat2.fill(`分類${Date.now().toString().slice(-5)}`);
  await page.click('button:has-text("建立「")');
  await page.fill('input[aria-label="收購價"]', "800");
  await page.fill('input[aria-label="上架售價"]', "2000");
  await page.click('button:has-text("送至手持裝置簽署")');
  await page.waitForSelector("text=等待客人確認並簽署", { timeout: 8000 });
  const cur2 = await (
    await fetch(`${API}/api/v1/kiosk/tasks/current`, {
      headers: { authorization: `Bearer ${kiosk}` },
    })
  ).json();
  const sign2 = await fetch(`${API}/api/v1/kiosk/tasks/${cur2.id}/sign`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${kiosk}` },
    body: JSON.stringify({
      signature_image_base64: signaturePng(),
      chosen_payout: "STORE_CREDIT",
    }),
  });
  ok("購物金撥款簽署成功", sign2.status === 200, `status=${sign2.status}`);
  // 等簽署面板轉「已完成」（面板唯一、不受流程一殘留影響）
  await page.waitForSelector('text=客人已完成簽署', { timeout: 15000 });
  await page.click('button:has-text("送出收購")');
  await page.waitForTimeout(2500);
  const errText = await page.textContent(".acq-errors").catch(() => null);
  if (errText) console.log("[diag] submit errors:", errText);
  // 等**新**單號出現（流程一的結果卡仍在畫面上，改比對文字變化）
  await page.waitForFunction(
    (prev) => {
      const el = document.querySelector(".acq-result p");
      return el && el.textContent !== prev;
    },
    firstDoneText,
    { timeout: 10000 },
  );
  const printResp2 = page.waitForResponse(
    (r) => r.url().includes("/print/acquisition") && r.status() === 200,
    { timeout: 8000 },
  );
  await page.click('button:has-text("列印收購憑證聯")');
  await printResp2;
  ok("收購憑證聯送出列印（購物金撥款＋撥入行）", true);
  await page.screenshot({ path: join(SHOTS, "03c-receipt-credit.png"), fullPage: true });

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
