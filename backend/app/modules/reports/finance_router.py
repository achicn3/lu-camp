"""Phase 6 財務報表路由（MANAGER；docs/19）：每日現金對帳等。

所有報表唯讀、store 範圍（由 token 的 store_id 限定）；金額整數元字串。
?format=csv|xlsx 走 export_response，與 JSON 同源（同一 service 取數，匯出只做呈現轉換）。
日界一律 UTC（與其餘報表的 date_trunc 一致；單店 dev 簡化，見 service.daily_cash）。
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.reports.export import ExportFormat, TabularExport, export_response
from app.modules.reports.schemas import DailyCashReport, SalesMarginReport
from app.modules.reports.service import ReportsService

router = APIRouter(prefix="/reports", tags=["reports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.get("/daily-cash", response_model=DailyCashReport, operation_id="dailyCashReport")
async def daily_cash(
    session: SessionDep,
    user: ManagerDep,
    report_date: Annotated[date, Query(alias="date")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> DailyCashReport | Response:
    """每日現金對帳（docs/19 §2.2）：依 session 分列 + 當日合計；expected 與關帳同公式。"""
    report = await ReportsService(session).daily_cash(user.store_id, report_date)
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("日期", report.date.isoformat()),
        ("合計開帳零用金", str(report.total_opening_float)),
        ("合計現金銷售", str(report.total_cash_sales)),
        ("合計作廢收購退現", str(report.total_acquisition_void_in)),
        ("合計收購付現", str(report.total_buyout_out)),
        ("合計寄售付款", str(report.total_consignment_payout_out)),
        ("合計退貨退現", str(report.total_sale_refund_out)),
        ("合計人工調整", str(report.total_manual_adjust)),
        ("合計應有現金", str(report.total_expected)),
        ("合計實點現金", str(report.total_counted)),
        ("合計差異", str(report.total_variance)),
        ("當日購物金兌付(只展示)", str(report.total_store_credit_redeemed_display_only)),
    ]
    exp = TabularExport(
        sheet="每日現金對帳",
        filename_stem=f"daily-cash-{report.store_id}-{report.date.isoformat()}",
        meta=meta,
        headers=[
            "班別ID",
            "狀態",
            "開帳時間",
            "關帳時間",
            "開帳人",
            "關帳人",
            "開帳零用金",
            "現金銷售",
            "作廢收購退現",
            "收購付現",
            "寄售付款",
            "退貨退現",
            "人工調整",
            "應有現金",
            "實點現金",
            "差異",
        ],
        rows=[
            [
                str(r.session_id),
                r.status,
                r.opened_at.isoformat(),
                r.closed_at.isoformat() if r.closed_at else "",
                str(r.opened_by),
                str(r.closed_by) if r.closed_by is not None else "",
                str(r.opening_float),
                str(r.cash_sales),
                str(r.acquisition_void_in),
                str(r.buyout_out),
                str(r.consignment_payout_out),
                str(r.sale_refund_out),
                str(r.manual_adjust_total),
                str(r.expected_amount),
                str(r.counted_amount) if r.counted_amount is not None else "",
                str(r.variance) if r.variance is not None else "",
            ]
            for r in report.sessions
        ],
    )
    return export_response(exp, fmt)


@router.get("/sales-margin", response_model=SalesMarginReport, operation_id="salesMarginReport")
async def sales_margin(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> SalesMarginReport | Response:
    """銷售 / 毛利（docs/19 §2.3）。半開區間 [from, to)；to<=from → 422。"""
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    report = await ReportsService(session).sales_margin(
        user.store_id, date_from=date_from, date_to=date_to
    )
    if fmt == "json":
        return report
    rate = "N/A" if report.gross_margin_rate is None else str(report.gross_margin_rate)
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("起", report.date_from.isoformat()),
        ("迄", report.date_to.isoformat()),
    ]
    exp = TabularExport(
        sheet="銷售毛利",
        filename_stem=f"sales-margin-{report.store_id}",
        meta=meta,
        headers=["指標", "值"],
        rows=[
            ["營業額", str(report.gross_turnover)],
            ["認列營收", str(report.recognized_revenue)],
            ["自有序號成本", str(report.owned_cogs)],
            ["自有散裝成本", str(report.bulk_cogs)],
            ["寄售抽成收入", str(report.consignment_commission_income)],
            ["毛利", str(report.gross_margin)],
            ["毛利率", rate],
            ["成本未知營收", str(report.unknown_cost_sales)],
            ["現金收款", str(report.cash_received)],
            ["購物金收款", str(report.store_credit_redeemed)],
            ["交易筆數", str(report.transaction_count)],
        ],
    )
    return export_response(exp, fmt)
