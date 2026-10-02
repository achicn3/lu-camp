"""待整理商品成色可空的 migration：還有沒成色的商品就拒絕降版（不替它們亂填成色）。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "38cc70aebdcc_pending_item_grade_optional.py"
    )
    spec = importlib.util.spec_from_file_location("pending_grade_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_allowed_when_every_item_has_a_grade(db_session: AsyncSession) -> None:
    await db_session.execute(text("DELETE FROM serialized_items WHERE grade IS NULL"))
    conn = await db_session.connection()
    await conn.run_sync(lambda c: _migration().abort_if_gradeless_items_exist(c))


async def test_downgrade_refused_with_gradeless_items(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    # 不建完整商品：只要讓計數查詢看到一筆沒成色的列（用暫存表遮蔽同名表）。
    await db_session.execute(
        text("CREATE TEMP TABLE serialized_items (grade varchar) ON COMMIT DROP")
    )
    await db_session.execute(text("INSERT INTO serialized_items VALUES (NULL)"))
    with pytest.raises(RuntimeError, match="1 件待整理商品沒有成色"):
        await conn.run_sync(lambda c: _migration().abort_if_gradeless_items_exist(c))
