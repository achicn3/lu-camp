"""開發/測試用一般商品 seed（**非 migration、勿在正式環境執行**）。

供採購/補貨頁（/purchasing）與盤點頁（/stocktake）的瀏覽器 E2E 使用：建立數筆一般商品
（CatalogProduct），其中部分低於補貨點以驗證「低庫存提醒」與盤點差異。

前置：先跑 seed_dev_store（門市 id=1）與 seed_dev_user（dev-manager）。
重跑安全：以固定 sku 為鍵，已存在即略過該筆。

執行（需明確 opt-in，且 APP_ENV 須為 development/test）：

    cd backend && ALLOW_DEV_SEED=true \
        uv run python -m app.scripts.seed_dev_purchasing
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select

import app.main  # noqa: F401  # 觸發全部模型註冊到 metadata（FK 解析）
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.inventory.models import CatalogProduct

_ALLOWED_ENVS = frozenset({"development", "test"})

# (sku, 品名, 售價, 現量, 補貨點)；現量 <= 補貨點 即為低庫存。
_SPECS = [
    ("GAS-230", "高山瓦斯罐 230g", "150", 2, 10),  # 低庫存
    ("ROPE-4MM", "營繩 4mm（10m）", "120", 1, 5),  # 低庫存
    ("PEG-Y", "Y 型營釘（4 入）", "180", 8, 6),  # 充足
    ("LANTERN-USB", "USB 充電營燈", "590", 15, 4),  # 充足
    ("STOVE-CLEAN", "爐具清潔刷", "90", 0, 3),  # 缺貨（低庫存）
]


def _ensure_dev_environment() -> None:
    app_env = get_settings().app_env
    if app_env not in _ALLOWED_ENVS:
        raise SystemExit(
            f"拒絕在 APP_ENV={app_env!r} 執行 dev seed（僅限 {sorted(_ALLOWED_ENVS)}）"
        )
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需明確 opt-in：設定 ALLOW_DEV_SEED=true 才執行")


async def _seed(store_id: int) -> int:
    sm = get_sessionmaker()
    created = 0
    async with sm() as session:
        for sku, name, price, qty, reorder in _SPECS:
            existing = await session.scalar(
                select(CatalogProduct).where(
                    CatalogProduct.store_id == store_id, CatalogProduct.sku == sku
                )
            )
            if existing is not None:
                continue
            session.add(
                CatalogProduct(
                    store_id=store_id,
                    sku=sku,
                    name=name,
                    brand_id=None,
                    unit_price=Decimal(price),
                    quantity_on_hand=qty,
                    reorder_point=reorder,
                )
            )
            created += 1
        await session.commit()
    return created


def main() -> None:
    _ensure_dev_environment()
    store_id = int(os.environ.get("SEED_STORE_ID", "1"))
    created = asyncio.run(_seed(store_id))
    print(f"seeded {created} catalog products (some below reorder point)")


if __name__ == "__main__":
    main()
