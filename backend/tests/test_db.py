"""core/db.py — async engine / session 對真實 DB 可運作。"""

from sqlalchemy import text

from app.core.db import get_session


async def test_get_session_yields_working_session() -> None:
    """get_session 產出的 session 能對 DB 執行查詢。"""
    sessions = get_session()
    session = await anext(sessions)
    try:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    finally:
        await sessions.aclose()
