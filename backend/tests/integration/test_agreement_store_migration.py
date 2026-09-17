"""agreement_versions 加 store_id 的 migration，在**有資料**的庫上驗證（2026-09-17）。

空庫測不出這一版真正的風險：原本的版本列是全店共用的，如果升版時把全部塞給第一家店，
其他分店的既有簽署就會指到別人店的版本列——店別隔離當場破功，而且爭議時拿出來的
「客人簽的那一份」會是另一家店的文件。

**已簽的任務不能改指向**（DB 觸發器擋著：簽署內容與歸屬不可修改，那是法律證據），
所以拆法是「有簽署參照的那家店留原列、其他店各複製一份」；若兩家以上都已有簽署參照，
升版一律中止交人工處理，不擅自搬動任何人的證據。

降版方向：版本號要變回全域唯一，多店資料硬降會撞唯一鍵，一律中止。
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
_BEFORE = "a4e7c2b9f6d1"  # 本輪 migration 的前一版
_THIS = "b8d1f3a6c209"


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
    name = "lucamp_migtest_agreement_store"
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


async def _seed_two_stores_sharing_one_agreement(db_name: str) -> tuple[int, int, int, int, int]:
    """前一版的形狀：兩家店共用同一列切結書，各有一筆已簽的收購切結。

    回 (A 店, B 店, 共用版本列 id, A 店任務 id, B 店任務 id)。
    """
    engine = create_async_engine(_url(db_name))
    async with engine.begin() as conn:
        store_a = (
            await conn.execute(text("INSERT INTO stores (name) VALUES ('A 店') RETURNING id"))
        ).scalar_one()
        store_b = (
            await conn.execute(text("INSERT INTO stores (name) VALUES ('B 店') RETURNING id"))
        ).scalar_one()
        agreement_id = (
            await conn.execute(
                text(
                    "INSERT INTO agreement_versions (version, title, body)"
                    " VALUES (1, '共用切結書', '共用內文') RETURNING id"
                )
            )
        ).scalar_one()
        task_ids = []
        for store_id in (store_a, store_b):
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO users (store_id, username, password_hash, role,"
                        " created_at, updated_at)"
                        " VALUES (:s, :u, 'h', 'CLERK', now(), now()) RETURNING id"
                    ).bindparams(s=store_id, u=f"clk-{store_id}")
                )
            ).scalar_one()
            contact_id = (
                await conn.execute(
                    text(
                        "INSERT INTO contacts (store_id, name, roles, national_id_enc,"
                        " created_at, updated_at)"
                        " VALUES (:s, '賣方', ARRAY['SELLER'], 'enc', now(), now()) RETURNING id"
                    ).bindparams(s=store_id)
                )
            ).scalar_one()
            task_ids.append(
                (
                    await conn.execute(
                        text(
                            # 已簽的任務必須帶撥款選擇與三組證據 hash（表上兩條 CHECK 在守）
                            "INSERT INTO signature_tasks (store_id, kind, status, contact_id,"
                            " content, agreement_version_id, created_by, chosen_payout,"
                            " signed_at, signature_sha256, content_sha256, evidence_hash,"
                            " created_at, updated_at)"
                            " VALUES (:s, 'ACQUISITION_AFFIDAVIT', 'SIGNED', :c,"
                            " '{}'::jsonb, :a, :u, 'CASH', now(), :h, :h, :h,"
                            " now(), now()) RETURNING id"
                        ).bindparams(
                            s=store_id, c=contact_id, a=agreement_id, u=user_id, h="0" * 64
                        )
                    )
                ).scalar_one()
            )
    await engine.dispose()
    return store_a, store_b, agreement_id, task_ids[0], task_ids[1]


async def _seed_one_signed_store_plus_empty_store(db_name: str) -> tuple[int, int, int, int]:
    """只有 A 店有已簽任務，B 店還沒用過切結書。回 (A 店, B 店, 共用列 id, A 店任務 id)。"""
    store_a, store_b, agreement_id, task_a, task_b = await _seed_two_stores_sharing_one_agreement(
        db_name
    )
    engine = create_async_engine(_url(db_name))
    async with engine.begin() as conn:
        # 已簽任務不能改指向，但整筆刪除可以（這裡是在造「B 店沒簽過」的前一版形狀）
        await conn.execute(
            text("DELETE FROM signature_tasks WHERE id = :t").bindparams(t=task_b)
        )
    await engine.dispose()
    return store_a, store_b, agreement_id, task_a


async def test_upgrade_aborts_when_two_stores_share_signed_agreements(scratch_db: str) -> None:
    """兩家店的簽署都指著同一列：已簽的歸屬不可改，只能中止並要求人工處理。"""
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    await _seed_two_stores_sharing_one_agreement(scratch_db)

    up = _alembic(scratch_db, "upgrade", _THIS)
    assert up.returncode != 0
    assert "人工" in up.stderr


async def test_upgrade_copies_for_stores_without_signed_history(scratch_db: str) -> None:
    """只有 A 店簽過：A 店留原列（證據不動），B 店拿到內容相同的自己那份。"""
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    store_a, store_b, shared_id, task_a = await _seed_one_signed_store_plus_empty_store(scratch_db)

    up = _alembic(scratch_db, "upgrade", _THIS)
    assert up.returncode == 0, up.stderr

    engine = create_async_engine(_url(scratch_db))
    async with engine.begin() as conn:
        signed = (
            await conn.execute(
                text(
                    "SELECT av.id, av.store_id FROM signature_tasks st"
                    " JOIN agreement_versions av ON av.id = st.agreement_version_id"
                    " WHERE st.id = :t"
                ).bindparams(t=task_a)
            )
        ).one()
        assert signed[0] == shared_id, "已簽任務的指向被改動了"
        assert signed[1] == store_a
        copies = (
            await conn.execute(
                text(
                    "SELECT store_id, version, body FROM agreement_versions ORDER BY store_id"
                )
            )
        ).all()
        assert [(row[0], row[1]) for row in copies] == [(store_a, 1), (store_b, 1)]
        assert copies[0][2] == copies[1][2] == "共用內文"  # 內容一字不差
    await engine.dispose()


async def test_downgrade_refuses_when_multiple_stores_have_versions(scratch_db: str) -> None:
    """多店降版會撞回全域唯一鍵：中止，不要默默丟掉某家店的切結書。"""
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    await _seed_one_signed_store_plus_empty_store(scratch_db)
    assert _alembic(scratch_db, "upgrade", _THIS).returncode == 0

    down = _alembic(scratch_db, "downgrade", _BEFORE)
    assert down.returncode != 0
    assert "降版" in down.stderr or "版本號" in down.stderr


async def test_single_store_downgrade_round_trips(scratch_db: str) -> None:
    """單店（目前的實際情形）要降得回去，否則這版就是單向門。"""
    assert (r := _alembic(scratch_db, "upgrade", _BEFORE)).returncode == 0, r.stderr
    engine = create_async_engine(_url(scratch_db))
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO stores (name) VALUES ('單店')"))
        await conn.execute(
            text(
                "INSERT INTO agreement_versions (version, title, body)"
                " VALUES (1, '切結書', '內文')"
            )
        )
    await engine.dispose()

    assert _alembic(scratch_db, "upgrade", _THIS).returncode == 0
    down = _alembic(scratch_db, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr
    assert _alembic(scratch_db, "upgrade", _THIS).returncode == 0
