// 庫存頁序號品：品牌／型號欄位與「選了品牌就收斂其餘選項」的瀏覽器煙霧。
//
// 先用 API 造出兩個品牌各自的庫存，再從畫面確認：欄位看得到品牌與型號；選了品牌之後
// 型號／分類／成色只剩該品牌實際有的；換品牌時舊的選擇會被清掉（否則查出空清單）。
// 需 backend + frontend 已起、已 seed（dev-manager）。執行：node scripts/inventory-filter-cascade-smoke.mjs
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

import { uniquePhone, validNationalId } from "./_national-id.mjs";

const BASE = process.env.SMOKE_BASE ?? "http://localhost:3000";
const API = process.env.SMOKE_API_BASE ?? "http://localhost:8000";
const SHOTS = process.env.SMOKE_SHOTS ?? join(homedir(), "tmp", "lu-camp-inventory-cascade-shots");
const RUN = String(Date.now()).slice(-6);
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
      ...(method === "POST" ? { "Idempotency-Key": `inv-${RUN}-${Math.random()}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} → ${response.status}: ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

/** 下拉目前列出的選項文字（不含「全部…」那個預設項）。 */
async function optionsOf(page, label) {
  return (
    await page.getByLabel(label, { exact: true }).locator("option").allTextContents()
  ).filter((t) => !t.startsWith("全部"));
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (err) => pageErrors.push(String(err)));

try {
  const { access_token: token } = await apiJson("/api/v1/auth/login", {
    method: "POST",
    body: { username: "dev-manager", password: "dev-test-123456" },
  });
  if ((await apiJson("/api/v1/cash-sessions/current", { token })) === null) {
    await apiJson("/api/v1/cash-sessions/open", {
      method: "POST",
      token,
      body: { opening_float: "2000" },
    });
  }

  const seller = await apiJson("/api/v1/contacts", {
    method: "POST",
    token,
    body: {
      name: `林賣家-${RUN}`,
      phone: uniquePhone(),
      national_id: validNationalId(),
      roles: ["SELLER"],
    },
  });

  // 兩個品牌各自的型號與分類，成色也刻意不同，才驗得出收斂。
  const setups = [
    { brand: `蠻牛-${RUN}`, model: `營釘-${RUN}`, category: `配件-${RUN}`, grade: "A" },
    { brand: `別牌-${RUN}`, model: `營柱-${RUN}`, category: `支架-${RUN}`, grade: "D" },
  ];
  for (const s of setups) {
    const brand = await apiJson("/api/v1/brands", { method: "POST", token, body: { name: s.brand } });
    const model = await apiJson("/api/v1/product-models", {
      method: "POST",
      token,
      body: { brand_id: brand.id, name: s.model },
    });
    const category = await apiJson("/api/v1/categories", {
      method: "POST",
      token,
      body: { name: s.category },
    });
    await apiJson("/api/v1/acquisitions", {
      method: "POST",
      token,
      body: {
        type: "BUYOUT",
        contact_id: seller.id,
        payout_method: "CASH",
        items: [
          {
            name: `${s.brand} ${s.model}`,
            brand_id: brand.id,
            product_model_id: model.id,
            category_id: category.id,
            grade: s.grade,
            acquisition_cost: "40",
            listed_price: "120",
          },
        ],
      },
    });
    s.brandId = brand.id;
  }
  ok("造出兩個品牌各一件庫存（成色、型號、分類都不同）", true);

  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', "dev-manager");
  await page.fill('input[name="password"]', "dev-test-123456");
  await page.click('button:has-text("登入")');
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/inventory`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "序號品" }).click();
  await page.getByRole("columnheader", { name: "型號" }).waitFor();

  const headers = (await page.getByRole("columnheader").allTextContents()).slice(0, 4);
  ok(
    "欄位順序：序號碼、品牌、品名、型號",
    JSON.stringify(headers) === JSON.stringify(["序號碼", "品牌", "品名", "型號"]),
    headers.join("、"),
  );

  const pagerText = await page.locator(".inv-pager .hint").first().innerText();
  ok(
    "分頁顯示總頁數與總件數",
    /第 \d+ \/ \d+ 頁・共 \d+ 件/.test(pagerText),
    pagerText,
  );
  const row = page.getByRole("row").filter({ hasText: setups[0].model });
  await row.first().waitFor();
  const rowText = await row.first().innerText();
  ok(
    "該列顯示的是自己的品牌與型號",
    rowText.includes(setups[0].brand) && rowText.includes(setups[0].model),
    rowText.replace(/\n/g, " | "),
  );
  await page.screenshot({ path: join(SHOTS, "01-columns.png"), fullPage: true });

  // 沒選品牌：兩個品牌的型號都在
  const allModels = await optionsOf(page, "型號");
  ok(
    "沒選品牌時型號列出全部實際有的",
    allModels.includes(setups[0].model) && allModels.includes(setups[1].model),
    allModels.join("、"),
  );

  await page.getByLabel("品牌", { exact: true }).selectOption(String(setups[0].brandId));
  // 等「想要的選項出現且別的不見」——只等「別的不見」會在查詢載入中、
  // 選項暫時全空的那一瞬間就通過，讀到的是空清單。
  await page.waitForFunction(
    ([wanted, other]) => {
      const texts = Array.from(
        document.querySelectorAll('select[aria-label="型號"] option'),
      ).map((o) => o.textContent ?? "");
      return texts.some((t) => t.includes(wanted)) && !texts.some((t) => t.includes(other));
    },
    [setups[0].model, setups[1].model],
    { timeout: 10_000 },
  );
  const narrowedModels = await optionsOf(page, "型號");
  const narrowedGrades = await optionsOf(page, "成色");
  const narrowedCats = await optionsOf(page, "類型");
  ok(
    "選了品牌後型號只剩該品牌的",
    narrowedModels.length === 1 && narrowedModels[0] === setups[0].model,
    narrowedModels.join("、"),
  );
  ok(
    "分類與成色也一起收斂",
    narrowedCats.length === 1 &&
      narrowedCats[0] === setups[0].category &&
      narrowedGrades.length === 1 &&
      narrowedGrades[0].startsWith("A"),
    `分類 ${narrowedCats.join("、")} / 成色 ${narrowedGrades.join("、")}`,
  );
  await page.screenshot({ path: join(SHOTS, "02-narrowed.png"), fullPage: true });

  // 選定下層條件後換品牌：舊選擇必須清掉，否則會查出空清單
  await page.getByLabel("型號", { exact: true }).selectOption({ label: setups[0].model });
  await page.getByLabel("品牌", { exact: true }).selectOption(String(setups[1].brandId));
  await page.waitForFunction(
    (wanted) => {
      const select = document.querySelector('select[aria-label="型號"]');
      const texts = Array.from(select?.options ?? []).map((o) => o.textContent ?? "");
      return select?.value === "" && texts.some((t) => t.includes(wanted));
    },
    setups[1].model,
    { timeout: 10_000 },
  );
  ok(
    "換品牌會清掉型號與成色的舊選擇",
    (await page.getByLabel("型號", { exact: true }).inputValue()) === "" &&
      (await page.getByLabel("成色", { exact: true }).inputValue()) === "",
  );
  const secondModels = await optionsOf(page, "型號");
  ok(
    "換到第二個品牌後，選項換成它的",
    secondModels.length === 1 && secondModels[0] === setups[1].model,
    secondModels.join("、"),
  );
  await page.screenshot({ path: join(SHOTS, "03-switched-brand.png"), fullPage: true });

  // 另外三個分頁：欄位與分頁一致，散裝批同樣依品牌收斂。
  for (const [tab, expected] of [
    ["久滯庫存", ["序號碼", "品牌", "品名", "型號"]],
    ["一般商品", ["商品編號", "品牌", "品名"]],
    ["散裝批", ["批號", "品牌", "名稱"]],
  ]) {
    await page.getByRole("tab", { name: tab }).click();
    // 等總筆數真的回來——分頁一開始就渲染成「第 1 頁」，早讀會讀到還沒載入的狀態
    // （同前面型號下拉那個坑：等待條件要寫想看到的結果，不是等元素出現）。
    await page
      .locator(".inv-pager .hint")
      .filter({ hasText: /第 \d+ \/ \d+ 頁・共 \d+ 件/ })
      .first()
      .waitFor({ timeout: 10_000 });
    const cols = (await page.getByRole("columnheader").allTextContents()).slice(0, expected.length);
    const pager = await page.locator(".inv-pager .hint").first().innerText();
    ok(
      `${tab}：欄位含品牌、分頁有總頁數`,
      JSON.stringify(cols) === JSON.stringify(expected) && /第 \d+ \/ \d+ 頁・共 \d+ 件/.test(pager),
      `${cols.join("、")} ｜ ${pager}`,
    );
    await page.screenshot({ path: join(SHOTS, `0${tab === "久滯庫存" ? 5 : tab === "一般商品" ? 6 : 7}-${encodeURIComponent(tab)}.png`), fullPage: true });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: join(SHOTS, "04-mobile.png"), fullPage: true });

  ok("頁面無 JS 例外", pageErrors.length === 0, pageErrors.join(" / "));
  console.log(`\n${checks - failures.length}/${checks} PASS；截圖 ${SHOTS}`);
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await page.screenshot({ path: join(SHOTS, "99-failure.png"), fullPage: true });
  throw error;
} finally {
  await browser.close();
}
