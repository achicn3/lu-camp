"""開發/測試用寄售帳篷 seed（**非 migration、勿在正式環境執行**）。

供「官網寄售帳篷」頁（/consignment/tents，docs/97）的瀏覽器 E2E 使用：建立幾頂在庫的寄售帳篷
（含品牌），並確保現金抽屜為開帳中（煙霧最後要在 POS 結帳）。

前置：先跑 seed_dev_store（門市 id=1）與 seed_dev_user（dev-manager）。
重跑安全：以固定 item_code 為鍵，已存在即略過該筆。

執行（需明確 opt-in，且 APP_ENV 須為 development/test）：

    cd backend && ALLOW_DEV_SEED=true \
        uv run python -m app.scripts.seed_dev_tents
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.main  # noqa: F401  # 觸發全部模型註冊到 metadata（FK 解析）
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import Brand
from app.modules.inventory.repository import InventoryRepository
from app.modules.inventory.service import InventoryService
from app.shared.enums import Grade, OwnershipType

_ALLOWED_ENVS = frozenset({"development", "test"})

# (序號, 品牌, 品名, 售價)
_SPECS = [
    ("TENT-WEB-01", "Snow Peak", "Amenity Dome M", "6800"),
    ("TENT-WEB-02", "DOD", "Kamaboko Tent 3M", "12800"),
    ("TENT-WEB-03", "OGAWA", "Pilz 15", "13800"),
]


def _ensure_dev_environment() -> None:
    app_env = get_settings().app_env
    if app_env not in _ALLOWED_ENVS:
        raise SystemExit(
            f"拒絕在 APP_ENV={app_env!r} 執行 dev seed（僅限 {sorted(_ALLOWED_ENVS)}）"
        )
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需明確 opt-in：設定 ALLOW_DEV_SEED=true 才執行")


async def _brand_id(session: AsyncSession, store_id: int, name: str) -> int:
    brand = await session.scalar(
        select(Brand).where(Brand.store_id == store_id, Brand.name == name)
    )
    if brand is None:
        brand = Brand(store_id=store_id, name=name)
        session.add(brand)
        await session.flush()
    return brand.id


async def _seed(store_id: int, user_id: int) -> int:
    sm = get_sessionmaker()
    created = 0
    async with sm() as session:
        if await CashDrawerService(session).get_current_session(store_id) is None:
            await CashDrawerService(session).open_session(store_id, user_id, Decimal("2000"))
        repo = InventoryRepository(session)
        for code, brand, name, price in _SPECS:
            if await repo.get_serialized_by_code(store_id, code) is not None:
                continue
            consignor = Contact(
                store_id=store_id,
                name=f"寄售人 {code}",
                phone=f"09{code[-2:]}000{code[-2:]}0",
                roles=["MEMBER", "SELLER"],
            )
            session.add(consignor)
            await session.flush()
            await InventoryService(session).create_serialized_item(
                store_id,
                item_code=code,
                name=name,
                brand_id=await _brand_id(session, store_id, brand),
                grade=Grade.A,
                ownership_type=OwnershipType.CONSIGNMENT,
                listed_price=Decimal(price),
                consignor_id=consignor.id,
                commission_pct=30,
            )
            created += 1
        await session.commit()
    return created


def main() -> None:
    _ensure_dev_environment()
    store_id = int(os.environ.get("SEED_STORE_ID", "1"))
    user_id = int(os.environ.get("SEED_USER_ID", "1"))
    created = asyncio.run(_seed(store_id, user_id))
    print(f"seeded {created} in-stock consignment tents + ensured open cash session")


if __name__ == "__main__":
    main()
