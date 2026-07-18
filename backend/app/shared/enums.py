"""跨模組共用列舉。"""

from enum import StrEnum


class UserRole(StrEnum):
    """使用者角色。MANAGER 為管理者（可跨店/解密 PII），CLERK 為門市店員。

    KIOSK 為手持簽署裝置的專用身分（docs/23 D4）：登入一次長駐，**僅能**使用簽署端點
    （中央預設拒絕：get_current_user 直接擋 KIOSK，見 core/deps.py），碰不到任何店務資料。
    """

    MANAGER = "MANAGER"
    CLERK = "CLERK"
    KIOSK = "KIOSK"


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
    ACQUISITION_VOID_IN 作廢收購時退回原付現（進帳，落當前開帳 session；F6.5）；
    SALE_REFUND_OUT 銷售退貨退現（出帳，Phase 4B）。
    """

    SALE_IN = "SALE_IN"
    BUYOUT_OUT = "BUYOUT_OUT"
    CONSIGNMENT_PAYOUT_OUT = "CONSIGNMENT_PAYOUT_OUT"
    MANUAL_ADJUST = "MANUAL_ADJUST"
    ACQUISITION_VOID_IN = "ACQUISITION_VOID_IN"
    SALE_REFUND_OUT = "SALE_REFUND_OUT"


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


class PurchaseOrderStatus(StrEnum):
    """採購單狀態機。

    DRAFT ─送出→ ORDERED ─分批收貨→ PARTIAL ─收足→ RECEIVED；
    DRAFT/ORDERED ─取消→ CANCELLED（僅在尚未收任何貨時可取消）。
    """

    DRAFT = "DRAFT"
    ORDERED = "ORDERED"
    PARTIAL = "PARTIAL"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class StocktakeStatus(StrEnum):
    """盤點單狀態。建立即 DRAFT（已快照 system_qty）；確認調整後轉 CONFIRMED（僅一次）。"""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class SaleStatus(StrEnum):
    """銷售單狀態。RETURNED 由退貨流程（Phase 4）設定。"""

    COMPLETED = "COMPLETED"
    RETURNED = "RETURNED"


class SaleLineType(StrEnum):
    """銷售明細行的品項種類。"""

    SERIALIZED = "SERIALIZED"
    CATALOG = "CATALOG"
    BULK_LOT = "BULK_LOT"
    MENU = "MENU"  # 餐飲/內用菜單品項（現做、不扣庫存、不折活動、不可購物金折抵）


class PaymentMethod(StrEnum):
    """付款方式（sales.payment_method 摘要欄；明細在 sale_tenders，docs/16 §1.6）。

    單一 tender 時為該 tender 型別、多 tender 為 MIXED；既有報表/收據相容。
    """

    CASH = "CASH"
    STORE_CREDIT = "STORE_CREDIT"
    LINE_PAY = "LINE_PAY"
    TAIWAN_PAY = "TAIWAN_PAY"
    MIXED = "MIXED"


class TenderType(StrEnum):
    """銷售收款明細的單筆付款型別（sale_tenders.tender_type，docs/16 §1.6；docs/30）。

    CASH 現金（走錢櫃 SALE_IN）；STORE_CREDIT 購物金（走帳本 DEBIT，不碰現金）；
    LINE_PAY / TAIWAN_PAY 行動支付（**非現金、不進抽屜**，比照 STORE_CREDIT；店家扣手續費，
    fee 記於 sale_tenders.fee_amount 為店家成本）。
    """

    CASH = "CASH"
    STORE_CREDIT = "STORE_CREDIT"
    LINE_PAY = "LINE_PAY"
    TAIWAN_PAY = "TAIWAN_PAY"

    @property
    def is_cash(self) -> bool:
        """是否走實體現金抽屜（關帳應有現金只認 CASH；其餘皆非現金、另列）。"""
        return self is TenderType.CASH


class SaleInvoiceStatus(StrEnum):
    """銷售單的開票狀態（sales.invoice_status）。

    NOT_ISSUED：einvoice_enabled 關閉或尚未開票（銷售仍完整記錄，§6）；
    PENDING_ISSUE：已排入電子發票開立佇列、尚未取得平台核可字軌號碼（本地 outbox pending，
      非「已開立」——尚無 invoice_no/開立日/隨機碼）；
    ISSUED：平台已核可、發票正式開立；
    PENDING_ALLOWANCE：退貨已建 G0401 折讓、平台尚未核可（比照 ISSUE/VOID：等平台成功才轉正式態）；
    ALLOWANCE：G0401 平台核可、折讓成立；VOID：已作廢。
    """

    NOT_ISSUED = "NOT_ISSUED"
    PENDING_ISSUE = "PENDING_ISSUE"
    ISSUED = "ISSUED"
    PENDING_ALLOWANCE = "PENDING_ALLOWANCE"
    ALLOWANCE = "ALLOWANCE"
    VOID = "VOID"


class InvoiceType(StrEnum):
    """發票交易類型（本系統領域層；與 MIG InvoiceTypeEnum 07/08 為不同概念）。"""

    B2C = "B2C"
    B2B = "B2B"


class InvoiceStatus(StrEnum):
    """發票（invoices）本地紀錄狀態。

    PENDING：本地已建、排入上傳佇列，尚未取得平台核可（無字軌號碼——序列化/配號待 T13 收尾）；
    ISSUED：平台 ProcessResult 核可、正式開立；
    VOID_PENDING：已核可發票申請作廢、F0501 已排隊但平台尚未確認（本地不可先當正式作廢，
      否則 F0501 失敗時本地顯示作廢、平台仍有效）；VOID：F0501 平台核可後的正式作廢；
    ALLOWANCE：已折讓。佇列本身的上傳狀態另見 UploadStatus。
    """

    PENDING = "PENDING"
    ISSUED = "ISSUED"
    VOID_PENDING = "VOID_PENDING"
    VOID = "VOID"
    ALLOWANCE = "ALLOWANCE"


class UploadStatus(StrEnum):
    """電子發票上傳佇列狀態（einvoice_upload_queue.status）。

    PENDING 待拋檔/待平台核可；UPLOADED 平台 ProcessResult 核可；FAILED 平台退回（可 retry）；
    CANCELLED 中止（如發票在核可前即被作廢，其待送 F0401 明確終止、不再拋檔）。
    """

    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EInvoiceAction(StrEnum):
    """電子發票上傳佇列的動作類型（einvoice_upload_queue.action）。"""

    ISSUE = "ISSUE"
    VOID = "VOID"
    ALLOWANCE = "ALLOWANCE"


class LinePayStatus(StrEnum):
    """LINE Pay 交易狀態（linepay_transactions.status，docs/30）。

    COMPLETE：pay 授權+請款成功（0000）、銷售成立；FAILED：平台拒付（結帳整筆回滾，
    此列不落庫——僅供對帳時記錄，正常路徑不會 commit 出 FAILED）；REFUNDED：退貨/作廢已呼叫
    refund 成功反轉（refunded_amount 累計，不超過 amount）；VOIDED：授權未請款前作廢（少用，
    oneTimeKeys/pay 為同步請款、反轉一律走 refund）。
    """

    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"


class LinePayRefundStatus(StrEnum):
    """LINE Pay 退款嘗試的持久化狀態（linepay_refund_attempts；docs/30 finding #1）。

    PENDING：已寫入、即將/正在呼叫平台 refund——若崩潰/回應遺失，重試見此即知「結果未定」，
      不得盲目重退（fail-closed，須人工對帳）。SUCCEEDED：平台已退款（0000/1165）——重試見此
      即跳過、不重退。FAILED：平台明確拒退——可安全重試。此表為 append-only 對帳日誌、無外鍵，
      以獨立交易提交，故能跨主交易回滾存活（唯一防重退依據）。
    """

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class EInvoiceMessageType(StrEnum):
    """MIG 4.1 存證訊息類型（einvoice_upload_queue.message_type、拋檔目錄名）。

    本店為自建 Turnkey 存證營業人（docs/14 §0）：F0401 開立、F0501 作廢、F0701 註銷、
    G0401 開立折讓、G0501 作廢折讓。實際 XML 欄位/長度/Enum 待 T13 依官方 XSD 落地。
    """

    F0401 = "F0401"
    F0501 = "F0501"
    F0701 = "F0701"
    G0401 = "G0401"
    G0501 = "G0501"


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


class CampaignStatus(StrEnum):
    """門市活動狀態機（docs/21）。DRAFT→ACTIVE→ENDED；DRAFT/ACTIVE→CANCELLED。"""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class SignatureTaskKind(StrEnum):
    """手持簽署任務類型（docs/23）。"""

    ACQUISITION_AFFIDAVIT = "ACQUISITION_AFFIDAVIT"  # 收購切結書＋條款＋品項＋撥款選擇
    STORE_CREDIT_USE = "STORE_CREDIT_USE"  # 購物金扣抵確認
    TRANSACTION_ACK = "TRANSACTION_ACK"  # 交易紀錄簽收


class SignatureTaskStatus(StrEnum):
    """簽署任務狀態機：PENDING → SIGNED / CANCELLED。

    無 EXPIRED 自動過期（單店無排程；過時任務由店員作廢或被新任務取代——kiosk 只顯示最新）。
    """

    PENDING = "PENDING"
    SIGNED = "SIGNED"
    CANCELLED = "CANCELLED"
