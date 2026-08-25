"""The cashflow guard migration is a frozen, complete deployment artifact."""

from pathlib import Path


def test_cashflow_guard_migration_is_frozen_and_complete() -> None:
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "a8c1f4e7b2d5_cashflow_audit_guards.py"
    ).read_text()

    assert "from app.modules" not in source
    assert 'down_revision: str | Sequence[str] | None = "f9d3b7a1c5e8"' in source
    assert "uq_cash_movements_store_idempotency_key" in source
    assert "trg_cash_movement_immutable" in source
    assert "ck_consignment_settlement_amounts_balance" in source
    for trigger_name in (
        "trg_store_credit_ledger_immutable",
        "trg_store_credit_reversal_guard",
        "trg_store_credit_credit_guard",
        "trg_store_credit_balance_chain_guard",
        "trg_store_credit_cache_sync",
    ):
        assert trigger_name in source
