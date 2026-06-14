"""SC-4 報表匯出：把表格化報表渲染成 CSV / Excel（檔內含產生時間/區間/店別）。docs/16 §4。"""

import csv
import io
from dataclasses import dataclass

from openpyxl import Workbook  # type: ignore[import-untyped]

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow(exp.headers)
    for row in exp.rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx(exp: TabularExport) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = exp.sheet[:31]  # Excel 工作表名上限 31 字
    for key, value in exp.meta:
        sheet.append([key, value])
    sheet.append([])
    sheet.append(exp.headers)
    for row in exp.rows:
        sheet.append(row)
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()
