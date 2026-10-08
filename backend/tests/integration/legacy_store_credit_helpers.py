"""模擬「整筆開＋購物金折讓」時期開出的發票（ADR-029；2026-10-08 起新單一律扣掉購物金後開）。

舊模式的發票仍要能作廢、退貨與補送折讓，所以測試用這支把剛結帳的發票改回舊模式當時的樣子：
整筆金額、`store_credit_mode=ALLOWANCE`。只能在發票送出（PENDING）前呼叫。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import split_tax_inclusive
from app.modules.einvoice.models import Invoice
from app.modules.sales.models import Sale
from app.shared.enums import InvoiceStatus, StoreCreditInvoiceMode


async def mark_legacy_allowance_invoice(session: AsyncSession, sale_id: int) -> Invoice:
    """把這筆銷售的待開發票改成舊模式：整筆開、開立後自動對購物金折讓。"""
    sale = await session.get(Sale, sale_id)
    invoice = await session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert sale is not None and invoice is not None
    assert invoice.status is InvoiceStatus.PENDING, "只能改還沒送出的發票"
    net, tax = split_tax_inclusive(sale.total, invoice.tax_rate)
    invoice.net, invoice.tax, invoice.total = Decimal(net), Decimal(tax), Decimal(net + tax)
    invoice.store_credit_mode = StoreCreditInvoiceMode.ALLOWANCE
    await session.flush()
    return invoice
