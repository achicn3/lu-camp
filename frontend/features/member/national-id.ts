// 中華民國身分證字號檢核（與後端 app/core/national_id.py 同規則）：
// 10 碼＝1 大寫英文字母 + 性別碼(1/2) + 8 數字，加權檢核碼 mod 10 == 0。
// 用於建檔/編輯會員時的前端防呆（後端 InvalidNationalId 422 為最終把關）。

const LETTER_VALUES: Record<string, number> = {
  A: 10, B: 11, C: 12, D: 13, E: 14, F: 15, G: 16, H: 17, I: 34,
  J: 18, K: 19, L: 20, M: 21, N: 22, O: 35, P: 23, Q: 24, R: 25,
  S: 26, T: 27, U: 28, V: 29, W: 32, X: 30, Y: 31, Z: 33,
};

const WEIGHTS = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1];

/** 回傳身分證字號是否合法（格式 + 檢核碼）。空字串/格式錯誤一律 false。 */
export function isValidNationalId(value: string): boolean {
  const s = value.trim();
  if (s.length !== 10) return false;
  const letter = s[0];
  const letterValue = LETTER_VALUES[letter];
  if (letterValue === undefined) return false;
  const digits = s.slice(1);
  if (!/^\d{9}$/.test(digits)) return false;
  if (digits[0] !== "1" && digits[0] !== "2") return false; // 性別碼
  const numbers = [
    Math.floor(letterValue / 10),
    letterValue % 10,
    ...digits.split("").map(Number),
  ];
  const total = numbers.reduce((acc, n, i) => acc + n * WEIGHTS[i], 0);
  return total % 10 === 0;
}
