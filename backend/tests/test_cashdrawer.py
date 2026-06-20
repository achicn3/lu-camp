"""cashdrawer 領域測試（開帳/結帳/現金異動/對帳；對齊 CLAUDE.md §7.4、§7.8）。

併發開帳見 tests/integration/test_cashdrawer_concurrency.py。
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import CashMovementType, CashSessionStatus, UserRole
from app.shared.exceptions import (
    CashSessionAlreadyClosed,
    CashSessionAlreadyOpen,
    NoOpenCashSession,
    UnknownCashMovementType,
)


async def _make_store_user(session: AsyncSession) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username="clerk", password_hash="h", role=UserRole.CLERK)
    session.add(user)
    await session.flush()
    return store.id, user.id


async def test_open_session(db_session: AsyncSession) -> None:
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    cs = await svc.open_session(store_id, user_id, Decimal("1000"))
    assert cs.status == CashSessionStatus.OPEN
    current = await svc.get_current_session(store_id)
    assert current is not None and current.id == cs.id


async def test_open_twice_rejected(db_session: AsyncSession) -> None:
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    await svc.open_session(store_id, user_id, Decimal("1000"))
    with pytest.raises(CashSessionAlreadyOpen):
        await svc.open_session(store_id, user_id, Decimal("500"))


async def test_record_movement_requires_open_session(db_session: AsyncSession) -> None:
    svc = CashDrawerService(db_session)
    store_id, _ = await _make_store_user(db_session)
    with pytest.raises(NoOpenCashSession):
        await svc.record_movement(store_id, CashMovementType.SALE_IN, Decimal("100"))


async def test_record_movement_under_open_session(db_session: AsyncSession) -> None:
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    await svc.open_session(store_id, user_id, Decimal("1000"))
    mv = await svc.record_movement(
        store_id, CashMovementType.SALE_IN, Decimal("100"), ref_type="sale", ref_id=1
    )
    assert mv.id is not None
    assert mv.store_id == store_id


async def test_expected_formula_and_variance_recorded(db_session: AsyncSession) -> None:
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    cs = await svc.open_session(store_id, user_id, Decimal("1000"))

    await svc.record_movement(store_id, CashMovementType.SALE_IN, Decimal("500"))
    await svc.record_movement(store_id, CashMovementType.SALE_IN, Decimal("300"))
    await svc.record_movement(store_id, CashMovementType.BUYOUT_OUT, Decimal("200"))
    await svc.record_movement(store_id, CashMovementType.CONSIGNMENT_PAYOUT_OUT, Decimal("100"))
    await svc.record_movement(store_id, CashMovementType.SALE_REFUND_OUT, Decimal("120"))
    await svc.record_movement(store_id, CashMovementType.MANUAL_ADJUST, Decimal("50"))
    await svc.record_movement(store_id, CashMovementType.MANUAL_ADJUST, Decimal("-30"))

    # 1000 + (500+300) - 200 - 100 - 120 + (50-30) = 1400
    expected = await svc.expected_amount(cs)
    assert expected == Decimal("1400")

    closed = await svc.close_session(cs, counted_amount=Decimal("1500"), closed_by=user_id)
    assert closed.status == CashSessionStatus.CLOSED
    assert closed.expected_amount == Decimal("1400")
    assert closed.variance == Decimal("100")  # 多出 100
    assert closed.closed_at is not None


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditLog]:
    rows = await session.scalars(select(AuditLog).where(AuditLog.action == action))
    return list(rows)


async def test_close_session_writes_audit_log(db_session: AsyncSession) -> None:
    """結帳是現金對帳事件，必須寫 audit_log（CLAUDE.md §5）。"""
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    cs = await svc.open_session(store_id, user_id, Decimal("1000"))
    await svc.record_movement(store_id, CashMovementType.SALE_IN, Decimal("200"))

    await svc.close_session(cs, counted_amount=Decimal("1190"), closed_by=user_id)

    rows = await _audit_rows(db_session, "CLOSE_CASH_SESSION")
    assert len(rows) == 1
    entry = rows[0]
    assert entry.store_id == store_id
    assert entry.actor_user_id == user_id
    assert entry.entity_type == "cash_session"
    assert entry.entity_id == str(cs.id)
    assert entry.after is not None
    assert entry.after["expected_amount"] == "1200"
    assert entry.after["counted_amount"] == "1190"
    assert entry.after["variance"] == "-10"


async def test_close_already_closed_rejected(db_session: AsyncSession) -> None:
    """已結帳的 session 不可重複結帳（避免覆寫對帳結果）。"""
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    cs = await svc.open_session(store_id, user_id, Decimal("1000"))
    await svc.close_session(cs, counted_amount=Decimal("1000"), closed_by=user_id)
    with pytest.raises(CashSessionAlreadyClosed):
        await svc.close_session(cs, counted_amount=Decimal("999"), closed_by=user_id)


async def test_manual_adjust_writes_audit_log_with_actor(db_session: AsyncSession) -> None:
    """手動現金調整必須寫 audit_log，含『誰』調整（CLAUDE.md §5）。"""
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    await svc.open_session(store_id, user_id, Decimal("1000"))

    await svc.record_movement(
        store_id,
        CashMovementType.MANUAL_ADJUST,
        Decimal("-50"),
        actor_user_id=user_id,
        ref_type="adjust",
        ref_id=7,
    )

    rows = await _audit_rows(db_session, "CASH_MANUAL_ADJUST")
    assert len(rows) == 1
    entry = rows[0]
    assert entry.actor_user_id == user_id
    assert entry.entity_type == "cash_session"
    assert entry.after is not None
    assert entry.after["amount"] == "-50"


async def test_non_manual_movement_writes_no_audit(db_session: AsyncSession) -> None:
    """SALE_IN 等由上游交易稽核，cashdrawer 不在此重複寫 audit。"""
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    await svc.open_session(store_id, user_id, Decimal("1000"))
    await svc.record_movement(store_id, CashMovementType.SALE_IN, Decimal("100"))
    assert await _audit_rows(db_session, "CASH_MANUAL_ADJUST") == []


async def test_expected_amount_rejects_unknown_movement_type(db_session: AsyncSession) -> None:
    """對帳遇到未知異動類型應炸出，而非靜默算錯現金。"""
    svc = CashDrawerService(db_session)
    store_id, user_id = await _make_store_user(db_session)
    cs = await svc.open_session(store_id, user_id, Decimal("1000"))

    async def _fake_list(_session_id: int) -> list[object]:
        return [SimpleNamespace(type="BOGUS", amount=Decimal("10"))]

    svc._repo.list_movements = _fake_list  # type: ignore[method-assign,assignment]
    with pytest.raises(UnknownCashMovementType):
        await svc.expected_amount(cs)
