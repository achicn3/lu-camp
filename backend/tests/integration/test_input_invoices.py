"""進項發票獨立登錄（docs/70 §5、ADR-030）：一張發票可涵蓋多批收貨（跨採購單、同供應商），
事後可登錄；修改、刪除限管理者並寫稽核。"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.test_purchasing_api import (
    _auth,
    _create_po,
    _create_supplier,
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
        store_id=store_id, username=f"inv-mgr-{store_id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(manager)
    await session.flush()
    return encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)


def _fields(
    number: str = "AB12345678", *, date: str = "2026-09-30", total: int = 1050
) -> dict[str, str]:
    net = round(total / 1.05)
    return {
        "invoice_number": number,
        "invoice_date": date,
        "invoice_net": str(net),
        "invoice_tax": str(total - net),
        "invoice_total": str(total),
    }


async def _received_batch(
    client: httpx.AsyncClient,
    token: str,
    *,
    supplier_id: int,
    catalog_id: int,
    qty: int = 2,
    cost: str = "100",
    invoice: dict[str, str] | None = None,
) -> tuple[int, int]:
    """建一張採購單並收足 → (採購單 id, 收貨批次 id)。"""
    po_id = await _create_po(
        client,
        token,
        supplier_id=supplier_id,
        catalog_product_id=catalog_id,
        qty=qty,
        unit_cost=cost,
    )
    resp = await _receive_all(client, token, po_id, invoice=invoice)
    assert resp.status_code == 200, resp.text
    return po_id, int(resp.json()["receipt_id"])


async def _create(
    client: httpx.AsyncClient, token: str, supplier_id: int, receipt_ids: list[int], **kw: Any
) -> httpx.Response:
    return await client.post(
        "/api/v1/purchase-input-invoices",
        json={"supplier_id": supplier_id, "receipt_ids": receipt_ids, **_fields(**kw)},
        headers=_auth(token),
    )


async def _uninvoiced(client: httpx.AsyncClient, token: str, supplier_id: int) -> list[int]:
    resp = await client.get(
        f"/api/v1/suppliers/{supplier_id}/uninvoiced-receipts", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return [r["receipt_id"] for r in resp.json()]


async def test_one_invoice_covers_receipts_from_two_orders(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """整月合併開一張：兩張採購單的收貨掛同一張發票。"""
    token, store_id, _ = await _seed_store(db_session, name="合併發票店")
    a = await _seed_catalog(db_session, store_id, sku="MI-A")
    b = await _seed_catalog(db_session, store_id, sku="MI-B")
    supplier = await _create_supplier(client, token, name="月結廠商")
    po1, r1 = await _received_batch(
        client, token, supplier_id=supplier, catalog_id=a, qty=2, cost="100"
    )
    po2, r2 = await _received_batch(
        client, token, supplier_id=supplier, catalog_id=b, qty=3, cost="50"
    )
    assert set(await _uninvoiced(client, token, supplier)) == {r1, r2}

    resp = await _create(client, token, supplier, [r1, r2], total=350)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["supplier_name"] == "月結廠商"
    assert body["invoice_total"] == "350"
    assert {(r["receipt_id"], r["purchase_order_id"], r["amount"]) for r in body["receipts"]} == {
        (r1, po1, "200"),
        (r2, po2, "150"),
    }
    assert body["receipts_total"] == "350"
    assert await _uninvoiced(client, token, supplier) == []

    po = (await client.get(f"/api/v1/purchase-orders/{po1}", headers=_auth(token))).json()
    assert po["receipts"][0]["invoice"]["id"] == body["id"]
    assert po["receipts"][0]["invoice"]["invoice_number"] == "AB12345678"


async def test_uninvoiced_receipts_show_amount_and_only_this_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="待開發票店")
    a = await _seed_catalog(db_session, store_id, sku="UN-A")
    mine = await _create_supplier(client, token, name="我的廠商")
    other = await _create_supplier(client, token, name="別家")
    po_id, receipt = await _received_batch(
        client, token, supplier_id=mine, catalog_id=a, qty=4, cost="25"
    )
    await _received_batch(client, token, supplier_id=other, catalog_id=a)

    resp = await client.get(f"/api/v1/suppliers/{mine}/uninvoiced-receipts", headers=_auth(token))

    assert resp.status_code == 200
    [row] = resp.json()
    assert row["receipt_id"] == receipt and row["purchase_order_id"] == po_id
    assert row["amount"] == "100"


async def test_receipts_must_belong_to_the_invoice_supplier_and_be_free(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="發票驗證店")
    a = await _seed_catalog(db_session, store_id, sku="IV-A")
    mine = await _create_supplier(client, token, name="甲廠")
    other = await _create_supplier(client, token, name="乙廠")
    _, r_mine = await _received_batch(client, token, supplier_id=mine, catalog_id=a)
    _, r_other = await _received_batch(client, token, supplier_id=other, catalog_id=a)

    wrong_supplier = await _create(client, token, mine, [r_mine, r_other])
    assert wrong_supplier.status_code == 422, wrong_supplier.text
    assert "乙廠" in wrong_supplier.json()["detail"]

    assert (await _create(client, token, mine, [r_mine])).status_code == 201
    taken = await _create(client, token, mine, [r_mine], number="CD12345678")
    assert taken.status_code == 409, taken.text

    unknown = await _create(client, token, mine, [999_999], number="EF12345678")
    assert unknown.status_code == 422
    empty = await _create(client, token, mine, [], number="EF12345678")
    assert empty.status_code == 422


async def test_same_number_and_date_cannot_be_registered_twice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="重複號碼店")
    a = await _seed_catalog(db_session, store_id, sku="DU-A")
    supplier = await _create_supplier(client, token)
    _, r1 = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    _, r2 = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    assert (await _create(client, token, supplier, [r1])).status_code == 201

    dup = await _create(client, token, supplier, [r2])
    assert dup.status_code == 409
    assert dup.headers["X-Lu-Camp-Error-Code"] == "DUPLICATE_INPUT_INVOICE"
    # 不同日期＝字軌跨期回收，允許
    assert (await _create(client, token, supplier, [r2], date="2026-11-30")).status_code == 201


async def test_receiving_with_an_already_registered_invoice_joins_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收第二批時填同一張發票（同供應商、同號同日同金額）→ 掛上那張，不另建、不擋。"""
    token, store_id, _ = await _seed_store(db_session, name="隨貨同票店")
    a = await _seed_catalog(db_session, store_id, sku="JO-A")
    supplier = await _create_supplier(client, token)
    invoice = _fields(total=400)
    _, r1 = await _received_batch(
        client, token, supplier_id=supplier, catalog_id=a, invoice=invoice
    )
    _, r2 = await _received_batch(
        client, token, supplier_id=supplier, catalog_id=a, invoice=invoice
    )

    listing = await client.get("/api/v1/purchase-input-invoices", headers=_auth(token))
    [only] = [inv for inv in listing.json() if inv["supplier_id"] == supplier]
    assert {r["receipt_id"] for r in only["receipts"]} == {r1, r2}


async def test_receiving_with_a_conflicting_registered_invoice_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="同號不同金額店")
    a = await _seed_catalog(db_session, store_id, sku="CF-A")
    supplier = await _create_supplier(client, token, name="甲")
    other = await _create_supplier(client, token, name="乙")
    await _received_batch(
        client, token, supplier_id=supplier, catalog_id=a, invoice=_fields(total=400)
    )

    for supplier_id, invoice in ((supplier, _fields(total=500)), (other, _fields(total=400))):
        po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=a)
        resp = await _receive_all(client, token, po_id, invoice=invoice)
        assert resp.status_code == 409, resp.text
        assert resp.headers["X-Lu-Camp-Error-Code"] == "DUPLICATE_INPUT_INVOICE"


async def test_only_managers_edit_and_edits_are_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="發票修改店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="ED-A")
    supplier = await _create_supplier(client, token)
    _, r1 = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    _, r2 = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    created = (await _create(client, token, supplier, [r1], total=200)).json()
    body = {"supplier_id": supplier, "receipt_ids": [r2], **_fields("ZZ00000001", total=210)}

    clerk = await client.put(
        f"/api/v1/purchase-input-invoices/{created['id']}", json=body, headers=_auth(token)
    )
    assert clerk.status_code == 403

    resp = await client.put(
        f"/api/v1/purchase-input-invoices/{created['id']}", json=body, headers=_auth(manager)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["invoice_number"] == "ZZ00000001"
    assert [r["receipt_id"] for r in resp.json()["receipts"]] == [r2]
    assert await _uninvoiced(client, token, supplier) == [r1]

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "UPDATE_INPUT_INVOICE", AuditLog.entity_id == str(created["id"])
        )
    )
    assert log is not None and log.before is not None and log.after is not None
    assert log.before["invoice_number"] == "AB12345678" and log.before["receipt_ids"] == [r1]
    assert log.after["invoice_total"] == "210" and log.after["receipt_ids"] == [r2]


async def test_manager_deletes_an_invoice_and_its_receipts_become_uninvoiced(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="發票刪除店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="DE-A")
    supplier = await _create_supplier(client, token)
    _, receipt = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    created = (await _create(client, token, supplier, [receipt])).json()
    url = f"/api/v1/purchase-input-invoices/{created['id']}"

    assert (await client.delete(url, headers=_auth(token))).status_code == 403
    assert (await client.delete(url, headers=_auth(manager))).status_code == 204
    assert (await client.get(url, headers=_auth(token))).status_code == 404
    assert await _uninvoiced(client, token, supplier) == [receipt]
    log = await db_session.scalar(select(AuditLog).where(AuditLog.action == "DELETE_INPUT_INVOICE"))
    assert log is not None and log.entity_id == str(created["id"])


async def test_order_with_invoiced_receipts_cannot_switch_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨已掛在甲的發票上，採購單就不能改成乙（先到發票移除）。"""
    token, store_id, _ = await _seed_store(db_session, name="換供應商擋店")
    manager = await _manager_token(db_session, store_id)
    a = await _seed_catalog(db_session, store_id, sku="SS-A")
    supplier = await _create_supplier(client, token, name="甲")
    other = await _create_supplier(client, token, name="乙")
    po_id, receipt = await _received_batch(client, token, supplier_id=supplier, catalog_id=a)
    assert (await _create(client, token, supplier, [receipt])).status_code == 201
    line = (await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token))).json()[
        "lines"
    ][0]

    resp = await client.put(
        f"/api/v1/purchase-orders/{po_id}",
        json={
            "supplier_id": other,
            "lines": [
                {
                    "id": line["id"],
                    "catalog_product_id": a,
                    "qty": line["qty"],
                    "received_qty": line["received_qty"],
                    "unit_cost": line["unit_cost"],
                }
            ],
        },
        headers=_auth(manager),
    )
    assert resp.status_code == 422, resp.text
    assert "發票" in resp.json()["detail"]


async def test_list_filters_by_supplier_and_counts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session, name="發票清單店")
    a = await _seed_catalog(db_session, store_id, sku="LI-A")
    mine = await _create_supplier(client, token, name="甲")
    other = await _create_supplier(client, token, name="乙")
    _, r1 = await _received_batch(client, token, supplier_id=mine, catalog_id=a)
    _, r2 = await _received_batch(client, token, supplier_id=other, catalog_id=a)
    assert (await _create(client, token, mine, [r1], number="AA00000001")).status_code == 201
    assert (await _create(client, token, other, [r2], number="AA00000002")).status_code == 201

    listing = await client.get(
        "/api/v1/purchase-input-invoices", params={"supplier_id": mine}, headers=_auth(token)
    )
    assert [inv["invoice_number"] for inv in listing.json()] == ["AA00000001"]
    count = await client.get(
        "/api/v1/purchase-input-invoices/count", params={"supplier_id": mine}, headers=_auth(token)
    )
    assert count.json() == {"count": 1}


async def test_batch_amount_follows_a_corrected_order(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """採購單改過（換商品、改進價）後，只收過一批的單，這批金額照改後的已收×進價。"""
    token, store_id, _ = await _seed_store(db_session, name="改單後金額店")
    manager = await _manager_token(db_session, store_id)
    wrong = await _seed_catalog(db_session, store_id, sku="AM-W", qty=0)
    right = await _seed_catalog(db_session, store_id, sku="AM-R", qty=0)
    supplier = await _create_supplier(client, token)
    po_id, receipt = await _received_batch(
        client, token, supplier_id=supplier, catalog_id=wrong, qty=4, cost="100"
    )
    edit = await client.put(
        f"/api/v1/purchase-orders/{po_id}",
        json={
            "supplier_id": supplier,
            "lines": [
                {
                    "id": None,
                    "catalog_product_id": right,
                    "qty": 4,
                    "received_qty": 4,
                    "unit_cost": "800",
                }
            ],
        },
        headers=_auth(manager),
    )
    assert edit.status_code == 200, edit.text

    resp = await client.get(
        f"/api/v1/suppliers/{supplier}/uninvoiced-receipts", headers=_auth(token)
    )
    [row] = resp.json()
    assert row["receipt_id"] == receipt and row["amount"] == "3200"
