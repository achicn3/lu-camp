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

from app.shared.enums import SaleStatus, SignatureTaskKind

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
