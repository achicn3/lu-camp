"""Phase 6 財務報表路由（MANAGER；docs/19）：每日現金對帳等。

所有報表唯讀、store 範圍（由 token 的 store_id 限定）；金額整數元字串。
?format=csv|xlsx 走 export_response，與 JSON 同源（同一 service 取數，匯出只做呈現轉換）。
日界一律 UTC（與其餘報表的 date_trunc 一致；單店 dev 簡化，見 service.daily_cash）。
"""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.reports.export import ExportFormat, TabularExport, export_response
from app.modules.reports.schemas import (
    CampaignPerformanceReport,
    ConsignmentPayablesReport,
    DailyCashReport,
    DailySummaryReport,
    InventoryValueReport,
    SalesMarginReport,
    TrendsReport,
)
from app.modules.reports.service import ReportsService
from app.shared.exceptions import DomainError

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


@router.get("/daily-summary", response_model=DailySummaryReport, operation_id="dailySummaryReport")
async def daily_summary(
    session: SessionDep,
    user: ManagerDep,
    report_date: Annotated[date, Query(alias="date")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> DailySummaryReport | Response:
    """每日營運儀表板（docs/19 R5）：今日營業額/認列營收/毛利/現金支出/購物金/估算淨利一覽。"""
    report = await ReportsService(session).daily_summary(user.store_id, report_date)
    if fmt == "json":
        return report
    rate = "N/A" if report.gross_margin_rate is None else str(report.gross_margin_rate)
    avg = "N/A" if report.avg_ticket is None else str(report.avg_ticket)
    net_income = "N/A" if report.estimated_net_income is None else str(report.estimated_net_income)
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("日期", report.date.isoformat()),
        ("估算淨利說明", report.estimated_net_income_note),
    ]
    exp = TabularExport(
        sheet="每日營運",
        filename_stem=f"daily-summary-{report.store_id}-{report.date.isoformat()}",
        meta=meta,
        headers=["指標", "值"],
        rows=[
            ["營業額", str(report.gross_turnover)],
            ["認列營收", str(report.recognized_revenue)],
            ["除稅淨額", str(report.net_sales_ex_tax)],
            ["稅額", str(report.tax)],
            ["寄售抽成收入", str(report.consignment_commission_income)],
            ["銷貨成本", str(report.cogs)],
            ["毛利", str(report.gross_margin)],
            ["毛利率", rate],
            ["成本未知營收", str(report.unknown_cost_sales)],
            ["餐飲營收", str(report.food_revenue)],
            ["二手營收", str(report.secondhand_revenue)],
            ["現金銷售", str(report.cash_sales_in)],
            ["作廢收購退現", str(report.acquisition_void_in)],
            ["收購付現", str(report.buyout_out)],
            ["寄售付款", str(report.consignment_payout_out)],
            ["人工調整", str(report.manual_adjust)],
            ["當日現金支出", str(report.total_cash_out)],
            ["應有現金", str(report.expected_cash)],
            ["實點現金", str(report.counted_cash)],
            ["現金差異", str(report.cash_variance)],
            ["購物金發出", str(report.store_credit_issued)],
            ["購物金兌付", str(report.store_credit_redeemed)],
            ["交易筆數", str(report.transaction_count)],
            ["客單價", avg],
            ["估算淨利", net_income],
        ],
    )
    return export_response(exp, fmt)


@router.get("/trends", response_model=TrendsReport, operation_id="financeTrendsReport")
async def trends(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
    granularity: Annotated[Literal["day", "week", "month", "quarter"], Query()] = "month",
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> TrendsReport | Response:
    """財務趨勢時間序列（docs/19 R6）：daily/weekly/monthly/quarterly KPI，餵趨勢圖。半開區間。"""
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    try:
        report = await ReportsService(session).trends(
            user.store_id, date_from=date_from, date_to=date_to, granularity=granularity
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("粒度", report.granularity),
        ("起", report.date_from.isoformat()),
        ("迄", report.date_to.isoformat()),
    ]
    exp = TabularExport(
        sheet="財務趨勢",
        filename_stem=f"trends-{report.store_id}-{report.granularity}",
        meta=meta,
        headers=[
            "期間",
            "營業額",
            "認列營收",
            "餐飲營收",
            "二手營收",
            "毛利",
            "毛利率",
            "銷貨成本",
            "現金支出",
            "購物金發出",
            "購物金兌付",
            "交易筆數",
        ],
        rows=[
            [
                r.period.isoformat(),
                str(r.gross_turnover),
                str(r.recognized_revenue),
                str(r.food_revenue),
                str(r.secondhand_revenue),
                str(r.gross_margin),
                "N/A" if r.gross_margin_rate is None else str(r.gross_margin_rate),
                str(r.cogs),
                str(r.total_cash_out),
                str(r.store_credit_issued),
                str(r.store_credit_redeemed),
                str(r.transaction_count),
            ]
            for r in report.rows
        ],
    )
    return export_response(exp, fmt)


@router.get(
    "/inventory-value", response_model=InventoryValueReport, operation_id="inventoryValueReport"
)
async def inventory_value(
    session: SessionDep,
    user: ManagerDep,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> InventoryValueReport | Response:
    """庫存價值與庫齡（docs/19 §2.4）：自有成本/售價、寄售在庫另列、catalog 成本 N/A、自有庫齡。"""
    report = await ReportsService(session).inventory_value(user.store_id)
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("自有在庫成本", str(report.total_owned_cost_value)),
        ("自有在庫售價", str(report.total_owned_retail_value)),
        ("寄售在庫售價(非自有資產)", str(report.consignment_inventory_gross)),
        ("數量品成本", "N/A"),
        ("庫齡<30天", str(report.owned_cost_aging.lt_30d)),
        ("庫齡30-90天", str(report.owned_cost_aging.d30_90)),
        ("庫齡90-180天", str(report.owned_cost_aging.d90_180)),
        ("庫齡180-365天", str(report.owned_cost_aging.d180_365)),
        ("庫齡>365天", str(report.owned_cost_aging.gt_365d)),
    ]
    exp = TabularExport(
        sheet="庫存價值",
        filename_stem=f"inventory-value-{report.store_id}",
        meta=meta,
        headers=["類別", "數量", "成本價值", "售價價值"],
        rows=[
            [
                "自有序號",
                str(report.owned_serialized_count),
                str(report.owned_serialized_cost),
                str(report.owned_serialized_retail),
            ],
            [
                "自有散裝(剩餘件)",
                str(report.owned_bulk_remaining_qty),
                str(report.owned_bulk_cost),
                str(report.owned_bulk_retail),
            ],
            [
                "寄售序號",
                str(report.consignment_serialized_count),
                "N/A",
                str(report.consignment_inventory_gross),
            ],
            [
                "寄售散裝(剩餘件)",
                str(report.consignment_bulk_remaining_qty),
                "N/A",
                "",
            ],
            [
                "數量型商品",
                str(report.catalog_total_qty),
                "N/A",
                str(report.catalog_retail_value),
            ],
        ],
    )
    return export_response(exp, fmt)


@router.get(
    "/consignment-payables",
    response_model=ConsignmentPayablesReport,
    operation_id="consignmentPayablesReport",
)
async def consignment_payables(
    session: SessionDep,
    user: ManagerDep,
    status_filter: Annotated[
        Literal["PENDING", "PAID", "CANCELLED", "ALL"], Query(alias="status")
    ] = "ALL",
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> ConsignmentPayablesReport | Response:
    """寄售應付（docs/19 §2.5）：只計 PENDING 待付；PAID/CANCELLED/reclaim 分欄；不輸出身分證。"""
    report = await ReportsService(session).consignment_payables(
        user.store_id, status_filter=status_filter
    )
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("狀態篩選", report.status_filter),
        ("待付合計(PENDING)", str(report.total_pending_payout)),
        ("已付合計(PAID)", str(report.total_paid_payout)),
        ("取消合計(CANCELLED)", str(report.total_cancelled_payout)),
        ("需追回合計(reclaim)", str(report.total_reclaim_needed_payout)),
    ]
    exp = TabularExport(
        sheet="寄售應付",
        filename_stem=f"consignment-payables-{report.store_id}",
        meta=meta,
        headers=[
            "結算ID",
            "寄售人",
            "電話",
            "銷售ID",
            "品號",
            "品名",
            "售價",
            "抽成",
            "應付",
            "狀態",
            "需追回",
            "售出時間",
        ],
        rows=[
            [
                str(r.settlement_id),
                r.consignor_name or "",
                r.consignor_phone or "",
                str(r.sale_id),
                r.item_code,
                r.item_name,
                str(r.gross),
                str(r.commission_amount),
                str(r.payout_amount),
                r.status,
                "是" if r.reclaim_needed else "否",
                r.sale_created_at.isoformat(),
            ]
            for r in report.rows
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
            ["餐飲營收", str(report.food_revenue)],
            ["二手營收", str(report.secondhand_revenue)],
            ["現金收款", str(report.cash_received)],
            ["購物金收款", str(report.store_credit_redeemed)],
            ["交易筆數", str(report.transaction_count)],
        ],
    )
    return export_response(exp, fmt)


@router.get(
    "/campaign-performance",
    response_model=CampaignPerformanceReport,
    operation_id="campaignPerformanceReport",
)
async def campaign_performance(
    session: SessionDep,
    user: ManagerDep,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> CampaignPerformanceReport | Response:
    """活動成效（docs/21 C4）：每檔生效中/已結束活動期間的營運成效 + 其發出的折讓。唯讀。"""
    report = await ReportsService(session).campaign_performance(user.store_id)
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
    ]
    exp = TabularExport(
        sheet="活動成效",
        filename_stem=f"campaign-performance-{report.store_id}",
        meta=meta,
        headers=[
            "活動",
            "狀態",
            "折扣%",
            "開始",
            "結束",
            "活動折讓總額",
            "營業額",
            "認列營收",
            "毛利",
            "毛利率",
            "交易筆數",
        ],
        rows=[
            [
                r.name,
                r.status.value,
                str(r.discount_pct),
                r.starts_at.isoformat(),
                r.ends_at.isoformat(),
                str(r.campaign_discount_total),
                str(r.gross_turnover),
                str(r.recognized_revenue),
                str(r.gross_margin),
                "N/A" if r.gross_margin_rate is None else str(r.gross_margin_rate),
                str(r.transaction_count),
            ]
            for r in report.rows
        ],
    )
    return export_response(exp, fmt)
