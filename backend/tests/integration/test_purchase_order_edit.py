"""採購單事後修改（docs/70 §4、ADR-030）：草稿全員可改；已下單／已收貨限管理者；
已收數量差額自動調庫存；最近一次進貨才同步商品成本；全部寫稽核。"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.user.models import User
from app.shared.enums import StockDirection, StockReason, UserRole
from tests.integration.test_purchasing_api import (
    _auth,
    _create_po,
    _create_supplier,
    _po_lines,
    _receive_all,
    _seed_catalog,
    _seed_store,
)


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


async def _manager_token(session: AsyncSession, store_id: int) -> str:
    manager = User(
        store_id=store_id, username=f"mgr-{store_id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(manager)
    await session.flush()
    return encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)


async def _put(
    client: httpx.AsyncClient, token: str, po_id: int, body: dict[str, Any]
) -> httpx.Response:
    return await client.put(f"/api/v1/purchase-orders/{po_id}", json=body, headers=_auth(token))


def _line(
    catalog_id: int, qty: int, cost: str, *, line_id: int | None = None, received: int = 0
) -> dict[str, Any]:
    return {
        "id": line_id,
        "catalog_product_id": catalog_id,
        "qty": qty,
        "received_qty": received,
        "unit_cost": cost,
    }


async def _qty(session: AsyncSession, catalog_id: int) -> int:
    product = await session.get(CatalogProduct, catalog_id, populate_existing=True)
    assert product is not None
    return product.quantity_on_hand


async def _corrections(session: AsyncSession, catalog_id: int) -> list[StockMovement]:
    rows = await session.scalars(
        select(StockMovement)
        .where(
            StockMovement.catalog_product_id == catalog_id,
            StockMovement.reason == StockReason.PURCHASE_CORRECTION,
        )
        .order_by(StockMovement.id)
    )
    return list(rows.all())


async def test_clerk_edits_a_draft_lines_and_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="草稿修改店")
    a = await _seed_catalog(db_session, store_id, sku="ED-A")
    b = await _seed_catalog(db_session, store_id, sku="ED-B")
    supplier = await _create_supplier(client, token, name="甲")
    other = await _create_supplier(client, token, name="乙")
    po_id = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=3, submit=False
    )
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        token,
        po_id,
        {"supplier_id": other, "lines": [_line(a, 5, "130", line_id=line_id), _line(b, 2, "50")]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "DRAFT"
    assert body["supplier_id"] == other and body["supplier_name"] == "乙"
    lines = {ln["catalog_product_id"]: ln for ln in body["lines"]}
    assert lines[a]["id"] == line_id and lines[a]["qty"] == 5 and lines[a]["unit_cost"] == "130"
    assert lines[b]["qty"] == 2
    assert body["total_cost"] == "750"


async def test_removing_a_line_deletes_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="刪列店")
    a = await _seed_catalog(db_session, store_id, sku="RM-A")
    b = await _seed_catalog(db_session, store_id, sku="RM-B")
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=3, submit=False
    )
    resp = await _put(client, token, po_id, {"supplier_id": supplier, "lines": [_line(b, 1, "9")]})
    assert resp.status_code == 200, resp.text
    assert [ln["catalog_product_id"] for ln in resp.json()["lines"]] == [b]


async def test_clerk_cannot_edit_an_ordered_order(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="店員不可改店")
    a = await _seed_catalog(db_session, store_id, sku="CL-A")
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=3)
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        token,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 4, "120", line_id=line_id)]},
    )
    assert resp.status_code == 403


async def test_ordered_order_cannot_claim_received_quantities(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """還沒收過貨的單不能靠修改把「已收」填上去（收貨要走收貨流程）。"""
    token, store_id, _ = await _seed_store(db_session, name="未收不可填已收店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="OR-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=3)
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 3, "120", line_id=line_id, received=3)]},
    )
    assert resp.status_code == 422
    assert await _qty(db_session, a) == 0


async def test_manager_lowers_received_qty_and_stock_follows(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="收貨數改少店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="LO-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=20)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    assert await _qty(db_session, a) == 20
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 2, "120", line_id=line_id, received=2)]},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RECEIVED"
    assert await _qty(db_session, a) == 2
    [movement] = await _corrections(db_session, a)
    assert movement.direction == StockDirection.OUT and movement.qty == 18
    assert movement.ref_type == "purchase_order" and movement.ref_id == po_id


async def test_lowering_received_below_what_is_left_is_blocked(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已經賣掉、庫存不夠扣 → 整筆擋下，什麼都不改。"""
    token, store_id, _ = await _seed_store(db_session, name="不夠扣店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="NE-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=10)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    product = await db_session.get(CatalogProduct, a)
    assert product is not None
    product.quantity_on_hand = 3  # 其餘 7 件已賣出
    await db_session.commit()  # 端點失敗會 rollback；先落地，免得一起被撤掉
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 2, "120", line_id=line_id, received=2)]},
    )

    assert resp.status_code == 409
    assert "只剩 3 件" in resp.json()["detail"]
    assert await _qty(db_session, a) == 3
    assert (await _po_lines(client, token, po_id))[0]["received_qty"] == 10


async def test_swapping_a_wrong_product_moves_the_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """選錯商品：移除錯的列、加對的列並填已收 → 錯的扣回、對的加上。"""
    token, store_id, _ = await _seed_store(db_session, name="換商品店")
    manager = await _manager_token(db_session, store_id)
    wrong = await _seed_catalog(db_session, store_id, sku="SW-W", qty=0)
    right = await _seed_catalog(db_session, store_id, sku="SW-R", qty=1)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=wrong, qty=4)
    assert (await _receive_all(client, token, po_id)).status_code == 200

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(right, 4, "120", received=4)]},
    )

    assert resp.status_code == 200, resp.text
    assert await _qty(db_session, wrong) == 0
    assert await _qty(db_session, right) == 5
    [out] = await _corrections(db_session, wrong)
    [into] = await _corrections(db_session, right)
    assert (out.direction, out.qty) == (StockDirection.OUT, 4)
    assert (into.direction, into.qty) == (StockDirection.IN, 4)


async def test_adding_an_unreceived_line_turns_the_order_partial(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="漏加店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="AD-A", qty=0)
    b = await _seed_catalog(db_session, store_id, sku="AD-B", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=2)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {
            "supplier_id": supplier,
            "lines": [_line(a, 2, "120", line_id=line_id, received=2), _line(b, 3, "40")],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PARTIAL"
    assert body["received_at"] is None
    assert await _qty(db_session, b) == 0


async def test_cost_change_on_the_latest_purchase_updates_product_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="成本同步店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="CO-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=2, unit_cost="120"
    )
    assert (await _receive_all(client, token, po_id)).status_code == 200
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 2, "100", line_id=line_id, received=2)]},
    )

    assert resp.status_code == 200, resp.text
    product = await db_session.get(CatalogProduct, a, populate_existing=True)
    assert product is not None and product.unit_cost == Decimal(100)


async def test_cost_change_on_an_older_purchase_leaves_product_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="舊單不動成本店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="OL-A", qty=0)
    supplier = await _create_supplier(client, token)
    old = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=2, unit_cost="120"
    )
    assert (await _receive_all(client, token, old, key="old")).status_code == 200
    new = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=2, unit_cost="150"
    )
    assert (await _receive_all(client, token, new, key="new")).status_code == 200
    line_id = (await _po_lines(client, token, old))[0]["id"]

    resp = await _put(
        client,
        manager,
        old,
        {"supplier_id": supplier, "lines": [_line(a, 2, "90", line_id=line_id, received=2)]},
    )

    assert resp.status_code == 200, resp.text
    product = await db_session.get(CatalogProduct, a, populate_existing=True)
    assert product is not None and product.unit_cost == Decimal(150)


async def test_removing_the_latest_purchase_falls_back_to_the_previous_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """最近一次進貨的商品被換掉（選錯商品）→ 成本退回前一次進貨的進價。"""
    token, store_id, _ = await _seed_store(db_session, name="退回前次成本店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="FB-A", qty=0)
    b = await _seed_catalog(db_session, store_id, sku="FB-B", qty=0)
    supplier = await _create_supplier(client, token)
    old = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=2, unit_cost="120"
    )
    assert (await _receive_all(client, token, old, key="fb-old")).status_code == 200
    new = await _create_po(
        client, token, supplier_id=supplier, catalog_product_id=a, qty=2, unit_cost="999"
    )
    assert (await _receive_all(client, token, new, key="fb-new")).status_code == 200

    resp = await _put(
        client, manager, new, {"supplier_id": supplier, "lines": [_line(b, 2, "999", received=2)]}
    )

    assert resp.status_code == 200, resp.text
    product_a = await db_session.get(CatalogProduct, a, populate_existing=True)
    product_b = await db_session.get(CatalogProduct, b, populate_existing=True)
    assert product_a is not None and product_a.unit_cost == Decimal(120)
    assert product_b is not None and product_b.unit_cost == Decimal(999)


async def test_edit_is_audited_with_before_and_after(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="稽核店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="AU-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=2)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 1, "120", line_id=line_id, received=1)]},
    )
    assert resp.status_code == 200, resp.text

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "UPDATE_PURCHASE_ORDER", AuditLog.entity_id == str(po_id)
        )
    )
    assert log is not None
    assert log.before is not None and log.after is not None
    assert log.before["lines"][0]["received_qty"] == 2
    assert log.after["lines"][0]["received_qty"] == 1


async def test_validation_rejects_bad_edits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="驗證店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="VA-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=2)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    received_over_qty = _line(a, 2, "120", line_id=line_id, received=3)
    duplicate = [_line(a, 2, "120", line_id=line_id, received=2), _line(a, 1, "5")]
    for lines in ([received_over_qty], duplicate, []):
        resp = await _put(client, manager, po_id, {"supplier_id": supplier, "lines": lines})
        assert resp.status_code == 422, (lines, resp.text)

    foreign = await _put(
        client,
        manager,
        po_id,
        {"supplier_id": supplier, "lines": [_line(a, 2, "120", line_id=999_999, received=2)]},
    )
    assert foreign.status_code == 422


async def test_cancelled_order_cannot_be_edited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="取消不可改店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="CA-A")
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=2)
    cancel = await client.post(f"/api/v1/purchase-orders/{po_id}/cancel", headers=_auth(token))
    assert cancel.status_code == 200
    resp = await _put(
        client, manager, po_id, {"supplier_id": supplier, "lines": [_line(a, 1, "1")]}
    )
    assert resp.status_code == 409


async def test_resending_the_same_edit_changes_nothing_more(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="重送店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="RS-A", qty=0)
    supplier = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier, catalog_product_id=a, qty=5)
    assert (await _receive_all(client, token, po_id)).status_code == 200
    line_id = (await _po_lines(client, token, po_id))[0]["id"]
    body = {"supplier_id": supplier, "lines": [_line(a, 3, "120", line_id=line_id, received=3)]}

    assert (await _put(client, manager, po_id, body)).status_code == 200
    assert (await _put(client, manager, po_id, body)).status_code == 200

    assert await _qty(db_session, a) == 3
    assert len(await _corrections(db_session, a)) == 1
