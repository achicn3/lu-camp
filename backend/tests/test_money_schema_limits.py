"""Public request schemas reject values that Numeric(12,0) cannot persist exactly."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.money import MAX_NTD
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.cashdrawer.schemas import (
    CashMovementCreateRequest,
    CashSessionCloseRequest,
    CashSessionOpenRequest,
)
from app.modules.customerdisplay.schemas import CartTenderRequest
from app.modules.einvoice.schemas import ManualInvoiceRegisterRequest
from app.modules.inventory.schemas import CatalogProductCreateRequest, PriceUpdateRequest
from app.modules.menu.schemas import MenuItemCreateRequest, MenuItemUpdateRequest
from app.modules.purchasing.schemas import (
    InputInvoiceIn,
    PurchaseOrderCreate,
    PurchaseOrderLineCreate,
)
from app.modules.sales.schemas import SaleTenderRequest
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.storecredit.schemas import StoreCreditAdjustRequest
from app.shared.enums import AcquisitionType, Grade, TenderType

TOO_LARGE = Decimal("1000000000000")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AcquisitionItemIn(name="相機", grade=Grade.A, listed_price=TOO_LARGE),
        lambda: CashSessionOpenRequest(opening_float=TOO_LARGE),
        lambda: CashSessionCloseRequest(counted_amount=TOO_LARGE),
        lambda: CashMovementCreateRequest(type="MANUAL_ADJUST", amount=-TOO_LARGE, note="盤差"),
        lambda: CartTenderRequest(tender_type=TenderType.CASH, amount=TOO_LARGE),
        lambda: PriceUpdateRequest(unit_price=TOO_LARGE),
        lambda: CatalogProductCreateRequest(name="商品", unit_price=TOO_LARGE),
        lambda: MenuItemCreateRequest(name="餐點", unit_price=TOO_LARGE),
        lambda: MenuItemUpdateRequest(unit_price=TOO_LARGE),
        lambda: PurchaseOrderLineCreate(catalog_product_id=1, qty=1, unit_cost=TOO_LARGE),
        lambda: SaleTenderRequest(tender_type=TenderType.CASH, amount=TOO_LARGE),
        lambda: StoreCreditAdjustRequest(amount=-TOO_LARGE, reason="盤差"),
    ],
)
def test_numeric_12_request_amounts_reject_thirteen_digits(factory: object) -> None:
    with pytest.raises(ValidationError):
        factory()  # type: ignore[operator]


def test_original_input_invoice_rejects_thirteen_digit_amount() -> None:
    with pytest.raises(ValidationError):
        InputInvoiceIn(
            invoice_number="AB12345678",
            invoice_date="2026-08-24",
            invoice_net=TOO_LARGE,
            invoice_tax=Decimal(0),
            invoice_total=TOO_LARGE,
        )


def test_manual_paper_invoice_rejects_thirteen_digit_total() -> None:
    with pytest.raises(ValidationError):
        ManualInvoiceRegisterRequest(
            invoice_no="AB12345678",
            invoice_date="2026-08-24",
            total=TOO_LARGE,
        )


def test_settings_tax_rate_rejects_more_than_four_decimal_places() -> None:
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(tax_rate=Decimal("0.05001"))


def test_purchase_order_line_allows_quantity_times_unit_cost_above_numeric_12() -> None:
    line = PurchaseOrderLineCreate(
        catalog_product_id=1,
        qty=2,
        unit_cost=MAX_NTD,
    )

    assert line.qty == 2
    assert line.unit_cost == MAX_NTD


def test_purchase_order_allows_aggregate_above_numeric_12_when_each_line_fits() -> None:
    order = PurchaseOrderCreate(
        supplier_id=1,
        lines=[
            PurchaseOrderLineCreate(
                catalog_product_id=1,
                qty=1,
                unit_cost=MAX_NTD,
            ),
            PurchaseOrderLineCreate(
                catalog_product_id=2,
                qty=1,
                unit_cost=Decimal(1),
            ),
        ],
    )

    assert len(order.lines) == 2


def test_buyout_total_rejects_numeric_12_overflow() -> None:
    with pytest.raises(ValidationError):
        AcquisitionCreate(
            type=AcquisitionType.BUYOUT,
            contact_id=1,
            items=[
                AcquisitionItemIn(
                    name="相機",
                    grade=Grade.A,
                    listed_price=MAX_NTD,
                    acquisition_cost=MAX_NTD,
                ),
                AcquisitionItemIn(
                    name="鏡頭",
                    grade=Grade.A,
                    listed_price=Decimal(1),
                    acquisition_cost=Decimal(1),
                ),
            ],
        )
