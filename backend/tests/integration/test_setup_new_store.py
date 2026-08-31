"""新機器開店設定（正式環境用，非開發 seed）。

搬到新機器時要把一間空店建起來：門市抬頭、系統設定、三個帳號。與 `seed_dev_*` 的
差別在於它**要能在正式環境跑**，所以防護不能是「APP_ENV 必須是 development」——
而是「資料庫必須是空的」。真正的災難是有人對著跑了一年的正式庫執行它，把店長密碼
重設掉；空庫檢查擋得住那件事，環境檢查擋不住（正式機的 APP_ENV 本來就是 production）。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.scripts.setup_new_store import (
    NewStoreSetup,
    StoreAlreadyInUse,
    ensure_database_is_empty,
    setup_from_env,
    setup_new_store,
)
from app.shared.enums import UserRole

ENV = {
    "STORE_NAME": "露坑",
    "STORE_TAX_ID": "62106366",
    "STORE_ADDRESS": "臺南市安南區長和路三段94巷六號",
    "STORE_PHONE": "09",
    "MANAGER_USERNAME": "admin",
    "MANAGER_PASSWORD": "manager-pw",
    "CLERK_USERNAME": "clerk",
    "CLERK_PASSWORD": "clerk-pw",
    "KIOSK_USERNAME": "ipad",
    "KIOSK_PASSWORD": "kiosk-pw",
}


def test_passwords_have_no_defaults() -> None:
    """密碼一律由環境變數提供——連預設值都不給，才不會有人「就這樣跑」而留下弱密碼。"""
    for missing in ("MANAGER_PASSWORD", "CLERK_PASSWORD", "KIOSK_PASSWORD"):
        env = {k: v for k, v in ENV.items() if k != missing}
        with pytest.raises(SystemExit, match=missing):
            setup_from_env(env)


def test_store_identity_has_no_defaults() -> None:
    """店名/統編也不給預設：印在客人手上那張發票的抬頭，不該有「不小心沿用」的機會。"""
    for missing in ("STORE_NAME", "STORE_TAX_ID"):
        env = {k: v for k, v in ENV.items() if k != missing}
        with pytest.raises(SystemExit, match=missing):
            setup_from_env(env)


async def test_creates_store_settings_and_three_accounts(db_session: AsyncSession) -> None:
    await setup_new_store(db_session, setup_from_env(ENV))

    store = await db_session.scalar(select(Store).where(Store.tax_id == "62106366"))
    assert store is not None
    assert store.name == "露坑"
    assert store.address == "臺南市安南區長和路三段94巷六號"

    settings = await db_session.scalar(
        select(StoreSettings).where(StoreSettings.store_id == store.id)
    )
    assert settings is not None
    # 店主裁示：發票開關先關著，之後自己打開。開著會在正式帳號還沒開通時就對平台送件。
    assert settings.einvoice_enabled is False
    assert settings.tax_rate == Decimal("0.05")  # 金額/稅率一律 Decimal（§6）
    assert settings.default_commission_pct == 50
    assert settings.default_margin_pct == 45

    users = (await db_session.scalars(select(User).order_by(User.username))).all()
    assert [(u.username, u.role) for u in users] == [
        ("admin", UserRole.MANAGER),
        ("clerk", UserRole.CLERK),
        ("ipad", UserRole.KIOSK),
    ]
    assert all(u.store_id == store.id for u in users)


async def test_passwords_are_hashed_not_stored(db_session: AsyncSession) -> None:
    """密碼不得以任何形式明文落庫。"""
    await setup_new_store(db_session, setup_from_env(ENV))

    users = (await db_session.scalars(select(User))).all()
    for user in users:
        assert "pw" not in user.password_hash
        assert user.password_hash.startswith("$")


async def test_refuses_to_run_on_a_database_that_already_has_a_store(
    db_session: AsyncSession,
) -> None:
    """有店就拒跑：真正的災難是對著跑了一年的正式庫執行，把店長密碼重設掉。

    環境檢查（APP_ENV 必須是 development）在這裡沒有用——正式機的 APP_ENV 本來就是
    production，這支腳本就是要在那裡跑的。能分辨「新機器」與「營業中的機器」的，
    只有資料庫本身是不是空的。
    """
    await setup_new_store(db_session, setup_from_env(ENV))

    with pytest.raises(StoreAlreadyInUse):
        await ensure_database_is_empty(db_session)


async def test_empty_database_passes_the_guard(db_session: AsyncSession) -> None:
    await ensure_database_is_empty(db_session)  # 不應拋出


def test_dine_in_tables_parsed_from_env() -> None:
    """桌號用逗號分隔；沒給就是空清單（沒有內用桌的店照樣要能建起來）。"""
    assert setup_from_env(ENV).dine_in_tables == []
    assert setup_from_env({**ENV, "DINE_IN_TABLES": "A1, A2 ,B1"}).dine_in_tables == [
        "A1",
        "A2",
        "B1",
    ]


def test_setup_is_a_dataclass_carrying_no_secrets_in_repr() -> None:
    """密碼不得出現在 repr／日誌——設定腳本出錯時很容易整包印出來。"""
    setup: NewStoreSetup = setup_from_env(ENV)

    assert "manager-pw" not in repr(setup)
    assert "clerk-pw" not in repr(setup)
    assert "kiosk-pw" not in repr(setup)
