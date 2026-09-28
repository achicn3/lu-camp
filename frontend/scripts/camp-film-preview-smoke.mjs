import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';
const shots = process.env.SMOKE_SHOTS ?? '/tmp/camp-film-preview-shots';
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.env.SMOKE_BASE ?? 'http://localhost:5200');
  await page.waitForFunction(() => window.ready);
  const frames = [];
  for (const time of [3, 8, 18, 25, 32, 38, 47]) {
    frames.push(await page.evaluate(time => {
      window.ctrl.pause(); window.ctrl.seek(time);
      return document.querySelector('canvas').toDataURL();
    }, time));
    await page.screenshot({ path: `${shots}/story-${time}.png` });
  }
  assert.equal(new Set(frames).size, frames.length, 'chapters render different frames');
  for (const [mode, effect] of [['paid', 'paid'], ['celebrate', 'signed']]) {
    await page.click(`[data-mode="${mode}"]`);
    await page.waitForFunction(effect => window.ctrl.snapshot().effect?.kind === effect, effect);
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${shots}/${mode}.png` });
  }
  await page.click('#add');
  assert.equal(await page.evaluate(() => window.ctrl.snapshot().effect?.kind), 'item');
  await page.click('[data-mode="hidden"]');
  const paused = await page.evaluate(() => window.ctrl.snapshot().time);
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => window.ctrl.snapshot().time), paused);
  await page.goto((process.env.SMOKE_BASE ?? 'http://localhost:5200') + '?reduced');
  await page.waitForFunction(() => window.ready);
  const still = await page.locator('canvas').screenshot();
  await page.waitForTimeout(400);
  assert.deepEqual(await page.locator('canvas').screenshot(), still);
  assert.deepEqual(errors, []);
  console.log(`PASS: 7 story frames, distinct completion effects, item, hidden, reduced motion; ${shots}`);
} finally { await browser.close(); }
