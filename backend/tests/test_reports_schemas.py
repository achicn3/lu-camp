"""報表輸出 schema 與下載檔的金額格式回歸測試。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.modules.reports import finance_router
from app.modules.reports.schemas import DailyCashReport, DailyCashSessionRow
from app.modules.reports.service import ReportsService


def _cash_row() -> DailyCashSessionRow:
    return DailyCashSessionRow(
        session_id=1,
        status="OPEN",
        opened_at=datetime.now(UTC),
        closed_at=None,
        opened_by=1,
        closed_by=None,
        opening_float=Decimal("1E+5"),
        cash_sales=Decimal("2E+4"),
        acquisition_void_in=Decimal("0E+2"),
        buyout_out=Decimal("3E+3"),
        consignment_payout_out=Decimal("0E+2"),
        sale_refund_out=Decimal("0E+2"),
        manual_adjust_total=Decimal("-5E+2"),
        expected_amount=Decimal("116500"),
        counted_amount=None,
        variance=None,
    )


def test_daily_cash_amounts_serialize_without_scientific_notation() -> None:
    payload = _cash_row().model_dump(mode="json")

    assert payload["opening_float"] == "100000"
    assert payload["cash_sales"] == "20000"
    assert payload["manual_adjust_total"] == "-500"
    amount_fields = {
        "opening_float",
        "cash_sales",
        "acquisition_void_in",
        "buyout_out",
        "consignment_payout_out",
        "sale_refund_out",
        "manual_adjust_total",
        "expected_amount",
        "counted_amount",
        "variance",
    }
    assert all(
        payload[field] is None or "e" not in str(payload[field]).lower() for field in amount_fields
    )


@pytest.mark.asyncio
async def test_daily_cash_csv_export_avoids_scientific_notation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _cash_row()
    report = DailyCashReport(
        generated_at=datetime.now(UTC),
        store_id=1,
        date=date(2026, 7, 20),
        sessions=[row],
        total_opening_float=Decimal("1E+5"),
        total_cash_sales=Decimal("2E+4"),
        total_acquisition_void_in=Decimal("0E+2"),
        total_buyout_out=Decimal("3E+3"),
        total_consignment_payout_out=Decimal("0E+2"),
        total_sale_refund_out=Decimal("0E+2"),
        total_manual_adjust=Decimal("-5E+2"),
        total_expected=Decimal("116500"),
        total_counted=Decimal("0E+2"),
        total_variance=Decimal("0E+2"),
        total_store_credit_redeemed_display_only=Decimal("0E+2"),
    )

    async def fake_daily_cash(
        _service: ReportsService,
        _store_id: int,
        _report_date: date,
    ) -> DailyCashReport:
        return report

    monkeypatch.setattr(ReportsService, "daily_cash", fake_daily_cash)
    response = await finance_router.daily_cash(
        session=cast(AsyncSession, object()),
        user=CurrentUser(id=1, role="MANAGER", store_id=1),
        report_date=report.date,
        fmt="csv",
    )

    assert isinstance(response, Response)
    text = bytes(response.body).decode("utf-8-sig")
    assert "100000" in text
    assert "1E+5" not in text
    assert "0E+2" not in text
