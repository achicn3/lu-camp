"""採購分批收貨 migration 的回復策略。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_migration(filename: str, mod_name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_receiving_migration_is_explicitly_irreversible() -> None:
    """舊模型無法無損表示 PARTIAL／多批收貨，downgrade 必須在改 schema 前明確拒絕。"""
    migration = _load_migration(
        "c1d2e3f4a5b6_po_partial_receiving.py", "po_partial_receiving_migration"
    )

    with pytest.raises(RuntimeError, match=r"irreversible.*roll forward"):
        migration.downgrade()


def test_supplier_name_snapshot_migration_is_explicitly_irreversible() -> None:
    """drop supplier_name 會遺失歷史供應商名快照且再升級會以目前名覆寫，downgrade 須明確拒絕。"""
    migration = _load_migration(
        "a2b3c4d5e6f7_po_supplier_name_snapshot.py", "po_supplier_name_snapshot_migration"
    )

    with pytest.raises(RuntimeError, match=r"irreversible.*roll forward"):
        migration.downgrade()
