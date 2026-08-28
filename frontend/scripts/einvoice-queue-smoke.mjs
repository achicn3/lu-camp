// 發票待處理頁煙霧：作廢/折讓送不出去時，這一頁是唯一看得到、能處理的地方。
//
// **自己建立前置資料**（開一張真發票再作廢，留下一筆待送出的作廢），而不是「找不到就
// 當通過」——那樣頁面壞掉、篩選錯誤或資料沒建成，煙霧照樣全綠（Codex 第三輪指出）。
// 執行：node scripts/einvoice-queue-smoke.mjs（backend:8000 + frontend:3000 已起，docs/20）
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = strip(process.env.SMOKE_BASE ?? "http://localhost:3000");
const API_BASE = strip(process.env.SMOKE_API_BASE ?? "http://localhost:8000");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "einvoice-queue");
const DB = process.env.SMOKE_DB ?? "lucamp_manual";
const USERNAME = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}
function strip(v) {
  return v.replace(/\/+$/, "");
}
function psql(sql) {
  return execFileSync("docker", ["exec", "lu-camp-db-1", "psql", "-U", "lucamp", "-d", DB, "-tAc", sql], {
    encoding: "utf8",
  }).trim();
}
async function api(path, { method = "GET", token, body, headers = {}, expect = [200] } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!expect.includes(res.status)) {
    throw new Error(`${method} ${path} → ${res.status}: ${JSON.stringify(data)?.slice(0, 300)}`);
  }
  return data;
}

let browser;
let token;
let restoreEInvoice = false;
let invoiceNo = null;
try {
  const login = await api("/api/v1/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  token = login.access_token;

  const settings = await api("/api/v1/settings", { token });
  if (!settings.einvoice_enabled) {
    await api("/api/v1/settings", { method: "PATCH", token, body: { einvoice_enabled: true } });
    restoreEInvoice = true;
  }
  if ((await api("/api/v1/cash-sessions/current", { token })) === null) {
    await api("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  }

  const runId = Date.now();
  const seller = await api("/api/v1/contacts", {
    method: "POST",
    token,
    expect: [201],
    body: {
      name: `SMOKE_EQ_${runId}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
      source_note: "einvoice-queue smoke",
    },
  });
  const acq = await api("/api/v1/acquisitions", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_EQ_ACQ_${runId}` },
    body: {
      type: "BUYOUT",
      contact_id: seller.id,
      payout_method: "CASH",
      note: "einvoice-queue smoke",
      items: [
        { name: `SMOKE_EQ_ITEM_${runId}`, grade: "A", listed_price: "300", acquisition_cost: "120" },
      ],
    },
  });
  const sale = await api("/api/v1/sales", {
    method: "POST",
    token,
    expect: [201],
    headers: { "Idempotency-Key": `SMOKE_EQ_SALE_${runId}` },
    body: {
      lines: [{ line_type: "SERIALIZED", item_code: acq.item_codes[0], qty: 1 }],
      tenders: [{ tender_type: "CASH", amount: "300" }],
      expected_einvoice_enabled: true,
    },
  });
  const invoice = await api(`/api/v1/einvoice/sales/${sale.id}/issue`, {
    method: "POST",
    token,
    expect: [200, 201],
  });
  invoiceNo = invoice.invoice_no;
  await api(`/api/v1/sales/${sale.id}/void`, { method: "POST", token, expect: [200] });

  // **把這一列移出背景自動送出的射程**（把建立時間往回推超過年齡上限）。
  // 不這麼做的話會跟背景排程賽跑：背景先送 → 按鈕路徑被略過（掩蓋按鈕/API 壞掉）；
  // 或畫面讀到 PENDING 之後背景才送 → 人工按下去吃 409、變成偶發失敗（Codex 第二輪）。
  const voidQueueId = psql(
    `SELECT q.id FROM einvoice_upload_queue q JOIN invoices i ON i.id=q.invoice_id
     WHERE i.invoice_no='${invoiceNo}' AND q.action='VOID' ORDER BY q.id DESC LIMIT 1`,
  );
  if (!voidQueueId) throw new Error("作廢後沒有產生 F0501 待送出項目（前置資料不成立）");
  psql(
    `UPDATE einvoice_upload_queue SET created_at = now() - interval '30 days',
     updated_at = now() - interval '30 days' WHERE id = ${Number(voidQueueId)}`,
  );
  ok("前置：開立並作廢一筆，產生待送出的作廢", Boolean(invoiceNo), `${invoiceNo} / #${voidQueueId}`);

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(USERNAME);
  await page.getByLabel("密碼").fill(PASSWORD);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  ok("登入成功", true);

  // 從導覽進入（不是直接打網址）——要驗的正是「店員找得到這一頁」。
  await page.getByRole("button", { name: "開啟系統選單" }).click();
  await page.getByRole("link", { name: /發票待處理/ }).click();
  await page.waitForURL(/\/einvoice-queue/, { timeout: 20000 });
  await page.getByRole("heading", { name: "發票待處理" }).waitFor({ timeout: 20000 });
  ok("可從系統選單進入發票待處理", true);

  await page.getByRole("button", { name: "全部" }).click();
  await page.getByText(/共 \d+ 筆/).waitFor({ timeout: 20000 });
  ok("清單讀取成功", true);
  await page.screenshot({ path: join(SHOTS, "01-list.png") });

  // 定位**那張發票的「作廢」列**。只用發票號碼會同時命中同一張發票的「開立」列
  // （已送出），於是就算 F0501 根本沒建立，測試也會誤判通過（Codex 第二輪）。
  const row = page.locator("tbody tr").filter({ hasText: invoiceNo }).filter({ hasText: "作廢" });
  const found = (await row.count()) > 0;
  ok(`清單有列出 ${invoiceNo} 的「作廢」列`, found);
  await page.screenshot({ path: join(SHOTS, "02-row.png") });

  if (found) {
    // 前置已把它移出背景射程，所以這裡**一定**看得到待送出與送出鈕；
    // 看不到就是頁面或狀態顯示壞了，直接紅，不做「可能是背景先送掉」的推測。
    const rowText = (await row.first().textContent()) ?? "";
    ok("該列狀態為待送出（已排除背景賽跑）", rowText.includes("待送出"), rowText.trim().slice(0, 60));

    const sendButton = row.first().getByRole("button", { name: /立即送出第 \d+ 筆/ });
    ok("待送出的作廢列有「立即送出」可按", (await sendButton.count()) > 0);
    await sendButton.click();
    await page.getByRole("status").waitFor({ timeout: 30000 });
    const msg = (await page.getByRole("status").textContent()) ?? "";
    ok("按下立即送出後真的送交平台", msg.includes("已送交平台"), msg.trim().slice(0, 80));
    await page.screenshot({ path: join(SHOTS, "03-sent.png") });
  }
} catch (err) {
  ok(`未預期錯誤：${err.message}`, false);
} finally {
  await browser?.close();
  if (restoreEInvoice && token) {
    try {
      await api("/api/v1/settings", { method: "PATCH", token, body: { einvoice_enabled: false } });
    } catch (err) {
      console.error("還原 einvoice_enabled 失敗：", err.message);
    }
  }
}

const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} 通過`);
process.exit(passed === results.length ? 0 : 1);
