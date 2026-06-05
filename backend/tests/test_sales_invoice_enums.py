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
    assert {s.value for s in SaleLineType} == {"SERIALIZED", "CATALOG", "BULK_LOT"}


def test_payment_method_cash_only() -> None:
    # docs/02 §1 約束：本期只收現金；列舉預留擴充但目前僅 CASH。
    assert {s.value for s in PaymentMethod} == {"CASH"}


def test_sale_invoice_status_values() -> None:
    # sales.invoice_status：含 NOT_ISSUED（開關關閉/未開票），不含 UPLOADED。
    assert {s.value for s in SaleInvoiceStatus} == {
        "ISSUED",
        "NOT_ISSUED",
        "VOID",
        "ALLOWANCE",
    }


def test_invoice_type_b2c_b2b() -> None:
    assert {s.value for s in InvoiceType} == {"B2C", "B2B"}


def test_invoice_status_values() -> None:
    # invoices.status：含 UPLOADED（已上傳平台），不含 NOT_ISSUED。
    assert {s.value for s in InvoiceStatus} == {
        "ISSUED",
        "UPLOADED",
        "VOID",
        "ALLOWANCE",
    }


def test_upload_status_values() -> None:
    assert {s.value for s in UploadStatus} == {"PENDING", "UPLOADED", "FAILED"}


def test_einvoice_action_values() -> None:
    assert {s.value for s in EInvoiceAction} == {"ISSUE", "VOID", "ALLOWANCE"}
