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
from typing import Any

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.modules.einvoice.platform_ids import (
    new_platform_allowance_number,
    new_platform_order_id,
)
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceIssueChannel,
    EInvoiceMessageType,
    InvoiceAllowanceSource,
    InvoiceStatus,
    InvoiceType,
    InvoiceVoidReason,
    UploadStatus,
)


def _enum_col(enum_cls: type) -> Enum:
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


# 發票作廢的稽核 action／entity：寫入端（service）與讀取端（repository 的月報查詢）共用，
# 只靠註解維繫的話，哪天改了字串，月報的紙本作廢那一段會靜默變空而且沒有測試會紅。
VOID_INVOICE_AUDIT_ACTION = "VOID_INVOICE"
INVOICE_AUDIT_ENTITY = "invoice"
# 撞號自動換 OrderId 的稽核 action（service 寫入、測試比對同一個常數）。
ROTATE_ORDER_ID_AUDIT_ACTION = "ROTATE_EINVOICE_ORDER_ID"


# 平台識別碼在**插入當下**產生（見 platform_ids）：放在欄位 default 而非只在 service 指派，
# 任何建立發票／折讓的路徑都不會漏掉，也不會有「先建列、事後才補編號」的空窗。
def _insert_params(context: DefaultExecutionContext) -> dict[str, Any]:
    # SQLAlchemy 2.0 的 get_current_parameters 本身沒有型別註記（回傳本列的插入參數 dict）。
    params: dict[str, Any] = context.get_current_parameters()  # type: ignore[no-untyped-call]
    return params


def _default_platform_order_id(context: DefaultExecutionContext) -> str:
    params = _insert_params(context)
    return new_platform_order_id(store_id=params["store_id"], sale_id=params["sale_id"])


def _default_platform_allowance_number(context: DefaultExecutionContext) -> str:
    return new_platform_allowance_number(store_id=_insert_params(context)["store_id"])


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
        # 作廢狀態與作廢原因必須一致：作廢（含作廢中）必有原因，未作廢不得有原因。
        # **與 migration f7a8b9c0d1e2 同名同條件**——測試庫由 metadata 建立，若只寫在
        # migration，測試會接受正式資料庫拒絕的資料（先前 signaturetaskkind 即因此漏網）。
        CheckConstraint(
            "(status IN ('VOID', 'VOID_PENDING')) = (void_reason IS NOT NULL)",
            name="ck_invoices_void_reason_matches_status",
        ),
        # 送 Amego 的 OrderId 同店唯一（平台端同賣方不可重複）。
        UniqueConstraint(
            "store_id", "platform_order_id", name="uq_invoices_store_platform_order_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    invoice_type: Mapped[InvoiceType] = mapped_column(_enum_col(InvoiceType))
    # 送 Amego 的 OrderId：建立時隨機產生並持久化，送出／對帳／補印一律讀這欄（platform_ids）。
    # 撞號時送出流程自動換新編號（EInvoiceService._rotate_order_id）。
    platform_order_id: Mapped[str] = mapped_column(String(40), default=_default_platform_order_id)
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
    # 證明聯**實際印出**的時間（None＝從未印出）。與 print_mark 無關——那是 MIG 的
    # 列印註記（紙本 vs 載具）。這裡記的是事實，用來決定下一次該印正本還是補印：
    # 要點 §26「證明聯以列印一次為限」，補印須加註「補印」二字且併同原聯才能兌獎。
    proof_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 作廢原因（僅作廢時有值）：SALE_VOID＝整筆銷售作廢、FULL_RETURN＝銷售有效但全退、
    # CORRECTION＝開立內容有誤重開。同樣是「作廢」，帳務意義不同，報表/稽核須能分辨。
    void_reason: Mapped[InvoiceVoidReason | None] = mapped_column(
        _enum_col(InvoiceVoidReason),
        nullable=True,
    )
    net: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 未稅
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 稅額
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 含稅總額
    # 結帳當下稅率快照（Codex 第九輪）：F0401 金額/TaxRate 以此計——結帳後改 settings
    # 稅率不得改變已落地發票的申報內容。
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0.05"))
    # Amego f0401 回傳的證明聯列印內容（docs/24）：一維條碼／左右 QR **內容字串**，
    # 列印以平台回傳為準。invoice_query 對帳復原的發票拿不到 → NULL（證明聯不可印）。
    barcode_text: Mapped[str | None] = mapped_column(String(40))
    qrcode_left: Mapped[str | None] = mapped_column(String(500))
    qrcode_right: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum_col(InvoiceStatus),
        default=InvoiceStatus.PENDING,
        server_default=InvoiceStatus.PENDING.value,
    )
    # 開立來源（docs/36）：MANUAL_PAPER＝手開紙本備用發票。**不另立 sale.invoice_status
    # 狀態**——下游到處以 invoice_status 分支，多一個「也算已開立」的值會要求每處都改，
    # 漏一處就錯；改以來源欄位區分，下游預設行為自動正確，只需在少數出口顯式擋下。
    issue_channel: Mapped[EInvoiceIssueChannel] = mapped_column(
        _enum_col(EInvoiceIssueChannel),
        default=EInvoiceIssueChannel.AMEGO,
        server_default=EInvoiceIssueChannel.AMEGO.value,
    )


class InvoiceAllowance(Base, TimestampMixin):
    """折讓單（退貨且原銷售已開票 → 產生 allowance 而非刪除發票，§7 不變量 5）。

    走 G0401（開立折讓）/G0501（作廢折讓）。`return_id` 連結退貨單（Phase 4B backend
    已存在）；為避免與 returns 模組緊耦合，此處不設 DB FK，僅存參照。

    `source=STORE_CREDIT`：混合付款的購物金部分，平台確認開立後自動開立，一張發票至多一張
    （ADR-029）。`void_requested_at`：整筆作廢時要求作廢這張折讓、G0401 結果還沒回來——
    等 G0401 收斂再決定送 G0501 或視為從未成立。`voided`＝已作廢（或從未在平台成立）。
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
        UniqueConstraint(
            "store_id", "platform_number", name="uq_invoice_allowances_store_platform_number"
        ),
        # 一張發票至多一張購物金折讓（ADR-029）：重送、重複回執都不會開第二張。
        Index(
            "uq_invoice_allowances_store_credit",
            "store_id",
            "invoice_id",
            unique=True,
            postgresql_where=text("source = 'STORE_CREDIT'"),
        ),
        # 購物金折讓不屬於任何退貨單。
        CheckConstraint(
            "source <> 'STORE_CREDIT' OR return_id IS NULL",
            name="ck_invoice_allowances_store_credit_no_return",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    source: Mapped[InvoiceAllowanceSource] = mapped_column(
        _enum_col(InvoiceAllowanceSource),
        default=InvoiceAllowanceSource.RETURN,
        server_default=InvoiceAllowanceSource.RETURN.value,
    )
    return_id: Mapped[int | None] = mapped_column()  # 退貨單參照（無 FK，避免跨模組耦合）
    allowance_no: Mapped[str | None] = mapped_column(String(16))  # 折讓證明單號；配號 deferred
    # 送 Amego 的折讓單號：建立時隨機產生並持久化（platform_ids）；核可後寫回 allowance_no。
    platform_number: Mapped[str] = mapped_column(
        String(16), default=_default_platform_allowance_number
    )
    net: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    voided: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    void_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # Amego 認領時凍結的 data JSON 全文（docs/24）：重送 byte-for-byte 用；Turnkey 路徑 NULL。
    amego_payload: Mapped[str | None] = mapped_column(Text)
    dropped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # **真的開始送出**的時點（docs/36）。與 dropped_at 不同：Amego 路徑的 dropped_at 是
    # 「認領/凍結 payload」時就寫、**先於**對帳查詢與實際 POST；若查詢因斷網失敗，
    # F0401 其實從未送出。posted_at 只在呼叫送出端點之前寫入，是「可能已到平台」的精確證據。
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # 回執成敗（權威結果，non-null）：status_code/message 皆選填，稽核軌跡必須能獨立
    # 證明平台回了成功或失敗（含重複/矛盾回執），不得只靠佇列狀態推論。
    success: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[str | None] = mapped_column(String(20))  # 平台結果/錯誤碼
    message: Mapped[str | None] = mapped_column(String(500))
    source_ref: Mapped[str | None] = mapped_column(String(200))  # 回執檔名/log 參照
    # 回執所屬交付世代（＝拋檔檔名的 a{n}；retry 會遞增）。稽核可區分舊/新世代回執。
    delivery_attempt: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
