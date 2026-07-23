"""returns 模型：退貨主檔與明細。

第一版支援銷售退貨的 append-only 紀錄，副作用（退現、回補庫存、結算反轉）在 service
同一交易內完成；不刪除原 sale / sale_line。

租戶完整性（§4）以 DB 層複合 FK 守護（比照 sale_tenders 的 (sale_id, store_id) 綁定）：
退貨單與其銷售同店、退貨明細與其退貨單同店、退貨明細與其銷售明細同店——跨店引用在
DB 層即被擋下，不全靠 service。idempotency_key（(store_id, key) 唯一）防雙擊/網路重試
重複退貨重複退現（比照 sales D-2）。
"""

from decimal import Decimal

from sqlalchemy import (
    DDL,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.modules.storecredit.models import StoreCreditLedger
from app.shared.enums import TenderType


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class CustomerReturn(Base, TimestampMixin):
    """退貨主檔。created_at 即退貨時間；refund_amount 為本次退貨含稅退款總額。"""

    __tablename__ = "returns"
    __table_args__ = (
        # (store_id, idempotency_key) 唯一：同 key 重送只建一筆、回原單（防重複退現）。
        UniqueConstraint("store_id", "idempotency_key", name="uq_returns_store_idempotency_key"),
        # 複合租戶鍵：供 return_lines 的 (return_id, store_id) 複合 FK 綁定。
        UniqueConstraint("id", "store_id", name="uq_returns_id_store"),
        # 退貨單必與其銷售同店（DB 層擋跨店退貨）。
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_returns_sale_store",
        ),
        CheckConstraint("refund_amount > 0", name="ck_returns_refund_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    reason: Mapped[str] = mapped_column(String(500))
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["ReturnLine"]] = relationship(
        back_populates="customer_return",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnLine.id",
    )
    refund_tenders: Mapped[list["ReturnTender"]] = relationship(
        back_populates="customer_return",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnTender.id",
    )


class ReturnLine(Base):
    """退貨明細：指回原 sale_line，保留本次退回數量與金額。"""

    __tablename__ = "return_lines"
    __table_args__ = (
        # 退貨明細必與其退貨單同店。
        ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_lines_return_store",
        ),
        # 退貨明細必與其銷售明細同店（DB 層擋跨店引用 sale_line）。
        ForeignKeyConstraint(
            ["sale_line_id", "store_id"],
            ["sale_lines.id", "sale_lines.store_id"],
            name="fk_return_lines_sale_line_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    return_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    sale_line_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    qty: Mapped[int] = mapped_column()
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))

    customer_return: Mapped[CustomerReturn] = relationship(back_populates="lines")


class ReturnTender(Base, TimestampMixin):
    """本次退貨的實際退款去向；各渠道金額加總應等於退貨退款總額。"""

    __tablename__ = "return_tenders"
    __table_args__ = (
        UniqueConstraint("return_id", "tender_type", name="uq_return_tenders_return_type"),
        CheckConstraint("amount > 0", name="ck_return_tenders_amount_positive"),
        ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_tenders_return_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    return_id: Mapped[int] = mapped_column(index=True)
    tender_type: Mapped[TenderType] = mapped_column(_enum_col(TenderType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))

    customer_return: Mapped[CustomerReturn] = relationship(back_populates="refund_tenders")


# 退貨金額與退款渠道、購物金帳本的 deferred 雙向守衛。service 可在同一交易內依序建立
# return、明細、退款渠道與帳本；到 COMMIT 才要求完整對平，兼顧原子寫入與 DB 不變量。
RETURN_TENDER_GUARD_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION returns_verify_refund_consistency(p_return_id BIGINT)
RETURNS void AS $$
DECLARE
  return_store INT;
  return_sale BIGINT;
  return_total NUMERIC;
  line_sum NUMERIC;
  tender_sum NUMERIC;
  sc_tender NUMERIC;
  sc_ledger NUMERIC;
  sc_contact INT;
  sale_buyer INT;
BEGIN
  SELECT store_id, sale_id, refund_amount
    INTO return_store, return_sale, return_total
    FROM returns WHERE id = p_return_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  SELECT COALESCE(SUM(refund_amount), 0) INTO line_sum
    FROM return_lines WHERE return_id = p_return_id;
  SELECT COALESCE(SUM(amount), 0) INTO tender_sum
    FROM return_tenders WHERE return_id = p_return_id;
  IF line_sum <> return_total THEN
    RAISE EXCEPTION '退貨明細加總必須等於退貨退款總額';
  END IF;
  IF tender_sum <> return_total THEN
    RAISE EXCEPTION '退款渠道加總必須等於退貨退款總額';
  END IF;
  SELECT COALESCE(MAX(amount), 0) INTO sc_tender
    FROM return_tenders
   WHERE return_id = p_return_id AND tender_type = 'STORE_CREDIT';
  SELECT signed_amount, contact_id INTO sc_ledger, sc_contact
    FROM store_credit_ledger
   WHERE store_id = return_store AND source_type = 'SALE_RETURN'
     AND entry_type = 'REFUND' AND source_id = p_return_id;
  sc_ledger := COALESCE(sc_ledger, 0);
  IF sc_tender <> sc_ledger THEN
    RAISE EXCEPTION '購物金退款渠道必須對應等額的 SALE_RETURN 回補帳本';
  END IF;
  IF sc_tender > 0 THEN
    SELECT buyer_contact_id INTO sale_buyer
      FROM sales WHERE id = return_sale AND store_id = return_store;
    IF sc_contact IS DISTINCT FROM sale_buyer THEN
      RAISE EXCEPTION '購物金退貨回補對象必須為原銷售買方';
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE OR REPLACE FUNCTION returns_refund_delete_guard() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '退貨與退款渠道為稽核事實，不可刪除';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_returns_refund_delete_guard
BEFORE DELETE ON returns
FOR EACH ROW EXECUTE FUNCTION returns_refund_delete_guard()
""",
    """
CREATE OR REPLACE FUNCTION return_refund_consistency_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM returns_verify_refund_consistency(OLD.return_id);
    RETURN OLD;
  END IF;
  PERFORM returns_verify_refund_consistency(NEW.return_id);
  IF TG_OP = 'UPDATE' AND OLD.return_id IS DISTINCT FROM NEW.return_id THEN
    PERFORM returns_verify_refund_consistency(OLD.return_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE OR REPLACE FUNCTION returns_refund_consistency_guard() RETURNS trigger AS $$
BEGIN
  PERFORM returns_verify_refund_consistency(NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE CONSTRAINT TRIGGER trg_return_lines_refund_consistency
AFTER INSERT OR UPDATE OR DELETE ON return_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION return_refund_consistency_guard()
""",
    """
CREATE CONSTRAINT TRIGGER trg_return_tenders_refund_consistency
AFTER INSERT OR UPDATE OR DELETE ON return_tenders
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION return_refund_consistency_guard()
""",
    """
CREATE CONSTRAINT TRIGGER trg_returns_refund_consistency
AFTER INSERT OR UPDATE ON returns
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION returns_refund_consistency_guard()
""",
)

RETURN_LEDGER_GUARD_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION return_ledger_refund_guard() RETURNS trigger AS $$
DECLARE
  expected_store INT;
  expected_buyer INT;
  expected_amount NUMERIC;
BEGIN
  IF NEW.entry_type <> 'REFUND' OR NEW.source_type <> 'SALE_RETURN' THEN
    RETURN NEW;
  END IF;
  SELECT r.store_id, s.buyer_contact_id, rt.amount
    INTO expected_store, expected_buyer, expected_amount
    FROM returns r
    JOIN sales s ON s.id = r.sale_id AND s.store_id = r.store_id
    JOIN return_tenders rt ON rt.return_id = r.id AND rt.store_id = r.store_id
                           AND rt.tender_type = 'STORE_CREDIT'
   WHERE r.id = NEW.source_id AND r.store_id = NEW.store_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'SALE_RETURN 回補必須對應同店退貨與購物金退款渠道';
  END IF;
  IF NEW.store_id IS DISTINCT FROM expected_store
     OR NEW.contact_id IS DISTINCT FROM expected_buyer
     OR NEW.signed_amount IS DISTINCT FROM expected_amount THEN
    RAISE EXCEPTION 'SALE_RETURN 回補的店、會員與金額必須和退貨退款渠道一致';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE CONSTRAINT TRIGGER trg_ledger_return_refund_backing
AFTER INSERT OR UPDATE ON store_credit_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION return_ledger_refund_guard()
""",
)

RETURN_TENDER_GUARD_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_returns_refund_delete_guard ON returns",
    "DROP TRIGGER IF EXISTS trg_returns_refund_consistency ON returns",
    "DROP TRIGGER IF EXISTS trg_return_tenders_refund_consistency ON return_tenders",
    "DROP TRIGGER IF EXISTS trg_return_lines_refund_consistency ON return_lines",
    "DROP FUNCTION IF EXISTS returns_refund_consistency_guard()",
    "DROP FUNCTION IF EXISTS return_refund_consistency_guard()",
    "DROP FUNCTION IF EXISTS returns_refund_delete_guard()",
    "DROP FUNCTION IF EXISTS returns_verify_refund_consistency(BIGINT)",
)

RETURN_LEDGER_GUARD_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_ledger_return_refund_backing ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS return_ledger_refund_guard()",
)

for _ddl in RETURN_TENDER_GUARD_DDL:
    # return_lines 依賴 sale_lines，建表順序晚於只依賴 returns 的 return_tenders；掛在此處時
    # 三張退貨表皆已存在，constraint triggers 可安全建立。
    event.listen(ReturnLine.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]

for _ddl in RETURN_LEDGER_GUARD_DDL:
    event.listen(StoreCreditLedger.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
