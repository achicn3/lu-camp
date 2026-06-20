"""SC-4 購物金報表路由（MANAGER；docs/16 §4）：負債/帳齡、流量、對帳；?format=csv|xlsx 匯出。

所有報表唯讀、store 範圍；數值從帳本推導。匯出檔含產生時間/區間/店別。
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.reports.export import (
    CSV_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    TabularExport,
    to_csv,
    to_xlsx,
)
from app.modules.reports.schemas import (
    EffectivenessReport,
    FlowsReport,
    LiabilityReport,
    ReconciliationReport,
)
from app.modules.reports.service import ReportsService
from app.shared.exceptions import DomainError

router = APIRouter(prefix="/reports/store-credit", tags=["reports"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]

ExportFormat = Literal["json", "csv", "xlsx"]


def _export_response(exp: TabularExport, fmt: ExportFormat) -> Response:
    if fmt == "csv":
        return Response(
            content=to_csv(exp),
            media_type=CSV_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{exp.filename_stem}.csv"'},
        )
    return Response(
        content=to_xlsx(exp),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{exp.filename_stem}.xlsx"'},
    )


@router.get("/liability", response_model=LiabilityReport, operation_id="storeCreditLiability")
async def liability(
    session: SessionDep,
    user: ManagerDep,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> LiabilityReport | Response:
    report = await ReportsService(session).liability(user.store_id)
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("總未兌付負債", str(report.total_outstanding)),
        ("負債健康比", report.liability_health_ratio or "N/A"),
        ("帳齡<30天", str(report.aging_buckets.lt_30d)),
        ("帳齡30-90天", str(report.aging_buckets.d30_90)),
        ("帳齡90-180天", str(report.aging_buckets.d90_180)),
        ("帳齡180-365天", str(report.aging_buckets.d180_365)),
        ("帳齡>365天", str(report.aging_buckets.gt_365d)),
    ]
    exp = TabularExport(
        sheet="購物金負債",
        filename_stem=f"store-credit-liability-{report.store_id}",
        meta=meta,
        headers=["會員ID", "姓名", "餘額"],
        rows=[[str(m.contact_id), m.name, str(m.balance)] for m in report.per_member],
    )
    return _export_response(exp, fmt)


@router.get("/flows", response_model=FlowsReport, operation_id="storeCreditFlows")
async def flows(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
    granularity: Annotated[Literal["day", "week", "month"], Query()] = "day",
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> FlowsReport | Response:
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    try:
        report = await ReportsService(session).flows(
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
        sheet="購物金流量",
        filename_stem=f"store-credit-flows-{report.store_id}",
        meta=meta,
        headers=[
            "期間",
            "發出",
            "兌付",
            "淨變化",
            "發出毛額",
            "發出沖正",
            "兌付毛額",
            "兌付沖正",
        ],
        rows=[
            [
                r.period.isoformat(),
                str(r.issued),
                str(r.redeemed),
                str(r.net_change),
                str(r.issued_gross),
                str(r.issued_reversed),
                str(r.redeemed_gross),
                str(r.redeemed_reversed),
            ]
            for r in report.rows
        ],
    )
    return _export_response(exp, fmt)


def _ratio_cell(value: object) -> str:
    return "N/A" if value is None else str(value)


@router.get(
    "/effectiveness", response_model=EffectivenessReport, operation_id="storeCreditEffectiveness"
)
async def effectiveness(
    session: SessionDep,
    user: ManagerDep,
    date_from: Annotated[datetime, Query(alias="from")],
    date_to: Annotated[datetime, Query(alias="to")],
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> EffectivenessReport | Response:
    """§5B 效益指標（MANAGER）。β/α/Δ 為估計值；α 為代理法，兌付樣本不足時報表加註。"""
    if date_to <= date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="to 必須晚於 from"
        )
    report = await ReportsService(session).effectiveness(
        user.store_id, date_from=date_from, date_to=date_to
    )
    if fmt == "json":
        return report
    estimate = "估計值"
    alpha_label = "估計值（代理法）"
    if report.alpha_sample_insufficient:
        alpha_label = "估計值（代理法，樣本不足）"
    rows = [
        ["選用率 take_rate", _ratio_cell(report.take_rate), "直接量測"],
        ["平均溢價率 avg_premium_rate", _ratio_cell(report.avg_premium_rate), "直接量測"],
        ["額外消費率 excess_spend_rate", _ratio_cell(report.excess_spend_rate), "直接量測"],
        ["實際毛利率 gross_margin_m", _ratio_cell(report.gross_margin_m), "直接量測"],
        ["沉澱率 beta_retention", _ratio_cell(report.beta_retention), estimate],
        ["新增比例 alpha_incremental", _ratio_cell(report.alpha_incremental), alpha_label],
        ["損益敏感度 delta_per_1000", _ratio_cell(report.delta_per_1000), estimate],
        ["兌付筆數 redemption_count", str(report.redemption_count), "直接量測"],
    ]
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("起", report.date_from.isoformat()),
        ("迄", report.date_to.isoformat()),
        ("α 代理法說明", report.alpha_method_note),
    ]
    exp = TabularExport(
        sheet="購物金效益",
        filename_stem=f"store-credit-effectiveness-{report.store_id}",
        meta=meta,
        headers=["指標", "值", "性質"],
        rows=rows,
    )
    return _export_response(exp, fmt)


@router.get(
    "/reconciliation",
    response_model=ReconciliationReport,
    operation_id="storeCreditReconciliation",
)
async def reconciliation(
    session: SessionDep,
    user: ManagerDep,
    fmt: Annotated[ExportFormat, Query(alias="format")] = "json",
) -> ReconciliationReport | Response:
    report = await ReportsService(session).reconciliation(user.store_id)
    if fmt == "json":
        return report
    meta = [
        ("產生時間", report.generated_at.isoformat()),
        ("店別", str(report.store_id)),
        ("帳本推導總負債", str(report.ledger_total_outstanding)),
        ("快取總負債", str(report.cached_total_outstanding)),
        ("快取可信", "是" if report.cached_total_trustworthy else "否"),
    ]
    exp = TabularExport(
        sheet="購物金對帳",
        filename_stem=f"store-credit-reconciliation-{report.store_id}",
        meta=meta,
        headers=["不一致項目"],
        rows=[[str(m)] for m in report.mismatches],
    )
    return _export_response(exp, fmt)
