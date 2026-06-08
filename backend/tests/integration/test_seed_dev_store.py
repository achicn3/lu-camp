"""dev seed 腳本測試：env 預設值（明顯測試值）＋ upsert 建立／重跑更新同一列。

`app.scripts.seed_dev_store` 為開發/測試輔助（非 migration）：塞一筆「明顯是測試」的
門市抬頭，讓列印（測 A）有完整抬頭、不被 agent 統編把關擋下；真統編下來重跑帶環境
變數即可，程式不動。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.store.models import Store
from app.scripts.seed_dev_store import DevStoreSeed, seed_from_env, upsert_dev_store


def test_seed_from_env_defaults_are_obvious_test_values() -> None:
    seed = seed_from_env({})
    assert seed.store_id == 1
    assert seed.tax_id == "00000000"  # 明顯佔位、非真統編
    assert "測試" in seed.name
    assert seed.invoice_track_info == "ZZ"


def test_seed_from_env_overrides_for_real_values() -> None:
    """真統編下來：只需帶環境變數重跑，程式不動。"""
    seed = seed_from_env(
        {"SEED_STORE_ID": "3", "SEED_STORE_TAX_ID": "12345678", "SEED_STORE_NAME": "露坑"}
    )
    assert seed.store_id == 3
    assert seed.tax_id == "12345678"
    assert seed.name == "露坑"


async def test_upsert_creates_store_when_absent(db_session: AsyncSession) -> None:
    seed = DevStoreSeed(
        store_id=1,
        name="露坑（測試）",
        tax_id="00000000",
        address="（測試地址）",
        phone="02-0000-0000",
        invoice_track_info="ZZ",
    )
    store = await upsert_dev_store(db_session, seed)
    assert store.id == 1
    fetched = await db_session.get(Store, 1)
    assert fetched is not None
    assert fetched.name == "露坑（測試）"
    assert fetched.tax_id == "00000000"


async def test_upsert_updates_same_row_on_rerun(db_session: AsyncSession) -> None:
    """重跑帶真值 → 更新同一列（id 穩定），不新增第二筆。"""
    await upsert_dev_store(
        db_session,
        DevStoreSeed(
            store_id=1,
            name="露坑（測試）",
            tax_id="00000000",
            address="（測試地址）",
            phone="02-0000-0000",
            invoice_track_info="ZZ",
        ),
    )
    store = await upsert_dev_store(
        db_session,
        DevStoreSeed(
            store_id=1,
            name="露坑",
            tax_id="12345678",
            address="台北市",
            phone="02-1234-5678",
            invoice_track_info="AB",
        ),
    )
    assert store.id == 1
    assert store.tax_id == "12345678"
    assert store.name == "露坑"
    count = await db_session.scalar(select(func.count()).select_from(Store))
    assert count == 1


async def test_seed_advances_id_sequence_for_later_normal_insert(
    db_session: AsyncSession,
) -> None:
    """顯式 id seed 後，一般流程（不帶 id）新增門市應拿下一個序號、不撞已 seed 的 id。

    多分店就緒（CLAUDE.md §4）：seed 不可讓之後正常建第二間店因序列未推進而撞 id 失敗。
    """
    await upsert_dev_store(
        db_session,
        DevStoreSeed(
            store_id=1,
            name="露坑（測試）",
            tax_id="00000000",
            address="（測試地址）",
            phone="02-0000-0000",
            invoice_track_info="ZZ",
        ),
    )
    other = Store(name="第二間店")
    db_session.add(other)
    await db_session.flush()  # 未推進序列時 nextval=1 → 撞 id=1、IntegrityError
    assert other.id is not None
    assert other.id != 1
