"""散裝販售籃結帳（ADR-025）：一籃一行，後台依先進先出分配到各來源。

情境同 test_bulk_baskets：甲 10 支（總成本 50 → 每支 5）、乙 20 支（總成本 160 → 每支 8），
同籃每支 20 元。賣 12 支 → 甲 10 支＋乙 2 支、成本 50＋16＝66；剩 18 支。
退貨回補依分配紀錄反向（後分配的先回），報表成本反轉用同一個順序逐來源計算。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.basket_repository import BulkBasketRepository
from app.modules.inventory.basket_service import BulkBasketService
from app.modules.inventory.models import BulkLot, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.reports.service import ReportsService
from app.modules.returns.service import ReturnsService
from app.modules.sales.models import GiftReason, SaleBulkAllocation, SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import BulkAcquisitionBasis, BulkLotStatus, Grade, StockDirection, UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    async with CustomerDisplayAwareClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


class Shop:
    def __init__(self, store_id: int, user_id: int, token: str) -> None:
        self.store_id = store_id
        self.user_id = user_id
        self.token = token

    def h(self, idem: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idem is not None:
            headers["Idempotency-Key"] = idem
        return headers


async def _shop(session: AsyncSession, name: str = "籃子門市") -> Shop:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    user = User(
        store_id=store.id, username=f"mgr-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(user)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, user.id, Decimal("1000"))
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=store.id)
    return Shop(store.id, user.id, token)


async def _lot(session: AsyncSession, shop: Shop, *, code: str, qty: int, cost: str) -> BulkLot:
    return await InventoryService(session).create_bulk_lot(
        shop.store_id,
        lot_code=code,
        name="無品牌營釘",
        grade=Grade.E,
        acquisition_cost=Decimal(cost),
        acquisition_basis=BulkAcquisitionBasis.UNSPECIFIED,
        unit_price=Decimal("20"),
        total_qty=qty,
    )


async def _basket_with_two_sources(
    session: AsyncSession, shop: Shop
) -> tuple[int, BulkLot, BulkLot]:
    svc = BulkBasketService(session)
    view = await svc.create(
        shop.store_id, name="無品牌營釘", unit_price=Decimal("20"), actor_user_id=shop.user_id
    )
    a = await _lot(session, shop, code=f"BK-A-{shop.store_id}", qty=10, cost="50")
    b = await _lot(session, shop, code=f"BK-B-{shop.store_id}", qty=20, cost="160")
    for lot in (a, b):
        await svc.add_existing_lot(
            shop.store_id, view.basket.id, lot.id, actor_user_id=shop.user_id
        )
    await session.commit()
    return view.basket.id, a, b


def _basket_line(basket_id: int, qty: int) -> dict[str, object]:
    return {"line_type": "BULK_LOT", "bulk_basket_id": basket_id, "qty": qty}


async def _sell(
    client: httpx.AsyncClient, shop: Shop, basket_id: int, qty: int, idem: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/sales", json={"lines": [_basket_line(basket_id, qty)]}, headers=shop.h(idem)
    )


async def _remaining(session: AsyncSession, *lots: BulkLot) -> list[tuple[int, BulkLotStatus]]:
    out = []
    for lot in lots:
        await session.refresh(lot)
        out.append((lot.remaining_qty, lot.status))
    return out


async def _allocations(session: AsyncSession, sale_line_id: int) -> list[tuple[int, int, int, int]]:
    rows = await session.scalars(
        select(SaleBulkAllocation)
        .where(SaleBulkAllocation.sale_line_id == sale_line_id)
        .order_by(SaleBulkAllocation.id)
    )
    return [(r.bulk_lot_id, r.qty, int(r.cost_snapshot), r.returned_qty) for r in rows.all()]


async def test_basket_line_spans_sources_fifo_with_one_sale_line(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, b = await _basket_with_two_sources(db_session, shop)

    resp = await _sell(client, shop, basket_id, 12, "bk-sale-1")
    assert resp.status_code == 201, resp.text
    lines = resp.json()["lines"]
    assert len(lines) == 1
    assert lines[0]["qty"] == 12
    assert lines[0]["line_total"] == "240"
    assert lines[0]["bulk_basket_id"] == basket_id

    line = await db_session.get(SaleLine, lines[0]["id"])
    assert line is not None
    await db_session.refresh(line)
    assert line.cost_snapshot == 66
    assert line.bulk_basket_id == basket_id
    assert line.bulk_lot_id == a.id  # 代表來源＝第一筆分配（供報表 join 品牌／分類）
    assert await _allocations(db_session, line.id) == [(a.id, 10, 50, 0), (b.id, 2, 16, 0)]
    assert await _remaining(db_session, a, b) == [
        (0, BulkLotStatus.SOLD_OUT),
        (18, BulkLotStatus.ON_SALE),
    ]
    moves = (
        await db_session.scalars(
            select(StockMovement)
            .where(StockMovement.ref_type == "sale", StockMovement.ref_id == resp.json()["id"])
            .order_by(StockMovement.id)
        )
    ).all()
    assert [(m.direction, m.bulk_lot_id, m.qty) for m in moves] == [
        (StockDirection.OUT, a.id, 10),
        (StockDirection.OUT, b.id, 2),
    ]


async def test_quote_matches_checkout_for_basket_line(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, _, _ = await _basket_with_two_sources(db_session, shop)
    quote = await client.post(
        "/api/v1/sales/quote", json={"lines": [_basket_line(basket_id, 12)]}, headers=shop.h()
    )
    assert quote.status_code == 200, quote.text
    assert quote.json()["lines"][0]["line_total"] == "240"


async def test_insufficient_basket_stock_rolls_back_everything(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, b = await _basket_with_two_sources(db_session, shop)
    resp = await _sell(client, shop, basket_id, 31, "bk-sale-over")
    assert resp.status_code == 409, resp.text
    assert await _remaining(db_session, a, b) == [
        (10, BulkLotStatus.ON_SALE),
        (20, BulkLotStatus.ON_SALE),
    ]


async def test_basket_of_other_store_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, _, _ = await _basket_with_two_sources(db_session, shop)
    other = await _shop(db_session, "別家")
    resp = await _sell(client, other, basket_id, 1, "bk-sale-cross")
    assert resp.status_code == 404, resp.text


async def test_bulk_line_rejects_both_lot_and_basket(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, _ = await _basket_with_two_sources(db_session, shop)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "BULK_LOT", "bulk_lot_id": a.id, "bulk_basket_id": basket_id}]
        },
        headers=shop.h("bk-sale-both"),
    )
    assert resp.status_code == 422, resp.text


async def test_idempotent_replay_does_not_allocate_twice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, b = await _basket_with_two_sources(db_session, shop)
    first = await _sell(client, shop, basket_id, 12, "bk-sale-replay")
    replay = await _sell(client, shop, basket_id, 12, "bk-sale-replay")
    assert first.status_code == 201, first.text
    assert replay.status_code in (200, 201), replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert await _remaining(db_session, a, b) == [
        (0, BulkLotStatus.SOLD_OUT),
        (18, BulkLotStatus.ON_SALE),
    ]


async def test_partial_return_restores_latest_allocation_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, b = await _basket_with_two_sources(db_session, shop)
    sale = (await _sell(client, shop, basket_id, 12, "bk-sale-ret")).json()
    line_id = sale["lines"][0]["id"]

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": line_id, "qty": 3}],
        },
        headers=shop.h("bk-ret-1"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["refund_amount"] == "60"
    # 乙分配的 2 支先回、再回甲 1 支。
    assert await _allocations(db_session, line_id) == [(a.id, 10, 50, 1), (b.id, 2, 16, 2)]
    assert await _remaining(db_session, a, b) == [
        (1, BulkLotStatus.ON_SALE),
        (20, BulkLotStatus.ON_SALE),
    ]

    rest = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": line_id, "qty": 9}],
        },
        headers=shop.h("bk-ret-2"),
    )
    assert rest.status_code == 201, rest.text
    assert await _allocations(db_session, line_id) == [(a.id, 10, 50, 10), (b.id, 2, 16, 2)]
    assert await _remaining(db_session, a, b) == [
        (10, BulkLotStatus.ON_SALE),
        (20, BulkLotStatus.ON_SALE),
    ]


async def test_void_restores_every_allocation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, a, b = await _basket_with_two_sources(db_session, shop)
    sale = (await _sell(client, shop, basket_id, 12, "bk-sale-void")).json()
    resp = await client.post(
        f"/api/v1/sales/{sale['id']}/void", json={"reason": "打錯"}, headers=shop.h("bk-void")
    )
    assert resp.status_code == 200, resp.text
    assert await _remaining(db_session, a, b) == [
        (10, BulkLotStatus.ON_SALE),
        (20, BulkLotStatus.ON_SALE),
    ]


async def test_margin_counts_basket_line_and_reverses_cost_by_source(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await _shop(db_session)
    basket_id, _, _ = await _basket_with_two_sources(db_session, shop)
    sale = (await _sell(client, shop, basket_id, 12, "bk-sale-margin")).json()
    start = datetime.now(UTC) - timedelta(hours=1)
    end = datetime.now(UTC) + timedelta(hours=1)
    svc = SalesService(db_session)

    before = await svc.margin_breakdown(shop.store_id, start, end)
    assert before.bulk_cogs == 66
    assert before.gross_margin == 240 - 66

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 3}],
        },
        headers=shop.h("bk-ret-margin"),
    )
    assert resp.status_code == 201, resp.text
    after = await svc.margin_breakdown(shop.store_id, start, end)
    # 退回的 3 支：乙 2 支（16）＋甲 1 支（5）＝21，與實際回到各來源的成本一致。
    assert after.bulk_cogs == 66 - 21
    assert after.gross_margin == (240 - 60) - (66 - 21)


async def test_source_only_in_allocation_cannot_be_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """乙不是代表來源，但它有賣出紀錄；硬刪會讓分配紀錄斷鏈。"""
    shop = await _shop(db_session)
    basket_id, _, b = await _basket_with_two_sources(db_session, shop)
    assert (await _sell(client, shop, basket_id, 12, "bk-sale-del")).status_code == 201
    resp = await client.delete(f"/api/v1/bulk-lots/{b.id}", headers=shop.h())
    assert resp.status_code == 409, resp.text


async def test_insights_margin_uses_allocation_cost_not_representative_lot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """代表來源甲每支 5 元；若洞察拿甲重算 12 支成本會得 60，實際是 66。"""
    shop = await _shop(db_session)
    basket_id, _, _ = await _basket_with_two_sources(db_session, shop)
    assert (await _sell(client, shop, basket_id, 12, "bk-sale-insight")).status_code == 201
    report = await ReportsService(db_session).insights(
        shop.store_id,
        date_from=datetime.now(UTC) - timedelta(hours=1),
        date_to=datetime.now(UTC) + timedelta(hours=1),
    )
    [row] = report.category_breakdown
    assert (row.units_sold, row.revenue, row.margin) == (12, Decimal(240), Decimal(240 - 66))


async def test_baskets_are_locked_in_id_order_regardless_of_cart_order(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """兩台收銀各以 A→B、B→A 結帳時不可互卡：整筆交易先依 id 鎖籃，再逐行處理（Codex 對抗審）。"""
    shop = await _shop(db_session)
    first_id, _, _ = await _basket_with_two_sources(db_session, shop)
    svc = BulkBasketService(db_session)
    second = await svc.create(
        shop.store_id, name="營繩", unit_price=Decimal("30"), actor_user_id=shop.user_id
    )
    lot = await _lot(db_session, shop, code=f"BK-C-{shop.store_id}", qty=5, cost="50")
    lot.unit_price = Decimal("30")
    await svc.add_existing_lot(shop.store_id, second.basket.id, lot.id, actor_user_id=shop.user_id)
    await db_session.commit()

    locked: list[int] = []
    original = BulkBasketRepository.get_for_update

    async def record(self: BulkBasketRepository, store_id: int, basket_id: int) -> object:
        locked.append(basket_id)
        return await original(self, store_id, basket_id)

    monkeypatch.setattr(BulkBasketRepository, "get_for_update", record)
    monkeypatch.setattr(
        BulkBasketRepository,
        "lock_for_sale",
        _recording_lock_for_sale(BulkBasketRepository.lock_for_sale, locked),
    )
    resp = await client.post(
        "/api/v1/sales",
        json={"lines": [_basket_line(second.basket.id, 1), _basket_line(first_id, 1)]},
        headers=shop.h("bk-sale-lock-order"),
    )
    assert resp.status_code == 201, resp.text
    ordered = sorted((first_id, second.basket.id))
    assert locked[:2] == ordered, locked


def _recording_lock_for_sale(original: Any, locked: list[int]) -> Any:
    async def wrapper(
        self: BulkBasketRepository, store_id: int, basket_ids: list[int], lot_ids: list[int]
    ) -> None:
        locked.extend(sorted(basket_ids))
        await original(self, store_id, basket_ids, lot_ids)

    return wrapper


async def test_gift_return_cost_follows_source_allocations(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """販售籃當贈品送出後部分退回：贈品報表沖回的成本＝實際回到各來源的成本（Codex 第三輪）。"""
    shop = await _shop(db_session)
    basket_id, _, _ = await _basket_with_two_sources(db_session, shop)
    reason = GiftReason(store_id=shop.store_id, code="PROMO", name="活動贈品")
    db_session.add(reason)
    await db_session.commit()
    sale = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {**_basket_line(basket_id, 12), "line_kind": "GIFT", "gift_reason_id": reason.id}
            ]
        },
        headers=shop.h("bk-sale-gift"),
    )
    assert sale.status_code == 201, sale.text
    body = sale.json()
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": body["id"],
            "reason": "顧客退回贈品",
            "lines": [{"sale_line_id": body["lines"][0]["id"], "qty": 3}],
        },
        headers=shop.h("bk-ret-gift"),
    )
    assert resp.status_code == 201, resp.text
    [adjustment] = await ReturnsService(db_session).gift_return_adjustments(
        shop.store_id,
        datetime.now(UTC) - timedelta(hours=1),
        datetime.now(UTC) + timedelta(hours=1),
    )
    # 退回 3 支＝乙 2 支（16）＋甲 1 支（5）＝21；整行按比例攤會得 round(66×3/12)=17。
    assert (adjustment.qty, adjustment.cost) == (3, Decimal(21))
