"""T10 — 銷售/發票相關共用列舉（值須對齊 docs/03 資料模型）。

這些是本系統的**領域狀態列舉**，與電子發票 MIG 序列化用的代碼
（InvoiceTypeEnum 07/08、CarrierTypeEnum、DonateMarkEnum 等）無關——
後者於 T13 依當前 MIG 規格實作（見 docs/14），不在此定義。
"""

from app.shared.enums import (
    EInvoiceAction,
    InvoiceStatus,
    InvoiceType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    UploadStatus,
)


def test_sale_status_values() -> None:
    assert {s.value for s in SaleStatus} == {"COMPLETED", "RETURNED"}


def test_sale_line_type_values() -> None:
    assert {s.value for s in SaleLineType} == {"SERIALIZED", "CATALOG", "BULK_LOT", "MENU"}


def test_payment_method_values() -> None:
    # SC-3（docs/16 §1.6）：payment_method 摘要欄擴充 STORE_CREDIT / MIXED；
    # docs/30 行動支付擴充 LINE_PAY / TAIWAN_PAY。
    assert {s.value for s in PaymentMethod} == {
        "CASH",
        "STORE_CREDIT",
        "LINE_PAY",
        "TAIWAN_PAY",
        "MIXED",
    }


def test_sale_invoice_status_values() -> None:
    # sales.invoice_status：NOT_ISSUED（未開票）、PENDING_ISSUE（已排開立、待平台核可）、ISSUED、
    # PENDING_ALLOWANCE（已排 G0401 折讓、待平台核可）、ALLOWANCE、VOID。
    assert {s.value for s in SaleInvoiceStatus} == {
        "NOT_ISSUED",
        "PENDING_ISSUE",
        "ISSUED",
        "PENDING_ALLOWANCE",
        "ALLOWANCE",
        "VOID",
    }


def test_invoice_type_b2c_b2b() -> None:
    assert {s.value for s in InvoiceType} == {"B2C", "B2B"}


def test_invoice_status_values() -> None:
    # invoices.status：PENDING（待平台核可，無字軌號碼）→ ISSUED（ProcessResult 核可）；
    # 已核可發票作廢：ISSUED → VOID_PENDING（F0501 已排、平台未確認）→ VOID；ALLOWANCE 折讓。
    assert {s.value for s in InvoiceStatus} == {
        "PENDING",
        "ISSUED",
        "VOID_PENDING",
        "VOID",
        "ALLOWANCE",
    }


def test_upload_status_values() -> None:
    # CANCELLED：發票在平台核可前即被作廢，其待送 F0401 的明確終態（非殭屍 PENDING）。
    assert {s.value for s in UploadStatus} == {"PENDING", "UPLOADED", "FAILED", "CANCELLED"}


def test_einvoice_action_values() -> None:
    assert {s.value for s in EInvoiceAction} == {"ISSUE", "VOID", "ALLOWANCE"}
