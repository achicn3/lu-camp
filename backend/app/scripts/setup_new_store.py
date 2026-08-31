"""新機器開店設定：把一間空店建起來（門市抬頭、系統設定、三個帳號）。

**與 `seed_dev_*` 的差別**：那些是開發輔助，明確拒絕在正式環境執行。這支相反——
它就是要在正式機上跑的，所以防護不能是「APP_ENV 必須是 development」（正式機的
APP_ENV 本來就是 production）。真正要防的災難是**有人對著跑了一年的正式庫執行它**，
把店長密碼重設掉、或憑空多出一間店。能分辨「新機器」與「營業中的機器」的，只有
資料庫本身是不是空的，所以防護是「已經有門市就拒跑」。

所有值都由環境變數提供，**密碼與店家識別（店名/統編）連預設值都不給**：有預設就會
有人「就這樣跑」，然後正式發票上印著測試抬頭、或留下一組人人都知道的密碼。

執行（先 `alembic upgrade head` 建好空的資料表）：

    cd backend && \
      STORE_NAME=露坑 STORE_TAX_ID=62106366 \
      STORE_ADDRESS=... STORE_PHONE=09 \
      MANAGER_USERNAME=admin MANAGER_PASSWORD=... \
      CLERK_USERNAME=clerk CLERK_PASSWORD=... \
      KIOSK_USERNAME=ipad KIOSK_PASSWORD=... \
      DINE_IN_TABLES=A1,A2,B1 \
      uv run python -m app.scripts.setup_new_store

稅率 5%、寄售抽成 50%、目標毛利 45%、發票開關預設關——這些沿用 `settings` 的欄位
預設（CLAUDE.md §6：不寫死於程式邏輯），需要調整請在設定頁改，不要改這支腳本。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.modules.sales.reasons import ensure_default_reasons
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


class StoreAlreadyInUse(RuntimeError):
    """資料庫裡已經有門市——這台不是新機器，拒絕設定。"""


@dataclass(frozen=True)
class Account:
    """一個要建立的帳號。`password` 標記為不入 repr，避免出錯時整包被印進日誌。"""

    username: str
    role: UserRole
    password: str = field(repr=False)


@dataclass(frozen=True)
class NewStoreSetup:
    """一間新店的完整設定值。"""

    name: str
    tax_id: str
    address: str
    phone: str
    invoice_track_info: str
    dine_in_tables: list[str]
    accounts: tuple[Account, ...] = field(repr=False)


_REQUIRED: Final = (
    ("STORE_NAME", "店名（會印在發票證明聯最上面那一行、收據與明細聯的抬頭）"),
    ("STORE_TAX_ID", "統一編號（發票上的營業人統編）"),
    ("MANAGER_PASSWORD", "管理者密碼"),
    ("CLERK_PASSWORD", "店員密碼"),
    ("KIOSK_PASSWORD", "顧客簽署裝置密碼"),
)


def setup_from_env(env: Mapping[str, str] | None = None) -> NewStoreSetup:
    """由環境變數組出設定；密碼與店家識別必填、無預設值。"""
    resolved = os.environ if env is None else env
    for name, what in _REQUIRED:
        if not (resolved.get(name) or "").strip():
            raise SystemExit(f"{name} 未設定（{what}）。這支腳本不提供預設值，請明確指定。")
    tables = [t.strip() for t in (resolved.get("DINE_IN_TABLES") or "").split(",") if t.strip()]
    return NewStoreSetup(
        name=resolved["STORE_NAME"].strip(),
        tax_id=resolved["STORE_TAX_ID"].strip(),
        address=(resolved.get("STORE_ADDRESS") or "").strip(),
        phone=(resolved.get("STORE_PHONE") or "").strip(),
        invoice_track_info=(resolved.get("STORE_INVOICE_TRACK_INFO") or "").strip(),
        dine_in_tables=tables,
        accounts=(
            Account(
                username=(resolved.get("MANAGER_USERNAME") or "admin").strip(),
                role=UserRole.MANAGER,
                password=resolved["MANAGER_PASSWORD"],
            ),
            Account(
                username=(resolved.get("CLERK_USERNAME") or "clerk").strip(),
                role=UserRole.CLERK,
                password=resolved["CLERK_PASSWORD"],
            ),
            Account(
                username=(resolved.get("KIOSK_USERNAME") or "ipad").strip(),
                role=UserRole.KIOSK,
                password=resolved["KIOSK_PASSWORD"],
            ),
        ),
    )


async def ensure_database_is_empty(session: AsyncSession) -> None:
    """已經有門市就拒跑——這台不是新機器。

    刻意查 `stores` 而不是查交易：一間剛設好但還沒開張的店交易數為零，用交易數判斷
    會讓腳本可以重跑並覆寫掉剛設定好的密碼。門市存在＝有人已經設定過了。
    """
    count = await session.scalar(select(func.count()).select_from(Store))
    if count:
        raise StoreAlreadyInUse(
            f"資料庫裡已經有 {count} 間門市，這台不是新機器。"
            "若確定要重來，請先確認沒有任何營業資料，再自行清空資料庫。"
        )


async def setup_new_store(session: AsyncSession, setup: NewStoreSetup) -> Store:
    """建立門市、系統設定與帳號；回傳建立的門市。"""
    store = Store(
        name=setup.name,
        tax_id=setup.tax_id,
        address=setup.address,
        phone=setup.phone,
        invoice_track_info=setup.invoice_track_info,
    )
    session.add(store)
    await session.flush()

    # 稅率/抽成/毛利/發票開關一律沿用欄位預設（CLAUDE.md §6：可設定、不寫死）；
    # 這裡只填「每間店本來就不同」的桌號。
    session.add(StoreSettings(store_id=store.id, dine_in_tables=setup.dine_in_tables))

    for account in setup.accounts:
        session.add(
            User(
                username=account.username,
                password_hash=hash_password(account.password),
                role=account.role,
                store_id=store.id,
            )
        )

    # 贈品原因是贈品的必填欄位：沒有原因代碼，POS 的贈品選單會是空的、送不出贈品。
    await ensure_default_reasons(session, store.id)
    await session.flush()
    return store


async def main() -> None:
    """空庫檢查 → 讀環境變數 → 建店 → commit，印出摘要（絕不印密碼）。"""
    setup = setup_from_env()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await ensure_database_is_empty(session)
        store = await setup_new_store(session, setup)
        await session.commit()
        print(f"已建立門市 id={store.id} name={store.name!r} tax_id={store.tax_id!r}")
        for account in setup.accounts:
            print(f"  帳號 {account.username}（{account.role}）")
        print(f"  內用桌號：{setup.dine_in_tables or '（無）'}")
        print("  發票開關：關閉（正式帳號開通後請在設定頁自行開啟）")


if __name__ == "__main__":
    asyncio.run(main())
