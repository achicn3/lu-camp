"""列舉值與資料庫 CHECK 約束的同步守衛。

本專案的列舉以 **VARCHAR + CHECK** 儲存（`native_enum=False`）。測試資料庫由 models 直接
`create_all`，CHECK 會自動包含所有列舉值；**但實際部署的資料庫是靠 migration 演進的**，
新增列舉值若忘了在 migration 重建 CHECK，測試全綠、真環境卻寫不進去。

（此檔即為該疏漏的回歸測試：`RETURN_INVOICE_CONSENT` 曾只加在 enums.py，
真 DB 煙霧才在 INSERT 時被 CHECK 擋下。）
"""

from importlib import util
from pathlib import Path
from types import ModuleType

from app.shared.enums import (
    EInvoiceIssueChannel,
    SaleInvoiceStatus,
    SaleStatus,
    ServiceMode,
    SignatureTaskKind,
)

_VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"


def _load(filename: str) -> ModuleType:
    spec = util.spec_from_file_location(f"migration_{filename}", _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_signature_task_kind_check_lists_every_enum_value() -> None:
    migration = _load("a7b8c9d0e1f2_return_consent_allows_anonymous_buyer.py")
    assert set(migration._NEW_KINDS) == {k.value for k in SignatureTaskKind}


def test_sale_status_check_lists_every_enum_value() -> None:
    migration = _load("f4c5d6e7a8b9_split_sale_invoice_lifecycle.py")
    assert set(migration._NEW_SALE_STATUSES) == {s.value for s in SaleStatus}


def test_sale_invoice_status_check_lists_every_enum_value() -> None:
    migration = _load("a3c5e7f9b1d2_sale_invoice_status_pending_void.py")
    assert set(migration._NEW) == {s.value for s in SaleInvoiceStatus}


def test_einvoice_issue_channel_check_lists_every_enum_value() -> None:
    """手開紙本發票的來源欄位（docs/36）。"""
    migration = _load("c7a9e1b3d5f7_invoice_issue_channel.py")
    assert set(migration._CHANNELS) == {c.value for c in EInvoiceIssueChannel}


def test_service_mode_check_lists_every_enum_value() -> None:
    """內用/外帶（docs/35）。"""
    migration = _load("b4d6f8a1c3e5_dine_in_service_mode_and_kitchen_ticket.py")
    assert set(migration._SERVICE_MODES) == {m.value for m in ServiceMode}
