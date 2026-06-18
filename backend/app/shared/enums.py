"""跨模組共用列舉。"""

from enum import StrEnum


class UserRole(StrEnum):
    """使用者角色。MANAGER 為管理者（可跨店/解密 PII），CLERK 為門市店員。"""

    MANAGER = "MANAGER"
    CLERK = "CLERK"


class ContactRole(StrEnum):
    """聯絡人角色（統一主檔可同時具備多重角色）。"""

    MEMBER = "MEMBER"
    SELLER = "SELLER"
    CONSIGNOR = "CONSIGNOR"


class Grade(StrEnum):
    """成色分級。S-D 走序號單品（serialized_item），E 為散裝批（bulk_lot）。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class OwnershipType(StrEnum):
    """序號品擁有型態。OWNED=買斷，CONSIGNMENT=寄售。"""

    OWNED = "OWNED"
    CONSIGNMENT = "CONSIGNMENT"


class SerializedItemStatus(StrEnum):
    """序號品狀態機。"""

    IN_STOCK = "IN_STOCK"
    SOLD = "SOLD"
    RETURNED_TO_CONSIGNOR = "RETURNED_TO_CONSIGNOR"
    WRITTEN_OFF = "WRITTEN_OFF"


class BulkLotStatus(StrEnum):
    """散裝批狀態。"""

    ON_SALE = "ON_SALE"
    SOLD_OUT = "SOLD_OUT"
    WRITTEN_OFF = "WRITTEN_OFF"


class BulkAcquisitionBasis(StrEnum):
    """散裝批收購計價基礎。"""

    WEIGHT = "WEIGHT"
    BAG = "BAG"
    UNSPECIFIED = "UNSPECIFIED"


class CashSessionStatus(StrEnum):
    """現金抽屜班別狀態。"""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CashMovementType(StrEnum):
    """現金異動類型。

    SALE_IN 進帳；BUYOUT_OUT / CONSIGNMENT_PAYOUT_OUT 出帳；MANUAL_ADJUST 可正可負；
    ACQUISITION_VOID_IN 作廢收購時退回原付現（進帳，落當前開帳 session；F6.5）。
    """

    SALE_IN = "SALE_IN"
    BUYOUT_OUT = "BUYOUT_OUT"
    CONSIGNMENT_PAYOUT_OUT = "CONSIGNMENT_PAYOUT_OUT"
    MANUAL_ADJUST = "MANUAL_ADJUST"
    ACQUISITION_VOID_IN = "ACQUISITION_VOID_IN"


class AcquisitionType(StrEnum):
    """收購/寄售入庫單類型。

    BUYOUT 買斷（建序號品、付現）；CONSIGNMENT 寄售（建序號品、不付現）；
    BULK_LOT E 級散裝（建散裝批、付現）。
    """

    BUYOUT = "BUYOUT"
    CONSIGNMENT = "CONSIGNMENT"
    BULK_LOT = "BULK_LOT"


class ItemKind(StrEnum):
    """庫存品種類（stock_movement 用）。"""

    SERIALIZED = "SERIALIZED"
    CATALOG = "CATALOG"
    BULK_LOT = "BULK_LOT"


class StockDirection(StrEnum):
    """庫存異動方向。"""

    IN = "IN"
    OUT = "OUT"
    ADJUST = "ADJUST"


class StockReason(StrEnum):
    """庫存異動原因。"""

    ACQUISITION = "ACQUISITION"
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    CONSIGN_RETURN = "CONSIGN_RETURN"
    WRITE_OFF = "WRITE_OFF"
    STOCKTAKE = "STOCKTAKE"


class SaleStatus(StrEnum):
    """銷售單狀態。RETURNED 由退貨流程（Phase 4）設定。"""

    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"


class SaleLineType(StrEnum):
    """銷售明細行的品項種類。"""

    SERIALIZED = "SERIALIZED"
    CATALOG = "CATALOG"
    BULK_LOT = "BULK_LOT"


class PaymentMethod(StrEnum):
    """付款方式（sales.payment_method 摘要欄；明細在 sale_tenders，docs/16 §1.6）。

    單一 tender 時為該 tender 型別、多 tender 為 MIXED；既有報表/收據相容。
    """

    CASH = "CASH"
    STORE_CREDIT = "STORE_CREDIT"
    MIXED = "MIXED"


class TenderType(StrEnum):
    """銷售收款明細的單筆付款型別（sale_tenders.tender_type，docs/16 §1.6）。

    CASH 現金（走錢櫃 SALE_IN）；STORE_CREDIT 購物金（走帳本 DEBIT，不碰現金）。
    """

    CASH = "CASH"
    STORE_CREDIT = "STORE_CREDIT"


class SaleInvoiceStatus(StrEnum):
    """銷售單的開票狀態（sales.invoice_status）。

    NOT_ISSUED：einvoice_enabled 關閉或尚未開票（銷售仍完整記錄，§6）；
    ISSUED：已開立發票；VOID：已作廢；ALLOWANCE：已折讓。
    """

    NOT_ISSUED = "NOT_ISSUED"
    ISSUED = "ISSUED"
    VOID = "VOID"
    ALLOWANCE = "ALLOWANCE"


class InvoiceType(StrEnum):
    """發票交易類型（本系統領域層；與 MIG InvoiceTypeEnum 07/08 為不同概念）。"""

    B2C = "B2C"
    B2B = "B2B"


class InvoiceStatus(StrEnum):
    """發票（invoices）狀態。UPLOADED 表已上傳整合平台並取得回執。"""

    ISSUED = "ISSUED"
    UPLOADED = "UPLOADED"
    VOID = "VOID"
    ALLOWANCE = "ALLOWANCE"


class UploadStatus(StrEnum):
    """上傳佇列/發票上傳狀態（einvoice_upload_queue.status、invoice.upload_status）。"""

    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"


class EInvoiceAction(StrEnum):
    """電子發票上傳佇列的動作類型（einvoice_upload_queue.action）。"""

    ISSUE = "ISSUE"
    VOID = "VOID"
    ALLOWANCE = "ALLOWANCE"


class ConsignmentSettlementStatus(StrEnum):
    """寄售結算狀態。售出時建 PENDING；付款（Phase 4）轉 PAID；退貨反轉為 CANCELLED。"""

    PENDING = "PENDING"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class StoreCreditEntryType(StrEnum):
    """購物金帳本分錄類型（docs/16 §1.1、ADR-012）。

    CREDIT 收購入帳（+）；DEBIT 消費扣抵（−）；REVERSAL 沖正（方向與被沖正列相反）；
    ADJUSTMENT 人工校正（限 MANAGER、必填事由、寫稽核；可正可負）。
    """

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    REVERSAL = "REVERSAL"
    ADJUSTMENT = "ADJUSTMENT"


class StoreCreditSourceType(StrEnum):
    """購物金分錄來源（docs/16 §1.1；source_id 可追溯 acquisition / sale）。"""

    ACQUISITION = "ACQUISITION"
    SALE = "SALE"
    SALE_VOID = "SALE_VOID"
    ACQUISITION_ROLLBACK = "ACQUISITION_ROLLBACK"
    MANUAL = "MANUAL"


class PayoutMethod(StrEnum):
    """收購撥款方式（docs/16 §1.7）。CONSIGNMENT 不撥款、恆為 CASH 預設值。"""

    CASH = "CASH"
    STORE_CREDIT = "STORE_CREDIT"
    SPLIT = "SPLIT"
