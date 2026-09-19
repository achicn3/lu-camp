// 讓煙霧腳本不被「開店前檢查」的每日自動導向帶走（ADR-022）。
//
// 那個導向是**這台裝置**每天第一次開系統時跳一次，記在 localStorage。
// 煙霧每次都開全新的瀏覽器 context ⇒ 每次都算「今天第一次」⇒ 登入後隨便點一個連結
// 都可能在半路被導到 /opening-check，`waitForURL` 就會逾時。
//
// 這不是產品 bug（店員只會被帶一次、而且那正是設計目的），而是煙霧環境與真實使用
// 習慣的落差。所以在**導覽之前**先把「今天已經導過」的旗標種進去。
//
// 用法（一定要在第一次 page.goto 之前呼叫）：
//     import { skipOpeningCheckRedirect } from "./_opening-check.mjs";
//     await skipOpeningCheckRedirect(page, BASE);
import { taipeiDateForScript } from "./_taipei-date.mjs";

/** 營業日以店面時區計；跨午夜時前後一天都種，免得剛好卡在換日。 */
function marksFor(now = new Date()) {
  const day = 24 * 60 * 60 * 1000;
  return [
    taipeiDateForScript(new Date(now.getTime() - day)),
    taipeiDateForScript(now),
    taipeiDateForScript(new Date(now.getTime() + day)),
  ].map((date) => `lu-camp.opening-check.${date}`);
}

/**
 * 種下「今天已經導過開店前檢查」的旗標。
 *
 * 用 `addInitScript` 而不是 `page.evaluate`：旗標必須在頁面 script 跑起來**之前**就存在，
 * 事後再寫已經來不及——導向在 layout 掛載時就決定了。
 */
export async function skipOpeningCheckRedirect(page) {
  await page.addInitScript((keys) => {
    try {
      for (const key of keys) window.localStorage.setItem(key, "1");
    } catch {
      // 無痕／封鎖 site data：導向本來就不會發生（讀不到 localStorage 時選擇不導向），
      // 所以吞掉即可，不必讓煙霧因此失敗。
    }
  }, marksFor());
}
