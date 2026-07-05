"""電子發票模型：本地發票紀錄、折讓、Turnkey 上傳佇列、回執事件（docs/14、docs/18 §7）。

每張表帶 `store_id`（多分店就緒）。金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元，§6）。
與 Turnkey 為檔案交換 + 回執輪詢：`einvoice_upload_queue` 為**持久外送佇列**（outbox），
每筆待送 XML 一列，狀態 `PENDING → UPLOADED/FAILED`（UploadStatus）；拋檔後記 xml_path
與 sha256（每筆交付都有 checksum）。核心不變量以 DB 約束守護：
- 一筆銷售至多一張發票（`uq_invoices_sale`）；
- 發票字軌號碼同店唯一（部分唯一索引，號碼配號 deferred → 允許 NULL）；
- 佇列列的 invoice_id / allowance_id 恰有其一（XOR）。

列舉存 VARCHAR + CHECK（native_enum=False），與既有模組一致。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceStatus,
    InvoiceType,
    UploadStatus,
)


def _enum_col(enum_cls: type) -> Enum:
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


class Invoice(Base, TimestampMixin):
    """一張已在本地開立的發票（對應一筆 sale）。

    `invoice_no`（字軌+號碼）配號流程 deferred（docs/18 §9 #7），故可為 NULL；一旦填入，
    同店唯一。B2C 買方統編於序列化時填 "0000000000"（docs/14 §2），DB 存 NULL 即可。
    `net + tax = total`、`total > 0` 由 CHECK 守護（§6 稅在總額層級推算一次）。
    """

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("sale_id", name="uq_invoices_sale"),
        # 供下游（allowances/queue）複合租戶 FK 指向。
        UniqueConstraint("id", "store_id", name="uq_invoices_id_store"),
        # 字軌號碼同店唯一（NULL 不受限：配號前允許多筆待配號）。
        Index(
            "uq_invoices_store_invoice_no",
            "store_id",
            "invoice_no",
            unique=True,
            postgresql_where=text("invoice_no IS NOT NULL"),
        ),
        # 複合租戶 FK：發票必與其銷售同店，擋跨店掛單。
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_invoices_sale_tenant",
        ),
        CheckConstraint("total > 0", name="ck_invoices_total_positive"),
        CheckConstraint("net >= 0 AND tax >= 0", name="ck_invoices_amounts_nonneg"),
        CheckConstraint("net + tax = total", name="ck_invoices_net_tax_total"),
        # 捐贈時必有捐贈碼（NPOBAN）；非捐贈時不得有捐贈碼。
        CheckConstraint(
            "(donate_mark = false AND npoban IS NULL)"
            " OR (donate_mark = true AND npoban IS NOT NULL)",
            name="ck_invoices_donate_npoban",
        ),
        # B2B 必有買方統編；B2C 買方統編應為空（序列化時填制式 0）。
        CheckConstraint(
            "(invoice_type = 'B2B' AND buyer_tax_id IS NOT NULL)"
            " OR (invoice_type = 'B2C' AND buyer_tax_id IS NULL)",
            name="ck_invoices_buyer_tax_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    invoice_type: Mapped[InvoiceType] = mapped_column(_enum_col(InvoiceType))
    invoice_no: Mapped[str | None] = mapped_column(String(16))  # 字軌+號碼；配號 deferred
    invoice_date: Mapped[date | None] = mapped_column(Date)  # 開立日；序列化以民國年輸出
    invoice_time: Mapped[str | None] = mapped_column(String(8))  # 開立時間 HH:MM:SS（F0401 必填）
    random_number: Mapped[str | None] = mapped_column(String(4))  # 防偽 4 位（deferred）
    buyer_tax_id: Mapped[str | None] = mapped_column(String(8))  # B2B 買方統編
    buyer_name: Mapped[str | None] = mapped_column(String(60))
    carrier_type: Mapped[str | None] = mapped_column(String(10))  # 載具類型（CarrierTypeEnum）
    # 載具號碼：MIG 4.0 起 CarrierId1/CarrierId2 長度由 64 調為 400。
    carrier_id: Mapped[str | None] = mapped_column(String(400))
    donate_mark: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    npoban: Mapped[str | None] = mapped_column(String(7))  # 捐贈碼 3–7 碼
    print_mark: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    net: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 未稅
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 稅額
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 含稅總額
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum_col(InvoiceStatus),
        default=InvoiceStatus.PENDING,
        server_default=InvoiceStatus.PENDING.value,
    )


class InvoiceAllowance(Base, TimestampMixin):
    """折讓單（退貨且原銷售已開票 → 產生 allowance 而非刪除發票，§7 不變量 5）。

    走 G0401（開立折讓）/G0501（作廢折讓）。`return_id` 連結退貨單（Phase 4B backend
    已存在）；為避免與 returns 模組緊耦合，此處不設 DB FK，僅存參照。
    """

    __tablename__ = "invoice_allowances"
    __table_args__ = (
        UniqueConstraint("id", "store_id", name="uq_invoice_allowances_id_store"),
        Index(
            "uq_invoice_allowances_store_no",
            "store_id",
            "allowance_no",
            unique=True,
            postgresql_where=text("allowance_no IS NOT NULL"),
        ),
        # 一張退貨單至多一張折讓（F6）：擋 raw/重呼造成同退貨重複折讓。NULL 不受限
        # （return_id 為選填參照；無退貨來源的折讓不套此保護）。
        Index(
            "uq_invoice_allowances_return",
            "store_id",
            "return_id",
            unique=True,
            postgresql_where=text("return_id IS NOT NULL"),
        ),
        # 複合租戶 FK：折讓必與其發票同店。
        ForeignKeyConstraint(
            ["invoice_id", "store_id"],
            ["invoices.id", "invoices.store_id"],
            name="fk_invoice_allowances_invoice_tenant",
        ),
        CheckConstraint("total > 0", name="ck_invoice_allowances_total_positive"),
        CheckConstraint("net >= 0 AND tax >= 0", name="ck_invoice_allowances_amounts_nonneg"),
        CheckConstraint("net + tax = total", name="ck_invoice_allowances_net_tax_total"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    return_id: Mapped[int | None] = mapped_column()  # 退貨單參照（無 FK，避免跨模組耦合）
    allowance_no: Mapped[str | None] = mapped_column(String(16))  # 折讓證明單號；配號 deferred
    net: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    voided: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class EInvoiceUploadQueue(Base, TimestampMixin):
    """Turnkey 上傳外送佇列（outbox）。每筆待送 XML 一列。

    狀態機（UploadStatus）：`PENDING`（待拋檔/待 Turnkey 上傳）→ `UPLOADED`（回執成功）/
    `FAILED`（回執失敗，可 retry 回 PENDING、attempts+1）。拋檔後記 `xml_path`+`xml_sha256`
    +`dropped_at`（每筆交付都有 checksum，docs/18 §7.3）。`invoice_id`/`allowance_id` 恰有
    其一（F-family 掛發票、G-family 掛折讓）。
    """

    __tablename__ = "einvoice_upload_queue"
    __table_args__ = (
        UniqueConstraint("id", "store_id", name="uq_einvoice_queue_id_store"),
        # 複合租戶 FK：佇列列與其發票/折讓同店。
        ForeignKeyConstraint(
            ["invoice_id", "store_id"],
            ["invoices.id", "invoices.store_id"],
            name="fk_einvoice_queue_invoice_tenant",
        ),
        ForeignKeyConstraint(
            ["allowance_id", "store_id"],
            ["invoice_allowances.id", "invoice_allowances.store_id"],
            name="fk_einvoice_queue_allowance_tenant",
        ),
        # 恰有一個目標（XOR）：發票類掛 invoice_id、折讓類掛 allowance_id。
        CheckConstraint(
            "(invoice_id IS NOT NULL) <> (allowance_id IS NOT NULL)",
            name="ck_einvoice_queue_target_xor",
        ),
        CheckConstraint("attempts >= 0", name="ck_einvoice_queue_attempts_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    action: Mapped[EInvoiceAction] = mapped_column(_enum_col(EInvoiceAction))
    message_type: Mapped[EInvoiceMessageType] = mapped_column(_enum_col(EInvoiceMessageType))
    invoice_id: Mapped[int | None] = mapped_column(index=True)
    allowance_id: Mapped[int | None] = mapped_column(index=True)
    status: Mapped[UploadStatus] = mapped_column(
        _enum_col(UploadStatus),
        default=UploadStatus.PENDING,
        server_default=UploadStatus.PENDING.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    xml_path: Mapped[str | None] = mapped_column(String(500))  # 落檔路徑（拋檔後）
    xml_sha256: Mapped[str | None] = mapped_column(String(64))  # 內容 checksum（拋檔後）
    dropped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))


class EInvoiceResultEvent(Base):
    """Turnkey 回執事件（ProcessResult/SummaryResult）落庫紀錄（docs/18 §7.3）。

    每筆佇列交付的最終結果或彙總對帳事件一列，供對帳與稽核（append-only，無 updated_at）。
    **自動解析 Turnkey 回執檔的 importer 待收尾階段依 3.9 手冊實作**（檔案命名/格式/錯誤碼）；
    此表與 `record_result` 讓平台結果可先被記錄（手動或 importer 皆寫此處）。
    """

    __tablename__ = "einvoice_result_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["queue_id", "store_id"],
            ["einvoice_upload_queue.id", "einvoice_upload_queue.store_id"],
            name="fk_einvoice_result_queue_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    queue_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    result_kind: Mapped[str] = mapped_column(String(20))  # 'PROCESS' / 'SUMMARY'
    status_code: Mapped[str | None] = mapped_column(String(20))  # 平台結果/錯誤碼
    message: Mapped[str | None] = mapped_column(String(500))
    source_ref: Mapped[str | None] = mapped_column(String(200))  # 回執檔名/log 參照
    # 回執所屬交付世代（＝拋檔檔名的 a{n}；retry 會遞增）。稽核可區分舊/新世代回執。
    delivery_attempt: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
