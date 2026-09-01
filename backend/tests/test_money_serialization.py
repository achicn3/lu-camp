"""金額序列化不得出現科學記號（CLAUDE.md §6）。

**這不是理論問題**：PostgreSQL 的 numeric 經 asyncpg 讀回來時，帶尾隨零的值會是
`Decimal('3E+4')` 而不是 `Decimal('30000')`，而 `str()` 會原樣輸出 `'3E+4'`。
前端的 `parseNtd` 讀不懂它 → 畫面顯示 `3E+4`、**條碼標籤印出 NT$0**
（一頂三萬的帳篷貼著 0 元標籤上架）。

報表模組早就踩過並修好（`format(value, "f")`），但其他模組沿用 `str(d)`——
本檔把「所有對外的金額欄位都必須是純十進位」釘成全域不變量。
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.core.money import format_ntd

# asyncpg 從 numeric 讀回來時真的會產生的形狀（實測 lucamp_manual 的 listed_price）。
SCIENTIFIC = [
    (Decimal("3E+4"), "30000"),
    (Decimal("2E+4"), "20000"),
    (Decimal("1.0E+5"), "100000"),
    (Decimal("1E+3"), "1000"),
    (Decimal("-3E+4"), "-30000"),
    (Decimal("0E+2"), "0"),
]


@pytest.mark.parametrize(("value", "expected"), SCIENTIFIC)
def test_scientific_decimals_render_as_plain_digits(value: Decimal, expected: str) -> None:
    assert format_ntd(value) == expected


@pytest.mark.parametrize("value", ["0", "1", "999999999999", "-1", "12345"])
def test_ordinary_values_are_unchanged(value: str) -> None:
    assert format_ntd(Decimal(value)) == value


def test_no_scientific_notation_survives_anywhere() -> None:
    """任何指數形式都不得殘留 E——前端一律以 `^-?\\d+$` 解析，帶 E 即整個判為無效。"""
    for exp in range(0, 12):
        assert "E" not in format_ntd(Decimal(f"7E+{exp}"))
        assert "e" not in format_ntd(Decimal(f"7E+{exp}"))


def test_no_module_defines_its_own_money_formatter() -> None:
    """所有金額序列化器都必須用 `app.core.money.format_ntd`，不得各自造一份。

    這條守衛是本次修正的重點：報表模組**兩年前就踩過並修好**同一個問題
    （`format(value, "f")`，註解寫得清清楚楚），但沒有任何東西要求其他模組跟進，
    於是另外 12 個模組繼續用 `str(d)`，直到三萬元的商品印出 NT$0 的標籤才被發現。
    修好一處而不釘住全域，等於把同一個 bug 留給未來的自己。
    """
    offenders: list[str] = []
    for path in sorted(Path("app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "PlainSerializer" not in source:
            continue
        # 這兩種寫法都會讓 Decimal('3E+4') 原樣輸出或另立一份實作。
        if "PlainSerializer(lambda d: str(d)" in source or 'format(d, "f")' in source:
            offenders.append(str(path))

    assert offenders == [], f"這些模組沒有使用 core.money.format_ntd：{offenders}"


def test_signing_canonical_form_is_representation_independent() -> None:
    """簽署內容的正規化形式不得受金額寫法影響。

    `"3E+4"` 與 `"30000"` 是同一筆錢。正規化若原樣沿用寫法，兩邊來源不同就比對不符——
    客人明明簽了對的金額卻被系統判成「與簽署內容不符，請重新簽」。這不是理論：
    交易總額直接從資料庫讀出來時就是 Decimal('3E+4')（實測 sales.total = 30000）。
    """
    from app.modules.signing.service import SigningService

    scientific = SigningService._canonical_affidavit_client_fields(
        SigningService, {"items": [{"name": "帳篷", "amount": "3E+4"}], "total": "3E+4"}
    )
    plain = SigningService._canonical_affidavit_client_fields(
        SigningService, {"items": [{"name": "帳篷", "amount": "30000"}], "total": "30000"}
    )

    assert scientific == plain
    assert scientific["total"] == "30000"
