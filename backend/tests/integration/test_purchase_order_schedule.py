"""採購單日期（docs/99 §5、第一期 1B）。

- 實際下單日：送出時預設今天、可以選過去（不能選未來）；送出後只有管理者能改。
- 預計到貨日：店員也能改（到貨資訊，不是改單內容）；收齊、取消的單不能改。
- 供應商付款條件只有管理者能設；送出時快照到採購單，之後供應商改條件不改寫舊單。
- 付款期限依快照與日期算出；管理者可以手動改、也可以恢復自動。改日期與期限留稽核。
"""

from collections.abc import AsyncGenerator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.time import store_date, utc_now
from app.main import create_app
from app.modules.inventory.models import CatalogProduct
from tests.integration.agenda_helpers import SeededStore, auth, seed_store

API = "/api/v1"


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


def _day(offset: int) -> str:
    return (store_date(utc_now()) + timedelta(days=offset)).isoformat()


async def _catalog(session: AsyncSession, store_id: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=f"GAS-{store_id}",
        name="瓦斯罐",
        unit_price=Decimal("180"),
        quantity_on_hand=0,
        reorder_point=5,
    )
    session.add(product)
    await session.commit()
    return product.id


async def _supplier(
    client: httpx.AsyncClient, shop: SeededStore, name: str, **terms: Any
) -> dict[str, Any]:
    created = await client.post(
        f"{API}/suppliers", json={"name": name, **terms}, headers=auth(shop.manager_token)
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _order(
    client: httpx.AsyncClient,
    shop: SeededStore,
    supplier_id: int,
    catalog_id: int,
    **extra: Any,
) -> dict[str, Any]:
    created = await client.post(
        f"{API}/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [{"catalog_product_id": catalog_id, "qty": 4, "unit_cost": "100"}],
            **extra,
        },
        headers=auth(shop.clerk_token),
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_supplier_payment_terms_are_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    monthly = await _supplier(
        client, shop, "露營批發", payment_terms="MONTHLY", payment_terms_value=10
    )
    assert (monthly["payment_terms"], monthly["payment_terms_value"]) == ("MONTHLY", 10)

    clerk = auth(shop.clerk_token)
    forbidden = await client.post(
        f"{API}/suppliers", json={"name": "店員建", "payment_terms": "PREPAID"}, headers=clerk
    )
    assert forbidden.status_code == 403, forbidden.text
    plain = await client.post(f"{API}/suppliers", json={"name": "店員建"}, headers=clerk)
    assert plain.status_code == 201, plain.text
    assert plain.json()["payment_terms"] is None

    patched = await client.patch(
        f"{API}/suppliers/{monthly['id']}", json={"payment_terms": "COD"}, headers=clerk
    )
    assert patched.status_code == 403, patched.text
    # 店員改聯絡方式照舊可以（沒碰付款條件）
    contact = await client.patch(
        f"{API}/suppliers/{monthly['id']}", json={"contact": "王先生"}, headers=clerk
    )
    assert contact.status_code == 200, contact.text
    assert contact.json()["payment_terms"] == "MONTHLY"

    invalid = await client.patch(
        f"{API}/suppliers/{monthly['id']}",
        json={"payment_terms": "MONTHLY", "payment_terms_value": None},
        headers=auth(shop.manager_token),
    )
    assert invalid.status_code == 422, invalid.text
    cod = await client.patch(
        f"{API}/suppliers/{monthly['id']}",
        json={"payment_terms": "COD", "payment_terms_value": None},
        headers=auth(shop.manager_token),
    )
    assert cod.status_code == 200, cod.text
    assert (cod.json()["payment_terms"], cod.json()["payment_terms_value"]) == ("COD", None)


async def test_submit_snapshots_terms_and_takes_the_real_order_day(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    catalog_id = await _catalog(db_session, shop.store_id)
    supplier = await _supplier(client, shop, "貨到付款行", payment_terms="COD")

    po = await _order(
        client,
        shop,
        supplier["id"],
        catalog_id,
        submit=True,
        ordered_on=_day(-3),
        expected_on=_day(2),
    )
    assert po["ordered_on"] == _day(-3)
    assert po["expected_on"] == _day(2)
    assert po["payment_terms"] == "COD"
    assert po["payment_due_on"] == _day(2)  # 貨到付款：還沒到貨就看預計到貨日
    assert po["payment_due_overridden"] is False

    # 供應商之後改條件：舊單照下單當時的條件
    await client.patch(
        f"{API}/suppliers/{supplier['id']}",
        json={"payment_terms": "PREPAID"},
        headers=auth(shop.manager_token),
    )
    again = await client.get(f"{API}/purchase-orders/{po['id']}", headers=auth(shop.clerk_token))
    assert again.json()["payment_terms"] == "COD"
    assert again.json()["payment_due_on"] == _day(2)

    future = await client.post(
        f"{API}/purchase-orders",
        json={
            "supplier_id": supplier["id"],
            "lines": [{"catalog_product_id": catalog_id, "qty": 1, "unit_cost": "100"}],
            "submit": True,
            "ordered_on": _day(1),
        },
        headers=auth(shop.clerk_token),
    )
    assert future.status_code == 422, future.text


async def test_draft_gets_its_order_day_when_submitted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    catalog_id = await _catalog(db_session, shop.store_id)
    supplier = await _supplier(client, shop, "先付行", payment_terms="PREPAID")

    draft = await _order(client, shop, supplier["id"], catalog_id, expected_on=_day(5))
    assert draft["status"] == "DRAFT"
    assert draft["ordered_on"] is None
    assert draft["payment_terms"] is None  # 還沒送出，條件還沒定下來
    assert draft["payment_due_on"] is None

    submitted = await client.post(
        f"{API}/purchase-orders/{draft['id']}/submit",
        json={"ordered_on": _day(-1)},
        headers=auth(shop.clerk_token),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["ordered_on"] == _day(-1)
    assert body["expected_on"] == _day(5)
    assert body["payment_terms"] == "PREPAID"
    assert body["payment_due_on"] == _day(-1)

    other = await _order(client, shop, supplier["id"], catalog_id)
    plain_submit = await client.post(
        f"{API}/purchase-orders/{other['id']}/submit", headers=auth(shop.clerk_token)
    )
    assert plain_submit.status_code == 200, plain_submit.text
    assert plain_submit.json()["ordered_on"] == _day(0)


async def test_schedule_edits_follow_who_may_change_what(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    catalog_id = await _catalog(db_session, shop.store_id)
    supplier = await _supplier(
        client, shop, "到貨後付", payment_terms="NET_DAYS", payment_terms_value=30
    )
    po = await _order(client, shop, supplier["id"], catalog_id, submit=True)
    url = f"{API}/purchase-orders/{po['id']}/schedule"
    clerk, mgr = auth(shop.clerk_token), auth(shop.manager_token)

    # 預計到貨：店員也能改（廠商說會晚到，接到電話的人就能改）
    moved = await client.patch(url, json={"expected_on": _day(4)}, headers=clerk)
    assert moved.status_code == 200, moved.text
    assert moved.json()["expected_on"] == _day(4)

    # 實際下單日：送出後只有管理者能改，而且不能是未來
    assert (
        await client.patch(url, json={"ordered_on": _day(-2)}, headers=clerk)
    ).status_code == 403
    assert (await client.patch(url, json={"ordered_on": _day(1)}, headers=mgr)).status_code == 422
    fixed = await client.patch(url, json={"ordered_on": _day(-2)}, headers=mgr)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["ordered_on"] == _day(-2)

    # 付款期限：還沒到貨算不出來；只有管理者能手動改，也能恢復自動
    assert fixed.json()["payment_due_on"] is None
    assert (
        await client.patch(url, json={"payment_due_on": _day(10)}, headers=clerk)
    ).status_code == 403
    overridden = await client.patch(url, json={"payment_due_on": _day(10)}, headers=mgr)
    assert overridden.json()["payment_due_on"] == _day(10)
    assert overridden.json()["payment_due_overridden"] is True
    restored = await client.patch(url, json={"payment_due_on": None}, headers=mgr)
    assert restored.json()["payment_due_on"] is None
    assert restored.json()["payment_due_overridden"] is False

    actions = [
        row.action
        for row in (
            await db_session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "purchase_order", AuditLog.entity_id == str(po["id"])
                )
                .order_by(AuditLog.id)
            )
        ).all()
    ]
    assert actions.count("UPDATE_PURCHASE_ORDER_SCHEDULE") == 3  # 下單日、改期限、恢復自動


async def test_monthly_due_date_follows_the_first_arrival(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    catalog_id = await _catalog(db_session, shop.store_id)
    supplier = await _supplier(
        client, shop, "月結行", payment_terms="MONTHLY", payment_terms_value=10
    )
    po = await _order(client, shop, supplier["id"], catalog_id, submit=True)
    assert po["payment_due_on"] is None  # 月結還沒到貨不知道算哪個月

    received = await client.post(
        f"{API}/purchase-orders/{po['id']}/receive",
        json={"lines": [{"line_id": po["lines"][0]["id"], "qty": 1}]},
        headers={**auth(shop.clerk_token), "Idempotency-Key": "schedule-receive-1"},
    )
    assert received.status_code == 200, received.text
    today = store_date(utc_now())
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=10)
    after = await client.get(f"{API}/purchase-orders/{po['id']}", headers=auth(shop.clerk_token))
    assert after.json()["payment_due_on"] == next_month.isoformat()


async def test_finished_orders_keep_their_dates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    catalog_id = await _catalog(db_session, shop.store_id)
    supplier = await _supplier(client, shop, "收齊行")
    cancelled = await _order(client, shop, supplier["id"], catalog_id, submit=True)
    await client.post(
        f"{API}/purchase-orders/{cancelled['id']}/cancel", headers=auth(shop.manager_token)
    )
    blocked = await client.patch(
        f"{API}/purchase-orders/{cancelled['id']}/schedule",
        json={"expected_on": _day(3)},
        headers=auth(shop.clerk_token),
    )
    assert blocked.status_code == 409, blocked.text

    draft = await _order(client, shop, supplier["id"], catalog_id)
    no_order_day = await client.patch(
        f"{API}/purchase-orders/{draft['id']}/schedule",
        json={"ordered_on": _day(-1)},
        headers=auth(shop.manager_token),
    )
    assert no_order_day.status_code == 409, no_order_day.text  # 草稿送出時才定下單日
