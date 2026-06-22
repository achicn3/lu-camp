"""資料備份／還原（CSV＋JSON 邏輯備份）測試。

驗證重點：
- 序列化無損：Decimal/datetime/date/bytes/None 編碼後再解碼仍相等（純單元，不碰 DB）。
- 備份產出：每張表都有 .json 與 .csv，外加 manifest.json（含 alembic 版本與列數）。
- 還原無損：TRUNCATE 後從 JSON 還原，金額（Decimal）、時間（datetime）、ARRAY 等型別原樣回來。
- 防呆：目標表非空且未 --truncate → RestoreError；schema 版本不符 → RestoreError（force 可繞過）。
- 唯讀：備份本身不改任何資料（列數前後一致）。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.store.models import Store
from app.scripts.data_backup import (
    RestoreError,
    backup_database,
    decode_value,
    encode_value,
    restore_database,
)
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus

# ── 純單元：序列化無損 ─────────────────────────────────────────────────────────


def test_encode_decode_roundtrip_scalars() -> None:
    from sqlalchemy import Date, DateTime, LargeBinary, Numeric, String

    dt = datetime(2026, 6, 22, 13, 45, 0, tzinfo=UTC)
    assert decode_value(DateTime(), encode_value(dt)) == dt

    d = date(2026, 6, 22)
    assert decode_value(Date(), encode_value(d)) == d

    amount = Decimal("12345")
    assert decode_value(Numeric(), encode_value(amount)) == amount
    assert isinstance(decode_value(Numeric(), encode_value(amount)), Decimal)

    blob = b"\x00\x01\xfe\xff"
    assert decode_value(LargeBinary(), encode_value(blob)) == blob

    assert encode_value(None) is None
    assert decode_value(String(), encode_value("店長")) == "店長"


def test_encode_value_is_json_serializable() -> None:
    payload = {
        "amount": encode_value(Decimal("999")),
        "when": encode_value(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        "roles": encode_value(["SELLER", "MEMBER"]),
        "blob": encode_value(b"\x01\x02"),
    }
    # 不丟例外即代表可寫入 JSON 檔。
    text = json.dumps(payload, ensure_ascii=False)
    assert "999" in text


# ── 整合：備份 → 還原 ──────────────────────────────────────────────────────────


async def _seed(session: AsyncSession) -> tuple[int, str]:
    store = Store(name="備份測試門市")
    session.add(store)
    await session.flush()
    session.add(
        Contact(
            store_id=store.id,
            name="王寄售",
            roles=["SELLER", "MEMBER"],
            national_id_enc="enc-secret-ciphertext",
            member_points=7,
        )
    )
    code = f"DS{store.id}-BAK0000001"
    session.add(
        SerializedItem(
            store_id=store.id,
            item_code=code,
            name="二手帳篷",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            acquisition_cost=Decimal("1800"),
            listed_price=Decimal("3200"),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()
    return store.id, code


async def _conn(session: AsyncSession) -> AsyncConnection:
    return await session.connection()


async def test_backup_writes_json_and_csv_per_table(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed(db_session)
    out = tmp_path / "backup"
    manifest = await backup_database(await _conn(db_session), out)

    assert (out / "manifest.json").exists()
    assert (out / "contacts.json").exists()
    assert (out / "contacts.csv").exists()
    assert (out / "serialized_items.json").exists()
    assert manifest["tables"]["contacts"] == 1
    assert manifest["tables"]["serialized_items"] == 1
    # CSV 第一列為欄名
    csv_head = (out / "contacts.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "national_id_enc" in csv_head


async def test_restore_roundtrip_preserves_types(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, code = await _seed(db_session)
    conn = await _conn(db_session)
    out = tmp_path / "backup"
    await backup_database(conn, out)

    # 清空 + 從 JSON 還原。
    inserted = await restore_database(conn, out, truncate=True)
    assert inserted["contacts"] == 1
    assert inserted["serialized_items"] == 1

    contact = (await conn.execute(select(Contact.__table__))).mappings().one()
    assert contact["name"] == "王寄售"
    assert contact["roles"] == ["SELLER", "MEMBER"]
    assert contact["national_id_enc"] == "enc-secret-ciphertext"
    assert contact["member_points"] == 7

    item = (await conn.execute(select(SerializedItem.__table__))).mappings().one()
    assert item["listed_price"] == Decimal("3200")
    assert isinstance(item["listed_price"], Decimal)
    assert item["acquisition_cost"] == Decimal("1800")
    assert item["grade"] == Grade.A
    assert item["store_id"] == store_id
    assert item["item_code"] == code


async def test_restore_can_insert_after_restore_without_pk_collision(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed(db_session)
    conn = await _conn(db_session)
    out = tmp_path / "backup"
    await backup_database(conn, out)
    await restore_database(conn, out, truncate=True)

    # sequence 已被推進 → 新增不撞既有 PK（拿得到新 id）。
    new_id = await conn.scalar(
        insert(Store).values(name="還原後新門市").returning(Store.id)
    )
    assert new_id is not None


async def test_restore_refuses_nonempty_without_truncate(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed(db_session)
    conn = await _conn(db_session)
    out = tmp_path / "backup"
    await backup_database(conn, out)

    with pytest.raises(RestoreError, match="非空"):
        await restore_database(conn, out, truncate=False)


async def test_restore_refuses_schema_version_mismatch(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    await _seed(db_session)
    conn = await _conn(db_session)
    out = tmp_path / "backup"
    await backup_database(conn, out)

    # 竄改 manifest 的 alembic 版本，模擬 schema 不符。
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["alembic_revision"] = "deadbeefcafe"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RestoreError, match="schema 版本不符"):
        await restore_database(conn, out, truncate=True)

    # force 可繞過版本檢查。
    inserted = await restore_database(conn, out, truncate=True, force=True)
    assert inserted["contacts"] == 1


async def test_backup_is_read_only(db_session: AsyncSession, tmp_path: Path) -> None:
    await _seed(db_session)
    conn = await _conn(db_session)
    before = await conn.scalar(select(Contact.__table__.c.id).limit(1))
    await backup_database(conn, tmp_path / "b1")
    count = len((await conn.execute(select(Contact.__table__))).all())
    after = await conn.scalar(select(Contact.__table__.c.id).limit(1))
    assert count == 1
    assert before == after
