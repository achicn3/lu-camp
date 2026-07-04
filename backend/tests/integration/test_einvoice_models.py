"""einvoice 模型 DB 不變量測試：核心約束在持久層擋下（繞過 service 的直插亦然）。

守護：一筆銷售至多一張發票、net+tax=total、total>0、捐贈↔捐贈碼、買方統編↔發票類型、
佇列目標 XOR。以 IntegrityError 斷言。
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceType,
    UploadStatus,
    UserRole,
)


async def _seed_sale(session: AsyncSession) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    sale = Sale(
        store_id=store.id,
        clerk_user_id=clerk.id,
        subtotal=Decimal(1000),
        tax=Decimal(50),
        total=Decimal(1050),
    )
    session.add(sale)
    await session.flush()
    return store.id, sale.id


def _valid_invoice(store_id: int, sale_id: int, **overrides: object) -> Invoice:
    kwargs: dict[str, object] = {
        "store_id": store_id,
        "sale_id": sale_id,
        "invoice_type": InvoiceType.B2C,
        "net": Decimal(1000),
        "tax": Decimal(50),
        "total": Decimal(1050),
    }
    kwargs.update(overrides)
    return Invoice(**kwargs)


async def test_one_invoice_per_sale(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(_valid_invoice(store_id, sale_id))
    await db_session.flush()
    db_session.add(_valid_invoice(store_id, sale_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_net_tax_must_equal_total(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(_valid_invoice(store_id, sale_id, total=Decimal(999)))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_total_must_be_positive(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(
        _valid_invoice(store_id, sale_id, net=Decimal(0), tax=Decimal(0), total=Decimal(0))
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_donate_requires_npoban(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(_valid_invoice(store_id, sale_id, donate_mark=True, npoban=None))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_b2b_requires_buyer_tax_id(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(
        _valid_invoice(store_id, sale_id, invoice_type=InvoiceType.B2B, buyer_tax_id=None)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_b2c_forbids_buyer_tax_id(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    db_session.add(
        _valid_invoice(store_id, sale_id, invoice_type=InvoiceType.B2C, buyer_tax_id="12345678")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_queue_target_xor_rejects_no_target(db_session: AsyncSession) -> None:
    store_id, _sale_id = await _seed_sale(db_session)
    db_session.add(
        EInvoiceUploadQueue(
            store_id=store_id,
            action=EInvoiceAction.ISSUE,
            message_type=EInvoiceMessageType.F0401,
            invoice_id=None,
            allowance_id=None,
            status=UploadStatus.PENDING,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
