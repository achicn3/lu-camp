"""開發/測試用 store seed（**非 migration、勿在正式環境執行**）。

塞入一筆「明顯是測試」的門市抬頭，讓列印功能（測 A：EPSON 收據聯）有完整抬頭、
不被 agent 統編把關擋下（見 hardware-agent `store_client.py` 不變量）。

值一律由**環境變數讀取、預設為明顯測試值**（不寫死任何真統編）：

    SEED_STORE_ID                 預設 1   （穩定目標列，upsert 以此為鍵）
    SEED_STORE_NAME               預設「露坑（測試）」
    SEED_STORE_TAX_ID             預設「00000000」  ← 明顯佔位，非真統編
    SEED_STORE_ADDRESS            預設「（測試地址）」
    SEED_STORE_PHONE              預設「02-0000-0000」
    SEED_STORE_INVOICE_TRACK_INFO 預設「ZZ」        ← 測試字軌（供日後測 B 發票格式）

真統編下來後**程式不動**，只需帶環境變數重跑、更新同一列（id 穩定）：

    SEED_STORE_TAX_ID=<真統編> SEED_STORE_NAME=露坑 \
        uv run python -m app.scripts.seed_dev_store

執行：``cd backend && uv run python -m app.scripts.seed_dev_store``
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.store.models import Store


@dataclass(frozen=True)
class DevStoreSeed:
    """一筆開發/測試門市抬頭的塞入值。"""

    store_id: int
    name: str
    tax_id: str
    address: str
    phone: str
    invoice_track_info: str


def seed_from_env(env: Mapping[str, str] | None = None) -> DevStoreSeed:
    """由環境變數組出 seed；未設者用明顯測試預設值。"""
    resolved = os.environ if env is None else env
    return DevStoreSeed(
        store_id=int(resolved.get("SEED_STORE_ID", "1")),
        name=resolved.get("SEED_STORE_NAME", "露坑（測試）"),
        tax_id=resolved.get("SEED_STORE_TAX_ID", "00000000"),
        address=resolved.get("SEED_STORE_ADDRESS", "（測試地址）"),
        phone=resolved.get("SEED_STORE_PHONE", "02-0000-0000"),
        invoice_track_info=resolved.get("SEED_STORE_INVOICE_TRACK_INFO", "ZZ"),
    )


async def upsert_dev_store(session: AsyncSession, seed: DevStoreSeed) -> Store:
    """以 ``seed.store_id`` 為穩定鍵 upsert 門市：存在則更新、否則建立；回傳該列。"""
    store = await session.get(Store, seed.store_id)
    if store is None:
        store = Store(id=seed.store_id)
        session.add(store)
    store.name = seed.name
    store.tax_id = seed.tax_id
    store.address = seed.address
    store.phone = seed.phone
    store.invoice_track_info = seed.invoice_track_info
    await session.flush()
    return store


async def main() -> None:
    """讀環境變數 → upsert dev store → commit，並印出結果摘要供核對。"""
    seed = seed_from_env()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        store = await upsert_dev_store(session, seed)
        await session.commit()
        print(  # 開發腳本 CLI 輸出
            f"seeded dev store id={store.id} name={store.name!r} "
            f"tax_id={store.tax_id!r} invoice_track_info={store.invoice_track_info!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
