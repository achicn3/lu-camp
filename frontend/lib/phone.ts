// 手機號碼（聯絡人用）：只收台灣手機，09 開頭共 10 碼（裁示 2026-09-16）。
// 後端 `app/core/phone.py` 有同一套規則並會再驗一次——這裡是為了讓店員當場看到錯，
// 不用等送出被退。門市自己的市話（stores.phone）不走這裡。

export const PHONE_HINT = "手機號碼須為 09 開頭的 10 碼數字";

const MOBILE = /^09\d{8}$/;
// 看起來像分隔符的字元：半形/全形連字號、en/em dash、各種空白、括號。
const SEPARATORS = /[-－–—\s()　]/g;

/**
 * 去掉分隔符與全形數字後回傳標準寫法；不是合法手機就回 `null`。
 *
 * 刻意**不做半套修補**（例如自動補 0 或截斷多餘位數）：湊出來的號碼打不通，
 * 比當場擋下來更糟。
 */
export function normalizeMobile(raw: string): string | null {
  const collapsed = raw.normalize("NFKC").replace(SEPARATORS, "");
  return MOBILE.test(collapsed) ? collapsed : null;
}

export function isValidMobile(raw: string): boolean {
  return normalizeMobile(raw) !== null;
}

/** 這串文字看起來是在打電話號碼嗎（用來決定搜尋字要帶進姓名欄還是手機欄）。 */
export function looksLikePhone(raw: string): boolean {
  const collapsed = raw.normalize("NFKC").replace(SEPARATORS, "");
  return collapsed.length > 0 && /^\d+$/.test(collapsed);
}
