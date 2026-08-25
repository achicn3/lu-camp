"""A succeeded provider refund is automatically completed locally without refunding twice."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.customerdisplay.background_service import CustomerDisplayBackgroundService
from app.modules.inventory.models import SerializedItem
from app.modules.returns.models import CustomerReturn, ReturnLine, ReturnTender
from app.modules.returns.service import ReturnLineInput, ReturnsService, _refund_identity
from app.modules.sales.models import (
    LinePayRefundAttempt,
    LinePayTransaction,
    Sale,
    SaleLine,
    SaleTender,
)
from app.modules.signing.models import SignatureTask
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    Grade,
    LinePayRefundStatus,
    LinePayStatus,
    OwnershipType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    SerializedItemStatus,
    SignatureTaskKind,
    SignatureTaskStatus,
    TenderType,
    UserRole,
)
from app.shared.exceptions import ReturnConflict, SignatureTaskNotPending


async def test_succeeded_refund_is_recovered_locally_without_provider_call() -> None:
    factory = get_sessionmaker()
    try:
        async with factory() as session:
            store = Store(name="退款復原門市")
            session.add(store)
            await session.flush()
            manager = User(
                store_id=store.id,
                username="refund-recovery-manager",
                password_hash="h",
                role=UserRole.MANAGER,
            )
            session.add(manager)
            await session.flush()
            item = SerializedItem(
                store_id=store.id,
                item_code="REFUND-RECOVERY-1",
                name="退款復原商品",
                grade=Grade.A,
                ownership_type=OwnershipType.OWNED,
                acquisition_cost=Decimal("60"),
                listed_price=Decimal("100"),
                status=SerializedItemStatus.SOLD,
            )
            returned_item = SerializedItem(
                store_id=store.id,
                item_code="REFUND-RECOVERY-2",
                name="先完成本地退貨的商品",
                grade=Grade.A,
                ownership_type=OwnershipType.OWNED,
                acquisition_cost=Decimal("60"),
                listed_price=Decimal("100"),
                status=SerializedItemStatus.IN_STOCK,
            )
            session.add_all([item, returned_item])
            await session.flush()
            sale = Sale(
                store_id=store.id,
                clerk_user_id=manager.id,
                subtotal=Decimal("190"),
                tax=Decimal("10"),
                total=Decimal("200"),
                awarded_points=0,
                payment_method=PaymentMethod.LINE_PAY,
                invoice_status=SaleInvoiceStatus.NOT_ISSUED,
                status=SaleStatus.COMPLETED,
            )
            session.add(sale)
            await session.flush()
            line = SaleLine(
                store_id=store.id,
                sale_id=sale.id,
                line_type=SaleLineType.SERIALIZED,
                serialized_item_id=item.id,
                description=item.name,
                qty=1,
                unit_price=Decimal("100"),
                line_total=Decimal("100"),
                discount_amount=Decimal(0),
                manual_discount_amount=Decimal(0),
                net_amount=Decimal("100"),
                cost_snapshot=Decimal("60"),
            )
            session.add(line)
            await session.flush()
            returned_line = SaleLine(
                store_id=store.id,
                sale_id=sale.id,
                line_type=SaleLineType.SERIALIZED,
                serialized_item_id=returned_item.id,
                description=returned_item.name,
                qty=1,
                unit_price=Decimal("100"),
                line_total=Decimal("100"),
                discount_amount=Decimal(0),
                manual_discount_amount=Decimal(0),
                net_amount=Decimal("100"),
                cost_snapshot=Decimal("60"),
            )
            session.add(returned_line)
            await session.flush()
            session.add_all(
                [
                    SaleTender(
                        store_id=store.id,
                        sale_id=sale.id,
                        tender_type=TenderType.LINE_PAY,
                        amount=Decimal("200"),
                    ),
                    LinePayTransaction(
                        store_id=store.id,
                        sale_id=sale.id,
                        order_id="refund-recovery-order",
                        transaction_id="123456789",
                        status=LinePayStatus.COMPLETE,
                        amount=Decimal("200"),
                        refunded_amount=Decimal("100"),
                        raw_response={},
                    ),
                ]
            )
            prior_return = CustomerReturn(
                store_id=store.id,
                sale_id=sale.id,
                refund_amount=Decimal("100"),
                reason="另一件已先完成本地退貨",
                clerk_user_id=manager.id,
                idempotency_key="already-local-return",
                idempotency_fingerprint="f" * 64,
            )
            session.add(prior_return)
            await session.flush()
            session.add_all(
                [
                    ReturnLine(
                        store_id=store.id,
                        return_id=prior_return.id,
                        sale_line_id=returned_line.id,
                        qty=1,
                        refund_amount=Decimal("100"),
                    ),
                    ReturnTender(
                        store_id=store.id,
                        return_id=prior_return.id,
                        tender_type=TenderType.LINE_PAY,
                        amount=Decimal("100"),
                    ),
                    LinePayRefundAttempt(
                        store_id=store.id,
                        refund_key=f"s{store.id}:return:already-local",
                        order_id="refund-recovery-order",
                        amount=Decimal("100"),
                        status=LinePayRefundStatus.SUCCEEDED,
                        return_code="0000",
                        recovery_kind="RETURN",
                        recovery_payload={"sale_id": sale.id},
                        recovered_at=datetime.now(UTC),
                    ),
                ]
            )
            await session.commit()

            reason = "平台已退、本地待復原"
            requested = {line.id: 1}
            refund_key = f"s{store.id}:return:{_refund_identity(sale.id, requested, reason, {})}"
            session.add(
                LinePayRefundAttempt(
                    store_id=store.id,
                    refund_key=refund_key,
                    order_id="refund-recovery-order",
                    amount=Decimal("100"),
                    status=LinePayRefundStatus.SUCCEEDED,
                    return_code="0000",
                    recovery_kind="RETURN",
                    recovery_payload={
                        "sale_id": sale.id,
                        "lines": [{"sale_line_id": line.id, "qty": 1}],
                        "reason": reason,
                        "actor_user_id": manager.id,
                        "idempotency_key": "refund-recovery-return",
                        "taiwan_pay_refund_confirmed": False,
                        "invoice_recalled": False,
                        "consent_signature_task_id": None,
                        "unreturned_gift_note": None,
                        "manual_paper_disposed": False,
                    },
                )
            )
            await session.commit()
            store_id = store.id

        async with factory() as session:
            with pytest.raises(ReturnConflict, match="本地復原"):
                await ReturnsService(session).create_return(
                    store_id,
                    sale_id=sale.id,
                    lines=[ReturnLineInput(sale_line_id=line.id, qty=1)],
                    reason="平台已退、本地待復原",
                    actor_user_id=manager.id,
                    idempotency_key="operator-retry-before-recovery",
                )
            await session.rollback()

        (
            scanned,
            recovered,
        ) = await CustomerDisplayBackgroundService.recover_succeeded_linepay_refunds_once()
        assert scanned >= 1
        assert recovered == 1

        async with factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(CustomerReturn)
                    .where(CustomerReturn.store_id == store_id)
                )
                == 2
            )
            txn = await session.scalar(
                select(LinePayTransaction).where(LinePayTransaction.store_id == store_id)
            )
            assert txn is not None
            assert txn.refunded_amount == Decimal("200")
            assert txn.status is LinePayStatus.REFUNDED
            attempt = await session.scalar(
                select(LinePayRefundAttempt).where(LinePayRefundAttempt.refund_key == refund_key)
            )
            assert attempt is not None
            assert attempt.recovered_at is not None
            assert attempt.recovery_error is None
            restored_item = await session.scalar(
                select(SerializedItem).where(SerializedItem.item_code == "REFUND-RECOVERY-1")
            )
            assert restored_item is not None
            assert restored_item.status is SerializedItemStatus.IN_STOCK
    finally:
        # Real-commit test state lives outside the usual savepoint fixture; the database is
        # process-isolated by tests/db_safety.py, so whole-store cleanup cannot touch real data.
        async with factory() as session:
            await session.execute(text("TRUNCATE stores CASCADE"))
            await session.commit()


async def test_provider_succeeded_recovery_can_consume_exact_expired_return_consent(
    db_session: AsyncSession,
) -> None:
    store = Store(name="逾期同意復原門市")
    db_session.add(store)
    await db_session.flush()
    manager = User(
        store_id=store.id,
        username="expired-refund-consent-manager",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    db_session.add(manager)
    await db_session.flush()
    sale = Sale(
        store_id=store.id,
        clerk_user_id=manager.id,
        subtotal=Decimal(0),
        tax=Decimal(0),
        total=Decimal(0),
        awarded_points=0,
        payment_method=PaymentMethod.CASH,
        invoice_status=SaleInvoiceStatus.NOT_ISSUED,
        status=SaleStatus.COMPLETED,
    )
    db_session.add(sale)
    await db_session.flush()
    task = SignatureTask(
        store_id=store.id,
        kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
        status=SignatureTaskStatus.EXPIRED,
        content={
            "return_lines": [{"sale_line_id": 123, "qty": 1}],
            "invoice_id": 456,
            "invoice_action": "ALLOWANCE",
            "refund_total": "100",
        },
        content_sha256="c" * 64,
        signature_sha256="s" * 64,
        evidence_hash="e" * 64,
        signed_at=datetime.now(UTC) - timedelta(minutes=6),
        expired_at=datetime.now(UTC),
        ref_type="sale",
        ref_id=sale.id,
        created_by=manager.id,
    )
    db_session.add(task)
    await db_session.flush()

    with pytest.raises(SignatureTaskNotPending):
        await SigningService(db_session).consume_return_consent(
            store.id,
            task.id,
            sale_id=sale.id,
            return_lines={123: 1},
            invoice_id=456,
            invoice_action="ALLOWANCE",
            refund_total=Decimal("100"),
        )

    recovered = await SigningService(db_session).consume_return_consent(
        store.id,
        task.id,
        sale_id=sale.id,
        return_lines={123: 1},
        invoice_id=456,
        invoice_action="ALLOWANCE",
        refund_total=Decimal("100"),
        allow_expired_provider_recovery=True,
    )

    assert recovered.status is SignatureTaskStatus.CONSUMED
    assert recovered.expired_at is not None
