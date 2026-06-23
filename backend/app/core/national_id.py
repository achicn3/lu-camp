"""中華民國身分證字號（national_id）格式與檢核碼驗證。

純函式、不記錄輸入值（PII，CLAUDE.md §5）。規則：
- 共 10 碼：1 大寫英文字母 + 1 性別碼（1 或 2）+ 8 位數字。
- 英文字母換算為兩位數（A=10、B=11 …），與其後 9 位數字依固定權重加權求和，
  總和能被 10 整除即合法。
"""

# 英文字母對應碼（內政部規則）。
_LETTER_VALUES: dict[str, int] = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16,
    "H": 17, "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22,
    "O": 35, "P": 23, "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28,
    "V": 29, "W": 32, "X": 30, "Y": 31, "Z": 33,
}

# 字母兩位數(1) + 兩位字母值權重(9) + 性別碼起算的 8 位(8..2) + 檢核碼(1)。
_WEIGHTS: tuple[int, ...] = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)


def is_valid_national_id(value: str) -> bool:
    """回傳身分證字號是否合法（格式 + 檢核碼）。空字串/格式錯誤一律 False。"""
    s = value.strip()
    if len(s) != 10:
        return False
    letter = s[0]
    if letter not in _LETTER_VALUES:
        return False
    digits = s[1:]
    if not digits.isdigit():
        return False
    if digits[0] not in ("1", "2"):  # 性別碼
        return False
    letter_value = _LETTER_VALUES[letter]
    numbers = [letter_value // 10, letter_value % 10, *(int(c) for c in digits)]
    total = sum(n * w for n, w in zip(numbers, _WEIGHTS, strict=True))
    return total % 10 == 0
