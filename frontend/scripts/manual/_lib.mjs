// 操作手冊製作共用工具：登入、截圖、標註、測試資料產生。
// 只在本機手冊專用資料庫（lucamp_manual）執行。
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { chromium } from "playwright";

export const BASE = (process.env.SMOKE_BASE ?? "http://localhost:3000").replace(/\/+$/, "");
export const API = (process.env.SMOKE_API_BASE ?? "http://localhost:8000").replace(/\/+$/, "");
export const SHOTS_ROOT = process.env.MANUAL_SHOTS ?? join(homedir(), "tmp", "lu-camp-manual", "shots");
export const MGR = { u: "dev-manager", p: "dev-test-123456" };
export const KIOSK = { u: "dev-kiosk", p: "dev-test-123456" };

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
      await page.screenshot({
        path,
        fullPage: true,
        clip: { x: 0, y: 0, width: box.width, height: Math.ceil(box.bottom) + 24 },
      });
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
