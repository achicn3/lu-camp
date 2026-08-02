"""在**有資料**的資料庫上一次升到 head（回歸測試）。

實跑還原演練時踩到：`sales` 上有 deferrable constraint trigger（trg_sales_tender_total），
同一個 `alembic upgrade head` 交易裡，前面的 migration 一旦 `UPDATE sales`，後面的
`ALTER TABLE sales DROP CONSTRAINT` 就會被 Postgres 以「has pending trigger events」拒絕。

**空庫測不出來**（沒有列就不會產生觸發事件），所以先前的 up→down→up 全是綠的；真正會踩到的是
新機部署、以及**還原一份舊備份後升級**——正好是備份還原這條路。此測試在建表後**先寫入資料**
再跑升級，把這個 class 的缺陷釘住。
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
# 起點：本輪 migration 之前的版本（拆分銷售與發票生命週期之前）。
_BASE_REVISION = "e3b4c5d6e7f8"


def _url(db_name: str) -> str:
    """本 repo 只裝 asyncpg（無同步驅動），故一律走 async engine。"""
    return (
        make_url(get_settings().database_url)
        .set(database=db_name)
        .render_as_string(hide_password=False)
    )


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": _url(db_name)}
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest_asyncio.fixture
async def scratch_db() -> AsyncIterator[str]:
    """建一個獨立的空庫給本測試用，結束即刪（不碰測試庫本身的 schema）。"""
    name = "lucamp_migtest_populated"
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


async def test_upgrade_to_head_succeeds_on_a_database_that_has_sales(scratch_db: str) -> None:
    """先升到本輪之前的版本 → 寫入一筆銷售 → 再一次升到 head，必須成功。

    這正是「新機部署」與「還原舊備份後升級」的形狀。若 migration 忘了結清延遲的觸發事件，
    這裡會以 `cannot ALTER TABLE "sales" because it has pending trigger events` 失敗。
    """
    down = _alembic(scratch_db, "upgrade", _BASE_REVISION)
    assert down.returncode == 0, down.stderr

    engine = create_async_engine(_url(scratch_db))
    async with engine.begin() as conn:
        store_id = (await conn.execute(
            text("INSERT INTO stores (name) VALUES ('遷移測試店') RETURNING id")
        )).scalar_one()
        clerk_id = (await conn.execute(
            text(
                "INSERT INTO users (store_id, username, password_hash, role, is_active,"
                " created_at, updated_at)"
                " VALUES (:s, 'mig-clerk', 'h', 'MANAGER', true, now(), now()) RETURNING id"
            ).bindparams(s=store_id)
        )).scalar_one()
        # **invoice_status 必須是 'VOID'**：f4c5d6e7a8b9 的回填正是
        # `UPDATE sales SET status='VOIDED' WHERE invoice_status='VOID'`——若這裡填別的值，
        # 那句 UPDATE 一列都不會動，就不會排隊延遲觸發事件，測試也就測不到要測的東西。
        # （收款明細必須與總額對平，否則 trg_sales_tender_total 會在 commit 時擋下——
        #  這正好也證明了那個延遲約束觸發器確實是活的。）
        sale_id = (await conn.execute(
            text(
                "INSERT INTO sales (store_id, clerk_user_id, subtotal, tax, total,"
                " payment_method, status, invoice_status, awarded_points,"
                " created_at, updated_at)"
                " VALUES (:s, :c, 100, 5, 100, 'CASH', 'COMPLETED', 'VOID', 0,"
                " now(), now())"
                " RETURNING id"
            ).bindparams(s=store_id, c=clerk_id)
        )).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO sale_tenders (store_id, sale_id, tender_type, amount,"
                " fee_amount, created_at, updated_at)"
                " VALUES (:s, :sale, 'CASH', 100, 0, now(), now())"
            ).bindparams(s=store_id, sale=sale_id)
        )
    await engine.dispose()

    up = _alembic(scratch_db, "upgrade", "head")
    assert up.returncode == 0, (
        "有資料的庫一次升到 head 失敗——很可能是某支 migration 先寫了資料、"
        f"後面又 ALTER 同一張表卻沒結清延遲觸發事件：\n{up.stderr}"
    )

    verify = create_async_engine(_url(scratch_db))
    async with verify.connect() as conn:
        assert (await conn.scalar(text("SELECT count(*) FROM sales"))) == 1
        # 新列舉值確實可用（CHECK 已重建）。
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'invoices' AND column_name = 'void_reason'"
                )
            )
        ) == 1
    await verify.dispose()
