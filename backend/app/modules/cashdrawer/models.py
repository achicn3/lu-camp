"""cashdrawer 模型：現金抽屜班別與現金異動。

cash_session 以 partial unique index 確保同一 store 至多一個 OPEN（靠約束擋，非先查再開）。
金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.enums import CashMovementType, CashSessionStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class CashSession(Base):
    """現金抽屜班別。同一 store 同時只允許一個 OPEN。"""

    __tablename__ = "cash_sessions"
    __table_args__ = (
        Index(
            "uq_one_open_cash_session_per_store",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    opened_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opening_float: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    status: Mapped[CashSessionStatus] = mapped_column(
        _enum_col(CashSessionStatus),
        default=CashSessionStatus.OPEN,
        server_default=CashSessionStatus.OPEN.value,
    )
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))


class CashMovement(Base):
    """現金異動（append-only 帳；無 updated_at）。"""

    __tablename__ = "cash_movements"
    __table_args__ = (
        Index(
            "uq_cash_movements_store_idempotency_key",
            "store_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("amount <> 0", name="ck_cash_movement_amount_nonzero"),
        CheckConstraint(
            "type = 'MANUAL_ADJUST' OR amount > 0",
            name="ck_cash_movement_system_amount_positive",
        ),
        CheckConstraint(
            "(idempotency_key IS NULL) = (idempotency_fingerprint IS NULL)",
            name="ck_cash_movement_idempotency_pair",
        ),
        CheckConstraint(
            "type <> 'MANUAL_ADJUST' OR"
            " (note IS NOT NULL AND btrim(note) <> ''"
            " AND idempotency_key IS NOT NULL"
            " AND char_length(idempotency_fingerprint) = 64)",
            name="ck_cash_movement_manual_fields",
        ),
        CheckConstraint(
            "type = 'MANUAL_ADJUST' OR"
            " (note IS NULL AND idempotency_key IS NULL AND idempotency_fingerprint IS NULL)",
            name="ck_cash_movement_system_fields",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    type: Mapped[CashMovementType] = mapped_column(_enum_col(CashMovementType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    ref_type: Mapped[str | None] = mapped_column(String(50))
    ref_id: Mapped[int | None] = mapped_column()
    # 手動調整事由（留痕，CLAUDE.md §5）；系統產生的異動為 NULL。
    note: Mapped[str | None] = mapped_column(String(200))
    # 人工調整的 HTTP 重試身分；系統產生的 movement 不使用。指紋綁定 session/type/amount/note，
    # 同鍵不同內容必須衝突，不能把另一筆調整誤當回放。
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


CASH_MOVEMENT_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION cash_movement_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'cash_movements is insert-only: UPDATE/DELETE forbidden';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_cash_movement_immutable
BEFORE UPDATE OR DELETE ON cash_movements
FOR EACH ROW EXECUTE FUNCTION cash_movement_immutable()
""",
)

CASH_MOVEMENT_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_cash_movement_immutable ON cash_movements",
    "DROP FUNCTION IF EXISTS cash_movement_immutable()",
)

for _ddl in CASH_MOVEMENT_IMMUTABLE_DDL:
    event.listen(CashMovement.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
