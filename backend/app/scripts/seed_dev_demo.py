"""開發/示範用「大量交易」seed（**非 migration、勿在正式環境執行**）。

目的：灌入跨約 120 天、混合品類（自有序號/寄售序號/散裝/數量型）與一檔活動折扣的大量現金銷售，
讓財務報表（每日儀表板 R5、銷售毛利 R2、趨勢 R6、活動成效 C4）有真實感的資料可看。

作法：庫存直接以 model 建立（與整合測試同法）；銷售經 `SalesService.create_sale`（確保金額/毛利
口徑正確），再把 `sales.created_at` 回填到過去日期以鋪出時間序（報表毛利/趨勢皆以 Sale.created_at
篩選）。皆現金、單一開帳；不灌購物金/退貨以保持可靠。活動折扣於建立當下套用後再回填日期。

前置：先跑 seed_dev_store（門市 id=1）與 seed_dev_user（dev-manager）。重跑會「累加」更多資料。

執行（需明確 opt-in，且 APP_ENV 須為 development/test）：

    cd backend && ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_dev_demo
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

import app.main  # noqa: F401  # 觸發模型註冊（FK 解析）
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
)

_ALLOWED_ENVS = frozenset({"development", "test"})
_RNG = random.Random(20260621)
_NOW = datetime.now(UTC)


def _back(days_ago_from: int, days_ago_to: int) -> datetime:
    """回傳 [days_ago_from, days_ago_to) 天前的隨機時刻（含營業時段時分）。"""
    day = _RNG.uniform(days_ago_to, days_ago_from)
    return _NOW - timedelta(days=day, hours=_RNG.uniform(-4, 4))


async def _seed() -> None:
    settings = get_settings()
    if settings.app_env not in _ALLOWED_ENVS:
        raise SystemExit(f"拒絕執行：APP_ENV={settings.app_env}（僅 development/test）")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需 ALLOW_DEV_SEED=true 明確 opt-in")

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        store = await session.scalar(select(Store).where(Store.id == 1))
        clerk = await session.scalar(select(User).where(User.username == "dev-manager"))
        if store is None or clerk is None:
            raise SystemExit("請先跑 seed_dev_store 與 seed_dev_user")
        store_id, clerk_id = store.id, clerk.id

        # 開帳（現金銷售需開帳中 session）；已開則沿用。
        cash = CashDrawerService(session)
        if await cash.get_current_session(store_id) is None:
            await cash.open_session(store_id, clerk_id, Decimal(5000))
            await session.commit()

        tag = _NOW.strftime("%H%M%S")
        # 寄售人（供寄售序號品 consignor_id）。
        consignors: list[int] = []
        for n in range(3):
            c = Contact(
                store_id=store_id,
                name=f"示範寄售人{n}-{tag}",
                roles=["SELLER"],
                national_id_enc=f"enc-con-{tag}-{n}",
            )
            session.add(c)
            await session.flush()
            consignors.append(c.id)

        # 數量型商品（高庫存，可重複售）。
        catalog_ids: list[int] = []
        for sku, name, price in [
            ("D-GAS", "高山瓦斯罐 230g", "150"),
            ("D-LAMP", "USB 露營燈", "590"),
            ("D-PEG", "鍛造營釘四入", "180"),
            ("D-ROPE", "反光營繩 4mm", "120"),
            ("D-CHAIR", "輕量摺疊椅", "880"),
            ("D-TABLE", "蛋捲桌", "1680"),
            ("D-COOK", "鈦合金鍋具組", "2200"),
            ("D-LIGHT", "氣氛串燈", "320"),
        ]:
            p = CatalogProduct(
                store_id=store_id,
                sku=f"{sku}-{tag}",
                name=name,
                unit_price=Decimal(price),
                quantity_on_hand=100000,
            )
            session.add(p)
            await session.flush()
            catalog_ids.append(p.id)

        # 自有序號品（買斷：有成本）。
        owned_codes: list[str] = []
        for i in range(45):
            price = _RNG.choice([1200, 1800, 2500, 3200, 4500, 6800, 9000])
            cost = int(price * _RNG.uniform(0.45, 0.65))
            code = f"DS{store_id}-OWN{tag}{i:03d}"
            session.add(
                SerializedItem(
                    store_id=store_id,
                    item_code=code,
                    name=f"二手裝備（自有）{i}",
                    grade=_RNG.choice([Grade.A, Grade.B, Grade.C]),
                    ownership_type=OwnershipType.OWNED,
                    acquisition_cost=Decimal(cost),
                    listed_price=Decimal(price),
                    status=SerializedItemStatus.IN_STOCK,
                )
            )
            owned_codes.append(code)

        # 寄售序號品（抽成 40-55%）。
        consign_codes: list[str] = []
        for i in range(18):
            price = _RNG.choice([1500, 2200, 3500, 5000, 8800])
            code = f"DS{store_id}-CON{tag}{i:03d}"
            session.add(
                SerializedItem(
                    store_id=store_id,
                    item_code=code,
                    name=f"二手裝備（寄售）{i}",
                    grade=_RNG.choice([Grade.A, Grade.B]),
                    ownership_type=OwnershipType.CONSIGNMENT,
                    consignor_id=_RNG.choice(consignors),
                    commission_pct=_RNG.choice([40, 45, 50, 55]),
                    listed_price=Decimal(price),
                    status=SerializedItemStatus.IN_STOCK,
                )
            )
            consign_codes.append(code)

        # 自有散裝批（E 級，高剩餘量）。
        bulk_ids: list[int] = []
        for i in range(6):
            total = 500
            unit = _RNG.choice([60, 90, 120, 180])
            cost = int(unit * total * _RNG.uniform(0.4, 0.6))
            lot = BulkLot(
                store_id=store_id,
                lot_code=f"DL{store_id}-{tag}{i:02d}",
                name=f"散裝雜項 E 級 {i}",
                grade=Grade.E,
                acquisition_cost=Decimal(cost),
                acquisition_basis=BulkAcquisitionBasis.BAG,
                unit_price=Decimal(unit),
                total_qty=total,
                remaining_qty=total,
                status=BulkLotStatus.ON_SALE,
            )
            session.add(lot)
            await session.flush()
            bulk_ids.append(lot.id)
        await session.commit()

        sales_svc = SalesService(session)
        owned_pool = list(owned_codes)
        consign_pool = list(consign_codes)
        _RNG.shuffle(owned_pool)
        _RNG.shuffle(consign_pool)
        n_sales = 0
        n_void = 0

        async def one_sale(when: datetime, *, key: str) -> int | None:
            nonlocal n_sales
            lines: list[SaleLineInput] = []
            for _ in range(_RNG.randint(1, 3)):
                roll = _RNG.random()
                if roll < 0.2 and owned_pool:
                    lines.append(
                        SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=owned_pool.pop())
                    )
                elif roll < 0.32 and consign_pool:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.SERIALIZED, item_code=consign_pool.pop()
                        )
                    )
                elif roll < 0.5:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.BULK_LOT,
                            bulk_lot_id=_RNG.choice(bulk_ids),
                            qty=_RNG.randint(1, 4),
                        )
                    )
                else:
                    lines.append(
                        SaleLineInput(
                            line_type=SaleLineType.CATALOG,
                            catalog_product_id=_RNG.choice(catalog_ids),
                            qty=_RNG.randint(1, 3),
                        )
                    )
            if not lines:
                return None
            sale = await sales_svc.create_sale(
                store_id, clerk_id, lines=lines, idempotency_key=key
            )
            sale.created_at = when  # 回填日期鋪出時間序（報表以 Sale.created_at 篩選）
            session.add(sale)
            await session.commit()
            n_sales += 1
            return sale.id

        # Phase A：無活動，120~15 天前的日常銷售。
        for i in range(200):
            sid = await one_sale(_back(120, 15), key=f"demoA-{tag}-{i}")
            if sid is not None and _RNG.random() < 0.04:  # ~4% 作廢
                got = await sales_svc.get_sale(store_id, sid)
                if got is not None:
                    await sales_svc.void_sale(got, clerk_id)
                    await session.commit()
                    n_void += 1

        # Phase B：一檔「開幕九折」活動（窗 [14 天前, 7 天後)），近 14 天的折扣銷售。
        camp_svc = CampaignService(session)
        camp = await camp_svc.create_campaign(
            store_id,
            name=f"開幕九折-{tag}",
            discount_pct=10,
            starts_at=_NOW - timedelta(days=14),
            ends_at=_NOW + timedelta(days=7),
            applies_owned_serialized=True,
            applies_owned_bulk=True,
            applies_catalog=True,
            applies_consignment=False,
            created_by=clerk_id,
        )
        await camp_svc.activate(store_id, camp.id, actor_user_id=clerk_id)
        await session.commit()
        for i in range(60):
            await one_sale(_back(14, 0), key=f"demoB-{tag}-{i}")

        print(
            f"seed_dev_demo 完成：sales={n_sales}（含作廢 {n_void}）、"
            f"自有序號 45、寄售序號 18、散裝 6、數量型 8、活動 1（開幕九折，近 14 天）。"
        )


if __name__ == "__main__":
    asyncio.run(_seed())
