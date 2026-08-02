"""贈品與臨時折扣的資料模型守衛（P1）。

這一層只驗「資料庫層擋得住說不通的資料」。定價邏輯與接線在 P2，不在此。

設計要點（見計畫 §3）：
- `line_type` 是**品項種類**（序號／一般／散裝／餐飲），`line_kind` 才是**商業性質**
  （一般銷售／贈品）。兩者正交，不可混為一談。
- 贈品原價價值走 `original_unit_price × qty`，**絕不寫進 `discount_amount`**——
  後者是活動折扣的欄位，活動報表直接讀它，混入贈品會污染報表。
- `net_amount` 才是「本行實付」；`line_total` 維持既有語意（活動折後 = unit_price × qty）。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import SaleLine
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineKind, SaleLineType, StockReason, UserRole


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    store = Store(name="贈品測試店", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"gd-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(clerk)
    session.add(StoreSettings(store_id=store.id))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    product = CatalogProduct(
        store_id=store.id,
        sku=f"GD-{store.id}",
        name="測試商品",
        unit_price=Decimal("500"),
        quantity_on_hand=50,
    )
    session.add(product)
    await session.flush()
    return store.id, clerk.id, product.id


async def _a_sale_line(session: AsyncSession) -> SaleLine:
    """建一筆最小可行的銷售並回傳其唯一明細行（供後續改欄位測 CHECK）。"""
    store_id, clerk_id, product_id = await _seed(session)
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=2)],
    )
    line = await session.scalar(select(SaleLine).where(SaleLine.sale_id == sale.id))
    assert line is not None
    return line


# ── 列舉 ────────────────────────────────────────────────────────────────────


def test_sale_line_kind_separates_commercial_nature_from_item_kind() -> None:
    """line_kind 只表達商業性質；品項種類仍在 line_type，兩者不得合併。"""
    assert {k.value for k in SaleLineKind} == {"NORMAL", "GIFT"}
    # line_type 不得混入 GIFT——否則「贈送一件序號品」就無法同時表達兩件事。
    assert "GIFT" not in {t.value for t in SaleLineType}


def test_stock_reason_has_gift_movements() -> None:
    """贈品出庫／退回必須能與一般銷售、一般退貨分辨（報表要能統計贈品數量）。"""
    values = {r.value for r in StockReason}
    assert "GIFT" in values
    assert "GIFT_RETURN" in values


# ── sale_lines 的 CHECK ─────────────────────────────────────────────────────


async def test_new_sale_lines_default_to_normal_kind(db_session: AsyncSession) -> None:
    """既有結帳路徑不帶 line_kind 時，必須是一般銷售（相容舊行為）。"""
    line = await _a_sale_line(db_session)
    assert line.line_kind is SaleLineKind.NORMAL
    assert line.manual_discount_amount == Decimal(0)
    assert line.net_amount == line.line_total  # 無臨時折扣 → 實付＝活動折後


async def test_gift_line_must_be_free(db_session: AsyncSession) -> None:
    """贈品行的實付必須是 0——這是贈品的定義，不能靠應用層自律。"""
    line = await _a_sale_line(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE sale_lines SET line_kind='GIFT', original_unit_price=500,"
                " gift_reason_id=NULL WHERE id=:i"
            ).bindparams(i=line.id)
        )
    await db_session.rollback()


async def test_gift_value_must_not_be_written_as_discount(db_session: AsyncSession) -> None:
    """贈品原價**不得**混入 discount_amount。

    discount_amount 是活動折扣專用欄位，活動報表直接 SUM 它；贈品若寫進去，
    報表會把「送出去的東西」算成「打折」，兩個數字都會錯。
    """
    line = await _a_sale_line(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE sale_lines SET line_kind='GIFT', unit_price=0, line_total=0,"
                " net_amount=0, original_unit_price=500, discount_amount=1000 WHERE id=:i"
            ).bindparams(i=line.id)
        )
    await db_session.rollback()


async def test_normal_line_net_amount_must_match_its_parts(db_session: AsyncSession) -> None:
    """一般行的實付必須等於「活動折後金額 − 臨時折扣」，不可各說各話。"""
    line = await _a_sale_line(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE sale_lines SET manual_discount_amount=100, net_amount=line_total"
                " WHERE id=:i"
            ).bindparams(i=line.id)
        )
    await db_session.rollback()


async def test_net_amount_can_never_go_negative(db_session: AsyncSession) -> None:
    """折扣不得把一行折成負數（否則等於倒貼給客人）。"""
    line = await _a_sale_line(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE sale_lines SET manual_discount_amount=line_total + 1,"
                " net_amount=-1 WHERE id=:i"
            ).bindparams(i=line.id)
        )
    await db_session.rollback()


async def test_gift_line_requires_a_reason(db_session: AsyncSession) -> None:
    """送東西一定要說明為什麼——事後稽核唯一能追的就是這個。"""
    line = await _a_sale_line(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE sale_lines SET line_kind='GIFT', unit_price=0, line_total=0,"
                " net_amount=0, discount_amount=0, original_unit_price=500,"
                " gift_reason_id=NULL WHERE id=:i"
            ).bindparams(i=line.id)
        )
    await db_session.rollback()


# ── 成本快照 ────────────────────────────────────────────────────────────────


async def test_catalog_product_can_record_a_cost(db_session: AsyncSession) -> None:
    """一般商品先前完全沒有成本欄位，導致其營收只能歸入『成本未知』。"""
    store_id, _clerk_id, product_id = await _seed(db_session)
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    product.unit_cost = Decimal("300")
    await db_session.flush()
    refreshed = await db_session.get(CatalogProduct, product_id)
    assert refreshed is not None and refreshed.unit_cost == Decimal("300")
    assert store_id


async def test_sale_line_carries_a_cost_snapshot(db_session: AsyncSession) -> None:
    """成本必須在成交當下凍結——否則日後調整商品成本會回頭改寫歷史毛利。"""
    store_id, clerk_id, product_id = await _seed(db_session)
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    product.unit_cost = Decimal("300")
    await db_session.flush()

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product_id, qty=2)],
    )
    line = await db_session.scalar(select(SaleLine).where(SaleLine.sale_id == sale.id))
    assert line is not None
    assert line.cost_snapshot == Decimal("600")  # 300 × 2，成交當下

    # 事後調整商品成本，歷史明細不得跟著變
    product.unit_cost = Decimal("450")
    await db_session.flush()
    await db_session.refresh(line)
    assert line.cost_snapshot == Decimal("600")
