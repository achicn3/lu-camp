"""Cash movement is an immutable, direction-safe financial ledger at DB level."""

from decimal import Decimal

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import CashMovementType, UserRole


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    store = Store(name="錢櫃守衛門市")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username="cash-guard-manager",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    session.add(manager)
    await session.flush()
    cash_session = await CashDrawerService(session).open_session(
        store.id, manager.id, Decimal("1000")
    )
    return store.id, manager.id, cash_session.id


async def test_db_rejects_zero_and_negative_system_movements(db_session: AsyncSession) -> None:
    store_id, _manager_id, cash_session_id = await _seed(db_session)

    for amount in (Decimal("0"), Decimal("-100")):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(
                    CashMovement(
                        store_id=store_id,
                        session_id=cash_session_id,
                        type=CashMovementType.SALE_IN,
                        amount=amount,
                    )
                )
                await db_session.flush()


async def test_db_rejects_manual_movement_without_retry_identity(
    db_session: AsyncSession,
) -> None:
    store_id, _manager_id, cash_session_id = await _seed(db_session)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                CashMovement(
                    store_id=store_id,
                    session_id=cash_session_id,
                    type=CashMovementType.MANUAL_ADJUST,
                    amount=Decimal("100"),
                    note="盤差",
                )
            )
            await db_session.flush()


async def test_db_rejects_cash_movement_update_and_delete(db_session: AsyncSession) -> None:
    store_id, _manager_id, _cash_session_id = await _seed(db_session)
    movement = await CashDrawerService(db_session).record_movement(
        store_id,
        CashMovementType.SALE_IN,
        Decimal("100"),
        ref_type="sale",
        ref_id=1,
    )

    with pytest.raises(DBAPIError, match="insert-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                update(CashMovement)
                .where(CashMovement.id == movement.id)
                .values(amount=Decimal("999"))
            )

    with pytest.raises(DBAPIError, match="insert-only"):
        async with db_session.begin_nested():
            await db_session.execute(delete(CashMovement).where(CashMovement.id == movement.id))
