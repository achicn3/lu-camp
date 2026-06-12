"""購物金（store credit）模型：不可變帳本＋帳戶快取（docs/16 §1、ADR-012）。

`store_credit_ledger` 為事實來源：INSERT only，應用層 repository 只提供新增，
DB trigger（見 `LEDGER_IMMUTABLE_DDL`，metadata 建表與 migration 共用同一定義）
直接拒絕 UPDATE/DELETE——雙保險。`store_credit_accounts` 為快取（餘額＋樂觀鎖
版本），同時是寫入序列化的鎖定錨點（SELECT … FOR UPDATE，沿 D-1 模式）；
餘額必須隨時可從帳本重算（I-3 對帳）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DDL,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import StoreCreditEntryType, StoreCreditSourceType


def _enum_col(enum_cls: type) -> Enum:
    # 存 VARCHAR + CHECK（無 PG ENUM 型別，downgrade 乾淨），與既有模組一致。
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


class StoreCreditLedger(Base):
    """購物金帳本分錄（append-only；無 updated_at——本表永不更新）。"""

    __tablename__ = "store_credit_ledger"
    __table_args__ = (
        # 冪等（I-5，沿 D-2）：同來源同類型只能有一筆；MANUAL 因 source_id NULL 不受限
        # （人工校正以 audit_log＋reason 留痕）。
        UniqueConstraint(
            "store_id",
            "source_type",
            "source_id",
            "entry_type",
            name="uq_store_credit_ledger_source",
        ),
        # 一列只能被沖正一次（adversarial review high）：不同 source 重複沖同一列
        # 會重複退/扣款。部分唯一索引（NULL 不受限）。
        Index(
            "uq_store_credit_ledger_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    entry_type: Mapped[StoreCreditEntryType] = mapped_column(_enum_col(StoreCreditEntryType))
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 非零；方向見 enum
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 滾動餘額，恆 >= 0
    cash_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))  # CREDIT 必填
    premium_rate_applied: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # CREDIT 必填
    source_type: Mapped[StoreCreditSourceType] = mapped_column(_enum_col(StoreCreditSourceType))
    source_id: Mapped[int | None] = mapped_column()
    reversal_of_id: Mapped[int | None] = mapped_column(ForeignKey("store_credit_ledger.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))  # 內容 sha256（冪等比對）
    reason: Mapped[str | None] = mapped_column(String(200))  # ADJUSTMENT 必填
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # G3 前恆 NULL
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StoreCreditAccount(Base, TimestampMixin):
    """帳戶快取（每店每 contact 一列）：餘額＋版本（樂觀鎖）；寫入鎖定錨點。"""

    __tablename__ = "store_credit_accounts"
    __table_args__ = (
        UniqueConstraint("store_id", "contact_id", name="uq_store_credit_accounts_contact"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 0), default=Decimal(0))
    version: Mapped[int] = mapped_column(default=0, server_default=text("0"))


# 帳本不可變 trigger（I-1 雙保險）：metadata 建表（測試）與 migration 共用同一定義，
# 避免兩處漂移。任何 UPDATE/DELETE 直接 RAISE。
# 每條一個語句（asyncpg prepared statement 不接受多語句）。
LEDGER_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION store_credit_ledger_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'store_credit_ledger 為 insert-only（ADR-012）：禁止 UPDATE/DELETE';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_ledger_immutable
BEFORE UPDATE OR DELETE ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_ledger_immutable()
""",
)

LEDGER_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_store_credit_ledger_immutable ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_ledger_immutable()",
)

for _ddl in LEDGER_IMMUTABLE_DDL:
    # sqlalchemy.DDL 無型別標註（第三方 stub 缺口），定向忽略、非弱化專案型別。
    event.listen(StoreCreditLedger.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
