"""T21-b：會員中心讀取查詢（各模組唯讀方法；docs/17 §1、§5）。

驗證 facade 將用到的各模組唯讀查詢：sales by-buyer、acquisition by-contact、
inventory by-consignor / by-acquisitions、consignment by-item-ids（含 PENDING 應撥加總）。
皆 store 範圍、分頁、排序；跨模組邊界由 facade（T21-c）以 item_ids 串接（不跨表）。

用 db_session 回滾隔離 + 直接插入 ORM（DEFERRABLE 守衛於 COMMIT 才驗，測試不提交、不觸發）。
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.service import AcquisitionService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    BulkAcquisitionBasis,
    ConsignmentSettlementStatus,
    ContactRole,
    Grade,
    OwnershipType,
    PayoutMethod,
    UserRole,
)

pytestmark = pytest.mark.asyncio


async def _seed_base(session: AsyncSession) -> tuple[int, int, int, int]:
    """建 store + clerk + 兩名會員；回 (store_id, clerk_id, member_a, member_b)。"""
    store = Store(name="會員讀取店")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    member_a = Contact(store_id=store.id, name="會員A", roles=[ContactRole.MEMBER.value])
    member_b = Contact(store_id=store.id, name="會員B", roles=[ContactRole.MEMBER.value])
    session.add_all([clerk, member_a, member_b])
    await session.flush()
    return store.id, clerk.id, member_a.id, member_b.id


def _sale(store_id: int, clerk_id: int, buyer_id: int | None, total: int) -> Sale:
    return Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        buyer_contact_id=buyer_id,
        subtotal=Decimal(total),
        tax=Decimal(0),
        total=Decimal(total),
    )


# ── sales：list_purchases_by_buyer ──


async def test_list_purchases_by_buyer_filters_and_orders(db_session: AsyncSession) -> None:
    store_id, clerk_id, member_a, member_b = await _seed_base(db_session)
    db_session.add_all(
        [
            _sale(store_id, clerk_id, member_a, 100),
            _sale(store_id, clerk_id, member_a, 200),
            _sale(store_id, clerk_id, member_b, 300),
            _sale(store_id, clerk_id, None, 400),  # 無買方
        ]
    )
    await db_session.flush()

    svc = SalesService(db_session)
    a_sales = await svc.list_purchases_by_buyer(store_id, member_a)
    assert [s.total for s in a_sales] == [Decimal(200), Decimal(100)]  # id desc
    b_sales = await svc.list_purchases_by_buyer(store_id, member_b)
    assert [s.total for s in b_sales] == [Decimal(300)]


async def test_list_purchases_by_buyer_paginates(db_session: AsyncSession) -> None:
    store_id, clerk_id, member_a, _ = await _seed_base(db_session)
    db_session.add_all([_sale(store_id, clerk_id, member_a, i) for i in range(1, 6)])
    await db_session.flush()
    svc = SalesService(db_session)
    page1 = await svc.list_purchases_by_buyer(store_id, member_a, limit=2, offset=0)
    page2 = await svc.list_purchases_by_buyer(store_id, member_a, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {s.id for s in page1}.isdisjoint({s.id for s in page2})


async def test_list_purchases_by_buyer_date_filter_before_pagination(
    db_session: AsyncSession,
) -> None:
    # 日期過濾須在分頁前套用：區間外的新單不可吃掉名額（Codex review P2）。
    store_id, clerk_id, member_a, _ = await _seed_base(db_session)
    old = _sale(store_id, clerk_id, member_a, 100)
    old.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    new = _sale(store_id, clerk_id, member_a, 200)
    new.created_at = datetime(2026, 6, 1, tzinfo=UTC)
    db_session.add_all([old, new])
    await db_session.flush()

    svc = SalesService(db_session)
    only_old = await svc.list_purchases_by_buyer(
        store_id, member_a, date_to=datetime(2026, 3, 1, tzinfo=UTC)
    )
    assert [s.total for s in only_old] == [Decimal(100)]
    only_new = await svc.list_purchases_by_buyer(
        store_id, member_a, date_from=datetime(2026, 3, 1, tzinfo=UTC)
    )
    assert [s.total for s in only_new] == [Decimal(200)]


async def test_list_purchases_by_buyer_store_scoped(db_session: AsyncSession) -> None:
    store_id, clerk_id, member_a, _ = await _seed_base(db_session)
    db_session.add(_sale(store_id, clerk_id, member_a, 100))
    await db_session.flush()
    svc = SalesService(db_session)
    assert await svc.list_purchases_by_buyer(store_id + 999, member_a) == []


# ── acquisition：list_by_contact ──


async def test_list_acquisitions_by_contact(db_session: AsyncSession) -> None:
    store_id, clerk_id, member_a, member_b = await _seed_base(db_session)
    db_session.add_all(
        [
            Acquisition(
                store_id=store_id,
                type=AcquisitionType.CONSIGNMENT,
                contact_id=member_a,
                clerk_user_id=clerk_id,
            ),
            Acquisition(
                store_id=store_id,
                type=AcquisitionType.CONSIGNMENT,
                contact_id=member_a,
                clerk_user_id=clerk_id,
            ),
            Acquisition(
                store_id=store_id,
                type=AcquisitionType.CONSIGNMENT,
                contact_id=member_b,
                clerk_user_id=clerk_id,
            ),
        ]
    )
    await db_session.flush()

    svc = AcquisitionService(db_session)
    a_acqs = await svc.list_by_contact(store_id, member_a)
    assert len(a_acqs) == 2
    assert all(a.contact_id == member_a for a in a_acqs)
    assert a_acqs[0].id > a_acqs[1].id  # id desc
    assert await svc.list_by_contact(store_id + 999, member_a) == []


# ── inventory：by-consignor / by-acquisitions ──


def _serialized(
    store_id: int,
    code: str,
    *,
    ownership: OwnershipType,
    consignor_id: int | None = None,
    acquisition_id: int | None = None,
) -> SerializedItem:
    return SerializedItem(
        store_id=store_id,
        item_code=code,
        name="品",
        grade=Grade.A,
        ownership_type=ownership,
        consignor_id=consignor_id,
        acquisition_id=acquisition_id,
        acquisition_cost=Decimal(100),
        listed_price=Decimal(300),
    )


async def test_list_serialized_by_consignor(db_session: AsyncSession) -> None:
    store_id, _, member_a, member_b = await _seed_base(db_session)
    cons = OwnershipType.CONSIGNMENT
    db_session.add_all(
        [
            _serialized(store_id, "C-A1", ownership=cons, consignor_id=member_a),
            _serialized(store_id, "C-A2", ownership=cons, consignor_id=member_a),
            _serialized(store_id, "C-B1", ownership=cons, consignor_id=member_b),
            _serialized(store_id, "OWN", ownership=OwnershipType.OWNED),
        ]
    )
    await db_session.flush()
    svc = InventoryService(db_session)
    items = await svc.list_serialized(store_id, consignor_id=member_a)
    assert {i.item_code for i in items} == {"C-A1", "C-A2"}


async def test_list_bulk_lots_by_consignor(db_session: AsyncSession) -> None:
    store_id, _, member_a, member_b = await _seed_base(db_session)
    db_session.add_all(
        [
            BulkLot(
                store_id=store_id,
                lot_code="L-A",
                name="散A",
                grade=Grade.E,
                consignor_id=member_a,
                acquisition_cost=Decimal(500),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(50),
                total_qty=10,
                remaining_qty=10,
            ),
            BulkLot(
                store_id=store_id,
                lot_code="L-B",
                name="散B",
                grade=Grade.E,
                consignor_id=member_b,
                acquisition_cost=Decimal(500),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(50),
                total_qty=10,
                remaining_qty=10,
            ),
        ]
    )
    await db_session.flush()
    svc = InventoryService(db_session)
    lots = await svc.list_bulk_lots(store_id, consignor_id=member_a)
    assert {lot.lot_code for lot in lots} == {"L-A"}


async def test_list_serialized_by_acquisitions(db_session: AsyncSession) -> None:
    store_id, clerk_id, member_a, _ = await _seed_base(db_session)
    acq = Acquisition(
        store_id=store_id,
        type=AcquisitionType.BUYOUT,
        contact_id=member_a,
        clerk_user_id=clerk_id,
        payout_method=PayoutMethod.CASH,
        total_cash_paid=Decimal(100),
        payout_cash_amount=Decimal(100),
    )
    db_session.add(acq)
    await db_session.flush()
    db_session.add_all(
        [
            _serialized(store_id, "BUY1", ownership=OwnershipType.OWNED, acquisition_id=acq.id),
            _serialized(store_id, "OTHER", ownership=OwnershipType.OWNED),
            # 同收購單下的寄售品：必須**不**經買斷路徑回傳（避免與 consignor 路徑重複）。
            _serialized(
                store_id,
                "CONS-UNDER-ACQ",
                ownership=OwnershipType.CONSIGNMENT,
                consignor_id=member_a,
                acquisition_id=acq.id,
            ),
            BulkLot(
                store_id=store_id,
                lot_code="BLK1",
                name="散買",
                grade=Grade.E,
                acquisition_id=acq.id,
                acquisition_cost=Decimal(100),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(50),
                total_qty=2,
                remaining_qty=2,
            ),
            BulkLot(  # 同收購單下的寄售散裝：亦不應經買斷路徑回傳。
                store_id=store_id,
                lot_code="BLK-CONS",
                name="散寄",
                grade=Grade.E,
                acquisition_id=acq.id,
                consignor_id=member_a,
                acquisition_cost=Decimal(100),
                acquisition_basis=BulkAcquisitionBasis.WEIGHT,
                unit_price=Decimal(50),
                total_qty=2,
                remaining_qty=2,
            ),
        ]
    )
    await db_session.flush()
    svc = InventoryService(db_session)
    items = await svc.list_serialized_by_acquisitions(store_id, [acq.id])
    assert {i.item_code for i in items} == {"BUY1"}  # 寄售品被排除
    lots = await svc.list_bulk_lots_by_acquisitions(store_id, [acq.id])
    assert {lot.lot_code for lot in lots} == {"BLK1"}  # 寄售散裝被排除
    # 空 ids → 空清單（不回全部）。
    assert await svc.list_serialized_by_acquisitions(store_id, []) == []
    assert await svc.list_bulk_lots_by_acquisitions(store_id, []) == []


# ── consignment：by-item-ids + PENDING 應撥加總 ──


async def test_consignment_settlements_and_pending_total_by_item_ids(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, member_a, _ = await _seed_base(db_session)
    item = _serialized(store_id, "C1", ownership=OwnershipType.CONSIGNMENT, consignor_id=member_a)
    db_session.add(item)
    await db_session.flush()
    sale = _sale(store_id, clerk_id, member_a, 1000)
    db_session.add(sale)
    await db_session.flush()
    db_session.add_all(
        [
            ConsignmentSettlement(
                store_id=store_id,
                serialized_item_id=item.id,
                sale_id=sale.id,
                gross=Decimal(1000),
                commission_pct=50,
                commission_amount=Decimal(500),
                payout_amount=Decimal(500),
                status=ConsignmentSettlementStatus.PENDING,
            ),
            ConsignmentSettlement(
                store_id=store_id,
                serialized_item_id=item.id,
                sale_id=sale.id,
                gross=Decimal(400),
                commission_pct=50,
                commission_amount=Decimal(200),
                payout_amount=Decimal(200),
                status=ConsignmentSettlementStatus.PAID,
            ),
        ]
    )
    await db_session.flush()

    svc = ConsignmentService(db_session)
    settlements = await svc.list_settlements_by_item_ids(store_id, [item.id])
    assert len(settlements) == 2
    # PENDING 應撥加總只計 PENDING（500），不含 PAID（200）。
    assert await svc.pending_payout_total_by_item_ids(store_id, [item.id]) == Decimal(500)
    # 空 ids → 0 / 空。
    assert await svc.pending_payout_total_by_item_ids(store_id, []) == Decimal(0)
    assert await svc.list_settlements_by_item_ids(store_id, []) == []
