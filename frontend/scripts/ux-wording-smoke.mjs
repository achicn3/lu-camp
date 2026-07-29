// UX 用語與提示煙霧（報表說明 tooltip／效益指標備註／對帳收斂／收購品名提示＋選定標籤）。
// 需：backend:8000、frontend:3000、已 seed dev-manager。截圖輸出 SMOKE_SHOTS。
import { mkdirSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
// 不寫死家目錄：他人 checkout 下會 EACCES/ENOENT（Codex P3）。
const SHOTS =
  process.env.SMOKE_SHOTS ?? join(homedir() || tmpdir(), "tmp", "lu-camp-uxfix", "wording");
const USER = process.env.SMOKE_USERNAME ?? "dev-manager";
const PASS = process.env.SEED_USER_PASSWORD ?? "dev-test-123456";

mkdirSync(SHOTS, { recursive: true });
let pass = 0;
let fail = 0;
function ok(name, cond, detail = "") {
  if (cond) {
    pass += 1;
    console.log(`✅ ${name}${detail ? ` — ${detail}` : ""}`);
  } else {
    fail += 1;
    console.log(`❌ ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const jsErrors = [];
  page.on("pageerror", (e) => jsErrors.push(String(e)));

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });

  // ── 1) 今日營運：營收三指標有 ⓘ 說明，且說明是白話（含實例）──
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.waitForSelector("text=營業額", { timeout: 15000 });
  const tipTitles = await page.$$eval(".info-tip", (els) => els.map((e) => e.getAttribute("title")));
  const turnoverTip = tipTitles.find((t) => t && t.includes("收銀機"));
  const recognizedTip = tipTitles.find((t) => t && t.includes("真正屬於店家"));
  ok("營業額有白話說明", Boolean(turnoverTip), (turnoverTip ?? "").slice(0, 24) + "…");
  ok("認列營收有白話說明（含寄售實例）", Boolean(recognizedTip) && recognizedTip.includes("寄售"));
  ok("說明用實例而非公式", Boolean(turnoverTip) && turnoverTip.includes("例："));
  await page.screenshot({ path: join(SHOTS, "01-dashboard-tips.png"), fullPage: true });

  // ── 2) 效益指標：備註欄不再空白，且結論指標講人話 ──
  await page.click('[role="tab"]:has-text("效益指標")');
  await page.waitForSelector(".inv-table tbody tr", { timeout: 15000 });
  const notes = await page.$$eval(".rpt-metric-note", (els) =>
    els.map((e) => (e.textContent ?? "").trim()),
  );
  ok("七項指標備註全數填寫", notes.length === 7 && notes.every((n) => n.length > 8), `共 ${notes.length} 筆`);
  const deltaNote = notes.find((n) => n.includes("每發出"));
  ok("結論指標說明白話（淨賺/淨賠）", Boolean(deltaNote) && deltaNote.includes("淨賺"));
  ok("說明不出現希臘字母術語", !notes.some((n) => /α|β|Δ/.test(n)));
  await page.screenshot({ path: join(SHOTS, "02-effectiveness-notes.png"), fullPage: true });

  // ── 3) 對帳：不再出現「快取」，改為一句結論 ──
  // 精確比對：「對帳」子字串也會命中「現金對帳」分頁。
  await page.getByRole("tab", { name: "對帳", exact: true }).click();
  await page.waitForSelector("text=帳目核對", { timeout: 15000 });
  const reconBody = await page.innerText("body");
  ok("對帳頁不再出現「快取」字眼", !reconBody.includes("快取"));
  ok("改為白話結論「帳目核對」", reconBody.includes("帳目核對"));
  await page.screenshot({ path: join(SHOTS, "03-reconciliation.png"), fullPage: true });

  // ── 4) 收購：品名提示（datalist）＋ 品牌選定後標籤化 ──
  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('input[aria-label="品名"]', { timeout: 15000 });
  const nameInput = page.locator('input[aria-label="品名"]').first();
  ok("品名欄位掛上建議清單", (await nameInput.getAttribute("list")) !== null);
  await nameInput.fill("帳");
  await page.waitForTimeout(900); // debounce 200ms + 往返
  const optionCount = await page.evaluate(() => {
    const input = document.querySelector('input[aria-label="品名"]');
    const list = input?.getAttribute("list");
    return list ? (document.getElementById(list)?.querySelectorAll("option").length ?? 0) : 0;
  });
  ok("輸入後取得歷史品名建議", optionCount > 0, `${optionCount} 筆`);
  ok("品名仍可自由輸入（純提示）", !(await nameInput.getAttribute("readonly")));
  await page.screenshot({ path: join(SHOTS, "04-item-name-suggest.png"), fullPage: true });

  // 品牌：輸入但未選 → 應提示「尚未選定」；點選後 → 變成標籤
  const brandInput = page.locator(".combo-input").first();
  await brandInput.fill("Snow");
  await page.waitForTimeout(700);
  const menuOption = page.locator(".combo-option").first();
  const hasExisting = await menuOption.count();
  if (hasExisting > 0) {
    await menuOption.click();
  } else {
    await page.locator(".combo-create").first().click();
  }
  await page.waitForSelector('[data-testid="combo-selected"]', { timeout: 10000 });
  ok("品牌選定後以標籤呈現（可一眼分辨已確認）", true);
  const chipText = await page.locator('[data-testid="combo-selected"]').first().innerText();
  ok("標籤顯示已選名稱與勾號", chipText.includes("✓"), chipText.replace(/\s+/g, " ").slice(0, 30));
  await page.screenshot({ path: join(SHOTS, "05-brand-chip.png"), fullPage: true });

  // 型號選定後改品牌 → 父層會清掉 productModelId，型號標籤必須跟著消失，
  // 否則畫面顯示「✓ 已選型號」但送出的內容是空的（Codex P2 迴歸）。
  const modelInput = page.locator(".combo-input").first(); // 品牌已是標籤，故第一個輸入框＝型號
  await modelInput.fill("測試型號");
  await page.waitForTimeout(700);
  const modelCreate = page.locator(".combo-create").first();
  if ((await modelCreate.count()) > 0) {
    await modelCreate.click();
  } else {
    await page.locator(".combo-option").first().click();
  }
  await page.waitForTimeout(500);
  const chipsBefore = await page.locator('[data-testid="combo-selected"]').count();
  ok("型號也可選定為標籤", chipsBefore >= 2, `${chipsBefore} 個標籤`);
  // 清掉品牌 → 型號應連帶失效
  await page.locator(".combo-chip-clear").first().click();
  await page.waitForTimeout(600);
  const chipsAfter = await page.locator('[data-testid="combo-selected"]').count();
  ok("清除品牌後型號標籤同步消失（不留假的已選狀態）", chipsAfter === 0, `剩 ${chipsAfter} 個`);
  await page.screenshot({ path: join(SHOTS, "06-brand-cleared.png"), fullPage: true });

  // ── 5) 估計轉售價 → 上架售價一鍵帶入（兩者常相同，省去重複輸入）──
  await page.locator('input[aria-label="估計轉售價"]').first().fill("2500");
  await page.waitForTimeout(300);
  const fillBtn = page.locator('button:has-text("同估計轉售價")').first();
  ok("出現「同估計轉售價」快捷", (await fillBtn.count()) > 0);
  await fillBtn.click();
  const listed = await page.locator('input[aria-label="上架售價"]').first().inputValue();
  ok("一鍵帶入後上架售價＝估計轉售價", listed === "2500", `上架售價=${listed}`);
  const acqTips = await page.$$eval(".info-tip", (els) =>
    els.map((e) => e.getAttribute("title") ?? ""),
  );
  const resaleHint = acqTips.find((t) => t.includes("不會存入")) ?? "";
  ok("估計轉售價有說明（點出不會存入系統）", resaleHint !== "", resaleHint.slice(0, 20) + "…");
  // 觸控/鍵盤可用：說明是可點擊的按鈕，點擊後展開（非僅 hover 的 title）。
  await page.locator(".info-tip").first().click();
  const popCount = await page.locator('[role="tooltip"]').count();
  ok("說明可點擊展開（觸控/鍵盤可用）", popCount > 0, `${popCount} 個泡泡`);
  await page.screenshot({ path: join(SHOTS, "07-resale-fill.png"), fullPage: true });

  // ── 6) 窄螢幕（手機/直式平板）說明泡泡不得溢出視窗 ──
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(`${BASE}/reports`, { waitUntil: "networkidle" });
  await page.waitForSelector(".info-tip", { timeout: 15000 });
  const tips = page.locator(".info-tip");
  const tipCount = Math.min(await tips.count(), 4);
  let overflowed = 0;
  for (let i = 0; i < tipCount; i += 1) {
    await tips.nth(i).click();
    await page.waitForTimeout(150);
    const box = await page.locator('[role="tooltip"]').first().boundingBox();
    // 四個方向都要驗：先前只檢查左右，漏掉了「靠近底部時往下溢出」（Codex 補審抓到）。
    if (
      box &&
      (box.x < 0 || box.x + box.width > 375 || box.y < 0 || box.y + box.height > 800)
    ) {
      overflowed += 1;
    }
    await tips.nth(i).click(); // 收起
  }
  ok("窄螢幕下說明泡泡不溢出視窗（四向）", overflowed === 0, `檢查 ${tipCount} 個，溢出 ${overflowed} 個`);

  // 靠底部的說明必須「往上翻」。前一版只驗「沒溢出」，一個永遠向下展開的實作只要泡泡夠小
  // 也會通過；且 Playwright 的 click() 會自動把元素捲進畫面，位置無法保證（Codex 補審）。
  // 改為：明確把某個 ⓘ 釘在視窗底部附近，再斷言泡泡底緣不低於按鈕頂緣（＝確實在上方）。
  const bottomTip = tips.first();
  const preBox = await bottomTip.boundingBox();
  // 直接把視窗高度縮到「按鈕下方只剩 24px」：靠捲動不可靠（頁面可能已到底，且 Playwright
  // 的 click 會自動捲回可見），而泡泡僅約 40px 高，留太多空間根本不會觸發翻轉。
  const shortHeight = Math.round((preBox?.y ?? 0) + (preBox?.height ?? 0) + 24);
  await page.setViewportSize({ width: 375, height: shortHeight });
  await page.waitForTimeout(250);
  const anchorBox = await bottomTip.boundingBox();
  await bottomTip.click({ force: true });
  await page.waitForTimeout(250);
  const popBox = await page.locator('[role="tooltip"]').first().boundingBox();
  const flippedUp =
    popBox !== null && anchorBox !== null && popBox.y + popBox.height <= anchorBox.y + 1;
  const inViewport = popBox !== null && popBox.y >= 0 && popBox.y + popBox.height <= shortHeight;
  ok(
    "靠底部的說明往上翻且完整可見",
    flippedUp && inViewport,
    popBox && anchorBox
      ? `泡泡底=${Math.round(popBox.y + popBox.height)} ⓘ頂=${Math.round(anchorBox.y)} 視窗高=${shortHeight}`
      : "無泡泡",
  );
  await bottomTip.click({ force: true });
  await tips.first().click();
  await page.screenshot({ path: join(SHOTS, "08-narrow-tooltip.png"), fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1100 });

  ok("無 JS 錯誤", jsErrors.length === 0, jsErrors.slice(0, 2).join(" | "));

  console.log(`\n結果：${pass}/${pass + fail} 通過`);
  console.log(`截圖：${SHOTS}`);
  if (fail > 0) process.exitCode = 1;
} finally {
  await browser.close();
}
