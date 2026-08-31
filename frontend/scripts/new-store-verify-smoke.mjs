// 新機器開店驗收：對剛用 `setup_new_store` 建好的空店，實際登入三個帳號、確認列印抬頭
// 正確、權限分層有效、資料庫真的是空的。
//
// 搬機器時的最後一道確認——`setup_new_store` 印出的摘要只證明它自己寫了什麼，這支才
// 證明**店員真的登得進去、看到的東西是對的**。
//
// 密碼由環境變數提供（不入 repo）：
//   MANAGER_USERNAME/MANAGER_PASSWORD、CLERK_*、KIOSK_* 與 setup_new_store 同名同值。
//
// 執行（backend + frontend 已對新庫起好）：
//   MANAGER_PASSWORD=... CLERK_PASSWORD=... KIOSK_PASSWORD=... \
//     node frontend/scripts/new-store-verify-smoke.mjs
import { chromium } from "playwright";

const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (process.env.SMOKE_API ?? "http://localhost:8000").replace(/\/+$/, "");
const STORE_NAME = process.env.STORE_NAME ?? "露坑";
const STORE_TAX_ID = process.env.STORE_TAX_ID ?? "62106366";
const STORE_ADDRESS = process.env.STORE_ADDRESS ?? "";
const STORE_PHONE = process.env.STORE_PHONE ?? "";

// 帳號密碼一律由環境變數提供——驗收腳本裡寫死密碼，等於把正式密碼放進 repo。
const ACCOUNTS = {
  manager: [process.env.MANAGER_USERNAME ?? "admin", process.env.MANAGER_PASSWORD],
  clerk: [process.env.CLERK_USERNAME ?? "clerk", process.env.CLERK_PASSWORD],
  kiosk: [process.env.KIOSK_USERNAME ?? "ipad", process.env.KIOSK_PASSWORD],
};
for (const [role, [, pw]] of Object.entries(ACCOUNTS)) {
  if (!pw) {
    console.error(`${role.toUpperCase()}_PASSWORD 未設定——驗收腳本不內建密碼。`);
    process.exit(2);
  }
}
const results = [];
const ok = (n, p, d = "") => { results.push({ n, p }); console.log(`${p ? "✅" : "❌"} ${n}${d ? `：${d}` : ""}`); };

async function login(page, user, pw) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("帳號").fill(user);
  await page.getByLabel("密碼").fill(pw);
  await page.getByRole("button", { name: "登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
}

const browser = await chromium.launch();
try {
  // 店長：看得到管理專屬入口與設定頁的正確值
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await login(page, ...ACCOUNTS.manager);
    ok("店長 admin 可登入", true);
    ok("身分顯示為管理者", (await page.getByText("管理者").first().innerText()) === "管理者");

    await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "設定" }).first().waitFor({ timeout: 15000 });
    ok("設定頁載入", (await page.locator("body").innerText()).length > 0);

    // 店家抬頭**沒有任何畫面顯示**（只印在收據/明細聯/發票上），所以驗收要打實際被
    // 列印程式使用的那支端點，而不是找畫面上的字——找不到不代表資料錯。
    const header = await (await fetch(`${API}/api/v1/stores/1/receipt-header`)).json();
    ok(`列印抬頭店名為 ${STORE_NAME}`, header.name === STORE_NAME, JSON.stringify(header.name));
    ok(`列印抬頭統編為 ${STORE_TAX_ID}`, header.tax_id === STORE_TAX_ID, header.tax_id);
    ok("列印抬頭地址正確", !STORE_ADDRESS || header.address === STORE_ADDRESS, header.address);
    ok("列印抬頭電話正確", !STORE_PHONE || header.phone === STORE_PHONE, header.phone);

    // 交易紀錄應該是空的——這是「乾淨」的實際驗收，不是看設定檔。
    // 等空狀態那句話真的出現，不能只等標題：標題在資料回來前就在了。
    await page.goto(`${BASE}/sales`, { waitUntil: "domcontentloaded" });
    let empty = true;
    try {
      await page.getByText("今日尚無交易").first().waitFor({ timeout: 15000 });
    } catch {
      empty = false;
    }
    ok("交易紀錄是空的", empty);
    await ctx.close();
  }

  // 店員：登得進去，且看不到管理專屬入口
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await login(page, ...ACCOUNTS.clerk);
    ok("店員 clerk 可登入", true);
    ok("身分顯示為店員", (await page.getByText("店員").first().innerText()) === "店員");
    await page.getByRole("button", { name: "開啟系統選單" }).click();
    const menu = await page.getByRole("navigation", { name: "系統選單" }).innerText();
    ok("店員看不到設定/報表入口", !menu.includes("設定") && !menu.includes("報表"), menu.replace(/\n/g, " "));
    await ctx.close();
  }

  // 顧客簽署裝置：登入後不得進店務頁（KIOSK 專用身分）
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("帳號").fill(ACCOUNTS.kiosk[0]);
    await page.getByLabel("密碼").fill(ACCOUNTS.kiosk[1]);
    await page.getByRole("button", { name: "登入" }).click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
    ok("簽署裝置 ipad 可登入", true);
    // 登入後會先短暫落在 "/" 才被守衛導走，要等最終位置，不能讀第一個 URL。
    await page.waitForURL((u) => u.pathname.startsWith("/kiosk"), { timeout: 15000 }).catch(() => {});
    ok("導向簽署專用頁而非店務頁", new URL(page.url()).pathname.startsWith("/kiosk"), page.url());
    await page.goto(`${BASE}/pos`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    ok("簽署裝置進不了 POS", new URL(page.url()).pathname.startsWith("/kiosk"), page.url());
    await ctx.close();
  }

  // 錯密碼要被擋（確認密碼真的有生效，不是任何值都能登入）
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("帳號").fill(ACCOUNTS.manager[0]);
    await page.getByLabel("密碼").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "登入" }).click();
    await page.waitForTimeout(2500);
    ok("錯誤密碼無法登入", new URL(page.url()).pathname.startsWith("/login"), page.url());
    await ctx.close();
  }
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.p);
console.log(`\n${results.length - failed.length}/${results.length} 通過`);
process.exit(failed.length === 0 ? 0 : 1);
