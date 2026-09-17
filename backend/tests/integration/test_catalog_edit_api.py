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
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import BulkLot, CatalogProduct
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
    assert log.before is not None and log.after is not None
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


async def test_pending_payment_blocks_discontinue(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已扣款、等著補單的購物車指名了這件商品：停售會讓那張單補不出來（錢收了、帳沒有）。

    刪除早就擋了這件事，停售當初漏掉——停售正好是「這東西不賣了」的清理時機，最容易撞上。
    """
    import json as _json

    from sqlalchemy import text

    from tests.integration.customer_display_helpers import ensure_paired_customer_display

    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="PEND-STOP", name="待補單商品")
    actor_id = await db_session.scalar(select(User.id).where(User.store_id == store_id))
    assert actor_id is not None
    terminal, device = await ensure_paired_customer_display(
        db_session, store_id=store_id, actor_user_id=actor_id
    )
    await db_session.execute(
        text(
            "INSERT INTO cart_sessions (store_id, pos_terminal_id, kiosk_device_id, status,"
            " revision, snapshot, snapshot_fingerprint, payment_checkout_payload,"
            " created_at, updated_at)"
            " VALUES (:s, :t, :d, 'PAYMENT_UNCERTAIN', 1, '{}'::jsonb, 'fp',"
            " CAST(:p AS jsonb), now(), now())"
        ),
        {
            "s": store_id,
            "t": terminal.id,
            "d": device.id,
            "p": _json.dumps(
                {"lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}]}
            ),
        },
    )
    await db_session.flush()

    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}",
        json={"is_active": False},
        headers=_auth(mgr),
    )
    assert resp.status_code == 409, resp.text
    assert "待確認付款" in resp.json()["detail"]


async def test_paid_sale_can_be_rebuilt_even_if_product_was_discontinued(
    db_session: AsyncSession,
) -> None:
    """補單是**重建已經發生的交易**：就算商品已停售也必須補得出來，否則錢收了、帳沒有。

    前一支測試擋住「先有待補單、後停售」；這支守反過來的順序（先停售、之後才確認扣款成功），
    以及任何漏網情形。一般結帳仍照擋。
    """
    from decimal import Decimal as _D

    from app.modules.sales.inputs import SaleLineInput, TenderInput
    from app.modules.sales.service import SalesService
    from app.shared.enums import SaleLineType, TenderType
    from app.shared.exceptions import SaleLineInvalid

    _mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="REBUILD-1", name="已停售但已扣款")
    actor_id = await db_session.scalar(select(User.id).where(User.store_id == store_id))
    assert actor_id is not None
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    product.is_active = False
    await db_session.flush()

    lines = [
        SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=1)
    ]
    tenders = [TenderInput(tender_type=TenderType.CASH, amount=_D(100))]

    # 一般結帳照擋
    with pytest.raises(SaleLineInvalid):
        await SalesService(db_session).create_sale(
            store_id, actor_id, lines=lines, tenders=tenders
        )

    # 補單放行（這筆交易已經發生，只是在補帳）
    sale = await SalesService(db_session).create_sale(
        store_id, actor_id, lines=lines, tenders=tenders, rebuilding_paid_sale=True
    )
    assert sale.total == _D(100)


async def test_cross_store_brand_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """別家店的品牌不可掛上來（§4），不存在的 id 要回 422 而不是 500。"""
    from sqlalchemy import text

    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="XSTORE-1", name="商品")
    other = Store(name="別家店")
    db_session.add(other)
    await db_session.flush()
    other_brand_id = await db_session.scalar(
        text("INSERT INTO brands (store_id, name, created_at, updated_at)"
             " VALUES (:s, '別店品牌', now(), now()) RETURNING id"),
        {"s": other.id},
    )

    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}",
        json={"brand_id": other_brand_id},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text


async def test_unknown_brand_id_is_422_not_500(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="BADREF-1", name="商品")
    resp = await client.patch(
        f"/api/v1/catalog-products/{product_id}",
        json={"brand_id": 999999},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text


async def test_rename_serialized_and_bulk(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """序號品與散裝批的改名：權限、他店、空白、稽核前後值。"""
    from decimal import Decimal as _D

    from app.modules.inventory.models import SerializedItem
    from app.shared.enums import (
        BulkAcquisitionBasis,
        BulkLotStatus,
        Grade,
        OwnershipType,
        SerializedItemStatus,
    )

    mgr, clerk, store_id = await _seed(db_session)
    item = SerializedItem(
        store_id=store_id,
        item_code="REN-1",
        name="打錯的帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=_D(500),
        listed_price=_D(1000),
        status=SerializedItemStatus.IN_STOCK,
    )
    lot = BulkLot(
        store_id=store_id,
        lot_code="REN-LOT",
        name="打錯的雜物堆",
        grade=Grade.E,
        acquisition_cost=_D(300),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=_D(50),
        total_qty=10,
        remaining_qty=10,
        status=BulkLotStatus.ON_SALE,
    )
    db_session.add_all([item, lot])
    await db_session.flush()

    # 店員不可改
    assert (
        await client.patch(
            f"/api/v1/serialized-items/{item.id}/name",
            json={"name": "x"},
            headers=_auth(clerk),
        )
    ).status_code == 403

    renamed = await client.patch(
        f"/api/v1/serialized-items/{item.id}/name",
        json={"name": "北歐風帳篷"},
        headers=_auth(mgr),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "北歐風帳篷"
    assert renamed.json()["item_code"] == "REN-1"  # 條碼不動

    lot_renamed = await client.patch(
        f"/api/v1/bulk-lots/{lot.id}/name", json={"name": "露營小物堆"}, headers=_auth(mgr)
    )
    assert lot_renamed.status_code == 200, lot_renamed.text
    assert lot_renamed.json()["name"] == "露營小物堆"

    logs = (
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(("RENAME_SERIALIZED_ITEM", "RENAME_BULK_LOT")))
            .order_by(AuditLog.id)
        )
    ).all()
    pairs = []
    for log in logs:
        assert log.before is not None and log.after is not None
        pairs.append((log.before["name"], log.after["name"]))
    assert pairs == [
        ("打錯的帳篷", "北歐風帳篷"),
        ("打錯的雜物堆", "露營小物堆"),
    ]


def _lot(store_id: int, *, code: str, name: str) -> "BulkLot":
    from decimal import Decimal as _D

    from app.shared.enums import BulkAcquisitionBasis, BulkLotStatus, Grade

    return BulkLot(
        store_id=store_id,
        lot_code=code,
        name=name,
        grade=Grade.E,
        acquisition_cost=_D(100),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=_D(10),
        total_qty=5,
        remaining_qty=5,
        status=BulkLotStatus.ON_SALE,
    )


async def test_rename_rejects_blank_name(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """空白品名擋下——清單上會變成看不出是什麼的空列。"""
    mgr, _, store_id = await _seed(db_session)
    own = _lot(store_id, code="OWN-LOT", name="自己的堆")
    db_session.add(own)
    await db_session.flush()
    blank = await client.patch(
        f"/api/v1/bulk-lots/{own.id}/name", json={"name": "   "}, headers=_auth(mgr)
    )
    assert blank.status_code == 422


async def test_rename_other_store_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """他店的品項一律 404（不洩漏跨店資料）。"""
    mgr, _, _ = await _seed(db_session)
    other = Store(name="別家店")
    db_session.add(other)
    await db_session.flush()
    lot = _lot(other.id, code="OTHER-LOT", name="別店的堆")
    db_session.add(lot)
    await db_session.flush()
    resp = await client.patch(
        f"/api/v1/bulk-lots/{lot.id}/name", json={"name": "改名"}, headers=_auth(mgr)
    )
    assert resp.status_code == 404


async def test_discontinued_product_is_still_countable_in_stocktake(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停售但還有庫存的商品仍要盤得到，否則帳面數量永遠校不回來。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="STOCKTAKE-1", name="停售但有貨")
    await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"is_active": False}, headers=_auth(mgr)
    )

    created = await client.post("/api/v1/stocktakes", json={}, headers=_auth(mgr, "st-1"))
    assert created.status_code == 201, created.text
    detail = await client.get(
        f"/api/v1/stocktakes/{created.json()['id']}", headers=_auth(mgr)
    )
    counted = [line["catalog_product_id"] for line in detail.json()["lines"]]
    assert product_id in counted


async def test_discontinued_sku_still_blocks_duplicates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停售品仍佔著 SKU：拿同一個編號建檔要在服務層被擋，不是撞到唯一鍵才回滾。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, sku="DUP-1", name="停售品")
    await client.patch(
        f"/api/v1/catalog-products/{product_id}", json={"is_active": False}, headers=_auth(mgr)
    )
    resp = await client.post(
        "/api/v1/catalog-products",
        json={"sku": "DUP-1", "name": "新商品", "unit_price": "100", "reorder_point": 0},
        headers=_auth(mgr, "dup-1"),
    )
    assert resp.status_code == 409, resp.text


async def test_zero_stock_discontinued_product_is_not_snapshotted_for_stocktake(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停售又沒庫存的不進盤點單：沒東西可數，卻會因「盤點過不能刪」而永遠刪不掉。"""
    mgr, _, store_id = await _seed(db_session)
    empty = CatalogProduct(
        store_id=store_id, sku="EMPTY-1", name="停售零庫存", unit_price=Decimal(100)
    )
    db_session.add(empty)
    await db_session.flush()
    await client.patch(
        f"/api/v1/catalog-products/{empty.id}", json={"is_active": False}, headers=_auth(mgr)
    )

    created = await client.post("/api/v1/stocktakes", json={}, headers=_auth(mgr, "st-empty"))
    assert created.status_code == 201, created.text
    detail = await client.get(f"/api/v1/stocktakes/{created.json()['id']}", headers=_auth(mgr))
    assert empty.id not in [line["catalog_product_id"] for line in detail.json()["lines"]]
