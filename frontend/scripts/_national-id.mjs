// 產生合法的中華民國身分證字號（供煙霧/示範腳本建立 SELLER/會員用）。
// 後端 createContact 自 feat/contacts-member-validation 起會檢核格式+檢核碼，
// 故測試資料的 national_id 不可再用 `SMOKE-xxx` 之類的假值。
const LETTER_VALUES = {
  A: 10, B: 11, C: 12, D: 13, E: 14, F: 15, G: 16, H: 17, I: 34,
  J: 18, K: 19, L: 20, M: 21, N: 22, O: 35, P: 23, Q: 24, R: 25,
  S: 26, T: 27, U: 28, V: 29, W: 32, X: 30, Y: 31, Z: 33,
};
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

/** 產生唯一手機號碼（手機同店唯一、必填）；供煙霧/示範腳本建立聯絡人。 */
export function uniquePhone(seed = Date.now() + Math.floor(Math.random() * 1e6)) {
  const n = Math.abs(Math.trunc(seed)) % 100000000;
  return `09${String(n).padStart(8, "0")}`;
}

/** 由種子（預設 now+亂數）產生合法身分證字號；不同種子幾乎必不同，供去重建檔。 */
export function validNationalId(seed = Date.now() + Math.floor(Math.random() * 1e6)) {
  const n = Math.abs(Math.trunc(seed));
  const letter = LETTERS[n % 26];
  const gender = (n % 2) + 1; // 性別碼 1 或 2
  const body = String(n).padStart(7, "0").slice(-7); // 中間 7 碼
  const lv = LETTER_VALUES[letter];
  const nums = [Math.floor(lv / 10), lv % 10, gender, ...body.split("").map(Number)];
  const weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1];
  const sum = nums.reduce((acc, d, i) => acc + d * weights[i], 0);
  const check = (10 - (sum % 10)) % 10;
  return `${letter}${gender}${body}${check}`;
}
