"""Focused regression tests for signature lifecycle migration backfills."""

from datetime import UTC, datetime
from importlib import util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from pytest import MonkeyPatch


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "d2a3b4c5d6e7_signature_task_lifecycle.py"
    )
    spec = util.spec_from_file_location("signature_lifecycle_migration", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, Any]]:
        return self._rows


class _Bind:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _Rows:
        sql = str(statement)
        self.calls.append((sql, parameters))
        if parameters is None:
            return _Rows(self.rows)
        return _Rows([])


def test_backfill_consumes_existing_sales_and_keeps_legacy_status_readable(
    monkeypatch: MonkeyPatch,
) -> None:
    migration = _load_migration()
    signed_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    rows = [
        {
            "id": 11,
            "store_id": 2,
            "kind": "TRANSACTION_ACK",
            "status": "SIGNED",
            "content": {"total": "300"},
            "ref_id": 91,
            "signature_image": b"png",
            "signed_at": signed_at,
            "bound_sale_id": None,
            "ack_sale_id": 91,
            "retention_days": 183,
            "is_bound": True,
        },
        {
            "id": 12,
            "store_id": 2,
            "kind": "ACQUISITION_AFFIDAVIT",
            "status": "CANCELLED",
            "content": {"total": "100"},
            "ref_id": None,
            "signature_image": None,
            "signed_at": None,
            "bound_sale_id": None,
            "ack_sale_id": None,
            "retention_days": 183,
            "is_bound": False,
        },
    ]
    bind = _Bind(rows)
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    migration._hash_existing_evidence()

    select_sql = bind.calls[0][0]
    assert "lower(st.ref_type) = 'sale'" in select_sql
    parameter_sets = [params for _sql, params in bind.calls if params is not None]
    updates = [params for params in parameter_sets if "task_id" in params and "status" in params]
    events = [params for params in parameter_sets if "to_status" in params]
    assert updates[0]["status"] == "CONSUMED"
    assert updates[0]["consumed_at"] == signed_at
    assert cast(datetime, updates[0]["signature_retention_until"]) > signed_at
    assert events[0]["from_status"] == "SIGNED"
    assert events[0]["to_status"] == "CONSUMED"
    assert events[0]["sale_id"] == 91
    assert updates[1]["status"] == "VOIDED"
    assert events[1]["from_status"] is None
    assert events[1]["to_status"] == "VOIDED"
