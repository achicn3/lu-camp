// 收購定價提示瀏覽器煙霧：先用 API 造出「以前收過同款」的歷史，再從畫面確認
// 店員選完品牌＋型號＋成色就看得到當時收多少、賣多少，並能展開比較各成色。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/price-hint-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-price-hint-shots");
const RUN = String(Date.now()).slice(-6);
const BRAND = `蠻牛-${RUN}`;
const MODEL = `營釘 20cm-${RUN}`;
const CATEGORY = `露營配件-${RUN}`;
mkdirSync(SHOTS, { recursive: true });

let checks = 0;
const failures = [];
function ok(name, pass, detail = "") {
  checks += 1;
  if (!pass) failures.push(name);
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${detail ? `：${detail}` : ""}`);
}

async function apiJson(path, { method = "GET", token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(method === "POST" ? { "Idempotency-Key": `ph-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });

  // 收現金的收購必須在開帳中的班別下進行（§7 不變量 8）。
  const current = await apiJson("/api/v1/cash-sessions/current", { token });
  if (current === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }

  const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: BRAND } });
  const model = await apiJson("/api/v1/product-models", {
    method: "POST",
    token,
    body: { brand_id: brand.id, name: MODEL },
  });
  const category = await apiJson("/api/v1/categories", {
    method: "POST",
    token,
    body: { name: CATEGORY },
  });
  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `李賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    },
  });

  // 歷史行情：A 級兩件（收 35/45、賣 100/130）、C 級一件（收 20、賣 70）。
  const history = [
    { grade: "A", acquisition_cost: "35", listed_price: "100" },
    { grade: "A", acquisition_cost: "45", listed_price: "130" },
    { grade: "C", acquisition_cost: "20", listed_price: "70" },
  ];
  for (const item of history) {
    await apiJson("/api/v1/acquisitions", {
      method: "POST",
      token,
      body: {
        type: "BUYOUT",
        contact_id: seller.id,
        payout_method: "CASH",
        items: [
          {
            name: `${BRAND} ${MODEL}`,
            brand_id: brand.id,
            product_model_id: model.id,
            category_id: category.id,
            ...item,
          },
        ],
      },
    });
  }
  // 同款寄售一件、架上價 999：不得混進行情（店家對寄售沒有收購成本）。
  await apiJson("/api/v1/acquisitions", {
    method: "POST",
    token,
    body: {
      type: "CONSIGNMENT",
      contact_id: seller.id,
      items: [
        {
          name: `${BRAND} ${MODEL}`,
          brand_id: brand.id,
          product_model_id: model.id,
          category_id: category.id,
          grade: "A",
          listed_price: "999",
          commission_pct: 50,
        },
      ],
    },
  });
  ok("造出 3 筆買斷歷史（A 級 2 件、C 級 1 件）＋ 1 筆同款寄售", true);

  // 後端先驗一次，畫面沒顯示時才分得出是 API 還是 UI 的問題。
  const hint = await apiJson(
    `/api/v1/serialized-items/price-hint?brand_id=${brand.id}&product_model_id=${model.id}`,
    { token },
  );
  ok(
    "API 只認買斷：寄售那件（架上價 999）沒有混進來",
    hint.total_count === 3 && hint.grades.every((g) => Number(g.listed_max) < 999),
    JSON.stringify(hint.grades),
  );
  ok(
    "API 回同款行情：A 級收 35–45、賣 100–130",
    hint.total_count === 3 &&
      hint.grades.find((g) => g.grade === "A")?.cost_min === "35" &&
      hint.grades.find((g) => g.grade === "A")?.cost_max === "45" &&
      hint.grades.find((g) => g.grade === "A")?.listed_max === "130",
    JSON.stringify(hint.grades),
  );

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);

  await page.goto(`${BASE}/acquisition`, { waitUntil: "networkidle" });
  await page.waitForSelector('[role="tab"]:has-text("買斷")');

  // 還沒選品牌型號時不該有提示（沒有可靠比對鍵就不要猜）。
  ok("未選品牌型號時不顯示提示", (await page.locator(".price-hint").count()) === 0);
  await page.screenshot({ path: join(SHOTS, "01-before-selecting.png"), fullPage: true });

  // 下拉選項是 role="option" 的按鈕（CreatableCombobox），不是一般 button。
  await page.getByLabel("品牌", { exact: true }).click();
  await page.getByLabel("品牌", { exact: true }).fill(BRAND);
  await page.getByRole("option", { name: BRAND, exact: true }).click();
  await page.getByLabel("型號", { exact: true }).click();
  await page.getByLabel("型號", { exact: true }).fill(MODEL);
  await page.getByRole("option", { name: MODEL, exact: true }).click();

  const hintBox = page.locator(".price-hint");
  await hintBox.waitFor({ timeout: 10_000 });
  ok("選完品牌＋型號就出現提示", await hintBox.isVisible());
  await page.screenshot({ path: join(SHOTS, "02-hint-no-grade.png"), fullPage: true });

  // 該列第一個 select 就是成色（同 acquisition-smoke 的取法）。
  await page.locator(".acq-row select").first().selectOption("A");
  await page.getByText(/A 近全新\/精品 以前收過 2 件/).waitFor();
  const mainLine = await page.locator(".price-hint-main").innerText();
  ok(
    "依成色顯示該級距的收購價與售價區間",
    mainLine.includes("收購 35–45") && mainLine.includes("售價 100–130"),
    mainLine,
  );
  const subLine = await page.locator(".price-hint-sub").first().innerText();
  // 最近一次是 C 級那件；必須連成色一起講，否則會被誤讀成 A 級的行情。
  ok(
    "最近一次有標成色，不會跟上面的區間混淆",
    subLine.includes("C 有使用痕跡") && subLine.includes("收 20") && subLine.includes("賣 70"),
    subLine,
  );
  await page.screenshot({ path: join(SHOTS, "03-hint-grade-a.png"), fullPage: true });

  await page.getByRole("button", { name: /看各成色行情/ }).click();
  await page.locator(".price-hint-table").waitFor();
  const tableText = await page.locator(".price-hint-table").innerText();
  ok(
    "展開後一眼比較各成色（A 與 C 都在）",
    tableText.includes("A 近全新/精品") && tableText.includes("C 有使用痕跡") && tableText.includes("70"),
    tableText.replace(/\n/g, " | "),
  );
  await page.screenshot({ path: join(SHOTS, "04-all-grades.png"), fullPage: true });

  // 換成沒收過的成色：提示仍在，但要老實說這個成色沒收過，不能拿別的成色的價唬人。
  await page.locator(".acq-row select").first().selectOption("S");
  await page.getByText(/但沒收過 S 全新\/未使用/).waitFor();
  ok("沒收過的成色會明說，不拿別級距的價唬人", true);
  await page.screenshot({ path: join(SHOTS, "05-grade-never-acquired.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(SHOTS, "06-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
