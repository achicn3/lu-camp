// 收購「購物金改成付現」（店主 2026-10-10）瀏覽器 E2E：
// 會員收購帳篷，撥購物金（$1,000＋溢價）→ 收購紀錄只有管理者看到「改成付現」→ 確認視窗講付現 $1,000、不用重簽
// → 送出後提示「請從抽屜拿現金 $1,000…已扣回購物金」→ 清單撥款改成「現金 1,000」、按鈕消失；
// 後端：購物金餘額回 0、抽屜多一筆收購付現 $1,000、商品還在。
// 需 backend + frontend 已起、已 seed（dev-manager）。會建立收購，請指向隔離測試庫：
//   SMOKE_ALLOW_WRITE=1 node scripts/convert-to-cash-smoke.mjs
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "convert-to-cash");
assert.equal(process.env.SMOKE_ALLOW_WRITE, "1", "會建立收購，請指向隔離測試庫");
mkdirSync(SHOTS, { recursive: true });

const results = [];
function ok(name, pass, detail = "") {
  results.push({ name, pass });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

let token = "";
async function api(method, path, body, headers = {}) {
  const resp = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  return { status: resp.status, body: text ? JSON.parse(text) : null };
}

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
page.on("pageerror", (err) => ok("頁面沒有 JS 錯誤", false, String(err)));
let originalRequire = null;

try {
  token = (
    await (
      await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "dev-manager", password: "dev-test-123456" }),
      })
    ).json()
  ).access_token;
  originalRequire = (await api("GET", "/api/v1/settings")).body.require_acquisition_affidavit;
  await api("PATCH", "/api/v1/settings", { require_acquisition_affidavit: false });
  if ((await api("GET", "/api/v1/cash-sessions/current")).body === null) {
    await api("POST", "/api/v1/cash-sessions/open", { opening_float: "5000" });
  }
  const run = randomUUID().slice(0, 6);
  const member = await api("POST", "/api/v1/contacts", {
    name: `改付現會員-${run}`,
    phone: uniquePhone(),
    national_id: validNationalId(),
    roles: ["SELLER", "MEMBER"],
  });
  const acq = await api(
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: member.body.id,
      items: [{ name: `帳篷-${run}`, grade: "A", acquisition_cost: "1000", listed_price: "1800" }],
      payout_method: "STORE_CREDIT",
    },
    { "Idempotency-Key": `convert-${run}` },
  );
  ok("API：收購撥購物金", acq.status === 201, `HTTP ${acq.status} ${JSON.stringify(acq.body?.detail ?? "")}`);
  const acqId = acq.body.acquisition_id;
  const credited = Number((await api("GET", `/api/v1/contacts/${member.body.id}/store-credit`)).body.balance);
  ok("客人拿到購物金（含溢價）", credited > 1000, String(credited));

  await skipOpeningCheckRedirect(page);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/acquisition/records`, { waitUntil: "networkidle" });

  const row = page.locator("tr", { hasText: `#${acqId}` });
  await row.waitFor();
  ok("清單撥款顯示購物金", (await row.textContent()).includes("購物金 1,000"));
  await row.getByRole("button", { name: "改成付現" }).click();
  const dialog = page.getByRole("dialog", { name: "改成付現" });
  await dialog.waitFor();
  const dialogText = (await dialog.textContent()) ?? "";
  ok("確認視窗講付現 $1,000、不用重簽", dialogText.includes("$1,000") && dialogText.includes("不用重新簽名"));
  await page.screenshot({ path: join(SHOTS, "01-confirm.png"), fullPage: true });
  await dialog.getByRole("button", { name: "確定改成付現" }).click();

  const notice = page.getByRole("status").filter({ hasText: "已改成付現" });
  await notice.waitFor();
  const noticeText = (await notice.textContent()) ?? "";
  ok(
    "提示付現金與扣回購物金",
    noticeText.includes("請從抽屜拿現金 $1,000") && noticeText.includes(`扣回 $${credited.toLocaleString("en-US")}`),
    noticeText,
  );
  await page.locator("tr", { hasText: `#${acqId}` }).filter({ hasText: "現金 1,000" }).waitFor();
  ok(
    "清單撥款改成現金、改成付現鈕消失",
    (await page.locator("tr", { hasText: `#${acqId}` }).getByRole("button", { name: "改成付現" }).count()) === 0,
  );
  await page.screenshot({ path: join(SHOTS, "02-converted.png"), fullPage: true });

  const balance = Number((await api("GET", `/api/v1/contacts/${member.body.id}/store-credit`)).body.balance);
  ok("後端：客人購物金扣回到 0", balance === 0, String(balance));
  const detail = await api("GET", `/api/v1/acquisitions/${acqId}`);
  ok(
    "後端：收購單撥款改成現金 1000",
    detail.body.payout_method === "CASH" && detail.body.total_cash_paid === "1000",
    JSON.stringify({ m: detail.body.payout_method, c: detail.body.total_cash_paid }),
  );
  const again = await api("POST", `/api/v1/acquisitions/${acqId}/convert-payout-to-cash`);
  ok("再改一次會被擋（不會付兩次）", again.status === 422, `HTTP ${again.status}`);
} catch (err) {
  ok("流程執行", false, String(err));
  await page.screenshot({ path: join(SHOTS, "99-error.png"), fullPage: true }).catch(() => {});
} finally {
  if (originalRequire !== null) {
    await api("PATCH", "/api/v1/settings", { require_acquisition_affidavit: originalRequire });
  }
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
