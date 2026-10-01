"""平台識別碼 migration（3f2adaec554d）：回填舊編號、降版守衛。

回填必須**一字不差**等於升級前推導出的編號：已送出或已認領待重送的訊息，對帳查詢與凍結
payload 都綁著舊編號，升級後換號就再也對不上平台。舊 `allowance_number()` 已自 app 移除，
它的格式只剩 migration 內的凍結複本，原本守它的測試搬到這裡。
"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.einvoice.service import EInvoiceService
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

_INT32_MAX = 2_147_483_647


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "3f2adaec554d_einvoice_platform_ids.py"
    )
    spec = importlib.util.spec_from_file_location("einvoice_platform_ids_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_allowance_number_keeps_short_readable_format() -> None:
    assert _migration().legacy_allowance_number(1, 9) == "L1-9"


def test_legacy_allowance_number_packs_long_ids_within_16_chars_without_collision() -> None:
    legacy = _migration().legacy_allowance_number
    first = legacy(_INT32_MAX, _INT32_MAX - 1)
    second = legacy(_INT32_MAX, _INT32_MAX)

    assert first.startswith("LX") and second.startswith("LX")
    assert len(first) <= 16 and len(second) <= 16
    assert first != second


async def _invoice(session: AsyncSession) -> None:
    """建一張待開立發票（編號由 ORM default 隨機產生）。"""
    store = Store(name="平台編號降版測試店")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="mig-clk", password_hash="h", role=UserRole.CLERK)
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
    await EInvoiceService(session).create_pending_invoice(
        store.id, sale_id=sale.id, total=Decimal(1050), tax_rate=Decimal("0.05")
    )
    await session.flush()


async def test_downgrade_guard_allows_legacy_ids_only(db_session: AsyncSession) -> None:
    """全是升級前回填的舊編號（還沒開出新發票）：可以降版，守衛不能擋掉正常回滾。"""
    await _invoice(db_session)
    await db_session.execute(
        text("UPDATE invoices SET platform_order_id = 'S' || store_id || '-' || sale_id")
    )
    conn = await db_session.connection()
    await conn.run_sync(lambda c: _migration().abort_if_random_ids_exist(c))


async def test_downgrade_guard_refuses_once_a_random_id_exists(db_session: AsyncSession) -> None:
    """新發票用的是隨機編號：降版後舊程式會用推導的編號查平台，全部對不上——必須拒絕。"""
    await _invoice(db_session)
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match="拒絕降版"):
        await conn.run_sync(lambda c: _migration().abort_if_random_ids_exist(c))
