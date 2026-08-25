from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.returns.service import ReturnsService
from app.modules.sales.service import SalesService
from app.shared.enums import LinePayRefundStatus


@pytest.mark.asyncio
async def test_manual_refund_resolution_locks_sale_before_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """人工裁定須與退貨共用 sale→attempt 鎖序，不能在 guard 後插隊改成成功。"""
    calls: list[str] = []
    attempt = SimpleNamespace(
        id=7,
        status=LinePayRefundStatus.PENDING,
        order_id="LP-LOCK-ORDER",
    )
    txn = SimpleNamespace(sale_id=11)

    class FakeRepository:
        async def get_refund_attempt(
            self, store_id: int, attempt_id: int, *, for_update: bool = False
        ) -> object:
            calls.append("attempt_lock" if for_update else "attempt_read")
            return attempt

        async def get_linepay_by_order_id(
            self, store_id: int, order_id: str, *, for_update: bool = False
        ) -> object:
            calls.append("transaction_read")
            return txn

        async def lock_sale(self, store_id: int, sale_id: int) -> object:
            calls.append("sale_lock")
            return SimpleNamespace(id=sale_id)

    session = AsyncMock()
    service = SalesService(session)
    service._repo = FakeRepository()  # type: ignore[assignment]
    monkeypatch.setattr("app.modules.sales.service.write_audit_log", AsyncMock())

    await service.resolve_linepay_refund(
        3,
        7,
        resolution=LinePayRefundStatus.SUCCEEDED,
        actor_user_id=5,
    )

    assert calls[:4] == ["attempt_read", "transaction_read", "sale_lock", "attempt_lock"]


@pytest.mark.asyncio
async def test_background_recovery_locks_sale_before_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """背景復原先讀定位資料，再依 sale→attempt 鎖序重讀，避免和正常退貨 AB-BA。"""
    calls: list[str] = []
    recovery = SimpleNamespace(
        refund_key="refund-key",
        store_id=3,
        sale_id=11,
        lines=(),
        reason="平台已退",
        actor_user_id=5,
        idempotency_key="return-key",
        taiwan_pay_refund_confirmed=False,
        invoice_recalled=False,
        consent_signature_task_id=None,
        unreturned_gift_note=None,
        manual_paper_disposed=False,
    )

    async def get_recovery(
        self: SalesService, attempt_id: int, *, for_update: bool = False
    ) -> object:
        calls.append("attempt_lock" if for_update else "attempt_read")
        return recovery

    async def lock_sale(self: SalesService, store_id: int, sale_id: int) -> object:
        calls.append("sale_lock")
        return SimpleNamespace(id=sale_id)

    async def create_return(self: ReturnsService, *args: object, **kwargs: object) -> object:
        calls.append("create_return")
        return SimpleNamespace(id=1)

    async def mark_recovered(self: SalesService, attempt_id: int) -> bool:
        calls.append("mark_recovered")
        return True

    monkeypatch.setattr(SalesService, "get_linepay_return_recovery", get_recovery)
    monkeypatch.setattr(SalesService, "get_sale_for_update", lock_sale)
    monkeypatch.setattr(ReturnsService, "create_return", create_return)
    monkeypatch.setattr(SalesService, "mark_linepay_return_recovered", mark_recovered)

    assert await ReturnsService(AsyncMock()).recover_succeeded_linepay_refund(9) is True
    assert calls == [
        "attempt_read",
        "sale_lock",
        "attempt_lock",
        "create_return",
        "mark_recovered",
    ]
