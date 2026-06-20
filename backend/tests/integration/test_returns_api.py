"""returns API integration tests（Phase 4B：退貨、退現、回補庫存）。"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.returns.models import CustomerReturn, ReturnLine
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    CashMovementType,
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleStatus,
    SerializedItemStatus,
    StockDirection,
    StockReason,
    UserRole,
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


async def _seed(session: AsyncSession) -> tuple[str, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku="SKU-RET",
        name="瓦斯罐",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _seed_serialized(
    session: AsyncSession,
    store_id: int,
    *,
    code: str,
    price: str,
    ownership_type: OwnershipType,
    consignor_id: int | None = None,
    commission_pct: int | None = None,
) -> int:
    item = await InventoryService(session).create_serialized_item(
        store_id,
        item_code=code,
        name="寄售睡墊" if ownership_type == OwnershipType.CONSIGNMENT else "二手睡墊",
        grade=Grade.A,
        ownership_type=ownership_type,
        listed_price=Decimal(price),
        acquisition_cost=Decimal("500") if ownership_type == OwnershipType.OWNED else None,
        consignor_id=consignor_id,
        commission_pct=commission_pct,
    )
    return item.id


async def _seed_bulk_lot(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    lot = await InventoryService(session).create_bulk_lot(
        store_id,
        lot_code="RET-BULK",
        name="散裝營釘",
        grade=Grade.E,
        acquisition_cost=Decimal("300"),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(price),
        total_qty=qty,
    )
    return lot.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def test_create_full_catalog_return_refunds_cash_and_restock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem="sale-return-catalog"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    sale_id = sale["id"]
    sale_line_id = sale["lines"][0]["id"]

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale_id,
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-1"),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sale_id"] == sale_id
    assert body["refund_amount"] == "240"
    assert body["reason"] == "顧客退貨"
    assert body["lines"] == [
        {
            "id": body["lines"][0]["id"],
            "sale_line_id": sale_line_id,
            "qty": 2,
            "refund_amount": "240",
        }
    ]
    got = await client.get(f"/api/v1/returns/{body['id']}", headers=_auth(token))
    assert got.status_code == 200, got.text
    assert got.json() == body

    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 10

    db_sale = await db_session.get(Sale, sale_id)
    assert db_sale is not None
    await db_session.refresh(db_sale)
    assert db_sale.status == SaleStatus.RETURNED

    cash_movements = (
        await db_session.scalars(
            select(CashMovement).where(
                CashMovement.ref_type == "return", CashMovement.ref_id == body["id"]
            )
        )
    ).all()
    assert [(m.type, m.amount) for m in cash_movements] == [
        (CashMovementType.SALE_REFUND_OUT, Decimal("240"))
    ]

    stock_movements = (
        await db_session.scalars(
            select(StockMovement).where(
                StockMovement.ref_type == "return", StockMovement.ref_id == body["id"]
            )
        )
    ).all()
    assert [(m.direction, m.reason, m.qty, m.catalog_product_id) for m in stock_movements] == [
        (StockDirection.IN, StockReason.RETURN, 2, catalog_id)
    ]


async def test_return_requires_open_cash_session_and_rolls_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem="sale-return-no-drawer"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    sale_id = sale["id"]
    sale_line_id = sale["lines"][0]["id"]

    current = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert current.status_code == 200
    session_id = current.json()["id"]
    closed = await client.post(
        f"/api/v1/cash-sessions/{session_id}/close",
        json={"counted_amount": "1240"},
        headers=_auth(token),
    )
    assert closed.status_code == 200, closed.text

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale_id,
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-2"),
    )

    assert resp.status_code == 409, resp.text

    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 8

    returns = (
        await db_session.scalars(select(CustomerReturn).where(CustomerReturn.sale_id == sale_id))
    ).all()
    assert returns == []
    cash_refunds = (
        await db_session.scalars(
            select(CashMovement).where(
                CashMovement.type == CashMovementType.SALE_REFUND_OUT,
                CashMovement.ref_type == "return",
            )
        )
    ).all()
    assert cash_refunds == []
    stock_returns = (
        await db_session.scalars(
            select(StockMovement).where(
                StockMovement.reason == StockReason.RETURN,
                StockMovement.ref_type == "return",
            )
        )
    ).all()
    assert stock_returns == []


async def test_return_qty_cannot_exceed_remaining_returnable_qty(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem="sale-return-over-qty"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    sale_line_id = sale["lines"][0]["id"]

    first = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客部分退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 1}],
        },
        headers=_auth(token, idem="ret-3"),
    )
    assert first.status_code == 201, first.text

    over = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客再次退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-4"),
    )

    assert over.status_code == 422, over.text
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 9

    returns = (
        await db_session.scalars(select(CustomerReturn).where(CustomerReturn.sale_id == sale["id"]))
    ).all()
    assert len(returns) == 1


async def test_store_credit_sale_return_is_rejected_without_side_effects(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id = await _seed(db_session)
    member = Contact(store_id=store_id, name="會員", roles=["MEMBER"])
    db_session.add(member)
    await db_session.flush()
    await StoreCreditService(db_session).adjust(
        store_id,
        member.id,
        amount=Decimal("500"),
        reason="測試入帳",
        created_by=clerk_id,
        idempotency_key="return-store-credit-balance",
    )
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "buyer_contact_id": member.id,
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}],
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
        },
        headers=_auth(token, idem="sale-return-store-credit"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="ret-5"),
    )

    assert resp.status_code == 409, resp.text
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 8
    returns = (
        await db_session.scalars(select(CustomerReturn).where(CustomerReturn.sale_id == sale["id"]))
    ).all()
    assert returns == []


async def test_create_serialized_consignment_return_restocks_and_cancels_settlement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    consignor = Contact(store_id=store_id, name="寄售人")
    db_session.add(consignor)
    await db_session.flush()
    item_id = await _seed_serialized(
        db_session,
        store_id,
        code="RET-SER",
        price="1800",
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor.id,
        commission_pct=40,
    )
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "RET-SER"}]},
        headers=_auth(token, idem="sale-return-serialized"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    sale_line_id = sale["lines"][0]["id"]

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 1}],
        },
        headers=_auth(token, idem="ret-6"),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["refund_amount"] == "1800"

    item = await db_session.get(SerializedItem, item_id)
    assert item is not None
    await db_session.refresh(item)
    assert item.status == SerializedItemStatus.IN_STOCK
    assert item.sold_date is None

    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale["id"])
    )
    assert settlement is not None
    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.CANCELLED
    assert settlement.reclaim_needed is False

    stock_movements = (
        await db_session.scalars(
            select(StockMovement).where(
                StockMovement.ref_type == "return",
                StockMovement.ref_id == resp.json()["id"],
            )
        )
    ).all()
    assert [(m.direction, m.reason, m.qty, m.serialized_item_id) for m in stock_movements] == [
        (StockDirection.IN, StockReason.RETURN, 1, item_id)
    ]


async def test_partial_return_of_only_consignment_line_cancels_its_settlement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """多品項銷售中只退寄售品時，該結算須反轉、不可仍被付款（Codex High：partial-return）。"""
    token, store_id, _ = await _seed(db_session)
    consignor = Contact(store_id=store_id, name="寄售人")
    db_session.add(consignor)
    await db_session.flush()
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    await _seed_serialized(
        db_session,
        store_id,
        code="RET-MIX-CON",
        price="1800",
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor.id,
        commission_pct=40,
    )
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "SERIALIZED", "item_code": "RET-MIX-CON"},
                {"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 1},
            ]
        },
        headers=_auth(token, idem="sale-partial-consignment"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    serialized_line_id = next(
        line["id"] for line in sale["lines"] if line["line_type"] == "SERIALIZED"
    )
    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale["id"])
    )
    assert settlement is not None

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "只退寄售品",
            "lines": [{"sale_line_id": serialized_line_id, "qty": 1}],
        },
        headers=_auth(token, idem="ret-7"),
    )
    assert resp.status_code == 201, resp.text

    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.CANCELLED

    db_sale = await db_session.get(Sale, sale["id"])
    assert db_sale is not None
    await db_session.refresh(db_sale)
    assert db_sale.status != SaleStatus.RETURNED

    # 結算已反轉 → 不可再付款給寄售人（避免對已退商品漏付現金）。
    pay = await client.post(
        f"/api/v1/consignment/settlements/{settlement.id}/pay",
        headers=_auth(token, idem="pay-after-partial-return"),
    )
    assert pay.status_code == 409, pay.text


async def test_create_bulk_return_reopens_sold_out_lot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    lot_id = await _seed_bulk_lot(db_session, store_id, price="80", qty=2)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "BULK_LOT", "bulk_lot_id": lot_id, "qty": 2}]},
        headers=_auth(token, idem="sale-return-bulk"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    sale_line_id = sale["lines"][0]["id"]

    lot = await db_session.get(BulkLot, lot_id)
    assert lot is not None
    await db_session.refresh(lot)
    initial_status = lot.status
    assert initial_status == BulkLotStatus.SOLD_OUT
    assert lot.remaining_qty == 0

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale_line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-8"),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["refund_amount"] == "160"
    await db_session.refresh(lot)
    returned_status = lot.status
    assert returned_status == BulkLotStatus.ON_SALE
    assert lot.remaining_qty == 2

    stock_movements = (
        await db_session.scalars(
            select(StockMovement).where(
                StockMovement.ref_type == "return",
                StockMovement.ref_id == resp.json()["id"],
            )
        )
    ).all()
    assert [(m.direction, m.reason, m.qty, m.bulk_lot_id) for m in stock_movements] == [
        (StockDirection.IN, StockReason.RETURN, 2, lot_id)
    ]


async def test_paid_consignment_return_marks_reclaim_needed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    consignor = Contact(store_id=store_id, name="寄售人")
    db_session.add(consignor)
    await db_session.flush()
    await _seed_serialized(
        db_session,
        store_id,
        code="RET-PAID-CON",
        price="1800",
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor.id,
        commission_pct=40,
    )
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "RET-PAID-CON"}]},
        headers=_auth(token, idem="sale-return-paid-consignment"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale["id"])
    )
    assert settlement is not None

    paid = await client.post(
        f"/api/v1/consignment/settlements/{settlement.id}/pay",
        headers=_auth(token, idem="pay-before-return"),
    )
    assert paid.status_code == 200, paid.text

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="ret-9"),
    )

    assert resp.status_code == 201, resp.text
    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.PAID
    assert settlement.reclaim_needed is True


async def test_return_missing_idempotency_key_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """缺 Idempotency-Key → 422（防無保護的重複退現）。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 1}]},
        headers=_auth(token, idem="sale-no-idem"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "顧客退貨",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_return_idempotent_replay_returns_same_without_double_refund(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key 同內容重送 → 回原退貨單、只退現一次、庫存只回補一次。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem="sale-idem-replay"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    body = {
        "sale_id": sale["id"],
        "reason": "顧客退貨",
        "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
    }

    first = await client.post("/api/v1/returns", json=body, headers=_auth(token, idem="ret-idem"))
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/returns", json=body, headers=_auth(token, idem="ret-idem"))
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]

    returns = (
        await db_session.scalars(select(CustomerReturn).where(CustomerReturn.sale_id == sale["id"]))
    ).all()
    assert len(returns) == 1
    refunds = (
        await db_session.scalars(
            select(CashMovement).where(
                CashMovement.type == CashMovementType.SALE_REFUND_OUT,
                CashMovement.ref_type == "return",
                CashMovement.ref_id == first.json()["id"],
            )
        )
    ).all()
    assert [m.amount for m in refunds] == [Decimal("120")]
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    await db_session.refresh(product)
    assert product.quantity_on_hand == 9  # 只回補 1（非 2）


async def test_return_same_idempotency_key_different_body_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key 不同內容 → 409，不靜默把不同退貨當成功。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem="sale-idem-conflict"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    line_id = sale["lines"][0]["id"]

    first = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退一件",
            "lines": [{"sale_line_id": line_id, "qty": 1}],
        },
        headers=_auth(token, idem="ret-dup"),
    )
    assert first.status_code == 201, first.text
    conflict = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退兩件",
            "lines": [{"sale_line_id": line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-dup"),
    )
    assert conflict.status_code == 409, conflict.text


async def test_return_cross_store_tenant_mismatch_blocked_by_db(
    db_session: AsyncSession,
) -> None:
    """DB 層複合 FK 擋下「退貨單 store 與其銷售 store 不一致」（租戶完整性，§4）。"""
    token_a, store_a, clerk_a = await _seed(db_session)
    store_b = Store(name="他店")
    db_session.add(store_b)
    await db_session.flush()
    catalog_id = await _seed_catalog(db_session, store_a, price="100", qty=5)

    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        sale_resp = await c.post(
            "/api/v1/sales",
            json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 1}]},
            headers=_auth(token_a, idem="sale-tenant"),
        )
    app.dependency_overrides.clear()
    assert sale_resp.status_code == 201, sale_resp.text
    sale_id = sale_resp.json()["id"]

    # store_b 的退貨單參照 store_a 的銷售 → 複合 FK fk_returns_sale_store 應擋下。
    db_session.add(
        CustomerReturn(
            store_id=store_b.id,
            sale_id=sale_id,
            refund_amount=Decimal("100"),
            reason="跨店",
            clerk_user_id=clerk_a,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def _make_cash_sale(
    client: httpx.AsyncClient, token: str, catalog_id: int, idem: str
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}]},
        headers=_auth(token, idem=idem),
    )
    assert resp.status_code == 201, resp.text
    sale: dict[str, Any] = resp.json()
    return sale


async def test_get_unknown_return_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """查不存在的退貨單 → 404。"""
    token, _, _ = await _seed(db_session)
    resp = await client.get("/api/v1/returns/999999", headers=_auth(token))
    assert resp.status_code == 404, resp.text


async def test_return_blank_reason_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """全空白原因（通過 schema minLength 但 strip 後為空）→ 422。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale = await _make_cash_sale(client, token, catalog_id, "sale-blank-reason")
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "   ",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="ret-blank"),
    )
    assert resp.status_code == 422, resp.text


async def test_return_duplicate_sale_line_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一 sale_line 在一張退貨單重複列入 → 422。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale = await _make_cash_sale(client, token, catalog_id, "sale-dup-line")
    line_id = sale["lines"][0]["id"]
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退貨",
            "lines": [
                {"sale_line_id": line_id, "qty": 1},
                {"sale_line_id": line_id, "qty": 1},
            ],
        },
        headers=_auth(token, idem="ret-dup-line"),
    )
    assert resp.status_code == 422, resp.text


async def test_return_unknown_sale_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """退不存在的銷售單 → 404。"""
    token, _, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/returns",
        json={"sale_id": 999999, "reason": "退貨", "lines": [{"sale_line_id": 1, "qty": 1}]},
        headers=_auth(token, idem="ret-unknown-sale"),
    )
    assert resp.status_code == 404, resp.text


async def test_return_foreign_sale_line_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """退貨明細指向不屬於該銷售單的 sale_line → 422。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale = await _make_cash_sale(client, token, catalog_id, "sale-foreign-line")
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退貨",
            "lines": [{"sale_line_id": 888888, "qty": 1}],
        },
        headers=_auth(token, idem="ret-foreign-line"),
    )
    assert resp.status_code == 422, resp.text


async def test_return_on_voided_sale_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已作廢的銷售單不可退貨 → 409。"""
    token, store_id, _ = await _seed(db_session)
    manager = User(store_id=store_id, username="mgr-ret", password_hash="h", role=UserRole.MANAGER)
    db_session.add(manager)
    await db_session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale = await _make_cash_sale(client, token, catalog_id, "sale-void-then-return")
    voided = await client.post(f"/api/v1/sales/{sale['id']}/void", headers=_auth(mgr_token))
    assert voided.status_code == 200, voided.text
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "退貨",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="ret-on-void"),
    )
    assert resp.status_code == 409, resp.text


async def test_return_fully_returned_sale_again_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已全數退貨的銷售單再退 → 409。"""
    token, store_id, _ = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="120", qty=10)
    sale = await _make_cash_sale(client, token, catalog_id, "sale-full-then-again")
    line_id = sale["lines"][0]["id"]
    first = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "全退",
            "lines": [{"sale_line_id": line_id, "qty": 2}],
        },
        headers=_auth(token, idem="ret-full-1"),
    )
    assert first.status_code == 201, first.text
    again = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "再退",
            "lines": [{"sale_line_id": line_id, "qty": 1}],
        },
        headers=_auth(token, idem="ret-full-2"),
    )
    assert again.status_code == 409, again.text


async def test_return_line_cross_store_sale_line_blocked_by_db(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """DB 層複合 FK 擋下「退貨明細 store 與其 sale_line store 不一致」（租戶完整性，§4）。

    兩店各自有自洽的銷售與退貨單；把 store_b 退貨單的一筆明細指向 store_a 的 sale_line，
    複合 FK fk_return_lines_sale_line_store 應擋下（即使該退貨單與其銷售同店）。
    """
    token_a, store_a, _ = await _seed(db_session)
    catalog_a = await _seed_catalog(db_session, store_a, price="100", qty=5)
    sale_a = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_a, "qty": 1}]},
        headers=_auth(token_a, idem="sale-tenant-a"),
    )
    assert sale_a.status_code == 201, sale_a.text
    sale_line_a = sale_a.json()["lines"][0]["id"]

    # store_b：自己的店員、銷售、退貨單（皆自洽）。
    store_b = Store(name="他店2")
    db_session.add(store_b)
    await db_session.flush()
    clerk_b = User(store_id=store_b.id, username="clkb", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk_b)
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store_b.id, clerk_b.id, Decimal("1000"))
    token_b = encode_access_token(user_id=clerk_b.id, role="CLERK", store_id=store_b.id)
    catalog_b = await _seed_catalog(db_session, store_b.id, price="100", qty=5)
    sale_b = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_b, "qty": 1}]},
        headers=_auth(token_b, idem="sale-tenant-b"),
    )
    assert sale_b.status_code == 201, sale_b.text
    ret_b = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale_b.json()["id"],
            "reason": "本店退貨",
            "lines": [{"sale_line_id": sale_b.json()["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token_b, idem="ret-tenant-b"),
    )
    assert ret_b.status_code == 201, ret_b.text

    sale_line = await db_session.get(SaleLine, sale_line_a)
    assert sale_line is not None and sale_line.store_id == store_a
    # store_b 退貨單的明細指向 store_a 的 sale_line → 複合 FK 應擋下。
    db_session.add(
        ReturnLine(
            store_id=store_b.id,
            return_id=ret_b.json()["id"],
            sale_line_id=sale_line_a,
            qty=1,
            refund_amount=Decimal("100"),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
