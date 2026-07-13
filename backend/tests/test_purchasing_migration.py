"""採購分批收貨 migration 的回復策略。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "c1d2e3f4a5b6_po_partial_receiving.py"
    )
    spec = importlib.util.spec_from_file_location("po_partial_receiving_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_receiving_migration_is_explicitly_irreversible() -> None:
    """舊模型無法無損表示 PARTIAL／多批收貨，downgrade 必須在改 schema 前明確拒絕。"""
    migration = _load_migration()

    with pytest.raises(RuntimeError, match=r"irreversible.*roll forward"):
        migration.downgrade()
