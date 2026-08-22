"""sales 模型：銷售單與明細（docs/03）。

每張表帶 store_id。金額 NUMERIC(scale 0) → Decimal（NT$ 整數元）：subtotal=未稅、tax=稅額、
total=含稅總額（= Σ 明細 line_total）。invoice_id 待 T13（einvoice）建 invoices 表後再加 FK。
列舉以 native_enum=False + CHECK 儲存。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
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
    AdjustmentScope,
    AdjustmentType,
    CalculationMethod,
    LinePayRefundStatus,
    LinePayStatus,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineKind,
    SaleLineType,
    SaleStatus,
    ServiceMode,
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
        # 內用/外帶與桌號自洽（docs/35）。三種合法組合逐一明寫，且**每個比較都必須 NULL-safe**
        # ——Postgres 的 CHECK 只在結果為 false 時拒絕，NULL 一律放行。
        # 寫成 `service_mode = 'DINE_IN'` 不行：service_mode 為 NULL 時該項是 NULL，
        # 整條 `NULL OR false OR false` 也是 NULL，於是 (NULL, 'A1') 這種髒列照樣進得來
        # （回歸測試：test_db_check_rejects_inconsistent_service_mode[None-A1]）。
        # `IS NOT DISTINCT FROM` 永遠回 true/false，才真的守得住。
        CheckConstraint(
            "(service_mode IS NOT DISTINCT FROM 'DINE_IN' AND table_no IS NOT NULL)"
            " OR (service_mode IS NOT DISTINCT FROM 'TAKEOUT' AND table_no IS NULL)"
            " OR (service_mode IS NULL AND table_no IS NULL)",
            name="ck_sales_service_mode_table_no",
        ),
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
    # 餐飲供應方式與桌號（docs/35）：純資訊欄位，不進入任何金額/稅/折扣/點數計算。
    # 無餐飲明細的銷售兩欄皆 NULL；DINE_IN 必有桌號、TAKEOUT 必無（見 __table_args__）。
    service_mode: Mapped[ServiceMode | None] = mapped_column(_enum_col(ServiceMode), nullable=True)
    # 桌號存**字串快照**而非指向設定清單：設定頁日後改掉桌號，歷史交易仍應顯示當時那一桌
    # （同「供應商名快照，不改寫歷史」的既有口徑）。
    table_no: Mapped[str | None] = mapped_column(String(20), nullable=True)


class SaleLine(Base, TimestampMixin):
    """銷售明細行。依 line_type 指向 serialized / catalog / bulk_lot 其一。"""

    __tablename__ = "sale_lines"
    __table_args__ = (
        # 複合租戶鍵：供 return_lines 的 (sale_line_id, store_id) 複合 FK 綁定（退貨租戶完整性）。
        UniqueConstraint("id", "store_id", name="uq_sale_lines_id_store"),
        # 贈品的定義就是實付 0 且不佔折扣欄位——靠應用層自律不夠，這裡是最後一道牆。
        # `original_unit_price` 記牌價（贈品原價價值 = 它 × qty），`discount_amount` 保持
        # 純活動折扣，兩者混用會讓活動報表把「送出去的東西」算成「打折」。
        CheckConstraint(
            "line_kind <> 'GIFT' OR ("
            " unit_price = 0 AND line_total = 0 AND net_amount = 0"
            " AND discount_amount = 0 AND manual_discount_amount = 0"
            " AND original_unit_price IS NOT NULL AND gift_reason_id IS NOT NULL)",
            name="ck_sale_lines_gift_shape",
        ),
        # 一般行：實付 = 活動折後金額 − 臨時折扣。三個數字不得各說各話。
        CheckConstraint(
            "line_kind <> 'NORMAL' OR net_amount = line_total - manual_discount_amount",
            name="ck_sale_lines_net_amount_consistent",
        ),
        # 折扣不得把一行折成負數（等於倒貼給客人）。
        CheckConstraint(
            "net_amount >= 0 AND manual_discount_amount >= 0",
            name="ck_sale_lines_amounts_nonneg",
        ),
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
    # 商業性質（一般銷售／贈品）——與 line_type（品項種類）正交，見 SaleLineKind。
    line_kind: Mapped[SaleLineKind] = mapped_column(
        _enum_col(SaleLineKind),
        default=SaleLineKind.NORMAL,
        server_default=SaleLineKind.NORMAL.value,
    )
    # 本行分攤到的**臨時折扣**（單品折扣＋整單折扣的分攤額）。與 campaign 的
    # discount_amount 分開存，否則活動報表會把手動折扣算成活動成效。
    manual_discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), default=Decimal(0), server_default=text("0")
    )
    # **本行實付**＝line_total − manual_discount_amount。退貨退款、發票品項一律以此為準；
    # line_total 維持既有語意（活動折後 = unit_price × qty），既有讀它的地方不受影響。
    # 未指定時預設為 line_total（＝無臨時折扣的情形）。這不是放水：一旦有折扣卻忘了扣，
    # ck_sale_lines_net_amount_consistent 會當場擋下。
    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 0),
        default=lambda ctx: ctx.get_current_parameters()["line_total"],
    )
    # 成交當下的成本（本行合計）。凍結於此，日後調整商品成本不會回頭改寫歷史毛利。
    # NULL＝無成本可知（餐飲、或未填成本的商品），報表沿用既有「成本未知」口徑。
    cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    gift_reason_id: Mapped[int | None] = mapped_column(ForeignKey("gift_reasons.id"))
    # 原因名稱快照：原因日後停用或改名，歷史單據仍看得到當初寫的是什麼
    # （沿用 purchase_orders.supplier_name 的既有慣例）。
    gift_reason_name: Mapped[str | None] = mapped_column(String(50))
    gift_note: Mapped[str | None] = mapped_column(String(200))
    # 因哪一行而贈（買 A 送 B）。純供追溯，不參與任何金額計算。
    parent_sale_line_id: Mapped[int | None] = mapped_column(ForeignKey("sale_lines.id"))


class GiftReason(Base, TimestampMixin):
    """贈品原因代碼（店家可管理）。

    **不實刪、只停用**（沿用 suppliers 的既有慣例）：歷史單據引用過的原因不能因為後台
    刪掉就消失；停用只是讓它不再出現在選單。單據另存 `gift_reason_name` 快照，
    改名也不回溯改寫歷史。
    """

    __tablename__ = "gift_reasons"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uq_gift_reasons_store_code"),
        UniqueConstraint("id", "store_id", name="uq_gift_reasons_id_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    requires_note: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))


class DiscountReason(Base, TimestampMixin):
    """臨時折扣原因代碼（店家可管理）。停用不實刪，理由同 GiftReason。"""

    __tablename__ = "discount_reasons"
    __table_args__ = (
        UniqueConstraint("store_id", "code", name="uq_discount_reasons_store_code"),
        UniqueConstraint("id", "store_id", name="uq_discount_reasons_id_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    requires_note: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))


class SaleAdjustment(Base, TimestampMixin):
    """一筆臨時折扣的**意圖與來歷**（金額結果則分攤到 sale_lines）。

    `requested_value` 是店員輸入的數字（固定金額或百分比），`applied_amount` 是系統實際
    套用的折扣金額——**報表一律用 applied_amount，不得事後重算**，否則商品價格變動後
    歷史折扣就會跟著漂。

    折扣紀錄**不可實刪，只能作廢**：作廢要留下誰、何時、為什麼，並重新計算訂單金額。
    """

    __tablename__ = "sale_adjustments"
    __table_args__ = (
        UniqueConstraint("id", "store_id", name="uq_sale_adjustments_id_store"),
        ForeignKeyConstraint(
            ["sale_id", "store_id"], ["sales.id", "sales.store_id"],
            name="fk_sale_adjustments_sale_store",
        ),
        # 單品折扣必指向某一行；整單折扣不得指定行（分攤結果另存於 allocations）。
        CheckConstraint(
            "(scope = 'ITEM' AND sale_line_id IS NOT NULL)"
            " OR (scope = 'ORDER' AND sale_line_id IS NULL)",
            name="ck_sale_adjustments_scope_shape",
        ),
        CheckConstraint("applied_amount >= 0", name="ck_sale_adjustments_applied_nonneg"),
        # 作廢的三個欄位同進同出，避免出現「作廢了但不知道是誰、為什麼」的半殘紀錄。
        CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)"
            " OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND void_reason IS NOT NULL)",
            name="ck_sale_adjustments_void_shape",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)
    sale_line_id: Mapped[int | None] = mapped_column(ForeignKey("sale_lines.id"))
    scope: Mapped[AdjustmentScope] = mapped_column(_enum_col(AdjustmentScope))
    adjustment_type: Mapped[AdjustmentType] = mapped_column(
        _enum_col(AdjustmentType),
        default=AdjustmentType.MANUAL_DISCOUNT,
        server_default=AdjustmentType.MANUAL_DISCOUNT.value,
    )
    calculation_method: Mapped[CalculationMethod] = mapped_column(_enum_col(CalculationMethod))
    requested_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    reason_id: Mapped[int | None] = mapped_column(ForeignKey("discount_reasons.id"))
    reason_name: Mapped[str | None] = mapped_column(String(50))
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(200))


class SaleAdjustmentAllocation(Base, TimestampMixin):
    """整單折扣分攤到各行的結果。

    **必須落盤**：退貨時要知道「這一行當初實際被折了多少」，不能依當下商品狀態重算——
    商品價格、活動、甚至商品本身都可能已經變了。
    """

    __tablename__ = "sale_adjustment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "adjustment_id", "sale_line_id", name="uq_sale_adjustment_alloc_pair"
        ),
        CheckConstraint("allocated_amount >= 0", name="ck_sale_adjustment_alloc_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    adjustment_id: Mapped[int] = mapped_column(ForeignKey("sale_adjustments.id"), index=True)
    sale_line_id: Mapped[int] = mapped_column(ForeignKey("sale_lines.id"), index=True)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))


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


class LinePayRefundAttempt(Base, TimestampMixin):
    """LINE Pay 退款嘗試的持久化對帳日誌（docs/30 finding #1：防重退）。

    **append-only、無外鍵**：以**獨立交易**提交，故能在主交易（退貨/作廢）回滾後仍存活——
    這是「呼叫平台 refund 後崩潰/回應遺失」時唯一能防重退的依據。refund_key 唯一：退貨＝
    `return:{冪等鍵}`、作廢＝`void:{sale_id}`（各只退一次）。重試前查此表：SUCCEEDED→跳過不重退；
    PENDING→結果未定 fail-closed 需人工對帳；FAILED→可安全重試。store_id/order_id 僅記錄（無 FK）。
    """

    __tablename__ = "linepay_refund_attempts"
    __table_args__ = (
        UniqueConstraint("refund_key", name="uq_linepay_refund_attempts_key"),
        CheckConstraint("amount > 0", name="ck_linepay_refund_attempts_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(index=True)  # 記錄用，無 FK（獨立交易不依賴父列已提交）
    refund_key: Mapped[str] = mapped_column(String(120))
    order_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    status: Mapped[LinePayRefundStatus] = mapped_column(_enum_col(LinePayRefundStatus))
    return_code: Mapped[str | None] = mapped_column(String(8))


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
  -- CHECK 不可 deferred、且建單先插 total=0 placeholder，故以延遲守衛於 COMMIT 驗。
  -- 負數一律拒。**零元單只允許整單贈品**（店主裁示：門市活動可能單獨送小物），
  -- 且此時不得有任何收款明細——沒有銷售額就沒有收款這回事。
  IF sale_total < 0 THEN
    RAISE EXCEPTION '銷售總額不可為負';
  END IF;
  SELECT COALESCE(SUM(amount), 0) INTO tender_sum FROM sale_tenders WHERE sale_id = p_sale_id;
  IF sale_total = 0 THEN
    IF tender_sum <> 0 THEN
      RAISE EXCEPTION '零元銷售不得有收款明細';
    END IF;
    -- 必須「有明細，且全部是贈品」。少了「有明細」這一半，一張沒有任何明細的
    -- 零元單就會被放行（raw DML 建空單）。
    IF NOT EXISTS (SELECT 1 FROM sale_lines WHERE sale_id = p_sale_id)
       OR EXISTS (
            SELECT 1 FROM sale_lines
            WHERE sale_id = p_sale_id AND line_kind <> 'GIFT'
          ) THEN
      RAISE EXCEPTION '零元銷售必須整單都是贈品（一般商品折到 0 元請改開贈品）';
    END IF;
    RETURN;
  END IF;
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
  SELECT store_id, buyer_contact_id, status
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
  -- 已作廢且有購物金扣抵 → 必須有對應沖正（第三輪 P2：raw UPDATE 設作廢不可漏沖回）。
  -- **判 sales.status，不判 invoice_status**（ADR-013／2026-08 金流稽核 P0-2）：後者是
  -- 發票的狀態。同月整筆退貨會作廢原發票（ADR-014）並把 invoice_status 寫成 VOID，但退貨
  -- 回補購物金走的是 REFUND/SALE_RETURN、本就沒有 SALE_VOID 沖正——用 invoice_status 判會
  -- 把那次回寫整筆擋掉，連平台回執事件都一起回滾。
  IF sc_tender > 0 AND sale_status = 'VOIDED' THEN
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
    -- **判 sales.status，不判 invoice_status**（ADR-013／2026-08 金流稽核 P0-1）：
    -- 電子發票關閉時該筆交易根本沒有發票，invoice_status 恆為 NOT_ISSUED——用它判會讓
    -- 每一次合法的「作廢購物金銷售」都在 COMMIT 被擋，店員只能改用人工調整去湊。
    SELECT status INTO sale_status
      FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
    IF NOT FOUND OR sale_status <> 'VOIDED' THEN
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
