"""acquisition 領域/服務測試（對齊 docs/07 Phase 2、CLAUDE.md §7.2/§7.8）。

整筆原子 rollback 的併發/失敗落地驗證見 tests/integration/test_acquisition_atomic.py，
API 端點見 tests/integration/test_acquisition_api.py。
"""

import itertools
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.acquisition.schemas import (
    AcquisitionCreate,
    AcquisitionItemIn,
    AcquisitionLotIn,
)
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, SerializedItem, StockMovement
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    BulkAcquisitionBasis,
    CashMovementType,
    Grade,
    ItemKind,
    OwnershipType,
    SerializedItemStatus,
    StockDirection,
    StockReason,
    UserRole,
)
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    InvalidCommissionPct,
    NoOpenCashSession,
)

_svc_idem = itertools.count()


async def _seed(
    session: AsyncSession, *, with_national_id: bool = True, open_drawer: bool = True
) -> tuple[int, int, int]:
    """建 store/clerk/contact，回傳 (store_id, clerk_id, contact_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clerk", password_hash="h", role=UserRole.CLERK)
    contact = Contact(
        store_id=store.id,
        name="賣家",
        national_id_enc="enc" if with_national_id else None,
    )
    session.add_all([clerk, contact])
    await session.flush()
    if open_drawer:
        await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    return store.id, clerk.id, contact.id


def _buyout(contact_id: int) -> AcquisitionCreate:
    return AcquisitionCreate(
        type=AcquisitionType.BUYOUT,
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="二手相機",
                grade=Grade.A,
                listed_price=Decimal("3000"),
                acquisition_cost=Decimal("1800"),
            ),
            AcquisitionItemIn(
                name="鏡頭",
                grade=Grade.B,
                listed_price=Decimal("1500"),
                acquisition_cost=Decimal("700"),
            ),
        ],
    )


async def test_buyout_creates_items_stock_and_cash(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session)
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, _buyout(contact_id), idempotency_key=f"svc-{next(_svc_idem)}"
    )

    assert result.acquisition_id > 0
    assert len(result.item_codes) == 2
    assert result.lot_code is None
    assert result.total_cash_paid == Decimal("2500")  # 1800 + 700

    items = list(
        await db_session.scalars(
            select(SerializedItem).where(SerializedItem.acquisition_id == result.acquisition_id)
        )
    )
    assert len(items) == 2
    assert all(i.ownership_type == OwnershipType.OWNED for i in items)
    assert all(i.status == SerializedItemStatus.IN_STOCK for i in items)

    movements = list(
        await db_session.scalars(
            select(StockMovement).where(StockMovement.ref_id == result.acquisition_id)
        )
    )
    assert len(movements) == 2
    assert all(
        m.direction == StockDirection.IN
        and m.reason == StockReason.ACQUISITION
        and m.item_kind == ItemKind.SERIALIZED
        for m in movements
    )

    cash = list(
        await db_session.scalars(
            select(CashMovement).where(CashMovement.ref_id == result.acquisition_id)
        )
    )
    assert len(cash) == 1
    assert cash[0].type == CashMovementType.BUYOUT_OUT
    assert cash[0].amount == Decimal("2500")


async def test_consignment_creates_items_no_cash(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session)
    data = AcquisitionCreate(
        type=AcquisitionType.CONSIGNMENT,
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="寄售包", grade=Grade.S, listed_price=Decimal("5000"), commission_pct=40
            )
        ],
    )
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, data, idempotency_key=f"svc-{next(_svc_idem)}"
    )
    assert result.total_cash_paid is None
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == result.acquisition_id)
    )
    assert item is not None
    assert item.ownership_type == OwnershipType.CONSIGNMENT
    assert item.consignor_id == contact_id
    assert item.commission_pct == 40

    cash_count = await db_session.scalar(
        select(func.count())
        .select_from(CashMovement)
        .where(CashMovement.ref_id == result.acquisition_id)
    )
    assert cash_count == 0


async def test_consignment_without_open_drawer_succeeds(db_session: AsyncSession) -> None:
    """寄售不付現，無需開帳即可入庫（§7.8 只限影響現金的操作）。"""
    store_id, clerk_id, contact_id = await _seed(db_session, open_drawer=False)
    data = AcquisitionCreate(
        type=AcquisitionType.CONSIGNMENT,
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="寄售品", grade=Grade.A, listed_price=Decimal("2000"), commission_pct=50
            )
        ],
    )
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, data, idempotency_key=f"svc-{next(_svc_idem)}"
    )
    assert result.acquisition_id > 0


async def test_bulk_lot_creates_lot_stock_and_cash(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session)
    data = AcquisitionCreate(
        type=AcquisitionType.BULK_LOT,
        contact_id=contact_id,
        lot=AcquisitionLotIn(
            name="二手衣散裝A堆",
            acquisition_cost=Decimal("3000"),
            acquisition_basis=BulkAcquisitionBasis.WEIGHT,
            total_qty=50,
            unit_price=Decimal("100"),
            label="A堆",
        ),
    )
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, data, idempotency_key=f"svc-{next(_svc_idem)}"
    )
    assert result.lot_code is not None
    assert result.item_codes == []
    assert result.total_cash_paid == Decimal("3000")

    lot = await db_session.scalar(
        select(BulkLot).where(BulkLot.acquisition_id == result.acquisition_id)
    )
    assert lot is not None
    assert lot.grade == Grade.E
    assert lot.remaining_qty == 50

    movement = await db_session.scalar(
        select(StockMovement).where(StockMovement.ref_id == result.acquisition_id)
    )
    assert movement is not None
    assert movement.item_kind == ItemKind.BULK_LOT
    assert movement.qty == 50

    cash = await db_session.scalar(
        select(CashMovement).where(CashMovement.ref_id == result.acquisition_id)
    )
    assert cash is not None
    assert cash.type == CashMovementType.BUYOUT_OUT
    assert cash.amount == Decimal("3000")

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "CREATE_ACQUISITION",
            AuditLog.entity_id == str(result.acquisition_id),
        )
    )
    assert audit is not None and audit.after is not None
    assert audit.after["type"] == "BULK_LOT"
    assert audit.after["item_count"] == 0
    assert audit.after["lot_code"] == result.lot_code
    assert audit.after["total_cash_paid"] == "3000"


async def test_contact_not_found(db_session: AsyncSession) -> None:
    store_id, clerk_id, _ = await _seed(db_session)
    with pytest.raises(ContactNotFound):
        await AcquisitionService(db_session).create_acquisition(
            store_id, clerk_id, _buyout(999999), idempotency_key="svc-nf"
        )


async def test_contact_without_national_id_rejected(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session, with_national_id=False)
    with pytest.raises(AcquisitionRequiresNationalId):
        await AcquisitionService(db_session).create_acquisition(
            store_id, clerk_id, _buyout(contact_id), idempotency_key=f"svc-{next(_svc_idem)}"
        )


async def test_buyout_without_open_drawer_rejected(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session, open_drawer=False)
    with pytest.raises(NoOpenCashSession):
        await AcquisitionService(db_session).create_acquisition(
            store_id, clerk_id, _buyout(contact_id), idempotency_key=f"svc-{next(_svc_idem)}"
        )


async def test_commission_pct_out_of_range_rejected(db_session: AsyncSession) -> None:
    """服務層防線：即使繞過 schema 邊界，commission_pct 超出 0-100 仍被擋下。"""
    store_id, clerk_id, contact_id = await _seed(db_session)
    bad_item = AcquisitionItemIn.model_construct(
        name="壞抽成",
        grade=Grade.A,
        listed_price=Decimal("100"),
        acquisition_cost=None,
        brand_id=None,
        product_model_id=None,
        commission_pct=200,
    )
    data = AcquisitionCreate.model_construct(
        type=AcquisitionType.CONSIGNMENT,
        contact_id=contact_id,
        note=None,
        items=[bad_item],
        lot=None,
    )
    with pytest.raises(InvalidCommissionPct):
        await AcquisitionService(db_session).create_acquisition(
            store_id, clerk_id, data, idempotency_key=f"svc-{next(_svc_idem)}"
        )


async def test_acquisition_writes_audit_without_pii(db_session: AsyncSession) -> None:
    """收購寫 CREATE_ACQUISITION 稽核，含彙總可溯源，但不得有 national_id 明文。"""
    store_id, clerk_id, contact_id = await _seed(db_session)
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, _buyout(contact_id), idempotency_key=f"svc-{next(_svc_idem)}"
    )

    rows = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "CREATE_ACQUISITION", AuditLog.store_id == store_id
            )
        )
    )
    assert len(rows) == 1
    entry = rows[0]
    assert entry.actor_user_id == clerk_id
    assert entry.entity_type == "acquisition"
    assert entry.entity_id == str(result.acquisition_id)
    assert entry.after is not None
    assert entry.after["type"] == "BUYOUT"
    assert entry.after["contact_id"] == contact_id
    assert entry.after["item_count"] == 2
    assert entry.after["total_cash_paid"] == "2500"
    # 只記 contact_id 參照，不含 national_id（明文或鍵）。
    assert "national_id" not in entry.after
    assert "national_id" not in str(entry.before)


async def test_consignment_audit_records_no_cash(db_session: AsyncSession) -> None:
    store_id, clerk_id, contact_id = await _seed(db_session)
    data = AcquisitionCreate(
        type=AcquisitionType.CONSIGNMENT,
        contact_id=contact_id,
        items=[
            AcquisitionItemIn(
                name="寄售品", grade=Grade.A, listed_price=Decimal("2000"), commission_pct=50
            )
        ],
    )
    result = await AcquisitionService(db_session).create_acquisition(
        store_id, clerk_id, data, idempotency_key=f"svc-{next(_svc_idem)}"
    )
    entry = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "CREATE_ACQUISITION",
            AuditLog.entity_id == str(result.acquisition_id),
        )
    )
    assert entry is not None and entry.after is not None
    assert entry.after["type"] == "CONSIGNMENT"
    assert entry.after["total_cash_paid"] is None


# ── schema 邊界驗證（不需 DB）──
def test_item_rejects_grade_e() -> None:
    with pytest.raises(ValidationError):
        AcquisitionItemIn(name="x", grade=Grade.E, listed_price=Decimal("100"))


def test_item_rejects_negative_money() -> None:
    with pytest.raises(ValidationError):
        AcquisitionItemIn(name="x", grade=Grade.A, listed_price=Decimal("-1"))


def test_item_rejects_fractional_money() -> None:
    with pytest.raises(ValidationError):
        AcquisitionItemIn(name="x", grade=Grade.A, listed_price=Decimal("10.5"))


def test_buyout_requires_items() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(type=AcquisitionType.BUYOUT, contact_id=1, items=[])


def test_buyout_item_requires_cost() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(
            type=AcquisitionType.BUYOUT,
            contact_id=1,
            items=[AcquisitionItemIn(name="x", grade=Grade.A, listed_price=Decimal("100"))],
        )


def test_buyout_item_rejects_commission() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(
            type=AcquisitionType.BUYOUT,
            contact_id=1,
            items=[
                AcquisitionItemIn(
                    name="x",
                    grade=Grade.A,
                    listed_price=Decimal("100"),
                    acquisition_cost=Decimal("50"),
                    commission_pct=30,
                )
            ],
        )


def test_consignment_item_requires_commission() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(
            type=AcquisitionType.CONSIGNMENT,
            contact_id=1,
            items=[AcquisitionItemIn(name="x", grade=Grade.A, listed_price=Decimal("100"))],
        )


def test_bulk_lot_requires_lot() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(type=AcquisitionType.BULK_LOT, contact_id=1, lot=None)


def test_lot_rejects_nonpositive_qty() -> None:
    with pytest.raises(ValidationError):
        AcquisitionLotIn(
            name="堆",
            acquisition_cost=Decimal("100"),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            total_qty=0,
            unit_price=Decimal("10"),
        )
