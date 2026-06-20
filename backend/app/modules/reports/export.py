"""SC-4 報表匯出：把表格化報表渲染成 CSV / Excel（檔內含產生時間/區間/店別）。docs/16 §4。"""

import csv
import io
from dataclasses import dataclass
from typing import Literal

from fastapi import Response
from openpyxl import Workbook  # type: ignore[import-untyped]

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ExportFormat = Literal["json", "csv", "xlsx"]

# 試算表公式注入防護：以這些字元開頭的儲存格，Excel/Sheets 會當公式執行（CSV/XLSX 皆然）。
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: object) -> str:
    """把不可信文字轉為安全儲存格：危險開頭字元前綴單引號，強制當純文字。"""
    text = str(value)
    if text and text[0] in _FORMULA_TRIGGERS:
        return "'" + text
    return text


@dataclass(frozen=True)
class TabularExport:
    """一份可匯出的報表：sheet 名、檔名、後設資料（產生時間/區間/店別等）、表頭與列。"""

    sheet: str
    filename_stem: str
    meta: list[tuple[str, str]]
    headers: list[str]
    rows: list[list[str]]


def to_csv(exp: TabularExport) -> bytes:
    """utf-8-sig（BOM）讓 Excel 直接開不亂碼中文。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for key, value in exp.meta:
        writer.writerow([_safe_cell(key), _safe_cell(value)])
    writer.writerow([])
    writer.writerow([_safe_cell(h) for h in exp.headers])
    for row in exp.rows:
        writer.writerow([_safe_cell(cell) for cell in row])
    return buf.getvalue().encode("utf-8-sig")


def export_response(exp: TabularExport, fmt: Literal["csv", "xlsx"]) -> Response:
    """把 TabularExport 渲染成附檔下載回應（CSV 或 XLSX）；供各報表端點共用。"""
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


def to_xlsx(exp: TabularExport) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = exp.sheet[:31]  # Excel 工作表名上限 31 字
    for key, value in exp.meta:
        sheet.append([_safe_cell(key), _safe_cell(value)])
    sheet.append([])
    sheet.append([_safe_cell(h) for h in exp.headers])
    for row in exp.rows:
        sheet.append([_safe_cell(cell) for cell in row])
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()
