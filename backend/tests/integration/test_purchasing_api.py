"""採購/補貨 API 整合測試：supplier → 採購單（草稿/送出）→ 分批收貨 → catalog 入庫。"""

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import ItemKind, StockDirection, StockReason, UserRole


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _recv_headers(token: str, key: str | None = None) -> dict[str, str]:
    """收貨需帶 Idempotency-Key；預設每次唯一（不同收貨事件）。"""
    return {**_auth(token), "Idempotency-Key": key or uuid.uuid4().hex}


async def _seed_store(session: AsyncSession, *, name: str = "採購店") -> tuple[str, int, int]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"clerk-{store.id}", password_hash="h", role=UserRole.CLERK
    )
    session.add(clerk)
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_catalog(
    session: AsyncSession,
    store_id: int,
    *,
    sku: str = "CAT-1",
    qty: int = 2,
    reorder_point: int = 5,
) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=sku,
        name="瓦斯罐",
        unit_price=Decimal("180"),
        quantity_on_hand=qty,
        reorder_point=reorder_point,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _create_supplier(
    client: httpx.AsyncClient, token: str, *, name: str = "補貨供應商"
) -> int:
    resp = await client.post(
        "/api/v1/suppliers",
        json={"name": name, "contact": "sales@example.test", "tax_id": "12345678"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _create_po(
    client: httpx.AsyncClient,
    token: str,
    *,
    supplier_id: int,
    catalog_product_id: int,
    qty: int = 10,
    unit_cost: str = "120",
    submit: bool = True,
) -> int:
    """建採購單；submit=True（預設）建立即『已下單』，False 為『草稿』。"""
    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [
                {"catalog_product_id": catalog_product_id, "qty": qty, "unit_cost": unit_cost}
            ],
            "submit": submit,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == ("ORDERED" if submit else "DRAFT")
    assert body["total_cost"] == str(Decimal(qty) * Decimal(unit_cost))
    assert body["lines"][0]["received_qty"] == 0
    return int(body["id"])


async def _po_lines(client: httpx.AsyncClient, token: str, po_id: int) -> list[dict[str, Any]]:
    got = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token))
    assert got.status_code == 200, got.text
    return list(got.json()["lines"])


async def _receive_all(
    client: httpx.AsyncClient,
    token: str,
    po_id: int,
    *,
    invoice: dict[str, str] | None = None,
    key: str | None = None,
) -> httpx.Response:
    """一次收足所有明細的待收數量（qty − received_qty）。"""
    lines = await _po_lines(client, token, po_id)
    recv = [{"line_id": ln["id"], "qty": ln["qty"] - ln["received_qty"]} for ln in lines]
    body: dict[str, Any] = {"lines": recv}
    if invoice is not None:
        body["invoice"] = invoice
    return await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive", json=body, headers=_recv_headers(token, key)
    )


async def _purchase_movements(
    session: AsyncSession, store_id: int, catalog_id: int
) -> list[StockMovement]:
    rows = await session.scalars(
        select(StockMovement).where(
            StockMovement.store_id == store_id,
            StockMovement.item_kind == ItemKind.CATALOG,
            StockMovement.catalog_product_id == catalog_id,
            StockMovement.direction == StockDirection.IN,
            StockMovement.reason == StockReason.PURCHASE,
        )
    )
    return list(rows.all())


# ── 收貨入庫 + 庫存異動 ──────────────────────────────────────────────


async def test_receive_purchase_order_replenishes_catalog_and_records_stock_movement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _clerk_id = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=10
    )

    received = await _receive_all(client, token, po_id)

    assert received.status_code == 200, received.text
    body = received.json()
    assert body["purchase_order"]["status"] == "RECEIVED"
    assert body["receipt_id"] is not None
    assert body["purchase_order"]["lines"][0]["received_qty"] == 10
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    assert product.quantity_on_hand == 12

    movements = await _purchase_movements(db_session, store_id, catalog_id)
    assert len(movements) == 1
    assert movements[0].qty == 10
    assert movements[0].ref_type == "goods_receipt"
    assert movements[0].ref_id == body["receipt_id"]

    low_stock = await client.get(
        "/api/v1/catalog-products", params={"low_stock": "true"}, headers=_auth(token)
    )
    assert low_stock.status_code == 200, low_stock.text
    assert all(row["id"] != catalog_id for row in low_stock.json())


async def test_receive_after_fully_received_returns_409_without_double_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """全收足後再收 → 409（狀態已 RECEIVED），庫存與異動不重複。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=catalog_id)
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    first = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 10}]},
        headers=_recv_headers(token),
    )
    second = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 10}]},
        headers=_recv_headers(token),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    assert product.quantity_on_hand == 12
    movement_count = await db_session.scalar(
        select(func.count())
        .select_from(StockMovement)
        .where(
            StockMovement.store_id == store_id,
            StockMovement.catalog_product_id == catalog_id,
            StockMovement.reason == StockReason.PURCHASE,
        )
    )
    assert movement_count == 1


# ── 草稿 → 送出 ─────────────────────────────────────────────────────


async def test_create_draft_cannot_receive_until_submitted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """草稿不可收貨；送出後轉『已下單』方可收貨。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, submit=False
    )

    # 草稿收貨 → 409
    early = await _receive_all(client, token, po_id)
    assert early.status_code == 409, early.text

    # 送出 → ORDERED
    submitted = await client.post(
        f"/api/v1/purchase-orders/{po_id}/submit", headers=_auth(token)
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "ORDERED"

    # 送出後可收貨
    received = await _receive_all(client, token, po_id)
    assert received.status_code == 200, received.text
    assert received.json()["purchase_order"]["status"] == "RECEIVED"

    # 已送出者再送出 → 409
    again = await client.post(f"/api/v1/purchase-orders/{po_id}/submit", headers=_auth(token))
    assert again.status_code == 409, again.text


# ── 分批收貨 ─────────────────────────────────────────────────────────


async def test_partial_receive_then_full_transitions_status_and_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """先收 12/20 → PARTIAL（庫存 +12、received_qty=12）；再收 8 → RECEIVED（+20）。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=20
    )
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    part = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 12}]},
        headers=_recv_headers(token),
    )
    assert part.status_code == 200, part.text
    po_body = part.json()["purchase_order"]
    assert po_body["status"] == "PARTIAL"
    assert po_body["lines"][0]["received_qty"] == 12
    assert po_body["received_at"] is None
    assert len(po_body["receipts"]) == 1
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None and product.quantity_on_hand == 12

    rest = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 8}]},
        headers=_recv_headers(token),
    )
    assert rest.status_code == 200, rest.text
    po_body = rest.json()["purchase_order"]
    assert po_body["status"] == "RECEIVED"
    assert po_body["lines"][0]["received_qty"] == 20
    assert po_body["received_at"] is not None
    assert len(po_body["receipts"]) == 2
    await db_session.refresh(product)
    assert product.quantity_on_hand == 20

    movements = await _purchase_movements(db_session, store_id, catalog_id)
    assert sorted(m.qty for m in movements) == [8, 12]


async def test_receive_over_remaining_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """本次收貨數量超過待收 → 422，且不改庫存/狀態。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=5
    )
    line_id = (await _po_lines(client, token, po_id))[0]["id"]

    resp = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 6}]},
        headers=_recv_headers(token),
    )
    assert resp.status_code == 422, resp.text
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None and product.quantity_on_hand == 0
    got = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token))
    assert got.json()["status"] == "ORDERED"


async def test_receive_line_from_other_order_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨明細不屬於本採購單 → 422。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=catalog_id)

    resp = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": 999999, "qty": 1}]},
        headers=_recv_headers(token),
    )
    assert resp.status_code == 422, resp.text


# ── 取消 ─────────────────────────────────────────────────────────────


async def test_cancel_draft_and_ordered_but_not_received(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """草稿/已下單可取消；部分到貨或已收貨不可取消（409）。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)

    # 草稿可取消
    draft = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, submit=False
    )
    cancelled = await client.post(
        f"/api/v1/purchase-orders/{draft}/cancel", headers=_auth(token)
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    # 已下單可取消
    ordered = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id
    )
    assert (
        await client.post(f"/api/v1/purchase-orders/{ordered}/cancel", headers=_auth(token))
    ).status_code == 200

    # 部分到貨不可取消
    partial = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=10
    )
    line_id = (await _po_lines(client, token, partial))[0]["id"]
    await client.post(
        f"/api/v1/purchase-orders/{partial}/receive",
        json={"lines": [{"line_id": line_id, "qty": 4}]},
        headers=_recv_headers(token),
    )
    blocked = await client.post(
        f"/api/v1/purchase-orders/{partial}/cancel", headers=_auth(token)
    )
    assert blocked.status_code == 409, blocked.text


# ── 收貨冪等（防網路重試重複入庫）────────────────────────────────────


async def test_receive_idempotent_retry_does_not_double_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 Idempotency-Key 重送：回原收貨、庫存與異動只加一次；同 key 不同 payload → 409。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=10
    )
    line_id = (await _po_lines(client, token, po_id))[0]["id"]
    key = uuid.uuid4().hex
    body = {"lines": [{"line_id": line_id, "qty": 3}]}

    first = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive", json=body, headers=_recv_headers(token, key)
    )
    retry = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive", json=body, headers=_recv_headers(token, key)
    )
    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert first.json()["receipt_id"] == retry.json()["receipt_id"]

    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None and product.quantity_on_hand == 3  # 只加一次
    movements = await _purchase_movements(db_session, store_id, catalog_id)
    assert len(movements) == 1 and movements[0].qty == 3

    conflict = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={"lines": [{"line_id": line_id, "qty": 5}]},
        headers=_recv_headers(token, key),
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.headers["X-Lu-Camp-Error-Code"] == "IDEMPOTENCY_KEY_CONFLICT"


async def test_list_outstanding_includes_ordered_and_partial(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """待收貨清單（status=ORDERED&status=PARTIAL）同時含已下單與部分到貨。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_ordered = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=5
    )
    po_partial = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=10
    )
    line_id = (await _po_lines(client, token, po_partial))[0]["id"]
    await client.post(
        f"/api/v1/purchase-orders/{po_partial}/receive",
        json={"lines": [{"line_id": line_id, "qty": 4}]},
        headers=_recv_headers(token),
    )

    resp = await client.get(
        "/api/v1/purchase-orders",
        params={"status": ["ORDERED", "PARTIAL"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    ids = [po["id"] for po in resp.json()]
    assert po_ordered in ids and po_partial in ids


async def test_submit_records_submit_actor(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """草稿由 A 建、由 B 送出 → 正式下單人記為 B（非草稿建立者）。"""
    token_a, store_id, clerk_a = await _seed_store(db_session)
    manager = User(
        store_id=store_id, username=f"mgr-{store_id}", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(manager)
    await db_session.flush()
    token_b = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token_a)
    po_id = await _create_po(
        client, token_a, supplier_id=supplier_id, catalog_product_id=catalog_id, submit=False
    )
    draft = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token_a))
    assert draft.json()["ordered_by"] == clerk_a

    submitted = await client.post(
        f"/api/v1/purchase-orders/{po_id}/submit", headers=_auth(token_b)
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["ordered_by"] == manager.id


# ── 建單驗證 ─────────────────────────────────────────────────────────


async def test_create_purchase_order_rejects_cross_store_catalog_product(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token_a, _store_a, _clerk_a = await _seed_store(db_session, name="A 店")
    _token_b, store_b, _clerk_b = await _seed_store(db_session, name="B 店")
    catalog_b = await _seed_catalog(db_session, store_b, sku="B-CAT")
    supplier_a = await _create_supplier(client, token_a, name="A 供應商")

    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_a,
            "lines": [{"catalog_product_id": catalog_b, "qty": 3, "unit_cost": "100"}],
        },
        headers=_auth(token_a),
    )

    assert resp.status_code == 422, resp.text


async def test_create_purchase_order_rejects_cross_store_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token_a, store_a, _clerk_a = await _seed_store(db_session, name="A 店")
    token_b, _store_b, _clerk_b = await _seed_store(db_session, name="B 店")
    catalog_a = await _seed_catalog(db_session, store_a, sku="A-CAT")
    supplier_b = await _create_supplier(client, token_b, name="B 供應商")

    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_b,
            "lines": [{"catalog_product_id": catalog_a, "qty": 3, "unit_cost": "100"}],
        },
        headers=_auth(token_a),
    )

    assert resp.status_code == 422, resp.text


async def test_create_supplier_blank_name_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    resp = await client.post("/api/v1/suppliers", json={"name": "   "}, headers=_auth(token))
    assert resp.status_code == 422, resp.text


async def test_list_suppliers_returns_created(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token, name="阿里山補給")
    resp = await client.get("/api/v1/suppliers", headers=_auth(token))
    assert resp.status_code == 200
    created = next(s for s in resp.json() if s["id"] == supplier_id)
    assert created["is_active"] is True  # 新建預設啟用


async def test_get_and_update_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """查看單一供應商；編輯名稱/聯絡/統編後反映；不存在 → 404。"""
    token, _store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token, name="舊名稱")

    got = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=_auth(token))
    assert got.status_code == 200 and got.json()["name"] == "舊名稱"

    patched = await client.patch(
        f"/api/v1/suppliers/{supplier_id}",
        json={"name": "新名稱", "contact": "0912-000-000", "tax_id": None},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "新名稱"
    assert patched.json()["contact"] == "0912-000-000"
    assert patched.json()["tax_id"] is None

    missing = await client.get("/api/v1/suppliers/999999", headers=_auth(token))
    assert missing.status_code == 404


async def test_update_supplier_duplicate_name_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """改名撞同店既有供應商名 → 409。"""
    token, _store_id, _ = await _seed_store(db_session)
    await _create_supplier(client, token, name="甲供應商")
    b_id = await _create_supplier(client, token, name="乙供應商")
    resp = await client.patch(
        f"/api/v1/suppliers/{b_id}", json={"name": "甲供應商"}, headers=_auth(token)
    )
    assert resp.status_code == 409, resp.text


async def test_deactivate_supplier_hides_from_default_list_but_keeps_record(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用供應商：預設清單（建單選單）隱藏，include_inactive 仍可見、可查、可重新啟用。"""
    token, _store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token, name="待停用商")

    deact = await client.post(
        f"/api/v1/suppliers/{supplier_id}/deactivate", headers=_auth(token)
    )
    assert deact.status_code == 200 and deact.json()["is_active"] is False

    # 預設清單（建單選單）不含停用者
    default_list = await client.get("/api/v1/suppliers", headers=_auth(token))
    assert all(s["id"] != supplier_id for s in default_list.json())
    # include_inactive 仍列出（供應商管理）
    all_list = await client.get(
        "/api/v1/suppliers", params={"include_inactive": "true"}, headers=_auth(token)
    )
    assert any(s["id"] == supplier_id for s in all_list.json())
    # 單筆查詢仍可取得（保留歷史）
    got = await client.get(f"/api/v1/suppliers/{supplier_id}", headers=_auth(token))
    assert got.status_code == 200

    # 重新啟用 → 回到預設清單
    react = await client.post(f"/api/v1/suppliers/{supplier_id}/activate", headers=_auth(token))
    assert react.status_code == 200 and react.json()["is_active"] is True
    back = await client.get("/api/v1/suppliers", headers=_auth(token))
    assert any(s["id"] == supplier_id for s in back.json())


async def test_create_po_duplicate_product_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token)
    cat_id = await _seed_catalog(db_session, store_id)
    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [
                {"catalog_product_id": cat_id, "qty": 5, "unit_cost": "100"},
                {"catalog_product_id": cat_id, "qty": 3, "unit_cost": "100"},
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_list_and_get_purchase_order(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token)
    cat_id = await _seed_catalog(db_session, store_id)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=cat_id)

    listed = await client.get("/api/v1/purchase-orders", headers=_auth(token))
    assert listed.status_code == 200
    assert any(po["id"] == po_id for po in listed.json())

    got = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["id"] == po_id

    missing = await client.get("/api/v1/purchase-orders/999999", headers=_auth(token))
    assert missing.status_code == 404


async def test_receive_unknown_purchase_order_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    resp = await client.post(
        "/api/v1/purchase-orders/999999/receive",
        json={"lines": [{"line_id": 1, "qty": 1}]},
        headers=_recv_headers(token),
    )
    assert resp.status_code == 404, resp.text


async def test_low_stock_incoming_qty_counts_open_pos_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """低庫存品 incoming_qty＝Σ 未收完（ORDERED＋PARTIAL）的 訂購−已收；
    不含 RECEIVED/DRAFT/CANCELLED。避免重複採購用。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, qty=0, reorder_point=100)
    supplier_id = await _create_supplier(client, token)

    # ORDERED：訂 10、未收 → 在途 10
    await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=10)
    # PARTIAL：訂 8、收 3 → 在途 5
    po_partial = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=8
    )
    line_id = (await _po_lines(client, token, po_partial))[0]["id"]
    await client.post(
        f"/api/v1/purchase-orders/{po_partial}/receive",
        json={"lines": [{"line_id": line_id, "qty": 3}]},
        headers=_recv_headers(token),
    )
    # RECEIVED：訂 4、全收 → 不計
    po_recv = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=4
    )
    await _receive_all(client, token, po_recv)
    # DRAFT：訂 20、未送出 → 不計
    await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=20, submit=False
    )
    # CANCELLED：訂 7、取消 → 不計
    po_cancel = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=7
    )
    await client.post(f"/api/v1/purchase-orders/{po_cancel}/cancel", headers=_auth(token))

    resp = await client.get(
        "/api/v1/catalog-products", params={"low_stock": "true"}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json() if r["id"] == catalog_id)
    assert row["incoming_qty"] == 15  # 10 + 5


async def test_list_purchase_orders_filters_by_status_and_paginates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """採購單清單可依狀態篩選、並支援 limit/offset 分頁。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, reorder_point=100)
    supplier_id = await _create_supplier(client, token)
    po_open = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=1, unit_cost="10"
    )
    po_recv = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=2, unit_cost="20"
    )
    await _receive_all(client, token, po_recv)

    ordered = await client.get(
        "/api/v1/purchase-orders", params={"status": "ORDERED"}, headers=_auth(token)
    )
    assert ordered.status_code == 200, ordered.text
    ordered_ids = [po["id"] for po in ordered.json()]
    assert po_open in ordered_ids and po_recv not in ordered_ids

    received = await client.get(
        "/api/v1/purchase-orders", params={"status": "RECEIVED"}, headers=_auth(token)
    )
    received_ids = [po["id"] for po in received.json()]
    assert po_recv in received_ids and po_open not in received_ids

    # 分頁：limit=1 各頁一筆、不重疊。
    page0 = await client.get(
        "/api/v1/purchase-orders", params={"limit": 1, "offset": 0}, headers=_auth(token)
    )
    page1 = await client.get(
        "/api/v1/purchase-orders", params={"limit": 1, "offset": 1}, headers=_auth(token)
    )
    assert len(page0.json()) == 1 and len(page1.json()) == 1
    assert page0.json()[0]["id"] != page1.json()[0]["id"]


# ── 進項發票（收貨時登錄；漏登可補登）──────────────────────────────────


async def test_receive_with_input_invoice_stores_and_splits_tax(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨帶進項發票 → 落庫且稅額拆分（total 1050、5% → net 1000/tax 50）；收貨批次可見。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    received = await _receive_all(
        client,
        token,
        po_id,
        invoice={
            "invoice_number": "AB12345678",
            "invoice_date": "2026-07-11",
            "invoice_total": "1050",
        },
    )
    assert received.status_code == 200, received.text
    receipts = received.json()["purchase_order"]["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["invoice"] == {
        "invoice_number": "AB12345678",
        "invoice_date": "2026-07-11",
        "invoice_total": "1050",
        "invoice_net": "1000",
        "invoice_tax": "50",
    }


async def test_receive_without_invoice_then_backfill_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨未帶發票 → invoice=None；補登一次成功；再補登 → 409（不可覆寫）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    received = await _receive_all(client, token, po_id)
    assert received.status_code == 200, received.text
    receipt_id = received.json()["receipt_id"]
    assert received.json()["purchase_order"]["receipts"][0]["invoice"] is None

    backfill = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receipts/{receipt_id}/invoice",
        json={
            "invoice_number": "CD98765432",
            "invoice_date": "2026-07-11",
            "invoice_total": "2100",
        },
        headers=_auth(token),
    )
    assert backfill.status_code == 200, backfill.text
    assert backfill.json()["invoice_net"] == "2000"
    assert backfill.json()["invoice_tax"] == "100"

    again = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receipts/{receipt_id}/invoice",
        json={
            "invoice_number": "EF11111111",
            "invoice_date": "2026-07-11",
            "invoice_total": "999",
        },
        headers=_auth(token),
    )
    assert again.status_code == 409, again.text


async def test_backfill_unknown_receipt_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """對不存在的收貨批次補登發票 → 409。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    resp = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receipts/999999/invoice",
        json={
            "invoice_number": "GH22222222",
            "invoice_date": "2026-07-11",
            "invoice_total": "500",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text


async def test_invoice_number_format_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """號碼非 2 英文＋8 數字 → 422（pydantic pattern）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)
    resp = await _receive_all(
        client,
        token,
        po_id,
        invoice={
            "invoice_number": "1234567890",
            "invoice_date": "2026-07-11",
            "invoice_total": "1050",
        },
    )
    assert resp.status_code == 422, resp.text


async def test_duplicate_invoice_number_rejected_across_pos(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同店同號同日的實體發票不可重複入帳（收貨與補登皆擋 409）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    p1 = await _seed_catalog(db_session, store_id, sku="DUP-1")
    p2 = await _seed_catalog(db_session, store_id, sku="DUP-2")
    supplier_id = await _create_supplier(client, token)
    po1 = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=p1)
    po2 = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=p2)
    invoice = {
        "invoice_number": "ZZ55667788",
        "invoice_date": "2026-07-11",
        "invoice_total": "1050",
    }
    first = await _receive_all(client, token, po1, invoice=invoice)
    assert first.status_code == 200, first.text
    # 收貨路徑重複 → 409
    dup_receive = await _receive_all(client, token, po2, invoice=invoice)
    assert dup_receive.status_code == 409, dup_receive.text
    assert dup_receive.headers["X-Lu-Camp-Error-Code"] == "DUPLICATE_INPUT_INVOICE"
    # po2 未收貨成功（原子回滾）：再收一次（無發票）→ 200，補登同號 → 409
    ok2 = await _receive_all(client, token, po2)
    assert ok2.status_code == 200, ok2.text
    receipt2 = ok2.json()["receipt_id"]
    dup_backfill = await client.post(
        f"/api/v1/purchase-orders/{po2}/receipts/{receipt2}/invoice",
        json=invoice,
        headers=_auth(token),
    )
    assert dup_backfill.status_code == 409, dup_backfill.text
    # 不同日期＝不同期別回收字軌 → 允許
    other_date = {**invoice, "invoice_date": "2026-09-11"}
    ok3 = await client.post(
        f"/api/v1/purchase-orders/{po2}/receipts/{receipt2}/invoice",
        json=other_date,
        headers=_auth(token),
    )
    assert ok3.status_code == 200, ok3.text
