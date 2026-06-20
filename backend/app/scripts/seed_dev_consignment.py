"""開發/測試用寄售結算 seed（**非 migration、勿在正式環境執行**）。

供寄售付款頁（/consignment）的瀏覽器 E2E 使用：建立數筆寄售人 + 寄售序號品，
並各自售出一次以產生 PENDING 結算；同時確保現金抽屜為開帳中（付款頁前置）。

前置：先跑 seed_dev_store（門市 id=1）與 seed_dev_user（dev-manager）。
重跑安全：以固定 item_code 為鍵，已存在即略過該筆。

執行（需明確 opt-in，且 APP_ENV 須為 development/test）：

    cd backend && ALLOW_DEV_SEED=true \
        uv run python -m app.scripts.seed_dev_consignment
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import app.main  # noqa: F401  # 觸發全部模型註冊到 metadata（FK 解析）
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.repository import InventoryRepository
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.shared.enums import Grade, OwnershipType, SaleLineType

_ALLOWED_ENVS = frozenset({"development", "test"})

# (寄售人, 電話, 序號, 品名, 售價, 抽成%)
_SPECS = [
    ("林小姐", "0912-345-678", "CON-TENT-01", "MSR 二人帳篷", "6800", 40),
    ("陳先生", "0922-111-222", "CON-BAG-01", "羽絨睡袋 -10°C", "4200", 50),
    ("王太太", "0933-444-555", "CON-CHAIR-01", "輕量露營椅", "1800", 50),
]


def _ensure_dev_environment() -> None:
    app_env = get_settings().app_env
    if app_env not in _ALLOWED_ENVS:
        raise SystemExit(
            f"拒絕在 APP_ENV={app_env!r} 執行 dev seed（僅限 {sorted(_ALLOWED_ENVS)}）"
        )
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需明確 opt-in：設定 ALLOW_DEV_SEED=true 才執行")


async def _seed(store_id: int, user_id: int) -> int:
    sm = get_sessionmaker()
    created = 0
    async with sm() as session:
        if await CashDrawerService(session).get_current_session(store_id) is None:
            await CashDrawerService(session).open_session(store_id, user_id, Decimal("2000"))
        repo = InventoryRepository(session)
        for name, phone, code, item_name, price, pct in _SPECS:
            if await repo.get_serialized_by_code(store_id, code) is not None:
                continue
            consignor = Contact(store_id=store_id, name=name, phone=phone, roles=["CONSIGNOR"])
            session.add(consignor)
            await session.flush()
            await InventoryService(session).create_serialized_item(
                store_id,
                item_code=code,
                name=item_name,
                grade=Grade.A,
                ownership_type=OwnershipType.CONSIGNMENT,
                listed_price=Decimal(price),
                consignor_id=consignor.id,
                commission_pct=pct,
            )
            await SalesService(session).create_sale(
                store_id,
                user_id,
                lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
                idempotency_key=f"dev-seed-consignment-{code}",
            )
            created += 1
        await session.commit()
    return created


def main() -> None:
    _ensure_dev_environment()
    store_id = int(os.environ.get("SEED_STORE_ID", "1"))
    user_id = int(os.environ.get("SEED_USER_ID", "1"))
    created = asyncio.run(_seed(store_id, user_id))
    print(f"seeded {created} consignment settlements (PENDING) + ensured open cash session")


if __name__ == "__main__":
    main()
