// 操作手冊製作共用工具：登入、截圖、標註、測試資料產生。
// 只在本機手冊專用資料庫（lucamp_manual）執行。
import { chmodSync, mkdirSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

export const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
export const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");

// 這些腳本會實際建單、改庫存、動現金、改設定、作廢與退貨——只能對「本機拋棄式環境」執行。
// fail-closed：預設只允許 localhost/127.0.0.1；要打其他主機必須明確帶
// MANUAL_ALLOW_REMOTE=true（代表操作者已確認那不是正式機）。
// 註：無法從外部確認對方連的是哪個資料庫，故仍需依 docs 使用專用庫 lucamp_manual。
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
function assertDisposableTarget() {
  if (process.env.MANUAL_ALLOW_REMOTE === "true") return;
  for (const [label, url] of [["SMOKE_BASE", BASE], ["SMOKE_API_BASE", API]]) {
    const host = new URL(url).hostname;
    if (!LOCAL_HOSTS.has(host)) {
      throw new Error(
        `拒絕執行：${label}=${url} 不是本機位址。手冊腳本會建立/作廢單據、調整庫存與現金、` +
          `變更全店設定，只能對拋棄式測試環境（專用資料庫 lucamp_manual）執行。` +
          `確定該環境可被破壞時，才加上 MANUAL_ALLOW_REMOTE=true 重跑。`,
      );
    }
  }
}
assertDisposableTarget();
export const SHOTS_ROOT = process.env.MANUAL_SHOTS ?? join(homedir(), "tmp", "lu-camp-manual", "shots");
export const MGR = { u: "dev-manager", p: "dev-test-123456" };
export const KIOSK = { u: "dev-kiosk", p: "dev-test-123456" };

/**
 * 登入權杖（storageState）的存放處：獨立於截圖樹之外的 0700 私有目錄。
 *
 * 本專案裁示「登入永不過期」，storageState 內的權杖等同一把長期有效的管理員鑰匙，
 * 因此 ① 不放在會被打包/分享的截圖目錄裡 ② 目錄 0700、檔案 0600 ③ 用完以
 * `node scripts/manual/99-cleanup.mjs` 刪除（手冊產完務必執行）。
 */
export function statePath(name) {
  const dir = join(SHOTS_ROOT, "..", ".manual-state");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  chmodSync(dir, 0o700);
  return join(dir, name);
}

/** 刪除所有已保存的登入權杖檔（收尾用；不存在也不報錯）。 */
export function purgeStateFiles() {
  const removed = [];
  for (const name of ["kiosk-state.json", "staff-state.json"]) {
    const path = statePath(name);
    try {
      rmSync(path, { force: true });
      removed.push(path);
    } catch {
      /* 已不存在或無權限：由呼叫端自行確認 */
    }
  }
  return removed;
}

export function shotsDir(section) {
  const dir = join(SHOTS_ROOT, section);
  mkdirSync(dir, { recursive: true });
  return dir;
}

export async function apiLogin(username = MGR.u, password = MGR.p) {
  const res = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error(`login failed ${res.status}`);
  return (await res.json()).access_token;
}

export async function apiJson(token, method, path, body, extra = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...extra,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

/**
 * 會實際觸發電子發票開立的步驟，必須明確 opt-in。
 *
 * 為什麼不能只看 `einvoice_enabled` 原本是不是關的：**Amego 憑證放在後端 env，不在資料庫裡**。
 * 只要後端設有正式憑證，腳本把開關打開後結帳就會開出**真發票並消耗字軌**——關回開關救不了
 * 已經開出去的稅務憑證。資料庫隔離對此無效（憑證不隨資料庫走）。
 * 因此預設**跳過**這類步驟；確認後端為無憑證或測試憑證時，才用
 * `MANUAL_ALLOW_EINVOICE_ISSUE=true` 重跑。
 *
 * @param {string} what 這個步驟在做什麼（顯示用）
 * @returns {boolean} 是否允許執行
 */
export function allowEInvoiceIssue(what) {
  if (process.env.MANUAL_ALLOW_EINVOICE_ISSUE === "true") return true;
  console.log(
    `⏭  已跳過「${what}」：此步驟會實際送出電子發票開立請求。\n` +
      `   若後端設有正式 Amego 憑證（憑證在 env、不在資料庫，隔離資料庫也擋不住），\n` +
      `   會開出真發票並消耗字軌，且無法回復。\n` +
      `   確認後端無憑證或使用測試憑證後，再加 MANUAL_ALLOW_EINVOICE_ISSUE=true 重跑。`,
  );
  return false;
}

/**
 * 全店設定的快照 → 執行 → 還原（含讀回驗證）。
 *
 * 手冊腳本為了截圖會暫時改全店設定（發票開關、LINE Pay、備份間隔…）。這些是**店家共用**
 * 的值，絕不可假設「原本是關的」而在收尾時無條件關掉，也不可因中途例外就留在被改過的狀態。
 * 因此：先讀原值 → 用 try/finally 保證還原 → 還原後**讀回比對**，不符就大聲報錯並讓行程
 * 以非 0 結束（呼叫端 CI/人工都看得到），不靜默吞掉。
 *
 * @param {string[]} keys 這次會改到的設定欄位名
 * @param {(original: Record<string, unknown>) => Promise<void>} fn 實際操作
 */
export async function withSettings(keys, fn) {
  const token = await apiLogin();
  const current = (await apiJson(token, "GET", "/api/v1/settings")).json;
  if (current === null) {
    throw new Error("讀不到目前設定，中止（不在未知狀態下變更全店設定）");
  }
  const original = Object.fromEntries(keys.map((k) => [k, current[k]]));
  console.log(`• 設定快照：${JSON.stringify(original)}`);
  // Ctrl-C / kill 不會走 finally，但設定此時已被改過。
  // 這裡的還原是**盡力而為**：Playwright 自己也掛 SIGINT 處理並會直接結束行程，非同步的
  // 還原請求常來不及送出（實測會被打斷）。因此除了嘗試還原，更重要的是把「還原用的指令」
  // 印出來，讓操作者一眼看到該怎麼手動收拾——不要讓店家的設定悄悄留在被改過的狀態。
  let restoring = false;
  const onSignal = (signal) => {
    if (restoring) return;
    restoring = true;
    console.error(
      `\n⚠ 收到 ${signal}：全店設定可能仍停在被本腳本改過的狀態。\n` +
        `   應還原為：${JSON.stringify(original)}\n` +
        `   請到「設定」頁人工確認，或執行：\n` +
        `   TOKEN=$(curl -s -X POST ${API}/api/v1/auth/login -H 'content-type: application/json' \\\n` +
        `     -d '{"username":"${MGR.u}","password":"<密碼>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')\n` +
        `   curl -X PATCH ${API}/api/v1/settings -H "authorization: Bearer $TOKEN" \\\n` +
        `     -H 'content-type: application/json' -d '${JSON.stringify(original)}'`,
    );
    void apiLogin()
      .then((t) => apiJson(t, "PATCH", "/api/v1/settings", original))
      .then(({ status }) => {
        if (status === 200) console.error(`✅ 已自動還原：${JSON.stringify(original)}`);
      })
      .finally(() => process.exit(130));
  };
  process.on("SIGINT", onSignal);
  process.on("SIGTERM", onSignal);
  try {
    await fn(original);
  } finally {
    process.off("SIGINT", onSignal);
    process.off("SIGTERM", onSignal);
    const restoreToken = await apiLogin();
    const patched = await apiJson(restoreToken, "PATCH", "/api/v1/settings", original);
    const after = (await apiJson(restoreToken, "GET", "/api/v1/settings")).json;
    const mismatched = keys.filter((k) => after === null || String(after[k]) !== String(original[k]));
    if (patched.status !== 200 || mismatched.length > 0) {
      process.exitCode = 1;
      console.error(
        `❌ 設定還原失敗（HTTP ${patched.status}；不符欄位：${mismatched.join(", ") || "—"}）。` +
          `請立即到「設定」頁人工確認下列欄位：${keys.join(", ")}`,
      );
    } else {
      console.log(`• 已還原設定並讀回確認：${JSON.stringify(original)}`);
    }
  }
}

/** 合法身分證字號產生器（與前端檢核同規則）。seed 決定尾數，避免重複。 */
export function validNationalId(seed = Math.floor(Math.random() * 1e7)) {
  const LETTER = { A: 10 };
  const weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1];
  const body = String(seed % 10_000_000).padStart(7, "0");
  for (let check = 0; check <= 9; check += 1) {
    const digits = `1${body}${check}`;
    const nums = [Math.floor(LETTER.A / 10), LETTER.A % 10, ...digits.split("").map(Number)];
    const total = nums.reduce((acc, n, i) => acc + n * weights[i], 0);
    if (total % 10 === 0) return `A${digits}`;
  }
  throw new Error("no check digit");
}

export function uniquePhone() {
  return `09${String(Date.now()).slice(-8)}`;
}

export async function newBrowser({ width = 1440, height = 900 } = {}) {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    locale: "zh-TW",
    timezoneId: "Asia/Taipei",
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => {
    errors.push(String(e));
    console.log(`⚠ JS 錯誤：${e}`);
  });
  return { browser, context, page, errors };
}

export async function login(page, who = MGR) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', who.u);
  await page.fill('input[name="password"]', who.p);
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.waitForTimeout(400);
}

/** 截圖工具：可截整頁、可視區或單一元素；支援以紅框標註區域。 */
export function makeShot(dir) {
  let n = 0;
  return async function shot(page, slug, opts = {}) {
    const {
      full = false,
      locator = null,
      highlight = [], // CSS selector 陣列，畫紅框
      padding = 12,
      content = false, // 截「頁首＋主要內容」實際高度，去掉頁尾大片空白
      // `content` 的高度上限（CSS px）。**必要的防呆**：清單頁的高度隨資料量長，
      // 種了 12 個月的示範資料之後，交易紀錄整頁高達 5,000 CSS px（15,000 裝置 px），
      // 手冊裡會變成讀者要滑過的表格牆，而且 chromium 解碼 42 MP 以上的 PNG 會直接失敗。
      // 圖是用來「看得懂這一頁長怎樣」，不是用來窮舉每一列。要完整長圖請明確加大這個值。
      contentMaxHeight = 2200,
    } = opts;
    n += 1;
    const name = `${String(n).padStart(2, "0")}-${slug}.png`;
    const path = join(dir, name);
    // 去除游標與焦點外框造成的視覺雜訊
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    });
    let added = false;
    if (highlight.length > 0) {
      await page.addStyleTag({
        content: `.__manual_hl { outline: 3px solid #e11d48 !important; outline-offset: 2px; border-radius: 6px; }`,
      });
      for (const sel of highlight) {
        await page
          .locator(sel)
          .first()
          .evaluate((el) => el.classList.add("__manual_hl"))
          .catch(() => {});
      }
      added = true;
    }
    // **關掉所有動畫與轉場再拍**：避免對話框淡入到一半就被拍下來。
    // 只影響截圖當下的呈現，不改任何行為。
    //
    // 訂正：這段最初是為了修「粗體中文看起來有殘影」而加的，**那個診斷是錯的**——
    // 真正的原因是本機只裝了 WenQuanYi Zen Hei（單一字重、沒有粗體），
    // 瀏覽器只好用「重複描邊偏移幾 px」合成粗體。同一行的拉丁字乾淨銳利，
    // 因為它 fallback 到有真粗體的字型。要根治得裝有多字重的中文字型（如 Noto Sans CJK）
    // 並重拍全部截圖。停用動畫本身仍是對的做法，只是解決的不是那個問題。
    //
    // 加 id 判重：每拍一張就注入一次的話，一支 28 張的腳本會在同一頁塞 28 個
    // 相同的 <style>。換頁後標籤會消失，所以仍要每次檢查、必要時補上。
    await page.evaluate(() => {
      if (document.getElementById("__manual_no_anim") !== null) return;
      const style = document.createElement("style");
      style.id = "__manual_no_anim";
      style.textContent = `*, *::before, *::after {
        animation-duration: 0s !important; animation-delay: 0s !important;
        transition-duration: 0s !important; transition-delay: 0s !important;
      }`;
      document.head.appendChild(style);
    });
    await page.waitForTimeout(250);
    if (locator) {
      const el = typeof locator === "string" ? page.locator(locator).first() : locator;
      await el.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(200);
      const box = await el.boundingBox();
      if (box) {
        const vp = page.viewportSize();
        await page.screenshot({
          path,
          clip: {
            x: Math.max(0, box.x - padding),
            y: Math.max(0, box.y - padding),
            width: Math.min(box.width + padding * 2, vp.width - Math.max(0, box.x - padding)),
            height: Math.min(box.height + padding * 2, vp.height - Math.max(0, box.y - padding)),
          },
        });
      } else {
        await page.screenshot({ path });
      }
    } else if (content) {
      const box = await page.evaluate(() => {
        const main = document.querySelector(".app-main") ?? document.body;
        const r = main.getBoundingClientRect();
        return {
          bottom: r.bottom + window.scrollY,
          width: document.documentElement.clientWidth,
        };
      });
      const wanted = Math.ceil(box.bottom) + 24;
      const height = Math.min(wanted, contentMaxHeight);
      if (height < wanted) {
        console.log(`   ✂ ${slug}：內容高 ${wanted}px，裁到 ${height}px（資料量造成的長清單）`);
      }
      await page.screenshot({ path, fullPage: true, clip: { x: 0, y: 0, width: box.width, height } });
    } else {
      await page.screenshot({ path, fullPage: full });
    }
    if (added) {
      await page.evaluate(() => {
        document.querySelectorAll(".__manual_hl").forEach((el) => el.classList.remove("__manual_hl"));
      });
    }
    console.log(`   📸 ${name}`);
    return name;
  };
}

export function note(msg) {
  console.log(`• ${msg}`);
}
