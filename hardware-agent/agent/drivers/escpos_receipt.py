"""ESC/POS 收據機真機驅動（T15）。

實作 `agent.interfaces.ReceiptPrinter`，把銷售與店家抬頭排版成 ESC/POS 位元組寫到
`SupportsWrite`（實機為 EPSON TM-T82III 的連線；測試用 byte buffer 斷言版面，免實機）。
金額皆為字串整數元（§6），驅動不做金額運算、只如實排版。電子發票列印為 placeholder
（內容待發票收尾階段，docs/14 §5），僅輸出佔位標記、不偽造發票內容。
"""

from __future__ import annotations

import unicodedata

from agent.escpos_printer import ESC, FS, GS, SupportsWrite
from agent.interfaces import InvoicePayload, SaleLinePayload, SalePayload, StoreHeader

_INIT = ESC + b"@"  # 初始化印表機
_ALIGN_CENTER = ESC + b"a" + bytes([1])
_ALIGN_LEFT = ESC + b"a" + bytes([0])
_CUT = GS + b"V" + bytes([0])  # full cut
# 切刀位於印字頭上方約 1.2cm；切紙前先進紙，讓最後內容（總計區）通過切刀，
# 否則結尾會被切掉並殘留到下一張頂端（實機驗證 2026-06-08）。
_FEED_BEFORE_CUT = b"\n" * 5
# EPSON TM-T82III 繁體中文：FS & 進中文（Big5）模式、FS . 離開（實機驗證 2026-06-08）。
# ASCII（< 0x80）在中文模式下仍以單位元組如實列印，故整份文件包在中文模式即可。
_ENTER_CHINESE = FS + b"&"
_EXIT_CHINESE = FS + b"."

# 品項表格欄寬（半形單位）：此台 TM-T82III 實機量得一行可印 34 半形（中文全形佔 2，
# 實機尺規驗證 2026-06-08）。品名靠左（過長截斷），單價/總價靠右對齊成固定欄，標題列
# 同寬，欄位才對得齊；超出 34 的內容會掉出可印範圍（金額消失），故欄寬合計須 = 34。
_WIDTH = 34
_UNIT_W = 7
_TOTAL_W = 7
_NAME_W = _WIDTH - _UNIT_W - _TOTAL_W
_SEP = "-" * _WIDTH
_TRUNCATE_MARK = ".."  # ASCII 截斷標記（Big5 安全、寬度確定）


def _line(text: str) -> bytes:
    # 繁中以 Big5 編碼（TM-T82III 字庫）；非 Big5 字以 ? 取代，避免單一字中斷整張列印。
    return text.encode("big5", errors="replace") + b"\n"


def _disp_width(text: str) -> int:
    """列印顯示寬度（半形）：東亞全形/寬字元佔 2，其餘 1。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in text)


def _pad_left_field(text: str, width: int) -> str:
    """靠左欄：補空白到 width 半形；過長則截斷並加 `..`（仍精準補滿 width）。"""
    if _disp_width(text) <= width:
        return text + " " * (width - _disp_width(text))
    keep = width - len(_TRUNCATE_MARK)
    out, used = "", 0
    for char in text:
        char_w = 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        if used + char_w > keep:
            break
        out += char
        used += char_w
    return out + _TRUNCATE_MARK + " " * (width - used - len(_TRUNCATE_MARK))


def _pad_right_field(text: str, width: int) -> str:
    """靠右欄：左補空白到 width 半形（數字欄用）。"""
    return " " * max(0, width - _disp_width(text)) + text


def _item_row(line: SaleLinePayload) -> str:
    """一列品項：品名（含 x數量）靠左、單價/總價靠右，固定欄寬對齊。"""
    name = f"{line.description} x{line.qty}"
    return (
        _pad_left_field(name, _NAME_W)
        + _pad_right_field(line.unit_price, _UNIT_W)
        + _pad_right_field(line.line_total, _TOTAL_W)
    )


_ITEM_HEADER = (
    _pad_left_field("品項", _NAME_W)
    + _pad_right_field("單價", _UNIT_W)
    + _pad_right_field("總價", _TOTAL_W)
)


def _header_block(header: StoreHeader) -> bytes:
    out = bytearray()
    out += _ALIGN_CENTER
    out += _line(header.name)
    out += _ALIGN_LEFT
    if header.tax_id:
        out += _line(f"統一編號：{header.tax_id}")
    if header.address:
        out += _line(f"地址：{header.address}")
    if header.phone:
        out += _line(f"電話：{header.phone}")
    out += _line(_SEP)
    return bytes(out)


def _totals_block(sale: SalePayload) -> bytes:
    out = bytearray()
    out += _line(_SEP)
    out += _line(f"未稅　 {sale.subtotal}")
    out += _line(f"營業稅 {sale.tax}")
    out += _line(f"總計　 {sale.total}")
    out += _line(f"付款方式：{sale.payment_method}")
    return bytes(out)


class EscposReceiptPrinter:
    """以 ESC/POS 位元組列印收據／明細聯的真機驅動。"""

    def __init__(self, writer: SupportsWrite) -> None:
        self._writer = writer

    def _emit_doc(self, sale: SalePayload, header: StoreHeader, *, title: str) -> None:
        out = bytearray()
        out += _INIT
        out += _ENTER_CHINESE  # 整份文件以中文（Big5）模式列印，ASCII 仍如實
        out += _header_block(header)
        out += _ALIGN_CENTER + _line(title) + _ALIGN_LEFT
        out += _line(_ITEM_HEADER)  # 欄位標題列：品項 / 單價 / 總價
        out += _line(_SEP)
        for line in sale.lines:
            out += _line(_item_row(line))
        out += _totals_block(sale)
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))

    def print_receipt(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="收據")

    def print_detail(self, sale: SalePayload, header: StoreHeader) -> None:
        self._emit_doc(sale, header, title="商品明細聯")

    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """placeholder：發票版面/欄位待發票收尾階段；此處僅輸出佔位、不偽造發票內容。"""
        out = bytearray()
        out += _INIT
        out += _ENTER_CHINESE
        out += _line(f"[電子發票待發票收尾階段] sale_id={invoice.sale_id}")
        out += _EXIT_CHINESE
        out += _FEED_BEFORE_CUT
        out += _CUT
        self._writer.write(bytes(out))
