// K5 購物金扣抵×手持簽署整合煙霧（docs/23 D3）：會員以購物金結帳 → POS 推「扣抵確認」→
// 客人（KIOSK）核對本次折抵/剩餘後簽名（API）→ POS 結帳綁定 → 驗證單次使用 409；
// 另驗 K5b：交易紀錄頁「推送簽收」→ 手持端收到 TRANSACTION_ACK → 簽名留存。
// 需 backend:8000 + frontend:3000、dev-manager + dev-kiosk 已 seed。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import zlib from "node:zlib";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "codex-test", "kiosk-sc-smoke");
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
  return Buffer.concat([
    magic,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]).toString("base64");
}

async function apiJson(method, path, token, body, extraHeaders = {}) {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...extraHeaders,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await r.json();
  } catch {
    // 空回應
  }
  return { status: r.status, data };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (e) => ok("頁面 JS 錯誤", false, String(e)));

try {
  const mgr = await apiLogin("dev-manager", "dev-test-123456");
  const kiosk = await apiLogin("dev-kiosk", "dev-test-123456");
  await apiJson("POST", "/api/v1/cash-sessions/open", mgr, { opening_float: "1000" });

  // 種子（API）：會員 + 以「收購撥購物金」入帳（500×溢價），並得到兩件可售品（各 500）。
  // national_id 用 K5 專屬值（B100000002）：A123456789 已被其他煙霧用掉，blind-index 去重
  // 會回傳既有 SELLER-only 聯絡人（非 MEMBER → 無法持購物金）。重跑時去重回同一會員（冪等）。
  const phone = `09${Date.now().toString().slice(-8)}`;
  const contact = await apiJson("POST", "/api/v1/contacts", mgr, {
    name: "扣抵會員",
    phone,
    national_id: "B100000002",
    roles: ["SELLER", "MEMBER"],
  });
  ok(
    "建立/取回會員",
    contact.status === 201 && contact.data.roles.includes("MEMBER"),
    `status=${contact.status} roles=${contact.data?.roles}`,
  );
  const memberId = contact.data.id;

  const mkAcq = (n) =>
    apiJson(
      "POST",
      "/api/v1/acquisitions",
      mgr,
      {
        type: "BUYOUT",
        contact_id: memberId,
        items: [{ name: `露營燈${n}`, grade: "A", listed_price: "500", acquisition_cost: "500" }],
        payout_method: "STORE_CREDIT",
      },
      { "Idempotency-Key": `k5-acq-${n}-${Date.now()}` },
    );
  const acq1 = await mkAcq(1);
  const acq2 = await mkAcq(2);
  ok("收購入帳購物金", acq1.status === 201 && acq2.status === 201, `${acq1.status}/${acq2.status}`);
  const item1 = acq1.data.item_codes[0];
  const item2 = acq2.data.item_codes[0];

  // 店員：登入 → POS → 掃碼 → 歸戶 → 購物金 → 推送手持簽署
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(400);
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/pos`, { waitUntil: "networkidle" });

  await page.fill(".pos-scan-input", item1);
  await page.press(".pos-scan-input", "Enter");
  await page.waitForSelector("text=露營燈1");
  ok("掃碼加入購物車", true);

  // 以**回傳的**電話查（blind-index 去重時會回既有會員、其電話為首次建立值）。
  await page.fill('.pos-member-search input', contact.data.phone);
  await page.click('button:has-text("查詢會員")');
  await page.click(`.pos-member-results button:has-text("扣抵會員")`);
  await page.waitForSelector("text=購物金餘額");
  ok("會員歸戶（顯示餘額）", true);

  await page.click('label.pos-tender-mode:has-text("購物金")');
  await page.waitForSelector('button:has-text("送至手持裝置簽署")');
  await page.click('button:has-text("送至手持裝置簽署")');
  await page.waitForSelector("text=等待客人確認並簽署", { timeout: 8000 });
  ok("推送扣抵確認至手持", true);
  // 結帳應被擋（等待簽署）
  const checkoutBtn = page.locator("button.pos-checkout");
  ok("結帳於簽署前停用", await checkoutBtn.isDisabled());
  await page.screenshot({ path: join(SHOTS, "01-pushed.png"), fullPage: true });

  // 客人（KIOSK）：任務內容應含本次折抵/剩餘 → 簽名
  const cur = (await apiJson("GET", "/api/v1/kiosk/tasks/current", kiosk)).data;
  ok("手持端收到扣抵任務", cur?.kind === "STORE_CREDIT_USE", `kind=${cur?.kind}`);
  // 餘額為相對驗證（重跑會累積）：balance_after = balance_before − debit。
  const before = Number(cur?.content?.balance_before);
  const after = Number(cur?.content?.balance_after);
  ok(
    "任務含折抵/剩餘快照",
    cur?.content?.debit === "500" && Number.isFinite(before) && after === before - 500,
    `debit=${cur?.content?.debit} before=${before} after=${after}`,
  );
  const sign = await apiJson("POST", `/api/v1/kiosk/tasks/${cur.id}/sign`, kiosk, {
    signature_image_base64: signaturePng(),
  });
  ok("手持端簽署成功", sign.status === 200, `status=${sign.status}`);

  // POS：顯示已簽 → 結帳
  await page.waitForSelector("text=客人已完成簽署", { timeout: 10000 });
  ok("POS 顯示客人已簽署", true);
  await page.screenshot({ path: join(SHOTS, "02-signed.png"), fullPage: true });
  await page.click("button.pos-checkout");
  await page.waitForSelector("text=已完成", { timeout: 10000 });
  ok("購物金結帳完成", true);
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // 單次使用：同一簽署綁第二筆結帳（API、同額別件）→ 409
  const dup = await apiJson(
    "POST",
    "/api/v1/sales",
    mgr,
    {
      lines: [{ line_type: "SERIALIZED", item_code: item2 }],
      buyer_contact_id: memberId,
      tenders: [{ tender_type: "STORE_CREDIT", amount: "500" }],
      signature_task_id: cur.id,
    },
    { "Idempotency-Key": `k5-dup-${Date.now()}` },
  );
  ok("扣抵簽署單次使用（重複綁定→409）", dup.status === 409, `status=${dup.status}`);

  // K5b：交易紀錄頁推「交易紀錄簽收」→ 手持端 TRANSACTION_ACK → 簽名留存
  await page.goto(`${BASE}/sales`, { waitUntil: "networkidle" });
  await page.click('button[aria-label^="推送銷售"]');
  await page.waitForSelector("text=交易紀錄簽收至手持裝置", { timeout: 8000 });
  ok("交易紀錄推送簽收", true);
  await page.screenshot({ path: join(SHOTS, "04-ack-pushed.png"), fullPage: true });
  const ack = (await apiJson("GET", "/api/v1/kiosk/tasks/current", kiosk)).data;
  ok("手持端收到簽收任務", ack?.kind === "TRANSACTION_ACK", `kind=${ack?.kind}`);
  const ackSign = await apiJson("POST", `/api/v1/kiosk/tasks/${ack.id}/sign`, kiosk, {
    signature_image_base64: signaturePng(),
  });
  ok("簽收簽名成功", ackSign.status === 200, `status=${ackSign.status}`);
} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
