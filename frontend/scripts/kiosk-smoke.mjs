// 手持簽署裝置瀏覽器煙霧（docs/23 K3）：真後端建立並配對客顯 → 店員經 API 建收購
// 切結任務 → 手持端看到後端 canonical PII、完整條款、品項與撥款 → 手寫簽名送出。
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

async function apiJson(token, method, path, body, extraHeaders = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
      ...extraHeaders,
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
  // ── 前置：KIOSK 登入、建立櫃檯並配對（真 cookie/device session）─────────
  const mgrToken = await apiLogin(MGR_USER, MGR_PASS);
  const page = await browser.newPage({ viewport: { width: 834, height: 1112 } }); // 直式平板
  await page.goto(`${BASE}/kiosk`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', KIOSK_USER);
  await page.fill('input[name="password"]', KIOSK_PASS);
  await page.click('button:has-text("啟用裝置")');
  await page.waitForSelector(".kiosk-pairing-code", { timeout: 8000 });
  const pairingCode = (await page.textContent(".kiosk-pairing-code"))?.trim();
  const terminal = await apiJson(
    mgrToken,
    "POST",
    "/api/v1/customer-display/terminals",
    {
      installation_id: crypto.randomUUID(),
      name: `客顯煙霧櫃檯 ${Date.now()}`,
    },
  );
  ok("建立 E2E 櫃檯", terminal.status === 201, `status=${terminal.status}`);
  const terminalId = terminal.json?.id;
  const paired = await apiJson(
    mgrToken,
    "POST",
    `/api/v1/customer-display/terminals/${terminalId}/pair`,
    { pairing_code: pairingCode },
  );
  ok("客顯與櫃檯配對", paired.status === 200, `status=${paired.status}`);
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 });

  // ── 店員端經 API 建立收購切結任務 ────────────────────────────────────
  const phone = uniquePhone();
  const nid = validNationalId();
  const sellerName = "煙霧簽署客";
  const address = "臺北市大安區露營路 88 號";
  const created = await apiJson(mgrToken, "POST", "/api/v1/contacts", {
    name: sellerName,
    phone,
    address,
    national_id: nid,
    roles: ["SELLER", "MEMBER"],
  });
  ok("建立 SELLER 聯絡人", created.status === 201, `status=${created.status}`);
  const contactId = created.json?.id;

  const masked = `${nid.slice(0, 3)}****${nid.slice(-3)}`;
  const taskRes = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    terminal_id: terminalId,
    content: {
      seller_name: "前端偽造姓名不得進入快照",
      national_id_masked: "Z99****999",
      phone: "0900000000",
      address: "前端偽造地址不得進入快照",
      items: [
        { name: "登山背包", amount: "1200" },
        { name: "登山杖一組", amount: "600" },
      ],
      total: "1800",
    },
  });
  ok("建立收購切結任務", taskRes.status === 201, `status=${taskRes.status}`);
  const taskId = taskRes.json?.id;
  ok(
    "後端以聯絡人主檔覆寫切結 PII",
    taskRes.json?.content?.seller_name === sellerName &&
      taskRes.json?.content?.phone === phone &&
      taskRes.json?.content?.address === address &&
      taskRes.json?.content?.national_id_masked === masked,
  );

  // ── 手持端：顯示真任務 → 簽名送出 ────────────────────────────────────
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  // PENDING 畫面與 SIGNING 畫面共用標題；等撥款按鈕出現才代表 ACK 已完成、完整互動畫面就緒。
  await page.waitForSelector("button.kiosk-payout-btn", { timeout: 8000 });
  ok("手持端顯示切結任務", true);
  const bodyText = await page.textContent(".kiosk-task-body");
  ok("顯示品項與金額", bodyText.includes("登山背包") && bodyText.includes("1,800"));
  ok(
    "手持端顯示後端 canonical PII",
    bodyText.includes(sellerName) &&
      bodyText.includes(phone) &&
      bodyText.includes(address) &&
      bodyText.includes(masked),
  );
  const agreementTitle = await page.textContent(".kiosk-agreement-title");
  ok(
    "顯示正式切結書標題",
    agreementTitle === "二手商品讓售切結書 暨 個人資料告知同意書",
    agreementTitle ?? "",
  );
  const agreementSections = [
    "一、物品來源保證（非贓物切結）",
    "二、交易確認",
    "三、售出概不退還",
    "四、瑕疵告知",
    "五、個人資料告知與同意（個人資料保護法第 8 條）",
    "六、其他",
  ];
  ok(
    "顯示完整六節切結條款",
    agreementSections.every((section) => bodyText.includes(section)),
  );
  // 購物金溢價（使用者裁示）：預設 10% → 1800 現金 → 購物金多得 $180
  ok(
    "購物金按鈕顯示溢價（多得）",
    bodyText.includes("多得") && bodyText.includes("180"),
    bodyText.match(/多得[^，。]*/)?.[0] ?? "",
  );
  await page.screenshot({ path: join(SHOTS, "01-task.png"), fullPage: true });
  const agreementBody = page.locator(".kiosk-agreement-body");
  const agreementScroll = await agreementBody.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  ok(
    "完整條款區可上下捲動",
    agreementScroll.scrollHeight > agreementScroll.clientHeight,
    `${agreementScroll.clientHeight}/${agreementScroll.scrollHeight}`,
  );
  await agreementBody.evaluate((element) => {
    element.scrollTop = (element.scrollHeight - element.clientHeight) / 2;
  });
  await page.screenshot({ path: join(SHOTS, "01b-agreement-middle.png"), fullPage: true });
  await agreementBody.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await page.screenshot({ path: join(SHOTS, "01c-agreement-bottom.png"), fullPage: true });
  await agreementBody.evaluate((element) => {
    element.scrollTop = 0;
  });

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
  const signedAt = Date.now();
  ok("送出後顯示完成畫面", true);
  await page.screenshot({ path: join(SHOTS, "03-done.png"), fullPage: true });

  // ── 完成畫面：感謝＋自動回待機倒數，且不再有店員帳密交回鎖（店主裁示）──────
  const thanksText = (await page.textContent(".kiosk-thanks-inner"))?.trim() ?? "";
  ok(
    "完成畫面顯示感謝與自動回待機倒數",
    /感謝您/.test(thanksText) && /\d+ 秒後自動回到待機畫面/.test(thanksText),
    thanksText.replace(/\s+/g, " "),
  );
  ok(
    "完成畫面不再要求店員帳密交回",
    (await page.locator('button:has-text("店員解鎖，接續下一位")').count()) === 0 &&
      (await page.locator(".kiosk-unlock-form").count()) === 0 &&
      (await page.locator('input[name="password"]').count()) === 0,
  );

  // ── 驗證後端狀態：任務 SIGNED、撥款 CASH、有簽名影像 ────────────────
  const check = await apiJson(mgrToken, "GET", `/api/v1/signing/tasks/${taskId}`);
  ok("後端任務為 SIGNED", check.json?.status === "SIGNED", `status=${check.json?.status}`);
  ok("撥款回填為現金", check.json?.chosen_payout === "CASH", `payout=${check.json?.chosen_payout}`);
  ok("已存簽名影像", check.json?.has_signature === true);

  const sig = await fetch(`${API_BASE}/api/v1/signing/tasks/${taskId}/signature`, {
    headers: { authorization: `Bearer ${mgrToken}` },
  });
  ok("簽名 PNG 可取回", sig.ok && sig.headers.get("content-type") === "image/png");

  // 已簽但未綁定收購的切結仍是 active；先依現行狀態機明確作廢，再推下一張切結。
  const voidedFirst = await apiJson(
    mgrToken,
    "POST",
    `/api/v1/signing/tasks/${taskId}/cancel`,
  );
  ok("未綁定切結先明確作廢", voidedFirst.status === 200, `status=${voidedFirst.status}`);
  const nextTask = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    terminal_id: terminalId,
    content: { total: "100", items: [{ name: "恢復測試品", amount: "100" }] },
  });
  ok("建立下一張切結任務", nextTask.status === 201, `status=${nextTask.status}`);

  // 倒數尚未結束前不得換人：感謝畫面仍在、下一張任務不得插隊到客人面前。
  await page.waitForTimeout(Math.max(0, signedAt + 6000 - Date.now()));
  ok(
    "倒數期間不提早帶出下一張任務",
    (await page.locator('h1:has-text("已完成簽署")').isVisible()) &&
      !(await page.locator('h1:has-text("收購確認與切結")').isVisible()),
  );

  // 倒數結束 → 自動清場回待機並恢復輪詢，直接帶出下一張任務（全程未輸入任何帳密）。
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 15000 });
  await page.waitForSelector("button.kiosk-payout-btn", { timeout: 8000 });
  ok(
    "倒數結束自動接續下一張任務（免店員帳密）",
    (await page.locator(".kiosk-unlock-form").count()) === 0 &&
      Date.now() - signedAt >= 9000,
    `${Math.round((Date.now() - signedAt) / 1000)}s`,
  );

  // 重整不再卡在交回鎖：仍直接顯示當前任務、不要求帳密。
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  ok(
    "重整後不再卡在交回鎖",
    (await page.locator(".kiosk-unlock-form").count()) === 0 &&
      (await page.locator('button:has-text("店員解鎖，接續下一位")').count()) === 0,
  );
  await page.waitForSelector("button.kiosk-payout-btn", { timeout: 8000 });

  // ── 回歸：後端 canonical 切結內容仍完整渲染 ──────────────────────────
  const ackBody = await page.textContent(".kiosk-task-body");
  ok(
    "canonical 切結內容完整渲染",
    ackBody.includes("恢復測試品") && ackBody.includes("合計金額"),
  );

  // ── 回歸：5xx 為曖昧（可能已寫入）不得清鎖恢復輪詢＋在途鎖定 payload（Codex K3 第八/九輪）
  // 延遲後回 500：模擬「已受理但伺服器失敗」，在途期間檢查控制項鎖定；500 後須保持凍結。
  await page.route("**/api/v1/kiosk/tasks/*/sign", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' });
  });
  await page.check('.kiosk-agree-check input[type="checkbox"]');
  await page.click('button.kiosk-payout-btn:has-text("現金")');
  await drawSignature(page);
  const submitClick = page.click("button.kiosk-submit");
  await page.waitForTimeout(300); // POST 在途
  ok("送出在途即鎖定清除簽名", await page.locator('button:has-text("清除重簽")').isDisabled());
  await submitClick;
  await page.waitForSelector(".kiosk-task-footer .form-error", { timeout: 6000 });
  await page.waitForTimeout(200);
  const recoverable =
    (await page.locator('h1:has-text("收購確認與切結")').isVisible()) &&
    (await page.locator("button.kiosk-submit").isEnabled());
  ok("5xx 後可重試、不卡死", recoverable);

  // 5xx 為曖昧：payload 仍鎖定，且持久簽署鎖未被清（localStorage 仍為 '1'）——不恢復輪詢
  ok("5xx 後鎖定清除簽名", await page.locator('button:has-text("清除重簽")').isDisabled());
  const lockKept = await page.evaluate(() =>
    window.localStorage.getItem("lu-camp.kiosk-signing"),
  );
  ok("5xx 為曖昧、持久簽署鎖未清", lockKept === "1", `lock=${lockKept}`);

  // 曖昧失敗後重整 → 進店員恢復畫面、不輪詢、不顯示待簽任務（Codex K3 第七輪 high）
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('h1:has-text("上一筆簽署尚未確認")', { timeout: 8000 });
  ok(
    "曖昧失敗重整後進恢復畫面、不洩漏任務",
    !(await page.locator('h1:has-text("收購確認與切結")').isVisible()),
  );
  // 店員確認並解鎖 → 恢復輪詢，待簽任務重新出現（可重新簽署）
  await page.unroute("**/api/v1/kiosk/tasks/*/sign");
  await page.click('button:has-text("店員確認並解鎖")');
  await page.fill('.kiosk-unlock-form input[name="username"]', MGR_USER);
  await page.fill('.kiosk-unlock-form input[name="password"]', MGR_PASS);
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  await page.waitForSelector("button.kiosk-payout-btn", { timeout: 8000 });
  ok("店員解鎖恢復後任務重現", true);

  // 重新簽署 → 感謝畫面；作廢後倒數到期自動回待機（無需任何店員操作）
  await page.check('.kiosk-agree-check input[type="checkbox"]');
  await page.click('button.kiosk-payout-btn:has-text("現金")');
  await drawSignature(page);
  await page.click("button.kiosk-submit");
  await page.waitForSelector('h1:has-text("已完成簽署")', { timeout: 8000 });
  ok("恢復後重新簽署成功", true);
  const voidedRecovery = await apiJson(
    mgrToken,
    "POST",
    `/api/v1/signing/tasks/${nextTask.json.id}/cancel`,
  );
  ok(
    "恢復測試切結明確作廢",
    voidedRecovery.status === 200,
    `status=${voidedRecovery.status}`,
  );
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 15000 });
  ok("完成畫面到期自動回待機（無店員操作）", true);
  await page.screenshot({ path: join(SHOTS, "03b-standby.png"), fullPage: true });

  // ── 釘選閘門：顯示任務 A 時店員取消並改推不同任務 B，不得自動換到客人面前，
  //    須店員確認解鎖才採用（Codex K3 第十輪 high）───────────────────────────
  const taskA = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    terminal_id: terminalId,
    content: { seller_name: "釘選客A", total: "500", items: [{ name: "A物", amount: "500" }] },
  });
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 }); // 顯示 A、釘選
  // 現行狀態機要求先明確撤回 A，再推不同內容的 B。
  await apiJson(mgrToken, "POST", `/api/v1/signing/tasks/${taskA.json.id}/cancel`);
  const taskB = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    terminal_id: terminalId,
    content: { total: "87654", items: [{ name: "B物", amount: "87654" }] },
  });
  await page.waitForSelector('h1:has-text("任務已更新")', { timeout: 8000 });
  ok(
    "改推不同任務不自動換到客人面前",
    !(await page.locator('h1:has-text("收購確認與切結")').isVisible()) &&
      !(await page.locator("text=87,654").isVisible()),
  );
  // 閘門顯示時重整 → 釘選持久，仍停在閘門、不放行 B（Codex K3 第十二輪 high）
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('h1:has-text("任務已更新")', { timeout: 8000 });
  ok(
    "閘門顯示時重整仍被擋",
    !(await page.locator('h1:has-text("收購確認與切結")').isVisible()),
  );
  // 店員確認解鎖 → 採用新任務 B
  await page.click('button:has-text("店員確認並解鎖")');
  await page.fill('.kiosk-unlock-form input[name="username"]', MGR_USER);
  await page.fill('.kiosk-unlock-form input[name="password"]', MGR_PASS);
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  ok("店員解鎖後採用新任務", true);

  // ── 釘選閘門（空窗）：取消 B → current=null 待機 → 建 C，C 仍須被閘門擋，不得因空窗
  //    被當成首張任務直接顯示（Codex K3 第十一輪 high）─────────────────────────
  await apiJson(mgrToken, "POST", `/api/v1/signing/tasks/${taskB.json.id}/cancel`);
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 }); // current=null → 待機
  // 空窗（current=null）期間重整 → 釘選持久，仍非「首張」狀態（Codex K3 第十二輪）
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 });
  const taskC = await apiJson(mgrToken, "POST", "/api/v1/signing/tasks", {
    kind: "ACQUISITION_AFFIDAVIT",
    contact_id: contactId,
    terminal_id: terminalId,
    content: { seller_name: "空窗後客C", total: "700", items: [{ name: "C物", amount: "700" }] },
  });
  await page.waitForSelector('h1:has-text("任務已更新")', { timeout: 8000 });
  ok(
    "取消到空窗再建新任務仍被閘門擋",
    !(await page.locator('h1:has-text("收購確認與切結")').isVisible()),
  );
  await page.click('button:has-text("店員確認並解鎖")');
  await page.fill('.kiosk-unlock-form input[name="username"]', MGR_USER);
  await page.fill('.kiosk-unlock-form input[name="password"]', MGR_PASS);
  await page.click('.kiosk-unlock-form button:has-text("解鎖")');
  await page.waitForSelector('h1:has-text("收購確認與切結")', { timeout: 8000 });
  ok("空窗後解鎖採用新任務", true);

  // ── 回歸：裝置 cookie 不具店務權限，進店務頁只會回登入 ─────────────────
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  ok("客顯裝置 cookie 不得進店務殼", page.url().endsWith("/login"), page.url());

  // 客顯 API 身分只取 HttpOnly device cookie；即使同 origin 殘留店務 bearer，
  // /kiosk 仍使用已配對裝置 session，不會被 bearer 取代成店務畫面。
  await page.evaluate((t) => window.localStorage.setItem("lu-camp.access-token", t), mgrToken);
  await page.goto(`${BASE}/kiosk`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(600);
  ok(
    "店務 bearer 不得取代客顯裝置身分",
    (await page.locator(".kiosk-task").isVisible()) &&
      !(await page.locator(".app-shell").isVisible()),
  );

  // ── 顧客購物車折扣用語：顧客只看到「折扣」，不出現內部用語「本行折抵」──────
  await apiJson(mgrToken, "POST", `/api/v1/signing/tasks/${taskC.json.id}/cancel`);
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 });
  const cashSession = await apiJson(mgrToken, "GET", "/api/v1/cash-sessions/current");
  if (cashSession.json === null) {
    await apiJson(mgrToken, "POST", "/api/v1/cash-sessions/open", {
      opening_float: "2000",
    });
  }
  const now = Date.now();
  const campaign = await apiJson(mgrToken, "POST", "/api/v1/campaigns", {
    name: `顧客螢幕折扣用語煙測 ${now}`,
    discount_pct: 10,
    starts_at: new Date(now - 86_400_000).toISOString(),
    ends_at: new Date(now + 86_400_000).toISOString(),
    applies_owned_serialized: true,
    applies_owned_bulk: false,
    applies_catalog: false,
    applies_consignment: false,
    consignment_discount_bearing: "STORE_ABSORBS",
  });
  await apiJson(mgrToken, "POST", `/api/v1/campaigns/${campaign.json.id}/activate`);
  const acquired = await apiJson(
    mgrToken,
    "POST",
    "/api/v1/acquisitions",
    {
      type: "BUYOUT",
      contact_id: contactId,
      payout_method: "CASH",
      items: [
        {
          name: `折扣用語測試帳篷 ${now}`,
          grade: "A",
          listed_price: "1000",
          acquisition_cost: "400",
        },
      ],
    },
    { "Idempotency-Key": `kiosk-smoke-discount-${now}` },
  );
  ok("收購折扣測試品（標價 1000）", acquired.status === 201, `status=${acquired.status}`);
  const cartPushed = await apiJson(
    mgrToken,
    "PUT",
    `/api/v1/customer-display/terminals/${terminalId}/cart`,
    {
      expected_revision: null,
      lines: [{ line_type: "SERIALIZED", item_code: acquired.json.item_codes[0], qty: 1 }],
    },
  );
  ok("推送折扣購物車到顧客螢幕", cartPushed.status === 200, `status=${cartPushed.status}`);
  await page.waitForSelector(".kiosk-cart-item", { timeout: 8000 });
  const cartText = (await page.textContent(".kiosk-cart-items")) ?? "";
  ok(
    "顧客螢幕逐行顯示「折扣」而非「本行折抵」",
    cartText.includes("折扣 $100") &&
      cartText.includes("原價 $1,000") &&
      cartText.includes("優惠價 $900") &&
      !cartText.includes("本行折抵"),
    cartText.replace(/\s+/g, " ").slice(0, 120),
  );
  await page.screenshot({ path: join(SHOTS, "04-cart-discount.png"), fullPage: true });
  await apiJson(
    mgrToken,
    "POST",
    `/api/v1/customer-display/terminals/${terminalId}/cart/cancel`,
    { expected_revision: cartPushed.json.revision, reason: "煙霧測試結束清場" },
  );
  await page.waitForSelector('h1:has-text("露營二手")', { timeout: 8000 });

} catch (err) {
  ok("煙霧未拋例外", false, String(err?.message ?? err));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
