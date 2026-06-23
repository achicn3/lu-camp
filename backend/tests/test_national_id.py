"""中華民國身分證字號檢核（is_valid_national_id）單元測試。

規則：1 大寫英文字母 + 1 性別碼(1/2) + 8 數字，共 10 碼，且加權檢核碼 mod 10 == 0。
驗證為純函式、不記錄輸入值（PII）。
"""

from app.core.national_id import is_valid_national_id


def test_canonical_valid_id() -> None:
    # A123456789 為經典合法測試碼（檢核和 130，整除 10）。
    assert is_valid_national_id("A123456789") is True


def test_more_valid_ids() -> None:
    # 其他合法碼（B 男性、A 女性 2 開頭；檢核碼皆已驗算）。
    assert is_valid_national_id("B123456780") is True
    assert is_valid_national_id("A223456781") is True


def test_bad_check_digit_rejected() -> None:
    # 末碼錯一位 → 檢核和不整除。
    assert is_valid_national_id("A123456788") is False


def test_wrong_length_rejected() -> None:
    assert is_valid_national_id("A12345678") is False
    assert is_valid_national_id("A1234567890") is False
    assert is_valid_national_id("") is False


def test_gender_digit_must_be_1_or_2() -> None:
    # 第二碼（性別碼）只能是 1 或 2。
    assert is_valid_national_id("A323456789") is False
    assert is_valid_national_id("A923456780") is False


def test_lowercase_letter_rejected() -> None:
    assert is_valid_national_id("a123456789") is False


def test_non_alpha_first_char_rejected() -> None:
    assert is_valid_national_id("1123456789") is False


def test_whitespace_is_trimmed() -> None:
    assert is_valid_national_id("  A123456789  ") is True


def test_embedded_non_digit_rejected() -> None:
    assert is_valid_national_id("A12345678X") is False
