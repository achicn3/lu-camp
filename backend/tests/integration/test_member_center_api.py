"""會員中心讀取 API 整合測試（T21-c；docs/17 §5.2）。

涵蓋 overview / purchases / purchase-detail / consignments / sourced-items：
彙整正確、union 買斷+寄售、PENDING 應撥加總、分頁、store 隔離、不外洩成本、404。

讀取端點不 commit；以 db_session 回滾隔離 + 直接插入 ORM 作種子（DEFERRABLE 守衛於
COMMIT 才驗，測試不提交、不觸發）。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.store.models import Store
from app.modules.storecredit.models import StoreCreditAccount
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    BulkAcquisitionBasis,
    ConsignmentSettlementStatus,
    ContactRole,
    Grade,
    OwnershipType,
    PaymentMethod,
    PayoutMethod,
    SaleLineType,
    SerializedItemStatus,
    TenderType,
    UserRole,
)

pytestmark = pytest.mark.asyncio


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


async def _setup(db_session: AsyncSession) -> tuple[int, str, int, int]:
    """store + manager + 會員A；回 (store_id, token, clerk_id, member_a)。"""
    store = Store(name="會員中心店")
    db_session.add(store)
    await db_session.flush()
    user = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    member = Contact(store_id=store.id, name="會員A", roles=[ContactRole.MEMBER.value])
    db_session.add_all([user, member])
    await db_session.flush()
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=store.id)
    return store.id, token, user.id, member.id


def _sale(store_id: int, clerk_id: int, buyer_id: int, total: int) -> Sale:
    return Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        buyer_contact_id=buyer_id,
        subtotal=Decimal(total),
        tax=Decimal(0),
        total=Decimal(total),
        payment_method=PaymentMethod.CASH,
    )


# ── purchases + detail ──


async def test_member_purchases_list_and_detail(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, clerk_id, member = await _setup(db_session)
    s1 = _sale(store_id, clerk_id, member, 100)
    s2 = _sale(store_id, clerk_id, member, 200)
    db_session.add_all([s1, s2])
    await db_session.flush()
    db_session.add_all(
        [
            SaleLine(
                store_id=store_id,
                sale_id=s2.id,
                line_type=SaleLineType.SERIALIZED,
                description="品X",
                qty=1,
                unit_price=Decimal(200),
                line_total=Decimal(200),
            ),
            SaleTender(
                store_id=store_id, sale_id=s2.id, tender_type=TenderType.CASH, amount=Decimal(200)
            ),
        ]
    )
    await db_session.flush()

    resp = await client.get(f"/api/v1/contacts/{member}/purchases", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert [r["sale_id"] for r in body] == [s2.id, s1.id]  # id desc
    assert body[0]["total"] == "200"  # 字串整數元
    assert body[0]["line_count"] == 1
    assert body[1]["line_count"] == 0

    detail = await client.get(f"/api/v1/contacts/{member}/purchases/{s2.id}", headers=_auth(token))
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["total"] == "200"
    assert [line["description"] for line in dbody["lines"]] == ["品X"]
    assert dbody["tenders"][0]["tender_type"] == "CASH"


async def test_member_purchase_detail_404_for_other_members_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, clerk_id, member = await _setup(db_session)
    other = Contact(store_id=store_id, name="別人", roles=[ContactRole.MEMBER.value])
    db_session.add(other)
    await db_session.flush()
    other_sale = _sale(store_id, clerk_id, other.id, 999)
    db_session.add(other_sale)
    await db_session.flush()
    resp = await client.get(
        f"/api/v1/contacts/{member}/purchases/{other_sale.id}", headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_member_purchases_404_when_contact_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token, _, _ = await _setup(db_session)
    resp = await client.get("/api/v1/contacts/999999/purchases", headers=_auth(token))
    assert resp.status_code == 404


# ── consignments ──


def _consigned_serialized(store_id: int, code: str, consignor_id: int) -> SerializedItem:
    return SerializedItem(
        store_id=store_id,
        item_code=code,
        name="寄售品",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        consignor_id=consignor_id,
        commission_pct=50,
        listed_price=Decimal(1000),
    )


async def test_member_consignments_with_settlement_and_pending_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, clerk_id, member = await _setup(db_session)
    item = _consigned_serialized(store_id, "CON-1", member)
    db_session.add(item)
    await db_session.flush()
    sale = _sale(store_id, clerk_id, member, 1000)
    db_session.add(sale)
    await db_session.flush()
    db_session.add(
        ConsignmentSettlement(
            store_id=store_id,
            serialized_item_id=item.id,
            sale_id=sale.id,
            gross=Decimal(1000),
            commission_pct=50,
            commission_amount=Decimal(500),
            payout_amount=Decimal(500),
            status=ConsignmentSettlementStatus.PENDING,
        )
    )
    # 寄售散裝（無結算）。
    db_session.add(
        BulkLot(
            store_id=store_id,
            lot_code="CON-LOT",
            name="寄售散",
            grade=Grade.E,
            consignor_id=member,
            acquisition_cost=Decimal(500),
            acquisition_basis=BulkAcquisitionBasis.WEIGHT,
            unit_price=Decimal(50),
            total_qty=10,
            remaining_qty=10,
        )
    )
    await db_session.flush()

    resp = await client.get(f"/api/v1/contacts/{member}/consignments", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["pending_payout_total"] == "500"
    kinds = {i["kind"] for i in body["items"]}
    assert kinds == {"SERIALIZED", "BULK_LOT"}
    ser = next(i for i in body["items"] if i["kind"] == "SERIALIZED")
    assert ser["payout_amount"] == "500"
    assert ser["settlement_status"] == "PENDING"
    assert ser["commission_pct"] == 50
    lot = next(i for i in body["items"] if i["kind"] == "BULK_LOT")
    assert lot["payout_amount"] is None


# ── sourced-items（union 買斷 + 寄售）──


async def test_member_sourced_items_union_buyout_and_consignment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, clerk_id, member = await _setup(db_session)
    acq = Acquisition(
        store_id=store_id,
        type=AcquisitionType.BUYOUT,
        contact_id=member,
        clerk_user_id=clerk_id,
        payout_method=PayoutMethod.CASH,
        total_cash_paid=Decimal(100),
        payout_cash_amount=Decimal(100),
    )
    db_session.add(acq)
    await db_session.flush()
    db_session.add_all(
        [
            SerializedItem(
                store_id=store_id,
                item_code="BUY-1",
                name="買斷品",
                grade=Grade.A,
                ownership_type=OwnershipType.OWNED,
                acquisition_id=acq.id,
                acquisition_cost=Decimal(100),
                listed_price=Decimal(300),
            ),
            _consigned_serialized(store_id, "CON-1", member),
            BulkLot(  # 買斷散裝（自有；經收購單）。
                store_id=store_id,
                lot_code="BUY-LOT",
                name="買斷散",
                grade=Grade.E,
                acquisition_id=acq.id,
                acquisition_cost=Decimal(100),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(20),
                total_qty=5,
                remaining_qty=5,
            ),
            BulkLot(  # 寄售散裝。
                store_id=store_id,
                lot_code="CON-LOT",
                name="寄售散",
                grade=Grade.E,
                consignor_id=member,
                acquisition_cost=Decimal(100),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(30),
                total_qty=5,
                remaining_qty=5,
            ),
        ]
    )
    await db_session.flush()

    resp = await client.get(f"/api/v1/contacts/{member}/sourced-items", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    by_code = {r["code"]: r for r in body}
    assert by_code["BUY-1"]["source_type"] == "BUYOUT"
    assert by_code["CON-1"]["source_type"] == "CONSIGNMENT"
    assert by_code["BUY-LOT"]["source_type"] == "BUYOUT"
    assert by_code["BUY-LOT"]["kind"] == "BULK_LOT"
    assert by_code["CON-LOT"]["source_type"] == "CONSIGNMENT"
    # 成本不外洩。
    assert "acquisition_cost" not in by_code["BUY-1"]
    assert by_code["BUY-1"]["listed_price"] == "300"

    # source_type 過濾。
    only_buy = await client.get(
        f"/api/v1/contacts/{member}/sourced-items",
        params={"source_type": "BUYOUT"},
        headers=_auth(token),
    )
    assert {r["code"] for r in only_buy.json()} == {"BUY-1", "BUY-LOT"}

    # status 過濾：散裝預設 ON_SALE → 只回兩個散裝堆。
    on_sale = await client.get(
        f"/api/v1/contacts/{member}/sourced-items",
        params={"status": "ON_SALE"},
        headers=_auth(token),
    )
    assert {r["code"] for r in on_sale.json()} == {"BUY-LOT", "CON-LOT"}


async def test_member_consignments_latest_settlement_per_item_not_starved(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # A 有兩筆結算、B 有一筆：B 不可因 A 的多筆而被略過（Codex review P2）。
    store_id, token, clerk_id, member = await _setup(db_session)
    item_a = _consigned_serialized(store_id, "A", member)
    item_b = _consigned_serialized(store_id, "B", member)
    db_session.add_all([item_a, item_b])
    await db_session.flush()
    sale = _sale(store_id, clerk_id, member, 100)
    db_session.add(sale)
    await db_session.flush()

    def _settlement(
        item_id: int, payout: int, status: ConsignmentSettlementStatus
    ) -> ConsignmentSettlement:
        return ConsignmentSettlement(
            store_id=store_id,
            serialized_item_id=item_id,
            sale_id=sale.id,
            gross=Decimal(payout * 2),
            commission_pct=50,
            commission_amount=Decimal(payout),
            payout_amount=Decimal(payout),
            status=status,
        )

    db_session.add(_settlement(item_a.id, 100, ConsignmentSettlementStatus.CANCELLED))
    db_session.add(_settlement(item_a.id, 200, ConsignmentSettlementStatus.PENDING))  # 較新
    db_session.add(_settlement(item_b.id, 50, ConsignmentSettlementStatus.PAID))
    await db_session.flush()

    resp = await client.get(f"/api/v1/contacts/{member}/consignments", headers=_auth(token))
    assert resp.status_code == 200
    by_code = {i["code"]: i for i in resp.json()["items"]}
    assert by_code["A"]["payout_amount"] == "200"  # A 取最新
    assert by_code["B"]["payout_amount"] == "50"  # B 未被略過
    assert by_code["B"]["settlement_status"] == "PAID"


async def test_member_consignments_pagination_merges_then_slices(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 兩來源各 2 列、limit=2 → 合併後正好 2 列（非 2×limit；Codex review P2）。
    store_id, token, _, member = await _setup(db_session)
    db_session.add_all([_consigned_serialized(store_id, f"SER-{i}", member) for i in range(2)])
    db_session.add_all(
        [
            BulkLot(
                store_id=store_id,
                lot_code=f"LOT-{i}",
                name="寄售散",
                grade=Grade.E,
                consignor_id=member,
                acquisition_cost=Decimal(100),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(50),
                total_qty=5,
                remaining_qty=5,
            )
            for i in range(2)
        ]
    )
    await db_session.flush()
    resp = await client.get(
        f"/api/v1/contacts/{member}/consignments",
        params={"limit": 2, "offset": 0},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


async def test_member_sourced_status_filter_not_truncated_by_pagination(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 較新的列為 SOLD、較舊一列 IN_STOCK；status=IN_STOCK&limit=1 必須回到那筆 IN_STOCK
    # （過濾下推 DB、在 LIMIT 之前；Codex review P2）。
    store_id, token, _, member = await _setup(db_session)
    older = _consigned_serialized(store_id, "OLD-INSTOCK", member)  # IN_STOCK 預設
    db_session.add(older)
    await db_session.flush()
    for i in range(3):
        sold = _consigned_serialized(store_id, f"NEW-SOLD-{i}", member)
        sold.status = SerializedItemStatus.SOLD
        db_session.add(sold)
    await db_session.flush()
    resp = await client.get(
        f"/api/v1/contacts/{member}/sourced-items",
        params={"status": "IN_STOCK", "limit": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["code"] for r in body] == ["OLD-INSTOCK"]


async def test_member_consignments_and_sourced_404_when_contact_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token, _, _ = await _setup(db_session)
    assert (
        await client.get("/api/v1/contacts/999999/consignments", headers=_auth(token))
    ).status_code == 404
    assert (
        await client.get("/api/v1/contacts/999999/sourced-items", headers=_auth(token))
    ).status_code == 404


# ── overview ──


async def test_member_overview(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id, token, clerk_id, member = await _setup(db_session)
    db_session.add(_sale(store_id, clerk_id, member, 100))
    db_session.add(StoreCreditAccount(store_id=store_id, contact_id=member, balance=Decimal(250)))
    item = _consigned_serialized(store_id, "CON-OV", member)
    db_session.add(item)
    await db_session.flush()
    # 寄售品賣給「別的客人」（買方非寄售人）→ 不計入會員本人的消費筆數。
    sale2 = Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        buyer_contact_id=None,
        subtotal=Decimal(800),
        tax=Decimal(0),
        total=Decimal(800),
        payment_method=PaymentMethod.CASH,
    )
    db_session.add(sale2)
    await db_session.flush()
    db_session.add(
        ConsignmentSettlement(
            store_id=store_id,
            serialized_item_id=item.id,
            sale_id=sale2.id,
            gross=Decimal(800),
            commission_pct=50,
            commission_amount=Decimal(400),
            payout_amount=Decimal(400),
            status=ConsignmentSettlementStatus.PENDING,
        )
    )
    await db_session.flush()

    resp = await client.get(f"/api/v1/contacts/{member}/overview", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["contact"]["id"] == member
    assert body["store_credit_balance"] == "250"
    assert body["pending_consignment_payout"] == "400"
    assert body["counts"]["purchases"] == 1
    assert body["counts"]["consigned_items"] == 1
    assert len(body["recent_purchases"]) == 1


async def test_member_overview_cross_store_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, _, _, member = await _setup(db_session)
    other_store = Store(name="他店")
    db_session.add(other_store)
    await db_session.flush()
    other_user = User(
        store_id=other_store.id, username="o", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(other_user)
    await db_session.flush()
    token_b = encode_access_token(user_id=other_user.id, role="MANAGER", store_id=other_store.id)
    resp = await client.get(f"/api/v1/contacts/{member}/overview", headers=_auth(token_b))
    assert resp.status_code == 404
