"""銷售作廢守衛的 DDL 綁定測試（2026-08 金流稽核 P0-1／P0-2 的再發防護）。

那次 bug 之所以能潛伏三週：`SaleStatus.VOIDED` 的字面值是寫死在 plpgsql 字串裡的，
把 enum 改名或改語意時，沒有任何東西會提醒 DDL 也要跟著改。這裡把兩者綁起來。

同時守住第二件事：migration 內嵌的 SQL 必須與 `models.py` 的 DDL 常數逐字相同——
否則「以 create_all 建的庫」（測試）與「以 migration 升級的庫」（正式）會裝到不同的函式，
而讀原始碼看不出來（稽核報告 P2-2）。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from app.modules.sales.models import (
    SALE_LEDGER_BACKING_DDL,
    SALE_TENDER_TOTAL_GUARD_DDL,
)
from app.shared.enums import SaleStatus

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "f9d3b7a1c5e8_sale_void_guards_use_sale_status.py"
)

_ALL_DDL = "\n".join(SALE_TENDER_TOTAL_GUARD_DDL + SALE_LEDGER_BACKING_DDL)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_p0_guard_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function_body(source: str, name: str) -> str:
    """從 DDL 文字中抽出某支函式的完整定義（含註解）。"""
    start = source.index(f"CREATE OR REPLACE FUNCTION {name}")
    end = source.index("$$ LANGUAGE plpgsql", start) + len("$$ LANGUAGE plpgsql")
    return source[start:end]


def test_void_guards_judge_sale_status_not_invoice_status() -> None:
    """作廢判定必須看 `sales.status`，且字面值跟著 SaleStatus enum 走。

    `invoice_status` 是**發票**的狀態：電子發票關閉時根本沒有發票，
    同月整筆退貨也會把它寫成 VOID——兩者都不代表「這筆銷售作廢了」（ADR-013）。
    """
    voided = SaleStatus.VOIDED.value
    assert f"sale_status = '{voided}'" in _ALL_DDL
    assert f"sale_status <> '{voided}'" in _ALL_DDL
    # 讀取的欄位是 status
    assert "SELECT status INTO sale_status" in _ALL_DDL
    assert "SELECT store_id, buyer_contact_id, status" in _ALL_DDL
    # 不得再拿 invoice_status 當作廢判定（註解可以提到它，比較運算不行）
    assert "invoice_status =" not in _ALL_DDL
    assert "invoice_status <>" not in _ALL_DDL
    assert "INTO sale_status" in _ALL_DDL
    assert "invoice_status INTO" not in _ALL_DDL


def test_migration_sql_matches_models_ddl() -> None:
    """migration 內嵌的新版 SQL 必須與 models.py 的 DDL 常數逐字相同。

    不一致的話，測試庫（create_all）與正式庫（migration）會裝到不同的函式體，
    而且讀原始碼看不出來——正是這次 bug 難以察覺的結構性成因。
    """
    migration = _load_migration()
    for const, fn_name in (
        ("_CONSISTENCY_FN_NEW", "sales_verify_store_credit_consistency"),
        ("_DEBIT_GUARD_FN_NEW", "sales_ledger_sale_debit_guard"),
    ):
        embedded = getattr(migration, const)
        assert embedded.strip() == _function_body(_ALL_DDL, fn_name).strip(), (
            f"{const} 與 models.py 的 {fn_name} 不一致：改了一邊要同步改另一邊"
        )


def test_migration_downgrade_restores_old_semantics() -> None:
    """downgrade 的 SQL 必須真的回到舊語意（判 invoice_status），否則降級是假的。"""
    migration = _load_migration()
    for const in ("_CONSISTENCY_FN_OLD", "_DEBIT_GUARD_FN_OLD"):
        old = getattr(migration, const)
        assert "invoice_status" in old
        assert f"'{SaleStatus.VOIDED.value}'" not in old
