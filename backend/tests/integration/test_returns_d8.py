"""D-8 修復（裁示 2026-07-16）：退貨按比例沖點數＋毛利報表扣退貨。

- 沖點：claw = floor(awarded_points × 退款 ÷ 原總額)；點數不足沖時 clamp 至現有（不擋退貨）。
- 報表：margin_components 依退貨行（退貨發生日落在查詢區間）按比例扣減營收與成本。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.inventory.service import InventoryService
from app.modules.returns.models import CustomerReturn
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, UserRole


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession) -> tuple[str, int, int, int]:
    """回 (token, store_id, clerk_id, member_id)；已開帳。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", phone="0911222333", roles=["MEMBER"])
    session.add_all([clerk, member])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    await session.commit()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id, member.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _member_points(session: AsyncSession, member_id: int) -> int:
    return int(
        await session.scalar(select(Contact.member_points).where(Contact.id == member_id)) or 0
    )


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku="SKU-D8",
        name="瓦斯罐",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def test_return_claws_member_points_proportionally(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """total 1000、awarded 10 點；退 300 → 沖 floor(10×300/1000)=3 點。"""
    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=20)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 10}],
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-sale-1"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    assert await _member_points(db_session, member_id) == 10

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "尺寸不合",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 3}],
        },
        headers=_auth(token, idem="d8-ret-1"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 7  # 10 − floor(10×300/1000)

    # 再退 3 件（累計退 600）→ 再沖 3 點；兩次分開按比例、合計不超過 awarded
    resp2 = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "尺寸不合",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 3}],
        },
        headers=_auth(token, idem="d8-ret-2"),
    )
    assert resp2.status_code == 201, resp2.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 4


async def test_return_points_clamp_when_member_spent_them(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """會員點數已被用掉（餘 1）→ 沖點 clamp 至 1、不阻擋退貨。"""
    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=20)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 10}],
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-sale-2"),
    )
    assert sale_resp.status_code == 201
    sale = sale_resp.json()
    # 模擬點數已被花掉：直接把餘額壓到 1
    member = await db_session.get(Contact, member_id)
    assert member is not None
    member.member_points = 1
    await db_session.commit()

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "商品瑕疵",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 5}],
        },
        headers=_auth(token, idem="d8-ret-3"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 0  # clamp：只沖得掉 1


async def test_margin_breakdown_subtracts_returns_in_window(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """毛利報表扣退貨：自有序號品售 3000/成本 500，退貨後營收與成本雙扣。"""
    token, store_id, clerk_id, _ = await _seed(db_session)
    item = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code="S1-D8TEST01",
        name="二手睡墊",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("3000"),
        acquisition_cost=Decimal("500"),
    )
    await db_session.commit()
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "S1-D8TEST01"}]},
        headers=_auth(token, idem="d8-sale-3"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()

    now = datetime.now(UTC)
    sale_row = await db_session.get(Sale, sale["id"])
    assert sale_row is not None
    sale_row.created_at = now - timedelta(days=2)
    await db_session.flush()

    svc = SalesService(db_session)
    sale_from = now - timedelta(days=3)
    sale_to = now - timedelta(days=1)
    before = await svc.margin_breakdown(store_id, sale_from, sale_to)
    assert before.recognized_revenue == Decimal("3000")
    assert before.owned_cogs == Decimal("500")
    assert before.gross_margin == Decimal("2500")
    assert before.cash_received == Decimal("3000")
    assert before.payment_methods == (("CASH", Decimal("3000"), Decimal("0")),)

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "客人反悔",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="d8-ret-4"),
    )
    assert resp.status_code == 201, resp.text
    returned = await db_session.get(CustomerReturn, resp.json()["id"])
    assert returned is not None
    returned.created_at = now
    await db_session.flush()
    db_session.expire_all()

    # 退貨不回寫原銷售期；原銷售期仍完整呈現收款與毛利。
    sale_only = await svc.margin_breakdown(store_id, sale_from, sale_to)
    assert sale_only.recognized_revenue == Decimal("3000")
    assert sale_only.cash_received == Decimal("3000")

    # 退貨期沒有新銷售，退款渠道與營收／成本均以負值歸在退貨日。
    return_only = await svc.margin_breakdown(
        store_id, now - timedelta(hours=1), now + timedelta(days=1)
    )
    assert return_only.recognized_revenue == Decimal("-3000")
    assert return_only.owned_cogs == Decimal("-500")
    assert return_only.gross_margin == Decimal("-2500")
    assert return_only.cash_received == Decimal("-3000")
    assert return_only.payment_methods == (("CASH", Decimal("-3000"), Decimal("0")),)

    clerk = await db_session.get(User, clerk_id)
    assert clerk is not None
    clerk.role = UserRole.MANAGER
    other_store = Store(name="退貨報表隔離門市")
    db_session.add(other_store)
    await db_session.flush()
    other_manager = User(
        store_id=other_store.id,
        username=f"d8-other-{other_store.id}",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    db_session.add(other_manager)
    await db_session.flush()
    manager_token = encode_access_token(user_id=clerk_id, role="MANAGER", store_id=store_id)
    other_token = encode_access_token(
        user_id=other_manager.id, role="MANAGER", store_id=other_store.id
    )
    report_params = {
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(days=1)).isoformat(),
    }
    json_report = await client.get(
        "/api/v1/reports/sales-margin",
        params=report_params,
        headers=_auth(manager_token),
    )
    assert json_report.status_code == 200
    assert json_report.json()["cash_received"] == "-3000"
    assert json_report.json()["payment_methods"] == [
        {"method": "CASH", "received": "-3000", "fee": "0"}
    ]
    csv_report = await client.get(
        "/api/v1/reports/sales-margin",
        params={**report_params, "format": "csv"},
        headers=_auth(manager_token),
    )
    assert csv_report.status_code == 200
    assert "付款方式 CASH 淨收款,'-3000" in csv_report.content.decode("utf-8-sig")
    isolated = await client.get(
        "/api/v1/reports/sales-margin",
        params=report_params,
        headers=_auth(other_token),
    )
    assert isolated.status_code == 200
    assert isolated.json()["cash_received"] == "0"
    assert isolated.json()["payment_methods"] == []

    full_again = await svc.margin_breakdown(store_id, sale_from, now + timedelta(days=1))
    assert full_again.gross_turnover == Decimal("0")
    assert full_again.cash_received == Decimal("0")

    _ = item


async def test_bulk_return_cogs_reversal_is_cumulative(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝差額法：批成本 10/3 件，賣 3 件 COGS=10；三次各退 1 件，累計反轉 COGS=10（非 3×3=9）。"""
    from app.modules.inventory.service import InventoryService
    from app.shared.enums import BulkAcquisitionBasis

    token, store_id, _, _ = await _seed(db_session)
    lot = await InventoryService(db_session).create_bulk_lot(
        store_id,
        lot_code="RET-D8-BULK",
        name="散裝營釘",
        grade=Grade.E,
        acquisition_cost=Decimal("10"),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal("100"),
        total_qty=3,
    )
    await db_session.commit()
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "BULK_LOT", "bulk_lot_id": lot.id, "qty": 3}]},
        headers=_auth(token, idem="d8-bulk-sale"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    line_id = sale["lines"][0]["id"]

    svc = SalesService(db_session)
    t0 = datetime.now(UTC) - timedelta(days=1)
    t1 = datetime.now(UTC) + timedelta(days=1)
    assert (await svc.margin_breakdown(store_id, t0, t1)).bulk_cogs == Decimal("10")

    # 三次各退 1 件
    for i in range(3):
        r = await client.post(
            "/api/v1/returns",
            json={
                "sale_id": sale["id"],
                "reason": "逐件退",
                "lines": [{"sale_line_id": line_id, "qty": 1}],
            },
            headers=_auth(token, idem=f"d8-bulk-ret-{i}"),
        )
        assert r.status_code == 201, r.text
    db_session.expire_all()

    # 全退後：bulk_cogs 淨額 = 10 − 10 = 0（差額法逐次反轉 3+4+3=10，非 3+3+3=9）
    after = await svc.margin_breakdown(store_id, t0, t1)
    assert after.bulk_cogs == Decimal("0"), f"cogs={after.bulk_cogs}（差額法應為 0）"


async def test_return_clawback_uses_non_menu_subtotal_and_cumulative(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """混單（貨 $1000＋餐飲 $1000）awarded=floor(1000/100)=10；退全部貨 → 沖全部 10 點
    （分母是非餐飲小計 $1000，非 total $2000）。Codex 波次二第三輪 P1。"""
    from decimal import Decimal

    from app.modules.menu.models import MenuItem

    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=20)
    menu = MenuItem(store_id=store_id, name="拿鐵", unit_price=Decimal("1000"))
    db_session.add(menu)
    await db_session.flush()
    menu_id = menu.id
    await db_session.commit()

    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 10},
                {"line_type": "MENU", "menu_item_id": menu_id, "qty": 1},
            ],
            # 含餐飲的結帳一律要宣告內用/外帶（docs/35）；本檔驗的是金流，用免桌號的外帶。
            "service_mode": "TAKEOUT",
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-mix-sale"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    assert await _member_points(db_session, member_id) == 10  # floor((2000-1000)/100)
    catalog_line = next(ln for ln in sale["lines"] if ln["line_type"] == "CATALOG")

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退全部貨",
            "lines": [{"sale_line_id": catalog_line["id"], "qty": 10}],
        },
        headers=_auth(token, idem="d8-mix-ret"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    # 退全部非餐飲 → 沖全部 10 點（舊口徑除以 total 只會沖 5）
    assert await _member_points(db_session, member_id) == 0


async def test_return_clawback_rounding_full_return_claws_all(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """$150（3 件×$50）awarded=1；三件分開退 → 差額法累計沖 1（逐次獨立 floor 會殘留 1）。"""
    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="50", qty=20)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 3}],
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-round-sale"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    assert await _member_points(db_session, member_id) == 1
    line_id = sale["lines"][0]["id"]

    for i in range(3):
        r = await client.post(
            "/api/v1/returns",
            json={
                "sale_id": sale["id"],
                "reason": "逐件退",
                "lines": [{"sale_line_id": line_id, "qty": 1}],
            },
            headers=_auth(token, idem=f"d8-round-ret-{i}"),
        )
        assert r.status_code == 201, r.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 0  # 差額法：第三次退才沖到 1
