"""手機號碼正規化與驗證（2026-09-16 裁示：只收 09 開頭 10 碼）。

為什麼要正規化：同一支手機寫成 `0912345678` 與 `0912-345-678` 會被「同店電話唯一」
當成兩個不同號碼，於是同一個人被建成兩筆——之後查得到兩個他，點數與收購紀錄各分一半。
"""

import pytest

from app.core.phone import InvalidPhone, normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0912345678", "0912345678"),
        ("0912-345-678", "0912345678"),  # 連字號
        ("0912 345 678", "0912345678"),  # 空格
        (" 0912345678 ", "0912345678"),  # 前後空白
        ("０９１２３４５６７８", "0912345678"),  # 全形數字（從 Excel/LINE 複製常見）
        ("0912–345–678", "0912345678"),  # 連接號（en dash，複製貼上常見）
    ],
)
def test_normalize_accepts_common_writings_of_the_same_number(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "0911",  # 太短
        "09123456789",  # 太長
        "0812345678",  # 不是 09 開頭
        "02-1234-5678",  # 市話（門市自己的電話不走這條，聯絡人只收手機）
        "0912abc678",  # 含英文
        "+886912345678",  # 國際格式，本店不收
    ],
)
def test_normalize_rejects_anything_that_is_not_a_taiwan_mobile(raw: str) -> None:
    with pytest.raises(InvalidPhone):
        normalize_phone(raw)


def test_error_message_tells_the_clerk_what_to_type() -> None:
    with pytest.raises(InvalidPhone, match="09"):
        normalize_phone("123")
