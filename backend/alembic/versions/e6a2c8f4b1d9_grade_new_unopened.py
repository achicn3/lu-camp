"""成色新增 N（全新未拆）

2026-09-16 裁示：成色加一個「全新未拆」選項，排在最前面；序號品標籤右下角據此印「全新」
（其餘成色一律印「二手」）。收購買斷與寄售都能選，散裝批不適用（固定 E）。

列舉以 VARCHAR + CHECK 儲存，同一個 Grade 型別用在三張表，三張的 CHECK 都要重建：
serialized_items.grade、bulk_lots.grade、category_pricing_rules.condition_band。

另外每個分類是依成色各存一組收購定價參數（新分類由 service 自動種齊）。**既有分類只有
S–D 五組**，這裡替它們補一組 N，參數採預設值（已裁示「沿用現行預設」）——刻意不去抄 S 或 A
的值，那兩帶可能被店長調過，抄過來等於替全新未拆做了一個沒人決定過的定價。

Revision ID: e6a2c8f4b1d9
Revises: d1f4a6c8b3e7
Create Date: 2026-09-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6a2c8f4b1d9"
down_revision: str | Sequence[str] | None = "d1f4a6c8b3e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "grade"  # SQLAlchemy 以列舉型別名命名的 CHECK，三張表同名
# 與 `app.shared.enums.Grade` 同步（由 test_enum_check_constraint_sync 守衛）。
_OLD_GRADES = ("S", "A", "B", "C", "D", "E")
_GRADES = ("N", *_OLD_GRADES)
_TABLES = (
    ("serialized_items", "grade"),
    ("bulk_lots", "grade"),
    ("category_pricing_rules", "condition_band"),
)

# 與 `app.modules.inventory.pricing_defaults` 的預設值一致。migration 刻意寫死、不 import
# app 程式碼：app 的預設值日後可能再調，歷史 migration 的行為不該跟著變。
_DEFAULT_DISCOUNT_CEILING_PCT = 60
_DEFAULT_MIN_MARGIN_PCT = 40
_DEFAULT_MIN_PRICE_MULTIPLE = "2.00"


def _replace_checks(values: tuple[str, ...]) -> None:
    # 先結清延遲的約束觸發事件：同一個 `alembic upgrade head` 交易裡，前面的 migration 若寫過
    # 這些表，ALTER TABLE 會被 Postgres 以「has pending trigger events」拒絕——空庫測不到，
    # 有資料的舊庫升級（新機部署、還原舊備份）才會炸。見 a3c5e7f9b1d2 同段說明。
    op.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    allowed = ", ".join(f"'{v}'" for v in values)
    for table, column in _TABLES:
        op.drop_constraint(_CK, table, type_="check")
        op.create_check_constraint(_CK, table, sa.text(f"{column} IN ({allowed})"))
    # 還原 deferred 模式，否則後續 migration 會靜默失去延遲約束的保護。
    op.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))


def upgrade() -> None:
    """Upgrade schema."""
    _replace_checks(_GRADES)
    # 只補「已經有定價規則的分類」：分類一律經 service 建立並種齊成色帶，沒有任何規則的分類
    # 不在正常路徑上，單獨塞一組 N 反而讓它變成「只有全新未拆有規則」的怪形狀。
    # NOT EXISTS 是防禦性寫法：正常流程下升級前舊 CHECK 不允許 N，不會有既存的 N 帶；
    # 留著是為了萬一有人手動重跑這段 SQL 時不撞唯一約束 (store, category, band)。
    op.execute(
        sa.text(
            "INSERT INTO category_pricing_rules"
            " (store_id, category_id, condition_band,"
            "  discount_ceiling_pct, min_margin_pct, min_price_multiple)"
            " SELECT DISTINCT r.store_id, r.category_id, 'N', :ceiling, :margin,"
            "  CAST(:multiple AS NUMERIC(5, 2))"
            " FROM category_pricing_rules r"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM category_pricing_rules n"
            "   WHERE n.store_id = r.store_id AND n.category_id = r.category_id"
            "     AND n.condition_band = 'N'"
            " )"
        ).bindparams(
            ceiling=_DEFAULT_DISCOUNT_CEILING_PCT,
            margin=_DEFAULT_MIN_MARGIN_PCT,
            multiple=_DEFAULT_MIN_PRICE_MULTIPLE,
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    **還有全新未拆的序號品或散裝批時一律中止**：舊版 CHECK 容不下 N，而任何自動改寫
    （例如改成 S）都會讓那件商品的標籤從「全新」變「二手」，等於竄改商品資訊。
    請先人工把這些品項改成別的成色（或確定要保留就不要降版）。
    定價規則的 N 帶沒有商品資訊可遺失，直接刪除。
    """
    conn = op.get_bind()
    remaining = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM serialized_items WHERE grade = 'N')"
            " + (SELECT count(*) FROM bulk_lots WHERE grade = 'N')"
        )
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"資料庫中有 {remaining} 件成色為「全新未拆」（N）的品項；舊版無法表示這個成色，"
            "自動改成其他成色會讓標籤從「全新」變成「二手」。"
            "請先人工處置這些品項的成色後再降版。"
        )
    op.execute(sa.text("DELETE FROM category_pricing_rules WHERE condition_band = 'N'"))
    _replace_checks(_OLD_GRADES)
