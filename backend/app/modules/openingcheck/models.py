"""開店前檢查模型（2026-09-17 裁示）。

兩張表，刻意做小：
- `opening_check_items`：店主自訂的「今日確認事項」（補零錢、發票紙…）。刪除採封存，
  否則已經勾過的歷史紀錄會指向不存在的項目。
- `opening_checks`：**每店每日一列**的完成狀態。裁示要求任何一台裝置完成就算完成，
  所以狀態存後端而不是瀏覽器。已勾的自訂項目與今天略過的自動項目各存一個陣列——
  自動項目（開帳、各裝置）的通過與否是即時判定的，沒有落庫的必要，只有「略過」要記。
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class OpeningCheckItem(Base, TimestampMixin):
    """店主自訂的開店前確認事項（每店一份）。"""

    __tablename__ = "opening_check_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    # 「前往處理」要去哪（選填）：例如補零錢 → /cash。空白代表只是提醒、沒有頁面可去。
    href: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OpeningCheck(Base, TimestampMixin):
    """某店某營業日的檢查狀態（每店每日一列）。"""

    __tablename__ = "opening_checks"
    __table_args__ = (
        UniqueConstraint("store_id", "business_date", name="uq_opening_checks_store_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    # 營業日以店面時區計（core.time.store_date）：跨午夜營業時不能用 UTC 日期切。
    business_date: Mapped[date] = mapped_column(Date)
    # 已勾的自訂項目 id。Postgres 的陣列不能掛外鍵，所以讀取時一律與現存項目取交集：
    # 項目被刪掉之後，這裡的殘值不會讓今天永遠完成不了（有測試守）。
    done_item_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    # 今天略過的自動項目 key（`cash_session`、`device:<kind>:<id>`）。裁示：不必填原因。
    skipped_keys: Mapped[list[str]] = mapped_column(ARRAY(String(100)), server_default=text("'{}'"))
