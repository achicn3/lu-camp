// Read-only browser check against local POS/Postgres and published Worker menu.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";
import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
const SHOTS = process.env.SMOKE_SHOTS ?? "/tmp/online-quiz-label-shots";
mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch();
try {
  const pos = await browser.newPage();
  await skipOpeningCheckRedirect(pos);
  await pos.goto("http://localhost:3116/login");
  await pos.locator('input[name="username"]').fill("dev-manager");
  await pos.locator('input[name="password"]').fill(process.env.SMOKE_PASSWORD ?? "dev-test-123456");
  await pos.getByRole("button", { name: "登入", exact: true }).click();
  await pos.waitForURL(url => !url.pathname.startsWith("/login"));
  await pos.goto("http://localhost:3116/menu?section=online");
  await pos.getByRole("heading", { name: "不知道點什麼？（引導推薦）", exact: true }).waitFor();
  await pos.screenshot({ path: `${SHOTS}/pos-summary.png`, fullPage: true });
  await pos.goto("http://localhost:3116/menu/quiz");
  await pos.getByLabel("在客人頁顯示「不知道點什麼？」").waitFor();
  const guest = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await guest.goto("http://localhost:8794");
  await guest.getByRole("button", { name: "不知道點什麼？", exact: true }).click();
  await guest.getByRole("heading", { name: "不知道點什麼？", exact: true }).waitFor();
  assert.ok(await guest.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await guest.screenshot({ path: `${SHOTS}/guest-quiz.png` });
  console.log("PASS renamed quiz entry, heading, POS summary and setting; 390px no overflow");
} finally { await browser.close(); }
