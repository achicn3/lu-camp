// POS 條碼欄的輸入處理（店主 2026-10-01）。
//
// 網頁沒辦法替使用者切換作業系統的輸入法（瀏覽器不開放；舊的 CSS `ime-mode` 早已廢除），
// 所以改成：偵測到中文輸入法在吃掃碼時講清楚要切英文，全形英數則直接轉回半形。

/** 全形英數與符號（U+FF01–FF5E）轉半形；全形空白轉一般空白。輸入法開全形時掃到的碼會變這樣。 */
export function toHalfWidth(value: string): string {
  return value
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/　/g, " ");
}

// 注音符號、注音聲調、中日韓漢字：條碼裡不可能出現，出現就是中文輸入法把掃碼吃掉了。
const IME_CHARS_RE = /[㄀-ㄯㆠ-ㆿˇˊˋ˙一-鿿]/;

export function looksLikeImeInput(value: string): boolean {
  return IME_CHARS_RE.test(value);
}

const FOCUS_KEEPERS = new Set(["INPUT", "TEXTAREA", "SELECT", "BUTTON"]);

/**
 * 條碼欄解鎖時要不要把焦點拉回來。焦點在頁面本身（剛載入、或剛掃完欄位停用過）或導覽連結上
 * （從選單點進來）才拉；店員正在打別的欄位、或對話框按鈕上，就不搶。
 */
export function scanFocusFree(active: Element | null): boolean {
  if (active == null || active === document.body) return true;
  if (FOCUS_KEEPERS.has(active.tagName)) return false;
  return !(active instanceof HTMLElement && active.isContentEditable);
}
