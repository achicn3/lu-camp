"""Phase 4 / Slice 4A — 寄售付款閉環（pay settlement）+ 現金對帳。

賣寄售品給客人時全額現金入帳（SALE_IN=gross）；事後付款給寄售人 = payout（gross−抽成）
出帳（CONSIGNMENT_PAYOUT_OUT），settlement PENDING→PAID。對帳重點：付款後抽屜淨增 = 抽成。
原子性/併發另見 test_consignment_payout_concurrency.py。
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.money import commission
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.models import Contact
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    UserRole,
)
from app.shared.exceptions import NoOpenCashSession, SettlementNotFound, SettlementNotPending

_PRICE = Decimal("1800")
_PCT = 40
_COMMISSION = Decimal(commission(_PRICE, _PCT))  # 720
_PAYOUT = _PRICE - _COMMISSION  # 1080
_OPENING = Decimal("1000")


async def _seed_consignment_sale(
    session: AsyncSession, *, open_drawer: bool = True
) -> tuple[int, int, ConsignmentSettlement]:
    """建 store+clerk+開帳 → 寄售序號品 → 現金售出 → 回 (store_id, clerk_id, PENDING 結算)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    if open_drawer:
        await CashDrawerService(session).open_session(store.id, clerk.id, _OPENING)
    consignor = Contact(store_id=store.id, name="寄售人", national_id_enc="enc")
    session.add(consignor)
    await session.flush()
    await InventoryService(session).create_serialized_item(
        store.id,
        item_code="C1",
        name="寄售帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        listed_price=_PRICE,
        consignor_id=consignor.id,
        commission_pct=_PCT,
    )
    sale = await SalesService(session).create_sale(
        store.id,
        clerk.id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="C1")],
    )
    settlement = await session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale.id)
    )
    assert settlement is not None
    assert settlement.status == ConsignmentSettlementStatus.PENDING
    assert settlement.payout_amount == _PAYOUT
    return store.id, clerk.id, settlement


async def test_pay_marks_paid_and_records_cash_out_and_audit(db_session: AsyncSession) -> None:
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)

    paid = await ConsignmentService(db_session).pay_settlement(
        store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="svc-pay-audit"
    )

    assert paid.status == ConsignmentSettlementStatus.PAID
    assert paid.paid_by == clerk_id
    assert paid.paid_at is not None

    # 一筆 CONSIGNMENT_PAYOUT_OUT，金額 = payout，且 ref 指回 settlement。
    movement = await db_session.scalar(
        select(CashMovement).where(
            CashMovement.store_id == store_id,
            CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
        )
    )
    assert movement is not None
    assert movement.amount == _PAYOUT
    assert movement.ref_type == "consignment_settlement"
    assert movement.ref_id == settlement.id

    # 稽核留痕（誰/對象；無 PII）。
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "CONSIGNMENT_PAYOUT",
            AuditLog.entity_type == "consignment_settlement",
            AuditLog.entity_id == str(settlement.id),
        )
    )
    assert audit is not None
    assert audit.actor_user_id == clerk_id


async def test_pay_reconciliation_net_drawer_is_commission(db_session: AsyncSession) -> None:
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)
    drawer = CashDrawerService(db_session)
    cs = await drawer.get_current_session(store_id)
    assert cs is not None

    before = await drawer.expected_amount(cs)
    assert before == _OPENING + _PRICE  # 開帳 + 全額現金入帳（SALE_IN）

    await ConsignmentService(db_session).pay_settlement(
        store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="svc-pay-reconcile"
    )

    after = await drawer.expected_amount(cs)
    assert after == before - _PAYOUT  # 付款後預期現金扣掉應付寄售人
    # 這筆寄售交易對抽屜的淨貢獻 = 抽成（店家真正收入），不是全額售價。
    assert after - _OPENING == _COMMISSION


async def test_pay_already_paid_raises_not_pending(db_session: AsyncSession) -> None:
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)
    svc = ConsignmentService(db_session)
    await svc.pay_settlement(
        store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="svc-pay-once"
    )

    with pytest.raises(SettlementNotPending):
        await svc.pay_settlement(
            store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="svc-pay-again"
        )

    # 不得重複出帳：CONSIGNMENT_PAYOUT_OUT 仍只有一筆。
    count = await db_session.scalar(
        select(func.count())
        .select_from(CashMovement)
        .where(
            CashMovement.store_id == store_id,
            CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
        )
    )
    assert count == 1


async def test_pay_without_open_session_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)
    drawer = CashDrawerService(db_session)
    cs = await drawer.get_current_session(store_id)
    assert cs is not None
    await drawer.close_session(cs, await drawer.expected_amount(cs), clerk_id)

    with pytest.raises(NoOpenCashSession):
        await ConsignmentService(db_session).pay_settlement(
            store_id,
            settlement.id,
            actor_user_id=clerk_id,
            idempotency_key="svc-pay-no-drawer",
        )

    # 付款被擋 → settlement 仍 PENDING。
    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.PENDING


async def test_pay_cross_store_not_found(db_session: AsyncSession) -> None:
    _store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)

    with pytest.raises(SettlementNotFound):
        await ConsignmentService(db_session).pay_settlement(
            999999,
            settlement.id,
            actor_user_id=clerk_id,
            idempotency_key="svc-pay-cross-store",
        )


async def _payout_count(session: AsyncSession, store_id: int) -> int:
    n = await session.scalar(
        select(func.count())
        .select_from(CashMovement)
        .where(
            CashMovement.store_id == store_id,
            CashMovement.type == CashMovementType.CONSIGNMENT_PAYOUT_OUT,
        )
    )
    return n or 0


async def test_void_before_pay_cancels_settlement_and_blocks_payout(
    db_session: AsyncSession,
) -> None:
    """作廢寄售銷售 → 結算 CANCELLED → 之後付款被擋（不付給已作廢銷售的寄售人，無出帳）。"""
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)
    sale = await db_session.get(Sale, settlement.sale_id)
    assert sale is not None
    await SalesService(db_session).void_sale(sale, clerk_id)

    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.CANCELLED

    with pytest.raises(SettlementNotPending):
        await ConsignmentService(db_session).pay_settlement(
            store_id,
            settlement.id,
            actor_user_id=clerk_id,
            idempotency_key="svc-pay-after-void",
        )
    assert await _payout_count(db_session, store_id) == 0


async def test_void_after_pay_flags_reclaim_not_double_reverse(db_session: AsyncSession) -> None:
    """已付款後作廢：結算維持 PAID 但標 reclaim_needed（須向寄售人追回）；不再出帳。"""
    store_id, clerk_id, settlement = await _seed_consignment_sale(db_session)
    await ConsignmentService(db_session).pay_settlement(
        store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="svc-pay-before-void"
    )
    sale = await db_session.get(Sale, settlement.sale_id)
    assert sale is not None
    await SalesService(db_session).void_sale(sale, clerk_id)

    await db_session.refresh(settlement)
    assert settlement.status == ConsignmentSettlementStatus.PAID
    assert settlement.reclaim_needed is True
    assert await _payout_count(db_session, store_id) == 1
