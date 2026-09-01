"""Phase 6 財務報表路由（MANAGER；docs/19）：每日現金對帳等。

所有報表唯讀、store 範圍（由 token 的 store_id 限定）；金額整數元字串。
?format=csv|xlsx 走 export_response，與 JSON 同源（同一 service 取數，匯出只做呈現轉換）。
時間瞬間維持 UTC；營業日與報表分桶固定以 Asia/Taipei 切界線。
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.core.money import format_ntd
from app.core.time import AwareDateTime, store_datetime_iso
from app.modules.reports.export import ExportFormat, TabularExport, export_response
from app.modules.reports.schemas import (
    CampaignPerformanceReport,
    ConsignmentPayablesReport,
    DailyCashReport,
    DailySummaryReport,
    DineInReport,
    DiscountReport,
    GiftReport,
    InsightsReport,
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
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("日期", report.date.isoformat()),
        ("合計開帳零用金", format_ntd(report.total_opening_float)),
        ("合計現金銷售", format_ntd(report.total_cash_sales)),
        ("合計作廢收購退現", format_ntd(report.total_acquisition_void_in)),
        ("合計收購付現", format_ntd(report.total_buyout_out)),
        ("合計寄售付款", format_ntd(report.total_consignment_payout_out)),
        ("合計退貨退現", format_ntd(report.total_sale_refund_out)),
        ("合計人工調整", format_ntd(report.total_manual_adjust)),
        ("合計應有現金", format_ntd(report.total_expected)),
        ("合計實點現金", format_ntd(report.total_counted)),
        ("合計差異", format_ntd(report.total_variance)),
        (
            "當日購物金兌付(只展示)",
            format_ntd(report.total_store_credit_redeemed_display_only),
        ),
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
                store_datetime_iso(r.opened_at),
                store_datetime_iso(r.closed_at) if r.closed_at else "",
                str(r.opened_by),
                str(r.closed_by) if r.closed_by is not None else "",
                format_ntd(r.opening_float),
                format_ntd(r.cash_sales),
                format_ntd(r.acquisition_void_in),
                format_ntd(r.buyout_out),
                format_ntd(r.consignment_payout_out),
                format_ntd(r.sale_refund_out),
                format_ntd(r.manual_adjust_total),
                format_ntd(r.expected_amount),
                format_ntd(r.counted_amount) if r.counted_amount is not None else "",
                format_ntd(r.variance) if r.variance is not None else "",
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
    """每日營運儀表板（docs/19 R5）：今日營業額/認列營收/毛利/現金支出/購物金一覽。"""
    report = await ReportsService(session).daily_summary(user.store_id, report_date)
    if fmt == "json":
        return report
    rate = "N/A" if report.gross_margin_rate is None else format_ntd(report.gross_margin_rate)
    avg = "N/A" if report.avg_ticket is None else format_ntd(report.avg_ticket)
    meta = [
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("日期", report.date.isoformat()),
    ]
    exp = TabularExport(
        sheet="每日營運",
        filename_stem=f"daily-summary-{report.store_id}-{report.date.isoformat()}",
        meta=meta,
        headers=["指標", "值"],
        rows=[
            ["營業額", format_ntd(report.gross_turnover)],
            ["認列營收", format_ntd(report.recognized_revenue)],
            ["除稅淨額", format_ntd(report.net_sales_ex_tax)],
            ["稅額", format_ntd(report.tax)],
            ["寄售抽成收入", format_ntd(report.consignment_commission_income)],
            ["銷貨成本", format_ntd(report.cogs)],
            ["毛利", format_ntd(report.gross_margin)],
            ["毛利率", rate],
            ["成本未知營收", format_ntd(report.unknown_cost_sales)],
            ["餐飲營收", format_ntd(report.food_revenue)],
            ["二手營收", format_ntd(report.secondhand_revenue)],
            ["現金銷售", format_ntd(report.cash_sales_in)],
            ["作廢收購退現", format_ntd(report.acquisition_void_in)],
            ["收購付現", format_ntd(report.buyout_out)],
            ["寄售付款", format_ntd(report.consignment_payout_out)],
            ["人工調整", format_ntd(report.manual_adjust)],
            ["當日現金支出", format_ntd(report.total_cash_out)],
            ["應有現金", format_ntd(report.expected_cash)],
            ["實點現金", format_ntd(report.counted_cash)],
            ["現金差異", format_ntd(report.cash_variance)],
            ["購物金發出", format_ntd(report.store_credit_issued)],
            ["購物金兌付", format_ntd(report.store_credit_redeemed)],
            ["交易筆數", str(report.transaction_count)],
            ["客單價", avg],
        ],
    )
    return export_response(exp, fmt)


@router.get("/trends", response_model=TrendsReport, operation_id="financeTrendsReport")
async def trends(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
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
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("粒度", report.granularity),
        ("起", store_datetime_iso(report.date_from)),
        ("迄", store_datetime_iso(report.date_to)),
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
                format_ntd(r.gross_turnover),
                format_ntd(r.recognized_revenue),
                format_ntd(r.food_revenue),
                format_ntd(r.secondhand_revenue),
                format_ntd(r.gross_margin),
                "N/A" if r.gross_margin_rate is None else format_ntd(r.gross_margin_rate),
                format_ntd(r.cogs),
                format_ntd(r.total_cash_out),
                format_ntd(r.store_credit_issued),
                format_ntd(r.store_credit_redeemed),
                str(r.transaction_count),
            ]
            for r in report.rows
        ],
    )
    return export_response(exp, fmt)


@router.get("/insights", response_model=InsightsReport, operation_id="businessInsightsReport")
async def insights(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> InsightsReport | Response:
    """經營洞察（#8）：品牌/類型暢銷彙整、周轉/滯銷摘要、業態營收結構。半開區間 [from, to)。

    匯出（?format=csv|xlsx）輸出品牌＋類型暢銷排行；周轉/業態結構置於 meta。
    """
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    report = await ReportsService(session).insights(
        user.store_id, date_from=date_from, date_to=date_to
    )
    if fmt == "json":
        return report

    def _days(value: float | None) -> str:
        return "N/A" if value is None else str(value)

    t, mix = report.turnover, report.revenue_mix
    meta = [
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("起", store_datetime_iso(report.date_from)),
        ("迄", store_datetime_iso(report.date_to)),
        ("在庫>90天件數", str(t.in_stock_over_90d)),
        ("平均周轉天數", _days(t.avg_turnover_days)),
        ("二手營收", format_ntd(mix.secondhand)),
        ("寄售抽成", format_ntd(mix.consignment_commission)),
        ("餐飲營收", format_ntd(mix.food)),
    ]
    rows = [
        [
            dim,
            r.label,
            str(r.units_sold),
            format_ntd(r.revenue),
            format_ntd(r.margin),
            format_ntd(r.avg_unit_price),
            _days(r.avg_days_in_stock),
        ]
        for dim, group in (("品牌", report.brand_breakdown), ("類型", report.category_breakdown))
        for r in group
    ]
    exp = TabularExport(
        sheet="經營洞察",
        filename_stem=f"insights-{report.store_id}",
        meta=meta,
        headers=["維度", "名稱", "售出件數", "營收", "毛利", "平均單價", "平均在庫天數"],
        rows=rows,
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
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("自有在庫成本", format_ntd(report.total_owned_cost_value)),
        ("自有在庫售價", format_ntd(report.total_owned_retail_value)),
        ("寄售在庫售價(非自有資產)", format_ntd(report.consignment_inventory_gross)),
        # 收貨會帶入進價（docs/32），所以這裡不再固定 N/A：真的沒有已知成本才顯示 N/A。
        (
            "一般商品成本",
            "N/A" if report.catalog_cost_value is None else format_ntd(report.catalog_cost_value),
        ),
        ("一般商品成本未知件數", str(report.catalog_unknown_cost_qty)),
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
                format_ntd(report.owned_serialized_cost),
                format_ntd(report.owned_serialized_retail),
            ],
            [
                "自有散裝(剩餘件)",
                str(report.owned_bulk_remaining_qty),
                format_ntd(report.owned_bulk_cost),
                format_ntd(report.owned_bulk_retail),
            ],
            [
                "寄售序號",
                str(report.consignment_serialized_count),
                "N/A",
                format_ntd(report.consignment_inventory_gross),
            ],
            [
                "寄售散裝(剩餘件)",
                str(report.consignment_bulk_remaining_qty),
                "N/A",
                "",
            ],
            [
                "一般商品",
                str(report.catalog_total_qty),
                "N/A"
                if report.catalog_cost_value is None
                else format_ntd(report.catalog_cost_value),
                format_ntd(report.catalog_retail_value),
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
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("狀態篩選", report.status_filter),
        ("待付合計(PENDING)", format_ntd(report.total_pending_payout)),
        ("已付合計(PAID)", format_ntd(report.total_paid_payout)),
        ("取消合計(CANCELLED)", format_ntd(report.total_cancelled_payout)),
        ("需追回合計(reclaim)", format_ntd(report.total_reclaim_needed_payout)),
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
                format_ntd(r.gross),
                format_ntd(r.commission_amount),
                format_ntd(r.payout_amount),
                r.status,
                "是" if r.reclaim_needed else "否",
                store_datetime_iso(r.sale_created_at),
            ]
            for r in report.rows
        ],
    )
    return export_response(exp, fmt)


@router.get("/sales-margin", response_model=SalesMarginReport, operation_id="salesMarginReport")
async def sales_margin(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
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
    rate = "N/A" if report.gross_margin_rate is None else format_ntd(report.gross_margin_rate)
    meta = [
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("起", store_datetime_iso(report.date_from)),
        ("迄", store_datetime_iso(report.date_to)),
    ]
    exp = TabularExport(
        sheet="銷售毛利",
        filename_stem=f"sales-margin-{report.store_id}",
        meta=meta,
        headers=["指標", "值"],
        rows=[
            ["營業額", format_ntd(report.gross_turnover)],
            ["認列營收", format_ntd(report.recognized_revenue)],
            ["自有序號成本", format_ntd(report.owned_cogs)],
            ["自有散裝成本", format_ntd(report.bulk_cogs)],
            ["一般商品成本", format_ntd(report.catalog_cogs)],
            ["寄售抽成收入", format_ntd(report.consignment_commission_income)],
            ["毛利", format_ntd(report.gross_margin)],
            ["毛利率", rate],
            ["成本未知營收", format_ntd(report.unknown_cost_sales)],
            ["餐飲營收", format_ntd(report.food_revenue)],
            ["二手營收", format_ntd(report.secondhand_revenue)],
            ["現金淨收款（扣退款）", format_ntd(report.cash_received)],
            ["購物金淨收款（扣退款）", format_ntd(report.store_credit_redeemed)],
            ["交易筆數", str(report.transaction_count)],
            ["支付手續費合計", format_ntd(report.payment_fee_total)],
            ["淨毛利（扣支付手續費）", format_ntd(report.net_margin)],
            ["臨時折扣", format_ntd(report.manual_discount_total)],
            ["送出贈品原價價值", format_ntd(report.gift_retail_value)],
            ["送出贈品成本", format_ntd(report.gift_cost)],
            ["退回贈品原價價值", format_ntd(report.gift_returned_retail_value)],
            ["退回贈品成本", format_ntd(report.gift_returned_cost)],
            ["淨贈品原價價值", format_ntd(report.net_gift_retail_value)],
            ["淨贈品成本", format_ntd(report.net_gift_cost)],
            ["貢獻毛利（淨毛利扣淨贈品成本）", format_ntd(report.contribution_margin)],
            *[
                row
                for method in report.payment_methods
                for row in (
                    [f"付款方式 {method.method} 淨收款", format_ntd(method.received)],
                    [f"付款方式 {method.method} 手續費", format_ntd(method.fee)],
                )
            ],
        ],
    )
    return export_response(exp, fmt)


@router.get("/discounts", response_model=DiscountReport, operation_id="discountReport")
async def discounts(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> DiscountReport | Response:
    """臨時折扣報表：依原因與店員彙總。半開區間 [from, to)；to<=from → 422。

    無主管核准機制（店主裁示不設上限），這份報表是事後稽核的主要依據。
    """
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    report = await ReportsService(session).discount_report(
        user.store_id, date_from=date_from, date_to=date_to
    )
    if fmt == "json":
        return report
    meta = [
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("起", store_datetime_iso(report.date_from)),
        ("迄", store_datetime_iso(report.date_to)),
        ("折扣總額", format_ntd(report.discount_total)),
    ]
    exp = TabularExport(
        sheet="臨時折扣",
        filename_stem=f"discounts-{report.store_id}",
        meta=meta,
        headers=["分類", "名稱", "筆數", "單品折扣", "整單折扣", "合計"],
        rows=[
            [
                "原因",
                row.reason_name,
                str(row.adjustment_count),
                format_ntd(row.item_discount_total),
                format_ntd(row.order_discount_total),
                format_ntd(row.discount_total),
            ]
            for row in report.by_reason
        ]
        + [
            [
                "店員",
                row.clerk_username,
                str(row.adjustment_count),
                "",
                "",
                format_ntd(row.discount_total),
            ]
            for row in report.by_clerk
        ],
    )
    return export_response(exp, fmt)


@router.get("/dine-in", response_model=DineInReport, operation_id="dineInReport")
async def dine_in(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
    granularity: Annotated[str, Query()] = "day",
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> DineInReport | Response:
    """餐飲內用／外帶報表（docs/39）：組數、佔比、趨勢、客單價與時段分佈。

    半開區間 [from, to)；to<=from → 422。

    **口徑**：一筆含餐飲品項的結帳＝一組；佔比的分母是「有餐飲的單」而非全店訂單；
    客單價只算 `MENU` 行。內用與外帶的客單價**不可直接比較**——外帶不累點、不折扣、
    不可用購物金（docs/35），計價條件本就不同。
    """
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    try:
        report = await ReportsService(session).dine_in_report(
            user.store_id, date_from=date_from, date_to=date_to, granularity=granularity
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if fmt == "json":
        return report
    # 匯出與 JSON **同源**（同一 service 取數），只做呈現轉換。
    # meta 帶上口徑：檔案離開系統之後，畫面上那兩句提醒就跟不過去了。
    exp = TabularExport(
        sheet="餐飲內用外帶",
        filename_stem=f"dine-in-{report.store_id}-{report.granularity}",
        meta=[
            ("店別", str(report.store_id)),
            ("區間", f"{report.date_from.isoformat()} ~ {report.date_to.isoformat()}"),
            ("粒度", report.granularity),
            ("組數定義", "一筆含餐飲品項的結帳算一組"),
            ("佔比分母", "有餐飲的單（不是全店訂單）"),
            (
                "客單價口徑",
                "只計餐飲品項；內用與外帶不可直接比較（外帶不累點/不折扣/不可用購物金）",
            ),
        ],
        headers=["服務型態", "組數", "佔比", "餐飲營收", "餐飲客單價", "整單合計"],
        rows=[
            [
                label,
                str(stats.groups),
                f"{stats.share:.4f}",
                format_ntd(stats.fnb_revenue),
                format_ntd(stats.avg_ticket),
                format_ntd(stats.gross_total),
            ]
            for label, stats in (
                ("內用", report.summary.dine_in),
                ("外帶", report.summary.takeout),
            )
        ]
        + [["", "", "", "", "", ""]]
        + [["期間起", "內用組數", "外帶組數", "內用營收", "外帶營收", ""]]
        + [
            [
                b.period.isoformat(),
                str(b.dine_in_groups),
                str(b.takeout_groups),
                format_ntd(b.dine_in_revenue),
                format_ntd(b.takeout_revenue),
                "",
            ]
            for b in report.trend
        ]
        # **時段分佈也要進檔案**：它是店主指名的四個指標之一，
        # 只印在畫面上等於「只能用眼睛看、不能拿去分析」。
        + [["", "", "", "", "", ""]]
        + [["時段（台北）", "內用組數", "外帶組數", "", "", ""]]
        + [
            [f"{h.hour:02d}:00", str(h.dine_in_groups), str(h.takeout_groups), "", "", ""]
            for h in report.hourly
        ],
    )
    return export_response(exp, fmt)


@router.get("/gifts", response_model=GiftReport, operation_id="giftReport")
async def gifts(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[AwareDateTime, Query(alias="from")],
    date_to: Annotated[AwareDateTime, Query(alias="to")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> GiftReport | Response:
    """贈品報表：依原因與品項彙總送出、退回及淨額。[from, to)；to<=from → 422。

    贈品原價不計入營業額、成本不混入商品毛利；退回歸屬退貨發生日。
    """
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    report = await ReportsService(session).gift_report(
        user.store_id, date_from=date_from, date_to=date_to
    )
    if fmt == "json":
        return report
    meta = [
        ("產生時間", store_datetime_iso(report.generated_at)),
        ("店別", str(report.store_id)),
        ("起", store_datetime_iso(report.date_from)),
        ("迄", store_datetime_iso(report.date_to)),
        ("贈品件數", str(report.gift_qty)),
        ("原價價值", format_ntd(report.retail_value)),
        ("成本", format_ntd(report.cost)),
        ("退回件數", str(report.returned_gift_qty)),
        ("退回原價價值", format_ntd(report.returned_retail_value)),
        ("退回成本", format_ntd(report.returned_cost)),
        ("淨件數", str(report.net_gift_qty)),
        ("淨原價價值", format_ntd(report.net_retail_value)),
        ("淨成本", format_ntd(report.net_cost)),
    ]
    exp = TabularExport(
        sheet="贈品",
        filename_stem=f"gifts-{report.store_id}",
        meta=meta,
        headers=[
            "分類",
            "名稱",
            "送出件數",
            "送出原價價值",
            "送出成本",
            "退回件數",
            "退回原價價值",
            "退回成本",
            "淨件數",
            "淨原價價值",
            "淨成本",
        ],
        rows=[
            [
                "原因",
                row.reason_name,
                str(row.gift_qty),
                format_ntd(row.retail_value),
                format_ntd(row.cost),
                str(row.returned_gift_qty),
                format_ntd(row.returned_retail_value),
                format_ntd(row.returned_cost),
                str(row.net_gift_qty),
                format_ntd(row.net_retail_value),
                format_ntd(row.net_cost),
            ]
            for row in report.by_reason
        ]
        + [
            [
                "品項",
                row.description,
                str(row.gift_qty),
                format_ntd(row.retail_value),
                format_ntd(row.cost),
                str(row.returned_gift_qty),
                format_ntd(row.returned_retail_value),
                format_ntd(row.returned_cost),
                str(row.net_gift_qty),
                format_ntd(row.net_retail_value),
                format_ntd(row.net_cost),
            ]
            for row in report.by_product
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
        ("產生時間", store_datetime_iso(report.generated_at)),
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
                store_datetime_iso(r.starts_at),
                store_datetime_iso(r.ends_at),
                format_ntd(r.campaign_discount_total),
                format_ntd(r.gross_turnover),
                format_ntd(r.recognized_revenue),
                format_ntd(r.gross_margin),
                "N/A" if r.gross_margin_rate is None else format_ntd(r.gross_margin_rate),
                str(r.transaction_count),
            ]
            for r in report.rows
        ],
    )
    return export_response(exp, fmt)
