"""sales 模型：銷售單與明細（docs/03）。

每張表帶 store_id。金額 NUMERIC(scale 0) → Decimal（NT$ 整數元）：subtotal=未稅、tax=稅額、
total=含稅總額（= Σ 明細 line_total）。invoice_id 待 T13（einvoice）建 invoices 表後再加 FK。
列舉以 native_enum=False + CHECK 儲存。
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.modules.storecredit.models import StoreCreditLedger
from app.shared.enums import (
    LinePayStatus,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    TenderType,
)


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Sale(Base, TimestampMixin):
    """銷售單。建立時即 COMPLETED；本階段一律 invoice_status=NOT_ISSUED（開票於 T13）。

    idempotency_key：由結帳端產生，(store_id, idempotency_key) 唯一，防網路重試重複建單/收錢
    （D-2）。同一 key 重送回原單、不重跑副作用。NULL 不受唯一限制（領域層直接呼叫可不帶）。
    idempotency_fingerprint：購物車內容的 sha256；重播時比對，同 key 但內容不同 → 拒絕（避免
    誤用/重用 key 把不同購物車的結帳靜默丟掉）。
    """

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("store_id", "idempotency_key", name="uq_sales_store_idempotency_key"),
        # 複合租戶鍵：供 sale_tenders 的 (sale_id, store_id) 複合 FK 綁定（SC-3 P2）。
        UniqueConstraint("id", "store_id", name="uq_sales_id_store"),
        # 一份購物金扣抵簽署至多綁一筆銷售（docs/23 K5，D3 單次使用）；顯式命名供 IntegrityError
        # 轉衝突（同 K4 acquisition）。
        UniqueConstraint("signature_task_id", name="uq_sales_signature_task"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    buyer_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    # 購物金扣抵手持簽署（docs/23 K5，D3）：以購物金付款時綁定的已簽 STORE_CREDIT_USE 任務。
    signature_task_id: Mapped[int | None] = mapped_column(ForeignKey("signature_tasks.id"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 結帳時實際累積的會員點數（docs/16 §0）；void 以此沖回、不重算（歷史單為 0）。
    awarded_points: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    payment_method: Mapped[PaymentMethod] = mapped_column(
        _enum_col(PaymentMethod),
        default=PaymentMethod.CASH,
        server_default=PaymentMethod.CASH.value,
    )
    invoice_status: Mapped[SaleInvoiceStatus] = mapped_column(
        _enum_col(SaleInvoiceStatus),
        default=SaleInvoiceStatus.NOT_ISSUED,
        server_default=SaleInvoiceStatus.NOT_ISSUED.value,
    )
    status: Mapped[SaleStatus] = mapped_column(
        _enum_col(SaleStatus),
        default=SaleStatus.COMPLETED,
        server_default=SaleStatus.COMPLETED.value,
    )


class SaleLine(Base, TimestampMixin):
    """銷售明細行。依 line_type 指向 serialized / catalog / bulk_lot 其一。"""

    __tablename__ = "sale_lines"
    __table_args__ = (
        # 複合租戶鍵：供 return_lines 的 (sale_line_id, store_id) 複合 FK 綁定（退貨租戶完整性）。
        UniqueConstraint("id", "store_id", name="uq_sale_lines_id_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    line_type: Mapped[SaleLineType] = mapped_column(_enum_col(SaleLineType))
    serialized_item_id: Mapped[int | None] = mapped_column(ForeignKey("serialized_items.id"))
    catalog_product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_products.id"))
    bulk_lot_id: Mapped[int | None] = mapped_column(ForeignKey("bulk_lots.id"))
    menu_item_id: Mapped[int | None] = mapped_column(ForeignKey("menu_items.id"))
    description: Mapped[str] = mapped_column(String(150))
    qty: Mapped[int] = mapped_column()
    # unit_price/line_total 為**實際成交（折後）**值——退貨退實付、報表認實收皆以此為準。
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 門市活動折扣留痕（docs/21 C2）：original_unit_price 為折前單價（無折扣→NULL）、
    # discount_amount 為該行折讓總額（=(原−折)×qty，預設 0）、campaign_id 指向套用的活動。
    original_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal(0), server_default=text("0")
    )
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))


class SaleTender(Base, TimestampMixin):
    """銷售收款明細（SC-3，docs/16 §1.6）。一筆 sale 一到多列，Σ amount = sales.total。

    CASH 走錢櫃 SALE_IN（現金部分）；STORE_CREDIT 走帳本 DEBIT（不碰現金，I-9）。
    每種 tender_type 一筆 sale 至多一列（與帳本「同 SALE 來源至多一筆 DEBIT」一致）。
    """

    __tablename__ = "sale_tenders"
    __table_args__ = (
        UniqueConstraint("sale_id", "tender_type", name="uq_sale_tenders_sale_type"),
        CheckConstraint("amount > 0", name="ck_sale_tenders_amount_positive"),
        # 複合租戶 FK（SC-3 P2）：收款明細必與其 sale 同店，擋跨店湊收款。
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_sale_tenders_sale_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    tender_type: Mapped[TenderType] = mapped_column(_enum_col(TenderType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 支付手續費（店家成本，docs/30）：行動支付（LINE Pay/台灣Pay）於結帳當下依 settings 費率
    # 快照 round_ntd(amount×pct/100)；現金/購物金為 0。不減 amount（客人付全額），另列為支出。
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal(0), server_default=text("0")
    )


class LinePayTransaction(Base, TimestampMixin):
    """LINE Pay 交易紀錄（docs/30）：對帳/退款/稽核。一筆 LINE_PAY 收款的銷售對應一列。

    order_id 由 (store, 冪等鍵) 確定性導出、唯一——重試恆同號、天然防重複扣款（先 check(order_id)）。
    transaction_id 為平台 64-bit 長整數，以字串保存（避免 JS/JSON Number 失真）。
    refunded_amount 累計退款、不得超過 amount（退貨/作廢反轉時累加）。raw_response 存 pay 原始回應
    （對帳存證）。status 見 LinePayStatus（正常路徑只 commit COMPLETE/REFUNDED）。
    """

    __tablename__ = "linepay_transactions"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_linepay_transactions_order_id"),
        UniqueConstraint("sale_id", name="uq_linepay_transactions_sale_id"),
        CheckConstraint("amount > 0", name="ck_linepay_transactions_amount_positive"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_linepay_transactions_refund_bounds",
        ),
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_linepay_transactions_sale_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    order_id: Mapped[str] = mapped_column(String(64))
    transaction_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[LinePayStatus] = mapped_column(_enum_col(LinePayStatus))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal(0), server_default=text("0")
    )
    raw_response: Mapped[dict[str, object]] = mapped_column(JSONB)


# 收款守衛（Codex SC-3 P3＋第二輪 P1）。DEFERRABLE INITIALLY DEFERRED，於 COMMIT 時驗：
#  (A) 對平：Σ sale_tenders.amount 必須等於 sales.total（現金＋購物金須與總額對平）。
#  (B) 購物金 ↔ 帳本雙向綁定（負債級）：STORE_CREDIT 收款金額必須對應一筆等額、同店、
#      同買方的 store_credit_ledger DEBIT/SALE 分錄；反之 DEBIT/SALE 分錄也必須對應一筆
#      等額的 STORE_CREDIT 收款——擋「有收款無扣抵」「有扣抵無收款」「對象/金額錯置」。
# sales_verify_store_credit_consistency 為 (B) 的共用判斷，由收款側與帳本側 trigger 共呼。
SALE_TENDER_TOTAL_GUARD_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION sales_verify_tender_total(p_sale_id BIGINT) RETURNS void AS $$
DECLARE
  sale_total NUMERIC;
  tender_sum NUMERIC;
BEGIN
  SELECT total INTO sale_total FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;  -- sale 已不存在（如刪除）：交由 FK 處理
  END IF;
  -- 總額必為正（SC-3 第三輪 P3）：CHECK 不可 deferred、且建單先插 total=0 placeholder，
  -- 故以延遲守衛於 COMMIT 驗——與 service 零總額拒一致，擋 raw DML 建零元單。
  IF sale_total <= 0 THEN
    RAISE EXCEPTION '銷售總額必須大於 0';
  END IF;
  SELECT COALESCE(SUM(amount), 0) INTO tender_sum FROM sale_tenders WHERE sale_id = p_sale_id;
  IF tender_sum <> sale_total THEN
    RAISE EXCEPTION '收款明細加總必須等於銷售總額（sale_tenders 與 sales.total 不對平）';
  END IF;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE OR REPLACE FUNCTION sales_verify_store_credit_consistency(p_sale_id BIGINT)
RETURNS void AS $$
DECLARE
  sale_store INT;
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
  debit_abs NUMERIC;
  debit_contact INT;
BEGIN
  SELECT store_id, buyer_contact_id, invoice_status
    INTO sale_store, sale_buyer, sale_status
    FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;  -- sale 已不存在（如刪除）：交由 FK／帳本側守衛處理
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = p_sale_id AND tender_type = 'STORE_CREDIT';
  sc_tender := COALESCE(sc_tender, 0);
  SELECT -signed_amount, contact_id INTO debit_abs, debit_contact
    FROM store_credit_ledger
   WHERE store_id = sale_store AND source_type = 'SALE' AND entry_type = 'DEBIT'
     AND source_id = p_sale_id;
  debit_abs := COALESCE(debit_abs, 0);
  IF sc_tender <> debit_abs THEN
    RAISE EXCEPTION '購物金收款必須對應等額的帳本 SALE 扣抵（sale_tenders 與 ledger 不一致）';
  END IF;
  IF sc_tender > 0 AND debit_contact IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION '購物金扣抵對象必須為該銷售的買方';
  END IF;
  -- 已作廢且有購物金扣抵 → 必須有對應沖正（第三輪 P2：raw UPDATE 設 VOID 不可漏沖回）
  IF sc_tender > 0 AND sale_status = 'VOID' THEN
    PERFORM 1 FROM store_credit_ledger
     WHERE store_id = sale_store AND source_type = 'SALE_VOID' AND entry_type = 'REVERSAL'
       AND source_id = p_sale_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION '已作廢的購物金銷售必須有對應的沖正分錄（SALE_VOID）';
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE OR REPLACE FUNCTION sale_tenders_total_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM sales_verify_tender_total(OLD.sale_id);
    PERFORM sales_verify_store_credit_consistency(OLD.sale_id);
    RETURN OLD;
  END IF;
  PERFORM sales_verify_tender_total(NEW.sale_id);
  PERFORM sales_verify_store_credit_consistency(NEW.sale_id);
  -- 收款被搬到別的 sale（第四輪 P1）：原 sale 也要重驗，否則原單失衡卻無人查。
  IF TG_OP = 'UPDATE' AND OLD.sale_id IS DISTINCT FROM NEW.sale_id THEN
    PERFORM sales_verify_tender_total(OLD.sale_id);
    PERFORM sales_verify_store_credit_consistency(OLD.sale_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE OR REPLACE FUNCTION sales_tender_total_guard() RETURNS trigger AS $$
BEGIN
  PERFORM sales_verify_tender_total(NEW.id);
  PERFORM sales_verify_store_credit_consistency(NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE CONSTRAINT TRIGGER trg_sale_tenders_total
AFTER INSERT OR UPDATE OR DELETE ON sale_tenders
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sale_tenders_total_guard()
""",
    """
CREATE CONSTRAINT TRIGGER trg_sales_tender_total
AFTER INSERT OR UPDATE ON sales
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sales_tender_total_guard()
""",
)

SALE_TENDER_TOTAL_GUARD_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_sales_tender_total ON sales",
    "DROP TRIGGER IF EXISTS trg_sale_tenders_total ON sale_tenders",
    "DROP FUNCTION IF EXISTS sales_tender_total_guard()",
    "DROP FUNCTION IF EXISTS sale_tenders_total_guard()",
    "DROP FUNCTION IF EXISTS sales_verify_store_credit_consistency(BIGINT)",
    "DROP FUNCTION IF EXISTS sales_verify_tender_total(BIGINT)",
)

# 帳本側對等綁定：store_credit_ledger 的 DEBIT/SALE 必對應「同店、同買方、等額」的銷售與
# 購物金收款。自含判斷（用 NEW 的 store_id，第三輪 P1：擋跨店 source_id 借殼）；不重複定義
# 收款側的 consistency 函式（避免 CREATE OR REPLACE 互蓋、行為依建表順序而異）。
SALE_LEDGER_BACKING_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION sales_ledger_sale_debit_guard() RETURNS trigger AS $$
DECLARE
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
BEGIN
  -- SALE_VOID 沖正（第四輪 P1）：只能對應「已作廢」的同店銷售——擋 raw 在銷售仍生效時
  -- 沖回購物金（憑空回補餘額）。與收款側「VOID 必有沖正」合為雙向不變量。
  IF NEW.entry_type = 'REVERSAL' AND NEW.source_type = 'SALE_VOID' THEN
    SELECT invoice_status INTO sale_status
      FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
    IF NOT FOUND OR sale_status <> 'VOID' THEN
      RAISE EXCEPTION 'SALE_VOID 沖正只能對應已作廢的同店銷售';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.entry_type <> 'DEBIT' OR NEW.source_type <> 'SALE' THEN
    RETURN NEW;
  END IF;
  -- 必對應「與本扣抵同店」的銷售（NEW.store_id），擋孤兒扣抵與跨店借殼 source_id
  SELECT buyer_contact_id INTO sale_buyer
    FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'SALE 扣抵必須對應同店的銷售（孤兒或跨店扣抵）';
  END IF;
  IF NEW.contact_id IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION 'SALE 扣抵對象必須為該銷售的買方';
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = NEW.source_id AND tender_type = 'STORE_CREDIT';
  IF COALESCE(sc_tender, 0) <> -NEW.signed_amount THEN
    RAISE EXCEPTION 'SALE 扣抵金額必須等於該銷售的購物金收款';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE CONSTRAINT TRIGGER trg_ledger_sale_debit_backing
AFTER INSERT OR UPDATE ON store_credit_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sales_ledger_sale_debit_guard()
""",
)

SALE_LEDGER_BACKING_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_ledger_sale_debit_backing ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS sales_ledger_sale_debit_guard()",
)

for _ddl in SALE_TENDER_TOTAL_GUARD_DDL:
    # 掛 sale_tenders（FK 下游、後建）after_create：此時 sales 與 sale_tenders 皆已存在，
    # 共用函式（含 store-credit 一致性）與兩個 constraint trigger 可一次安裝完。
    event.listen(SaleTender.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]

for _ddl in SALE_LEDGER_BACKING_DDL:
    # 帳本側對等 trigger 掛 store_credit_ledger after_create；共用判斷函式以 CREATE OR
    # REPLACE 再建一次（與收款側同名同義，重覆無害；plpgsql 對 sales/sale_tenders 的引用
    # 執行期才解析，不受建表順序影響）。
    event.listen(StoreCreditLedger.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
