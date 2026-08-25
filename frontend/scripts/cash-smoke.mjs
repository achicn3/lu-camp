// 錢櫃現金調整瀏覽器煙霧：真 backend + 真 Postgres。
// 將第一次已成功提交的回應改成 503，再由使用者點擊重試；
// 驗證兩次請求沿用同一冪等鍵，DB 最後只有一筆現金異動。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-shots", "cash-adjust-retry");
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";
const results = [];

mkdirSync(SHOTS, { recursive: true });

function ok(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "✅" : "❌"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body, expect = [200] } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  if (!expect.includes(response.status)) {
    throw new Error(`${method} ${path} → ${response.status}: ${text.slice(0, 300)}`);
  }
  return text ? JSON.parse(text) : null;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (error) => ok("頁面 JS 錯誤", false, String(error)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: PASS },
  });
  let cashSession = await apiJson("/api/v1/cash-sessions/current", { token });
  if (cashSession === null) {
    cashSession = await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
      expect: [201],
    });
  }

  const observedKeys = [];
  let firstCommittedResponseHidden = false;
  await page.route("**/api/v1/cash-sessions/*/movements", async (route) => {
    const request = route.request();
    if (request.method() !== "POST") {
      await route.continue();
      return;
    }
    observedKeys.push(request.headers()["idempotency-key"] ?? "");
    if (!firstCommittedResponseHidden) {
      const committed = await route.fetch();
      if (!committed.ok()) {
        throw new Error(`第一次真實調整未成功：${committed.status()}`);
      }
      firstCommittedResponseHidden = true;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "模擬後端已入帳、但回應在網路中遺失" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', PASS);
  await page.click('button:has-text("登入")');
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 15000 });
  await page.goto(`${BASE}/cash`, { waitUntil: "networkidle" });

  const note = `回應遺失重試-${Date.now()}`;
  await page.getByLabel("調整金額（可負）").fill("137");
  await page.getByLabel("事由").fill(note);
  await page.getByRole("button", { name: "送出調整" }).click();
  await page.getByRole("alert").filter({ hasText: "回應在網路中遺失" }).waitFor();
  ok("第一次已入帳但畫面收到失敗", true);
  await page.screenshot({ path: join(SHOTS, "01-response-lost.png"), fullPage: true });

  // 表單保留原金額與事由，使用者直接再點一次。
  await page.getByRole("button", { name: "送出調整" }).click();
  await page.getByText("已調整", { exact: true }).waitFor();
  ok("使用者可直接重試並成功收斂", true);

  const movements = await apiJson(
    `/api/v1/cash-sessions/${cashSession.id}/movements`,
    { token },
  );
  const matching = movements.filter((movement) => movement.note === note);
  ok(
    "重試前後沿用同一冪等鍵",
    observedKeys.length === 2 && observedKeys[0] !== "" && observedKeys[0] === observedKeys[1],
    observedKeys.join(" / "),
  );
  ok("DB 只有一筆 137 元調整", matching.length === 1 && matching[0].amount === "137");
  await page.screenshot({ path: join(SHOTS, "02-idempotent-retry.png"), fullPage: true });
} catch (error) {
  ok("煙霧流程例外", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
console.log(`截圖：${SHOTS}`);
process.exit(failed.length === 0 ? 0 : 1);
