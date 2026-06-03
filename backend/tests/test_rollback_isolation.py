"""驗證 conftest 的回滾隔離：測試間不污染、不殘留資料。

兩個測試共用同一張已存在的 probe 表；test_a 寫入並 commit，
若隔離正確，test_b 在乾淨狀態下執行（看不到 test_a 的資料、count 為 0）。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_a_insert_then_commit_is_isolated(
    db_session: AsyncSession, _rollback_probe_table: None
) -> None:
    # 起點乾淨（不依賴測試執行順序）。
    count = (await db_session.execute(text("SELECT count(*) FROM _rollback_probe"))).scalar_one()
    assert count == 0

    await db_session.execute(text("INSERT INTO _rollback_probe (id) VALUES (1)"))
    await db_session.commit()  # 即使 commit，外層交易 rollback 仍會丟棄

    count = (await db_session.execute(text("SELECT count(*) FROM _rollback_probe"))).scalar_one()
    assert count == 1


async def test_b_db_is_clean_after_previous_test(
    db_session: AsyncSession, _rollback_probe_table: None
) -> None:
    # test_a 的寫入應已被回滾，這裡必須看到乾淨的表。
    count = (await db_session.execute(text("SELECT count(*) FROM _rollback_probe"))).scalar_one()
    assert count == 0
