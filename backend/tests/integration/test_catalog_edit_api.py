"""一般商品的編輯與停售（2026-09-17 裁示）。

補上刪除做不到的那一半：進過貨、賣過的商品刪不掉（紀錄要留著），但**打錯字要能改、
不賣了要能從清單消失**。原本兩者都沒有，誤建的商品只能一直掛在庫存頁上。

- 編輯：品名／品牌／型號／分類／再訂購點。**SKU 不給改**——它就是標籤上的條碼，
  改了已印出去的標籤會掃不到。
- 停售：只是從庫存清單與 POS 消失；採購單、交易紀錄、報表一律不動，庫存數量也還在。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, mgr.id, Decimal(1000))
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
    )


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _product(session: AsyncSession, store_id: int, *, sku: str, name: str) -> int:
    product = CatalogProduct(
        store_id=store_id, sku=sku, name=name, unit_price=Decimal(100), quantity_on_hand=5
    )
    session.add(product)
    await session.flush()
    return product.id


async def test_rename_product(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """打錯字要能改，而且賣過的也能改——品名不是帳的一部分。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="EDIT-1", name="瓦斯罐（打錯）")

    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}",
        json={"name": "高山瓦斯罐 230g"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "高山瓦斯罐 230g"
    assert resp.json()["sku"] == "EDIT-1"  # 條碼不動


async def test_sold_product_can_still_be_renamed_without_touching_history(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """改名不會改寫已成交的明細：交易紀錄存的是成交當下的品名快照。"""
    mgr, clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="SOLD-EDIT", name="舊名稱")
    sale = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}]},
        headers=_auth(clerk, "edit-sale"),
    )
    assert sale.status_code == 201, sale.text

    renamed = await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"name": "新名稱"}, headers=_auth(mgr)
    )
    assert renamed.status_code == 200, renamed.text

    detail = await client.get(f"/api/v1/sales/{sale.json()['id']}", headers=_auth(mgr))
    assert detail.json()["lines"][0]["description"] == "舊名稱"


async def test_blank_name_rejected(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="BLANK-1", name="原名")
    blank = await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"name": "   "}, headers=_auth(mgr)
    )
    assert blank.status_code == 422


async def test_edit_is_audited(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """改品名是會影響對外顯示的操作，要留前後值。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="AUD-1", name="原名")

    await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"name": "改過的名字"}, headers=_auth(mgr)
    )
    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_CATALOG_PRODUCT")
    )
    assert log is not None
    assert log.before["name"] == "原名"
    assert log.after["name"] == "改過的名字"


async def test_clerk_cannot_edit(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="RBAC-1", name="商品")
    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"name": "x"}, headers=_auth(clerk)
    )
    assert resp.status_code == 403


async def test_discontinue_hides_from_list_and_pos_but_keeps_records(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停售＝從清單與 POS 消失；庫存數量、採購單、交易紀錄一概不動。"""
    mgr, clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="STOP-1", name="不賣了")

    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}",
        json={"is_active": False},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    listed = await client.get("/api/v1/catalog-products?q=STOP", headers=_auth(mgr))
    assert [p["sku"] for p in listed.json()] == []

    with_inactive = await client.get(
        "/api/v1/catalog-products?q=STOP&include_inactive=true", headers=_auth(mgr)
    )
    assert [p["sku"] for p in with_inactive.json()] == ["STOP-1"]

    # POS 掃不到
    scanned = await client.get("/api/v1/catalog-products/by-sku/STOP-1", headers=_auth(clerk))
    assert scanned.status_code == 404

    # 資料還在，庫存數量沒被動過
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    assert product.quantity_on_hand == 5


async def test_discontinued_product_cannot_be_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """就算前端硬帶 id 進來也要擋：停售的東西不能結帳（服務層才是權威）。"""
    mgr, clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="STOP-2", name="停售品")
    await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"is_active": False}, headers=_auth(mgr)
    )

    sale = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}]},
        headers=_auth(clerk, "stopped-sale"),
    )
    assert sale.status_code == 422, sale.text
    assert "停售" in sale.text


async def test_reactivate(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """停售是可逆的：想再賣就恢復上架。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="BACK-1", name="回來賣")
    await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"is_active": False}, headers=_auth(mgr)
    )
    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"is_active": True}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    listed = await client.get("/api/v1/catalog-products?q=BACK", headers=_auth(mgr))
    assert [p["sku"] for p in listed.json()] == ["BACK-1"]
