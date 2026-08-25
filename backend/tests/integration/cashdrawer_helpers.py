"""Privileged cleanup helpers for committed integration-test data only."""

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement


async def delete_cash_movements_for_test(session: AsyncSession, *, store_id: int) -> None:
    """Delete fixtures only inside pytest's disposable per-process database.

    Production DML remains protected by ``cash_movement_immutable``. PostgreSQL's
    replication-role bypass is intentionally fenced by the generated test database name.
    """
    database_name = await session.scalar(text("SELECT current_database()"))
    if not isinstance(database_name, str) or "_test_" not in database_name:
        raise RuntimeError("refusing cash movement trigger bypass outside disposable test database")
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    await session.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
    await session.execute(text("SET LOCAL session_replication_role = origin"))
