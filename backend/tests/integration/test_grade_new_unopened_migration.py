"""成色新增「全新未拆」（N）的 migration，在**有資料**的庫上驗證（2026-09-16）。

兩件事空庫測不出來，所以先升到前一版、寫入資料，再升到 head：

1. **既有分類要補一組 N 的收購定價規則**。每個分類是依成色各存一組參數，新分類由
   service 自動種 6 組；但舊分類只有 S–D 五組，沒補的話收購時選「全新未拆」會算不出
   建議最高收購成本。
2. **三張表的 CHECK 都要放行 N**。測試庫是 `create_all` 建的，CHECK 自動包含所有值；
   真正部署的庫靠 migration 演進，漏一張就會在 INSERT 時被擋。

降版方向：只要還有任何序號品／散裝批是 N，**一律中止**——舊版的 CHECK 容不下這個值，
硬降只會變成資料寫不回去或被迫竄改成色（把全新未拆默默改成 S 會讓標籤印成「二手」）。
"""

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_BEFORE = "d1f4a6c8b3e7"  # 本輪 migration 的前一版
_THIS = "e6a2c8f4b1d9"


def _url(db_name: str) -> str:
    return (
        make_url(get_settings().database_url)
        .set(database=db_name)
        .render_as_string(hide_password=False)
    )


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": _url(db_name)}
    return subprocess.run(
        ["uv", "run", "--no-sync", "alembic", *args],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest_asyncio.fixture
async def scratch_db() -> AsyncIterator[str]:
    name = "lucamp_migtest_grade_n"
    admin = create_async_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _seed_category_with_old_bands(db_name: str) -> tuple[int, int]:
    """前一版的形狀：一個分類、S–D 五組定價規則（其中 S 被店長調過）。"""
    engine = create_async_engine(_url(db_name))
    async with engine.begin() as conn:
        store_id = (
            await conn.execute(text("INSERT INTO stores (name) VALUES ('成色遷移店') RETURNING id"))
        ).scalar_one()
        category_id = (
            await conn.execute(
                text(
                    "INSERT INTO categories (store_id, name, target_margin_pct)"
                    " VALUES (:s, '爐具', 45) RETURNING id"
                ).bindparams(s=store_id)
            )
        ).scalar_one()
        for band in ("S", "A", "B", "C", "D"):
            # S 故意調成非預設值：N 應採用**預設值**（已裁示），而不是去抄某個被調過的帶。
            ceiling = 70 if band == "S" else 60
            await conn.execute(
                text(
                    "INSERT INTO category_pricing_rules (store_id, category_id, condition_band,"
                    " discount_ceiling_pct, min_margin_pct, min_price_multiple)"
                    " VALUES (:s, :c, :b, :ceil, 40, 2.00)"
                ).bindparams(s=store_id, c=category_id, b=band, ceil=ceiling)
            )
    await engine.dispose()
    return store_id, category_id


async def test_upgrade_backfills_n_rule_and_widens_every_check(scratch_db: str) -> None:
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    store_id, category_id = await _seed_category_with_old_bands(scratch_db)

    up = _alembic(scratch_db, "upgrade", _THIS)
    assert up.returncode == 0, up.stderr

    engine = create_async_engine(_url(scratch_db))
    async with engine.begin() as conn:
        rule = (
            await conn.execute(
                text(
                    "SELECT discount_ceiling_pct, min_margin_pct, min_price_multiple"
                    " FROM category_pricing_rules WHERE category_id = :c AND condition_band = 'N'"
                ).bindparams(c=category_id)
            )
        ).one()
        assert (rule[0], rule[1], str(rule[2])) == (60, 40, "2.00"), "N 應採用預設參數"
        bands = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM category_pricing_rules WHERE category_id = :c"
                ).bindparams(c=category_id)
            )
        ).scalar_one()
        assert bands == 6

        # 三張表的 CHECK 都要真的收得下 N（直接寫入驗證，不只看約束文字）。
        await conn.execute(
            text(
                "INSERT INTO serialized_items (store_id, item_code, name, grade, ownership_type,"
                " listed_price, acquisition_cost) VALUES (:s, 'MIG-N-1', '未拆爐頭', 'N', 'OWNED',"
                " 2400, 1200)"
            ).bindparams(s=store_id)
        )
        n_items = (
            await conn.execute(text("SELECT count(*) FROM serialized_items WHERE grade = 'N'"))
        ).scalar_one()
        assert n_items == 1
        bulk_def = (
            await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'bulk_lots'::regclass AND conname = 'grade'"
                )
            )
        ).scalar_one()
        assert "'N'" in bulk_def
    await engine.dispose()


async def test_round_trip_leaves_exactly_one_n_rule_per_category(scratch_db: str) -> None:
    """升 → 降 → 升：降版要清掉 N 帶，再升回來要重新補、且剛好一組（不重複、不遺漏）。"""
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    _store_id, category_id = await _seed_category_with_old_bands(scratch_db)

    assert (r := _alembic(scratch_db, "upgrade", _THIS)).returncode == 0, r.stderr
    assert (r := _alembic(scratch_db, "downgrade", _BEFORE)).returncode == 0, r.stderr
    assert (r := _alembic(scratch_db, "upgrade", _THIS)).returncode == 0, r.stderr

    engine = create_async_engine(_url(scratch_db))
    async with engine.connect() as conn:
        n_rules = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM category_pricing_rules"
                    " WHERE category_id = :c AND condition_band = 'N'"
                ).bindparams(c=category_id)
            )
        ).scalar_one()
    await engine.dispose()
    assert n_rules == 1


async def test_downgrade_refuses_while_any_item_is_new_unopened(scratch_db: str) -> None:
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    store_id, _category_id = await _seed_category_with_old_bands(scratch_db)
    assert (r := _alembic(scratch_db, "upgrade", _THIS)).returncode == 0, r.stderr

    engine = create_async_engine(_url(scratch_db))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO serialized_items (store_id, item_code, name, grade, ownership_type,"
                " listed_price, acquisition_cost) VALUES (:s, 'MIG-N-2', '未拆帳篷', 'N', 'OWNED',"
                " 5000, 2500)"
            ).bindparams(s=store_id)
        )
    await engine.dispose()

    down = _alembic(scratch_db, "downgrade", _BEFORE)
    assert down.returncode != 0, "還有全新未拆的品項，降版必須中止"
    assert "全新未拆" in down.stderr

    # 中止後資料原封不動：成色沒有被偷改。
    engine = create_async_engine(_url(scratch_db))
    async with engine.connect() as conn:
        grade = (
            await conn.execute(
                text("SELECT grade FROM serialized_items WHERE item_code = 'MIG-N-2'")
            )
        ).scalar_one()
    await engine.dispose()
    assert grade == "N"
